"""In-process pub/sub for ws fan-out.

Single uvicorn worker is a project invariant, so the broadcast hub
lives in process memory. Publishers (REST handlers, the listening-mode
state machine, trigger updates) call :meth:`PubSub.publish` synchronously
without ever blocking. Each subscriber owns a bounded ``asyncio.Queue``
sized via ``max_per_subscriber``; when the queue is full the OLDEST
message is dropped and the subscriber is then sent a synthetic
``system`` envelope describing the drop. Tests pin this behaviour with
a 200-message burst against a stalled subscriber.

The ``triggers.invalidate`` topic is coalesced: a burst of invalidates
within ``coalesce_window_ms`` collapses into a single envelope per
subscriber. Coalescing is optional and per-instance; production wires
the default of ``50`` ms.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..ws.envelope import Envelope, build_envelope
from ..ws.topics import Topic
from .errors import ErrorCode

_logger = logging.getLogger(__name__)

DEFAULT_QUEUE_CAP = 32
DEFAULT_COALESCE_MS = 50

_COALESCED_TOPICS = frozenset({Topic.triggers_invalidate})


@dataclass(slots=True)
class SubscriberState:
    """Per-subscriber state owned by :class:`PubSub`.

    Public so the ws server can swap the topic set when a client sends
    a fresh ``subscribe`` frame; consumers should otherwise treat this
    as opaque.
    """

    queue: asyncio.Queue[Envelope]
    topics: set[Topic]
    coalesce_pending: dict[Topic, Envelope] = field(default_factory=dict)
    coalesce_handles: dict[Topic, asyncio.TimerHandle] = field(default_factory=dict)
    closed: bool = False


class Subscriber:
    """Handle returned by :meth:`PubSub.subscribe`.

    Consumers call :meth:`get` to receive envelopes one at a time and
    :meth:`close` (or use it as an async context manager) to detach
    from the hub.
    """

    def __init__(self, hub: PubSub, state: SubscriberState) -> None:
        self._hub = hub
        self._state = state

    @property
    def topics(self) -> frozenset[Topic]:
        return frozenset(self._state.topics)

    async def get(self) -> Envelope:
        """Await the next envelope addressed to this subscriber."""
        return await self._state.queue.get()

    def get_nowait(self) -> Envelope:
        """Non-blocking variant of :meth:`get` (used by tests)."""
        return self._state.queue.get_nowait()

    def qsize(self) -> int:
        return self._state.queue.qsize()

    def close(self) -> None:
        self._hub.unsubscribe(self)

    @property
    def state(self) -> SubscriberState:
        return self._state


class PubSub:
    """Per-process broadcast hub.

    The hub is intentionally tiny: a list of subscribers and a method
    that fans envelopes out to those whose topic-set matches. Backpressure
    is handled by the per-subscriber queue's drop-oldest policy.
    """

    def __init__(
        self,
        *,
        max_per_subscriber: int = DEFAULT_QUEUE_CAP,
        coalesce_window_ms: int = DEFAULT_COALESCE_MS,
    ) -> None:
        if max_per_subscriber < 1:
            raise ValueError("max_per_subscriber must be >= 1")
        if coalesce_window_ms < 0:
            raise ValueError("coalesce_window_ms must be >= 0")
        self._max = max_per_subscriber
        self._coalesce_window_ms = coalesce_window_ms
        self._subs: list[SubscriberState] = []

    def subscribe(self, topics: Iterable[Topic]) -> Subscriber:
        """Register a subscriber and return its handle."""
        topic_set = set(topics)
        if not topic_set:
            raise ValueError("subscribe requires at least one topic")
        # Always allow ``system`` so backpressure notices land.
        topic_set.add(Topic.system)
        state = SubscriberState(
            queue=asyncio.Queue(maxsize=self._max),
            topics=topic_set,
        )
        self._subs.append(state)
        return Subscriber(self, state)

    def unsubscribe(self, sub: Subscriber) -> None:
        state = sub.state
        if state.closed:
            return
        state.closed = True
        for handle in state.coalesce_handles.values():
            handle.cancel()
        state.coalesce_handles.clear()
        state.coalesce_pending.clear()
        try:
            self._subs.remove(state)
        except ValueError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, envelope: Envelope) -> None:
        """Fan ``envelope`` out to every interested subscriber.

        Never blocks. Per-subscriber queue overflow drops the oldest
        envelope and pushes a synthetic ``system`` notice describing
        the drop into the same queue (it is itself subject to
        drop-oldest if the consumer is fully stalled).
        """
        topic = envelope.topic
        for state in list(self._subs):
            if state.closed:
                continue
            if topic not in state.topics:
                continue
            if topic in _COALESCED_TOPICS and self._coalesce_window_ms > 0:
                self._coalesce(state, envelope)
                continue
            self._enqueue(state, envelope)

    def _coalesce(self, state: SubscriberState, envelope: Envelope) -> None:
        topic = envelope.topic
        state.coalesce_pending[topic] = envelope
        if topic in state.coalesce_handles:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. synchronous test path); fall back
            # to immediate delivery so callers don't have to invent a
            # loop just to receive the envelope.
            self._flush_topic(state, topic)
            return
        delay = self._coalesce_window_ms / 1000.0
        handle = loop.call_later(delay, self._flush_topic_safe, state, topic)
        state.coalesce_handles[topic] = handle

    def _flush_topic_safe(self, state: SubscriberState, topic: Topic) -> None:
        if state.closed:
            return
        self._flush_topic(state, topic)

    def _flush_topic(self, state: SubscriberState, topic: Topic) -> None:
        envelope = state.coalesce_pending.pop(topic, None)
        handle = state.coalesce_handles.pop(topic, None)
        if handle is not None:
            handle.cancel()
        if envelope is None:
            return
        self._enqueue(state, envelope)

    def _enqueue(self, state: SubscriberState, envelope: Envelope) -> None:
        try:
            state.queue.put_nowait(envelope)
            return
        except asyncio.QueueFull:
            pass

        # Drop-oldest, then enqueue, then notify the subscriber.
        try:
            dropped = state.queue.get_nowait()
        except asyncio.QueueEmpty:
            dropped = None

        try:
            state.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            # Should be unreachable — we just freed a slot — but guard
            # so a future race is loud rather than silent.
            _logger.error("pubsub queue still full after drop-oldest; envelope discarded")
            return

        if dropped is None:
            # The queue raced empty between ``put_nowait`` raising
            # ``QueueFull`` and ``get_nowait`` running (e.g. a coalesce
            # flush callback consumed the last slot). Nothing was
            # actually dropped, so don't fabricate a system notice
            # claiming otherwise.
            return

        notice = build_envelope(
            topic=Topic.system,
            payload={
                "code": ErrorCode.ws_backpressure_drop.value,
                "topic": dropped.topic.value,
                "dropped": 1,
            },
        )
        try:
            state.queue.put_nowait(notice)
        except asyncio.QueueFull:
            # Make room by dropping the next oldest. The notice carries
            # diagnostic value; losing one envelope to surface the drop
            # is the documented trade-off.
            try:
                state.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                state.queue.put_nowait(notice)
            except asyncio.QueueFull:
                _logger.error("pubsub: failed to enqueue backpressure notice")


__all__ = [
    "DEFAULT_COALESCE_MS",
    "DEFAULT_QUEUE_CAP",
    "PubSub",
    "Subscriber",
    "SubscriberState",
]
