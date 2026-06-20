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
import concurrent.futures
import functools
import json
import logging
import secrets
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..activities.adventure import (
    MAX_ADVENTURE_BEATS,
    GeneratedBeat,
    generate_boss_beat,
    generate_next_beat,
    stable_index,
)
from ..activities.content_resolver import (
    GenericDescriptor,
    ResolvedReward,
    ResolvedRoom,
    ResolvedToy,
    RewardActivityContext,
    resolve_child_profiles,
    resolve_reward,
    resolve_role_slots,
    resolve_rooms,
    resolve_toys,
)
from ..activities.element_corpus import get_element
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
from ..activities.roles import (
    ROLE_DISPLAY_NAMES,
    Role,
)
from ..activities.song_corpus import Song, pick_song
from ..activities.topic_extract import extract_themes
from ..ai.breaker import CircuitBreaker
from ..ai.judge import judge_and_persist
from ..ai.labeled_events import (
    GENERATOR_PATH_CLAUDE,
    GENERATOR_PATH_OFFLINE,
    INTERJECTION_SOURCE_PARENT_INSERT,
    PARENT_SIGNAL_DISMISS,
    PARENT_SIGNAL_END_EARLY,
    PARENT_SIGNAL_THUMBS_UP,
    append_interjection_event,
    record_generation,
    schedule_judge_sample,
    update_parent_signal,
)
from ..core import (
    boss_fights_enabled,
    jokes_enabled,
    play_standalone_enabled,
    play_target_depth,
    songs_enabled,
)
from ..core.auth import TokenScope
from ..core.game_linearity import get_game_linearity
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


def get_sync_ai_client() -> Any:
    """FastAPI dependency: return a SyncAIClient (or None if no token).

    Production: builds an AnthropicClient from the on-disk OAuth token.
    Tests override this dep to inject StubClient.complete_text_sync.
    Returns None when no token is on disk — annotator skips gracefully.
    """
    from ..ai.client import AnthropicClient  # noqa: PLC0415
    from ..ai.oauth import load_token  # noqa: PLC0415

    token = load_token()
    if token is None:
        return None
    return AnthropicClient(token)


