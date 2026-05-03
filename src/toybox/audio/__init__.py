"""Audio capture, ring buffer, and VAD gate for Phase B (Step 11).

This package wraps the ``sounddevice`` mic stream, a thread-safe rolling
ring buffer for downstream STT context, and a silero-vad gate that
forwards only speech chunks to the consumer side. Public surface:

* :class:`MicCapture` — start/stop the mic, async-iterate VAD-gated
  speech chunks, snapshot the rolling buffer.
* :class:`RingBuffer` — thread-safe int16 ring; capture writes from the
  sounddevice callback thread, consumers read from the asyncio side.
* :class:`VadGate` — silero-vad gating with an injectable predictor so
  tests never need the real ONNX model.
* :class:`SileroVadPredictor` — production predictor that lazy-loads
  ``data/models/silero_vad.onnx`` on first call.
* :func:`resolve_device` — honors ``TOYBOX_MIC_DEVICE_INDEX``.
"""

from __future__ import annotations

from .capture import MicCapture
from .devices import resolve_device
from .ring_buffer import RingBuffer
from .vad import SileroVadPredictor, VadGate

__all__ = [
    "MicCapture",
    "RingBuffer",
    "SileroVadPredictor",
    "VadGate",
    "resolve_device",
]
