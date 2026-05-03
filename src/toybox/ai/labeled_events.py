"""``labeled_events`` recorder + ChatML serialization helpers.

Every activity generation (Claude or offline) writes one
``labeled_events`` row before the activity is returned. Parent-signal
endpoints (thumbs-up, dismiss-before-start, end-early) update the row
in place by ``activity_id`` to fill ``parent_signal``,
``parent_signal_set_at``, and ``ended_at_step``.

The judge sampler (``toybox.ai.judge``) is invoked from the call
sites that hold an ``AIClient`` — :func:`toybox.api.activities._do_propose`
(via FastAPI dependency injection) and
:class:`toybox.core.escalation.EscalationDispatcher` (via constructor
factory). After :func:`record_generation` returns the new ``row_id``,
the call site invokes :func:`schedule_judge_sample` with a 1-in-N rate
(env-tunable via ``TOYBOX_EVAL_JUDGE_RATE``). The kid-facing path is
**never** awaited on the judge — the call is fire-and-forget via
:func:`asyncio.AbstractEventLoop.create_task` so the latency path is
unchanged. Background task exceptions surface via a done-callback
that logs at WARNING (see :func:`_log_judge_task_exception`).

ChatML format
-------------

The ``inputs_chatml_json`` column stores the generator's input as a
ChatML messages array — the same shape Phase E SFT iterations consume
without re-encoding::

    [
      {"role": "system", "content": "<persona card + rubric guardrails>"},
      {"role": "user",   "content": "<intent + slot + persona + inventory + transcript window>"}
    ]

The ``activity_json`` column stores the assistant's output side. Phase
E's exporter (``toybox.ai.eval_dump``) appends the assistant turn to
the messages array on the fly so the JSONL line is a complete
fine-tuning example.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from ..activities.models import Activity

_logger = logging.getLogger(__name__)

JUDGE_RATE_ENV: Final[str] = "TOYBOX_EVAL_JUDGE_RATE"
DEFAULT_JUDGE_RATE: Final[int] = 5

# Generator path values stored in the ``generator_path`` column. The DB
# CHECK constraint accepts only these three, with ``"local"`` reserved
# for Phase E's locally-hosted SFT model.
GENERATOR_PATH_CLAUDE: Final[str] = "claude"
GENERATOR_PATH_OFFLINE: Final[str] = "offline"
GENERATOR_PATH_LOCAL: Final[str] = "local"

VALID_GENERATOR_PATHS: Final[frozenset[str]] = frozenset(
    {GENERATOR_PATH_CLAUDE, GENERATOR_PATH_OFFLINE, GENERATOR_PATH_LOCAL}
)

# Parent signal values pinned by the spec. Stored as REAL so future
# half-steps (e.g. -0.25 for "dismissed but parent left a comment") can
# be added without a migration.
PARENT_SIGNAL_THUMBS_UP: Final[float] = 1.0
PARENT_SIGNAL_DISMISS: Final[float] = -1.0
PARENT_SIGNAL_END_EARLY: Final[float] = -0.5


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ChatMLMessage:
    """One ChatML message: ``{"role": ..., "content": ...}``."""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class GeneratorContext:
    """Context the generator (offline or Claude) saw at call time.

    Stored verbatim in ``inputs_chatml_json`` so Phase E SFT exports
    have everything needed to reconstruct a training example. All
    fields are optional except ``intent`` — the rest are populated as
    the codebase grows them (Phase C step 18 wires real
    toys/rooms/profiles).
    """

    intent: str
    slot: str | None = None
    transcript_window: str | None = None
    persona_id: str | None = None
    persona_card: str | None = None
    available_toys: tuple[str, ...] = field(default_factory=tuple)
    available_rooms: tuple[str, ...] = field(default_factory=tuple)
    child_profile: dict[str, Any] | None = None
    listening_mode: int | None = None
    time_of_day: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def build_chatml_messages(ctx: GeneratorContext) -> list[ChatMLMessage]:
    """Build the ChatML system + user messages for ``ctx``.

    Pure function — no I/O. Output is a stable, sorted-key JSON
    rendering so two equivalent contexts produce byte-identical
    ``inputs_chatml_json`` (load-bearing for Phase E dedup).
    """
    system_lines = [
        "You generate short play activities for a child's interactive toy.",
        "Reply with EXACTLY one JSON object matching the Activity schema "
        "(5 steps, indexed 0..4) and nothing else.",
        "Stay in persona; reference only the listed toys and rooms; "
        "use age-appropriate vocabulary.",
    ]
    if ctx.persona_card:
        system_lines.append("Persona card:")
        system_lines.append(ctx.persona_card)
    system_content = "\n".join(system_lines)

    user_payload: dict[str, Any] = {
        "intent": ctx.intent,
        "slot": ctx.slot,
        "transcript_window": ctx.transcript_window,
        "persona_id": ctx.persona_id,
        "available_toys": list(ctx.available_toys),
        "available_rooms": list(ctx.available_rooms),
        "child_profile": ctx.child_profile,
        "listening_mode": ctx.listening_mode,
        "time_of_day": ctx.time_of_day,
    }
    if ctx.extra:
        # Merge extras under their own key so the canonical fields
        # above stay queryable without a wildcard scan.
        user_payload["extra"] = ctx.extra
    user_content = json.dumps(user_payload, sort_keys=True, ensure_ascii=False)

    return [
        ChatMLMessage(role="system", content=system_content),
        ChatMLMessage(role="user", content=user_content),
    ]


def serialize_chatml(messages: Sequence[ChatMLMessage]) -> str:
    """Serialize ChatML messages to the JSON form stored in the DB."""
    return json.dumps(
        [m.to_dict() for m in messages],
        sort_keys=True,
        ensure_ascii=False,
    )


def serialize_activity(activity: Activity) -> str:
    """Serialize an :class:`Activity` to the JSON form stored in the DB."""
    return activity.model_dump_json()


def resolve_judge_rate() -> int:
    """Read ``TOYBOX_EVAL_JUDGE_RATE`` from env, clamped to >= 1.

    Default 5 means ~20% of generations get judged. Values <= 0 are
    treated as "no sampling" and return ``0`` — :func:`should_judge`
    interprets 0 as never-judge so an operator can disable cleanly.
    """
    raw = os.environ.get(JUDGE_RATE_ENV)
    if raw is None or raw == "":
        return DEFAULT_JUDGE_RATE
    try:
        n = int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int; using default %d",
            JUDGE_RATE_ENV,
            raw,
            DEFAULT_JUDGE_RATE,
        )
        return DEFAULT_JUDGE_RATE
    if n < 0:
        return 0
    return n


def should_judge(row_id: int, *, rate: int | None = None) -> bool:
    """Decide whether ``row_id`` is in the 1-in-``rate`` sample.

    Uses ``row_id % rate == 0`` rather than RNG so the sampling is
    deterministic given the autoincrement id — easier to reason about
    in tests and avoids a ``random.Random`` allocation per generation.
    Rate 0 means never judge.
    """
    effective_rate = rate if rate is not None else resolve_judge_rate()
    if effective_rate <= 0:
        return False
    return row_id % effective_rate == 0


def record_generation(
    conn: sqlite3.Connection,
    *,
    activity: Activity,
    ctx: GeneratorContext,
    generator_path: str,
    generated_at: str | None = None,
) -> int:
    """Insert one ``labeled_events`` row. Returns the new row id.

    ``generator_path`` must be one of :data:`VALID_GENERATOR_PATHS`. The
    DB CHECK constraint backstops this but we validate up front so a
    typo at the call site surfaces with the offending value rather than
    SQLite's opaque ``CHECK constraint failed`` message.

    Raises:
        ValueError: if ``generator_path`` is not recognised.
    """
    if generator_path not in VALID_GENERATOR_PATHS:
        raise ValueError(
            f"generator_path must be one of {sorted(VALID_GENERATOR_PATHS)}; "
            f"got {generator_path!r}"
        )
    messages = build_chatml_messages(ctx)
    inputs_json = serialize_chatml(messages)
    activity_json = serialize_activity(activity)
    ts = generated_at if generated_at is not None else _now_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, "
            " inputs_chatml_json, activity_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (activity.id, ts, generator_path, inputs_json, activity_json),
        )
    new_id = cur.lastrowid
    if new_id is None:  # pragma: no cover - sqlite always returns an id
        raise RuntimeError("INSERT did not return a rowid")
    return int(new_id)


def update_parent_signal(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    signal: float,
    ended_at_step: int | None = None,
    set_at: str | None = None,
) -> bool:
    """Update the ``parent_signal`` column for the row matching ``activity_id``.

    Returns True iff a row was updated. Returns False (and logs at
    DEBUG) when no labeled_events row exists for ``activity_id`` —
    that's the legitimate case for activities generated before this
    migration shipped. The endpoint MUST NOT 500 just because there's
    no row to label.

    ``ended_at_step`` is only persisted when non-None; the column stays
    NULL otherwise (matches the schema's "null unless end-early" doc).
    """
    ts = set_at if set_at is not None else _now_iso()
    sets = ["parent_signal = ?", "parent_signal_set_at = ?"]
    params: list[Any] = [signal, ts]
    if ended_at_step is not None:
        sets.append("ended_at_step = ?")
        params.append(ended_at_step)
    params.append(activity_id)
    sql = f"UPDATE labeled_events SET {', '.join(sets)} WHERE activity_id = ?"
    with conn:
        cur = conn.execute(sql, params)
    if cur.rowcount == 0:
        _logger.debug(
            "no labeled_events row for activity_id=%s; signal=%s skipped",
            activity_id,
            signal,
        )
        return False
    return True


def update_judge_scores(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    judge_scores_json: str,
    run_at: str | None = None,
) -> bool:
    """Persist judge scores for ``activity_id``. Returns True on update."""
    ts = run_at if run_at is not None else _now_iso()
    with conn:
        cur = conn.execute(
            "UPDATE labeled_events "
            "SET judge_scores_json = ?, judge_run_at = ? "
            "WHERE activity_id = ?",
            (judge_scores_json, ts, activity_id),
        )
    return cur.rowcount > 0


def _log_judge_task_exception(task: asyncio.Task[Any]) -> None:
    """Done-callback: drain background judge tasks so unraised exceptions log.

    Without this callback an exception inside ``judge_and_persist`` (e.g.
    a sqlite write failure on :func:`update_judge_scores`) would surface
    as ``Task exception was never retrieved`` at process exit and never
    show up in operational logs. We swallow :class:`asyncio.CancelledError`
    silently — that's the documented shutdown path.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    _logger.warning(
        "background judge task raised (%s: %s); judge_scores stays NULL",
        type(exc).__name__,
        exc,
    )


