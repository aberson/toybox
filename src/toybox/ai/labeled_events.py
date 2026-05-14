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
    tool_calls: Sequence[dict[str, Any]] | None = None,
) -> int:
    """Insert one ``labeled_events`` row. Returns the new row id.

    ``generator_path`` must be one of :data:`VALID_GENERATOR_PATHS`. The
    DB CHECK constraint backstops this but we validate up front so a
    typo at the call site surfaces with the offending value rather than
    SQLite's opaque ``CHECK constraint failed`` message.

    ``tool_calls`` is the per-generation tool-call telemetry produced
    by loop-mode generators (see
    ``documentation/plan/phase-e.md`` §"Tool-call telemetry shape").
    ``None`` (the default) leaves the column NULL so single-shot rows
    are byte-identical to the pre-Step-28 path. Loop-mode callers pass
    a (possibly empty) list which is JSON-encoded and stored verbatim.

    Raises:
        ValueError: if ``generator_path`` is not recognised.
    """
    if generator_path not in VALID_GENERATOR_PATHS:
        raise ValueError(
            f"generator_path must be one of {sorted(VALID_GENERATOR_PATHS)}; got {generator_path!r}"
        )
    messages = build_chatml_messages(ctx)
    inputs_json = serialize_chatml(messages)
    activity_json = serialize_activity(activity)
    ts = generated_at if generated_at is not None else _now_iso()
    tool_calls_json: str | None = None
    if tool_calls is not None:
        tool_calls_json = json.dumps(
            list(tool_calls),
            sort_keys=True,
            ensure_ascii=False,
        )
    with conn:
        cur = conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, "
            " inputs_chatml_json, activity_json, tool_calls) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                activity.id,
                ts,
                generator_path,
                inputs_json,
                activity_json,
                tool_calls_json,
            ),
        )
    new_id = cur.lastrowid
    if new_id is None:  # pragma: no cover - sqlite always returns an id
        raise RuntimeError("INSERT did not return a rowid")
    return int(new_id)


def get_tool_calls(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
) -> list[dict[str, Any]] | None:
    """Read back the tool_calls JSON column for ``activity_id``.

    Returns ``None`` for rows persisted before Step 28's loop mode
    landed, OR rows from the single-shot path (which leaves the column
    NULL by design). Returns an empty list when loop mode ran but the
    model emitted zero tool calls before the final activity.
    """
    row = conn.execute(
        "SELECT tool_calls FROM labeled_events WHERE activity_id = ?",
        (activity_id,),
    ).fetchone()
    if row is None or row["tool_calls"] is None:
        return None
    return list(json.loads(row["tool_calls"]))


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
                "background judge task raised in thread (%s: %s); judge_scores stays NULL",
                type(exc).__name__,
                exc,
            )

    thread = threading.Thread(target=_runner, name="toybox-judge-sample", daemon=True)
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
        return _run_judge_in_thread(lambda: judge_call(activity=activity, ctx=ctx, row_id=row_id))
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
    "count_rows",
    "get_tool_calls",
    "record_generation",
    "resolve_judge_rate",
    "schedule_judge_sample",
    "serialize_activity",
    "serialize_chatml",
    "should_judge",
    "update_judge_scores",
    "update_parent_signal",
]


# --------------------------------------------------------------------- CLI


