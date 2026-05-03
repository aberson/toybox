"""Test-only WAV-to-buffer adapter for the smoke E2E.

Replaces the live PortAudio :class:`sounddevice.InputStream` in the
``--smoke`` codepath with a deterministic WAV reader that satisfies the
:data:`toybox.audio.capture.StreamFactory` contract on
:class:`toybox.audio.capture.MicCapture`. The adapter loops the WAV
forever (so the smoke can keep running while the test waits for the
suggestion to land downstream) and feeds frames at real-time pacing so
silero-vad's stateful frame-level decision behaves the same as it would
against a real mic.

This module is **test-only**: it is intentionally NOT exported from
:mod:`toybox.audio`. Production code constructs :class:`MicCapture`
without a ``stream_factory``, which falls back to the real
:class:`sounddevice.InputStream`.
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from .vad import SAMPLE_RATE

_logger = logging.getLogger(__name__)

# Pacing helper: when a callback runs we sleep this fraction of the
# remaining real-time budget. Keeping it just under 1.0 lets PortAudio's
# upstream consumer drain without us drifting forever.
_PACING_SLEEP_SLACK: Final[float] = 0.99


PortAudioCallback = Callable[[NDArray[np.int16], int, Any, Any], None]


def _read_wav_int16_mono(path: Path) -> tuple[NDArray[np.int16], int]:
    """Read ``path`` as int16 mono PCM. Returns ``(samples, sample_rate)``.

    Errors loudly when the WAV is not 16-bit PCM or the channel count is
    above 2 — the smoke fixture must be the canonical
    16 kHz / mono / int16 shape and a quiet fallback would just hide a
    broken fixture from the test author.
    """
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        sampwidth = wav.getsampwidth()
        n_channels = wav.getnchannels()
        n_frames = wav.getnframes()
        raw = wav.readframes(n_frames)

    if sampwidth != 2:
        raise ValueError(
            f"WavToBufferStream expects 16-bit PCM; got sampwidth={sampwidth} bytes ({path})"
        )
    if n_channels not in (1, 2):
        raise ValueError(
            f"WavToBufferStream expects mono or stereo; got channels={n_channels} ({path})"
        )

    samples = np.frombuffer(raw, dtype=np.int16)
    if n_channels == 2:
        # Down-mix stereo to mono by averaging in int32 to avoid wrap.
        stereo = samples.reshape(-1, 2).astype(np.int32)
        samples = ((stereo[:, 0] + stereo[:, 1]) // 2).astype(np.int16)
    return samples, sample_rate


class WavToBufferStream:
    """Sounddevice-shape stub that pumps a WAV file into the capture callback.

    Construct via :meth:`factory_for` so :class:`MicCapture` can accept
    it as a ``stream_factory``. The factory closes over the WAV path; the
    instance does the real work.

    Pacing
    ------
    A real :class:`sounddevice.InputStream` delivers ``blocksize`` frames
    every ``blocksize / sample_rate`` seconds. We mirror that here so
    silero-vad sees the same temporal envelope it would against the live
    mic — running faster than realtime would batch frames inside a
    single VAD window and break the speech-segment decision boundary.

    Looping
    -------
    The WAV is looped indefinitely. The smoke harness waits for a
    transcript-driven suggestion to fire, then tears the backend down;
    it does not depend on EOF. Looping also means the capture daemon
    behaves like a continuously-on mic, matching the production lifecycle.
    """

    def __init__(
        self,
        *,
        wav_path: Path,
        blocksize: int,
        sample_rate: int,
        channels: int,
        dtype: str,
        callback: PortAudioCallback,
    ) -> None:
        if channels != 1:
            raise ValueError(f"WavToBufferStream only supports mono; got channels={channels}")
        if dtype != "int16":
            raise ValueError(f"WavToBufferStream only supports int16; got dtype={dtype!r}")
        if blocksize <= 0:
            raise ValueError(f"blocksize must be > 0, got {blocksize}")
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be > 0, got {sample_rate}")

        samples, file_rate = _read_wav_int16_mono(wav_path)
        if file_rate != sample_rate:
            raise ValueError(
                f"WavToBufferStream WAV rate {file_rate} Hz does not match "
                f"requested sample_rate {sample_rate} Hz ({wav_path})"
            )

        self._wav_path = wav_path
        self._samples = samples
        self._blocksize = blocksize
        self._sample_rate = sample_rate
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False
        self._closed = False

    @classmethod
    def factory_for(cls, wav_path: Path) -> Callable[..., WavToBufferStream]:
        """Return a ``stream_factory`` callable bound to ``wav_path``.

        :class:`MicCapture` invokes the factory with PortAudio-shape
        keyword args (``device``, ``samplerate``, ``channels``, ``dtype``,
        ``blocksize``, ``callback``); we ignore ``device`` and forward
        the rest.
        """
        resolved = wav_path

        def _factory(
            *,
            device: int | None = None,  # noqa: ARG001 -- unused; honors PortAudio shape
            samplerate: int = SAMPLE_RATE,
            channels: int = 1,
            dtype: str = "int16",
            blocksize: int = 512,
            callback: PortAudioCallback,
        ) -> WavToBufferStream:
            return cls(
                wav_path=resolved,
                blocksize=blocksize,
                sample_rate=samplerate,
                channels=channels,
                dtype=dtype,
                callback=callback,
            )

        return _factory

    # ------------------------------------------------------------------
    # sounddevice.InputStream contract: start / stop / close
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the pump thread. Idempotent: a second call is a no-op."""
        if self._started:
            return
        if self._closed:
            raise RuntimeError("WavToBufferStream is closed")
        self._started = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._pump_loop,
            name="toybox-wav-pump",
            daemon=True,
        )
        self._thread.start()
        _logger.info(
            "wav-to-buffer stream started (path=%s, blocksize=%d, sample_rate=%d)",
            self._wav_path,
            self._blocksize,
            self._sample_rate,
        )

    def stop(self) -> None:
        """Signal the pump thread to exit and join it. Idempotent."""
        if not self._started:
            return
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        self._started = False

    def close(self) -> None:
        """Release the stream. Safe to call multiple times."""
        self.stop()
        self._closed = True

    # ------------------------------------------------------------------
    # Pump loop (runs on a worker thread, mirrors PortAudio semantics)
    # ------------------------------------------------------------------

    def _pump_loop(self) -> None:
        """Feed ``blocksize``-shaped int16 buffers to the callback at sample-rate cadence.

        The loop wraps the WAV indefinitely so the capture daemon behaves
        like an always-on mic. ``indata`` is shaped ``(blocksize, 1)`` to
        match :func:`sounddevice.InputStream`'s int16 mono buffer.
        """
        block_period = self._blocksize / float(self._sample_rate)
        n_total = self._samples.size
        cursor = 0
        next_deadline = time.monotonic() + block_period
        while not self._stop_event.is_set():
            # Pull the next blocksize samples, wrapping the WAV as needed.
            end = cursor + self._blocksize
            if end <= n_total:
                buf = self._samples[cursor:end]
                cursor = end
            else:
                first = self._samples[cursor:]
                wrap_remaining = self._blocksize - first.size
                second = self._samples[:wrap_remaining]
                buf = np.concatenate((first, second))
                cursor = wrap_remaining
            if cursor >= n_total:
                cursor = 0

            indata = np.ascontiguousarray(buf.reshape(-1, 1), dtype=np.int16)
            try:
                self._callback(indata, self._blocksize, None, None)
            except Exception:  # pragma: no cover -- defensive
                _logger.exception("wav-to-buffer callback raised; pump continues")

            # Real-time pacing: sleep until the next deadline. If we've
            # fallen behind (slow consumer), don't sleep — just keep up.
            now = time.monotonic()
            sleep_for = (next_deadline - now) * _PACING_SLEEP_SLACK
            if sleep_for > 0.0:
                self._stop_event.wait(timeout=sleep_for)
            # Clamp the next deadline to ``now`` so a long stall doesn't
            # accrue arrears that the loop tries to repay as a catch-up
            # burst (which would feed many blocks back-to-back without
            # the natural sample-rate cadence VAD relies on).
            next_deadline = max(next_deadline + block_period, time.monotonic())


__all__ = ["WavToBufferStream"]
