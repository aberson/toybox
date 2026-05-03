"""faster-whisper STT for VAD-gated speech chunks.

This module wraps :class:`faster_whisper.WhisperModel` for use inside the
asyncio mic pipeline. The contract:

1. :class:`WhisperTranscriber` is constructed once at startup. The
   underlying ``WhisperModel`` is **lazy-loaded** on the first
   ``transcribe()`` call so import-time is cheap and the operator
   ``--download`` entrypoint can resolve the model path without paying
   for inference initialization.
2. GPU is preferred: the constructor first attempts ``device="cuda"``;
   any init failure (no CUDA toolkit, no cuDNN, no GPU, etc.) falls back
   to ``device="cpu"`` with a warning. Callers can pin the device via the
   ``device=`` keyword for deterministic tests.
3. :meth:`WhisperTranscriber.transcribe` runs the underlying
   ``model.transcribe()`` via :func:`asyncio.to_thread` so the mic loop's
   event loop is never blocked by ASR work.
4. The model factory is injectable so tests can substitute a fake that
   yields scripted segments without downloading the real ~500 MB model.

The ``--download`` operator entrypoint pre-fetches the model into the
on-disk cache at ``data/models/`` and exits without running any
transcription. First-run cost is paid up-front so the first real session
doesn't stall on a download.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

_logger = logging.getLogger(__name__)

WHISPER_MODEL_ENV: Final[str] = "TOYBOX_WHISPER_MODEL"
DEFAULT_MODEL: Final[str] = "small"

# Cache layout: faster-whisper's ``download_root`` points here. The
# directory is gitignored (see ``.gitignore``); we ship a ``.gitkeep``
# so the path exists at install time.
DEFAULT_CACHE_DIR: Final[Path] = Path("data") / "models"

# Sample rate the audio capture pipeline emits. faster-whisper's
# ``transcribe()`` infers sample rate from the array shape, but we
# document the assumption here for the duration calculation.
EXPECTED_SAMPLE_RATE: Final[int] = 16000
assert EXPECTED_SAMPLE_RATE == 16000  # locks the contract; faster-whisper assumes 16 kHz mono.

# Sentinel used when faster-whisper cannot detect a language (or the
# stub returns a falsy value). Surfaced in :class:`Transcript.language`
# instead of an empty string so downstream gates can branch on it.
UNKNOWN_LANGUAGE: Final[str] = "unknown"

# Type alias for an injectable WhisperModel factory. Tests pass a stub so
# we never need to download the real model. Mirrors the ``stream_factory``
# pattern in ``capture.py``.
ModelFactory = Callable[..., "WhisperModel"]


def _whisper_model_from_env() -> str:
    """Return the configured whisper model size, with a fallback warning."""
    raw = os.environ.get(WHISPER_MODEL_ENV)
    if raw is None:
        return DEFAULT_MODEL
    stripped = raw.strip()
    if not stripped:
        _logger.warning(
            "%s=%r is empty; falling back to %r",
            WHISPER_MODEL_ENV,
            raw,
            DEFAULT_MODEL,
        )
        return DEFAULT_MODEL
    return stripped


def _logprob_to_confidence(mean_logprob: float) -> float:
    """Map a mean segment log-probability to a [0, 1] confidence score.

    faster-whisper's per-segment ``avg_logprob`` is the average log
    probability of the decoded tokens — it is always ``<= 0`` (a perfect
    decode is 0; less-confident decodes are increasingly negative).

    We use ``confidence = exp(mean_logprob)`` clamped to ``[0, 1]``:

    * ``exp(0.0) = 1.0`` — a deterministic decode maps to full
      confidence.
    * ``exp(-1.0) ~= 0.37`` — an "uncertain" decode maps to a moderate
      confidence.
    * ``exp(-5.0) ~= 0.0067`` — a very-uncertain decode maps near zero.

    The exponential mapping was chosen over a linear shift like
    ``(logprob + 5) / 5`` because it has a principled probabilistic
    interpretation (geometric mean of token probabilities) and gracefully
    handles values past the linear cutoff. Non-finite inputs (NaN, +/-inf)
    map to 0.0; the upstream caller treats 0.0 as "no confidence" and the
    confidence-floor gate (Step 13) discards it.
    """
    if not math.isfinite(mean_logprob):
        return 0.0
    if mean_logprob >= 0.0:
        return 1.0
    value = math.exp(mean_logprob)
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


class Transcript(BaseModel):
    """Result of a single ``transcribe()`` call.

    ``confidence`` is the exp-mapped mean of segment ``avg_logprob`` in
    ``[0, 1]``. ``language`` is faster-whisper's detected language code
    (e.g. ``"en"``); when the model cannot detect a language (empty
    audio, ``None`` from the upstream lib, missing attribute) the
    wrapper substitutes :data:`UNKNOWN_LANGUAGE` (``"unknown"``) so the
    field is always non-empty and downstream gates can branch on it.
    ``duration_ms`` is the audio duration the model reports — not the
    wall-clock inference time.
    """

    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    language: str = Field(default=UNKNOWN_LANGUAGE, min_length=1)
    duration_ms: int = Field(ge=0)


class WhisperTranscriber:
    """Async wrapper around faster-whisper with GPU autodetect + CPU fallback.

    The class is instantiable (no module-level singleton); the host
    application is expected to construct **one** transcriber at startup
    and share it across all callers. The underlying
    :class:`faster_whisper.WhisperModel` (and CTranslate2 backend) is
    not safe for concurrent ``transcribe()`` calls, so this wrapper
    serializes them with an :class:`asyncio.Lock` -- multiple concurrent
    ``await transcribe(...)`` calls run sequentially in FIFO order.

    Tests inject a ``model_factory`` that returns a controllable fake;
    production passes ``None`` so the real
    :class:`faster_whisper.WhisperModel` is used.

    Call :meth:`close` during application shutdown to drop the model
    reference. After ``close()`` further ``transcribe()`` calls raise
    :class:`RuntimeError`.
    """

    def __init__(
        self,
        *,
        model_size: str | None = None,
        device: str | None = None,
        cache_dir: Path | None = None,
        model_factory: ModelFactory | None = None,
        compute_type: str | None = None,
    ) -> None:
        self._model_size = model_size if model_size is not None else _whisper_model_from_env()
        self._cache_dir = cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR
        self._explicit_device = device
        self._compute_type = compute_type
        if model_factory is None:
            from faster_whisper import WhisperModel as _WhisperModel  # noqa: PLC0415

            self._model_factory: ModelFactory = _WhisperModel
        else:
            self._model_factory = model_factory

        # ``_model`` must be initialized BEFORE _resolve_device runs so
        # the CUDA-success path can park its probe model here for reuse
        # by the first ``transcribe()`` call.
        self._model: WhisperModel | None = None
        self._closed: bool = False
        # Serializes ``transcribe()`` calls -- the underlying
        # WhisperModel/CTranslate2 backend is not safe to call from
        # multiple threads at once, so we hold this lock across the
        # ``asyncio.to_thread(...)`` await.
        self._lock: asyncio.Lock = asyncio.Lock()
        # Resolve the actual device eagerly so the operator sees the
        # decision in the startup log. When the caller pinned a device
        # explicitly the model load stays lazy until the first
        # ``transcribe()`` call; the CUDA-probe path constructs (and
        # caches) the model up-front so failures surface at startup.
        self._resolved_device = self._resolve_device()
        _logger.info(
            "whisper transcriber configured (model=%r, device=%r, cache_dir=%s)",
            self._model_size,
            self._resolved_device,
            self._cache_dir,
        )

    # ------------------------------------------------------------------
    # Properties (mostly for tests + diagnostics)
    # ------------------------------------------------------------------

    @property
    def model_size(self) -> str:
        return self._model_size

    @property
    def device(self) -> str:
        return self._resolved_device

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    # ------------------------------------------------------------------
    # Device resolution
    # ------------------------------------------------------------------

    def _resolve_device(self) -> str:
        """Return the device the model will load on.

        If the caller passed ``device="cpu"`` or ``device="cuda"``
        explicitly we honour it without probing — model construction
        happens lazily on the first ``transcribe()`` call. Otherwise we
        try CUDA first by constructing a probe model; on any failure we
        fall back to CPU (eagerly constructing the CPU model so the
        operator sees both legs of the fallback in the startup logs and
        so the first ``transcribe()`` call doesn't surprise-stall on
        model load). A successful CUDA init is reused for the first
        ``transcribe()`` call (no double-load).
        """
        if self._explicit_device is not None:
            # No probe — trust the caller. Model construction happens
            # lazily on the first transcribe() call.
            return self._explicit_device

        try:
            self._model = self._build_model("cuda")
            return "cuda"
        except Exception as exc:  # noqa: BLE001 -- any init failure -> CPU
            # ``exc_info=True`` so the operator sees the exception class
            # + traceback in the startup log; bare ``%s`` swallows the
            # type name and any chained context.
            _logger.warning(
                "CUDA whisper init failed (%s); falling back to CPU",
                exc,
                exc_info=True,
            )
            self._model = self._build_model("cpu")
            return "cpu"

    def _build_model(self, device: str) -> WhisperModel:
        """Construct a ``WhisperModel`` via the injected factory.

        ``compute_type`` defaults to a sensible per-device value:
        ``float16`` on CUDA (fits in 8 GB, decent quality) and ``int8``
        on CPU (fastest with acceptable quality on the ``small`` model).
        Callers can override via the constructor.
        """
        if self._compute_type is not None:
            compute_type = self._compute_type
        elif device == "cuda":
            compute_type = "float16"
        else:
            compute_type = "int8"

        return self._model_factory(
            model_size_or_path=self._model_size,
            device=device,
            compute_type=compute_type,
            download_root=str(self._cache_dir),
        )

    def _ensure_model(self) -> WhisperModel:
        if self._model is not None:
            return self._model
        self._model = self._build_model(self._resolved_device)
        return self._model

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def transcribe(self, audio: NDArray[np.int16]) -> Transcript:
        """Transcribe ``audio`` (int16 mono @ 16 kHz) to a :class:`Transcript`.

        The underlying ``model.transcribe()`` is offloaded to a thread
        via :func:`asyncio.to_thread` so the calling event loop is free
        to keep draining the mic queue while inference runs.

        Concurrent callers are serialized with an instance-level
        :class:`asyncio.Lock`: the second ``await`` waits until the
        first returns. This is required because the underlying
        WhisperModel / CTranslate2 backend is not safe to call from
        multiple threads at once.
        """
        if self._closed:
            raise RuntimeError("transcriber is closed")
        if audio.ndim != 1:
            raise ValueError(f"transcribe expects mono 1-D audio, got shape {audio.shape}")
        if audio.dtype != np.int16:
            raise TypeError(f"transcribe expects int16 audio, got {audio.dtype}")
        async with self._lock:
            if self._closed:
                raise RuntimeError("transcriber is closed")
            return await asyncio.to_thread(self._transcribe_sync, audio)

    async def close(self) -> None:
        """Drop the underlying model reference and reject further calls.

        Safe to call multiple times. ``close()`` waits for any in-flight
        ``transcribe()`` to finish (it shares the lock); only *new* calls
        made after ``close()`` returns will raise :class:`RuntimeError`.
        Must not be called from inside a ``transcribe()`` coroutine.
        """
        async with self._lock:
            self._closed = True
            self._model = None

    def _transcribe_sync(self, audio: NDArray[np.int16]) -> Transcript:
        """Blocking transcription path. Runs in the worker thread."""
        # Empty audio short-circuit -- skip the model call entirely so
        # we don't drag in lazy-loaded model weights for a no-op buffer.
        if audio.size == 0:
            return Transcript(
                text="",
                confidence=0.0,
                language=UNKNOWN_LANGUAGE,
                duration_ms=0,
            )

        model = self._ensure_model()
        # faster-whisper accepts float32 in [-1, 1]. Dividing by 32768
        # maps int16 min (-32768) to -1.0 exactly; max (+32767) maps to
        # ~0.99997. The ``clip`` floor catches the symmetric edge after
        # the divide.
        as_float = (audio.astype(np.float32) / 32768.0).clip(-1.0, 1.0)
        segments_iter, info = model.transcribe(as_float)
        # ``segments_iter`` is a generator -- materialize it so we can
        # iterate twice (text + logprob mean).
        segments = list(segments_iter)

        # Collapse intra/inter-segment whitespace. faster-whisper often
        # emits leading-space tokens (" let's", " play") which would
        # otherwise produce double-spaces when joined.
        joined = "".join(seg.text for seg in segments)
        text = re.sub(r"\s+", " ", joined).strip()
        if segments:
            logprobs = [
                float(seg.avg_logprob)
                for seg in segments
                if math.isfinite(float(seg.avg_logprob))
            ]
            if not logprobs:
                _logger.warning(
                    "all segments had non-finite avg_logprob; "
                    "confidence will be 0.0 (segment_count=%d)",
                    len(segments),
                )
                mean_logprob = float("-inf")
            else:
                mean_logprob = sum(logprobs) / len(logprobs)
        else:
            mean_logprob = float("-inf")

        confidence = _logprob_to_confidence(mean_logprob)
        # ``info.duration`` is seconds; round to int ms.
        duration_seconds = float(getattr(info, "duration", 0.0) or 0.0)
        duration_ms = max(0, int(round(duration_seconds * 1000.0)))
        raw_language = getattr(info, "language", None)
        language = str(raw_language) if raw_language else UNKNOWN_LANGUAGE

        return Transcript(
            text=text,
            confidence=confidence,
            language=language,
            duration_ms=duration_ms,
        )


# ----------------------------------------------------------------------
# --download operator entrypoint
# ----------------------------------------------------------------------


def _download_model(
    *,
    model_size: str | None = None,
    cache_dir: Path | None = None,
    model_factory: ModelFactory | None = None,
) -> str:
    """Pre-fetch the configured whisper model into the on-disk cache.

    Returns the resolved model size. Forces ``device="cpu"`` for the
    pre-fetch so the path works on hosts without a GPU. The factory is
    invoked exactly once with ``download_root=cache_dir`` so the model
    files land in the project-relative cache directory.
    """
    resolved_size = model_size if model_size is not None else _whisper_model_from_env()
    resolved_cache = cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR
    resolved_cache.mkdir(parents=True, exist_ok=True)

    if model_factory is None:
        from faster_whisper import WhisperModel as _WhisperModel  # noqa: PLC0415

        factory: ModelFactory = _WhisperModel
    else:
        factory = model_factory

    _logger.info(
        "downloading whisper model %r to %s (this can take several minutes)",
        resolved_size,
        resolved_cache,
    )
    factory(
        model_size_or_path=resolved_size,
        device="cpu",
        compute_type="int8",
        download_root=str(resolved_cache),
    )
    _logger.info("whisper model %r ready in %s", resolved_size, resolved_cache)
    return resolved_size


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toybox.audio.stt",
        description=(
            "faster-whisper operator entrypoint. Use --download to pre-fetch "
            "the configured model into data/models/ before the first session."
        ),
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help=(
            f"Pre-fetch the configured whisper model "
            f"(${WHISPER_MODEL_ENV}, default {DEFAULT_MODEL!r}) into "
            f"{DEFAULT_CACHE_DIR}. Exits cleanly when the model is cached."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if not args.download:
        parser.print_help()
        return 0
    print(f"{WHISPER_MODEL_ENV}={os.environ.get(WHISPER_MODEL_ENV, '(unset)')}")
    resolved_size = _whisper_model_from_env()
    try:
        resolved = _download_model()
    except Exception as exc:  # noqa: BLE001 -- diagnostic only
        # Surface enough context (model, cache path, exception class)
        # for the operator to file a useful bug report. The full
        # traceback goes to the logger so it isn't lost.
        _logger.exception("whisper --download failed")
        print(
            (
                f"download failed: model={resolved_size!r}, "
                f"cache={DEFAULT_CACHE_DIR}, "
                f"exc_type={type(exc).__name__}: {exc}"
            ),
            file=sys.stderr,
        )
        return 1
    print(f"whisper model {resolved!r} ready in {DEFAULT_CACHE_DIR}")
    return 0


if __name__ == "__main__":  # pragma: no cover -- operator entry
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CACHE_DIR",
    "DEFAULT_MODEL",
    "EXPECTED_SAMPLE_RATE",
    "ModelFactory",
    "Transcript",
    "UNKNOWN_LANGUAGE",
    "WHISPER_MODEL_ENV",
    "WhisperTranscriber",
    "main",
]
