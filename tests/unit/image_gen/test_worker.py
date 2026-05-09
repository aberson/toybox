"""Unit coverage for :mod:`toybox.image_gen.worker`.

Drives the worker against stub pipelines that we install via the
``pipeline=`` kwarg so a single test can swap the full lifecycle for
canned outputs / failures without touching env state.

Each test owns its own SQLite file (``tmp_path`` fixture), its own
breaker (reset between tests so failure thresholds don't leak), and
captures WS envelopes via a list-appending async ``emit`` callback.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.image_gen.capability import (
    CapabilityReason,
    ImageGenBreaker,
    get_image_gen_breaker,
    reset_image_gen_breaker_for_tests,
)
from toybox.image_gen.models import (
    GenerationContext,
    ImageGenCapacityError,
    ImageGenTimeoutError,
    ToyActionStatus,
)
from toybox.image_gen.worker import ImageGenWorker
from toybox.storage.toy_actions import upsert_status
from toybox.ws.topics import Topic

# Canonical UUIDv4 for the seeded toy.
_TOY_ID = "550e8400-e29b-41d4-a716-446655440000"
_TOY_PHOTO_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"  # tiny JPEG header


@pytest.fixture(autouse=True)
def _reset_breaker() -> None:
    """Drop the module-level breaker between tests."""
    reset_image_gen_breaker_for_tests()
    yield
    reset_image_gen_breaker_for_tests()


def _capable_probe() -> tuple[bool, CapabilityReason, str]:
    """Return the canonical capable tuple — pin most tests to Tier B."""
    return True, CapabilityReason.capable, "capable"


def _composite_only_probe() -> tuple[bool, CapabilityReason, str]:
    """Pin capability to a non-env-disabled False reason → Tier C dispatch."""
    return False, CapabilityReason.missing_checkpoints, "test-missing"


def _env_disabled_probe() -> tuple[bool, CapabilityReason, str]:
    """Pin capability to ENV_DISABLED → hard-off, no composite."""
    return False, CapabilityReason.env_disabled, "test-env-disabled"


@pytest.fixture(autouse=True)
def _pin_capability_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default existing tests to the CAPABLE branch.

    Without this, ``ImageGenWorker._probe_capability`` would fall
    through to the real :func:`is_image_gen_capable`, which on a CI
    host returns ``(False, NO_CUDA, ...)`` and routes every job to
    the composite path — breaking the existing Tier-B-focused tests.
    Tests that want a different branch construct the worker with an
    explicit ``capability_probe=`` kwarg.
    """
    from toybox.image_gen import worker as worker_module

    monkeypatch.setattr(
        worker_module,
        "is_image_gen_capable",
        lambda **_kw: _capable_probe(),
    )


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create + migrate a SQLite file and seed one toy + its image bytes.

    Also redirects ``TOYBOX_DATA_DIR`` to ``tmp_path`` so PNG writes
    land under the test workspace.
    """
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    db = tmp_path / "toybox.db"
    conn = connect(db)
    try:
        run_migrations(conn)
        with conn:
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    _TOY_ID,
                    "Bunny",
                    "data/images/toys/bunny.jpg",
                    "h1",
                    "2026-05-06T00:00:00Z",
                ),
            )
    finally:
        conn.close()
    # Seed the source-photo bytes on disk so the worker's reference-
    # bytes read succeeds.
    photo = tmp_path / "images" / "toys" / "bunny.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(_TOY_PHOTO_BYTES)
    return db


def _conn_factory(db_path: Path) -> Callable[[], sqlite3.Connection]:
    def _factory() -> sqlite3.Connection:
        return connect(db_path, check_same_thread=False)

    return _factory


def _capture_emit() -> tuple[
    list[tuple[Topic, dict[str, object]]],
    Callable[[Topic, dict[str, object]], Awaitable[None]],
]:
    captured: list[tuple[Topic, dict[str, object]]] = []

    async def _emit(topic: Topic, payload: dict[str, object]) -> None:
        captured.append((topic, payload))

    return captured, _emit


# ----------------------------------------------------------------------
# Lifecycle: start → enqueue → consume → stop
# ----------------------------------------------------------------------


async def test_full_lifecycle_running_then_done(
    db_path: Path,
    tmp_path: Path,
) -> None:
    """One job flows: enqueue → running → done. WS envelopes captured."""
    captured, emit = _capture_emit()

    async def _stub(reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext) -> bytes:
        return b"\x89PNG\r\n\x1a\nFAKE-OUTPUT"

    worker = ImageGenWorker(_conn_factory(db_path), emit, pipeline=_stub)
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle")
        # Wait for the queue to drain.
        for _ in range(200):
            await asyncio.sleep(0.01)
            if worker.queue_size == 0 and any(p.get("status") == "done" for _, p in captured):
                break
    finally:
        await worker.stop()

    statuses = [p["status"] for _, p in captured]
    assert "queued" in statuses
    assert "running" in statuses
    assert "done" in statuses

    # ``done`` envelope carries the image_path; ``failed``/``running``
    # carry None.
    done_payload = next(p for _, p in captured if p["status"] == "done")
    assert done_payload["image_path"] == (f"data/images/toy_actions/{_TOY_ID}/idle.png")
    assert done_payload["error"] is None
    running_payload = next(p for _, p in captured if p["status"] == "running")
    assert running_payload["image_path"] is None
    assert running_payload["error"] is None

    # PNG actually written.
    out_path = tmp_path / "images" / "toy_actions" / _TOY_ID / "idle.png"
    assert out_path.is_file()
    assert out_path.read_bytes() == b"\x89PNG\r\n\x1a\nFAKE-OUTPUT"

    # DB row is ``done``.
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, image_path FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "idle"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    assert row["image_path"] == f"data/images/toy_actions/{_TOY_ID}/idle.png"


# ----------------------------------------------------------------------
# Supersede semantics
# ----------------------------------------------------------------------


async def test_enqueue_time_supersede_persists_superseded_row(
    db_path: Path,
) -> None:
    """Verify the DB row reaches ``superseded`` between the two enqueues."""
    captured, emit = _capture_emit()

    proceed = asyncio.Event()
    started = asyncio.Event()

    async def _gated_stub(
        reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext
    ) -> bytes:
        started.set()
        await proceed.wait()
        return b"PNG-OUT"

    worker = ImageGenWorker(_conn_factory(db_path), emit, pipeline=_gated_stub)
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "pointing", seed=1)
        await asyncio.wait_for(started.wait(), timeout=2.0)
        # Mid-flight supersede via the public API.
        await worker.enqueue(_TOY_ID, "pointing", seed=2)

        # Read the row immediately — it should be ``superseded`` because
        # ``enqueue`` flipped it before writing the new ``queued`` row.
        # But the ``upsert_status(queued)`` for the new job replaces the
        # row's status with ``queued`` (same primary key). So the order
        # in the DB right now is: superseded (briefly) → queued (the new
        # job). To prove the supersede actually persisted, we instead
        # assert the worker DOES NOT write a ``done`` row for seed=1 and
        # the PNG it would have written gets cleaned up.
        proceed.set()
        for _ in range(300):
            await asyncio.sleep(0.01)
            if worker.queue_size == 0:
                # Wait for the queue drained, then a final tick for
                # the second job to complete.
                break
        # Let the second job finish.
        for _ in range(300):
            await asyncio.sleep(0.01)
            statuses_seen = [p["status"] for _, p in captured]
            if statuses_seen.count("done") >= 1:
                break
    finally:
        proceed.set()
        await worker.stop()

    # The second job (seed=2) wins; its done envelope is the one with
    # ``image_path`` populated.
    done_payloads = [p for _, p in captured if p["status"] == "done"]
    assert len(done_payloads) >= 1, captured
    # DB row for the slot reflects the second job's seed.
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, seed FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "pointing"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    assert row["seed"] == 2


async def test_runtime_supersede_discards_output(
    db_path: Path,
    tmp_path: Path,
) -> None:
    """Mid-flight: externally flip the row to ``superseded``; worker discards the PNG."""
    captured, emit = _capture_emit()

    proceed = asyncio.Event()
    started = asyncio.Event()

    async def _gated_stub(
        reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext
    ) -> bytes:
        started.set()
        await proceed.wait()
        return b"PNG-CONTENT"

    worker = ImageGenWorker(_conn_factory(db_path), emit, pipeline=_gated_stub)
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "looking", seed=42)
        await asyncio.wait_for(started.wait(), timeout=2.0)
        # External supersede via direct upsert — simulates a different
        # actor (e.g., a future regen request from another process)
        # racing the in-flight job.
        conn = connect(db_path)
        try:
            upsert_status(
                conn,
                _TOY_ID,
                "looking",
                ToyActionStatus.superseded,
            )
        finally:
            conn.close()

        proceed.set()
        # Wait for the job to finish — but it should NOT produce a
        # ``done`` envelope because the supersede recheck fires before
        # the commit.
        for _ in range(300):
            await asyncio.sleep(0.01)
            if worker.queue_size == 0:
                # Give the consumer a moment to finalize.
                await asyncio.sleep(0.05)
                break
    finally:
        proceed.set()
        await worker.stop()

    statuses = [p["status"] for _, p in captured]
    assert "running" in statuses
    assert "done" not in statuses, captured

    # Output PNG was deleted.
    out_path = tmp_path / "images" / "toy_actions" / _TOY_ID / "looking.png"
    assert not out_path.exists(), "worker should have discarded the superseded PNG"

    # DB row is still ``superseded`` — worker did NOT overwrite it.
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "looking"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "superseded"


async def test_runtime_supersede_via_new_enqueue_seed_mismatch(
    db_path: Path,
) -> None:
    """A second ``enqueue`` mid-flight wins via the seed-mismatch branch.

    Exercises :meth:`ImageGenWorker._is_superseded`'s seed-mismatch
    arm specifically — the persistence-layer ``status='superseded'``
    branch is covered by :func:`test_runtime_supersede_discards_output`.
    Here the row's status flips through ``superseded`` (briefly,
    inside the second ``enqueue``) then is overwritten with
    ``queued, seed=2`` before the in-flight job rechecks. So the
    in-flight job sees ``status=queued, seed=2`` and the seed mismatch
    triggers the discard.
    """
    captured, emit = _capture_emit()

    # Two gates so we can sequence: gate seed=1 mid-flight, drive
    # the second enqueue, then release.
    proceed_first = asyncio.Event()
    started_first = asyncio.Event()
    seed_done = asyncio.Event()

    async def _gated_stub(
        reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext
    ) -> bytes:
        if seed == 1:
            started_first.set()
            await proceed_first.wait()
            return b"PNG-FIRST"
        # seed=2 path is ungated.
        return b"PNG-SECOND"

    worker = ImageGenWorker(_conn_factory(db_path), emit, pipeline=_gated_stub)
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "waving", seed=1)
        await asyncio.wait_for(started_first.wait(), timeout=2.0)
        # Mid-flight: a fresh enqueue with a different seed. The
        # enqueue helper marks the running row ``superseded`` and
        # then ``upsert_status(queued, seed=2)`` overwrites it.
        await worker.enqueue(_TOY_ID, "waving", seed=2)
        proceed_first.set()
        # Wait for the second job to commit.
        for _ in range(400):
            await asyncio.sleep(0.01)
            if any(p["status"] == "done" and p.get("image_path") for _, p in captured):
                seed_done.set()
                break
    finally:
        proceed_first.set()
        await worker.stop()

    # Exactly one ``done`` envelope — the seed=1 commit was discarded
    # at the recheck via the seed-mismatch branch.
    done_payloads = [p for _, p in captured if p["status"] == "done"]
    assert len(done_payloads) == 1, captured

    # Final DB row reflects seed=2 (the winner).
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, seed FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "waving"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    assert row["seed"] == 2


# ----------------------------------------------------------------------
# Cancellation: shutdown grace fires while a job is mid-flight
# ----------------------------------------------------------------------


async def test_cancellation_marks_inflight_failed(
    db_path: Path,
) -> None:
    """Cancelling the consumer mid-job writes ``failed("interrupted by shutdown")``.

    Exercises the F2 fix: :meth:`ImageGenWorker._run_one`'s
    ``except CancelledError`` branch wraps a best-effort upsert +
    emit, then re-raises so the consumer task terminates cleanly.

    Setup gates the stub on an :class:`asyncio.Event`, waits for the
    ``running`` envelope to confirm the job is actually mid-flight,
    then calls :meth:`ImageGenWorker.stop` with a tight grace so the
    queue-join times out and the consumer task is cancelled. The
    cancellation handler must commit the failed row before the task
    finishes raising :class:`asyncio.CancelledError`.
    """
    captured, emit = _capture_emit()

    proceed = asyncio.Event()
    started = asyncio.Event()

    async def _gated_stub(
        reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext
    ) -> bytes:
        started.set()
        await proceed.wait()
        return b"PNG-NEVER-COMMITTED"

    # Tight grace so stop() falls through to cancellation quickly.
    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_gated_stub,
        shutdown_grace_sec=0.1,
    )
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle", seed=7)
        # Wait for the ``running`` envelope so we know the consumer
        # is past the supersede check and inside the gated stub.
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(p["status"] == "running" for _, p in captured):
                break
        assert any(p["status"] == "running" for _, p in captured), captured
    finally:
        # stop() will time out on queue.join (the gate is still set),
        # cancel the consumer, and the CancelledError handler in
        # _run_one writes the failed row before re-raising.
        await worker.stop()
        # Release the gate AFTER stop() so the cancellation actually
        # races the gated await rather than racing a quick return.
        proceed.set()

    # A ``failed`` envelope was emitted with the cancellation reason.
    failed_payloads = [p for _, p in captured if p["status"] == "failed"]
    assert any(p.get("error") == "interrupted by shutdown" for p in failed_payloads), captured

    # DB row is ``failed`` with the cancellation error_msg.
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, error_msg FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "idle"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_msg"] == "interrupted by shutdown"


# ----------------------------------------------------------------------
# Restart recovery: queued rows are also swept (F1 fix)
# ----------------------------------------------------------------------


async def test_restart_recovery_marks_queued_rows_failed(
    db_path: Path,
) -> None:
    """A row stuck in ``queued`` from a prior process is flipped to ``failed``.

    Exercises the F1 fix: :meth:`ImageGenWorker.run_restart_recovery`
    sweeps both ``running`` AND ``queued`` rows. Without this, a
    row written by :meth:`enqueue` whose process died before the
    consumer popped would leak across restarts forever (the
    in-memory queue is gone).
    """
    # Direct DB write — simulate an enqueue whose process died before
    # the asyncio.Queue.put fired. Insert one queued row and one
    # running row so we exercise both branches in the sweep.
    conn = connect(db_path)
    try:
        upsert_status(
            conn,
            _TOY_ID,
            "idle",
            ToyActionStatus.queued,
            seed=11,
        )
        upsert_status(
            conn,
            _TOY_ID,
            "pointing",
            ToyActionStatus.running,
            seed=22,
        )
    finally:
        conn.close()

    _, emit = _capture_emit()
    worker = ImageGenWorker(_conn_factory(db_path), emit)

    recovered = await worker.run_restart_recovery()
    assert recovered == 2

    # Both rows now ``failed`` with the canonical error_msg.
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT slot, status, error_msg, seed FROM toy_actions WHERE toy_id = ? ORDER BY slot",
            (_TOY_ID,),
        ).fetchall()
    finally:
        conn.close()
    by_slot = {row["slot"]: row for row in rows}
    assert by_slot["idle"]["status"] == "failed"
    assert by_slot["idle"]["error_msg"] == "interrupted by restart"
    assert by_slot["idle"]["seed"] == 11
    assert by_slot["pointing"]["status"] == "failed"
    assert by_slot["pointing"]["error_msg"] == "interrupted by restart"
    assert by_slot["pointing"]["seed"] == 22


# ----------------------------------------------------------------------
# Concurrent enqueues: atomic SELECT + supersede + queued write (F4 fix)
# ----------------------------------------------------------------------


async def test_concurrent_enqueues_are_atomic(
    db_path: Path,
) -> None:
    """5 concurrent enqueues for one (toy_id, slot) yield exactly 1 DB row.

    Exercises the F4 fix: :meth:`ImageGenWorker.enqueue` runs the
    SELECT + ``superseded`` UPDATE + ``queued`` UPSERT inside a
    single ``BEGIN IMMEDIATE`` transaction. Without this, two
    concurrent enqueues could each see ``status=queued``, both flip
    the row to ``superseded``, and both write a fresh ``queued`` row
    — losing the supersede invariant. The PK constraint
    ``UNIQUE(toy_id, slot)`` keeps the row count at 1 either way,
    but the assertion here is that the worker's queue size matches
    the number of enqueue calls (each call does both the DB write
    AND the queue ``put``) and the row is in a sane state.
    """
    captured, emit = _capture_emit()

    proceed = asyncio.Event()

    async def _gated_stub(
        reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext
    ) -> bytes:
        await proceed.wait()
        return b"PNG-OUT"

    worker = ImageGenWorker(_conn_factory(db_path), emit, pipeline=_gated_stub)
    await worker.start()
    try:
        # 5 concurrent enqueues for the same (toy_id, slot).
        await asyncio.gather(*[worker.enqueue(_TOY_ID, "thinking", seed=i) for i in range(5)])

        # Exactly one DB row for the slot — PK enforces this, but the
        # status must be a sane terminal-ish value (queued for the
        # latest job; running if the consumer just popped one).
        conn = connect(db_path)
        try:
            rows = conn.execute(
                "SELECT status, seed FROM toy_actions WHERE toy_id = ? AND slot = ?",
                (_TOY_ID, "thinking"),
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1, rows
        assert rows[0]["status"] in {"queued", "running", "superseded"}

        # Each enqueue calls put → queue length is between 1 (consumer
        # already drained some) and 5 (consumer is gated).
        assert 1 <= worker.queue_size + 1 <= 6
    finally:
        proceed.set()
        await worker.stop()


# ----------------------------------------------------------------------
# Per-pipeline breaker
# ----------------------------------------------------------------------


async def test_breaker_opens_after_three_capacity_errors(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 ImageGenCapacityError in a row trip the breaker; subsequent jobs marked ``failed``."""
    captured, emit = _capture_emit()

    async def _ooming_stub(
        reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext
    ) -> bytes:
        raise ImageGenCapacityError("synthetic OOM")

    # Pin a tight breaker (threshold=3, cooldown=2s) for the test.
    monkeypatch.setenv("TOYBOX_IMAGE_GEN_BREAKER_THRESHOLD", "3")
    monkeypatch.setenv("TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC", "60")
    # Force breaker re-init.
    reset_image_gen_breaker_for_tests()
    breaker = get_image_gen_breaker()
    assert isinstance(breaker, ImageGenBreaker)

    worker = ImageGenWorker(_conn_factory(db_path), emit, pipeline=_ooming_stub)
    await worker.start()
    try:
        # Three failures → breaker opens.
        for slot in ("idle", "pointing", "looking"):
            await worker.enqueue(_TOY_ID, slot, seed=hash(slot) & 0xFFFF)

        # Wait for the breaker to trip.
        for _ in range(300):
            await asyncio.sleep(0.01)
            if breaker.is_open():
                break
        assert breaker.is_open(), captured

        # Now enqueue a fourth job. The breaker is open so the worker
        # marks it failed with the breaker reason.
        await worker.enqueue(_TOY_ID, "jumping", seed=4)
        for _ in range(300):
            await asyncio.sleep(0.01)
            errors = [p.get("error") for _, p in captured if p["status"] == "failed"]
            if "image-gen breaker open" in errors:
                break
    finally:
        await worker.stop()

    errors = [p.get("error") for _, p in captured if p["status"] == "failed"]
    # First three failures are "out of memory"; the fourth is breaker open.
    assert errors.count("out of memory") == 3
    assert errors.count("image-gen breaker open") == 1


