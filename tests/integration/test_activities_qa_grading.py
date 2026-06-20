"""Phase W Step W3 — Q&A answer-grading auto-resolve integration tests.

These exercise the FULL producer→consumer round trip (code-quality.md §4):
a Q&A step authored in a TEMPLATE (``question`` + ``expected_answer``) flows
through propose → persist → advance, a matching transcript row lands in the
last 30s, and the household ``qa_grading`` dial drives the grade — proving
that ``POST /advance`` AUTO-RESOLVES the R3 gate WITHOUT a parent
``approve-question`` call.

The primary test (``test_template_authored_qa_step_auto_resolves_end_to_end``)
deliberately does NOT direct-DB-UPDATE ``question`` / ``expected_answer`` onto
the row — it loads a fixture template that AUTHORS them and drives the real
propose → advance handlers, so it would have failed against the iteration-1
wiring gap (the INSERTs omitted both columns). It asserts the auto-grade
fires through the production caller (advance handler) AND that the
``activity.state`` WS envelope carries ``question_pending=False`` so the
kiosk un-hides Next.

The remaining cases (off / no-match / judge-branch) reuse the lighter-weight
``_inject_question`` direct-UPDATE helper because they assert GRADE-PATH
behavior (tolerance dial, transcript matching, judge vs offline) that is
orthogonal to the wiring, and the wiring is already proven end-to-end by the
primary test.

Covered:
* template-authored Q&A step → propose/advance auto-resolves end-to-end +
  WS envelope question_pending=False (the wiring round trip).
* lenient + matching transcript → advance auto-resolves (offline grader).
* qa_grading="off" → R3 parent-tap path UNCHANGED (advance still 409s;
  approve-question still works).
* non-matching / absent transcript → gate stays pending (409).
* Claude judge path: gate-green (judge "CORRECT") auto-resolves; the
  judge unavailable / not-green branch falls back to the offline grader.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.activities import get_sync_ai_client
from toybox.core.pubsub import PubSub
from toybox.core.transcript_retention import _format_ended_at_cutoff
from toybox.db.connection import connect
from toybox.ws.topics import Topic

# ---------------------------------------------------------------------------
# Fixtures — stage the Q&A-authoring fixture template + force offline path.
# ---------------------------------------------------------------------------


@pytest.fixture
def qa_gate_dir(tmp_path: Path) -> Path:
    """Stage ``qa_gate.json`` as the only ``boredom`` template.

    The template authors ``question`` + ``expected_answer`` on its first
    step (``ask``), so the seeded picker MUST land on it and the propose
    path MUST carry both fields into ``activity_steps`` — which is exactly
    the wiring iteration-1 missed.
    """
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "activities"
        / "branching"
        / "qa_gate.json"
    )
    staged = tmp_path / "qa_templates"
    staged.mkdir()
    (staged / "boredom.json").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    src_schema = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "toybox"
        / "activities"
        / "templates"
        / "_schema.json"
    )
    shutil.copy(src_schema, staged / "_schema.json")
    return staged


@pytest.fixture
def offline_client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient with ``get_sync_ai_client`` pinned to ``None``.

    This workstation usually has an OAuth token on disk, so without this
    override the Q&A judge would make a REAL capability probe / Claude call
    (flaky, non-deterministic). Returning ``None`` forces
    ``_grade_via_claude`` to raise immediately so the deterministic offline
    grader decides — mirroring how the judge-branch tests inject their stub.
    """
    app.dependency_overrides[get_sync_ai_client] = lambda: None
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_sync_ai_client, None)


# ---------------------------------------------------------------------------
# Helpers — propose / approve / advance / inject.
# ---------------------------------------------------------------------------


