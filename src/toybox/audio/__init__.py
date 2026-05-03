"""Audio capture, ring buffer, VAD gate, and STT for Phase B.

This package wraps the ``sounddevice`` mic stream, a thread-safe rolling
ring buffer for downstream STT context, a silero-vad gate that forwards
only speech chunks to the consumer side, and the faster-whisper STT
wrapper that turns those speech chunks into :class:`Transcript` records.
Public surface:

* :class:`MicCapture` — start/stop the mic, async-iterate VAD-gated
  speech chunks, snapshot the rolling buffer.
* :class:`RingBuffer` — thread-safe int16 ring; capture writes from the
  sounddevice callback thread, consumers read from the asyncio side.
* :class:`VadGate` — silero-vad gating with an injectable predictor so
  tests never need the real ONNX model.
* :class:`SileroVadPredictor` — production predictor that lazy-loads
  ``data/models/silero_vad.onnx`` on first call.
* :class:`Transcript` / :class:`WhisperTranscriber` — Pydantic STT
  result + async wrapper around faster-whisper, with GPU autodetect and
  CPU fallback.
* :func:`resolve_device` — honors ``TOYBOX_MIC_DEVICE_INDEX``.
"""

from __future__ import annotations

from .capture import MicCapture
from .devices import resolve_device
from .ring_buffer import RingBuffer
from .stt import DEFAULT_MODEL, WHISPER_MODEL_ENV, Transcript, WhisperTranscriber
from .vad import SileroVadPredictor, VadGate

__all__ = [
    "DEFAULT_MODEL",
    "MicCapture",
    "RingBuffer",
    "SileroVadPredictor",
    "Transcript",
    "VadGate",
    "WHISPER_MODEL_ENV",
    "WhisperTranscriber",
    "resolve_device",
]
