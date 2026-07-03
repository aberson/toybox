"""Kokoro-82M neural TTS engine (Phase Z Z3).

Public entry: :func:`synthesize` — ``(text, voice) -> WAV bytes``
(24 kHz mono, 16-bit PCM). The heavy deps (``kokoro_onnx``,
``soundfile``, ``onnxruntime``) live behind lazy imports inside
:func:`_build_engine` / the real synthesis path, mirroring
:mod:`toybox.image_gen.pipeline`'s contract: importing this module is
cheap and works WITHOUT the ``tts`` optional extra installed. The
``tests/unit/tts/test_lazy_imports.py`` test pins this.

Stub-runtime injection (mirrors ``TOYBOX_IMAGE_GEN_STUB``):

* ``TOYBOX_TTS_STUB=1`` short-circuits the real engine and returns a
  tiny deterministic — but structurally valid — WAV built with the
  stdlib ``wave`` module. No optional deps needed; this is the path
  CI and the Z4 worker / enqueue tests use. In stub mode
  :func:`is_tts_capable` returns ``True``.

Provider seam (the "GPU flip is a config change, not a refactor"
requirement): the ONNX session is constructed HERE, by us, with the
providers listed in :data:`ONNX_PROVIDERS`, and handed to
``kokoro_onnx.Kokoro.from_session``. Flipping to GPU later means
changing that provider tuple (e.g. prepending
``"CUDAExecutionProvider"`` and installing ``onnxruntime-gpu``) —
no call-site changes anywhere else.

Model files live under ``<data_root>/models/tts/`` where
``<data_root>`` is ``TOYBOX_DATA_DIR`` (default ``data/``) — the same
data-root convention as :mod:`toybox.image_gen.__main__` and the
corpus modules. Fetch them with ``python -m toybox.tts --download``
(see :mod:`toybox.tts.__main__`).

A module-level cached engine keeps subsequent calls fast (the Z4
worker keeps the process alive so the cache stays warm across clips).
Cache survives env changes only via process restart — same caveat as
the image-gen pipeline cache.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import struct
import wave
from importlib import util as importlib_util
from pathlib import Path
from typing import Any, Final

_logger = logging.getLogger(__name__)

# Env knobs.
STUB_ENV: Final[str] = "TOYBOX_TTS_STUB"
DATA_DIR_ENV: Final[str] = "TOYBOX_DATA_DIR"

# Fallback Kokoro voice for personas without a ``neural_voice`` in
# their voice_profile (custom personas, future library additions).
# ``af_heart`` is the top-graded en-US voice in the published Kokoro
# voice grades. Consumers import THIS constant — do not restate the
# string elsewhere (code-quality.md §2, one source of truth).
DEFAULT_NEURAL_VOICE: Final[str] = "af_heart"

# Kokoro-82M emits 24 kHz mono; the WAV contract for every clip
# (stub and real) is 24 kHz mono 16-bit PCM.
SAMPLE_RATE: Final[int] = 24000

# On-disk model layout under ``<data_root>/models/tts/``. Filenames
# match the kokoro-onnx project's released assets (see __main__.py
# for the download URLs).
MODELS_SUBDIR: Final[Path] = Path("models") / "tts"
MODEL_FILENAME: Final[str] = "kokoro-v1.0.onnx"
VOICES_FILENAME: Final[str] = "voices-v1.0.bin"

# ONNX execution providers, in preference order. THE provider seam:
# a later GPU flip is "prepend CUDAExecutionProvider + install
# onnxruntime-gpu", nothing else changes. Phase Z deliberately stays
# CPU-only (8 GB VRAM budget is reserved for SD 1.5 — plan §6).
ONNX_PROVIDERS: Final[tuple[str, ...]] = ("CPUExecutionProvider",)

# Kokoro language tag for every clip; all library personas are en.
_KOKORO_LANG: Final[str] = "en-us"

# Module-level cached engine. ``None`` until the first real
# ``synthesize`` call; subsequent calls reuse. Typed ``Any`` because
# the kokoro_onnx types live behind the lazy import.
_cached_engine: Any = None


def _data_root() -> Path:
    """Resolve the data-tree root (``TOYBOX_DATA_DIR``, default ``data``)."""
    raw = os.environ.get(DATA_DIR_ENV)
    return Path(raw) if raw else Path("data")


def model_dir() -> Path:
    """Directory holding the Kokoro ONNX model + voices bin."""
    return _data_root() / MODELS_SUBDIR


def model_path() -> Path:
    """Full path of the Kokoro ONNX model file."""
    return model_dir() / MODEL_FILENAME


def voices_path() -> Path:
    """Full path of the Kokoro voices bin."""
    return model_dir() / VOICES_FILENAME


def _stub_active() -> bool:
    """Return True iff ``TOYBOX_TTS_STUB`` is set to a truthy value.

    Same truthy vocabulary as ``TOYBOX_IMAGE_GEN_STUB``.
    """
    raw = os.environ.get(STUB_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _deps_importable() -> bool:
    """Return True iff the ``tts`` extra's deps are importable.

    ``find_spec`` only inspects import metadata — it does NOT execute
    the heavy modules, so this probe stays cheap.
    """
    try:
        return (
            importlib_util.find_spec("kokoro_onnx") is not None
            and importlib_util.find_spec("soundfile") is not None
        )
    except (ImportError, ValueError):  # pragma: no cover — defensive
        return False


def is_tts_capable() -> bool:
    """Capability probe: can :func:`synthesize` produce audio here?

    * Stub mode (``TOYBOX_TTS_STUB=1``) → always ``True`` (the stub
      needs no deps and no model files).
    * Otherwise: the ``tts`` extra's deps must be importable AND both
      model files must be present under ``<data_root>/models/tts/``.

    Mirrors :func:`toybox.image_gen.capability.is_image_gen_capable`'s
    "deps + checkpoints on disk" shape, minus the GPU branches — the
    engine is CPU-only by design.
    """
    if _stub_active():
        return True
    if not _deps_importable():
        return False
    return model_path().is_file() and voices_path().is_file()


def _build_engine() -> Any:
    """Construct the Kokoro engine over OUR onnxruntime session.

    Lazy heavy imports live here. Building the session ourselves (vs
    letting ``Kokoro(...)`` build its own) is what makes
    :data:`ONNX_PROVIDERS` the single provider seam.
    """
    import onnxruntime as ort  # noqa: PLC0415 — lazy by contract
    from kokoro_onnx import Kokoro  # noqa: PLC0415 — lazy by contract

    session = ort.InferenceSession(
        str(model_path()),
        providers=list(ONNX_PROVIDERS),
    )
    engine = Kokoro.from_session(session, str(voices_path()))
    _logger.info(
        "kokoro tts engine loaded (model=%s, providers=%s)",
        model_path(),
        list(ONNX_PROVIDERS),
    )
    return engine


def _get_engine() -> Any:
    """Return the process-wide engine, lazily constructed on first call."""
    global _cached_engine
    if _cached_engine is None:
        _cached_engine = _build_engine()
    return _cached_engine


def reset_engine_cache_for_tests() -> None:
    """Drop the cached engine.

    Used by the autouse fixture in ``tests/unit/tts/conftest.py`` (and
    available to Z4 worker test fixtures) so no test observes an engine
    another test constructed.
    """
    global _cached_engine
    _cached_engine = None


def _stub_wav_bytes(text: str, voice: str) -> bytes:
    """Deterministic tiny valid WAV (24 kHz mono 16-bit), no deps.

    The tone frequency is derived from a sha256 of ``(voice, text)``
    so distinct inputs produce distinct bytes (useful for the Z4
    cache tests) while repeat calls are byte-identical. ~0.1 s of
    audio ≈ 4.8 KB — big enough to parse, small enough for CI.
    """
    digest = hashlib.sha256(f"{voice}:{text}".encode()).digest()
    # 220–440 Hz — audible if a stub clip ever leaks to a speaker.
    freq = 220.0 + (digest[0] / 255.0) * 220.0
    n_frames = SAMPLE_RATE // 10
    amplitude = 8000
    frames = bytearray()
    for i in range(n_frames):
        sample = int(amplitude * math.sin(2.0 * math.pi * freq * i / SAMPLE_RATE))
        frames += struct.pack("<h", sample)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(frames))
    return buf.getvalue()


def synthesize(text: str, voice: str) -> bytes:
    """Render ``text`` in Kokoro voice ``voice`` to WAV bytes.

    Returns a complete WAV container (24 kHz mono 16-bit PCM). Raises
    :class:`ValueError` on empty/whitespace ``text`` or ``voice`` —
    callers (the Z4 worker) must not enqueue blanks.

    In stub mode (``TOYBOX_TTS_STUB=1``) a deterministic tiny WAV is
    returned without touching the optional deps; a loud WARNING is
    logged on every stub call so an accidental production stub is
    visible in log scrapers (same rationale as the image-gen stub).

    Exception contract: only :class:`ValueError` (empty inputs) is
    raised deliberately. Everything else propagates RAW — an
    ``ImportError`` from a broken/absent ``tts`` extra install,
    onnxruntime session/model-load errors, kokoro unknown-voice
    errors. Callers are expected to gate on :func:`is_tts_capable`
    first and catch-and-degrade around the call (the Z4 worker's
    contract: a failed clip just leaves the Web Speech fallback in
    place — plan §5 "no breaker needed").
    """
    if not text.strip():
        raise ValueError("synthesize requires non-empty text")
    if not voice.strip():
        raise ValueError("synthesize requires a non-empty voice id")

    if _stub_active():
        _logger.warning(
            "tts running in STUB mode (TOYBOX_TTS_STUB=1) — output is a "
            "deterministic tone, not real speech"
        )
        return _stub_wav_bytes(text, voice)

    import soundfile as sf  # noqa: PLC0415 — lazy by contract

    engine = _get_engine()
    samples, sample_rate = engine.create(text, voice=voice, speed=1.0, lang=_KOKORO_LANG)
    if int(sample_rate) != SAMPLE_RATE:  # pragma: no cover — engine contract drift
        _logger.warning(
            "kokoro returned sample_rate=%s (expected %d); writing actual rate",
            sample_rate,
            SAMPLE_RATE,
        )
    buf = io.BytesIO()
    sf.write(buf, samples, int(sample_rate), format="WAV", subtype="PCM_16")
    return buf.getvalue()


__all__ = [
    "DATA_DIR_ENV",
    "DEFAULT_NEURAL_VOICE",
    "MODEL_FILENAME",
    "MODELS_SUBDIR",
    "ONNX_PROVIDERS",
    "SAMPLE_RATE",
    "STUB_ENV",
    "VOICES_FILENAME",
    "is_tts_capable",
    "model_dir",
    "model_path",
    "reset_engine_cache_for_tests",
    "synthesize",
    "voices_path",
]
