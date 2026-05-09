"""Capability gate + per-pipeline breaker for the image-gen subsystem.

Mirrors :mod:`toybox.ai.capability` / :mod:`toybox.ai.breaker`:

* :func:`is_image_gen_capable` — four-branch gate; lazy-imports
  torch only when the env-disable + checkpoint checks pass.
* :class:`ImageGenBreaker` — thin wrapper over
  :class:`toybox.ai.breaker.CircuitBreaker` so the image-gen worker
  has its own breaker independent of Claude / local-LLM. Phase F's
  worker (F4) calls :meth:`check_and_record` after every job; F2
  just ships the class + a module-level singleton.

The capability check intentionally does NOT also run the full
``CLIPVisionModelWithProjection.from_pretrained`` smoke — that's
expensive (~3 GB of weights into RAM) and the manifest sha-check
shipped in F1 already validates checkpoint integrity. A per-file
``Path.exists()`` here is sufficient to distinguish "no checkpoints
at all" from "checkpoints loaded fine"; sha drift is the
manifest's job.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import Final

from ..ai.breaker import CircuitBreaker

_logger = logging.getLogger(__name__)


class CapabilityReason(StrEnum):
    """Branch key for :func:`is_image_gen_capable`'s return tuple.

    Phase F.5-3a: the worker dispatch + REST endpoints branch on this
    enum instead of prefix-matching the human-readable detail string.
    Each member maps 1:1 to one of the four "False" arms of
    :func:`is_image_gen_capable` (plus :attr:`CAPABLE` for the
    success arm):

    * :attr:`CAPABLE` — gate is open; full Tier B pipeline runs.
    * :attr:`ENV_DISABLED` — operator explicitly set
      ``TOYBOX_IMAGE_GEN_ENABLED=false``. Hard-off; no Tier C either.
    * :attr:`NO_CUDA` — torch / CUDA driver not available. Tier C
      composite is the fallback.
    * :attr:`LOW_VRAM` — free VRAM below the configured floor. Tier C
      composite is the fallback.
    * :attr:`MISSING_CHECKPOINTS` — one or more required checkpoint
      files absent on disk. Tier C composite is the fallback.

    The bool component of :func:`is_image_gen_capable`'s return tuple
    stays for backwards compat at call sites that only need yes/no;
    the enum is the dispatch key; the human-readable string is for UI
    display.
    """

    CAPABLE = "capable"
    ENV_DISABLED = "env_disabled"
    NO_CUDA = "no_cuda"
    LOW_VRAM = "low_vram"
    MISSING_CHECKPOINTS = "missing_checkpoints"


# Env-var names mirror the operator runbook §"Env-var reference".
ENABLED_ENV: Final[str] = "TOYBOX_IMAGE_GEN_ENABLED"
MIN_VRAM_GB_ENV: Final[str] = "TOYBOX_IMAGE_GEN_MIN_VRAM_GB"
MODEL_DIR_ENV: Final[str] = "TOYBOX_IMAGE_GEN_MODEL_DIR"
BREAKER_OPEN_SEC_ENV: Final[str] = "TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC"
BREAKER_THRESHOLD_ENV: Final[str] = "TOYBOX_IMAGE_GEN_BREAKER_THRESHOLD"

DEFAULT_MIN_VRAM_GB: Final[int] = 12
DEFAULT_MODEL_DIR: Final[str] = "data/models/image_gen"
DEFAULT_BREAKER_OPEN_SEC: Final[float] = 300.0
DEFAULT_BREAKER_THRESHOLD: Final[int] = 3

# Files that must exist under ``MODEL_DIR_ENV`` for image-gen to be
# capable. Paths relative to the model dir root. Mirrors the layout
# the F1 setup script writes under
# ``data/models/image_gen/{sdxl,ip_adapter,pixel_art_lora,bg_remove}/``.
REQUIRED_CHECKPOINTS: Final[tuple[str, ...]] = (
    "sdxl/stable-diffusion-xl-base-1.0/model_index.json",
    "sdxl/stable-diffusion-xl-base-1.0/unet/diffusion_pytorch_model.fp16.safetensors",
    "ip_adapter/sdxl_models/ip-adapter_sdxl_vit-h.safetensors",
    "ip_adapter/models/image_encoder/model.safetensors",
    "pixel_art_lora/pixel-art-xl.safetensors",
    "bg_remove/u2net.onnx",
)


def _env_bool_disabled(name: str) -> bool:
    """Return True iff ``name`` is set to a value that disables the feature.

    The operator runbook documents three values: ``auto`` (default;
    capability-gated), ``true`` (force-on), ``false`` (force-off).
    Only the explicit ``false`` triggers the env-disable branch
    here; ``auto`` and ``true`` fall through to the live probes.
    Empty / missing → not disabled.
    """
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() in {"false", "0", "off", "no"}


def _model_dir() -> Path:
    """Return the configured image-gen model dir root."""
    raw = os.environ.get(MODEL_DIR_ENV)
    return Path(raw) if raw else Path(DEFAULT_MODEL_DIR)


def _min_vram_gb() -> float:
    """Return the configured VRAM floor (GB)."""
    raw = os.environ.get(MIN_VRAM_GB_ENV)
    if raw is None:
        return float(DEFAULT_MIN_VRAM_GB)
    try:
        return float(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not a number; using %d",
            MIN_VRAM_GB_ENV,
            raw,
            DEFAULT_MIN_VRAM_GB,
        )
        return float(DEFAULT_MIN_VRAM_GB)


def _missing_checkpoints(model_dir: Path) -> list[str]:
    """Return relative paths of any missing required checkpoint files."""
    missing = []
    for relative in REQUIRED_CHECKPOINTS:
        if not (model_dir / relative).is_file():
            missing.append(relative)
    return missing


def _probe_cuda_and_vram() -> tuple[bool, float]:
    """Return ``(cuda_available, free_vram_gb)`` via a lazy torch import.

    Isolated as a separate function so tests can monkeypatch it
    without faking torch itself. ``free_vram_gb`` is meaningless
    when ``cuda_available`` is False; callers must check the bool
    first.
    """
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError:
        # No torch installed → effectively no CUDA. The image_gen
        # extras are optional; downstream tests use the stub path.
        return False, 0.0
    if not torch.cuda.is_available():
        return False, 0.0
    # ``mem_get_info()`` returns (free_bytes, total_bytes) for the
    # current device. The capability gate cares about *free* VRAM
    # because a Phase E local-LLM run holding 10 GB should flip the
    # gate even on a 16 GB card.
    try:
        free_bytes, _total = torch.cuda.mem_get_info()
    except Exception as exc:  # pragma: no cover — defensive
        _logger.warning("torch.cuda.mem_get_info() failed: %s", exc)
        return True, 0.0
    return True, float(free_bytes) / float(1024**3)


def is_image_gen_capable(*, check_free_vram: bool = True) -> tuple[bool, CapabilityReason, str]:
    """Four-branch capability gate, in priority order.

    1. Env-disabled (``TOYBOX_IMAGE_GEN_ENABLED=false``).
    2. CUDA not available.
    3. Free VRAM below floor (skipped when ``check_free_vram=False``).
    4. One or more required checkpoints missing on disk.
    5. Else capable.

    Returns ``(capable, reason_enum, detail)``:

    * ``capable`` — bool, True iff every gate passed. Kept for
      backwards-compat with call sites that only want yes/no.
    * ``reason_enum`` — :class:`CapabilityReason`, the dispatch key
      used by the F.5-3a worker + REST routes (no prefix-matching on
      the detail string).
    * ``detail`` — human-readable detail (e.g.
      ``"VRAM 6.9GB < floor 12.0GB"``); the parent UI renders this
      verbatim in the banner.

    ``check_free_vram=False`` is for request-time callers (post-commit
    enqueue hook, ``/regenerate`` endpoints, the actions GET): once the
    boot probe established the hardware fits, mid-flight free-VRAM dips
    during an active generation are normal — SDXL peaks at ~6 GB on
    this card, which drops free VRAM below the 6 GB floor for the
    duration of the gen. Re-checking at request time would 409 every
    regenerate click that lands during another sprite's run. The worker
    handles real OOM via ``ImageGenCapacityError`` + breaker, which is
    the appropriate fast-fail for that case. The boot probe still uses
    the strict check (default) so an operator with too small a card
    sees the failure at startup.
    """
    if _env_bool_disabled(ENABLED_ENV):
        return (
            False,
            CapabilityReason.ENV_DISABLED,
            "image-gen disabled via TOYBOX_IMAGE_GEN_ENABLED",
        )

    cuda_available, free_gb = _probe_cuda_and_vram()
    if not cuda_available:
        return False, CapabilityReason.NO_CUDA, "CUDA not available"

    if check_free_vram:
        floor_gb = _min_vram_gb()
        if free_gb < floor_gb:
            # Format VRAM with one decimal place so 7.4 GB doesn't render
            # as "7GB" and look like the floor was exactly hit.
            return (
                False,
                CapabilityReason.LOW_VRAM,
                f"VRAM {free_gb:.1f}GB < floor {floor_gb:.1f}GB",
            )

    model_dir = _model_dir()
    missing = _missing_checkpoints(model_dir)
    if missing:
        # Cap the reason length so a wholesale missing dir doesn't
        # produce a 1KB banner; first 3 file names are enough to
        # tell the operator what's wrong.
        sample = ", ".join(missing[:3])
        if len(missing) > 3:
            sample += f", ... ({len(missing)} total)"
        return (
            False,
            CapabilityReason.MISSING_CHECKPOINTS,
            f"checkpoints missing: {sample}",
        )

    return True, CapabilityReason.CAPABLE, "capable"


# ---------------------------------------------------------------------
# Per-pipeline breaker
# ---------------------------------------------------------------------


class ImageGenBreaker:
    """Per-pipeline breaker for the image-gen worker.

    Wraps :class:`toybox.ai.breaker.CircuitBreaker` so the image-gen
    failure window is independent of Claude / local-LLM breakers
    (the plan §"Per-pipeline breaker" rationale: a flaky GPU should
    not disable Claude calls and vice versa).

    The wrapper exposes :meth:`check_and_record` — the F4 worker's
    intended call site — plus :attr:`circuit_breaker` for tests
    that want to drive the underlying breaker directly.
    """

    def __init__(
        self,
        *,
        threshold: int | None = None,
        cooldown_sec: float | None = None,
    ) -> None:
        resolved_threshold = (
            threshold
            if threshold is not None
            else _env_int(BREAKER_THRESHOLD_ENV, DEFAULT_BREAKER_THRESHOLD)
        )
        resolved_cooldown = (
            cooldown_sec
            if cooldown_sec is not None
            else _env_float(BREAKER_OPEN_SEC_ENV, DEFAULT_BREAKER_OPEN_SEC)
        )
        self._breaker = CircuitBreaker(
            threshold=resolved_threshold,
            cooldown_sec=resolved_cooldown,
        )

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Underlying :class:`CircuitBreaker` for direct test access."""
        return self._breaker

    def is_open(self) -> bool:
        """Return True iff the breaker is currently open."""
        return self._breaker.is_open()

    def check_and_record(self, success: bool) -> None:
        """Record one image-gen attempt outcome.

        ``success=True`` → reset failure counter, close the breaker.
        ``success=False`` → increment failure counter; open at
        threshold.

        Worker calls this after every job. CUDA OOM and timeout
        both register as failures here (the worker decides which
        exception type to raise upstream; the breaker only cares
        about pass/fail).
        """
        if success:
            self._breaker.record_success()
        else:
            self._breaker.record_failure()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _logger.warning("%s=%r is not an int; using %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        _logger.warning("%s=%r is not a float; using %s", name, raw, default)
        return default


_image_gen_breaker: ImageGenBreaker | None = None


def get_image_gen_breaker() -> ImageGenBreaker:
    """Return the process-wide image-gen breaker, lazily constructed."""
    global _image_gen_breaker
    if _image_gen_breaker is None:
        _image_gen_breaker = ImageGenBreaker()
    return _image_gen_breaker


def reset_image_gen_breaker_for_tests() -> None:
    """Drop the cached breaker. Used by test fixtures."""
    global _image_gen_breaker
    _image_gen_breaker = None


__all__ = [
    "BREAKER_OPEN_SEC_ENV",
    "BREAKER_THRESHOLD_ENV",
    "CapabilityReason",
    "DEFAULT_BREAKER_OPEN_SEC",
    "DEFAULT_BREAKER_THRESHOLD",
    "DEFAULT_MIN_VRAM_GB",
    "DEFAULT_MODEL_DIR",
    "ENABLED_ENV",
    "ImageGenBreaker",
    "MIN_VRAM_GB_ENV",
    "MODEL_DIR_ENV",
    "REQUIRED_CHECKPOINTS",
    "get_image_gen_breaker",
    "is_image_gen_capable",
    "reset_image_gen_breaker_for_tests",
]
