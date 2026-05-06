"""Unit coverage for :mod:`toybox.image_gen.models`."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from toybox.image_gen.models import (
    ACTION_PROMPTS,
    ACTION_SLOTS,
    GenerationContext,
    ToyActionRow,
    ToyActionStatus,
)


def test_action_slots_has_ten_unique_members_in_canonical_order() -> None:
    expected = (
        "idle",
        "pointing",
        "looking",
        "jumping",
        "cheering",
        "thinking",
        "waving",
        "running",
        "sleeping",
        "confused",
    )
    assert ACTION_SLOTS == expected
    assert len(ACTION_SLOTS) == 10
    assert len(set(ACTION_SLOTS)) == 10


def test_action_prompts_keyed_by_action_slots_with_nonempty_strings() -> None:
    assert set(ACTION_PROMPTS.keys()) == set(ACTION_SLOTS)
    for slot in ACTION_SLOTS:
        prompt = ACTION_PROMPTS[slot]
        assert isinstance(prompt, str)
        assert prompt.strip() != ""


def test_toy_action_status_members() -> None:
    assert ToyActionStatus.queued == "queued"
    assert ToyActionStatus.running == "running"
    assert ToyActionStatus.done == "done"
    assert ToyActionStatus.failed == "failed"
    assert ToyActionStatus.superseded == "superseded"
    # F3 added ``not_started`` as a UI-only placeholder synthesized by
    # ``storage.toy_actions.list_for_toy`` for slots with no DB row;
    # never persisted, never produced by the worker.
    assert ToyActionStatus.not_started == "not_started"
    # All six members and no surprise extras.
    assert {m.value for m in ToyActionStatus} == {
        "queued",
        "running",
        "done",
        "failed",
        "superseded",
        "not_started",
    }


def test_toy_action_row_defaults_and_typing() -> None:
    row = ToyActionRow(
        toy_id="abc",
        slot="idle",
        status=ToyActionStatus.queued,
    )
    assert row.image_path is None
    assert row.seed is None
    assert row.error_msg is None
    assert row.updated_at == ""


def test_generation_context_is_frozen_and_slotted() -> None:
    ctx = GenerationContext(
        toy_display_name="Bunny",
        persona_display_name="Hopper",
        tags=("plush", "soft"),
    )
    assert ctx.toy_display_name == "Bunny"
    # Frozen → assignment raises.
    with pytest.raises(FrozenInstanceError):
        ctx.toy_display_name = "Other"  # type: ignore[misc]


def test_generation_context_persona_optional() -> None:
    ctx = GenerationContext(
        toy_display_name="Bunny",
        persona_display_name=None,
        tags=(),
    )
    assert ctx.persona_display_name is None
    assert ctx.tags == ()
