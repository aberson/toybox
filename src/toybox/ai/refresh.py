"""Background OAuth-token refresh loop.

A long-lived ``asyncio.Task`` polls the on-disk token's ``expires_at``
once a minute. When the token is within
``TOYBOX_OAUTH_REFRESH_LEAD_SEC`` (default 300s) of expiry it kicks off
a refresh via the supplied client. Refresh failures are logged at
WARNING and swallowed — the loop must NEVER raise into the parent
event loop, and a transient network blip should not bring the host
process down.

Tests inject a fake refresher and a fake clock so the polling cadence
doesn't slow them down.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable

from .oauth import OAuthToken, load_token, save_token

_logger = logging.getLogger(__name__)

_LEAD_SEC_ENV = "TOYBOX_OAUTH_REFRESH_LEAD_SEC"
_DEFAULT_LEAD_SEC = 300
_DEFAULT_POLL_SEC = 60.0

# A "refresher" takes the current token + returns a freshly-rotated one.
# Tests substitute a deterministic stub; production wires this to the
# real Anthropic refresh endpoint inside :class:`AnthropicClient`.
Refresher = Callable[[OAuthToken], Awaitable[OAuthToken]]


def _lead_sec() -> int:
    raw = os.environ.get(_LEAD_SEC_ENV)
    if raw is None:
        return _DEFAULT_LEAD_SEC
    try:
        return int(raw)
    except ValueError:
        _logger.warning("%s=%r is not an int; using %d", _LEAD_SEC_ENV, raw, _DEFAULT_LEAD_SEC)
        return _DEFAULT_LEAD_SEC


async def _refresh_once(
    refresher: Refresher,
    *,
    now_epoch: int,
    lead_sec: int,
    on_refresh: Callable[[OAuthToken], None] | None = None,
) -> bool:
    """One pass of the polling loop. Public for testability.

    Returns True iff a refresh was actually attempted (regardless of
    outcome). False means the token was either missing or still fresh.
    """
    token = load_token()
    if token is None:
        return False
    if token.expires_at - now_epoch >= lead_sec:
        return False
    try:
        new_token = await refresher(token)
    except Exception as exc:  # noqa: BLE001 - intentional broad swallow
        _logger.warning("oauth refresh failed: %s", exc)
        return True
    save_token(new_token)
    if on_refresh is not None:
        try:
            on_refresh(new_token)
        except Exception as exc:  # noqa: BLE001 - hook must not crash loop
            _logger.warning("oauth refresh hook raised: %s", exc)
    return True


async def _refresh_loop(
    refresher: Refresher,
    *,
    poll_sec: float,
    on_refresh: Callable[[OAuthToken], None] | None,
) -> None:
    """Long-running coroutine. Cancellation is the clean shutdown path."""
    lead = _lead_sec()
    try:
        while True:
            try:
                await _refresh_once(
                    refresher,
                    now_epoch=int(time.time()),
                    lead_sec=lead,
                    on_refresh=on_refresh,
                )
            except Exception as exc:  # noqa: BLE001 - never crash the loop
                _logger.warning("oauth refresh tick raised: %s", exc)
            await asyncio.sleep(poll_sec)
    except asyncio.CancelledError:
        # Clean shutdown — re-raise so the awaiter sees CancelledError
        # but don't log a warning. This is the documented exit path.
        raise


def start_refresh_loop(
    refresher: Refresher,
    *,
    poll_sec: float = _DEFAULT_POLL_SEC,
    on_refresh: Callable[[OAuthToken], None] | None = None,
) -> asyncio.Task[None]:
    """Spawn the background refresh task.

    Args:
        refresher: Async callable that exchanges the current token for a
            fresh one. The caller is responsible for hitting the real
            Anthropic refresh endpoint; the loop only handles polling +
            persistence + logging.
        poll_sec: Polling cadence, default 60s. Tests pass a small value.
        on_refresh: Optional sync hook invoked AFTER the new token is
            persisted. The :class:`AnthropicClient` wires this to its
            ``update_token`` so the SDK rebuilds with the new bearer.

    Returns:
        The ``asyncio.Task`` running the loop. Cancel it for a clean
        shutdown — the task swallows ``CancelledError`` cleanly with
        no warnings emitted.
    """
    return asyncio.create_task(
        _refresh_loop(refresher, poll_sec=poll_sec, on_refresh=on_refresh),
        name="toybox.ai.refresh",
    )


__all__ = ["Refresher", "start_refresh_loop"]
