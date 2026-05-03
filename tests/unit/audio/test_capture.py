"""Unit tests for MicCapture (callback queueing, overflow, VAD wiring).

Most tests inject synthetic int16 buffers via ``_handle_frame`` directly
instead of going through sounddevice. The dedicated ``_audio_callback``
tests below exercise the actual sounddevice-shape entry point.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from toybox.audio.capture import (
    DEFAULT_RING_SECONDS,
    DEFAULT_SPEECH_QUEUE_MAXSIZE,
    RING_SECONDS_ENV,
    SPEECH_QUEUE_MAXSIZE_ENV,
    MicCapture,
)
from toybox.audio.devices import (
    DEVICE_INDEX_ENV,
    device_index_from_env,
    resolve_device,
)
from toybox.audio.ring_buffer import RingBuffer
from toybox.audio.vad import SAMPLE_RATE, SILERO_FRAME_SAMPLES, Predictor, VadGate


def _frame(value: int = 0, n: int = SILERO_FRAME_SAMPLES) -> NDArray[np.int16]:
    return np.full(n, value, dtype=np.int16)


# ---------------------------------------------------------------------
# Stream stubs for start()/stop() tests that don't want real PortAudio.
# ---------------------------------------------------------------------


class _FakeStream:
    """In-process stand-in for sounddevice.InputStream."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.callback = kwargs.get("callback")
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def _fake_factory(streams: list[_FakeStream]) -> Any:
    def factory(**kwargs: Any) -> _FakeStream:
        s = _FakeStream(**kwargs)
        streams.append(s)
        return s

    return factory


def _failing_factory(exc: Exception) -> Any:
    def factory(**_kwargs: Any) -> _FakeStream:
        raise exc

    return factory


# ---------------------------------------------------------------------
# Bare wiring (used by tests that don't want a real start())
# ---------------------------------------------------------------------


def _make_capture(
    *,
    queue_frames: int = 4,
    predictor: Predictor | None = None,
    ring: RingBuffer | None = None,
    speech_queue_maxsize: int | None = None,
) -> MicCapture:
    pred: Predictor = predictor if predictor is not None else (lambda _f: 0.0)
    return MicCapture(
        vad=VadGate(predictor=pred, threshold=0.5, hangover_frames=0),
        ring_buffer=ring or RingBuffer(capacity_samples=4096, sample_rate=SAMPLE_RATE),
        queue_frames=queue_frames,
        sample_rate=SAMPLE_RATE,
        speech_queue_maxsize=speech_queue_maxsize,
    )


async def _bootstrap_loop_state(capture: MicCapture) -> None:
    """Wire up the asyncio queues without opening a real sounddevice stream."""
    capture._loop = asyncio.get_running_loop()  # noqa: SLF001 -- test seam
    capture._frame_queue = asyncio.Queue(maxsize=capture.queue_frames)  # noqa: SLF001
    capture._speech_queue = asyncio.Queue(maxsize=capture.speech_queue_maxsize)  # noqa: SLF001
    capture._running = True  # noqa: SLF001
    capture._reader_task = asyncio.create_task(capture._reader_loop())  # noqa: SLF001


async def _shutdown(capture: MicCapture) -> None:
    capture._running = False  # noqa: SLF001
    capture._enqueue_sentinel()  # noqa: SLF001
    if capture._reader_task is not None:  # noqa: SLF001
        await capture._reader_task  # noqa: SLF001
        capture._reader_task = None  # noqa: SLF001
    if capture._speech_queue is not None:  # noqa: SLF001
        try:
            capture._speech_queue.put_nowait(None)  # noqa: SLF001
        except asyncio.QueueFull:
            pass


async def _collect_speech(capture: MicCapture) -> list[NDArray[np.int16]]:
    out: list[NDArray[np.int16]] = []
    iterator: AsyncIterator[NDArray[np.int16]] = capture.__aiter__()
    async for chunk in iterator:
        out.append(chunk)
    return out


