"""Phase G G3 — branching gameplay end-to-end (path A vs path B).

Walks a single branching template (``valid_branching.json``) through
two distinct activities, picking a different choice in each one,
and asserts:

* ``activity_steps`` rows match the chosen path in ``seq`` order.
* ``chosen_label`` is populated on rows where the kid had a choice
  AND matches the rendered label they saw (read back from the
  previous row's persisted ``choices_json``).
* The anti-signal signature is identical across the two paths
  (path-agnostic by design — Phase G does NOT change the signature
  computation; same template + same slot fills → same signature
  regardless of which branch the kid took).
* The WS ``activity.state`` envelope for the choice-bearing step
  includes ``choices: [{label, choice_index}]`` with rendered
  labels (no unresolved ``{slot}`` placeholders).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.ws.topics import Topic


@pytest.fixture
def valid_branching_dir(tmp_path: Path) -> Path:
    """Stage the ``valid_branching.json`` fixture as the only
    ``boredom`` template so the seeded picker MUST land on it.

    The template has 5 nodes with one choice point at step 0:

    * open (choices: sneak | announce)
    * sneak (next: snack_ending)
    * announce (next: victory_ending)
    * snack_ending (reached via ``sneak.next`` — falls through to
      victory_ending under Rule 3 because it's NOT a ``choices[*].next``
      target. Branch-destination-leaf termination only applies to steps
      reached via a ``choices[*].next`` edge, not via ``step.next``.)
    * victory_ending (last array entry — true terminal)

    Path A: open → sneak → snack_ending → (fall-through) → victory_ending → completed
    Path B: open → announce → victory_ending → completed
    """
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "activities"
        / "branching"
        / "valid_branching.json"
    )
    payload = fixture.read_text(encoding="utf-8")
    staged = tmp_path / "templates"
    staged.mkdir()
    (staged / "boredom.json").write_text(payload, encoding="utf-8")
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
    assert resp.status_code == 200, resp.text
    return cast("dict[str, Any]", resp.json())


def _propose(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    seed: int,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "intent": "boredom",
        "slot": None,
        "hour": 12,
        "seed": seed,
    }
    if context is not None:
        payload["context"] = context
    resp = client.post(
        "/api/activities/propose",
        json=payload,
        headers=parent_headers,
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _activity_signature(db_path: Path, activity_id: str) -> str:
    """Read the persisted anti-signal signature off the activity's
    summary envelope so the test can compare across paths.
    """
    import json as _json

    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT summary FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    payload = _json.loads(row["summary"])
    sig = payload["metadata"]["signature"]
    return cast("str", sig)


def _walk_path_a(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    start_version: int,
) -> dict[str, Any]:
    """open (choice 0 = sneak) → sneak (next → snack_ending) →
    snack_ending (fall-through to victory_ending) →
    victory_ending → completed.
    """
    version = start_version
    state = _advance(client, parent_headers, activity_id, version)  # → running, seq=1
    state = _advance(client, parent_headers, activity_id, state["version"], choice_index=0)
    state = _advance(client, parent_headers, activity_id, state["version"])
    state = _advance(client, parent_headers, activity_id, state["version"])
    state = _advance(client, parent_headers, activity_id, state["version"])
    return state


def _walk_path_b(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    start_version: int,
) -> dict[str, Any]:
    """open (choice 1 = announce) → announce (next → victory_ending) →
    victory_ending → completed.
    """
    version = start_version
    state = _advance(client, parent_headers, activity_id, version)  # → running, seq=1
    state = _advance(client, parent_headers, activity_id, state["version"], choice_index=1)
    state = _advance(client, parent_headers, activity_id, state["version"])
    state = _advance(client, parent_headers, activity_id, state["version"])
    return state


def test_branching_path_a_persists_chosen_path(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path A: open → sneak → snack_ending → victory_ending →
    completed. Persisted ``activity_steps`` rows trace this path
    in ``seq`` order; ``chosen_label`` on seq=1 matches the
    "Sneak closer quietly" button the kid saw.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", valid_branching_dir)
    generator.clear_template_cache()

    body = _propose(client, parent_headers, seed=11)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _walk_path_a(client, parent_headers, body["id"], state["version"])
    assert state["state"] == "completed"

    conn = connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT seq, step_template_id, chosen_label FROM activity_steps "
            "WHERE activity_id = ? ORDER BY seq",
            (body["id"],),
        ).fetchall()
    finally:
        conn.close()

    template_path = [str(r["step_template_id"]) for r in rows]
    assert template_path == ["open", "sneak", "snack_ending", "victory_ending"]
    # The choice was made at seq=1 ("open"); chosen_label populated.
    assert str(rows[0]["chosen_label"]) == "Sneak closer quietly"
    # Other rows have NULL chosen_label (linear advances, no kid choice).
    assert rows[1]["chosen_label"] is None
    assert rows[2]["chosen_label"] is None
    assert rows[3]["chosen_label"] is None
    generator.clear_template_cache()


def test_branching_path_b_persists_different_chosen_path(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path B: open → announce → victory_ending → completed. A
    different activity (different seed) takes a different branch
    and the persisted path differs from path A — proving the lazy
    advance handler genuinely walks template edges, not a fixed
    list.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", valid_branching_dir)
    generator.clear_template_cache()

    body = _propose(client, parent_headers, seed=22)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _walk_path_b(client, parent_headers, body["id"], state["version"])
    assert state["state"] == "completed"

    conn = connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT seq, step_template_id, chosen_label FROM activity_steps "
            "WHERE activity_id = ? ORDER BY seq",
            (body["id"],),
        ).fetchall()
    finally:
        conn.close()

    template_path = [str(r["step_template_id"]) for r in rows]
    assert template_path == ["open", "announce", "victory_ending"]
    assert str(rows[0]["chosen_label"]) == "Announce yourself bravely"
    generator.clear_template_cache()


def test_branching_signature_path_agnostic(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The anti-signal signature is computed from template_id + slot
    fills, NOT from the path the kid took. Two activities of the
    SAME template with the SAME slot fills must hash to the SAME
    signature even if they take different branches. Phase G plan:
    "anti-signal signature stays template-level, not path-level."
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", valid_branching_dir)
    generator.clear_template_cache()

    # Same seed → same template + same slot fills → same signature
    # by construction. Different ``context`` keys → different
    # activity UUIDs (the deterministic UUID derivation hashes
    # context too) so we can persist both rows side-by-side.
    body_a = _propose(client, parent_headers, seed=99, context={"path": "a"})
    state_a = _approve(client, parent_headers, body_a["id"], body_a["version"])
    _walk_path_a(client, parent_headers, body_a["id"], state_a["version"])

    body_b = _propose(client, parent_headers, seed=99, context={"path": "b"})
    state_b = _approve(client, parent_headers, body_b["id"], body_b["version"])
    _walk_path_b(client, parent_headers, body_b["id"], state_b["version"])

    sig_a = _activity_signature(db_path, body_a["id"])
    sig_b = _activity_signature(db_path, body_b["id"])
    assert sig_a == sig_b, (
        f"path-agnostic invariant broken: path A signature {sig_a!r} != "
        f"path B signature {sig_b!r}"
    )
    generator.clear_template_cache()


@pytest.fixture
def branch_leaf_terminal_dir(tmp_path: Path) -> Path:
    """Stage the ``branch_leaf_terminal.json`` fixture, which mirrors
    the ``request_play_soak_superhero_12`` shape: a choice step whose
    targets (``cat_end``, ``baby_end``) are leaves with no ``next``,
    and where ``cat_end`` is NOT the last array entry. Without the
    Rule 2.5 fix, advancing past ``cat_end`` would fall through to
    ``baby_end`` (the bug the user reported as "save the cat AND see
    the baby ending"). With the fix, ``cat_end`` terminates the
    activity right after the kid sees it.
    """
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "activities"
        / "branching"
        / "branch_leaf_terminal.json"
    )
    payload = fixture.read_text(encoding="utf-8")
    staged = tmp_path / "templates"
    staged.mkdir()
    (staged / "boredom.json").write_text(payload, encoding="utf-8")
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


def test_branch_destination_leaf_terminates_no_fall_through(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    branch_leaf_terminal_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the "save the cat → see the baby ending too" bug.

    Picking choice 0 (``cat_end``) at the fork must terminate the
    activity after ``cat_end`` is shown. The persisted rows must be
    exactly ``["fork", "cat_end"]`` — NOT ``["fork", "cat_end",
    "baby_end"]``. The activity state must be ``completed`` after the
    advance past ``cat_end``.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", branch_leaf_terminal_dir)
    generator.clear_template_cache()

    body = _propose(client, parent_headers, seed=11)
    state = _approve(client, parent_headers, body["id"], body["version"])
    # approved → running (renders fork at seq=1)
    state = _advance(client, parent_headers, body["id"], state["version"])
    # Pick "the cat" → inserts cat_end at seq=2
    state = _advance(client, parent_headers, body["id"], state["version"], choice_index=0)
    assert state["state"] == "running"
    # Advance past cat_end → must terminate, NOT insert baby_end.
    state = _advance(client, parent_headers, body["id"], state["version"])
    assert state["state"] == "completed"

    conn = connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT seq, step_template_id FROM activity_steps "
            "WHERE activity_id = ? ORDER BY seq",
            (body["id"],),
        ).fetchall()
    finally:
        conn.close()

    template_path = [str(r["step_template_id"]) for r in rows]
    assert template_path == ["fork", "cat_end"], (
        f"branch destination leaf must terminate, got {template_path!r}; "
        "fall-through into the sibling branch's ending is the bug."
    )
    generator.clear_template_cache()


def test_ws_activity_state_envelope_includes_rendered_choices(
    client: TestClient,
    parent_headers: dict[str, str],
    pubsub: PubSub,
    valid_branching_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The WS ``activity.state`` envelope for the choice-bearing
    step includes ``choices: [{label, choice_index}]`` with rendered
    labels (no unresolved ``{slot}`` placeholders). Subscribes
    BEFORE propose so the subscriber catches every emit since the
    subscription started.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", valid_branching_dir)
    generator.clear_template_cache()

    sub = pubsub.subscribe([Topic.activity_state])
    try:
        body = _propose(client, parent_headers, seed=11)
        state = _approve(client, parent_headers, body["id"], body["version"])
        state = _advance(client, parent_headers, body["id"], state["version"])
        # Drain envelopes; find the one for THIS activity AFTER the
        # propose envelope. The "running" state envelope is what we
        # want — it carries the seq=1 row with its choices.
        envelopes: list[Any] = []
        while True:
            try:
                envelopes.append(sub.get_nowait())
            except Exception:  # pubsub raises queue.Empty equivalent  # noqa: BLE001
                break

        # Find the envelope where the activity is "running" and seq=1
        # is current — that's the one the kiosk uses to render the
        # choice buttons.
        running_envelopes = [
            e
            for e in envelopes
            if e.payload.get("id") == body["id"] and e.payload.get("state") == "running"
        ]
        assert running_envelopes, "expected at least one running envelope for activity"
        env = running_envelopes[-1]
        steps = env.payload["steps"]
        current_step = next(s for s in steps if s["current"])
        assert current_step["seq"] == 1
        choices = current_step["choices"]
        assert isinstance(choices, list)
        assert len(choices) == 2
        for idx, choice in enumerate(choices):
            assert choice["choice_index"] == idx
            assert isinstance(choice["label"], str)
            assert "{" not in choice["label"], (
                f"unrendered slot in WS envelope choice label: {choice['label']!r}"
            )
    finally:
        sub.close()
    generator.clear_template_cache()
