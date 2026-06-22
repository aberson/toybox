"""Unit tests for scripts/batch_scenes.py (Phase Y scene-library batch CLI).

All tests run with TOYBOX_IMAGE_GEN_STUB=1 so the pipeline returns deterministic
placeholder PNGs — no GPU required. This exercises the CLI wiring (scene
enumeration, skip/force, dry-run, single-scene restriction, output paths)
end-to-end through ``pipeline.generate_scene``'s stub branch.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest
from PIL import Image

from toybox.activities.scene_catalog import SCENE_IDS

# Load the standalone script as a module (scripts/ is not an importable package).
_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "batch_scenes.py"
_spec = importlib.util.spec_from_file_location("batch_scenes", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
batch_scenes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(batch_scenes)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOYBOX_IMAGE_GEN_STUB", "1")


def _assert_opaque_png(path: Path) -> None:
    assert path.exists() and path.stat().st_size > 0, path
    img = Image.open(io.BytesIO(path.read_bytes()))
    assert img.format == "PNG"
    assert img.mode == "RGB", f"scene PNG should be opaque RGB, got {img.mode}"


def test_renders_every_scene(tmp_path: Path) -> None:
    rc = batch_scenes.run(["--out-dir", str(tmp_path)])
    assert rc == 0
    written = sorted(p.stem for p in tmp_path.glob("*.png"))
    assert written == sorted(SCENE_IDS)
    for scene in SCENE_IDS:
        _assert_opaque_png(tmp_path / f"{scene}.png")


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    rc = batch_scenes.run(["--dry-run", "--out-dir", str(tmp_path)])
    assert rc == 0
    assert list(tmp_path.glob("*.png")) == []


def test_single_scene_restricts(tmp_path: Path) -> None:
    rc = batch_scenes.run(["--scene", "lab", "--out-dir", str(tmp_path)])
    assert rc == 0
    assert [p.name for p in tmp_path.glob("*.png")] == ["lab.png"]
    _assert_opaque_png(tmp_path / "lab.png")


def test_unknown_scene_returns_error(tmp_path: Path) -> None:
    rc = batch_scenes.run(["--scene", "atlantis", "--out-dir", str(tmp_path)])
    assert rc == 2
    assert list(tmp_path.glob("*.png")) == []


def test_skips_existing_without_force(tmp_path: Path) -> None:
    sentinel = tmp_path / "lab.png"
    sentinel.write_bytes(b"PRE-EXISTING")
    rc = batch_scenes.run(["--out-dir", str(tmp_path)])
    assert rc == 0
    # lab.png was present → left untouched; the rest were rendered.
    assert sentinel.read_bytes() == b"PRE-EXISTING"
    for scene in SCENE_IDS:
        if scene != "lab":
            _assert_opaque_png(tmp_path / f"{scene}.png")


def test_force_overwrites_existing(tmp_path: Path) -> None:
    sentinel = tmp_path / "lab.png"
    sentinel.write_bytes(b"PRE-EXISTING")
    rc = batch_scenes.run(["--scene", "lab", "--force", "--out-dir", str(tmp_path)])
    assert rc == 0
    assert sentinel.read_bytes() != b"PRE-EXISTING"
    _assert_opaque_png(sentinel)
