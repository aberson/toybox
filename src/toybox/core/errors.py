"""Stable error codes shared between backend and frontend.

The TypeScript counterpart at ``frontend/src/shared/errors.ts`` is generated
from this module by ``tools/gen_types_ts.py``. Do not edit the generated file
by hand; regenerate after adding or removing any member.

All values match their member names so the StrEnum round-trips through JSON
and HTTP error envelopes without surprises.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Canonical error codes surfaced via API + ws envelopes."""

    lan_bind_requires_pin = "lan_bind_requires_pin"
    invalid_display_name = "invalid_display_name"
    version_conflict = "version_conflict"
    ws_backpressure_drop = "ws_backpressure_drop"
    upload_too_large = "upload_too_large"
    unsupported_image_type = "unsupported_image_type"


__all__ = ["ErrorCode"]
