"""WS visibility for ``Topic.toy_actions``.

Asserts the parent-scope-only contract from plan §F4:

* A parent-scope token subscribed to ``toy_actions`` receives published
  envelopes.
* A child-scope token does NOT — the topic is filtered out of the
  child's subscription set on connect.

Mirrors the auth + connect pattern used in
:mod:`tests.integration.test_ws_origin` and
:mod:`tests.integration.test_ws_heartbeat`.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from toybox.core.pubsub import PubSub
from toybox.ws.envelope import build_envelope
from toybox.ws.topics import Topic

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="starlette TestClient WS teardown races on Linux — see issue #210",
)


def _publish_toy_actions_envelope(pubsub: PubSub) -> dict[str, object]:
    """Publish a canonical-shape ``toy_actions`` envelope; return its payload."""
    payload: dict[str, object] = {
        "toy_id": "550e8400-e29b-41d4-a716-446655440000",
        "slot": "idle",
        "status": "done",
        "image_path": (
            "data/images/toy_actions/550e8400-e29b-41d4-a716-446655440000/idle.png"
        ),
        "error": None,
    }
    pubsub.publish(build_envelope(topic=Topic.toy_actions, payload=payload))
    return payload


def test_parent_token_receives_toy_actions(
    client: TestClient,
    parent_token: str,
    pubsub: PubSub,
) -> None:
    """A parent-scope token subscribed to ``toy_actions`` sees envelopes."""
    with client.websocket_connect(
        f"/ws?token={parent_token}",
        headers={"origin": "http://127.0.0.1:4000"},
    ) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert "toy_actions" in ready["topics"]

        payload = _publish_toy_actions_envelope(pubsub)

        # Drain envelopes until we see the toy_actions one (skip pings).
        for _ in range(10):
            frame = ws.receive_json()
            if frame.get("topic") == "toy_actions":
                break
        else:  # pragma: no cover -- safety
            raise AssertionError("never received toy_actions envelope")

        assert frame["payload"]["toy_id"] == payload["toy_id"]
        assert frame["payload"]["slot"] == "idle"
        assert frame["payload"]["status"] == "done"
        assert frame["payload"]["image_path"] == payload["image_path"]


def test_child_token_does_not_receive_toy_actions(
    client: TestClient,
    child_token: str,
    pubsub: PubSub,
) -> None:
    """A child-scope token's subscription set excludes ``toy_actions``."""
    with client.websocket_connect(
        f"/ws?token={child_token}",
        headers={"origin": "http://127.0.0.1:4000"},
    ) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        # The child's ``ready`` topic list MUST NOT include
        # ``toy_actions`` — that's the parent-scope-only contract.
        assert "toy_actions" not in ready["topics"]

        # Even if we publish, the child's queue is filtered by the
        # subscription set; the envelope never reaches it. We can only
        # assert the negative space — published envelope, no delivery.
        _publish_toy_actions_envelope(pubsub)

        # Try requesting ``toy_actions`` explicitly. The server should
        # respond with ``rejected`` and fall back to the child's
        # default topic set — still excluding toy_actions.
        ws.send_json({"type": "subscribe", "topics": ["toy_actions"]})
        for _ in range(5):
            frame = ws.receive_json()
            if frame.get("type") == "subscribed":
                break
        else:  # pragma: no cover -- safety
            raise AssertionError("never received subscribed reply")

        assert "toy_actions" in frame.get("rejected", [])
        assert "toy_actions" not in frame["topics"]


def test_topic_value_is_stable_wire_string() -> None:
    """The ``Topic.toy_actions`` enum value must be the string clients see.

    Pins the wire identifier so a typo refactor on the server doesn't
    silently break frontend subscribers.
    """
    assert Topic.toy_actions.value == "toy_actions"


def test_parent_scope_includes_toy_actions() -> None:
    """The parent topic set wires :data:`Topic.toy_actions` into ``_PARENT_TOPICS``."""
    from toybox.core.auth import TokenScope
    from toybox.ws.server import topics_for_scope

    assert Topic.toy_actions in topics_for_scope(TokenScope.parent)
    assert Topic.toy_actions not in topics_for_scope(TokenScope.child)
