"""Phase G G3 — ``POST /api/activities/{id}/advance`` with ``choice_index``.

Covers:

* All four edge-resolution branches: linear fall-through, explicit
  ``next``, choices-resolves-to-target, and terminal completion.
* All three 400 error codes: ``choice_required``,
  ``choice_not_allowed``, ``invalid_choice_index``.
* Idempotency: a stale ``If-Match-Version`` retry after a successful
  advance returns 409 AND does NOT double-INSERT.
* Pre-G2 regression: an activity with 5 pre-seeded rows (legacy
  shape, empty ``slot_fills_json``) still walks through linear
  fall-through using the existing handler path.

Each test stages a templates directory with a single fixture template
and monkeypatches ``generator.TEMPLATES_DIR`` so the seeded picker
lands deterministically on it. Mirrors the G2 fixture pattern in
``test_g2_lazy_insertion.py``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.db.connection import connect

# ---------------------------------------------------------------------------
# Fixture helpers — stage a per-test templates dir + propose an activity.
# ---------------------------------------------------------------------------


def _stage_templates_dir(tmp_path: Path, fixture_name: str, intent: str) -> Path:
    """Copy ``tests/fixtures/activities/branching/<fixture_name>`` into a
    fresh ``templates/`` dir and copy the schema alongside.

    Returns the staged dir path so the caller can monkeypatch
    ``generator.TEMPLATES_DIR`` to it. Mirrors
    ``test_g2_lazy_insertion.branching_templates_dir``.
    """
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "activities"
        / "branching"
        / fixture_name
    )
    payload = fixture.read_text(encoding="utf-8")
    staged = tmp_path / "templates"
    staged.mkdir(exist_ok=True)
    (staged / f"{intent}.json").write_text(payload, encoding="utf-8")
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
def valid_branching_dir(tmp_path: Path) -> Path:
    """5-step branching template: open → (sneak | announce) →
    (snack_ending | victory_ending). 1 choice point, 4 reachable
    nodes per path. Exercises rules 1 (choices), 2 (explicit next),
    and 4 (terminal).
    """
    return _stage_templates_dir(tmp_path, "valid_branching.json", "boredom")


@pytest.fixture
def mixed_linear_branching_dir(tmp_path: Path) -> Path:
    """7-step template: 4 linear steps → choice point → 2 endings.
    Exercises rule 3 (linear fall-through) AND rule 1 (choices).
    """
    return _stage_templates_dir(tmp_path, "mixed_linear_branching.json", "boredom")


@pytest.fixture
def slot_substituted_choices_dir(tmp_path: Path) -> Path:
    """3-step template: choice point at step 0 → 2 endings. Choice
    labels carry slot placeholders so a substitution-skipping bug
    surfaces in ``chosen_label``.
    """
    return _stage_templates_dir(tmp_path, "slot_substituted_choices.json", "boredom")


def _propose_branching(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    seed: int = 11,
) -> dict[str, Any]:
    """Propose an activity. Hour pinned to 12 so the eligibility
    filter doesn't drop the fixture. Caller sets the templates dir
    via monkeypatch BEFORE calling this.
    """
    resp = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": seed},
        headers=parent_headers,
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _approve(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
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
    choice_index: int | None = None,
    expected_status: int = 200,
) -> dict[str, Any]:
    headers = {**parent_headers, "If-Match-Version": str(version)}
    payload: dict[str, Any] | None = None
    if choice_index is not None:
        payload = {"choice_index": choice_index}
    resp = client.post(
        f"/api/activities/{activity_id}/advance",
        json=payload,
        headers=headers,
    )
    assert resp.status_code == expected_status, resp.text
    return cast("dict[str, Any]", resp.json())


def _set_branching_templates_dir(
    monkeypatch: pytest.MonkeyPatch, templates_dir: Path
) -> None:
    """Monkeypatch the generator module to use ``templates_dir`` and
    clear caches. Yields, expects caller to clean up via fixture
    teardown (the cache helper above calls clear before AND after).
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", templates_dir)
    generator.clear_template_cache()


# ---------------------------------------------------------------------------
# Edge resolution: rule 1 (choices → resolve to chosen target).
# ---------------------------------------------------------------------------


