"""WebSocket heartbeat: server pings, client pongs, no-pong → close."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from toybox.ws.heartbeat import HeartbeatConfig
from toybox.ws.server import get_heartbeat_config


@pytest.fixture
def fast_heartbeat(app: FastAPI) -> FastAPI:
    """Override the heartbeat dep so the test runs in tens of milliseconds."""
    app.dependency_overrides[get_heartbeat_config] = lambda: HeartbeatConfig(
        ping_interval_sec=0.05,
        ping_timeout_sec=0.4,
    )
    return app


def test_server_pings_periodically(fast_heartbeat: FastAPI, parent_token: str) -> None:
    with TestClient(fast_heartbeat) as client:
        with client.websocket_connect(
            f"/ws?token={parent_token}",
            headers={"origin": "http://127.0.0.1:4000"},
        ) as ws:
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            ping = ws.receive_json()
            assert ping["type"] == "ping"
            ws.send_json({"type": "pong"})
            # A second ping should arrive after the next interval.
            second = ws.receive_json()
            assert second["type"] == "ping"


def test_no_pong_closes_connection(fast_heartbeat: FastAPI, parent_token: str) -> None:
    with TestClient(fast_heartbeat) as client:
        with client.websocket_connect(
            f"/ws?token={parent_token}",
            headers={"origin": "http://127.0.0.1:4000"},
        ) as ws:
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            # Drain pings without ever responding — eventually the
            # server's timeout loop fires and closes the connection.
            start = time.monotonic()
            disconnect_seen = False
            while time.monotonic() - start < 2.0:
                try:
                    msg = ws.receive_json()
                    assert msg["type"] in {"ping", "ready"}
                except WebSocketDisconnect:
                    disconnect_seen = True
                    break
            assert disconnect_seen, "server never closed the silent connection"


def test_heartbeat_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from toybox.ws.heartbeat import (
        DEFAULT_PING_INTERVAL_SEC,
        DEFAULT_PING_TIMEOUT_SEC,
        heartbeat_config,
    )

    cfg = heartbeat_config()
    assert cfg.ping_interval_sec == DEFAULT_PING_INTERVAL_SEC
    assert cfg.ping_timeout_sec == DEFAULT_PING_TIMEOUT_SEC

    monkeypatch.setenv("TOYBOX_WS_PING_INTERVAL_SEC", "0.1")
    monkeypatch.setenv("TOYBOX_WS_PING_TIMEOUT_SEC", "0.5")
    cfg2 = heartbeat_config()
    assert cfg2.ping_interval_sec == 0.1
    assert cfg2.ping_timeout_sec == 0.5


@pytest.mark.parametrize(
    "env_var, attr",
    [
        ("TOYBOX_WS_PING_INTERVAL_SEC", "ping_interval_sec"),
        ("TOYBOX_WS_PING_TIMEOUT_SEC", "ping_timeout_sec"),
    ],
)
def test_heartbeat_config_invalid_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
    attr: str,
) -> None:
    """Bad floats on either side fall back to the documented defaults."""
    from toybox.ws.heartbeat import (
        DEFAULT_PING_INTERVAL_SEC,
        DEFAULT_PING_TIMEOUT_SEC,
        heartbeat_config,
    )

    defaults = {
        "ping_interval_sec": DEFAULT_PING_INTERVAL_SEC,
        "ping_timeout_sec": DEFAULT_PING_TIMEOUT_SEC,
    }
    monkeypatch.setenv(env_var, "not-a-float")
    cfg = heartbeat_config()
    assert getattr(cfg, attr) == defaults[attr]
