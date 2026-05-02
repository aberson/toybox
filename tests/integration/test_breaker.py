"""Coverage for the in-process :class:`CircuitBreaker`.

The breaker is exercised by injecting a fake monotonic clock so the
cooldown can be advanced deterministically without ``asyncio.sleep``.
"""

from __future__ import annotations

import pytest

from toybox.ai.breaker import BreakerState, CircuitBreaker


class _FakeClock:
    """A monotonic-style clock the test can advance manually."""

    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def test_three_consecutive_failures_opens_breaker() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0, time_source=clock)

    breaker.record_failure()
    assert breaker.is_open() is False
    breaker.record_failure()
    assert breaker.is_open() is False
    breaker.record_failure()
    assert breaker.is_open() is True
    assert breaker.state is BreakerState.open
    # Plain failures are NOT rate-limited.
    assert breaker.is_rate_limited() is False


def test_success_resets_failure_count() -> None:
    """Two failures + a success + two more failures should NOT trip a
    threshold-3 breaker — the success resets the consecutive counter."""
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0, time_source=clock)

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.closed


def test_429_opens_immediately_and_respects_retry_after() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0, time_source=clock)

    breaker.record_429(retry_after=30.0)
    assert breaker.is_open() is True
    assert breaker.is_rate_limited() is True

    # After 29s the cooldown is NOT yet elapsed.
    clock.advance(29.0)
    assert breaker.is_open() is True

    # At 30s the open→half_open transition is now eligible.
    clock.advance(1.0)
    assert breaker.state is BreakerState.half_open


def test_429_with_no_retry_after_uses_default_cooldown() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0, time_source=clock)

    breaker.record_429(retry_after=None)
    assert breaker.is_open() is True

    clock.advance(59.0)
    assert breaker.is_open() is True
    clock.advance(1.0)
    assert breaker.state is BreakerState.half_open


def test_cooldown_elapses_to_half_open_then_probe_closes_on_success() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=2, cooldown_sec=10.0, time_source=clock)

    breaker.record_failure()
    breaker.record_failure()  # open
    assert breaker.is_open() is True

    clock.advance(10.0)
    # Now eligible to half-open. The first try wins the slot.
    assert breaker.try_half_open() is True
    # A second concurrent caller is rejected.
    assert breaker.try_half_open() is False

    breaker.record_success()
    assert breaker.state is BreakerState.closed


def test_probe_failure_reopens_breaker() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=2, cooldown_sec=10.0, time_source=clock)

    breaker.record_failure()
    breaker.record_failure()  # open

    clock.advance(10.0)
    assert breaker.try_half_open() is True

    breaker.record_failure()  # probe failed → reopen
    assert breaker.is_open() is True

    # Cooldown is fresh (10s from now).
    clock.advance(9.0)
    assert breaker.is_open() is True
    clock.advance(1.0)
    assert breaker.state is BreakerState.half_open


def test_rate_limited_flag_clears_after_success() -> None:
    """A 429-induced open should NOT keep marking the breaker as
    rate-limited once a successful probe closes the circuit."""
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=10.0, time_source=clock)

    breaker.record_429(retry_after=10.0)
    assert breaker.is_rate_limited() is True

    clock.advance(10.0)
    assert breaker.try_half_open() is True
    breaker.record_success()
    assert breaker.is_rate_limited() is False
    assert breaker.state is BreakerState.closed


@pytest.mark.parametrize("retry_after", [-5.0, 0.0])
def test_nonpositive_retry_after_falls_back_to_default_cooldown(retry_after: float) -> None:
    """A negative or zero ``Retry-After`` (server bug) must NOT yield a
    zero/negative cooldown — fall back to the configured default. A zero
    cooldown would auto-transition straight to half_open on the next
    state read, defeating the breaker entirely."""
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_sec=42.0, time_source=clock)

    breaker.record_429(retry_after=retry_after)
    assert breaker.is_open() is True

    clock.advance(41.0)
    assert breaker.is_open() is True
    clock.advance(1.0)
    assert breaker.state is BreakerState.half_open


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("closed", False),
        ("still_open", False),
    ],
)
def test_try_half_open_returns_false_when_not_eligible(scenario: str, expected: bool) -> None:
    """try_half_open() returns False both from a fresh closed breaker
    and from an open breaker whose cooldown has not yet elapsed."""
    clock = _FakeClock()
    breaker = CircuitBreaker(threshold=2, cooldown_sec=10.0, time_source=clock)

    if scenario == "still_open":
        breaker.record_failure()
        breaker.record_failure()  # open, cooldown NOT elapsed
        assert breaker.state is BreakerState.open

    assert breaker.try_half_open() is expected


def test_env_default_threshold_and_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOYBOX_CLAUDE_BREAKER_THRESHOLD", "5")
    monkeypatch.setenv("TOYBOX_CLAUDE_BREAKER_COOLDOWN_SEC", "120.5")
    breaker = CircuitBreaker()
    assert breaker.threshold == 5
    assert breaker.cooldown_sec == pytest.approx(120.5)


def test_env_invalid_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unparseable env var must fall back to the same defaults the
    breaker uses when the env is unset entirely. Comparing against a
    clean-env reference avoids hard-coding the magic numbers (3, 60.0)
    in two places."""
    # Reference: env unset → defaults.
    monkeypatch.delenv("TOYBOX_CLAUDE_BREAKER_THRESHOLD", raising=False)
    monkeypatch.delenv("TOYBOX_CLAUDE_BREAKER_COOLDOWN_SEC", raising=False)
    reference = CircuitBreaker()

    # Garbage env values → must match the reference.
    monkeypatch.setenv("TOYBOX_CLAUDE_BREAKER_THRESHOLD", "not-an-int")
    monkeypatch.setenv("TOYBOX_CLAUDE_BREAKER_COOLDOWN_SEC", "definitely-not-a-float")
    breaker = CircuitBreaker()
    assert breaker.threshold == reference.threshold
    assert breaker.cooldown_sec == pytest.approx(reference.cooldown_sec)
