"""Unit coverage for :func:`toybox.api.activities._select_boss_name`.

Phase W Step W5: the boss-fight beat casts a boss-role toy from the
adventure cast. The protagonist/hero is ``cast[0]`` (the first toy with a
display name) and is NEVER cast as its own boss. Among the non-hero cast a
three-tier preference applies — an EXPLICIT ``big_bad_boss`` tag, then an
EXPLICIT ``boss_mini_boss`` tag, then a soft-fallback unrestricted
(``allowed_roles == ()``) toy — so an explicit tag genuinely wins over an
untagged toy even when the untagged toy sorts first. ``None`` falls back to
a generic boss descriptor inside the engine. This pure helper picks the
name; covered here without a DB.
"""

from __future__ import annotations

from toybox.activities.content_resolver import ResolvedToy
from toybox.api.activities import _select_boss_name


def _toy(toy_id: str, name: str, allowed: tuple[str, ...]) -> ResolvedToy:
    return ResolvedToy(id=toy_id, display_name=name, allowed_roles=allowed)


def test_prefers_big_bad_boss() -> None:
    toys = [
        _toy("hero", "Hero", ("friend",)),
        _toy("a", "Mini", ("boss_mini_boss",)),
        _toy("b", "BigBad", ("big_bad_boss",)),
    ]
    assert _select_boss_name(toys) == "BigBad"


def test_falls_back_to_mini_boss() -> None:
    toys = [
        _toy("hero", "Hero", ("friend",)),
        _toy("a", "Friend", ("friend",)),
        _toy("b", "Mini", ("boss_mini_boss",)),
    ]
    assert _select_boss_name(toys) == "Mini"


def test_explicit_tag_beats_unrestricted_that_sorts_first() -> None:
    """MEDIUM-1 lock: an unrestricted non-hero toy that sorts AHEAD of an
    explicitly-tagged boss must NOT win — the tag takes precedence."""
    toys = [
        _toy("hero", "Hero", ()),
        # Unrestricted, listed BEFORE the tagged boss (sort-order ahead).
        _toy("a", "Anyone", ()),
        _toy("b", "BigBad", ("big_bad_boss",)),
    ]
    assert _select_boss_name(toys) == "BigBad"


def test_big_bad_boss_preferred_over_mini_boss() -> None:
    """big_bad_boss tier wins over boss_mini_boss even when mini sorts first."""
    toys = [
        _toy("hero", "Hero", ()),
        _toy("a", "Mini", ("boss_mini_boss",)),
        _toy("b", "BigBad", ("big_bad_boss",)),
    ]
    assert _select_boss_name(toys) == "BigBad"


def test_hero_never_selected_even_if_unrestricted_and_first() -> None:
    """The hero (cast[0]) is excluded from boss candidacy. With only the
    hero present (unrestricted, sorts first) there is no eligible boss."""
    toys = [_toy("hero", "Hero", ())]
    assert _select_boss_name(toys) is None


def test_unrestricted_non_hero_soft_fallback() -> None:
    """Only unrestricted non-hero toys present → one is chosen (Phase K
    soft-fallback), and it is the first non-hero in resolver order."""
    toys = [
        _toy("hero", "Hero", ()),
        _toy("a", "FirstNonHero", ()),
        _toy("b", "SecondNonHero", ()),
    ]
    assert _select_boss_name(toys) == "FirstNonHero"


def test_returns_none_when_no_boss_eligible() -> None:
    """Non-hero toys all carry restrictions excluding the boss roles."""
    toys = [
        _toy("hero", "Hero", ()),
        _toy("a", "Friend", ("friend",)),
        _toy("b", "Sidekick", ("sidekick",)),
    ]
    assert _select_boss_name(toys) is None


def test_returns_none_for_empty_cast() -> None:
    assert _select_boss_name([]) is None


def test_first_match_in_tier_wins() -> None:
    """Within a tier the resolver's order is stable across a replay."""
    toys = [
        _toy("hero", "Hero", ()),
        _toy("a", "BossOne", ("big_bad_boss",)),
        _toy("b", "BossTwo", ("big_bad_boss",)),
    ]
    assert _select_boss_name(toys) == "BossOne"


def test_toy_without_display_name_is_not_the_hero_slot() -> None:
    """A nameless leading toy is skipped entirely (cast filters it out), so
    the first NAMED toy is the hero — not the nameless row."""
    toys = [
        _toy("z", "", ()),  # filtered: no display name
        _toy("hero", "Hero", ()),  # this is the hero (cast[0])
        _toy("b", "BigBad", ("big_bad_boss",)),
    ]
    assert _select_boss_name(toys) == "BigBad"
