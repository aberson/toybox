"""WebSocket Origin allow-list."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.mark.parametrize(
    "origin",
    ["http://127.0.0.1:4000", "http://localhost:4000"],
)
def test_origin_loopback_allowed(
    client: TestClient,
    parent_token: str,
    origin: str,
) -> None:
    with client.websocket_connect(
        f"/ws?token={parent_token}",
        headers={"origin": origin},
    ) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"


def test_origin_unknown_rejected(
    client: TestClient,
    parent_token: str,
) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws?token={parent_token}",
            headers={"origin": "http://evil.example"},
        ):
            pass
    assert exc.value.code == 1008


def test_origin_missing_rejected(
    client: TestClient,
    parent_token: str,
) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws?token={parent_token}"):
            pass
    assert exc.value.code == 1008


def test_lan_ip_added_via_env(
    client: TestClient,
    parent_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOYBOX_LAN_IP", "192.168.7.42")
    with client.websocket_connect(
        f"/ws?token={parent_token}",
        headers={"origin": "http://192.168.7.42:4000"},
    ) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"


def test_missing_token_closed_1008(client: TestClient) -> None:
    with client.websocket_connect(
        "/ws",
        headers={"origin": "http://127.0.0.1:4000"},
    ) as ws:
        # The server is waiting for an ``auth`` message; sending the
        # wrong type drops it into the close path with code 1008.
        ws.send_json({"type": "noise"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1008


def test_invalid_token_closed_1008(client: TestClient) -> None:
    with client.websocket_connect(
        "/ws?token=not-a-real-token",
        headers={"origin": "http://127.0.0.1:4000"},
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1008


def test_subscribe_reply_includes_rejected_names(
    client: TestClient,
    parent_token: str,
) -> None:
    """When a client sends ``subscribe`` with an unknown topic name, the
    server's ``subscribed`` reply lists the bad names under
    ``rejected``. The fallback semantics (resolve to scope defaults
    when nothing was valid) are unchanged.
    """
    with client.websocket_connect(
        f"/ws?token={parent_token}",
        headers={"origin": "http://127.0.0.1:4000"},
    ) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        ws.send_json({"type": "subscribe", "topics": ["bogus_topic"]})
        # The receive may yield a server ping first; pull until we
        # find the ``subscribed`` frame.
        for _ in range(5):
            frame = ws.receive_json()
            if frame.get("type") == "subscribed":
                break
        else:  # pragma: no cover - safety
            raise AssertionError("never received subscribed reply")
        assert "bogus_topic" in frame.get("rejected", [])
        # Falls back to the scope's allowed topics rather than zero.
        assert frame["topics"], "subscribed must default to allowed topics"


def test_invalid_token_via_auth_message_closed_1008(client: TestClient) -> None:
    """Invalid tokens delivered via the in-band ``auth`` message also
    close 1008 — there's no path that yields an open socket without a
    valid token, regardless of how the token was supplied.
    """
    with client.websocket_connect(
        "/ws",
        headers={"origin": "http://127.0.0.1:4000"},
    ) as ws:
        ws.send_json({"type": "auth", "token": "definitely-not-issued"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1008
