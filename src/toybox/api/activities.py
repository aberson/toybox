"""Activity lifecycle REST API.

Implements the propose / approve / dismiss / regenerate / advance / end
/ didn't-work transitions described in the Step 8 problem statement.
Mutations enforce optimistic concurrency through the ``If-Match-Version``
header (see :mod:`toybox.core.version_check`) and emit
``activity.state`` envelopes via the process-singleton
:class:`toybox.core.pubsub.PubSub`.

The persisted shape (``activities`` + ``activity_steps`` rows) is
serialized to a wire model :class:`ActivityResponse` that combines the
persisted columns and the generator's in-memory step list. Step text
is stored in ``activity_steps.body``; the response uses the same
``body`` field for clarity (matching the migration's column name).
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import secrets
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..activities.content_resolver import (
    GenericDescriptor,
    ResolvedRoom,
    ResolvedToy,
    resolve_child_profiles,
    resolve_role_slots,
    resolve_rooms,
    resolve_toys,
)
from ..activities.feedback import (
    KIND_DIDNT_WORK,
    KIND_DISMISSED_PRE_APPROVAL,
    KIND_LOVED_IT,
)
from ..activities.generator import (
    ADAPTER_CLAUDE,
    ADAPTER_LOCAL,
    MODE_LOOP,
    build_generator_context,
    find_template_by_id,
    generate,
    render_with_slot_fills,
    resolve_dispatch,
)
from ..activities.interjection import build_interjection_step
from ..activities.interjections import InterjectionKind
from ..activities.joke_corpus import Joke, apply_toy_substitution, pick_joke
from ..activities.roles import ROLE_DISPLAY_NAMES, Role
from ..activities.song_corpus import Song, pick_song
from ..ai.judge import judge_and_persist
from ..ai.labeled_events import (
    GENERATOR_PATH_CLAUDE,
    GENERATOR_PATH_OFFLINE,
    PARENT_SIGNAL_DISMISS,
    PARENT_SIGNAL_END_EARLY,
    PARENT_SIGNAL_THUMBS_UP,
    record_generation,
    schedule_judge_sample,
    update_parent_signal,
)
from ..core import (
    jokes_enabled,
    play_embedded_enabled,
    play_endings_enabled,
    play_standalone_enabled,
    play_target_depth,
    songs_enabled,
)
from ..core.auth import TokenScope
from ..core.pubsub import PubSub
from ..core.queue import (
    DISMISSED_STATE,
    PROPOSED_STATE,
    evict_oldest_for_capacity,
)
from ..core.version_check import (
    VersionConflictError,
    if_match_version_dependency,
)
from ..db import connect, resolve_db_path
from ..ws.envelope import build_envelope
from ..ws.server import get_pubsub
from ..ws.topics import Topic
from .auth_dep import RequireScope

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/activities", tags=["activities"])

# Type alias for the FastAPI-injected judge call. ``None`` means the
# judge is not configured (no OAuth token) and the recorder skips
# scheduling. Production wires this via :func:`get_judge_call`; tests
# override the dependency with either ``None`` or a recording stub.
JudgeCall = Any

# Activity state literals (pinned by tests + frontend).
STATE_PROPOSED = "proposed"
STATE_APPROVED = "approved"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_COMPLETED = "completed"
STATE_ENDED = "ended"
STATE_DISMISSED = "dismissed"
STATE_DIDNT_WORK = "didnt_work"

# Valid transition map: source state -> set of target states.
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_PROPOSED: frozenset({STATE_APPROVED, STATE_DISMISSED}),
    STATE_APPROVED: frozenset({STATE_RUNNING, STATE_ENDED, STATE_DISMISSED}),
    STATE_RUNNING: frozenset(
        {
            STATE_RUNNING,
            STATE_PAUSED,
            STATE_COMPLETED,
            STATE_ENDED,
            STATE_DIDNT_WORK,
            STATE_DISMISSED,
        }
    ),
    STATE_PAUSED: frozenset({STATE_RUNNING, STATE_ENDED, STATE_DIDNT_WORK, STATE_DISMISSED}),
    STATE_COMPLETED: frozenset({STATE_DIDNT_WORK}),
    STATE_ENDED: frozenset({STATE_DIDNT_WORK}),
    STATE_DISMISSED: frozenset(),
    STATE_DIDNT_WORK: frozenset(),
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def get_activities_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield an activities-scoped SQLite connection.

    ``check_same_thread=False`` because FastAPI dispatches sync
    generator setup, the handler body, and teardown via
    ``run_in_threadpool``; anyio may pick a different worker for each
    leg, which would otherwise trip
    ``sqlite3.ProgrammingError`` in ``conn.close()``.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def get_judge_call() -> JudgeCall:
    """FastAPI dependency: return the judge_call partial (or ``None``).

    Production: when an OAuth token is on disk, build a
    :class:`~toybox.ai.client.AnthropicClient` and partial it into
    :func:`toybox.ai.judge.judge_and_persist` plus the DB-path resolver.
    The result is the callable
    :func:`toybox.ai.labeled_events.schedule_judge_sample` invokes when
    a row is in-sample. When no token is available we return ``None``
    so the sampler skips silently — the recorder still writes the row,
    just without judge scores.

    Tests override this dependency to inject a deterministic stub.
    The token is resolved per-call (not cached) so a fresh login is
    picked up on the next request without a process restart; this is
    cheap because :func:`toybox.ai.oauth.load_token` is just a JSON
    read.
    """
    # Late imports keep the import surface tight and avoid pulling
    # AnthropicClient (which lazy-imports the SDK) on every module load.
    from ..ai.client import AnthropicClient  # noqa: PLC0415
    from ..ai.oauth import load_token  # noqa: PLC0415

    token = load_token()
    if token is None:
        return None
    ai_client = AnthropicClient(token)
    return functools.partial(
        judge_and_persist,
        ai_client=ai_client,
        db_path_resolver=resolve_db_path,
    )


class ChoiceOption(BaseModel):
    """Phase G G3: one runtime choice button on an activity step.

    Distinct from the template-time :class:`toybox.activities.models.Choice`
    (which carries ``next``, the successor step id used by the server's
    edge resolver). The kiosk consumes only this runtime shape: a
    rendered label string + the array index used as ``choice_index``
    when posting back to ``/advance``. The ``next`` field is
    intentionally NOT exposed to the kid — the server resolves edges,
    the kid just picks an option.
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    choice_index: int = Field(ge=0)


class ActivityStepResponse(BaseModel):
    """Wire shape for one activity step.

    Phase F Step F6: ``action_slot`` carries the per-step action
    vocabulary key (one of :data:`toybox.image_gen.models.ACTION_SLOTS`)
    or ``None`` for legacy rows / templates that don't pin a slot.
    The kiosk renders the matching toy sprite (F7) when the slot is
    set AND the activity has at least one toy with a sprite for it.

    Phase G G3 additions (additive, both nullable for backward-compat):

    * ``choices`` — runtime choice buttons rendered with this step's
      slot fills. ``None`` on linear steps (no buttons), populated to
      ``[{label, choice_index}, ...]`` on branching steps.
    * ``chosen_label`` — the label the kid picked at THIS step (NOT
      the next step). Populated when the kid advanced past a choice
      point; ``None`` for linear-advance and terminal rows.
    """

    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=1)
    body: str = Field(min_length=1)
    sfx: str | None = None
    expected_action: str | None = None
    current: bool = False
    action_slot: str | None = None
    # Phase G G3: rendered choice buttons. Parsed by the serializer
    # from the persisted ``activity_steps.choices_json`` (a JSON
    # array of strings) by enumerating array indices into
    # ``choice_index``. ``None`` on linear steps.
    choices: list[ChoiceOption] | None = None
    # Phase G G3: ``chosen_label`` records the label of the choice
    # the kid picked at this step. Populated by the advance handler
    # on the previous step's row when a branching choice resolved;
    # ``None`` for linear advance, terminal, and steps not yet
    # advanced past.
    chosen_label: str | None = None
    # Phase K K13: per-step discriminator the kiosk dispatches on
    # (K12 StepCard.tsx). ``"text" | "fork" | "song" | "joke"``;
    # ``None`` on legacy rows (kiosk defaults to ``"text"`` defensively).
    # K13 populates this for standalone song/joke intents; K14 / K15
    # populate it for embedded / ending / parent / spontaneity
    # interjection steps.
    kind: str | None = None
    # Phase K K13: per-step metadata blob the kiosk reads for kind-
    # specific payload. Today's known keys:
    #
    #   * ``audio_url`` (str)  — song mp3 URL (SongPlayer reads this
    #     directly; falls back to ``/api/static/songs/audio/<id>.mp3``
    #     via ``song_id`` when missing).
    #   * ``song_id`` (str)    — corpus id for song step.
    #   * ``joke_id`` (str)    — corpus id for joke step.
    #   * ``punchline`` (str)  — reveal beat for joke step.
    #   * ``interjection`` (str) — embedded|ending|parent|spontaneity (K14/K15).
    #   * ``source_id`` (str)  — corpus entry id (K14/K15 telemetry).
    #
    # ``None`` on legacy rows / steps with no per-step metadata.
    metadata: dict[str, Any] | None = None


class RoleAssignment(BaseModel):
    """Phase K K5: one role slot's resolved filler.

    Surfaced on :class:`ActivityResponse.roles` so the parent UI (K7)
    can render the "cast list" panel without re-running the slot-fill
    engine. Exactly ONE of ``toy_id`` / ``generic_descriptor`` is set:

    * ``toy_id`` populated when the slot-fill engine assigned a real
      catalog toy (``ResolvedToy``) — the kiosk can dereference the id
      for the sprite, and ``display_name`` carries the toy's friendly
      label.
    * ``generic_descriptor`` populated when the toy pool was exhausted
      before every ``optional_role`` slot could be filled by a real toy
      — :data:`toybox.activities.generic_descriptors.GENERIC_DESCRIPTORS`
      provides a fallback flavor string and ``display_name`` mirrors it
      so renderers can use a single field for both branches.

    The discriminator is the presence of one or the other; the kiosk's
    sprite resolver short-circuits when ``toy_id is None``.
    """

    model_config = ConfigDict(frozen=True)

    role_name: str = Field(min_length=1)
    toy_id: str | None = None
    generic_descriptor: str | None = None
    display_name: str = Field(min_length=1)


class ActivityResponse(BaseModel):
    """Wire shape for an activity returned by the REST API."""

    model_config = ConfigDict(frozen=True)

    id: str
    state: str
    version: int = Field(ge=1)
    title: str | None = None
    summary: str | None = None
    persona_id: str | None = None
    intent_source: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    # Phase F: hydrated toy ids for the kiosk's ToyActionSprite resolution
    # (renders sprite for toy_ids[0]). Empty list when the activity didn't
    # involve any toys.
    toy_ids: list[str] = Field(default_factory=list)
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    steps: list[ActivityStepResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Step 23: "why this?" telemetry. Read from the persisted summary
    # JSON envelope's ``metadata`` block on the way out. ``None`` when
    # the activity row predates step 23 (no envelope key) or when the
    # propose call didn't supply a trigger / persona rationale.
    trigger_phrase: str | None = None
    persona_reasoning: str | None = None
    # Phase K K5: resolved role-slot assignments. Keyed by the lowercase
    # snake_case :class:`Role` value (``"quest_giver"``). Always present;
    # ``{}`` for templates with no ``required_roles`` / ``optional_roles``.
    # The slot-fill engine (K4) populates this on propose; the recast
    # endpoint (K6) and insert-joke/song endpoints (K15) may rewrite it.
    roles: dict[str, RoleAssignment] = Field(default_factory=dict)
    # Phase K K5: pre-rendered cast summary string for the parent UI's
    # "What this looks like" panel (K7). Format:
    # ``"Quest Giver: Wise Owl, Hero: Captain Bear"`` — sorted by role
    # name for determinism, comma-separated. Empty string when ``roles``
    # is empty.
    cast_summary: str = ""
    # Phase K K13: explanation for a synthetic ``state="dismissed"``
    # response returned when a standalone intent (``request_song`` /
    # ``request_joke``) hits a disabled surface flag or content master.
    # The propose call returns HTTP 200 with this field set to
    # ``"surface_disabled"`` and no persisted activity row / no
    # ``activity.state`` WS envelope. ``None`` on every other code path
    # (real activities never carry a reason).
    reason: str | None = None


class ProposeRequest(BaseModel):
    """Body for ``POST /api/activities/propose``."""

    intent: str = Field(min_length=1)
    slot: str | None = None
    hour: int = Field(ge=0, le=23)
    seed: int = Field(ge=0)
    persona_id: str | None = None
    session_id: str | None = None
    context: dict[str, Any] | None = None
    # Step 23: optional "why this?" telemetry surfaced on the suggestion
    # card. ``trigger_phrase`` is the literal substring of the
    # transcript that fired the trigger that led to this propose call;
    # ``None`` when proposed manually (no trigger). ``persona_reasoning``
    # is a short rationale for the chosen persona — when caller supplies
    # one we persist it verbatim, otherwise we synthesise a default.
    # Step 23 iter-2: cap at 512 chars BEFORE persistence — the trigger
    # phrase is a literal substring of a child-spoken transcript, so an
    # uncapped 10K-char input would inflate every activity row + every
    # WS state envelope (and is a trivial abuse vector). 512 is well
    # north of any realistic spoken phrase.
    trigger_phrase: str | None = Field(default=None, max_length=512)
    persona_reasoning: str | None = Field(default=None, max_length=512)


class ApproveRequest(BaseModel):
    """Body for ``POST /api/activities/{id}/approve``."""

    child_ids: list[str] | None = None


class RegenerateRequest(BaseModel):
    """Body for ``POST /api/activities/{id}/regenerate``."""

    intent: str | None = None
    slot: str | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    seed: int | None = Field(default=None, ge=0)
    persona_id: str | None = None
    context: dict[str, Any] | None = None
    # Step 23: regenerate may inherit the source's "why this?" telemetry.
    # ``None`` falls through to the source row's value so the freshly-
    # proposed activity still has rationale to render. Same 512-char cap
    # as ``ProposeRequest`` (see comment above).
    trigger_phrase: str | None = Field(default=None, max_length=512)
    persona_reasoning: str | None = Field(default=None, max_length=512)


class DidntWorkRequest(BaseModel):
    """Body for ``POST /api/activities/{id}/didnt-work``."""

    reason: str | None = None


class AdvanceRequest(BaseModel):
    """Phase G G3: optional body for ``POST /api/activities/{id}/advance``.

    Linear advance steps (no ``choices`` on the current step) post no
    body or an empty body — both shapes resolve to ``choice_index=None``.
    Branching advance steps post ``{"choice_index": <int>}`` to identify
    which choice button the kid tapped. The handler validates against
    the persisted ``activity_steps.choices_json`` length on the current
    step and raises 400 with ``code=invalid_choice_index`` if the index
    is out of range, ``code=choice_required`` if the field is missing
    when required, and ``code=choice_not_allowed`` if the field is
    present when the current step has no choices.
    """

    model_config = ConfigDict(extra="forbid")

    choice_index: int | None = Field(default=None, ge=0)


def _ensure_session(conn: sqlite3.Connection, session_id: str | None) -> str:
    """Resolve or create a session id. Phase A keeps this trivial."""
    if session_id:
        row = conn.execute(
            "SELECT id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is not None:
            return session_id
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (session_id, _now_iso()),
            )
        return session_id
    new_id = str(uuid.uuid4())
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            (new_id, _now_iso()),
        )
    return new_id


def _resolve_default_child_ids(conn: sqlite3.Connection) -> list[str]:
    """Default child_ids when no caller-pinned set was passed.

    Returns every non-archived child profile in the household. The
    earlier "singleton only" heuristic returned ``[]`` whenever the
    household had more than one child — which silently dropped all
    per-child constraints (banned_themes, reading_level) for any
    family with siblings, and left every activity row's ``child_ids``
    as NULL so the "delete child while referenced" 409 could never
    fire. Multi-child UNION/MINIMUM aggregation is already supported
    by :func:`aggregate_child_constraints`, so attaching every child
    is the correct multi-child default; specific-child propose still
    works by passing ``context["child_ids"]``.
    """
    rows = conn.execute("SELECT id FROM children ORDER BY rowid").fetchall()
    return [str(r["id"]) for r in rows]


def _resolve_propose_child_ids(
    conn: sqlite3.Connection,
    context: dict[str, Any] | None,
) -> list[str]:
    """Pick the child_ids the propose flow should aggregate constraints from.

    Caller-supplied ``context["child_ids"]`` wins (multi-child propose
    can pass an explicit list); otherwise we fall back to every child
    in the household so the resolver sees the union of constraints.
    Empty list (legitimately empty DB) means no per-child constraints.
    """
    if context is not None:
        raw = context.get("child_ids")
        if isinstance(raw, list):
            cleaned = [str(c) for c in raw if isinstance(c, str) and c]
            if cleaned:
                return cleaned
    return _resolve_default_child_ids(conn)


def _activity_signature(conn: sqlite3.Connection, activity_id: str) -> str | None:
    """Read the signature from the persisted ``activities.summary`` JSON.

    Returns ``None`` if the row is missing, the JSON is malformed, or
    ``metadata.signature`` is absent (rows persisted before Phase D
    step 20). Callers MUST treat ``None`` as "skip the feedback write"
    rather than substituting an empty string — empty signatures don't
    match any candidate's hash and would just be dead rows.
    """
    row = conn.execute(
        "SELECT summary FROM activities WHERE id = ?",
        (activity_id,),
    ).fetchone()
    if row is None or not row["summary"]:
        return None
    try:
        payload = json.loads(row["summary"])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    sig = metadata.get("signature")
    if isinstance(sig, str) and sig:
        return sig
    return None


