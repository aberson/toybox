"""Coverage for the Claude-as-judge async caller.

Uses :class:`StubClient` to drive the AI client interface so no live
Claude is required.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest

from toybox.activities.generator import generate
from toybox.ai.client import AIMessage, AIResponse
from toybox.ai.judge import (
    judge_activity,
    parse_judge_response,
)
from toybox.ai.labeled_events import GeneratorContext
from toybox.ai.rubric import InvalidRubricScoresError


def _activity() -> Any:
    return generate(
        intent="boredom",
        slot=None,
        context={"unit": "judge"},
        hour=10,
        seed=42,
    )


# --------------------------------------------------------------------- prompts


# --------------------------------------------------------------------- parse


def test_parse_judge_response_happy_path() -> None:
    text = json.dumps(
        {
            "schema": 5,
            "age_appropriateness": 4,
            "doability": 5,
            "persona_fidelity": 4,
            "coherence": 4,
            "safety": 5,
            "hallucinated_props": [],
            "judge_notes": "great",
        }
    )
    s = parse_judge_response(text)
    assert s.schema == 5
    assert s.judge_notes == "great"


def test_parse_judge_response_strips_code_fence() -> None:
    payload = {
        "schema": 4,
        "age_appropriateness": 4,
        "doability": 4,
        "persona_fidelity": 4,
        "coherence": 4,
        "safety": 4,
    }
    text = "```json\n" + json.dumps(payload) + "\n```"
    s = parse_judge_response(text)
    assert s.schema == 4


def test_parse_judge_response_bad_json_raises() -> None:
    with pytest.raises(InvalidRubricScoresError):
        parse_judge_response("not json at all")


def test_parse_judge_response_non_object_raises() -> None:
    with pytest.raises(InvalidRubricScoresError):
        parse_judge_response("[1,2,3]")


# --------------------------------------------------------------------- judge_activity


class _StubAI:
    """Minimal AIClient that returns a pre-set text reply or raises."""

    def __init__(
        self,
        *,
        reply: str | None = None,
        raises: BaseException | None = None,
        delay_sec: float = 0.0,
    ) -> None:
        self.reply = reply
        self.raises = raises
        self.delay_sec = delay_sec
        self.calls: list[Sequence[AIMessage]] = []

    async def complete_text(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        self.calls.append(tuple(messages))
        if self.delay_sec > 0:
            await asyncio.sleep(self.delay_sec)
        if self.raises is not None:
            raise self.raises
        assert self.reply is not None
        return AIResponse(text=self.reply, model="stub")

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:  # pragma: no cover - judge doesn't use vision
        raise NotImplementedError


@pytest.mark.asyncio
async def test_judge_activity_happy_path() -> None:
    payload = json.dumps(
        {
            "schema": 4,
            "age_appropriateness": 4,
            "doability": 4,
            "persona_fidelity": 4,
            "coherence": 4,
            "safety": 4,
            "hallucinated_props": [],
            "judge_notes": "ok",
        }
    )
    ai = _StubAI(reply=payload)
    activity = _activity()
    ctx = GeneratorContext(intent="boredom")
    scores = await judge_activity(ai_client=ai, activity=activity, ctx=ctx)
    assert scores is not None
    assert scores.schema == 4
    assert ai.calls, "judge should have called the AI client once"


@pytest.mark.asyncio
async def test_judge_activity_returns_none_on_malformed() -> None:
    ai = _StubAI(reply="this is not json")
    activity = _activity()
    ctx = GeneratorContext(intent="boredom")
    scores = await judge_activity(ai_client=ai, activity=activity, ctx=ctx)
    assert scores is None


@pytest.mark.asyncio
async def test_judge_activity_returns_none_on_transport_error() -> None:
    ai = _StubAI(raises=RuntimeError("boom"))
    activity = _activity()
    ctx = GeneratorContext(intent="boredom")
    scores = await judge_activity(ai_client=ai, activity=activity, ctx=ctx)
    assert scores is None


@pytest.mark.asyncio
async def test_judge_activity_times_out() -> None:
    ai = _StubAI(reply="never seen", delay_sec=2.0)
    activity = _activity()
    ctx = GeneratorContext(intent="boredom")
    scores = await judge_activity(
        ai_client=ai,
        activity=activity,
        ctx=ctx,
        timeout_sec=0.05,
    )
    assert scores is None


@pytest.mark.asyncio
async def test_judge_activity_propagates_cancellation() -> None:
    """Cancellation must bubble up so process shutdown stays prompt."""
    ai = _StubAI(reply="ignored", delay_sec=5.0)
    activity = _activity()
    ctx = GeneratorContext(intent="boredom")

    task = asyncio.create_task(
        judge_activity(ai_client=ai, activity=activity, ctx=ctx)
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


