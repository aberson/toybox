"""Unit tests for :mod:`toybox.metrics`.

Direct coverage of the snapshot builder against a hand-seeded SQLite DB
+ judge-vs-parent agreement maths against synthetic ``labeled_events``
rows. The integration tests in ``tests/integration/test_metrics_api.py``
exercise the full REST + ws envelope path.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from toybox.ai.breaker import BreakerState, CircuitBreaker
from toybox.ai.rubric import DIMENSION_KEYS
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.metrics import (
    DEFAULT_BASELINE_PATH,
    SnapshotInputs,
    build_metrics_envelope,
    get_metrics_snapshot,
    record_buffer_overrun,
    reset_counters_for_test,
)
from toybox.metrics import _eval_gate_status as _eval_gate_status_fn
from toybox.ws.topics import Topic


def _iso_hours_ago(hours: float) -> str:
    """Return a production-shape ``T...Z`` ISO timestamp ``hours`` ago.

    Mirrors the format produced by ``datetime.now(UTC).isoformat(...)``
    + the ``+00:00 → Z`` swap used everywhere in the production write
    path. Pinning the format here is load-bearing for the H1 regression
    test: the bug was that the SQL cutoff string (``YYYY-MM-DD HH:MM:SS``)
    sorts lexicographically wrong against ``YYYY-MM-DDTHH:MM:SSZ``, so
    the regression only reproduces with this exact wire shape.
    """
    moment = datetime.now(UTC) - timedelta(hours=hours)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh, migrated SQLite file."""
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(db_path)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _reset_counters() -> Iterator[None]:
    """Module-level counters persist across cases; reset before + after."""
    reset_counters_for_test()
    yield
    reset_counters_for_test()


def _seed_session(conn: sqlite3.Connection) -> str:
    sid = "session-1"
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            (sid, "2026-05-03T12:00:00Z"),
        )
    return sid


def _seed_activity(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    state: str,
    session_id: str,
    created_at: str = "2026-05-03T12:00:00Z",
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, persona_id, child_ids, "
            " room_ids, toy_ids, intent_source, created_at, started_at, ended_at) "
            "VALUES (?, ?, ?, 1, '{}', NULL, NULL, NULL, NULL, ?, ?, NULL, NULL)",
            (activity_id, session_id, state, "test", created_at),
        )


def _seed_labeled_event(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    parent_signal: float | None,
    judge_scores: dict[str, int] | None,
    generated_at: str | None = None,
) -> None:
    judge_json = json.dumps(judge_scores) if judge_scores is not None else None
    when = generated_at if generated_at is not None else _iso_hours_ago(1)
    with conn:
        conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, inputs_chatml_json, "
            " activity_json, parent_signal, parent_signal_set_at, "
            " judge_scores_json, judge_run_at) "
            "VALUES (?, ?, 'offline', '[]', '{}', ?, NULL, ?, NULL)",
            (activity_id, when, parent_signal, judge_json),
        )


# ---------------------------------------------------------------------
# Empty DB
# ---------------------------------------------------------------------


def test_empty_db_zeroed_counts(conn: sqlite3.Connection) -> None:
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.activities.proposed_current == 0
    assert snap.activities.approved_current == 0
    assert snap.activities.running_current == 0
    assert snap.activities.completed_current == 0
    assert snap.activities.dismissed_current == 0
    assert snap.activities.ended_current == 0
    assert snap.activities.didnt_work_current == 0
    assert snap.transcripts.total == 0
    assert snap.transcripts.last_24h == 0
    assert snap.activity_quality.judge_parent_agreement.overlap_count == 0
    assert snap.activity_quality.judge_parent_agreement.agreement_rate is None
    assert snap.activity_quality.safety_autofails_last_24h == 0
    # Per-dimension means are None when there are no judge-sampled rows.
    for key in DIMENSION_KEYS:
        assert snap.activity_quality.last_24h_mean_scores[key] is None


# ---------------------------------------------------------------------
# Activity counters
# ---------------------------------------------------------------------


