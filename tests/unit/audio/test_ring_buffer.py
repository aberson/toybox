"""Unit tests for the int16 ring buffer."""

from __future__ import annotations

import threading

import numpy as np
import pytest

from toybox.audio.ring_buffer import RingBuffer


def _ramp(n: int) -> np.ndarray:
    """Distinct int16 ramp so rotation bugs are obvious in failures."""
    # Use values that fit in int16 (max ±32767).
    return np.arange(1, n + 1, dtype=np.int32).astype(np.int16)


def test_from_seconds_helper_rounds_to_nearest_sample() -> None:
    rb = RingBuffer.from_seconds(seconds=0.5, sample_rate=16000)
    assert rb.capacity_samples == 8000


def test_constructor_rejects_bad_args() -> None:
    with pytest.raises(ValueError):
        RingBuffer(capacity_samples=0, sample_rate=16000)
    with pytest.raises(ValueError):
        RingBuffer(capacity_samples=10, sample_rate=0)
    with pytest.raises(ValueError):
        RingBuffer.from_seconds(seconds=0, sample_rate=16000)


def test_write_rejects_wrong_dtype() -> None:
    rb = RingBuffer(capacity_samples=8, sample_rate=16000)
    with pytest.raises(TypeError):
        rb.write(np.array([1.0, 2.0], dtype=np.float32))


def test_write_rejects_non_mono() -> None:
    rb = RingBuffer(capacity_samples=8, sample_rate=16000)
    with pytest.raises(ValueError):
        rb.write(np.zeros((4, 2), dtype=np.int16))


def test_partial_fill_snapshot_returns_what_was_written() -> None:
    rb = RingBuffer(capacity_samples=10, sample_rate=16000)
    # Fresh buffer reflects construction args.
    assert rb.capacity_samples == 10
    assert rb.sample_rate == 16000
    assert rb.filled_samples == 0
    rb.write(_ramp(4))
    snap = rb.snapshot()
    assert snap.tolist() == [1, 2, 3, 4]
    assert rb.filled_samples == 4


def test_snapshot_seconds_clamps_to_filled_amount() -> None:
    rb = RingBuffer(capacity_samples=1000, sample_rate=100)
    rb.write(_ramp(50))
    # Asked for 1 second (100 samples) but only 50 are buffered.
    snap = rb.snapshot(seconds=1.0)
    assert snap.size == 50
    assert snap.tolist() == list(range(1, 51))


def test_snapshot_seconds_returns_only_recent_window() -> None:
    rb = RingBuffer(capacity_samples=1000, sample_rate=100)
    rb.write(_ramp(80))
    # 0.2 s @ 100 Hz = 20 samples. Last 20 of [1..80] is [61..80].
    snap = rb.snapshot(seconds=0.2)
    assert snap.tolist() == list(range(61, 81))


def test_write_past_capacity_drops_oldest() -> None:
    rb = RingBuffer(capacity_samples=10, sample_rate=16000)
    rb.write(_ramp(7))  # [1..7]
    rb.write(_ramp(7) + 100)  # [101..107] — total 14, cap 10 → keep last 10
    snap = rb.snapshot()
    # Logical order = [4, 5, 6, 7, 101, 102, 103, 104, 105, 106, 107] trimmed to 10
    expected = [5, 6, 7, 101, 102, 103, 104, 105, 106, 107]
    assert snap.tolist() == expected
    assert rb.filled_samples == 10


def test_write_larger_than_capacity_keeps_only_tail() -> None:
    rb = RingBuffer(capacity_samples=5, sample_rate=16000)
    rb.write(_ramp(20))  # [1..20] — only last 5 survive
    snap = rb.snapshot()
    assert snap.tolist() == [16, 17, 18, 19, 20]
    assert rb.filled_samples == 5


def test_repeated_writes_after_wrap_preserve_order() -> None:
    rb = RingBuffer(capacity_samples=6, sample_rate=16000)
    for chunk in (
        np.array([1, 2, 3], dtype=np.int16),
        np.array([4, 5, 6], dtype=np.int16),
        np.array([7, 8], dtype=np.int16),
        np.array([9, 10, 11, 12], dtype=np.int16),
    ):
        rb.write(chunk)
    # Last 6 samples written = [7, 8, 9, 10, 11, 12]
    assert rb.snapshot().tolist() == [7, 8, 9, 10, 11, 12]


def test_snapshot_zero_seconds_returns_empty() -> None:
    rb = RingBuffer(capacity_samples=10, sample_rate=16000)
    rb.write(_ramp(5))
    snap = rb.snapshot(seconds=0.0)
    assert snap.size == 0


def test_snapshot_rejects_negative_seconds() -> None:
    rb = RingBuffer(capacity_samples=10, sample_rate=16000)
    with pytest.raises(ValueError):
        rb.snapshot(seconds=-0.1)


def test_snapshot_returns_a_copy_not_a_view() -> None:
    rb = RingBuffer(capacity_samples=10, sample_rate=16000)
    rb.write(_ramp(5))
    snap = rb.snapshot()
    snap[0] = 999
    again = rb.snapshot()
    assert again[0] == 1, "snapshot must not alias the internal buffer"


def test_snapshot_huge_seconds_after_wrap_clamps_to_capacity() -> None:
    """``snapshot(seconds=very_large)`` after wrap returns exactly capacity samples."""
    rb = RingBuffer(capacity_samples=10, sample_rate=100)
    # Write past capacity to force wrap.
    rb.write(_ramp(25))  # only the last 10 [16..25] survive
    # Ask for 10x the buffer's worth of audio in seconds.
    snap = rb.snapshot(seconds=10.0 * (rb.capacity_samples / rb.sample_rate))
    assert snap.size == rb.capacity_samples
    assert snap.tolist() == list(range(16, 26))


def test_concurrent_writes_do_not_corrupt_buffer() -> None:
    """Smoke test: many threads writing 1 sample each. All should land."""
    rb = RingBuffer(capacity_samples=2048, sample_rate=16000)

    def writer(value: int) -> None:
        rb.write(np.array([value], dtype=np.int16))

    threads = [threading.Thread(target=writer, args=(i % 1000 + 1,)) for i in range(2000)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = rb.snapshot()
    # Buffer wraps once (2000 writes into a 2048-sample ring); we just
    # care that exactly 2000 valid samples are stored without segfaults
    # or data races crashing.
    assert snap.size == 2000
    assert snap.dtype == np.int16
