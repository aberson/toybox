"""silero-vad gating.

The VAD operates on fixed-size 512-sample frames at 16 kHz (silero's
canonical input). Incoming audio is buffered until at least one frame
is available; each frame's speech probability is compared against
``threshold`` and contiguous speech frames (with a hangover so the tail
of a phrase isn't clipped) are concatenated into a single emitted
chunk.

The :class:`VadGate` is constructed with an injectable
``predict(frame: np.ndarray) -> float`` so tests never need the real
ONNX model. :class:`SileroVadPredictor` is the production
implementation that lazy-loads ``data/models/silero_vad.onnx`` on first
call and raises :class:`FileNotFoundError` with a clear message if the
model isn't present (operators download it; we deliberately do *not*
bundle the model in git).
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    import onnxruntime as ort

_logger = logging.getLogger(__name__)

# Silero v5 expects exactly 512 samples at 16 kHz per inference call.
SILERO_FRAME_SAMPLES: Final[int] = 512
SAMPLE_RATE: Final[int] = 16000

THRESHOLD_ENV: Final[str] = "TOYBOX_VAD_THRESHOLD"
DEFAULT_THRESHOLD: Final[float] = 0.5

# Hangover keeps the tail of a phrase: this many *frames* of silence
# are still considered "speech" before we close the segment. 8 frames
# at 512 samples / 16 kHz = ~256 ms — long enough to ride through
# inter-word pauses without hard-clipping the closing consonant.
DEFAULT_HANGOVER_FRAMES: Final[int] = 8

DEFAULT_MODEL_PATH: Final[Path] = Path("data") / "models" / "silero_vad.onnx"
MODEL_PATH_ENV: Final[str] = "TOYBOX_VAD_MODEL_PATH"


Predictor = Callable[[NDArray[np.float32]], float]


def _threshold_from_env() -> float:
    raw = os.environ.get(THRESHOLD_ENV)
    if raw is None:
        return DEFAULT_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not a float; falling back to %.2f",
            THRESHOLD_ENV,
            raw,
            DEFAULT_THRESHOLD,
        )
        return DEFAULT_THRESHOLD
    if not 0.0 <= value <= 1.0:
        _logger.warning(
            "%s=%.3f outside 0..1; falling back to %.2f",
            THRESHOLD_ENV,
            value,
            DEFAULT_THRESHOLD,
        )
        return DEFAULT_THRESHOLD
    return value


class VadGate:
    """Frame-level VAD with hangover-aware speech-segment concatenation.

    ``feed(samples)`` accepts arbitrary-length int16 audio and yields
    *speech segments* — contiguous int16 arrays that the gate has
    decided are speech. Non-speech frames are dropped. ``flush()``
    closes any open segment; call it on shutdown.
    """

    def __init__(
        self,
        *,
        predictor: Predictor,
        threshold: float | None = None,
        hangover_frames: int = DEFAULT_HANGOVER_FRAMES,
        sample_rate: int = SAMPLE_RATE,
        frame_samples: int = SILERO_FRAME_SAMPLES,
    ) -> None:
        if threshold is None:
            threshold = _threshold_from_env()
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in 0..1, got {threshold}")
        if hangover_frames < 0:
            raise ValueError(f"hangover_frames must be >= 0, got {hangover_frames}")
        if frame_samples <= 0:
            raise ValueError(f"frame_samples must be > 0, got {frame_samples}")
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be > 0, got {sample_rate}")

        self._predictor = predictor
        self._threshold = threshold
        self._hangover_frames = hangover_frames
        self._sample_rate = sample_rate
        self._frame_samples = frame_samples

        # Pending int16 audio that hasn't filled a frame yet.
        self._pending: NDArray[np.int16] = np.empty(0, dtype=np.int16)
        # Open speech segment (int16 frames concatenated).
        self._segment: list[NDArray[np.int16]] = []
        # Frames since last frame that scored above threshold; we close
        # the segment once this exceeds the hangover.
        self._silence_streak = 0
        self._in_speech = False

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def frame_samples(self) -> int:
        return self._frame_samples

    def feed(self, samples: NDArray[np.int16]) -> Iterator[NDArray[np.int16]]:
        """Process ``samples`` and yield any closed speech segments."""
        if samples.dtype != np.int16:
            raise TypeError(f"VadGate expects int16 samples, got {samples.dtype}")
        if samples.ndim != 1:
            raise ValueError(f"VadGate expects mono 1-D samples, got shape {samples.shape}")
        if samples.size == 0:
            return

        if self._pending.size:
            buffer = np.concatenate((self._pending, samples))
        else:
            buffer = samples

        n_frames, leftover = divmod(buffer.size, self._frame_samples)
        if leftover:
            self._pending = buffer[-leftover:].copy()
            framed = buffer[: n_frames * self._frame_samples]
        else:
            self._pending = np.empty(0, dtype=np.int16)
            framed = buffer

        for i in range(n_frames):
            frame = framed[i * self._frame_samples : (i + 1) * self._frame_samples]
            yield from self._process_frame(frame)

    def flush(self) -> Iterator[NDArray[np.int16]]:
        """Close any open speech segment. Yields it if there was one."""
        if self._in_speech and self._segment:
            chunk = np.concatenate(self._segment)
            self._segment = []
            self._in_speech = False
            self._silence_streak = 0
            yield chunk
        # Drop any sub-frame leftover; it's < 32 ms and not worth a
        # forced VAD call on a zero-padded frame.
        self._pending = np.empty(0, dtype=np.int16)

    def _process_frame(self, frame: NDArray[np.int16]) -> Iterator[NDArray[np.int16]]:
        # silero expects float32 in [-1.0, 1.0].
        as_float = (frame.astype(np.float32) / 32768.0).clip(-1.0, 1.0)
        score = float(self._predictor(as_float))
        if not math.isfinite(score):
            _logger.warning(
                "vad predictor returned non-finite score %r; treating as silence",
                score,
            )
            score = 0.0
        is_speech = score >= self._threshold

        if is_speech:
            self._segment.append(frame.copy())
            self._silence_streak = 0
            self._in_speech = True
            return

        if self._in_speech:
            # Carry the silence frame as part of the hangover so the
            # tail of the phrase isn't clipped, then close once we've
            # exceeded the hangover budget.
            self._segment.append(frame.copy())
            self._silence_streak += 1
            if self._silence_streak > self._hangover_frames:
                # Trim the hangover silence from the closed segment so
                # the consumer doesn't get N frames of trailing room
                # tone.
                trim = self._silence_streak * self._frame_samples
                concatenated = np.concatenate(self._segment)
                if trim < concatenated.size:
                    chunk = concatenated[:-trim]
                else:
                    chunk = np.empty(0, dtype=np.int16)
                self._segment = []
                self._in_speech = False
                self._silence_streak = 0
                if chunk.size:
                    yield chunk


def _resolve_model_path() -> Path:
    raw = os.environ.get(MODEL_PATH_ENV)
    return Path(raw) if raw else DEFAULT_MODEL_PATH


class SileroVadPredictor:
    """Lazy-loading silero-vad ONNX predictor.

    Construct cheaply; the ONNX model is only loaded on the first
    call. If the model file is absent we raise
    :class:`FileNotFoundError` with the resolved path so an operator
    sees exactly where to drop ``silero_vad.onnx``.

    Silero's ONNX export uses an internal recurrent state. We carry
    the LSTM state across frames so consecutive ``__call__``s see a
    coherent sequence; it is reset by :meth:`reset_state`.
    """

    def __init__(self, model_path: Path | None = None) -> None:
        self._model_path = model_path if model_path is not None else _resolve_model_path()
        self._session: ort.InferenceSession | None = None
        self._state: NDArray[np.float32] | None = None
        self._sr_array = np.array(SAMPLE_RATE, dtype=np.int64)

    @property
    def model_path(self) -> Path:
        return self._model_path

    def reset_state(self) -> None:
        """Reset the LSTM hidden state between distinct utterances."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def _ensure_loaded(self) -> ort.InferenceSession:
        if self._session is not None:
            return self._session
        if not self._model_path.is_file():
            raise FileNotFoundError(
                f"silero-vad ONNX model not found at {self._model_path!s}. "
                "Download it from https://github.com/snakers4/silero-vad and place "
                "it at this path, or set TOYBOX_VAD_MODEL_PATH to override."
            )
        import onnxruntime as ort  # noqa: PLC0415  — lazy import is intentional

        self._session = ort.InferenceSession(
            str(self._model_path),
            providers=["CPUExecutionProvider"],
        )
        if self._state is None:
            self.reset_state()
        return self._session

    def __call__(self, frame: NDArray[np.float32]) -> float:
        session = self._ensure_loaded()
        if self._state is None:  # pragma: no cover — defensive
            self.reset_state()
        assert self._state is not None
        if frame.dtype != np.float32:
            frame = frame.astype(np.float32)
        if frame.ndim != 1:
            raise ValueError(f"SileroVadPredictor expects 1-D float frame, got {frame.shape}")
        x = frame.reshape(1, -1)
        outputs = session.run(
            None,
            {"input": x, "state": self._state, "sr": self._sr_array},
        )
        prob = float(np.asarray(outputs[0]).reshape(-1)[0])
        self._state = np.asarray(outputs[1], dtype=np.float32)
        return prob


__all__ = [
    "DEFAULT_HANGOVER_FRAMES",
    "DEFAULT_MODEL_PATH",
    "DEFAULT_THRESHOLD",
    "MODEL_PATH_ENV",
    "Predictor",
    "SAMPLE_RATE",
    "SILERO_FRAME_SAMPLES",
    "SileroVadPredictor",
    "THRESHOLD_ENV",
    "VadGate",
]
