"""Phase Z Z4 — TtsWorker unit suite (stub engine, no optional deps).

Covers the worker contract from the plan §5 / issue #6:

* fire-and-forget enqueue that never blocks the caller,
* capability-gated no-op,
* skip-if-exists at BOTH enqueue and drain time,
* per-item exception isolation (one failed synth never kills the loop),
* bounded-queue drop,
* clean shutdown (drain within grace, then cancel),
* the module-level singleton + ``enqueue_clip`` convenience.

All waits are condition-polls under ``asyncio.timeout`` — bounded, not
timing races.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from toybox.tts import worker as worker_mod
from toybox.tts.cache import clip_path
from toybox.tts.engine import _stub_wav_bytes
from toybox.tts.worker import (
    TtsWorker,
    enqueue_clip,
    get_tts_worker,
    reset_tts_worker_for_tests,
    start_tts_worker,
    stop_tts_worker,
)

_VOICE = "af_heart"


@pytest.fixture(autouse=True)
def _reset_singleton() -> Iterator[None]:
    reset_tts_worker_for_tests()
    yield
    reset_tts_worker_for_tests()


@pytest.fixture
def stub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub engine + isolated data root for every worker test."""
    monkeypatch.setenv("TOYBOX_TTS_STUB", "1")
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    return tmp_path


async def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll ``predicate()`` until true, bounded by ``timeout``."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


# ---------------------------------------------------------------------
# Happy path — stub render lands a valid WAV at the cache path
# ---------------------------------------------------------------------


async def test_renders_stub_clip_to_cache_path(stub_env: Path) -> None:
    worker = TtsWorker()
    await worker.start()
    try:
        text = "Step one spoken aloud."
        assert worker.enqueue(text, _VOICE) is True
        path = clip_path(_VOICE, text)
        await _wait_for(path.is_file)
        raw = path.read_bytes()
        assert raw[:4] == b"RIFF"
        assert raw[8:12] == b"WAVE"
        assert worker.rendered_count == 1
        # No half-written temp file left behind (write-then-rename).
        assert list(path.parent.glob("*.tmp")) == []
    finally:
        await worker.stop()


# ---------------------------------------------------------------------
# Enqueue no-op branches
# ---------------------------------------------------------------------


