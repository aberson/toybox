"""Generator must surface the picked toy's id on Activity.toy_ids.

The kiosk's ``ToyActionSprite`` resolves a sprite path from
``activity.toy_ids[0]`` + the per-step ``action_slot``. Today
:func:`toybox.activities.generator._pick_toy_name` returns only the
display name and the picked :class:`ResolvedToy.id` is dropped on the
floor, so every persisted activity carries ``toy_ids = NULL`` and the
sprite render path never fires. These tests pin the contract that
``generate(...)`` must thread the chosen toy's id onto
``Activity.toy_ids`` as a one-element tuple, and degrade to an empty
tuple when ``available_toys`` is empty.
"""

from __future__ import annotations

from toybox.activities import generate
from toybox.activities.content_resolver import ResolvedToy


def test_generate_populates_toy_ids_with_single_available_toy() -> None:
    """A single-toy ``available_toys`` collapses the pick — the generator
    must record THAT toy's id (not its display name, not nothing) on
    ``Activity.toy_ids`` so the kiosk sprite resolver can render."""
    only_toy = ResolvedToy(
        id="toy-abc-123",
        display_name="Bluey",
        tags=("dog",),
        last_used_at=None,
    )
    activity = generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=7,
        available_toys=(only_toy,),
    )
    # toy_ids is a tuple[str, ...] (matches metadata["slot_values"]'s
    # immutability convention), holding exactly the picked toy's id.
    assert activity.toy_ids == ("toy-abc-123",)


def test_generate_emits_empty_toy_ids_when_no_available_toys() -> None:
    """Empty catalog → empty ``toy_ids`` tuple. The kiosk's sprite path
    treats an empty tuple as "no sprite", which mirrors the pre-fix
    placeholder behavior — and crucially does NOT carry stale ids."""
    activity = generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=7,
        available_toys=(),
    )
    assert activity.toy_ids == ()


def test_generate_toy_ids_picks_from_provided_set() -> None:
    """With a multi-toy catalog and a fixed seed, the picked id MUST be
    one of the provided ids — never a fabricated value, never the
    display name. Belt-and-braces for accidental regressions where the
    field is populated but with the wrong value."""
    toys = (
        ResolvedToy(id="toy-1", display_name="Bluey", tags=(), last_used_at=None),
        ResolvedToy(id="toy-2", display_name="Bingo", tags=(), last_used_at=None),
        ResolvedToy(id="toy-3", display_name="Rex", tags=(), last_used_at=None),
    )
    activity = generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=42,
        available_toys=toys,
    )
    assert activity.toy_ids in {("toy-1",), ("toy-2",), ("toy-3",)}