def test_advance_with_choice_inserts_target_step(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 1: current step has ``choices`` + valid ``choice_index``
    → next step lazily-INSERTed; previous row's ``chosen_label``
    populated with the rendered label the kid saw.
    """
    _set_branching_templates_dir(monkeypatch, valid_branching_dir)

    body = _propose_branching(client, parent_headers)
    activity_id = body["id"]
    state = _approve(client, parent_headers, activity_id, body["version"])

    # First /advance flips proposed → running on the existing seq=1
    # row (no INSERT — that row is already there from G2 propose).
    state = _advance(client, parent_headers, activity_id, state["version"])
    assert state["state"] == "running"
    # Step 1 is the choice-bearing "open" step.
    current = next(s for s in state["steps"] if s["current"])
    assert current["seq"] == 1
    assert current["choices"] is not None
    assert len(current["choices"]) == 2

    # Pick choice 0 ("Sneak closer quietly" → "sneak").
    state = _advance(client, parent_headers, activity_id, state["version"], choice_index=0)
    # New row inserted at seq=2 with current=1; previous row's
    # chosen_label populated with the rendered label kid saw.
    seqs = sorted(s["seq"] for s in state["steps"])
    assert seqs == [1, 2], f"expected exactly seqs 1 and 2, got {seqs}"
    prev = next(s for s in state["steps"] if s["seq"] == 1)
    new = next(s for s in state["steps"] if s["seq"] == 2)
    assert prev["current"] is False
    assert prev["chosen_label"] == "Sneak closer quietly"
    assert new["current"] is True

    # Verify the inserted row's body matches the "sneak" template
    # step rendered with the activity's slot fills (no `{` placeholder
    # left over).
    assert "{" not in new["body"], (
        f"unrendered slot in inserted body: {new['body']!r}"
    )

    # Spot-check the DB so we know choices_json + step_template_id
    # are persisted on the new row (not just present in the response).
    conn = connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT seq, chosen_label, step_template_id FROM activity_steps "
            "WHERE activity_id = ? ORDER BY seq",
            (activity_id,),
        ).fetchall()
    finally:
        conn.close()
    assert [int(r["seq"]) for r in rows] == [1, 2]
    assert str(rows[0]["chosen_label"]) == "Sneak closer quietly"
    assert str(rows[1]["step_template_id"]) == "sneak"


# ---------------------------------------------------------------------------
# Edge resolution: rule 2 (explicit `next`).
# ---------------------------------------------------------------------------


def test_advance_explicit_next_inserts_target_step(
    client: TestClient,
    parent_headers: dict[str, str],
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 2: after picking the choice, the kid lands on a step
    that has explicit ``next`` (e.g. ``sneak`` → ``snack_ending``).
    Posting /advance with no body resolves the explicit next target.
    """
    _set_branching_templates_dir(monkeypatch, valid_branching_dir)
    body = _propose_branching(client, parent_headers)
    state = _approve(client, parent_headers, body["id"], body["version"])
    # advance to running on step 1
    state = _advance(client, parent_headers, body["id"], state["version"])
    # pick "sneak" via choice 0
    state = _advance(client, parent_headers, body["id"], state["version"], choice_index=0)
    # /sneak/ has explicit next → snack_ending. Bare advance should
    # resolve it without a choice_index.
    state = _advance(client, parent_headers, body["id"], state["version"])
    seqs = sorted(s["seq"] for s in state["steps"])
    assert seqs == [1, 2, 3]
    new = next(s for s in state["steps"] if s["seq"] == 3)
    assert new["current"] is True
    # The "snack_ending" body mentions "tiny mouse".
    assert "tiny mouse" in new["body"].lower() or "mouse" in new["body"].lower()


# ---------------------------------------------------------------------------
# Edge resolution: rule 3 (linear fall-through).
# ---------------------------------------------------------------------------


def test_advance_linear_fallthrough_inserts_next_array_step(
    client: TestClient,
    parent_headers: dict[str, str],
    mixed_linear_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 3: a step with neither ``choices`` nor ``next`` falls
    through to the next array position. The mixed fixture has 4
    linear steps before the choice point.
    """
    _set_branching_templates_dir(monkeypatch, mixed_linear_branching_dir)
    body = _propose_branching(client, parent_headers)
    state = _approve(client, parent_headers, body["id"], body["version"])
    # approve → running on seq=1 (no body)
    state = _advance(client, parent_headers, body["id"], state["version"])
    # 3 more linear advances should land on the choice step at seq=5.
    for expected_seq in (2, 3, 4):
        state = _advance(client, parent_headers, body["id"], state["version"])
        current = next(s for s in state["steps"] if s["current"])
        assert current["seq"] == expected_seq

    # seq=5 is the "fork" step with choices.
    state = _advance(client, parent_headers, body["id"], state["version"])
    current = next(s for s in state["steps"] if s["current"])
    assert current["seq"] == 5
    assert current["choices"] is not None and len(current["choices"]) == 2


# ---------------------------------------------------------------------------
# Edge resolution: rule 4 (terminal completion).
# ---------------------------------------------------------------------------


def test_advance_past_terminal_completes(
    client: TestClient,
    parent_headers: dict[str, str],
    slot_substituted_choices_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 4: choosing through the choice point lands on a
    terminal step (last entry in the template array, no ``next``,
    no ``choices``); one more /advance flips the activity into
    ``completed`` without inserting a new row.

    The fixture's two endings are at template indices 1 and 2.
    Index 2 (``charge_ending``) is the LAST entry, so picking
    choice 1 lands on it and the next /advance is rule-4 terminal
    (no `next`, no `choices`, IS last in array). Picking choice 0
    would land on ``sneak_ending`` (index 1) which falls through
    via rule 3 to ``charge_ending`` — also fine, but doesn't
    exercise rule 4 cleanly.
    """
    _set_branching_templates_dir(monkeypatch, slot_substituted_choices_dir)
    body = _propose_branching(client, parent_headers)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    state = _advance(client, parent_headers, body["id"], state["version"], choice_index=1)
    # We're now on seq=2, the "charge_ending" — last entry in array,
    # so rule 4 terminal applies on the next /advance.
    rows_before = len(state["steps"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    assert state["state"] == "completed"
    # No new row inserted on the terminal advance.
    assert len(state["steps"]) == rows_before
    # No row is current after completion.
    assert all(s["current"] is False for s in state["steps"])


# ---------------------------------------------------------------------------
# 400 errors: choice_required / choice_not_allowed / invalid_choice_index.
# ---------------------------------------------------------------------------


def test_advance_choice_required_when_step_has_choices(
    client: TestClient,
    parent_headers: dict[str, str],
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``choice_required`` 400: current step has ``choices`` but the
    POST omitted ``choice_index``.
    """
    _set_branching_templates_dir(monkeypatch, valid_branching_dir)
    body = _propose_branching(client, parent_headers)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    # Now on seq=1 = "open" with choices. Bare advance must 400.
    resp = client.post(
        f"/api/activities/{body['id']}/advance",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "choice_required"


def test_advance_choice_not_allowed_when_no_choices(
    client: TestClient,
    parent_headers: dict[str, str],
    mixed_linear_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``choice_not_allowed`` 400: the request provided
    ``choice_index`` but the current step is linear (no choices).
    """
    _set_branching_templates_dir(monkeypatch, mixed_linear_branching_dir)
    body = _propose_branching(client, parent_headers)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    # seq=1 in mixed_linear is a linear (no-choices) step. Posting
    # choice_index=0 must be rejected.
    state = _advance(
        client,
        parent_headers,
        body["id"],
        state["version"],
        choice_index=0,
        expected_status=400,
    )
    # _advance returns the parsed response body even on non-200.
    assert state["detail"]["code"] == "choice_not_allowed"


def test_advance_invalid_choice_index(
    client: TestClient,
    parent_headers: dict[str, str],
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``invalid_choice_index`` 400: ``choice_index`` is out of range
    for the current step's choices length.
    """
    _set_branching_templates_dir(monkeypatch, valid_branching_dir)
    body = _propose_branching(client, parent_headers)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    # seq=1 has 2 choices; index 5 is out of range.
    state = _advance(
        client,
        parent_headers,
        body["id"],
        state["version"],
        choice_index=5,
        expected_status=400,
    )
    assert state["detail"]["code"] == "invalid_choice_index"
    assert state["detail"]["choice_count"] == 2


# ---------------------------------------------------------------------------
# Idempotency: stale If-Match-Version on advance retry → 409 + no INSERT.
# ---------------------------------------------------------------------------


def test_advance_stale_version_does_not_double_insert(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idempotency under retry: a successful /advance bumps version
    and INSERTs the next step; a retried POST with the OLD version
    must 409 with NO additional INSERT.
    """
    _set_branching_templates_dir(monkeypatch, valid_branching_dir)
    body = _propose_branching(client, parent_headers)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    stale_version = state["version"]
    # First advance with choice 0 succeeds.
    state = _advance(
        client, parent_headers, body["id"], state["version"], choice_index=0
    )
    assert state["state"] == "running"

    conn = connect(db_path, check_same_thread=False)
    try:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM activity_steps WHERE activity_id = ?",
            (body["id"],),
        ).fetchone()["n"]
    finally:
        conn.close()

    # Retry with the STALE version — the same body the client
    # would have queued before learning the version bumped.
    resp = client.post(
        f"/api/activities/{body['id']}/advance",
        json={"choice_index": 0},
        headers={**parent_headers, "If-Match-Version": str(stale_version)},
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "version_conflict"

    conn = connect(db_path, check_same_thread=False)
    try:
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM activity_steps WHERE activity_id = ?",
            (body["id"],),
        ).fetchone()["n"]
    finally:
        conn.close()
    assert after == before, (
        f"stale-version retry must not double-insert; before={before} after={after}"
    )


# ---------------------------------------------------------------------------
# Pre-G2 regression: 5-row legacy activity advances correctly.
# ---------------------------------------------------------------------------


def test_pre_g2_legacy_activity_still_advances_through_completion(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Phase G G3 must not break in-flight pre-G2 activities. The G2
    integration test already covers the same shape; mirrored here so
    G3 changes can't regress it without flagging the file under test.
    """
    from tests.fixtures.lazy_insert import backfill_legacy_steps

    body = client.post(
        "/api/activities/propose",
        json={"intent": "request_play", "slot": "unicorns", "hour": 12, "seed": 42},
        headers=parent_headers,
    )
    assert body.status_code == 201, body.text
    activity_id = body.json()["id"]

    conn = connect(db_path, check_same_thread=False)
    try:
        backfill_legacy_steps(conn, activity_id)
        with conn:
            conn.execute(
                "UPDATE activities SET slot_fills_json = '{}' WHERE id = ?",
                (activity_id,),
            )
    finally:
        conn.close()

    state = _approve(client, parent_headers, activity_id, 1)
    # 5 advances + 1 to complete.
    for _ in range(5):
        state = _advance(client, parent_headers, activity_id, state["version"])
    final = _advance(client, parent_headers, activity_id, state["version"])
    assert final["state"] == "completed"


# ---------------------------------------------------------------------------
# Wire payload pin: ``choices`` field shape on the response.
# ---------------------------------------------------------------------------


def test_advance_response_choices_shape(
    client: TestClient,
    parent_headers: dict[str, str],
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the response shape: ``step.choices`` is a list of
    ``{label: str, choice_index: int}`` objects with ``choice_index``
    set to the array enumeration index.
    """
    _set_branching_templates_dir(monkeypatch, valid_branching_dir)
    body = _propose_branching(client, parent_headers)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    current = next(s for s in state["steps"] if s["current"])
    assert isinstance(current["choices"], list)
    assert len(current["choices"]) == 2
    assert current["choices"][0]["choice_index"] == 0
    assert current["choices"][1]["choice_index"] == 1
    for c in current["choices"]:
        assert isinstance(c["label"], str)
        assert "{" not in c["label"]


def test_advance_response_chosen_label_after_choice(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    slot_substituted_choices_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``chosen_label`` on the previous step matches the rendered
    label the kid saw — including slot substitutions (proves the
    label was recorded from the persisted ``choices_json``, not
    re-rendered ad-hoc).
    """
    _set_branching_templates_dir(monkeypatch, slot_substituted_choices_dir)
    body = _propose_branching(client, parent_headers)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    current = next(s for s in state["steps"] if s["current"])
    assert current["choices"] is not None
    expected_label = current["choices"][0]["label"]

    state = _advance(client, parent_headers, body["id"], state["version"], choice_index=0)
    prev = next(s for s in state["steps"] if s["seq"] == 1)
    assert prev["chosen_label"] == expected_label
    # Sanity: label must contain the resolved {toy} or {room} value
    # (rather than the placeholder), so we know substitution happened.
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT slot_fills_json FROM activities WHERE id = ?",
            (body["id"],),
        ).fetchone()
    finally:
        conn.close()
    fills = json.loads(str(row["slot_fills_json"]))
    # The first choice in slot_substituted_choices.json is
    # "Sneak past {toy}" — the rendered label must contain the toy.
    assert fills["toy"] in expected_label


# ---------------------------------------------------------------------------
# Phase G G6 fix: step-back across a choice point must rewind cleanly so
# the next /advance doesn't trip the "next row exists" legacy path with
# 400 ``choice_not_allowed``. UAT-bug repro: kid picks a choice, operator
# hits step-back, kid taps a choice button → 400 wedges the activity.
# ---------------------------------------------------------------------------


def _step_back(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
) -> dict[str, Any]:
    resp = client.post(
        f"/api/activities/{activity_id}/step-back",
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert resp.status_code == 200, resp.text
    return cast("dict[str, Any]", resp.json())


def test_step_back_across_choice_then_advance_same_choice_works(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repro of the G6 UAT bug: kid picks a choice, operator hits
    step-back, kid taps the SAME choice again. After the fix, the
    activity rewinds cleanly and the second choice succeeds —
    activity_steps now has rows [1, 2] again with the same chosen
    target re-inserted.
    """
    _set_branching_templates_dir(monkeypatch, valid_branching_dir)

    body = _propose_branching(client, parent_headers)
    activity_id = body["id"]
    state = _approve(client, parent_headers, activity_id, body["version"])
    state = _advance(client, parent_headers, activity_id, state["version"])
    # Pick choice 0 — inserts seq=2 (the "sneak" target).
    state = _advance(client, parent_headers, activity_id, state["version"], choice_index=0)
    assert sorted(s["seq"] for s in state["steps"]) == [1, 2]

    # Step-back: rewinds across the choice point. seq=2 should be
    # deleted; seq=1 becomes current again with chosen_label cleared.
    state = _step_back(client, parent_headers, activity_id, state["version"])
    seqs_after_back = sorted(s["seq"] for s in state["steps"])
    assert seqs_after_back == [1], (
        f"step-back across a choice point must DELETE seq>{1}, got seqs={seqs_after_back}"
    )
    cur = next(s for s in state["steps"] if s["current"])
    assert cur["seq"] == 1
    assert cur["chosen_label"] is None, (
        "chosen_label must be cleared on rewind so the choice slate is fresh"
    )
    assert cur["choices"] is not None and len(cur["choices"]) == 2

    # Tap the SAME choice again — must succeed (was 400 before the fix).
    state = _advance(client, parent_headers, activity_id, state["version"], choice_index=0)
    assert sorted(s["seq"] for s in state["steps"]) == [1, 2]
    new = next(s for s in state["steps"] if s["seq"] == 2)
    assert new["current"] is True
    prev = next(s for s in state["steps"] if s["seq"] == 1)
    assert prev["chosen_label"] == "Sneak closer quietly"


def test_step_back_across_choice_then_advance_different_choice_works(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator-UX win: after step-back across a choice point, the kid
    can pick a DIFFERENT branch and the activity proceeds down the new
    path (not stuck on the previously-chosen target).
    """
    _set_branching_templates_dir(monkeypatch, valid_branching_dir)

    body = _propose_branching(client, parent_headers)
    activity_id = body["id"]
    state = _approve(client, parent_headers, activity_id, body["version"])
    state = _advance(client, parent_headers, activity_id, state["version"])
    # Pick choice 0 → "sneak"
    state = _advance(client, parent_headers, activity_id, state["version"], choice_index=0)
    state = _step_back(client, parent_headers, activity_id, state["version"])

    # Now pick choice 1 → "announce" (a DIFFERENT branch).
    state = _advance(client, parent_headers, activity_id, state["version"], choice_index=1)
    assert sorted(s["seq"] for s in state["steps"]) == [1, 2]
    new = next(s for s in state["steps"] if s["seq"] == 2)
    assert new["current"] is True
    prev = next(s for s in state["steps"] if s["seq"] == 1)
    assert prev["chosen_label"] == "Announce yourself bravely"

    # Spot-check the DB: the seq=2 row's step_template_id is the "announce"
    # target, NOT the "sneak" target the kid had picked the first time.
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT step_template_id FROM activity_steps "
            "WHERE activity_id = ? AND seq = 2",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert str(row["step_template_id"]) == "announce", (
        "step-back+different-choice must rewrite the chosen target, "
        f"got step_template_id={row['step_template_id']!r}"
    )
