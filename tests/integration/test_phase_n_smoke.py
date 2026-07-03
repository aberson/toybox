"""Phase N Step N5 — element_microgame end-to-end smoke gate.

The canonical regression guard for Phase N. Exercises the new
``template_type=element_microgame`` shape end-to-end through the real
production caller chain with real corpora, real validators, real DB.
No mocks (per ``.claude/rules/code-quality.md`` § "New components
require an integration test through the production caller").

The element_microgame templates (N4) live in
``src/toybox/activities/templates/branching/request_activity.json``
under intent ``request_activity``. Each template:

* declares ``template_type: "element_microgame"`` (N2 typed marker),
* has exactly 4 steps: text → fork (family) → fork (fact) → text (N2 rule 1/2/3/4),
* carries ``element_id`` on every step (N2 rule 5),
* declares ``required_roles: ["guide_mentor"]`` (N2 rule 6),
* and ``ending_step: {"kind": "song", "auto": true, "element_id": <id>}`` (N2 rule 7).

Sub-tests, one per Phase N spec checkpoint (plan §5 N5):

* (a) ``test_propose_returns_element_microgame_template`` — propose with
  ``template_type=element_microgame`` succeeds; envelope carries the
  expected shape (4 steps, two fork steps, element_id on every step).
* (b1) ``test_walk_correct_correct_advances_to_reward`` — pick fact_a
  at both forks; activity walks to the terminal advance and a reward
  fires (per plan §5 N5 "(b) all 4 steps walk + 2 fork picks resolve
  correctly").
* (b2) ``test_walk_wrong_wrong_still_reaches_reward`` — pick the wrong
  fork on both forks; activity still walks to the terminal advance
  ("advance anyway" engine semantic per plan §1 step 2/3).
* (b3) ``test_step_count_is_four`` — propose response carries exactly
  4 steps (N2 rule 1).
* (c1) ``test_resolved_persona_is_periodic_table`` — when caller pins
  ``persona_id=periodic_table``, the activity's ``persona_id``
  surfaces on the wire as ``periodic_table``.
* (c2) ``test_persona_reasoning_names_iridia`` — when caller pins
  ``persona_id=periodic_table`` and supplies no ``persona_reasoning``,
  the synthesized default mentions "Iridia" (locks the N0b fix on the
  new templates; ``_build_persona_reasoning`` reads ``persona.display_name``
  which is "Professor Iridia" for the periodic_table library persona).
* (d1) ``test_every_step_carries_element_id`` — every step in the
  proposed wire envelope (proposed-state preview, sourced from the
  template) has ``element_id`` set to the activity's element_id.
* (d2) ``test_running_state_steps_carry_element_id`` — after approve +
  one advance, the running-state WS envelope (sourced from the
  persisted ``activity_steps`` rows via the
  ``_resolve_element_id_for_persisted_step`` path) STILL carries the
  ``element_id`` on the persisted current step. The M3 wire-shape
  regression (running-state element_id silently disappeared) is the
  same drift class that would bite N4 templates if the M3 fix ever
  regressed.
* (e1) ``test_reward_fires_after_terminal_advance`` — after the
  terminal advance + dismiss, an activity_steps row of kind='reward'
  has been appended.
* (e2) ``test_reward_song_resolver_picks_a_song`` — drive the L3
  reward resolver directly for an element_microgame-shaped activity
  context (theme=music to match the template's
  ``recommended_themes=["music"]``); a song reward fires from the
  shipped 75-entry M7 corpus.
* (e3) ``test_reward_selects_element_themed_song_when_available`` —
  **SPEC-GAP TEST**: the plan §3 promises "ending_step.element_id so
  Phase L's reward matcher picks the element-themed song" but
  ``EndingStep`` has ``extra='ignore'`` (models.py:404) AND ``Song``
  has ``extra='forbid'`` with NO element_id field (song_corpus.py:148).
  The N5 spec asks us to assert "song with element_id matches the
  activity's element_id when available". As shipped, this property
  does not hold — the resolver picks by ``theme`` + ``persona_compat``
  only. We codify the spec-violating assertion with ``xfail(strict=True)``
  so a future production fix that wires up element_id-based song
  selection will FLIP the test green and force the smoke gate to be
  re-examined (per ``.claude/rules/code-quality.md`` § "Audit wire
  shape when storage representation changes" — spec drift caught at
  the wire is the load-bearing pattern).
* (e4) ``test_reward_falls_back_to_any_song_when_no_themed_song_exists``
  — for ANY element_microgame the resolver returns a non-None song
  reward (the M7 corpus has 19 ``music``-themed songs across all
  ages, so the picker is always able to land on one).

Determinism: every sub-test stages a single-template fixture (one
specific element_microgame template extracted from production
``request_activity.json``) so the seeded picker MUST land on it.
Same pattern as ``test_phase_m_smoke.py`` ``_stage_single_template_dir``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.activities import element_corpus, joke_corpus, song_corpus
from toybox.activities.content_resolver import (
    RewardActivityContext,
    resolve_reward,
)
from toybox.db.connection import connect
from toybox.personas.loader import load_library_personas

# ---------------------------------------------------------------------
# Shared fixture helpers — mirror the test_phase_m_smoke.py pattern.
# ---------------------------------------------------------------------


_PRODUCTION_TEMPLATES_DIR: Path = (
    Path(__file__).resolve().parents[2] / "src" / "toybox" / "activities" / "templates"
)
_PRODUCTION_BRANCHING_DIR: Path = _PRODUCTION_TEMPLATES_DIR / "branching"


# Three element_microgame template ids the smoke gate parametrizes over.
# All three elements have age_band=3-5 (verified via
# data/elements/elements.json) so the corpus is in the kid-likely band.
#
# * h-1 = Hydrogen (family: nonmetal)
# * au-79 = Gold (family: transition_metal)
# * fe-26 = Iron (family: transition_metal)
#
# Per plan §3, every element_microgame template has the same
# ``recommended_themes: ["music"]`` (verified by grep — all 118
# carry music as the only recommended_theme). So the L3 song resolver
# picks from the same 19-song "music"-theme pool regardless of element.
# The plan §2's "element-themed song" promise is NOT enforceable at
# the wire today; sub-test (e3) marks the assertion xfail to surface it.
_TARGET_ELEMENTS: list[tuple[str, str]] = [
    # (element_id, template_id)
    ("h-1", "element_microgame_h_1"),
    ("au-79", "element_microgame_au_79"),
    ("fe-26", "element_microgame_fe_26"),
]


def _extract_template_payload(intent: str, template_id: str) -> dict[str, Any]:
    """Read one template by id from the production branching JSON.

    Phase N N4 appended 118 ``element_microgame_*`` entries to
    ``request_activity.json``. We pull a single entry verbatim so the
    smoke test exercises the SAME bytes the parent UI sees in
    production — not a paraphrased fixture that could silently drift
    from the shipped templates.
    """
    src = _PRODUCTION_BRANCHING_DIR / f"{intent}.json"
    payload = json.loads(src.read_text(encoding="utf-8"))
    templates = payload.get("templates", [])
    for entry in templates:
        if entry.get("id") == template_id:
            return cast("dict[str, Any]", entry)
    raise AssertionError(
        f"template {template_id!r} not found in {src.name}; "
        "Phase N N4 generator step may have not appended this id "
        "(check scripts/generate_element_microgames.py output)"
    )


def _stage_single_template_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    intent: str,
    template_payload: dict[str, Any],
) -> Path:
    """Stage a tmp templates dir holding ONLY ``template_payload`` under
    ``<intent>.json`` (plus the schema). The autouse
    ``_isolate_to_production_templates`` fixture from conftest has
    already pointed ``generator.TEMPLATES_DIR`` somewhere harmless; we
    override that pointer here so the seeded picker MUST land on the
    one template we authored. Mirrors test_phase_m_smoke.py.
    """
    from toybox.activities import generator

    staged = tmp_path / f"templates_n5_{intent}"
    staged.mkdir(exist_ok=True)
    shutil.copy(_PRODUCTION_TEMPLATES_DIR / "_schema.json", staged / "_schema.json")
    (staged / f"{intent}.json").write_text(
        json.dumps({"intent": intent, "templates": [template_payload]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(generator, "TEMPLATES_DIR", staged)
    generator.clear_template_cache()
    return staged


def _seed_toys(
    db_path: Path,
    toys: list[tuple[str, str, list[str] | None]],
) -> None:
    """Insert toys with optional per-toy ``allowed_roles`` (JSON-encoded).
    Mirrors ``_seed_toys`` in test_phase_m_smoke.py.
    """
    conn = connect(db_path)
    try:
        with conn:
            for toy_id, display_name, allowed_roles in toys:
                allowed_json = json.dumps(allowed_roles) if allowed_roles is not None else None
                conn.execute(
                    "INSERT INTO toys "
                    "(id, display_name, image_path, image_hash, type, tags, "
                    " persona_id, archived, created_at, last_used_at, allowed_roles) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
                    " '2026-01-01T00:00:00Z', NULL, ?)",
                    (toy_id, display_name, f"img/{toy_id}.png", f"hash-{toy_id}", allowed_json),
                )
    finally:
        conn.close()


def _seed_library_personas(db_path: Path, tmp_path: Path) -> None:
    """Load the four shipped library personas via the real loader."""
    conn = connect(db_path)
    try:
        load_library_personas(conn, tmp_path)
    finally:
        conn.close()


def _seed_only_iridia_library(db_path: Path, tmp_path: Path) -> None:
    """Load the full library via the real loader, then DELETE every
    library persona EXCEPT ``periodic_table``. Makes the propose
    handler's ``_pick_random_library_persona`` path deterministic — the
    only persona the picker can reach is Iridia.

    Why not stage a one-persona library directly: the loader resolves
    avatar paths against ``library.parent`` and refuses anything outside
    the library tree. Staging a copy at a fresh location collides with
    that safety check. The delete-after-load approach sidesteps that.

    Caller-pinned ``persona_id`` populates ``activity.persona_id`` on
    the wire but NOT ``metadata.persona`` (see
    src/toybox/api/activities.py:2004-2010). Letting the propose handler
    randomly pick from a one-persona pool is the only way to make BOTH
    surfaces consistent for the smoke gate without modifying production.
    """
    conn = connect(db_path)
    try:
        load_library_personas(conn, tmp_path)
        with conn:
            conn.execute(
                "DELETE FROM personas WHERE source = 'library' AND id != ?",
                ("periodic_table",),
            )
    finally:
        conn.close()


def _stage_elements_corpus(tmp_path: Path) -> None:
    """Copy the production ``data/elements/elements.json`` into the test's
    ``TOYBOX_DATA_DIR`` so element_corpus.get_element resolves for every
    element_id referenced by an element_microgame template.

    Required for the resolve_reward path because:
    1. resolve_reward → _compute_activity_themes → _template_recommended_themes
    2. _template_recommended_themes → find_template_by_id
    3. find_template_by_id → _parse_template → validate_template
    4. validate_template → get_element(step.element_id) for the M3 check

    A missing elements.json under TOYBOX_DATA_DIR crashes the chain at
    step 4. Tests that monkeypatch TOYBOX_DATA_DIR (for song corpus
    staging) MUST also stage the elements corpus.
    """
    src_elements = Path(__file__).resolve().parents[2] / "data" / "elements" / "elements.json"
    if not src_elements.is_file():  # pragma: no cover -- shipped file
        raise AssertionError(
            f"production element corpus not found at {src_elements}; Phase M M1 may have moved it"
        )
    elements_dir = tmp_path / "elements"
    elements_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(src_elements, elements_dir / "elements.json")
    element_corpus.clear_element_cache()


def _propose(
    client: TestClient,
    headers: dict[str, str],
    *,
    intent: str = "request_activity",
    seed: int = 17,
    persona_id: str | None = "periodic_table",
) -> dict[str, Any]:
    body: dict[str, Any] = {"intent": intent, "slot": None, "hour": 12, "seed": seed}
    if persona_id is not None:
        body["persona_id"] = persona_id
    response = client.post(
        "/api/activities/propose",
        json=body,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


def _approve(
    client: TestClient,
    headers: dict[str, str],
    activity_id: str,
    version: int,
    *,
    reward_type: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if reward_type is not None:
        body["reward_type"] = reward_type
    response = client.post(
        f"/api/activities/{activity_id}/approve",
        json=body,
        headers={**headers, "If-Match-Version": str(version)},
    )
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


def _advance(
    client: TestClient,
    headers: dict[str, str],
    activity_id: str,
    version: int,
    *,
    choice_index: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] | None = None
    if choice_index is not None:
        body = {"choice_index": choice_index}
    response = client.post(
        f"/api/activities/{activity_id}/advance",
        json=body,
        headers={**headers, "If-Match-Version": str(version)},
    )
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


def _walk_with_fork_picks(
    client: TestClient,
    headers: dict[str, str],
    activity_id: str,
    start_version: int,
    *,
    fork1_choice: int,
    fork2_choice: int,
) -> dict[str, Any]:
    """Walk an element_microgame to terminal via approve→advances.

    Template shape (per N4 generator):

    1. ``intro`` (kind=text, no fork) — advance with no choice
    2. ``family_fork`` (kind=fork, 2 choices) — advance with ``fork1_choice``
    3. ``fact_fork`` (kind=fork, 2 choices) — advance with ``fork2_choice``
    4. ``reward`` (kind=text, terminal) — advance to fire L4 reward step

    Returns the final response (may be ``running`` if a reward step
    fired and Phase L's two-phase terminal advance is still on Phase 1).
    """
    version = start_version
    # Advance 1: approved → running, inserts step 1 (intro).
    state = _advance(client, headers, activity_id, version)
    # Advance 2: walk past intro to family_fork. Intro has no choices,
    # so the advance carries no choice_index.
    state = _advance(client, headers, activity_id, int(state["version"]))
    # Advance 3: at family_fork — pick fork1_choice.
    state = _advance(
        client,
        headers,
        activity_id,
        int(state["version"]),
        choice_index=fork1_choice,
    )
    # Advance 4: at fact_fork — pick fork2_choice.
    state = _advance(
        client,
        headers,
        activity_id,
        int(state["version"]),
        choice_index=fork2_choice,
    )
    # Advance 5: terminal advance from reward step. Per Phase L two-phase
    # terminal advance, this inserts the reward step at current=1 and
    # keeps state=running (state flips to completed on the NEXT advance).
    # Cap safety: stop after N additional advances regardless of state to
    # avoid an infinite loop if a future engine change rewrites the
    # contract.
    for _ in range(6):
        if state["state"] in ("completed", "ended"):
            break
        version = int(state["version"])
        state = _advance(client, headers, activity_id, version)
    return state


# ---------------------------------------------------------------------
# (a) Propose
# ---------------------------------------------------------------------


def test_propose_returns_element_microgame_template(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propose against a single-template fixture (Hydrogen's element_microgame)
    and verify the wire envelope carries the element_microgame shape:
    state=proposed, 4 steps, element_id on every step, two fork steps
    (steps[1] and steps[2]).
    """
    element_id, template_id = "h-1", "element_microgame_h_1"
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _seed_toys(
        db_path,
        [
            ("toy_owl", "Wise Owl", None),
            ("toy_bear", "Captain Bear", None),
        ],
    )
    _seed_library_personas(db_path, tmp_path)

    proposed = _propose(
        client,
        parent_headers,
        intent="request_activity",
        seed=11,
        persona_id="periodic_table",
    )
    assert proposed["state"] == "proposed"
    assert len(proposed["steps"]) == 4, (
        f"element_microgame must surface exactly 4 steps on the preview "
        f"wire (N2 rule 1); got {len(proposed['steps'])} "
        f"(steps={proposed['steps']!r})"
    )
    # Every step carries the activity's element_id.
    for idx, step in enumerate(proposed["steps"]):
        assert step.get("element_id") == element_id, (
            f"step[{idx}] missing element_id={element_id!r} on propose preview; "
            f"got {step.get('element_id')!r}"
        )
    # steps[1] and steps[2] are fork steps with exactly 2 choices.
    for fork_idx in (1, 2):
        choices = proposed["steps"][fork_idx].get("choices") or []
        assert len(choices) == 2, (
            f"element_microgame step[{fork_idx}] must surface exactly 2 "
            f"fork choices on the wire; got {len(choices)} "
            f"(choices={choices!r})"
        )


# ---------------------------------------------------------------------
# (b) Walk + fork picks
# ---------------------------------------------------------------------


def test_step_count_is_four(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins N2 rule 1 + the propose-preview render: the wire envelope
    surfaces exactly 4 steps for an element_microgame, no more, no less.
    Regression guard for a future template authoring slip (e.g. M4's
    initial single-step shape).
    """
    template_id = "element_microgame_au_79"
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _seed_toys(db_path, [("toy_owl", "Wise Owl", None)])
    _seed_library_personas(db_path, tmp_path)

    proposed = _propose(
        client,
        parent_headers,
        intent="request_activity",
        seed=3,
        persona_id="periodic_table",
    )
    # N2 rule 1.
    assert len(proposed["steps"]) == 4


def test_walk_correct_correct_advances_to_reward(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pick choice_index=0 at both forks. Per the N4 generator the first
    choice on each fork is the corpus-derived "correct" answer
    (peer_in_family + fact_a_true). Activity walks through all 4 steps
    and the L4 reward step is appended after the terminal advance.

    The smoke gate doesn't verify "correctness" of the kid's choice — the
    engine has no concept of "correct" beyond template authoring. It DOES
    verify the choice is accepted, advance proceeds, and a reward step
    fires.
    """
    element_id, template_id = "au-79", "element_microgame_au_79"
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _seed_toys(
        db_path,
        [
            ("toy_owl", "Wise Owl", None),
            ("toy_bear", "Captain Bear", None),
        ],
    )
    _seed_library_personas(db_path, tmp_path)

    proposed = _propose(
        client,
        parent_headers,
        intent="request_activity",
        seed=23,
        persona_id="periodic_table",
    )
    approved = _approve(
        client,
        parent_headers,
        proposed["id"],
        proposed["version"],
        reward_type="song",
    )
    final = _walk_with_fork_picks(
        client,
        parent_headers,
        proposed["id"],
        approved["version"],
        fork1_choice=0,
        fork2_choice=0,
    )
    # Activity reached a terminal state (completed or running-with-reward-pending).
    assert final["state"] in ("completed", "running"), (
        f"expected terminal state after walking 4 steps + reward dismiss; "
        f"got state={final['state']!r}; steps={final.get('steps', [])!r}"
    )
    # A reward step was appended somewhere along the walk.
    rows = _fetch_step_rows(db_path, proposed["id"])
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) >= 1, (
        f"expected at least one reward step after terminal advance; "
        f"got {len(reward_rows)}; rows={rows!r}"
    )
    # Final state's element_id round-trips. Per the M3 wire-shape
    # contract every persisted activity_steps row that maps to a
    # template step with element_id should carry it on the GET response.
    non_reward_steps = [s for s in final.get("steps", []) if s.get("kind") != "reward"]
    assert non_reward_steps, "expected non-reward steps on terminal response"
    for step in non_reward_steps:
        assert step.get("element_id") == element_id, (
            f"step element_id drift on terminal state: expected {element_id!r}, "
            f"got {step.get('element_id')!r} (step={step!r})"
        )


def test_walk_wrong_wrong_still_reaches_reward(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pick choice_index=1 at both forks — the "wrong" choices per the
    N4 generator convention. The engine has no concept of "correct"; per
    plan §1 step 2/3 ("advance anyway" semantic) the activity walks
    just the same and the reward fires.
    """
    element_id, template_id = "fe-26", "element_microgame_fe_26"
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _seed_toys(
        db_path,
        [
            ("toy_owl", "Wise Owl", None),
            ("toy_bear", "Captain Bear", None),
        ],
    )
    _seed_library_personas(db_path, tmp_path)

    proposed = _propose(
        client,
        parent_headers,
        intent="request_activity",
        seed=29,
        persona_id="periodic_table",
    )
    approved = _approve(
        client,
        parent_headers,
        proposed["id"],
        proposed["version"],
        reward_type="song",
    )
    final = _walk_with_fork_picks(
        client,
        parent_headers,
        proposed["id"],
        approved["version"],
        fork1_choice=1,
        fork2_choice=1,
    )
    assert final["state"] in ("completed", "running"), (
        f"wrong-fork walk failed to reach terminal: state={final['state']!r}"
    )
    rows = _fetch_step_rows(db_path, proposed["id"])
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) >= 1, f"wrong-fork walk failed to fire reward: rows={rows!r}"
    # element_id still rides on non-reward steps even when the kid took
    # the wrong-fact path.
    non_reward_steps = [s for s in final.get("steps", []) if s.get("kind") != "reward"]
    for step in non_reward_steps:
        if step.get("element_id") is not None:
            assert step["element_id"] == element_id, (
                f"wrong-fork walk corrupted element_id: expected {element_id!r}, "
                f"got {step['element_id']!r}"
            )


# ---------------------------------------------------------------------
# (c) Persona
# ---------------------------------------------------------------------


def test_resolved_persona_is_periodic_table(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The activity's wire envelope MUST carry ``persona_id="periodic_table"``.

    Approach: stage a SINGLE-PERSONA library (Iridia only) so the
    propose handler's ``_pick_random_library_persona`` path
    deterministically picks her. (Historical note: this staging was
    originally the ONLY way to make both wire surfaces consistent —
    pre-Phase-Z a caller-pinned ``persona_id`` populated
    ``activity.persona_id`` but NOT ``metadata.persona``. Z1 closed
    that asymmetry via ``_hydrate_persona_meta_by_id``; the
    random-pick staging is kept because it also exercises the
    library-pick path this smoke gate is about.)
    """
    template_id = "element_microgame_h_1"
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _seed_toys(db_path, [("toy_owl", "Wise Owl", None)])
    _seed_only_iridia_library(db_path, tmp_path)

    proposed = _propose(
        client,
        parent_headers,
        intent="request_activity",
        seed=5,
        persona_id=None,
    )
    assert proposed["persona_id"] == "periodic_table", (
        f"expected persona_id='periodic_table' (Professor Iridia) on "
        f"element_microgame; got {proposed.get('persona_id')!r}"
    )
    # Defense in depth: the metadata envelope's persona block also
    # carries the display name, which the Phase O parent UI surfaces
    # in the "why this?" panel.
    persona_meta = proposed.get("metadata", {}).get("persona")
    assert persona_meta is not None, (
        "metadata.persona must be populated when the propose handler "
        "picks a library persona; "
        f"metadata={proposed.get('metadata')!r}"
    )
    assert persona_meta.get("display_name") == "Professor Iridia", (
        f"expected persona display_name='Professor Iridia'; "
        f"got {persona_meta.get('display_name')!r}"
    )


def test_persona_reasoning_names_iridia(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N0b fix surface: the synthesized ``persona_reasoning`` for an
    element_microgame must reference "Iridia" (the resolved persona's
    display_name).

    ``_build_persona_reasoning`` priority order:
    1. caller-supplied → wins verbatim
    2. ``"<display_name> picked for <intent>"`` → contains "Professor Iridia"
    3. fallback ``"matched on intent"`` → no name

    The test pins path 2 by passing ``persona_reasoning=None`` and
    pinning persona_id=periodic_table. The N0b fix was about ensuring
    the synthesized text uses the RESOLVED persona's display_name (was
    leaking a stale "professor pip" from a regenerate-source's reasoning
    field per memory `project_phase_n_o_staged_2026-05-18`).
    """
    template_id = "element_microgame_au_79"
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _seed_toys(db_path, [("toy_owl", "Wise Owl", None)])
    _seed_only_iridia_library(db_path, tmp_path)

    proposed = _propose(
        client,
        parent_headers,
        intent="request_activity",
        seed=7,
        persona_id=None,
    )
    reasoning = proposed.get("persona_reasoning")
    assert reasoning is not None, (
        f"persona_reasoning must be populated when a persona is bound; got {reasoning!r}"
    )
    assert "Iridia" in reasoning, (
        f"persona_reasoning must name 'Iridia' for periodic_table persona "
        f"(N0b regression guard — see memory project_phase_n_o_staged_2026-05-18); "
        f"got {reasoning!r}"
    )


# ---------------------------------------------------------------------
# (d) ElementCard wire shape — element_id on every step
# ---------------------------------------------------------------------


@pytest.mark.parametrize("element_id,template_id", _TARGET_ELEMENTS)
def test_every_step_carries_element_id(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    element_id: str,
    template_id: str,
) -> None:
    """N2 rule 5: every step in an element_microgame template carries
    ``element_id``. The wire envelope must surface this so the kiosk's
    ElementCard component renders consistently across all 4 steps
    (per plan §3 "element_id on every step so ElementCard renders
    consistently"). Parametrize across 3 elements to catch a template-
    authoring slip on a specific element.
    """
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _seed_toys(db_path, [("toy_owl", "Wise Owl", None)])
    _seed_library_personas(db_path, tmp_path)

    proposed = _propose(
        client,
        parent_headers,
        intent="request_activity",
        seed=13,
        persona_id="periodic_table",
    )
    for idx, step in enumerate(proposed["steps"]):
        assert step.get("element_id") == element_id, (
            f"template {template_id!r}: step[{idx}] missing element_id "
            f"on the wire envelope (proposed-state preview path); "
            f"got element_id={step.get('element_id')!r}, "
            f"expected {element_id!r}"
        )


def test_running_state_steps_carry_element_id(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase M3 regression guard: when the activity transitions out of
    ``proposed``/``approved`` into ``running``, the WS envelope is
    sourced from the persisted ``activity_steps`` rows (NOT from the
    preview path that reads the template directly).
    ``_resolve_element_id_for_persisted_step`` joins back to the
    template by ``step_template_id`` to re-emit ``element_id`` on the
    running-state wire. Phase M iter 1 dropped this and the kiosk
    ElementCard went dark mid-activity. N5 pins it for element_microgames.
    """
    element_id, template_id = "h-1", "element_microgame_h_1"
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _seed_toys(db_path, [("toy_owl", "Wise Owl", None)])
    _seed_library_personas(db_path, tmp_path)

    proposed = _propose(
        client,
        parent_headers,
        intent="request_activity",
        seed=41,
        persona_id="periodic_table",
    )
    approved = _approve(
        client,
        parent_headers,
        proposed["id"],
        proposed["version"],
        reward_type="song",
    )
    # First advance: approved → running, inserts step 1.
    running = _advance(client, parent_headers, proposed["id"], approved["version"])
    assert running["state"] == "running"
    # The current step (the intro) must surface element_id on the wire.
    current_steps = [s for s in running.get("steps", []) if s.get("current")]
    assert len(current_steps) == 1, (
        f"expected exactly one current step on running-state wire; "
        f"got {len(current_steps)} (steps={running.get('steps', [])!r})"
    )
    assert current_steps[0].get("element_id") == element_id, (
        f"running-state element_id resolution failed (M3 regression class): "
        f"expected {element_id!r}, got {current_steps[0].get('element_id')!r}; "
        f"steps={running.get('steps', [])!r}"
    )


# ---------------------------------------------------------------------
# (e) Reward — song from M7 corpus
# ---------------------------------------------------------------------


def _fetch_step_rows(db_path: Path, activity_id: str) -> list[dict[str, Any]]:
    """Fetch ``activity_steps`` rows for an activity, sorted by seq.

    Bypasses the wire envelope so the reward-row assertion is rooted in
    persisted state (the Phase L two-phase terminal advance may leave
    state=running until the reward step is dismissed; querying the DB
    directly avoids that timing dependency).
    """
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT seq, body, kind, metadata_json, current "
            "FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
            (activity_id,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "seq": int(r["seq"]),
                "body": str(r["body"]),
                "kind": str(r["kind"]) if r["kind"] is not None else None,
                "metadata_json": r["metadata_json"],
                "current": bool(r["current"]),
            }
        )
    return out


def test_reward_fires_after_terminal_advance(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After approving with ``reward_type='song'`` and walking to terminal,
    the activity has at least one persisted activity_steps row with
    ``kind='reward'``. Uses the shipped 75-entry M7 song corpus + the
    music theme that every element_microgame template advertises.

    The song corpus loader needs the audio files to exist on disk for
    the L3 ``require_audio=True`` filter. The shipped manifest at
    ``data/songs/manifest.json`` has 19 ``music``-themed entries; the
    actual ``data/songs/audio/`` is operator-rendered + gitignored.
    For the smoke gate we copy the production manifest into
    ``TOYBOX_DATA_DIR`` and stage stub audio files for every music
    entry so the resolver's audio probe passes.
    """
    element_id, template_id = "au-79", "element_microgame_au_79"
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _seed_toys(db_path, [("toy_owl", "Wise Owl", None)])
    _seed_library_personas(db_path, tmp_path)
    _stage_music_songs_with_audio(tmp_path, monkeypatch)
    # Enable songs surface.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('songs_enabled', 'true') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
    finally:
        conn.close()

    proposed = _propose(
        client,
        parent_headers,
        intent="request_activity",
        seed=51,
        persona_id="periodic_table",
    )
    approved = _approve(
        client,
        parent_headers,
        proposed["id"],
        proposed["version"],
        reward_type="song",
    )
    _walk_with_fork_picks(
        client,
        parent_headers,
        proposed["id"],
        approved["version"],
        fork1_choice=0,
        fork2_choice=0,
    )

    rows = _fetch_step_rows(db_path, proposed["id"])
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) >= 1, (
        f"expected ≥1 activity_steps row with kind='reward' for "
        f"reward_type='song' + element_microgame {template_id!r} "
        f"(element_id={element_id!r}); got rows={rows!r}"
    )
    # The reward row's metadata should declare song-shaped fields per
    # Phase L L4 (reward_kind=song, reward_id pointing into the M7 corpus).
    reward_metadata = json.loads(reward_rows[0]["metadata_json"] or "{}")
    assert reward_metadata.get("reward_kind") == "song", (
        f"reward step metadata.reward_kind must be 'song'; "
        f"got {reward_metadata.get('reward_kind')!r}; "
        f"metadata={reward_metadata!r}"
    )
    reward_id = reward_metadata.get("reward_id")
    assert isinstance(reward_id, str) and reward_id, (
        f"reward step metadata.reward_id must be a non-empty string; got {reward_id!r}"
    )


def _stage_music_songs_with_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Copy the production song manifest into ``TOYBOX_DATA_DIR`` + stage
    stub audio files so the L3 resolver's ``require_audio`` filter
    passes. ALSO stages the production elements corpus under the same
    data root because the resolver path walks back through
    ``find_template_by_id → validate_template → get_element`` for every
    element_microgame step, and an unfound elements.json crashes the
    chain. Keeps the bytes byte-identical to production so any future
    manifest tweak surfaces here (real corpus, no synthesis).
    """
    src_manifest = Path(__file__).resolve().parents[2] / "data" / "songs" / "manifest.json"
    if not src_manifest.is_file():  # pragma: no cover -- shipped file
        raise AssertionError(
            f"production song manifest not found at {src_manifest}; Phase M M7 may have moved it"
        )
    songs_dir = tmp_path / "songs"
    audio_dir = songs_dir / "audio"
    songs_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(src_manifest, songs_dir / "manifest.json")
    manifest = json.loads((songs_dir / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest:
        audio_path = entry.get("audio_path", "")
        if not audio_path:
            continue
        full = songs_dir / audio_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"\x00" * 32)
    _stage_elements_corpus(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    song_corpus.clear_song_cache()
    joke_corpus.clear_joke_cache()
    element_corpus.clear_element_cache()


@pytest.mark.parametrize("element_id,template_id", _TARGET_ELEMENTS)
def test_reward_song_resolver_picks_a_song(
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    element_id: str,
    template_id: str,
) -> None:
    """Drive the L3 reward resolver directly with an
    element_microgame-shaped activity context (persona=periodic_table,
    template carries ``recommended_themes=["music"]``). The resolver
    must pick a song from the M7 corpus's 19 music-themed entries.

    This is the canonical "reward fires" assertion stripped of the
    Phase L wire-level two-phase advance ambiguity. The end-to-end
    test_reward_fires_after_terminal_advance above pins the wire path;
    THIS test pins the resolver contract.
    """
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _stage_music_songs_with_audio(tmp_path, monkeypatch)

    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('songs_enabled', 'true') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
        ctx = RewardActivityContext(
            id=f"n5-resolver-{element_id}",
            session_id=f"n5-resolver-session-{element_id}",
            persona_id="periodic_table",
            slot_fills_json=json.dumps({"__template_id": template_id}),
            current_step_count=4,
        )
        resolved = resolve_reward(conn, ctx, "song")
    finally:
        conn.close()
        song_corpus.clear_song_cache()
        joke_corpus.clear_joke_cache()

    assert resolved is not None, (
        f"L3 resolver returned None for element_microgame {template_id!r} "
        f"(element_id={element_id!r}) with reward_type='song' + "
        f"recommended_themes=['music']; the M7 corpus has 19 music-themed "
        f"songs so this should always land on one"
    )
    assert resolved.kind == "song", (
        f"resolver picked kind={resolved.kind!r}; expected 'song' "
        f"(reward_type was explicitly 'song')"
    )


@pytest.mark.parametrize("element_id,template_id", _TARGET_ELEMENTS)
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Spec gap surfaced by N5: plan §3 promises 'ending_step.element_id "
        "so Phase L's reward matcher picks the element-themed song' but "
        "(1) EndingStep has extra='ignore' so the field is silently dropped, "
        "(2) Song has extra='forbid' with NO element_id field, "
        "(3) the L3 resolver (_try_pick_song) matches songs by theme + "
        "persona_compat ONLY — never by element_id. As shipped there is "
        "no production path for 'element-themed song'. A future production "
        "fix that wires element_id-based song selection will flip this "
        "test green and force the spec to be reconciled. See report § "
        "spec-clarity flag for N5."
    ),
)
def test_reward_selects_element_themed_song_when_available(
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    element_id: str,
    template_id: str,
) -> None:
    """SPEC-GAP probe (xfail strict).

    The plan §3 + the N5 problem statement promise: when an element has
    a themed song in the M7 corpus (e.g. Hydrogen → some hydrogen-themed
    song), the L3 resolver picks that themed song over a generic. We
    stage the production song corpus + manually inject a synthetic
    "element-themed" song entry tagged with the activity's element_id
    (no real ``element_id`` field on Song today — we use the entry id
    as the carrier and expect the resolver to dispatch through it).

    On the SHIPPED behavior, the resolver picks by theme=music alone and
    has no way to disambiguate "this music song mentions Hydrogen" from
    "this music song is unrelated to Hydrogen". The xfail strict=True
    pins the gap; a future code change that adds element_id matching
    will flip this green and the marker will fire a spec-redacted alert.
    """
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    # Stage the production manifest + inject a synthetic "element-themed"
    # song whose id encodes the element_id. The resolver must surface
    # THIS entry (over the 19 generic music songs) for the property to
    # hold per spec.
    songs_dir = tmp_path / "songs"
    audio_dir = songs_dir / "audio"
    songs_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    src_manifest = Path(__file__).resolve().parents[2] / "data" / "songs" / "manifest.json"
    manifest = json.loads(src_manifest.read_text(encoding="utf-8"))
    themed_song_id = f"n5-themed-{element_id.replace('-', '_')}"
    themed_entry: dict[str, Any] = {
        "id": themed_song_id,
        "title": f"Themed song for {element_id}",
        "audio_path": f"audio/{themed_song_id}.mp3",
        "duration_seconds": 10,
        "theme": "music",
        "age_band": "3-5",
        "persona_compat": ["periodic_table", "all"],
        "license": "CC-BY-4.0",
        "credit": "N5 smoke fixture",
        "lyrics": f"This song is about element {element_id}.",
    }
    manifest.append(themed_entry)
    (songs_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for entry in manifest:
        audio_path = entry.get("audio_path", "")
        if not audio_path:
            continue
        full = songs_dir / audio_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"\x00" * 32)

    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    song_corpus.clear_song_cache()
    joke_corpus.clear_joke_cache()

    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('songs_enabled', 'true') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
        ctx = RewardActivityContext(
            id=f"n5-themed-{element_id}",
            session_id=f"n5-themed-session-{element_id}",
            persona_id="periodic_table",
            slot_fills_json=json.dumps({"__template_id": template_id}),
            current_step_count=4,
        )
        resolved = resolve_reward(conn, ctx, "song")
    finally:
        conn.close()
        song_corpus.clear_song_cache()
        joke_corpus.clear_joke_cache()

    assert resolved is not None, "resolver returned None despite staged song"
    assert resolved.reward_id == themed_song_id, (
        f"spec promise: when element_id={element_id!r} has a themed song "
        f"({themed_song_id!r}), the resolver picks it. Got "
        f"reward_id={resolved.reward_id!r} instead — the resolver picked "
        f"by theme=music alone with no element_id consideration."
    )


@pytest.mark.parametrize("element_id,template_id", _TARGET_ELEMENTS)
def test_reward_falls_back_to_any_song_when_no_themed_song_exists(
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    element_id: str,
    template_id: str,
) -> None:
    """Fallback case from the N5 spec: for an element WITHOUT a themed
    song, the resolver still fires + picks SOME song-corpus entry.

    Since the shipped corpus has NO element_id field on songs (sub-test
    (e3) flags this as a spec gap), every element falls through to "no
    themed song available" → the picker walks the corpus by theme=music
    and returns one of 19 music-themed entries. The assertion this
    test pins is the weaker "ANY song fires" version of the spec —
    which is what the shipped behavior actually delivers.
    """
    payload = _extract_template_payload("request_activity", template_id)
    _stage_single_template_dir(
        tmp_path,
        monkeypatch,
        intent="request_activity",
        template_payload=payload,
    )
    _stage_music_songs_with_audio(tmp_path, monkeypatch)

    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('songs_enabled', 'true') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
        ctx = RewardActivityContext(
            id=f"n5-fallback-{element_id}",
            session_id=f"n5-fallback-session-{element_id}",
            persona_id="periodic_table",
            slot_fills_json=json.dumps({"__template_id": template_id}),
            current_step_count=4,
        )
        resolved = resolve_reward(conn, ctx, "song")
    finally:
        conn.close()
        song_corpus.clear_song_cache()
        joke_corpus.clear_joke_cache()

    assert resolved is not None, (
        f"fallback path: even when no element-themed song exists for "
        f"{element_id!r}, the resolver MUST pick SOME song from the "
        f"M7 corpus's 19 music-themed entries (the templates declare "
        f"recommended_themes=['music'] so the theme filter narrows "
        f"to this pool)"
    )
    assert resolved.kind == "song", (
        f"resolver picked kind={resolved.kind!r} on fallback; expected "
        f"'song' (reward_type was 'song' so fallback chain shouldn't walk)"
    )
