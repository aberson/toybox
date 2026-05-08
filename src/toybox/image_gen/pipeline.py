"""SDXL + IP-Adapter + pixel-art LoRA → quantize → bg-remove pipeline.

Public entry: :func:`generate_action` — async, runs the heavy work
in :func:`asyncio.to_thread` with a per-call :func:`asyncio.wait_for`
cap.

ALL heavy imports (``torch``, ``diffusers``, ``transformers``,
``rembg``) live INSIDE :func:`_run_pipeline_sync` so module import
is cheap when the feature is disabled. The
``test_lazy_imports`` test pins this contract.

Stub-runtime injection:

* ``TOYBOX_IMAGE_GEN_STUB=1`` short-circuits the real pipeline and
  delegates to ``tests.fixtures.image_gen.stub_pipeline.generate_action_stub``
  — the path the worker / REST / kiosk tests downstream use to
  exercise orchestration without a GPU.
* ``TOYBOX_IMAGE_GEN_STUB_MODE=oom`` makes the stub raise a
  synthetic ``RuntimeError("CUDA out of memory")`` which the
  pipeline catches + re-raises as :class:`ImageGenCapacityError`,
  so the worker's breaker integration can be exercised in CI.
* ``TOYBOX_IMAGE_GEN_STUB_DELAY_SEC`` makes the stub block long
  enough to trip the timeout, exercising
  :class:`ImageGenTimeoutError`.

The canonical SDXL config follows
``documentation/operator/image-gen-runtime.md`` §"Canonical pipeline
config" — three rules pinned by the 8 GB feasibility probe:

1. Load ``CLIPVisionModelWithProjection`` explicitly and pass it
   into the pipeline ``from_pretrained`` call.
2. Do NOT call ``pipe.enable_attention_slicing()`` (clobbers the
   IPA-aware attention processors).
3. Use ``pipe.vae.enable_slicing()`` (the ``pipe.enable_vae_slicing()``
   alias is deprecated).

A module-level cached pipeline object keeps subsequent calls fast:
loading SDXL + the IP-Adapter + LoRA takes minutes; reuse is
mandatory for the 10-sprites-per-toy throughput. The F4 worker
keeps the process alive so the cache stays warm across jobs.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import logging
import os
from typing import Any, Final

from .models import (
    ACTION_PROMPTS,
    GenerationContext,
    ImageGenCapacityError,
    ImageGenTimeoutError,
)

_logger = logging.getLogger(__name__)

# Env knobs (mirroring the operator runbook).
TIMEOUT_ENV: Final[str] = "TOYBOX_IMAGE_GEN_TIMEOUT_SEC"
OUTPUT_DIM_ENV: Final[str] = "TOYBOX_IMAGE_GEN_OUTPUT_DIM"
PALETTE_COLORS_ENV: Final[str] = "TOYBOX_IMAGE_GEN_PALETTE_COLORS"
MODEL_DIR_ENV: Final[str] = "TOYBOX_IMAGE_GEN_MODEL_DIR"
STUB_ENV: Final[str] = "TOYBOX_IMAGE_GEN_STUB"
STUB_MODE_ENV: Final[str] = "TOYBOX_IMAGE_GEN_STUB_MODE"
STUB_DELAY_ENV: Final[str] = "TOYBOX_IMAGE_GEN_STUB_DELAY_SEC"

DEFAULT_TIMEOUT_SEC: Final[float] = 120.0
DEFAULT_OUTPUT_DIM: Final[int] = 128
DEFAULT_PALETTE_COLORS: Final[int] = 32
DEFAULT_MODEL_DIR: Final[str] = "data/models/image_gen"
DEFAULT_NEGATIVE_PROMPT: Final[str] = (
    "photorealistic, 3d, blurry, smooth shading, antialiased, gradient"
)

# Module-level cached pipeline. ``None`` until first real call;
# subsequent calls reuse. The worker keeps the process alive so the
# cache survives across jobs. Tests that load the stub never touch
# this. Typed ``Any`` because the diffusers types live behind the
# lazy import.
_cached_pipeline: Any = None


def _stub_active() -> bool:
    """Return True iff ``TOYBOX_IMAGE_GEN_STUB`` is set to a truthy value."""
    raw = os.environ.get(STUB_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _timeout_sec() -> float:
    raw = os.environ.get(TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_TIMEOUT_SEC
    try:
        return float(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not a float; using %s",
            TIMEOUT_ENV,
            raw,
            DEFAULT_TIMEOUT_SEC,
        )
        return DEFAULT_TIMEOUT_SEC


def _output_dim() -> int:
    raw = os.environ.get(OUTPUT_DIM_ENV)
    if raw is None:
        return DEFAULT_OUTPUT_DIM
    try:
        parsed = int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int; using %d",
            OUTPUT_DIM_ENV,
            raw,
            DEFAULT_OUTPUT_DIM,
        )
        return DEFAULT_OUTPUT_DIM
    return parsed if parsed > 0 else DEFAULT_OUTPUT_DIM


def _palette_colors() -> int:
    raw = os.environ.get(PALETTE_COLORS_ENV)
    if raw is None:
        return DEFAULT_PALETTE_COLORS
    try:
        parsed = int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int; using %d",
            PALETTE_COLORS_ENV,
            raw,
            DEFAULT_PALETTE_COLORS,
        )
        return DEFAULT_PALETTE_COLORS
    # Pillow's ``Image.quantize`` allows up to 256 palette entries.
    if parsed < 2:
        return 2
    if parsed > 256:
        return 256
    return parsed


def _model_dir() -> str:
    raw = os.environ.get(MODEL_DIR_ENV)
    return raw if raw else DEFAULT_MODEL_DIR


def _build_prompt(slot: str, ctx: GenerationContext) -> str:
    """Compose the SDXL positive prompt per plan §New components.

    Format: ``"<intro>, pixel art, 16-bit, sprite, retro game style,
    transparent background, <ACTION_PROMPTS[slot]>"`` where ``<intro>``
    is ``f"{persona_display_name} the {toy_display_name}"`` if
    persona is set, else ``f"a {toy_display_name}"``.

    Tags are NOT injected — IP-Adapter conditions on the photo and
    the plan explicitly notes tags are noisy here.
    """
    if slot not in ACTION_PROMPTS:
        raise ValueError(f"unknown action slot {slot!r}")
    if ctx.persona_display_name:
        intro = f"{ctx.persona_display_name} the {ctx.toy_display_name}"
    else:
        intro = f"a {ctx.toy_display_name}"
    return (
        f"{intro}, pixel art, 16-bit, sprite, retro game style, "
        f"transparent background, {ACTION_PROMPTS[slot]}"
    )


def _invoke_stub(
    reference_bytes: bytes,
    slot: str,
    seed: int,
    ctx: GenerationContext,
) -> bytes:
    """Delegate to the test stub fixture.

    Dynamic import so the production install path doesn't require the
    ``tests`` directory on ``sys.path``. The stub respects the
    ``TOYBOX_IMAGE_GEN_STUB_MODE`` / ``TOYBOX_IMAGE_GEN_STUB_DELAY_SEC``
    knobs to simulate OOM / timeout for CI coverage.
    """
    # Loud WARNING on every stub call: if a deploy bundles ``tests/``
    # (some Docker layouts do) and ``TOYBOX_IMAGE_GEN_STUB=1`` is set
    # accidentally in production, the stub would otherwise silently
    # ship 16x16 placeholder PNGs to real toys. The log entry is
    # loud enough to surface in prod log scrapers.
    _logger.warning(
        "image-gen running in STUB mode (TOYBOX_IMAGE_GEN_STUB=1) — "
        "output is a deterministic 16x16 PNG, not real generation"
    )
    try:
        module = importlib.import_module("tests.fixtures.image_gen.stub_pipeline")
    except ImportError as exc:
        raise RuntimeError(
            "TOYBOX_IMAGE_GEN_STUB=1 but tests.fixtures.image_gen.stub_pipeline "
            "is not importable; the stub is only available in dev/CI checkouts"
        ) from exc
    fn = module.generate_action_stub
    return bytes(fn(reference_bytes, slot, seed, ctx))


def _run_pipeline_sync(
    reference_bytes: bytes,
    slot: str,
    seed: int,
    ctx: GenerationContext,
) -> bytes:
    """Synchronous worker; runs inside :func:`asyncio.to_thread`.

    Real-pipeline path lazy-imports torch / diffusers / transformers
    / rembg INSIDE this function. CUDA OOM is caught + re-raised as
    :class:`ImageGenCapacityError` so the worker breaker can trip.
    """
    if _stub_active():
        # The stub honors STUB_MODE=oom by raising a synthetic
        # exception we recognize and re-raise as ImageGenCapacityError.
        try:
            return _invoke_stub(reference_bytes, slot, seed, ctx)
        except _StubCudaOOM as exc:
            raise ImageGenCapacityError(
                f"stub CUDA OOM simulated for slot={slot}"
            ) from exc

    # ----------------------------------------------------------------
    # Lazy heavy imports. Mypy does not see torch/diffusers/etc. in
    # this venv (they're optional extras), so we type the locals as
    # ``Any`` and rely on runtime structure. The lazy-import test
    # asserts these are NOT loaded by ``import toybox.image_gen.pipeline``.
    # ----------------------------------------------------------------
    import torch  # type: ignore[import-not-found]
    from diffusers import (  # type: ignore[import-not-found]
        StableDiffusionXLPipeline,
    )
    from PIL import Image
    from rembg import new_session, remove  # type: ignore[import-not-found]
    from transformers import (  # type: ignore[import-not-found]
        CLIPVisionModelWithProjection,
    )

    model_dir = _model_dir()

    # 1. Subject-isolate via rembg using the local u2net.onnx.
    # rembg lets you pass a session built from a model name; for
    # robustness we honor whichever knob the installed version
    # exposes. ``new_session(model_name="u2net")`` works across
    # rembg ≥ 2.0.
    bg_session = new_session(
        model_name="u2net",
        providers=["CPUExecutionProvider"],
    )
    cutout_bytes = remove(reference_bytes, session=bg_session)
    cutout_image = Image.open(io.BytesIO(cutout_bytes)).convert("RGBA")

    # 2. Construct (or reuse) the SDXL pipeline.
    global _cached_pipeline
    if _cached_pipeline is None:
        # Wrap construction so a partial-construction failure (e.g.
        # SDXL loads but LoRA OOMs) doesn't leave half-loaded
        # CUDA tensors pinned via Python refs until GC runs.
        # We bind ``image_encoder`` / ``pipe`` to ``None`` first so
        # the cleanup branch can ``del`` them unconditionally.
        image_encoder = None
        pipe = None
        try:
            # Rule 1: load the CLIP image encoder explicitly.
            image_encoder = CLIPVisionModelWithProjection.from_pretrained(
                f"{model_dir}/ip_adapter/models/image_encoder",
                torch_dtype=torch.float16,
                local_files_only=True,
            )
            pipe = StableDiffusionXLPipeline.from_pretrained(
                f"{model_dir}/sdxl/stable-diffusion-xl-base-1.0",
                image_encoder=image_encoder,
                torch_dtype=torch.float16,
                variant="fp16",
                use_safetensors=True,
                local_files_only=True,
            )
            pipe.load_ip_adapter(
                f"{model_dir}/ip_adapter",
                subfolder="sdxl_models",
                weight_name="ip-adapter_sdxl_vit-h.safetensors",
            )
            pipe.set_ip_adapter_scale(0.6)
            pipe.load_lora_weights(
                f"{model_dir}/pixel_art_lora",
                weight_name="pixel-art-xl.safetensors",
            )
            # Fuse LoRA into the base UNet weights so the cpu-offload
            # hook moves a single tensor graph. Without fusion, LoRA
            # adapter modules attached by peft can stay on CPU when
            # the offload hook moves the UNet to CUDA, producing the
            # "addmm: tensors on cuda:0 and cpu" error on the first
            # forward pass. Fusion is one-way; we never need to
            # unload this LoRA so there's nothing to unfuse.
            pipe.fuse_lora()
            # Memory knobs.
            pipe.enable_model_cpu_offload()
            pipe.vae.enable_slicing()
            # Rule 2: DO NOT call pipe.enable_attention_slicing().
        except Exception:
            # Best-effort cleanup so a partial-construction failure
            # doesn't pin VRAM. (``del locals()[name]`` does NOT
            # work in CPython for function locals — known footgun.
            # Use explicit ``del`` on each binding instead.)
            try:
                del pipe
            except NameError:
                pass
            try:
                del image_encoder
            except NameError:
                pass
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:  # pragma: no cover — defensive
                pass
            raise
        _cached_pipeline = pipe
    pipe = _cached_pipeline

    # 3. Build prompt + run generation.
    prompt = _build_prompt(slot, ctx)
    generator = torch.Generator("cuda").manual_seed(seed)
    try:
        result = pipe(
            prompt=prompt,
            negative_prompt=DEFAULT_NEGATIVE_PROMPT,
            ip_adapter_image=cutout_image,
            generator=generator,
            num_inference_steps=25,
            guidance_scale=5.0,
            height=1024,
            width=1024,
        )
    except torch.cuda.OutOfMemoryError as exc:
        raise ImageGenCapacityError(
            f"CUDA OOM during generation for slot={slot}"
        ) from exc
    raw_image = result.images[0]

    # 4. Pillow palette quantize (downsample → quantize → RGBA).
    target_dim = _output_dim()
    colors = _palette_colors()
    rgba = raw_image.convert("RGBA")
    downsampled = rgba.resize(
        (target_dim, target_dim),
        Image.Resampling.BILINEAR,
    )
    # ``Image.quantize`` requires an RGB-mode image; reduce to RGB
    # for the quantize step then re-attach an alpha channel from
    # the second rembg pass.
    rgb_for_quant = downsampled.convert("RGB")
    quantized = rgb_for_quant.quantize(
        colors=colors,
        method=Image.Quantize.MEDIANCUT,
    )
    quantized_rgba = quantized.convert("RGBA")

    # 5. Second rembg pass to clean residual non-transparent pixels
    #    (SDXL's "transparent background" prompt is a hint, not a
    #    guarantee). We feed it the quantized PNG bytes so rembg
    #    operates on the same artifact we'll save.
    intermediate = io.BytesIO()
    quantized_rgba.save(intermediate, format="PNG")
    cleaned_bytes = remove(intermediate.getvalue(), session=bg_session)

    # 6. Return PNG bytes. The second rembg pass returns PNG bytes
    #    directly when given PNG input; no re-encode needed.
    return bytes(cleaned_bytes)


class _StubCudaOOM(RuntimeError):
    """Internal sentinel raised by the stub to simulate CUDA OOM."""


async def generate_action(
    reference_bytes: bytes,
    slot: str,
    seed: int,
    ctx: GenerationContext,
) -> bytes:
    """Generate one action sprite end-to-end.

    Returns PNG bytes (RGBA). Heavy work runs in
    :func:`asyncio.to_thread` so the event loop is never blocked.

    Raises:
        ImageGenTimeoutError: When the per-call ``asyncio.wait_for``
            cap (default 120s, env-overridable via
            ``TOYBOX_IMAGE_GEN_TIMEOUT_SEC``) fires.
        ImageGenCapacityError: On real or simulated CUDA OOM.
        ValueError: When ``slot`` is not in :data:`ACTION_SLOTS`.
    """
    timeout = _timeout_sec()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_pipeline_sync, reference_bytes, slot, seed, ctx),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise ImageGenTimeoutError(
            f"generation exceeded {timeout}s for slot={slot}"
        ) from exc


def reset_pipeline_cache_for_tests() -> None:
    """Drop the cached pipeline so the next call rebuilds.

    Used by tests that need to re-exercise the load path. The real
    pipeline is never built in CI (no GPU); this is mostly a
    safety hatch for the integration test on operator hardware.
    """
    global _cached_pipeline
    _cached_pipeline = None


__all__ = [
    "DEFAULT_NEGATIVE_PROMPT",
    "DEFAULT_OUTPUT_DIM",
    "DEFAULT_PALETTE_COLORS",
    "DEFAULT_TIMEOUT_SEC",
    "MODEL_DIR_ENV",
    "OUTPUT_DIM_ENV",
    "PALETTE_COLORS_ENV",
    "STUB_DELAY_ENV",
    "STUB_ENV",
    "STUB_MODE_ENV",
    "TIMEOUT_ENV",
    "generate_action",
    "reset_pipeline_cache_for_tests",
]
