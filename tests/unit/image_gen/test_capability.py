"""Unit coverage for :func:`toybox.image_gen.capability.is_image_gen_capable`.

Each branch of the four-way capability gate is exercised. Torch is
NOT installed in the test venv (it's an optional extra), so we
monkeypatch the ``_probe_cuda_and_vram`` helper directly rather
than poking at ``torch.cuda``. This mirrors the
``tests/integration/test_capability_*.py`` pattern of asserting
behaviour through monkeypatched seams instead of running real
network probes.

The required-checkpoint set is mode-aware via
``TOYBOX_IMAGE_GEN_CARTOON_MODE``; tests cover both ``checkpoint``
(default) and ``lora`` branches.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from toybox.image_gen import capability
from toybox.image_gen.capability import (
    CARTOON_MODE_ENV,
    ENABLED_ENV,
    MODEL_DIR_ENV,
    CapabilityReason,
    ImageGenBreaker,
    _probe_cuda_and_vram,
    _required_checkpoints,
    get_image_gen_breaker,
    is_image_gen_capable,
    reset_image_gen_breaker_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_breaker_singleton() -> None:
    """Ensure the breaker singleton starts fresh for each test."""
    reset_image_gen_breaker_for_tests()


def _seed_required(model_dir: Path) -> None:
    """Create empty placeholders for whatever the current mode requires."""
    for relative in _required_checkpoints():
        target = model_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")


def test_disabled_via_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENABLED_ENV, "false")
    # Ensure we'd otherwise pass — but env-disable should short-circuit.
    monkeypatch.setattr(capability, "_probe_cuda_and_vram", lambda: (True, 99.0))
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))
    _seed_required(tmp_path)

    capable, reason_enum, detail = is_image_gen_capable()

    assert capable is False
    assert reason_enum is CapabilityReason.env_disabled
    assert "TOYBOX_IMAGE_GEN_ENABLED" in detail


def test_no_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.setattr(capability, "_probe_cuda_and_vram", lambda: (False, 0.0))

    capable, reason_enum, detail = is_image_gen_capable()

    assert capable is False
    assert reason_enum is CapabilityReason.no_cuda
    assert detail == "CUDA not available"


def test_probe_returns_false_when_torch_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_probe_cuda_and_vram`` must swallow ``ImportError`` from torch.

    The integration test in ``tests/integration/test_image_gen_real_gpu.py``
    calls :func:`is_image_gen_capable` at collection time via the
    ``skipif`` decorator. If torch isn't installed and the probe leaked
    ``ImportError``, pytest collection would crash on a no-torch host.

    Force the import to fail by sentinel-blanking ``sys.modules["torch"]``
    and assert the helper returns the canonical ``(False, 0.0)``.
    """
    # ``sys.modules[name] = None`` makes a subsequent ``import name`` raise
    # ImportError. ``monkeypatch.setitem`` undoes the change at teardown
    # so we don't pollute the rest of the suite.
    monkeypatch.setitem(sys.modules, "torch", None)
    cuda_available, free_gb = _probe_cuda_and_vram()
    assert cuda_available is False
    assert free_gb == 0.0


def test_low_vram(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    # Free VRAM 4 GB, floor at default 12 GB → low-vram branch wins.
    monkeypatch.setattr(capability, "_probe_cuda_and_vram", lambda: (True, 4.0))
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))
    _seed_required(tmp_path)

    capable, reason_enum, detail = is_image_gen_capable()

    assert capable is False
    assert reason_enum is CapabilityReason.low_vram
    assert "VRAM" in detail
    assert "floor" in detail


def test_missing_checkpoints(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.setattr(capability, "_probe_cuda_and_vram", lambda: (True, 99.0))
    # Empty model dir → all checkpoints missing.
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))

    capable, reason_enum, detail = is_image_gen_capable()

    assert capable is False
    assert reason_enum is CapabilityReason.missing_checkpoints
    assert detail.startswith("checkpoints missing:")


def test_capable_path_checkpoint_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """All four gates pass under default cartoon-mode=checkpoint."""
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.delenv(CARTOON_MODE_ENV, raising=False)  # default = checkpoint
    monkeypatch.setattr(capability, "_probe_cuda_and_vram", lambda: (True, 16.0))
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))
    _seed_required(tmp_path)

    capable, reason_enum, detail = is_image_gen_capable()

    assert capable is True
    assert reason_enum is CapabilityReason.capable
    assert detail == "capable"


