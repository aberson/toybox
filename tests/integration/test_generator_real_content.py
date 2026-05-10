"""Integration tests for step 19: real catalog content in the generator.

These tests exercise the full propose path through the FastAPI test
client: seed catalog tables (toys, rooms, children) → call
``POST /api/activities/propose`` → assert the generated activity AND
the labeled_events row reflect the seeded content.

Coverage:

* Seeded toys appear in the activity's step text (the generator picks
  one to substitute for the ``{toy}`` placeholder).
* Seeded toys + rooms appear in the labeled_events row's
  ``inputs_chatml_json`` (Phase E SFT export feed).
* Banned-themes filter wins ALL the templates → safe-default surfaces.
* Empty catalog (zero toys, rooms, children) → propose still succeeds
  with placeholder vocabulary.
* Multi-child banned themes union into the directive surface.
* Step 20 anti-signal still fires with real-catalog slot values: a
  pre-seeded ``didnt_work`` row vetoes the matching candidate even
  though slot values are now real toy/room names.
* Every propose continues to write a labeled_events row.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.activities.feedback import (
    KIND_DIDNT_WORK,
    compute_signature,
)
from toybox.db.connection import connect


def _propose(
    client: TestClient,
    headers: dict[str, str],
    *,
    intent: str = "request_play",
    slot: str | None = "unicorns",
    hour: int = 12,
    seed: int = 42,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "intent": intent,
        "slot": slot,
        "hour": hour,
        "seed": seed,
    }
    if context is not None:
        body["context"] = context
    response = client.post(
        "/api/activities/propose",
        json=body,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


def _read_labeled(db_path: Path, activity_id: str) -> sqlite3.Row | None:
    conn = connect(db_path)
    try:
        row: sqlite3.Row | None = conn.execute(
            "SELECT * FROM labeled_events WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()
        return row
    finally:
        conn.close()


def _seed_toys(
    db_path: Path,
    toys: list[tuple[str, str]],
    *,
    last_used_at: str | None = None,
) -> None:
    """Insert ``toys`` as ``(id, display_name)`` rows."""
    conn = connect(db_path)
    try:
        with conn:
            for tid, name in toys:
                conn.execute(
                    "INSERT INTO toys "
                    "(id, display_name, image_path, image_hash, type, tags, "
                    " persona_id, archived, created_at, last_used_at) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
                    " '2026-01-01T00:00:00Z', ?)",
                    (tid, name, f"img/{tid}.png", f"hash-{tid}", last_used_at),
                )
    finally:
        conn.close()


def _seed_rooms(db_path: Path, rooms: list[tuple[str, str]]) -> None:
    """Insert ``rooms`` as ``(id, display_name)`` rows."""
    conn = connect(db_path)
    try:
        with conn:
            for rid, name in rooms:
                conn.execute(
                    "INSERT INTO rooms (id, display_name, image_path, image_hash, notes) "
                    "VALUES (?, ?, NULL, NULL, NULL)",
                    (rid, name),
                )
    finally:
        conn.close()


def _seed_child(
    db_path: Path,
    *,
    child_id: str,
    display_name: str = "Test Child",
    banned_themes: str | None = None,
    reading_level: str | None = None,
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            # Wipe any existing rows for the same id to keep tests independent.
            conn.execute("DELETE FROM children WHERE id = ?", (child_id,))
            conn.execute(
                "INSERT INTO children "
                "(id, display_name, birthdate, pronouns, reading_level, "
                " interests, comfort, banned_themes, notes) "
                "VALUES (?, ?, NULL, NULL, ?, NULL, NULL, ?, NULL)",
                (child_id, display_name, reading_level, banned_themes),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_seeded_toys_appear_in_activity_and_labeled_event(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Spec contract: seeded toys + rooms + 1 child profile MUST surface
    in the activity output AND the labeled_events row.

    M3: includes a child profile seed (banned_themes + reading_level)
    so the labeled_events ChatML carries the child constraints — Phase
    E SFT export depends on this.
    """
    toys = [
        ("toy-a", "Bluey"),
        ("toy-b", "Buzz Lightyear"),
        ("toy-c", "Woody"),
    ]
    _seed_toys(db_path, toys)
    _seed_rooms(db_path, [("room-1", "Living Room"), ("room-2", "Kitchen")])
    _seed_child(
        db_path,
        child_id="child-1",
        banned_themes="scary",
        reading_level="early-reader",
    )

    activity = _propose(
        client,
        parent_headers,
        context={"child_ids": ["child-1"]},
    )

    # The {toy} placeholder is now substituted with one of the seeded
    # display names — NOT the legacy "Mr. Unicorn" placeholder.
    seeded_names = {name for _, name in toys}
    step_text_blob = " ".join(s["body"] for s in activity["steps"])
    assert any(name in step_text_blob for name in seeded_names), (
        f"expected one of {seeded_names!r} in step text; got {step_text_blob!r}"
    )
    # Banned themes filter: any template tagged "scary" must NOT have
    # been picked. Templates are pre-checked by id+title; we assert
    # the chosen template_id doesn't contain "scary".
    title_blob = (activity.get("title") or "").lower()
    assert "scary" not in title_blob

    # The labeled_events row carries the catalog content + child
    # constraints in inputs_chatml_json.
    row = _read_labeled(db_path, activity["id"])
    assert row is not None
    chatml = json.loads(row["inputs_chatml_json"])
    user_payload = json.loads(chatml[1]["content"])
    assert set(user_payload["available_toys"]) == seeded_names
    assert set(user_payload["available_rooms"]) == {"Living Room", "Kitchen"}
    cp = user_payload["child_profile"]
    assert cp is not None
    assert "scary" in cp["banned_themes"]
    assert cp["reading_level"] == "early-reader"


