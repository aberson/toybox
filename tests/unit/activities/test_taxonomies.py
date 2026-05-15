"""Phase K K1 — taxonomy completeness + single-source-of-truth pinning.

These tests assert that:

* :class:`toybox.activities.roles.Role` covers exactly the 10 canonical
  role names from documentation/phase-k-plan.md §5.
* :class:`toybox.activities.themes.Theme` covers exactly the 12 theme
  names from §5.
* :class:`toybox.activities.interjections.InterjectionKind` covers
  exactly the 4 kinds from phase-k-plan §2's
  ``metadata.interjection`` wire shape.
* :data:`GENERIC_DESCRIPTORS` has a non-empty fallback for every Role.
* The roles.py + generic_descriptors.py modules export the SAME
  ``GENERIC_DESCRIPTORS`` object (``is`` equality, per
  code-quality.md §2 "One source of truth for data-shape constants").
* :data:`ROLE_DEFAULTS` and :data:`DEFAULT_ROLE_SPONTANEITY_RATES`
  alias the same object (``is`` equality).
* Each role has a per-content-type rate pair in [0.0, 1.0] matching
  the plan §5 numerics verbatim.

Per code-quality.md §2, tests MUST import the canonical lists from the
production module and assert their shape. NEVER hard-code a parallel
copy of the role list / theme list / interjection list here — that
would re-create the duplicate-source-of-truth bug the rule exists
to prevent. The constant we DO repeat (the plan §5 numerics) is the
acceptance assertion: tests pin the production module to the plan,
they don't define the values.
"""

from __future__ import annotations

from toybox.activities import generic_descriptors as gd_module
from toybox.activities import roles as roles_module
from toybox.activities.interjections import (
    INTERJECTION_DISPLAY_NAMES,
    InterjectionKind,
)
from toybox.activities.roles import (
    DEFAULT_ROLE_SPONTANEITY_RATES,
    GENERIC_DESCRIPTORS,
    ROLE_DEFAULTS,
    ROLE_DISPLAY_NAMES,
    Role,
)
from toybox.activities.themes import THEME_DISPLAY_NAMES, Theme

# ---------------------------------------------------------------------------
# Role taxonomy
# ---------------------------------------------------------------------------


def test_role_membership_is_exactly_ten_canonical_names() -> None:
    """Role StrEnum members match plan §5 verbatim (count + values)."""
    # Snake_case form of plan §5's "10 roles" list.
    expected = {
        "friend",
        "quest_giver",
        "guide_mentor",
        "needs_saving",
        "boss_mini_boss",
        "big_bad_boss",
        "frenemy",
        "sidekick",
        "trickster",
        "helper_townsperson",
    }
    actual = {r.value for r in Role}
    assert actual == expected, f"Role taxonomy drift: {actual ^ expected!r}"
    assert len(Role) == 10


def test_role_display_names_cover_all_roles() -> None:
    for role in Role:
        assert role in ROLE_DISPLAY_NAMES, f"missing display name for {role.value!r}"
        assert ROLE_DISPLAY_NAMES[role].strip(), f"empty display name for {role.value!r}"


def test_role_defaults_match_plan_section_5() -> None:
    """Per-role default spontaneity rates match plan §5 verbatim."""
    expected: dict[Role, dict[str, float]] = {
        Role.trickster: {"jokes_rate": 0.30, "songs_rate": 0.10},
        Role.frenemy: {"jokes_rate": 0.20, "songs_rate": 0.05},
        Role.sidekick: {"jokes_rate": 0.15, "songs_rate": 0.15},
        Role.needs_saving: {"jokes_rate": 0.10, "songs_rate": 0.20},
        Role.friend: {"jokes_rate": 0.10, "songs_rate": 0.10},
        Role.boss_mini_boss: {"jokes_rate": 0.10, "songs_rate": 0.00},
        Role.helper_townsperson: {"jokes_rate": 0.05, "songs_rate": 0.10},
        Role.quest_giver: {"jokes_rate": 0.05, "songs_rate": 0.10},
        Role.big_bad_boss: {"jokes_rate": 0.05, "songs_rate": 0.00},
        Role.guide_mentor: {"jokes_rate": 0.05, "songs_rate": 0.05},
    }
    assert set(ROLE_DEFAULTS.keys()) == set(expected.keys())
    for role, pair in expected.items():
        actual = ROLE_DEFAULTS[role]
        assert actual["jokes_rate"] == pair["jokes_rate"], role
        assert actual["songs_rate"] == pair["songs_rate"], role
        # Bounds invariant for the spontaneity engine.
        assert 0.0 <= actual["jokes_rate"] <= 1.0
        assert 0.0 <= actual["songs_rate"] <= 1.0


