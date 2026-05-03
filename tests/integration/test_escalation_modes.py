"""Per-mode call-count assertions for the escalation dispatcher.

The dispatcher routes transcripts to (offline | claude) per a 5-mode
table. These tests pin the table by driving synthetic transcripts +
intents and counting the resulting Claude / offline invocations on
recording stubs.

No real templates, no real Claude SDK, no real network — every gate is
a small fake so the only thing being exercised is the dispatcher's
mode logic itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from toybox.activities.models import Activity, ActivityStep
from toybox.ai.breaker import BreakerState, CircuitBreaker
from toybox.ai.client import AIMessage, AIResponse, StubClient
from toybox.audio.stt import Transcript
from toybox.core.capability import CapabilityReason
from toybox.core.escalation import SPONTANEOUS_INTENT, EscalationDispatcher
from toybox.core.listening import ListeningMode
from toybox.core.throttle import MinIntervalThrottle
from toybox.triggers.registry import Intent

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def _make_activity(*, intent: str, slot: str | None, source: str) -> Activity:
    """Build a syntactically-valid 5-step Activity for tests."""
    suffix = slot or "none"
    steps = [
        ActivityStep(
            step_index=i,
            text=f"{source} step {i} for {intent}/{suffix}",
        )
        for i in range(5)
    ]
    return Activity(
        id=f"00000000-0000-4000-8000-{abs(hash((intent, slot, source))) % 10**12:012d}",
        template_id=f"{source}_{intent}",
        title=f"{source} activity for {intent}",
        steps=steps,
    )


class _RecordingOfflineGenerator:
    """Captures every call to the offline generator for assertion."""

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
        return _make_activity(intent=intent, slot=slot, source="offline")


def _capable_check() -> Callable[[], Awaitable[tuple[bool, CapabilityReason | None]]]:
    async def check() -> tuple[bool, CapabilityReason | None]:
        return True, None

    return check


def _incapable_check(
    reason: CapabilityReason,
) -> Callable[[], Awaitable[tuple[bool, CapabilityReason | None]]]:
    async def check() -> tuple[bool, CapabilityReason | None]:
        return False, reason

    return check


def _valid_activity_json() -> str:
    """A Claude-like JSON response that parses cleanly into Activity."""
    activity = _make_activity(intent="claude", slot=None, source="claude")
    return activity.model_dump_json()


def _make_transcript(text: str, *, confidence: float = 0.9) -> Transcript:
    return Transcript(
        text=text,
        confidence=confidence,
        language="en",
        duration_ms=1000,
    )


def _trigger(name: str, slot: str | None = None) -> Intent:
    return Intent(name=name, slot=slot, pattern_id=f"pat_{name}")


# ---------------------------------------------------------------------------
# Mode 1 / Mode 2 — never call Claude
# ---------------------------------------------------------------------------


async def test_mode_1_never_calls_claude() -> None:
    stub = StubClient(responses=[_valid_activity_json()] * 10)
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    transcripts = [_make_transcript(f"let's play game {i}") for i in range(10)]
    intents = [_trigger("request_play", "game")]

    for t in transcripts:
        result = await dispatcher.on_transcript(t, ListeningMode.OFFLINE, intents)
        assert result is not None
        assert result.template_id.startswith("offline_")

    assert stub.calls == []
    assert len(offline.calls) == 10


async def test_mode_1_returns_none_when_no_intents() -> None:
    stub = StubClient()
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    result = await dispatcher.on_transcript(
        _make_transcript("nothing matches"),
        ListeningMode.OFFLINE,
        [],
    )
    assert result is None
    assert stub.calls == []
    assert offline.calls == []


async def test_mode_2_never_calls_claude() -> None:
    stub = StubClient(responses=[_valid_activity_json()] * 10)
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    transcripts = [_make_transcript(f"trigger {i}") for i in range(10)]
    intents = [_trigger("request_story", "dragons")]

    for t in transcripts:
        result = await dispatcher.on_transcript(t, ListeningMode.LOW, intents)
        assert result is not None

    assert stub.calls == []
    assert len(offline.calls) == 10


# ---------------------------------------------------------------------------
# Mode 3 — curated triggers escalate to Claude when capable
# ---------------------------------------------------------------------------


async def test_mode_3_calls_claude_on_curated_triggers_when_capable() -> None:
    """With min-interval 0, every curated trigger should escalate to Claude."""
    responses = [_valid_activity_json() for _ in range(3)]
    stub = StubClient(responses=responses)
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    intents = [_trigger("request_play", "blocks")]

    for _ in range(3):
        result = await dispatcher.on_transcript(
            _make_transcript("let's play blocks"),
            ListeningMode.DEFAULT,
            intents,
        )
        assert result is not None
        # Claude path activities carry the synthetic claude template_id.
        assert result.template_id == "claude_claude"

    assert len(stub.calls) == 3
    assert offline.calls == []


async def test_mode_3_returns_none_when_no_intents() -> None:
    stub = StubClient()
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    result = await dispatcher.on_transcript(
        _make_transcript("noise"),
        ListeningMode.DEFAULT,
        [],
    )
    assert result is None
    assert stub.calls == []
    assert offline.calls == []


async def test_mode_3_falls_back_to_offline_when_breaker_open() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_sec=60.0, time_source=clock)
    breaker.record_failure()  # opens immediately at threshold=1
    assert breaker.is_open() is True

    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    intents = [_trigger("request_play", "trains")]

    result = await dispatcher.on_transcript(
        _make_transcript("let's play trains"),
        ListeningMode.DEFAULT,
        intents,
    )
    assert result is not None
    assert stub.calls == []
    assert offline.calls == [("request_play", "trains")]


async def test_mode_3_falls_back_to_offline_when_not_capable() -> None:
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_incapable_check(CapabilityReason.token_missing),
        offline_generator=offline,
    )
    intents = [_trigger("request_play", "puzzles")]

    result = await dispatcher.on_transcript(
        _make_transcript("let's play puzzles"),
        ListeningMode.DEFAULT,
        intents,
    )
    assert result is not None
    assert stub.calls == []
    assert offline.calls == [("request_play", "puzzles")]


async def test_mode_3_falls_back_to_offline_when_throttled() -> None:
    """First transcript hits Claude, second within window falls back."""
    clock = _FakeClock()
    stub = StubClient(responses=[_valid_activity_json(), _valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=throttle,
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    intents = [_trigger("request_play", "boats")]

    first = await dispatcher.on_transcript(
        _make_transcript("let's play boats"),
        ListeningMode.DEFAULT,
        intents,
    )
    second = await dispatcher.on_transcript(
        _make_transcript("let's play boats again"),
        ListeningMode.DEFAULT,
        intents,
    )

    assert first is not None
    assert second is not None
    assert len(stub.calls) == 1  # only the first hit Claude
    assert offline.calls == [("request_play", "boats")]  # second went offline


# ---------------------------------------------------------------------------
# Mode 4 — same as Mode 3 for transcripts; spontaneous timer fires Claude
# ---------------------------------------------------------------------------


async def test_mode_4_transcript_path_matches_mode_3() -> None:
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    intents = [_trigger("request_story", "knights")]

    result = await dispatcher.on_transcript(
        _make_transcript("tell me a story about knights"),
        ListeningMode.HIGH,
        intents,
    )
    assert result is not None
    assert len(stub.calls) == 1
    assert offline.calls == []


async def test_mode_4_spontaneous_timer_fires_claude_when_capable() -> None:
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    result = await dispatcher.maybe_fire_spontaneous(ListeningMode.HIGH)
    assert result is not None
    assert result.template_id == "claude_claude"
    assert len(stub.calls) == 1
    assert offline.calls == []


@pytest.mark.parametrize(
    "mode",
    [
        ListeningMode.OFFLINE,
        ListeningMode.LOW,
        ListeningMode.DEFAULT,
        ListeningMode.INTENSE,
    ],
)
async def test_mode_4_spontaneous_does_nothing_in_other_modes(
    mode: ListeningMode,
) -> None:
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    result = await dispatcher.maybe_fire_spontaneous(mode)
    assert result is None
    assert stub.calls == []
    assert offline.calls == []


async def test_mode_4_spontaneous_falls_back_to_offline_when_throttled() -> None:
    """Spontaneous calls are subject to the same global throttle."""
    clock = _FakeClock()
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    # Pre-acquire to consume the slot.
    throttle.try_acquire()

    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=throttle,
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    result = await dispatcher.maybe_fire_spontaneous(ListeningMode.HIGH)
    assert result is not None
    assert stub.calls == []
    assert offline.calls == [(SPONTANEOUS_INTENT, None)]


# ---------------------------------------------------------------------------
# Mode 5 — every above-floor transcript escalates
# ---------------------------------------------------------------------------


async def test_mode_5_calls_claude_for_every_transcript_when_unthrottled() -> None:
    responses = [_valid_activity_json() for _ in range(5)]
    stub = StubClient(responses=responses)
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    # No triggers passed — mode 5 should still escalate using a
    # synthesized intent.
    for i in range(5):
        result = await dispatcher.on_transcript(
            _make_transcript(f"random utterance {i}"),
            ListeningMode.INTENSE,
            [],  # no triggers
        )
        assert result is not None

    assert len(stub.calls) == 5
    assert offline.calls == []


async def test_mode_5_uses_first_intent_when_one_matched() -> None:
    """When mode 5 has both a transcript AND a trigger, the trigger wins."""
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    intents = [_trigger("request_play", "magnets")]

    result = await dispatcher.on_transcript(
        _make_transcript("let's play magnets"),
        ListeningMode.INTENSE,
        intents,
    )
    assert result is not None
    # Stub records ``("complete_text", tuple(messages), kwargs)`` so
    # ``args`` here IS the messages tuple.
    method, messages, _kwargs = stub.calls[0]
    assert method == "complete_text"
    assert isinstance(messages[0], AIMessage)
    assert "request_play" in messages[0].content
    assert "magnets" in messages[0].content


async def test_mode_5_throttled_calls_fall_back_to_offline() -> None:
    clock = _FakeClock()
    stub = StubClient(responses=[_valid_activity_json()] * 3)
    offline = _RecordingOfflineGenerator()
    throttle = MinIntervalThrottle(10.0, clock=clock)
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=throttle,
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    # First call → Claude. Subsequent within-window calls → offline.
    for i in range(3):
        result = await dispatcher.on_transcript(
            _make_transcript(f"utt {i}"),
            ListeningMode.INTENSE,
            [],
        )
        assert result is not None

    assert len(stub.calls) == 1
    # 2 offline fallbacks for the 2nd + 3rd transcripts. The default
    # SPONTANEOUS_INTENT is the synthesized intent for empty-intent
    # mode 5 transcripts.
    assert offline.calls == [
        (SPONTANEOUS_INTENT, None),
        (SPONTANEOUS_INTENT, None),
    ]

    # After the throttle window elapses, Claude is reachable again.
    clock.advance(10.0)
    fourth = await dispatcher.on_transcript(
        _make_transcript("utt 3"),
        ListeningMode.INTENSE,
        [],
    )
    assert fourth is not None
    assert len(stub.calls) == 2


# ---------------------------------------------------------------------------
# AIClient prompt shape — the dispatcher passes intent + transcript context
# ---------------------------------------------------------------------------


async def test_claude_prompt_includes_intent_slot_and_transcript() -> None:
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    await dispatcher.on_transcript(
        _make_transcript("let's play with the dragon"),
        ListeningMode.DEFAULT,
        [_trigger("request_play", "dragon")],
    )

    method, messages, kwargs = stub.calls[0]
    assert method == "complete_text"
    assert messages[0].role == "user"
    assert "request_play" in messages[0].content
    assert "dragon" in messages[0].content
    # The system prompt is non-empty and explains the JSON schema.
    assert kwargs["system"] is not None
    assert "JSON" in kwargs["system"] or "json" in kwargs["system"].lower()


async def test_claude_success_records_breaker_success() -> None:
    """A clean Claude call must reset the breaker's failure counter."""
    breaker = CircuitBreaker(threshold=2, cooldown_sec=10.0)
    breaker.record_failure()
    assert breaker._consecutive_failures == 1  # noqa: SLF001 -- pin internal state

    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    await dispatcher.on_transcript(
        _make_transcript("let's play"),
        ListeningMode.DEFAULT,
        [_trigger("request_play")],
    )
    # Success path resets the consecutive-failure counter.
    assert breaker._consecutive_failures == 0  # noqa: SLF001


