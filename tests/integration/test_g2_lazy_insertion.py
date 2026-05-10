"""Phase G G2 — lazy step insertion + slot-fill persistence (DB side).

Pinned here:

* After ``POST /api/activities/propose`` from a 5-step linear
  template, ``activity_steps`` has exactly 1 row (down from 5),
  with ``current=1``, ``seq=1``, and the row's body matches
  ``Activity.steps[0].text``.
* The ``activities.slot_fills_json`` column is populated with
  the resolved slot map (parseable JSON object, keys present).
* For a branching template fixture where ``steps[0]`` has
  ``choices``, ``activity_steps.choices_json`` is populated with
  the rendered button labels (no ``{slot}`` placeholders
  remaining).
* ``activity_steps.step_template_id`` is populated when the
  template step has an ``id``, NULL otherwise.
* **Iter-2 privacy boundary**: ``slot_fills`` is persistence-only
  metadata. It MUST NOT appear in the WS ``activity.state``
  envelope (child kiosk subscribes to it) nor in the REST GET
  ``/api/activities/{id}`` response (no consumer renders raw
  slot fills — kiosk reads pre-rendered ``body`` strings, parent
  UI doesn't surface slot internals). Future slots like
  ``child_name`` would auto-leak without code change otherwise.
* **Regression** for in-flight pre-G2 activities: an activity
  whose 5 step rows + empty ``slot_fills_json`` were INSERTed at
  the old shape (simulated via direct DB inserts) still loads
  through the GET endpoint and advances correctly through the
  full sequence using the existing linear-fall-through
  ``post_advance`` handler. Phase G must not break activities
  that were running across the migration boundary.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.ws.topics import Topic

PROPOSE_BODY: dict[str, Any] = {
    "intent": "request_play",
    "slot": "unicorns",
    "hour": 12,
    "seed": 42,
}


def _propose(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json=PROPOSE_BODY,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


# ---------------------------------------------------------------------------
# G2 done-when (a): only steps[0] is INSERTed at activity creation.
# ---------------------------------------------------------------------------


def test_propose_inserts_only_steps_zero(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Phase G G2 done-when (a) + (d): the single inserted row is
    ``steps[0]`` with ``current=1``, ``seq=1``."""
    body = _propose(client, parent_headers)
    activity_id = body["id"]

    conn = connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT seq, body, current FROM activity_steps "
            "WHERE activity_id = ? ORDER BY seq ASC",
            (activity_id,),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, f"expected 1 row under lazy insertion, got {len(rows)}"
    row = rows[0]
    assert int(row["seq"]) == 1
    assert int(row["current"]) == 1
    # Body matches the propose response's first step (which was
    # rendered from steps[0]'s template text + slot fills).
    assert str(row["body"]) == body["steps"][0]["body"]


# ---------------------------------------------------------------------------
# G2 done-when (b): activities.slot_fills_json populated.
# ---------------------------------------------------------------------------


def test_propose_populates_slot_fills_json(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    branching_templates_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase G G2 done-when (b): the resolved slot map lands on
    ``activities.slot_fills_json``. Encoded with ``sort_keys=True``
    so byte-identity holds across reads (canonical convention).

    Iter-2: pins KEY PARITY — the persisted slot_fills_json must
    have the same keys + values that the generator emitted on
    ``Activity.metadata['slot_fills']``. A silent fill-corruption
    bug (e.g. persistence reads stale fills, or drops/renames a
    key) would slip past the iter-1 ``len > 0 + values are strings``
    check; this test catches it. Uses the branching fixture (which
    pins the slot vocabulary to ``{toy}``, ``{room}``,
    ``{action_verb}``, ``{adjective}``) so we can also pin the
    expected key set rather than just ``len > 0``.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", branching_templates_dir)
    generator.clear_template_cache()

    # Compute the expected slot map by calling generate() directly
    # with the same inputs the propose flow uses below — keys +
    # values MUST match what the persistence layer writes. Inputs
    # are pinned to the same hour/seed so the seeded picker lands
    # on the same template + same fills.
    expected_activity = generator.generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=11,
    )
    expected_fills = expected_activity.metadata["slot_fills"]
    assert isinstance(expected_fills, dict)
    assert len(expected_fills) > 0, "fixture must use at least one slot"

    body = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": 11},
        headers=parent_headers,
    )
    assert body.status_code == 201, body.text
    activity_id = body.json()["id"]

    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT slot_fills_json FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    raw = row["slot_fills_json"]
    assert raw is not None
    decoded = json.loads(raw)
    assert isinstance(decoded, dict)
    # Iter-2 key parity: persisted keys + values MUST match the
    # generator's ``Activity.metadata['slot_fills']`` exactly. This
    # is the load-bearing assertion that catches silent fill-
    # corruption bugs (drift between what the generator computed
    # and what the persistence layer wrote).
    assert decoded == expected_fills, (
        f"persisted slot_fills_json {decoded!r} does not match "
        f"generator-emitted slot_fills {expected_fills!r}"
    )
    for k, v in decoded.items():
        assert isinstance(k, str)
        assert isinstance(v, str)
    # Round-trip with sort_keys → byte-identical encoding contract.
    assert raw == json.dumps(decoded, sort_keys=True)
    generator.clear_template_cache()


