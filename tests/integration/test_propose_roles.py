"""Phase K K5 — propose path wires the role slot-fill engine end-to-end.

This is the integration test required by ``.claude/rules/code-quality.md``
§4 "New components require an integration test through the production
caller". K4 shipped ``resolve_role_slots`` + ``GenericDescriptor`` as
a unit-tested module; K5 wires it into ``_do_propose`` so role-bearing
templates get their ``{role_name}`` placeholders resolved + the cast
list surfaces on the propose response.

The test exercises the FULL production caller (``POST /api/activities/propose``)
to catch the silent-wiring failure mode the rule warns about — calling
``resolve_role_slots`` directly would re-verify K4's correctness but
would NOT verify that the K5 wire-up actually invokes the engine from
inside the propose handler.

Determinism contract: the test pins the persona's ``role_weights`` so
both the seeded RNG inside ``resolve_role_slots`` AND the alphabetical
tie-break (per ``_pick_weighted``) produce a byte-identical cast every
run. Toy display-names are picked so ``role_weights[quest_giver] = 2.0``
biases the first-sorted-id toy into ``quest_giver`` and the next-sorted-
id toy fills ``friend``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.db.connection import connect

# Pin the propose request so seed + hour are stable across reruns. The
# specific intent matches the role-required fixture file (``boredom.json``).
_PROPOSE_BODY: dict[str, Any] = {
    "intent": "boredom",
    "slot": None,
    "hour": 12,
    "seed": 99,
    "persona_id": "role_weighted_fixture",
}


# Fixture persona id matches the JSON under tests/fixtures/personas/.
_PERSONA_ID = "role_weighted_fixture"


@pytest.fixture
def role_template_dir(tmp_path: Path) -> Path:
    """Stage a templates directory whose only ``boredom.json`` is the
    role-required fixture. The seeded picker MUST land on the single
    eligible template so the test is robust against future template
    additions.
    """
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "activities"
        / "branching"
        / "role_required_quest.json"
    )
    payload = fixture.read_text(encoding="utf-8")
    staged = tmp_path / "templates_role_required"
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


def _seed_toys(db_path: Path) -> list[tuple[str, str]]:
    """Insert four toys with predictable id ordering.

    Returns the seeded ``[(toy_id, display_name)]`` list sorted by id
    (the same order the K4 picker sees after its internal sort). The
    first id ("toy_a_alpha") gets the role-weight bias for the first
    role processed in name-sorted order; the second id ("toy_b_bear")
    gets the bias for the second.
    """
    toys = [
        ("toy_a_alpha", "Alpha Owl"),
        ("toy_b_bear", "Captain Bear"),
        ("toy_c_cat", "Curious Cat"),
        ("toy_d_duck", "Dapper Duck"),
    ]
    conn = connect(db_path)
    try:
        with conn:
            for toy_id, display_name in toys:
                conn.execute(
                    "INSERT INTO toys "
                    "(id, display_name, image_path, image_hash, type, tags, "
                    " persona_id, archived, created_at, last_used_at) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
                    " '2026-01-01T00:00:00Z', NULL)",
                    (toy_id, display_name, f"img/{toy_id}.png", f"hash-{toy_id}"),
                )
    finally:
        conn.close()
    return toys


def _seed_role_weighted_persona(db_path: Path) -> None:
    """Insert the fixture persona row with explicit role_weights JSON.

    The K1 migration (0014) added ``role_weights`` / ``voice_profile`` /
    ``spontaneity_rates`` columns to ``personas``; we INSERT directly
    here (rather than via the library loader) so the test stays
    self-contained — no avatar PNG, no library directory copy.
    """
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "personas" / "role_weighted.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    role_weights_json = json.dumps(payload["role_weights"], sort_keys=True, separators=(",", ":"))
    spontaneity_rates_json = json.dumps(
        payload["spontaneity_rates"], sort_keys=True, separators=(",", ":")
    )
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO personas "
                "(id, display_name, archetype, system_prompt, avatar_image_path, "
                " behavior_tags, age_range_min, age_range_max, language, source, "
                " default_voice_tone, created_at, role_weights, voice_profile, "
                " spontaneity_rates) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["id"],
                    payload["display_name"],
                    payload["archetype"],
                    payload["system_prompt"],
                    payload["avatar_image_path"],
                    json.dumps(payload["behavior_tags"]),
                    payload["age_range_min"],
                    payload["age_range_max"],
                    payload["language"],
                    payload["source"],
                    payload["default_voice_tone"],
                    "2026-05-15T00:00:00Z",
                    role_weights_json,
                    None,  # voice_profile null
                    spontaneity_rates_json,
                ),
            )
    finally:
        conn.close()


def test_propose_wires_role_slot_engine_end_to_end(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    role_template_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase K K5 done-when: propose populates ``ActivityResponse.roles``,
    renders ``{role_name}`` placeholders in step bodies, and persists
    the role-name keys onto ``activities.slot_fills_json``.

    Code-quality.md §4 compliance: this test exercises the production
    caller (``POST /api/activities/propose``) so a silent wire-up
    regression (e.g. a future refactor that drops the K5 hook from
    ``_do_propose``) fails here.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", role_template_dir)
    generator.clear_template_cache()

    seeded_toys = _seed_toys(db_path)
    _seed_role_weighted_persona(db_path)

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    # ----- (a) response.roles is a dict with the 2 declared role keys -----
    roles = body.get("roles")
    assert isinstance(roles, dict), f"response.roles must be a dict, got {type(roles).__name__}"
    assert set(roles.keys()) == {"quest_giver", "friend"}, (
        f"expected roles to be exactly {{quest_giver, friend}}, got {sorted(roles.keys())!r}"
    )

    # ----- (b) each RoleAssignment carries a real toy_id + display_name --
    # With 4 toys seeded and 2 required roles, both roles MUST be filled
    # by real catalog toys (the K4 eligibility gate would have returned
    # None had the pool been too small). ``role_weights`` ∈ [0.0, 2.0]
    # is a soft bias, not a deterministic forcing function — we assert
    # the SHAPE invariants (real toy id, no generic descriptor, distinct
    # toys per role, display_name mirrors the catalog) rather than the
    # specific id picked, since the RNG draw across two roles can land
    # on any of the 4 toys in either slot.
    toy_id_to_name = dict(seeded_toys)
    for role_name, assignment in roles.items():
        assert isinstance(assignment, dict)
        # Real-toy branch — neither role should fall back to a generic
        # descriptor since the pool (4 toys) >> required_roles (2).
        toy_id = assignment.get("toy_id")
        assert toy_id is not None, f"role {role_name!r} should have a real toy id"
        assert assignment.get("generic_descriptor") is None
        assert toy_id in toy_id_to_name, f"role {role_name!r} toy_id {toy_id!r} not in seeded pool"
        assert assignment.get("display_name") == toy_id_to_name[toy_id]
        assert assignment.get("role_name") == role_name

    friend_toy_id = roles["friend"]["toy_id"]
    quest_giver_toy_id = roles["quest_giver"]["toy_id"]
    # The two roles must consume distinct toys (no toy in two roles).
    assert friend_toy_id != quest_giver_toy_id, (
        "K4 picker must not assign the same toy to two roles in one cast"
    )

    # ----- (c) cast_summary is the sorted, formatted, role-display-name list -
    cast_summary = body.get("cast_summary")
    assert isinstance(cast_summary, str) and cast_summary, (
        f"cast_summary must be a non-empty string, got {cast_summary!r}"
    )
    # Build the expected summary from the actual resolved cast — the
    # format is deterministic ("Friend: <name>, Quest Giver: <name>")
    # regardless of which specific toy each role landed on.
    expected_summary = (
        f"Friend: {toy_id_to_name[friend_toy_id]}, "
        f"Quest Giver: {toy_id_to_name[quest_giver_toy_id]}"
    )
    assert cast_summary == expected_summary, (
        f"cast_summary should be the sorted, formatted cast list; "
        f"got {cast_summary!r}, expected {expected_summary!r}"
    )

    # ----- (d) step bodies contain the substituted toy display names ------
    # The fixture template references ``{quest_giver}`` and ``{friend}``
    # in every step. ``render_with_slot_fills`` runs at K5 propose time
    # — if the wire-up dropped the merge, the bodies would still carry
    # literal ``{quest_giver}`` / ``{friend}`` placeholders.
    friend_display = toy_id_to_name[friend_toy_id]
    quest_giver_display = toy_id_to_name[quest_giver_toy_id]
    steps = body.get("steps") or []
    assert steps, "propose response must include rendered steps"
    rendered_text = " | ".join(str(s.get("body", "")) for s in steps)
    assert friend_display in rendered_text, (
        f"step bodies should contain the resolved 'friend' display name "
        f"{friend_display!r}; got {rendered_text!r}"
    )
    assert quest_giver_display in rendered_text, (
        f"step bodies should contain the resolved 'quest_giver' display name "
        f"{quest_giver_display!r}; got {rendered_text!r}"
    )
    # No unresolved role placeholders should leak.
    assert "{quest_giver}" not in rendered_text
    assert "{friend}" not in rendered_text

    # ----- (e) activities.slot_fills_json carries role-name keys ----------
    activity_id = body["id"]
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT slot_fills_json FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    persisted = json.loads(row["slot_fills_json"])
    assert isinstance(persisted, dict)
    # Pre-existing legacy keys (toy / room / action_verb / adjective) may
    # also be present from the generator's earlier pass — we only assert
    # the K5 contract: role-name keys exist with the resolved values.
    assert persisted.get("friend") == friend_display, (
        f"slot_fills_json.friend should equal the resolved friend display name "
        f"{friend_display!r}; got {persisted.get('friend')!r}"
    )
    assert persisted.get("quest_giver") == quest_giver_display, (
        f"slot_fills_json.quest_giver should equal the resolved quest_giver display name "
        f"{quest_giver_display!r}; got {persisted.get('quest_giver')!r}"
    )

    generator.clear_template_cache()


# Issue #135: title leaked literal {role_name} placeholders because the
# propose path re-rendered step bodies + choice labels with the merged
# role_slot_overlay but never re-rendered the title. This regression test
# pins the fix end-to-end through the production endpoint.
_PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")


def test_propose_substitutes_role_placeholder_in_title(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    role_template_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #135 regression: ``response["title"]`` must NOT contain a
    literal ``{role_name}`` placeholder after propose merges the
    role-slot overlay. Resolved role display names must appear verbatim
    in the rendered title.

    Fixture template title: ``"A quest for {quest_giver} and {friend}"``
    — both role placeholders MUST resolve to picked-toy display names.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", role_template_dir)
    generator.clear_template_cache()

    _seed_toys(db_path)
    _seed_role_weighted_persona(db_path)

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    title = body.get("title")
    assert isinstance(title, str) and title, (
        f"propose response must have a non-empty title; got {title!r}"
    )

    # ----- (a) no literal {role_name} placeholders leak -----
    leftover = _PLACEHOLDER_RE.search(title)
    assert leftover is None, (
        f"propose response title still contains a literal placeholder "
        f"{leftover.group(0) if leftover else None!r} (issue #135 regression): {title!r}"
    )

    # ----- (b) each resolved role display name appears verbatim in title -
    roles = body.get("roles") or {}
    assert set(roles.keys()) == {"quest_giver", "friend"}, (
        f"propose must seed both roles; got {sorted(roles.keys())!r}"
    )
    for role_name, assignment in roles.items():
        display_name = assignment.get("display_name")
        assert isinstance(display_name, str) and display_name, (
            f"role {role_name!r} must have a non-empty display_name; got {display_name!r}"
        )
        assert display_name in title, (
            f"role {role_name!r} display_name {display_name!r} should appear "
            f"verbatim in title {title!r}"
        )

    generator.clear_template_cache()


# ---------------------------------------------------------------------
# Per-toy role restrictions (migration 0017): end-to-end through propose
# ---------------------------------------------------------------------


def _seed_role_restricted_toys(db_path: Path) -> list[tuple[str, str, str | None]]:
    """Insert toys whose ``allowed_roles`` cover both restricted + open.

    Returns ``[(toy_id, display_name, allowed_roles_json | None)]``.
    The restricted toy is keyed FIRST in id-sort order so the K4 picker
    sees it as the primary candidate for the role-weight bias path.
    """
    toys: list[tuple[str, str, str | None]] = [
        # Bowser: restricted to a role the fixture template does NOT
        # require (quest_giver / friend are the requirements). Without
        # the per-toy restriction wire-up, Bowser would be a viable
        # candidate for both required roles.
        ("a_bowser", "Bowser", '["big_bad_boss"]'),
        # Three unrestricted toys so the picker can fill quest_giver +
        # friend without falling back.
        ("b_owl", "Wise Owl", None),
        ("c_cat", "Clever Cat", None),
        ("d_duck", "Daring Duck", None),
    ]
    conn = connect(db_path)
    try:
        with conn:
            for toy_id, display_name, allowed_json in toys:
                conn.execute(
                    "INSERT INTO toys "
                    "(id, display_name, image_path, image_hash, type, tags, "
                    " persona_id, archived, created_at, last_used_at, allowed_roles) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
                    " '2026-01-01T00:00:00Z', NULL, ?)",
                    (
                        toy_id,
                        display_name,
                        f"img/{toy_id}.png",
                        f"hash-{toy_id}",
                        allowed_json,
                    ),
                )
    finally:
        conn.close()
    return toys


def test_propose_excludes_role_restricted_toy_from_non_allowed_slot(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    role_template_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production-caller integration test for the per-toy role restriction.

    Setup: 4 toys — Bowser restricted to ``big_bad_boss``; the others
    unrestricted. Template requires ``quest_giver`` + ``friend``.
    Bowser MUST NOT be cast into either slot — the picker should
    prefer one of the unrestricted toys for both roles. This catches
    the silent-wiring failure mode where the filter never reaches the
    picker from the production propose path (code-quality.md §4).
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", role_template_dir)
    generator.clear_template_cache()

    seeded = _seed_role_restricted_toys(db_path)
    _seed_role_weighted_persona(db_path)

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    roles = body.get("roles") or {}
    assert set(roles.keys()) == {"quest_giver", "friend"}, (
        f"propose must seed both roles; got {sorted(roles.keys())!r}"
    )

    restricted_id = seeded[0][0]  # Bowser
    unrestricted_ids = {t[0] for t in seeded[1:]}

    for role_name, assignment in roles.items():
        toy_id = assignment.get("toy_id")
        assert toy_id is not None, f"role {role_name!r} must have a toy_id"
        assert toy_id != restricted_id, (
            f"per-toy role restriction failed: Bowser (restricted to big_bad_boss) "
            f"was cast as {role_name!r}; allowed_roles filter never reached the "
            f"picker from the production propose path"
        )
        assert toy_id in unrestricted_ids, (
            f"role {role_name!r} toy_id {toy_id!r} unexpected — expected one of "
            f"the three unrestricted toys"
        )

    generator.clear_template_cache()


def test_propose_casts_restricted_toy_for_its_allowed_role(
    client: TestClient,
    parent_headers: dict[str, str],
    tmp_path: Path,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror of the above but with a template whose required_role IS
    the toy's allowed role — the restricted toy SHOULD be cast.

    Builds a one-off boredom template that requires ``big_bad_boss``
    only, seeds Bowser (restricted to big_bad_boss) as the sole toy,
    and asserts Bowser fills the slot. The point is to lock that the
    filter doesn't accidentally exclude eligible toys.
    """
    from toybox.activities import generator

    # Build a custom fixture: required role = ``big_bad_boss`` only.
    custom_template = {
        "intent": "boredom",
        "templates": [
            {
                "id": "fixture_role_required_big_bad_boss",
                "title": "{big_bad_boss} stomps in.",
                "buckets": ["always"],
                "required_roles": ["big_bad_boss"],
                "optional_roles": [],
                "steps": [
                    {"text": "{big_bad_boss} arrives and grumbles dramatically."},
                    {"text": "{big_bad_boss} declares a friendly competition."},
                    {"text": "Everyone cheers as {big_bad_boss} laughs along."},
                ],
            }
        ],
    }
    staged = tmp_path / "templates_big_bad_boss"
    staged.mkdir()
    (staged / "boredom.json").write_text(json.dumps(custom_template, indent=2), encoding="utf-8")
    src_schema = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "toybox"
        / "activities"
        / "templates"
        / "_schema.json"
    )
    import shutil  # local import — only this branch needs it.

    shutil.copy(src_schema, staged / "_schema.json")

    monkeypatch.setattr(generator, "TEMPLATES_DIR", staged)
    generator.clear_template_cache()

    # Seed Bowser only — restricted to big_bad_boss; the template's
    # required role IS big_bad_boss, so the filter keeps Bowser in.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO toys "
                "(id, display_name, image_path, image_hash, type, tags, "
                " persona_id, archived, created_at, last_used_at, allowed_roles) "
                "VALUES ('a_bowser', 'Bowser', 'img/bowser.png', 'hash-bowser', "
                " NULL, NULL, NULL, 0, '2026-01-01T00:00:00Z', NULL, "
                " '[\"big_bad_boss\"]')"
            )
    finally:
        conn.close()
    _seed_role_weighted_persona(db_path)

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    roles = body.get("roles") or {}
    assert set(roles.keys()) == {"big_bad_boss"}, (
        f"propose must seed only the big_bad_boss role; got {sorted(roles.keys())!r}"
    )
    assignment = roles["big_bad_boss"]
    assert assignment.get("toy_id") == "a_bowser", (
        f"the restricted Bowser SHOULD be cast as big_bad_boss; got {assignment!r}"
    )

    generator.clear_template_cache()
