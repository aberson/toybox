"""LAN-binding startup guard.

Exposing the backend on a non-loopback host without a parent PIN configured
would leak the family-private API to any device on the LAN. This module
refuses such configurations at startup, with a stable error code so the
frontend (and operators) can detect the failure mode.

Step 21 wires the real PIN check through :func:`toybox.core.pin.pin_is_set`,
re-exported here so the call site in :mod:`toybox.main` stays a one-liner
without having to import both modules.
"""

from __future__ import annotations

import sqlite3

from .errors import ErrorCode
from .pin import pin_is_set as _pin_is_set

# Hosts that do not expose the service to the LAN.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class BindGuardError(RuntimeError):
    """Raised when the configured bind host requires a PIN that is not set."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.code = ErrorCode.lan_bind_requires_pin
        super().__init__(
            f"refusing to bind on non-loopback host {host!r} without a parent PIN "
            f"(code={self.code.value})"
        )


def is_loopback(host: str) -> bool:
    """Return True iff ``host`` is one of the loopback aliases we trust."""
    return host in _LOOPBACK_HOSTS


def check_bind_safe(host: str, *, pin_set: bool) -> None:
    """Raise :class:`BindGuardError` if ``host`` is non-loopback and no PIN is set.

    Returns ``None`` on success so the call site can use it as a startup
    invariant: ``check_bind_safe(host, pin_set=settings.pin_set)``.
    """
    if is_loopback(host):
        return
    if pin_set:
        return
    raise BindGuardError(host)


def pin_is_set(conn: sqlite3.Connection) -> bool:
    """Re-export of :func:`toybox.core.pin.pin_is_set`.

    Kept here so the bind-guard call site in :mod:`toybox.main` only
    has to import this module: ``check_bind_safe(host, pin_set=pin_is_set(conn))``
    reads as a single startup invariant.
    """
    return _pin_is_set(conn)


__all__ = ["BindGuardError", "check_bind_safe", "is_loopback", "pin_is_set"]
