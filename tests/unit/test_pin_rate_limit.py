"""Unit tests for the parent PIN rate-limit state machine."""

from __future__ import annotations

import pytest

from toybox.core.pin_rate_limit import (
    ATTEMPT_WINDOW_SECONDS,
    LOCK_DURATION_SECONDS,
    MAX_ATTEMPTS,
    PinRateLimiter,
)


@pytest.fixture
def limiter() -> PinRateLimiter:
    """A fresh limiter per test so module-singleton state can't leak."""
    return PinRateLimiter()


def test_initial_state_is_unlocked(limiter: PinRateLimiter) -> None:
    assert limiter.is_locked() is False
    assert limiter.seconds_until_unlock() == 0.0
    status = limiter.status()
    assert status.attempts == 0
    assert status.attempts_remaining == MAX_ATTEMPTS


def test_four_failures_do_not_lock(limiter: PinRateLimiter) -> None:
    statuses = [limiter.record_failed_attempt(now=10.0 + i) for i in range(4)]
    assert [s.attempts for s in statuses] == [1, 2, 3, 4]
    assert all(not s.locked for s in statuses)
    assert statuses[-1].attempts_remaining == 1


def test_fifth_failure_engages_lock(limiter: PinRateLimiter) -> None:
    final = None
    for i in range(5):
        final = limiter.record_failed_attempt(now=100.0 + i)
    assert final is not None
    assert final.locked is True
    assert final.attempts == 5
    assert final.attempts_remaining == 0
    # Lock window roughly equals LOCK_DURATION_SECONDS at the moment
    # of locking.
    assert pytest.approx(final.seconds_until_unlock, rel=1e-6) == LOCK_DURATION_SECONDS


def test_attempts_during_lock_do_not_increment(limiter: PinRateLimiter) -> None:
    for i in range(5):
        limiter.record_failed_attempt(now=100.0 + i)
    assert limiter.is_locked(now=200.0) is True
    # Same lock state — count not bumped past MAX_ATTEMPTS.
    after = limiter.record_failed_attempt(now=200.0)
    assert after.locked is True
    assert after.attempts == 5  # unchanged


def test_lock_expires_after_lock_duration(limiter: PinRateLimiter) -> None:
    for i in range(5):
        limiter.record_failed_attempt(now=100.0 + i)
    # Just before expiry: still locked.
    assert limiter.is_locked(now=100.0 + 4 + LOCK_DURATION_SECONDS - 0.5) is True
    # Just after expiry: lock auto-clears.
    after = limiter.record_failed_attempt(now=100.0 + 4 + LOCK_DURATION_SECONDS + 1.0)
    assert after.locked is False
    # Counter resets on lock expiry, so this is the first attempt of a new window.
    assert after.attempts == 1


def test_window_resets_after_5_minutes_without_lock(limiter: PinRateLimiter) -> None:
    """4 failures, then 6 minutes of silence — counter resets on next failure."""
    for i in range(4):
        limiter.record_failed_attempt(now=10.0 + i)
    # 6 minutes (> 5 min window) since the first failure.
    after = limiter.record_failed_attempt(now=10.0 + ATTEMPT_WINDOW_SECONDS + 60.0)
    # New window started → count is 1, not 5 (i.e. no lock).
    assert after.attempts == 1
    assert after.locked is False


def test_successful_attempt_resets_counter(limiter: PinRateLimiter) -> None:
    limiter.record_failed_attempt(now=10.0)
    limiter.record_failed_attempt(now=11.0)
    limiter.record_successful_attempt(now=12.0)
    status = limiter.status(now=13.0)
    assert status.attempts == 0
    assert status.attempts_remaining == MAX_ATTEMPTS
    # And a subsequent failure starts a fresh window.
    after = limiter.record_failed_attempt(now=14.0)
    assert after.attempts == 1


def test_successful_attempt_does_not_drop_active_lock(limiter: PinRateLimiter) -> None:
    """Defensive: a stray success during a lock must not unlock the gate."""
    for i in range(5):
        limiter.record_failed_attempt(now=100.0 + i)
    assert limiter.is_locked(now=200.0) is True
    limiter.record_successful_attempt(now=200.0)
    assert limiter.is_locked(now=200.0) is True