def _write_feedback(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    kind: str,
    reason: str | None = None,
    step_seq: int | None = None,
) -> None:
    """Insert a ``feedback`` row keyed by the activity's stored signature.

    Best-effort wrapper. If the activity row has no usable signature
    (pre-Phase-D-step-20 row, malformed summary, etc.) this no-ops so
    a parent's button click can't surface as a 500. Sqlite errors
    likewise log WARNING and swallow — feedback writes are an input
    to a *future* generator pick, not a load-bearing UX path.
    """
    signature = _activity_signature(conn, activity_id)
    if signature is None:
        _logger.info(
            "skipping feedback write for activity %s (kind=%s): no signature on row",
            activity_id,
            kind,
        )
        return
    try:
        with conn:
            conn.execute(
                "INSERT INTO feedback "
                "(id, activity_id, step_seq, kind, signature, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    activity_id,
                    step_seq,
                    kind,
                    signature,
                    reason,
                    _now_iso(),
                ),
            )
    except sqlite3.Error:
        _logger.warning(
            "feedback write failed for activity %s (kind=%s); skipping",
            activity_id,
            kind,
            exc_info=True,
        )


def _safe_update_parent_signal(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    signal: float,
    ended_at_step: int | None = None,
) -> None:
    """Wrap ``update_parent_signal`` so a labeled_events failure can't 500.

    The signal write is observability — losing it must not break the
    state-transition that the parent just clicked. Logs WARNING on any
    sqlite/IO error.
    """
    try:
        update_parent_signal(
            conn,
            activity_id=activity_id,
            signal=signal,
            ended_at_step=ended_at_step,
        )
    except Exception:  # noqa: BLE001 -- eval scaffold must never break the lifecycle
        _logger.warning(
            "labeled_events parent_signal=%s failed for activity %s; skipping",
            signal,
            activity_id,
            exc_info=True,
        )


def _current_step_seq(conn: sqlite3.Connection, activity_id: str) -> int | None:
    """Return the seq of the currently-active step, or None if none."""
    row = conn.execute(
        "SELECT seq FROM activity_steps WHERE activity_id = ? AND current = 1 LIMIT 1",
        (activity_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row["seq"])


def _fetch_activity_row(conn: sqlite3.Connection, activity_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM activities WHERE id = ?",
        (activity_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "activity_not_found", "id": activity_id},
        )
    return row


def _decode_choices_json(raw: Any) -> list[ChoiceOption] | None:
    """Phase G G3: parse a persisted ``choices_json`` cell into the
    runtime ``[{label, choice_index}]`` shape.

    Returns ``None`` for both NULL and empty-array inputs — the
    kiosk's ``step.choices`` check is nullish-only (``step.choices?.length``
    not legal here in TS, but logically equivalent), and an empty
    list is semantically "no choices" so we collapse the two cases
    to one wire shape. Malformed JSON is logged + dropped (returns
    ``None``) rather than raising — a corrupt cell on one step row
    must not break the whole activity GET.
    """
    if raw is None:
        return None
    try:
        decoded = json.loads(str(raw))
    except json.JSONDecodeError:
        _logger.warning("activity_steps.choices_json malformed; treating as no choices")
        return None
    if not isinstance(decoded, list) or not decoded:
        return None
    return [ChoiceOption(label=str(label), choice_index=idx) for idx, label in enumerate(decoded)]


def _decode_step_metadata_json(raw: Any) -> dict[str, Any] | None:
    """Phase K K13: parse a persisted ``activity_steps.metadata_json``
    cell into the wire-side dict shape.

    Returns ``None`` for both NULL and empty-object inputs — the kiosk
    treats both as "no per-step metadata" (the K12 StepCard reads keys
    defensively via optional chaining). Malformed JSON is logged +
    dropped (returns ``None``) rather than raising — a corrupt cell on
    one step row must not break the whole activity GET. Non-object
    payloads (e.g. an array accidentally written) are likewise dropped.
    """
    if raw is None:
        return None
    try:
        decoded = json.loads(str(raw))
    except json.JSONDecodeError:
        _logger.warning("activity_steps.metadata_json malformed; treating as no metadata")
        return None
    if not isinstance(decoded, dict) or not decoded:
        return None
    return {str(k): v for k, v in decoded.items()}


def _fetch_steps(conn: sqlite3.Connection, activity_id: str) -> list[ActivityStepResponse]:
    rows = conn.execute(
        "SELECT seq, body, sfx, expected_action, current, action_slot, "
        " choices_json, chosen_label, kind, metadata_json "
        "FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    return [
        ActivityStepResponse(
            seq=int(r["seq"]),
            body=str(r["body"]),
            sfx=r["sfx"],
            expected_action=r["expected_action"],
            current=bool(r["current"]),
            action_slot=r["action_slot"],
            choices=_decode_choices_json(r["choices_json"]),
            chosen_label=r["chosen_label"],
            kind=r["kind"],
            metadata=_decode_step_metadata_json(r["metadata_json"]),
        )
        for r in rows
    ]


def _render_template_plan_steps(
    template_id: str | None,
    slot_fills_raw: str | None,
) -> list[ActivityStepResponse] | None:
    """Phase G G2.5: render the full template step plan for preview.

    Returns the activity's full template steps mapped to response shape
    (rendered with the activity's persisted slot fills), with seq=1
    marked ``current=True``. Used by ``_row_to_response`` for activities
    in ``proposed`` / ``approved`` state so the parent dashboard's
    suggestion card can preview ALL steps before approval — restoring the
    pre-G2 review UX that was lost when ``_persist_activity`` switched
    from pre-seeding all 5 rows to lazy-inserting only ``steps[0]``.

    Returns ``None`` (graceful fallback to ``_fetch_steps``) when:
    - the activity has no ``template_id`` in its metadata envelope
    - the template can't be resolved (renamed / removed since propose)
    - ``slot_fills_json`` is malformed

    Activity-step rows in the DB remain authoritative for
    ``running``/``completed`` activities (they record the kid's actually-
    played path including ``chosen_label`` on choice points); this
    function is only consulted for the preview states.
    """
    if template_id is None:
        return None
    template = find_template_by_id(template_id)
    if template is None:
        return None

    fills: dict[str, str] = {}
    if slot_fills_raw:
        try:
            decoded = json.loads(slot_fills_raw)
            if isinstance(decoded, dict):
                fills = {str(k): str(v) for k, v in decoded.items()}
        except json.JSONDecodeError:
            return None

    rendered: list[ActivityStepResponse] = []
    for idx, step in enumerate(template.steps):
        body = render_with_slot_fills(step.text, fills)
        choices: list[ChoiceOption] | None = None
        if step.choices is not None:
            choices = [
                ChoiceOption(
                    label=render_with_slot_fills(label, fills),
                    choice_index=ci,
                )
                for ci, (label, _next) in enumerate(step.choices)
            ]
        rendered.append(
            ActivityStepResponse(
                seq=idx + 1,
                body=body,
                sfx=step.sfx,
                expected_action=step.expected_action,
                current=(idx == 0),
                action_slot=step.action_slot,
                choices=choices,
                chosen_label=None,
            )
        )
    return rendered


def _row_to_response(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> ActivityResponse:
    activity_id = str(row["id"])
    # NOTE: ``activities.summary`` is overloaded as a JSON envelope
    # (``{"title": ..., "metadata": ..., "template_id": ...}``). The
    # column is declared free-form by the migration; we parse it here
    # with a graceful fallback for plaintext rows. A future migration
    # may split this into dedicated columns; until then the write side
    # in :func:`_do_propose` mirrors this contract.
    summary_raw = row["summary"]
    metadata: dict[str, Any] = {}
    title: str | None = None
    if summary_raw:
        try:
            payload = json.loads(summary_raw)
            if isinstance(payload, dict):
                title = payload.get("title")
                metadata = payload.get("metadata") or {}
        except json.JSONDecodeError:
            title = summary_raw
    child_ids_raw = row["child_ids"]
    child_ids: list[str]
    if child_ids_raw:
        try:
            decoded = json.loads(child_ids_raw)
            child_ids = [str(c) for c in decoded] if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            child_ids = []
    else:
        child_ids = []

    toy_ids_raw = row["toy_ids"]
    toy_ids: list[str]
    if toy_ids_raw:
        try:
            decoded_toy = json.loads(toy_ids_raw)
            toy_ids = [str(t) for t in decoded_toy] if isinstance(decoded_toy, list) else []
        except json.JSONDecodeError:
            toy_ids = []
    else:
        toy_ids = []

    # Step 23: surface trigger_phrase + persona_reasoning at the top
    # level of the response so the parent UI's "why this?" panel can
    # render them without re-parsing ``metadata``. Both live inside the
    # summary envelope's ``metadata`` block for storage (no schema
    # migration); we surface them at the top level IN ADDITION TO
    # keeping them in metadata. The duplication is intentional — the
    # top-level fields are the wire contract, the metadata copy is the
    # source of truth on read-back from the persisted row.
    trigger_phrase: str | None = None
    persona_reasoning: str | None = None
    raw_trigger = metadata.get("trigger_phrase")
    if isinstance(raw_trigger, str) and raw_trigger:
        trigger_phrase = raw_trigger
    raw_reasoning = metadata.get("persona_reasoning")
    if isinstance(raw_reasoning, str) and raw_reasoning:
        persona_reasoning = raw_reasoning
    # Phase K K5: reconstruct the typed role-assignment list + cast
    # summary from the persisted metadata envelope BEFORE we strip the
    # K5 keys below. Defensive parsing so a malformed envelope (or a
    # pre-K5 activity row) cleanly falls through to empty values — the
    # wire schema's defaults handle both.
    roles_map: dict[str, RoleAssignment] = {}
    raw_role_records = metadata.get("role_assignments")
    if isinstance(raw_role_records, list):
        for entry in raw_role_records:
            if not isinstance(entry, dict):
                continue
            role_name_raw = entry.get("role_name")
            display_name_raw = entry.get("display_name")
            if not isinstance(role_name_raw, str) or not role_name_raw:
                continue
            if not isinstance(display_name_raw, str) or not display_name_raw:
                continue
            toy_id_raw = entry.get("toy_id")
            generic_raw = entry.get("generic_descriptor")
            roles_map[role_name_raw] = RoleAssignment(
                role_name=role_name_raw,
                toy_id=str(toy_id_raw) if isinstance(toy_id_raw, str) and toy_id_raw else None,
                generic_descriptor=(
                    str(generic_raw) if isinstance(generic_raw, str) and generic_raw else None
                ),
                display_name=display_name_raw,
            )
    cast_summary_raw = metadata.get("cast_summary")
    cast_summary = cast_summary_raw if isinstance(cast_summary_raw, str) else ""
    # Phase G G2 (iter-2): ``slot_fills`` is persistence-only telemetry
    # (the lazy advance handler in G3 reads it from
    # ``activities.slot_fills_json``, NOT from the wire) — no UI consumes
    # the raw slot map. Strip it here so neither the REST GET nor the WS
    # ``activity.state`` envelope (which derives from this same response
    # via ``_emit_state``) leaks it. ``room`` can carry parent-authored
    # names (e.g. "Mom's office"); future slots like ``child_name`` would
    # auto-leak to the child kiosk without code change. Stripping at this
    # one chokepoint covers both surfaces — the model_dump in
    # ``_emit_state`` sees the already-cleaned metadata, and there's no
    # second read path that bypasses ``_row_to_response``.
    # Phase K K5: ``role_assignments`` + ``cast_summary`` are persisted
    # under ``metadata`` so a single envelope round-trips both the
    # legacy fields and the new role state. Surfaced at the top level
    # on the response via the dedicated ``roles`` / ``cast_summary``
    # fields; stripped from ``metadata`` here so we don't duplicate the
    # data on the wire (and so the WS envelope doesn't carry two copies
    # of the role display names — one structured, one as the
    # comma-separated string).
    metadata = {
        k: v
        for k, v in metadata.items()
        if k not in ("slot_fills", "role_assignments", "cast_summary")
    }

    # Phase G G2.5: for proposed / approved activities, render the full
    # template step plan so the parent dashboard's suggestion card can
    # preview all steps before approval. Pre-G2 the propose response
    # always carried 5 rows (pre-seeded); G2's lazy-insert dropped that
    # to 1, breaking the parent's review UX. Once the activity is
    # running/completed, fall back to activity_steps (the kid's actually-
    # played path is the source of truth).
    state = str(row["state"])
    template_id_for_plan: str | None = None
    if summary_raw and state in (STATE_PROPOSED, STATE_APPROVED):
        try:
            payload = json.loads(summary_raw)
            if isinstance(payload, dict):
                tid = payload.get("template_id")
                if isinstance(tid, str) and tid:
                    template_id_for_plan = tid
        except json.JSONDecodeError:
            pass

    steps: list[ActivityStepResponse] | None = None
    if template_id_for_plan is not None:
        slot_fills_raw = row["slot_fills_json"] if "slot_fills_json" in row.keys() else None
        steps = _render_template_plan_steps(template_id_for_plan, slot_fills_raw)
    if steps is None:
        steps = _fetch_steps(conn, activity_id)

    return ActivityResponse(
        id=activity_id,
        state=state,
        version=int(row["version"]),
        title=title,
        summary=summary_raw if not title else None,
        persona_id=row["persona_id"],
        intent_source=row["intent_source"],
        child_ids=child_ids,
        toy_ids=toy_ids,
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        steps=steps,
        metadata=metadata,
        trigger_phrase=trigger_phrase,
        persona_reasoning=persona_reasoning,
        roles=roles_map,
        cast_summary=cast_summary,
    )


def _emit_state(pubsub: PubSub, response: ActivityResponse) -> None:
    """Publish the activity-state envelope, stripping parent-only fields.

    Step 23 iter-2 (M1): the ``activity.state`` topic is in the child
    kiosk's allow-list (see ``toybox.ws.server._CHILD_TOPICS``), so the
    envelope payload crosses a privacy boundary. ``trigger_phrase`` is a
    literal substring of a child-spoken transcript (PII) and
    ``persona_reasoning`` is a parent-facing rationale string; neither
    belongs on the kid panel even though the kid UI never renders them.
    We strip both from the WS payload while leaving them on the REST
    response (the GET path is parent-only, so unaffected).
    """
    payload = response.model_dump(mode="json")
    payload.pop("trigger_phrase", None)
    payload.pop("persona_reasoning", None)
    # The same two fields are also persisted into ``metadata`` as Step 23
    # "why this?" telemetry (see _propose's metadata write below). Popping
    # only the top-level surface left the metadata copies on the wire,
    # leaking PII to every subscriber including the child-scope kiosk.
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("trigger_phrase", None)
        metadata.pop("persona_reasoning", None)
    pubsub.publish(
        build_envelope(
            topic=Topic.activity_state,
            payload=payload,
        )
    )


def _persist_activity(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    session_id: str,
    persona_id: str | None,
    intent_source: str,
    summary_payload: dict[str, Any],
    steps: list[dict[str, Any]],
    state: str,
    toy_ids: Sequence[str] = (),
    slot_fills: dict[str, str] | None = None,
) -> None:
    """Insert one ``activities`` row plus ONLY the first step row.

    Phase G G2: this is the lazy-insertion path. ``steps`` is the
    full list of template steps (so the propose flow can keep
    delivering byte-identical Activity envelopes), but only
    ``steps[0]`` is actually written to ``activity_steps``. The row
    is marked ``current=1`` so the advance handler can flip it to
    the next template step on the first ``/advance`` POST. Subsequent
    template steps are inserted lazily by G3's advance handler,
    rendered with the persisted ``activities.slot_fills_json`` so
    the kid's experience stays coherent (same ``{toy}``, same
    ``{adjective}``, same ``{room}`` across all steps).

    The new ``slot_fills`` argument carries the resolved slot map
    (slot-name → value, e.g.
    ``{"toy": "Penguin", "room": "kitchen", "adjective": "sparkly"}``)
    that the generator computed at creation time. Encoded with
    ``sort_keys=True`` so byte-identity holds across reads. Defaults
    to ``None`` → empty ``'{}'`` for callers that don't have a
    resolver in the loop (smoke / fixture / test paths) — matches
    the migration default for in-flight pre-G2 activities.
    """
    summary_blob = json.dumps(summary_payload, sort_keys=True)
    # Phase G: persist the resolved slot map. ``sort_keys=True`` is
    # load-bearing — multiple generator runs with the same template
    # + seed should produce byte-identical ``slot_fills_json`` so a
    # downstream byte-comparison test (e.g. activity-id determinism)
    # does not flap on dict-iteration order.
    slot_fills_blob = json.dumps(slot_fills or {}, sort_keys=True)
    created_at = _now_iso()
    toy_ids_blob = json.dumps(list(toy_ids)) if toy_ids else None
    with conn:
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, persona_id, child_ids, room_ids, "
            " toy_ids, intent_source, created_at, started_at, ended_at, slot_fills_json) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?)",
            (
                activity_id,
                session_id,
                state,
                summary_blob,
                persona_id,
                None,
                toy_ids_blob,
                intent_source,
                created_at,
                slot_fills_blob,
            ),
        )
        # Phase G G2 — lazy step insertion. We INSERT only ``steps[0]``;
        # the advance handler in G3 inserts subsequent steps as the
        # kid progresses. The first step is marked ``current=1`` so
        # the kiosk has something to render the moment the activity
        # is approved. ``choices_json`` and ``step_template_id`` are
        # populated when the template carries them on this step.
        if steps:
            first = steps[0]
            choices_rendered = first.get("choices_rendered")
            choices_blob: str | None
            if choices_rendered is None:
                choices_blob = None
            else:
                # Always encode as a JSON list of strings; sort_keys
                # is irrelevant for arrays but kept off so tests can
                # pin the exact label ordering the kiosk renders.
                choices_blob = json.dumps(list(choices_rendered))
            # Phase K K13: encode per-step metadata blob. None /
            # empty-dict both serialise to NULL so the wire stays
            # symmetric with the read path's "no metadata" shape.
            raw_step_metadata = first.get("metadata")
            metadata_blob: str | None
            if isinstance(raw_step_metadata, dict) and raw_step_metadata:
                metadata_blob = json.dumps(raw_step_metadata, sort_keys=True)
            else:
                metadata_blob = None
            conn.execute(
                "INSERT INTO activity_steps "
                "(id, activity_id, seq, body, sfx, expected_action, current, action_slot, "
                " choices_json, step_template_id, kind, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    activity_id,
                    first["seq"],
                    first["body"],
                    first.get("sfx"),
                    first.get("expected_action"),
                    # Phase G: the inserted row is the kid's starting
                    # step. Mark it ``current=1`` regardless of what
                    # the caller passed — under lazy insertion there
                    # is exactly ONE row at creation and it must be
                    # current for the kiosk to render anything.
                    1,
                    # Phase F Step F6: per-step action slot. None for
                    # legacy callers / templates that don't set it.
                    first.get("action_slot"),
                    choices_blob,
                    first.get("step_id"),
                    # Phase K K13: step-kind discriminator + arbitrary
                    # per-step metadata. ``None`` on template-driven
                    # rows (current default = "text" implied).
                    first.get("kind"),
                    metadata_blob,
                ),
            )


