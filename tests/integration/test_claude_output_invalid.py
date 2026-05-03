"""Malformed Claude output → offline fallback + ``system`` ws warning.

When Claude returns a payload that doesn't parse into :class:`Activity`
the dispatcher must:

* return an offline activity (caller never gets None on a Claude path),
* publish a ``Topic.system`` envelope with ``code=claude_output_invalid``
  carrying a short preview of the bad text + the model id,
* NOT trip the breaker (content failure is not transport failure).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from toybox.activities.models import Activity, ActivityStep
from toybox.ai.breaker import CircuitBreaker
from toybox.ai.client import StubClient
from toybox.audio.stt import Transcript
from toybox.core.capability import CapabilityReason
from toybox.core.escalation import INVALID_PREVIEW_LIMIT, EscalationDispatcher
from toybox.core.listening import ListeningMode
from toybox.core.throttle import MinIntervalThrottle
from toybox.triggers.registry import Intent
from toybox.ws.envelope import Envelope
from toybox.ws.topics import Topic


class _RecordingOfflineGenerator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def __call__(
        self,
        intent: str,
        slot: str | None,
        context: dict[str, Any] | None,
        hour: int,
        seed: int,
        *,
        persona_id: str | None = None,
    ) -> Activity:
        self.calls.append((intent, slot))
        return Activity(
            id="00000000-0000-4000-8000-000000000003",
            template_id=f"offline_{intent}",
            title=f"offline {intent}",
            steps=[ActivityStep(step_index=i, text=f"offline step {i}") for i in range(5)],
        )


def _capable_check() -> Callable[[], Awaitable[tuple[bool, CapabilityReason | None]]]:
    async def check() -> tuple[bool, CapabilityReason | None]:
        return True, None

    return check


def _transcript(text: str) -> Transcript:
    return Transcript(text=text, confidence=0.9, language="en", duration_ms=1000)


def _intent() -> Intent:
    return Intent(name="request_play", slot="cars", pattern_id="lets_play_X")


@pytest.mark.parametrize(
    "bad_payload",
    [
        "not json at all",
        # Valid JSON, wrong shape (missing required fields).
        '{"hello": "world"}',
        # Valid JSON Activity-shaped but only 3 steps (schema requires 5).
        (
            '{"id": "00000000-0000-4000-8000-000000000099",'
            '"template_id": "claude_dynamic", "title": "x",'
            '"steps": [{"step_index": 0, "text": "a"},'
            '{"step_index": 1, "text": "b"},'
            '{"step_index": 2, "text": "c"}]}'
        ),
    ],
    ids=["non_json", "wrong_shape", "wrong_step_count"],
)
async def test_malformed_output_falls_back_and_emits_system_warning(
    bad_payload: str,
) -> None:
    captured: list[Envelope] = []

    def publisher(envelope: Envelope) -> None:
        captured.append(envelope)

    breaker = CircuitBreaker(threshold=3, cooldown_sec=10.0)
    stub = StubClient(responses=[bad_payload])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
        publisher=publisher,
    )

    result = await dispatcher.on_transcript(
        _transcript("let's play cars"),
        ListeningMode.DEFAULT,
        [_intent()],
    )

    # Caller still gets an Activity — the offline fallback.
    assert result is not None
    assert result.template_id.startswith("offline_")
    assert offline.calls == [("request_play", "cars")]

    # Exactly one system envelope published.
    assert len(captured) == 1
    env = captured[0]
    assert env.topic is Topic.system
    assert env.payload["code"] == "claude_output_invalid"
    assert "model" in env.payload
    assert isinstance(env.payload["model"], str)
    assert env.payload["model"]
    # Preview is bounded to keep the wire payload small.
    assert isinstance(env.payload["preview"], str)
    assert len(env.payload["preview"]) <= INVALID_PREVIEW_LIMIT

    # Content failure does NOT open the breaker.
    assert breaker.is_open() is False


async def test_malformed_output_without_publisher_still_falls_back() -> None:
    """Publisher is optional — the offline fallback must work without one."""
    breaker = CircuitBreaker(threshold=3, cooldown_sec=10.0)
    stub = StubClient(responses=["totally not json"])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
        publisher=None,
    )

    result = await dispatcher.on_transcript(
        _transcript("let's play cars"),
        ListeningMode.DEFAULT,
        [_intent()],
    )
    assert result is not None
    assert offline.calls == [("request_play", "cars")]
    assert breaker.is_open() is False


async def test_malformed_output_preview_truncated_to_limit() -> None:
    """A very long bad payload is truncated in the system envelope preview."""
    captured: list[Envelope] = []
    long_payload = "x" * (INVALID_PREVIEW_LIMIT * 5)

    stub = StubClient(responses=[long_payload])
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=_RecordingOfflineGenerator(),
        publisher=captured.append,
    )

    await dispatcher.on_transcript(
        _transcript("let's play cars"),
        ListeningMode.DEFAULT,
        [_intent()],
    )
    assert len(captured) == 1
    assert len(captured[0].payload["preview"]) == INVALID_PREVIEW_LIMIT


async def test_publisher_exception_is_swallowed() -> None:
    """A misbehaving publisher must not break the offline fallback path."""

    def angry_publisher(envelope: Envelope) -> None:
        raise RuntimeError("publisher kaboom")

    stub = StubClient(responses=["not json"])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
        publisher=angry_publisher,
    )

    # Must not raise.
    result = await dispatcher.on_transcript(
        _transcript("let's play cars"),
        ListeningMode.DEFAULT,
        [_intent()],
    )
    assert result is not None
    assert offline.calls == [("request_play", "cars")]