def test_propose_writes_labeled_event_for_every_generation(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    _seed_toys(db_path, [("toy-a", "Apollo")])
    a1 = _propose(client, parent_headers, seed=1)
    a2 = _propose(client, parent_headers, seed=2)
    assert _read_labeled(db_path, a1["id"]) is not None
    assert _read_labeled(db_path, a2["id"]) is not None


def test_empty_catalog_falls_back_to_placeholder_toy(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    # No toys, no rooms, no children seeded → propose must succeed
    # with the Phase A placeholder vocabulary.
    activity = _propose(client, parent_headers)
    step_text_blob = " ".join(s["body"] for s in activity["steps"])
    # Mr. Unicorn is the legacy placeholder. Either it appears (templates
    # that use {toy}) or there's no toy reference at all (templates
    # without {toy}). Either way: no crash, no malformed prompt.
    row = _read_labeled(db_path, activity["id"])
    assert row is not None
    chatml = json.loads(row["inputs_chatml_json"])
    user_payload = json.loads(chatml[1]["content"])
    assert user_payload["available_toys"] == []
    assert user_payload["available_rooms"] == []
    # No-child case: child_profile is None.
    assert user_payload["child_profile"] is None
    # Phase G G2.5: propose response carries the full template plan
    # (5 steps for linear templates). DB activity_steps still lazy-
    # inserted to 1 row at creation.
    assert len(activity["steps"]) == 5
    assert step_text_blob


def test_banned_theme_filters_all_templates_to_safe_default(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Seed a child whose banned theme matches every shipped template id/title.

    Picking the words ``play`` + ``story`` + ``activity`` + ``boredom``
    + ``invent`` + ``parade`` + ``quest`` etc. would be brittle — we
    use the shipped templates' IDs all share short word stems. Use
    ``play``, ``story``, ``boredom``, and any other top-level intent
    word that appears in template ids; the fallback safe-default uses
    none of these.

    M2: The spec for this path is "safe-default + WARNING". The unit
    test pins the WARNING for ``apply_banned_themes_filter``; this
    integration test pins it end-to-end — i.e. the WARNING fires on a
    real propose call, not just inside the unit-tested helper.
    """
    # Insert a child whose banned themes match BOTH "play" (in
    # play_*  template ids) and the other shipped intent stems. The
    # safe-default's id is "safe_default_quiet_moment" which contains
    # none of these stems.
    _seed_child(
        db_path,
        child_id="c1",
        banned_themes="play,story,activity,boredom,invent,quest,parade",
    )

    with caplog.at_level(logging.WARNING):
        activity = _propose(client, parent_headers)
    # The chosen template should be the safe-default — the activity's
    # title is the safe-default title.
    assert activity["title"] == "A quiet moment together"
    # Spec: a WARNING fires when banned_themes wipes out all candidates.
    # Match either the apply_banned_themes_filter WARNING or the
    # _select_template "all template pools wiped" WARNING — either
    # surfacing satisfies the observability contract.
    matches = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and ("safe-default" in r.message or "filtered" in r.message or "wiped" in r.message)
    ]
    assert matches, f"expected a WARNING about banned-themes filtering; saw {caplog.records!r}"


def test_multi_child_banned_themes_union_in_chatml(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    _seed_child(db_path, child_id="ca", banned_themes="scary", reading_level="early-reader")
    _seed_child(db_path, child_id="cb", banned_themes="loud", reading_level="fluent")

    activity = _propose(
        client,
        parent_headers,
        context={"child_ids": ["ca", "cb"]},
    )
    row = _read_labeled(db_path, activity["id"])
    assert row is not None
    chatml = json.loads(row["inputs_chatml_json"])
    user_payload = json.loads(chatml[1]["content"])
    cp = user_payload["child_profile"]
    assert cp is not None
    assert set(cp["banned_themes"]) == {"scary", "loud"}
    # Reading level minimum: early-reader < fluent.
    assert cp["reading_level"] == "early-reader"


def test_multi_child_minimum_reading_level_is_pre_reader(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    _seed_child(db_path, child_id="ca", reading_level="pre-reader")
    _seed_child(db_path, child_id="cb", reading_level="fluent")
    activity = _propose(
        client,
        parent_headers,
        context={"child_ids": ["ca", "cb"]},
    )
    row = _read_labeled(db_path, activity["id"])
    assert row is not None
    chatml = json.loads(row["inputs_chatml_json"])
    user_payload = json.loads(chatml[1]["content"])
    cp = user_payload["child_profile"]
    assert cp is not None
    assert cp["reading_level"] == "pre-reader"


def test_step_20_anti_signal_against_real_slot_values(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Pre-seed a ``didnt_work`` feedback row for one candidate's
    real-slot signature; assert the generator picks something else.

    Setup:
    1. Seed two toys (so the toy-name slot value is deterministic with seed).
    2. Probe what the generator would otherwise pick by calling propose
       once with a known seed.
    3. Insert a feedback row with kind=``didnt_work`` for the SAME
       (template_id, slot_values) signature.
    4. Re-propose with the same seed — assert the picked template_id
       is DIFFERENT (the veto fired).
    """
    _seed_toys(db_path, [("toy-a", "Apollo"), ("toy-b", "Buzz")])

    # Probe: what does the generator pick on this seed without feedback?
    probe = _propose(client, parent_headers, seed=99, slot="dragons")
    probe_signature = probe["metadata"]["signature"]
    # template_id lives in labeled_events.activity_json (it's also at
    # the summary JSON top level, not inside the metadata dict).
    probe_row = _read_labeled(db_path, probe["id"])
    assert probe_row is not None
    probe_activity = json.loads(probe_row["activity_json"])
    probe_template_id = probe_activity["template_id"]

    # The signature in the activity row is computed by the generator
    # from the actual slot_values. It MUST match the pre-computed
    # version we'd reconstruct from the slot field.
    assert probe_signature == compute_signature(probe_template_id, probe["metadata"]["slot_values"])

    # Insert a didnt_work feedback row keyed on this signature.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO feedback "
                "(id, activity_id, step_seq, kind, signature, reason, created_at) "
                "VALUES (?, ?, NULL, ?, ?, NULL, '2026-05-03T00:00:00Z')",
                (str(uuid.uuid4()), probe["id"], KIND_DIDNT_WORK, probe_signature),
            )
    finally:
        conn.close()

    # Re-propose with the same seed. Even though the seed would
    # otherwise drive the same template pick, the feedback consultation
    # should now veto the original signature and produce a different
    # template OR a re-rolled slot.
    second = _propose(
        client, parent_headers, seed=99, slot="dragons", context={"force_uniqueness": "x"}
    )
    second_row = _read_labeled(db_path, second["id"])
    assert second_row is not None
    second_activity = json.loads(second_row["activity_json"])
    second_template_id = second_activity["template_id"]
    # Different template_id (veto fired) OR different signature (slot
    # changed). The contract is "veto re-picks", which can manifest as
    # either; both are acceptable.
    assert (
        second_template_id != probe_template_id
        or second["metadata"]["signature"] != probe_signature
    )


def test_propose_with_unknown_child_id_does_not_crash(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Caller passes a non-existent child_id; resolver returns no
    constraints and propose still succeeds with placeholders."""
    activity = _propose(
        client,
        parent_headers,
        context={"child_ids": ["does-not-exist"]},
    )
    # Phase G G2.5: propose response carries the full template plan
    # (5 steps for linear templates).
    assert len(activity["steps"]) == 5
    row = _read_labeled(db_path, activity["id"])
    assert row is not None


# ---------------------------------------------------------------------------
# Smoke: the resolver doesn't break the existing offline path.
# ---------------------------------------------------------------------------


def test_existing_intents_still_work_with_real_content(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    _seed_toys(db_path, [("t-a", "Apollo")])
    for intent in ("request_play", "request_story", "request_activity", "boredom"):
        activity = _propose(client, parent_headers, intent=intent, seed=1)
        # Phase G G2.5: propose response carries the full template plan
        # (5 steps for linear templates).
        assert len(activity["steps"]) == 5
        row = _read_labeled(db_path, activity["id"])
        assert row is not None
