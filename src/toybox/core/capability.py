"""Capability composition scaffolding.

Phase A is offline-only: there is no Claude client, no token store, no
circuit breaker. ``current_capability_reason`` always returns ``None``
(meaning "fully capable" for whatever Phase A endpoints care about) so
``/api/health`` can already return the contract shape that Phase A Step 4
will fill in for real.

When Step 4 lands, the composition logic moves here and the call site at
``api/health.py`` keeps working.
"""

from __future__ import annotations


def current_capability_reason() -> str | None:
    """Return the active capability-reason code, or None if fully capable.

    Phase A stub: always None. Phase A Step 4 replaces this with a real
    composition over (config, token, breaker, rate-limit, network) state.
    """
    return None


__all__ = ["current_capability_reason"]