def _propose_boredom(
    client: TestClient, parent_headers: dict[str, str], *, seed: int = 42
) -> dict[str, Any]:
    """Propose a ``boredom`` activity (picks the staged ``qa_gate`` template)."""
    resp = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": seed},
        headers=parent_headers,
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _propose(
    client: TestClient, parent_headers: dict[str, str], *, seed: int = 42
) -> dict[str, Any]:
    resp = client.post(
        "/api/activities/propose",
        json={"intent": "request_play", "slot": "unicorns", "hour": 12, "seed": seed},
        headers=parent_headers,
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _approve(
    client: TestClient, parent_headers: dict[str, str], activity_id: str, version: int
) -> dict[str, Any]:
    resp = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert resp.status_code == 200, resp.text
    return cast("dict[str, Any]", resp.json())


def _advance(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
    *,
    expected_status: int = 200,
) -> dict[str, Any]:
    resp = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert resp.status_code == expected_status, resp.text
    return cast("dict[str, Any]", resp.json())


def _stage_running(
    client: TestClient, parent_headers: dict[str, str], *, seed: int = 42
) -> dict[str, Any]:
    body = _propose(client, parent_headers, seed=seed)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    assert state["state"] == "running"
    return state


def _inject_question(
    db_path: Path, activity_id: str, question: str, expected_answer: str | None
) -> None:
    """Set ``question`` (+ optional ``expected_answer``) on the current step.

    Used ONLY by grade-path tests (tolerance / matching / judge) where the
    wiring is irrelevant — the wiring is proven end-to-end by
    ``test_template_authored_qa_step_auto_resolves_end_to_end``.
    """
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "UPDATE activity_steps SET question = ?, expected_answer = ? "
                "WHERE activity_id = ? AND current = 1",
                (question, expected_answer, activity_id),
            )
    finally:
        conn.close()


def _insert_transcript(db_path: Path, text: str) -> None:
    """Insert a transcript row whose ``ended_at`` is 'now' (within window).

    ``ended_at`` is formatted byte-identically to the pipeline via
    ``_format_ended_at_cutoff`` so the grader's ``ended_at >= cutoff``
    lexicographic comparison matches.
    """
    now_iso = _format_ended_at_cutoff(datetime.now(UTC))
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at, mode) VALUES (?, ?, ?)",
                ("sess-qa", now_iso, 3),
            )
            conn.execute(
                "INSERT INTO transcripts (id, session_id, started_at, ended_at, text) "
                "VALUES (?, ?, ?, ?, ?)",
                ("tx-qa", "sess-qa", now_iso, now_iso, text),
            )
    finally:
        conn.close()


def _set_qa_grading(db_path: Path, value: str) -> None:
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('qa_grading', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (value,),
            )
    finally:
        conn.close()


def _question_approved(db_path: Path, activity_id: str) -> int | None:
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT question_approved FROM activity_steps WHERE activity_id = ? AND current = 1",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return None if row["question_approved"] is None else int(row["question_approved"])


# ---------------------------------------------------------------------------
# PRIMARY — full producer→consumer round trip through a TEMPLATE-AUTHORED
# Q&A step (code-quality.md §4: integration test through the production
# caller). No direct-DB injection of question / expected_answer.
# ---------------------------------------------------------------------------