def _attempt_transition(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    expected_version: int,
    new_state: str,
    additional_sets: tuple[tuple[str, Any], ...] = (),
) -> tuple[bool, sqlite3.Row]:
    """Atomically transition state + bump version when the version matches.

    Returns ``(ok, latest_row)``. When ``ok`` is ``False`` the latest
    row is the post-failure read so the caller can craft a 409.
    """
    set_clauses = ["state = ?", "version = version + 1"]
    params: list[Any] = [new_state]
    for column, value in additional_sets:
        set_clauses.append(f"{column} = ?")
        params.append(value)
    sql = f"UPDATE activities SET {', '.join(set_clauses)} WHERE id = ? AND version = ?"
    params.extend([activity_id, expected_version])
    with conn:
        cur = conn.execute(sql, params)
    ok = cur.rowcount == 1
    row = _fetch_activity_row(conn, activity_id)
    return ok, row


def _enforce_transition(current_state: str, target: str) -> None:
    allowed = _VALID_TRANSITIONS.get(current_state, frozenset())
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_transition",
                "current_state": current_state,
                "target_state": target,
            },
        )


def _build_persona_reasoning(
    *,
    caller_supplied: str | None,
    intent: str,
    persona_meta: dict[str, Any] | None,
) -> str:
    """Compose a short rationale for the chosen persona.

    Step 23 contract: the suggestion card's "why this?" panel needs a
    non-empty rationale string. Priority order:

    1. Caller-supplied ``persona_reasoning`` (e.g. listening passes a
       string explaining the match) — wins verbatim, after stripping.
    2. Synthesised default of the form
       ``"<persona display_name> picked for <intent>"`` when a library
       persona was selected.
    3. Final fallback ``"matched on intent"`` so callers without a
       persona still see something — better than an empty panel.
    """
    if caller_supplied is not None:
        stripped = caller_supplied.strip()
        if stripped:
            return stripped
    if persona_meta is not None:
        display_name = persona_meta.get("display_name")
        if isinstance(display_name, str) and display_name:
            return f"{display_name} picked for {intent}"
    return "matched on intent"


def _load_persona_role_weights(
    conn: sqlite3.Connection, persona_id: str | None
) -> dict[str, float]:
    """Phase K K5: fetch ``personas.role_weights`` JSON for the propose path.

    Returns an empty mapping when:

    * ``persona_id`` is ``None`` (no persona pinned and the library is empty).
    * The persona row is absent (caller pinned an id that doesn't exist —
      treated as "no preference" rather than raising; matches the broader
      "propose never 500s on missing data" contract).
    * The persisted JSON is NULL, empty, or malformed.

    Mapping keys are role-name strings (lowercase snake_case
    :class:`~toybox.activities.roles.Role` values); unknown keys are NOT
    filtered here — the K4 picker silently ignores them, and a defensive
    filter would mask a stale schema-validation regression.
    """
    if persona_id is None:
        return {}
    try:
        row = conn.execute(
            "SELECT role_weights FROM personas WHERE id = ?",
            (persona_id,),
        ).fetchone()
    except sqlite3.Error:
        _logger.warning(
            "role_weights read failed for persona %r; treating as uniform",
            persona_id,
            exc_info=True,
        )
        return {}
    if row is None:
        return {}
    raw = row["role_weights"]
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _logger.warning(
            "role_weights JSON malformed for persona %r; treating as uniform",
            persona_id,
        )
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, bool):
            # ``bool`` is a subtype of ``int``; reject explicitly so a
            # ``True``/``False`` weight doesn't masquerade as 1.0/0.0.
            continue
        if isinstance(value, int | float):
            out[key] = float(value)
    return out


@dataclass(frozen=True, slots=True)
class _PersonaForRoleSlots:
    """Minimal :class:`~toybox.activities.content_resolver._PersonaLike`
    implementation used by :func:`_do_propose` to call
    :func:`toybox.activities.content_resolver.resolve_role_slots`.

    The picker reads only ``id`` + ``role_weights``; using a frozen
    dataclass here (instead of constructing a Pydantic
    :class:`~toybox.personas.models.RoleWeights`) keeps the K5 wire-up
    isolated from the loader's strict validation path. K4's picker is
    already defensive against unknown keys / non-numeric values.
    """

    id: str
    role_weights: dict[str, float]


def _build_role_assignments(
    role_slot_result: dict[str, Any] | None,
) -> tuple[dict[str, str], list[dict[str, Any]], str]:
    """Phase K K5: translate :func:`resolve_role_slots` output for persistence.

    Returns ``(slot_fills_overlay, role_records, cast_summary)`` where:

    * ``slot_fills_overlay`` — dict keyed by lowercase role-name, value
      is the resolved ``display_name``. Merged into the existing
      ``slot_fills`` map so :func:`render_with_slot_fills` substitutes
      ``{role_name}`` placeholders with the right string.
    * ``role_records`` — list of plain-dict ``RoleAssignment`` rows the
      caller stashes onto the activity's metadata envelope. Each carries
      ``role_name`` (str), ``toy_id`` (str | None), ``generic_descriptor``
      (str | None), and ``display_name`` (str). Exactly one of ``toy_id``
      and ``generic_descriptor`` is set — the discriminator the wire
      :class:`RoleAssignment` model enforces.
    * ``cast_summary`` — comma-separated
      ``"<RoleDisplay>: <DisplayName>"`` string sorted by role-name
      (``role.value``) for determinism. Empty when no roles resolved.

    Returns three empty values when ``role_slot_result`` is ``None`` or
    empty (template has no roles, or eligibility gate rejected it).
    """
    slot_fills_overlay: dict[str, str] = {}
    role_records: list[dict[str, Any]] = []
    if not role_slot_result:
        return slot_fills_overlay, role_records, ""

    # Sort by role-name for deterministic ordering on the wire.
    for role_name in sorted(role_slot_result.keys()):
        value = role_slot_result[role_name]
        if isinstance(value, GenericDescriptor):
            display_name = value.display_name
            record = {
                "role_name": role_name,
                "toy_id": None,
                "generic_descriptor": display_name,
                "display_name": display_name,
            }
        else:
            # ``ResolvedToy`` — duck-typed read so a future shape change
            # doesn't require an import cycle here.
            display_name = str(value.display_name)
            toy_id = str(value.id)
            record = {
                "role_name": role_name,
                "toy_id": toy_id,
                "generic_descriptor": None,
                "display_name": display_name,
            }
        slot_fills_overlay[role_name] = display_name
        role_records.append(record)

    summary_pieces: list[str] = []
    for record in role_records:
        role_name = str(record["role_name"])
        try:
            role_enum = Role(role_name)
            display_label = ROLE_DISPLAY_NAMES[role_enum]
        except (ValueError, KeyError):
            # An unknown role-name string would mean K4 returned a key
            # outside the taxonomy — taxonomy-completeness tests gate
            # that, but defend the rendering anyway with the raw key.
            display_label = role_name
        summary_pieces.append(f"{display_label}: {record['display_name']}")
    cast_summary = ", ".join(summary_pieces)
    return slot_fills_overlay, role_records, cast_summary


