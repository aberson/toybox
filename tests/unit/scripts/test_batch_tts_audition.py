"""Unit tests for scripts/batch_tts_audition.py (Phase Z Z7-prep audition CLI).

Render-path tests run with ``TOYBOX_TTS_STUB=1`` so ``synthesize`` returns
tiny deterministic-but-valid WAVs — no model files, no ``tts`` extra. The
dry-run contract ("lists targets + exits 0 WITHOUT the tts extra, no heavy
imports, no synthesis") is pinned with a clean-interpreter subprocess, the
same shape as ``tests/unit/tts/test_download_cli.py`` — the worktree/CI venv
genuinely lacks the extra, so an eager heavy import or an accidental synth
call fails the test for real.

Expected sample count = the hardcoded voice sweep + one file per library
persona JSON. Persona samples are NEVER deduped into the sweep files (the
script's documented decision): a persona whose cast voice also appears in
the sweep still gets its own ``persona_<id>_<voice>.wav``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from toybox.personas.loader import LIBRARY_DIR
from toybox.tts.engine import DATA_DIR_ENV, DEFAULT_NEURAL_VOICE, STUB_ENV, synthesize

# Load the standalone script as a module (scripts/ is not an importable package).
# Registering it in sys.modules before exec is required for its @dataclass:
# dataclasses resolves the owning module via sys.modules at class-creation time.
_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "batch_tts_audition.py"
_spec = importlib.util.spec_from_file_location("batch_tts_audition", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
batch_tts_audition = importlib.util.module_from_spec(_spec)
sys.modules["batch_tts_audition"] = batch_tts_audition
_spec.loader.exec_module(batch_tts_audition)


def _library_persona_castings() -> list[tuple[str, str]]:
    """Test oracle: (persona_id, expected voice) straight from the library JSONs."""
    castings: list[tuple[str, str]] = []
    for path in sorted(LIBRARY_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        persona = json.loads(path.read_text(encoding="utf-8"))
        voice = (persona.get("voice_profile") or {}).get("neural_voice") or DEFAULT_NEURAL_VOICE
        castings.append((persona["id"], voice))
    return castings


_CASTINGS = _library_persona_castings()
_EXPECTED_TOTAL = len(batch_tts_audition.KOKORO_EN_VOICES) + len(_CASTINGS)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(STUB_ENV, "1")
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "data-root"))


def _assert_valid_wav(path: Path) -> None:
    assert path.exists() and path.stat().st_size > 0, path
    with wave.open(io.BytesIO(path.read_bytes()), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getnframes() > 0


def test_sanity_library_has_personas_and_castings_in_sweep() -> None:
    """The step brief's expectations: 4 personas, castings drawn from the sweep."""
    assert len(_CASTINGS) == 4
    # Literal change-detector: the kokoro-onnx model-files-v1.0 voices bin
    # ships exactly 28 English voices. Update this literal on a model rev
    # that adds/drops voices (then re-run the audition for the operator).
    assert len(batch_tts_audition.KOKORO_EN_VOICES) == 28
    for _persona_id, voice in _CASTINGS:
        assert voice in batch_tts_audition.KOKORO_EN_VOICES


def test_dry_run_lists_targets_and_exits_zero_in_clean_interpreter(tmp_path: Path) -> None:
    """--dry-run works without the tts extra, prints every target, touches nothing."""
    data_root = tmp_path / "clean-data-root"
    env = dict(os.environ)
    env[DATA_DIR_ENV] = str(data_root)
    # No stub either: dry-run must not synthesize at all.
    env.pop(STUB_ENV, None)
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for voice in batch_tts_audition.KOKORO_EN_VOICES:
        assert f"{voice}.wav" in result.stdout
    for persona_id, voice in _CASTINGS:
        assert f"persona_{persona_id}_{voice}.wav" in result.stdout
    assert "would render" in result.stdout
    # Dry-run must not touch the filesystem — not even a mkdir.
    assert not data_root.exists()


def test_help_exits_zero_in_clean_interpreter() -> None:
    env = dict(os.environ)
    env.pop(STUB_ENV, None)
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--dry-run" in result.stdout
    assert "--force" in result.stdout


def test_dry_run_writes_nothing_in_process(tmp_path: Path) -> None:
    out_dir = tmp_path / "audition"
    rc = batch_tts_audition.run(["--dry-run", "--out-dir", str(out_dir)])
    assert rc == 0
    assert not out_dir.exists()


def test_stub_run_writes_every_sample(tmp_path: Path) -> None:
    out_dir = tmp_path / "audition"
    rc = batch_tts_audition.run(["--out-dir", str(out_dir)])
    assert rc == 0
    wavs = list(out_dir.glob("*.wav"))
    assert len(wavs) == _EXPECTED_TOTAL
    for voice in batch_tts_audition.KOKORO_EN_VOICES:
        _assert_valid_wav(out_dir / f"{voice}.wav")
    for persona_id, voice in _CASTINGS:
        _assert_valid_wav(out_dir / f"persona_{persona_id}_{voice}.wav")


def test_default_out_dir_resolves_under_toybox_data_dir(tmp_path: Path) -> None:
    """Without --out-dir, samples land at <TOYBOX_DATA_DIR>/tts/audition."""
    rc = batch_tts_audition.run([])
    assert rc == 0
    default_dir = tmp_path / "data-root" / "tts" / "audition"
    assert len(list(default_dir.glob("*.wav"))) == _EXPECTED_TOTAL


