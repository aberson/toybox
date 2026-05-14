"""In-process circuit breaker for the Claude call site.

The single-uvicorn-worker invariant means breaker state lives in this
process — no Redis, no shared cache. States:

* ``closed``    — normal operation; calls go through.
* ``open``      — recent failures opened the breaker; reject without a call.
* ``half_open`` — cooldown elapsed; one probe is allowed; on success →
                  closed, on failure → open with a fresh cooldown.

A 429 response opens the breaker immediately (regardless of consecutive
failure count) and honors the ``Retry-After`` header value as the
cooldown duration.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from enum import StrEnum

_logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 3
_DEFAULT_COOLDOWN_SEC = 60.0
_COOLDOWN_ENV = "TOYBOX_CLAUDE_BREAKER_COOLDOWN_SEC"
_THRESHOLD_ENV = "TOYBOX_CLAUDE_BREAKER_THRESHOLD"


class BreakerState(StrEnum):
    """Public state names. ``closed`` means closed-circuit i.e. healthy."""

    closed = "closed"
    open = "open"
    half_open = "half_open"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        _logger.warning("%s=%r is not a float; using %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _logger.warning("%s=%r is not an int; using %d", name, raw, default)
        return default


class CircuitBreaker:
    """open/closed/half_open breaker with consecutive-failure threshold.

    The breaker is intentionally synchronous — it's read on every Claude
    call and gates an async wrapper. ``time_source`` is injectable so
    tests can drive the cooldown without ``asyncio.sleep``.

    Attributes:
        threshold: Consecutive failures before the breaker opens. Defaults
            to ``TOYBOX_CLAUDE_BREAKER_THRESHOLD`` (env) or 3.
        cooldown_sec: Seconds the breaker stays open before a half-open
            probe is allowed. Defaults to
            ``TOYBOX_CLAUDE_BREAKER_COOLDOWN_SEC`` (env) or 60s.
    """

    def __init__(
        self,
        *,
        threshold: int | None = None,
        cooldown_sec: float | None = None,
        time_source: Callable[[], float] | None = None,
    ) -> None:
        self.threshold = (
            threshold if threshold is not None else _env_int(_THRESHOLD_ENV, _DEFAULT_THRESHOLD)
        )
        self.cooldown_sec = (
            cooldown_sec
            if cooldown_sec is not None
            else _env_float(_COOLDOWN_ENV, _DEFAULT_COOLDOWN_SEC)
        )
        self._time = time_source if time_source is not None else time.monotonic
        self._state: BreakerState = BreakerState.closed
        self._consecutive_failures: int = 0
        # ``_opened_at`` is the monotonic timestamp at which the current
        # open window started; ``_cooldown_until`` is when it ends. Both
        # are 0 in the closed state.
        self._cooldown_until: float = 0.0
        # True iff the most-recent open transition was caused by a 429.
        # The capability layer reads this to surface ``rate_limited``
        # instead of ``breaker_open``. Reset on success.
        self._rate_limited: bool = False

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @property
    def state(self) -> BreakerState:
        """Return the current breaker state, advancing open→half_open if due.

        Reading the state is the only way the breaker auto-transitions
        from open to half_open; that transition does NOT yet allow a
        probe — call :meth:`try_half_open` for that.
        """
        if self._state is BreakerState.open and self._time() >= self._cooldown_until:
            self._state = BreakerState.half_open
        return self._state

    def is_open(self) -> bool:
        """Return True iff the breaker is open (cooldown not yet elapsed)."""
        return self.state is BreakerState.open

    def is_rate_limited(self) -> bool:
        """Return True iff the breaker is open due to a recent 429.

        The capability gate uses this to surface ``rate_limited`` (which
        outranks ``breaker_open`` in the priority table) when the open
        cause was specifically a 429 with ``Retry-After``.
        """
        return self.is_open() and self._rate_limited

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def record_success(self) -> None:
        """Successful call. Resets the failure counter and closes the breaker."""
        self._consecutive_failures = 0
        self._state = BreakerState.closed
        self._cooldown_until = 0.0
        self._rate_limited = False

    def record_failure(self) -> None:
        """Non-429 failure. Opens the breaker once ``threshold`` is hit.

        Note that ``try_half_open()`` eagerly flips state back to ``open``
        the moment it claims the probe slot, so by the time a probe
        failure arrives here ``self._state`` is already ``open``. The
        threshold check below catches that case (one failure on top of
        the prior threshold-tripping run is still ≥ threshold), so we
        intentionally do NOT branch on ``half_open`` here.
        """
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.threshold:
            self._open(self.cooldown_sec, rate_limited=False)

    def record_429(self, retry_after: float | None) -> None:
        """Rate-limit response. Opens the breaker for ``retry_after`` seconds.

        ``None`` (no ``Retry-After`` header) falls back to ``cooldown_sec``.
        Zero, negative, or NaN values are clamped to ``cooldown_sec`` — a
        zero cooldown would auto-transition straight to ``half_open`` on
        the next state read, which defeats the breaker.
        """
        cooldown = self.cooldown_sec
        if retry_after is not None and retry_after > 0:
            cooldown = retry_after
        # 429 also counts as a failure for telemetry parity, though the
        # immediate open transition is what matters here.
        self._consecutive_failures += 1
        self._open(cooldown, rate_limited=True)

    def try_half_open(self) -> bool:
        """If the breaker is half_open, claim the probe slot and return True.

        Concurrent callers compete for the slot — the first to call wins
        and gets True, subsequent callers see False until the probe
        resolves to ``record_success`` or ``record_failure``. (In a
        single-uvicorn-worker, single-flight pattern this is enough.)
        """
        # Touch state so open→half_open transition can fire.
        current = self.state
        if current is BreakerState.half_open:
            # Prevent a second concurrent probe by transitioning back to
            # open with a fresh cooldown — the probe completes via
            # record_success() (→ closed) or record_failure() (→ open
            # with another fresh cooldown). The second concurrent caller
            # sees open and is rejected; only the first wins the slot.
            self._state = BreakerState.open
            self._cooldown_until = self._time() + self.cooldown_sec
            return True
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _open(self, cooldown_sec: float, *, rate_limited: bool) -> None:
        self._state = BreakerState.open
        self._cooldown_until = self._time() + cooldown_sec
        self._rate_limited = rate_limited


_LOCAL_COOLDOWN_ENV = "TOYBOX_LOCAL_BREAKER_COOLDOWN_SEC"
_LOCAL_THRESHOLD_ENV = "TOYBOX_LOCAL_BREAKER_THRESHOLD"

_local_breaker: CircuitBreaker | None = None


def get_local_breaker() -> CircuitBreaker:
    """Return the process-wide local-adapter breaker, lazily constructed.

    Per-adapter independence is the load-bearing invariant: this
    breaker instance is fully separate from any Claude
    :class:`CircuitBreaker` constructed at the API call sites, so
    tripping one does NOT affect the other. The local probe in
    :func:`toybox.ai.capability.is_local_capable` records failures /
    successes against this instance directly. Threshold + cooldown
    read from their own env vars so an operator can tune Claude and
    local breakers independently -- the Claude path's
    :data:`_THRESHOLD_ENV` must NOT be inherited.
    """
    global _local_breaker
    if _local_breaker is None:
        _local_breaker = CircuitBreaker(
            threshold=_env_int(_LOCAL_THRESHOLD_ENV, _DEFAULT_THRESHOLD),
            cooldown_sec=_env_float(_LOCAL_COOLDOWN_ENV, _DEFAULT_COOLDOWN_SEC),
        )
    return _local_breaker


def reset_local_breaker_for_tests() -> None:
    """Drop the cached local breaker. Used by test fixtures."""
    global _local_breaker
    _local_breaker = None


__all__ = [
    "BreakerState",
    "CircuitBreaker",
    "get_local_breaker",
    "reset_local_breaker_for_tests",
]
