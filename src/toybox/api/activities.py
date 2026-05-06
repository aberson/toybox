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
import json
import logging
import secrets
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..activities.content_resolver import (
    ResolvedRoom,
    ResolvedToy,
    resolve_child_profiles,
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
    generate,
    resolve_dispatch,
)
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
from ..core.auth import TokenScope
from ..core.pubsub import PubSub
from ..core.queue import (
    DISMISSED_STATE,
    PROPOSED_QUEUE_CAP,
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


class ActivityStepResponse(BaseModel):
    """Wire shape for one activity step.

    Phase F Step F6: ``action_slot`` carries the per-step action
    vocabulary key (one of :data:`toybox.image_gen.models.ACTION_SLOTS`)
    or ``None`` for legacy rows / templates that don't pin a slot.
    The kiosk renders the matching toy sprite (F7) when the slot is
    set AND the activity has at least one toy with a sprite for it.
    """

    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=1)
    body: str = Field(min_length=1)
    sfx: str | None = None
    expected_action: str | None = None
    current: bool = False
    action_slot: str | None = None


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


def _fetch_steps(conn: sqlite3.Connection, activity_id: str) -> list[ActivityStepResponse]:
    rows = conn.execute(
        "SELECT seq, body, sfx, expected_action, current, action_slot "
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
        )
        for r in rows
    ]


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
    return ActivityResponse(
        id=activity_id,
        state=str(row["state"]),
        version=int(row["version"]),
        title=title,
        summary=summary_raw if not title else None,
        persona_id=row["persona_id"],
        intent_source=row["intent_source"],
        child_ids=child_ids,
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        steps=_fetch_steps(conn, activity_id),
        metadata=metadata,
        trigger_phrase=trigger_phrase,
        persona_reasoning=persona_reasoning,
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
) -> None:
    summary_blob = json.dumps(summary_payload, sort_keys=True)
    created_at = _now_iso()
    with conn:
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, persona_id, child_ids, room_ids, "
            " toy_ids, intent_source, created_at, started_at, ended_at) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, NULL, NULL, ?, ?, NULL, NULL)",
            (
                activity_id,
                session_id,
                state,
                summary_blob,
                persona_id,
                None,
                intent_source,
                created_at,
            ),
        )
        for step in steps:
            conn.execute(
                "INSERT INTO activity_steps "
                "(id, activity_id, seq, body, sfx, expected_action, current, action_slot) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    activity_id,
                    step["seq"],
                    step["body"],
                    step.get("sfx"),
                    step.get("expected_action"),
                    1 if step.get("current") else 0,
                    # Phase F Step F6: per-step action slot. None for
                    # legacy callers / templates that don't set it.
                    step.get("action_slot"),
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
    """
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
    # - local+*: NotImplementedError (Step 26 / E2 deliverable)
    dispatch = resolve_dispatch()
    loop_tool_calls: list[dict[str, Any]] | None = None
    generator_path_for_recording = GENERATOR_PATH_OFFLINE
    if dispatch.adapter == ADAPTER_LOCAL:
        raise NotImplementedError(
            f"local adapter ships in Step 26 (E2); requested mode={dispatch.mode}"
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
    session_id = _ensure_session(conn, body.session_id)

    # Evict oldest first so the cap of 5 holds for the new row.
    evicted_ids = evict_oldest_for_capacity(conn, cap=PROPOSED_QUEUE_CAP)
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
        }
        for idx, step in enumerate(activity.steps)
    ]
    _persist_activity(
        conn,
        activity_id=activity.id,
        session_id=session_id,
        persona_id=activity.persona_id,
        intent_source=body.intent,
        summary_payload=summary_payload,
        steps=steps,
        state=PROPOSED_STATE,
    )

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


@router.post("/{activity_id}/advance", response_model=ActivityResponse)
def post_advance(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent, TokenScope.child}))],
) -> ActivityResponse:
    """Advance one step. approved → running on first call; running → running/completed otherwise."""
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
        "SELECT id, seq, current FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    if not steps:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "activity_has_no_steps", "id": activity_id},
        )

    if current_state == STATE_APPROVED:
        next_index = 0
    else:
        current_index = next((i for i, s in enumerate(steps) if int(s["current"]) == 1), -1)
        next_index = current_index + 1

    if next_index >= len(steps):
        target = STATE_COMPLETED
        ok, row = _attempt_transition(
            conn,
            activity_id=activity_id,
            expected_version=expected_version,
            new_state=target,
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

    additional: tuple[tuple[str, Any], ...] = ()
    if current_state == STATE_APPROVED:
        additional = (("started_at", _now_iso()),)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=target,
        additional_sets=additional,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    target_seq = int(steps[next_index]["seq"])
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
    "RegenerateRequest",
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
