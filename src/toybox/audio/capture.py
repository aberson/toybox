"""Mic capture daemon: sounddevice callback - asyncio queue - VAD gate.

The capture lifecycle is:

1. :meth:`MicCapture.start` opens a sounddevice ``InputStream`` and
   captures the running asyncio loop reference. The PortAudio worker
   thread invokes ``_audio_callback`` with int16 frames; that callback
   forwards each frame to the asyncio side via
   ``loop.call_soon_threadsafe``. The callback never blocks on the
   queue: if the queue is at capacity we drop the OLDEST frame (and log
   a ``mic queue overflow`` warning) to keep latency bounded.
2. :meth:`MicCapture.__aiter__` yields VAD-gated speech chunks. The
   reader task pulls int16 frames off the queue, mirrors them into the
   rolling :class:`RingBuffer`, runs the :class:`VadGate`, and pushes
   speech segments to a second async queue that ``__aiter__`` consumes.
3. :meth:`MicCapture.stop` closes the stream, flushes any open VAD
   segment (so a phrase mid-utterance at shutdown still emits), and
   joins the reader task.

The ``--test N`` operator entry captures ``N`` seconds, prints the
detected device name + peak dBFS + overflow count, and exits cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import os
import sys
from collections.abc import AsyncIterator, Callable
from types import TracebackType
from typing import TYPE_CHECKING, Any, Final

import numpy as np
from numpy.typing import NDArray

from .devices import (
    DEVICE_INDEX_ENV,
    device_index_from_env,
    device_name,
)
from .ring_buffer import RingBuffer
from .vad import SAMPLE_RATE, SileroVadPredictor, VadGate

if TYPE_CHECKING:
    import sounddevice as sd

_logger = logging.getLogger(__name__)

# Mic queue cap: 64 frames at 512 samples / 16 kHz ~= 2.05 s of headroom.
# Generous enough that a single GC pause won't drop audio, tight enough
# that a stalled consumer surfaces overflow within seconds.
DEFAULT_QUEUE_FRAMES: Final[int] = 64

DEFAULT_BLOCKSIZE: Final[int] = 512  # matches silero's frame size

RING_SECONDS_ENV: Final[str] = "TOYBOX_AUDIO_RING_SECONDS"
DEFAULT_RING_SECONDS: Final[float] = 120.0

SPEECH_QUEUE_MAXSIZE_ENV: Final[str] = "TOYBOX_AUDIO_SPEECH_QUEUE_MAXSIZE"
DEFAULT_SPEECH_QUEUE_MAXSIZE: Final[int] = 64

# Type alias for an injectable sounddevice.InputStream factory. Tests
# pass a stub so we never need a real PortAudio device.
StreamFactory = Callable[..., Any]


def _ring_seconds_from_env() -> float:
    raw = os.environ.get(RING_SECONDS_ENV)
    if raw is None:
        return DEFAULT_RING_SECONDS
    try:
        value = float(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not a float; falling back to %.0f",
            RING_SECONDS_ENV,
            raw,
            DEFAULT_RING_SECONDS,
        )
        return DEFAULT_RING_SECONDS
    if not math.isfinite(value):
        _logger.warning(
            "%s=%r is not finite; falling back to %.0f",
            RING_SECONDS_ENV,
            raw,
            DEFAULT_RING_SECONDS,
        )
        return DEFAULT_RING_SECONDS
    if value <= 0:
        _logger.warning(
            "%s=%.3f must be > 0; falling back to %.0f",
            RING_SECONDS_ENV,
            value,
            DEFAULT_RING_SECONDS,
        )
        return DEFAULT_RING_SECONDS
    return value


def _speech_queue_maxsize_from_env() -> int:
    raw = os.environ.get(SPEECH_QUEUE_MAXSIZE_ENV)
    if raw is None:
        return DEFAULT_SPEECH_QUEUE_MAXSIZE
    try:
        value = int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int; falling back to %d",
            SPEECH_QUEUE_MAXSIZE_ENV,
            raw,
            DEFAULT_SPEECH_QUEUE_MAXSIZE,
        )
        return DEFAULT_SPEECH_QUEUE_MAXSIZE
    if value <= 0:
        _logger.warning(
            "%s=%d must be > 0; falling back to %d",
            SPEECH_QUEUE_MAXSIZE_ENV,
            value,
            DEFAULT_SPEECH_QUEUE_MAXSIZE,
        )
        return DEFAULT_SPEECH_QUEUE_MAXSIZE
    return value


class MicCapture:
    """Capture int16 audio, ring-buffer it, and emit VAD-gated speech.

    The class is instantiable (no module-level singleton) so the
    application's lifespan can wire it up explicitly. Tests inject a
    stub ``vad_predictor`` and a ``stream_factory`` and feed synthetic
    frames straight into :meth:`_handle_frame` -- they never touch
    sounddevice.
    """

    def __init__(
        self,
        *,
        vad: VadGate | None = None,
        ring_buffer: RingBuffer | None = None,
        device_index: int | None = None,
        sample_rate: int = SAMPLE_RATE,
        blocksize: int = DEFAULT_BLOCKSIZE,
        queue_frames: int = DEFAULT_QUEUE_FRAMES,
        ring_seconds: float | None = None,
        speech_queue_maxsize: int | None = None,
        stream_factory: StreamFactory | None = None,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be > 0, got {sample_rate}")
        if blocksize <= 0:
            raise ValueError(f"blocksize must be > 0, got {blocksize}")
        if queue_frames <= 0:
            raise ValueError(f"queue_frames must be > 0, got {queue_frames}")
        if device_index is not None:
            if not isinstance(device_index, int) or isinstance(device_index, bool):
                raise ValueError(
                    f"device_index must be a non-negative int or None, got {device_index!r}"
                )
            if device_index < 0:
                raise ValueError(
                    f"device_index must be >= 0, got {device_index}"
                )

        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._queue_frames = queue_frames
        self._device_index = device_index
        self._stream_factory = stream_factory

        if speech_queue_maxsize is None:
            speech_queue_maxsize = _speech_queue_maxsize_from_env()
        if speech_queue_maxsize <= 0:
            raise ValueError(
                f"speech_queue_maxsize must be > 0, got {speech_queue_maxsize}"
            )
        self._speech_queue_maxsize = speech_queue_maxsize

        seconds = ring_seconds if ring_seconds is not None else _ring_seconds_from_env()
        self._ring = (
            ring_buffer
            if ring_buffer is not None
            else RingBuffer.from_seconds(seconds=seconds, sample_rate=sample_rate)
        )
        if vad is None:
            vad = VadGate(predictor=SileroVadPredictor())
        self._vad = vad

        # Bounded async queue of incoming int16 frames. Lazily created
        # in ``start`` so ``__init__`` doesn't require a running loop.
        self._frame_queue: asyncio.Queue[NDArray[np.int16]] | None = None
        # Bounded output queue of VAD-gated speech segments, drained by
        # ``__aiter__``.
        self._speech_queue: asyncio.Queue[NDArray[np.int16] | None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: sd.InputStream | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._overflow_count = 0
        self._speech_overflow_count = 0
        self._running = False

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def blocksize(self) -> int:
        return self._blocksize

    @property
    def queue_frames(self) -> int:
        return self._queue_frames

    @property
    def overflow_count(self) -> int:
        return self._overflow_count

    @property
    def speech_overflow_count(self) -> int:
        return self._speech_overflow_count

    @property
    def speech_queue_maxsize(self) -> int:
        return self._speech_queue_maxsize

    @property
    def ring_buffer(self) -> RingBuffer:
        return self._ring

    @property
    def vad(self) -> VadGate:
        return self._vad

    @property
    def is_running(self) -> bool:
        return self._running

    def snapshot(self, seconds: float) -> NDArray[np.int16]:
        """Return the most recent ``seconds`` of audio from the ring buffer."""
        return self._ring.snapshot(seconds=seconds)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the mic stream and start the reader task.

        ``_running`` is only flipped after the stream has been
        successfully opened and started, so a partial-failure leaves
        the object in a clean re-startable state.
        """
        if self._running:
            return

        loop = asyncio.get_running_loop()
        frame_queue: asyncio.Queue[NDArray[np.int16]] = asyncio.Queue(
            maxsize=self._queue_frames
        )
        speech_queue: asyncio.Queue[NDArray[np.int16] | None] = asyncio.Queue(
            maxsize=self._speech_queue_maxsize
        )

        # Resolve device name eagerly for the start log; not fatal on
        # failure.
        try:
            resolved_name = device_name(self._device_index)
        except Exception as exc:  # pragma: no cover -- diagnostics only
            _logger.warning("could not resolve device name: %s", exc)
            resolved_name = "<unknown>"

        # Open the sounddevice stream. Lazy-import keeps the module
        # importable on machines without PortAudio (CI).
        if self._stream_factory is None:
            import sounddevice as sd  # noqa: PLC0415

            stream_factory: StreamFactory = sd.InputStream
        else:
            stream_factory = self._stream_factory

        # Bind queues / loop BEFORE we wire the callback so an early
        # callback firing from PortAudio finds them ready. We tear the
        # state back down in the failure path below.
        self._loop = loop
        self._frame_queue = frame_queue
        self._speech_queue = speech_queue
        self._overflow_count = 0
        self._speech_overflow_count = 0

        try:
            stream = stream_factory(
                device=self._device_index,
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self._blocksize,
                callback=self._audio_callback,
            )
            if stream is None:
                raise RuntimeError("stream_factory returned None")
            stream.start()
        except Exception:
            # Roll back state so a follow-up start() can try again.
            self._loop = None
            self._frame_queue = None
            self._speech_queue = None
            self._stream = None
            raise

        self._stream = stream
        self._running = True
        _logger.info(
            "mic capture started (device_index=%s, device_name=%s, sample_rate=%d, blocksize=%d)",
            self._device_index,
            resolved_name,
            self._sample_rate,
            self._blocksize,
        )

        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="toybox-mic-reader"
        )

    async def stop(self) -> None:
        """Close the stream, flush the VAD, and join the reader.

        Defensive: every step is wrapped so a failure in one stage
        still tears down the rest. The speech-queue sentinel is
        always pushed so a consumer's ``async for`` exits cleanly.
        """
        if not self._running:
            return
        self._running = False

        if self._stream is not None:
            stream = self._stream
            self._stream = None
            try:
                stream.stop()
            except Exception as exc:  # pragma: no cover -- defensive
                _logger.warning("error stopping mic stream: %s", exc)
            try:
                stream.close()
            except Exception as exc:  # pragma: no cover -- defensive
                _logger.warning("error closing mic stream: %s", exc)

        # Signal the reader to drain remaining frames and exit. We
        # enqueue a sentinel via call_soon_threadsafe to mirror how the
        # callback delivers frames; doing it directly is fine here too
        # because we're on the loop, but threadsafe is harmless.
        if self._frame_queue is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._enqueue_sentinel)

        if self._reader_task is not None:
            reader_task = self._reader_task
            self._reader_task = None
            try:
                await reader_task
            except asyncio.CancelledError:  # pragma: no cover -- defensive
                pass
            except Exception:
                _logger.exception(
                    "mic reader task raised during stop; iterator will still terminate"
                )

        # Always push the speech-iterator sentinel so ``__aiter__``
        # exits, even if the reader task crashed above.
        speech_queue = self._speech_queue
        if speech_queue is not None:
            try:
                speech_queue.put_nowait(None)
            except asyncio.QueueFull:
                # Drop one to make room for the sentinel; consumers
                # will see the EOF before the dropped chunk would have
                # mattered.
                with contextlib.suppress(asyncio.QueueEmpty):
                    speech_queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    speech_queue.put_nowait(None)

    async def __aenter__(self) -> MicCapture:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Async iteration (VAD-gated speech)
    # ------------------------------------------------------------------

    def __aiter__(self) -> AsyncIterator[NDArray[np.int16]]:
        if self._speech_queue is None:
            raise RuntimeError("MicCapture must be start()-ed before iteration")
        return self._iter_speech()

    async def _iter_speech(self) -> AsyncIterator[NDArray[np.int16]]:
        if self._speech_queue is None:  # pragma: no cover -- guarded above
            raise RuntimeError("MicCapture must be start()-ed before iteration")
        speech_queue = self._speech_queue
        while True:
            chunk = await speech_queue.get()
            if chunk is None:
                return
            yield chunk

    # ------------------------------------------------------------------
    # Sounddevice callback (runs on the PortAudio thread)
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        indata: NDArray[np.int16],
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """sounddevice callback. **Runs on the PortAudio worker thread.**

        Must be lightweight -- copy out the buffer (sounddevice reuses
        ``indata``) and bridge to asyncio. All real work happens on the
        loop side.
        """
        if status:
            # PortAudio status flags (input overflow, etc.) -- log but
            # keep going. Dropped samples on PortAudio's side are
            # already gone; we surface the event for diagnostics.
            _logger.warning("mic callback status: %s", status)
        try:
            mono = np.ascontiguousarray(indata[:, 0], dtype=np.int16).copy()
        except Exception as exc:  # pragma: no cover -- defensive
            _logger.warning("mic callback copy failed: %s", exc)
            return
        self._submit_frame_threadsafe(mono)

    def _submit_frame_threadsafe(self, frame: NDArray[np.int16]) -> None:
        loop = self._loop
        if loop is None or not self._running:
            # Stream is closing or not yet started; drop silently. The
            # fact that PortAudio handed us a buffer after stop()
            # returned (or before start() finished) is a timing
            # artifact, not a bug.
            return
        loop.call_soon_threadsafe(self._handle_frame, frame)

    def _handle_frame(self, frame: NDArray[np.int16]) -> None:
        """Loop-side hook: enqueue the frame, dropping oldest on overflow.

        Public-by-convention so tests can inject synthetic int16 buffers
        without going through sounddevice.
        """
        queue = self._frame_queue
        if queue is None:
            return
        try:
            queue.put_nowait(frame)
            return
        except asyncio.QueueFull:
            pass

        # Drop oldest, then enqueue. The ``mic queue overflow`` log
        # matches the observability contract called out in the plan
        # and issue #15.
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover -- race with reader
            pass
        self._overflow_count += 1
        _logger.warning(
            "mic queue overflow; dropped 1 (overflow_count=%d, capacity=%d)",
            self._overflow_count,
            self._queue_frames,
        )
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:  # pragma: no cover -- should be unreachable
            _logger.error("mic queue still full after drop-oldest; frame discarded")

    # ------------------------------------------------------------------
    # Reader loop
    # ------------------------------------------------------------------

    def _enqueue_sentinel(self) -> None:
        queue = self._frame_queue
        if queue is None:
            return
        # Sentinel is a zero-length int16 array -- never produced by the
        # mic so it's unambiguous.
        try:
            queue.put_nowait(np.empty(0, dtype=np.int16))
        except asyncio.QueueFull:
            # Drop one to make room; the reader will pick up the
            # sentinel on the next iteration.
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(np.empty(0, dtype=np.int16))

    async def _push_speech_chunk(self, chunk: NDArray[np.int16]) -> None:
        """Push a closed VAD segment to the speech queue, drop-OLDEST on overflow."""
        speech_queue = self._speech_queue
        if speech_queue is None:  # pragma: no cover -- defensive
            return
        try:
            speech_queue.put_nowait(chunk)
            return
        except asyncio.QueueFull:
            pass
        # Drop oldest non-sentinel chunk to make room.
        try:
            dropped = speech_queue.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover -- race
            dropped = None
        # If we just dropped the EOF sentinel, push it back at the
        # tail; otherwise it's a genuine speech chunk being discarded.
        if dropped is None:
            with contextlib.suppress(asyncio.QueueFull):
                speech_queue.put_nowait(None)
            return
        self._speech_overflow_count += 1
        _logger.warning(
            "speech queue overflow; dropped 1 (overflow_count=%d, capacity=%d)",
            self._speech_overflow_count,
            self._speech_queue_maxsize,
        )
        with contextlib.suppress(asyncio.QueueFull):
            speech_queue.put_nowait(chunk)

    async def _reader_loop(self) -> None:
        assert self._frame_queue is not None
        assert self._speech_queue is not None
        frame_queue = self._frame_queue
        try:
            while True:
                frame = await frame_queue.get()
                if frame.size == 0:
                    # Stop sentinel -- flush VAD then exit. Only reset
                    # the LSTM state if flush actually emitted a chunk
                    # (otherwise we'd double-count after a normal
                    # segment close immediately followed by stop()).
                    flushed = False
                    for chunk in self._vad.flush():
                        await self._push_speech_chunk(chunk)
                        flushed = True
                    if flushed:
                        self._reset_vad_state()
                    return
                self._ring.write(frame)
                segment_closed = False
                for chunk in self._vad.feed(frame):
                    await self._push_speech_chunk(chunk)
                    segment_closed = True
                if segment_closed:
                    self._reset_vad_state()
        except asyncio.CancelledError:  # pragma: no cover -- defensive
            raise
        except Exception:
            # Keep the daemon loud, not silent -- and self-heal so the
            # PortAudio callback doesn't spin overflow logs forever.
            _logger.exception("mic reader loop crashed; capture is shutting down")
            self._running = False
            # Stop/close the stream so the PortAudio worker thread also
            # winds down. sounddevice's stop()/close() are thread-safe.
            # Don't null _stream here -- stop() remains the canonical
            # idempotent cleanup path.
            stream = self._stream
            if stream is not None:
                try:
                    stream.stop()
                except Exception as exc:  # pragma: no cover -- defensive
                    _logger.warning(
                        "error stopping mic stream after reader crash: %s", exc
                    )
                try:
                    stream.close()
                except Exception as exc:  # pragma: no cover -- defensive
                    _logger.warning(
                        "error closing mic stream after reader crash: %s", exc
                    )
            speech_queue = self._speech_queue
            if speech_queue is not None:
                with contextlib.suppress(asyncio.QueueFull):
                    speech_queue.put_nowait(None)
            raise

    def _reset_vad_state(self) -> None:
        """Best-effort LSTM reset on the underlying predictor (if it has one)."""
        predictor = getattr(self._vad, "_predictor", None)
        reset = getattr(predictor, "reset_state", None)
        if callable(reset):
            try:
                reset()
            except Exception as exc:  # pragma: no cover -- defensive
                _logger.warning("vad predictor reset_state failed: %s", exc)


