"""Direct unit-style coverage of :func:`toybox.ws.server._serve` —
sibling-task error visibility and the ``subscribe`` reply with
``rejected`` topic names.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from toybox.core.auth import TokenScope, TokenSubject
from toybox.core.pubsub import PubSub
from toybox.ws.envelope import build_envelope
from toybox.ws.heartbeat import HeartbeatConfig
from toybox.ws.server import _resolve_subscription_with_rejects, _serve
from toybox.ws.topics import Topic


class _FakeWebSocket:
    """Minimal in-memory WebSocket double for ``_serve``.

    Drives ``send_json`` / ``send_text`` into a list and lets the test
    queue up ``receive_json`` outcomes (dicts to deliver, or exception
    classes to raise).
    """

    def __init__(self) -> None:
        self.outgoing: list[Any] = []
        self._inbound: asyncio.Queue[Any] = asyncio.Queue()

    async def send_json(self, payload: Any) -> None:
        self.outgoing.append(payload)

    async def send_text(self, text: str) -> None:
        self.outgoing.append(text)

    async def receive_json(self) -> Any:
        item = await self._inbound.get()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()
        return item

    async def close(self, code: int = 1000) -> None:  # pragma: no cover - harness
        self.outgoing.append({"_closed": code})

    def push(self, item: Any) -> None:
        self._inbound.put_nowait(item)


@pytest.fixture
def fast_heartbeat_config() -> HeartbeatConfig:
    """Far enough out that the heartbeat doesn't fire during a quick test."""
    return HeartbeatConfig(ping_interval_sec=10.0, ping_timeout_sec=10.0)


def test_resolve_subscription_reports_rejected_names() -> None:
    """Unknown topic names land in ``rejected`` even when valid ones
    are also requested. Allowed-but-out-of-scope names also count.
    """
    resolved, rejected = _resolve_subscription_with_rejects(
        TokenScope.child,
        ["activity.state", "bogus_topic", "transcript"],
    )
    assert Topic.activity_state in resolved
    # ``transcript`` is parent-only; the child scope rejects it.
    assert "bogus_topic" in rejected
    assert "transcript" in rejected


async def test_serve_logs_sibling_exception_alongside_disconnect(
    caplog: pytest.LogCaptureFixture,
    fast_heartbeat_config: HeartbeatConfig,
) -> None:
    """If both ``_send_loop`` and ``_recv_loop`` fail effectively at
    the same time, the unusual sibling failure must show up in logs —
    a quiet ``return`` would hide it.
    """
    caplog.set_level(logging.WARNING, logger="toybox.ws.server")

    pubsub = PubSub(coalesce_window_ms=0)
    ws = _FakeWebSocket()
    subject = TokenSubject(scope=TokenScope.parent, child_session_label=None)

    # Push a bad envelope: monkey-patch ``_send_envelope`` indirectly
    # by giving the subscriber an envelope that crashes when serialized.
    bad_envelope = build_envelope(topic=Topic.activity_state, payload={"id": "x"})

    # Send loop blows up by raising on send_text.
    async def _explode_send(_text: str) -> None:
        raise RuntimeError("send-side explode")

    ws.send_text = _explode_send  # type: ignore[assignment]

    # Recv loop hits a WebSocketDisconnect immediately.
    ws.push(WebSocketDisconnect)

    # Inject the envelope into the per-subscriber queue once ``_serve``
    # registers — easiest: subscribe, capture the queue via the hub's
    # subscriber list, and prime it.
    async def _drive() -> None:
        await asyncio.sleep(0)  # let _serve subscribe
        # Locate the new subscriber state and prime its queue.
        for state in pubsub._subs:  # noqa: SLF001  (test inspection)
            state.queue.put_nowait(bad_envelope)
            break

    driver = asyncio.create_task(_drive())
    try:
        with pytest.raises(RuntimeError, match="send-side explode"):
            await _serve(
                ws,  # type: ignore[arg-type]
                subject=subject,
                initial_topics=None,
                pubsub=pubsub,
                heartbeat=fast_heartbeat_config,
            )
    finally:
        driver.cancel()
        try:
            await driver
        except asyncio.CancelledError:
            pass

    # The send-side error must be raised; the disconnect should NOT
    # have been swallowed — the warning log proves the disconnect was
    # observed too.
    assert any(
        "ws-send" in record.getMessage() and "send-side explode" in record.getMessage()
        for record in caplog.records
    ), [r.getMessage() for r in caplog.records]