# ---------------------------------------------------------------------
# _handle_frame -> ring + queue behavior
# ---------------------------------------------------------------------


async def test_handle_frame_pushes_into_ring_via_reader() -> None:
    """Frames pushed via ``_handle_frame`` reach the ring readable by ``snapshot``."""
    capture = _make_capture(queue_frames=4)
    await _bootstrap_loop_state(capture)
    try:
        capture._handle_frame(_frame(1))  # noqa: SLF001
        capture._handle_frame(_frame(2))  # noqa: SLF001
        # Yield so the reader runs.
        for _ in range(4):
            await asyncio.sleep(0)
        snap = capture.snapshot(seconds=1.0)
        assert snap.size == 2 * SILERO_FRAME_SAMPLES
        assert int(snap[0]) == 1
        assert int(snap[-1]) == 2
    finally:
        await _shutdown(capture)


async def test_overflow_drops_oldest_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Reader sees frames 2 and 3 (not 1) when queue overflows by one."""
    capture = _make_capture(queue_frames=2)
    capture._loop = asyncio.get_running_loop()  # noqa: SLF001
    capture._frame_queue = asyncio.Queue(maxsize=2)  # noqa: SLF001
    capture._speech_queue = asyncio.Queue(maxsize=8)  # noqa: SLF001
    capture._running = True  # noqa: SLF001
    # Deliberately do NOT start the reader yet -- we want to stack
    # frames in the queue first to trigger the overflow path.

    capture._handle_frame(_frame(1))  # noqa: SLF001
    capture._handle_frame(_frame(2))  # noqa: SLF001
    assert capture._frame_queue.qsize() == 2  # noqa: SLF001

    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        capture._handle_frame(_frame(3))  # noqa: SLF001

    assert capture.overflow_count == 1
    assert capture._frame_queue.qsize() == 2  # still bounded  # noqa: SLF001

    # Now start the reader and observe what it consumed.
    capture._reader_task = asyncio.create_task(capture._reader_loop())  # noqa: SLF001
    try:
        for _ in range(4):
            await asyncio.sleep(0)
        snap = capture.snapshot(seconds=1.0)
        # Reader should have processed frames 2 and 3 (frame 1 was evicted).
        assert snap.size == 2 * SILERO_FRAME_SAMPLES
        assert int(snap[0]) == 2
        assert int(snap[-1]) == 3
    finally:
        await _shutdown(capture)

    # Printf-style log: substring match on the rendered message.
    overflow_msgs = [
        r.getMessage()
        for r in caplog.records
        if "mic queue overflow" in r.getMessage()
    ]
    assert len(overflow_msgs) == 1
    assert "overflow_count=1" in overflow_msgs[0]
    assert "capacity=2" in overflow_msgs[0]


async def test_speech_chunks_emit_via_async_iterator() -> None:
    """End-to-end: speech-scoring frames flow through the gate."""

    # 3 speech frames followed by silence -> segment closes after hangover=0.
    scores = [0.9, 0.9, 0.9, 0.0]

    class Pred:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _frame: NDArray[np.float32]) -> float:
            score = scores[min(self.calls, len(scores) - 1)]
            self.calls += 1
            return score

    capture = _make_capture(queue_frames=8, predictor=Pred())
    await _bootstrap_loop_state(capture)

    for i in range(len(scores)):
        capture._handle_frame(_frame(i + 1))  # noqa: SLF001

    # Let the reader process all queued frames.
    for _ in range(20):
        await asyncio.sleep(0)

    # Now stop -- flush + sentinel.
    await _shutdown(capture)
    chunks = await _collect_speech(capture)

    assert len(chunks) == 1
    assert chunks[0].size == 3 * SILERO_FRAME_SAMPLES


async def test_snapshot_reflects_recent_frames() -> None:
    capture = _make_capture(
        queue_frames=8,
        ring=RingBuffer(capacity_samples=2 * SILERO_FRAME_SAMPLES, sample_rate=SAMPLE_RATE),
    )
    await _bootstrap_loop_state(capture)
    try:
        # Push 3 frames into a 2-frame ring -- oldest should evict.
        capture._handle_frame(_frame(11))  # noqa: SLF001
        capture._handle_frame(_frame(22))  # noqa: SLF001
        capture._handle_frame(_frame(33))  # noqa: SLF001
        for _ in range(10):
            await asyncio.sleep(0)
        snap = capture.snapshot(seconds=2 * SILERO_FRAME_SAMPLES / SAMPLE_RATE)
        assert snap.size == 2 * SILERO_FRAME_SAMPLES
        # First half = older frame value (22), second half = newest (33).
        assert int(snap[0]) == 22
        assert int(snap[-1]) == 33
    finally:
        await _shutdown(capture)


# ---------------------------------------------------------------------
# _audio_callback (sounddevice-shape entry point)
# ---------------------------------------------------------------------


async def test_audio_callback_delivers_1d_int16_to_ring() -> None:
    """The PortAudio callback emits 2-D (blocksize, 1) int16; we want 1-D in the ring."""
    streams: list[_FakeStream] = []
    capture = MicCapture(
        vad=VadGate(predictor=lambda _f: 0.0, threshold=0.5, hangover_frames=0),
        sample_rate=SAMPLE_RATE,
        blocksize=512,
        queue_frames=4,
        ring_seconds=1.0,
        stream_factory=_fake_factory(streams),
    )
    await capture.start()
    try:
        block = np.full((512, 1), 7, dtype=np.int16)
        capture._audio_callback(block, 512, None, None)  # noqa: SLF001
        # Yield repeatedly so the cross-thread call_soon dispatches.
        for _ in range(8):
            await asyncio.sleep(0)
        snap = capture.snapshot(seconds=1.0)
        assert snap.ndim == 1
        assert snap.dtype == np.int16
        assert snap.size == 512
        assert int(snap[0]) == 7
    finally:
        await capture.stop()


async def test_audio_callback_copies_against_buffer_reuse() -> None:
    """sounddevice reuses ``indata`` -- the callback must defensively copy."""
    streams: list[_FakeStream] = []
    capture = MicCapture(
        vad=VadGate(predictor=lambda _f: 0.0, threshold=0.5, hangover_frames=0),
        sample_rate=SAMPLE_RATE,
        blocksize=512,
        queue_frames=4,
        ring_seconds=1.0,
        stream_factory=_fake_factory(streams),
    )
    await capture.start()
    try:
        block = np.full((512, 1), 42, dtype=np.int16)
        capture._audio_callback(block, 512, None, None)  # noqa: SLF001
        # Now mutate the input AFTER the callback returned. The data
        # already in the ring must be unaffected.
        block[:] = 0
        for _ in range(8):
            await asyncio.sleep(0)
        snap = capture.snapshot(seconds=1.0)
        assert snap.size == 512
        assert int(snap[0]) == 42
        assert int(snap[-1]) == 42
    finally:
        await capture.stop()


async def test_audio_callback_logs_warning_on_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A truthy status flag must surface as a printf-style WARNING log."""
    streams: list[_FakeStream] = []
    capture = MicCapture(
        vad=VadGate(predictor=lambda _f: 0.0, threshold=0.5, hangover_frames=0),
        sample_rate=SAMPLE_RATE,
        blocksize=512,
        queue_frames=4,
        ring_seconds=1.0,
        stream_factory=_fake_factory(streams),
    )
    await capture.start()
    try:
        block = np.zeros((512, 1), dtype=np.int16)
        with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
            capture._audio_callback(block, 512, None, "input overflow")  # noqa: SLF001
        msgs = [r.getMessage() for r in caplog.records if "callback status" in r.getMessage()]
        assert len(msgs) == 1
        assert "input overflow" in msgs[0]
    finally:
        await capture.stop()


