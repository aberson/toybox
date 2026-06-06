"""Unit tests for :mod:`toybox.ai.animator`.

Covers:
1. Happy path — StubClient with valid JSON returns correct {seq: animation} dict.
2. Reward step override — even if Claude returns "spin", reward step gets "shine".
3. Unknown animation filtered — unknown animation name for a seq is dropped.
4. Exception path — malformed JSON from StubClient → empty dict returned.
5. Empty steps → empty dict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from toybox.ai.animator import annotate_step_animations
from toybox.ai.client import StubClient


@dataclass
class _FakeStep:
    """Minimal ActivityStepResponse stand-in for animator tests."""

    seq: int
    body: str
    kind: str | None = None


def _make_annotation_json(items: list[dict[str, Any]]) -> str:
    return json.dumps({"annotations": items})


class TestAnnotateStepAnimations:
    def test_happy_path_returns_seq_to_animation_map(self) -> None:
        steps = [
            _FakeStep(seq=1, body="Do something fun"),
            _FakeStep(seq=2, body="Jump around"),
        ]
        payload = _make_annotation_json([
            {"seq": 1, "animation": "wobble"},
            {"seq": 2, "animation": "jump"},
        ])
        stub = StubClient(responses=[payload])
        result = annotate_step_animations(steps, "wizard", stub)
        assert result == {1: "wobble", 2: "jump"}
        # One call was made with correct method name.
        assert len(stub.calls) == 1
        assert stub.calls[0][0] == "complete_text_sync"

    def test_reward_step_always_gets_shine(self) -> None:
        """Even if Claude returns 'spin' for a reward step, result is 'shine'."""
        steps = [
            _FakeStep(seq=1, body="Regular step"),
            _FakeStep(seq=2, body="You did it!", kind="reward"),
        ]
        payload = _make_annotation_json([
            {"seq": 1, "animation": "float"},
            {"seq": 2, "animation": "spin"},  # Claude chose spin for reward
        ])
        stub = StubClient(responses=[payload])
        result = annotate_step_animations(steps, None, stub)
        assert result[1] == "float"
        assert result[2] == "shine"  # Overridden to shine regardless

    def test_unknown_animation_is_dropped(self) -> None:
        steps = [_FakeStep(seq=1, body="Do something")]
        payload = _make_annotation_json([
            {"seq": 1, "animation": "explode"},  # not in vocabulary
        ])
        stub = StubClient(responses=[payload])
        result = annotate_step_animations(steps, None, stub)
        assert 1 not in result

    def test_malformed_json_returns_empty_dict(self) -> None:
        """StubClient returns non-JSON → parse error → empty dict."""
        steps = [_FakeStep(seq=1, body="Do something")]
        stub = StubClient(responses=["not valid json at all"])
        result = annotate_step_animations(steps, None, stub)
        assert result == {}

    def test_missing_annotations_key_returns_empty_dict(self) -> None:
        steps = [_FakeStep(seq=1, body="Something")]
        stub = StubClient(responses=[json.dumps({"other": "key"})])
        result = annotate_step_animations(steps, None, stub)
        assert result == {}

    def test_empty_steps_returns_empty_dict_without_calling_client(self) -> None:
        stub = StubClient(responses=['{"annotations": [{"seq": 1, "animation": "float"}]}'])
        result = annotate_step_animations([], None, stub)
        assert result == {}
        # No network call should be made when there are no steps.
        assert stub.calls == []

    def test_persona_id_included_in_user_message(self) -> None:
        steps = [_FakeStep(seq=1, body="Step body")]
        payload = _make_annotation_json([{"seq": 1, "animation": "pulse"}])
        stub = StubClient(responses=[payload])
        annotate_step_animations(steps, "detective", stub)
        _, msgs, _ = stub.calls[0]
        assert "detective" in msgs[0].content

    def test_no_persona_id_uses_neutral_note(self) -> None:
        steps = [_FakeStep(seq=1, body="Step body")]
        payload = _make_annotation_json([{"seq": 1, "animation": "float"}])
        stub = StubClient(responses=[payload])
        annotate_step_animations(steps, None, stub)
        _, msgs, _ = stub.calls[0]
        assert "neutral" in msgs[0].content

    def test_partial_annotations_only_returns_annotated_seqs(self) -> None:
        """Claude only annotates seq=1; seq=2 is absent from result."""
        steps = [
            _FakeStep(seq=1, body="First"),
            _FakeStep(seq=2, body="Second"),
        ]
        payload = _make_annotation_json([{"seq": 1, "animation": "spin"}])
        stub = StubClient(responses=[payload])
        result = annotate_step_animations(steps, None, stub)
        assert result == {1: "spin"}
        assert 2 not in result

    def test_all_valid_animation_names_accepted(self) -> None:
        """All six Animation enum values should pass through without being filtered."""
        valid = ["shine", "jump", "spin", "pulse", "wobble", "float"]
        for anim in valid:
            steps = [_FakeStep(seq=1, body="A step")]
            stub = StubClient(responses=[_make_annotation_json([{"seq": 1, "animation": anim}])])
            result = annotate_step_animations(steps, None, stub)
            assert result.get(1) == anim, f"Expected {anim!r} to be accepted"
