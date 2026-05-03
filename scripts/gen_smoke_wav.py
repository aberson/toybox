"""One-shot helper to (re)render the smoke E2E fixture WAV.

Renders ``"Let's play unicorns."`` via pyttsx3 (Windows SAPI5 offline TTS),
resamples to 16 kHz mono int16, and writes
``tests/fixtures/audio/lets_play_unicorns.wav``. The fixture must
transcribe via faster-whisper above the 0.55 confidence floor and match
the ``lets_play_X`` trigger pattern.

Usage::

    uv run --with pyttsx3 python scripts/gen_smoke_wav.py

pyttsx3 is **not** tracked as a project dependency: its cross-platform
extras (notably the macOS ``pyobjc`` framework collection) bloat
``uv.lock`` by thousands of lines for a one-shot helper. The committed
WAV at ``tests/fixtures/audio/lets_play_unicorns.wav`` is the source of
truth; only re-run this script (with ``uv run --with pyttsx3``) when the
fixture needs to be refreshed (e.g. a faster-whisper upgrade changes
confidence-floor behaviour). The script is idempotent — it overwrites
the existing WAV.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import wave
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray

try:
    import pyttsx3
except ImportError:
    print(
        "pyttsx3 is not installed. To regenerate the fixture WAV, run:\n"
        "  uv run --with pyttsx3 python scripts/gen_smoke_wav.py\n"
        "On Windows this uses SAPI5; pyttsx3 is not tracked as a project dep "
        "since the WAV is committed and only regenerated on demand.",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

DEFAULT_PHRASE: Final[str] = "Let's play unicorns."
DEFAULT_OUT: Final[Path] = Path("tests") / "fixtures" / "audio" / "lets_play_unicorns.wav"
TARGET_SAMPLE_RATE: Final[int] = 16000

# pyttsx3 default rate is 200 wpm which faster-whisper handles fine on
# clear voices but the SAPI5 default voice is borderline; 150 wpm gives
# whisper-small the headroom it needs to clear the 0.55 confidence floor.
SLOW_RATE_WPM: Final[int] = 150

_logger = logging.getLogger("gen_smoke_wav")


def _read_wav_int16_mono(path: Path) -> tuple[NDArray[np.int16], int]:
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        sampwidth = wav.getsampwidth()
        n_channels = wav.getnchannels()
        n_frames = wav.getnframes()
        raw = wav.readframes(n_frames)
    if sampwidth != 2:
        raise ValueError(f"expected 16-bit PCM, got sampwidth={sampwidth} bytes ({path})")
    samples = np.frombuffer(raw, dtype=np.int16)
    if n_channels == 2:
        stereo = samples.reshape(-1, 2).astype(np.int32)
        samples = ((stereo[:, 0] + stereo[:, 1]) // 2).astype(np.int16)
    elif n_channels != 1:
        raise ValueError(f"expected mono or stereo, got channels={n_channels} ({path})")
    return samples, sample_rate


def _resample_linear_int16(
    samples: NDArray[np.int16],
    src_rate: int,
    dst_rate: int,
) -> NDArray[np.int16]:
    """Linear interpolation resample. Adequate for speech smoke tests.

    A higher-quality resampler (scipy.signal.resample_poly) would be
    nicer but adds a dep that this offline-rendered fixture does not
    need; whisper handles linear-resampled speech at 16 kHz fine.
    """
    if src_rate == dst_rate:
        return samples.astype(np.int16, copy=False)
    if samples.size == 0:
        return samples.astype(np.int16, copy=False)
    src_n = samples.size
    dst_n = max(1, int(round(src_n * dst_rate / src_rate)))
    src_idx = np.linspace(0.0, src_n - 1.0, dst_n, dtype=np.float64)
    floor_idx = np.floor(src_idx).astype(np.int64)
    ceil_idx = np.minimum(floor_idx + 1, src_n - 1)
    frac = src_idx - floor_idx
    src_f = samples.astype(np.float64)
    interp = src_f[floor_idx] * (1.0 - frac) + src_f[ceil_idx] * frac
    return np.clip(interp, -32768, 32767).astype(np.int16)


def _write_wav_int16_mono(path: Path, samples: NDArray[np.int16], sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())


def _render_via_pyttsx3(phrase: str, raw_path: Path, *, rate_wpm: int) -> None:
    """Render ``phrase`` to a SAPI5 WAV at ``raw_path``."""
    engine = pyttsx3.init()
    engine.setProperty("rate", rate_wpm)
    engine.save_to_file(phrase, str(raw_path))
    engine.runAndWait()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gen_smoke_wav",
        description="Render the smoke E2E fixture WAV via pyttsx3 (SAPI5).",
    )
    parser.add_argument(
        "--phrase",
        default=DEFAULT_PHRASE,
        help=f"Phrase to render (default: {DEFAULT_PHRASE!r}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output WAV path (default: {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=SLOW_RATE_WPM,
        help=f"TTS speech rate in wpm (default: {SLOW_RATE_WPM}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _build_arg_parser().parse_args(argv)

    out_path: Path = args.out
    phrase: str = args.phrase
    rate_wpm: int = args.rate

    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_path = Path(tmp_dir) / "raw.wav"
        _logger.info("rendering phrase via pyttsx3 (rate=%d wpm)", rate_wpm)
        _render_via_pyttsx3(phrase, raw_path, rate_wpm=rate_wpm)
        if not raw_path.is_file() or raw_path.stat().st_size == 0:
            _logger.error("pyttsx3 produced no output at %s", raw_path)
            return 1

        samples, src_rate = _read_wav_int16_mono(raw_path)
        _logger.info("loaded raw render (samples=%d, src_rate=%d)", samples.size, src_rate)
        resampled = _resample_linear_int16(samples, src_rate, TARGET_SAMPLE_RATE)
        _write_wav_int16_mono(out_path, resampled, TARGET_SAMPLE_RATE)
        duration_sec = resampled.size / TARGET_SAMPLE_RATE
        _logger.info(
            "wrote %s (samples=%d, sample_rate=%d, duration=%.2fs)",
            out_path,
            resampled.size,
            TARGET_SAMPLE_RATE,
            duration_sec,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover -- one-shot helper
    raise SystemExit(main(sys.argv[1:]))
