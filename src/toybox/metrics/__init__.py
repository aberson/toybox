"""Operator metrics: snapshot assembly + 30s ws topic publisher.

Public surface:

* :func:`record_buffer_overrun` — bumps the in-memory mic-overflow counter.
  Called from the audio thread when ``MicCapture._handle_frame`` fires the
  ``mic queue overflow`` log. Thread-safe via a module-level lock.
* :func:`get_metrics_snapshot` — pure read. Assembles the full snapshot
  from the in-memory counters + DB queries (counts from ``activities``,
  ``transcripts``, ``labeled_events``, ``settings``). Does not mutate.
* :func:`build_metrics_envelope` — wraps a snapshot in a ws envelope on
  the ``metrics`` topic.
* :func:`start_metrics_publisher` — fires a single asyncio task that
  publishes a snapshot every ``interval_sec`` seconds to a
  :class:`~toybox.core.pubsub.PubSub`. The returned :class:`asyncio.Task`
  cancels cleanly on shutdown.

Counter persistence
-------------------

Per the step 24 plan: load-bearing totals (``activities.proposed_total``,
``transcripts.total``, ``labeled_events.*``) come from DB COUNT(*) so
they survive process restarts. Only the buffer-overrun counter is
process-local — restart resets it to zero. That's acceptable for v1
because mic-overflow is an "is anything stalled right now?" health
signal, not an audit trail.

Ws reconnects do NOT reset any counter — pubsub subscribe/unsubscribe
is independent of counter state. The publisher fires on its own cadence
and the next subscriber sees the same in-memory + DB values.

Concurrency
-----------

``record_buffer_overrun`` is called from the PortAudio worker thread
(via :meth:`MicCapture._handle_frame`). The snapshot read path runs on
the FastAPI threadpool worker (the REST handler offloads
:func:`get_metrics_snapshot` via :func:`asyncio.to_thread`) and the ws
publisher reads from the asyncio loop, so the counter MUST be
thread-safe. We guard the int with a :class:`threading.Lock`. Snapshot
reads acquire the lock briefly to read the counter, then release before
doing DB work — this keeps any DB latency from blocking the audio
thread when it tries to bump the counter.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ..ai.breaker import BreakerState, CircuitBreaker
from ..ai.capability import is_capable
from ..ai.rubric import DIMENSION_KEYS
from ..core.capability import CapabilityReason
from ..core.listening import ListeningMode, current_mode
from ..core.mic_state import current_mic_enabled
from ..core.pubsub import PubSub
from ..core.throttle import min_interval_from_env
from ..db import connect, resolve_db_path
from ..ws.envelope import Envelope, build_envelope
from ..ws.topics import Topic

_logger = logging.getLogger(__name__)

# Default cadence for the ws publisher. Spec says "every 30s"; tests
# inject a much shorter interval (e.g. 0.05s) to assert at least two
# snapshots arrive within ~5s.
DEFAULT_PUBLISH_INTERVAL_SEC: Final[float] = 30.0

# Path to the eval-gate baseline file. Surfaced in the snapshot so the
# operator UI can flag "baseline is still placeholder" without a separate
# endpoint. The real baseline lives in tests/fixtures/eval/ alongside
# the eval CLI; we read it as JSON.
DEFAULT_BASELINE_PATH: Final[Path] = Path("tests") / "fixtures" / "eval" / "baseline_scores.json"

# Lock + counter. Module-level state by design — see module docstring.
_lock = threading.Lock()
_buffer_overruns_total: int = 0


def record_buffer_overrun() -> None:
    """Bump the buffer-overrun counter. Thread-safe."""
    global _buffer_overruns_total
    with _lock:
        _buffer_overruns_total += 1


def reset_counters_for_test() -> None:
    """Reset the in-memory counter. Test-only — production never calls this.

    Module-level counters persist across pytest cases; tests that assert
    counter deltas need a clean slate. Production code never resets a
    live counter (no fitness purpose).
    """
    global _buffer_overruns_total
    with _lock:
        _buffer_overruns_total = 0


def _read_buffer_overruns() -> int:
    with _lock:
        return _buffer_overruns_total


# ---------------------------------------------------------------------
# Snapshot dataclasses
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActivityCounts:
    """Activity counters derived from the ``activities`` table.

    The ``*_current`` fields are point-in-time counts of rows currently
    in each state — e.g. ``approved_current`` drops to zero once the
    only approved row transitions to ``running``. The ``last_24h``
    breakdown is keyed by current state but filtered to rows whose
    ``created_at`` lies in the last 24h, mirroring the same semantics.
    """

    proposed_current: int
    approved_current: int
    running_current: int
    completed_current: int
    ended_current: int
    dismissed_current: int
    didnt_work_current: int
    last_24h: dict[str, int]


@dataclass(frozen=True, slots=True)
class TranscriptCounts:
    """Transcript counters derived from the ``transcripts`` table."""

    total: int
    last_24h: int


@dataclass(frozen=True, slots=True)
class AudioStatus:
    """Mic-pipeline diagnostics."""

    mic_device: str | None
    queue_depth: int
    buffer_overruns_total: int
    mic_enabled: bool = True


@dataclass(frozen=True, slots=True)
class AIStatus:
    """AI gate + breaker + listening-mode summary."""

    breaker_state: str
    breaker_retry_after_iso: str | None
    claude_capable: bool
    claude_capability_reason: str | None
    listening_mode: int
    min_interval_throttle_seconds: float


@dataclass(frozen=True, slots=True)
class JudgeParentAgreement:
    """Judge-vs-parent agreement metric on the labeled_events overlap.

    ``overlap_count`` is the number of rows where both ``parent_signal``
    and ``judge_scores_json`` are set. ``agreement_rate`` is the simpler
    metric (per the spec): percentage agreement on sign of
    ``parent_signal`` vs sign of ``mean(judge_scores) - 3.0``. A row
    with ``parent_signal == 0`` or judge mean exactly ``3.0`` counts as
    "neutral" and is excluded from the comparison numerator + denominator.
    Cohen's kappa is intentionally NOT computed — agreement_rate is a
    simpler proxy that the operator UI can act on.
    """

    overlap_count: int
    agreement_rate: float | None
    metric_name: str = "sign_agreement_rate"


@dataclass(frozen=True, slots=True)
class ActivityQuality:
    """Activity-quality summary derived from ``labeled_events``."""

    last_24h_mean_scores: dict[str, float | None]
    judge_parent_agreement: JudgeParentAgreement
    safety_autofails_last_24h: int


@dataclass(frozen=True, slots=True)
class EvalGateStatus:
    """Eval-gate snapshot from ``baseline_scores.json``."""

    last_run_at: str | None
    mean_dimension_scores: dict[str, float] | None
    regressions_detected: int
    placeholder_baseline: bool


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Full operator-dashboard snapshot.

    The model_dump form (via :meth:`to_dict`) is what the REST endpoint
    returns and what the ws envelope payload carries — same shape.
    """

    generated_at: str
    activities: ActivityCounts
    transcripts: TranscriptCounts
    audio: AudioStatus
    ai: AIStatus
    activity_quality: ActivityQuality
    eval_gate: EvalGateStatus
    ws_subscribers: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict (used by REST + ws envelope)."""
        return {
            "generated_at": self.generated_at,
            "ws_subscribers": self.ws_subscribers,
            "activities": {
                "proposed_current": self.activities.proposed_current,
                "approved_current": self.activities.approved_current,
                "running_current": self.activities.running_current,
                "completed_current": self.activities.completed_current,
                "ended_current": self.activities.ended_current,
                "dismissed_current": self.activities.dismissed_current,
                "didnt_work_current": self.activities.didnt_work_current,
                "last_24h": dict(self.activities.last_24h),
            },
            "transcripts": {
                "total": self.transcripts.total,
                "last_24h": self.transcripts.last_24h,
            },
            "audio": {
                "mic_device": self.audio.mic_device,
                "queue_depth": self.audio.queue_depth,
                "buffer_overruns_total": self.audio.buffer_overruns_total,
                "mic_enabled": self.audio.mic_enabled,
            },
            "ai": {
                "breaker_state": self.ai.breaker_state,
                "breaker_retry_after_iso": self.ai.breaker_retry_after_iso,
                "claude_capable": self.ai.claude_capable,
                "claude_capability_reason": self.ai.claude_capability_reason,
                "listening_mode": self.ai.listening_mode,
                "min_interval_throttle_seconds": self.ai.min_interval_throttle_seconds,
            },
            "activity_quality": {
                "last_24h_mean_scores": dict(self.activity_quality.last_24h_mean_scores),
                "judge_parent_agreement": {
                    "overlap_count": self.activity_quality.judge_parent_agreement.overlap_count,
                    "agreement_rate": self.activity_quality.judge_parent_agreement.agreement_rate,
                    "metric_name": self.activity_quality.judge_parent_agreement.metric_name,
                },
                "safety_autofails_last_24h": self.activity_quality.safety_autofails_last_24h,
            },
            "eval_gate": {
                "last_run_at": self.eval_gate.last_run_at,
                "mean_dimension_scores": (
                    dict(self.eval_gate.mean_dimension_scores)
                    if self.eval_gate.mean_dimension_scores is not None
                    else None
                ),
                "regressions_detected": self.eval_gate.regressions_detected,
                "placeholder_baseline": self.eval_gate.placeholder_baseline,
            },
        }


# ---------------------------------------------------------------------
# DB-derived helpers
# ---------------------------------------------------------------------


# Cutoff predicate used by every "last 24h" query in this module. The
# ``datetime(<column>)`` wrapper is load-bearing: production rows write
# their timestamps via ``datetime.now(UTC).isoformat(...).replace('+00:00', 'Z')``
# (e.g. ``2026-05-03T14:30:00Z``) while ``datetime('now', '-1 day')``
# renders as a space-separated, no-Z string (e.g. ``2026-05-02 14:30:00``).
# Without ``datetime(<column>)`` SQLite falls back to a lexicographic
# string compare where ``T`` (0x54) > space (0x20), so a row up to ~23h
# OLDER than the intended cutoff still satisfies ``column >= cutoff``.
# Wrapping both sides forces SQLite to parse them as datetimes before
# comparing, which is the only way to get the right answer regardless of
# whether the column is in T-Z or space-separated form.
_LAST_24H_PREDICATE = "datetime({col}) >= datetime('now', '-1 day')"


def _last_24h_clause(column: str) -> str:
    """Return ``datetime(<column>) >= datetime('now', '-1 day')``.

    Uses the single :data:`_LAST_24H_PREDICATE` template so every call
    site shares the same cutoff semantics.
    """
    return _LAST_24H_PREDICATE.format(col=column)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    """Run a single-row COUNT(*) query, returning 0 on error.

    Snapshot must never raise — the operator UI polls this every 30s and
    a transient sqlite blip should surface as zeroed counters with a log
    line, not a 500.
    """
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        _logger.warning("metrics: count query failed: %s", sql, exc_info=True)
        return 0
    if row is None:
        return 0
    return int(row[0]) if row[0] is not None else 0


def _activity_counts(conn: sqlite3.Connection) -> ActivityCounts:
    """Aggregate ``activities`` table counts (current state + last 24h)."""
    states = (
        "proposed",
        "approved",
        "running",
        "completed",
        "ended",
        "dismissed",
        "didnt_work",
    )
    current: dict[str, int] = {
        s: _safe_count(conn, "SELECT COUNT(*) FROM activities WHERE state = ?", (s,))
        for s in states
    }

    # Last 24h breakdown by current state. ``created_at`` is the propose
    # time; for "approved/dismissed/ended in the last 24h" we'd need a
    # state-transition log (Phase E). For v1 the proxy is "rows whose
    # created_at is in the last 24h, grouped by current state" — close
    # enough for an operator's "is anything happening?" view.
    cutoff_sql = (
        f"SELECT state, COUNT(*) FROM activities "  # noqa: S608 -- static template
        f"WHERE {_last_24h_clause('created_at')} "
        f"GROUP BY state"
    )
    last_24h: dict[str, int] = {s: 0 for s in states}
    try:
        for row in conn.execute(cutoff_sql).fetchall():
            state = str(row[0])
            if state in last_24h:
                last_24h[state] = int(row[1])
    except sqlite3.Error:
        _logger.warning("metrics: last_24h activities query failed", exc_info=True)
    return ActivityCounts(
        proposed_current=current["proposed"],
        approved_current=current["approved"],
        running_current=current["running"],
        completed_current=current["completed"],
        ended_current=current["ended"],
        dismissed_current=current["dismissed"],
        didnt_work_current=current["didnt_work"],
        last_24h=last_24h,
    )


def _transcript_counts(conn: sqlite3.Connection) -> TranscriptCounts:
    """Aggregate ``transcripts`` table counts.

    The schema has ``started_at`` (no ``created_at``) as the time column;
    we use that for the last-24h window. Rows with ``started_at IS NULL``
    are counted in ``total`` but not in the windowed count.
    """
    total = _safe_count(conn, "SELECT COUNT(*) FROM transcripts")
    last_24h = _safe_count(
        conn,
        f"SELECT COUNT(*) FROM transcripts "  # noqa: S608 -- static template
        f"WHERE started_at IS NOT NULL AND {_last_24h_clause('started_at')}",
    )
    return TranscriptCounts(total=total, last_24h=last_24h)


def _last_24h_mean_scores(conn: sqlite3.Connection) -> dict[str, float | None]:
    """Mean per-dimension judge scores over rows from the last 24h.

    ``judge_scores_json`` is a TEXT JSON blob persisted by the judge
    sampler (see :mod:`toybox.ai.judge`). Returns a dict keyed by every
    dimension in :data:`toybox.ai.rubric.DIMENSION_KEYS`; values are
    ``None`` when no judge-sampled rows exist in the window.
    """
    sums: dict[str, float] = {k: 0.0 for k in DIMENSION_KEYS}
    counts: dict[str, int] = {k: 0 for k in DIMENSION_KEYS}
    try:
        rows = conn.execute(
            f"SELECT judge_scores_json FROM labeled_events "  # noqa: S608 -- static template
            f"WHERE judge_scores_json IS NOT NULL "
            f"AND {_last_24h_clause('generated_at')}"
        ).fetchall()
    except sqlite3.Error:
        _logger.warning("metrics: judge-mean query failed", exc_info=True)
        return {k: None for k in DIMENSION_KEYS}

    for row in rows:
        raw = row[0]
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for key in DIMENSION_KEYS:
            value = payload.get(key)
            if isinstance(value, int | float):
                sums[key] += float(value)
                counts[key] += 1

    return {key: (sums[key] / counts[key]) if counts[key] > 0 else None for key in DIMENSION_KEYS}


def _judge_parent_agreement(conn: sqlite3.Connection) -> JudgeParentAgreement:
    """Compute the simpler ``sign_agreement_rate`` metric over the last 24h.

    For every row from the last 24h where BOTH ``parent_signal`` and
    ``judge_scores_json`` are non-null:

    * ``parent_signal`` is in {-1.0, -0.5, 0.0, +1.0}. Sign:
      negative→bad, zero→neutral, positive→good.
    * Judge mean = mean of the six dimension scores. Sign:
      ``mean - 3.0 < 0``→bad, ``== 0``→neutral, ``> 0``→good.

    A row counts as "agree" iff both signs are non-zero and equal. Rows
    where either side is neutral are excluded from the rate (numerator
    + denominator). ``overlap_count`` is the raw row count BEFORE the
    neutral exclusion, so the operator can see "we have N labelled
    overlap rows" even when the agreement metric is undefined. Rows
    with ``generated_at`` older than 24h are excluded entirely so the
    metric matches the "Activity quality (24h)" UI section it lives in.
    """
    try:
        rows = conn.execute(
            f"SELECT parent_signal, judge_scores_json FROM labeled_events "  # noqa: S608
            f"WHERE parent_signal IS NOT NULL AND judge_scores_json IS NOT NULL "
            f"AND {_last_24h_clause('generated_at')}"
        ).fetchall()
    except sqlite3.Error:
        _logger.warning("metrics: agreement query failed", exc_info=True)
        return JudgeParentAgreement(overlap_count=0, agreement_rate=None)

    overlap = len(rows)
    if overlap == 0:
        return JudgeParentAgreement(overlap_count=0, agreement_rate=None)

    agree = 0
    compared = 0
    for row in rows:
        parent_signal = row[0]
        judge_raw = row[1]
        try:
            parent_value = float(parent_signal)
        except (TypeError, ValueError):
            continue
        try:
            judge_payload = json.loads(judge_raw) if judge_raw else None
        except json.JSONDecodeError:
            continue
        if not isinstance(judge_payload, dict):
            continue
        # Mean across all six dimensions present in the JSON.
        scores: list[float] = []
        for key in DIMENSION_KEYS:
            v = judge_payload.get(key)
            if isinstance(v, int | float):
                scores.append(float(v))
        if not scores:
            continue
        judge_mean = sum(scores) / len(scores)
        parent_sign = _sign(parent_value)
        judge_sign = _sign(judge_mean - 3.0)
        if parent_sign == 0 or judge_sign == 0:
            continue
        compared += 1
        if parent_sign == judge_sign:
            agree += 1

    rate = (agree / compared) if compared > 0 else None
    return JudgeParentAgreement(overlap_count=overlap, agreement_rate=rate)


def _sign(x: float) -> int:
    """Return -1 / 0 / +1. Used by :func:`_judge_parent_agreement`."""
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _safety_autofails_last_24h(conn: sqlite3.Connection) -> int:
    """Count rows with ``safety <= 1`` in ``judge_scores_json`` over 24h.

    Mirrors :data:`toybox.ai.rubric.SAFETY_AUTOFAIL`. ``safety`` is
    extracted from the JSON payload — there's no dedicated column.
    """
    try:
        rows = conn.execute(
            f"SELECT judge_scores_json FROM labeled_events "  # noqa: S608 -- static template
            f"WHERE judge_scores_json IS NOT NULL "
            f"AND {_last_24h_clause('generated_at')}"
        ).fetchall()
    except sqlite3.Error:
        _logger.warning("metrics: safety autofail query failed", exc_info=True)
        return 0
    count = 0
    for row in rows:
        raw = row[0]
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        safety = payload.get("safety")
        if isinstance(safety, int | float) and float(safety) <= 1.0:
            count += 1
    return count


def _eval_gate_status(baseline_path: Path) -> EvalGateStatus:
    """Read the eval baseline file. Best-effort.

    The file shape is documented in :mod:`toybox.ai.eval_run`: a top-
    level dict with ``generated_at`` and a ``fixtures`` map keyed by
    fixture id. Each fixture has ``placeholder: bool`` + ``scores``
    dict. When every fixture is ``placeholder=true`` the gate is in
    its "no real baseline yet" state — the operator UI surfaces that
    so the dashboard isn't misread as "we're holding a real baseline".
    """
    if not baseline_path.is_file():
        return EvalGateStatus(
            last_run_at=None,
            mean_dimension_scores=None,
            regressions_detected=0,
            placeholder_baseline=True,
        )
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _logger.warning("metrics: baseline read failed at %s", baseline_path, exc_info=True)
        return EvalGateStatus(
            last_run_at=None,
            mean_dimension_scores=None,
            regressions_detected=0,
            placeholder_baseline=True,
        )
    if not isinstance(payload, dict):
        return EvalGateStatus(
            last_run_at=None,
            mean_dimension_scores=None,
            regressions_detected=0,
            placeholder_baseline=True,
        )
    generated_at = payload.get("generated_at")
    last_run_at = generated_at if isinstance(generated_at, str) else None

    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, dict) or not fixtures:
        return EvalGateStatus(
            last_run_at=last_run_at,
            mean_dimension_scores=None,
            regressions_detected=0,
            placeholder_baseline=True,
        )

    placeholder_count = 0
    sums: dict[str, float] = {k: 0.0 for k in DIMENSION_KEYS}
    counts: dict[str, int] = {k: 0 for k in DIMENSION_KEYS}
    for _fid, fixture in fixtures.items():
        if not isinstance(fixture, dict):
            continue
        if fixture.get("placeholder") is True:
            placeholder_count += 1
        scores = fixture.get("scores")
        if not isinstance(scores, dict):
            continue
        for key in DIMENSION_KEYS:
            v = scores.get(key)
            if isinstance(v, int | float):
                sums[key] += float(v)
                counts[key] += 1
    means: dict[str, float] = {
        key: (sums[key] / counts[key]) for key in DIMENSION_KEYS if counts[key] > 0
    }
    placeholder_baseline = placeholder_count >= len(fixtures)

    return EvalGateStatus(
        last_run_at=last_run_at,
        mean_dimension_scores=means if means else None,
        # ``regressions_detected`` is computed by ``eval_run`` at CI time;
        # the snapshot exposes 0 here because there's no live regression
        # state to surface without re-running the eval. The field exists
        # so a future "stash regressions in a settings row" wire-up has
        # a slot to land in without a snapshot-shape change.
        regressions_detected=0,
        placeholder_baseline=placeholder_baseline,
    )


def _audio_status(
    *,
    mic_device: str | None,
    queue_depth: int,
    mic_enabled: bool,
) -> AudioStatus:
    """Build the AudioStatus block from caller-supplied live values.

    The mic device and queue depth come from the live :class:`MicCapture`
    instance — there's no module-level handle to it. The metrics module
    accepts them as parameters so the dependency graph stays acyclic
    (audio.capture imports metrics for ``record_buffer_overrun``; metrics
    must NOT import audio.capture).
    """
    return AudioStatus(
        mic_device=mic_device,
        queue_depth=queue_depth,
        buffer_overruns_total=_read_buffer_overruns(),
        mic_enabled=mic_enabled,
    )


def _ai_status(breaker: CircuitBreaker, listening_mode: int | None) -> AIStatus:
    """Build the AIStatus block from the live breaker + persisted mode.

    ``listening_mode`` may be ``None`` when the metrics caller hasn't
    pre-resolved it; in that case we fall back to ``OFFLINE`` (mirroring
    :func:`toybox.ai.capability._read_listening_mode`'s defensive
    behaviour). The capability check is best-effort and runs the same
    pure compose logic the runtime uses; a probe failure surfaces as
    ``claude_capable=False`` with a reason string.
    """
    state = breaker.state
    retry_after_iso: str | None = None
    if state is BreakerState.open:
        # The breaker stores ``_cooldown_until`` as a monotonic-clock
        # timestamp; computing an ISO wall-clock ETA requires us to
        # translate. We use ``datetime.now(UTC) + remaining_seconds``.
        # Internal access is intentional — the public API doesn't
        # surface the remaining cooldown otherwise.
        remaining = max(
            0.0,
            breaker._cooldown_until - breaker._time(),  # noqa: SLF001 -- internal access by design
        )
        try:
            now = datetime.now(UTC)
            eta = now.timestamp() + remaining
            retry_after_iso = (
                datetime.fromtimestamp(eta, tz=UTC)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (OSError, OverflowError, ValueError):  # pragma: no cover -- defensive
            # Leave retry_after_iso at None; the dashboard renders "—".
            pass

    return AIStatus(
        breaker_state=state.value,
        breaker_retry_after_iso=retry_after_iso,
        claude_capable=False,
        claude_capability_reason=None,
        listening_mode=(
            int(listening_mode) if listening_mode is not None else int(ListeningMode.OFFLINE)
        ),
        min_interval_throttle_seconds=min_interval_from_env(),
    )


# ---------------------------------------------------------------------
# Public snapshot entrypoint
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotInputs:
    """Live values the snapshot needs that aren't in the DB.

    The metrics module deliberately does NOT hold module-level handles
    to ``MicCapture`` or :class:`CircuitBreaker`; instead callers pass
    them per-snapshot. This keeps the import graph one-directional
    (audio + ai → metrics) and lets tests inject stubs trivially.
    """

    breaker: CircuitBreaker
    mic_device: str | None = None
    mic_queue_depth: int = 0
    ws_subscribers: int = 0
    listening_mode: int | None = None
    capability_check_result: tuple[bool, CapabilityReason | None] | None = None
    baseline_path: Path = field(default_factory=lambda: DEFAULT_BASELINE_PATH)


def get_metrics_snapshot(
    conn: sqlite3.Connection,
    inputs: SnapshotInputs,
) -> MetricsSnapshot:
    """Assemble the full snapshot. Pure read — never mutates ``conn``.

    Failures in any sub-query log WARNING and the field defaults to a
    safe empty value (``0`` for counters, ``None`` for optional means).
    The dashboard surfaces a stale or partial snapshot rather than
    blanking out on the first transient sqlite blip.
    """
    activities = _activity_counts(conn)
    transcripts = _transcript_counts(conn)
    try:
        mic_enabled = current_mic_enabled(conn)
    except sqlite3.Error:
        # Defensive: a transient DB blip during a metrics tick should
        # surface a "mic on" default rather than mislabel the status as
        # muted. Audit trail prefers false-positives (extra rows) over
        # false-negatives (silently-dropped rows).
        mic_enabled = True
    audio = _audio_status(
        mic_device=inputs.mic_device,
        queue_depth=inputs.mic_queue_depth,
        mic_enabled=mic_enabled,
    )

    # Resolve listening mode: caller value wins; otherwise read from
    # the same connection (best-effort, falls back to OFFLINE).
    mode_value = inputs.listening_mode
    if mode_value is None:
        try:
            mode_value = int(current_mode(conn))
        except (sqlite3.Error, ValueError):
            mode_value = int(ListeningMode.OFFLINE)

    ai = _ai_status(inputs.breaker, listening_mode=mode_value)

    # Capability is async; if the caller didn't pre-compute it (the ws
    # publisher does, the unit tests typically don't) we leave the
    # breaker-state-only view in place. The capability fields stay
    # ``False`` / ``None`` rather than misleadingly showing ``True``.
    if inputs.capability_check_result is not None:
        capable, reason = inputs.capability_check_result
        ai = AIStatus(
            breaker_state=ai.breaker_state,
            breaker_retry_after_iso=ai.breaker_retry_after_iso,
            claude_capable=capable,
            claude_capability_reason=reason.value if reason is not None else None,
            listening_mode=ai.listening_mode,
            min_interval_throttle_seconds=ai.min_interval_throttle_seconds,
        )

    last_24h_means = _last_24h_mean_scores(conn)
    agreement = _judge_parent_agreement(conn)
    safety_fails = _safety_autofails_last_24h(conn)

    eval_gate = _eval_gate_status(inputs.baseline_path)

    return MetricsSnapshot(
        generated_at=_now_iso(),
        activities=activities,
        transcripts=transcripts,
        audio=audio,
        ai=ai,
        activity_quality=ActivityQuality(
            last_24h_mean_scores=last_24h_means,
            judge_parent_agreement=agreement,
            safety_autofails_last_24h=safety_fails,
        ),
        eval_gate=eval_gate,
        ws_subscribers=inputs.ws_subscribers,
    )


def build_metrics_envelope(snapshot: MetricsSnapshot) -> Envelope:
    """Wrap a :class:`MetricsSnapshot` in a ws envelope on the metrics topic."""
    return build_envelope(topic=Topic.metrics, payload=snapshot.to_dict())


# ---------------------------------------------------------------------
# 30s publisher
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublisherDeps:
    """Dependencies the publisher pulls a fresh snapshot from each tick.

    ``conn_factory`` returns a per-tick connection so a long-lived task
    doesn't pin a single sqlite handle (which would interact poorly with
    pytest's per-test DB swap and with operator hot-restart). The
    connection is closed at the end of each tick.

    ``inputs_factory`` builds a :class:`SnapshotInputs` per tick — the
    live mic device / queue / breaker state can drift between ticks.
    """

    conn_factory: Callable[[], sqlite3.Connection]
    inputs_factory: Callable[[], SnapshotInputs]


async def _publish_once(pubsub: PubSub, deps: PublisherDeps) -> None:
    """One snapshot + publish cycle. All errors logged + swallowed.

    The capability probe is awaited per tick (the sync ``inputs_factory``
    cannot run an async probe) and threaded through ``SnapshotInputs``
    so every published snapshot reflects the live capability state. A
    probe failure surfaces as ``claude_capable=False`` with no reason.
    """
    try:
        conn = deps.conn_factory()
    except Exception:  # noqa: BLE001 -- defensive
        _logger.warning("metrics publisher: conn_factory failed", exc_info=True)
        return
    try:
        try:
            inputs = deps.inputs_factory()
        except Exception:  # noqa: BLE001 -- defensive
            _logger.warning("metrics publisher: inputs_factory failed", exc_info=True)
            return
        # Resolve capability inline if the factory didn't pre-compute it.
        # The REST endpoint pre-computes; the publisher's sync factory
        # cannot, so we await here and substitute via dataclasses.replace
        # (SnapshotInputs is frozen).
        if inputs.capability_check_result is None:
            try:
                cap_result = await resolve_capability(
                    inputs.breaker,
                    listening_mode=inputs.listening_mode,
                )
                inputs = dataclasses.replace(inputs, capability_check_result=cap_result)
            except Exception:  # noqa: BLE001 -- defensive
                _logger.warning("metrics publisher: capability resolve failed", exc_info=True)
        try:
            snapshot = get_metrics_snapshot(conn, inputs)
        except Exception:  # noqa: BLE001 -- defensive
            _logger.warning("metrics publisher: snapshot build failed", exc_info=True)
            return
    finally:
        try:
            conn.close()
        except sqlite3.Error:  # pragma: no cover -- defensive
            pass

    envelope = build_metrics_envelope(snapshot)
    try:
        pubsub.publish(envelope)
    except Exception:  # noqa: BLE001 -- defensive
        _logger.warning("metrics publisher: publish failed", exc_info=True)


async def _publisher_loop(
    pubsub: PubSub,
    deps: PublisherDeps,
    *,
    interval_sec: float,
) -> None:
    """Loop body: emit a snapshot every ``interval_sec`` seconds.

    The loop publishes immediately on entry so a fresh subscriber
    doesn't have to wait the full interval for the first snapshot, then
    sleeps and re-publishes. Cancelled cleanly via :class:`asyncio.CancelledError`.
    """
    try:
        while True:
            await _publish_once(pubsub, deps)
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        # Publish a final snapshot on shutdown? No — the loop is a
        # producer for the dashboard, not an audit log. A shutdown
        # snapshot would just be noise.
        raise


def start_metrics_publisher(
    pubsub: PubSub,
    deps: PublisherDeps,
    *,
    interval_sec: float = DEFAULT_PUBLISH_INTERVAL_SEC,
) -> asyncio.Task[None]:
    """Spawn the publisher task. Caller owns the cancel.

    Returns the asyncio :class:`Task`. Mirror the pattern used by the
    existing ws send/recv tasks in :func:`toybox.ws.server._serve`:
    keep a reference, cancel on shutdown, and ``await`` the task so the
    cancellation propagates and the loop teardown is clean.
    """
    if interval_sec <= 0:
        raise ValueError(f"interval_sec must be > 0, got {interval_sec}")
    return asyncio.create_task(
        _publisher_loop(pubsub, deps, interval_sec=interval_sec),
        name="toybox-metrics-publisher",
    )


# ---------------------------------------------------------------------
# Convenience: build a default conn_factory + inputs_factory.
# ---------------------------------------------------------------------


def default_conn_factory() -> Callable[[], sqlite3.Connection]:
    """Return a conn_factory that reads ``TOYBOX_DB_PATH`` per call.

    Used by the production lifespan. Tests pass a per-test factory that
    points at the per-test SQLite file.
    """

    def _factory() -> sqlite3.Connection:
        return connect(resolve_db_path(), check_same_thread=False)

    return _factory


async def resolve_capability(
    breaker: CircuitBreaker,
    listening_mode: int | None,
) -> tuple[bool, CapabilityReason | None]:
    """Run the live capability check and return ``(capable, reason)``.

    Wrapper so callers (the ws publisher inputs_factory) can pre-compute
    capability without each one importing :mod:`toybox.ai.capability`
    directly.
    """
    return await is_capable(breaker, listening_mode=listening_mode)


__all__ = [
    "ActivityCounts",
    "ActivityQuality",
    "AIStatus",
    "AudioStatus",
    "build_metrics_envelope",
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_PUBLISH_INTERVAL_SEC",
    "default_conn_factory",
    "EvalGateStatus",
    "get_metrics_snapshot",
    "JudgeParentAgreement",
    "MetricsSnapshot",
    "PublisherDeps",
    "record_buffer_overrun",
    "reset_counters_for_test",
    "resolve_capability",
    "SnapshotInputs",
    "start_metrics_publisher",
    "TranscriptCounts",
]