def _annotate_and_persist_step_animations(
    conn: sqlite3.Connection,
    activity_id: str,
    persona_id: str | None,
    sync_client: Any,
) -> None:
    """Phase S S2: annotate each step with an avatar animation hint and
    persist the result into activity_steps.metadata_json before the WS
    broadcast fires.

    No-ops when sync_client is None (no OAuth token on disk) or when
    annotate_step_animations returns {} (Claude unavailable). The kiosk
    falls back to 'float' for steps without avatar_animation in metadata.
    """
    if sync_client is None:
        return
    from ..ai.animator import annotate_step_animations  # noqa: PLC0415

    steps = _fetch_steps(conn, activity_id)
    annotations = annotate_step_animations(steps, persona_id, sync_client)
    if not annotations:
        return

    # Fetch step rows so we can merge into existing metadata blobs.
    step_rows = conn.execute(
        "SELECT seq, metadata_json FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()

    with conn:
        for row in step_rows:
            seq = int(row["seq"])
            if seq not in annotations:
                continue
            existing: dict[str, Any] = {}
            raw = row["metadata_json"]
            if raw:
                try:
                    decoded = json.loads(str(raw))
                    if isinstance(decoded, dict):
                        existing = decoded
                except json.JSONDecodeError:
                    pass
            existing["avatar_animation"] = annotations[seq]
            conn.execute(
                "UPDATE activity_steps SET metadata_json = ? WHERE activity_id = ? AND seq = ?",
                (json.dumps(existing, sort_keys=True), activity_id, seq),
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
    # Phase M Step M3: optional reference to a Periodic Table element
    # corpus entry (``data/elements/elements.json``). When non-null, the
    # kiosk's ElementCard renders the matching sprite + symbol + name +
    # atomic number above the step text. The denormalized element fields
    # (``element_symbol`` / ``element_name`` / ``element_atomic_number``)
    # ride alongside in ``metadata`` so the kiosk doesn't need a
    # separate /api/elements/<id> fetch — matches the song/joke
    # ``metadata.audio_url`` / ``metadata.punchline`` denormalization
    # pattern. ``None`` on the overwhelming majority of steps (non-
    # Periodic-Table content).
    element_id: str | None = None
    # Phase R Step R3: optional Q&A gating. ``question`` is the text
    # displayed to the child and parent. ``question_pending`` is True
    # when question IS NOT NULL AND question_approved IS NULL — the child
    # kiosk hides Next ("Waiting for parent…") and the parent panel shows
    # approve/skip buttons. Both are None/False on the overwhelming
    # majority of steps (no Q&A on most templates).
    question: str | None = None
    question_pending: bool = False


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
    # Phase K K15 Surface S: True iff the most recent /advance roll
    # fired a spontaneity interjection (the kiosk dispatched into a
    # spontaneity-marked step). The K15 parent UI can render a "a toy
    # is being silly!" badge or skip the next "next-step ready" jingle
    # off this field without re-reading per-step ``metadata.interjection``.
    # Computed at handler time (NOT persisted to ``activities``) so the
    # field is False on every GET / list endpoint and True only on the
    # advance response that just inserted the spontaneity step. The
    # parent-insert endpoints (POST /insert-{joke,song}) intentionally
    # leave this False — those are explicit parent actions, not emergent
    # surprise, so the badge would be misleading. Defaults to False.
    interjection_pending: bool = False
    # Phase K K13: explanation for a synthetic ``state="dismissed"``
    # response returned when a standalone intent (``request_song`` /
    # ``request_joke``) hits a disabled surface flag or content master.
    # The propose call returns HTTP 200 with this field set to
    # ``"surface_disabled"`` and no persisted activity row / no
    # ``activity.state`` WS envelope. ``None`` on every other code path
    # (real activities never carry a reason).
    reason: str | None = None
    # Phase L Step L4: per-activity reward type selected by the parent
    # at approve time. ``None`` ONLY for legacy pre-L activity rows
    # whose ``activities.reward_type`` column is NULL — post-L4 the
    # approve handler always writes a value (defaulting to ``"random"``).
    # The kiosk reads this off the WS envelope to know which reward
    # step will be appended at activity end. See
    # documentation/phase-l-plan.md §2 for the NULL-not-coerced contract.
    reward_type: Literal["picture", "joke", "song", "random", "none"] | None = None
    # Phase O Step O2: surface the activity's source template id at the
    # top of the wire response. Persisted in the summary envelope's
    # ``template_id`` sibling key (see ``_do_propose`` summary_payload
    # construction); also mirrored on ``metadata["template_id"]`` so the
    # two surfaces stay in lockstep. ``None`` only on legacy / template-
    # less rows whose summary envelope predates the field.
    template_id: str | None = None
    # Phase O Step O2: surface the source template's recommended_themes
    # so the parent UI's ``categorize()`` helper can bucket activities
    # into Adventures / Elements / Feelings & Friends without a separate
    # template fetch. ``[]`` when the template omits the field or the
    # row is template-less. List of plain strings (Theme.value); the
    # wire shape deliberately does NOT leak the Theme enum class name.
    recommended_themes: list[str] = Field(default_factory=list)


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
    # When true, the propose handler reads the most-recent unexpired
    # transcripts, extracts themes via
    # :func:`toybox.activities.topic_extract.extract_themes`, and biases
    # the template picker toward those themes (no-op when no themes
    # match — see :func:`_apply_preferred_themes`). Default ``False``
    # for back-compat with existing tests + mic-driven proposals (which
    # already carry the transcript's literal phrase via
    # :data:`Intent.phrase`). Frontend "Trigger now" + "New activity"
    # set this to ``True`` so manual buttons inherit recent context.
    use_recent_transcripts: bool = False
    # Phase O follow-up: parent Play sub-tab category filter. When set,
    # restricts the template pool to ones that would categorize() to the
    # same bucket on the frontend — so "Trigger now" from the Elements
    # sub-tab produces an element activity, etc. Maps directly to the
    # frontend ``categorize()`` precedence: Elements > Feelings & Friends
    # > Adventures. None (the "All" sub-tab / no-tab / mic-driven path)
    # preserves the existing behavior. Soft-fallback semantics in
    # :func:`_apply_category_filter`: when no templates match, the
    # filter degrades to no-op rather than starve the picker.
    category: Literal["adventures", "elements", "feelings-friends"] | None = None
    # Phase R Step R4: optional template pin. When set, the generator
    # bypasses the slot-picker and uses the named template directly.
    # Supplied by the search UI's "Play again"/"Try this" buttons so a
    # parent can replay a specific past template rather than getting a
    # random activity of the same intent.  When the template_id is
    # unknown (template deleted/renamed), generate() falls back to the
    # normal picker and logs a warning.
    template_id: str | None = None
    # Phase W Step W4: dynamic adventure mode. When True, the propose
    # handler creates the activity with ``activities.adventure=1`` and
    # seeds the FIRST beat via the adventure engine
    # (:mod:`toybox.activities.adventure`) instead of selecting + persisting
    # a template; subsequent beats are generated at advance time. Default
    # ``False`` so a normal propose is byte-identical to today.
    adventure: bool = False


class ApproveRequest(BaseModel):
    """Body for ``POST /api/activities/{id}/approve``.

    Phase L Step L4: ``reward_type`` selects the kind of reward step
    that fires at activity end. Server applies the default ``"random"``
    when the parent UI omits the field, persisted to
    ``activities.reward_type``. ``None`` from the wire → server writes
    ``"random"``; persisted NULL means "legacy pre-L row" and is never
    produced by this code path post-L4. See documentation/phase-l-plan.md
    §"ApproveRequest" + §8.

    L follow-up Change D: the ``"none"`` member is the explicit opt-out
    — the parent picked "no reward this activity". Persisted verbatim
    so we can tell "parent opted out" apart from "row predates Phase L"
    (NULL) in metrics.

    L follow-up Change E: ``reward_id`` is the optional specific
    picture-reward pick from the second dropdown. Only meaningful when
    ``reward_type == "picture"``; ignored otherwise. Persisted into
    ``activities.slot_fills_json`` under the reserved ``__reward_id``
    key — no new column — so the L3 resolver can prefer the pin at
    advance time. Missing/archived/deleted-between-approve-and-play
    pins fall back to the random tag-match pool pick (resolver's
    contract; see ``_pick_picture``).
    """

    child_ids: list[str] | None = None
    reward_type: Literal["picture", "joke", "song", "random", "none"] | None = None
    reward_id: str | None = None


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
    # Same semantics as :attr:`ProposeRequest.use_recent_transcripts`.
    # Regenerate funnels through :func:`_do_propose` so the flag rides
    # the same code path.
    use_recent_transcripts: bool = False


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


class ApproveQuestionRequest(BaseModel):
    """Phase R Step R3: body for POST /api/activities/{id}/approve-question.

    ``result`` is the parent's resolution for the current step's Q&A gate:
    ``"approved"`` when the child gave a satisfactory answer (sets
    ``question_approved=1``), ``"skipped"`` when the parent decides to
    skip the check (sets ``question_approved=2``). Either value unblocks
    the advance path. ``version`` is the current activity version for the
    optimistic-concurrency check (same 409 pattern as other mutations).
    """

    model_config = ConfigDict(extra="forbid")

    result: Literal["approved", "skipped"]
    version: int = Field(ge=1)


class ApproveQuestionResponse(BaseModel):
    """Phase R Step R3: response from POST /api/activities/{id}/approve-question."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=1)


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


def _enrich_element_metadata(
    metadata: dict[str, Any] | None,
    element_id: str | None,
) -> dict[str, Any] | None:
    """Phase M Step M3 — denormalize element corpus fields into step metadata.

    When a step has a non-null ``element_id``, resolve it via the
    element corpus and merge ``element_id`` / ``element_symbol`` /
    ``element_name`` / ``element_atomic_number`` into the step's
    metadata blob. The kiosk's ElementCard reads these fields from
    ``step.metadata`` without needing a separate ``/api/elements/<id>``
    fetch — same pattern as song step ``metadata.audio_url`` and joke
    step ``metadata.punchline`` denormalization at K13.

    Defensive on miss: if the corpus lookup returns ``None`` (corpus
    drift between template-load gate and serialize time — unlikely,
    but the kiosk falls back to the generic periodic-table avatar on
    sprite 404, so a missing record degrades the same way).

    Returns the (possibly new) metadata dict, or ``None`` when there
    is no element_id AND the incoming metadata was empty.
    """
    if element_id is None:
        return metadata
    element = get_element(element_id)
    if element is None:
        # Corpus drift: the validator gate caught nothing at load
        # time but the entry is now missing. Surface the element_id
        # so the kiosk can render the fallback avatar; symbol/name
        # are intentionally absent — ElementCard tolerates missing
        # fields per phase-m-plan.md §5.3.
        merged: dict[str, Any] = dict(metadata) if metadata else {}
        merged["element_id"] = element_id
        return merged
    merged_meta: dict[str, Any] = dict(metadata) if metadata else {}
    merged_meta["element_id"] = element.id
    merged_meta["element_symbol"] = element.symbol
    merged_meta["element_name"] = element.name
    merged_meta["element_atomic_number"] = element.atomic_number
    return merged_meta


def _resolve_element_id_for_persisted_step(
    template_id: str | None,
    step_template_id: str | None,
) -> str | None:
    """Phase M Step M3 — look up the template step's ``element_id``.

    The ``activity_steps`` table doesn't carry ``element_id`` as its
    own column (kept additive to avoid a migration). The template
    cache is already in process memory; we just resolve the step by
    its ``step_template_id`` and read the field directly. Returns
    ``None`` whenever the template / step can't be resolved or the
    step has no element_id — graceful for legacy rows.
    """
    if template_id is None or step_template_id is None:
        return None
    template = find_template_by_id(template_id)
    if template is None:
        return None
    for step in template.steps:
        if step.id == step_template_id:
            return step.element_id
    return None


def _fetch_steps(
    conn: sqlite3.Connection,
    activity_id: str,
    *,
    template_id: str | None = None,
) -> list[ActivityStepResponse]:
    rows = conn.execute(
        "SELECT seq, body, sfx, expected_action, current, action_slot, "
        " choices_json, chosen_label, kind, metadata_json, step_template_id, "
        " question, question_approved "
        "FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    responses: list[ActivityStepResponse] = []
    for r in rows:
        # Phase M Step M3 — resolve element_id via the template lookup
        # so the kiosk's ElementCard can render the sprite + denormalized
        # element fields. Template lookup is process-cached; no DB hit.
        element_id = _resolve_element_id_for_persisted_step(template_id, r["step_template_id"])
        metadata = _decode_step_metadata_json(r["metadata_json"])
        metadata = _enrich_element_metadata(metadata, element_id)
        # Phase R Step R3: Q&A gating columns.
        question_val: str | None = r["question"] if "question" in r.keys() else None
        question_approved_val = r["question_approved"] if "question_approved" in r.keys() else None
        question_pending = question_val is not None and question_approved_val is None
        responses.append(
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
                metadata=metadata,
                element_id=element_id,
                question=question_val,
                question_pending=question_pending,
            )
        )
    return responses


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
        # Phase M Step M3 — surface the template-time element_id on the
        # preview wire too (proposed / approved states use this path).
        # The denormalized element fields ride in ``metadata`` so the
        # kiosk's ElementCard renders identically on preview and at
        # runtime.
        preview_metadata = _enrich_element_metadata(None, step.element_id)
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
                element_id=step.element_id,
                metadata=preview_metadata,
                # Phase R Step R3: surface the template-time question on
                # the preview wire so the parent's suggestion card shows
                # a Q&A step before approval. ``question_pending`` is True
                # on the preview because no row exists yet to record a
                # resolution (mirrors the freshly-inserted runtime row's
                # NULL ``question_approved``). ``None`` question →
                # not pending, byte-identical for non-Q&A templates.
                question=step.question,
                question_pending=step.question is not None,
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
    #
    # Phase M Step M3 (iter-2 fix): resolve ``template_id`` from the
    # summary envelope UNCONDITIONALLY — it's the input to TWO orthogonal
    # decisions:
    #
    #   1. Whether to call ``_render_template_plan_steps`` (the full
    #      preview) — gated on state ∈ {proposed, approved}.
    #   2. Whether ``_fetch_steps`` can resolve per-step ``element_id``
    #      via template lookup — needed at EVERY state where the kiosk
    #      renders a step (running / paused / ended / completed /
    #      did_not_work).
    #
    # Iter-1 conflated the two and only set ``template_id_for_plan`` for
    # the preview states. Effect: ElementCard rendered on the parent
    # dashboard preview, then evaporated on the kiosk as soon as the
    # activity transitioned to ``running``. Wire-shape test:
    # ``tests/integration/test_element_id_wire_shape.py``.
    state = str(row["state"])
    template_id: str | None = None
    if summary_raw:
        try:
            payload = json.loads(summary_raw)
            if isinstance(payload, dict):
                tid = payload.get("template_id")
                if isinstance(tid, str) and tid:
                    template_id = tid
        except json.JSONDecodeError:
            pass

    steps: list[ActivityStepResponse] | None = None
    if template_id is not None and state in (STATE_PROPOSED, STATE_APPROVED):
        slot_fills_raw = row["slot_fills_json"] if "slot_fills_json" in row.keys() else None
        steps = _render_template_plan_steps(template_id, slot_fills_raw)
    if steps is None:
        # Phase M Step M3 — thread template_id so the runtime read can
        # resolve per-step element_id via the template lookup. Falls
        # back to None on legacy / template-less rows; _fetch_steps
        # tolerates that.
        steps = _fetch_steps(conn, activity_id, template_id=template_id)

    # Phase L Step L5: the K15 spontaneity advance-hook (the only path
    # that ever flipped ``interjection_pending`` to True) was removed.
    # The field stays on the wire model so the parent UI's existing
    # conditional rendering keeps working — it just permanently reads
    # False now. A future cleanup phase can drop the field entirely
    # once the parent UI's reference is also removed.
    interjection_pending = False

    # Phase L Step L4: surface ``reward_type`` at the top of the wire
    # response. NULL in the DB (pre-L legacy rows where the column was
    # never written) maps to ``None`` on the wire — we deliberately do
    # NOT coerce NULL to ``"random"`` per plan §2 ("that would lie
    # about a never-set value"). Tolerant of rows whose persisted value
    # somehow drifted outside the four-member literal — we surface as
    # ``None`` rather than crash the GET path.
    reward_type_value: Literal["picture", "joke", "song", "random", "none"] | None = None
    if "reward_type" in row.keys():
        raw_reward_type = row["reward_type"]
        if isinstance(raw_reward_type, str) and raw_reward_type in (
            "picture",
            "joke",
            "song",
            "random",
            "none",
        ):
            reward_type_value = raw_reward_type  # type: ignore[assignment]

    # Phase O Step O2: surface ``template_id`` + the template's
    # ``recommended_themes`` at the top of the wire response so the
    # parent UI's categorize() helper can bucket activities without an
    # extra template fetch. ``template_id`` was already extracted above
    # for the step-render decision; mirror it onto ``metadata`` too so
    # the legacy metadata-keyed consumers stay in lockstep with the new
    # typed top-level field (single source of truth — both surfaces
    # always carry the same value). Empty themes list on legacy /
    # template-less rows + templates that omit the field.
    if template_id is not None:
        metadata = {**metadata, "template_id": template_id}
    recommended_themes_value: list[str] = []
    if template_id is not None:
        template_for_themes = find_template_by_id(template_id)
        if template_for_themes is not None:
            # ``_Template.recommended_themes`` is ``tuple[Theme, ...]``;
            # Theme is a StrEnum so ``.value`` is already lowercased
            # ASCII. Surface plain strings so the wire shape is a JSON
            # array of literals (NOT the Theme class name).
            recommended_themes_value = [
                theme.value for theme in template_for_themes.recommended_themes
            ]

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
        interjection_pending=interjection_pending,
        template_id=template_id,
        recommended_themes=recommended_themes_value,
        reward_type=reward_type_value,
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
    adventure: bool = False,
) -> None:
    """Insert one ``activities`` row plus ONLY the first step row.

    Phase W Step W4: ``adventure`` sets ``activities.adventure=1`` so the
    advance handler generates each subsequent step as a beat instead of
    reading a template row. Default ``False`` keeps a normal propose
    byte-identical.

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
            " toy_ids, intent_source, created_at, started_at, ended_at, slot_fills_json, "
            " adventure) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?, ?)",
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
                1 if adventure else 0,
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
                " choices_json, step_template_id, kind, metadata_json, question, expected_answer) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    # Phase R Step R3 / Phase W Step W3: thread the Q&A
                    # gating fields from the template step onto the row.
                    # ``None`` for every step that doesn't author a
                    # question / expected_answer (the overwhelming
                    # majority), so this INSERT stays byte-identical to
                    # the pre-W3 behavior for existing templates.
                    first.get("question"),
                    first.get("expected_answer"),
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
            # Standalone song/joke steps never carry a Q&A gate; keep the
            # keys present (defaulting to None) so the persistence layer's
            # ``first.get("question")`` reads NULL symmetrically with the
            # template path.
            "question": None,
            "expected_answer": None,
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


def _do_propose_adventure(
    body: ProposeRequest,
    conn: sqlite3.Connection,
    pubsub: PubSub,
    *,
    sync_client: Any = None,
) -> ActivityResponse:
    """Phase W Step W4: propose flow for a dynamic adventure.

    Creates the activity with ``activities.adventure=1`` and seeds the
    FIRST beat via :func:`toybox.activities.adventure.generate_next_beat`
    instead of selecting + persisting a template. Mirrors
    :func:`_do_propose_standalone` for persona pick / session / eviction /
    persistence so the wire envelope is shaped identically; the only
    differences are ``adventure=True`` on the activities row and the
    beat-shaped step.

    Beat 0 is generated ONLINE when the capability gate is green (the
    Claude path), else OFFLINE. The household ``game_linearity`` dial
    decides whether the beat emits choices. ``summary.template_id`` is
    ``None`` so the GET path falls back to ``_fetch_steps`` (the persisted
    beat row is the source of truth — same as the standalone surface).
    """
    effective_persona_id = body.persona_id
    persona_meta: dict[str, Any] | None = None
    if effective_persona_id is None:
        picked = _pick_random_library_persona(conn)
        if picked is not None:
            effective_persona_id = picked["id"]
            persona_meta = picked

    # Resolve the cast (toy display names) best-effort — a corrupt toy row
    # must not break propose; the engine falls back to generic descriptors
    # when the cast is empty.
    resolved_toys: list[ResolvedToy] = []
    try:
        resolved_toys = resolve_toys(conn)
    except sqlite3.Error:
        _logger.warning(
            "content_resolver.resolve_toys failed on adventure propose; "
            "the engine will use generic descriptors",
            exc_info=True,
        )
    cast = _build_adventure_cast(resolved_toys)

    # Linearity dial (W2): linear adventures emit no choices. Read fresh so
    # a parent UI change takes effect on the next propose.
    linear = get_game_linearity(conn) == "linear"

    # MEDIUM-2 fix: beat 0 MUST share the SAME seed every later beat uses, or
    # the offline theme disagrees between the opener and the rest. Advance
    # derives its seed from the activity id (``_adventure_seed_for``), so we
    # mint the id FIRST and seed beat 0 the same way — ``body.seed`` is no
    # longer used for adventures.
    activity_id = str(uuid.uuid4())
    seed = _adventure_seed_from_id(activity_id)

    # Online iff a sync client is wired. The inner capability gate inside
    # ``_make_adventure_online_call`` is authoritative and degrades to offline
    # on any non-green gate, so a separate probe here would be redundant
    # (LOW-4) — mirror the advance path's ``online = sync_client is not None``.
    online = sync_client is not None

    beat = _generate_adventure_beat(
        conn,
        sync_client,
        history=(),
        cast=cast,
        beat_index=0,
        linear=linear,
        seed=seed,
        online=online,
    )

    session_id = _ensure_session(conn, body.session_id)

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
        "title": "Adventure",
        "metadata": metadata_envelope,
        # No template — the beats are generated. ``_render_template_plan_steps``
        # short-circuits on a missing template_id, so the GET path falls
        # back to ``_fetch_steps`` which reads the persisted beat rows.
        "template_id": None,
    }
    steps_persist = [
        {
            "seq": 1,
            "body": beat.body,
            "sfx": None,
            "expected_action": None,
            "current": False,  # _persist_activity forces current=1 on insert
            "action_slot": None,
            "step_id": None,
            "choices_rendered": beat.choices,
            "kind": beat.kind,
            "metadata": None,
            "question": None,
            "expected_answer": None,
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
        toy_ids=[t.id for t in resolved_toys],
        slot_fills={},
        adventure=True,
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
    sync_client: Any = None,
) -> ActivityResponse:
    """Shared propose-and-persist helper.

    Phase W Step W4: ``sync_client`` is the FastAPI-injected sync AI client
    used ONLY by the adventure branch to seed the first beat online when
    the capability gate is green (offline fallback otherwise). ``None``
    for the regular template path.

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

    # Phase W Step W4: dynamic adventure mode. When the parent requests an
    # adventure, seed the first beat via the engine instead of selecting +
    # persisting a template. Runs BEFORE the template-generator setup so an
    # adventure never touches the catalog picker. A normal propose
    # (``adventure`` false) skips this entirely and is byte-identical.
    if body.adventure:
        return _do_propose_adventure(body, conn, pubsub, sync_client=sync_client)

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

    # Manual buttons ("Trigger now" + "New activity") set
    # ``use_recent_transcripts`` so the next propose biases toward
    # whatever the kid was just talking about. We extract themes here
    # and pass them through to the offline picker. Best-effort: a
    # transcript-read failure or zero matches degrades to no-bias
    # (current behavior). Capped at 20 recent rows — that's roughly
    # the last 5–10 minutes of speech, plenty of context without
    # turning every propose call into a long scan.
    preferred_themes: tuple[str, ...] = ()
    # First transcript text + extracted themes are kept around so the
    # "Why this?" panel can surface the bias provenance ("kid said X →
    # we biased toward [adventure]"). Set only when bias actually had
    # input to work with — empty when no transcripts or no theme match.
    bias_source_phrase: str | None = None
    if body.use_recent_transcripts:
        try:
            transcript_rows = conn.execute(
                "SELECT text FROM transcripts WHERE text IS NOT NULL "
                "ORDER BY ended_at DESC LIMIT 20"
            ).fetchall()
            transcript_texts = [str(r["text"]) for r in transcript_rows]
            extracted = extract_themes(transcript_texts)
            preferred_themes = tuple(str(t) for t in extracted)
            if preferred_themes:
                _logger.info(
                    "propose: biasing toward themes %s from %d recent transcript(s)",
                    list(preferred_themes),
                    len(transcript_texts),
                )
                # Most-recent transcript becomes the "why this?" trigger
                # phrase fallback so the parent can see what the kid
                # said that drove the bias.
                if transcript_texts:
                    bias_source_phrase = transcript_texts[0]
        except sqlite3.Error:
            _logger.warning(
                "propose: recent-transcript read failed; falling back to no-bias",
                exc_info=True,
            )
    # Phase W Step W2: read the household game-linearity dial once per
    # propose. When set to "linear", the offline generator excludes any
    # template with a branching step. Read fresh per call so a parent UI
    # change takes effect on the next propose without a restart. The
    # getter degrades to the "nonlinear" default if the row is missing or
    # corrupt, so this never breaks propose.
    linear_only = get_game_linearity(conn) == "linear"
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
                preferred_themes=preferred_themes,
                category=body.category,
                pinned_template_id=body.template_id,
                linear_only=linear_only,
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
            preferred_themes=preferred_themes,
            category=body.category,
            pinned_template_id=body.template_id,
            linear_only=linear_only,
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
    elif bias_source_phrase is not None and bias_source_phrase.strip():
        # No explicit trigger phrase, but the bias path read recent
        # transcripts and found themes — surface the most-recent one
        # so "Why this?" shows the kid's actual speech instead of the
        # generic "(no trigger — proposed manually)" fallback.
        metadata["trigger_phrase"] = bias_source_phrase.strip()[:512]
    metadata["persona_reasoning"] = _build_persona_reasoning(
        caller_supplied=body.persona_reasoning,
        intent=body.intent,
        persona_meta=persona_meta,
    )
    # Append the bias provenance to ``persona_reasoning`` when the
    # transcript-bias path actually had themes to use. Lets the parent
    # see that the picker was nudged toward a specific theme set
    # rather than just hitting the default pool — useful both for
    # trust ("the system is listening") and debugging ("the bias
    # picked adventure even though I wanted magic").
    if preferred_themes:
        metadata["persona_reasoning"] = (
            f"{metadata['persona_reasoning']} "
            f"(biased toward {', '.join(preferred_themes)} from recent speech)"
        )
    # H3: when the loop-mode dispatch caught a narrow transient failure
    # and fell back to the offline generator, surface the reason on the
    # activity's metadata envelope so an operator running queries
    # against ``labeled_events.activity_json`` can compute the % of
    # intended loop calls that fell back vs. real Claude outages.
    if loop_fallback_reason is not None:
        metadata["fallback_reason"] = loop_fallback_reason
    # Phase K (issue #135): title is provisionally the generator's
    # already-rendered value. When ``role_slot_overlay`` is non-empty
    # the title is re-rendered below from ``template_for_roles.title``
    # so role-name placeholders (``{trickster}`` etc.) resolve to the
    # picked toy display names. Without that re-render the literal
    # ``{role_name}`` leaks to the parent suggestion card + kiosk header.
    rendered_title = activity.title
    summary_payload = {
        "title": rendered_title,
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
            # Phase R Step R3 / Phase W Step W3: thread the Q&A gating
            # fields from the generated runtime step through to the
            # persistence layer so the first-step INSERT populates the
            # ``activity_steps.question`` / ``expected_answer`` columns.
            # ``None`` on every non-Q&A step (the overwhelming majority).
            "question": step.question,
            "expected_answer": step.expected_answer,
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
    # SlotRegistry word-list names). Also re-render the title + step
    # bodies + choice labels NOW so the persisted ``activity_steps`` row
    # carries the substituted text — the generator ran without role
    # context so its initial render left the placeholders intact.
    #
    # Issue #135 fix: the title re-render uses ``template_for_roles.title``
    # (the raw template text with placeholders intact), NOT
    # ``activity.title`` (which the generator already partial-rendered).
    # Re-rendering the partial would be a no-op because role placeholders
    # are still literal in the partial — we MUST render from the raw
    # template to pick up the merged role fills.
    if role_slot_overlay:
        slot_fills_arg.update(role_slot_overlay)
        # Re-render title from raw template (guarded — ``template_for_roles``
        # is only set when a role-bearing template was found above; an
        # empty overlay won't reach this branch so the guard is belt-and-
        # suspenders).
        if template_for_roles is not None:
            rendered_title = render_with_slot_fills(template_for_roles.title, slot_fills_arg)
            summary_payload["title"] = rendered_title
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
        # Re-encode summary_payload now that metadata changed. Reuse
        # ``rendered_title`` (set above when role_slot_overlay was
        # non-empty) so the role-substituted title is preserved.
        summary_payload = {
            "title": rendered_title,
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

    # Phase L Step L5: the K14 ending auto-append surface was deleted —
    # ``_build_ending_row`` no longer exists. Phase L's per-activity
    # reward step (resolved by :func:`resolve_reward` and inserted at
    # terminal advance, see L3/L4) replaces it. Propose-time inserts
    # only seq=1; the L4 reward row is appended lazily when the kid
    # clears the last renderable template step. See
    # ``documentation/phase-l-plan.md`` for the surface-deletion
    # rationale.

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
    sync_client: Annotated[Any, Depends(get_sync_ai_client)],  # Phase W W4
) -> ActivityResponse:
    """Generate a new activity at ``proposed``. Drops oldest if cap reached.

    Sync handler: SQLite work runs on the FastAPI threadpool worker.
    The judge sample is scheduled by :func:`_do_propose` via
    :func:`toybox.ai.labeled_events.schedule_judge_sample`, which spins
    up a short-lived daemon thread to host an event loop for the
    detached coroutine (the kid-facing path stays sync).
    """
    return _do_propose(body, conn, pubsub, judge_call=judge_call, sync_client=sync_client)


@router.post("/{activity_id}/approve", response_model=ActivityResponse)
def post_approve(
    activity_id: str,
    body: ApproveRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
    sync_client: Annotated[Any, Depends(get_sync_ai_client)],  # Phase S S2
) -> ActivityResponse:
    """proposed → approved (optimistically).

    Phase L Step L4 writes two new fields on this transition:

    * ``activities.reward_type`` — defaults to ``"random"`` when the
      ApproveRequest omits the field. Persisted alongside the state
      transition in the same UPDATE so a stale retry can't end up with
      the reward type written but the state unchanged.
    * ``activities.slot_fills_json["__template_id"]`` — reserved key
      the L3 reward resolver reads at advance time to look up the
      template's ``recommended_themes``. We pull the template id from
      the persisted summary envelope (``{"title", "metadata",
      "template_id"}``) and rewrite the slot fills blob with the key
      added. Pre-L approves where the summary envelope is missing the
      template_id (legacy plaintext summaries) leave slot_fills_json
      unchanged — the resolver tolerates the missing key.
    """
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, STATE_APPROVED)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)

    child_ids = body.child_ids or _resolve_default_child_ids(conn)
    encoded_children = json.dumps(child_ids) if child_ids else None

    # Phase L Step L4: default-apply ``random`` when the caller omits
    # the field. The DB column is nullable to preserve the "legacy
    # pre-L row = NULL" distinction, but every post-L4 approve writes
    # a concrete value.
    reward_type_value: str = body.reward_type or "random"

    # L follow-up Change E: capture the specific picture pick (when
    # set) so the slot_fills writer below persists it. We honour
    # ``reward_id`` ONLY when the approve also pins
    # ``reward_type == "picture"`` — the second dropdown is hidden on
    # other reward types, so a non-None ``reward_id`` arriving with
    # ``reward_type != "picture"`` is silently dropped rather than
    # 422-rejecting (defensive against a client racing the dropdown).
    pinned_reward_id: str | None = None
    if reward_type_value == "picture" and body.reward_id:
        pinned_reward_id = body.reward_id

    # Phase L Step L4: bolt ``__template_id`` onto ``slot_fills_json``
    # so the L3 reward resolver can find the template's
    # ``recommended_themes``. The template id lives on the summary
    # envelope's top-level ``template_id`` key (written at propose-time
    # by ``_persist_activity``). We decode it best-effort here — a
    # legacy plaintext summary or a malformed envelope leaves the
    # slot_fills unchanged and the resolver falls through to the
    # transcript-only theme source.
    template_id: str | None = None
    summary_raw = row["summary"]
    if summary_raw:
        try:
            summary_payload = json.loads(summary_raw)
        except json.JSONDecodeError:
            summary_payload = None
        if isinstance(summary_payload, dict):
            tid = summary_payload.get("template_id")
            if isinstance(tid, str) and tid:
                template_id = tid

    slot_fills_blob: str | None = None
    # L follow-up Change E: write slot_fills whenever EITHER template_id
    # is resolvable OR the parent pinned a specific reward — both keys
    # share the same JSON blob, so we serialize once.
    if template_id is not None or pinned_reward_id is not None:
        slot_fills_raw = row["slot_fills_json"] if "slot_fills_json" in row.keys() else None
        existing_slot_fills: dict[str, Any] = {}
        if slot_fills_raw:
            try:
                decoded_slot_fills = json.loads(slot_fills_raw)
            except json.JSONDecodeError:
                decoded_slot_fills = None
            if isinstance(decoded_slot_fills, dict):
                existing_slot_fills = decoded_slot_fills
        if template_id is not None:
            existing_slot_fills["__template_id"] = template_id
        if pinned_reward_id is not None:
            existing_slot_fills["__reward_id"] = pinned_reward_id
        # ``sort_keys=True`` matches ``_persist_activity``'s convention
        # — keeps byte-identical slot_fills_json across reads, which a
        # downstream byte-comparison test could otherwise see flap on
        # dict-iteration order.
        slot_fills_blob = json.dumps(existing_slot_fills, sort_keys=True)

    additional_sets: tuple[tuple[str, Any], ...] = (
        ("child_ids", encoded_children),
        ("reward_type", reward_type_value),
    )
    if slot_fills_blob is not None:
        additional_sets = (*additional_sets, ("slot_fills_json", slot_fills_blob))

    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_APPROVED,
        additional_sets=additional_sets,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    # Phase S S2: annotate each step with avatar animation hints and
    # persist into metadata_json before the WS broadcast fires.
    _annotate_and_persist_step_animations(
        conn,
        activity_id,
        row["persona_id"] if "persona_id" in row.keys() else None,
        sync_client,
    )
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
    # Step 23: inherit the "why this?" trigger phrase from the source
    # row when the caller doesn't override. The trigger phrase represents
    # what the kid actually said that prompted the original suggestion —
    # carrying it forward keeps the suggestion card's "Why this?" panel
    # coherent across "skip & try another" and avoids a sudden empty
    # panel.
    #
    # Phase N D1 fix: do NOT inherit ``persona_reasoning``. Regenerate
    # explicitly DOES NOT inherit the source's ``persona_id`` (a fresh
    # library persona is picked downstream in ``_do_propose`` — see the
    # ``persona_id = body.persona_id`` comment above). Pre-fix, this
    # block STILL copied the source's ``persona_reasoning`` text into the
    # regenerate's ProposeRequest as ``caller_supplied``, where
    # ``_build_persona_reasoning`` then preferred it verbatim over the
    # newly-bound persona's display name. Result: the regen card's
    # rationale named the source's persona while the activity's
    # ``persona_id`` (and the kiosk's resolved ``display_name``) was the
    # new one — the exact "Inspector Pip picked for X" → "Professor
    # Iridia on kiosk" mismatch the Phase M UAT (defect D1) caught.
    # Leaving ``inherited_reasoning`` as ``body.persona_reasoning`` means
    # an explicit caller-supplied value still wins (preserving the
    # documented caller-wins priority in ``_build_persona_reasoning``);
    # the implicit-from-source path is the only one that's gone.
    inherited_trigger = body.trigger_phrase
    inherited_reasoning = body.persona_reasoning
    if inherited_trigger is None:
        source_response = _row_to_response(conn, row)
        inherited_trigger = source_response.trigger_phrase
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
            use_recent_transcripts=body.use_recent_transcripts,
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
    #
    # Issue #135 fix: also re-render the title from the RAW
    # ``template.title`` text (not from the persisted ``title`` which
    # has already been substituted at propose time — re-rendering that
    # would be a no-op since role placeholders are gone). When the
    # template is no longer findable (renamed between propose + recast)
    # we leave the persisted title unchanged: best-effort contract
    # mirrors how step-body re-rendering also short-circuits below.
    if role_overlay:
        slot_fills.update(role_overlay)
        if template is not None:
            title = render_with_slot_fills(template.title, slot_fills)

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


# ---------------------------------------------------------------------
# Phase K K15 Surface P — parent-inserted interjections
# ---------------------------------------------------------------------

# States the parent-insert endpoints accept. Plan §6 K15: ``running`` /
# ``paused`` only — proposed activities should be dismissed + re-proposed
# instead, and terminal states (completed/ended/dismissed) have no
# "current_step + 1" to insert at.
_INSERT_ALLOWED_STATES: frozenset[str] = frozenset({STATE_RUNNING, STATE_PAUSED})


def _parent_insert_corpus_kind(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    expected_version: int,
    kind: str,
) -> tuple[sqlite3.Row, sqlite3.Row, Joke | Song]:
    """Shared front-half of the parent-insert endpoints.

    Validates state + version + content-master gate, picks a fresh
    corpus entry for the requested kind, and returns
    ``(activity_row, current_step_row, corpus_entry)`` so the caller
    can run the version-bumped INSERT in one transaction. Raises
    :class:`HTTPException` on every rejection path so the two endpoints
    stay one-liners around this function.

    State guard: 409 ``insert_only_when_running_or_paused`` for any
    other state (terminal or pre-approval).
    Version guard: 409 ``version_conflict`` (the standard
    :class:`VersionConflictError`) when ``If-Match-Version`` doesn't
    match the persisted version.
    Content-master guard: 409 ``content_disabled`` when the matching
    content master (``jokes_enabled`` / ``songs_enabled``) is off —
    parent UI surfaces the button as disabled, but a stale client could
    still POST after the parent toggled the flag off so we re-check
    server-side.
    Corpus-empty guard: 409 ``corpus_unavailable`` when no candidate
    survives the persona filter (e.g. fresh install before song audio
    was rendered). Operator sees a clear code rather than a 500.
    """
    if kind == "song":
        content_master_on = songs_enabled.get(conn)
    elif kind == "joke":
        content_master_on = jokes_enabled.get(conn)
    else:
        # Programmer error — the only callers are the two endpoints
        # below, which hard-code ``"joke"`` / ``"song"``.
        raise ValueError(f"_parent_insert_corpus_kind: unsupported kind {kind!r}")

    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    if current_state not in _INSERT_ALLOWED_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "insert_only_when_running_or_paused",
                "current_version": current_version,
                "current_state": current_state,
            },
        )
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    if not content_master_on:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "content_disabled",
                "current_version": current_version,
                "current_state": current_state,
                "kind": kind,
            },
        )

    # Locate the current step row — needed for ``seq + 1`` insertion
    # and the ``previous_step_id`` argument to
    # :func:`_insert_interjection_step_row`. A running/paused activity
    # MUST have a current step row (the lazy-insert path always marks
    # one); defense-in-depth raises 409 if not so a malformed row can't
    # 500 the endpoint.
    current_step = conn.execute(
        "SELECT id, seq FROM activity_steps WHERE activity_id = ? AND current = 1 LIMIT 1",
        (activity_id,),
    ).fetchone()
    if current_step is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_current_step", "id": activity_id},
        )

    # Slot fills for the corpus entry's display text. Parent-insert is
    # theme-untagged — the parent picked "any joke/song" via the button
    # (not via a theme-tagged template step), so we don't filter on
    # ``theme`` here. Persona-compat still applies (so a wizard activity
    # doesn't get a princess-only joke). Fresh ``secrets.randbits(31)``
    # seed ensures successive parent taps don't pick the same entry
    # back-to-back.
    seed_value = secrets.randbits(31)
    persona_id = row["persona_id"]
    corpus_entry: Joke | Song | None
    if kind == "song":
        corpus_entry = pick_song(
            seed=seed_value,
            persona_id=persona_id,
            theme=None,
            require_audio=True,
        )
    else:  # joke
        corpus_entry = pick_joke(
            seed=seed_value,
            persona_id=persona_id,
            theme=None,
        )
    if corpus_entry is None:
        # Empty corpus or no persona-compatible entry. Surface as 409
        # so the parent UI can render a toast rather than swallowing
        # the click.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "corpus_unavailable",
                "current_version": current_version,
                "current_state": current_state,
                "kind": kind,
            },
        )
    return row, current_step, corpus_entry


def _parent_insert_finish(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    *,
    activity_id: str,
    expected_version: int,
    current_state: str,
    activity_row: sqlite3.Row,
    current_step: sqlite3.Row,
    corpus_entry: Joke | Song,
    kind: str,
) -> ActivityResponse:
    """Shared back-half: build interjection row, INSERT, bump version,
    emit envelope, log event. Caller already validated state + corpus.
    """
    # Read persisted slot fills so role placeholders in the corpus
    # entry render with the activity's cast. Same defensive parsing as
    # the advance handler.
    slot_fills_raw = activity_row["slot_fills_json"]
    slot_fills: dict[str, str] = {}
    if slot_fills_raw:
        try:
            decoded = json.loads(slot_fills_raw)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            slot_fills = {str(k): str(v) for k, v in decoded.items()}

    # Toy display name for joke ``{toy}`` placeholder — best-effort
    # via the catalog resolver, matching the embedded/ending pattern.
    toy_display_name: str | None = None
    if kind == "joke":
        try:
            toys = resolve_toys(conn)
        except sqlite3.Error:
            _logger.warning(
                "parent-insert: resolve_toys failed; joke degrades to no-toy form",
                exc_info=True,
            )
            toys = []
        if toys:
            toy_display_name = toys[0].display_name

    new_seq = int(current_step["seq"]) + 1
    interjection_row = build_interjection_step(
        interjection=InterjectionKind.parent,
        corpus_entry=corpus_entry,
        slot_fills=slot_fills,
        seq=new_seq,
        toy_display_name=toy_display_name,
    )

    # Atomic version bump + row insert. State stays where it was
    # (running stays running, paused stays paused) — the parent insert
    # is a content mutation, not a lifecycle transition. Reusing
    # :func:`_attempt_transition` with ``new_state=current_state``
    # gives us the same optimistic-concurrency WHERE clause every other
    # mutation uses, so a concurrent /advance landing between the
    # endpoint's fetch and INSERT surfaces as a clean 409.
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=current_state,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    with conn:
        # Insert the interjection row at current+1 AND mark it current.
        # The kid's current step is then "the interjection just inserted"
        # — they see it next on their kiosk subscription envelope. The
        # previous step's ``current=0`` is flipped inside
        # ``_insert_interjection_step_row``. Plan §6 K15: "Insert a
        # themed interjection at current_step+1; kiosk shows it next."
        # The previous-step row's ``chosen_label`` is NOT written —
        # parent insert is not a kid choice.
        _insert_interjection_step_row(
            conn,
            activity_id=activity_id,
            interjection_row=interjection_row,
            new_seq=new_seq,
            previous_step_id=str(current_step["id"]),
            chosen_label=None,
        )

    # Telemetry: append an interjection event to the activity's
    # labeled_events.tool_calls JSON. Best-effort (no row → log + skip)
    # via :func:`append_interjection_event`. The event sink mustn't
    # 500 the parent's click; observability loss is acceptable, broken
    # UX is not.
    try:
        append_interjection_event(
            conn,
            activity_id=activity_id,
            source=INTERJECTION_SOURCE_PARENT_INSERT,
            interjection_kind=InterjectionKind.parent.value,
            corpus_entry_id=corpus_entry.id,
            step_seq=new_seq,
        )
    except Exception:  # noqa: BLE001 -- telemetry must never break the lifecycle
        _logger.warning(
            "parent-insert: append_interjection_event failed for activity %s",
            activity_id,
            exc_info=True,
        )

    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    _logger.info(
        "parent_insert activity %s kind=%s corpus_id=%s seq=%d version=%d",
        activity_id,
        kind,
        corpus_entry.id,
        new_seq,
        int(row["version"]),
    )
    return response


@router.post("/{activity_id}/insert-joke", response_model=ActivityResponse)
def post_insert_joke(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Phase K K15 Surface P: parent inserts a joke at current_step+1.

    Allowed only while the activity is ``running`` or ``paused`` — the
    parent UI greys the button outside those states. Body is empty
    (server picks the corpus entry; parent picks "any joke" via the
    button, not a specific joke). Gated on ``jokes_enabled``: 409
    ``content_disabled`` if the master is off. Version-conflict and
    state-guard 409s match the recast endpoint's pattern.

    Honors ``If-Match-Version`` like every other activity mutation.
    Server picks a fresh ``secrets.randbits(31)`` seed each call so two
    successive taps don't deliver the same joke.
    """
    activity_row, current_step, corpus_entry = _parent_insert_corpus_kind(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        kind="joke",
    )
    return _parent_insert_finish(
        conn,
        pubsub,
        activity_id=activity_id,
        expected_version=expected_version,
        current_state=str(activity_row["state"]),
        activity_row=activity_row,
        current_step=current_step,
        corpus_entry=corpus_entry,
        kind="joke",
    )


@router.post("/{activity_id}/insert-song", response_model=ActivityResponse)
def post_insert_song(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Phase K K15 Surface P: parent inserts a song at current_step+1.

    Mirror of :func:`post_insert_joke` but for the song corpus
    (``require_audio=True`` so the kiosk never 404s on a not-yet-
    rendered entry). Gated on ``songs_enabled``.
    """
    activity_row, current_step, corpus_entry = _parent_insert_corpus_kind(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        kind="song",
    )
    return _parent_insert_finish(
        conn,
        pubsub,
        activity_id=activity_id,
        expected_version=expected_version,
        current_state=str(activity_row["state"]),
        activity_row=activity_row,
        current_step=current_step,
        corpus_entry=corpus_entry,
        kind="song",
    )


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
    # Phase R Step R3 / Phase W Step W3: carry the template step's Q&A
    # gating fields onto the lazily-inserted row so a question step
    # reached mid-activity gates (R3) and can auto-grade (W3). ``_Template``
    # internal steps always carry both attrs (default None); guard with
    # ``getattr`` so a non-_StepTemplate ``template_step`` (e.g. a future
    # caller passing a different shape) degrades to None rather than
    # raising.
    step_question = getattr(template_step, "question", None)
    step_expected_answer = getattr(template_step, "expected_answer", None)
    conn.execute(
        "INSERT INTO activity_steps "
        "(id, activity_id, seq, body, sfx, expected_action, current, action_slot, "
        " choices_json, step_template_id, question, expected_answer) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
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
            step_question,
            step_expected_answer,
        ),
    )


@router.post("/{activity_id}/advance", response_model=ActivityResponse)
def post_advance(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent, TokenScope.child}))],
    sync_client: Annotated[Any, Depends(get_sync_ai_client)],  # Phase W W3
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
        "SELECT id, seq, current, choices_json, step_template_id, kind, "
        " question, question_approved, expected_answer, chosen_label "
        "FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    if not steps:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "activity_has_no_steps", "id": activity_id},
        )

    advance_body = body if body is not None else AdvanceRequest()

    # Phase L two-phase terminal advance — Phase 2: the current step
    # is a reward step (kind="reward"), meaning Phase 1 inserted it.
    # The kid just tapped past it; dismiss to ``completed``. Short-
    # circuit here so the lazy-insert path's
    # ``_resolve_template_step_index`` fallback doesn't mistakenly
    # land the reward step's NULL ``step_template_id`` + seq-based
    # fallback array index on an unrelated template entry and insert
    # a sibling branch's row.
    if current_state == STATE_RUNNING:
        current_step_row = next((s for s in steps if int(s["current"]) == 1), None)
        if current_step_row is not None and str(current_step_row["kind"]) == "reward":
            if advance_body.choice_index is not None:
                raise _bad_advance("choice_not_allowed", reason="current_step_is_reward")
            return _terminal_advance(conn, pubsub, activity_id, expected_version)

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

    # Phase W Step W4: dynamic adventure advance. When the activity is an
    # adventure, generate the NEXT beat (from the prior choices + recent
    # transcript) and insert it via the lazy-insert seam — NOT a template
    # row. After ``MAX_ADVENTURE_BEATS`` route to the normal
    # reward/terminal/end path. Handled before the template lazy-insert
    # path below so an adventure never touches ``find_template_by_id``.
    activity_is_adventure = (
        "adventure" in row.keys() and row["adventure"] is not None and int(row["adventure"]) == 1
    )
    if activity_is_adventure:
        return _advance_adventure(
            conn,
            pubsub,
            sync_client,
            activity_id=activity_id,
            expected_version=expected_version,
            target=target,
            steps=steps,
            current_row=current_row,
            advance_body=advance_body,
            seed_seed=_adventure_seed_for(row),
        )

    # Phase R Step R3: Q&A gating. If the current step has a ``question``
    # that has not yet been approved or skipped (question_approved IS
    # NULL), block the advance so the parent must resolve it first.
    current_question = current_row["question"] if "question" in current_row.keys() else None
    current_q_approved = (
        current_row["question_approved"] if "question_approved" in current_row.keys() else None
    )
    if current_question is not None and current_q_approved is None:
        # Phase W Step W3: optional Q&A auto-grading. When the household
        # ``qa_grading`` dial is not "off" AND the current step carries an
        # ``expected_answer``, attempt an auto-grade against the recent
        # transcript window BEFORE falling back to the R3 parent-tap 409.
        # On a confident match we resolve the gate via the SAME path the
        # parent's approve-question uses (set question_approved=1, bump
        # version, emit the WS envelope) and let advance proceed. On no
        # confident match — or with grading off, or any grading fault —
        # we fall through to the existing 409 unchanged (byte-identical R3
        # behavior when grading is off).
        from ..core.qa_grading import get_qa_grading  # noqa: PLC0415

        tolerance = get_qa_grading(conn)
        current_expected = (
            current_row["expected_answer"] if "expected_answer" in current_row.keys() else None
        )
        auto_resolved = False
        if tolerance != "off" and current_expected:
            if _attempt_auto_grade(
                conn,
                sync_client,
                tolerance=tolerance,
                question=str(current_question),
                expected=str(current_expected),
            ):
                # Confident match → resolve the gate exactly like an
                # approve-question "approved" (question_approved=1).
                _resolve_question_gate(
                    conn,
                    pubsub,
                    activity_id=activity_id,
                    step_id=str(current_row["id"]),
                    approved_value=1,
                )
                # The gate bumped activities.version; re-fetch so the
                # advance below uses the fresh version for its
                # optimistic-concurrency UPDATE.
                row = _fetch_activity_row(conn, activity_id)
                current_version = int(row["version"])
                expected_version = current_version
                auto_resolved = True
        if not auto_resolved:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "question_pending"},
            )

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
        # Phase L Step L5: the K14 ending-picker branch was removed
        # when jokes/songs became per-activity reward types — the
        # L4 ``_terminal_advance`` path appends a reward step if one
        # is configured for this activity, supplanting the old ending.
        if advance_body.choice_index is not None:
            raise _bad_advance("choice_not_allowed", reason="current_step_is_terminal")
        return _terminal_advance(conn, pubsub, activity_id, expected_version)
    elif current_template_index + 1 < len(template.steps):
        # Rule 3: fall through to next array position.
        if advance_body.choice_index is not None:
            raise _bad_advance("choice_not_allowed", reason="current_step_is_linear")
        target_template_step = template.steps[current_template_index + 1]
    else:
        # Rule 4: terminal. Phase L Step L5: the K14 ending-picker
        # branch was removed; ``_terminal_advance`` (L4) appends the
        # per-activity reward step if one is configured.
        if advance_body.choice_index is not None:
            raise _bad_advance("choice_not_allowed", reason="current_step_is_terminal")
        return _terminal_advance(conn, pubsub, activity_id, expected_version)

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

    # Phase L Step L5: removed the K14 Surface B (embedded auto song/
    # joke picker) and the K15 Surface S (spontaneity roll). Both
    # surfaces have been replaced by per-activity reward types resolved
    # at terminal advance (L4). The remaining branches insert the
    # resolved template step verbatim through the legacy
    # ``_insert_next_step`` path.
    new_seq = int(current_row["seq"]) + 1

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
    with conn:
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


# Phase W Step W3: how far back the auto-grader reads transcript text.
# 30 seconds is the spec window — the kid's spoken answer to the current
# step's question lands in the last few seconds. Read INDEPENDENTLY of the
# transcript_retention setting (that only governs deletion); a household
# on the 60s retention floor still has the last 30s available.
_QA_GRADING_WINDOW_SECONDS: int = 30

# Phase W Step W3: judge-call budget. The kid is waiting at the kiosk, so
# this is much tighter than the background judge's 30s — a slow judge must
# fall back to the offline grader fast rather than stall the advance.
_QA_JUDGE_TIMEOUT_SEC: float = 8.0
_QA_JUDGE_MAX_TOKENS: int = 16

# Phase W Step W3: module-singleton circuit breaker for the kid-facing Q&A
# judge call. Mirrors the ``_VISION_BREAKER`` pattern in
# :mod:`toybox.api.toys` / :mod:`toybox.api.rooms`: one in-process breaker
# per call site so a 429 burst / outage seen by THIS gate is remembered
# across advances. A throwaway ``CircuitBreaker()`` per call (the prior
# bug) made the breaker state invisible — every advance paid the full
# timeout during an outage instead of being short-circuited by an already-
# open breaker. Shared breaker state is read by ``is_capable`` (it returns
# not-capable when the breaker is open) and updated by the outcome
# recorders below.
_QA_JUDGE_BREAKER: CircuitBreaker = CircuitBreaker()


# Phase W Step W4: how far back the adventure online beat reads recent
# speech. Same 30s window the W3 grader uses — the child's reaction to the
# previous beat lands in the last few seconds.
_ADVENTURE_TRANSCRIPT_WINDOW_SECONDS: int = 30

# Phase W Step W4: kid-facing budget for the online beat call. Like the W3
# judge, the child is waiting synchronously at the kiosk so a slow Claude
# call must fall back to the deterministic offline assembly fast rather
# than stall the advance.
_ADVENTURE_BEAT_TIMEOUT_SEC: float = 8.0
_ADVENTURE_BEAT_MAX_TOKENS: int = 256

# Phase W Step W4: module-singleton circuit breaker for the adventure beat
# Claude call (mirrors :data:`_QA_JUDGE_BREAKER`). One in-process breaker
# per call site so a 429 burst / outage seen by THIS call is remembered
# across advances and short-circuits subsequent beats to offline.
_ADVENTURE_BEAT_BREAKER: CircuitBreaker = CircuitBreaker()


def _build_adventure_cast(resolved_toys: Sequence[ResolvedToy]) -> tuple[str, ...]:
    """Phase W Step W4: build the adventure cast from resolved toys.

    Returns the toy display names in :func:`resolve_toys`'s deterministic
    order (``last_used_at DESC`` then ``id COLLATE BINARY ASC`` — NOT a
    NOCASE sort). The adventure engine treats
    ``cast[0]`` as the hero and ``cast[1]`` as the ally, falling back to
    generic descriptors when the household has fewer than two toys — so
    this can return an empty tuple safely.
    """
    return tuple(t.display_name for t in resolved_toys if t.display_name)


def _select_boss_name(resolved_toys: Sequence[ResolvedToy]) -> str | None:
    """Phase W Step W5: pick a boss-role toy display name from the cast.

    The protagonist/hero is ``cast[0]`` — the FIRST resolved toy with a
    display name (see :func:`_build_adventure_cast`). The hero is NEVER cast
    as its own boss, so it is excluded from candidacy here.

    Among the NON-hero cast a three-tier preference applies, so an explicit
    boss tag genuinely beats an untagged toy (the COMMON case is
    ``allowed_roles == ()`` "unrestricted", which would otherwise win on
    sort order alone and defeat the tag):

    1. a toy that EXPLICITLY lists
       :class:`~toybox.activities.roles.Role.big_bad_boss` in its
       :attr:`ResolvedToy.allowed_roles`;
    2. else a toy that EXPLICITLY lists
       :class:`~toybox.activities.roles.Role.boss_mini_boss`;
    3. else an unrestricted (``allowed_roles == ()``) non-hero toy may fill
       the role (Phase K soft-fallback).

    Returns ``None`` when no non-hero cast member qualifies under any tier —
    the adventure engine then falls back to a generic boss descriptor
    (never crashes).

    Within each tier the resolver's deterministic order wins so the boss is
    stable across a replay. :func:`resolve_toys` orders by ``last_used_at
    DESC`` (NULLs last) then ``id COLLATE BINARY ASC`` — NOT a NOCASE sort.
    """
    # The hero is cast[0]: the first toy carrying a display name. Exclude it.
    non_hero: list[ResolvedToy] = []
    hero_seen = False
    for toy in resolved_toys:
        if not toy.display_name:
            continue
        if not hero_seen:
            hero_seen = True
            continue
        non_hero.append(toy)

    # Tier 1: an EXPLICIT big_bad_boss tag.
    for toy in non_hero:
        if Role.big_bad_boss.value in toy.allowed_roles:
            return toy.display_name
    # Tier 2: an EXPLICIT boss_mini_boss tag.
    for toy in non_hero:
        if Role.boss_mini_boss.value in toy.allowed_roles:
            return toy.display_name
    # Tier 3: a soft-fallback unrestricted non-hero toy.
    for toy in non_hero:
        if not toy.allowed_roles:
            return toy.display_name
    return None


def _adventure_history_from_steps(rows: Sequence[sqlite3.Row]) -> tuple[str, ...]:
    """Phase W Step W4: extract the child's choice history from beat rows.

    Reads each beat row's ``chosen_label`` (the label the child picked at
    that beat, recorded on the previous step by the lazy-insert seam),
    oldest-first, skipping NULLs. This is the ``history`` the adventure
    engine echoes into the next beat's transition text.
    """
    history: list[str] = []
    for r in rows:
        label = r["chosen_label"] if "chosen_label" in r.keys() else None
        if isinstance(label, str) and label:
            history.append(label)
    return tuple(history)


def _make_adventure_online_call(sync_client: Any) -> Any:
    """Phase W Step W4: build the ``online_call`` for the adventure engine.

    Returns a callable ``(system, user) -> str`` that performs the Claude
    transport under the SAME capability gate + 8s timeout + shared-breaker
    pattern as W3's :func:`_grade_via_claude`. The returned callable raises
    on ANY failure (gate not green, no client, transport error, timeout)
    so :func:`toybox.activities.adventure.generate_next_beat` degrades to
    the deterministic offline assembly.

    Returns ``None`` when no sync client is available — the engine then
    uses the offline assembly directly (never attempts a call).
    """
    if sync_client is None:
        return None

    def _online_call(system: str, user: str) -> str:
        from ..ai.capability import is_capable  # noqa: PLC0415
        from ..ai.client import AIMessage  # noqa: PLC0415

        capable, reason = asyncio.run(is_capable(_ADVENTURE_BEAT_BREAKER))
        if not capable:
            raise RuntimeError(f"capability gate not green for adventure beat: {reason}")

        def _call() -> Any:
            return sync_client.complete_text_sync(
                [AIMessage(role="user", content=user)],
                system=system,
                max_tokens=_ADVENTURE_BEAT_MAX_TOKENS,
            )

        # 8s budget independent of the client's 60s transport timeout —
        # same ``finally: pool.shutdown(wait=False)`` discipline as W3 so a
        # ``with`` block's ``shutdown(wait=True)`` can't re-block on the
        # orphaned worker for the full transport timeout.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(_call)
            try:
                response = future.result(timeout=_ADVENTURE_BEAT_TIMEOUT_SEC)
            except concurrent.futures.TimeoutError as exc:
                _ADVENTURE_BEAT_BREAKER.record_failure()
                raise RuntimeError(
                    f"adventure beat exceeded {_ADVENTURE_BEAT_TIMEOUT_SEC}s budget"
                ) from exc
            except Exception:
                _ADVENTURE_BEAT_BREAKER.record_failure()
                raise
        finally:
            pool.shutdown(wait=False)

        _ADVENTURE_BEAT_BREAKER.record_success()
        return str(response.text)

    return _online_call


def _generate_adventure_beat(
    conn: sqlite3.Connection,
    sync_client: Any,
    *,
    history: tuple[str, ...],
    cast: tuple[str, ...],
    beat_index: int,
    linear: bool,
    seed: int,
    online: bool,
) -> GeneratedBeat:
    """Phase W Step W4: generate one adventure beat, never raising.

    Reads the recent transcript window (best-effort), then drives the pure
    adventure engine. ``online`` requests the Claude path (degrades to
    offline on any failure via ``generate_next_beat``). The WHOLE call is
    wrapped so a generation fault NEVER 500s the advance — on an
    unexpected error it falls back to a final offline assembly, and if even
    that raises the caller is responsible for ending the adventure
    gracefully.
    """
    transcript_window = ""
    if online:
        try:
            transcript_window = _read_transcript_window(
                conn, window_seconds=_ADVENTURE_TRANSCRIPT_WINDOW_SECONDS
            )
        except Exception:  # noqa: BLE001 -- transcript read must never break advance
            _logger.warning("adventure: transcript-window read failed", exc_info=True)
            transcript_window = ""

    online_call = _make_adventure_online_call(sync_client) if online else None
    return generate_next_beat(
        history,
        transcript_window,
        cast,
        online=online,
        beat_index=beat_index,
        linear=linear,
        seed=seed,
        online_call=online_call,
    )


def _generate_adventure_boss_beat(
    conn: sqlite3.Connection,
    sync_client: Any,
    *,
    history: tuple[str, ...],
    cast: tuple[str, ...],
    boss_name: str | None,
    beat_index: int,
    linear: bool,
    seed: int,
    online: bool,
) -> GeneratedBeat:
    """Phase W Step W5: generate the CLIMAX boss-fight beat, never raising.

    Mirrors :func:`_generate_adventure_beat` (best-effort transcript read +
    the same online_call / capability-gate / breaker path) but drives the
    pure engine's :func:`toybox.activities.adventure.generate_boss_beat` so
    the result is stamped ``kind="boss_fight"`` and casts ``boss_name``
    (falling back to a generic boss descriptor inside the engine when it is
    ``None``). Degrades to the deterministic offline boss assembly on any
    Claude failure.
    """
    transcript_window = ""
    if online:
        try:
            transcript_window = _read_transcript_window(
                conn, window_seconds=_ADVENTURE_TRANSCRIPT_WINDOW_SECONDS
            )
        except Exception:  # noqa: BLE001 -- transcript read must never break advance
            _logger.warning("adventure: boss transcript-window read failed", exc_info=True)
            transcript_window = ""

    online_call = _make_adventure_online_call(sync_client) if online else None
    return generate_boss_beat(
        history,
        transcript_window,
        cast,
        boss_name,
        online=online,
        beat_index=beat_index,
        linear=linear,
        seed=seed,
        online_call=online_call,
    )


# Phase W Step W4: bound for the per-adventure determinism seed derived from
# the activity id. Well within ProposeRequest.seed's range.
_ADVENTURE_SEED_MODULO: int = 1_000_000


def _adventure_seed_from_id(activity_id: str) -> int:
    """Phase W Step W4: derive a stable per-adventure determinism seed.

    Beat 0 (propose) and every later beat (advance) MUST share ONE seed so
    the offline ``_theme_for(seed)`` keeps a single theme across the whole
    adventure (adventure.py's "one theme across all beats" contract). The
    seed is keyed on the activity id alone — stable across the run and across
    processes — and reuses the adventure module's SHA-256-mod algorithm via
    :func:`toybox.activities.adventure.stable_index` (single source of truth,
    code-quality.md §2) rather than re-implementing the hash here.
    """
    return stable_index(0, 0, activity_id, _ADVENTURE_SEED_MODULO)


def _adventure_seed_for(row: sqlite3.Row) -> int:
    """Phase W Step W4: per-adventure determinism seed for an activities row."""
    return _adventure_seed_from_id(str(row["id"]))


def _insert_adventure_beat(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    beat: GeneratedBeat,
    new_seq: int,
    previous_step_id: str,
    chosen_label: str | None,
) -> None:
    """Phase W Step W4: lazily INSERT a generated adventure beat row.

    Mirrors :func:`_insert_next_step` (the G3 lazy-insert seam) but writes
    a generated beat instead of a rendered template step: it marks the
    previous step ``current=0`` (recording ``chosen_label`` when the child
    picked a choice), then INSERTs the new beat writing ``beat.kind``
    verbatim — normally
    :data:`toybox.activities.adventure.ADVENTURE_BEAT_KIND`, but ``"boss_fight"``
    persists unchanged for a W5 climax beat. ``choices_json``
    holds the beat's choice labels (NULL on a linear beat). No
    ``step_template_id`` (adventures have no template) and no Q&A fields.

    Caller MUST be inside the version-bumped transaction so a stale retry
    cannot double-insert.
    """
    choices_blob: str | None = None
    if beat.choices is not None:
        choices_blob = json.dumps(list(beat.choices))

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
        " choices_json, step_template_id, kind, metadata_json, question, expected_answer) "
        "VALUES (?, ?, ?, ?, NULL, NULL, 1, NULL, ?, NULL, ?, NULL, NULL, NULL)",
        (
            str(uuid.uuid4()),
            activity_id,
            new_seq,
            beat.body,
            choices_blob,
            beat.kind,
        ),
    )


def _advance_adventure(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    sync_client: Any,
    *,
    activity_id: str,
    expected_version: int,
    target: str,
    steps: Sequence[sqlite3.Row],
    current_row: sqlite3.Row,
    advance_body: AdvanceRequest,
    seed_seed: int,
) -> ActivityResponse:
    """Phase W Step W4: advance one beat of a running adventure.

    Resolves the child's choice on the current beat (recording the picked
    label), then either generates + inserts the NEXT beat or — once the
    adventure has reached :data:`MAX_ADVENTURE_BEATS` — routes to the normal
    reward/terminal/end path (the SAME path a template activity uses; we do
    NOT invent a new terminal path). Generation NEVER 500s the advance:
    :func:`_generate_adventure_beat` degrades to the deterministic offline
    assembly, and if even that raised we end the adventure gracefully.
    """
    # Resolve the choice on the current beat. A beat with choices REQUIRES a
    # choice_index (mirrors the G3 rule 1); a linear beat must NOT carry one.
    current_choices = _decode_choices_json(current_row["choices_json"])
    chosen_label: str | None = None
    if current_choices is not None and len(current_choices) > 0:
        ci = advance_body.choice_index
        if ci is None:
            raise _bad_advance("choice_required", choice_count=len(current_choices))
        if ci < 0 or ci >= len(current_choices):
            raise _bad_advance(
                "invalid_choice_index",
                choice_index=ci,
                choice_count=len(current_choices),
            )
        chosen_label = current_choices[ci].label
    elif advance_body.choice_index is not None:
        raise _bad_advance("choice_not_allowed", reason="current_beat_is_linear")

    # Beat count so far == number of persisted steps. The next beat is
    # 0-based index ``len(steps)``. Terminate once we have already shown
    # MAX_ADVENTURE_BEATS beats — route to the normal terminal path.
    next_beat_index = len(steps)
    linear = get_game_linearity(conn) == "linear"

    if next_beat_index >= MAX_ADVENTURE_BEATS:
        # Record the child's final choice on the last beat (so the played
        # path is complete) before handing off to the terminal/reward path.
        if chosen_label is not None:
            with conn:
                conn.execute(
                    "UPDATE activity_steps SET chosen_label = ? WHERE activity_id = ? AND id = ?",
                    (chosen_label, activity_id, str(current_row["id"])),
                )
        return _terminal_advance(conn, pubsub, activity_id, expected_version)

    # Build the choice history + cast, then generate the next beat. Online
    # when the gate is green (decided inside _generate_adventure_beat via
    # the online_call's own is_capable check); we pass online=True so the
    # engine attempts Claude and degrades to offline on any failure.
    history = _adventure_history_from_steps(steps)
    if chosen_label is not None:
        history = (*history, chosen_label)
    resolved_toys: list[ResolvedToy] = []
    try:
        resolved_toys = resolve_toys(conn)
    except sqlite3.Error:
        _logger.warning(
            "adventure advance: resolve_toys failed; using generic descriptors",
            exc_info=True,
        )
    cast = _build_adventure_cast(resolved_toys)

    # Phase W Step W5: the CLIMAX beat is the LAST generated beat (index
    # MAX_ADVENTURE_BEATS - 1). When the boss-fights flag is on, emit a
    # distinct kind="boss_fight" beat casting a boss-role toy from the cast
    # (generic boss descriptor when none) instead of an ordinary beat. The
    # boss beat IS the final beat — the NEXT advance crosses the cap and
    # routes to the terminal/reward path (no 7th beat). When the flag is off
    # the climax is an ordinary adventure_beat (W4 behavior unchanged).
    is_climax = next_beat_index == MAX_ADVENTURE_BEATS - 1
    boss_enabled = boss_fights_enabled.get(conn)
    emit_boss = is_climax and boss_enabled

    try:
        if emit_boss:
            boss_name = _select_boss_name(resolved_toys)
            beat = _generate_adventure_boss_beat(
                conn,
                sync_client,
                history=history,
                cast=cast,
                boss_name=boss_name,
                beat_index=next_beat_index,
                linear=linear,
                seed=seed_seed,
                online=sync_client is not None,
            )
        else:
            beat = _generate_adventure_beat(
                conn,
                sync_client,
                history=history,
                cast=cast,
                beat_index=next_beat_index,
                linear=linear,
                seed=seed_seed,
                online=sync_client is not None,
            )
    except Exception:  # noqa: BLE001 -- generation must NEVER 500 the advance
        _logger.warning(
            "adventure advance: beat generation failed for %s; ending adventure",
            activity_id,
            exc_info=True,
        )
        return _terminal_advance(conn, pubsub, activity_id, expected_version)

    new_seq = int(current_row["seq"]) + 1
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=target,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    with conn:
        _insert_adventure_beat(
            conn,
            activity_id=activity_id,
            beat=beat,
            new_seq=new_seq,
            previous_step_id=str(current_row["id"]),
            chosen_label=chosen_label,
        )

    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


def _read_transcript_window(conn: sqlite3.Connection, *, window_seconds: int) -> str:
    """Return the concatenated transcript text from the last ``window_seconds``.

    Phase W Step W3. Reads ``transcripts.text`` for rows whose ``ended_at``
    falls within the window, most-recent first, and joins them with
    newlines. The cutoff is formatted byte-identically to the pipeline's
    ``ended_at`` via :func:`toybox.core.transcript_retention._format_ended_at_cutoff`
    so the lexicographic ``ended_at >= ?`` comparison matches numeric
    comparison against the underlying instant.

    Deliberately INDEPENDENT of the household transcript-retention setting:
    retention governs DELETION (the sweep loop), not how far back the
    grader may look. Rows already swept are simply absent.

    Returns ``""`` when no rows fall in the window — the offline grader
    then returns False (no confident match) and the gate stays pending.
    """
    from datetime import timedelta  # noqa: PLC0415

    from ..core.transcript_retention import _format_ended_at_cutoff  # noqa: PLC0415

    # ``UTC`` and ``datetime`` are module-top imports; only ``timedelta``
    # is needed locally.
    cutoff = _format_ended_at_cutoff(datetime.now(UTC) - timedelta(seconds=window_seconds))
    rows = conn.execute(
        "SELECT text FROM transcripts "
        "WHERE text IS NOT NULL AND ended_at IS NOT NULL AND ended_at >= ? "
        "ORDER BY ended_at DESC, id DESC",
        (cutoff,),
    ).fetchall()
    return "\n".join(str(r["text"]) for r in rows if r["text"])


def _grade_via_claude(
    sync_client: Any,
    *,
    question: str,
    expected: str,
    transcript_window: str,
) -> bool:
    """Phase W Step W3: capability-gated Claude judge for one Q&A answer.

    Returns True only on a confident "correct" verdict. On ANY failure
    path — gate not green, no client, transport error, malformed reply,
    timeout — raises so the caller falls back to the deterministic offline
    grader. A clean "incorrect" verdict returns False (the gate stays
    pending for the parent to resolve).

    The capability gate is :func:`toybox.ai.capability.is_capable`, the
    SAME gate every kid-facing Claude call site goes through, driven with
    the SHARED module-singleton :data:`_QA_JUDGE_BREAKER` (Phase W Step W3
    fix — a throwaway breaker per call hid outage state, so every advance
    paid the full timeout during an outage). It is async; we drive it from
    this sync route via ``asyncio.run`` (mirrors
    :func:`_resolve_local_dispatch`). When the gate is not green we raise
    so the offline grader takes over.

    The Claude call itself runs under a hard :data:`_QA_JUDGE_TIMEOUT_SEC`
    deadline (Phase W Step W3 fix): ``complete_text_sync`` uses the client's
    60s transport timeout, but the kid is waiting synchronously at the
    kiosk, so we cap the wait at 8s via a single-shot thread + ``.result``
    deadline. A timeout records a breaker failure and raises so the caller
    falls back to the offline grader fast rather than stalling advance.
    """
    if sync_client is None:
        raise RuntimeError("no sync AI client available for Q&A judge")

    from ..ai.capability import is_capable  # noqa: PLC0415
    from ..ai.client import AIMessage  # noqa: PLC0415

    capable, reason = asyncio.run(is_capable(_QA_JUDGE_BREAKER))
    if not capable:
        raise RuntimeError(f"capability gate not green for Q&A judge: {reason}")

    system = (
        "You are grading whether a young child's spoken answer to a "
        "question is correct. Reply with EXACTLY one word: CORRECT or "
        "INCORRECT. No other text. Be lenient about phrasing, spelling, "
        "and extra words — judge the meaning."
    )
    user = json.dumps(
        {
            "question": question,
            "expected_answer": expected,
            "child_said": transcript_window,
        },
        ensure_ascii=False,
    )

    def _call() -> Any:
        return sync_client.complete_text_sync(
            [AIMessage(role="user", content=user)],
            system=system,
            max_tokens=_QA_JUDGE_MAX_TOKENS,
        )

    # Enforce the 8s budget independently of the client's 60s transport
    # timeout. A single worker thread runs the blocking call; ``.result``
    # raises ``TimeoutError`` if it overruns. CRITICAL: shut the pool down
    # with ``wait=False`` in a ``finally`` (NOT a ``with`` block, whose
    # ``__exit__`` does ``shutdown(wait=True)`` and would re-block on the
    # orphaned worker for the client's full 60s — defeating the 8s budget).
    # We cannot forcibly kill the worker thread, so it drains in the
    # background; the kid-facing advance returns at the 8s deadline.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(_call)
        try:
            response = future.result(timeout=_QA_JUDGE_TIMEOUT_SEC)
        except concurrent.futures.TimeoutError as exc:
            # A slow/hung judge is an outage signal — trip the shared
            # breaker so subsequent advances short-circuit instead of each
            # paying the full 8s.
            _QA_JUDGE_BREAKER.record_failure()
            raise RuntimeError(f"Q&A judge exceeded {_QA_JUDGE_TIMEOUT_SEC}s budget") from exc
        except Exception:
            # Transport error / malformed call — record a failure and let
            # the caller fall back to the offline grader.
            _QA_JUDGE_BREAKER.record_failure()
            raise
    finally:
        pool.shutdown(wait=False)

    _QA_JUDGE_BREAKER.record_success()
    verdict = str(response.text).strip().upper()
    return verdict.startswith("CORRECT")


def _attempt_auto_grade(
    conn: sqlite3.Connection,
    sync_client: Any,
    *,
    tolerance: str,
    question: str,
    expected: str,
) -> bool:
    """Phase W Step W3: decide whether the current Q&A gate auto-resolves.

    Reads the last 30 seconds of transcript text, then grades it — via the
    capability-gated Claude judge when the gate is green, else via the
    deterministic offline :func:`toybox.core.qa_grading.grade_answer`. Any
    Claude-path failure (gate not green, transport error, timeout,
    malformed reply) degrades to the offline grader.

    The WHOLE attempt is best-effort: an unexpected error (e.g. a transient
    DB read failure on the transcript window) is swallowed and reported as
    "no confident match" so a grading fault NEVER breaks advance — the
    caller falls through to the existing R3 parent-tap 409.

    Returns True only on a confident match.
    """
    from ..core.qa_grading import grade_answer  # noqa: PLC0415

    try:
        window = _read_transcript_window(conn, window_seconds=_QA_GRADING_WINDOW_SECONDS)
    except Exception:  # noqa: BLE001 -- grading must never break advance
        _logger.warning("qa auto-grade: transcript-window read failed", exc_info=True)
        return False

    # Try the capability-gated Claude judge first; fall back to the offline
    # grader on any failure or when the gate is not green.
    try:
        return _grade_via_claude(
            sync_client,
            question=question,
            expected=expected,
            transcript_window=window,
        )
    except Exception as exc:  # noqa: BLE001 -- fall back to offline grader
        _logger.debug("qa auto-grade: claude judge unavailable (%s); using offline grader", exc)

    try:
        return grade_answer(window, expected, tolerance)
    except Exception:  # noqa: BLE001 -- grading must never break advance
        _logger.warning("qa auto-grade: offline grader raised", exc_info=True)
        return False


def _resolve_question_gate(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    *,
    activity_id: str,
    step_id: str,
    approved_value: int,
) -> sqlite3.Row:
    """Resolve a step's R3 Q&A gate and broadcast the new state.

    Single source of truth for the approve-question resolution path,
    shared by :func:`post_approve_question` (parent tap) and the Phase W
    Step W3 auto-grade path in :func:`post_advance`. Both must produce a
    byte-identical effect: set ``question_approved`` on the step row +
    bump ``activities.version`` atomically, then emit the same
    ``activity.state`` WS envelope so the child kiosk receives
    ``question_pending=False`` and unhides the Next button.

    ``approved_value`` is 1 (approved) or 2 (skipped); the auto-grade path
    always passes 1 (a confident match is an approval). Returns the
    re-read activity row so callers can read the bumped version.
    """
    with conn:
        conn.execute(
            "UPDATE activity_steps SET question_approved = ? WHERE id = ?",
            (approved_value, step_id),
        )
        conn.execute(
            "UPDATE activities SET version = version + 1 WHERE id = ?",
            (activity_id,),
        )
    updated_row = _fetch_activity_row(conn, activity_id)
    response_obj = _row_to_response(conn, updated_row)
    _emit_state(pubsub, response_obj)
    return updated_row


@router.post("/{activity_id}/approve-question", response_model=ApproveQuestionResponse)
def post_approve_question(
    activity_id: str,
    body: ApproveQuestionRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ApproveQuestionResponse:
    """Phase R Step R3: approve or skip the current step's Q&A gate.

    Validates that the current running activity has a pending question on
    its current step, sets ``question_approved`` (1=approved, 2=skipped),
    bumps ``activities.version`` in the same transaction, then calls
    ``_emit_state`` so the child kiosk receives the updated envelope and
    unhides the Next button.

    Returns ``{"version": N}`` on success. Raises 409 on:

    * version_conflict — ``body.version`` does not match the persisted
      version (standard optimistic-concurrency pattern).
    * no_current_step — running activity has no current step row.
    * no_question_pending — the current step has no question, or the
      question was already resolved (idempotency guard).
    * invalid_transition — activity is not in a state where a question
      can be resolved (must be ``running`` or ``paused``).
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
                "target_state": "approve_question",
            },
        )
    if current_version != body.version:
        raise VersionConflictError(current_version, current_state)

    # Find the current step row.
    current_step_row = conn.execute(
        "SELECT id, question, question_approved "
        "FROM activity_steps WHERE activity_id = ? AND current = 1 LIMIT 1",
        (activity_id,),
    ).fetchone()
    if current_step_row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_current_step", "id": activity_id},
        )

    question_val = current_step_row["question"] if "question" in current_step_row.keys() else None
    q_approved_val = (
        current_step_row["question_approved"]
        if "question_approved" in current_step_row.keys()
        else None
    )
    if question_val is None or q_approved_val is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "no_question_pending",
                "id": activity_id,
                "question": question_val,
                "question_approved": q_approved_val,
            },
        )

    approved_value = 1 if body.result == "approved" else 2
    step_id = str(current_step_row["id"])

    updated_row = _resolve_question_gate(
        conn,
        pubsub,
        activity_id=activity_id,
        step_id=step_id,
        approved_value=approved_value,
    )
    new_version = int(updated_row["version"])

    _logger.info(
        "approve_question activity=%s result=%s version=%d->%d",
        activity_id,
        body.result,
        current_version,
        new_version,
    )
    return ApproveQuestionResponse(version=new_version)


