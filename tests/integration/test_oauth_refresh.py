"""Coverage for the OAuth on-disk shim and the background refresh loop.

We don't poll the real loop interval (60s by default) — tests inject a
sub-second poll cadence and a stub refresher so the round-trip happens
in well under a second.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.ai.oauth import OAuthToken, load_token, save_token
from toybox.ai.refresh import _refresh_once, start_refresh_loop


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    secrets = tmp_path / "secrets.json"
    monkeypatch.setenv("TOYBOX_SECRETS_PATH", str(secrets))
    yield secrets


def test_save_and_load_round_trip(secrets_dir: Path) -> None:
    token = OAuthToken(access_token="a", refresh_token="r", expires_at=12345)
    save_token(token)
    loaded = load_token()
    assert loaded == token


def test_load_token_missing_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOYBOX_SECRETS_PATH", str(tmp_path / "absent.json"))
    assert load_token() is None


def test_load_token_malformed_returns_none(secrets_dir: Path) -> None:
    secrets_dir.write_text("{not json", encoding="utf-8")
    assert load_token() is None


def test_load_token_missing_field_returns_none(secrets_dir: Path) -> None:
    secrets_dir.write_text('{"access_token": "a"}', encoding="utf-8")
    assert load_token() is None


async def test_refresh_once_no_token_does_nothing(secrets_dir: Path) -> None:
    """No token on disk → refresher must NOT be called."""
    called = False

    async def refresher(_token: OAuthToken) -> OAuthToken:
        nonlocal called
        called = True
        return _token

    attempted = await _refresh_once(refresher, now_epoch=0, lead_sec=300)
    assert attempted is False
    assert called is False


async def test_refresh_once_skips_when_token_fresh(secrets_dir: Path) -> None:
    save_token(OAuthToken(access_token="a", refresh_token="r", expires_at=10_000))
    called = False

    async def refresher(_token: OAuthToken) -> OAuthToken:
        nonlocal called
        called = True
        return _token

    # 5000s left, lead=300 → no refresh.
    attempted = await _refresh_once(refresher, now_epoch=5_000, lead_sec=300)
    assert attempted is False
    assert called is False


async def test_refresh_once_refreshes_when_inside_lead_window(secrets_dir: Path) -> None:
    save_token(OAuthToken(access_token="old", refresh_token="r-old", expires_at=10_000))

    async def refresher(token: OAuthToken) -> OAuthToken:
        assert token.access_token == "old"
        return OAuthToken(access_token="new", refresh_token="r-new", expires_at=20_000)

    # 100s before expiry, lead=300 → refresh.
    attempted = await _refresh_once(refresher, now_epoch=9_900, lead_sec=300)
    assert attempted is True

    persisted = load_token()
    assert persisted is not None
    assert persisted.access_token == "new"
    assert persisted.refresh_token == "r-new"
    assert persisted.expires_at == 20_000


async def test_refresh_once_swallows_refresher_errors(secrets_dir: Path) -> None:
    save_token(OAuthToken(access_token="old", refresh_token="r-old", expires_at=10_000))

    async def refresher(_token: OAuthToken) -> OAuthToken:
        raise RuntimeError("boom")

    # Inside lead window — refresher is called and raises; the helper
    # MUST swallow rather than propagating.
    attempted = await _refresh_once(refresher, now_epoch=9_900, lead_sec=300)
    assert attempted is True

    # On-disk token is untouched.
    persisted = load_token()
    assert persisted is not None
    assert persisted.access_token == "old"


async def test_refresh_once_invokes_on_refresh_hook(secrets_dir: Path) -> None:
    save_token(OAuthToken(access_token="old", refresh_token="r-old", expires_at=10_000))
    captured: list[OAuthToken] = []

    async def refresher(_token: OAuthToken) -> OAuthToken:
        return OAuthToken(access_token="new", refresh_token="r-new", expires_at=20_000)

    await _refresh_once(
        refresher,
        now_epoch=9_900,
        lead_sec=300,
        on_refresh=captured.append,
    )
    assert len(captured) == 1
    assert captured[0].access_token == "new"


async def test_start_refresh_loop_cancels_cleanly(secrets_dir: Path) -> None:
    """The background task must accept cancellation without warnings."""

    async def refresher(_token: OAuthToken) -> OAuthToken:  # pragma: no cover - never called
        return _token

    task = start_refresh_loop(refresher, poll_sec=0.01)
    # Yield once so the loop reaches its first sleep.
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done()


async def test_start_refresh_loop_runs_a_refresh(secrets_dir: Path) -> None:
    """End-to-end: an expiring token gets refreshed by the running loop."""
    save_token(
        OAuthToken(access_token="old", refresh_token="r-old", expires_at=int(time.time()) + 10)
    )

    async def refresher(_token: OAuthToken) -> OAuthToken:
        return OAuthToken(
            access_token="rotated",
            refresh_token="r-new",
            expires_at=int(time.time()) + 3600,
        )

    task = start_refresh_loop(refresher, poll_sec=0.01)
    try:
        # Default lead is 300s; token expires in 10s, so the first tick
        # should trigger a refresh.
        for _ in range(50):
            await asyncio.sleep(0.02)
            current = load_token()
            if current is not None and current.access_token == "rotated":
                break
        loaded = load_token()
        assert loaded is not None
        assert loaded.access_token == "rotated"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_start_refresh_loop_survives_exception_across_ticks(secrets_dir: Path) -> None:
    """Spec: 'On refresh failure: WARNING log + continue. Must NOT crash,
    must NOT raise out of the loop.' This pins that a refresher raising
    on the first tick does NOT kill the loop — the second tick still
    runs and persists a new token. ``test_refresh_once_swallows_*`` only
    covers a single tick; this test exercises the running loop.
    """
    save_token(
        OAuthToken(access_token="old", refresh_token="r-old", expires_at=int(time.time()) + 10)
    )

    call_count = 0

    async def refresher(_token: OAuthToken) -> OAuthToken:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient blip")
        return OAuthToken(
            access_token="recovered",
            refresh_token="r-new",
            expires_at=int(time.time()) + 3600,
        )

    task = start_refresh_loop(refresher, poll_sec=0.05)
    try:

        async def _wait_for_recovery() -> None:
            while True:
                current = load_token()
                if current is not None and current.access_token == "recovered":
                    return
                await asyncio.sleep(0.05)

        await asyncio.wait_for(_wait_for_recovery(), timeout=5.0)
        # Confirm the loop is still running, not crashed.
        assert not task.done()
        assert call_count >= 2
        loaded = load_token()
        assert loaded is not None
        assert loaded.access_token == "recovered"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