def test_activity_state_counters_reflect_db_state(conn: sqlite3.Connection) -> None:
    sid = _seed_session(conn)
    _seed_activity(conn, activity_id="a1", state="proposed", session_id=sid)
    _seed_activity(conn, activity_id="a2", state="proposed", session_id=sid)
    _seed_activity(conn, activity_id="a3", state="approved", session_id=sid)
    _seed_activity(conn, activity_id="a4", state="running", session_id=sid)
    _seed_activity(conn, activity_id="a5", state="completed", session_id=sid)
    _seed_activity(conn, activity_id="a6", state="ended", session_id=sid)
    _seed_activity(conn, activity_id="a7", state="dismissed", session_id=sid)
    _seed_activity(conn, activity_id="a8", state="didnt_work", session_id=sid)

    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.activities.proposed_current == 2
    assert snap.activities.approved_current == 1
    assert snap.activities.running_current == 1
    assert snap.activities.completed_current == 1
    assert snap.activities.ended_current == 1
    assert snap.activities.dismissed_current == 1
    assert snap.activities.didnt_work_current == 1


def test_last_24h_window_excludes_old_rows(conn: sqlite3.Connection) -> None:
    """H1 regression: 24h window must use ``datetime(col)`` so production
    ``T...Z`` timestamps compare correctly against SQLite's
    ``datetime('now', '-1 day')`` cutoff (which renders space-separated,
    no-Z). Lexicographic compare put rows up to ~23h older than the
    cutoff inside the window. Using production-format timestamps here is
    the only way to actually exercise the regression — a SQLite-format
    timestamp string would land on the same side of the (broken) compare
    as the cutoff and pass the test for the wrong reason.
    """
    sid = _seed_session(conn)
    # 25 hours ago: production ISO-Z format. With the old broken SQL
    # ('column >= datetime(...)' with no datetime() wrapper on the
    # column) this row sorted as >= the space-separated cutoff and
    # leaked into the window. With the fix it's correctly excluded.
    _seed_activity(
        conn,
        activity_id="old",
        state="proposed",
        session_id=sid,
        created_at=_iso_hours_ago(25),
    )
    # 23 hours ago: inside the window, production format.
    _seed_activity(
        conn,
        activity_id="new",
        state="approved",
        session_id=sid,
        created_at=_iso_hours_ago(23),
    )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    # Both rows count in current totals.
    assert snap.activities.proposed_current == 1
    assert snap.activities.approved_current == 1
    # Last-24h includes only the recent (23h-old) row.
    assert snap.activities.last_24h["approved"] == 1
    assert snap.activities.last_24h["proposed"] == 0


def test_last_24h_proposed_count_is_one_with_production_iso_z_format(
    conn: sqlite3.Connection,
) -> None:
    """H1 regression spec: seed one ~23h-old row + one ~25h-old row
    using ``T...Z`` production format and assert the window count is
    exactly 1 (not 2, which is what the lexicographic-compare bug
    produced). This is the test that would have failed against iter-1.
    """
    sid = _seed_session(conn)
    _seed_activity(
        conn,
        activity_id="recent",
        state="proposed",
        session_id=sid,
        created_at=_iso_hours_ago(23),
    )
    _seed_activity(
        conn,
        activity_id="too-old",
        state="proposed",
        session_id=sid,
        created_at=_iso_hours_ago(25),
    )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.activities.last_24h["proposed"] == 1


# ---------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------


def test_transcript_counts(conn: sqlite3.Connection) -> None:
    sid = _seed_session(conn)
    with conn:
        conn.execute(
            "INSERT INTO transcripts (id, session_id, mic_id, started_at, "
            " ended_at, text, confidence, triggered_intent) "
            "VALUES ('t1', ?, NULL, ?, NULL, 'hi', 0.9, NULL)",
            (sid, _iso_hours_ago(1)),
        )
        conn.execute(
            "INSERT INTO transcripts (id, session_id, mic_id, started_at, "
            " ended_at, text, confidence, triggered_intent) "
            "VALUES ('t-old', ?, NULL, ?, NULL, 'old', 0.9, NULL)",
            (sid, _iso_hours_ago(25)),
        )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.transcripts.total == 2
    assert snap.transcripts.last_24h == 1