def _run_judge_in_thread(coro_factory: Any) -> threading.Thread:
    """Run ``coro_factory()`` on a fresh event loop in a daemon thread.

    Used as the fallback when :func:`schedule_judge_sample` is called
    from a sync HTTP handler that has no running event loop. The
    thread is daemon so it doesn't block process shutdown; if the judge
    call is mid-flight when the server stops it just dies — that's the
    same best-effort contract as the in-loop scheduler. Exceptions are
    logged via the same callback used for the in-loop path.
    """
    def _runner() -> None:
        try:
            asyncio.run(coro_factory())
        except Exception as exc:  # noqa: BLE001 -- best-effort
            _logger.warning(
                "background judge task raised in thread (%s: %s); "
                "judge_scores stays NULL",
                type(exc).__name__,
                exc,
            )

    thread = threading.Thread(
        target=_runner, name="toybox-judge-sample", daemon=True
    )
    thread.start()
    return thread


def schedule_judge_sample(
    *,
    row_id: int,
    activity: Activity,
    ctx: GeneratorContext,
    judge_call: Any,
    rate: int | None = None,
) -> Any:
    """If ``row_id`` is in the sample, fire the judge async (no await).

    ``judge_call`` is the awaitable factory — typically
    :func:`toybox.ai.judge.judge_and_persist` partial'd with the
    dependencies it needs (an :class:`AIClient` and a
    ``db_path_resolver``). We never await the result here; the
    kid-facing path returns immediately and the judge fills the row when
    it lands.

    Returns one of:

    * The scheduled :class:`asyncio.Task`, when called from within a
      running event loop (the dispatcher's async path).
    * A :class:`threading.Thread` running a fresh event loop, when
      called from a sync HTTP handler (the propose path). The thread is
      daemon so process shutdown isn't blocked.
    * ``None`` when the sample was skipped or ``judge_call`` is ``None``.

    Tests may inspect the return value to assert on the schedule
    decision; production callers should ignore it.

    A done-callback is attached on the asyncio path so a raise inside
    ``judge_call`` (e.g. sqlite write failure) is logged at WARNING
    rather than surfacing as ``Task exception was never retrieved`` at
    process shutdown. The thread path mirrors that with a try/except
    around :func:`asyncio.run`.
    """
    if judge_call is None:
        return None
    if not should_judge(row_id, rate=rate):
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — caller is on a sync HTTP handler (the
        # propose path). Spin up a daemon thread that owns its own
        # loop. Capture (activity, ctx, row_id) by closure so the
        # coroutine builds inside the new loop's thread.
        return _run_judge_in_thread(
            lambda: judge_call(activity=activity, ctx=ctx, row_id=row_id)
        )
    coro = judge_call(activity=activity, ctx=ctx, row_id=row_id)
    task = loop.create_task(coro)
    task.add_done_callback(_log_judge_task_exception)
    return task


__all__ = [
    "ChatMLMessage",
    "DEFAULT_JUDGE_RATE",
    "GENERATOR_PATH_CLAUDE",
    "GENERATOR_PATH_LOCAL",
    "GENERATOR_PATH_OFFLINE",
    "GeneratorContext",
    "JUDGE_RATE_ENV",
    "PARENT_SIGNAL_DISMISS",
    "PARENT_SIGNAL_END_EARLY",
    "PARENT_SIGNAL_THUMBS_UP",
    "VALID_GENERATOR_PATHS",
    "build_chatml_messages",
    "record_generation",
    "resolve_judge_rate",
    "schedule_judge_sample",
    "serialize_activity",
    "serialize_chatml",
    "should_judge",
    "update_judge_scores",
    "update_parent_signal",
]
