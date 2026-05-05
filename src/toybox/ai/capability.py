"""Live capability gate.

Gathers the six runtime signals the pure
:func:`toybox.core.capability.compose_capability` function consumes:

* listening mode (parent slider)        — ``settings.listening_mode``
* config presence                       — ``TOYBOX_CLAUDE_TEXT_MODEL`` env
* token presence + token expiry         — ``~/.toybox/secrets.json``
* breaker state                         — :class:`CircuitBreaker`
* recent 429 / rate-limited             — derived from breaker open-cause
* network reachability                  — TCP probe to api.anthropic.com

The pure composition function is reused as-is — DO NOT redefine
``CapabilityReason`` here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sqlite3
import time
from collections.abc import Awaitable, Callable

from ..core.capability import (
    CapabilityReason,
    CapabilityState,
    compose_capability,
)
from ..core.listening import ListeningMode, current_mode
from ..db import connect, resolve_db_path
from .breaker import CircuitBreaker
from .client import TEXT_MODEL_ENV
from .oauth import OAuthToken, load_token

_logger = logging.getLogger(__name__)

NetworkProbe = Callable[[], Awaitable[bool]]

_DEFAULT_PROBE_HOST = "api.anthropic.com"
_DEFAULT_PROBE_PORT = 443
_DEFAULT_PROBE_TIMEOUT_SEC = 2.0


def _connect_probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def default_network_probe() -> bool:
    """Default reachability probe — TCP to api.anthropic.com:443.

    Tests substitute a stub returning False (or True) so they don't have
    to hit real DNS. The blocking ``socket.create_connection`` runs in
    ``asyncio.to_thread`` so we don't stall the event loop.
    """
    return await asyncio.to_thread(
        _connect_probe, _DEFAULT_PROBE_HOST, _DEFAULT_PROBE_PORT, _DEFAULT_PROBE_TIMEOUT_SEC
    )


def _config_present() -> bool:
    """True iff the minimal pinned-model env var is non-empty.

    The default fallback in :mod:`toybox.ai.client` means we can run
    without explicitly setting it, BUT the capability matrix demands a
    way to surface ``config_missing``. We treat an empty string as
    explicitly-cleared to give operators a simple kill-switch.
    """
    raw = os.environ.get(TEXT_MODEL_ENV)
    if raw is None:
        # Unset → fall back to default model → config IS present.
        return True
    return bool(raw.strip())


def _read_listening_mode() -> int:
    """Read the persisted listening mode, defaulting to OFFLINE on error.

    A DB error here is benign at the capability layer — it just blocks
    AI calls until the DB is reachable, which is the same protective
    behavior we want for ``listening_mode == 1``.
    """
    try:
        conn = connect(resolve_db_path())
    except sqlite3.Error as exc:
        _logger.warning("capability: cannot open DB to read mode: %s", exc)
        return int(ListeningMode.OFFLINE)
    try:
        return int(current_mode(conn))
    except (sqlite3.Error, ValueError) as exc:
        _logger.warning("capability: cannot read listening mode: %s", exc)
        return int(ListeningMode.OFFLINE)
    finally:
        conn.close()


def _gather_token() -> tuple[bool, bool]:
    """Return ``(token_present, token_expired)``."""
    token: OAuthToken | None = load_token()
    if token is None:
        return False, False
    return True, token.is_expired(int(time.time()))


async def is_capable(
    breaker: CircuitBreaker,
    *,
    network_probe: NetworkProbe | None = None,
    listening_mode: int | None = None,
) -> tuple[bool, CapabilityReason | None]:
    """Gather live signals and call :func:`compose_capability`.

    Args:
        breaker: The shared in-process :class:`CircuitBreaker`.
        network_probe: Optional override; defaults to a TCP probe of
            ``api.anthropic.com:443``. Tests pass a stub returning the
            desired state without hitting DNS.
        listening_mode: Optional override; if ``None`` the mode is read
            from the SQLite settings row. Tests pass an int directly to
            avoid touching the DB.

    Returns:
        ``(True, None)`` if every gate is clear; otherwise
        ``(False, reason)`` where ``reason`` is the highest-priority
        :class:`CapabilityReason`.
    """
    probe = network_probe if network_probe is not None else default_network_probe

    mode = listening_mode if listening_mode is not None else _read_listening_mode()
    config_present = _config_present()
    token_present, token_expired = _gather_token()
    # A 429-induced OPEN surfaces as ``rate_limited`` (priority above
    # ``breaker_open``); a failure-induced OPEN surfaces as
    # ``breaker_open``. Both come from the same breaker — the
    # ``is_rate_limited()`` predicate distinguishes them.
    rate_limited = breaker.is_rate_limited()
    breaker_open = breaker.is_open() and not rate_limited
    network_online = await probe()

    state = CapabilityState(
        listening_mode=mode,
        config_present=config_present,
        token_present=token_present,
        token_expired=token_expired,
        breaker_open=breaker_open,
        rate_limited=rate_limited,
        network_online=network_online,
    )
    return compose_capability(state)


async def is_capable_from_state(state: CapabilityState) -> tuple[bool, CapabilityReason | None]:
    """Direct passthrough useful for tests that build state by hand."""
    return compose_capability(state)


# Cause string returned by :func:`is_local_capable` until E1c lands the
# real probe. Centralised so the integration test that pins the carve-
# out behavior matches the actual return value.
LOCAL_NOT_INSTALLED_REASON: str = "local runtime not yet installed"


async def is_local_capable() -> tuple[bool, str | None]:
    """Phase E carve-out stub: local runtime is never capable yet.

    Returns ``(False, "local runtime not yet installed")`` until E1c
    lands the real ``HTTP GET /v1/models`` probe + the per-adapter
    breaker integration. The Claude :func:`is_capable` is unchanged;
    the two probes are intentionally orthogonal so a Claude breaker
    trip can't disable the local path (and vice versa).

    The shape mirrors :func:`is_capable` (``(bool, reason)``); we use
    a plain ``str`` instead of ``CapabilityReason`` because the local
    probe's failure causes don't overlap the Claude reason enum and
    we don't want a fake enum entry stored on the labeled_events
    capability column.
    """
    return False, LOCAL_NOT_INSTALLED_REASON


__all__ = [
    "LOCAL_NOT_INSTALLED_REASON",
    "NetworkProbe",
    "default_network_probe",
    "is_capable",
    "is_capable_from_state",
    "is_local_capable",
]
