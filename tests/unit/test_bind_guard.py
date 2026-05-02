"""Unit tests for the LAN-bind startup guard."""

from __future__ import annotations

import pytest

from toybox.core.bind_guard import BindGuardError, check_bind_safe, is_loopback
from toybox.core.errors import ErrorCode


def test_loopback_v4_passes_without_pin() -> None:
    check_bind_safe("127.0.0.1", pin_set=False)


def test_localhost_passes_without_pin() -> None:
    check_bind_safe("localhost", pin_set=False)


def test_loopback_v6_passes_without_pin() -> None:
    check_bind_safe("::1", pin_set=False)


def test_lan_bind_without_pin_raises() -> None:
    with pytest.raises(BindGuardError) as exc_info:
        check_bind_safe("0.0.0.0", pin_set=False)
    assert exc_info.value.code is ErrorCode.lan_bind_requires_pin
    assert "lan_bind_requires_pin" in str(exc_info.value)
    assert "0.0.0.0" in str(exc_info.value)


def test_lan_bind_with_pin_passes() -> None:
    """When the parent PIN is configured, LAN binding is allowed."""
    check_bind_safe("0.0.0.0", pin_set=True)


def test_external_ip_with_pin_passes() -> None:
    check_bind_safe("192.168.1.42", pin_set=True)


def test_external_ip_without_pin_raises() -> None:
    with pytest.raises(BindGuardError):
        check_bind_safe("192.168.1.42", pin_set=False)


def test_is_loopback_helper() -> None:
    assert is_loopback("127.0.0.1")
    assert is_loopback("localhost")
    assert is_loopback("::1")
    assert not is_loopback("0.0.0.0")
    assert not is_loopback("192.168.1.42")
