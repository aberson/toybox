"""Thread-safe rolling ring buffer of int16 PCM samples.

The mic capture callback runs on a non-asyncio thread (sounddevice's
PortAudio worker). Consumers reading the rolling buffer for STT context
live on the asyncio side. ``RingBuffer`` is a small, lock-protected
fixed-size array that supports both producers without depending on
asyncio primitives — the lock is a plain ``threading.Lock`` because
both sides may touch it from different threads.

Capacity is expressed in *samples* (not seconds) to keep the type
flat; the constructor takes a sample rate + duration helper for
convenience and the capture daemon configures it via
``TOYBOX_AUDIO_RING_SECONDS``.
"""

from __future__ import annotations

import threading

import numpy as np
from numpy.typing import NDArray


class RingBuffer:
    """Fixed-size int16 ring buffer with thread-safe writes and reads.

    The buffer holds the most recent ``capacity_samples`` samples. When
    a write would exceed capacity, the oldest samples are overwritten
    (FIFO eviction) — there is no "full" error path because mic capture
    must never block.

    Reads return a *copy* so callers can release the lock immediately
    and operate on a stable snapshot.
    """

    def __init__(self, capacity_samples: int, sample_rate: int) -> None:
        if capacity_samples <= 0:
            raise ValueError(f"capacity_samples must be > 0, got {capacity_samples}")
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be > 0, got {sample_rate}")
        self._capacity = capacity_samples
        self._sample_rate = sample_rate
        self._buffer: NDArray[np.int16] = np.zeros(capacity_samples, dtype=np.int16)
        # ``_write`` is the next slot to write; ``_filled`` saturates at
        # ``_capacity`` so partial reads (before first wrap) work.
        self._write = 0
        self._filled = 0
        self._lock = threading.Lock()

    @classmethod
    def from_seconds(cls, seconds: float, sample_rate: int) -> RingBuffer:
        """Construct sized for ``seconds`` at the given sample rate."""
        if seconds <= 0:
            raise ValueError(f"seconds must be > 0, got {seconds}")
        return cls(capacity_samples=int(round(seconds * sample_rate)), sample_rate=sample_rate)

    @property
    def capacity_samples(self) -> int:
        return self._capacity

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def filled_samples(self) -> int:
        """Currently-stored sample count (saturates at ``capacity_samples``)."""
        with self._lock:
            return self._filled

    def write(self, samples: NDArray[np.int16]) -> None:
        """Append ``samples`` (int16, mono) to the ring, overwriting the oldest.

        If ``samples`` is longer than the ring capacity, only the tail
        of length ``capacity_samples`` is retained — the older portion
        of ``samples`` would have been overwritten on the next wrap
        anyway, so we skip it eagerly.
        """
        if samples.dtype != np.int16:
            raise TypeError(f"RingBuffer expects int16 samples, got {samples.dtype}")
        if samples.ndim != 1:
            raise ValueError(f"RingBuffer expects 1-D mono samples, got shape {samples.shape}")

        with self._lock:
            n = samples.shape[0]
            if n == 0:
                return
            if n >= self._capacity:
                # Only the last ``capacity`` samples can survive.
                self._buffer[:] = samples[-self._capacity :]
                self._write = 0
                self._filled = self._capacity
                return

            end = self._write + n
            if end <= self._capacity:
                self._buffer[self._write : end] = samples
            else:
                first = self._capacity - self._write
                self._buffer[self._write :] = samples[:first]
                self._buffer[: n - first] = samples[first:]
            self._write = end % self._capacity
            self._filled = min(self._capacity, self._filled + n)

    def snapshot(self, seconds: float | None = None) -> NDArray[np.int16]:
        """Return a copy of the most recent samples (oldest → newest).

        Args:
            seconds: How much history to return. ``None`` returns
                everything currently buffered. Values larger than the
                buffered amount are clamped (you get whatever exists).
        """
        if seconds is not None and seconds < 0:
            raise ValueError(f"seconds must be >= 0, got {seconds}")
        with self._lock:
            if self._filled == 0:
                return np.empty(0, dtype=np.int16)
            if seconds is None:
                wanted = self._filled
            else:
                wanted = min(self._filled, int(round(seconds * self._sample_rate)))
            if wanted == 0:
                return np.empty(0, dtype=np.int16)

            # Logical layout: oldest sample sits at ``_write`` when full,
            # at index 0 otherwise.
            if self._filled < self._capacity:
                # Linear: samples occupy [0 .. _filled).
                start = self._filled - wanted
                return self._buffer[start : self._filled].copy()

            # Wrapped: oldest is at _write, newest is at (_write - 1) % cap.
            # Take the *last* ``wanted`` samples in time order.
            newest_end = self._write  # exclusive in logical "end" sense
            start = (newest_end - wanted) % self._capacity
            if start < newest_end:
                return self._buffer[start:newest_end].copy()
            # Wrap straddle.
            return np.concatenate((self._buffer[start:], self._buffer[:newest_end]))


__all__ = ["RingBuffer"]