def test_default_role_spontaneity_rates_is_same_object_as_role_defaults() -> None:
    """code-quality.md §2: tests assert ``is``, not ``==``, so re-duplication fails CI."""
    assert DEFAULT_ROLE_SPONTANEITY_RATES is ROLE_DEFAULTS


# ---------------------------------------------------------------------------
# Generic-descriptor fallback table
# ---------------------------------------------------------------------------


def test_generic_descriptors_cover_every_role_non_empty() -> None:
    """Acceptance #9: for each of the 10 roles, GENERIC_DESCRIPTORS[role] is non-empty."""
    for role in Role:
        assert role.value in GENERIC_DESCRIPTORS, f"missing descriptor for {role.value!r}"
        descriptor = GENERIC_DESCRIPTORS[role.value]
        assert isinstance(descriptor, str)
        assert descriptor.strip(), f"empty descriptor for {role.value!r}"


def test_generic_descriptors_keyed_exactly_by_role_members() -> None:
    """No extra keys (would silently outlive a removed role) and no missing keys."""
    expected = {r.value for r in Role}
    assert set(GENERIC_DESCRIPTORS.keys()) == expected


def test_generic_descriptors_re_exported_via_roles_module_is_same_object() -> None:
    """Single source of truth — both import paths return the SAME object."""
    assert roles_module.GENERIC_DESCRIPTORS is gd_module.GENERIC_DESCRIPTORS


# ---------------------------------------------------------------------------
# Theme taxonomy
# ---------------------------------------------------------------------------


def test_theme_membership_is_exactly_twelve_canonical_names() -> None:
    """Theme StrEnum members match plan §5 / §1 verbatim."""
    expected = {
        "adventure",
        "magic",
        "space",
        "animals",
        "vehicles",
        "food",
        "friendship",
        "pirates",
        "knights",
        "weather",
        "music",
        "silly",
    }
    actual = {t.value for t in Theme}
    assert actual == expected, f"Theme taxonomy drift: {actual ^ expected!r}"
    assert len(Theme) == 12


def test_theme_display_names_cover_all_themes() -> None:
    for theme in Theme:
        assert theme in THEME_DISPLAY_NAMES, f"missing display name for {theme.value!r}"
        assert THEME_DISPLAY_NAMES[theme].strip(), f"empty display name for {theme.value!r}"


# ---------------------------------------------------------------------------
# InterjectionKind taxonomy
# ---------------------------------------------------------------------------


def test_interjection_kind_membership_is_exactly_four_canonical_names() -> None:
    """4 interjection kinds match plan §2's metadata.interjection wire shape."""
    expected = {"embedded", "ending", "parent", "spontaneity"}
    actual = {k.value for k in InterjectionKind}
    assert actual == expected, f"InterjectionKind taxonomy drift: {actual ^ expected!r}"
    assert len(InterjectionKind) == 4


def test_interjection_display_names_cover_all_kinds() -> None:
    for kind in InterjectionKind:
        assert kind in INTERJECTION_DISPLAY_NAMES, f"missing display name for {kind.value!r}"
        assert INTERJECTION_DISPLAY_NAMES[kind].strip(), f"empty for {kind.value!r}"
