"""Integration tests for the ``metrics`` ws topic.

Covers:

* The publisher emits a snapshot envelope on the ``metrics`` topic
  immediately on start (no full-interval wait).
* Snapshots arrive at the configured cadence (≥2 within ~5s when the
  interval is 0.5s).
* A subscriber that connects after the publisher has been running for
  a while still sees the next snapshot — the publisher state survives
  reconnects (it's a server-side loop, not per-connection).
* Cancelling the publisher task is clean (no ``Task exception was
  never retrieved`` noise).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from toybox.ai.breaker import CircuitBreaker
from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.metrics import (
    PublisherDeps,
    SnapshotInputs,
    record_buffer_overrun,
    reset_counters_for_test,
    start_metrics_publisher,
)
from toybox.ws.envelope import Envelope
from toybox.ws.topics import Topic


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh, migrated SQLite file."""
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    return path


@pytest.fixture(autouse=True)
def _reset_counters() -> Iterator[None]:
    reset_counters_for_test()
    yield
    reset_counters_for_test()


def _build_deps(db_path: Path, breaker: CircuitBreaker) -> PublisherDeps:
    def _conn_factory() -> Any:
        return connect(db_path, check_same_thread=False)

    def _inputs_factory() -> SnapshotInputs:
        return SnapshotInputs(breaker=breaker)

    return PublisherDeps(conn_factory=_conn_factory, inputs_factory=_inputs_factory)


async def _drain_metrics_envelope(
    pubsub: PubSub,
    *,
    timeout: float = 1.0,
) -> Envelope:
    """Subscribe, wait for the next ``metrics`` envelope, return it.

    Only ``Topic.metrics`` envelopes count — system notices and other
    envelopes are skipped.
    """
    sub = pubsub.subscribe([Topic.metrics])
    try:
        async with asyncio.timeout(timeout):
            while True:
                env = await sub.get()
                if env.topic is Topic.metrics:
                    return env
    finally:
        sub.close()


async def test_publisher_emits_initial_snapshot_immediately(
    db_path: Path,
) -> None:
    """The publisher should publish on entry, not after the first sleep."""
    pubsub = PubSub(coalesce_window_ms=0)
    breaker = CircuitBreaker()
    # Long interval — if the publisher waits for the interval before its
    # first snapshot, this test would time out.
    task = start_metrics_publisher(pubsub, _build_deps(db_path, breaker), interval_sec=60.0)
    try:
        env = await _drain_metrics_envelope(pubsub, timeout=2.0)
        assert env.topic is Topic.metrics
        assert "generated_at" in env.payload
        assert env.payload["activities"]["proposed_current"] == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_publisher_emits_repeated_snapshots(db_path: Path) -> None:
    """At least two snapshots arrive within the test window.

    Uses a 0.2s interval and asserts two metrics envelopes are received
    in under 2s. The ``generated_at`` field is ISO-second precision so
    two snapshots emitted within the same second can carry the same
    value — the test asserts on count + topic, not on timestamp delta.
    """
    pubsub = PubSub(coalesce_window_ms=0)
    breaker = CircuitBreaker()
    task = start_metrics_publisher(pubsub, _build_deps(db_path, breaker), interval_sec=0.2)
    try:
        env1 = await _drain_metrics_envelope(pubsub, timeout=2.0)
        env2 = await _drain_metrics_envelope(pubsub, timeout=2.0)
        assert env1.topic is Topic.metrics
        assert env2.topic is Topic.metrics
        # Both carry a generated_at; values may match if both snapshots
        # land in the same wall-clock second.
        assert isinstance(env1.payload["generated_at"], str)
        assert isinstance(env2.payload["generated_at"], str)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_counters_persist_across_subscriber_reconnect(db_path: Path) -> None:
    """A subscriber that reconnects sees the SAME counter values.

    The metrics module's counters are module-level + thread-safe, not
    per-connection. A reconnect (= subscribe new + close old) must NOT
    reset them.
    """
    pubsub = PubSub(coalesce_window_ms=0)
    breaker = CircuitBreaker()
    task = start_metrics_publisher(pubsub, _build_deps(db_path, breaker), interval_sec=0.2)
    try:
        # Bump the in-memory overrun counter twice.
        record_buffer_overrun()
        record_buffer_overrun()

        # First subscriber sees count=2.
        env_first = await _drain_metrics_envelope(pubsub, timeout=2.0)
        assert env_first.payload["audio"]["buffer_overruns_total"] == 2

        # "Reconnect": subscribe a fresh client.
        env_second = await _drain_metrics_envelope(pubsub, timeout=2.0)
        assert env_second.payload["audio"]["buffer_overruns_total"] == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_publisher_cancels_cleanly_no_warnings(
    db_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cancelling the publisher must not log Task exception warnings.

    Mirrors the contract followed by the ws server's send/recv tasks.
    """
    caplog.set_level(logging.WARNING, logger="toybox.metrics")
    pubsub = PubSub(coalesce_window_ms=0)
    breaker = CircuitBreaker()
    task = start_metrics_publisher(pubsub, _build_deps(db_path, breaker), interval_sec=0.2)
    # Let it tick at least once.
    await _drain_metrics_envelope(pubsub, timeout=2.0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # No CancelledError surfaced as a logger warning.
    assert all("Task exception" not in record.getMessage() for record in caplog.records)