def count_rows(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    sft_filter: bool = False,
) -> int:
    """Count rows in ``labeled_events`` matching the optional filters.

    Args:
        conn: SQLite connection.
        since: Optional ISO 8601 lower bound on ``generated_at``. Caller
            is expected to pass a validated string; the CLI front-end
            does the validation.
        sft_filter: When True, apply the Phase E SFT-export filter:
            (1) ``parent_signal != -1``, AND
            (2) when ``judge_scores_json`` is present, ``safety >= 4``
            AND the sum of the five 1..5-int rubric scores
            (schema, age_appropriateness, doability, persona_fidelity,
            coherence) is ``>= 18`` — i.e. mean ``>= 3.6`` (strictly
            above 3.5). Sums of integers can't equal 17.5; the SQL
            comparison ``>= 17.5`` is therefore equivalent to ``>= 18``,
            which we keep documented honestly here. AND
            (3) ``redact_for_sft = 0`` — the operator opt-out flag
            added by migration 0013 excludes rows the operator marked
            ineligible (PII the automated redactor can't catch).

    Returns:
        The integer count.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if since is not None:
        clauses.append("generated_at >= ?")
        params.append(since)
    if sft_filter:
        # Mean-quality floor: judge scores live in a JSON column. We
        # apply the filter via json_extract so the CLI doesn't have to
        # decode every row in Python. The mean-quality formula is
        # ((schema + age_appropriateness + doability + persona_fidelity
        #   + coherence) / 5.0). We keep the SQL straightforward by
        # using sqlite's json1 extension (shipped with the stdlib build
        # used by uv-managed Python on Windows).
        clauses.append("(parent_signal IS NULL OR parent_signal != -1)")
        clauses.append(
            "(judge_scores_json IS NULL OR ("
            " CAST(json_extract(judge_scores_json, '$.safety') AS INTEGER) >= 4"
            " AND ("
            "  CAST(json_extract(judge_scores_json, '$.schema') AS INTEGER)"
            "  + CAST(json_extract(judge_scores_json, '$.age_appropriateness') AS INTEGER)"
            "  + CAST(json_extract(judge_scores_json, '$.doability') AS INTEGER)"
            "  + CAST(json_extract(judge_scores_json, '$.persona_fidelity') AS INTEGER)"
            "  + CAST(json_extract(judge_scores_json, '$.coherence') AS INTEGER)"
            " ) >= 17.5"
            "))"
        )
        clauses.append("redact_for_sft = 0")
    sql = "SELECT COUNT(*) AS n FROM labeled_events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    row = conn.execute(sql, params).fetchone()
    return int(row["n"]) if row is not None else 0


def _validate_iso8601(raw: str) -> str:
    """Raise :class:`argparse.ArgumentTypeError` if ``raw`` is not ISO 8601."""
    import argparse

    candidate = raw
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--since must be ISO 8601 (got {raw!r}: {exc})") from exc
    return raw


def _cli_main(argv: list[str] | None = None) -> int:
    """Operator CLI: ``python -m toybox.ai.labeled_events --count [--since ISO] [--sft-filter]``.

    Prints the integer count (followed by a newline) to stdout. Exits 0
    on success. Useful for shell-side gates such as::

        if [ "$(uv run python -m toybox.ai.labeled_events --count --sft-filter)" -ge 50 ]; then
          echo "ok"
        fi
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="toybox.ai.labeled_events",
        description=(
            "Count labeled_events rows matching optional filters. "
            "When --sft-filter is set, rows the operator opted out of "
            "(redact_for_sft = 1) are excluded from the count."
        ),
    )
    parser.add_argument(
        "--count",
        action="store_true",
        help="Print the row count (default behavior; flag exists for shell parity).",
    )
    parser.add_argument(
        "--since",
        type=_validate_iso8601,
        default=None,
        help="Filter to rows with generated_at >= this ISO 8601 timestamp.",
    )
    parser.add_argument(
        "--sft-filter",
        action="store_true",
        help=(
            "Apply the SFT-export filter (parent_signal != -1 AND, when judge "
            "scores are present, safety >= 4 AND mean_quality >= 3.6 — i.e. "
            "strictly above 3.5; the rubric scores are 1..5 ints, so the sum "
            "of the five rubric fields is gated at >= 18; AND "
            "redact_for_sft = 0)."
        ),
    )
    args = parser.parse_args(argv)

    # Late import to keep module import lightweight.
    from ..db import resolve_db_path
    from ..db.connection import connect

    conn = connect(resolve_db_path())
    try:
        count = count_rows(conn, since=args.since, sft_filter=args.sft_filter)
    finally:
        conn.close()
    print(count)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli_main(sys.argv[1:]))
