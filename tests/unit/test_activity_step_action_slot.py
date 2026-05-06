"""Phase F Step F6 — :class:`ActivityStep.action_slot` validator coverage.

Pins:

* ``action_slot=None`` is accepted (default; pre-F6 + missing-from-output
  behavior).
* Every member of :data:`ACTION_SLOTS` is accepted as a valid value.
* Out-of-vocabulary strings raise :class:`pydantic.ValidationError`.
* Non-string scalar types are rejected.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from toybox.activities.models import ActivityStep
from toybox.image_gen.models import ACTION_SLOTS


def test_action_slot_default_none() -> None:
    step = ActivityStep(step_index=0, text="hi")
    assert step.action_slot is None


def test_action_slot_explicit_none_accepted() -> None:
    step = ActivityStep(step_index=0, text="hi", action_slot=None)
    assert step.action_slot is None


def test_action_slot_accepts_every_vocab_member() -> None:
    """Loop, not parametrize — the exhaustiveness over ACTION_SLOTS is
    the load-bearing assertion. A future renumbering of the tuple shows
    up here as a single failure rather than ten unrelated test names."""
    for slot in ACTION_SLOTS:
        step = ActivityStep(step_index=0, text="hi", action_slot=slot)
        assert step.action_slot == slot


def test_action_slot_rejects_out_of_vocab_string() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ActivityStep(step_index=0, text="hi", action_slot="banana")
    # The custom validator raises ValueError; pydantic re-wraps it.
    msg = str(exc_info.value)
    assert "banana" in msg
    assert "action_slot" in msg


def test_action_slot_rejects_empty_string() -> None:
    """The empty string is an out-of-vocab value, not a synonym for None."""
    with pytest.raises(ValidationError):
        ActivityStep(step_index=0, text="hi", action_slot="")


def test_action_slot_rejects_non_string_scalar() -> None:
    with pytest.raises(ValidationError):
        ActivityStep(step_index=0, text="hi", action_slot=42)  # type: ignore[arg-type]