# ---------------------------------------------------------------------
# Post-stop callback race & __aiter__ guard
# ---------------------------------------------------------------------


async def test_callback_after_stop_is_silently_dropped() -> None:
    """Frames arriving from PortAudio after stop() must not crash or count overflow."""
    streams: list[_FakeStream] = []
    capture = MicCapture(
        vad=VadGate(predictor=lambda _f: 0.0, threshold=0.5, hangover_frames=0),
        sample_rate=SAMPLE_RATE,
        blocksize=512,
        queue_frames=4,
        ring_seconds=1.0,
        stream_factory=_fake_factory(streams),
    )
    await capture.start()
    await capture.stop()
    # Now simulate PortAudio firing one final callback after we shut down.
    block = np.full((512, 1), 99, dtype=np.int16)
    capture._audio_callback(block, 512, None, None)  # noqa: SLF001
    # No yield strictly required -- _submit_frame_threadsafe should
    # short-circuit because _running is False -- but yield anyway to
    # surface any latent crashes.
    for _ in range(4):
        await asyncio.sleep(0)
    assert capture.overflow_count == 0


async def test_aiter_before_start_raises() -> None:
    capture = _make_capture()
    with pytest.raises(RuntimeError):
        capture.__aiter__()


# ---------------------------------------------------------------------
# Sentinel + queue-full edge case
# ---------------------------------------------------------------------


