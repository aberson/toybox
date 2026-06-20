"""Phase W Step W5 — unit tests for the boss-fight CLIMAX beat engine.

Exercise the pure engine in :mod:`toybox.activities.adventure`'s boss path
with no network and no DB:

* offline ``generate_boss_beat`` is deterministic for a fixed seed and is
  stamped ``kind="boss_fight"``,
* the boss name (a cast boss-role toy display name) appears in the body /
  choices; ``None`` falls back to a generic boss descriptor (never crashes),
* linear → no choices (single resolution); non-linear → 2-3 choices,
* the online path parses a stubbed reply (stamped boss_fight) and degrades
  to the offline boss assembly on any failure.
"""

from __future__ import annotations

import json

from toybox.activities.adventure import (
    BOSS_FIGHT_KIND,
    GeneratedBeat,
    generate_boss_beat,
    parse_online_boss_beat,
)
from toybox.activities.generic_descriptors import GENERIC_DESCRIPTORS

CAST = ("Penguin", "Robot")
BOSS = "Lord Grumble"


def _offline_boss(
    history: tuple[str, ...],
    boss_name: str | None,
    *,
    beat_index: int,
    linear: bool,
    seed: int,
) -> GeneratedBeat:
    return generate_boss_beat(
        history,
        "",
        CAST,
        boss_name,
        online=False,
        beat_index=beat_index,
        linear=linear,
        seed=seed,
    )


def test_offline_boss_beat_is_deterministic_and_boss_kind() -> None:
    """Same inputs → byte-identical boss beat stamped kind=boss_fight."""
    a = _offline_boss((), BOSS, beat_index=5, linear=False, seed=7)
    b = _offline_boss((), BOSS, beat_index=5, linear=False, seed=7)
    assert a == b
    assert a.kind == BOSS_FIGHT_KIND


def test_offline_boss_beat_names_the_boss() -> None:
    """The resolved boss-role name appears in the body or its choices."""
    beat = _offline_boss((), BOSS, beat_index=5, linear=False, seed=11)
    rendered = beat.body + " ".join(beat.choices or ())
    assert BOSS in rendered


def test_offline_boss_beat_falls_back_to_generic_when_no_boss() -> None:
    """boss_name=None → generic boss descriptor; never crashes / empty."""
    beat = _offline_boss((), None, beat_index=5, linear=False, seed=11)
    rendered = beat.body + " ".join(beat.choices or ())
    assert GENERIC_DESCRIPTORS["big_bad_boss"] in rendered
    assert beat.body
    assert beat.kind == BOSS_FIGHT_KIND


def test_nonlinear_boss_beat_has_choices() -> None:
    beat = _offline_boss((), BOSS, beat_index=5, linear=False, seed=3)
    assert beat.choices is not None
    assert 2 <= len(beat.choices) <= 3


def test_linear_boss_beat_has_no_choices() -> None:
    beat = _offline_boss((), BOSS, beat_index=5, linear=True, seed=3)
    assert beat.choices is None
    assert beat.body
    assert beat.kind == BOSS_FIGHT_KIND


def test_boss_beat_varies_with_seed() -> None:
    a = _offline_boss((), BOSS, beat_index=5, linear=False, seed=1)
    b = _offline_boss((), BOSS, beat_index=5, linear=False, seed=999)
    # Different seeds drive different theme/openers — bodies should differ.
    assert a.body != b.body or a.choices != b.choices


def test_online_boss_beat_parses_and_is_boss_kind() -> None:
    """A valid stubbed reply parses into a boss_fight beat (online path)."""
    raw = json.dumps(
        {
            "body": "The boss appears!",
            "choices": ["Outsmart it", "Be brave"],
        }
    )

    def _call(_system: str, _user: str) -> str:
        return raw

    beat = generate_boss_beat(
        (),
        "",
        CAST,
        BOSS,
        online=True,
        beat_index=5,
        linear=False,
        seed=7,
        online_call=_call,
    )
    assert beat.kind == BOSS_FIGHT_KIND
    assert beat.body == "The boss appears!"
    assert beat.choices == ("Outsmart it", "Be brave")


def test_online_boss_degrades_to_offline_on_malformed_reply() -> None:
    """A malformed reply → deterministic offline boss assembly fallback."""

    def _bad_call(_system: str, _user: str) -> str:
        return "not json at all"

    online = generate_boss_beat(
        (),
        "",
        CAST,
        BOSS,
        online=True,
        beat_index=5,
        linear=False,
        seed=7,
        online_call=_bad_call,
    )
    offline = _offline_boss((), BOSS, beat_index=5, linear=False, seed=7)
    assert online == offline
    assert online.kind == BOSS_FIGHT_KIND


def test_online_boss_degrades_to_offline_on_raising_call() -> None:
    def _raise(_system: str, _user: str) -> str:
        raise RuntimeError("transport down")

    online = generate_boss_beat(
        (),
        "",
        CAST,
        BOSS,
        online=True,
        beat_index=5,
        linear=True,
        seed=42,
        online_call=_raise,
    )
    offline = _offline_boss((), BOSS, beat_index=5, linear=True, seed=42)
    assert online == offline


def test_parse_online_boss_beat_forces_kind() -> None:
    """parse_online_boss_beat stamps boss_fight even though the parse
    helper it reuses returns adventure_beat."""
    raw = json.dumps({"body": "boss!", "choices": ["a", "b"]})
    beat = parse_online_boss_beat(raw, linear=False)
    assert beat.kind == BOSS_FIGHT_KIND
