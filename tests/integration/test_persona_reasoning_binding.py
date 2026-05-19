"""Phase N Step N0b: ``persona_reasoning`` consistency with bound persona.

UAT Phase M defect D1: the propose card's ``persona_reasoning`` referenced a
persona name ("professor pip" / "Inspector Pip") that did NOT match the
actually-bound persona on the resulting activity ("Professor Iridia" on the
kiosk runtime card). Cosmetic / wire-shape inconsistency that erodes parent
trust.

This module pins two invariants that together close D1:

1. **Regenerate must NOT inherit stale ``persona_reasoning`` from the source**
   row. Regenerate explicitly DOES NOT inherit the source's ``persona_id``
   (see ``post_regenerate``); inheriting the rationale text that names the
   old persona is the bug — the picked persona on the regenerated row is
   typically different, so the inherited string lies about the new pick.
   We assert the regenerated row's ``persona_reasoning`` names the
   ACTUALLY-bound persona's ``display_name`` (or falls back to the
   "matched on intent" sentinel when no library row is loaded).

2. **The escalation-dispatcher persistence path must synthesize
   ``persona_reasoning`` from the persona's ``display_name``**, not from the
   raw ``persona_id`` slug. A slug like ``"periodic_table"`` leaking into
   the parent-facing "why this?" panel is its own less-noticed flavour of
   the same bug. We assert the dispatcher-persisted row carries the
   display name in its ``persona_reasoning`` when a library persona is
   pinned, falling back to ``"matched on intent"`` when no persona is
   pinned (the existing ``activity.persona_id is None`` branch is
   untouched).

Both surfaces share the same root pathology: ``persona_reasoning`` text was
generated independently of the final persona binding. The fix wires the
synthesizers to the actually-bound persona's ``display_name`` and stops the
regenerate path from carrying stale text across a different binding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from toybox.db.connection import connect

# Match the schema_test seed shape — exercising just the columns the
# library-persona picker actually reads (id, display_name, source).
_INSERT_PERSONA_SQL = (
    "INSERT INTO personas (id, display_name, system_prompt, source, created_at) "
    "VALUES (?, ?, ?, ?, ?)"
)


def _seed_two_library_personas(db_path: Path) -> None:
    """Seed two distinct ``source='library'`` personas.

    Two are needed so the random picker on the second propose call
    (the regenerate's fresh propose) has a CHANCE of landing on a
    different persona than the first call. With one persona the
    regenerate would always re-bind to the same one and the bug
    would be impossible to surface.
    """
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                _INSERT_PERSONA_SQL,
                ("detective", "Inspector Pip", "p1", "library", "2026-01-01T00:00:00Z"),
            )
            conn.execute(
                _INSERT_PERSONA_SQL,
                (
                    "periodic_table",
                    "Professor Iridia",
                    "p2",
                    "library",
                    "2026-01-01T00:00:00Z",
                ),
            )
    finally:
        conn.close()


_PERSONA_ID_TO_DISPLAY: dict[str, str] = {
    "detective": "Inspector Pip",
    "periodic_table": "Professor Iridia",
}


def _propose(client: TestClient, headers: dict[str, str], seed: int) -> dict[str, Any]:
    """Submit a propose request and return the parsed JSON body."""
    response = client.post(
        "/api/activities/propose",
        json={
            "intent": "request_play",
            "slot": "unicorns",
            "hour": 12,
            "seed": seed,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


# ---------------------------------------------------------------------------
# 1. Regenerate must not inherit stale persona_reasoning across a re-pick.
# ---------------------------------------------------------------------------


def test_regenerate_persona_reasoning_matches_new_persona(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Phase N D1 fix: regenerate's response carries text naming the
    NEW persona (or the no-persona fallback), NOT the source's persona.

    Cycle:
      * Propose #1 → picks library persona A (Inspector Pip OR Professor
        Iridia, random).
      * Regenerate the source → fresh propose picks library persona B.
      * The regenerate response's ``persona_reasoning`` MUST name B's
        ``display_name`` (or be the ``matched on intent`` sentinel), not
        A's. The bug carried A's text into B's response.

    With two library rows the regenerate picks a fresh persona; whichever
    pick lands, the rationale text must agree with the persona_id that
    landed on the response row.
    """
    _seed_two_library_personas(db_path)

    # Propose #1: capture the source row's persona binding + reasoning.
    source = _propose(client, parent_headers, seed=17)
    source_persona_id = source["persona_id"]
    assert source_persona_id in _PERSONA_ID_TO_DISPLAY, source
    source_display = _PERSONA_ID_TO_DISPLAY[source_persona_id]
    source_reasoning = source["persona_reasoning"]
    assert isinstance(source_reasoning, str) and source_reasoning, source
    # Defence: pin that propose #1 itself is wire-consistent — the source
    # response's reasoning names the source's persona. Without this the
    # regenerate-side assertion below could mask a propose-side breakage.
    assert source_display in source_reasoning, (
        f"propose persona_reasoning {source_reasoning!r} does not name "
        f"bound persona display_name {source_display!r}"
    )

    # Regenerate with a DIFFERENT seed so the fresh propose has a real
    # chance of landing on the OTHER library persona row. We don't pin a
    # specific outcome — we only assert wire-consistency between
    # persona_id and persona_reasoning on whichever pick the regenerate
    # lands on.
    response = client.post(
        f"/api/activities/{source['id']}/regenerate",
        json={"intent": "request_play", "hour": 12, "seed": 91},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert response.status_code == 200, response.text
    regen = response.json()
    regen_persona_id = regen["persona_id"]
    assert regen_persona_id in _PERSONA_ID_TO_DISPLAY, regen
    regen_display = _PERSONA_ID_TO_DISPLAY[regen_persona_id]
    regen_reasoning = regen["persona_reasoning"]
    assert isinstance(regen_reasoning, str) and regen_reasoning

    # Core D1 invariant: the regen's reasoning must name the BOUND
    # persona. If the bug still bites and the regen picked a different
    # persona than the source, the inherited stale text would name the
    # source's persona — which is exactly the cross-persona case we want
    # to catch. If the regen happens to pick the same persona as the
    # source, the assertion is still true (and trivially so).
    assert regen_display in regen_reasoning, (
        f"regenerate persona_reasoning {regen_reasoning!r} does not name "
        f"bound persona display_name {regen_display!r} "
        f"(persona_id={regen_persona_id!r})"
    )


def test_regenerate_explicit_caller_persona_reasoning_still_wins(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Belt + braces: a regenerate body that EXPLICITLY supplies
    ``persona_reasoning`` (e.g. an automation pre-filling the rationale)
    still wins verbatim. The D1 fix narrows ONLY the implicit
    "inherit silently from source" path — explicit caller-supplied text
    keeps the existing caller_supplied-wins priority documented on
    ``_build_persona_reasoning``.
    """
    _seed_two_library_personas(db_path)
    source = _propose(client, parent_headers, seed=23)
    response = client.post(
        f"/api/activities/{source['id']}/regenerate",
        json={
            "intent": "request_play",
            "hour": 12,
            "seed": 99,
            "persona_reasoning": "matched on child interest in unicorns",
        },
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert response.status_code == 200, response.text
    regen = response.json()
    assert regen["persona_reasoning"] == "matched on child interest in unicorns"


# ---------------------------------------------------------------------------
# 2. Escalation-dispatcher path: synthesize from display_name, not slug.
# ---------------------------------------------------------------------------


def test_dispatcher_persona_reasoning_uses_display_name(
    db_path: Path,
) -> None:
    """``main._persist_dispatcher_activity`` synthesizes ``persona_reasoning``
    from the persona's ``display_name`` when a library persona is pinned —
    NOT from the raw ``persona_id`` slug.

    Pre-fix behaviour wrote ``"persona periodic_table picked for boredom"``
    (slug leaked to the parent-facing UI). Post-fix the same row reads
    ``"Professor Iridia picked for boredom"``, matching the propose-path
    convention in ``_build_persona_reasoning``.

    We drive the persistence helper directly rather than the full mic
    pipeline — the audio capture surface is out of scope for D1 and the
    helper is the sole writer of the dispatcher branch's
    ``persona_reasoning``.
    """
    # Late import: keeps the test independent of the production lifespan
    # bootstrap (no need to spin a TestClient with the full WS layer).
    from toybox.activities.models import Activity, ActivityStep
    from toybox.core.pubsub import PubSub
    from toybox.main import PRODUCTION_SESSION_ID, _persist_dispatcher_activity
    from toybox.triggers.registry import Intent

    _seed_two_library_personas(db_path)
    conn = connect(db_path, check_same_thread=False)
    try:
        # Insert the production-session row the dispatcher path INSERTs
        # against (FK-anchored: ``_persist_dispatcher_activity`` writes
        # ``activities.session_id = PRODUCTION_SESSION_ID`` unconditionally).
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (PRODUCTION_SESSION_ID, "2026-05-18T00:00:00Z"),
            )

        activity = Activity(
            id="00000000-0000-0000-0000-000000000d01",
            template_id="boredom_dance",
            persona_id="periodic_table",
            title="Dance break",
            steps=[
                ActivityStep(step_index=0, text="Step 1"),
                ActivityStep(step_index=1, text="Step 2"),
                ActivityStep(step_index=2, text="Step 3"),
            ],
            version=1,
            metadata={},
            toy_ids=(),
        )
        intent = Intent(name="boredom", slot=None, pattern_id="curated-boredom-base")
        pubsub = PubSub(max_per_subscriber=4, coalesce_window_ms=0)
        _persist_dispatcher_activity(activity, intent, conn, pubsub)

        # Read back the persisted summary envelope and confirm the
        # reasoning string names the DISPLAY NAME, not the slug.
        row = conn.execute(
            "SELECT summary FROM activities WHERE id = ?",
            (activity.id,),
        ).fetchone()
        assert row is not None
        envelope = json.loads(row["summary"])
        metadata = envelope.get("metadata", {})
        reasoning = metadata.get("persona_reasoning")
        assert isinstance(reasoning, str) and reasoning, envelope
        assert "Professor Iridia" in reasoning, reasoning
        # Defence: the slug must NOT appear (the pre-fix string was
        # ``"persona periodic_table picked for boredom"``).
        assert "periodic_table" not in reasoning, reasoning
    finally:
        conn.close()


def test_dispatcher_persona_reasoning_no_persona_falls_back(
    db_path: Path,
) -> None:
    """When the dispatcher's Activity has ``persona_id=None`` (library
    empty / generator didn't pin one), ``persona_reasoning`` falls back
    to the intent-only sentinel. This pins the no-persona branch so the
    display-name fix doesn't regress it.
    """
    from toybox.activities.models import Activity, ActivityStep
    from toybox.core.pubsub import PubSub
    from toybox.main import PRODUCTION_SESSION_ID, _persist_dispatcher_activity
    from toybox.triggers.registry import Intent

    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (PRODUCTION_SESSION_ID, "2026-05-18T00:00:00Z"),
            )

        activity = Activity(
            id="00000000-0000-0000-0000-000000000d02",
            template_id="boredom_dance",
            persona_id=None,
            title="No persona",
            steps=[
                ActivityStep(step_index=0, text="Step 1"),
                ActivityStep(step_index=1, text="Step 2"),
                ActivityStep(step_index=2, text="Step 3"),
            ],
            version=1,
            metadata={},
            toy_ids=(),
        )
        intent = Intent(name="boredom", slot=None, pattern_id="curated-boredom-base")
        pubsub = PubSub(max_per_subscriber=4, coalesce_window_ms=0)
        _persist_dispatcher_activity(activity, intent, conn, pubsub)

        row = conn.execute(
            "SELECT summary FROM activities WHERE id = ?",
            (activity.id,),
        ).fetchone()
        assert row is not None
        envelope = json.loads(row["summary"])
        reasoning = envelope.get("metadata", {}).get("persona_reasoning")
        # Phase N D1 fix: dispatcher path now shares the
        # ``_build_persona_reasoning`` synthesizer with the propose path,
        # so the no-persona fallback string is the propose-path sentinel
        # ``"matched on intent"`` rather than the pre-fix dispatcher-only
        # ``f"matched on intent {intent.name}"``. The frontend's existing
        # default placeholder is also literal ``"matched on intent"`` —
        # the dispatcher path and the propose path now both match the
        # UI's expected default.
        assert reasoning == "matched on intent", reasoning
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Negative-control: the propose-only test in test_activity_polish.py
# already pins the happy-path "caller-supplied wins" and "default
# synthesis is non-empty" invariants. We keep this module focused on the
# D1 surfaces — propose binding agreement is implicit in the
# regenerate-#1 assertion above (the source row's reasoning is checked
# against source's persona_id before the regenerate runs).
# ---------------------------------------------------------------------------


_ALL_TESTS: tuple[Any, ...] = (
    test_regenerate_persona_reasoning_matches_new_persona,
    test_regenerate_explicit_caller_persona_reasoning_still_wins,
    test_dispatcher_persona_reasoning_uses_display_name,
    test_dispatcher_persona_reasoning_no_persona_falls_back,
)


def test_module_pins_all_d1_surfaces() -> None:
    """Pin: this module covers all four D1 surfaces (regen-implicit,
    regen-explicit, dispatcher-with-persona, dispatcher-no-persona).
    Adding or dropping a test should be an intentional choice surfaced
    in code review."""
    assert len(_ALL_TESTS) == 4