async def test_enqueue_sentinel_when_queue_full_drops_one_and_succeeds() -> None:
    """``_enqueue_sentinel`` must succeed even when the frame queue is at capacity."""
    capture = _make_capture(queue_frames=2)
    capture._loop = asyncio.get_running_loop()  # noqa: SLF001
    capture._frame_queue = asyncio.Queue(maxsize=2)  # noqa: SLF001
    capture._speech_queue = asyncio.Queue(maxsize=8)  # noqa: SLF001
    # Fill the queue to capacity without going through _handle_frame
    # (so overflow_count stays clean).
    capture._frame_queue.put_nowait(_frame(1))  # noqa: SLF001
    capture._frame_queue.put_nowait(_frame(2))  # noqa: SLF001
    assert capture._frame_queue.qsize() == 2  # noqa: SLF001

    capture._enqueue_sentinel()  # noqa: SLF001

    # Queue still bounded; sentinel (size 0) is now somewhere inside.
    assert capture._frame_queue.qsize() == 2  # noqa: SLF001
    contents = []
    while not capture._frame_queue.empty():  # noqa: SLF001
        contents.append(capture._frame_queue.get_nowait())  # noqa: SLF001
    sizes = [c.size for c in contents]
    assert 0 in sizes


# ---------------------------------------------------------------------
# Flush-on-stop emits in-flight speech via the iterator
# ---------------------------------------------------------------------


async def test_flush_on_stop_emits_inflight_segment_via_iterator() -> None:
    """All-speech frames + stop() -> exactly one chunk arrives via async for."""
    capture = _make_capture(queue_frames=8, predictor=lambda _f: 0.9)
    await _bootstrap_loop_state(capture)

    for _ in range(4):
        capture._handle_frame(_frame(7))  # noqa: SLF001
    for _ in range(20):
        await asyncio.sleep(0)

    await _shutdown(capture)
    chunks = await _collect_speech(capture)
    assert len(chunks) == 1
    assert chunks[0].size == 4 * SILERO_FRAME_SAMPLES


# ---------------------------------------------------------------------
# Reader-loop crash self-heals (MED #4)
# ---------------------------------------------------------------------


async def test_reader_crash_terminates_iterator_and_resets_running() -> None:
    """If VAD raises mid-stream, _running flips false and the iterator EOFs."""

    class BoomPredictor:
        def __call__(self, _frame: NDArray[np.float32]) -> float:
            raise RuntimeError("boom")

    capture = _make_capture(queue_frames=4, predictor=BoomPredictor())
    await _bootstrap_loop_state(capture)

    capture._handle_frame(_frame(1))  # noqa: SLF001
    # Wait for the reader task to crash.
    for _ in range(20):
        await asyncio.sleep(0)
        if capture._reader_task is not None and capture._reader_task.done():  # noqa: SLF001
            break

    # Reader should have crashed and self-healed: _running flipped off.
    assert capture._running is False  # noqa: SLF001

    # The iterator should terminate cleanly via the sentinel pushed by
    # the crash handler.
    chunks = await _collect_speech(capture)
    assert chunks == []

    # Now stop() must be a clean no-op (no hang, no second crash).
    await capture.stop()