def _has_reward_step(conn: sqlite3.Connection, activity_id: str) -> bool:
    """Phase L Step L4: True iff this activity already has a reward step.

    Idempotency guard for :func:`_terminal_advance` — the state
    transition itself already wedges a second call (post-COMPLETED
    advance returns 409), but the check provides defense-in-depth and
    pins the contract that a reward step is appended at most once.
    """
    row = conn.execute(
        "SELECT 1 FROM activity_steps WHERE activity_id = ? AND kind = 'reward' LIMIT 1",
        (activity_id,),
    ).fetchone()
    return row is not None


def _build_reward_step_metadata(resolved: ResolvedReward) -> dict[str, Any]:
    """Phase L Step L4: build the per-step metadata blob for a reward step.

    Mirrors the wire shape locked in documentation/phase-l-plan.md §8:
    every key is present in every reward kind (uniform shape on the
    kiosk wire) but only the kind-appropriate keys carry non-NULL
    values. The L3 resolver already filtered the values per-kind; we
    just project them into the dict shape.
    """
    return {
        "reward_kind": resolved.kind,
        "reward_id": resolved.reward_id,
        "image_url": resolved.image_url,
        "animation": resolved.animation.value if resolved.animation is not None else None,
        "audio_url": resolved.audio_url,
        "body": resolved.body,
        "setup": resolved.setup,
        "punchline": resolved.punchline,
    }


