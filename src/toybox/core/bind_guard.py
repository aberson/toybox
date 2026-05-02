"""LAN-binding startup guard.

Exposing the backend on a non-loopback host without a parent PIN configured
would leak the family-private API to any device on the LAN. This module
refuses such configurations at startup, with a stable error code so the
frontend (and operators) can detect the failure mode.

Phase A note: the parent PIN system is not implemented yet. Until Phase D
ships PIN auth, ``pin_set`` is always ``False`` in production code, so the
guard is effectively "loopback-only." The ``pin_set`` parameter is plumbed
through now to keep the call sites honest once the PIN is real.
"""

from __future__ import annotations

from .errors import ErrorCode

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


__all__ = ["BindGuardError", "check_bind_safe", "is_loopback"]
