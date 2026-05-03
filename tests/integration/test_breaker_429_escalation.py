"""429 → breaker open → offline fallback round-trip for the escalation path.

The Anthropic SDK is not imported here. The dispatcher detects 429s by
duck-typing on ``status_code == 429`` (and a class-name fallback for
SDK-shaped ``RateLimitError``). The fakes below pose as those shapes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from toybox.activities.models import Activity, ActivityStep
from toybox.ai.breaker import CircuitBreaker
from toybox.ai.client import AIResponse
from toybox.audio.stt import Transcript
from toybox.core.capability import CapabilityReason
from toybox.core.escalation import EscalationDispatcher
from toybox.core.listening import ListeningMode
from toybox.core.throttle import MinIntervalThrottle
from toybox.triggers.registry import Intent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


class _RateLimitError(Exception):
    """Stand-in for ``anthropic.RateLimitError``.

    The dispatcher's 429 detection looks at ``status_code`` and the
    class name; this fake satisfies both so the test doesn't depend on
    the SDK being installed.
    """

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.status_code = 429
        # Mimic the SDK's response.headers shape.
        headers: dict[str, str] = {}
        if retry_after is not None:
            headers["retry-after"] = str(retry_after)

        class _Resp:
            def __init__(self, hdrs: dict[str, str]) -> None:
                self.headers = hdrs

        self.response = _Resp(headers)


class _ScriptedClient:
    """An AIClient whose ``complete_text`` follows a scripted plan.

    Each entry is either a callable that returns an AIResponse OR an
    exception instance to raise. The script is consumed in order; once
    exhausted, the last entry repeats.
    """

    def __init__(self, script: list[Callable[[], AIResponse] | BaseException]) -> None:
        self._script = script
        self._idx = 0
        self.calls = 0

    async def complete_text(
        self,
        messages: Any,  # noqa: ANN401
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        self.calls += 1
        action = self._script[min(self._idx, len(self._script) - 1)]
        self._idx += 1
        if isinstance(action, BaseException):
            raise action
        return action()

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:
        raise NotImplementedError


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
            id="00000000-0000-4000-8000-000000000001",
            template_id=f"offline_{intent}",
            title=f"offline {intent}",
            steps=[ActivityStep(step_index=i, text=f"offline step {i}") for i in range(5)],
        )


def _capable_check() -> Callable[[], Awaitable[tuple[bool, CapabilityReason | None]]]:
    async def check() -> tuple[bool, CapabilityReason | None]:
        return True, None

    return check


def _valid_activity_response() -> AIResponse:
    activity = Activity(
        id="00000000-0000-4000-8000-000000000002",
        template_id="claude_dynamic",
        title="claude activity",
        steps=[ActivityStep(step_index=i, text=f"claude step {i}") for i in range(5)],
    )
    from toybox.ai.client import text_model

    return AIResponse(text=activity.model_dump_json(), model=text_model())


def _transcript(text: str) -> Transcript:
    return Transcript(text=text, confidence=0.9, language="en", duration_ms=1000)


def _intent() -> Intent:
    return Intent(name="request_play", slot="legos", pattern_id="lets_play_X")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_429_opens_breaker_and_routes_subsequent_calls_to_offline() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0, time_source=clock)
    client = _ScriptedClient(script=[_RateLimitError(retry_after=30.0)])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=client,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    # First transcript: hits Claude → 429 → breaker opens; offline fallback.
    result_1 = await dispatcher.on_transcript(
        _transcript("let's play legos"),
        ListeningMode.DEFAULT,
        [_intent()],
    )
    assert result_1 is not None
    assert client.calls == 1
    assert breaker.is_open() is True
    assert breaker.is_rate_limited() is True
    assert offline.calls == [("request_play", "legos")]

    # Second transcript: breaker open → no Claude call at all.
    result_2 = await dispatcher.on_transcript(
        _transcript("let's play legos"),
        ListeningMode.DEFAULT,
        [_intent()],
    )
    assert result_2 is not None
    assert client.calls == 1  # unchanged — short-circuited by the breaker
    assert offline.calls == [
        ("request_play", "legos"),
        ("request_play", "legos"),
    ]


async def test_429_retry_after_governs_breaker_cooldown() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=120.0, time_source=clock)
    client = _ScriptedClient(script=[_RateLimitError(retry_after=15.0)])
    dispatcher = EscalationDispatcher(
        ai_client=client,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=_RecordingOfflineGenerator(),
    )

    await dispatcher.on_transcript(
        _transcript("let's play legos"),
        ListeningMode.DEFAULT,
        [_intent()],
    )
    assert breaker.is_open() is True

    # 14 seconds later the cooldown is NOT yet elapsed (Retry-After=15).
    clock.advance(14.0)
    assert breaker.is_open() is True

    # At 15 seconds the breaker is eligible to half-open.
    clock.advance(1.0)
    assert breaker.try_half_open() is True


async def test_429_breaker_half_opens_then_probe_succeeds_and_closes() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0, time_source=clock)
    client = _ScriptedClient(
        script=[
            _RateLimitError(retry_after=10.0),
            _valid_activity_response,  # second call succeeds
        ]
    )
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=client,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    # 1) 429 trips breaker open.
    await dispatcher.on_transcript(
        _transcript("let's play legos"),
        ListeningMode.DEFAULT,
        [_intent()],
    )
    assert breaker.is_open() is True

    # 2) Advance past the 10s Retry-After cooldown.
    clock.advance(10.0)
    # Reading the state advances open → half_open.
    assert breaker.is_open() is False  # auto-transitioned to half_open

    # 3) Next dispatch: capability still True, breaker not strictly
    #    open, throttle clean → probe call. Returns clean activity.
    result = await dispatcher.on_transcript(
        _transcript("let's play legos again"),
        ListeningMode.DEFAULT,
        [_intent()],
    )
    assert result is not None
    assert result.template_id == "claude_dynamic"
    assert client.calls == 2
    # Probe success closes the breaker fully.
    assert breaker.is_open() is False
    assert breaker.is_rate_limited() is False


async def test_429_with_no_retry_after_uses_breaker_default_cooldown() -> None:
    """``Retry-After`` missing → breaker falls back to its configured cooldown."""
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=42.0, time_source=clock)
    client = _ScriptedClient(script=[_RateLimitError(retry_after=None)])
    dispatcher = EscalationDispatcher(
        ai_client=client,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=_RecordingOfflineGenerator(),
    )

    await dispatcher.on_transcript(
        _transcript("let's play legos"),
        ListeningMode.DEFAULT,
        [_intent()],
    )
    # 41s elapsed: still open.
    clock.advance(41.0)
    assert breaker.is_open() is True
    # 42s: half-open eligible.
    clock.advance(1.0)
    assert breaker.try_half_open() is True


class _APIStatusError500(Exception):
    """Stand-in for ``anthropic.APIStatusError`` with a non-429 status.

    The dispatcher's 429 detection must NOT classify a 5xx server error as a
    rate limit. This guards against an over-broad class-name fallback.
    """

    def __init__(self) -> None:
        super().__init__("internal server error")
        self.status_code = 500


async def test_apistatuserror_with_non_429_status_is_not_rate_limit() -> None:
    """5xx ``APIStatusError`` must record_failure, not record_429."""
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0, time_source=clock)
    client = _ScriptedClient(script=[_APIStatusError500(), _APIStatusError500()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=client,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    # Two 5xx failures: breaker counts them as plain failures, not 429s.
    await dispatcher.on_transcript(
        _transcript("let's play legos"), ListeningMode.DEFAULT, [_intent()]
    )
    await dispatcher.on_transcript(
        _transcript("let's play legos"), ListeningMode.DEFAULT, [_intent()]
    )

    # threshold=3, two failures -> still closed, NOT rate-limited.
    assert breaker.is_open() is False
    assert breaker.is_rate_limited() is False
    # Both fell back to offline.
    assert len(offline.calls) == 2