# ----------------------------------------------------------------------
# --test N operator script
# ----------------------------------------------------------------------


def _peak_dbfs(samples: NDArray[np.int16]) -> float:
    """Convert peak |sample| to dBFS. Returns -inf for pure silence."""
    if samples.size == 0:
        return float("-inf")
    peak = int(np.max(np.abs(samples.astype(np.int32))))
    if peak <= 0:
        return float("-inf")
    return 20.0 * math.log10(peak / 32768.0)


class _NullVadPredictor:
    """Predictor that always reports silence -- used by --test so the
    operator script doesn't require the real ONNX model. The script
    cares about peak level and overflow events, not VAD output."""

    def __call__(self, frame: NDArray[np.float32]) -> float:
        return 0.0


async def _run_test_capture(seconds: float) -> int:
    """Capture ``seconds`` of audio and print device + peak + overflow."""
    device_index = device_index_from_env()
    try:
        resolved_name = device_name(device_index)
    except Exception as exc:
        print(f"could not resolve mic device: {exc}", file=sys.stderr)
        return 1
    print(f"device_index: {device_index if device_index is not None else 'system default'}")
    print(f"device_name:  {resolved_name}")
    print(f"sample_rate:  {SAMPLE_RATE} Hz")
    print("channels:     1 (mono int16)")

    capture = MicCapture(
        vad=VadGate(predictor=_NullVadPredictor()),
        device_index=device_index,
    )
    try:
        await capture.start()
    except Exception as exc:
        print(f"could not start mic stream: {exc}", file=sys.stderr)
        return 1

    try:
        await asyncio.sleep(seconds)
    finally:
        await capture.stop()

    snap = capture.snapshot(seconds=seconds)
    peak = _peak_dbfs(snap)
    print(f"captured:     {snap.size / SAMPLE_RATE:.2f} s")
    if math.isinf(peak):
        print("peak_dbfs:    -inf (silence)")
    else:
        print(f"peak_dbfs:    {peak:.1f} dBFS")
    print(f"overflow:     {capture.overflow_count} event(s)")
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toybox.audio.capture",
        description="Mic capture diagnostic -- captures audio and prints level/overflow stats.",
    )
    parser.add_argument(
        "--test",
        type=float,
        metavar="SECONDS",
        help=(
            "Capture SECONDS of audio, then print the detected device name, "
            "peak dBFS, and any mic queue overflow events."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.test is None:
        parser.print_help()
        return 0
    if args.test <= 0:
        print("--test SECONDS must be > 0", file=sys.stderr)
        return 2
    print(f"TOYBOX_MIC_DEVICE_INDEX={os.environ.get(DEVICE_INDEX_ENV, '(unset)')}")
    return asyncio.run(_run_test_capture(args.test))


if __name__ == "__main__":  # pragma: no cover -- operator entry
    raise SystemExit(main())


__all__ = [
    "DEFAULT_BLOCKSIZE",
    "DEFAULT_QUEUE_FRAMES",
    "DEFAULT_RING_SECONDS",
    "DEFAULT_SPEECH_QUEUE_MAXSIZE",
    "MicCapture",
    "RING_SECONDS_ENV",
    "SPEECH_QUEUE_MAXSIZE_ENV",
    "main",
]