async def test_reader_crash_stops_and_closes_stream() -> None:
    """Reader crash must stop+close the stream so PortAudio winds down."""

    class BoomPredictor:
        def __call__(self, _frame: NDArray[np.float32]) -> float:
            raise RuntimeError("boom")

    streams: list[_FakeStream] = []
    capture = MicCapture(
        vad=VadGate(predictor=BoomPredictor(), threshold=0.5, hangover_frames=0),
        sample_rate=SAMPLE_RATE,
        blocksize=512,
        queue_frames=4,
        ring_seconds=1.0,
        stream_factory=_fake_factory(streams),
    )
    await capture.start()
    assert len(streams) == 1
    fake_stream = streams[0]
    assert fake_stream.started is True
    assert fake_stream.stopped is False
    assert fake_stream.closed is False

    # Drive a frame through the (real) callback path to trigger the boom.
    block = np.full((512, 1), 1, dtype=np.int16)
    capture._audio_callback(block, 512, None, None)  # noqa: SLF001
    for _ in range(20):
        await asyncio.sleep(0)
        if capture._reader_task is not None and capture._reader_task.done():  # noqa: SLF001
            break

    # Reader crashed AND stopped/closed the stream as part of self-heal.
    assert capture._running is False  # noqa: SLF001
    assert fake_stream.stopped is True
    assert fake_stream.closed is True

    # stop() remains the canonical cleanup path -- must still be a clean
    # no-op (idempotent stop/close on the FakeStream tolerates re-entry).
    await capture.stop()


async def test_stream_factory_returning_none_raises_clear_error() -> None:
    """A factory that returns None instead of a stream must surface a clear error."""

    def factory(**_kwargs: Any) -> Any:
        return None

    capture = MicCapture(
        vad=VadGate(predictor=lambda _f: 0.0, threshold=0.5, hangover_frames=0),
        sample_rate=SAMPLE_RATE,
        blocksize=512,
        queue_frames=4,
        ring_seconds=1.0,
        stream_factory=factory,
    )
    with pytest.raises(RuntimeError, match="stream_factory returned None"):
        await capture.start()
    # State rolled back -- a follow-up start() with a working factory works.
    assert capture.is_running is False


async def test_stop_completes_iterator_when_reader_already_dead() -> None:
    """stop() must push the EOF sentinel even if awaiting reader_task raised."""

    class BoomPredictor:
        def __call__(self, _frame: NDArray[np.float32]) -> float:
            raise RuntimeError("boom")

    streams: list[_FakeStream] = []
    capture = MicCapture(
        vad=VadGate(predictor=BoomPredictor(), threshold=0.5, hangover_frames=0),
        sample_rate=SAMPLE_RATE,
        blocksize=512,
        queue_frames=4,
        ring_seconds=1.0,
        stream_factory=_fake_factory(streams),
    )
    await capture.start()
    # Drive a frame through the (real) callback path so the reader hits
    # the boom predictor.
    block = np.full((512, 1), 1, dtype=np.int16)
    capture._audio_callback(block, 512, None, None)  # noqa: SLF001
    for _ in range(20):
        await asyncio.sleep(0)
        if capture._reader_task is not None and capture._reader_task.done():  # noqa: SLF001
            break

    await capture.stop()
    # Iterator terminates without hanging.
    chunks: list[NDArray[np.int16]] = []
    async for c in capture:
        chunks.append(c)
    assert chunks == []


# ---------------------------------------------------------------------
# start() partial failure (MED #3)
# ---------------------------------------------------------------------


