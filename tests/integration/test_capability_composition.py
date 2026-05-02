"""Coverage for the Step 4 capability composition function."""

from __future__ import annotations

from dataclasses import replace

from toybox.core.capability import (
    CapabilityReason,
    CapabilityState,
    compose_capability,
)

# A baseline state where every gate is clear; tests flip one field at a time
# to assert the matching reason wins.
_CLEAR = CapabilityState(
    listening_mode=3,
    config_present=True,
    token_present=True,
    token_expired=False,
    breaker_open=False,
    rate_limited=False,
    network_online=True,
)


def test_capability_true_when_all_clear() -> None:
    assert compose_capability(_CLEAR) == (True, None)


def test_config_missing_when_mode_is_1() -> None:
    state = replace(_CLEAR, listening_mode=1)
    assert compose_capability(state) == (False, CapabilityReason.config_missing)


def test_config_missing_when_config_absent() -> None:
    state = replace(_CLEAR, config_present=False)
    assert compose_capability(state) == (False, CapabilityReason.config_missing)


def test_token_missing() -> None:
    state = replace(_CLEAR, token_present=False)
    assert compose_capability(state) == (False, CapabilityReason.token_missing)


def test_token_expired() -> None:
    state = replace(_CLEAR, token_expired=True)
    assert compose_capability(state) == (False, CapabilityReason.token_expired)


def test_rate_limited() -> None:
    state = replace(_CLEAR, rate_limited=True)
    assert compose_capability(state) == (False, CapabilityReason.rate_limited)


def test_breaker_open() -> None:
    state = replace(_CLEAR, breaker_open=True)
    assert compose_capability(state) == (False, CapabilityReason.breaker_open)


def test_network_offline() -> None:
    state = replace(_CLEAR, network_online=False)
    assert compose_capability(state) == (False, CapabilityReason.network_offline)


def test_priority_config_beats_token_missing() -> None:
    state = replace(_CLEAR, listening_mode=1, token_present=False)
    assert compose_capability(state) == (False, CapabilityReason.config_missing)


def test_priority_token_missing_beats_token_expired() -> None:
    state = replace(_CLEAR, token_present=False, token_expired=True)
    assert compose_capability(state) == (False, CapabilityReason.token_missing)


def test_priority_token_expired_beats_rate_limited() -> None:
    state = replace(_CLEAR, token_expired=True, rate_limited=True)
    assert compose_capability(state) == (False, CapabilityReason.token_expired)


def test_priority_rate_limited_beats_breaker() -> None:
    state = replace(_CLEAR, rate_limited=True, breaker_open=True)
    assert compose_capability(state) == (False, CapabilityReason.rate_limited)


def test_priority_breaker_beats_network() -> None:
    state = replace(_CLEAR, breaker_open=True, network_online=False)
    assert compose_capability(state) == (False, CapabilityReason.breaker_open)


def test_capability_reason_enum_complete() -> None:
    """Pin the canonical 6-value CapabilityReason set so adding a new value
    requires updating compose_capability priority too."""
    assert {r.value for r in CapabilityReason} == {
        "config_missing",
        "token_missing",
        "token_expired",
        "breaker_open",
        "rate_limited",
        "network_offline",
    }