def test_template_authored_qa_step_auto_resolves_end_to_end(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    qa_gate_dir: Path,
    pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Q&A step authored in a TEMPLATE flows through propose → advance and
    auto-resolves WITHOUT a parent tap.

    This is the wiring regression test: ``question`` + ``expected_answer``
    are authored ONLY in ``qa_gate.json`` and must reach the
    ``activity_steps`` row via the real propose path. Asserts:

    * the persisted row carries both fields (the wiring),
    * advance auto-resolves the gate (question_approved=1) + bumps version
      twice + proceeds (the consumer),
    * the ``activity.state`` WS envelope's current step has
      ``question_pending=False`` (LOW-6 — kiosk un-hides Next).
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", qa_gate_dir)
    generator.clear_template_cache()

    _set_qa_grading(db_path, "lenient")

    body = _propose_boredom(offline_client, parent_headers, seed=11)
    activity_id = body["id"]

    # The wiring: the persisted first step row MUST carry the authored
    # question + expected_answer (NOT injected by the test).
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT question, expected_answer FROM activity_steps "
            "WHERE activity_id = ? AND seq = 1",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["question"] == "What colour is the sky on a clear day?", (
        "template-authored question did not reach the activity_steps row — "
        "the propose-time INSERT dropped it (iteration-1 wiring gap)"
    )
    assert row["expected_answer"] == "blue", (
        "template-authored expected_answer did not reach the activity_steps row"
    )

    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])
    assert state["state"] == "running"

    # The kid answers out loud; the transcript lands in the 30s window.
    _insert_transcript(db_path, "I think the sky is blue")

    # Subscribe BEFORE the auto-resolving advance so we capture the
    # gate-resolve WS envelope (question_pending=False).
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        after = _advance(offline_client, parent_headers, activity_id, state["version"])
        assert after["state"] in {"running", "completed"}
        # Version bumped at least twice: gate resolve + advance transition.
        assert after["version"] >= state["version"] + 2

        # LOW-6: the auto-grade path emitted an activity.state envelope whose
        # current step has question_pending=False so the kiosk un-hides Next.
        saw_unhidden = False
        while True:
            try:
                envelope = sub.get_nowait()
            except asyncio.QueueEmpty:
                break
            if envelope.topic is not Topic.activity_state:
                continue
            if envelope.payload.get("id") != activity_id:
                continue
            current_steps = [s for s in envelope.payload.get("steps", []) if s.get("current")]
            for s in current_steps:
                if s.get("question_pending") is False:
                    saw_unhidden = True
        assert saw_unhidden, (
            "auto-grade did not emit an activity.state envelope with a current "
            "step having question_pending=False"
        )
    finally:
        sub.close()

    # question_approved persisted as 1 (approved) on the gated step.
    conn = connect(db_path, check_same_thread=False)
    try:
        approved_row = conn.execute(
            "SELECT question_approved FROM activity_steps "
            "WHERE activity_id = ? AND question = 'What colour is the sky on a clear day?'",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert approved_row is not None
    assert int(approved_row["question_approved"]) == 1

    generator.clear_template_cache()


# ---------------------------------------------------------------------------
# Offline grader path (AI client pinned to None) — lenient + matching.
# ---------------------------------------------------------------------------


def test_advance_auto_resolves_with_lenient_and_matching_transcript(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """qa_grading=lenient + matching transcript → advance auto-resolves the
    gate WITHOUT a parent approve-question call (offline grader).
    """
    state = _stage_running(offline_client, parent_headers, seed=42)
    activity_id = state["id"]
    _set_qa_grading(db_path, "lenient")
    _inject_question(db_path, activity_id, "What colour?", "blue")
    _insert_transcript(db_path, "I think it is blue")

    # No approve-question call — advance directly. The offline grader
    # matches "blue" and resolves the gate, so advance proceeds (200).
    after = _advance(offline_client, parent_headers, activity_id, state["version"])
    assert after["state"] in {"running", "completed"}
    # Version bumped at least twice: once by the gate resolve, once by the
    # advance transition.
    assert after["version"] >= state["version"] + 2
    # question_approved persisted as 1 (approved) on the (now previous)
    # gated step. Look it up directly: the gated step may no longer be
    # current after advance, so query by approved flag.
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT question_approved FROM activity_steps "
            "WHERE activity_id = ? AND question = 'What colour?'",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert int(row["question_approved"]) == 1


def test_advance_stays_pending_when_transcript_absent(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """qa_grading=lenient but no transcript row → no confident match → 409."""
    state = _stage_running(offline_client, parent_headers, seed=43)
    activity_id = state["id"]
    _set_qa_grading(db_path, "lenient")
    _inject_question(db_path, activity_id, "What colour?", "blue")
    # No transcript inserted.

    body = _advance(
        offline_client, parent_headers, activity_id, state["version"], expected_status=409
    )
    assert body["detail"]["code"] == "question_pending"
    assert _question_approved(db_path, activity_id) is None


def test_advance_stays_pending_when_transcript_does_not_match(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """qa_grading=strict + non-matching transcript → gate stays pending (409)."""
    state = _stage_running(offline_client, parent_headers, seed=44)
    activity_id = state["id"]
    _set_qa_grading(db_path, "strict")
    _inject_question(db_path, activity_id, "What colour?", "blue")
    _insert_transcript(db_path, "purple and green")

    body = _advance(
        offline_client, parent_headers, activity_id, state["version"], expected_status=409
    )
    assert body["detail"]["code"] == "question_pending"
    assert _question_approved(db_path, activity_id) is None


# ---------------------------------------------------------------------------
# qa_grading="off" → R3 parent-tap path UNCHANGED.
# ---------------------------------------------------------------------------


def test_grading_off_keeps_r3_parent_tap_path(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """With grading off, advance still 409s even with a matching transcript;
    approve-question still resolves the gate (byte-identical R3 behavior).
    """
    state = _stage_running(offline_client, parent_headers, seed=45)
    activity_id = state["id"]
    _set_qa_grading(db_path, "off")
    _inject_question(db_path, activity_id, "What colour?", "blue")
    _insert_transcript(db_path, "definitely blue")

    # Advance must STILL block — grading off short-circuits auto-grade.
    body = _advance(
        offline_client, parent_headers, activity_id, state["version"], expected_status=409
    )
    assert body["detail"]["code"] == "question_pending"
    assert _question_approved(db_path, activity_id) is None

    # The R3 approve-question path still works.
    resp = offline_client.post(
        f"/api/activities/{activity_id}/approve-question",
        json={"result": "approved", "version": state["version"]},
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    new_version = resp.json()["version"]
    assert new_version == state["version"] + 1
    assert _question_approved(db_path, activity_id) == 1

    # And advance now proceeds.
    after = _advance(offline_client, parent_headers, activity_id, new_version)
    assert after["state"] in {"running", "completed"}


def test_grading_off_without_expected_answer_unchanged(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """A question with NO expected_answer never auto-grades, even when the
    dial is on — falls through to the R3 409.
    """
    state = _stage_running(offline_client, parent_headers, seed=46)
    activity_id = state["id"]
    _set_qa_grading(db_path, "lenient")
    _inject_question(db_path, activity_id, "What colour?", None)
    _insert_transcript(db_path, "blue blue blue")

    body = _advance(
        offline_client, parent_headers, activity_id, state["version"], expected_status=409
    )
    assert body["detail"]["code"] == "question_pending"


# ---------------------------------------------------------------------------
# Claude judge path — gate green (judge) vs gate not green (offline fallback).
# ---------------------------------------------------------------------------


class _StubSyncClient:
    """Minimal SyncAIClient stub returning a canned verdict."""

    def __init__(self, verdict: str) -> None:
        self._verdict = verdict
        self.calls = 0

    def complete_text_sync(self, messages: Any, **kwargs: Any) -> Any:
        self.calls += 1
        from toybox.ai.client import AIResponse

        return AIResponse(text=self._verdict, model="stub")


@pytest.fixture
def client_with_stub_ai(
    app: FastAPI,
    request: pytest.FixtureRequest,
) -> Iterator[tuple[TestClient, _StubSyncClient]]:
    """TestClient with ``get_sync_ai_client`` overridden to a stub.

    The stub's verdict is taken from the indirect param.
    """
    verdict = getattr(request, "param", "CORRECT")
    stub = _StubSyncClient(verdict)
    app.dependency_overrides[get_sync_ai_client] = lambda: stub
    try:
        with TestClient(app) as test_client:
            yield test_client, stub
    finally:
        app.dependency_overrides.pop(get_sync_ai_client, None)


async def _capable_true(*_args: Any, **_kwargs: Any) -> tuple[bool, None]:
    return True, None


async def _capable_false(*_args: Any, **_kwargs: Any) -> tuple[bool, Any]:
    from toybox.ai.capability import CapabilityReason

    return False, CapabilityReason.network_offline


def test_claude_judge_green_correct_auto_resolves(
    client_with_stub_ai: tuple[TestClient, _StubSyncClient],
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate green + judge "CORRECT" → auto-resolve via Claude (not offline).

    The offline grader would FAIL on this transcript ("totally unrelated"),
    so a pass proves the Claude verdict drove the resolution.
    """
    client, stub = client_with_stub_ai
    monkeypatch.setattr("toybox.ai.capability.is_capable", _capable_true)

    state = _stage_running(client, parent_headers, seed=47)
    activity_id = state["id"]
    # Reset: _stage_running's approve runs the S2 step-animator, which also
    # uses the sync client. We only want to count the W3 judge call below.
    stub.calls = 0
    _set_qa_grading(db_path, "strict")
    _inject_question(db_path, activity_id, "What colour?", "blue")
    _insert_transcript(db_path, "totally unrelated words")

    after = _advance(client, parent_headers, activity_id, state["version"])
    assert after["state"] in {"running", "completed"}
    assert stub.calls == 1, "Claude judge should have been called once"
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT question_approved FROM activity_steps "
            "WHERE activity_id = ? AND question = 'What colour?'",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert int(row["question_approved"]) == 1


def test_claude_judge_gate_not_green_falls_back_to_offline(
    client_with_stub_ai: tuple[TestClient, _StubSyncClient],
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate NOT green → never calls the judge → offline grader decides.

    Offline grader sees a matching transcript ("blue"), so the gate still
    auto-resolves — but via the offline path, and the stub judge is never
    called.
    """
    client, stub = client_with_stub_ai
    monkeypatch.setattr("toybox.ai.capability.is_capable", _capable_false)

    state = _stage_running(client, parent_headers, seed=48)
    activity_id = state["id"]
    # Reset after _stage_running's S2 step-animator call (shares the stub).
    stub.calls = 0
    _set_qa_grading(db_path, "strict")
    _inject_question(db_path, activity_id, "What colour?", "blue")
    _insert_transcript(db_path, "it is blue")

    after = _advance(client, parent_headers, activity_id, state["version"])
    assert after["state"] in {"running", "completed"}
    assert stub.calls == 0, "judge must NOT be called when the gate is not green"
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT question_approved FROM activity_steps "
            "WHERE activity_id = ? AND question = 'What colour?'",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert int(row["question_approved"]) == 1
