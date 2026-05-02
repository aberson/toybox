"""Application-level ping/pong heartbeat.

Starlette doesn't expose RFC-6455 control frames at the app layer, so
the heartbeat is a small JSON dance:

* server periodically sends ``{"type": "ping", "ts": <iso>}``
* client must reply with ``{"type": "pong"}`` within a configurable
  timeout
* if no pong arrives in time, the connection is closed with code 1011

Two env vars tune the cadence:

* ``TOYBOX_WS_PING_INTERVAL_SEC`` (default 20) — how often the server
  sends a ping while the client is silent.
* ``TOYBOX_WS_PING_TIMEOUT_SEC`` (default 30) — close-on-no-pong
  threshold, measured from the last successful receive (any frame).

Tests pass tiny intervals (e.g. 0.05s ping / 0.1s timeout) so the
test suite never spends 50 seconds waiting for the production
defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

PING_INTERVAL_ENV = "TOYBOX_WS_PING_INTERVAL_SEC"
PING_TIMEOUT_ENV = "TOYBOX_WS_PING_TIMEOUT_SEC"
DEFAULT_PING_INTERVAL_SEC = 20.0
DEFAULT_PING_TIMEOUT_SEC = 30.0


@dataclass(frozen=True, slots=True)
class HeartbeatConfig:
    """Resolved ping/timeout cadence for the ws server."""

    ping_interval_sec: float
    ping_timeout_sec: float


def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def heartbeat_config() -> HeartbeatConfig:
    """Read the env-tuned heartbeat config (or the defaults)."""
    return HeartbeatConfig(
        ping_interval_sec=_read_float_env(PING_INTERVAL_ENV, DEFAULT_PING_INTERVAL_SEC),
        ping_timeout_sec=_read_float_env(PING_TIMEOUT_ENV, DEFAULT_PING_TIMEOUT_SEC),
    )


__all__ = [
    "DEFAULT_PING_INTERVAL_SEC",
    "DEFAULT_PING_TIMEOUT_SEC",
    "HeartbeatConfig",
    "PING_INTERVAL_ENV",
    "PING_TIMEOUT_ENV",
    "heartbeat_config",
]