async def test_start_partial_failure_leaves_clean_restartable_state() -> None:
    """If the stream factory raises, start() propagates AND the next start works."""
    attempts = {"n": 0}
    streams: list[_FakeStream] = []

    def factory(**kwargs: Any) -> _FakeStream:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("PortAudio refused the device")
        s = _FakeStream(**kwargs)
        streams.append(s)
        return s

    capture = MicCapture(
        vad=VadGate(predictor=lambda _f: 0.0, threshold=0.5, hangover_frames=0),
        sample_rate=SAMPLE_RATE,
        blocksize=512,
        queue_frames=4,
        ring_seconds=1.0,
        stream_factory=factory,
    )

    with pytest.raises(OSError):
        await capture.start()
    assert capture.is_running is False

    # Second attempt must succeed because state was rolled back.
    await capture.start()
    assert capture.is_running is True
    await capture.stop()


# ---------------------------------------------------------------------
# LSTM reset_state (MED #5)
# ---------------------------------------------------------------------


async def test_predictor_reset_state_called_per_closed_segment() -> None:
    scores = [0.9, 0.9, 0.0]  # speech, speech, silence -> closes with hangover=0

    class StubPredictor:
        def __init__(self) -> None:
            self.calls = 0
            self.resets = 0

        def __call__(self, _f: NDArray[np.float32]) -> float:
            score = scores[min(self.calls, len(scores) - 1)]
            self.calls += 1
            return score

        def reset_state(self) -> None:
            self.resets += 1

    pred = StubPredictor()
    capture = _make_capture(queue_frames=8, predictor=pred)
    await _bootstrap_loop_state(capture)
    try:
        for i in range(len(scores)):
            capture._handle_frame(_frame(i + 1))  # noqa: SLF001
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        await _shutdown(capture)
    chunks = await _collect_speech(capture)
    assert len(chunks) == 1
    # Exactly one segment closed -> exactly one reset_state.
    assert pred.resets == 1


async def test_reset_state_no_op_if_predictor_lacks_method() -> None:
    """Predictors without reset_state must keep working."""
    scores = [0.9, 0.9, 0.0]

    class NoResetPred:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _f: NDArray[np.float32]) -> float:
            score = scores[min(self.calls, len(scores) - 1)]
            self.calls += 1
            return score

    capture = _make_capture(queue_frames=8, predictor=NoResetPred())
    await _bootstrap_loop_state(capture)
    try:
        for i in range(len(scores)):
            capture._handle_frame(_frame(i + 1))  # noqa: SLF001
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        await _shutdown(capture)
    chunks = await _collect_speech(capture)
    assert len(chunks) == 1


# ---------------------------------------------------------------------
# Speech queue drop-OLDEST (MED #6)
# ---------------------------------------------------------------------