# ---------------------------------------------------------------------------
# G2 done-when (e): branching template populates choices_json.
# ---------------------------------------------------------------------------


@pytest.fixture
def branching_templates_dir(tmp_path: Path) -> Path:
    """Stage a templates directory whose only content is a branching
    fixture template, so the seeded picker MUST land on it.

    The generator's ``TEMPLATES_DIR`` resolves at module load via a
    ``Final[Path]`` constant; we monkeypatch it per-test below
    rather than mutating the constant at module level (cache
    invalidation hooks off the dir path).

    Iter-2: switched from ``valid_branching.json`` to
    ``slot_substituted_choices.json``. The previous fixture's choice
    labels (``"Sneak closer quietly"`` / ``"Announce yourself
    bravely"``) had NO ``{slot}`` placeholders, so the
    ``"{" not in label`` assertion was trivially true and the
    load-bearing ``_substitute(label, slot_values)`` path was never
    actually exercised. The new fixture's labels are
    ``"Sneak past {toy}"`` and ``"Charge into {room}"`` — so we can
    assert the resolved values appear verbatim, catching a
    substitution-skipping bug.
    """
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "activities"
        / "branching"
        / "slot_substituted_choices.json"
    )
    payload = fixture.read_text(encoding="utf-8")
    staged = tmp_path / "templates"
    staged.mkdir()
    (staged / "boredom.json").write_text(payload, encoding="utf-8")
    # Also copy the schema so the loader's validator finds it.
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


def test_propose_with_branching_first_step_populates_choices_json(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    branching_templates_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase G G2 done-when (e): when ``steps[0]`` has ``choices``,
    the persistence layer renders each label using the slot fills
    and writes the JSON-encoded list to
    ``activity_steps.choices_json`` on the new row. Step ids
    round-trip through ``step_template_id``.

    Iter-2: now backed by the ``slot_substituted_choices`` fixture
    whose labels are ``"Sneak past {toy}"`` and
    ``"Charge into {room}"``. We compute the expected resolved
    labels by calling ``generate()`` with the same inputs and pin
    that the persisted ``choices_json`` matches verbatim — the
    iter-1 ``"{" not in label`` check trivially passed on a
    placeholder-free fixture and never actually exercised the
    ``_substitute(label, slot_values)`` path.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", branching_templates_dir)
    generator.clear_template_cache()

    # Pre-compute the expected rendered labels with the same inputs
    # so we can pin the substitution-output verbatim.
    expected_activity = generator.generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=11,
    )
    assert expected_activity.steps[0].choices_rendered is not None
    expected_labels = list(expected_activity.steps[0].choices_rendered)

    body = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": 11},
        headers=parent_headers,
    )
    assert body.status_code == 201, body.text
    activity_id = body.json()["id"]

    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT seq, body, choices_json, step_template_id FROM activity_steps "
            "WHERE activity_id = ? ORDER BY seq ASC",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert int(row["seq"]) == 1
    # First step in the fixture has id="open" → propagates to the row.
    assert str(row["step_template_id"]) == "open"
    raw = row["choices_json"]
    assert raw is not None, "choices_json must be populated when steps[0] has choices"
    decoded = json.loads(raw)
    assert isinstance(decoded, list)
    assert len(decoded) == 2
    # Iter-2 load-bearing: persisted labels MUST match the
    # generator's rendered ``choices_rendered`` byte-for-byte.
    assert decoded == expected_labels, (
        f"persisted choices_json {decoded!r} does not match "
        f"generator-rendered labels {expected_labels!r}"
    )
    # Iter-2: ALSO assert that the resolved ``{toy}`` / ``{room}``
    # values appear verbatim in the persisted labels — proves the
    # substitution path was actually exercised (not just a
    # placeholder-free fixture passing a vacuous "no ``{`` left"
    # check).
    fills = expected_activity.metadata["slot_fills"]
    toy_value = fills["toy"]
    room_value = fills["room"]
    persisted_text = " | ".join(decoded)
    assert toy_value in persisted_text, (
        f"resolved toy={toy_value!r} did not appear in persisted labels {decoded!r}"
    )
    assert room_value in persisted_text, (
        f"resolved room={room_value!r} did not appear in persisted labels {decoded!r}"
    )
    for label in decoded:
        assert isinstance(label, str)
        # All slot placeholders must be rendered before persistence.
        assert "{" not in label, f"unrendered slot in persisted label: {label!r}"
    generator.clear_template_cache()


