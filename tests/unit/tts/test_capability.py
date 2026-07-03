"""Phase Z Z3 — :func:`toybox.tts.is_tts_capable` probe branches.

The worktree/CI environment intentionally does NOT install the ``tts``
extra, so the "no deps" branch is exercised for real. Branches that
require installed deps are exercised by monkeypatching the module's
``_deps_importable`` seam — the probe's decision logic is what's under
test, not ``importlib``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toybox.tts import is_tts_capable
from toybox.tts.engine import (
    DATA_DIR_ENV,
    MODEL_FILENAME,
    STUB_ENV,
    VOICES_FILENAME,
    model_dir,
)


@pytest.fixture
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the data root at an empty tmp dir; no stub env."""
    monkeypatch.delenv(STUB_ENV, raising=False)
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    return tmp_path


def test_not_capable_without_stub_or_model_files(isolated_data_dir: Path) -> None:
    """Fresh host: no stub, no model files (and in CI no deps) → False."""
    assert is_tts_capable() is False


def test_capable_with_stub_env(monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path) -> None:
    monkeypatch.setenv(STUB_ENV, "1")
    assert is_tts_capable() is True


def test_not_capable_when_deps_present_but_files_missing(
    monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    monkeypatch.setattr("toybox.tts.engine._deps_importable", lambda: True)
    assert is_tts_capable() is False


def test_not_capable_when_only_one_model_file_present(
    monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    monkeypatch.setattr("toybox.tts.engine._deps_importable", lambda: True)
    target = model_dir()
    target.mkdir(parents=True)
    (target / MODEL_FILENAME).write_bytes(b"onnx")
    assert is_tts_capable() is False


def test_capable_when_deps_and_both_model_files_present(
    monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    monkeypatch.setattr("toybox.tts.engine._deps_importable", lambda: True)
    target = model_dir()
    target.mkdir(parents=True)
    (target / MODEL_FILENAME).write_bytes(b"onnx")
    (target / VOICES_FILENAME).write_bytes(b"voices")
    assert is_tts_capable() is True


def test_not_capable_when_deps_missing_even_with_files(
    monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    monkeypatch.setattr("toybox.tts.engine._deps_importable", lambda: False)
    target = model_dir()
    target.mkdir(parents=True)
    (target / MODEL_FILENAME).write_bytes(b"onnx")
    (target / VOICES_FILENAME).write_bytes(b"voices")
    assert is_tts_capable() is False


def test_model_dir_respects_toybox_data_dir(isolated_data_dir: Path) -> None:
    assert model_dir() == isolated_data_dir / "models" / "tts"
