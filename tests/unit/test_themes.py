"""Phase M Step M8 — theme taxonomy invariants.

Locks the ``Theme`` enum's identity (``is`` not ``==``), display-name
round-trip, and completeness so that future additions/removals must
update both this file AND the JSON-schema mirror in
``src/toybox/activities/templates/_schema.json``.

Per ``.claude/rules/code-quality.md`` §2: data-shape constants have one
source of truth (``toybox.activities.themes``) and regression tests
assert identity so a silent duplicate definition elsewhere fails CI.
"""

from __future__ import annotations

from toybox.activities.themes import THEME_DISPLAY_NAMES, Theme


def test_feelings_member_identity() -> None:
    """``Theme("feelings")`` must coerce to the same enum singleton.

    ``is`` (not ``==``) per code-quality §2: a future duplicate
    definition with the same string value would still ``==`` but fail
    ``is`` — making the regression visible.
    """
    assert Theme("feelings") is Theme.feelings


def test_feelings_display_name_round_trip() -> None:
    """The display map must carry a title-cased entry for the new value."""
    assert THEME_DISPLAY_NAMES[Theme.feelings] == "Feelings"


def test_theme_membership_is_complete() -> None:
    """Membership snapshot — fails loudly if anyone adds/removes a theme.

    The snapshot is sorted (set equality) so order changes inside
    ``Theme`` don't false-alarm; new members must update this set,
    deletions force a deliberate edit here, and that edit prompts a
    grep audit against every consumer (per code-quality §1).
    """
    assert set(Theme) == {
        Theme.adventure,
        Theme.magic,
        Theme.space,
        Theme.animals,
        Theme.vehicles,
        Theme.food,
        Theme.friendship,
        Theme.pirates,
        Theme.knights,
        Theme.weather,
        Theme.music,
        Theme.silly,
        Theme.feelings,
    }


def test_display_names_cover_every_member() -> None:
    """Every ``Theme`` member must have an entry in ``THEME_DISPLAY_NAMES``.

    The parent UI keys off this dict; a missing entry would render as a
    raw lowercase value or KeyError depending on the call site. The
    completeness check above guarantees the set; this guarantees the
    dict is in sync with the set.
    """
    assert set(THEME_DISPLAY_NAMES.keys()) == set(Theme)