def _pick_random_library_persona(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Pick a random ``source='library'`` persona for variety on propose.

    Returns a small dict (id, display_name, archetype, avatar_image_path)
    or ``None`` when the personas table has no library rows (e.g. fresh
    DB before the loader ran). Used to drive avatar variety on the
    kiosk; activity content is still template-driven.
    """
    row = conn.execute(
        "SELECT id, display_name, archetype, avatar_image_path "
        "FROM personas WHERE source = 'library' "
        "ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "display_name": str(row["display_name"]),
        "archetype": row["archetype"],
        "avatar_image_path": row["avatar_image_path"],
    }


def _resolve_local_dispatch(dispatch: Any) -> Any:
    """Probe the local runtime and degrade to Claude when it's not ready.

    Phase E Step 28 partial: when ``TOYBOX_GENERATOR_ADAPTER=local`` we
    call :func:`toybox.ai.capability.is_local_capable` to check the
    runtime + per-adapter breaker. On ``(True, None)`` we keep the
    local dispatch (which routes through
    :class:`toybox.ai.local.LocalActivityGenerator`, currently a
    NotImplementedError-raising stub pointing at Step 26 / issue #38).
    On ``(False, reason)`` we degrade to ``ADAPTER_CLAUDE`` with the
    same ``mode``, log the reason at WARNING level so an operator can
    grep it, and let the existing Claude dispatch handle the request.
    """
    # Late import: keep ``is_local_capable`` (and its urllib + breaker
    # plumbing) out of the v1 import surface for operators who never
    # flip the env var.
    from ..activities.generator import ADAPTER_CLAUDE as _ADAPTER_CLAUDE  # noqa: PLC0415
    from ..activities.generator import GeneratorDispatch  # noqa: PLC0415
    from ..ai.capability import is_local_capable  # noqa: PLC0415

    capable, reason = asyncio.run(is_local_capable())
    if capable:
        return dispatch
    _logger.warning(
        "local adapter not capable (%s); falling back to claude path",
        reason,
        extra={"capability_reason": reason, "adapter": "local"},
    )
    return GeneratorDispatch(adapter=_ADAPTER_CLAUDE, mode=dispatch.mode)


def _dispatch_local(mode: str) -> None:
    """Route a capability-green local dispatch through LocalActivityGenerator.

    Pre-Step-26 this always raises :class:`NotImplementedError` -- the
    point is to surface the carve-out hint via the local adapter's
    own message (so a reader of the traceback sees Step 26 / issue
    #38) rather than via a hard-coded string in this file. Step 26
    swaps the body to drive
    :meth:`toybox.ai.local.LocalActivityGenerator.generate_activity_loop`
    against a real local runtime + a real ToolDispatcher.
    """
    # Late imports keep the local module + tool plumbing out of the
    # v1 import surface for operators who never flip the env var.
    from typing import cast as _cast  # noqa: PLC0415

    from ..ai.local import LocalActivityGenerator  # noqa: PLC0415
    from ..ai.tools import ToolDispatcher  # noqa: PLC0415

    generator = LocalActivityGenerator()
    # Drive the adapter through the same Protocol method the loop path
    # would once Step 26 ships. The current implementation raises
    # NotImplementedError; that exception propagates up through the
    # route handler and Starlette's TestClient surfaces it to the test
    # (and an operator sees it as a 500 with the Step 26 hint baked
    # into the traceback rather than an opaque generic message).
    #
    # ``tools`` is set to ``None`` (cast for mypy) because the adapter
    # raises before reading the second arg; Step 26 will replace this
    # with a real ToolDispatcher backed by a per-request connection
    # factory, exactly as :func:`_run_loop_generation` does for Claude.
    if mode == MODE_LOOP:
        asyncio.run(generator.generate_activity_loop(object(), _cast(ToolDispatcher, None)))
    else:
        asyncio.run(generator.generate_activity(object()))


def _run_loop_generation(
    body: ProposeRequest,
    conn: sqlite3.Connection,
    *,
    effective_persona_id: str | None,
    resolved_toys: list[ResolvedToy],
    resolved_rooms: list[ResolvedRoom],
) -> tuple[Any, list[dict[str, Any]]]:
    """Drive ``ClaudeActivityGenerator.generate_activity_loop`` end-to-end.

    Returns ``(activity, tool_calls)``. The propose path persists both
    onto the labeled_events row.

    Late imports (asyncio, the adapter, AnthropicClient, the OAuth
    loader) keep the v1 ``claude+single`` path's import surface
    untouched — those modules only matter when an operator opts in via
    ``TOYBOX_GENERATOR_MODE=loop``.
    """
    from ..ai.adapters import ClaudeActivityGenerator  # noqa: PLC0415
    from ..ai.adapters.claude import ClaudeAdapterContext  # noqa: PLC0415
    from ..ai.client import AnthropicClient, StubClient  # noqa: PLC0415
    from ..ai.oauth import load_token  # noqa: PLC0415
    from ..ai.tools import ToolContext, ToolDispatcher  # noqa: PLC0415

    token = load_token()
    client: Any
    if token is None:
        # Loop mode requires Claude. Without a token we can't ship a
        # loop-mode response; fall back to a deterministic stub so
        # tests can drive the path without OAuth. Production callers
        # without a token won't hit this branch because the capability
        # gate filters at the listening-mode layer.
        client = StubClient()
    else:
        client = AnthropicClient(token)

    db_path = resolve_db_path()

    def _connection_factory() -> sqlite3.Connection:
        return connect(db_path, check_same_thread=False)

    tool_ctx = ToolContext(
        connection_factory=_connection_factory,
        activity_id=None,
        child_id=None,
        session_id=body.session_id,
    )
    tools = ToolDispatcher(tool_ctx)

    system_prompt = _build_loop_system_prompt(
        intent=body.intent,
        slot=body.slot,
        persona_id=effective_persona_id,
        toys=resolved_toys,
        rooms=resolved_rooms,
    )
    user_prompt = json.dumps(
        {
            "intent": body.intent,
            "slot": body.slot,
            "hour": body.hour,
            "seed": body.seed,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    adapter_ctx = ClaudeAdapterContext(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    adapter = ClaudeActivityGenerator(client)

    activity = asyncio.run(adapter.generate_activity_loop(adapter_ctx, tools))
    return activity, adapter.tool_calls


def _build_loop_system_prompt(
    *,
    intent: str,
    slot: str | None,
    persona_id: str | None,
    toys: list[ResolvedToy],
    rooms: list[ResolvedRoom],
) -> str:
    """Compose a minimal system prompt for the loop-mode adapter.

    Kept short on purpose — the loop's system prompt is augmented at
    the adapter layer with the tool catalog and the JSON schema
    instruction. Catalog content (toys, rooms) is offered in summary
    so the model knows what's available before it starts calling
    tools to look up specifics.
    """
    parts = [
        "You generate a single 5-step play activity for a young child.",
        f"Intent: {intent}",
    ]
    if slot is not None:
        parts.append(f"Slot: {slot}")
    if persona_id is not None:
        parts.append(f"Persona: {persona_id}")
    if toys:
        parts.append("Toys (most recent first): " + ", ".join(t.display_name for t in toys[:8]))
    if rooms:
        parts.append("Rooms: " + ", ".join(r.display_name for r in rooms[:6]))
    parts.append(
        "Reply with EXACTLY one Activity JSON object "
        "({id, template_id, title, steps:[5 items], version, metadata}) "
        "and nothing else when you are done. "
        'Each step MUST include an "action_slot" key with one of '
        '"idle", "pointing", "looking", "jumping", "cheering", '
        '"thinking", "waving", "running", "sleeping", "confused" — '
        'pick the slot that best matches the step\'s verb (default "idle").'
    )
    return "\n".join(parts)


# Phase K K13: standalone-intent surface gating.
#
# The two intents below produce single-step corpus-driven activities
# (song or joke) rather than the template-driven 5-step plan that
# `request_play` / `boredom` / `request_story` / `request_activity`
# yield. Surfaces are gated on (content_master AND play_standalone_enabled);
# when either flag is OFF the propose call returns HTTP 200 with a
# synthetic dismissed body (no DB row, no `activity.state` WS envelope).
# Plan §7 pins the exact body shape.
_STANDALONE_SONG_INTENT = "request_song"
_STANDALONE_JOKE_INTENT = "request_joke"
_STANDALONE_INTENTS: frozenset[str] = frozenset({_STANDALONE_SONG_INTENT, _STANDALONE_JOKE_INTENT})

# The kiosk's K12 SongPlayer falls back to this static-mount URL pattern
# when ``audio_url`` is absent on a song step's metadata. K13's propose
# path emits ``audio_url`` directly using this prefix so the kiosk
# doesn't have to special-case the fallback — but the prefix lives in
# ONE place so a future mount-path move (e.g. CDN cutover) is a single
# edit (code-quality.md §2).
_SONG_AUDIO_URL_PREFIX = "/api/static/songs/audio"


def _standalone_surface_enabled(conn: sqlite3.Connection, intent: str) -> bool:
    """Return True when (content_master AND play_standalone_enabled).

    ``intent`` must be one of :data:`_STANDALONE_INTENTS`; passing any
    other value raises ``KeyError`` (defense — the caller must gate
    first).
    """
    if intent == _STANDALONE_SONG_INTENT:
        content_master_on = songs_enabled.get(conn)
    elif intent == _STANDALONE_JOKE_INTENT:
        content_master_on = jokes_enabled.get(conn)
    else:
        raise KeyError(f"non-standalone intent {intent!r}")
    return content_master_on and play_standalone_enabled.get(conn)


def _embedded_surface_enabled(conn: sqlite3.Connection, kind: str) -> bool:
    """Phase K K14: return True when (content_master AND play_embedded_enabled).

    ``kind`` MUST be ``"song"`` or ``"joke"``; any other value raises
    ``KeyError`` to surface caller bugs rather than silently degrading.
    Mirrors :func:`_standalone_surface_enabled`'s shape so a future
    consolidation (one gate-resolver per surface kind) drops in cleanly.
    """
    if kind == "song":
        content_master_on = songs_enabled.get(conn)
    elif kind == "joke":
        content_master_on = jokes_enabled.get(conn)
    else:
        raise KeyError(f"unknown interjection kind {kind!r}")
    return content_master_on and play_embedded_enabled.get(conn)


def _endings_surface_enabled(conn: sqlite3.Connection, kind: str) -> bool:
    """Phase K K14: return True when (content_master AND play_endings_enabled).

    Same shape as :func:`_embedded_surface_enabled`. Kept as separate
    functions (rather than one parametric helper) so each surface flag
    is one grep target — code-quality §1 grep-all-consumers discipline.
    """
    if kind == "song":
        content_master_on = songs_enabled.get(conn)
    elif kind == "joke":
        content_master_on = jokes_enabled.get(conn)
    else:
        raise KeyError(f"unknown interjection kind {kind!r}")
    return content_master_on and play_endings_enabled.get(conn)


def _build_ending_row(
    conn: sqlite3.Connection,
    *,
    template: Any,
    seed: int,
    persona_id: str | None,
    slot_fills: dict[str, str],
    new_seq: int,
) -> dict[str, Any] | None:
    """Phase K K14 Surface E: pick + build the persisted-row dict for an
    activity's ending interjection.

    Called at advance-time from :func:`post_advance`'s terminal branches
    when the kid has cleared the last renderable template step. Mirrors
    :func:`_pick_embedded_corpus_step`'s shape: a pure function that
    returns the :func:`build_interjection_step` dict (or ``None`` when
    the gate is off / no entry matches / the template carries no
    ending). The caller INSERTs the row via
    :func:`_insert_interjection_step_row` inside the version-bumped
    transaction; lazy insertion at advance-time matches G2's
    "one row per visible step" persistence pattern.

    Why lazy: eagerly INSERTing the ending at propose time (with
    ``seq = last_template_seq + 1``, ``current = 0``) is incompatible
    with G2's single-step persistence: ``_persist_activity`` only writes
    seq=1, so an eager ending visible to the pre-G2 / legacy-linear
    branch (``current_index + 1 < len(steps)``) makes the FIRST advance
    promote directly to seq=4, jumping the kid past template steps 2
    and 3 entirely. The K14 review (issue #127) caught this.

    The corpus pick is deterministic: ``pick_song`` / ``pick_joke``
    filter on ``theme = recommended_themes[0]`` (the strongest theme
    per phase-k-plan §6 K14 step) + ``persona_compat`` + (for songs)
    ``require_audio=True``. ``seed`` is the deterministic value the
    caller derives from ``(activity_id, new_seq)`` — same shape as the
    embedded picker so replay + recast stability survive.

    Returns:
        The :func:`build_interjection_step` dict on success, or
        ``None`` when:

        * ``template.ending_step`` is ``None``.
        * ``(<content_master> AND play_endings_enabled)`` is False for
          the ending's kind.
        * ``template.recommended_themes`` is empty (no theme to filter
          on — the K3 validator already gates this for ``auto: true``
          steps, but is silent on ``ending_step`` alone because Phase G
          templates without theme tags can still set an ending; we
          degrade silently rather than crash).
        * The corpus pick returns ``None`` (no matching entry — fresh
          install before audio rendering, or persona has no compatible
          entries). Logged at INFO so an operator can grep occurrences.
    """
    if template is None or template.ending_step is None:
        return None
    ending = template.ending_step
    kind = ending.kind  # "song" | "joke"
    if not _endings_surface_enabled(conn, kind):
        return None
    if not template.recommended_themes:
        _logger.info(
            "ending step for template %r requested but recommended_themes is "
            "empty; skipping ending pick (no row built)",
            template.id,
        )
        return None
    theme = template.recommended_themes[0]

    corpus_entry: Joke | Song | None
    if kind == "song":
        corpus_entry = pick_song(
            seed=seed,
            persona_id=persona_id,
            theme=theme,
            require_audio=True,
        )
    else:  # joke
        corpus_entry = pick_joke(
            seed=seed,
            persona_id=persona_id,
            theme=theme,
        )
    if corpus_entry is None:
        _logger.info(
            "ending step for template %r kind=%s theme=%s found no corpus entry; "
            "skipping (no row built)",
            template.id,
            kind,
            theme.value,
        )
        return None

    # Toy display name for the joke {toy} placeholder — best-effort via
    # the catalog resolver. Failure degrades to no-toy form (same as
    # the K13 standalone + K14 embedded paths).
    toy_display_name: str | None = None
    if kind == "joke":
        try:
            toys = resolve_toys(conn)
        except sqlite3.Error:
            _logger.warning(
                "ending pick: resolve_toys failed; joke degrades to no-toy form",
                exc_info=True,
            )
            toys = []
        if toys:
            toy_display_name = toys[0].display_name

    return build_interjection_step(
        interjection=InterjectionKind.ending,
        corpus_entry=corpus_entry,
        slot_fills=slot_fills,
        seq=new_seq,
        toy_display_name=toy_display_name,
    )


def _dismissed_surface_disabled_response() -> ActivityResponse:
    """Build the synthetic dismissed-path response per plan §7.

    Returns an ``ActivityResponse`` with ``state="dismissed"``,
    ``reason="surface_disabled"``, a fresh UUIDv4 id, version=1, and
    enough other fields populated to satisfy the wire model. No DB row
    is persisted; no ``activity.state`` envelope is emitted. The
    response stays a real ``ActivityResponse`` (not a sibling type) so
    the parent UI's existing suggestion-card branch can read
    ``state==="dismissed"`` and surface the "couldn't propose right now"
    flavor; the ``reason`` field gives the parent UI an explicit
    discriminator for the surface-flag case.
    """
    return ActivityResponse(
        id=str(uuid.uuid4()),
        state=STATE_DISMISSED,
        version=1,
        title=None,
        summary=None,
        persona_id=None,
        intent_source=None,
        child_ids=[],
        toy_ids=[],
        created_at=_now_iso(),
        started_at=None,
        ended_at=None,
        steps=[],
        metadata={},
        trigger_phrase=None,
        persona_reasoning=None,
        roles={},
        cast_summary="",
        reason="surface_disabled",
    )


def _do_propose_standalone(
    body: ProposeRequest,
    conn: sqlite3.Connection,
    pubsub: PubSub,
) -> ActivityResponse:
    """Phase K K13: propose flow for the standalone song / joke intents.

    Bypasses the template generator (Activity model requires 3+ steps)
    and produces a single-step activity directly from the corpus.
    Persists + emits + returns like the template path, but step.kind
    is ``"song"`` / ``"joke"`` and step.metadata carries the per-kind
    payload the K12 kiosk renders (audio_url + song_id for songs;
    punchline + joke_id for jokes).
    """
    # Persona pick mirrors the template flow so the kiosk avatar still
    # varies across propose calls. Falls through to no persona when the
    # library is empty.
    effective_persona_id = body.persona_id
    persona_meta: dict[str, Any] | None = None
    if effective_persona_id is None:
        picked = _pick_random_library_persona(conn)
        if picked is not None:
            effective_persona_id = picked["id"]
            persona_meta = picked

    # Catalog toys — only needed by the joke path for {toy} substitution.
    # Best-effort: a corrupt toy row mustn't break propose, so we degrade
    # to no-toy form rather than 500.
    resolved_toys: list[ResolvedToy] = []
    try:
        resolved_toys = resolve_toys(conn)
    except sqlite3.Error:
        _logger.warning(
            "content_resolver.resolve_toys failed on standalone propose; "
            "joke substitution will use no-toy form",
            exc_info=True,
        )

    # Pick the corpus entry. Both pickers honour persona_compat + age_band
    # filters; we don't pin an age band here (None = any). The seed
    # comes from the ProposeRequest, so the same trigger phrase at the
    # same seed always picks the same song/joke (deterministic for
    # tests + telemetry).
    activity_id = str(uuid.uuid4())
    title: str
    step_kind: str
    step_body: str
    step_metadata: dict[str, Any]

    if body.intent == _STANDALONE_SONG_INTENT:
        # require_audio=True — the kiosk would 404 on a corpus entry
        # whose .mp3 hasn't been rendered yet, and the standalone
        # surface is the kid's only path to the song (no template
        # context to fall through to). Tests stub a renderable corpus.
        song = pick_song(
            seed=body.seed,
            persona_id=effective_persona_id,
            require_audio=True,
        )
        if song is None:
            # No renderable song matches the filter (fresh install
            # before audio rendered, or persona has no compatible
            # entries). Treat as a soft dismiss — reuse the
            # surface-disabled body so the kid-voice flow is uniform.
            # The INFO log below is the operator's only signal that
            # the cause was corpus-empty rather than a parent toggle.
            _logger.info(
                "standalone request_song found no audio-renderable corpus entry "
                "for persona=%r seed=%d; returning dismissed",
                effective_persona_id,
                body.seed,
            )
            return _dismissed_surface_disabled_response()
        title = f"Sing a song: {song.title}"
        step_kind = "song"
        step_body = song.title
        step_metadata = {
            "song_id": song.id,
            "audio_url": f"{_SONG_AUDIO_URL_PREFIX}/{song.id}.mp3",
        }
    else:  # request_joke
        joke = pick_joke(
            seed=body.seed,
            persona_id=effective_persona_id,
        )
        if joke is None:
            _logger.info(
                "standalone request_joke found no corpus entry for "
                "persona=%r seed=%d; returning dismissed",
                effective_persona_id,
                body.seed,
            )
            return _dismissed_surface_disabled_response()
        # Optional toy substitution: if the joke has {toy} placeholder
        # AND we have at least one toy in the catalog, pick the first
        # for deterministic shape. apply_toy_substitution handles all
        # four cases (slot-yes/no × toy-yes/no).
        toy_display = resolved_toys[0].display_name if resolved_toys else None
        setup, punchline = apply_toy_substitution(joke, toy_display)
        title = "Tell me a joke"
        step_kind = "joke"
        step_body = setup
        step_metadata = {
            "joke_id": joke.id,
            "punchline": punchline,
        }

    # Persist. Reuse the canonical _persist_activity helper so the
    # `activities` row + first `activity_steps` row land identically to
    # the template path. step_metadata is round-tripped via the new K13
    # ``metadata_json`` column.
    session_id = _ensure_session(conn, body.session_id)

    # Evict oldest first so the configured cap holds for the new row,
    # matching the template path's behaviour.
    evicted_ids = evict_oldest_for_capacity(conn, cap=play_target_depth.get(conn))
    for eid in evicted_ids:
        evicted_row = _fetch_activity_row(conn, eid)
        _emit_state(pubsub, _row_to_response(conn, evicted_row))

    metadata_envelope: dict[str, Any] = {}
    if persona_meta is not None:
        metadata_envelope["persona"] = persona_meta
    if body.trigger_phrase is not None and body.trigger_phrase.strip():
        metadata_envelope["trigger_phrase"] = body.trigger_phrase.strip()
    metadata_envelope["persona_reasoning"] = _build_persona_reasoning(
        caller_supplied=body.persona_reasoning,
        intent=body.intent,
        persona_meta=persona_meta,
    )

    summary_payload = {
        "title": title,
        "metadata": metadata_envelope,
        # No template for standalone — the corpus entry id lives on the
        # step's metadata blob instead. `_render_template_plan_steps`
        # short-circuits on a missing template_id, so the GET path
        # falls back to `_fetch_steps` which reads the persisted
        # single-step row including its kind + metadata. That's the
        # intended path for standalone activities.
        "template_id": None,
    }
    steps_persist = [
        {
            "seq": 1,
            "body": step_body,
            "sfx": None,
            "expected_action": None,
            "current": False,  # _persist_activity forces current=1 on insert
            "action_slot": None,
            "step_id": None,
            "choices_rendered": None,
            "kind": step_kind,
            "metadata": step_metadata,
        }
    ]
    _persist_activity(
        conn,
        activity_id=activity_id,
        session_id=session_id,
        persona_id=effective_persona_id,
        intent_source=body.intent,
        summary_payload=summary_payload,
        steps=steps_persist,
        state=PROPOSED_STATE,
        toy_ids=[],
        slot_fills={},
    )

    row = _fetch_activity_row(conn, activity_id)
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


def _do_propose(
    body: ProposeRequest,
    conn: sqlite3.Connection,
    pubsub: PubSub,
    judge_call: JudgeCall = None,
) -> ActivityResponse:
    """Shared propose-and-persist helper.

    Carries no auth — both ``post_propose`` and ``post_regenerate``
    funnel through here and keep the auth check on the route handler.

    ``judge_call`` is the FastAPI-injected judge factory (see
    :func:`get_judge_call`). When non-``None`` and the new
    ``labeled_events`` row is in-sample, an async judge task is fired
    via :func:`toybox.ai.labeled_events.schedule_judge_sample` to fill
    in ``judge_scores_json``. The task runs detached; the kid-facing
    HTTP response returns immediately.

    Phase E Step 28 carve-out: ``TOYBOX_GENERATOR_ADAPTER`` and
    ``TOYBOX_GENERATOR_MODE`` env vars dispatch between the v1 offline
    path (default), the Claude tool-loop path
    (``adapter=claude, mode=loop``), and the not-yet-implemented local
    paths (``adapter=local`` raises :class:`NotImplementedError`).

    Phase K K13: ``intent ∈ {"request_song", "request_joke"}`` routes
    to :func:`_do_propose_standalone` (corpus-driven single-step
    activity). When the corresponding surface flag is OFF, the helper
    returns the synthetic ``state="dismissed"`` response per plan §7
    — no DB row, no WS envelope.
    """
    # Phase K K13: standalone-intent gate. Runs BEFORE persona-pick /
    # template-generator setup so the dismissed path stays purely
    # synthetic (no catalog reads, no labeled_events row). The judge
    # sample is skipped for both standalone branches — the corpus
    # picks are deterministic and don't benefit from the judge loop.
    if body.intent in _STANDALONE_INTENTS:
        if not _standalone_surface_enabled(conn, body.intent):
            return _dismissed_surface_disabled_response()
        return _do_propose_standalone(body, conn, pubsub)

    # Caller-pinned persona wins; otherwise pick a fresh library one
    # so the kiosk avatar varies across propose calls. Falls through to
    # no persona when the library is empty (kiosk fallback letter
    # handles that case).
    effective_persona_id = body.persona_id
    persona_meta: dict[str, Any] | None = None
    if effective_persona_id is None:
        picked = _pick_random_library_persona(conn)
        if picked is not None:
            effective_persona_id = picked["id"]
            persona_meta = picked
    # Step 19: resolve real catalog content (toys, rooms, children)
    # BEFORE generating. The resolver is best-effort — sqlite errors
    # log WARNING and degrade to empty inputs (placeholder vocabulary)
    # so propose can never 500 on a missing/corrupt catalog row.
    resolved_toys: list[ResolvedToy] = []
    resolved_rooms: list[ResolvedRoom] = []
    resolved_children = None
    try:
        resolved_toys = resolve_toys(conn)
        resolved_rooms = resolve_rooms(conn)
        propose_child_ids = _resolve_propose_child_ids(conn, body.context)
        resolved_children = resolve_child_profiles(conn, propose_child_ids)
    except sqlite3.Error:
        _logger.warning(
            "content_resolver query failed on propose; falling back to placeholders",
            exc_info=True,
        )

    # Phase E Step 28: dispatch matrix.
    # - claude+single (default = v1): offline generator (current path)
    # - claude+loop: ClaudeActivityGenerator.generate_activity_loop
    # - local+*: capability-gated. is_local_capable() probes the
    #   configured local runtime + per-adapter breaker; when True the
    #   path routes through LocalActivityGenerator (which currently
    #   raises NotImplementedError pointing at Step 26 / issue #38).
    #   When False (no runtime, model not loaded, breaker open) we
    #   fall back to the Claude path and log the reason at WARNING
    #   so an operator can grep the cause.
    dispatch = resolve_dispatch()
    if dispatch.adapter == ADAPTER_LOCAL:
        dispatch = _resolve_local_dispatch(dispatch)
    loop_tool_calls: list[dict[str, Any]] | None = None
    generator_path_for_recording = GENERATOR_PATH_OFFLINE
    if dispatch.adapter == ADAPTER_LOCAL:
        # is_local_capable returned True -- route through the local
        # adapter. Pre-Step-26 this raises NotImplementedError; the
        # TestClient re-raises so an operator sees the carve-out hint
        # rather than a silent fallback indistinguishable from a real
        # local-runtime outage.
        _dispatch_local(dispatch.mode)
        # Defense-in-depth: _dispatch_local MUST raise pre-Step-26.
        # If Step 26 makes it return cleanly without re-routing through
        # this caller, every branch below would fall through to the
        # offline-generation path -- local-mode requests would silently
        # produce offline activities. Force Step 26 to consciously
        # rewrite this branch.
        raise RuntimeError(
            "Step 28 partial: _dispatch_local must raise NotImplementedError; "
            "reached unreachable code. Step 26 (#38) will replace this branch "
            "with a real local-generation path."
        )
    loop_fallback_reason: str | None = None
    if dispatch.adapter == ADAPTER_CLAUDE and dispatch.mode == MODE_LOOP:
        try:
            activity, loop_tool_calls = _run_loop_generation(
                body,
                conn,
                effective_persona_id=effective_persona_id,
                resolved_toys=resolved_toys,
                resolved_rooms=resolved_rooms,
            )
            generator_path_for_recording = GENERATOR_PATH_CLAUDE
        except NotImplementedError:
            raise
        # H3: narrow the catch to expected transient failures only.
        # ``TypeError``/``AttributeError``/``KeyError``/``ImportError`` are
        # programming bugs and must propagate to the route handler so an
        # operator sees a 500 (and the structured traceback) rather than
        # a silent fallback to the offline path indistinguishable from a
        # real Claude outage.
        except (
            RuntimeError,
            TimeoutError,  # asyncio.TimeoutError is an alias on 3.11+
            sqlite3.Error,
            ValidationError,
            ConnectionError,
        ) as exc:
            _logger.error(
                "claude+loop dispatch failed (%s: %s); falling back to offline generator",
                type(exc).__name__,
                exc,
                exc_info=True,
                extra={
                    "error_class": exc.__class__.__name__,
                    "fallback_reason": "transient",
                    "intent": body.intent,
                    "slot": body.slot,
                },
            )
            loop_fallback_reason = "transient_loop_failure"
            activity = generate(
                intent=body.intent,
                slot=body.slot,
                context=body.context,
                hour=body.hour,
                seed=body.seed,
                persona_id=effective_persona_id,
                conn=conn,
                available_toys=resolved_toys,
                available_rooms=resolved_rooms,
                resolved_children=resolved_children,
            )
            loop_tool_calls = None
    else:
        # claude+single (default = v1 byte-identical path).
        # Pass ``conn`` so the picker consults the ``feedback`` table —
        # past parent ``didnt_work``/``loved_it``/``dismissed_pre_approval``
        # entries adjust candidate ranking per Phase D step 20. The
        # consultation is best-effort (sqlite errors degrade to uniform
        # pick) so this can't break propose.
        activity = generate(
            intent=body.intent,
            slot=body.slot,
            context=body.context,
            hour=body.hour,
            seed=body.seed,
            persona_id=effective_persona_id,
            conn=conn,
            available_toys=resolved_toys,
            available_rooms=resolved_rooms,
            resolved_children=resolved_children,
        )
    # Phase K K5: role-slot resolution. When the picked template
    # declared ``required_roles`` or ``optional_roles`` at K3 schema
    # extension time, resolve them now via K4's slot-fill engine and
    # merge the per-role display names into the activity's slot_fills
    # so :func:`render_with_slot_fills` substitutes ``{role_name}``
    # placeholders in step text. The resolved cast (structured form,
    # role-name → resolved toy id OR generic descriptor) lands on the
    # activity's metadata envelope so :func:`_row_to_response` can
    # surface ``ActivityResponse.roles`` + ``cast_summary``.
    #
    # Best-effort: a missing template (renamed between propose runs)
    # or a no-roles template short-circuits silently — the legacy
    # ``{toy}`` substitution path stays the load-bearing fill for the
    # 200 pre-K templates.
    role_slot_overlay: dict[str, str] = {}
    role_records: list[dict[str, Any]] = []
    cast_summary = ""
    template_for_roles = find_template_by_id(activity.template_id)
    if template_for_roles is not None and (
        template_for_roles.required_roles or template_for_roles.optional_roles
    ):
        role_weights = _load_persona_role_weights(conn, effective_persona_id)
        persona_for_picker = _PersonaForRoleSlots(
            id=effective_persona_id or "",
            role_weights=role_weights,
        )
        try:
            role_slot_result = resolve_role_slots(
                template_for_roles,
                resolved_toys,
                persona_for_picker,
                body.seed,
            )
        except Exception:  # noqa: BLE001 -- defense: role-slot bug must not break propose
            _logger.warning(
                "resolve_role_slots failed for template %r; skipping role wire-up",
                activity.template_id,
                exc_info=True,
            )
            role_slot_result = None
        else:
            # ``None`` from a clean return means the eligibility gate
            # rejected the template (required_roles > toy pool). The
            # legacy ``{toy}`` substitution stays in place, but role
            # placeholders will echo literally — that's the design
            # fallback for an undersized household; future iterations
            # (K6 recast) re-pick. Log so operators can grep occurrences
            # and re-tune the toy pool.
            if role_slot_result is None:
                _logger.warning(
                    "resolve_role_slots eligibility-gate rejected template %r "
                    "(required_roles count exceeds available toy pool); "
                    "step text will retain role placeholders literally",
                    activity.template_id,
                )
        role_slot_overlay, role_records, cast_summary = _build_role_assignments(role_slot_result)

    session_id = _ensure_session(conn, body.session_id)

    # Evict oldest first so the configured cap holds for the new row.
    # ``play_target_depth.get`` is read fresh per call so the parent UI
    # can flip the preset and have the very next propose honour it
    # without a restart.
    evicted_ids = evict_oldest_for_capacity(conn, cap=play_target_depth.get(conn))
    for eid in evicted_ids:
        evicted_row = _fetch_activity_row(conn, eid)
        _emit_state(pubsub, _row_to_response(conn, evicted_row))

    # NOTE: ``activities.summary`` is overloaded as a JSON envelope
    # carrying ``title``, ``metadata``, and ``template_id``. The
    # migration declares the column free-form, so this is schema-legal
    # but invisible to schema readers. A future migration may split
    # this into dedicated ``title``/``metadata_json`` columns; until
    # then ``_row_to_response`` parses the same envelope.
    # Splice persona metadata into the activity's metadata envelope so
    # the kiosk can render the persona's display name + avatar path
    # without an extra round-trip. ``activity.metadata`` is a frozen
    # dict, so build a fresh copy.
    metadata = dict(activity.metadata)
    if persona_meta is not None:
        metadata["persona"] = persona_meta
    # Step 23: "why this?" telemetry. ``trigger_phrase`` is recorded
    # verbatim when the caller (listening pipeline) supplies the
    # transcript substring that fired the trigger; pre-step-23 callers
    # leave it ``None`` and the field stays absent from the envelope so
    # ``_row_to_response`` surfaces ``trigger_phrase=None``. The
    # ``persona_reasoning`` defaults to a synthesised string built from
    # the intent + persona (or ``"matched on intent"`` when no persona)
    # so the UI panel always has something to show — empty fields hurt
    # parent trust more than a generic rationale.
    if body.trigger_phrase is not None and body.trigger_phrase.strip():
        metadata["trigger_phrase"] = body.trigger_phrase.strip()
    metadata["persona_reasoning"] = _build_persona_reasoning(
        caller_supplied=body.persona_reasoning,
        intent=body.intent,
        persona_meta=persona_meta,
    )
    # H3: when the loop-mode dispatch caught a narrow transient failure
    # and fell back to the offline generator, surface the reason on the
    # activity's metadata envelope so an operator running queries
    # against ``labeled_events.activity_json`` can compute the % of
    # intended loop calls that fell back vs. real Claude outages.
    if loop_fallback_reason is not None:
        metadata["fallback_reason"] = loop_fallback_reason
    summary_payload = {
        "title": activity.title,
        "metadata": metadata,
        "template_id": activity.template_id,
    }
    steps = [
        {
            "seq": idx + 1,
            "body": step.text,
            "sfx": step.sfx,
            "expected_action": step.expected_action,
            "current": False,
            # Phase F Step F6: thread the per-step action slot from
            # the generator output through the persistence layer to
            # the kiosk WS envelope.
            "action_slot": step.action_slot,
            # Phase G G2: thread the template step id and rendered
            # choice labels through to the persistence layer so the
            # first-step INSERT can populate ``step_template_id`` +
            # ``choices_json`` columns. Both are ``None`` on legacy
            # linear templates.
            "step_id": step.step_id,
            "choices_rendered": step.choices_rendered,
        }
        for idx, step in enumerate(activity.steps)
    ]
    # Phase G G2: pull the resolved slot map off ``metadata["slot_fills"]``
    # (set by the generator) so the persistence layer can write
    # ``activities.slot_fills_json``. Defensive default to ``{}`` for
    # callers that bypass the generator (tests, ad-hoc fixtures).
    raw_slot_fills = metadata.get("slot_fills", {})
    if isinstance(raw_slot_fills, dict):
        slot_fills_arg: dict[str, str] = {str(k): str(v) for k, v in raw_slot_fills.items()}
    else:
        slot_fills_arg = {}
    # Phase K K5: merge the role-name → display-name overlay so
    # ``render_with_slot_fills`` resolves ``{quest_giver}`` etc. The
    # overlay never collides with legacy slot names (role values are
    # snake_case role taxonomy entries, never ``toy``/``slot``/the
    # SlotRegistry word-list names). Also re-render step bodies +
    # choice labels NOW so the persisted ``activity_steps`` row carries
    # the substituted text — the generator ran without role context so
    # its initial render left the placeholders intact.
    if role_slot_overlay:
        slot_fills_arg.update(role_slot_overlay)
        # Re-render every step's body + choice labels with the merged
        # slot fills. The generator already substituted legacy slots;
        # this pass picks up the newly-merged role names.
        for step_record, original_step in zip(steps, activity.steps, strict=True):
            step_record["body"] = render_with_slot_fills(original_step.text, slot_fills_arg)
            if original_step.choices_rendered is not None:
                step_record["choices_rendered"] = tuple(
                    render_with_slot_fills(label, slot_fills_arg)
                    for label in original_step.choices_rendered
                )
    if role_records:
        # Stash on the persisted metadata envelope so ``_row_to_response``
        # can reconstruct ``ActivityResponse.roles`` + ``cast_summary``
        # on every read (REST GET, WS ``activity.state`` envelope).
        metadata["role_assignments"] = role_records
        metadata["cast_summary"] = cast_summary
        # Re-encode summary_payload now that metadata changed.
        summary_payload = {
            "title": activity.title,
            "metadata": metadata,
            "template_id": activity.template_id,
        }
    _persist_activity(
        conn,
        activity_id=activity.id,
        session_id=session_id,
        persona_id=activity.persona_id,
        intent_source=body.intent,
        summary_payload=summary_payload,
        steps=steps,
        state=PROPOSED_STATE,
        toy_ids=list(activity.toy_ids),
        slot_fills=slot_fills_arg,
    )

    # Phase K K14 Surface E: the ending step is NOT inserted here. It is
    # built and persisted lazily at advance-time in :func:`post_advance`
    # when the kid clears the last renderable template step. Eager
    # insertion at propose-time is incompatible with G2's lazy single-
    # step persistence: the only template row written here is seq=1, so
    # an eager ending at seq=last_template_seq+1 would be visible to the
    # pre-G2 / legacy-linear advance branch (``current_index + 1 <
    # len(steps)``) on the very first advance, jumping the kid past the
    # template's middle steps entirely. The :func:`_build_ending_row`
    # picker is called from the terminal-advance branches below.

    # Phase C step 15: write a labeled_events row BEFORE returning the
    # activity. The recorder is best-effort — a failure here must NOT
    # break the propose flow (the activity is already persisted; the
    # eval scaffold is observability, not load-bearing). Failures log
    # WARNING and we proceed to emit + return. When the row lands and
    # the sampler picks it, we also fire the async judge task — the
    # judge call itself is non-blocking and best-effort.
    ctx: Any = None
    row_id: int | None = None
    try:
        # Step 19: surface real toys/rooms/profile in inputs_chatml_json
        # so Phase E SFT exports see catalog content — not the empty
        # placeholders that pre-step-19 rows carried. ``available_toys``
        # is materialised as the toy display names; rooms include their
        # features for richer system-prompt context.
        toy_names: tuple[str, ...] = tuple(t.display_name for t in resolved_toys)
        room_names: tuple[str, ...] = tuple(r.display_name for r in resolved_rooms)
        child_profile_payload: dict[str, Any] | None = None
        if resolved_children is not None and (
            resolved_children.banned_themes or resolved_children.reading_level
        ):
            child_profile_payload = {
                "banned_themes": list(resolved_children.banned_themes),
                "reading_level": resolved_children.reading_level,
            }
        ctx = build_generator_context(
            intent=body.intent,
            slot=body.slot,
            persona_id=activity.persona_id,
            available_toys=toy_names,
            available_rooms=room_names,
            child_profile=child_profile_payload,
            extra={"hour": body.hour, "seed": body.seed}
            if body.context is None
            else {"hour": body.hour, "seed": body.seed, "caller_context": body.context},
        )
        row_id = record_generation(
            conn,
            activity=activity,
            ctx=ctx,
            generator_path=generator_path_for_recording,
            tool_calls=loop_tool_calls,
        )
    except Exception:  # noqa: BLE001 -- eval scaffold must never break propose
        _logger.warning(
            "labeled_events record failed for activity %s; skipping",
            activity.id,
            exc_info=True,
        )

    # Schedule the async judge sample. Wrapped in its own try/except so
    # a sampler bug (no event loop, judge_call raises while building the
    # coroutine, etc.) cannot break propose. The sampler itself short-
    # circuits cleanly when ``judge_call`` is None.
    if row_id is not None and ctx is not None:
        try:
            schedule_judge_sample(
                row_id=row_id,
                activity=activity,
                ctx=ctx,
                judge_call=judge_call,
            )
        except Exception:  # noqa: BLE001 -- judge scheduling must never break propose
            _logger.warning(
                "judge sample scheduling failed for activity %s; continuing",
                activity.id,
                exc_info=True,
            )

    row = _fetch_activity_row(conn, activity.id)
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/propose", response_model=ActivityResponse, status_code=status.HTTP_201_CREATED)
def post_propose(
    body: ProposeRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    judge_call: Annotated[JudgeCall, Depends(get_judge_call)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Generate a new activity at ``proposed``. Drops oldest if cap reached.

    Sync handler: SQLite work runs on the FastAPI threadpool worker.
    The judge sample is scheduled by :func:`_do_propose` via
    :func:`toybox.ai.labeled_events.schedule_judge_sample`, which spins
    up a short-lived daemon thread to host an event loop for the
    detached coroutine (the kid-facing path stays sync).
    """
    return _do_propose(body, conn, pubsub, judge_call=judge_call)


@router.post("/{activity_id}/approve", response_model=ActivityResponse)
def post_approve(
    activity_id: str,
    body: ApproveRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """proposed → approved (optimistically)."""
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, STATE_APPROVED)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)

    child_ids = body.child_ids or _resolve_default_child_ids(conn)
    encoded_children = json.dumps(child_ids) if child_ids else None
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_APPROVED,
        additional_sets=(("child_ids", encoded_children),),
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/dismiss", response_model=ActivityResponse)
def post_dismiss(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """proposed → dismissed.

    Step 15: dismiss-before-start writes ``parent_signal = -1`` to the
    matching ``labeled_events`` row. Best-effort — if the row doesn't
    exist (activity predates step 15) we silently no-op.
    """
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, DISMISSED_STATE)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=DISMISSED_STATE,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    _safe_update_parent_signal(conn, activity_id=activity_id, signal=PARENT_SIGNAL_DISMISS)
    # Phase D step 20: dismiss-before-approval is a soft anti-signal.
    # Only write the feedback row when the activity was still in the
    # ``proposed`` state at the moment of dismiss — a parent who
    # dismisses an *approved* (or running) activity already has the
    # ``end-early`` / ``didnt-work`` paths available for harder signals.
    if current_state == PROPOSED_STATE:
        _write_feedback(conn, activity_id=activity_id, kind=KIND_DISMISSED_PRE_APPROVAL)
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/regenerate", response_model=ActivityResponse)
def post_regenerate(
    activity_id: str,
    body: RegenerateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    judge_call: Annotated[JudgeCall, Depends(get_judge_call)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Propose a fresh activity. Source's fate depends on its state:

    - ``proposed``/``approved``/``running``: dismiss the source (it was
      live or pending; the user is abandoning it).
    - ``completed``/``ended``: leave the source in its terminal state
      (the activity already finished; no need to overwrite history with
      ``dismissed``). Just propose a new one — same UX from the user's
      seat (suggestion card replaces the panel) without losing the
      "kid finished all 5 steps" / "parent ended early" signal.
    - ``dismissed``/``didnt_work``: 409 — already abandoned, the
      panel/card shouldn't be visible to even offer this action.
    """
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)

    pre_dismiss_states = {PROPOSED_STATE, STATE_APPROVED, STATE_RUNNING}
    skip_states = {STATE_COMPLETED, STATE_ENDED}
    if current_state in pre_dismiss_states:
        ok, dismissed_row = _attempt_transition(
            conn,
            activity_id=activity_id,
            expected_version=expected_version,
            new_state=DISMISSED_STATE,
        )
        if not ok:
            raise VersionConflictError(int(dismissed_row["version"]), str(dismissed_row["state"]))
        _emit_state(pubsub, _row_to_response(conn, dismissed_row))
    elif current_state in skip_states:
        # Source already terminal — no transition. Fall through to propose.
        pass
    else:
        # dismissed / didnt_work — already abandoned.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_transition",
                "current_state": current_state,
                "target_state": "regenerate",
            },
        )

    intent = body.intent or str(row["intent_source"]) or "boredom"
    # Random seed when caller doesn't supply one — the seed feeds both
    # the template-pick rng AND the deterministic UUID hash, so a fixed
    # fallback meant every regenerate produced the same suggestion (and
    # collided on activities.id the second time around). Tests that
    # need determinism still pass an explicit seed.
    seed = body.seed if body.seed is not None else secrets.randbits(31)
    hour = body.hour if body.hour is not None else datetime.now(UTC).hour
    # Don't inherit the source's persona — let _do_propose pick a fresh
    # library persona so each "skip & try another" gives the kiosk a
    # different character. Caller can pin a persona via body.persona_id.
    persona_id = body.persona_id
    # Fold source identity into the UUID hash too — defense in depth so
    # that even an unlikely seed collision doesn't surface as a 500.
    context = dict(body.context) if body.context is not None else {}
    context.setdefault("regen_source", activity_id)
    context.setdefault("regen_source_version", current_version)
    # Step 23: inherit the "why this?" telemetry from the source row
    # when the caller doesn't override. This keeps the suggestion card's
    # panel coherent across "skip & try another" — the trigger phrase
    # that started the original suggestion is still the why for the
    # follow-up, and re-using it avoids a sudden empty panel.
    inherited_trigger = body.trigger_phrase
    inherited_reasoning = body.persona_reasoning
    if inherited_trigger is None or inherited_reasoning is None:
        source_response = _row_to_response(conn, row)
        if inherited_trigger is None:
            inherited_trigger = source_response.trigger_phrase
        if inherited_reasoning is None:
            inherited_reasoning = source_response.persona_reasoning
    return _do_propose(
        ProposeRequest(
            intent=intent,
            slot=body.slot,
            hour=hour,
            seed=seed,
            persona_id=persona_id,
            session_id=str(row["session_id"]),
            context=context,
            trigger_phrase=inherited_trigger,
            persona_reasoning=inherited_reasoning,
        ),
        conn,
        pubsub,
        judge_call=judge_call,
    )


@router.post("/{activity_id}/recast", response_model=ActivityResponse)
def post_recast(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Phase K K6: re-roll the role cast on a proposed activity.

    Re-runs :func:`resolve_role_slots` with a fresh server-side seed,
    rewrites ``activities.slot_fills_json`` (role-name keys overwritten,
    legacy slot keys preserved), re-renders every persisted
    ``activity_steps.body`` (and ``choices_json``) via
    :func:`render_with_slot_fills`, increments ``activities.version``,
    and emits an ``activity.state`` envelope with the updated response.

    State guard: this is a *proposed-only* operation (kids haven't seen
    the cast yet — recast while running/paused would re-render text on
    a screen the kid is currently looking at). State ≠ ``proposed``
    returns 409 ``{"code": "recast_only_when_proposed", ...}``. Plan §11
    flags a v2 idea to lift this gate once mid-activity re-render UX is
    designed.

    Best-effort role resolution: a template that no longer exists, or
    declares no roles, leaves ``slot_fills_json`` + step bodies as-is
    and still bumps the version — the call always succeeds for any
    proposed row so the parent UI's "New cast" button never wedges.
    """
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    # State guard fires FIRST so a caller that re-tries after approval
    # gets the precise "recast_only_when_proposed" code, not a generic
    # version_conflict that masks the real reason the click was rejected.
    if current_state != STATE_PROPOSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "recast_only_when_proposed",
                "current_version": current_version,
                "current_state": current_state,
            },
        )
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)

    # Parse the persisted summary envelope: ``title``, ``metadata``,
    # ``template_id``. Defensive against legacy plaintext summaries
    # (no envelope) — the recast still proceeds, just without role
    # resolution.
    summary_raw = row["summary"]
    title: str | None = None
    metadata: dict[str, Any] = {}
    template_id: str | None = None
    if summary_raw:
        try:
            payload = json.loads(summary_raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            title_raw = payload.get("title")
            if isinstance(title_raw, str):
                title = title_raw
            meta_raw = payload.get("metadata")
            if isinstance(meta_raw, dict):
                metadata = dict(meta_raw)
            tid_raw = payload.get("template_id")
            if isinstance(tid_raw, str) and tid_raw:
                template_id = tid_raw

    # Parse the persisted slot fills so we can preserve non-role keys
    # (``toy``, ``room``, ``adjective``, etc.). Recast only overwrites
    # the role-name keys — the legacy substitution surface stays
    # byte-identical for templates without declared roles.
    slot_fills_raw = row["slot_fills_json"]
    slot_fills: dict[str, str] = {}
    if slot_fills_raw:
        try:
            decoded_fills = json.loads(slot_fills_raw)
        except json.JSONDecodeError:
            decoded_fills = None
        if isinstance(decoded_fills, dict):
            slot_fills = {str(k): str(v) for k, v in decoded_fills.items()}

    # Resolve the new cast. The template may have been removed/renamed
    # between propose and recast (best-effort propose contract); we
    # treat that as "no roles to re-roll" rather than 500-ing.
    persona_id = row["persona_id"]
    template = find_template_by_id(template_id) if template_id is not None else None
    role_overlay: dict[str, str] = {}
    role_records: list[dict[str, Any]] = []
    new_cast_summary = ""
    if template is not None and (template.required_roles or template.optional_roles):
        try:
            resolved_toys = resolve_toys(conn)
        except sqlite3.Error:
            _logger.warning(
                "resolve_toys failed on recast for activity %s; leaving slot_fills unchanged",
                activity_id,
                exc_info=True,
            )
            resolved_toys = []
        role_weights = _load_persona_role_weights(conn, persona_id)
        persona_for_picker = _PersonaForRoleSlots(
            id=str(persona_id) if persona_id is not None else "",
            role_weights=role_weights,
        )
        # ``secrets.randbits(31)`` mirrors ``post_regenerate``'s
        # fresh-seed convention. The role-slot picker hashes
        # (template_id, sorted toy ids, persona id, seed) so a new
        # seed reliably reshuffles even when role_weights tilt one
        # toy hard into one slot.
        new_seed = secrets.randbits(31)
        try:
            role_slot_result = resolve_role_slots(
                template,
                resolved_toys,
                persona_for_picker,
                new_seed,
            )
        except Exception:  # noqa: BLE001 -- defense: role-slot bug must not wedge recast
            _logger.warning(
                "resolve_role_slots failed on recast for activity %s",
                activity_id,
                exc_info=True,
            )
            role_slot_result = None
        if role_slot_result is None:
            # Eligibility gate (required_roles > toy pool) or a caught
            # exception above. We keep the version bump + emit so the
            # caller sees "I clicked the button and something happened"
            # but role text stays whatever it was at propose time.
            _logger.warning(
                "recast: resolve_role_slots returned no cast for activity %s "
                "(template %r); slot_fills unchanged",
                activity_id,
                template_id,
            )
        else:
            role_overlay, role_records, new_cast_summary = _build_role_assignments(role_slot_result)

    # Merge the new role overlay onto the persisted slot_fills. Role
    # name keys overwrite; non-role keys (toy / room / adjective / ...)
    # are preserved verbatim — the legacy ``{toy}`` substitution path
    # MUST survive recast even on role-bearing templates.
    if role_overlay:
        slot_fills.update(role_overlay)

    # Re-render every persisted activity_steps row using the new fills.
    # In ``proposed`` state lazy-insert has only written ``steps[0]`` so
    # this is typically one row, but the loop covers any future change
    # to the persistence strategy. We re-render even when role_overlay
    # is empty so the merge above doesn't have to special-case "did
    # anything change?" — render is idempotent when the fills are.
    step_rows = conn.execute(
        "SELECT id, body, choices_json, step_template_id, seq "
        "FROM activity_steps WHERE activity_id = ?",
        (activity_id,),
    ).fetchall()

    # Resolve template steps for re-rendering. We need the ORIGINAL
    # template step text — the persisted ``body`` has already been
    # substituted at propose time, so re-rendering it would silently
    # become a no-op (placeholders are gone). Same logic for
    # ``choices_json``: we need the template's choice labels, not the
    # rendered ones we persisted.
    rendered_updates: list[tuple[str, str | None, str]] = []
    if template is not None:
        template_steps = template.steps
        for step_row in step_rows:
            step_template_id = step_row["step_template_id"]
            template_idx = _resolve_template_step_index(
                template_steps,
                step_template_id=step_template_id,
                fallback_array_index=int(step_row["seq"]) - 1,
            )
            if template_idx < 0:
                # Step doesn't map to any template step (corrupt row or
                # post-template-rename activity). Leave the persisted
                # body untouched — re-rendering with no template text
                # to draw from is a no-op anyway.
                continue
            tstep = template_steps[template_idx]
            new_body = render_with_slot_fills(tstep.text, slot_fills)
            new_choices_blob: str | None
            if tstep.choices is not None:
                rendered_labels = [
                    render_with_slot_fills(label, slot_fills) for label, _next in tstep.choices
                ]
                new_choices_blob = json.dumps(rendered_labels)
            else:
                # Preserve the existing choices_json — including NULL —
                # so re-render never strips choice metadata from a row
                # that legitimately had it (defense against a future
                # template-step shape change).
                new_choices_blob = step_row["choices_json"]
            rendered_updates.append((new_body, new_choices_blob, str(step_row["id"])))

    # Rebuild the summary envelope with the new role metadata. The
    # ``role_assignments`` + ``cast_summary`` keys are read back by
    # :func:`_row_to_response` to populate the wire-level ``roles`` +
    # ``cast_summary`` fields. When no roles resolved we strip both
    # keys so the wire response cleanly reflects "no cast" rather than
    # carrying stale pre-recast records.
    if role_records:
        metadata["role_assignments"] = role_records
        metadata["cast_summary"] = new_cast_summary
    else:
        metadata.pop("role_assignments", None)
        metadata.pop("cast_summary", None)
    new_summary_payload: dict[str, Any] = {
        "title": title,
        "metadata": metadata,
    }
    if template_id is not None:
        new_summary_payload["template_id"] = template_id
    new_summary_blob = json.dumps(new_summary_payload, sort_keys=True)
    new_slot_fills_blob = json.dumps(slot_fills, sort_keys=True)

    # Two transactions, ordered: (1) bump version + rewrite summary +
    # slot_fills via ``_attempt_transition`` (reuses the optimistic-
    # concurrency WHERE clause so a concurrent mutation between fetch
    # + UPDATE surfaces as a clean 409 rather than a silent double-bump);
    # (2) re-render the persisted ``activity_steps`` rows. A crash
    # between the two leaves slot_fills_json ahead of the persisted
    # step bodies, but ``_row_to_response`` renders steps in the
    # ``proposed`` state from the template plan + current slot_fills
    # rather than reading ``activity_steps.body``, so the wire client
    # never sees the partial-write window.
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=current_state,
        additional_sets=(
            ("summary", new_summary_blob),
            ("slot_fills_json", new_slot_fills_blob),
        ),
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    if rendered_updates:
        with conn:
            for new_body, new_choices_blob, step_id in rendered_updates:
                conn.execute(
                    "UPDATE activity_steps SET body = ?, choices_json = ? WHERE id = ?",
                    (new_body, new_choices_blob, step_id),
                )

    _logger.info(
        "recast activity %s from version %d -> %d (%d role(s) re-rolled)",
        activity_id,
        current_version,
        int(row["version"]),
        len(role_records),
    )
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


def _bad_advance(code: str, **extra: Any) -> HTTPException:
    """Phase G G3: build a 400 with the canonical ``{code, ...}`` body
    used by the advance handler's three branching-input error cases.
    """
    detail: dict[str, Any] = {"code": code}
    detail.update(extra)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _resolve_template_step_index(
    template_steps: tuple[Any, ...],
    *,
    step_template_id: str | None,
    fallback_array_index: int,
) -> int:
    """Phase G G3: find the array index of an activity row's step in its
    template. Prefers ``step_template_id`` (set on Phase G branching
    templates and any template step that has an authored ``id``); falls
    back to ``fallback_array_index`` (typically ``seq - 1``) for legacy
    linear templates whose steps have no ids. Returns ``-1`` if no
    match is possible — caller handles that as a corrupt-row case.
    """
    if step_template_id is not None:
        for idx, t_step in enumerate(template_steps):
            if t_step.id == step_template_id:
                return idx
    if 0 <= fallback_array_index < len(template_steps):
        return fallback_array_index
    return -1


def _is_branch_destination_leaf(template_steps: Sequence[Any], step: Any) -> bool:
    """A step is a "branch-destination leaf" iff some step in the same
    template references its ``id`` via ``choices[*].next`` AND the step
    itself has neither ``choices`` nor ``next``. Such steps terminate
    on advance — implicit fall-through to ``steps[i+1]`` is wrong for
    branch endings (would bleed one branch's outcome into another, e.g.
    "save the cat" silently chaining into the "save the baby" outcome).
    Templates that intentionally chain post-branch content do it via
    explicit ``next`` (see the ``train_13`` mountain/beach/cookie merge
    into ``tunnel``).
    """
    if step.id is None:
        return False
    if step.choices is not None or step.next is not None:
        return False
    target_id = step.id
    for s in template_steps:
        if s.choices is None:
            continue
        for _label, dest in s.choices:
            if dest == target_id:
                return True
    return False


def _pick_embedded_corpus_step(
    conn: sqlite3.Connection,
    *,
    template: Any,
    template_step: Any,
    slot_fills: dict[str, str],
    seed: int,
    persona_id: str | None,
    new_seq: int,
) -> dict[str, Any] | None:
    """Phase K K14 Surface B (enabled path): pick a corpus entry whose
    theme matches the template's ``recommended_themes`` and return the
    persist-row dict produced by :func:`build_interjection_step`.

    Caller has already gated ``(content_master AND play_embedded_enabled)``
    via :func:`_embedded_surface_enabled` for this step's kind. The
    function still defends: if the picker finds no candidate (corpus
    not yet rendered, persona-incompatible theme, etc.), it returns
    ``None`` and the caller degrades to a terminal advance.

    Picks deterministically:

    * Filter by ``theme = template.recommended_themes[0]`` (the
      strongest theme, per phase-k-plan §6 K14 step).
    * ``persona_id`` filter mirrors the standalone path so persona
      compatibility carries through.
    * ``require_audio=True`` for songs so the kiosk doesn't 404 on a
      not-yet-rendered entry — same logic as the K13 standalone song
      surface.

    Returns the build_interjection_step dict (``kind``, ``body``,
    ``metadata``, ``seq``, etc.) with ``new_seq`` and ``slot_fills``
    threaded so the caller can INSERT directly. ``None`` means no
    corpus row available.
    """
    if not template.recommended_themes:
        _logger.info(
            "embedded pick for template %r: recommended_themes empty; "
            "auto step kind=%s degrades to terminal",
            template.id,
            template_step.kind,
        )
        return None
    theme = template.recommended_themes[0]
    kind = template_step.kind

    corpus_entry: Joke | Song | None
    if kind == "song":
        corpus_entry = pick_song(
            seed=seed,
            persona_id=persona_id,
            theme=theme,
            require_audio=True,
        )
    elif kind == "joke":
        corpus_entry = pick_joke(
            seed=seed,
            persona_id=persona_id,
            theme=theme,
        )
    else:
        # Caller is expected to dispatch only on song/joke auto steps;
        # any other kind is a programming error.
        raise ValueError(f"_pick_embedded_corpus_step: unsupported kind {kind!r}")

    if corpus_entry is None:
        _logger.info(
            "embedded pick for template %r kind=%s theme=%s found no entry",
            template.id,
            kind,
            theme.value,
        )
        return None

    # Toy display name for the joke {toy} placeholder — best-effort via
    # the catalog resolver. Failure degrades to no-toy form (same as
    # the K13 standalone path).
    toy_display_name: str | None = None
    if kind == "joke":
        try:
            toys = resolve_toys(conn)
        except sqlite3.Error:
            _logger.warning(
                "embedded pick: resolve_toys failed; joke degrades to no-toy form",
                exc_info=True,
            )
            toys = []
        if toys:
            toy_display_name = toys[0].display_name

    return build_interjection_step(
        interjection=InterjectionKind.embedded,
        corpus_entry=corpus_entry,
        slot_fills=slot_fills,
        seq=new_seq,
        toy_display_name=toy_display_name,
    )


def _insert_interjection_step_row(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    interjection_row: dict[str, Any],
    new_seq: int,
    previous_step_id: str,
    chosen_label: str | None = None,
) -> None:
    """Phase K K14: persist a :func:`build_interjection_step` row into
    ``activity_steps``.

    Sibling of :func:`_insert_next_step` for the corpus-driven path.
    Both flip ``current=0`` on the previous step (optionally writing
    ``chosen_label`` if the kid passed through a choice point) and
    INSERT the new row at ``new_seq``. The interjection row has no
    ``choices_rendered``, no ``step_template_id``, but DOES have
    ``kind`` + ``metadata_json`` (the K13-introduced columns the K12
    kiosk dispatches on).

    Caller MUST be inside the version-bumped transaction so a stale
    retry cannot double-insert.
    """
    prev_set_clauses = ["current = 0"]
    prev_params: list[Any] = []
    if chosen_label is not None:
        prev_set_clauses.append("chosen_label = ?")
        prev_params.append(chosen_label)
    prev_params.extend([activity_id, previous_step_id])
    conn.execute(
        f"UPDATE activity_steps SET {', '.join(prev_set_clauses)} WHERE activity_id = ? AND id = ?",
        prev_params,
    )

    metadata = interjection_row.get("metadata") or {}
    metadata_blob: str | None
    if isinstance(metadata, dict) and metadata:
        metadata_blob = json.dumps(metadata, sort_keys=True)
    else:
        metadata_blob = None

    conn.execute(
        "INSERT INTO activity_steps "
        "(id, activity_id, seq, body, sfx, expected_action, current, action_slot, "
        " choices_json, step_template_id, kind, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            activity_id,
            new_seq,
            interjection_row["body"],
            interjection_row.get("sfx"),
            interjection_row.get("expected_action"),
            interjection_row.get("action_slot"),
            None,  # choices_json: interjections never branch
            None,  # step_template_id: interjections have no template-step id
            interjection_row.get("kind"),
            metadata_blob,
        ),
    )


def _resolve_template_index_by_id(template_steps: Sequence[Any], target_id: str) -> int:
    """Return the array index of the template step whose id equals
    ``target_id``, or ``-1`` if none. Mirrors the inline loop the
    advance handler uses for ``next`` / ``choices[].next`` resolution;
    pulled out so the K14 embedded skip-loop can reuse it.
    """
    for idx, t_step in enumerate(template_steps):
        if t_step.id == target_id:
            return idx
    return -1


def _skip_disabled_embedded(
    *,
    template_steps: Sequence[Any],
    starting_index: int,
    starting_step: Any,
    is_enabled: dict[str, bool],
) -> tuple[Any, int] | None:
    """Phase K K14 Surface B (disabled path): walk past auto song/joke
    steps when their surface gate is off.

    Mirrors the post_advance edge resolution rules in miniature, but
    only for templates step kinds ``"song"``/``"joke"`` with
    ``auto=True``. Linear / fall-through / explicit-next steps pass
    through unchanged. Branching steps (``choices``) are NEVER auto
    interjections (the K3 validator gates that via
    ``_check_song_joke_shape``: song/joke kinds cannot carry choices),
    so the skip walk never has to dispatch on a choice fork.

    Arguments:
        template_steps: The template's full step array.
        starting_index: Array index of the auto step the caller wants
            to potentially skip past.
        starting_step: ``template_steps[starting_index]``, passed
            explicitly so callers that already resolved it don't double-
            lookup.
        is_enabled: ``{"song": <bool>, "joke": <bool>}`` — the
            ``(content_master AND play_embedded_enabled)`` resolution
            for each kind. Pre-computed by the caller so the skip walk
            is pure (no conn dependency, trivially testable).

    Returns:
        ``(skipped_target_step, skipped_target_index)`` — the FIRST
        non-skipped step the engine should advance to. ``None`` means
        every reachable step from ``starting_index`` forward is an
        auto step whose surface is off → caller treats as terminal.

    The walk is bounded by ``len(template_steps)`` iterations so a
    pathological template (every step an auto interjection) cannot loop
    forever. Pre-K14 templates have zero auto steps, so the typical
    cost is one iteration of the early-exit guard.
    """
    current_idx = starting_index
    current_step = starting_step
    # Defense-in-depth: bound the walk by the template's length so a
    # malformed template (cycle in explicit-next pointers, all auto
    # interjections) can't wedge advance.
    for _ in range(len(template_steps) + 1):
        is_auto_song_joke = current_step.kind in ("song", "joke") and current_step.auto is True
        if not is_auto_song_joke:
            return current_step, current_idx
        if is_enabled.get(current_step.kind, False):
            # Surface is enabled — keep this step; the caller will
            # write a real corpus pick for it.
            return current_step, current_idx
        # Auto interjection whose surface is OFF. Walk its outgoing
        # edge. Auto interjections never carry ``choices`` (K3 validator
        # bars it); they may have ``next`` or fall through.
        if current_step.next is not None:
            target_idx = _resolve_template_index_by_id(template_steps, current_step.next)
            if target_idx < 0:
                return None
            current_idx = target_idx
            current_step = template_steps[current_idx]
            continue
        # Branch-destination leaves terminate — same rule 2.5 the
        # advance handler applies. Auto song/joke shouldn't be a branch
        # destination (validator gates), but defense-in-depth.
        if _is_branch_destination_leaf(template_steps, current_step):
            return None
        if current_idx + 1 < len(template_steps):
            current_idx += 1
            current_step = template_steps[current_idx]
            continue
        # Last array entry with no next → terminal.
        return None
    # Fell off the iteration bound — treat as terminal so the activity
    # completes rather than wedging. Logged at WARNING by the caller.
    return None


def _insert_next_step(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    template_step: Any,
    slot_fills: dict[str, str],
    new_seq: int,
    previous_step_id: str,
    chosen_label: str | None = None,
) -> None:
    """Phase G G3: lazily INSERT the next ``activity_steps`` row.

    Single source of truth for step insertion under lazy advance —
    rule 1 (choices), rule 2 (explicit ``next``), and rule 3
    (fall-through) all funnel through here. The caller is responsible
    for resolving which template step is the target; this helper does
    the rendering + persistence.

    Renders ``body`` and (when ``template_step.choices`` is present)
    each choice label using ``slot_fills`` so the inserted row is
    byte-identical to what step 1 would have rendered with the same
    fills (consistent with the G2 propose-time pattern). Marks the
    previous step ``current=0`` AND writes ``chosen_label`` on it
    when the kid passed through a choice point — the label written
    is the EXACT rendered string from the previous step's persisted
    ``choices_json`` so it matches what the kid saw.

    Caller MUST be inside the version-bumped transaction so a stale
    retry cannot double-insert.
    """
    body_text = render_with_slot_fills(template_step.text, slot_fills)
    choices_blob: str | None = None
    if template_step.choices is not None:
        rendered_labels = [
            render_with_slot_fills(label, slot_fills) for label, _next in template_step.choices
        ]
        choices_blob = json.dumps(rendered_labels)
    # Mark the previous step "not current" AND record the kid's choice
    # if any. We update by id (not seq) so a future helper that calls
    # this with a non-monotonic seq still flips the right row.
    prev_set_clauses = ["current = 0"]
    prev_params: list[Any] = []
    if chosen_label is not None:
        prev_set_clauses.append("chosen_label = ?")
        prev_params.append(chosen_label)
    prev_params.extend([activity_id, previous_step_id])
    conn.execute(
        f"UPDATE activity_steps SET {', '.join(prev_set_clauses)} WHERE activity_id = ? AND id = ?",
        prev_params,
    )
    conn.execute(
        "INSERT INTO activity_steps "
        "(id, activity_id, seq, body, sfx, expected_action, current, action_slot, "
        " choices_json, step_template_id) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            activity_id,
            new_seq,
            body_text,
            template_step.sfx,
            template_step.expected_action,
            template_step.action_slot,
            choices_blob,
            template_step.id,
        ),
    )


@router.post("/{activity_id}/advance", response_model=ActivityResponse)
def post_advance(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent, TokenScope.child}))],
    body: AdvanceRequest | None = None,
) -> ActivityResponse:
    """Advance one step. approved → running on first call; running → running/completed otherwise.

    Phase G G3: optional body ``{"choice_index": int}`` when the
    current step has ``choices``. Edge resolution (per the four
    rules in §"Vocabulary and conventions" of the Phase G plan):

    1. Current step has ``choices`` → ``choice_index`` REQUIRED →
       resolve ``choices[choice_index].next`` → lazy-INSERT next
       step rendered with the activity's slot fills + record
       ``chosen_label`` on the previous step.
    2. Current step has ``next`` → ``choice_index`` MUST be absent
       (else 400 ``choice_not_allowed``) → resolve target id →
       lazy-INSERT next step.
    3. Neither + not last in template array → fall through to
       ``steps[i + 1]`` → lazy-INSERT next step.
    4. Neither + last in template array → terminal; transition to
       ``completed``; no INSERT.

    Pre-G2 in-flight activities (5 rows already pre-seeded) skip the
    lazy-insert path and use the existing "row already exists" branch
    so the migration boundary doesn't break them.

    Idempotency: ``If-Match-Version`` mismatch → 409 with no INSERT
    (the version check fires before the INSERT path).
    """
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    if current_state == STATE_APPROVED:
        target = STATE_RUNNING
    elif current_state == STATE_RUNNING:
        target = STATE_RUNNING
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_transition",
                "current_state": current_state,
                "target_state": STATE_RUNNING,
            },
        )
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)

    steps = conn.execute(
        "SELECT id, seq, current, choices_json, step_template_id "
        "FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    if not steps:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "activity_has_no_steps", "id": activity_id},
        )

    advance_body = body if body is not None else AdvanceRequest()

    # Special-case ``approved → running``: the first /advance after
    # approve doesn't insert a new row — steps[0] is already present
    # (G2 lazy-inserted it at propose time) and the kiosk is already
    # rendering it. Just flip ``state`` to running + ``current=1``
    # (already set by G2 — defensive re-set is cheap).
    if current_state == STATE_APPROVED:
        if advance_body.choice_index is not None:
            # Approve → running is a position-zero transition; there
            # is nothing to "choose" yet because the kid hasn't seen
            # any branching step. Reject loudly so a confused client
            # surfaces the bug instead of silently dropping the index.
            raise _bad_advance("choice_not_allowed", reason="initial_advance_no_choice")
        ok, row = _attempt_transition(
            conn,
            activity_id=activity_id,
            expected_version=expected_version,
            new_state=STATE_RUNNING,
            additional_sets=(("started_at", _now_iso()),),
        )
        if not ok:
            raise VersionConflictError(int(row["version"]), str(row["state"]))
        with conn:
            # Defensive: ensure exactly seq=1 is current. G2 already
            # set this at insert time but a buggy intermediate write
            # might have left things off — this handler is the only
            # callsite that bumps the activity to running.
            conn.execute(
                "UPDATE activity_steps SET current = 0 WHERE activity_id = ?",
                (activity_id,),
            )
            conn.execute(
                "UPDATE activity_steps SET current = 1 WHERE activity_id = ? AND seq = 1",
                (activity_id,),
            )
        response = _row_to_response(conn, row)
        _emit_state(pubsub, response)
        return response

    # Running path: find the current row.
    current_index = next((i for i, s in enumerate(steps) if int(s["current"]) == 1), -1)
    if current_index < 0:
        # Shouldn't happen — running activities always have a current
        # row. Treat as terminal so a buggy intermediate state can't
        # wedge the kiosk forever.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_current_step", "id": activity_id},
        )
    current_row = steps[current_index]

    # Pre-G2 path: the next row already exists in activity_steps.
    # This holds for in-flight activities created BEFORE G2 (all 5 rows
    # pre-seeded) AND for the special test fixture flow that backfills
    # legacy rows on top of a G2 propose. Skip the lazy-insert work
    # entirely — just flip ``current`` and bump version.
    if current_index + 1 < len(steps):
        next_row = steps[current_index + 1]
        # Reject ``choice_index`` for legacy linear flow — pre-G2
        # activities never had choices, so a client posting one is
        # confused. Cheap defense; the alternative is silent drop.
        # (G3 plan: rule 2's "choice_not_allowed" applies here too.)
        if advance_body.choice_index is not None:
            raise _bad_advance("choice_not_allowed", reason="legacy_linear_no_choices")
        ok, row = _attempt_transition(
            conn,
            activity_id=activity_id,
            expected_version=expected_version,
            new_state=target,
        )
        if not ok:
            raise VersionConflictError(int(row["version"]), str(row["state"]))
        target_seq = int(next_row["seq"])
        with conn:
            conn.execute(
                "UPDATE activity_steps SET current = 0 WHERE activity_id = ?",
                (activity_id,),
            )
            conn.execute(
                "UPDATE activity_steps SET current = 1 WHERE activity_id = ? AND seq = ?",
                (activity_id, target_seq),
            )
        response = _row_to_response(conn, row)
        _emit_state(pubsub, response)
        return response

    # Lazy-insert path (current_index == len(steps) - 1): the next
    # step (if any) does NOT yet exist in activity_steps. Recover the
    # template, evaluate the edge rule on the current step, and either
    # INSERT the next row or transition to completed.
    summary_raw = row["summary"]
    template_id: str | None = None
    if summary_raw:
        try:
            payload = json.loads(summary_raw)
            if isinstance(payload, dict):
                tid = payload.get("template_id")
                if isinstance(tid, str) and tid:
                    template_id = tid
        except json.JSONDecodeError:
            template_id = None
    if template_id is None:
        # No template_id → cannot resolve edges. Terminal fallback so
        # an old activity without the envelope contract still ends
        # cleanly instead of 500ing.
        return _terminal_advance(conn, pubsub, activity_id, expected_version)

    template = find_template_by_id(template_id)
    if template is None:
        # Template was removed/renamed between propose and advance.
        # Treat as terminal; the kiosk completes the activity rather
        # than wedging.
        _logger.warning(
            "advance: template %r not found for activity %s — terminating",
            template_id,
            activity_id,
        )
        return _terminal_advance(conn, pubsub, activity_id, expected_version)

    # Recover the current step's index in the template array. Branching
    # templates always have ``step_template_id`` populated (their step[0]
    # has an authored ``id`` per the schema's reachability invariant);
    # legacy linear templates fall back to seq-1.
    current_step_template_id = current_row["step_template_id"]
    current_template_index = _resolve_template_step_index(
        template.steps,
        step_template_id=current_step_template_id,
        fallback_array_index=int(current_row["seq"]) - 1,
    )
    if current_template_index < 0:
        return _terminal_advance(conn, pubsub, activity_id, expected_version)
    current_template_step = template.steps[current_template_index]

    # Load slot fills (G2 column).
    slot_fills_raw = row["slot_fills_json"]
    slot_fills: dict[str, str] = {}
    if slot_fills_raw:
        try:
            decoded = json.loads(slot_fills_raw)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            slot_fills = {str(k): str(v) for k, v in decoded.items()}

    # Edge resolution.
    target_template_step: Any = None
    chosen_label: str | None = None

    if current_template_step.choices is not None:
        # Rule 1: choices → choice_index REQUIRED.
        ci = advance_body.choice_index
        choices_tuple = current_template_step.choices
        if ci is None:
            raise _bad_advance("choice_required", choice_count=len(choices_tuple))
        if ci < 0 or ci >= len(choices_tuple):
            raise _bad_advance(
                "invalid_choice_index",
                choice_index=ci,
                choice_count=len(choices_tuple),
            )
        # Read the rendered label the kid actually saw from the
        # previous step's persisted ``choices_json`` (NOT from the
        # template — that has unrendered ``{slot}`` placeholders).
        # Fall through to the rendered-now value if the persisted
        # column is somehow corrupt — better than failing the advance.
        persisted_choices = _decode_choices_json(current_row["choices_json"])
        if persisted_choices is not None and 0 <= ci < len(persisted_choices):
            chosen_label = persisted_choices[ci].label
        else:
            chosen_label = render_with_slot_fills(choices_tuple[ci][0], slot_fills)
        target_step_id = choices_tuple[ci][1]
        for t_step in template.steps:
            if t_step.id == target_step_id:
                target_template_step = t_step
                break
    elif current_template_step.next is not None:
        # Rule 2: explicit next → choice_index must NOT be set.
        if advance_body.choice_index is not None:
            raise _bad_advance("choice_not_allowed", reason="current_step_uses_explicit_next")
        target_step_id = current_template_step.next
        for t_step in template.steps:
            if t_step.id == target_step_id:
                target_template_step = t_step
                break
    elif _is_branch_destination_leaf(template.steps, current_template_step):
        # Rule 2.5: branch-destination leaves terminate. They have no
        # outgoing edge of their own and are reached as the explicit
        # target of some step's ``choices[*].next``; implicit fall-through
        # to the next array entry is the wrong semantics here because
        # the next entry is typically a SIBLING branch's ending.
        # Phase K K14 Surface E: before completing, give the K14 ending
        # picker a chance to insert a themed interjection.
        if advance_body.choice_index is not None:
            raise _bad_advance("choice_not_allowed", reason="current_step_is_terminal")
        return _advance_to_ending_or_terminal(
            conn,
            pubsub,
            activity_id=activity_id,
            expected_version=expected_version,
            target=target,
            template=template,
            slot_fills=slot_fills,
            persona_id=row["persona_id"],
            current_row=current_row,
        )
    elif current_template_index + 1 < len(template.steps):
        # Rule 3: fall through to next array position.
        if advance_body.choice_index is not None:
            raise _bad_advance("choice_not_allowed", reason="current_step_is_linear")
        target_template_step = template.steps[current_template_index + 1]
    else:
        # Rule 4: terminal. Phase K K14 Surface E: before completing,
        # give the K14 ending picker a chance to insert a themed
        # interjection at ``seq = current_row.seq + 1`` so the kid
        # gets a song/joke wrap-up before the activity completes.
        if advance_body.choice_index is not None:
            raise _bad_advance("choice_not_allowed", reason="current_step_is_terminal")
        return _advance_to_ending_or_terminal(
            conn,
            pubsub,
            activity_id=activity_id,
            expected_version=expected_version,
            target=target,
            template=template,
            slot_fills=slot_fills,
            persona_id=row["persona_id"],
            current_row=current_row,
        )

    if target_template_step is None:
        # An ``id`` reference that didn't resolve — graph validator
        # should have caught this at template-load time, but defend
        # against runtime drift.
        _logger.error(
            "advance: edge target id not found in template %r for activity %s",
            template_id,
            activity_id,
        )
        return _terminal_advance(conn, pubsub, activity_id, expected_version)

    # Phase K K14 Surface B: if the resolved target is an auto song/joke
    # step AND the embedded surface is disabled, walk past it (and any
    # subsequent auto interjections) until a renderable template step is
    # found OR the walk falls off the end → terminate. The skip is
    # entirely server-side; the kid never sees a placeholder.
    is_enabled = {
        "song": _embedded_surface_enabled(conn, "song"),
        "joke": _embedded_surface_enabled(conn, "joke"),
    }
    target_template_index = _resolve_template_index_by_id(
        template.steps, target_template_step.id or ""
    )
    if target_template_index < 0:
        # The target step has no id (legacy linear step reached by
        # fall-through). Use the array-index path: find its position by
        # identity. Fallback to current_template_index + 1 since the
        # rule-3 branch above set it that way.
        for idx, t_step in enumerate(template.steps):
            if t_step is target_template_step:
                target_template_index = idx
                break
    skip_result = _skip_disabled_embedded(
        template_steps=template.steps,
        starting_index=target_template_index,
        starting_step=target_template_step,
        is_enabled=is_enabled,
    )
    if skip_result is None:
        # Every reachable step is a disabled-surface auto interjection.
        # Treat as terminal — the kid completes the activity cleanly.
        return _terminal_advance(conn, pubsub, activity_id, expected_version)
    target_template_step, _target_index = skip_result

    # Phase K K14 Surface B: when the (post-skip) target is an auto
    # song/joke step, pick a corpus entry from
    # ``template.recommended_themes`` and use ``build_interjection_step``
    # to produce the persisted row shape. Otherwise the legacy
    # ``_insert_next_step`` path renders a plain text/fork step.
    interjection_row: dict[str, Any] | None = None
    if target_template_step.kind in ("song", "joke") and target_template_step.auto is True:
        # Phase K K14: derive a deterministic seed for the corpus
        # pick from ``(activity_id, new_seq)`` per phase-k-plan §6 K14
        # step. Same ``(activity, position)`` always produces the same
        # interjection → recast-stability + telemetry replay both work.
        # The python ``hash`` builtin is salted per process, so we use
        # the first 8 bytes of a SHA-256 digest (stable across restarts)
        # interpreted as an unsigned int. The picker only does
        # ``seed % len(candidates)`` so any uniform-ish 64-bit value is
        # acceptable.
        new_seq_for_seed = int(current_row["seq"]) + 1
        seed_input = f"{activity_id}:{new_seq_for_seed}".encode()
        seed_value = int.from_bytes(
            hashlib.sha256(seed_input).digest()[:8],
            byteorder="big",
            signed=False,
        )
        interjection_row = _pick_embedded_corpus_step(
            conn,
            template=template,
            template_step=target_template_step,
            slot_fills=slot_fills,
            seed=seed_value,
            persona_id=row["persona_id"],
            new_seq=new_seq_for_seed,
        )
        # ``None`` means no corpus entry matched the theme filter — the
        # kid's experience would be a 404'd kiosk step; degrade to a
        # graceful skip via terminal (no row inserted). Logged INFO by
        # the picker.
        if interjection_row is None:
            return _terminal_advance(conn, pubsub, activity_id, expected_version)

    # Atomically: bump version + state, INSERT next step, mark current
    # ``not current`` (and write ``chosen_label`` if branching). All
    # under one transaction so a stale retry post-bump can't double-
    # insert (the version check inside ``_attempt_transition`` is the
    # gate).
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=target,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    new_seq = int(current_row["seq"]) + 1
    with conn:
        if interjection_row is not None:
            _insert_interjection_step_row(
                conn,
                activity_id=activity_id,
                interjection_row=interjection_row,
                new_seq=new_seq,
                previous_step_id=str(current_row["id"]),
                chosen_label=chosen_label,
            )
        else:
            _insert_next_step(
                conn,
                activity_id=activity_id,
                template_step=target_template_step,
                slot_fills=slot_fills,
                new_seq=new_seq,
                previous_step_id=str(current_row["id"]),
                chosen_label=chosen_label,
            )
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


