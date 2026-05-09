"""Pin :class:`toybox.activities.models.Activity.toy_ids` shape.

The kiosk sprite resolver reads ``activity.toy_ids[0]``. Today the
``Activity`` model has no ``toy_ids`` field at all, so the generator
has nowhere to thread the picked toy's id and the propose-time
persistence layer hardcodes ``toy_ids = NULL`` in its INSERT. These
tests pin the model contract: ``toy_ids`` is a ``tuple[str, ...]``,
defaults to ``()``, and round-trips through the constructor.
"""

from __future__ import annotations

from toybox.activities.models import Activity, ActivityStep


def _five_steps() -> list[ActivityStep]:
    """Activity requires exactly 5 ActivityStep entries (min/max=5)."""
    return [
        ActivityStep(step_index=i, text=f"step {i}", sfx=None, expected_action=None)
        for i in range(5)
    ]


def test_activity_toy_ids_defaults_to_empty_tuple() -> None:
    """Omitting ``toy_ids`` yields ``()`` — empty, immutable, JSON-safe."""
    activity = Activity(
        id="00000000-0000-4000-8000-000000000000",
        template_id="t1",
        persona_id=None,
        title="title",
        steps=_five_steps(),
        version=1,
        metadata={},
    )
    assert activity.toy_ids == ()


def test_activity_accepts_toy_ids_kwarg() -> None:
    """The constructor accepts ``toy_ids`` as a tuple of strings and the
    field round-trips. This is the load-bearing assertion: until the
    fix lands, Pydantic raises ``ValidationError`` (extra-field-forbid)
    or ``TypeError`` because no such field exists on the frozen model."""
    activity = Activity(
        id="00000000-0000-4000-8000-000000000001",
        template_id="t1",
        persona_id=None,
        title="title",
        steps=_five_steps(),
        version=1,
        metadata={},
        toy_ids=("toy-abc",),
    )
    assert activity.toy_ids == ("toy-abc",)