# ---------------------------------------------------------------------------
# Generic transport failure → breaker.record_failure() + offline fallback
# ---------------------------------------------------------------------------


class _RaisingClient:
    """An AIClient that raises a non-429 exception on every call."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete_text(
        self,
        messages: Any,  # noqa: ANN401 -- protocol shape varies in tests
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        self.calls += 1
        raise RuntimeError("network kaboom")

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:
        raise NotImplementedError


async def test_generic_failure_records_breaker_failure_and_falls_back() -> None:
    breaker = CircuitBreaker(threshold=2, cooldown_sec=10.0)
    raising = _RaisingClient()
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=raising,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    result = await dispatcher.on_transcript(
        _make_transcript("let's play"),
        ListeningMode.DEFAULT,
        [_trigger("request_play")],
    )
    # Caller still gets an Activity (offline fallback).
    assert result is not None
    assert result.template_id.startswith("offline_")
    assert raising.calls == 1
    assert offline.calls == [("request_play", None)]
    # Failure recorded but not yet open (threshold=2).
    assert breaker.is_open() is False

    # Second failure → breaker opens.
    await dispatcher.on_transcript(
        _make_transcript("let's play"),
        ListeningMode.DEFAULT,
        [_trigger("request_play")],
    )
    assert breaker.is_open() is True


# ---------------------------------------------------------------------------
# Cancellation propagation (HIGH 1)
# ---------------------------------------------------------------------------


class _BlockingClient:
    """An AIClient whose ``complete_text`` blocks on an event until cancelled."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.calls = 0

    async def complete_text(
        self,
        messages: Any,  # noqa: ANN401 -- protocol shape varies in tests
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        self.calls += 1
        self.entered.set()
        # Block forever — caller must cancel us.
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:
        raise NotImplementedError


async def test_cancellation_propagates_through_dispatcher() -> None:
    """Cancelling a dispatcher task mid-Claude call must NOT debit the breaker.

    Regression: prior code used ``except BaseException`` which swallowed
    ``asyncio.CancelledError`` and treated it as a transport failure.
    """
    breaker = CircuitBreaker(threshold=2, cooldown_sec=10.0)
    blocking = _BlockingClient()
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=blocking,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    task = asyncio.create_task(
        dispatcher.on_transcript(
            _make_transcript("let's play"),
            ListeningMode.DEFAULT,
            [_trigger("request_play")],
        )
    )
    # Wait until complete_text is in-flight, then cancel.
    await blocking.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Breaker must not have been debited by a cancellation.
    assert breaker._consecutive_failures == 0  # noqa: SLF001 -- pin internal state
    assert breaker.is_open() is False
    # Cancellation aborted before offline path.
    assert offline.calls == []


# ---------------------------------------------------------------------------
# Half-open probe single-flight (HIGH 2)
# ---------------------------------------------------------------------------


class _CountingClient:
    """An AIClient that returns valid Activity JSON and counts each call."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0
        self._gate = asyncio.Event()

    async def complete_text(
        self,
        messages: Any,  # noqa: ANN401
        *,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> AIResponse:
        self.calls += 1
        # Yield once so concurrent callers can race the gate before we
        # return — without this the first call would resolve before the
        # second even reaches the breaker check.
        await asyncio.sleep(0)
        return AIResponse(text=self._response, model="stub")

    async def describe_image(
        self,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:
        raise NotImplementedError


async def test_half_open_single_probe_under_concurrency() -> None:
    """Two concurrent dispatches in half_open: only one Claude call fires."""
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_sec=10.0, time_source=clock)
    # Open the breaker, then advance past cooldown so reading state
    # transitions it to half_open on next access.
    breaker.record_failure()
    assert breaker.is_open() is True
    clock.advance(11.0)
    assert breaker.state is BreakerState.half_open

    counting = _CountingClient(_valid_activity_json())
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=counting,
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    results = await asyncio.gather(
        dispatcher.on_transcript(
            _make_transcript("first"),
            ListeningMode.INTENSE,
            [],
        ),
        dispatcher.on_transcript(
            _make_transcript("second"),
            ListeningMode.INTENSE,
            [],
        ),
    )
    assert all(r is not None for r in results)
    # Exactly one Claude call — the other lost the half_open probe race.
    assert counting.calls == 1
    # The losing call fell back to offline.
    assert len(offline.calls) == 1


# ---------------------------------------------------------------------------
# capability_check exception safety (MEDIUM 3)
# ---------------------------------------------------------------------------


def _raising_capability_check(
    exc: Exception,
) -> Callable[[], Awaitable[tuple[bool, CapabilityReason | None]]]:
    async def check() -> tuple[bool, CapabilityReason | None]:
        raise exc

    return check


async def test_capability_check_raising_falls_back_to_offline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A raising capability_check must NOT crash the dispatcher."""
    import logging

    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_raising_capability_check(RuntimeError("boom")),
        offline_generator=offline,
    )
    with caplog.at_level(logging.WARNING, logger="toybox.core.escalation"):
        result = await dispatcher.on_transcript(
            _make_transcript("let's play"),
            ListeningMode.DEFAULT,
            [_trigger("request_play")],
        )
    assert result is not None
    assert result.template_id.startswith("offline_")
    assert stub.calls == []
    assert offline.calls == [("request_play", None)]
    # Operator-visible warning was emitted.
    assert any("capability_check raised" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Throttle non-consumption when upstream gate is closed (MEDIUM 5)
# ---------------------------------------------------------------------------


async def test_throttle_not_consumed_when_capability_false() -> None:
    """A False capability check must not consume the throttle ticket."""
    clock = _FakeClock()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=throttle,
        capability_check=_incapable_check(CapabilityReason.token_missing),
        offline_generator=offline,
    )
    result = await dispatcher.on_transcript(
        _make_transcript("let's play"),
        ListeningMode.DEFAULT,
        [_trigger("request_play")],
    )
    assert result is not None
    # Ticket still available because the capability gate short-circuited
    # before the throttle was touched.
    assert throttle.try_acquire() is True


