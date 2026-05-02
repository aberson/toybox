"""AI runtime layer.

Step 5 of Phase A. Wraps the Anthropic SDK behind an async ``AIClient``
protocol so call sites in steps 6-9 can land without live Claude. The
package also owns the live capability gate, in-process circuit breaker,
and background OAuth refresh task.

Public surface:

* :class:`AIClient` — Protocol every client (real or stub) implements.
* :class:`AnthropicClient` — real impl wrapping ``anthropic`` SDK.
* :class:`StubClient` — deterministic test double, no network.
* :func:`is_capable` — async live capability gate; composes signals via
  :func:`toybox.core.capability.compose_capability`.
* :class:`CircuitBreaker` — in-process open/closed/half_open breaker.
* :func:`start_refresh_loop` — background OAuth token refresh task.
"""

from __future__ import annotations

from .breaker import BreakerState, CircuitBreaker
from .capability import is_capable
from .client import AIClient, AnthropicClient, StubClient
from .oauth import (
    SECRETS_PATH,
    OAuthToken,
    load_token,
    save_token,
    secrets_path,
)
from .refresh import start_refresh_loop

__all__ = [
    "AIClient",
    "AnthropicClient",
    "BreakerState",
    "CircuitBreaker",
    "OAuthToken",
    "SECRETS_PATH",
    "StubClient",
    "is_capable",
    "load_token",
    "save_token",
    "secrets_path",
    "start_refresh_loop",
]
