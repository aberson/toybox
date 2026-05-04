"""Unit tests for the parametric slot registry.

The registry is the contract that template authors and the generator
share — these tests pin (a) every named slot returns a value from the
documented vocabulary, (b) catalog-backed slots fall back to the
documented sentinels when empty, (c) unknown slots echo a placeholder
rather than crash, (d) RNG consumption is deterministic per seed, and
(e) the signature-contributing whitelist is exactly the documented
set so a future addition forces a deliberate decision about feedback
granularity.
"""

from __future__ import annotations

import random

import pytest

from toybox.activities.content_resolver import ResolvedRoom
from toybox.activities.slots import (
    ACTION_VERBS,
    ADJECTIVES,
    BODY_PARTS,
    COUNTS,
    DEFAULT_ROOM_NAME,
    KNOWN_SLOTS,
    PROPS,
    SIGNATURE_CONTRIBUTING_SLOTS,
    SlotRegistry,
)


def _registry(rooms: tuple[ResolvedRoom, ...] = ()) -> SlotRegistry:
    return SlotRegistry.from_resolved(rooms)


class TestRegistryFill:
    def test_action_verb_returns_value_from_list(self) -> None:
        reg = _registry()
        v = reg.fill("action_verb", random.Random(0))
        assert v in ACTION_VERBS

    def test_adjective_returns_value_from_list(self) -> None:
        reg = _registry()
        v = reg.fill("adjective", random.Random(0))
        assert v in ADJECTIVES

    def test_prop_returns_value_from_list(self) -> None:
        reg = _registry()
        v = reg.fill("prop", random.Random(0))
        assert v in PROPS

    def test_body_part_returns_value_from_list(self) -> None:
        reg = _registry()
        v = reg.fill("body_part", random.Random(0))
        assert v in BODY_PARTS

    def test_count_returns_value_from_list(self) -> None:
        reg = _registry()
        v = reg.fill("count", random.Random(0))
        assert v in COUNTS

    def test_room_returns_catalog_name(self) -> None:
        rooms = (
            ResolvedRoom(id="r1", display_name="Kitchen", features=()),
            ResolvedRoom(id="r2", display_name="Living Room", features=()),
        )
        reg = _registry(rooms)
        v = reg.fill("room", random.Random(0))
        assert v in {"Kitchen", "Living Room"}

    def test_room_falls_back_when_catalog_empty(self) -> None:
        reg = _registry(())
        v = reg.fill("room", random.Random(0))
        assert v == DEFAULT_ROOM_NAME

    def test_unknown_slot_echoes_placeholder(self) -> None:
        """Typo-in-template should surface visibly rather than crash."""
        reg = _registry()
        v = reg.fill("nonsense_slot", random.Random(0))
        assert v == "{nonsense_slot}"


class TestDeterminism:
    def test_same_seed_same_value(self) -> None:
        reg = _registry()
        a = reg.fill("action_verb", random.Random(42))
        b = reg.fill("action_verb", random.Random(42))
        assert a == b

    def test_different_seeds_eventually_differ(self) -> None:
        """Across many seeds, the registry must produce real variety
        (not always the same word)."""
        reg = _registry()
        values = {reg.fill("adjective", random.Random(s)) for s in range(50)}
        # At least 5 distinct values across 50 seeds — pinning that
        # the registry isn't accidentally collapsed to a constant.
        assert len(values) >= 5, values

    def test_room_pick_deterministic(self) -> None:
        rooms = (
            ResolvedRoom(id="r1", display_name="Kitchen", features=()),
            ResolvedRoom(id="r2", display_name="Living Room", features=()),
            ResolvedRoom(id="r3", display_name="Bedroom", features=()),
        )
        reg = _registry(rooms)
        a = reg.fill("room", random.Random(7))
        b = reg.fill("room", random.Random(7))
        assert a == b


class TestContract:
    def test_known_slots_matches_fill_branches(self) -> None:
        """``KNOWN_SLOTS`` is the authoring contract; every name in it
        must produce a real value from the registry (not the
        unknown-slot placeholder echo)."""
        reg = _registry(
            (ResolvedRoom(id="r", display_name="Kitchen", features=()),),
        )
        for name in KNOWN_SLOTS:
            v = reg.fill(name, random.Random(0))
            assert v != f"{{{name}}}", f"slot {name!r} echoed placeholder"

    def test_signature_contributing_slots_is_documented_set(self) -> None:
        """The signature whitelist directly affects feedback aggregation
        — pin it so any future change forces a deliberate decision and
        a docstring update."""
        assert SIGNATURE_CONTRIBUTING_SLOTS == frozenset({"toy", "slot"})

    def test_word_lists_are_non_empty(self) -> None:
        """Empty list would crash ``rng.choice``."""
        for name, lst in [
            ("ACTION_VERBS", ACTION_VERBS),
            ("ADJECTIVES", ADJECTIVES),
            ("PROPS", PROPS),
            ("BODY_PARTS", BODY_PARTS),
            ("COUNTS", COUNTS),
        ]:
            assert len(lst) > 0, f"{name} is empty"


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 999])
def test_full_round_trip_deterministic(seed: int) -> None:
    """Calling fill across multiple slots in sequence with the same
    seed must produce the same tuple every time. This is the
    invariant the generator depends on for byte-identical output
    given a fixed seed."""
    rooms = (
        ResolvedRoom(id="r1", display_name="Kitchen", features=()),
    )
    reg = _registry(rooms)
    rng_a = random.Random(seed)
    rng_b = random.Random(seed)
    seq_a = tuple(
        reg.fill(s, rng_a)
        for s in ("room", "action_verb", "adjective", "prop", "body_part", "count")
    )
    seq_b = tuple(
        reg.fill(s, rng_b)
        for s in ("room", "action_verb", "adjective", "prop", "body_part", "count")
    )
    assert seq_a == seq_b