def test_manifest_lists_persona_castings_before_voice_sweep(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "audition"
    rc = batch_tts_audition.run(["--out-dir", str(out_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    persona_section = out.index("Persona castings")
    sweep_section = out.index("Voice sweep")
    assert persona_section < sweep_section
    # Every persona line sits in the persona section, with its file path.
    for persona_id, voice in _CASTINGS:
        line_pos = out.index(f"persona_{persona_id}_{voice}.wav")
        assert persona_section < line_pos < sweep_section
    assert str(out_dir / f"{batch_tts_audition.KOKORO_EN_VOICES[0]}.wav") in out


def test_skips_existing_without_force(tmp_path: Path) -> None:
    out_dir = tmp_path / "audition"
    out_dir.mkdir()
    sentinel = out_dir / "af_heart.wav"
    sentinel.write_bytes(b"PRE-EXISTING")
    rc = batch_tts_audition.run(["--out-dir", str(out_dir)])
    assert rc == 0
    assert sentinel.read_bytes() == b"PRE-EXISTING"
    # Everything else still rendered.
    assert len(list(out_dir.glob("*.wav"))) == _EXPECTED_TOTAL


def test_force_rerenders_existing(tmp_path: Path) -> None:
    out_dir = tmp_path / "audition"
    out_dir.mkdir()
    sentinel = out_dir / "af_heart.wav"
    sentinel.write_bytes(b"PRE-EXISTING")
    rc = batch_tts_audition.run(["--out-dir", str(out_dir), "--force"])
    assert rc == 0
    assert sentinel.read_bytes() != b"PRE-EXISTING"
    _assert_valid_wav(sentinel)


def test_one_failing_voice_continues_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Per-item isolation: one broken voice logs an ERROR with traceback and
    the batch continues; exit stays 0 because the audition still produced samples."""

    def _flaky(text: str, voice: str) -> bytes:
        if voice == "am_santa":  # a sweep-only voice (no persona casts it)
            raise RuntimeError("simulated kokoro failure")
        return synthesize(text, voice)

    monkeypatch.setattr("toybox.tts.engine.synthesize", _flaky)
    out_dir = tmp_path / "audition"
    rc = batch_tts_audition.run(["--out-dir", str(out_dir)])
    assert rc == 0
    assert not (out_dir / "am_santa.wav").exists()
    assert len(list(out_dir.glob("*.wav"))) == _EXPECTED_TOTAL - 1
    out = capsys.readouterr().out
    assert "[FAILED]" in out  # manifest marks the broken item


def test_all_failures_exit_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(text: str, voice: str) -> bytes:
        raise RuntimeError("simulated total engine failure")

    monkeypatch.setattr("toybox.tts.engine.synthesize", _boom)
    out_dir = tmp_path / "audition"
    rc = batch_tts_audition.run(["--out-dir", str(out_dir)])
    assert rc == 1
    assert list(out_dir.glob("*.wav")) == []


def test_persona_without_neural_voice_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A casting-less persona still gets a sample, in DEFAULT_NEURAL_VOICE."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "custom.json").write_text(
        json.dumps({"id": "custom", "voice_profile": {"rate": 1.0, "pitch": 1.0}}),
        encoding="utf-8",
    )
    (library / "_schema.json").write_text("{}", encoding="utf-8")  # must be skipped
    monkeypatch.setattr("toybox.personas.loader.LIBRARY_DIR", library)
    out_dir = tmp_path / "audition"
    rc = batch_tts_audition.run(["--out-dir", str(out_dir)])
    assert rc == 0
    _assert_valid_wav(out_dir / f"persona_custom_{DEFAULT_NEURAL_VOICE}.wav")
    assert len(list(out_dir.glob("persona_*.wav"))) == 1


def test_persona_with_unsafe_neural_voice_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traversal-shaped neural_voice never becomes a path segment: the
    persona is auditioned under DEFAULT_NEURAL_VOICE and nothing escapes
    the audition dir."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "sneaky.json").write_text(
        json.dumps(
            {
                "id": "sneaky",
                "voice_profile": {"rate": 1.0, "pitch": 1.0, "neural_voice": "../evil"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("toybox.personas.loader.LIBRARY_DIR", library)
    out_dir = tmp_path / "audition"
    rc = batch_tts_audition.run(["--out-dir", str(out_dir)])
    assert rc == 0
    _assert_valid_wav(out_dir / f"persona_sneaky_{DEFAULT_NEURAL_VOICE}.wav")
    assert len(list(out_dir.glob("persona_*.wav"))) == 1
    # The raw value never reached the filesystem — no traversal artifact
    # anywhere under the test tree, and no synthesize call with it either.
    assert list(tmp_path.rglob("evil.wav")) == []
    assert list(tmp_path.rglob("*evil*")) == []


def test_persona_with_unsafe_id_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A persona whose id cannot be a safe filename segment is skipped
    entirely; the voice sweep is unaffected."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "bad.json").write_text(
        json.dumps({"id": "Bad Id!", "voice_profile": {"rate": 1.0, "pitch": 1.0}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("toybox.personas.loader.LIBRARY_DIR", library)
    out_dir = tmp_path / "audition"
    rc = batch_tts_audition.run(["--out-dir", str(out_dir)])
    assert rc == 0
    assert list(out_dir.glob("persona_*.wav")) == []
    assert len(list(out_dir.glob("*.wav"))) == len(batch_tts_audition.KOKORO_EN_VOICES)


def test_malformed_persona_json_is_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "broken.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("toybox.personas.loader.LIBRARY_DIR", library)
    out_dir = tmp_path / "audition"
    rc = batch_tts_audition.run(["--out-dir", str(out_dir)])
    assert rc == 0
    # No persona samples, but the full sweep still rendered.
    assert list(out_dir.glob("persona_*.wav")) == []
    assert len(list(out_dir.glob("*.wav"))) == len(batch_tts_audition.KOKORO_EN_VOICES)
