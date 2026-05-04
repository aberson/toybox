"""Unit tests for :mod:`toybox.ai.house_vision`.

Mirrors :mod:`tests.unit.test_toy_vision` — same inject-a-stub-client
pattern, same four failure paths plus the missing-label gate that's
unique to house vision (we treat ``suggested_room_label`` as
mandatory).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import pytest

from toybox.ai.client import AIMessage, AIResponse
from toybox.ai.house_vision import HouseVisionSuggestion, suggest_room


class _StubVisionClient:
    """Minimal AIClient stub: queue a response or an exception."""

    def __init__(
        self,
        *,
        text: str | None = None,
        exc: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self._text = text
        self._exc = exc
        self._delay = delay
        self.calls = 0

    async def complete_text(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:  # pragma: no cover -- not exercised by house_vision
        raise NotImplementedError

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:
        self.calls += 1
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        if self._exc is not None:
            raise self._exc
        assert self._text is not None
        return AIResponse(text=self._text, model="claude-haiku-4-5-20251001")


@pytest.mark.asyncio
async def test_suggest_room_success_path() -> None:
    client = _StubVisionClient(
        text='{"suggested_room_label": "Living Room", '
        '"features": [{"name": "couch"}, {"name": "rug"}]}'
    )
    result = await suggest_room(client, b"img-bytes", timeout_sec=1.0)
    assert isinstance(result, HouseVisionSuggestion)
    assert result.suggested_room_label == "Living Room"
    assert [f.name for f in result.features] == ["couch", "rug"]
    assert client.calls == 1


@pytest.mark.asyncio
async def test_suggest_room_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """If the SDK call doesn't return in time, we get (None, "timeout")."""
    client = _StubVisionClient(text='{"suggested_room_label": "X", "features": []}', delay=2.0)
    caplog.set_level(logging.WARNING, logger="toybox.ai.house_vision")
    result = await suggest_room(client, b"img-bytes", timeout_sec=0.05)
    assert result == (None, "timeout")
    assert any("timed out" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_suggest_room_rate_limited(caplog: pytest.LogCaptureFixture) -> None:
    """A 429 response surfaces as ``(None, "rate_limited")`` at INFO level."""
    client = _StubVisionClient(exc=Exception("HTTP 429: rate limit exceeded"))
    caplog.set_level(logging.INFO, logger="toybox.ai.house_vision")
    result = await suggest_room(client, b"img-bytes", timeout_sec=1.0)
    assert result == (None, "rate_limited")
    assert any("rate-limited" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_suggest_room_malformed_json(caplog: pytest.LogCaptureFixture) -> None:
    client = _StubVisionClient(text="this is not json at all")
    caplog.set_level(logging.WARNING, logger="toybox.ai.house_vision")
    result = await suggest_room(client, b"img-bytes", timeout_sec=1.0)
    assert result == (None, "malformed")
    assert any("not valid JSON" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_suggest_room_missing_label_returns_malformed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A response without ``suggested_room_label`` is malformed.

    The label is what tabs photos in the parent UI; without it we
    can't surface the suggestion.
    """
    client = _StubVisionClient(text='{"features": [{"name": "rug"}]}')
    caplog.set_level(logging.WARNING, logger="toybox.ai.house_vision")
    result = await suggest_room(client, b"img-bytes", timeout_sec=1.0)
    assert result == (None, "malformed")


@pytest.mark.asyncio
async def test_suggest_room_missing_features_defaults_to_empty() -> None:
    """A response without ``features`` falls back to an empty list."""
    client = _StubVisionClient(text='{"suggested_room_label": "Bathroom"}')
    result = await suggest_room(client, b"img-bytes", timeout_sec=1.0)
    assert isinstance(result, HouseVisionSuggestion)
    assert result.suggested_room_label == "Bathroom"
    assert result.features == []