# ---------------------------------------------------------------------------
# G2 iter-2: ``slot_fills`` is persistence-only — it must NOT leak to
# either the WS ``activity.state`` envelope (child kiosk-visible) or the
# REST GET response (parent-visible but no UI consumer). Mirror of the
# Step-23 PII-strip pattern in test_activity_polish.py.
# ---------------------------------------------------------------------------


def _payload_has_key_anywhere(node: object, key: str) -> bool:
    """Recursive walk -- catches the metadata-nested copy that a
    top-level membership check would miss. Mirrors the helper in
    test_activity_polish.py.
    """
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_payload_has_key_anywhere(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_payload_has_key_anywhere(item, key) for item in node)
    return False


def test_ws_activity_state_envelope_omits_slot_fills(
    client: TestClient,
    parent_headers: dict[str, str],
    pubsub: PubSub,
) -> None:
    """The ``activity.state`` envelope MUST NOT include
    ``slot_fills`` anywhere in its payload tree. Today's slots
    (``toy``, ``room``, ``adjective``, ``action_verb``) are
    low-risk, but ``room`` can carry parent-authored names
    (e.g. "Mom's office") and any future slot (``child_name``,
    etc.) would auto-leak to the kid kiosk without code change.
    The kiosk consumes pre-rendered ``body`` / ``choices_json``
    strings, never the raw fill map — so stripping is safe.
    """
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        body = _propose(client, parent_headers)
        envelope = sub.get_nowait()
        assert envelope.topic is Topic.activity_state
        assert envelope.payload["id"] == body["id"]
        assert not _payload_has_key_anywhere(envelope.payload, "slot_fills"), (
            "slot_fills must be stripped from the WS activity.state envelope "
            "(persistence-only telemetry)"
        )
    finally:
        sub.close()


