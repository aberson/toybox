"""Unit tests for the silero-vad gate using stub predictors."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest
from numpy.typing import NDArray

from toybox.audio.vad import (
    DEFAULT_HANGOVER_FRAMES,
    DEFAULT_THRESHOLD,
    SILERO_FRAME_SAMPLES,
    THRESHOLD_ENV,
    SileroVadPredictor,
    VadGate,
)


def _silence(n_frames: int) -> NDArray[np.int16]:
    return np.zeros(n_frames * SILERO_FRAME_SAMPLES, dtype=np.int16)


def _tone(n_frames: int, amplitude: int = 5000) -> NDArray[np.int16]:
    """Simple non-zero buffer; the value doesn't matter — the stub decides."""
    return np.full(n_frames * SILERO_FRAME_SAMPLES, amplitude, dtype=np.int16)


class _ScriptedPredictor:
    """Returns a pre-baked sequence of scores; loops on the last value."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.calls = 0

    def __call__(self, frame: NDArray[np.float32]) -> float:
        if self.calls < len(self.scores):
            score = self.scores[self.calls]
        else:
            score = self.scores[-1]
        self.calls += 1
        return score


def _drain(it: Iterator[NDArray[np.int16]]) -> list[NDArray[np.int16]]:
    return list(it)


# ---------------------------------------------------------------------
# Threshold + simple gating
# ---------------------------------------------------------------------


def test_silence_predictor_yields_nothing() -> None:
    gate = VadGate(predictor=lambda _frame: 0.0, threshold=0.5, hangover_frames=2)
    chunks = _drain(gate.feed(_silence(10)))
    assert chunks == []
    chunks_flush = _drain(gate.flush())
    assert chunks_flush == []


def test_speech_predictor_emits_segment_after_hangover() -> None:
    # Speech for 5 frames, then silence — segment emits once we've
    # exceeded hangover (default = 8) of trailing silence frames.
    scores = [0.9] * 5 + [0.0] * (DEFAULT_HANGOVER_FRAMES + 2)
    gate = VadGate(predictor=_ScriptedPredictor(scores), threshold=DEFAULT_THRESHOLD)
    chunks = _drain(gate.feed(_tone(len(scores))))
    assert len(chunks) == 1
    # Trim should remove ALL hangover frames; emitted = the 5 speech frames.
    assert chunks[0].size == 5 * SILERO_FRAME_SAMPLES


def test_threshold_boundary_value_is_inclusive() -> None:
    # Exactly at threshold counts as speech.
    gate = VadGate(
        predictor=_ScriptedPredictor([0.5, 0.5, 0.0, 0.0, 0.0]),
        threshold=0.5,
        hangover_frames=1,
    )
    chunks = _drain(gate.feed(_tone(5)))
    assert len(chunks) == 1
    assert chunks[0].size == 2 * SILERO_FRAME_SAMPLES


def test_subthreshold_score_is_silence() -> None:
    gate = VadGate(
        predictor=_ScriptedPredictor([0.49, 0.49, 0.49]),
        threshold=0.5,
        hangover_frames=1,
    )
    assert _drain(gate.feed(_tone(3))) == []
    assert _drain(gate.flush()) == []


# ---------------------------------------------------------------------
# Hangover behavior
# ---------------------------------------------------------------------


def test_hangover_bridges_short_silence() -> None:
    # speech, speech, silence (within hangover), speech → one combined segment.
    scores = [0.9, 0.9, 0.0, 0.9, 0.9]
    gate = VadGate(
        predictor=_ScriptedPredictor(scores + [0.0] * 5),
        threshold=0.5,
        hangover_frames=2,
    )
    chunks = _drain(gate.feed(_tone(len(scores) + 5)))
    # After 5 score-frames + 5 silence: silence streak exceeds hangover at frame 8 (3rd silence).
    # The closed segment (post-trim) should be the leading 5 frames of audio.
    assert len(chunks) == 1
    assert chunks[0].size == 5 * SILERO_FRAME_SAMPLES


def test_hangover_zero_emits_immediately_on_silence() -> None:
    scores = [0.9, 0.9, 0.0]
    gate = VadGate(
        predictor=_ScriptedPredictor(scores + [0.0] * 3),
        threshold=0.5,
        hangover_frames=0,
    )
    chunks = _drain(gate.feed(_tone(len(scores) + 3)))
    assert len(chunks) == 1
    # 2 speech frames retained; the 1 silence frame trims out.
    assert chunks[0].size == 2 * SILERO_FRAME_SAMPLES


def test_flush_emits_open_segment() -> None:
    # Speech, never silenced → only flush() can close the segment.
    gate = VadGate(
        predictor=lambda _f: 0.9,
        threshold=0.5,
        hangover_frames=2,
    )
    chunks_during = _drain(gate.feed(_tone(4)))
    assert chunks_during == []
    chunks_flush = _drain(gate.flush())
    assert len(chunks_flush) == 1
    assert chunks_flush[0].size == 4 * SILERO_FRAME_SAMPLES


def test_flush_when_no_open_segment_yields_nothing() -> None:
    gate = VadGate(predictor=lambda _f: 0.0, threshold=0.5)
    assert _drain(gate.flush()) == []


# ---------------------------------------------------------------------
# Frame buffering
# ---------------------------------------------------------------------


def test_partial_frame_is_held_until_complete() -> None:
    predictor = _ScriptedPredictor([0.9, 0.9, 0.0, 0.0])
    gate = VadGate(
        predictor=predictor,
        threshold=0.5,
        hangover_frames=0,
    )
    # Feed less than one frame — should not invoke predictor at all.
    half = SILERO_FRAME_SAMPLES // 2
    _drain(gate.feed(np.zeros(half, dtype=np.int16)))
    assert predictor.calls == 0
    # Add the rest of the frame: now exactly one inference fires.
    _drain(gate.feed(np.zeros(SILERO_FRAME_SAMPLES - half, dtype=np.int16)))
    assert predictor.calls == 1


def test_predictor_receives_float32_in_unit_range() -> None:
    captured: list[NDArray[np.float32]] = []

    def predictor(frame: NDArray[np.float32]) -> float:
        captured.append(frame)
        return 0.0

    gate = VadGate(predictor=predictor, threshold=0.5, hangover_frames=0)
    # Use full-scale int16 to exercise the normalization edge.
    full_scale = np.full(SILERO_FRAME_SAMPLES, 32767, dtype=np.int16)
    _drain(gate.feed(full_scale))
    assert len(captured) == 1
    f = captured[0]
    assert f.dtype == np.float32
    assert f.size == SILERO_FRAME_SAMPLES
    assert -1.0 <= float(f.min()) <= 1.0
    assert -1.0 <= float(f.max()) <= 1.0
    assert float(f.max()) > 0.99  # near +1.0


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def test_feed_rejects_wrong_dtype() -> None:
    gate = VadGate(predictor=lambda _f: 0.0)
    with pytest.raises(TypeError):
        _drain(gate.feed(np.array([0.0, 0.0], dtype=np.float32)))


def test_feed_rejects_multichannel() -> None:
    gate = VadGate(predictor=lambda _f: 0.0)
    with pytest.raises(ValueError):
        _drain(gate.feed(np.zeros((SILERO_FRAME_SAMPLES, 2), dtype=np.int16)))


def test_feed_empty_is_noop() -> None:
    gate = VadGate(predictor=lambda _f: 0.0)
    assert _drain(gate.feed(np.empty(0, dtype=np.int16))) == []


def test_invalid_threshold_rejected() -> None:
    with pytest.raises(ValueError):
        VadGate(predictor=lambda _f: 0.0, threshold=1.5)
    with pytest.raises(ValueError):
        VadGate(predictor=lambda _f: 0.0, threshold=-0.1)


def test_invalid_hangover_rejected() -> None:
    with pytest.raises(ValueError):
        VadGate(predictor=lambda _f: 0.0, threshold=0.5, hangover_frames=-1)


# ---------------------------------------------------------------------
# Env-var threshold
# ---------------------------------------------------------------------


def test_threshold_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(THRESHOLD_ENV, "0.8")
    gate = VadGate(predictor=lambda _f: 0.0)
    assert gate.threshold == pytest.approx(0.8)


def test_threshold_env_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(THRESHOLD_ENV, "not-a-float")
    gate = VadGate(predictor=lambda _f: 0.0)
    assert gate.threshold == pytest.approx(DEFAULT_THRESHOLD)


def test_threshold_env_out_of_range_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(THRESHOLD_ENV, "1.5")
    gate = VadGate(predictor=lambda _f: 0.0)
    assert gate.threshold == pytest.approx(DEFAULT_THRESHOLD)


def test_explicit_threshold_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(THRESHOLD_ENV, "0.8")
    gate = VadGate(predictor=lambda _f: 0.0, threshold=0.3)
    assert gate.threshold == pytest.approx(0.3)


# ---------------------------------------------------------------------
# SileroVadPredictor (lazy load + missing-model error)
# ---------------------------------------------------------------------


def test_silero_predictor_raises_clear_error_when_model_missing(
    tmp_path: pytest.TempPathFactory,
) -> None:
    bogus = tmp_path / "no_such_silero.onnx"  # type: ignore[operator]
    predictor = SileroVadPredictor(model_path=bogus)
    frame = np.zeros(SILERO_FRAME_SAMPLES, dtype=np.float32)
    with pytest.raises(FileNotFoundError) as exc:
        predictor(frame)
    assert str(bogus) in str(exc.value)


def test_silero_predictor_does_not_load_until_called() -> None:
    """Construction must not touch the model file (lazy load contract)."""
    from pathlib import Path

    predictor = SileroVadPredictor(model_path=Path("does_not_exist.onnx"))
    # Internal session is unloaded; the first __call__ does the work.
    assert predictor._session is None  # noqa: SLF001


# ---------------------------------------------------------------------
# Validation (frame_samples / sample_rate at construction)
# ---------------------------------------------------------------------


def test_invalid_frame_samples_rejected() -> None:
    with pytest.raises(ValueError):
        VadGate(predictor=lambda _f: 0.0, threshold=0.5, frame_samples=0)
    with pytest.raises(ValueError):
        VadGate(predictor=lambda _f: 0.0, threshold=0.5, frame_samples=-1)


def test_invalid_sample_rate_rejected() -> None:
    with pytest.raises(ValueError):
        VadGate(predictor=lambda _f: 0.0, threshold=0.5, sample_rate=0)
    with pytest.raises(ValueError):
        VadGate(predictor=lambda _f: 0.0, threshold=0.5, sample_rate=-16000)


# ---------------------------------------------------------------------
# Non-finite predictor scores treated as silence + warning
# ---------------------------------------------------------------------


def test_nonfinite_predictor_score_is_silence_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging as _logging

    gate = VadGate(predictor=lambda _f: float("nan"), threshold=0.5, hangover_frames=0)
    with caplog.at_level(_logging.WARNING, logger="toybox.audio.vad"):
        chunks = _drain(gate.feed(_tone(2)))
    assert chunks == []
    msgs = [r.getMessage() for r in caplog.records if "non-finite" in r.getMessage()]
    assert msgs, "expected non-finite warning log"
