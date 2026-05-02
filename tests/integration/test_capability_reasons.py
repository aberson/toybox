"""Live capability-gate coverage for Step 5.

These tests focus on the ``ai/capability.is_capable()`` LIVE
state-gathering layer — the pure ``compose_capability`` function is
already pinned in ``test_capability_composition.py`` and we don't
duplicate that here.

Each test arranges the live signals (env, on-disk token, breaker, mode,
network probe) so a single capability reason wins, then asserts the
expected ``(capable, reason)`` tuple.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from toybox.ai.breaker import CircuitBreaker
from toybox.ai.capability import is_capable
from toybox.ai.oauth import OAuthToken, save_token
from toybox.core.capability import CapabilityReason
from toybox.core.listening import ListeningMode

NetworkProbe = Callable[[], Awaitable[bool]]


def _online_probe() -> NetworkProbe:
    async def _probe() -> bool:
        return True

    return _probe


def _offline_probe() -> NetworkProbe:
    async def _probe() -> bool:
        return False

    return _probe


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ``~/.toybox/secrets.json`` at a tmp dir for isolation.

    We use the documented ``TOYBOX_SECRETS_PATH`` env override so the
    real file in ``~/.toybox/`` is never touched by tests.
    """
    secrets = tmp_path / "secrets.json"
    monkeypatch.setenv("TOYBOX_SECRETS_PATH", str(secrets))
    yield secrets


@pytest.fixture
def with_text_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the config-present check return True by default."""
    monkeypatch.setenv("TOYBOX_CLAUDE_TEXT_MODEL", "claude-sonnet-4-6")


def _valid_token(ttl_sec: int = 3600) -> OAuthToken:
    return OAuthToken(
        access_token="acc-tok",
        refresh_token="ref-tok",
        expires_at=int(time.time()) + ttl_sec,
    )


@pytest.mark.usefixtures("secrets_dir", "with_text_model")
async def test_capable_true_when_all_clear(secrets_dir: Path) -> None:
    save_token(_valid_token(), secrets_dir)
    breaker = CircuitBreaker()
    capable, reason = await is_capable(
        breaker,
        network_probe=_online_probe(),
        listening_mode=int(ListeningMode.DEFAULT),
    )
    assert capable is True
    assert reason is None


@pytest.mark.usefixtures("secrets_dir")
async def test_config_missing_when_text_model_blank(
    monkeypatch: pytest.MonkeyPatch, secrets_dir: Path
) -> None:
    monkeypatch.setenv("TOYBOX_CLAUDE_TEXT_MODEL", "   ")
    save_token(_valid_token(), secrets_dir)
    breaker = CircuitBreaker()
    capable, reason = await is_capable(
        breaker,
        network_probe=_online_probe(),
        listening_mode=int(ListeningMode.DEFAULT),
    )
    assert capable is False
    assert reason is CapabilityReason.config_missing


@pytest.mark.usefixtures("secrets_dir", "with_text_model")
async def test_config_missing_when_mode_is_offline(secrets_dir: Path) -> None:
    save_token(_valid_token(), secrets_dir)
    breaker = CircuitBreaker()
    capable, reason = await is_capable(
        breaker,
        network_probe=_online_probe(),
        listening_mode=int(ListeningMode.OFFLINE),
    )
    assert capable is False
    assert reason is CapabilityReason.config_missing


@pytest.mark.usefixtures("secrets_dir", "with_text_model")
async def test_token_missing_when_secrets_absent(secrets_dir: Path) -> None:
    # secrets_dir points at a tmp path; we deliberately do NOT save.
    assert not secrets_dir.exists()
    breaker = CircuitBreaker()
    capable, reason = await is_capable(
        breaker,
        network_probe=_online_probe(),
        listening_mode=int(ListeningMode.DEFAULT),
    )
    assert capable is False
    assert reason is CapabilityReason.token_missing


@pytest.mark.usefixtures("secrets_dir", "with_text_model")
async def test_token_expired_when_past_expires_at(secrets_dir: Path) -> None:
    save_token(_valid_token(ttl_sec=-10), secrets_dir)
    breaker = CircuitBreaker()
    capable, reason = await is_capable(
        breaker,
        network_probe=_online_probe(),
        listening_mode=int(ListeningMode.DEFAULT),
    )
    assert capable is False
    assert reason is CapabilityReason.token_expired


@pytest.mark.usefixtures("secrets_dir", "with_text_model")
async def test_breaker_open_after_consecutive_failures(secrets_dir: Path) -> None:
    save_token(_valid_token(), secrets_dir)
    breaker = CircuitBreaker(threshold=2, cooldown_sec=60.0)
    breaker.record_failure()
    breaker.record_failure()  # threshold hit → OPEN, NOT rate-limited
    capable, reason = await is_capable(
        breaker,
        network_probe=_online_probe(),
        listening_mode=int(ListeningMode.DEFAULT),
    )
    assert capable is False
    assert reason is CapabilityReason.breaker_open


@pytest.mark.usefixtures("secrets_dir", "with_text_model")
async def test_rate_limited_after_429(secrets_dir: Path) -> None:
    save_token(_valid_token(), secrets_dir)
    breaker = CircuitBreaker(cooldown_sec=60.0)
    breaker.record_429(retry_after=30.0)  # OPEN with rate-limited flag
    capable, reason = await is_capable(
        breaker,
        network_probe=_online_probe(),
        listening_mode=int(ListeningMode.DEFAULT),
    )
    assert capable is False
    assert reason is CapabilityReason.rate_limited


@pytest.mark.usefixtures("secrets_dir", "with_text_model")
async def test_network_offline(secrets_dir: Path) -> None:
    save_token(_valid_token(), secrets_dir)
    breaker = CircuitBreaker()
    capable, reason = await is_capable(
        breaker,
        network_probe=_offline_probe(),
        listening_mode=int(ListeningMode.DEFAULT),
    )
    assert capable is False
    assert reason is CapabilityReason.network_offline


@pytest.mark.usefixtures("secrets_dir", "with_text_model")
async def test_priority_token_missing_beats_network_offline(secrets_dir: Path) -> None:
    """Smoke check that the live wiring obeys the pinned priority."""
    breaker = CircuitBreaker()
    capable, reason = await is_capable(
        breaker,
        network_probe=_offline_probe(),
        listening_mode=int(ListeningMode.DEFAULT),
    )
    # No token saved → token_missing wins over network_offline.
    assert capable is False
    assert reason is CapabilityReason.token_missing


async def test_token_missing_when_secrets_file_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed JSON behaves like a missing file (capability matrix says
    ``token_missing``, not a hard crash)."""
    secrets = tmp_path / "secrets.json"
    secrets.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("TOYBOX_SECRETS_PATH", str(secrets))
    monkeypatch.setenv("TOYBOX_CLAUDE_TEXT_MODEL", "claude-sonnet-4-6")
    breaker = CircuitBreaker()
    capable, reason = await is_capable(
        breaker,
        network_probe=_online_probe(),
        listening_mode=int(ListeningMode.DEFAULT),
    )
    assert capable is False
    assert reason is CapabilityReason.token_missing
