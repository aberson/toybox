"""Async wrapper around the Anthropic SDK.

Step 5 ships a Protocol so call sites in steps 6-9 can land without
live Claude. Two concrete impls are provided:

* :class:`AnthropicClient` — wraps the official ``anthropic`` SDK in
  ``asyncio.to_thread`` so the Phase B mic loop is not blocked. The SDK
  is imported lazily so tests don't require the dep installed.
* :class:`StubClient` — deterministic test double; no network at all.

Models are pinned to env vars (``TOYBOX_CLAUDE_TEXT_MODEL`` and
``TOYBOX_CLAUDE_VISION_MODEL``); we never hard-code a model string at
the call site.

The ``--check`` CLI in :mod:`toybox.ai.__main__` uses the same Protocol
to print live capability state for the M1 manual-setup step.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .oauth import OAuthToken

_logger = logging.getLogger(__name__)

DEFAULT_TEXT_MODEL = "claude-sonnet-4-6"
DEFAULT_VISION_MODEL = "claude-haiku-4-5-20251001"

TEXT_MODEL_ENV = "TOYBOX_CLAUDE_TEXT_MODEL"
VISION_MODEL_ENV = "TOYBOX_CLAUDE_VISION_MODEL"


def text_model() -> str:
    """Return the configured text model id."""
    return os.environ.get(TEXT_MODEL_ENV, DEFAULT_TEXT_MODEL)


def vision_model() -> str:
    """Return the configured vision model id."""
    return os.environ.get(VISION_MODEL_ENV, DEFAULT_VISION_MODEL)


@dataclass(frozen=True, slots=True)
class AIMessage:
    """A single text message in a chat-style request."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class AIResponse:
    """A model response.

    Wire shape is intentionally minimal — call sites that need the raw
    SDK object can extend the Protocol; for now Phase B only needs the
    text + the model id used.
    """

    text: str
    model: str


@runtime_checkable
class AIClient(Protocol):
    """Async-friendly Protocol every AI client implements.

    Real impls wrap the SDK in ``asyncio.to_thread``; the stub is
    deterministic. Step 6+ call sites depend on this Protocol, not on
    the concrete classes, so swapping in the stub for a test is a
    one-line dependency override.
    """

    async def complete_text(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        """Run a text completion against the configured text model."""
        ...

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:
        """Describe ``image_bytes`` against the configured vision model."""
        ...


class AnthropicClient:
    """Real Anthropic SDK wrapper.

    The SDK is imported lazily so test environments without the
    ``anthropic`` package can still import :mod:`toybox.ai.client`. The
    constructor accepts the bearer ``token`` directly so the refresh
    loop can rebuild the underlying SDK client when the token rotates.

    Real network calls are NOT exercised in unit/integration tests — the
    test suite uses :class:`StubClient` everywhere. The thin wrapper
    here exists so steps 6-9 can flip a flag and land live calls.
    """

    def __init__(self, token: OAuthToken) -> None:
        self._token = token
        self._sdk: Any | None = None  # lazy

    @property
    def token(self) -> OAuthToken:
        """Return the bearer token currently in use."""
        return self._token

    def update_token(self, token: OAuthToken) -> None:
        """Swap the bearer token in place. Called by the refresh loop."""
        self._token = token
        # Force the lazy SDK to rebuild on next call so the new bearer
        # is picked up.
        self._sdk = None

    def _get_sdk(self) -> Any:
        if self._sdk is None:
            try:
                import anthropic  # type: ignore[import-not-found,unused-ignore]
            except ImportError as exc:  # pragma: no cover - dep-gated
                raise RuntimeError(
                    "anthropic SDK not installed; install or use StubClient"
                ) from exc
            self._sdk = anthropic.Anthropic(auth_token=self._token.access_token)
        return self._sdk

    def _complete_text_sync(
        self,
        messages: Sequence[AIMessage],
        max_tokens: int,
        system: str | None,
    ) -> AIResponse:  # pragma: no cover - exercised only when SDK present
        sdk = self._get_sdk()
        kwargs: dict[str, Any] = {
            "model": text_model(),
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system is not None:
            kwargs["system"] = system
        result = sdk.messages.create(**kwargs)
        # Best-effort text extraction across SDK shapes.
        text = ""
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text += getattr(block, "text", "")
        return AIResponse(text=text, model=text_model())

    async def complete_text(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        return await asyncio.to_thread(self._complete_text_sync, messages, max_tokens, system)

    def _describe_image_sync(
        self,
        image_bytes: bytes,
        prompt: str,
        media_type: str,
        max_tokens: int,
    ) -> AIResponse:  # pragma: no cover - exercised only when SDK present
        import base64

        sdk = self._get_sdk()
        b64 = base64.b64encode(image_bytes).decode("ascii")
        result = sdk.messages.create(
            model=vision_model(),
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        text = ""
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text += getattr(block, "text", "")
        return AIResponse(text=text, model=vision_model())

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:
        return await asyncio.to_thread(
            self._describe_image_sync, image_bytes, prompt, media_type, max_tokens
        )


class StubClient:
    """Deterministic test double — no network, no SDK import.

    The ``responses`` and ``image_responses`` constructor args let tests
    pre-seed the queue of replies; if empty, a synthetic echo is
    returned. This is the implementation steps 6-9 should depend on in
    their own unit tests.
    """

    def __init__(
        self,
        *,
        responses: Sequence[str] | None = None,
        image_responses: Sequence[str] | None = None,
    ) -> None:
        self._responses: list[str] = list(responses or [])
        self._image_responses: list[str] = list(image_responses or [])
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def complete_text(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        self.calls.append(
            ("complete_text", tuple(messages), {"max_tokens": max_tokens, "system": system})
        )
        if self._responses:
            text = self._responses.pop(0)
        else:
            text = f"[stub:{text_model()}] " + (messages[-1].content if messages else "")
        return AIResponse(text=text, model=text_model())

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:
        self.calls.append(
            (
                "describe_image",
                (image_bytes, prompt),
                {"media_type": media_type, "max_tokens": max_tokens},
            )
        )
        if self._image_responses:
            text = self._image_responses.pop(0)
        else:
            text = f"[stub:{vision_model()}] {prompt} ({len(image_bytes)} bytes)"
        return AIResponse(text=text, model=vision_model())


__all__ = [
    "AIClient",
    "AIMessage",
    "AIResponse",
    "AnthropicClient",
    "DEFAULT_TEXT_MODEL",
    "DEFAULT_VISION_MODEL",
    "StubClient",
    "TEXT_MODEL_ENV",
    "VISION_MODEL_ENV",
    "text_model",
    "vision_model",
]


if __name__ == "__main__":  # pragma: no cover - thin alias for plan parity
    # ``python -m toybox.ai.client --check`` is the form the M1 setup
    # step references in ``documentation/plan.md``. We delegate to the
    # package's __main__ so there's a single source of truth.
    import sys

    from .__main__ import main as _check_main

    sys.exit(_check_main(sys.argv[1:]))