def _advance_to_ending_or_terminal(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    *,
    activity_id: str,
    expected_version: int,
    target: str,
    template: Any,
    slot_fills: dict[str, str],
    persona_id: str | None,
    current_row: Any,
    chosen_label: str | None = None,
) -> ActivityResponse:
    """Phase K K14 Surface E: insert a themed ending interjection if the
    template defined one and the surface gate is on; otherwise complete.

    Called from :func:`post_advance` at the two "no more template steps"
    points (Rule 2.5 branch-destination-leaf and Rule 4 last-array-entry).
    Mirrors :func:`_terminal_advance`'s transition + WS broadcast shape so
    a kid who completes a template that has NO ending (or with the
    surface off, or whose corpus has no matching entry) still hits the
    exact-same completed-state path.

    On the happy path (ending built): same transaction shape as the
    embedded picker — version bump, current=0 on previous, INSERT new
    interjection at ``seq = current_row.seq + 1`` with ``current=1``,
    ``activity.state`` envelope on the pubsub. Activity stays at
    ``running`` (the kid still has one more step to clear before
    completion).

    The seed for the ending pick is derived from ``(activity_id,
    new_seq)`` — the same shape the embedded picker uses — so the same
    ``(activity, position)`` always produces the same ending entry.
    Replay + telemetry parity with the embedded path is the goal.
    """
    new_seq = int(current_row["seq"]) + 1
    seed_input = f"{activity_id}:{new_seq}:ending".encode()
    seed_value = int.from_bytes(
        hashlib.sha256(seed_input).digest()[:8],
        byteorder="big",
        signed=False,
    )
    ending_row = _build_ending_row(
        conn,
        template=template,
        seed=seed_value,
        persona_id=persona_id,
        slot_fills=slot_fills,
        new_seq=new_seq,
    )
    if ending_row is None:
        # No ending applicable: complete the activity as before.
        return _terminal_advance(conn, pubsub, activity_id, expected_version)

    # Ending row built — INSERT it as the kid's next step. Same
    # transaction shape as the embedded picker path: bump version,
    # flip previous current=0, INSERT new current=1 interjection row.
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=target,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    with conn:
        _insert_interjection_step_row(
            conn,
            activity_id=activity_id,
            interjection_row=ending_row,
            new_seq=new_seq,
            previous_step_id=str(current_row["id"]),
            chosen_label=chosen_label,
        )
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


