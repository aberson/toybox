"""Capability composition.

The runtime ``is_capable()`` decision (used at every Claude call site)
factors six independent signals: listening mode, config presence, token
presence, token expiry, circuit-breaker state, rate-limit state, and
network reachability. Step 4 ships the pure composition function so
each branch is independently testable; Step 5 wires the live state.

Priority ordering matters: when multiple blockers are present at once
we surface the highest-priority one. The order is a contract — tests
in ``tests/integration/test_capability_composition.py`` pin it so
future refactors can't silently reshuffle which reason wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapabilityReason(StrEnum):
    """Why ``is_capable()`` returned False, surfaced on the ``system`` topic."""

    config_missing = "config_missing"
    token_missing = "token_missing"
    token_expired = "token_expired"
    breaker_open = "breaker_open"
    rate_limited = "rate_limited"
    network_offline = "network_offline"


@dataclass(frozen=True, slots=True)
class CapabilityState:
    """Snapshot of every signal that feeds :func:`compose_capability`."""

    listening_mode: int
    config_present: bool
    token_present: bool
    token_expired: bool
    breaker_open: bool
    rate_limited: bool
    network_online: bool


def compose_capability(state: CapabilityState) -> tuple[bool, CapabilityReason | None]:
    """Return ``(is_capable, reason_if_not)``.

    Returns ``(True, None)`` only if every condition is clear. Otherwise
    the highest-priority blocker wins so each branch is independently
    reachable in tests.

    Priority order:

    1. ``config_missing``   — mode == 1 OR not ``config_present``
    2. ``token_missing``    — not ``token_present``
    3. ``token_expired``    — ``token_expired``
    4. ``rate_limited``     — ``rate_limited``
    5. ``breaker_open``     — ``breaker_open``
    6. ``network_offline``  — not ``network_online``
    """
    if state.listening_mode == 1 or not state.config_present:
        return False, CapabilityReason.config_missing
    if not state.token_present:
        return False, CapabilityReason.token_missing
    if state.token_expired:
        return False, CapabilityReason.token_expired
    if state.rate_limited:
        return False, CapabilityReason.rate_limited
    if state.breaker_open:
        return False, CapabilityReason.breaker_open
    if not state.network_online:
        return False, CapabilityReason.network_offline
    return True, None


def current_capability_reason() -> str | None:
    """Return the active capability-reason code, or None if fully capable.

    Phase A placeholder — the real wiring (token store, breaker, network
    probe) lands in Step 5. The signature is preserved so ``/api/health``
    keeps returning the documented contract shape.
    """
    return None


__all__ = [
    "CapabilityReason",
    "CapabilityState",
    "compose_capability",
    "current_capability_reason",
]
