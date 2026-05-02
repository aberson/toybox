"""Per-subscriber bounded queue + drop-oldest backpressure handling."""

from __future__ import annotations

import asyncio

import pytest

from toybox.core.errors import ErrorCode
from toybox.core.pubsub import PubSub
from toybox.ws.envelope import Envelope, build_envelope
from toybox.ws.topics import Topic

# ``asyncio_mode = "auto"`` in ``pyproject.toml`` already marks every
# async test as asyncio, so no per-function ``@pytest.mark.asyncio``
# decorators are needed.


async def test_burst_drops_oldest_and_sends_system_notice() -> None:
    hub = PubSub(max_per_subscriber=32, coalesce_window_ms=0)
    sub = hub.subscribe([Topic.activity_state])
    try:
        # Publish a 200-message burst against the (stalled) consumer.
        for i in range(200):
            hub.publish(
                build_envelope(
                    topic=Topic.activity_state,
                    payload={"id": str(i), "state": "proposed", "version": 1},
                )
            )

        # Drain everything synchronously; we expect <= 32 envelopes total
        # and at least one ``system`` backpressure notice.
        drained = []
        while True:
            try:
                drained.append(sub.get_nowait())
            except asyncio.QueueEmpty:
                break

        assert len(drained) <= 32
        notices = [
            e
            for e in drained
            if e.topic is Topic.system
            and e.payload.get("code") == ErrorCode.ws_backpressure_drop.value
        ]
        assert notices, "expected at least one ws_backpressure_drop notice"

        # Pin the full notice payload shape — parent UI consumes
        # ``code`` + ``topic`` + ``dropped`` to surface a "we missed
        # some updates" banner.
        for notice in notices:
            assert notice.payload["code"] == ErrorCode.ws_backpressure_drop.value
            assert notice.payload["topic"] == Topic.activity_state.value
            assert notice.payload["dropped"] == 1

        # The oldest envelopes were the early ones (id=0..n); since the
        # cap is 32 and we got at most 32, the surviving payloads must
        # all have id >= 200 - 32 = 168 (allowing for the notice slot).
        kept_ids = {int(e.payload["id"]) for e in drained if e.topic is Topic.activity_state}
        if kept_ids:
            assert min(kept_ids) >= 200 - 32
    finally:
        sub.close()


async def test_subscriber_only_sees_subscribed_topics() -> None:
    hub = PubSub(coalesce_window_ms=0)
    listening_sub = hub.subscribe([Topic.listening_mode])
    activity_sub = hub.subscribe([Topic.activity_state])
    try:
        hub.publish(build_envelope(topic=Topic.listening_mode, payload={"mode": 4}))
        hub.publish(
            build_envelope(
                topic=Topic.activity_state,
                payload={"id": "x", "state": "proposed", "version": 1},
            )
        )

        listening_envelope = listening_sub.get_nowait()
        assert listening_envelope.topic is Topic.listening_mode
        with pytest.raises(asyncio.QueueEmpty):
            listening_sub.get_nowait()

        activity_envelope = activity_sub.get_nowait()
        assert activity_envelope.topic is Topic.activity_state
    finally:
        listening_sub.close()
        activity_sub.close()


async def test_system_notice_reaches_subscriber_without_system_topic() -> None:
    """A subscriber that asked only for ``activity.state`` still gets
    the ``system`` backpressure notice — :meth:`PubSub.subscribe`
    augments every subscription with :data:`Topic.system` precisely so
    notices explaining that subscriber's drops always land.
    """
    hub = PubSub(max_per_subscriber=2, coalesce_window_ms=0)
    sub = hub.subscribe([Topic.activity_state])
    try:
        # 5 messages against a cap of 2 → forced drop-oldest.
        for i in range(5):
            hub.publish(
                build_envelope(
                    topic=Topic.activity_state,
                    payload={"id": str(i), "state": "proposed", "version": 1},
                )
            )

        drained: list[Envelope] = []
        while True:
            try:
                drained.append(sub.get_nowait())
            except asyncio.QueueEmpty:
                break

        notices = [e for e in drained if e.topic is Topic.system]
        assert notices, "subscriber did not receive system notice for its own drops"
    finally:
        sub.close()


async def test_no_notice_when_drop_raced_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the queue empties between ``put_nowait`` raising
    ``QueueFull`` and ``get_nowait`` running, ``_enqueue`` must skip
    the system notice — nothing was actually dropped.
    """
    hub = PubSub(max_per_subscriber=1, coalesce_window_ms=0)
    sub = hub.subscribe([Topic.activity_state])
    try:
        # Fill to cap.
        first = build_envelope(
            topic=Topic.activity_state,
            payload={"id": "first", "state": "proposed", "version": 1},
        )
        hub.publish(first)

        # Patch ``put_nowait`` to raise ``QueueFull`` once, then drain
        # the queue out from under the second call. The next attempt
        # must succeed (we just freed the slot) and the function must
        # NOT publish a notice claiming a drop.
        original_put = sub.state.queue.put_nowait
        call_count = {"n": 0}

        def patched_put(item: Envelope) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate the race: drain the queue then raise
                # ``QueueFull`` so ``_enqueue`` enters the drop-oldest
                # branch.
                sub.state.queue.get_nowait()
                raise asyncio.QueueFull()
            return original_put(item)

        monkeypatch.setattr(sub.state.queue, "put_nowait", patched_put)

        second = build_envelope(
            topic=Topic.activity_state,
            payload={"id": "second", "state": "proposed", "version": 1},
        )
        hub.publish(second)

        drained: list[Envelope] = []
        while True:
            try:
                drained.append(sub.get_nowait())
            except asyncio.QueueEmpty:
                break

        # The second envelope should have landed; no system notice
        # should accompany it because the race meant nothing was
        # actually evicted.
        assert any(e.topic is Topic.activity_state and e.payload["id"] == "second" for e in drained)
        assert not any(e.topic is Topic.system for e in drained), (
            "fabricated a backpressure notice when no drop occurred"
        )
    finally:
        sub.close()


async def test_triggers_invalidate_coalesces() -> None:
    hub = PubSub(max_per_subscriber=8, coalesce_window_ms=10)
    sub = hub.subscribe([Topic.triggers_invalidate])
    try:
        # 5 invalidates within the coalescing window.
        for i in range(5):
            hub.publish(
                build_envelope(
                    topic=Topic.triggers_invalidate,
                    payload={"reason": f"r-{i}"},
                )
            )

        # Wait for the coalescing window to fire.
        await asyncio.sleep(0.05)

        # We should see ONE coalesced envelope, not five.
        first = sub.get_nowait()
        assert first.topic is Topic.triggers_invalidate
        # Last-wins: the final reason is the one delivered.
        assert first.payload["reason"] == "r-4"
        with pytest.raises(asyncio.QueueEmpty):
            sub.get_nowait()
    finally:
        sub.close()