def _terminal_advance(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    activity_id: str,
    expected_version: int,
) -> ActivityResponse:
    """Phase G G3: complete the activity (rule 4 — terminal node).

    Pulled out of :func:`post_advance` so all the "no successor"
    branches (template missing, edge unresolved, last-array entry)
    share the exact same transition + WS broadcast path.
    """
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_COMPLETED,
        additional_sets=(("ended_at", _now_iso()),),
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    with conn:
        conn.execute(
            "UPDATE activity_steps SET current = 0 WHERE activity_id = ?",
            (activity_id,),
        )
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/step-back", response_model=ActivityResponse)
def post_step_back(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Roll the current step back by one (kid hit Next prematurely on the kiosk).

    Mirror of :func:`post_advance` but in reverse — flips ``current=1`` from
    the current step to the prior one and bumps the activity version. Only
    valid from ``running``/``paused`` (other states have no current step to
    roll back from); also rejects when the current step is already ``seq=1``.
    """
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    if current_state not in {STATE_RUNNING, STATE_PAUSED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_transition",
                "current_state": current_state,
                "target_state": current_state,
            },
        )
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)

    steps = conn.execute(
        "SELECT id, seq, current, choices_json "
        "FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    current_index = next((i for i, s in enumerate(steps) if int(s["current"]) == 1), -1)
    if current_index <= 0:
        # current_index == -1: no current step (shouldn't happen in
        # running/paused, but defend against a malformed row).
        # current_index == 0: already on seq=1, nothing to roll back to.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_prior_step", "id": activity_id},
        )

    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=current_state,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    prev_seq = int(steps[current_index - 1]["seq"])
    cur_seq = int(steps[current_index]["seq"])
    # Phase G G6 fix: if the step we're rolling BACK to is a
    # choice-bearing step (post-G2 branching template), an unflip-only
    # rollback would leave seq>prev_seq in the table with the kid's
    # already-chosen path. The kiosk re-renders the choice buttons,
    # the kid taps one, and the advance handler's legacy "next row
    # exists" branch rejects ``choice_index`` with ``choice_not_allowed``
    # — wedging the activity with a 400 the kiosk surfaces as
    # ``advance: api error 400``. Fix: rewind the chosen path entirely
    # — DELETE rows with seq > prev_seq (so the lazy-insert path fires
    # on the next advance) and clear ``chosen_label`` on prev_seq (so
    # the choice slate is fresh; G3's _insert_next_step will rewrite
    # it on the new choice).
    #
    # Pre-G2 in-flight activities (5 rows pre-seeded, no choices_json
    # anywhere) keep the existing unflip-only behavior — deleting
    # their rows would lose body content that the lazy-insert path
    # can't reconstruct (their slot_fills_json defaults to '{}').
    prev_row_choices = steps[current_index - 1]["choices_json"]
    rolling_back_across_choice = prev_row_choices is not None
    with conn:
        if rolling_back_across_choice:
            conn.execute(
                "DELETE FROM activity_steps WHERE activity_id = ? AND seq > ?",
                (activity_id, prev_seq),
            )
            conn.execute(
                "UPDATE activity_steps SET current = 1, chosen_label = NULL "
                "WHERE activity_id = ? AND seq = ?",
                (activity_id, prev_seq),
            )
        else:
            conn.execute(
                "UPDATE activity_steps SET current = 0 WHERE activity_id = ? AND seq = ?",
                (activity_id, cur_seq),
            )
            conn.execute(
                "UPDATE activity_steps SET current = 1 WHERE activity_id = ? AND seq = ?",
                (activity_id, prev_seq),
            )
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/pause", response_model=ActivityResponse)
def post_pause(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """running → paused (idempotent).

    Step 23: a parent who clicks "pause" twice gets two 200s back with
    the SAME version both times — pausing an already-paused activity is
    a no-op rather than a 409. Same idea on the resume side. The
    second click MUST NOT bump the version, otherwise the optimistic
    concurrency check on every other mutation would race the panel's
    cached version on the next click.

    Step 23 iter-2 (L2): the idempotent-state check fires BEFORE the
    version check. A concurrent same-version double-tap (both racers
    saw version V; the first wins and bumps to V+1; the second arrives
    with stale-V) MUST still 200 because the target state is already
    reached. Reordering keeps the docstring's idempotency promise true
    even under that race.
    """
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    if current_state == STATE_PAUSED:
        # Idempotent no-op: return the row unchanged. We deliberately
        # skip ``_emit_state`` here so quiescent panels don't get a
        # spurious envelope when nothing changed. The version check is
        # bypassed on purpose — a stale ``If-Match-Version`` arriving
        # AFTER the row already reached the target state is still a
        # successful idempotent outcome from the parent's POV.
        return _row_to_response(conn, row)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    _enforce_transition(current_state, STATE_PAUSED)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_PAUSED,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/resume", response_model=ActivityResponse)
def post_resume(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """paused → running (idempotent).

    Mirror of :func:`post_pause` — calling resume on an already-running
    activity returns 200 with the same version, no envelope emit. See
    that function's docstring for the rationale (incl. the iter-2 L2
    reordering: state check fires before version check, so a stale
    ``If-Match-Version`` from a concurrent double-tap still 200s).
    """
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    if current_state == STATE_RUNNING:
        # Idempotent no-op (see post_pause).
        return _row_to_response(conn, row)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    _enforce_transition(current_state, STATE_RUNNING)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_RUNNING,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/end", response_model=ActivityResponse)
def post_end(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """approved/running → ended.

    Step 15: end-early writes ``parent_signal = -0.5`` and the seq of
    the step that was current at the moment of end (``ended_at_step``)
    to the matching labeled_events row. The current-step lookup runs
    before the row's ``current`` flag is touched downstream.
    """
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, STATE_ENDED)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    # Capture the in-progress step seq BEFORE we transition — the
    # transition itself doesn't clear the current flag, but we want the
    # value at end-time even if a future change does.
    ended_at_step = _current_step_seq(conn, activity_id)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_ENDED,
        additional_sets=(("ended_at", _now_iso()),),
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    _safe_update_parent_signal(
        conn,
        activity_id=activity_id,
        signal=PARENT_SIGNAL_END_EARLY,
        ended_at_step=ended_at_step,
    )
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/thumbs-up", response_model=ActivityResponse)
def post_thumbs_up(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Parent thumbs-up: write ``parent_signal = +1`` to labeled_events.

    No state transition — thumbs-up is a feedback signal independent of
    the activity lifecycle. Returns the current activity unchanged so
    the parent UI can confirm the click landed. Idempotent: a second
    click overwrites the same row with the same value.

    No ``If-Match-Version`` is required: thumbs-up doesn't modify the
    activity itself, so a concurrent state transition can't conflict
    with it.
    """
    row = _fetch_activity_row(conn, activity_id)
    _safe_update_parent_signal(conn, activity_id=activity_id, signal=PARENT_SIGNAL_THUMBS_UP)
    # Phase D step 20: thumbs-up boosts future picks of the same
    # signature. Idempotent at the parent's level (clicking twice
    # writes two rows; the consultation stacks weights, so a
    # double-click counts as extra love — small but acceptable side
    # effect, less surprising than de-duping behind the parent's back).
    _write_feedback(conn, activity_id=activity_id, kind=KIND_LOVED_IT)
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/didnt-work", response_model=ActivityResponse)
def post_didnt_work(
    activity_id: str,
    body: DidntWorkRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Flag any state as ``didnt_work`` (anti-signal feedback for Phase D)."""
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, STATE_DIDNT_WORK)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_DIDNT_WORK,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    # Phase D step 20: ``didnt_work`` is the hard veto. Always write
    # the row (with the signature from the persisted activity) — the
    # previous behaviour only wrote when ``reason`` was supplied, so
    # silent button presses produced zero anti-signal. Reason is
    # carried through when present.
    _write_feedback(
        conn,
        activity_id=activity_id,
        kind=KIND_DIDNT_WORK,
        reason=body.reason,
    )
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


# Maximum rows returned by ``GET /api/activities/proposed``. Pinned at
# 5 to match the legacy ``PROPOSED_QUEUE_CAP``; the parent dashboard's
# scrolling queue paints at most 5 cards at once so a larger fetch
# would just be extra wire weight. Keeping it as a module constant
# (rather than a query param) means the contract is visible to the
# parent UI's contract test in one place.
_PROPOSED_LIST_LIMIT = 5

# States that count as "currently in flight" for the ``include_active``
# branch of the list endpoint. ``proposed`` is excluded — it's the queue
# itself, and the parent UI distinguishes "what's next" (items) from
# "what's playing" (active). ``ended`` / ``dismissed`` / ``didnt_work``
# are terminal so they're skipped too. ``completed`` is the post-final-
# step state and stays visible until the parent ends or restarts the
# activity, hence it's still considered active here.
_ACTIVE_STATES: tuple[str, ...] = (
    STATE_APPROVED,
    STATE_RUNNING,
    STATE_PAUSED,
    STATE_COMPLETED,
)


class ProposedListResponse(BaseModel):
    """Wire shape for ``GET /api/activities/proposed``.

    Without ``include_active`` the response is ``{"items": [...]}``
    where ``items`` is at most :data:`_PROPOSED_LIST_LIMIT` activities
    in ``created_at DESC`` order. With ``include_active=true`` the same
    response carries an extra ``active`` field — the most recent
    non-terminal (``approved/running/paused/completed``) activity for
    the production session, or ``None`` when no such row exists. The
    parent dashboard uses this combined fetch to paint the suggestion
    queue + the currently-playing card in a single REST round-trip on
    mount, then keeps both in sync via the ``activity.state`` ws topic.
    """

    model_config = ConfigDict(frozen=True)

    items: list[ActivityResponse] = Field(default_factory=list)
    active: ActivityResponse | None = None


def _fetch_recent_proposed(
    conn: sqlite3.Connection,
    limit: int,
) -> list[ActivityResponse]:
    """Return up to ``limit`` ``proposed`` activities, newest first.

    Mirrors :func:`oldest_proposed_ids` from :mod:`toybox.core.queue`
    but flips the ordering — the parent queue wants newest-on-top so
    a freshly autonomous-proposed activity appears at the head of the
    scrolling card stack. Tie-breaks on ``id`` so two rows written in
    the same second (rare but possible at second precision) have a
    deterministic order.
    """
    rows = conn.execute(
        "SELECT * FROM activities WHERE state = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (PROPOSED_STATE, limit),
    ).fetchall()
    return [_row_to_response(conn, row) for row in rows]


def _fetch_active_activity(
    conn: sqlite3.Connection,
) -> ActivityResponse | None:
    """Return the most recent non-terminal activity, or ``None``.

    Filters to :data:`_ACTIVE_STATES` so terminal rows
    (``ended``/``dismissed``/``didnt_work``) and the queue itself
    (``proposed``) are excluded. ``created_at DESC`` then ``id DESC``
    keeps the ordering deterministic across same-second inserts —
    matches :func:`_fetch_recent_proposed`'s tiebreak so the wire
    contract is uniform between ``items`` and ``active``.
    """
    placeholders = ",".join("?" for _ in _ACTIVE_STATES)
    row = conn.execute(
        f"SELECT * FROM activities WHERE state IN ({placeholders}) "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        _ACTIVE_STATES,
    ).fetchone()
    if row is None:
        return None
    return _row_to_response(conn, row)


@router.get("/proposed", response_model=ProposedListResponse)
def get_proposed_list(
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
    include_active: bool = False,
) -> ProposedListResponse:
    """List up to 5 most-recent ``proposed`` activities for the parent queue.

    Parent-token scope only — the child kiosk consumes the ws
    ``activity.state`` topic and never needs the REST list. The
    ``include_active`` query flag adds an ``active`` field to the
    response carrying the currently-playing (non-terminal) activity so
    the parent dashboard can paint both the queue and the in-flight
    card in a single mount-time round-trip; subsequent updates flow
    over the ws topic.

    Declared BEFORE the ``/{activity_id}`` GET so FastAPI's path-
    matching picks this static route over the dynamic one — otherwise
    ``/proposed`` would route to ``get_activity(activity_id='proposed')``
    and 404 on the row lookup.
    """
    items = _fetch_recent_proposed(conn, _PROPOSED_LIST_LIMIT)
    active = _fetch_active_activity(conn) if include_active else None
    return ProposedListResponse(items=items, active=active)


@router.get("/{activity_id}", response_model=ActivityResponse)
def get_activity(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent, TokenScope.child}))],
) -> ActivityResponse:
    """Read-back endpoint used by tests + parent UI."""
    row = _fetch_activity_row(conn, activity_id)
    return _row_to_response(conn, row)


__all__ = [
    "ActivityResponse",
    "ActivityStepResponse",
    "ApproveRequest",
    "DidntWorkRequest",
    "ProposeRequest",
    "ProposedListResponse",
    "RegenerateRequest",
    "RoleAssignment",
    "STATE_APPROVED",
    "STATE_COMPLETED",
    "STATE_DIDNT_WORK",
    "STATE_DISMISSED",
    "STATE_ENDED",
    "STATE_PAUSED",
    "STATE_PROPOSED",
    "STATE_RUNNING",
    "get_activities_db",
    "get_judge_call",
    "router",
]
