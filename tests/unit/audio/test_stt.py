"""Unit tests for the faster-whisper STT wrapper.

All tests use a stub :class:`_FakeWhisperModel` injected via the
``model_factory`` seam so the suite never downloads the real ~500 MB
model and never requires a GPU. The ``--download`` operator path is
covered by asserting the factory is invoked once with the resolved
model size + cache path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import Levenshtein
import numpy as np
import pytest
from numpy.typing import NDArray
from pydantic import ValidationError

from toybox.audio.stt import (
    DEFAULT_CACHE_DIR,
    DEFAULT_MODEL,
    EXPECTED_SAMPLE_RATE,
    UNKNOWN_LANGUAGE,
    WHISPER_MODEL_ENV,
    Transcript,
    WhisperTranscriber,
    _download_model,
    _logprob_to_confidence,
    main,
)

# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------


@dataclass
class _FakeSegment:
    """Mimics faster_whisper.transcribe.Segment for the fields we read."""

    text: str
    avg_logprob: float


@dataclass
class _FakeInfo:
    """Mimics faster_whisper.transcribe.TranscriptionInfo."""

    language: str = "en"
    duration: float = 0.0


class _FakeWhisperModel:
    """Scripted stand-in for ``faster_whisper.WhisperModel``.

    ``segments`` are returned verbatim from ``transcribe()``.
    ``transcribe_delay`` is the wall-clock pause inside ``transcribe()``
    (used by the non-blocking test). Records the args it was constructed
    with so tests can assert factory wiring. ``captured_audio`` /
    ``call_log`` give tests visibility into the per-call audio payload
    and the ordering of overlapping calls.
    """

    def __init__(
        self,
        *,
        segments: Iterable[_FakeSegment] = (),
        info: _FakeInfo | None = None,
        transcribe_delay: float = 0.0,
        construct_args: dict[str, Any] | None = None,
        call_log: list[str] | None = None,
        call_label: str = "",
    ) -> None:
        self._segments = list(segments)
        self._info = info if info is not None else _FakeInfo()
        self._transcribe_delay = transcribe_delay
        self.construct_args = construct_args if construct_args is not None else {}
        self.transcribe_call_count = 0
        self.captured_audio: list[NDArray[np.float32]] = []
        self._call_log = call_log
        self._call_label = call_label

    def transcribe(
        self,
        audio: NDArray[np.float32],
        **_kwargs: Any,
    ) -> tuple[Iterator[_FakeSegment], _FakeInfo]:
        self.transcribe_call_count += 1
        self.captured_audio.append(np.asarray(audio).copy())
        if self._call_log is not None:
            self._call_log.append(f"start:{self._call_label}")
        if self._transcribe_delay > 0:
            time.sleep(self._transcribe_delay)
        if self._call_log is not None:
            self._call_log.append(f"end:{self._call_label}")
        # Match faster-whisper's API: returns a generator, not a list.
        return iter(self._segments), self._info


def _factory(
    model: _FakeWhisperModel,
    calls: list[dict[str, Any]] | None = None,
) -> Any:
    """Wrap a single fake model so it captures construction args."""

    def factory(**kwargs: Any) -> _FakeWhisperModel:
        if calls is not None:
            calls.append(kwargs)
        model.construct_args = dict(kwargs)
        return model

    return factory


def _scripted_factory(
    models: list[_FakeWhisperModel],
    calls: list[dict[str, Any]],
    fail_on_devices: set[str] | None = None,
) -> Any:
    """Factory that hands out ``models`` in order, optionally raising."""
    iterator = iter(models)

    def factory(**kwargs: Any) -> _FakeWhisperModel:
        calls.append(kwargs)
        device = kwargs.get("device")
        if fail_on_devices and device in fail_on_devices:
            raise RuntimeError(f"simulated init failure on device={device!r}")
        try:
            model = next(iterator)
        except StopIteration as exc:  # pragma: no cover -- test wiring bug
            raise AssertionError("factory called more times than models scripted") from exc
        model.construct_args = dict(kwargs)
        return model

    return factory


# ---------------------------------------------------------------------
# Transcript Pydantic validation
# ---------------------------------------------------------------------


def test_transcript_pydantic_validation() -> None:
    # Happy path constructs cleanly.
    t = Transcript(text="hi", confidence=0.5, language="en", duration_ms=100)
    assert t.text == "hi"
    assert t.confidence == 0.5
    assert t.language == "en"
    assert t.duration_ms == 100

    # text must be a string.
    with pytest.raises(ValidationError):
        Transcript(text=123, confidence=0.5, language="en", duration_ms=100)  # type: ignore[arg-type]

    # confidence outside 0..1 is rejected.
    with pytest.raises(ValidationError):
        Transcript(text="hi", confidence=1.5, language="en", duration_ms=100)
    with pytest.raises(ValidationError):
        Transcript(text="hi", confidence=-0.1, language="en", duration_ms=100)

    # duration_ms must be >= 0.
    with pytest.raises(ValidationError):
        Transcript(text="hi", confidence=0.5, language="en", duration_ms=-1)


# ---------------------------------------------------------------------
# Confidence mapping
# ---------------------------------------------------------------------


def test_confidence_within_unit_range() -> None:
    """Every plausible logprob maps into [0, 1]."""
    samples = [-100.0, -10.0, -5.0, -1.0, -0.5, -0.1, 0.0, 0.1]
    for lp in samples:
        c = _logprob_to_confidence(lp)
        assert 0.0 <= c <= 1.0, f"confidence {c!r} for logprob {lp!r} out of range"


def test_confidence_high_logprob_close_to_one() -> None:
    """Mean logprob near 0 -> confidence near 1."""
    assert _logprob_to_confidence(0.0) == pytest.approx(1.0)
    assert _logprob_to_confidence(-0.05) > 0.94
    assert _logprob_to_confidence(-0.1) > 0.9


def test_confidence_low_logprob_close_to_zero() -> None:
    """Very negative mean logprob -> confidence near 0."""
    assert _logprob_to_confidence(-10.0) < 0.001
    assert _logprob_to_confidence(-50.0) < 1e-15
    assert _logprob_to_confidence(float("-inf")) == 0.0


def test_confidence_handles_non_finite_logprob() -> None:
    assert _logprob_to_confidence(float("nan")) == 0.0
    assert _logprob_to_confidence(float("inf")) == 0.0


# ---------------------------------------------------------------------
# transcribe() wiring
# ---------------------------------------------------------------------


async def test_transcribe_returns_transcript_with_text() -> None:
    """Stub model returns segments -> assert text joined + cleaned to reference.

    The stub deliberately injects a leading-space + double-space drift
    ("lets play  unicorns") so the Levenshtein assertion is real (it
    measures the wrapper's whitespace-collapse + apostrophe handling),
    not a tautology against the stub.
    """
    reference = "let's play unicorns"
    # Stub-side text has drifts the wrapper can fix (whitespace) and
    # one it can't (missing apostrophe). Levenshtein ratio of
    # "lets play unicorns" vs "let's play unicorns" ~= 0.95 (one edit).
    model = _FakeWhisperModel(
        segments=[
            _FakeSegment(text=" lets play ", avg_logprob=-0.05),
            _FakeSegment(text=" unicorns", avg_logprob=-0.05),
        ],
        info=_FakeInfo(language="en", duration=1.5),
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    audio = np.zeros(16000, dtype=np.int16)

    transcript = await transcriber.transcribe(audio)

    # Whitespace collapse strips the leading space and the double-space
    # at the segment seam.
    assert transcript.text == "lets play unicorns"
    assert "  " not in transcript.text
    # Levenshtein measures the apostrophe drift the wrapper can't fix.
    assert Levenshtein.ratio(transcript.text, reference) >= 0.85
    assert transcript.language == "en"
    assert transcript.duration_ms == 1500
    assert 0.0 <= transcript.confidence <= 1.0
    # exp(-0.05) ~= 0.951
    assert transcript.confidence > 0.9


async def test_transcribe_does_not_block_event_loop() -> None:
    """While the stub's transcribe() sleeps, a parallel task keeps ticking.

    Proves ``asyncio.to_thread`` actually offloads the blocking call.
    """
    model = _FakeWhisperModel(
        segments=[_FakeSegment(text="hi", avg_logprob=-0.1)],
        info=_FakeInfo(language="en", duration=0.5),
        transcribe_delay=0.2,
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    audio = np.zeros(8000, dtype=np.int16)

    counter = 0

    async def ticker() -> None:
        nonlocal counter
        # Tick every 10 ms while transcribe() is running.
        for _ in range(40):
            counter += 1
            await asyncio.sleep(0.01)

    transcribe_task = asyncio.create_task(transcriber.transcribe(audio))
    ticker_task = asyncio.create_task(ticker())

    transcript = await transcribe_task
    await ticker_task

    # If transcribe() blocked the loop, counter would be ~0 when it
    # returned (the 200 ms sleep would have starved the ticker). With
    # to_thread offloading, the ticker advances during the wait.
    assert counter >= 10, f"event loop appears blocked during transcribe (counter={counter})"
    assert transcript.text == "hi"


# ---------------------------------------------------------------------
# Device resolution
# ---------------------------------------------------------------------


def test_cpu_explicit_device_skips_cuda_attempt() -> None:
    """device='cpu' constructor arg -> factory only called once when transcribe runs."""
    model = _FakeWhisperModel(
        segments=[], info=_FakeInfo(language="en", duration=0.0)
    )
    calls: list[dict[str, Any]] = []
    factory = _scripted_factory([model], calls)

    transcriber = WhisperTranscriber(device="cpu", model_factory=factory)
    # No probe -> no construction yet.
    assert calls == []
    assert transcriber.device == "cpu"

    # Lazy load on first transcribe call (sync path is fine to test directly).
    transcriber._ensure_model()  # noqa: SLF001 -- test seam
    assert len(calls) == 1
    assert calls[0]["device"] == "cpu"


def test_cuda_failure_falls_back_to_cpu(caplog: pytest.LogCaptureFixture) -> None:
    """CUDA factory call raises -> warn + retry on CPU."""
    cpu_model = _FakeWhisperModel(
        segments=[], info=_FakeInfo(language="en", duration=0.0)
    )
    calls: list[dict[str, Any]] = []
    factory = _scripted_factory(
        [cpu_model], calls, fail_on_devices={"cuda"}
    )

    with caplog.at_level(logging.WARNING, logger="toybox.audio.stt"):
        transcriber = WhisperTranscriber(model_factory=factory)

    assert transcriber.device == "cpu"
    # First call: cuda (raised). Second: cpu (succeeded).
    assert len(calls) == 2
    assert calls[0]["device"] == "cuda"
    assert calls[1]["device"] == "cpu"

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("CUDA whisper init failed" in m for m in warnings), warnings


def test_cuda_success_caches_probe_model() -> None:
    """When CUDA init succeeds, the probe model is reused for transcribe()."""
    cuda_model = _FakeWhisperModel(
        segments=[_FakeSegment(text="hi", avg_logprob=-0.1)],
        info=_FakeInfo(language="en", duration=0.1),
    )
    calls: list[dict[str, Any]] = []
    factory = _scripted_factory([cuda_model], calls)

    transcriber = WhisperTranscriber(model_factory=factory)
    assert transcriber.device == "cuda"
    assert len(calls) == 1
    # Calling _ensure_model again must not construct a second model.
    transcriber._ensure_model()  # noqa: SLF001 -- test seam
    assert len(calls) == 1


# ---------------------------------------------------------------------
# Env-var driven model selection
# ---------------------------------------------------------------------


def test_default_model_is_small(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WHISPER_MODEL_ENV, raising=False)
    model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    transcriber._ensure_model()  # noqa: SLF001 -- test seam
    assert model.construct_args["model_size_or_path"] == DEFAULT_MODEL
    assert DEFAULT_MODEL == "small"


def test_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WHISPER_MODEL_ENV, "tiny")
    model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    transcriber._ensure_model()  # noqa: SLF001 -- test seam
    assert model.construct_args["model_size_or_path"] == "tiny"


def test_empty_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(WHISPER_MODEL_ENV, "   ")
    model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    with caplog.at_level(logging.WARNING, logger="toybox.audio.stt"):
        transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    transcriber._ensure_model()  # noqa: SLF001 -- test seam
    assert model.construct_args["model_size_or_path"] == DEFAULT_MODEL
    assert any("is empty" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------
# transcribe() input validation
# ---------------------------------------------------------------------


async def test_transcribe_rejects_wrong_dtype() -> None:
    transcriber = WhisperTranscriber(
        device="cpu",
        model_factory=_factory(_FakeWhisperModel(segments=[], info=_FakeInfo())),
    )
    audio = np.zeros(1000, dtype=np.float32)
    with pytest.raises(TypeError):
        await transcriber.transcribe(audio)  # type: ignore[arg-type]


async def test_transcribe_rejects_non_mono() -> None:
    transcriber = WhisperTranscriber(
        device="cpu",
        model_factory=_factory(_FakeWhisperModel(segments=[], info=_FakeInfo())),
    )
    audio = np.zeros((1000, 2), dtype=np.int16)
    with pytest.raises(ValueError):
        await transcriber.transcribe(audio)


# ---------------------------------------------------------------------
# --download operator path
# ---------------------------------------------------------------------


def test_download_operator_prefetches_via_factory(tmp_path: Path) -> None:
    """``_download_model`` invokes the factory once with resolved size + cache path."""
    cache_dir = tmp_path / "models"
    model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    calls: list[dict[str, Any]] = []
    factory = _scripted_factory([model], calls)

    resolved = _download_model(
        model_size="tiny",
        cache_dir=cache_dir,
        model_factory=factory,
    )

    assert resolved == "tiny"
    assert len(calls) == 1
    args = calls[0]
    assert args["model_size_or_path"] == "tiny"
    assert args["device"] == "cpu"
    assert args["download_root"] == str(cache_dir)
    # The cache dir is created by the helper.
    assert cache_dir.is_dir()


def test_download_operator_resolves_env_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(WHISPER_MODEL_ENV, raising=False)
    cache_dir = tmp_path / "models"
    calls: list[dict[str, Any]] = []
    factory = _scripted_factory(
        [_FakeWhisperModel(segments=[], info=_FakeInfo())], calls
    )
    resolved = _download_model(cache_dir=cache_dir, model_factory=factory)
    assert resolved == DEFAULT_MODEL
    assert calls[0]["model_size_or_path"] == DEFAULT_MODEL


def test_default_cache_dir_is_data_models() -> None:
    assert DEFAULT_CACHE_DIR == Path("data") / "models"


# ---------------------------------------------------------------------
# Sample-rate constant
# ---------------------------------------------------------------------


def test_expected_sample_rate_is_16k() -> None:
    """Lock the sample-rate contract (faster-whisper assumes 16 kHz mono)."""
    assert EXPECTED_SAMPLE_RATE == 16000


# ---------------------------------------------------------------------
# Concurrency: lock around model.transcribe()
# ---------------------------------------------------------------------


async def test_concurrent_transcribes_are_serialized() -> None:
    """Two ``gather()``-ed calls run sequentially, not interleaved.

    Underlying WhisperModel/CTranslate2 isn't safe for concurrent
    calls. The wrapper holds an ``asyncio.Lock`` across the
    ``to_thread`` await -- proven by recording start/end markers on
    the fake and asserting the second start always comes after the
    first end.
    """
    call_log: list[str] = []
    model = _FakeWhisperModel(
        segments=[_FakeSegment(text="hi", avg_logprob=-0.1)],
        info=_FakeInfo(language="en", duration=0.1),
        # Small artificial delay forces overlap if the lock is missing.
        transcribe_delay=0.05,
        call_log=call_log,
        call_label="A",
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    audio = np.zeros(8000, dtype=np.int16)

    # Run two calls concurrently. If the lock is missing the worker
    # threads would interleave: ``["start:A", "start:A", "end:A", "end:A"]``.
    await asyncio.gather(
        transcriber.transcribe(audio),
        transcriber.transcribe(audio),
    )

    assert call_log == ["start:A", "end:A", "start:A", "end:A"], call_log


# ---------------------------------------------------------------------
# Empty segments / language fallback
# ---------------------------------------------------------------------


async def test_transcribe_empty_segments_yields_zero_confidence() -> None:
    """Non-empty audio + stub returns ``segments=[]`` -> empty text, 0.0 confidence.

    This is the "model heard noise, decoded nothing" path -- distinct
    from the empty-audio short-circuit below.
    """
    model = _FakeWhisperModel(segments=[], info=_FakeInfo(language="en", duration=0.0))
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    audio = np.zeros(1000, dtype=np.int16)

    transcript = await transcriber.transcribe(audio)

    assert transcript.text == ""
    assert transcript.confidence == 0.0
    # Stub kept "en" so the language passes through even when segments
    # are empty (only the empty-audio short-circuit forces "unknown").
    assert transcript.language == "en"
    assert transcript.duration_ms == 0
    # Sanity: model WAS called (vs. the empty-audio short-circuit).
    assert model.transcribe_call_count == 1


async def test_transcribe_empty_audio_short_circuits() -> None:
    """Zero-length input never calls the model -> unknown language sentinel."""
    model = _FakeWhisperModel(
        segments=[_FakeSegment(text="should not be returned", avg_logprob=-0.05)],
        info=_FakeInfo(language="en", duration=99.0),
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    audio = np.zeros(0, dtype=np.int16)

    transcript = await transcriber.transcribe(audio)

    assert transcript.text == ""
    assert transcript.confidence == 0.0
    assert transcript.language == UNKNOWN_LANGUAGE
    assert transcript.duration_ms == 0
    assert model.transcribe_call_count == 0


# ---------------------------------------------------------------------
# int16 -> float32 conversion
# ---------------------------------------------------------------------


async def test_transcribe_converts_int16_to_normalized_float32() -> None:
    """Wrapper hands the model float32 in [-1, 1]; int16 min maps to -1.0 exactly."""
    model = _FakeWhisperModel(
        segments=[_FakeSegment(text="x", avg_logprob=-0.1)],
        info=_FakeInfo(language="en", duration=0.0),
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    audio = np.array([-32768, 0, 32767], dtype=np.int16)

    await transcriber.transcribe(audio)

    assert len(model.captured_audio) == 1
    captured = model.captured_audio[0]
    assert captured.dtype == np.float32
    # Floor: int16 min divided by 32768 is exactly -1.0; clip floor
    # also pins it (defends against any future divisor change).
    assert captured[0] == pytest.approx(-1.0)
    assert captured[1] == pytest.approx(0.0)
    # int16 max divided by 32768 is just under 1.0.
    assert 0.99 < captured[2] < 1.0
    assert captured.min() >= -1.0
    assert captured.max() <= 1.0


# ---------------------------------------------------------------------
# language fallback
# ---------------------------------------------------------------------


async def test_transcribe_language_none_becomes_unknown() -> None:
    model = _FakeWhisperModel(
        segments=[_FakeSegment(text="hi", avg_logprob=-0.1)],
        info=_FakeInfo(language=None, duration=0.5),  # type: ignore[arg-type]
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    transcript = await transcriber.transcribe(np.zeros(1000, dtype=np.int16))
    assert transcript.language == UNKNOWN_LANGUAGE


async def test_transcribe_language_empty_string_becomes_unknown() -> None:
    model = _FakeWhisperModel(
        segments=[_FakeSegment(text="hi", avg_logprob=-0.1)],
        info=_FakeInfo(language="", duration=0.5),
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    transcript = await transcriber.transcribe(np.zeros(1000, dtype=np.int16))
    assert transcript.language == UNKNOWN_LANGUAGE


async def test_transcribe_language_attr_missing_becomes_unknown() -> None:
    """``info`` object with no ``language`` attribute at all -> sentinel."""

    class _BareInfo:
        # Deliberately no ``language`` attribute.
        duration = 0.25

    info_obj = _BareInfo()
    model = _FakeWhisperModel(
        segments=[_FakeSegment(text="hi", avg_logprob=-0.1)],
        info=info_obj,  # type: ignore[arg-type]
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    transcript = await transcriber.transcribe(np.zeros(1000, dtype=np.int16))
    assert transcript.language == UNKNOWN_LANGUAGE


# ---------------------------------------------------------------------
# All-non-finite avg_logprob warning
# ---------------------------------------------------------------------


async def test_transcribe_all_nonfinite_logprobs_warns_and_zero_confidence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-empty segments whose avg_logprob is all NaN/inf -> warn + 0.0 conf."""
    model = _FakeWhisperModel(
        segments=[
            _FakeSegment(text="hi", avg_logprob=float("nan")),
            _FakeSegment(text="there", avg_logprob=float("-inf")),
        ],
        info=_FakeInfo(language="en", duration=0.5),
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    with caplog.at_level(logging.WARNING, logger="toybox.audio.stt"):
        transcript = await transcriber.transcribe(np.zeros(1000, dtype=np.int16))

    assert transcript.confidence == 0.0
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("non-finite avg_logprob" in m for m in messages), messages


# ---------------------------------------------------------------------
# close() lifecycle
# ---------------------------------------------------------------------


async def test_close_drops_model_and_rejects_further_calls() -> None:
    model = _FakeWhisperModel(
        segments=[_FakeSegment(text="hi", avg_logprob=-0.1)],
        info=_FakeInfo(language="en", duration=0.1),
    )
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    # Force lazy load before close so we can assert it's dropped.
    transcriber._ensure_model()  # noqa: SLF001 -- test seam
    assert transcriber._model is model  # noqa: SLF001 -- test seam

    await transcriber.close()
    assert transcriber._model is None  # noqa: SLF001 -- test seam

    with pytest.raises(RuntimeError, match="closed"):
        await transcriber.transcribe(np.zeros(1000, dtype=np.int16))

    # Idempotent.
    await transcriber.close()


# ---------------------------------------------------------------------
# WhisperModel factory wiring (download_root + compute_type)
# ---------------------------------------------------------------------


def test_factory_receives_correct_kwargs_for_cpu(tmp_path: Path) -> None:
    model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    calls: list[dict[str, Any]] = []
    factory = _scripted_factory([model], calls)

    transcriber = WhisperTranscriber(
        device="cpu",
        cache_dir=tmp_path / "models",
        model_factory=factory,
    )
    transcriber._ensure_model()  # noqa: SLF001 -- test seam

    assert len(calls) == 1
    args = calls[0]
    assert args["device"] == "cpu"
    assert args["compute_type"] == "int8"
    assert args["download_root"] == str(tmp_path / "models")


def test_factory_receives_correct_kwargs_for_cuda(tmp_path: Path) -> None:
    cuda_model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    calls: list[dict[str, Any]] = []
    factory = _scripted_factory([cuda_model], calls)
    # No explicit device -> probes CUDA. Factory succeeds, so we stay on CUDA.
    transcriber = WhisperTranscriber(
        cache_dir=tmp_path / "models", model_factory=factory
    )
    assert transcriber.device == "cuda"
    assert calls[0]["device"] == "cuda"
    assert calls[0]["compute_type"] == "float16"
    assert calls[0]["download_root"] == str(tmp_path / "models")


def test_explicit_compute_type_overrides_default(tmp_path: Path) -> None:
    """``compute_type=`` constructor arg wins over per-device defaults."""
    model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    calls: list[dict[str, Any]] = []
    factory = _scripted_factory([model], calls)
    transcriber = WhisperTranscriber(
        device="cpu",
        cache_dir=tmp_path / "models",
        model_factory=factory,
        compute_type="float32",
    )
    transcriber._ensure_model()  # noqa: SLF001 -- test seam
    assert calls[0]["compute_type"] == "float32"


# ---------------------------------------------------------------------
# Startup INFO log
# ---------------------------------------------------------------------


def test_constructor_logs_resolved_device_and_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    with caplog.at_level(logging.INFO, logger="toybox.audio.stt"):
        WhisperTranscriber(device="cpu", model_factory=_factory(model))

    info_messages = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO and r.name == "toybox.audio.stt"
    ]
    matching = [m for m in info_messages if "device='cpu'" in m and "model='small'" in m]
    assert len(matching) == 1, info_messages


# ---------------------------------------------------------------------
# CUDA-failure log includes exc_info
# ---------------------------------------------------------------------


def test_cuda_failure_warning_includes_exc_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cpu_model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    calls: list[dict[str, Any]] = []
    factory = _scripted_factory([cpu_model], calls, fail_on_devices={"cuda"})

    with caplog.at_level(logging.WARNING, logger="toybox.audio.stt"):
        WhisperTranscriber(model_factory=factory)

    cuda_records = [
        r for r in caplog.records if "CUDA whisper init failed" in r.getMessage()
    ]
    assert len(cuda_records) == 1
    record = cuda_records[0]
    # ``exc_info=True`` on the warning -> the LogRecord carries the
    # original exception so the formatter renders the traceback.
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError


# ---------------------------------------------------------------------
# Env-var whitespace handling (extra: real value with surrounding spaces)
# ---------------------------------------------------------------------


def test_model_env_real_value_with_surrounding_whitespace_is_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WHISPER_MODEL_ENV, "  tiny  ")
    model = _FakeWhisperModel(segments=[], info=_FakeInfo())
    transcriber = WhisperTranscriber(device="cpu", model_factory=_factory(model))
    transcriber._ensure_model()  # noqa: SLF001 -- test seam
    assert model.construct_args["model_size_or_path"] == "tiny"


