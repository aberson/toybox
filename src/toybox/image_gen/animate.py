"""AnimateDiff + AnimateLCM motion adapter for offline toy action animations.

Public entry: :func:`generate_animation` — async, returns animated WebP bytes.

ALL heavy imports (``torch``, ``diffusers``, ``rembg``) live INSIDE
:func:`_run_animate_sync` so module import is cheap when the feature is
disabled. The ``test_animate_lazy_imports`` test pins this contract.

Shared pipeline constants imported from :mod:`pipeline` (single source of
truth per workspace code-quality.md):
  DEFAULT_BASE_MODEL_PATH, DEFAULT_IP_ADAPTER_PATH,
  DEFAULT_IP_ADAPTER_SUBFOLDER, DEFAULT_IP_ADAPTER_WEIGHT_NAME,
  IP_ADAPTER_SCALE, DEFAULT_NEGATIVE_PROMPT.
Do NOT redefine these here.

Stub mode: ``TOYBOX_IMAGE_GEN_STUB=1`` returns 16×1×1 animated WebP without
touching the GPU (same env var as pipeline.py).
OOM simulation: ``TOYBOX_IMAGE_GEN_STUB_MODE=oom`` raises
:class:`ImageGenCapacityError`.
Delay simulation: ``TOYBOX_IMAGE_GEN_STUB_DELAY_SEC=<N>`` sleeps N seconds
(for timeout-path tests).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import time
from typing import Any, Final

from .models import (
    ACTION_PROMPTS,
    GenerationContext,
    ImageGenCapacityError,
    ImageGenTimeoutError,
)
from .pipeline import (
    DEFAULT_BASE_MODEL_PATH,
    DEFAULT_IP_ADAPTER_PATH,
    DEFAULT_IP_ADAPTER_SUBFOLDER,
    DEFAULT_IP_ADAPTER_WEIGHT_NAME,
    DEFAULT_NEGATIVE_PROMPT,
    IP_ADAPTER_SCALE,
    STUB_DELAY_ENV,
    STUB_ENV,
    STUB_MODE_ENV,
)

_logger = logging.getLogger(__name__)

# AnimateLCM HuggingFace repo id (wangfuyun/AnimateLCM is trained for LCM
# schedulers at 4–8 steps; standard AnimateDiff adapters target 25-step DDIM
# and are incompatible with our LCM-based pipeline).
MOTION_ADAPTER_REPO: Final[str] = "wangfuyun/AnimateLCM"

MOTION_ADAPTER_PATH_ENV: Final[str] = "TOYBOX_IMAGE_GEN_MOTION_ADAPTER_PATH"
NUM_FRAMES_ENV: Final[str] = "TOYBOX_IMAGE_GEN_ANIMATE_NUM_FRAMES"
FPS_ENV: Final[str] = "TOYBOX_IMAGE_GEN_ANIMATE_FPS"
ANIMATE_OUTPUT_DIM_ENV: Final[str] = "TOYBOX_IMAGE_GEN_ANIMATE_OUTPUT_DIM"
NUM_STEPS_ENV: Final[str] = "TOYBOX_IMAGE_GEN_ANIMATE_NUM_STEPS"
TIMEOUT_ENV: Final[str] = "TOYBOX_IMAGE_GEN_ANIMATE_TIMEOUT_SEC"

DEFAULT_MOTION_ADAPTER_PATH: Final[str] = "data/models/image_gen/animatelcm"
DEFAULT_NUM_FRAMES: Final[int] = 16
DEFAULT_FPS: Final[int] = 8
# 256×256 requires 4× less VRAM per frame than 512×512 while being
# indistinguishable at 112px kiosk display size.
DEFAULT_ANIMATE_OUTPUT_DIM: Final[int] = 256
DEFAULT_NUM_STEPS: Final[int] = 8
DEFAULT_TIMEOUT_SEC: Final[float] = 300.0

# Module-level cached pipeline, separate from pipeline.py's _cached_pipeline.
_cached_animate_pipeline: Any = None


def _stub_active() -> bool:
    raw = os.environ.get(STUB_ENV)
    return raw is not None and raw.strip().lower() in {"1", "true", "yes", "on"}


def _motion_adapter_path() -> str:
    return os.environ.get(MOTION_ADAPTER_PATH_ENV) or DEFAULT_MOTION_ADAPTER_PATH


def _num_frames() -> int:
    raw = os.environ.get(NUM_FRAMES_ENV)
    if raw is None:
        return DEFAULT_NUM_FRAMES
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_NUM_FRAMES


def _fps() -> int:
    raw = os.environ.get(FPS_ENV)
    if raw is None:
        return DEFAULT_FPS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_FPS


def _animate_output_dim() -> int:
    raw = os.environ.get(ANIMATE_OUTPUT_DIM_ENV)
    if raw is None:
        return DEFAULT_ANIMATE_OUTPUT_DIM
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_ANIMATE_OUTPUT_DIM
    except ValueError:
        return DEFAULT_ANIMATE_OUTPUT_DIM


def _num_steps() -> int:
    raw = os.environ.get(NUM_STEPS_ENV)
    if raw is None:
        return DEFAULT_NUM_STEPS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_NUM_STEPS


def _timeout_sec() -> float:
    raw = os.environ.get(TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_TIMEOUT_SEC
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SEC


def _make_stub_webp() -> bytes:
    """Return a minimal animated WebP (N×1×1 transparent frames) for stub mode."""
    from PIL import Image

    n = _num_frames()
    fps = _fps()
    frames = [Image.new("RGBA", (1, 1), (0, 0, 0, 0)) for _ in range(n)]
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=1000 // fps,
    )
    return buf.getvalue()


def _build_animate_pipeline() -> Any:
    """Build AnimateDiffPipeline with AnimateLCM adapter + IP-Adapter Plus.

    Uses shared constants from pipeline.py (DEFAULT_BASE_MODEL_PATH,
    DEFAULT_IP_ADAPTER_PATH, etc.) — not redefined here.
    AnimateLCM requires ``beta_schedule="linear"`` on the LCMScheduler.
    """
    import torch
    from diffusers import AnimateDiffPipeline, LCMScheduler, MotionAdapter

    adapter = MotionAdapter.from_pretrained(  # type: ignore[no-untyped-call]
        _motion_adapter_path(), torch_dtype=torch.float16
    )
    pipe = AnimateDiffPipeline.from_pretrained(  # type: ignore[no-untyped-call]
        DEFAULT_BASE_MODEL_PATH,
        motion_adapter=adapter,
        torch_dtype=torch.float16,
        safety_checker=None,
        requires_safety_checker=False,
    )
    # AnimateLCM requires beta_schedule="linear"; standard AnimateDiff
    # adapters use DDIM — do NOT omit this parameter.
    pipe.scheduler = LCMScheduler.from_config(  # type: ignore[no-untyped-call]
        pipe.scheduler.config, beta_schedule="linear"
    )
    pipe.load_ip_adapter(
        DEFAULT_IP_ADAPTER_PATH,
        subfolder=DEFAULT_IP_ADAPTER_SUBFOLDER,
        weight_name=DEFAULT_IP_ADAPTER_WEIGHT_NAME,
    )
    pipe.set_ip_adapter_scale(IP_ADAPTER_SCALE)
    pipe.to("cuda")
    pipe.enable_vae_slicing()
    return pipe


def _pil_to_png_bytes(img: Any) -> bytes:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _run_animate_sync(
    reference_bytes: bytes,
    slot: str,
    seed: int,
    ctx: GenerationContext,
) -> bytes:
    """Synchronous worker; runs inside :func:`asyncio.to_thread`."""
    if _stub_active():
        mode = os.environ.get(STUB_MODE_ENV, "").strip().lower()
        if mode == "oom":
            raise ImageGenCapacityError(
                f"stub CUDA OOM simulated for animate slot={slot}"
            )
        delay_raw = os.environ.get(STUB_DELAY_ENV)
        if delay_raw is not None:
            try:
                time.sleep(float(delay_raw))
            except ValueError:
                pass
        return _make_stub_webp()

    import torch
    from PIL import Image
    from rembg import new_session, remove

    global _cached_animate_pipeline
    if _cached_animate_pipeline is None:
        _cached_animate_pipeline = _build_animate_pipeline()
    pipe = _cached_animate_pipeline

    bg_session = new_session(model_name="u2net", providers=["CPUExecutionProvider"])

    cutout_bytes = remove(reference_bytes, session=bg_session)
    reference_image = Image.open(io.BytesIO(cutout_bytes)).convert("RGBA")

    prompt = (
        f"cartoon character, {ACTION_PROMPTS[slot]}, cute, expressive, "
        f"{ctx.toy_display_name}"
    )

    num_frames = _num_frames()
    num_steps = _num_steps()
    output_dim = _animate_output_dim()
    fps = _fps()
    generator = torch.Generator("cuda").manual_seed(seed)

    try:
        result = pipe(
            prompt=prompt,
            negative_prompt=DEFAULT_NEGATIVE_PROMPT,
            ip_adapter_image=reference_image,
            num_frames=num_frames,
            guidance_scale=1.0,
            num_inference_steps=num_steps,
            height=output_dim,
            width=output_dim,
            generator=generator,
        )
    except torch.cuda.OutOfMemoryError as exc:
        raise ImageGenCapacityError(
            f"CUDA OOM during animation for slot={slot}"
        ) from exc

    raw_frames: list[Image.Image] = result.frames[0]

    # Per-frame rembg for clean transparency on limbs that swing outside
    # the reference silhouette. Mean-mask propagation saves rembg calls
    # but produces edge artifacts — per-frame is correct for animation.
    clean_frames = [
        Image.open(
            io.BytesIO(remove(_pil_to_png_bytes(f), session=bg_session))
        ).convert("RGBA")
        for f in raw_frames
    ]

    buf = io.BytesIO()
    clean_frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=clean_frames[1:],
        loop=0,
        duration=1000 // fps,
    )
    return buf.getvalue()


async def generate_animation(
    reference_bytes: bytes,
    slot: str,
    seed: int,
    ctx: GenerationContext,
) -> bytes:
    """Generate one animated sprite end-to-end.

    Returns animated WebP bytes (RGBA, ``DEFAULT_NUM_FRAMES`` frames at
    ``DEFAULT_FPS`` by default).

    Raises:
        ImageGenTimeoutError: When the per-call :func:`asyncio.wait_for`
            cap fires (default 300s, env-overridable via
            ``TOYBOX_IMAGE_GEN_ANIMATE_TIMEOUT_SEC``).
        ImageGenCapacityError: On real or simulated CUDA OOM.
    """
    timeout = _timeout_sec()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_animate_sync, reference_bytes, slot, seed, ctx),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise ImageGenTimeoutError(
            f"animation exceeded {timeout}s for slot={slot}"
        ) from exc


def reset_animate_cache_for_tests() -> None:
    """Drop the cached animate pipeline (test isolation helper)."""
    global _cached_animate_pipeline
    _cached_animate_pipeline = None


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m toybox.image_gen.animate [--download] [--help]``"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m toybox.image_gen.animate",
        description="AnimateDiff toy action animation pipeline utilities.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help=(
            f"Download the AnimateLCM motion adapter "
            f"({MOTION_ADAPTER_REPO}) to {DEFAULT_MOTION_ADAPTER_PATH}"
        ),
    )
    args = parser.parse_args(argv)

    if not args.download:
        parser.print_help()
        return 0

    from diffusers import MotionAdapter

    dest = _motion_adapter_path()
    _logger.info("Downloading AnimateLCM motion adapter to %s ...", dest)
    try:
        adapter = MotionAdapter.from_pretrained(MOTION_ADAPTER_REPO)  # type: ignore[no-untyped-call]
        adapter.save_pretrained(dest)
    except Exception as exc:
        _logger.error("Download failed: %s", exc, exc_info=True)
        return 1
    _logger.info("Motion adapter downloaded to %s", dest)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())


__all__ = [
    "ANIMATE_OUTPUT_DIM_ENV",
    "DEFAULT_ANIMATE_OUTPUT_DIM",
    "DEFAULT_FPS",
    "DEFAULT_MOTION_ADAPTER_PATH",
    "DEFAULT_NUM_FRAMES",
    "DEFAULT_NUM_STEPS",
    "DEFAULT_TIMEOUT_SEC",
    "FPS_ENV",
    "MOTION_ADAPTER_PATH_ENV",
    "MOTION_ADAPTER_REPO",
    "NUM_FRAMES_ENV",
    "NUM_STEPS_ENV",
    "TIMEOUT_ENV",
    "generate_animation",
    "reset_animate_cache_for_tests",
]
