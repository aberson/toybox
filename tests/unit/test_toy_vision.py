"""Unit tests for :mod:`toybox.ai.toy_vision`.

We exercise the parse/classify logic and the four failure paths
(success / timeout / 429 / malformed) by injecting a stub
:class:`~toybox.ai.client.AIClient` that returns canned responses or
raises canned exceptions.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import pytest

from toybox.ai.client import AIMessage, AIResponse
from toybox.ai.toy_vision import ToyVisionSuggestion, suggest_fields


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
    ) -> AIResponse:  # pragma: no cover -- not exercised by toy_vision
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
async def test_suggest_fields_success_path() -> None:
    client = _StubVisionClient(
        text='{"display_name": "Sparkle Unicorn", '
        '"tags": ["plush", "unicorn", "pink"], '
        '"persona_match_id": null}'
    )
    result = await suggest_fields(client, b"img-bytes", timeout_sec=1.0)
    assert isinstance(result, ToyVisionSuggestion)
    assert result.display_name == "Sparkle Unicorn"
    assert result.tags == ["plush", "unicorn", "pink"]
    assert result.persona_match_id is None
    assert client.calls == 1


@pytest.mark.asyncio
async def test_suggest_fields_strips_markdown_fence() -> None:
    """Claude regularly wraps JSON in ```json ... ``` — strip it.

    Kept (rather than trimmed) because real Claude responses include
    fences often enough that this isn't merely an implementation detail.
    """
    client = _StubVisionClient(
        text='```json\n{"display_name": "Bear", "tags": ["plush"], "persona_match_id": null}\n```'
    )
    result = await suggest_fields(client, b"img-bytes", timeout_sec=1.0)
    assert isinstance(result, ToyVisionSuggestion)
    assert result.display_name == "Bear"


@pytest.mark.asyncio
async def test_suggest_fields_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """If the SDK call doesn't return in time, we get (None, "timeout")."""
    client = _StubVisionClient(text='{"display_name": "X"}', delay=2.0)
    caplog.set_level(logging.WARNING, logger="toybox.ai.toy_vision")
    result = await suggest_fields(client, b"img-bytes", timeout_sec=0.05)
    assert result == (None, "timeout")
    assert any("timed out" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_suggest_fields_rate_limited(caplog: pytest.LogCaptureFixture) -> None:
    """A 429 response surfaces as ``(None, "rate_limited")`` at INFO level."""
    client = _StubVisionClient(exc=Exception("HTTP 429: rate limit exceeded"))
    caplog.set_level(logging.INFO, logger="toybox.ai.toy_vision")
    result = await suggest_fields(client, b"img-bytes", timeout_sec=1.0)
    assert result == (None, "rate_limited")
    assert any("rate-limited" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_suggest_fields_classifies_sdk_class_names() -> None:
    """SDK exception class names should drive classification (L3).

    Iter-1 grepped the message for 'rate limit' / 'timeout'; iter-2
    matches the class name first so the real SDK's
    ``RateLimitError`` / ``APITimeoutError`` classify correctly even
    when the message is empty or innocuous.
    """

    class RateLimitError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    rate_client = _StubVisionClient(exc=RateLimitError(""))
    rate_result = await suggest_fields(rate_client, b"img", timeout_sec=1.0)
    assert rate_result == (None, "rate_limited")

    timeout_client = _StubVisionClient(exc=APITimeoutError(""))
    timeout_result = await suggest_fields(timeout_client, b"img", timeout_sec=1.0)
    assert timeout_result == (None, "timeout")


@pytest.mark.asyncio
async def test_suggest_fields_malformed_json(caplog: pytest.LogCaptureFixture) -> None:
    client = _StubVisionClient(text="this is not json at all")
    caplog.set_level(logging.WARNING, logger="toybox.ai.toy_vision")
    result = await suggest_fields(client, b"img-bytes", timeout_sec=1.0)
    assert result == (None, "malformed")
    assert any("not valid JSON" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_suggest_fields_schema_mismatch_returns_malformed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """JSON parses but doesn't match the schema → malformed."""
    client = _StubVisionClient(text='{"unrelated_key": "value", "tags": ["plush"]}')
    caplog.set_level(logging.WARNING, logger="toybox.ai.toy_vision")
    result = await suggest_fields(client, b"img-bytes", timeout_sec=1.0)
    assert result == (None, "malformed")


@pytest.mark.asyncio
async def test_suggest_fields_partial_response_fills_defaults() -> None:
    """A response with only ``display_name`` parses, with empty tags + null persona.

    Pins the M8 contract: missing optional fields fall back to the
    Pydantic defaults (``tags=[]``, ``persona_match_id=None``) rather
    than failing the whole call. Iter-1 had test coverage for fully
    wrong shapes only, not partial shapes.
    """
    client = _StubVisionClient(text='{"display_name": "Bear"}')
    result = await suggest_fields(client, b"img-bytes", timeout_sec=1.0)
    assert isinstance(result, ToyVisionSuggestion)
    assert result.display_name == "Bear"
    assert result.tags == []
    assert result.persona_match_id is None


@pytest.mark.asyncio
async def test_suggest_fields_normalises_messy_tags() -> None:
    """Mixed case + duplicate tags → lowercased, deduped."""
    client = _StubVisionClient(
        text='{"display_name": "Bear", '
        '"tags": ["Plush", "plush", "BROWN", " plush "], '
        '"persona_match_id": null}'
    )
    result = await suggest_fields(client, b"img-bytes", timeout_sec=1.0)
    assert isinstance(result, ToyVisionSuggestion)
    assert result.tags == ["plush", "brown"]


@pytest.mark.asyncio
async def test_suggest_fields_other_error_returns_error() -> None:
    """An unrelated exception bucket lands under ``error``."""
    client = _StubVisionClient(exc=Exception("network glitch"))
    result = await suggest_fields(client, b"img-bytes", timeout_sec=1.0)
    assert result == (None, "error")


@pytest.mark.asyncio
async def test_suggest_fields_handles_string_tag_field() -> None:
    """Some Claude variants return a single string instead of a list."""
    client = _StubVisionClient(
        text='{"display_name": "Lego","tags": "blocks", "persona_match_id": null}'
    )
    result = await suggest_fields(client, b"img-bytes", timeout_sec=1.0)
    assert isinstance(result, ToyVisionSuggestion)
    assert result.tags == ["blocks"]
