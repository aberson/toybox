"""Smoke tests for the :class:`StubClient` and the model-pinning helpers.

The real :class:`AnthropicClient` wraps the SDK; we don't exercise live
network calls in tests, but we do verify the StubClient implements the
:class:`AIClient` Protocol and that model selection honors the env
overrides.
"""

from __future__ import annotations

import pytest

from toybox.ai.client import (
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
    AIClient,
    AIMessage,
    StubClient,
    text_model,
    vision_model,
)


def test_stub_implements_ai_client_protocol() -> None:
    """Step 6+ call sites depend on this Protocol; pin the relationship."""
    stub: AIClient = StubClient()
    assert isinstance(stub, AIClient)


def test_default_models_match_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOYBOX_CLAUDE_TEXT_MODEL", raising=False)
    monkeypatch.delenv("TOYBOX_CLAUDE_VISION_MODEL", raising=False)
    assert text_model() == DEFAULT_TEXT_MODEL
    assert vision_model() == DEFAULT_VISION_MODEL


def test_env_overrides_select_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOYBOX_CLAUDE_TEXT_MODEL", "claude-test-text")
    monkeypatch.setenv("TOYBOX_CLAUDE_VISION_MODEL", "claude-test-vision")
    assert text_model() == "claude-test-text"
    assert vision_model() == "claude-test-vision"


async def test_stub_complete_text_returns_seeded_response() -> None:
    stub = StubClient(responses=["hello world"])
    response = await stub.complete_text([AIMessage(role="user", content="ping")])
    assert response.text == "hello world"
    assert response.model == text_model()


async def test_stub_describe_image_returns_seeded_response() -> None:
    stub = StubClient(image_responses=["a duck"])
    response = await stub.describe_image(b"\x89PNG", prompt="what is this?")
    assert response.text == "a duck"
    assert response.model == vision_model()
