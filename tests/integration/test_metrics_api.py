"""Integration tests for ``GET /api/metrics``.

End-to-end exercise of the REST endpoint via the shared TestClient
fixture in ``tests/integration/conftest.py``. Auth, scope, snapshot
shape, and counter surfacing are all verified through the wire.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.ai.breaker import BreakerState, CircuitBreaker
from toybox.api.metrics import get_metrics_breaker
from toybox.db.connection import connect
from toybox.metrics import record_buffer_overrun, reset_counters_for_test


@pytest.fixture(autouse=True)
def _reset_metrics_counters() -> None:
    reset_counters_for_test()


def _seed_session_row(db_path: Path, session_id: str = "session-1") -> str:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (session_id, "2026-05-03T12:00:00Z"),
            )
    finally:
        conn.close()
    return session_id


def _seed_activity(
    db_path: Path,
    *,
    activity_id: str,
    state: str,
    session_id: str = "session-1",
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, summary, persona_id, child_ids, "
                " room_ids, toy_ids, intent_source, created_at, started_at, ended_at) "
                "VALUES (?, ?, ?, 1, '{}', NULL, NULL, NULL, NULL, ?, "
                " datetime('now'), NULL, NULL)",
                (activity_id, session_id, state, "test"),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Auth / scope (M6: parametrize via _PROTECTED_ENDPOINTS to mirror the
# convention pinned by test_children_api.py / test_toys_api.py /
# test_rooms_api.py).
# ---------------------------------------------------------------------


_PROTECTED_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/metrics", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED_ENDPOINTS)
def test_metrics_endpoints_require_parent_token(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Every metrics endpoint must 401 without a token.

    Uses the same parametrize convention as the children/toys/rooms
    suites so a future expansion of the metrics surface picks up the
    contract automatically.
    """
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    detail = cast("dict[str, Any]", response.json())["detail"]
    assert detail["code"] == "auth_required"


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED_ENDPOINTS)
def test_metrics_endpoints_reject_child_token(
    client: TestClient,
    child_token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Child-scope tokens must not have access to the metrics endpoints."""
    headers = {"Authorization": f"Bearer {child_token}"}
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == 403
    detail = cast("dict[str, Any]", response.json())["detail"]
    assert detail["code"] == "auth_scope_forbidden"


# ---------------------------------------------------------------------
# Empty DB
# ---------------------------------------------------------------------


def test_metrics_empty_db_full_shape(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    resp = client.get("/api/metrics", headers=parent_headers)
    assert resp.status_code == 200, resp.text
    body = cast("dict[str, Any]", resp.json())
    # Every top-level field present
    for key in (
        "generated_at",
        "activities",
        "transcripts",
        "audio",
        "ai",
        "activity_quality",
        "eval_gate",
        "ws_subscribers",
    ):
        assert key in body, f"missing {key} in {body}"
    # Counters are zero — point-in-time per-state ``*_current`` shape.
    assert body["activities"]["proposed_current"] == 0
    assert body["activities"]["approved_current"] == 0
    assert body["activities"]["running_current"] == 0
    assert body["activities"]["completed_current"] == 0
    assert body["activities"]["ended_current"] == 0
    assert body["activities"]["dismissed_current"] == 0
    assert body["activities"]["didnt_work_current"] == 0
    assert body["transcripts"]["total"] == 0
    assert body["transcripts"]["last_24h"] == 0
    # Audio block has the expected nullable shape
    assert body["audio"]["mic_device"] is None
    assert body["audio"]["queue_depth"] == 0
    assert body["audio"]["buffer_overruns_total"] == 0
    # AI block surfaces the closed breaker
    assert body["ai"]["breaker_state"] == BreakerState.closed.value
    assert body["ai"]["breaker_retry_after_iso"] is None
    # listening_mode default is 3 (DEFAULT) once migrations seed the row
    assert body["ai"]["listening_mode"] >= 1
    assert body["ai"]["listening_mode"] <= 5
    # Activity quality nulls
    assert body["activity_quality"]["judge_parent_agreement"]["overlap_count"] == 0
    assert body["activity_quality"]["judge_parent_agreement"]["agreement_rate"] is None
    # Eval gate placeholder field is always present; the committed
    # fixture is all-placeholder so the flag is True at rest.
    assert body["eval_gate"]["placeholder_baseline"] is True


# ---------------------------------------------------------------------
# Activity counts reflect the DB
# ---------------------------------------------------------------------


def test_metrics_activity_counts_reflect_db(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    _seed_session_row(db_path)
    _seed_activity(db_path, activity_id="a1", state="proposed")
    _seed_activity(db_path, activity_id="a2", state="approved")
    _seed_activity(db_path, activity_id="a3", state="dismissed")
    _seed_activity(db_path, activity_id="a4", state="ended")

    resp = client.get("/api/metrics", headers=parent_headers)
    assert resp.status_code == 200, resp.text
    body = cast("dict[str, Any]", resp.json())
    assert body["activities"]["proposed_current"] == 1
    assert body["activities"]["approved_current"] == 1
    assert body["activities"]["dismissed_current"] == 1
    assert body["activities"]["ended_current"] == 1
    # All 4 are recent (datetime('now')) so last_24h reflects them too
    assert body["activities"]["last_24h"]["proposed"] == 1
    assert body["activities"]["last_24h"]["approved"] == 1


# ---------------------------------------------------------------------
# Buffer overrun counter increments on record call
# ---------------------------------------------------------------------


def test_buffer_overrun_counter_increments(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    record_buffer_overrun()
    record_buffer_overrun()
    resp = client.get("/api/metrics", headers=parent_headers)
    assert resp.status_code == 200
    body = cast("dict[str, Any]", resp.json())
    assert body["audio"]["buffer_overruns_total"] == 2


# ---------------------------------------------------------------------
# Breaker state parametrize
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setup", "expected_state"),
    [
        (lambda b: None, BreakerState.closed.value),
        (lambda b: b.record_429(retry_after=60.0), BreakerState.open.value),
    ],
)
def test_breaker_state_parametrize(
    client: TestClient,
    parent_headers: dict[str, str],
    app: Any,
    setup: Any,
    expected_state: str,
) -> None:
    breaker = CircuitBreaker()
    setup(breaker)
    app.dependency_overrides[get_metrics_breaker] = lambda: breaker
    resp = client.get("/api/metrics", headers=parent_headers)
    assert resp.status_code == 200, resp.text
    body = cast("dict[str, Any]", resp.json())
    assert body["ai"]["breaker_state"] == expected_state
    if expected_state == BreakerState.open.value:
        assert body["ai"]["breaker_retry_after_iso"] is not None


# ---------------------------------------------------------------------
# Labeled-events overlap surfaces in metrics
# ---------------------------------------------------------------------


def _seed_labeled_event(
    db_path: Path,
    *,
    activity_id: str,
    parent_signal: float | None,
    judge_scores: dict[str, int] | None,
    generated_at: str | None = None,
) -> None:
    judge_json = json.dumps(judge_scores) if judge_scores is not None else None
    # Default to a recent T-Z timestamp so it lands inside the
    # 24h window the agreement query (M2) now applies.
    when = generated_at if generated_at is not None else _recent_iso_z()
    conn: sqlite3.Connection = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO labeled_events "
                "(activity_id, generated_at, generator_path, inputs_chatml_json, "
                " activity_json, parent_signal, parent_signal_set_at, "
                " judge_scores_json, judge_run_at) "
                "VALUES (?, ?, 'offline', '[]', '{}', ?, NULL, ?, NULL)",
                (activity_id, when, parent_signal, judge_json),
            )
    finally:
        conn.close()


def _recent_iso_z() -> str:
    """Production-shape ISO-Z timestamp ~1 hour ago."""
    return (
        (datetime.now(UTC) - timedelta(hours=1))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def test_judge_parent_agreement_surfaces(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """M2: agreement now respects the 24h window. Seed only-recent rows."""
    _seed_session_row(db_path)
    _seed_activity(db_path, activity_id="ev1", state="ended")
    _seed_activity(db_path, activity_id="ev2", state="ended")
    judge_scores_high = {
        "schema": 5,
        "age_appropriateness": 5,
        "doability": 5,
        "persona_fidelity": 5,
        "coherence": 5,
        "safety": 5,
    }
    judge_scores_low = {
        "schema": 2,
        "age_appropriateness": 2,
        "doability": 2,
        "persona_fidelity": 2,
        "coherence": 2,
        "safety": 2,
    }
    _seed_labeled_event(
        db_path,
        activity_id="ev1",
        parent_signal=1.0,
        judge_scores=judge_scores_high,
        # Recent, T-Z format — inside the 24h window.
        generated_at=_recent_iso_z(),
    )
    _seed_labeled_event(
        db_path,
        activity_id="ev2",
        parent_signal=-1.0,
        judge_scores=judge_scores_low,
        generated_at=_recent_iso_z(),
    )

    resp = client.get("/api/metrics", headers=parent_headers)
    assert resp.status_code == 200
    body = cast("dict[str, Any]", resp.json())
    agreement = body["activity_quality"]["judge_parent_agreement"]
    assert agreement["overlap_count"] == 2
    # Both rows agree (positive/positive, negative/negative)
    assert agreement["agreement_rate"] == 1.0