def test_rest_get_response_omits_slot_fills(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """REST GET ``/api/activities/{id}`` (parent-only scope) also
    strips ``slot_fills`` from the response — no UI consumer reads
    the raw map (parent UI doesn't surface slot internals). Stripping
    at the ``_row_to_response`` chokepoint covers both REST + WS
    surfaces in one place; this test pins the REST half.
    """
    body = _propose(client, parent_headers)
    fetched = client.get(
        f"/api/activities/{body['id']}",
        headers=parent_headers,
    ).json()
    assert not _payload_has_key_anywhere(fetched, "slot_fills"), (
        "slot_fills must be stripped from the REST GET response "
        "(persistence-only telemetry)"
    )


# ---------------------------------------------------------------------------
# G2 done-when (regression): pre-G2 5-row activity still advances correctly.
# ---------------------------------------------------------------------------


def test_pre_g2_inflight_activity_still_advances(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Phase G G2 regression: an activity whose 5 step rows were
    INSERTed at the OLD pre-G2 shape (no ``choices_json``, no
    ``step_template_id``, ``slot_fills_json='{}'`` from the
    migration default) loads correctly and advances through the
    full sequence using the existing linear-fall-through
    ``post_advance`` handler. The migration explicitly preserves
    in-flight activities — they keep running unchanged.
    """
    # Use the propose flow to seed the activity row, then
    # backfill steps 2..5 directly. After backfill the row set
    # matches what an in-flight pre-G2 activity looked like.
    from tests.fixtures.lazy_insert import backfill_legacy_steps

    body = _propose(client, parent_headers)
    activity_id = body["id"]
    conn = connect(db_path, check_same_thread=False)
    try:
        backfill_legacy_steps(conn, activity_id)
        # Force the slot_fills_json to the empty default (the
        # migration's NOT NULL DEFAULT '{}'), simulating a row
        # whose pre-G2 INSERT did not write the column.
        with conn:
            conn.execute(
                "UPDATE activities SET slot_fills_json = '{}' WHERE id = ?",
                (activity_id,),
            )
        # Verify the fixture shape is what we expect.
        rows = conn.execute(
            "SELECT seq, current FROM activity_steps WHERE activity_id = ? ORDER BY seq",
            (activity_id,),
        ).fetchall()
    finally:
        conn.close()
    assert [int(r["seq"]) for r in rows] == [1, 2, 3, 4, 5]
    # Only seq=1 has current=1 (set by the lazy-insertion path);
    # backfilled rows are current=0.
    assert [int(r["current"]) for r in rows] == [1, 0, 0, 0, 0]

    # Drive the full lifecycle: approve + 5 advances + 1 final
    # advance to flip into completed.
    approve = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert approve.status_code == 200
    state = approve.json()
    for _ in range(5):
        adv = client.post(
            f"/api/activities/{activity_id}/advance",
            headers={**parent_headers, "If-Match-Version": str(state["version"])},
        )
        assert adv.status_code == 200, adv.text
        state = adv.json()
    final = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert final.status_code == 200
    assert final.json()["state"] == "completed", (
        "pre-G2 in-flight activity must advance through to completed using "
        "the existing linear handler — Phase G must not break running activities"
    )


# ---------------------------------------------------------------------------
# G2.5: propose response carries the full template plan (not just
# steps[0]). Restores the parent-dashboard review UX that G2's lazy
# insertion narrowed when the response was built from activity_steps
# rows. The DB contract (activity_steps has 1 row at propose) is
# unchanged; only the response shape is widened for proposed/approved.
# ---------------------------------------------------------------------------


def test_propose_response_carries_full_template_plan(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """G2.5: propose response shows ALL 5 template steps (rendered with
    slot fills) so the parent can preview the full activity before
    approving. activity_steps DB rows remain lazy-inserted (1 row at
    propose). On state transition to running/completed, the response
    narrows to the kid's actually-played path (activity_steps).
    """
    body = _propose(client, parent_headers)
    activity_id = body["id"]
    version = body["version"]

    # Response: full plan (5 steps for linear templates)
    assert body["state"] == "proposed"
    assert len(body["steps"]) == 5
    assert body["steps"][0]["seq"] == 1
    assert body["steps"][0]["current"] is True
    for s in body["steps"][1:]:
        assert s["current"] is False
        # All bodies must be fully-rendered (no leftover slot placeholders).
        assert "{" not in s["body"], f"unrendered slot in step body: {s['body']!r}"

    # DB: still only 1 row (lazy insert is untouched)
    conn = connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT seq FROM activity_steps WHERE activity_id = ? ORDER BY seq",
            (activity_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "G2 lazy-insert contract: only steps[0] in DB"

    # Approve preserves the full plan (still pre-running).
    approve = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert approve.status_code == 200
    approved = approve.json()
    assert approved["state"] == "approved"
    assert len(approved["steps"]) == 5
    version = approved["version"]

    # Start play: the first advance (approved -> running) flips to the
    # activity_steps view since we now reflect the kid's played path.
    advance = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert advance.status_code == 200
    running = advance.json()
    assert running["state"] == "running"
    assert len(running["steps"]) == 1, (
        "running activities should reflect activity_steps (kid's actual path), "
        "not the full template plan"
    )