async def test_throttle_not_consumed_when_breaker_open() -> None:
    """An open breaker must not consume the throttle ticket."""
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_sec=60.0, time_source=clock)
    breaker.record_failure()
    assert breaker.is_open() is True

    throttle = MinIntervalThrottle(30.0, clock=clock)
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=breaker,
        throttle=throttle,
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    result = await dispatcher.on_transcript(
        _make_transcript("let's play"),
        ListeningMode.DEFAULT,
        [_trigger("request_play")],
    )
    assert result is not None
    # Ticket still available — breaker gate fired before throttle.
    assert throttle.try_acquire() is True


# ---------------------------------------------------------------------------
# Concurrent dispatch (MEDIUM 6) — separate from half-open: throttle wins
# ---------------------------------------------------------------------------


async def test_concurrent_on_transcript_throttles_to_one_call() -> None:
    """Two gather()-ed dispatches with a real throttle: only one calls Claude."""
    clock = _FakeClock()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    counting = _CountingClient(_valid_activity_json())
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=counting,
        breaker=CircuitBreaker(),
        throttle=throttle,
        capability_check=_capable_check(),
        offline_generator=offline,
    )

    results = await asyncio.gather(
        dispatcher.on_transcript(
            _make_transcript("first"),
            ListeningMode.DEFAULT,
            [_trigger("request_play")],
        ),
        dispatcher.on_transcript(
            _make_transcript("second"),
            ListeningMode.DEFAULT,
            [_trigger("request_play")],
        ),
    )
    assert all(r is not None for r in results)
    assert counting.calls == 1
    # The other call fell back to offline.
    assert len(offline.calls) == 1


# ---------------------------------------------------------------------------
# Empty transcript prompt shape (LOW 10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("transcript_text", "expect_transcript_line"),
    [
        ("let's play with the dragon", True),
        ("", False),
    ],
)
async def test_claude_user_prompt_omits_transcript_line_when_empty(
    transcript_text: str,
    expect_transcript_line: bool,
) -> None:
    """Empty transcript text must not produce a ``Transcript:`` prompt line.

    Mode 5 escalates every above-floor transcript even with no triggers,
    so this exercises both the populated and empty-text branches of
    :func:`_claude_user_prompt`.
    """
    stub = StubClient(responses=[_valid_activity_json()])
    offline = _RecordingOfflineGenerator()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        offline_generator=offline,
    )
    transcript = Transcript(
        text=transcript_text,
        confidence=0.9,
        language="en",
        duration_ms=1000,
    )
    await dispatcher.on_transcript(transcript, ListeningMode.INTENSE, [])

    _method, messages, _kwargs = stub.calls[0]
    content = messages[0].content
    if expect_transcript_line:
        assert "Transcript:" in content
    else:
        assert "Transcript:" not in content