def _extract_template_id_from_summary(summary_raw: object) -> str | None:
    """Phase Q Step Q5 helper: pull ``template_id`` out of the ``summary`` JSON.

    Mirrors the in-line decode pattern used elsewhere in this module
    (``_row_to_response``, ``post_advance`` lazy-insert path) — kept
    here so :func:`_insert_reward_step_as_current` can reuse it without
    duplicating the JSON tolerance branches. Returns ``None`` for any
    decode / shape failure so legacy rows continue to fall back to the
    pre-Q reward behaviour.
    """
    if not summary_raw or not isinstance(summary_raw, str):
        return None
    try:
        payload = json.loads(summary_raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    tid = payload.get("template_id")
    if isinstance(tid, str) and tid:
        return tid
    return None


def _resolve_primary_element_id(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    template_id: str | None,
) -> str | None:
    """Phase Q Step Q5: find the activity's "primary" element id.

    The primary element id is the ``element_id`` of the first persisted
    ``activity_steps`` row (lowest seq) whose template-time step has a
    non-null ``element_id``. ``activity_steps`` does not store
    ``element_id`` as its own column (additive, see
    :func:`_resolve_element_id_for_persisted_step`) — we walk the
    persisted rows in seq order and resolve each via the template.

    Returns ``None`` when:

    * the activity has no ``template_id`` (legacy row pre-M3 envelope),
    * the template can't be loaded (deleted / orphaned),
    * no persisted step row maps to a template step with ``element_id``.

    Matches the M3 element_id surface contract: the kiosk's
    ElementCard renders against the same template-time field.
    """
    if template_id is None:
        return None
    template = find_template_by_id(template_id)
    if template is None:
        return None
    template_step_element: dict[str, str] = {}
    for step in template.steps:
        # Template steps without an ``id`` predate Phase G branching and
        # can't be matched against ``activity_steps.step_template_id``;
        # skip them rather than raise. Steps without ``element_id`` are
        # outside Phase Q's element-aware reward scope.
        if step.id is not None and step.element_id is not None:
            template_step_element[step.id] = step.element_id
    if not template_step_element:
        return None
    rows = conn.execute(
        "SELECT step_template_id FROM activity_steps "
        "WHERE activity_id = ? AND step_template_id IS NOT NULL "
        "ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    for r in rows:
        step_template_id = r["step_template_id"]
        if not isinstance(step_template_id, str):
            continue
        element_id = template_step_element.get(step_template_id)
        if element_id is not None:
            return element_id
    return None


def _insert_reward_step_as_current(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    row: sqlite3.Row,
) -> bool:
    """Phase L Step L4 (two-phase fix): resolve + INSERT a reward step
    as the new ``current`` step at activity end. Does NOT touch the
    activity's state — the caller leaves ``state=running`` so the
    kiosk's ``isActiveKioskActivity`` predicate keeps StepCard mounted
    and dispatches on ``step.kind === "reward"`` to RewardStep.

    Reads ``activities.reward_type`` (the parent's pick at approve
    time; defaults to ``"random"`` when L4's post_approve wrote it).
    Pre-L legacy rows have NULL here → the resolver is NOT called and
    no reward step is appended.

    Builds a :class:`RewardActivityContext` from the activity row +
    the current step count, calls :func:`resolve_reward`, and on a hit
    INSERTs a ``kind="reward"`` row at ``seq = max(seq) + 1`` with
    ``current=1``. Flips every prior step's ``current=0`` so the
    reward step is the sole current row. For picture rewards, updates
    ``rewards.last_used_at`` to the current ISO timestamp so the
    sort-by-recency picker rotates picks across rewards.

    Bumps ``activities.version`` once (for the insert + the
    current-flag move, batched). Returns ``True`` when a reward step
    was appended, ``False`` otherwise (no resolver hit / pre-L row /
    already has a reward step).
    """
    if _has_reward_step(conn, activity_id):
        return False

    reward_type_raw = row["reward_type"] if "reward_type" in row.keys() else None
    if reward_type_raw is None:
        return False
    if reward_type_raw not in ("picture", "joke", "song", "random", "none"):
        _logger.warning(
            "reward step skipped: activity %r has unknown reward_type %r",
            activity_id,
            reward_type_raw,
        )
        return False
    # L follow-up Change D: explicit opt-out short-circuits before the
    # resolver runs. Distinct from NULL (legacy) but produces the same
    # observable outcome — no reward step appended; the activity wraps
    # cleanly via the legacy single-advance path in
    # :func:`_maybe_fire_reward_or_complete`.
    if reward_type_raw == "none":
        return False

    persona_id_raw = row["persona_id"] if "persona_id" in row.keys() else None
    session_id_raw = row["session_id"] if "session_id" in row.keys() else None
    slot_fills_raw = row["slot_fills_json"] if "slot_fills_json" in row.keys() else None

    # ``current_step_count`` mixes into the deterministic seed in the
    # L3 resolver. Use ``COUNT(*)`` over existing steps — this counts
    # the steps the kid has actually walked, matching the L3 contract
    # ("re-advancing produces a different outcome when the step
    # counter changes").
    step_count_row = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(MAX(seq), 0) AS max_seq "
        "FROM activity_steps WHERE activity_id = ?",
        (activity_id,),
    ).fetchone()
    current_step_count = int(step_count_row["c"])
    new_seq = int(step_count_row["max_seq"]) + 1

    # Phase Q Step Q5: extract the activity's primary element id from
    # the first persisted step row whose template-time element_id is
    # non-null. Pre-Q activities (and templates without element_id
    # steps) thread ``None`` here, preserving the pre-Q reward picker
    # behaviour (theme → untheme fallback).
    summary_raw = row["summary"] if "summary" in row.keys() else None
    template_id_for_element = _extract_template_id_from_summary(summary_raw)
    primary_element_id = _resolve_primary_element_id(
        conn, activity_id=activity_id, template_id=template_id_for_element
    )

    context = RewardActivityContext(
        id=activity_id,
        session_id=str(session_id_raw) if session_id_raw is not None else "",
        persona_id=str(persona_id_raw) if persona_id_raw is not None else None,
        slot_fills_json=str(slot_fills_raw) if slot_fills_raw is not None else None,
        current_step_count=current_step_count,
        element_id=primary_element_id,
    )
    resolved = resolve_reward(conn, context, reward_type_raw)
    if resolved is None:
        return False

    metadata = _build_reward_step_metadata(resolved)
    metadata_blob = json.dumps(metadata, sort_keys=True)
    # Reward body text on the kiosk-rendered ``activity_steps.body``
    # field: this is what the kiosk's default text dispatcher would
    # show for an unknown ``kind``. Use the resolver's ``body`` which
    # is the display name for picture, punchline for joke, or title
    # for song — a sensible fallback if the kiosk's L10 RewardStep
    # component is missing in the wire (e.g. a stale parent-only
    # build). The ``activity_steps.body`` NOT NULL constraint requires
    # a non-empty value; ResolvedReward.body is always non-empty by
    # the L3 contract.
    body_text = resolved.body
    with conn:
        # Two-phase terminal advance: the kid just crossed past the
        # last regular step. We INSERT the reward at ``current=1`` and
        # flip every other step to ``current=0`` so the kiosk's
        # StepCard mounts on the reward step (state stays ``running``
        # — the caller does NOT transition to completed in this phase).
        conn.execute(
            "UPDATE activity_steps SET current = 0 WHERE activity_id = ?",
            (activity_id,),
        )
        conn.execute(
            "INSERT INTO activity_steps "
            "(id, activity_id, seq, body, sfx, expected_action, current, action_slot, "
            " choices_json, step_template_id, kind, metadata_json) "
            "VALUES (?, ?, ?, ?, NULL, NULL, 1, NULL, NULL, NULL, ?, ?)",
            (
                str(uuid.uuid4()),
                activity_id,
                new_seq,
                body_text,
                "reward",
                metadata_blob,
            ),
        )
        # Bump version so optimistic-concurrency clients see the new
        # reward step on their next read. Two-phase contract: Phase 1
        # is V → V+1 (this insert), Phase 2 is V+1 → V+2 (the
        # subsequent dismiss-advance that flips to completed).
        conn.execute(
            "UPDATE activities SET version = version + 1 WHERE id = ?",
            (activity_id,),
        )
        # Per-row recency tracking for picture rewards only — the joke
        # / song corpora live in JSON-on-disk and have no per-entry
        # timestamp column (v2 candidate; see plan §4 "Out of scope").
        if resolved.kind == "picture":
            conn.execute(
                "UPDATE rewards SET last_used_at = ? WHERE id = ?",
                (_now_iso(), resolved.reward_id),
            )
    return True


def _terminal_advance(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    activity_id: str,
    expected_version: int,
) -> ActivityResponse:
    """Phase G G3: terminal advance dispatcher.

    Phase L UAT fix (two-phase contract): the kiosk's render is
    mutually exclusive between "active kiosk activity" (StepCard) and
    "All done!" (terminal screen) based on ``isTerminalState(state)``.
    The pre-fix L4 wire transitioned to ``completed`` BEFORE inserting
    the reward step, so the kiosk jumped straight to "All done!" and
    the reward step never rendered.

    The fix splits ``_terminal_advance`` into two phases that the
    caller (post_advance) re-enters via successive /advance calls:

    * **Phase 1** — no reward step exists yet. If the activity has a
      fireable reward, INSERT it as ``current=1`` (via
      :func:`_insert_reward_step_as_current`) and KEEP state=running.
      The kiosk renders the reward step in StepCard / RewardStep.
      Otherwise (NULL reward_type, no rewards, all pools empty) fall
      through to the legacy completed-transition path so activities
      without a reward still wrap cleanly in one advance.

    * **Phase 2** — reward step already exists (the kid tapped past
      it). Transition to ``completed``, flip ALL steps to current=0,
      and bump version. The kiosk receives state=completed and
      switches to "All done!".

    The dispatch is internal — callers continue to invoke
    ``_terminal_advance`` and don't need to know which phase fires.
    """
    if _has_reward_step(conn, activity_id):
        return _complete_after_reward(conn, pubsub, activity_id, expected_version)
    return _maybe_fire_reward_or_complete(conn, pubsub, activity_id, expected_version)


def _maybe_fire_reward_or_complete(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    activity_id: str,
    expected_version: int,
) -> ActivityResponse:
    """Phase L two-phase terminal advance — Phase 1.

    The kid has just crossed past the last regular step. Try to fire
    a reward step (KEEP state=running, INSERT reward at current=1). If
    the reward picker doesn't fire (NULL reward_type, no eligible
    rewards, empty pools), fall through to the legacy
    completed-transition path so the activity still wraps in one call.

    The optimistic-concurrency contract is preserved both ways: if
    the reward fires we bump version once (V → V+1, the insert); if
    the reward doesn't fire we bump version once (V → V+1, the state
    transition). The caller sees a deterministic single-version-bump
    Phase 1 either way.
    """
    # Read the row at the current version so the helper can build the
    # RewardActivityContext. ``expected_version`` was validated by the
    # caller (post_advance) before reaching here.
    row = _fetch_activity_row(conn, activity_id)
    if int(row["version"]) != expected_version:
        # Defense-in-depth: a concurrent writer slipped in between
        # post_advance's check and ours. Surface as 409 same as the
        # _attempt_transition path below.
        raise VersionConflictError(int(row["version"]), str(row["state"]))

    appended = _insert_reward_step_as_current(conn, activity_id=activity_id, row=row)
    if appended:
        # Reward step now lives as current=1, state still ``running``.
        # The version bump happened inside the helper; re-read the row
        # so the response carries V+1 + the new step.
        row = _fetch_activity_row(conn, activity_id)
        response = _row_to_response(conn, row)
        _emit_state(pubsub, response)
        return response

    # No reward fired (NULL reward_type / no eligible rewards / empty
    # pools). Legacy single-advance path: transition to completed and
    # flip all current=0 so the kiosk shows "All done!" in one call.
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


def _complete_after_reward(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    activity_id: str,
    expected_version: int,
) -> ActivityResponse:
    """Phase L two-phase terminal advance — Phase 2.

    The reward step already exists (Phase 1 inserted it). The kid
    just tapped past it (or the 6s auto-advance fired). Transition
    state to ``completed``, flip ALL steps' ``current=0`` (including
    the reward step), bump version once. The kiosk receives
    state=completed → ``showAllDone`` → "All done!" screen.
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
    "ApproveQuestionRequest",
    "ApproveQuestionResponse",
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
