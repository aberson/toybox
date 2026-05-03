"""Min-interval throttle for the Claude call site.

The throttle is a single-resource gate: at most one acquire per
``interval_sec`` window. It is **not** a queue — callers that are
turned away should fall back to another path (offline generator)
rather than wait. Holding callers blocked while the model is busy is
the wrong shape: the dispatcher's job is to make a decision now and
move on, not to bunch up future activities.

The clock is injectable so tests can drive the window without real
sleeps. ``threading.Lock`` guards the acquire/check race; callers run
single-tasked today (the escalation dispatcher consumes one transcript
at a time), but defensive locking keeps the surface honest if a future
caller fires from multiple tasks/threads.

Env vars live next to their consumer per project convention — Step
14's escalation dispatcher reads :data:`CLAUDE_MIN_INTERVAL_SEC_ENV`
when constructing its throttle.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Final

_logger = logging.getLogger(__name__)

CLAUDE_MIN_INTERVAL_SEC_ENV: Final[str] = "TOYBOX_CLAUDE_MIN_INTERVAL_SEC"
DEFAULT_CLAUDE_MIN_INTERVAL_SEC: Final[float] = 30.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        _logger.warning("%s=%r is not a float; using %s", name, raw, default)
        return default


def min_interval_from_env() -> float:
    """Resolve the Claude min-interval from the environment.

    Falls back to :data:`DEFAULT_CLAUDE_MIN_INTERVAL_SEC` on missing /
    unparseable values. Negative values are clamped to ``0.0`` because
    a negative interval is meaningless — every acquire would succeed,
    which is what ``0.0`` already means.
    """
    value = _env_float(CLAUDE_MIN_INTERVAL_SEC_ENV, DEFAULT_CLAUDE_MIN_INTERVAL_SEC)
    if value < 0.0:
        _logger.warning(
            "%s=%s is negative; clamping to 0.0",
            CLAUDE_MIN_INTERVAL_SEC_ENV,
            value,
        )
        return 0.0
    return value


class MinIntervalThrottle:
    """At most one acquire per ``interval_sec``.

    The first :meth:`try_acquire` always succeeds; subsequent acquires
    succeed only after ``interval_sec`` has elapsed since the last
    successful acquire. ``interval_sec=0.0`` disables throttling
    entirely (every call returns ``True``).

    Args:
        interval_sec: Minimum gap between successful acquires. Must be
            ``>= 0``. Negative values raise :class:`ValueError`.
        clock: Time source returning seconds. Defaults to
            :func:`time.monotonic`. Tests inject a fake clock so the
            interval can be advanced deterministically.
    """

    def __init__(
        self,
        interval_sec: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if interval_sec < 0.0:
            raise ValueError(f"interval_sec must be >= 0, got {interval_sec}")
        self._interval = float(interval_sec)
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.Lock()
        # ``None`` sentinel: no acquire yet. We don't initialise to
        # ``-inf`` because an injected clock may return a finite value
        # below 0.0 and we want the first acquire to always pass without
        # arithmetic on a sentinel constant.
        self._last_acquire: float | None = None

    @property
    def interval_sec(self) -> float:
        return self._interval

    def try_acquire(self) -> bool:
        """Return True iff the throttle window has elapsed; record the acquire.

        On True the caller "owns" the slot for the next ``interval_sec``
        seconds — i.e. subsequent calls in that window return False.
        """
        with self._lock:
            now = self._clock()
            if self._last_acquire is None or (now - self._last_acquire) >= self._interval:
                self._last_acquire = now
                return True
            return False

    def time_until_next(self) -> float:
        """Seconds until the next :meth:`try_acquire` would succeed.

        Returns ``0.0`` if the next acquire would succeed immediately
        (no prior acquire, or window already elapsed). Negative gaps are
        clamped to ``0.0``.
        """
        with self._lock:
            if self._last_acquire is None:
                return 0.0
            elapsed = self._clock() - self._last_acquire
            remaining = self._interval - elapsed
            return max(0.0, remaining)

    def reset(self) -> None:
        """Forget the last acquire. Test hook; production rarely needs it."""
        with self._lock:
            self._last_acquire = None


__all__ = [
    "CLAUDE_MIN_INTERVAL_SEC_ENV",
    "DEFAULT_CLAUDE_MIN_INTERVAL_SEC",
    "MinIntervalThrottle",
    "min_interval_from_env",
]
