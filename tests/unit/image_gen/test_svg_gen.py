"""Unit coverage for the Claude-Images SVG generator (svg_gen.py).

Covers the pure helpers (prompt building, media sniff, SVG extraction,
sanitisation) and the ``generate_action_svg`` orchestration against a
:class:`~toybox.ai.client.StubClient` — no network, no OAuth token.
"""

from __future__ import annotations

import email.message
import urllib.error

import pytest

from toybox.ai.client import AIResponse, StubClient, svg_model
from toybox.image_gen.models import GenerationContext
from toybox.image_gen.svg_gen import (
    ClaudeImagesUnavailable,
    SvgGenerationError,
    SvgRateLimitedError,
    _extract_svg,
    _retry_delay,
    _sanitize_svg,
    _sniff_media_type,
    build_user_prompt,
    generate_action_svg,
)

_CTX = GenerationContext(
    toy_display_name="Sydney Bagheera Pillow",
    persona_display_name=None,
    tags=("plush", "black cat", "pink sunglasses"),
)


# ─── build_user_prompt ────────────────────────────────────────────────────


def test_prompt_includes_toy_name_and_tags() -> None:
    prompt = build_user_prompt("idle", _CTX)
    assert "Sydney Bagheera Pillow" in prompt
    assert "plush" in prompt and "black cat" in prompt


def test_idle_prompt_requests_a_looping_animation() -> None:
    prompt = build_user_prompt("idle", _CTX)
    assert "@keyframes" in prompt
    assert "infinite" in prompt
    # Non-flashing requirement is spelled out for a11y.
    assert "non-flashing" in prompt


def test_non_idle_prompt_is_static() -> None:
    prompt = build_user_prompt("jumping", _CTX)
    assert "static pose" in prompt
    assert "@keyframes" not in prompt


def test_prompt_includes_persona_when_present() -> None:
    ctx = GenerationContext(
        toy_display_name="Rex",
        persona_display_name="Captain Whiskers",
        tags=(),
    )
    assert "Captain Whiskers" in build_user_prompt("waving", ctx)


# ─── _sniff_media_type ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"\xff\xd8\xff\xe0xx", "image/jpeg"),
        (b"RIFF\x00\x00\x00\x00WEBPxx", "image/webp"),
        (b"garbage-bytes", "image/jpeg"),  # default
    ],
)
def test_sniff_media_type(data: bytes, expected: str) -> None:
    assert _sniff_media_type(data) == expected


# ─── _extract_svg ─────────────────────────────────────────────────────────


def test_extract_plain_svg() -> None:
    text = '<svg viewBox="0 0 128 128"><rect/></svg>'
    assert _extract_svg(text) == text


def test_extract_svg_from_markdown_fence() -> None:
    text = "Here you go:\n```svg\n<svg><circle/></svg>\n```\n"
    assert _extract_svg(text) == "<svg><circle/></svg>"


def test_extract_svg_strips_leading_and_trailing_prose() -> None:
    text = "Sure! <svg><path/></svg> Hope that helps."
    assert _extract_svg(text) == "<svg><path/></svg>"


def test_extract_returns_none_when_no_svg() -> None:
    assert _extract_svg("I cannot draw that.") is None


def test_extract_returns_none_on_truncated_svg() -> None:
    # No closing tag (output hit max_tokens mid-document).
    assert _extract_svg("<svg><path d='M0 0 L10") is None


# ─── _sanitize_svg ────────────────────────────────────────────────────────


def test_sanitize_strips_script_tags() -> None:
    dirty = "<svg><script>alert(1)</script><rect/></svg>"
    clean = _sanitize_svg(dirty)
    assert "<script" not in clean
    assert "alert(1)" not in clean
    assert "<rect/>" in clean


def test_sanitize_strips_event_handlers() -> None:
    dirty = "<svg><rect onload=\"x()\" onclick='y()'/></svg>"
    clean = _sanitize_svg(dirty)
    assert "onload" not in clean
    assert "onclick" not in clean


def test_sanitize_strips_foreign_object() -> None:
    dirty = "<svg><foreignObject><body>hi</body></foreignObject><g/></svg>"
    clean = _sanitize_svg(dirty)
    assert "foreignObject" not in clean
    assert "<g/>" in clean