def test_capable_path_lora_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All four gates pass under cartoon-mode=lora."""
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.setenv(CARTOON_MODE_ENV, "lora")
    monkeypatch.setattr(capability, "_probe_cuda_and_vram", lambda: (True, 16.0))
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))
    _seed_required(tmp_path)

    capable, reason_enum, _detail = is_image_gen_capable()

    assert capable is True
    assert reason_enum is CapabilityReason.capable


def test_required_checkpoints_checkpoint_mode_lists_cartoon_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mode=checkpoint requires the cartoon_checkpoint UNet, not SD 1.5 base."""
    monkeypatch.setenv(CARTOON_MODE_ENV, "checkpoint")
    required = _required_checkpoints()
    joined = "\n".join(required)
    assert "cartoon_checkpoint/model_index.json" in joined
    assert "cartoon_checkpoint/unet/diffusion_pytorch_model.fp16.safetensors" in joined
    assert "sd15/lcm_lora/pytorch_lora_weights.safetensors" in joined
    assert "bg_remove/u2net.onnx" in joined
    assert "sd15/base/" not in joined
    assert "cartoon_lora/" not in joined


def test_required_checkpoints_lora_mode_lists_base_and_cartoon_lora(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mode=lora requires SD 1.5 base + cartoon_lora, not cartoon_checkpoint."""
    monkeypatch.setenv(CARTOON_MODE_ENV, "lora")
    required = _required_checkpoints()
    joined = "\n".join(required)
    assert "sd15/base/model_index.json" in joined
    assert "sd15/base/unet/diffusion_pytorch_model.fp16.safetensors" in joined
    assert "cartoon_lora/pytorch_lora_weights.safetensors" in joined
    assert "sd15/lcm_lora/pytorch_lora_weights.safetensors" in joined
    assert "bg_remove/u2net.onnx" in joined
    assert "cartoon_checkpoint/" not in joined


def test_required_checkpoints_unknown_mode_returns_base_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown mode → base set only; pipeline.py rejects the mode at load."""
    monkeypatch.setenv(CARTOON_MODE_ENV, "garbage")
    required = _required_checkpoints()
    assert "sd15/lcm_lora/pytorch_lora_weights.safetensors" in required
    assert "bg_remove/u2net.onnx" in required
    joined = "\n".join(required)
    assert "cartoon_checkpoint/" not in joined
    assert "cartoon_lora/" not in joined
    assert "sd15/base/" not in joined


def test_missing_checkpoints_lora_mode_reports_cartoon_lora(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lora mode missing the cartoon_lora file → missing_checkpoints reason."""
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.setenv(CARTOON_MODE_ENV, "lora")
    monkeypatch.setattr(capability, "_probe_cuda_and_vram", lambda: (True, 99.0))
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))
    # Seed everything except cartoon_lora.
    for relative in _required_checkpoints():
        if relative.startswith("cartoon_lora/"):
            continue
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    capable, reason_enum, detail = is_image_gen_capable()

    assert capable is False
    assert reason_enum is CapabilityReason.missing_checkpoints
    assert "cartoon_lora" in detail


_IPA_PATHS = (
    "ip_adapter/models/ip-adapter-plus_sd15.bin",
    "ip_adapter/models/image_encoder/model.safetensors",
)


def test_required_checkpoints_base_includes_ipa_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IPA + CLIP image encoder live in the base set (mode-independent).

    Both ``checkpoint`` and ``lora`` modes load IPA for toy-image
    conditioning, so every mode (including the unknown-mode fallthrough
    that returns the base only) must include the two IPA paths.
    """
    for mode_value in ("checkpoint", "lora", "garbage"):
        monkeypatch.setenv(CARTOON_MODE_ENV, mode_value)
        required = _required_checkpoints()
        for ipa_path in _IPA_PATHS:
            assert ipa_path in required, (
                f"mode={mode_value!r} missing IPA path {ipa_path!r}"
            )


def test_required_checkpoints_excludes_ipa_bin_image_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the .safetensors encoder is pinned; .bin format must not be.

    diffusers prefers .safetensors when both formats are present in the
    encoder dir; pinning both would force the operator to keep
    duplicate weight files on disk. Codifies the choice so a future
    refactor doesn't accidentally add ``pytorch_model.bin``.
    """
    monkeypatch.setenv(CARTOON_MODE_ENV, "checkpoint")
    required = _required_checkpoints()
    assert "ip_adapter/models/image_encoder/pytorch_model.bin" not in required
    assert "ip_adapter/models/image_encoder/config.json" not in required


def _seed_required_except_ipa(model_dir: Path) -> None:
    """Seed everything the current mode requires *except* the IPA files.

    Isolates IPA-absent as the sole cause of a missing-checkpoints
    verdict — all other per-mode prerequisites are present.
    """
    for relative in _required_checkpoints():
        if relative in _IPA_PATHS:
            continue
        target = model_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")


@pytest.mark.parametrize("mode", ["checkpoint", "lora"])
def test_missing_ipa_flips_capable_to_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """When only the IPA files are absent, the gate reports missing_checkpoints.

    Runs under both cartoon modes — IPA is required in both, so the
    gate must flip False regardless of how the rest of the checkpoint
    set is shaped.
    """
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.setenv(CARTOON_MODE_ENV, mode)
    monkeypatch.setattr(capability, "_probe_cuda_and_vram", lambda: (True, 99.0))
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))
    _seed_required_except_ipa(tmp_path)

    capable, reason_enum, detail = is_image_gen_capable()

    assert capable is False
    assert reason_enum is CapabilityReason.missing_checkpoints
    # The detail string lists up to 3 missing relative paths; with
    # exactly the two IPA files missing, both must appear verbatim.
    for ipa_path in _IPA_PATHS:
        assert ipa_path in detail, f"detail string missing {ipa_path!r}: {detail!r}"


@pytest.mark.parametrize("mode", ["checkpoint", "lora"])
def test_creating_ipa_files_flips_capable_to_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """Seeding the IPA files alongside the rest of the set opens the gate.

    Pairs with :func:`test_missing_ipa_flips_capable_to_false`: the only
    delta between the two states is the IPA files' presence on disk, so
    a True verdict here proves IPA is the gating factor.
    """
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.setenv(CARTOON_MODE_ENV, mode)
    monkeypatch.setattr(capability, "_probe_cuda_and_vram", lambda: (True, 99.0))
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))
    _seed_required_except_ipa(tmp_path)
    # Now add the IPA files.
    for ipa_path in _IPA_PATHS:
        target = tmp_path / ipa_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    capable, reason_enum, detail = is_image_gen_capable()

    assert capable is True, f"detail={detail!r}"
    assert reason_enum is CapabilityReason.capable


def test_breaker_records_failures_and_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    """3 consecutive failures → breaker open."""
    breaker = ImageGenBreaker(threshold=3, cooldown_sec=300.0)
    assert breaker.is_open() is False
    breaker.check_and_record(success=False)
    breaker.check_and_record(success=False)
    assert breaker.is_open() is False  # threshold not yet hit
    breaker.check_and_record(success=False)
    assert breaker.is_open() is True


def test_breaker_success_resets_failure_counter() -> None:
    breaker = ImageGenBreaker(threshold=3, cooldown_sec=300.0)
    breaker.check_and_record(success=False)
    breaker.check_and_record(success=False)
    breaker.check_and_record(success=True)
    breaker.check_and_record(success=False)
    breaker.check_and_record(success=False)
    # Counter reset on success → still closed after only 2 fresh fails.
    assert breaker.is_open() is False


def test_breaker_singleton_returns_same_instance() -> None:
    first = get_image_gen_breaker()
    second = get_image_gen_breaker()
    assert first is second


def test_breaker_independent_of_claude_breaker() -> None:
    """The image-gen breaker is its own instance; tripping it doesn't
    affect the Claude / local breakers (separate singleton trees)."""
    from toybox.ai.breaker import CircuitBreaker

    image_breaker = ImageGenBreaker(threshold=1, cooldown_sec=300.0)
    image_breaker.check_and_record(success=False)
    assert image_breaker.is_open() is True
    # A freshly-constructed Claude breaker is unaffected — different
    # state, different counters.
    claude_breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0)
    assert claude_breaker.is_open() is False