async def test_speech_queue_overflow_drops_oldest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the speech queue is at capacity, the OLDEST chunk is dropped."""

    capture = _make_capture(queue_frames=8, speech_queue_maxsize=2)
    capture._loop = asyncio.get_running_loop()  # noqa: SLF001
    capture._frame_queue = asyncio.Queue(maxsize=8)  # noqa: SLF001
    capture._speech_queue = asyncio.Queue(maxsize=2)  # noqa: SLF001

    chunk_a = np.full(SILERO_FRAME_SAMPLES, 1, dtype=np.int16)
    chunk_b = np.full(SILERO_FRAME_SAMPLES, 2, dtype=np.int16)
    chunk_c = np.full(SILERO_FRAME_SAMPLES, 3, dtype=np.int16)

    await capture._push_speech_chunk(chunk_a)  # noqa: SLF001
    await capture._push_speech_chunk(chunk_b)  # noqa: SLF001
    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        await capture._push_speech_chunk(chunk_c)  # noqa: SLF001

    assert capture.speech_overflow_count == 1

    contents: list[NDArray[np.int16]] = []
    while not capture._speech_queue.empty():  # noqa: SLF001
        item = capture._speech_queue.get_nowait()  # noqa: SLF001
        assert item is not None
        contents.append(item)
    # Drop-oldest: a is gone, b and c remain in order.
    assert len(contents) == 2
    assert int(contents[0][0]) == 2
    assert int(contents[1][0]) == 3

    msgs = [r.getMessage() for r in caplog.records if "speech queue overflow" in r.getMessage()]
    assert len(msgs) == 1
    assert "overflow_count=1" in msgs[0]
    assert "capacity=2" in msgs[0]


async def test_push_speech_chunk_preserves_eof_sentinel_at_head() -> None:
    """If the queue is full and the head is the EOF sentinel, preserve it.

    Drop-oldest must NOT discard the sentinel: consumers depend on it to
    terminate ``async for``. The new chunk is dropped instead, and
    ``_speech_overflow_count`` is NOT bumped (the sentinel-preserve
    branch is conceptually a clean shutdown, not a runtime overflow).
    """
    capture = _make_capture(queue_frames=4, speech_queue_maxsize=1)
    capture._loop = asyncio.get_running_loop()  # noqa: SLF001
    capture._frame_queue = asyncio.Queue(maxsize=4)  # noqa: SLF001
    capture._speech_queue = asyncio.Queue(maxsize=1)  # noqa: SLF001

    # Seed the queue with the EOF sentinel.
    await capture._push_speech_chunk(None)  # type: ignore[arg-type]  # noqa: SLF001

    # Now push a real chunk -- queue is full, head is sentinel.
    chunk = np.full(SILERO_FRAME_SAMPLES, 9, dtype=np.int16)
    await capture._push_speech_chunk(chunk)  # noqa: SLF001

    # Sentinel preserved; chunk dropped silently.
    assert capture._speech_queue.qsize() == 1  # noqa: SLF001
    head = capture._speech_queue.get_nowait()  # noqa: SLF001
    assert head is None
    assert capture.speech_overflow_count == 0


# ---------------------------------------------------------------------
# device_index validation (MED #7)
# ---------------------------------------------------------------------


def test_device_index_negative_rejected() -> None:
    with pytest.raises(ValueError):
        MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            device_index=-1,
        )


def test_device_index_non_int_rejected() -> None:
    with pytest.raises(ValueError):
        MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            device_index="abc",  # type: ignore[arg-type]
        )


def test_device_index_zero_accepted() -> None:
    capture = MicCapture(
        vad=VadGate(predictor=lambda _f: 0.0),
        device_index=0,
    )
    assert capture is not None


# ---------------------------------------------------------------------
# Speech queue maxsize env (MED #6)
# ---------------------------------------------------------------------


def test_speech_queue_maxsize_env_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SPEECH_QUEUE_MAXSIZE_ENV, "16")
    capture = MicCapture(
        vad=VadGate(predictor=lambda _f: 0.0),
        sample_rate=SAMPLE_RATE,
    )
    assert capture.speech_queue_maxsize == 16


def test_speech_queue_maxsize_env_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(SPEECH_QUEUE_MAXSIZE_ENV, "garbage")
    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        capture = MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            sample_rate=SAMPLE_RATE,
        )
    assert capture.speech_queue_maxsize == DEFAULT_SPEECH_QUEUE_MAXSIZE


def test_speech_queue_maxsize_env_zero_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Zero is non-positive; must fall back to default with a warning."""
    monkeypatch.setenv(SPEECH_QUEUE_MAXSIZE_ENV, "0")
    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        capture = MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            sample_rate=SAMPLE_RATE,
        )
    assert capture.speech_queue_maxsize == DEFAULT_SPEECH_QUEUE_MAXSIZE
    msgs = [
        r.getMessage()
        for r in caplog.records
        if SPEECH_QUEUE_MAXSIZE_ENV in r.getMessage()
    ]
    assert any("must be > 0" in m for m in msgs)