def test_sanitize_neutralises_javascript_href() -> None:
    dirty = '<svg><a href="javascript:alert(1)"><rect/></a></svg>'
    clean = _sanitize_svg(dirty)
    assert "javascript:" not in clean


# ─── generate_action_svg ──────────────────────────────────────────────────


async def test_generate_returns_sanitised_svg_via_stub() -> None:
    stub = StubClient(image_responses=["```svg\n<svg><script>bad()</script><rect/></svg>\n```"])
    result = await generate_action_svg(b"\x89PNG\r\n\x1a\n", "idle", _CTX, client=stub)
    assert result.startswith("<svg")
    assert result.endswith("</svg>")
    assert "<script" not in result
    assert "<rect/>" in result


async def test_generate_uses_svg_model_and_system_prompt() -> None:
    stub = StubClient(image_responses=["<svg><rect/></svg>"])
    await generate_action_svg(b"data", "looking", _CTX, client=stub)
    name, _args, kwargs = stub.calls[0]
    assert name == "describe_image"
    assert kwargs["model"] == svg_model()
    assert isinstance(kwargs["system"], str) and kwargs["system"]


async def test_generate_raises_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # client=None forces the build-from-token path; no token on disk.
    monkeypatch.setattr("toybox.image_gen.svg_gen.load_token", lambda: None)
    with pytest.raises(ClaudeImagesUnavailable):
        await generate_action_svg(b"data", "idle", _CTX)


async def test_generate_raises_on_malformed_reply() -> None:
    stub = StubClient(image_responses=["I'm sorry, I can't do that."])
    with pytest.raises(SvgGenerationError):
        await generate_action_svg(b"data", "idle", _CTX, client=stub)


# ─── 429 retry / backoff ──────────────────────────────────────────────────


def _http_error(code: int, *, retry_after: str | None = None) -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.anthropic.com", code, "err", hdrs, None)


class _FlakyClient:
    """Async client double that raises HTTPError for the first ``fail_times``
    calls, then returns a canned SVG. Records the call count."""

    def __init__(
        self,
        *,
        fail_times: int,
        code: int = 429,
        svg: str = "<svg><rect/></svg>",
    ) -> None:
        self.fail_times = fail_times
        self.code = code
        self.svg = svg
        self.calls = 0

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
        model: str | None = None,
        system: str | None = None,
    ) -> AIResponse:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _http_error(self.code)
        return AIResponse(text=self.svg, model=model or "stub")


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the retry backoff instant so the tests don't actually wait."""

    async def _fast(_seconds: float) -> None:
        return None

    monkeypatch.setattr("toybox.image_gen.svg_gen.asyncio.sleep", _fast)


def test_retry_delay_honours_retry_after_capped() -> None:
    # Retry-After within cap is used verbatim; above cap is clamped.
    assert _retry_delay(_http_error(429, retry_after="5"), 0) == 5.0
    assert _retry_delay(_http_error(429, retry_after="999"), 0) == 20.0


def test_retry_delay_exponential_without_header() -> None:
    # No Retry-After → exponential base 2: 2, 4, 8 ... capped at 20.
    assert _retry_delay(_http_error(429), 0) == 2.0
    assert _retry_delay(_http_error(429), 1) == 4.0
    assert _retry_delay(_http_error(429), 10) == 20.0  # capped


async def test_generate_retries_429_then_succeeds() -> None:
    client = _FlakyClient(fail_times=2)  # two 429s, third call returns SVG
    result = await generate_action_svg(b"data", "idle", _CTX, client=client)
    assert result.startswith("<svg")
    assert client.calls == 3


async def test_generate_raises_rate_limited_after_budget() -> None:
    client = _FlakyClient(fail_times=99)  # always 429
    with pytest.raises(SvgRateLimitedError):
        await generate_action_svg(b"data", "idle", _CTX, client=client)
    assert client.calls == 3  # _SVG_MAX_ATTEMPTS


async def test_generate_propagates_non_retryable_http_error() -> None:
    # A 500 is not retryable → bubbles up as the raw HTTPError, not
    # SvgRateLimitedError (the worker handles it generically).
    client = _FlakyClient(fail_times=99, code=500)
    with pytest.raises(urllib.error.HTTPError):
        await generate_action_svg(b"data", "idle", _CTX, client=client)
    assert client.calls == 1  # no retries on a non-retryable status
