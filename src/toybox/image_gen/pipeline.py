"""SD 1.5 + LCM-LoRA + cartoon-style → bg-remove pipeline.

Public entry: :func:`generate_action` — async, runs the heavy work
in :func:`asyncio.to_thread` with a per-call :func:`asyncio.wait_for`
cap.

ALL heavy imports (``torch``, ``diffusers``, ``rembg``) live INSIDE
:func:`_run_pipeline_sync` so module import is cheap when the
feature is disabled. The ``test_lazy_imports`` test pins this
contract.

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

Cartoon mode dispatch (per ``TOYBOX_IMAGE_GEN_CARTOON_MODE``,
default ``checkpoint``):

* ``checkpoint``: ``StableDiffusionPipeline.from_pretrained(TOYBOX_IMAGE_GEN_CARTOON_PATH, ...)``
  — full cartoon checkpoint replaces SD 1.5 base. Then load
  LCM-LoRA only and ``set_adapters(["lcm"])``.
* ``lora``: ``StableDiffusionPipeline.from_pretrained(TOYBOX_IMAGE_GEN_BASE_MODEL_PATH, ...)``
  then ``pipe.load_lora_weights(TOYBOX_IMAGE_GEN_CARTOON_PATH, adapter_name="cartoon")``
  + LCM-LoRA, then ``set_adapters(["lcm", "cartoon"], adapter_weights=[1.0, 1.0])``.

In both modes: ``pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)``,
``pipe.to("cuda")`` once at construction, ``pipe.vae.enable_slicing()``,
``safety_checker=None, requires_safety_checker=False``. Generation
runs at ``num_inference_steps=4, guidance_scale=1.0, height=512,
width=512`` (LCM convention; higher CFG hurts LCM output).

A module-level cached pipeline object keeps subsequent calls fast.
The F4 worker keeps the process alive so the cache stays warm
across jobs. Cache survives env changes ONLY via process restart.
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
MODEL_DIR_ENV: Final[str] = "TOYBOX_IMAGE_GEN_MODEL_DIR"
BASE_MODEL_PATH_ENV: Final[str] = "TOYBOX_IMAGE_GEN_BASE_MODEL_PATH"
CARTOON_MODE_ENV: Final[str] = "TOYBOX_IMAGE_GEN_CARTOON_MODE"
CARTOON_PATH_ENV: Final[str] = "TOYBOX_IMAGE_GEN_CARTOON_PATH"
LCM_LORA_PATH_ENV: Final[str] = "TOYBOX_IMAGE_GEN_LCM_LORA_PATH"
STUB_ENV: Final[str] = "TOYBOX_IMAGE_GEN_STUB"
STUB_MODE_ENV: Final[str] = "TOYBOX_IMAGE_GEN_STUB_MODE"
STUB_DELAY_ENV: Final[str] = "TOYBOX_IMAGE_GEN_STUB_DELAY_SEC"

DEFAULT_TIMEOUT_SEC: Final[float] = 120.0
DEFAULT_OUTPUT_DIM: Final[int] = 512
DEFAULT_MODEL_DIR: Final[str] = "data/models/image_gen"
DEFAULT_BASE_MODEL_PATH: Final[str] = "data/models/image_gen/sd15/base"
DEFAULT_CARTOON_MODE: Final[str] = "checkpoint"
DEFAULT_CARTOON_PATH: Final[str] = "data/models/image_gen/cartoon_checkpoint"
DEFAULT_LCM_LORA_PATH: Final[str] = "data/models/image_gen/sd15/lcm_lora"
# Filesystem layout pinned by P2's huggingface_hub download (see
# capability.py:_BASE_REQUIRED_CHECKPOINTS): the IP-Adapter Plus
# weights sit at ``ip_adapter/models/ip-adapter-plus_sd15.bin`` and the
# CLIP ViT-L image encoder at ``ip_adapter/models/image_encoder/``.
# ``load_ip_adapter`` finds the encoder via ``image_encoder_folder``
# (defaults to ``"image_encoder"`` relative to ``subfolder``).
DEFAULT_IP_ADAPTER_PATH: Final[str] = "data/models/image_gen/ip_adapter"
DEFAULT_IP_ADAPTER_SUBFOLDER: Final[str] = "models"
DEFAULT_IP_ADAPTER_WEIGHT_NAME: Final[str] = "ip-adapter-plus_sd15.bin"
# IPA conditioning strength applied to the toy reference cutout. Initial
# 0.6 per Phase P plan; P7 operator UAT may tune up/down and P7b pins
# the final value. Higher = identity tightens but pose can collapse.
IP_ADAPTER_SCALE: Final[float] = 0.6
DEFAULT_NEGATIVE_PROMPT: Final[str] = (
    "photorealistic, 3d, blurry, smooth shading, antialiased, gradient"
    ", text, letters, numbers, writing, symbols, watermark"
)

CARTOON_MODE_CHECKPOINT: Final[str] = "checkpoint"
CARTOON_MODE_LORA: Final[str] = "lora"
_VALID_CARTOON_MODES: Final[frozenset[str]] = frozenset(
    {CARTOON_MODE_CHECKPOINT, CARTOON_MODE_LORA}
)

# Module-level cached pipeline. ``None`` until first real call;
# subsequent calls reuse. The worker keeps the process alive so the
# cache survives across jobs. Tests that load the stub never touch
# this. Typed ``Any`` because the diffusers types live behind the
# lazy import.
_cached_pipeline: Any = None

# Phase Y: separate cache for the SCENE (backdrop) pipeline. Scenes are opaque
# full-bleed scenery generated text2img with NO IP-Adapter — keeping a distinct
# cache from ``_cached_pipeline`` (which has IPA Plus loaded) avoids cross-
# contaminating the sprite path. Only the offline batch CLI (scripts/
# batch_scenes.py) builds this; the runtime never generates scenes on demand.
_cached_scene_pipeline: Any = None


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


def _cartoon_mode() -> str:
    raw = os.environ.get(CARTOON_MODE_ENV)
    if raw is None:
        return DEFAULT_CARTOON_MODE
    normalized = raw.strip().lower()
    if normalized not in _VALID_CARTOON_MODES:
        raise ValueError(f"{CARTOON_MODE_ENV}={raw!r} not in {sorted(_VALID_CARTOON_MODES)}")
    return normalized


def _model_dir() -> str:
    raw = os.environ.get(MODEL_DIR_ENV)
    return raw if raw else DEFAULT_MODEL_DIR


def _base_model_path() -> str:
    raw = os.environ.get(BASE_MODEL_PATH_ENV)
    return raw if raw else DEFAULT_BASE_MODEL_PATH


def _cartoon_path() -> str:
    raw = os.environ.get(CARTOON_PATH_ENV)
    return raw if raw else DEFAULT_CARTOON_PATH


def _lcm_lora_path() -> str:
    raw = os.environ.get(LCM_LORA_PATH_ENV)
    return raw if raw else DEFAULT_LCM_LORA_PATH


def _build_prompt(slot: str, ctx: GenerationContext) -> str:
    """Compose the SD 1.5 positive prompt for Phase P.

    Format:

        "<intro>, <tags>, <ACTION_PROMPTS[slot]>,
         2D cartoon, simple shapes, clean lines, transparent background"

    where ``<intro>`` is ``f"{persona_display_name} the {toy_display_name}"``
    when persona is set, else ``f"a {toy_display_name}"``.

    Phase P dropped the per-call palette-hex tokens: the IP-Adapter
    Plus reference image now carries identity/colour conditioning, and
    public benchmarks showed hex tokens biased SD 1.5 tokenizers
    toward rendering literal text glyphs of the codes. See
    ``documentation/phase-p-plan.md`` §"Design Decisions".

    ``ctx.tags`` may be empty — the comma is unconditional but
    consecutive commas remain visible to the diffuser; that's fine
    (SD 1.5 tokenizers handle empty tag lists without artifacts in
    practice).
    """
    if slot not in ACTION_PROMPTS:
        raise ValueError(f"unknown action slot {slot!r}")
    intro = (
        f"{ctx.persona_display_name} the {ctx.toy_display_name}"
        if ctx.persona_display_name
        else f"a {ctx.toy_display_name}"
    )
    tags = ", ".join(ctx.tags) if ctx.tags else ""
    return (
        f"{intro}, {tags}, "
        f"{ACTION_PROMPTS[slot]}, "
        f"2D cartoon, simple shapes, clean lines, transparent background"
    )


def _resolve_ipa_scale(ctx: GenerationContext) -> float:
    """Resolve the IP-Adapter conditioning scale for one generation call.

    Phase Y: returns ``ctx.ipa_scale`` when the caller supplied a per-call
    override, else the module default :data:`IP_ADAPTER_SCALE` (0.6). Keeping
    this a tiny pure function makes the override resolution unit-testable
    without a GPU (the ``set_ip_adapter_scale`` call it feeds lives in the
    real-pipeline path, exercised on operator hardware).
    """
    if ctx.ipa_scale is not None:
        return ctx.ipa_scale
    return IP_ADAPTER_SCALE


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


def _build_pipeline(torch_mod: Any) -> Any:
    """Construct the cartoon pipeline per ``TOYBOX_IMAGE_GEN_CARTOON_MODE``.

    Imports diffusers lazily; expects an already-imported ``torch``
    module. Best-effort cleanup on partial-construction failure so a
    LoRA-load OOM doesn't leave half-loaded CUDA tensors pinned.

    Phase P additions:

    * After cartoon-mode adapters are loaded + activated, load the
      IP-Adapter Plus weights (``ip-adapter-plus_sd15.bin``) and CLIP
      ViT-L image encoder via ``pipe.load_ip_adapter(...)`` and set
      the conditioning strength via
      ``pipe.set_ip_adapter_scale(IP_ADAPTER_SCALE)``. Both branches
      get IPA — toy-image conditioning is the same for ``checkpoint``
      and ``lora`` cartoon modes. Wrapped in the same try/except so a
      partial IPA load doesn't pin CUDA tensors.
    """
    from diffusers import LCMScheduler, StableDiffusionPipeline

    mode = _cartoon_mode()
    pipe = None
    try:
        if mode == CARTOON_MODE_CHECKPOINT:
            pipe = StableDiffusionPipeline.from_pretrained(  # type: ignore[no-untyped-call]
                _cartoon_path(),
                torch_dtype=torch_mod.float16,
                variant="fp16",
                use_safetensors=True,
                local_files_only=True,
                safety_checker=None,
                requires_safety_checker=False,
            )
            pipe.load_lora_weights(_lcm_lora_path(), adapter_name="lcm")
            pipe.set_adapters(["lcm"], adapter_weights=[1.0])
        else:
            pipe = StableDiffusionPipeline.from_pretrained(  # type: ignore[no-untyped-call]
                _base_model_path(),
                torch_dtype=torch_mod.float16,
                variant="fp16",
                use_safetensors=True,
                local_files_only=True,
                safety_checker=None,
                requires_safety_checker=False,
            )
            pipe.load_lora_weights(_cartoon_path(), adapter_name="cartoon")
            pipe.load_lora_weights(_lcm_lora_path(), adapter_name="lcm")
            pipe.set_adapters(["lcm", "cartoon"], adapter_weights=[1.0, 1.0])

        # IP-Adapter Plus: identical load shape in both cartoon modes.
        # ``image_encoder_folder`` defaults to ``"image_encoder"`` and
        # resolves relative to ``subfolder``, so the encoder is loaded
        # from ``<ip_adapter>/models/image_encoder/`` matching the P2
        # download layout (see capability.py:_BASE_REQUIRED_CHECKPOINTS).
        pipe.load_ip_adapter(
            DEFAULT_IP_ADAPTER_PATH,
            subfolder=DEFAULT_IP_ADAPTER_SUBFOLDER,
            weight_name=DEFAULT_IP_ADAPTER_WEIGHT_NAME,
        )
        pipe.set_ip_adapter_scale(IP_ADAPTER_SCALE)

        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)  # type: ignore[no-untyped-call]
        pipe.to("cuda")
        pipe.vae.enable_slicing()
    except Exception:
        try:
            del pipe
        except NameError:
            pass
        try:
            if torch_mod.cuda.is_available():
                torch_mod.cuda.empty_cache()
        except Exception:  # pragma: no cover — defensive
            pass
        raise
    return pipe


def _run_pipeline_sync(
    reference_bytes: bytes,
    slot: str,
    seed: int,
    ctx: GenerationContext,
) -> bytes:
    """Synchronous worker; runs inside :func:`asyncio.to_thread`.

    Real-pipeline path lazy-imports torch / diffusers / rembg INSIDE
    this function. CUDA OOM is caught + re-raised as
    :class:`ImageGenCapacityError` so the worker breaker can trip.
    """
    if _stub_active():
        # The stub honors STUB_MODE=oom by raising a synthetic
        # exception we recognize and re-raise as ImageGenCapacityError.
        try:
            return _invoke_stub(reference_bytes, slot, seed, ctx)
        except _StubCudaOOM as exc:
            raise ImageGenCapacityError(f"stub CUDA OOM simulated for slot={slot}") from exc

    # ----------------------------------------------------------------
    # Lazy heavy imports. Mypy's project-level override silences the
    # missing-import warnings for these optional extras. The
    # lazy-import test asserts these are NOT loaded by
    # ``import toybox.image_gen.pipeline``.
    # ----------------------------------------------------------------
    import torch
    from PIL import Image
    from rembg import new_session, remove

    # 1. Subject-isolate via rembg using the local u2net.onnx.
    bg_session = new_session(
        model_name="u2net",
        providers=["CPUExecutionProvider"],
    )
    cutout_bytes = remove(reference_bytes, session=bg_session)
    cutout_image = Image.open(io.BytesIO(cutout_bytes)).convert("RGBA")

    # 2. Construct (or reuse) the SD 1.5 + LCM + cartoon pipeline.
    global _cached_pipeline
    if _cached_pipeline is None:
        _cached_pipeline = _build_pipeline(torch)
    pipe = _cached_pipeline

    # 3. Build prompt + run generation. Phase P: the rembg cutout is
    #    passed as ``ip_adapter_image`` so IPA Plus carries identity /
    #    colour conditioning instead of the dropped palette-hex tokens.
    #    Phase Y: set the IPA scale PER CALL (not just at build) so an
    #    optional ``ctx.ipa_scale`` override takes effect and, equally
    #    important, a previous call's override does NOT leak onto this
    #    cached pipeline — every call re-pins the scale to its resolved
    #    value (the override or the IP_ADAPTER_SCALE default).
    pipe.set_ip_adapter_scale(_resolve_ipa_scale(ctx))
    prompt = _build_prompt(slot, ctx)
    generator = torch.Generator("cuda").manual_seed(seed)
    try:
        result = pipe(
            prompt=prompt,
            negative_prompt=DEFAULT_NEGATIVE_PROMPT,
            ip_adapter_image=cutout_image,
            generator=generator,
            num_inference_steps=4,
            guidance_scale=1.0,
            height=512,
            width=512,
        )
    except torch.cuda.OutOfMemoryError as exc:
        raise ImageGenCapacityError(f"CUDA OOM during generation for slot={slot}") from exc
    raw_image = result.images[0]

    # 4. Second rembg pass to clean residual non-transparent pixels
    #    (the "transparent background" prompt is a hint, not a
    #    guarantee). Feed it the raw PNG bytes so rembg operates on
    #    the same artifact we'll downsample.
    intermediate = io.BytesIO()
    raw_image.convert("RGBA").save(intermediate, format="PNG")
    cleaned_bytes = remove(intermediate.getvalue(), session=bg_session)
    cleaned_image = Image.open(io.BytesIO(cleaned_bytes)).convert("RGBA")

    # 5. Resize to the configured output dim and return PNG bytes.
    #    Phase P: ``DEFAULT_OUTPUT_DIM`` is now 512 (matches the
    #    diffuser's native output), so this resize is a no-op in the
    #    default path. The call stays so the ``TOYBOX_IMAGE_GEN_OUTPUT_DIM``
    #    env override (operator-set to a non-512 value) still works.
    #    LANCZOS for the high-quality downscale case the override picks.
    target_dim = _output_dim()
    output_image = cleaned_image.resize(
        (target_dim, target_dim),
        Image.Resampling.LANCZOS,
    )
    buffer = io.BytesIO()
    output_image.save(buffer, format="PNG")
    return buffer.getvalue()


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
        raise ImageGenTimeoutError(f"generation exceeded {timeout}s for slot={slot}") from exc


def _invoke_scene_stub(scene_id: str, prompt: str, seed: int) -> bytes:
    """Delegate scene generation to the test stub fixture (Phase Y).

    Mirrors :func:`_invoke_stub` for the scene path. Dynamic import so the
    production install doesn't require the ``tests`` directory on ``sys.path``.
    """
    _logger.warning(
        "scene-gen running in STUB mode (TOYBOX_IMAGE_GEN_STUB=1) — "
        "output is a deterministic 16x16 PNG, not real generation"
    )
    try:
        module = importlib.import_module("tests.fixtures.image_gen.stub_pipeline")
    except ImportError as exc:
        raise RuntimeError(
            "TOYBOX_IMAGE_GEN_STUB=1 but tests.fixtures.image_gen.stub_pipeline "
            "is not importable; the stub is only available in dev/CI checkouts"
        ) from exc
    fn = module.generate_scene_stub
    return bytes(fn(scene_id, prompt, seed))


def _build_scene_pipeline(torch_mod: Any) -> Any:
    """Construct the cartoon text2img pipeline for scene backdrops (Phase Y).

    Identical to :func:`_build_pipeline`'s cartoon-adapter setup EXCEPT it does
    NOT load IP-Adapter — scenes have no toy reference image, they are pure
    text2img scenery. Best-effort CUDA cleanup on partial-construction failure.
    """
    from diffusers import LCMScheduler, StableDiffusionPipeline

    mode = _cartoon_mode()
    pipe = None
    try:
        if mode == CARTOON_MODE_CHECKPOINT:
            pipe = StableDiffusionPipeline.from_pretrained(  # type: ignore[no-untyped-call]
                _cartoon_path(),
                torch_dtype=torch_mod.float16,
                variant="fp16",
                use_safetensors=True,
                local_files_only=True,
                safety_checker=None,
                requires_safety_checker=False,
            )
            pipe.load_lora_weights(_lcm_lora_path(), adapter_name="lcm")
            pipe.set_adapters(["lcm"], adapter_weights=[1.0])
        else:
            pipe = StableDiffusionPipeline.from_pretrained(  # type: ignore[no-untyped-call]
                _base_model_path(),
                torch_dtype=torch_mod.float16,
                variant="fp16",
                use_safetensors=True,
                local_files_only=True,
                safety_checker=None,
                requires_safety_checker=False,
            )
            pipe.load_lora_weights(_cartoon_path(), adapter_name="cartoon")
            pipe.load_lora_weights(_lcm_lora_path(), adapter_name="lcm")
            pipe.set_adapters(["lcm", "cartoon"], adapter_weights=[1.0, 1.0])

        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)  # type: ignore[no-untyped-call]
        pipe.to("cuda")
        pipe.vae.enable_slicing()
    except Exception:
        try:
            del pipe
        except NameError:
            pass
        try:
            if torch_mod.cuda.is_available():
                torch_mod.cuda.empty_cache()
        except Exception:  # pragma: no cover — defensive
            pass
        raise
    return pipe


def _run_scene_sync(scene_id: str, prompt: str, seed: int) -> bytes:
    """Synchronous scene-backdrop generation; runs inside :func:`asyncio.to_thread`.

    Text2img with the cartoon LCM pipeline, NO IP-Adapter, NO rembg — the output
    is an OPAQUE RGB PNG (full-bleed scenery). CUDA OOM is caught + re-raised as
    :class:`ImageGenCapacityError`. Heavy imports stay inside this function so
    ``import toybox.image_gen.pipeline`` remains cheap.
    """
    if _stub_active():
        try:
            return _invoke_scene_stub(scene_id, prompt, seed)
        except _StubCudaOOM as exc:
            raise ImageGenCapacityError(f"stub CUDA OOM simulated for scene={scene_id}") from exc

    import torch
    from PIL import Image

    global _cached_scene_pipeline
    if _cached_scene_pipeline is None:
        _cached_scene_pipeline = _build_scene_pipeline(torch)
    pipe = _cached_scene_pipeline

    dim = _output_dim()
    generator = torch.Generator("cuda").manual_seed(seed)
    try:
        result = pipe(
            prompt=prompt,
            negative_prompt=DEFAULT_NEGATIVE_PROMPT,
            generator=generator,
            num_inference_steps=4,
            guidance_scale=1.0,
            height=dim,
            width=dim,
        )
    except torch.cuda.OutOfMemoryError as exc:
        raise ImageGenCapacityError(
            f"CUDA OOM during scene generation for scene={scene_id}"
        ) from exc

    # Scenes are opaque: flatten to RGB (no alpha), no rembg cutout.
    raw_image = result.images[0].convert("RGB")
    output_image = raw_image.resize((dim, dim), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    output_image.save(buffer, format="PNG")
    return buffer.getvalue()


async def generate_scene(scene_id: str, prompt: str, seed: int) -> bytes:
    """Generate one opaque scene-backdrop PNG end-to-end (Phase Y).

    Offline-only: called by ``scripts/batch_scenes.py`` to pre-render the scene
    library. The runtime never calls this — it serves the static PNGs. Heavy
    work runs in :func:`asyncio.to_thread` with the same per-call
    :func:`asyncio.wait_for` cap as :func:`generate_action`.

    Returns PNG bytes (opaque RGB).

    Raises:
        ImageGenTimeoutError: When the per-call timeout fires.
        ImageGenCapacityError: On real or simulated CUDA OOM.
    """
    timeout = _timeout_sec()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_scene_sync, scene_id, prompt, seed),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise ImageGenTimeoutError(
            f"scene generation exceeded {timeout}s for scene={scene_id}"
        ) from exc


def reset_pipeline_cache_for_tests() -> None:
    """Drop the cached pipeline so the next call rebuilds.

    Used by tests that need to re-exercise the load path. The real
    pipeline is never built in CI (no GPU); this is mostly a
    safety hatch for the integration test on operator hardware.
    """
    global _cached_pipeline, _cached_scene_pipeline
    _cached_pipeline = None
    _cached_scene_pipeline = None


__all__ = [
    "BASE_MODEL_PATH_ENV",
    "CARTOON_MODE_CHECKPOINT",
    "CARTOON_MODE_ENV",
    "CARTOON_MODE_LORA",
    "CARTOON_PATH_ENV",
    "DEFAULT_BASE_MODEL_PATH",
    "DEFAULT_CARTOON_MODE",
    "DEFAULT_CARTOON_PATH",
    "DEFAULT_IP_ADAPTER_PATH",
    "DEFAULT_IP_ADAPTER_SUBFOLDER",
    "DEFAULT_IP_ADAPTER_WEIGHT_NAME",
    "DEFAULT_LCM_LORA_PATH",
    "DEFAULT_NEGATIVE_PROMPT",
    "DEFAULT_OUTPUT_DIM",
    "DEFAULT_TIMEOUT_SEC",
    "IP_ADAPTER_SCALE",
    "LCM_LORA_PATH_ENV",
    "MODEL_DIR_ENV",
    "OUTPUT_DIM_ENV",
    "STUB_DELAY_ENV",
    "STUB_ENV",
    "STUB_MODE_ENV",
    "TIMEOUT_ENV",
    "generate_action",
    "generate_scene",
    "reset_pipeline_cache_for_tests",
]