# ---------------------------------------------------------------------
# _download_model is idempotent (cache dir mkdir uses exist_ok=True)
# ---------------------------------------------------------------------


def test_download_model_is_idempotent(tmp_path: Path) -> None:
    cache_dir = tmp_path / "models"
    cache_dir.mkdir()
    # Pre-create a sentinel file so we can assert nothing wipes it.
    sentinel = cache_dir / "already_here.txt"
    sentinel.write_text("preserve me")

    calls: list[dict[str, Any]] = []
    factory = _scripted_factory(
        [
            _FakeWhisperModel(segments=[], info=_FakeInfo()),
            _FakeWhisperModel(segments=[], info=_FakeInfo()),
        ],
        calls,
    )

    _download_model(model_size="tiny", cache_dir=cache_dir, model_factory=factory)
    _download_model(model_size="tiny", cache_dir=cache_dir, model_factory=factory)

    assert len(calls) == 2
    assert sentinel.read_text() == "preserve me"


# ---------------------------------------------------------------------
# main() CLI entrypoint
# ---------------------------------------------------------------------


def test_main_help_returns_zero_and_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main([])`` prints help and exits cleanly."""
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert "--download" in captured.out


def test_main_download_calls_download_helper(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main(['--download'])`` invokes ``_download_model`` once and returns 0."""
    call_count = 0

    def spy(**_kwargs: Any) -> str:
        nonlocal call_count
        call_count += 1
        return "tiny"

    monkeypatch.setattr("toybox.audio.stt._download_model", spy)
    rc = main(["--download"])
    captured = capsys.readouterr()

    assert rc == 0
    assert call_count == 1
    assert "ready in" in captured.out


def test_main_download_failure_returns_one_and_includes_context(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``_download_model`` raising -> rc=1, stderr includes model + cache + exc class."""

    def boom(**_kwargs: Any) -> str:
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr("toybox.audio.stt._download_model", boom)
    monkeypatch.setenv(WHISPER_MODEL_ENV, "tiny")

    rc = main(["--download"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "download failed" in captured.err
    assert "tiny" in captured.err
    assert str(DEFAULT_CACHE_DIR) in captured.err
    assert "RuntimeError" in captured.err


# ---------------------------------------------------------------------
# Transcript pydantic: language defaults to UNKNOWN
# ---------------------------------------------------------------------


def test_transcript_language_defaults_to_unknown() -> None:
    """Omitting ``language=`` leaves the field at UNKNOWN_LANGUAGE."""
    t = Transcript(text="hi", confidence=0.5, duration_ms=100)
    assert t.language == UNKNOWN_LANGUAGE


def test_transcript_language_rejects_empty_string() -> None:
    """``language=""`` is rejected (min_length=1)."""
    with pytest.raises(ValidationError):
        Transcript(text="hi", confidence=0.5, language="", duration_ms=100)
