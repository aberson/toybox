"""Unit tests for the canonical ErrorCode enum."""

from __future__ import annotations

from toybox.core.errors import ErrorCode

REQUIRED_MEMBERS = (
    "lan_bind_requires_pin",
    "invalid_display_name",
    "version_conflict",
    "ws_backpressure_drop",
    "upload_too_large",
    "unsupported_image_type",
)


def test_required_members_present() -> None:
    """Every error code documented in the plan exists on the enum."""
    members = {m.name for m in ErrorCode}
    for required in REQUIRED_MEMBERS:
        assert required in members, f"missing ErrorCode.{required}"


def test_str_enum_value_equality() -> None:
    """StrEnum members compare equal to their string values for round-tripping."""
    assert ErrorCode.lan_bind_requires_pin == "lan_bind_requires_pin"
    assert ErrorCode.upload_too_large == "upload_too_large"


def test_member_value_matches_name() -> None:
    """All values match their member names so JSON round-trips are stable."""
    for member in ErrorCode:
        assert member.value == member.name


def test_members_are_strings() -> None:
    """StrEnum members are usable as plain strings."""
    for member in ErrorCode:
        assert isinstance(member, str)