def test_speech_queue_maxsize_env_negative_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Negative ints are non-positive; must fall back with a warning."""
    monkeypatch.setenv(SPEECH_QUEUE_MAXSIZE_ENV, "-5")
    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        capture = MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            sample_rate=SAMPLE_RATE,
        )
    assert capture.speech_queue_maxsize == DEFAULT_SPEECH_QUEUE_MAXSIZE
    msgs = [
        r.getMessage()
        for r in caplog.records
        if SPEECH_QUEUE_MAXSIZE_ENV in r.getMessage()
    ]
    assert any("must be > 0" in m for m in msgs)


# ---------------------------------------------------------------------
# Device resolution
# ---------------------------------------------------------------------


def test_resolve_device_default_is_none() -> None:
    assert resolve_device(None) is None
    assert resolve_device("") is None
    assert resolve_device("default") is None
    assert resolve_device("DEFAULT") is None


def test_resolve_device_parses_int() -> None:
    assert resolve_device("3") == 3
    assert resolve_device("0") == 0


def test_resolve_device_invalid_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="toybox.audio.devices"):
        assert resolve_device("not-an-int") is None
        assert resolve_device("-1") is None
    assert any("TOYBOX_MIC_DEVICE_INDEX" in r.getMessage() for r in caplog.records)


def test_device_index_from_env_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEVICE_INDEX_ENV, "7")
    assert device_index_from_env() == 7
    monkeypatch.delenv(DEVICE_INDEX_ENV)
    assert device_index_from_env() is None


# ---------------------------------------------------------------------
# Ring-seconds env
# ---------------------------------------------------------------------


def test_ring_seconds_env_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RING_SECONDS_ENV, "30")
    capture = MicCapture(
        vad=VadGate(predictor=lambda _f: 0.0),
        sample_rate=SAMPLE_RATE,
    )
    assert capture.ring_buffer.capacity_samples == 30 * SAMPLE_RATE


def test_ring_seconds_env_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(RING_SECONDS_ENV, "garbage")
    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        capture = MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            sample_rate=SAMPLE_RATE,
        )
    assert capture.ring_buffer.capacity_samples == int(DEFAULT_RING_SECONDS * SAMPLE_RATE)


def test_ring_seconds_env_inf_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(RING_SECONDS_ENV, "inf")
    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        capture = MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            sample_rate=SAMPLE_RATE,
        )
    assert capture.ring_buffer.capacity_samples == int(DEFAULT_RING_SECONDS * SAMPLE_RATE)


def test_ring_seconds_env_nan_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(RING_SECONDS_ENV, "nan")
    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        capture = MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            sample_rate=SAMPLE_RATE,
        )
    assert capture.ring_buffer.capacity_samples == int(DEFAULT_RING_SECONDS * SAMPLE_RATE)


def test_ring_seconds_env_zero_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Zero is non-positive; must fall back to default with a warning."""
    monkeypatch.setenv(RING_SECONDS_ENV, "0")
    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        capture = MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            sample_rate=SAMPLE_RATE,
        )
    assert capture.ring_buffer.capacity_samples == int(DEFAULT_RING_SECONDS * SAMPLE_RATE)
    msgs = [
        r.getMessage() for r in caplog.records if RING_SECONDS_ENV in r.getMessage()
    ]
    assert any("must be > 0" in m for m in msgs)


def test_ring_seconds_env_negative_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Negative floats are non-positive; must fall back with a warning."""
    monkeypatch.setenv(RING_SECONDS_ENV, "-1.5")
    with caplog.at_level(logging.WARNING, logger="toybox.audio.capture"):
        capture = MicCapture(
            vad=VadGate(predictor=lambda _f: 0.0),
            sample_rate=SAMPLE_RATE,
        )
    assert capture.ring_buffer.capacity_samples == int(DEFAULT_RING_SECONDS * SAMPLE_RATE)
    msgs = [
        r.getMessage() for r in caplog.records if RING_SECONDS_ENV in r.getMessage()
    ]
    assert any("must be > 0" in m for m in msgs)
