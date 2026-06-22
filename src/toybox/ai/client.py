"""Async wrapper around the Anthropic Messages API (raw HTTPS + OAuth).

Step 5 ships a Protocol so call sites in steps 6-9 can land without
live Claude. Two concrete impls are provided:

* :class:`AnthropicClient` -- POSTs to ``https://api.anthropic.com/v1/messages``
  using the OAuth bearer from ``~/.toybox/secrets.json``. Sync HTTP via
  stdlib ``urllib.request``, wrapped in ``asyncio.to_thread`` so the
  Phase B mic loop is not blocked. No SDK dep -- toybox only has Claude
  OAuth (subscription auth, not an API key), and the messages-API wire
  format is stable across SDK versions.
* :class:`StubClient` -- deterministic test double; no network at all.

Models are pinned to env vars (``TOYBOX_CLAUDE_TEXT_MODEL`` and
``TOYBOX_CLAUDE_VISION_MODEL``); we never hard-code a model string at
the call site.

The ``--check`` CLI in :mod:`toybox.ai.__main__` uses the same Protocol
to print live capability state for the M1 manual-setup step.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

from .oauth import OAuthToken

_logger = logging.getLogger(__name__)

DEFAULT_TEXT_MODEL = "claude-sonnet-4-6"
DEFAULT_VISION_MODEL = "claude-haiku-4-5-20251001"
# SVG sprite generation (the "Claude Images" flag) draws a cartoon SVG of
# the toy from its reference photo — a far harder task than the one-line
# field-suggestion vision call, so it needs a capable model rather than the
# haiku vision default. Pinned to Opus 4.8 (the operator's subscription
# tier) and overridable via env.
DEFAULT_SVG_MODEL = "claude-opus-4-8"

TEXT_MODEL_ENV = "TOYBOX_CLAUDE_TEXT_MODEL"
VISION_MODEL_ENV = "TOYBOX_CLAUDE_VISION_MODEL"
SVG_MODEL_ENV = "TOYBOX_CLAUDE_SVG_MODEL"

# Messages API endpoint + wire-version header. The version pin is the
# canonical Anthropic-recommended value -- it locks the response shape
# so a future API rev that adds/changes fields doesn't break the
# extraction below until we explicitly bump.
_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_REQUEST_TIMEOUT_SEC = 60


def text_model() -> str:
    """Return the configured text model id."""
    return os.environ.get(TEXT_MODEL_ENV, DEFAULT_TEXT_MODEL)


def vision_model() -> str:
    """Return the configured vision model id."""
    return os.environ.get(VISION_MODEL_ENV, DEFAULT_VISION_MODEL)


def svg_model() -> str:
    """Return the configured Claude-Images SVG-generation model id."""
    return os.environ.get(SVG_MODEL_ENV, DEFAULT_SVG_MODEL)


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
        model: str | None = None,
        system: str | None = None,
    ) -> AIResponse:
        """Describe ``image_bytes`` against a vision-capable model.

        ``model`` overrides the default vision model (the Claude-Images
        SVG path passes :func:`svg_model`); ``system`` adds a system
        prompt. Both default to the prior behaviour when omitted.
        """
        ...


@runtime_checkable
class SyncAIClient(Protocol):
    """Synchronous Protocol for callers in a sync FastAPI route.

    ``AnthropicClient.complete_text_sync`` and ``StubClient.complete_text_sync``
    both implement this. Callers that need a sync call (e.g. ``post_approve``)
    depend on this Protocol, not on ``AIClient`` (which is async).
    """

    def complete_text_sync(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse: ...


class AnthropicClient:
    """OAuth-direct client for the Anthropic Messages API.

    Posts to ``https://api.anthropic.com/v1/messages`` with
    ``Authorization: Bearer <oauth-access-token>`` and the standard
    ``anthropic-version`` header. No SDK dependency -- toybox only has
    Claude OAuth (subscription account, not an API key), and the
    messages-API wire format is stable across SDK versions.

    Sync HTTP via stdlib ``urllib.request``; the public methods wrap
    each call in ``asyncio.to_thread`` so the Phase B mic loop is not
    blocked.

    Real network calls are NOT exercised in unit/integration tests --
    the test suite uses :class:`StubClient` everywhere. This thin
    wrapper is what production code-paths land on once capability is
    green.
    """

    def __init__(self, token: OAuthToken) -> None:
        self._token = token

    @property
    def token(self) -> OAuthToken:
        """Return the bearer token currently in use."""
        return self._token

    def update_token(self, token: OAuthToken) -> None:
        """Swap the bearer token in place. Called by the refresh loop."""
        self._token = token

    def _post_messages(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:  # pragma: no cover - exercised only against live API
        """Synchronous POST to ``/v1/messages``; returns the parsed JSON body.

        Errors (auth, rate limit, server) bubble up as
        ``urllib.error.HTTPError``; the breaker module is the
        higher-level retry / circuit gate, so we don't add an inline
        retry loop here.
        """
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _API_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token.access_token}",
                "anthropic-version": _ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SEC) as resp:
            return cast(dict[str, Any], json.loads(resp.read()))

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        """Concatenate all top-level ``type=="text"`` content blocks."""
        text = ""
        for block in data.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text += block.get("text", "")
        return text

    def _do_complete_text_sync(
        self,
        messages: Sequence[AIMessage],
        max_tokens: int,
        system: str | None,
    ) -> AIResponse:  # pragma: no cover - exercised only against live API
        payload: dict[str, Any] = {
            "model": text_model(),
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system is not None:
            payload["system"] = system
        data = self._post_messages(payload)
        return AIResponse(text=self._extract_text(data), model=text_model())

    def complete_text_sync(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:  # pragma: no cover - exercised only against live API
        return self._do_complete_text_sync(messages, max_tokens, system)

    async def complete_text(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        return await asyncio.to_thread(self._do_complete_text_sync, messages, max_tokens, system)

    def _describe_image_sync(
        self,
        image_bytes: bytes,
        prompt: str,
        media_type: str,
        max_tokens: int,
        model: str,
        system: str | None,
    ) -> AIResponse:  # pragma: no cover - exercised only against live API
        import base64

        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
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
        }
        if system is not None:
            payload["system"] = system
        data = self._post_messages(payload)
        return AIResponse(text=self._extract_text(data), model=model)

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
        return await asyncio.to_thread(
            self._describe_image_sync,
            image_bytes,
            prompt,
            media_type,
            max_tokens,
            model or vision_model(),
            system,
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

    def complete_text_sync(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        self.calls.append(
            ("complete_text_sync", tuple(messages), {"max_tokens": max_tokens, "system": system})
        )
        if self._responses:
            text = self._responses.pop(0)
        else:
            text = f"[stub-sync:{text_model()}] " + (messages[-1].content if messages else "")
        return AIResponse(text=text, model=text_model())

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
        used_model = model or vision_model()
        self.calls.append(
            (
                "describe_image",
                (image_bytes, prompt),
                {
                    "media_type": media_type,
                    "max_tokens": max_tokens,
                    "model": used_model,
                    "system": system,
                },
            )
        )
        if self._image_responses:
            text = self._image_responses.pop(0)
        else:
            text = f"[stub:{used_model}] {prompt} ({len(image_bytes)} bytes)"
        return AIResponse(text=text, model=used_model)


__all__ = [
    "AIClient",
    "AIMessage",
    "AIResponse",
    "AnthropicClient",
    "DEFAULT_SVG_MODEL",
    "DEFAULT_TEXT_MODEL",
    "DEFAULT_VISION_MODEL",
    "SVG_MODEL_ENV",
    "StubClient",
    "SyncAIClient",
    "TEXT_MODEL_ENV",
    "VISION_MODEL_ENV",
    "svg_model",
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
