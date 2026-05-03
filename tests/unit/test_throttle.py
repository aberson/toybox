"""Coverage for :class:`MinIntervalThrottle`.

The throttle is exercised with an injected fake clock so the interval
window can be advanced deterministically without any real sleeps.
"""

from __future__ import annotations

import threading

import pytest

from toybox.core.throttle import (
    CLAUDE_MIN_INTERVAL_SEC_ENV,
    DEFAULT_CLAUDE_MIN_INTERVAL_SEC,
    MinIntervalThrottle,
    min_interval_from_env,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def test_first_acquire_always_succeeds() -> None:
    clock = _FakeClock()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    assert throttle.try_acquire() is True


def test_second_acquire_within_interval_fails() -> None:
    clock = _FakeClock()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    assert throttle.try_acquire() is True
    clock.advance(29.0)
    assert throttle.try_acquire() is False


def test_acquire_after_interval_succeeds_again() -> None:
    clock = _FakeClock()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    assert throttle.try_acquire() is True
    clock.advance(30.0)
    assert throttle.try_acquire() is True


def test_zero_interval_disables_throttling() -> None:
    """An interval of 0.0 means every acquire succeeds (no throttling)."""
    clock = _FakeClock()
    throttle = MinIntervalThrottle(0.0, clock=clock)
    for _ in range(5):
        assert throttle.try_acquire() is True


def test_negative_interval_raises() -> None:
    with pytest.raises(ValueError, match="interval_sec"):
        MinIntervalThrottle(-1.0)


def test_time_until_next_zero_before_first_acquire() -> None:
    throttle = MinIntervalThrottle(30.0, clock=_FakeClock())
    assert throttle.time_until_next() == 0.0


def test_time_until_next_decays_with_clock() -> None:
    clock = _FakeClock()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    throttle.try_acquire()
    assert throttle.time_until_next() == pytest.approx(30.0)
    clock.advance(10.0)
    assert throttle.time_until_next() == pytest.approx(20.0)
    clock.advance(20.0)
    assert throttle.time_until_next() == 0.0


def test_time_until_next_clamped_to_zero_after_window() -> None:
    clock = _FakeClock()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    throttle.try_acquire()
    clock.advance(60.0)  # well past the window
    assert throttle.time_until_next() == 0.0


def test_reset_forgets_last_acquire() -> None:
    clock = _FakeClock()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    assert throttle.try_acquire() is True
    assert throttle.try_acquire() is False
    throttle.reset()
    assert throttle.try_acquire() is True


def test_concurrent_acquires_only_one_wins() -> None:
    """Two threads racing on a fresh throttle: exactly one True, one False.

    Without the lock the read+write to ``_last_acquire`` is not atomic,
    and both threads could see the sentinel and acquire. The lock makes
    this deterministic regardless of scheduling.
    """
    clock = _FakeClock()
    throttle = MinIntervalThrottle(30.0, clock=clock)
    results: list[bool] = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        got = throttle.try_acquire()
        with results_lock:
            results.append(got)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [False, True]


def test_min_interval_from_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLAUDE_MIN_INTERVAL_SEC_ENV, raising=False)
    assert min_interval_from_env() == DEFAULT_CLAUDE_MIN_INTERVAL_SEC


def test_min_interval_from_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLAUDE_MIN_INTERVAL_SEC_ENV, "5.5")
    assert min_interval_from_env() == pytest.approx(5.5)


def test_min_interval_from_env_unparseable_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLAUDE_MIN_INTERVAL_SEC_ENV, "not-a-float")
    assert min_interval_from_env() == DEFAULT_CLAUDE_MIN_INTERVAL_SEC


def test_min_interval_from_env_negative_clamped_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLAUDE_MIN_INTERVAL_SEC_ENV, "-3.0")
    assert min_interval_from_env() == 0.0
