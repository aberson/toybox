"""In-memory rate-limit state for the parent PIN gate.

The state machine matches the spec in ``documentation/plan.md`` Step 21:

* Window: 5 wrong PIN attempts within 5 minutes triggers a lock.
* Lock duration: 15 minutes from the 5th failure.
* While locked, any attempt — even with the correct PIN — is rejected.
* On the first failure outside the window, the counter resets to 1.
* On a successful attempt, the counter resets to 0.
* On lock expiry, both the lock and the counter reset.
* Process restart resets all state. Persisting across restarts is out
  of scope for v1; the spec calls this out as acceptable.

A single module-level :class:`_RateLimiter` is exported via the
:func:`get_rate_limiter` accessor so unit tests can construct an
isolated instance via :class:`PinRateLimiter` without touching shared
state. The API layer goes through the module-level singleton.

Concurrency: PIN endpoints are async, but the underlying counter is
guarded by a :class:`threading.Lock` (matching the metrics counter
pattern) so a couple of in-flight checks can't double-count.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# Window-based bucket parameters. The constants are exported so tests
# can assert the values without re-typing the magic numbers.
ATTEMPT_WINDOW_SECONDS = 300.0  # 5 minutes
LOCK_DURATION_SECONDS = 900.0  # 15 minutes
MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class RateLimitStatus:
    """Snapshot of the rate-limit state after recording an attempt.

    ``locked`` is ``True`` once the failure count has reached
    :data:`MAX_ATTEMPTS` within :data:`ATTEMPT_WINDOW_SECONDS`.
    ``seconds_until_unlock`` is ``0.0`` when not locked.
    """

    attempts: int
    locked: bool
    seconds_until_unlock: float
    attempts_remaining: int


class PinRateLimiter:
    """Thread-safe sliding-window counter for failed PIN attempts.

    The window is "from the first failure in the current bucket"; once
    :data:`ATTEMPT_WINDOW_SECONDS` elapses without reaching the lock
    threshold, the counter resets on the next failure.
    """

    def __init__(
        self,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        window_seconds: float = ATTEMPT_WINDOW_SECONDS,
        lock_duration_seconds: float = LOCK_DURATION_SECONDS,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._lock_duration_seconds = lock_duration_seconds
        self._lock = threading.Lock()
        self._failure_count = 0
        # Wall-clock seconds at which the current failure window opened.
        # ``None`` means there are no failures in flight.
        self._window_started_at: float | None = None
        # Wall-clock seconds at which the current lock expires; ``None``
        # means not locked.
        self._locked_until: float | None = None

    # -- internal helpers ------------------------------------------------

    @staticmethod
    def _resolve_now(now: float | None) -> float:
        return now if now is not None else time.monotonic()

    def _drop_expired_lock(self, now: float) -> None:
        """If the lock has expired, clear both the lock and the window."""
        if self._locked_until is not None and now >= self._locked_until:
            self._locked_until = None
            self._failure_count = 0
            self._window_started_at = None

    def _drop_expired_window(self, now: float) -> None:
        """If the window has expired without locking, reset the counter."""
        if (
            self._window_started_at is not None
            and now - self._window_started_at > self._window_seconds
        ):
            self._failure_count = 0
            self._window_started_at = None

    def _build_status(self, now: float) -> RateLimitStatus:
        if self._locked_until is not None and now < self._locked_until:
            return RateLimitStatus(
                attempts=self._failure_count,
                locked=True,
                seconds_until_unlock=self._locked_until - now,
                attempts_remaining=0,
            )
        remaining = max(0, self._max_attempts - self._failure_count)
        return RateLimitStatus(
            attempts=self._failure_count,
            locked=False,
            seconds_until_unlock=0.0,
            attempts_remaining=remaining,
        )

    # -- public API ------------------------------------------------------

    def record_failed_attempt(self, now: float | None = None) -> RateLimitStatus:
        """Record a failed PIN attempt and return the resulting status.

        If the resulting count reaches :data:`MAX_ATTEMPTS`, the lock
        is engaged for :data:`LOCK_DURATION_SECONDS` and the returned
        status carries ``locked=True``.
        """
        ts = self._resolve_now(now)
        with self._lock:
            self._drop_expired_lock(ts)
            # Already locked? Don't increment further; surface the
            # remaining-lock window. This matches the "during lock,
            # all attempts return 423" invariant.
            if self._locked_until is not None and ts < self._locked_until:
                return self._build_status(ts)
            self._drop_expired_window(ts)
            if self._window_started_at is None:
                self._window_started_at = ts
                self._failure_count = 1
            else:
                self._failure_count += 1
            if self._failure_count >= self._max_attempts:
                self._locked_until = ts + self._lock_duration_seconds
            return self._build_status(ts)

    def record_successful_attempt(self, now: float | None = None) -> None:
        """Reset the failure counter (no-op if already zero)."""
        ts = self._resolve_now(now)
        with self._lock:
            # Honour an active lock — a "successful" attempt here means
            # the verify_pin call succeeded but the API layer should
            # have rejected at the lock check. Defensive: don't drop
            # the lock on a stray success.
            if self._locked_until is not None and ts < self._locked_until:
                return
            self._failure_count = 0
            self._window_started_at = None
            self._locked_until = None

    def is_locked(self, now: float | None = None) -> bool:
        """Return True iff the lock is currently engaged."""
        ts = self._resolve_now(now)
        with self._lock:
            self._drop_expired_lock(ts)
            return self._locked_until is not None and ts < self._locked_until

    def seconds_until_unlock(self, now: float | None = None) -> float:
        """Return seconds remaining on the lock, or ``0.0`` if not locked."""
        ts = self._resolve_now(now)
        with self._lock:
            self._drop_expired_lock(ts)
            if self._locked_until is None:
                return 0.0
            return max(0.0, self._locked_until - ts)

    def status(self, now: float | None = None) -> RateLimitStatus:
        """Return a non-mutating snapshot of the current state."""
        ts = self._resolve_now(now)
        with self._lock:
            self._drop_expired_lock(ts)
            self._drop_expired_window(ts)
            return self._build_status(ts)


# Module-level singleton used by the API layer. Tests that need
# isolation construct their own :class:`PinRateLimiter` and inject it
# via the FastAPI ``dependency_overrides`` mechanism (see
# ``api/auth.py`` for the dep wiring). Lazy-init mirrors the metrics
# breaker pattern in :mod:`toybox.api.metrics` so import-time work
# stays minimal and tests that don't touch this module never construct
# a stray limiter.
_RATE_LIMITER: PinRateLimiter | None = None


def _process_rate_limiter() -> PinRateLimiter:
    """Lazy-init the process-wide PIN rate limiter."""
    global _RATE_LIMITER
    if _RATE_LIMITER is None:
        _RATE_LIMITER = PinRateLimiter()
    return _RATE_LIMITER


def get_rate_limiter() -> PinRateLimiter:
    """Return the process-wide PIN rate limiter."""
    return _process_rate_limiter()


__all__ = [
    "ATTEMPT_WINDOW_SECONDS",
    "LOCK_DURATION_SECONDS",
    "MAX_ATTEMPTS",
    "PinRateLimiter",
    "RateLimitStatus",
    "get_rate_limiter",
]