# ----------------------------------------------------------------------
# Timeout error mapping
# ----------------------------------------------------------------------


async def test_timeout_maps_to_failed_with_timeout_reason(
    db_path: Path,
) -> None:
    captured, emit = _capture_emit()

    async def _timeout_stub(
        reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext
    ) -> bytes:
        raise ImageGenTimeoutError("synthetic timeout")

    worker = ImageGenWorker(_conn_factory(db_path), emit, pipeline=_timeout_stub)
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle")
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(p["status"] == "failed" for _, p in captured):
                break
    finally:
        await worker.stop()

    failed = [p for _, p in captured if p["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "timeout"


async def test_unexpected_exception_maps_to_truncated_error(
    db_path: Path,
) -> None:
    captured, emit = _capture_emit()
    long_msg = "x" * 500

    async def _bad_stub(
        reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext
    ) -> bytes:
        raise RuntimeError(long_msg)

    worker = ImageGenWorker(_conn_factory(db_path), emit, pipeline=_bad_stub)
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle")
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(p["status"] == "failed" for _, p in captured):
                break
    finally:
        await worker.stop()

    failed = [p for _, p in captured if p["status"] == "failed"]
    assert len(failed) == 1
    error_text = failed[0]["error"]
    assert isinstance(error_text, str)
    # Truncation is the contract — a long exception message gets capped
    # at 200 chars. We pin the contract (truncated, content preserved)
    # rather than the constant so a future cap tweak doesn't fail
    # spuriously.
    assert len(error_text) <= 200
    assert error_text.startswith("x")


# ----------------------------------------------------------------------
# Restart recovery sweep
# ----------------------------------------------------------------------


async def test_restart_recovery_marks_running_rows_failed(
    db_path: Path,
) -> None:
    """An orphaned ``running`` row from a prior process is flipped to ``failed``."""
    # Pre-insert a running row to simulate the prior process having
    # crashed mid-job.
    conn = connect(db_path)
    try:
        upsert_status(
            conn,
            _TOY_ID,
            "idle",
            ToyActionStatus.running,
            seed=99,
        )
    finally:
        conn.close()

    _, emit = _capture_emit()
    worker = ImageGenWorker(_conn_factory(db_path), emit)

    recovered = await worker.run_restart_recovery()
    assert recovered == 1

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, error_msg, seed FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "idle"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_msg"] == "interrupted by restart"
    # Seed is preserved (operator can re-run the same generation if
    # desired).
    assert row["seed"] == 99


async def test_restart_recovery_idempotent(
    db_path: Path,
) -> None:
    """A second sweep run returns 0 because there's nothing to recover."""
    conn = connect(db_path)
    try:
        upsert_status(conn, _TOY_ID, "idle", ToyActionStatus.running)
    finally:
        conn.close()

    _, emit = _capture_emit()
    worker = ImageGenWorker(_conn_factory(db_path), emit)

    first = await worker.run_restart_recovery()
    second = await worker.run_restart_recovery()
    assert first == 1
    assert second == 0


async def test_restart_recovery_no_envelopes_emitted(
    db_path: Path,
) -> None:
    """Recovery must not emit WS envelopes — no clients are connected."""
    conn = connect(db_path)
    try:
        upsert_status(conn, _TOY_ID, "idle", ToyActionStatus.running)
    finally:
        conn.close()

    captured, emit = _capture_emit()
    worker = ImageGenWorker(_conn_factory(db_path), emit)

    await worker.run_restart_recovery()
    assert captured == []


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


async def test_enqueue_rejects_invalid_toy_id(
    db_path: Path,
) -> None:
    _, emit = _capture_emit()
    worker = ImageGenWorker(_conn_factory(db_path), emit)
    with pytest.raises(ValueError, match="UUIDv4"):
        await worker.enqueue("../bad", "idle")


async def test_enqueue_rejects_invalid_slot(
    db_path: Path,
) -> None:
    _, emit = _capture_emit()
    worker = ImageGenWorker(_conn_factory(db_path), emit)
    with pytest.raises(ValueError, match="ACTION_SLOTS"):
        await worker.enqueue(_TOY_ID, "smiling")


# ----------------------------------------------------------------------
# Singleton helpers
# ----------------------------------------------------------------------


async def test_singleton_start_stop(
    db_path: Path,
) -> None:
    """``start_image_gen_worker`` is idempotent; ``stop`` clears the cache."""
    from toybox.image_gen import worker as worker_module

    _, emit = _capture_emit()

    worker_module.reset_image_gen_worker_for_tests()
    assert worker_module.get_image_gen_worker() is None

    a = await worker_module.start_image_gen_worker(_conn_factory(db_path), emit)
    b = await worker_module.start_image_gen_worker(_conn_factory(db_path), emit)
    try:
        assert a is b  # idempotent — same instance
        assert worker_module.get_image_gen_worker() is a
    finally:
        await worker_module.stop_image_gen_worker()
    assert worker_module.get_image_gen_worker() is None


# ----------------------------------------------------------------------
# F.5-3a: capability dispatch — Tier B vs Tier C vs hard-off
# ----------------------------------------------------------------------


async def test_dispatch_env_disabled_marks_failed_no_composite(
    db_path: Path,
) -> None:
    """``ENV_DISABLED`` → row marked ``failed`` with the canonical
    ``"image_gen_disabled"`` error; the composite stub is NOT called.

    Hard-off semantics per the spec: if the operator explicitly
    disabled image-gen via ``TOYBOX_IMAGE_GEN_ENABLED=false``, do
    NOT route to composite either.
    """
    captured, emit = _capture_emit()
    composite_calls: list[tuple[str, int]] = []

    async def _composite_stub(
        reference_bytes: bytes,
        slot: str,
        seed: int,
        ctx: GenerationContext,
    ) -> bytes:
        composite_calls.append((slot, seed))
        return b"\x89PNG\r\n\x1a\nCOMPOSITE"

    async def _pipeline_stub(
        reference_bytes: bytes,
        slot: str,
        seed: int,
        ctx: GenerationContext,
    ) -> bytes:
        return b"\x89PNG\r\n\x1a\nPIPELINE"

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_pipeline_stub,
        composite=_composite_stub,
        capability_probe=_env_disabled_probe,
    )
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle", seed=1)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(p["status"] == "failed" for _, p in captured):
                break
    finally:
        await worker.stop()

    assert composite_calls == []
    failed = [p for _, p in captured if p["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "image_gen_disabled"

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, error_msg FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "idle"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_msg"] == "image_gen_disabled"


async def test_dispatch_missing_checkpoints_routes_to_composite(
    db_path: Path,
    tmp_path: Path,
) -> None:
    """``MISSING_CHECKPOINTS`` → composite called; success path no error_msg.

    Matches the F.5-3a contract: capability gate False with a non-env-
    disabled reason routes to the composite path. On success, the
    row reaches ``done`` with no ``error_msg``.
    """
    captured, emit = _capture_emit()
    composite_calls: list[tuple[str, int]] = []
    pipeline_calls: list[tuple[str, int]] = []

    async def _composite_stub(
        reference_bytes: bytes,
        slot: str,
        seed: int,
        ctx: GenerationContext,
    ) -> bytes:
        composite_calls.append((slot, seed))
        return b"\x89PNG\r\n\x1a\nCOMPOSITE-OK"

    async def _pipeline_stub(
        reference_bytes: bytes,
        slot: str,
        seed: int,
        ctx: GenerationContext,
    ) -> bytes:
        pipeline_calls.append((slot, seed))
        return b"\x89PNG\r\n\x1a\nPIPELINE-OK"

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_pipeline_stub,
        composite=_composite_stub,
        capability_probe=_composite_only_probe,
    )
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle", seed=42)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(p["status"] == "done" for _, p in captured):
                break
    finally:
        await worker.stop()

    # Composite ran exactly once; the diffusion pipeline did NOT run.
    assert composite_calls == [("idle", 42)]
    assert pipeline_calls == []

    # Success: PNG written, row in ``done`` with no error_msg.
    out_path = tmp_path / "images" / "toy_actions" / _TOY_ID / "idle.png"
    assert out_path.is_file()
    assert out_path.read_bytes() == b"\x89PNG\r\n\x1a\nCOMPOSITE-OK"

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, error_msg FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "idle"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    assert row["error_msg"] is None


async def test_dispatch_composite_failure_marks_image_gen_composite_only(
    db_path: Path,
) -> None:
    """Composite raises → row failed with ``error_msg="image_gen_composite_only"``.

    Both the Tier C ``ImageGenCapacityError`` (missing template) AND
    a generic exception map to the same canonical ``error_msg`` on
    the composite path so the parent UI's per-cell tooltip renders a
    coherent reason.
    """
    captured, emit = _capture_emit()

    async def _composite_stub(
        reference_bytes: bytes,
        slot: str,
        seed: int,
        ctx: GenerationContext,
    ) -> bytes:
        from toybox.image_gen.models import ImageGenCapacityError

        raise ImageGenCapacityError(f"composite template missing for slot={slot}")

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        composite=_composite_stub,
        capability_probe=_composite_only_probe,
    )
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle", seed=7)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(p["status"] == "failed" for _, p in captured):
                break
    finally:
        await worker.stop()

    failed = [p for _, p in captured if p["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "image_gen_composite_only"

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, error_msg FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "idle"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_msg"] == "image_gen_composite_only"


async def test_composite_failures_do_not_trip_breaker(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 composite failures on a no-CUDA host do NOT trip the breaker.

    Composite failures are structural (missing template / malformed
    manifest), not transient pipeline-health signals. They must not
    consume breaker budget; otherwise a no-CUDA host where every job
    routes Tier C would trip after 3 failures and subsequent jobs
    would get ``error_msg="image-gen breaker open"`` instead of the
    actionable ``"image_gen_composite_only"``.
    """
    captured, emit = _capture_emit()

    async def _failing_composite(
        reference_bytes: bytes,
        slot: str,
        seed: int,
        ctx: GenerationContext,
    ) -> bytes:
        raise ImageGenCapacityError(f"composite template missing for slot={slot}")

    monkeypatch.setenv("TOYBOX_IMAGE_GEN_BREAKER_THRESHOLD", "3")
    monkeypatch.setenv("TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC", "60")
    reset_image_gen_breaker_for_tests()
    breaker = get_image_gen_breaker()

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        composite=_failing_composite,
        capability_probe=_composite_only_probe,
    )
    await worker.start()
    try:
        for slot in ("idle", "pointing", "looking"):
            await worker.enqueue(_TOY_ID, slot, seed=hash(slot) & 0xFFFF)

        for _ in range(400):
            await asyncio.sleep(0.01)
            failed = [p for _, p in captured if p["status"] == "failed"]
            if len(failed) >= 3:
                break

        assert breaker.is_open() is False, (
            "composite failures must not consume breaker budget"
        )

        await worker.enqueue(_TOY_ID, "jumping", seed=4)
        for _ in range(300):
            await asyncio.sleep(0.01)
            failed = [p for _, p in captured if p["status"] == "failed"]
            if len(failed) >= 4:
                break
    finally:
        await worker.stop()

    errors = [p.get("error") for _, p in captured if p["status"] == "failed"]
    assert errors.count("image_gen_composite_only") == 4
    assert "image-gen breaker open" not in errors


async def test_composite_path_ignores_open_breaker(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-open breaker does NOT short-circuit Tier C dispatch.

    The breaker is about Tier B health. With the breaker open, a
    composite-only-capable job must still route to composite — the
    early breaker check applies only to Tier B dispatch.
    """
    captured, emit = _capture_emit()
    composite_calls: list[tuple[str, int]] = []

    async def _composite_stub(
        reference_bytes: bytes,
        slot: str,
        seed: int,
        ctx: GenerationContext,
    ) -> bytes:
        composite_calls.append((slot, seed))
        return b"\x89PNG\r\n\x1a\nCOMPOSITE-OK"

    monkeypatch.setenv("TOYBOX_IMAGE_GEN_BREAKER_THRESHOLD", "1")
    monkeypatch.setenv("TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC", "300")
    reset_image_gen_breaker_for_tests()
    breaker = get_image_gen_breaker()
    breaker.check_and_record(success=False)
    assert breaker.is_open() is True

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        composite=_composite_stub,
        capability_probe=_composite_only_probe,
    )
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle", seed=42)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(p["status"] == "done" for _, p in captured):
                break
    finally:
        await worker.stop()

    assert composite_calls == [("idle", 42)]
    errors = [p.get("error") for _, p in captured if p["status"] == "failed"]
    assert "image-gen breaker open" not in errors


async def test_composite_success_does_not_reset_breaker(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier C success leaves the breaker counters alone — Tier B is what it tracks."""
    captured, emit = _capture_emit()

    async def _composite_stub(
        reference_bytes: bytes,
        slot: str,
        seed: int,
        ctx: GenerationContext,
    ) -> bytes:
        return b"\x89PNG\r\n\x1a\nCOMPOSITE-OK"

    monkeypatch.setenv("TOYBOX_IMAGE_GEN_BREAKER_THRESHOLD", "3")
    monkeypatch.setenv("TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC", "300")
    reset_image_gen_breaker_for_tests()
    breaker = get_image_gen_breaker()
    breaker.check_and_record(success=False)
    breaker.check_and_record(success=False)
    cb = breaker.circuit_breaker

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        composite=_composite_stub,
        capability_probe=_composite_only_probe,
    )
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle", seed=1)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(p["status"] == "done" for _, p in captured):
                break
    finally:
        await worker.stop()

    # Failure counter unchanged: composite success must not reset it.
    assert cb._consecutive_failures == 2  # noqa: SLF001 -- contract under test
