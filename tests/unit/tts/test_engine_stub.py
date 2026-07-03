"""Phase Z Z3 — stub-mode engine contract.

CI has no model files and no ``tts`` extra, so everything here runs
against ``TOYBOX_TTS_STUB=1`` (mirrors the image-gen stub testing
shape). The load-bearing assertions:

* the stub emits a STRUCTURALLY VALID WAV (parsed with the stdlib
  ``wave`` module — channels / rate / width / frames all sane), so
  the Z4 worker + static mount can serve stub clips end-to-end;
* stub output is deterministic (byte-identical on repeat calls) and
  input-sensitive (distinct text/voice → distinct bytes) — the Z4
  content-hash cache depends on both;
* input validation raises before any synthesis work.
"""

from __future__ import annotations

import io
import wave

import pytest

from toybox.tts import DEFAULT_NEURAL_VOICE, synthesize
from toybox.tts.engine import SAMPLE_RATE, STUB_ENV


@pytest.fixture
def stub_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STUB_ENV, "1")


def test_default_neural_voice_constant() -> None:
    """Pins the fallback voice id the plan casts for profile-less personas."""
    assert DEFAULT_NEURAL_VOICE == "af_heart"


def test_stub_synthesize_round_trips_valid_wav(stub_mode: None) -> None:
    data = synthesize("Hello there, brave explorer!", DEFAULT_NEURAL_VOICE)
    assert data[:4] == b"RIFF"
    with wave.open(io.BytesIO(data), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnframes() > 0


def test_stub_output_is_deterministic(stub_mode: None) -> None:
    first = synthesize("The wizard waves his wand.", "am_michael")
    second = synthesize("The wizard waves his wand.", "am_michael")
    assert first == second


def test_stub_output_differs_by_text(stub_mode: None) -> None:
    a = synthesize("Step one: find the red block.", "am_michael")
    b = synthesize("Step two: stack it on the tower.", "am_michael")
    assert a != b


def test_stub_output_differs_by_voice(stub_mode: None) -> None:
    a = synthesize("Once upon a time.", "af_bella")
    b = synthesize("Once upon a time.", "bf_emma")
    assert a != b


def test_synthesize_rejects_empty_text(stub_mode: None) -> None:
    with pytest.raises(ValueError):
        synthesize("", DEFAULT_NEURAL_VOICE)


def test_synthesize_rejects_whitespace_text(stub_mode: None) -> None:
    with pytest.raises(ValueError):
        synthesize("   \n\t", DEFAULT_NEURAL_VOICE)


def test_synthesize_rejects_empty_voice(stub_mode: None) -> None:
    with pytest.raises(ValueError):
        synthesize("Hello", "")


def test_synthesize_rejects_whitespace_voice(stub_mode: None) -> None:
    """Symmetric with the text guard — a whitespace-only voice id must
    ValueError here, not die inside kokoro on the real path."""
    with pytest.raises(ValueError):
        synthesize("Hello", "   ")
