"""Unit coverage for ``scripts/f5_compute_manifest.py``.

Phase P added an ``EXPECTED_RELATIVE_PATHS`` list so the IP-Adapter weights
surface in the manifest as ``{"status": "missing"}`` rather than silently
disappearing when the operator hasn't run the download script yet. The
script lives outside the toybox package, so we load it through
``importlib.util.spec_from_file_location`` rather than a normal import.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "f5_compute_manifest.py"


def _load_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("_f5_compute_manifest", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def manifest_module() -> Iterator[types.ModuleType]:
    module = _load_module()
    yield module
    sys.modules.pop("_f5_compute_manifest", None)


def test_missing_expected_files_marked_status_missing(
    tmp_path: Path, manifest_module: types.ModuleType
) -> None:
    """Empty model dir: every EXPECTED path lands in the entries with status=missing."""
    entries = manifest_module.compute_entries(tmp_path)

    for expected_rel in manifest_module.EXPECTED_RELATIVE_PATHS:
        assert entries[expected_rel] == {"status": "missing"}


def test_present_expected_file_hashed_not_missing(
    tmp_path: Path, manifest_module: types.ModuleType
) -> None:
    """When the expected file exists, it is hashed normally — no status:missing fallback."""
    ipa_rel = "ip_adapter/models/ip-adapter-plus_sd15.bin"
    ipa_path = tmp_path / ipa_rel
    ipa_path.parent.mkdir(parents=True)
    payload = b"fake-ipa-weights" * 100
    ipa_path.write_bytes(payload)

    entries = manifest_module.compute_entries(tmp_path)

    entry = entries[ipa_rel]
    assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
    assert entry["size_bytes"] == len(payload)
    assert "status" not in entry


def test_unrelated_safetensors_still_hashed(
    tmp_path: Path, manifest_module: types.ModuleType
) -> None:
    """Pre-existing F.5 behaviour preserved: arbitrary .safetensors / .bin / .onnx are walked."""
    bin_path = tmp_path / "sd15" / "base" / "unet" / "diffusion_pytorch_model.fp16.safetensors"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_bytes(b"x" * 2048)

    entries = manifest_module.compute_entries(tmp_path)

    assert "sd15/base/unet/diffusion_pytorch_model.fp16.safetensors" in entries