# ---------------------------------------------------------------------
# Buffer overrun counter (process-local)
# ---------------------------------------------------------------------


def test_record_buffer_overrun_thread_safe_increment(conn: sqlite3.Connection) -> None:
    record_buffer_overrun()
    record_buffer_overrun()
    record_buffer_overrun()
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.audio.buffer_overruns_total == 3


# ---------------------------------------------------------------------
# Judge-parent agreement metric
# ---------------------------------------------------------------------


def _make_judge_scores(value: int) -> dict[str, int]:
    return {key: value for key in DIMENSION_KEYS}


def test_agreement_metric_full_agreement(conn: sqlite3.Connection) -> None:
    sid = _seed_session(conn)
    _seed_activity(conn, activity_id="ev1", state="ended", session_id=sid)
    _seed_activity(conn, activity_id="ev2", state="ended", session_id=sid)
    # Parent thumbs-up + judge mean=5 (positive sign agreement)
    _seed_labeled_event(
        conn,
        activity_id="ev1",
        parent_signal=1.0,
        judge_scores=_make_judge_scores(5),
    )
    # Parent dismiss + judge mean=2 (negative sign agreement)
    _seed_labeled_event(
        conn,
        activity_id="ev2",
        parent_signal=-1.0,
        judge_scores=_make_judge_scores(2),
    )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.activity_quality.judge_parent_agreement.overlap_count == 2
    assert snap.activity_quality.judge_parent_agreement.agreement_rate == 1.0


def test_agreement_metric_partial_disagreement(conn: sqlite3.Connection) -> None:
    sid = _seed_session(conn)
    _seed_activity(conn, activity_id="ev1", state="ended", session_id=sid)
    _seed_activity(conn, activity_id="ev2", state="ended", session_id=sid)
    # Agreement: parent +1 / judge mean=5
    _seed_labeled_event(
        conn,
        activity_id="ev1",
        parent_signal=1.0,
        judge_scores=_make_judge_scores(5),
    )
    # Disagreement: parent -1 / judge mean=5
    _seed_labeled_event(
        conn,
        activity_id="ev2",
        parent_signal=-1.0,
        judge_scores=_make_judge_scores(5),
    )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.activity_quality.judge_parent_agreement.overlap_count == 2
    # 1 of 2 compared rows agreed → 0.5
    assert snap.activity_quality.judge_parent_agreement.agreement_rate == 0.5


def test_agreement_metric_neutral_rows_excluded(conn: sqlite3.Connection) -> None:
    sid = _seed_session(conn)
    _seed_activity(conn, activity_id="ev1", state="ended", session_id=sid)
    _seed_activity(conn, activity_id="ev2", state="ended", session_id=sid)
    # Neutral judge mean (=3.0) — excluded from numerator and denominator
    _seed_labeled_event(
        conn,
        activity_id="ev1",
        parent_signal=1.0,
        judge_scores=_make_judge_scores(3),
    )
    # Real positive agreement row
    _seed_labeled_event(
        conn,
        activity_id="ev2",
        parent_signal=1.0,
        judge_scores=_make_judge_scores(5),
    )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.activity_quality.judge_parent_agreement.overlap_count == 2
    # Only one row was compared; it agreed → 1.0
    assert snap.activity_quality.judge_parent_agreement.agreement_rate == 1.0


def test_agreement_metric_no_judge_rows_returns_none(conn: sqlite3.Connection) -> None:
    sid = _seed_session(conn)
    _seed_activity(conn, activity_id="ev1", state="proposed", session_id=sid)
    # parent signal but no judge scores → not in overlap
    _seed_labeled_event(
        conn,
        activity_id="ev1",
        parent_signal=1.0,
        judge_scores=None,
    )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.activity_quality.judge_parent_agreement.overlap_count == 0
    assert snap.activity_quality.judge_parent_agreement.agreement_rate is None