async def test_enqueue_is_capability_gated(stub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TTS not capable → nothing queued, consumer stays parked."""
    monkeypatch.setattr(worker_mod, "is_tts_capable", lambda: False)
    worker = TtsWorker()
    await worker.start()
    try:
        assert worker.enqueue("never rendered", _VOICE) is False
        assert worker.queue_size == 0
        assert not clip_path(_VOICE, "never rendered").exists()
    finally:
        await worker.stop()


async def test_enqueue_skips_existing_clip(stub_env: Path) -> None:
    text = "Already rendered once."
    path = clip_path(_VOICE, text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFFxxxxWAVE")
    worker = TtsWorker()
    await worker.start()
    try:
        assert worker.enqueue(text, _VOICE) is False
        assert worker.queue_size == 0
        # The pre-existing bytes are untouched (no re-render).
        assert path.read_bytes() == b"RIFFxxxxWAVE"
    finally:
        await worker.stop()


async def test_enqueue_rejects_blank_and_unsafe_inputs(stub_env: Path) -> None:
    worker = TtsWorker()
    await worker.start()
    try:
        assert worker.enqueue("", _VOICE) is False
        assert worker.enqueue("   ", _VOICE) is False
        assert worker.enqueue("text", "  ") is False
        assert worker.enqueue("text", "../evil") is False
        assert worker.queue_size == 0
    finally:
        await worker.stop()


async def test_enqueue_before_start_and_after_stop_no_ops(stub_env: Path) -> None:
    worker = TtsWorker()
    assert worker.enqueue("too early", _VOICE) is False
    await worker.start()
    await worker.stop()
    assert worker.enqueue("too late", _VOICE) is False
    assert not clip_path(_VOICE, "too early").exists()
    assert not clip_path(_VOICE, "too late").exists()


# ---------------------------------------------------------------------
# Non-blocking enqueue + bounded-queue drop (deterministic: the synth
# is parked on a threading.Event, so the consumer is provably busy
# while the enqueues return)
# ---------------------------------------------------------------------


async def test_enqueue_returns_while_synth_is_blocked(
    stub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    in_synth = threading.Event()

    def blocking_synth(text: str, voice: str) -> bytes:
        in_synth.set()
        assert release.wait(timeout=10.0)
        return _stub_wav_bytes(text, voice)

    monkeypatch.setattr(worker_mod, "synthesize", blocking_synth)
    worker = TtsWorker()
    await worker.start()
    try:
        assert worker.enqueue("first clip", _VOICE) is True
        await _wait_for(in_synth.is_set)
        # Consumer is provably stuck inside synthesize — this enqueue
        # returning at all proves the request path never blocks on the
        # render (a blocking implementation would deadlock here, since
        # only THIS coroutine ever sets ``release``).
        assert worker.enqueue("second clip", _VOICE) is True
        assert not release.is_set()
        release.set()
        await _wait_for(clip_path(_VOICE, "second clip").is_file)
    finally:
        release.set()
        await worker.stop()


async def test_full_queue_drops_instead_of_blocking(
    stub_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    release = threading.Event()
    in_synth = threading.Event()

    def blocking_synth(text: str, voice: str) -> bytes:
        in_synth.set()
        assert release.wait(timeout=10.0)
        return _stub_wav_bytes(text, voice)

    monkeypatch.setattr(worker_mod, "synthesize", blocking_synth)
    worker = TtsWorker(queue_maxsize=1)
    await worker.start()
    try:
        assert worker.enqueue("in flight", _VOICE) is True
        await _wait_for(in_synth.is_set)  # popped; queue now empty
        assert worker.enqueue("queued", _VOICE) is True
        with caplog.at_level(logging.WARNING, logger="toybox.tts.worker"):
            assert worker.enqueue("dropped", _VOICE) is False  # queue full
        # The WARNING is the only observability for a dropped clip —
        # pin it so a silent-drop regression is visible.
        assert any(
            record.levelno == logging.WARNING and "queue full" in record.getMessage()
            for record in caplog.records
        ), caplog.records
        release.set()
        await _wait_for(clip_path(_VOICE, "queued").is_file)
        assert not clip_path(_VOICE, "dropped").exists()
    finally:
        release.set()
        await worker.stop()


# ---------------------------------------------------------------------
# Drain-side behaviours
# ---------------------------------------------------------------------


async def test_drain_skips_clip_rendered_while_queued(
    stub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip-if-exists is re-checked at drain: a clip that materialised
    while its job sat in the queue is never re-synthesised."""
    release = threading.Event()
    in_synth = threading.Event()
    calls: list[str] = []

    def recording_synth(text: str, voice: str) -> bytes:
        calls.append(text)
        in_synth.set()
        assert release.wait(timeout=10.0)
        return _stub_wav_bytes(text, voice)

    monkeypatch.setattr(worker_mod, "synthesize", recording_synth)
    worker = TtsWorker()
    await worker.start()
    try:
        assert worker.enqueue("blocker", _VOICE) is True
        await _wait_for(in_synth.is_set)
        assert worker.enqueue("later", _VOICE) is True
        # "later" materialises (e.g. an operator batch) before drain.
        later_path = clip_path(_VOICE, "later")
        later_path.parent.mkdir(parents=True, exist_ok=True)
        later_path.write_bytes(b"RIFFpre-WAVE")
        release.set()
        await asyncio.wait_for(worker._queue.join(), timeout=5.0)
        assert calls == ["blocker"], calls
        assert later_path.read_bytes() == b"RIFFpre-WAVE"
    finally:
        release.set()
        await worker.stop()


async def test_per_item_exception_isolation(
    stub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising synth (raw ImportError/RuntimeError per the engine's
    documented contract) is logged + skipped; the NEXT job renders."""

    def flaky_synth(text: str, voice: str) -> bytes:
        if text == "boom":
            raise RuntimeError("onnx session exploded")
        return _stub_wav_bytes(text, voice)

    monkeypatch.setattr(worker_mod, "synthesize", flaky_synth)
    worker = TtsWorker()
    await worker.start()
    try:
        assert worker.enqueue("boom", _VOICE) is True
        assert worker.enqueue("survivor", _VOICE) is True
        await _wait_for(clip_path(_VOICE, "survivor").is_file)
        assert not clip_path(_VOICE, "boom").exists()
        assert worker.rendered_count == 1
    finally:
        await worker.stop()


async def test_stop_drains_queued_jobs_within_grace(stub_env: Path) -> None:
    worker = TtsWorker()
    await worker.start()
    texts = ["clip one", "clip two", "clip three"]
    for text in texts:
        assert worker.enqueue(text, _VOICE) is True
    await worker.stop()
    for text in texts:
        assert clip_path(_VOICE, text).is_file(), text
    assert worker.rendered_count == 3


async def test_stop_grace_timeout_cancels_wedged_consumer(
    stub_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The grace-timeout branch of ``stop()``: a wedged in-flight synth
    (never released until AFTER stop returns) must not hang shutdown —
    ``wait_for(queue.join())`` times out, logs the WARNING, and the
    consumer task is cancelled. A regression here means the app's
    lifespan shutdown hangs for real."""
    release = threading.Event()
    in_synth = threading.Event()

    def wedged_synth(text: str, voice: str) -> bytes:
        in_synth.set()
        assert release.wait(timeout=30.0)
        return _stub_wav_bytes(text, voice)

    monkeypatch.setattr(worker_mod, "synthesize", wedged_synth)
    worker = TtsWorker(shutdown_grace_sec=0.05)
    await worker.start()
    assert worker.enqueue("wedged clip", _VOICE) is True
    await _wait_for(in_synth.is_set)  # consumer provably stuck in synth
    assert worker.enqueue("backlog clip", _VOICE) is True  # join can't clear

    try:
        with caplog.at_level(logging.WARNING, logger="toybox.tts.worker"):
            # Bounded: stop() must return promptly via the timeout →
            # cancel path, NOT wait for ``release`` (only set below).
            await asyncio.wait_for(worker.stop(), timeout=5.0)
    finally:
        release.set()  # let the orphaned synth thread finish cleanly

    assert worker._consumer is None  # consumer cancelled + dropped
    assert worker.enqueue("after stop", _VOICE) is False
    assert any(
        record.levelno == logging.WARNING and "shutdown grace" in record.getMessage()
        for record in caplog.records
    ), caplog.records


# ---------------------------------------------------------------------
# Module-level singleton + enqueue_clip (the API-layer entry point)
# ---------------------------------------------------------------------


async def test_singleton_lifecycle_and_enqueue_clip(stub_env: Path) -> None:
    # No worker running → module-level enqueue no-ops.
    assert get_tts_worker() is None
    assert enqueue_clip("orphan text", _VOICE) is False

    worker = await start_tts_worker()
    try:
        assert get_tts_worker() is worker
        # Idempotent start returns the same instance.
        assert await start_tts_worker() is worker
        assert enqueue_clip("via module helper", _VOICE) is True
        await _wait_for(clip_path(_VOICE, "via module helper").is_file)
    finally:
        await stop_tts_worker()

    assert get_tts_worker() is None
    assert enqueue_clip("after stop", _VOICE) is False
    # Idempotent stop.
    await stop_tts_worker()
