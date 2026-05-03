"""Mic device enumeration + ``TOYBOX_MIC_DEVICE_INDEX`` resolution.

Kept tiny on purpose: ``sounddevice.query_devices`` is the source of
truth and its return shape (``dict`` per device) is what callers
actually want. Everything here is just a typed wrapper plus the env
var contract documented in :mod:`toybox.audio.capture`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

_logger = logging.getLogger(__name__)

DEVICE_INDEX_ENV = "TOYBOX_MIC_DEVICE_INDEX"


def resolve_device(env_value: str | None) -> int | None:
    """Translate the env-var string to a sounddevice device index.

    ``None`` (env unset) and the literal string ``"default"`` (any
    case) both resolve to ``None`` → use the system default. Any
    other value must parse as a non-negative int. An unparseable
    value logs a WARNING and falls back to ``None`` so the daemon
    still boots on the system mic.
    """
    if env_value is None:
        return None
    raw = env_value.strip()
    if raw == "" or raw.lower() == "default":
        return None
    try:
        idx = int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int; falling back to system default device",
            DEVICE_INDEX_ENV,
            env_value,
        )
        return None
    if idx < 0:
        _logger.warning(
            "%s=%d must be >= 0; falling back to system default device",
            DEVICE_INDEX_ENV,
            idx,
        )
        return None
    return idx


def device_index_from_env() -> int | None:
    """Convenience: read ``TOYBOX_MIC_DEVICE_INDEX`` from the environment."""
    return resolve_device(os.environ.get(DEVICE_INDEX_ENV))


def query_devices() -> list[dict[str, Any]]:
    """Return the full ``sounddevice.query_devices()`` list.

    Imported lazily so the audio package can be imported (and tested)
    on machines without a working PortAudio install.
    """
    import sounddevice as sd  # noqa: PLC0415  — lazy import is intentional

    raw = sd.query_devices()
    # ``query_devices()`` returns a ``DeviceList`` (list of dicts) when
    # called with no args.
    return [dict(d) for d in raw]


def device_name(index: int | None) -> str:
    """Human-readable device name for logs / the operator script."""
    import sounddevice as sd  # noqa: PLC0415

    info = sd.query_devices(index, "input") if index is not None else sd.query_devices(kind="input")
    if isinstance(info, dict):
        name = info.get("name")
        if isinstance(name, str):
            return name
    return f"<device {index!r}>"


__all__ = [
    "DEVICE_INDEX_ENV",
    "device_index_from_env",
    "device_name",
    "query_devices",
    "resolve_device",
]