def test_agreement_metric_excludes_old_rows(conn: sqlite3.Connection) -> None:
    """M2: agreement is rendered under the "Activity quality (24h)"
    section, so the underlying query must also be 24h-windowed (and use
    the H1 fix's ``datetime(col)`` wrapper to compare ISO-Z timestamps
    correctly).
    """
    sid = _seed_session(conn)
    _seed_activity(conn, activity_id="ev-recent", state="ended", session_id=sid)
    _seed_activity(conn, activity_id="ev-old", state="ended", session_id=sid)
    # Recent overlap row in the window.
    _seed_labeled_event(
        conn,
        activity_id="ev-recent",
        parent_signal=1.0,
        judge_scores=_make_judge_scores(5),
        generated_at=_iso_hours_ago(2),
    )
    # ~25h old overlap row — must be excluded.
    _seed_labeled_event(
        conn,
        activity_id="ev-old",
        parent_signal=-1.0,
        judge_scores=_make_judge_scores(2),
        generated_at=_iso_hours_ago(25),
    )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    # Only the recent row contributes to the overlap count + rate.
    assert snap.activity_quality.judge_parent_agreement.overlap_count == 1
    assert snap.activity_quality.judge_parent_agreement.agreement_rate == 1.0


# ---------------------------------------------------------------------
# Per-dimension means
# ---------------------------------------------------------------------


def test_per_dimension_means_compute_correctly(conn: sqlite3.Connection) -> None:
    sid = _seed_session(conn)
    for i in range(3):
        aid = f"ev{i}"
        _seed_activity(conn, activity_id=aid, state="ended", session_id=sid)
    # 3 rows, each with safety=4 schema=5 etc — means should be exact.
    _seed_labeled_event(
        conn,
        activity_id="ev0",
        parent_signal=None,
        judge_scores={
            "schema": 5,
            "age_appropriateness": 4,
            "doability": 4,
            "persona_fidelity": 4,
            "coherence": 4,
            "safety": 4,
        },
    )
    _seed_labeled_event(
        conn,
        activity_id="ev1",
        parent_signal=None,
        judge_scores={
            "schema": 5,
            "age_appropriateness": 4,
            "doability": 4,
            "persona_fidelity": 4,
            "coherence": 4,
            "safety": 4,
        },
    )
    _seed_labeled_event(
        conn,
        activity_id="ev2",
        parent_signal=None,
        judge_scores={
            "schema": 5,
            "age_appropriateness": 4,
            "doability": 4,
            "persona_fidelity": 4,
            "coherence": 4,
            "safety": 4,
        },
    )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.activity_quality.last_24h_mean_scores["schema"] == 5.0
    assert snap.activity_quality.last_24h_mean_scores["age_appropriateness"] == 4.0
    assert snap.activity_quality.last_24h_mean_scores["safety"] == 4.0


def test_safety_autofails_counted(conn: sqlite3.Connection) -> None:
    sid = _seed_session(conn)
    _seed_activity(conn, activity_id="ev1", state="ended", session_id=sid)
    _seed_activity(conn, activity_id="ev2", state="ended", session_id=sid)
    _seed_labeled_event(
        conn,
        activity_id="ev1",
        parent_signal=None,
        judge_scores={
            "schema": 4,
            "age_appropriateness": 4,
            "doability": 4,
            "persona_fidelity": 4,
            "coherence": 4,
            "safety": 1,
        },
    )
    _seed_labeled_event(
        conn,
        activity_id="ev2",
        parent_signal=None,
        judge_scores={
            "schema": 4,
            "age_appropriateness": 4,
            "doability": 4,
            "persona_fidelity": 4,
            "coherence": 4,
            "safety": 5,
        },
    )
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    assert snap.activity_quality.safety_autofails_last_24h == 1


# ---------------------------------------------------------------------
# Breaker / AI status surfacing
# ---------------------------------------------------------------------


def test_breaker_state_surfaced_open_after_429(conn: sqlite3.Connection) -> None:
    breaker = CircuitBreaker()
    breaker.record_429(retry_after=60.0)
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=breaker))
    assert snap.ai.breaker_state == BreakerState.open.value
    # Retry-after ISO should be set on open
    assert snap.ai.breaker_retry_after_iso is not None
    assert "T" in snap.ai.breaker_retry_after_iso


