"""Phase Z neural TTS subsystem (Kokoro-82M, CPU in-process).

Public surface — re-exports from :mod:`.engine`. Importing this
package is intentionally cheap; the heavy deps (``kokoro_onnx``,
``soundfile``, ``onnxruntime``) live behind lazy imports inside the
engine's real-synthesis path, so the package imports fine WITHOUT the
``tts`` optional extra installed (pinned by
``tests/unit/tts/test_lazy_imports.py``).

The package is structured so:

* Z3 ships the engine + download CLI + voice-id schema — no
  production wiring.
* Z4 lands the clip cache + background worker + enqueue hooks
  (consuming :func:`synthesize` / :func:`is_tts_capable`).
* Z5 wires kiosk clip playback against the Z4 wire shape.
"""

from __future__ import annotations

from .engine import DEFAULT_NEURAL_VOICE, is_tts_capable, synthesize

__all__ = [
    "DEFAULT_NEURAL_VOICE",
    "is_tts_capable",
    "synthesize",
]