# ---------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------


def test_build_metrics_envelope_topic_and_shape(conn: sqlite3.Connection) -> None:
    snap = get_metrics_snapshot(conn, SnapshotInputs(breaker=CircuitBreaker()))
    env = build_metrics_envelope(snap)
    assert env.topic is Topic.metrics
    assert env.schema_version == 1
    assert "generated_at" in env.payload
    assert "activities" in env.payload
    assert "activity_quality" in env.payload


# ---------------------------------------------------------------------
# Eval-gate placeholder branches (H2)
# ---------------------------------------------------------------------


def _write_baseline_fixture(
    path: Path,
    *,
    placeholder: bool,
    fixture_count: int = 2,
    generated_at: str = "2026-05-01T00:00:00Z",
) -> Path:
    """Write a baseline_scores.json with all fixtures flagged ``placeholder``.

    Returns the path for use as ``SnapshotInputs.baseline_path``.
    """
    fixtures: dict[str, dict[str, object]] = {}
    for i in range(fixture_count):
        fixtures[f"f{i:03d}"] = {
            "placeholder": placeholder,
            "scores": {
                "schema": 4,
                "age_appropriateness": 4,
                "doability": 4,
                "persona_fidelity": 4,
                "coherence": 4,
                "safety": 4,
                "hallucinated_props": [],
                "judge_notes": "test",
            },
        }
    payload: dict[str, object] = {
        "generated_at": generated_at,
        "fixtures": fixtures,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_eval_gate_real_baseline_flag_false(tmp_path: Path) -> None:
    """All fixtures flagged ``placeholder=false`` → ``placeholder_baseline=False``."""
    baseline = _write_baseline_fixture(
        tmp_path / "baseline_scores_real.json",
        placeholder=False,
    )
    status = _eval_gate_status_fn(baseline)
    assert status.placeholder_baseline is False
    assert status.last_run_at == "2026-05-01T00:00:00Z"
    assert status.mean_dimension_scores is not None
    assert status.mean_dimension_scores["schema"] == 4.0


def test_eval_gate_placeholder_baseline_flag_true(tmp_path: Path) -> None:
    """All fixtures flagged ``placeholder=true`` → ``placeholder_baseline=True``."""
    baseline = _write_baseline_fixture(
        tmp_path / "baseline_scores_placeholder.json",
        placeholder=True,
    )
    status = _eval_gate_status_fn(baseline)
    assert status.placeholder_baseline is True
    assert status.mean_dimension_scores is not None


def test_eval_gate_repo_baseline_is_placeholder() -> None:
    """The committed fixture is the placeholder shape used by every iter.

    Sanity-pin that the committed baseline still flags placeholder=True
    so a future refresh can't silently flip the dashboard while the
    judge still hasn't been run.
    """
    status = _eval_gate_status_fn(DEFAULT_BASELINE_PATH)
    assert status.placeholder_baseline is True


def test_eval_gate_missing_file_defaults_placeholder(tmp_path: Path) -> None:
    """Missing baseline file → conservative ``placeholder_baseline=True``."""
    missing = tmp_path / "does_not_exist.json"
    status = _eval_gate_status_fn(missing)
    assert status.placeholder_baseline is True
    assert status.last_run_at is None
    assert status.mean_dimension_scores is None


def test_eval_gate_malformed_json_defaults_placeholder_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed JSON → conservative default + WARNING log."""
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not really json", encoding="utf-8")

    import logging as _logging

    caplog.set_level(_logging.WARNING, logger="toybox.metrics")
    status = _eval_gate_status_fn(malformed)
    assert status.placeholder_baseline is True
    assert status.last_run_at is None
    assert status.mean_dimension_scores is None
    # The function logs at WARNING with "baseline read failed" on the
    # toybox.metrics logger.
    matching = [
        rec
        for rec in caplog.records
        if rec.name == "toybox.metrics" and "baseline read failed" in rec.getMessage()
    ]
    assert matching, "expected a 'baseline read failed' WARNING log"
