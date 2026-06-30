"""Render a cartoon character portrait for each library persona.

Reuses the F.5 SD 1.5 + LCM-LoRA cartoon pipeline that produced the
118-element sprites (see :mod:`scripts.generate_element_sprites`), minus
the periodic-table-cell Pillow text overlay — a persona avatar is a pure
character portrait with no text panel.

Output: ``<output-dir>/<persona_id>.png`` (512x512), default
``data/images/persona_art``. Install step is separate so the operator can
eyeball the renders before overwriting the committed avatars.

Per-persona prompts are hand-authored from each persona's system_prompt
(``src/toybox/personas/library/<id>.json``) so the portrait matches the
character the kiosk voices. Seed is derived from the persona id (sha256)
so re-runs are visually stable, matching the element-sprite convention.

Run on F.5-capable hardware:
    uv run --extra image_gen python scripts/generate_persona_avatars.py
    uv run --extra image_gen python scripts/generate_persona_avatars.py --ids wizard
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import sys
import time
from pathlib import Path
from typing import Any

_MODEL_DIR = Path("data/models/image_gen")
_SD15_BASE_DIR = _MODEL_DIR / "sd15" / "base"
_LCM_LORA_DIR = _MODEL_DIR / "sd15" / "lcm_lora"
_CARTOON_CHECKPOINT_DIR = _MODEL_DIR / "cartoon_checkpoint"

_DEFAULT_OUTPUT_DIR = Path("data/images/persona_art")
_OUTPUT_DIM = 512
_SEED_MODULUS = 2**31

# Character portraits drawn from each persona's system_prompt. "centered
# portrait, facing forward" biases SD toward a clean single-subject avatar;
# the per-persona accent color echoes the v1 placeholder tile so the kiosk's
# persona identity stays recognizable.
_PERSONA_PROMPTS: dict[str, str] = {
    "wizard": (
        "Marvelous the Wizard, a kindly old cartoon wizard with a tall pointy "
        "purple hat dotted with stars, a long fluffy white beard and a crinkly "
        "warm smile, holding a glowing magic wand. Soft watercolor background, "
        "centered character portrait facing forward, friendly children's book "
        "illustration style."
    ),
    "detective": (
        "Inspector Pip, a cheerful little cartoon detective, big curious eyes and "
        "a friendly grin, wearing a tan deerstalker hat and coat, holding up a "
        "large round magnifying glass. Soft watercolor background, centered "
        "character portrait facing forward, friendly children's book "
        "illustration style."
    ),
    "princess": (
        "Princess Lyra, a brave and friendly young cartoon princess with a small "
        "golden crown and a flowing rose-pink gown, warm welcoming smile. Soft "
        "watercolor background, centered character portrait facing forward, "
        "friendly children's book illustration style."
    ),
    "periodic_table": (
        "Professor Iridia, a friendly cartoon scientist teacher with curly hair "
        "and round glasses, wearing a teal-trimmed lab coat, delighted curious "
        "smile, holding a small glowing science beaker. Soft watercolor "
        "background, centered character portrait facing forward, friendly "
        "children's book illustration style."
    ),
}

_NEGATIVE_PROMPT = (
    "photorealistic, 3d, blurry, smooth shading, antialiased, gradient, "
    "text, letters, numbers, writing, symbols, watermark, multiple characters, "
    "extra limbs, deformed hands"
)

_MISSING_WEIGHTS_HINT = (
    "F.5 model weights appear to be missing or unreadable. Run the per-component "
    "download scripts first:\n"
    "  uv run python scripts/f5_download_sd15.py\n"
    "  uv run python scripts/f5_download_lcm.py\n"
    "  uv run python scripts/f5_download_cartoon_checkpoint.py\n"
    "See documentation/operator/image-gen-runtime.md for the full setup."
)

_logger = logging.getLogger("generate_persona_avatars")


def _derive_seed(persona_id: str) -> int:
    digest = hashlib.sha256(persona_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % _SEED_MODULUS


def _build_pipeline() -> Any:
    try:
        import torch
        from diffusers import LCMScheduler, StableDiffusionPipeline
    except ImportError as exc:
        raise RuntimeError(
            "image_gen extras not installed. Run `uv sync --extra image_gen` and retry."
        ) from exc

    if not _CARTOON_CHECKPOINT_DIR.exists() and not _SD15_BASE_DIR.exists():
        raise RuntimeError(_MISSING_WEIGHTS_HINT)
    if not _LCM_LORA_DIR.exists():
        raise RuntimeError(_MISSING_WEIGHTS_HINT)

    base_path = _CARTOON_CHECKPOINT_DIR if _CARTOON_CHECKPOINT_DIR.exists() else _SD15_BASE_DIR
    _logger.info("loading pipeline from %s", base_path)
    pipe = StableDiffusionPipeline.from_pretrained(  # type: ignore[no-untyped-call]
        str(base_path),
        torch_dtype=torch.float16,
        variant="fp16",
        use_safetensors=True,
        local_files_only=True,
        safety_checker=None,
        requires_safety_checker=False,
    )
    pipe.load_lora_weights(str(_LCM_LORA_DIR), adapter_name="lcm")
    pipe.set_adapters(["lcm"], adapter_weights=[1.0])
    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)  # type: ignore[no-untyped-call]
    pipe.to("cuda")
    pipe.vae.enable_slicing()
    return pipe


def _render_one(pipe: Any, prompt: str, seed: int) -> bytes:
    import torch

    generator = torch.Generator("cuda").manual_seed(seed)
    result = pipe(
        prompt=prompt,
        negative_prompt=_NEGATIVE_PROMPT,
        generator=generator,
        num_inference_steps=4,
        guidance_scale=1.0,
        height=_OUTPUT_DIM,
        width=_OUTPUT_DIM,
    )
    buffer = io.BytesIO()
    result.images[0].save(buffer, format="PNG")
    return bytes(buffer.getvalue())


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a cartoon portrait per library persona.")
    parser.add_argument("--ids", nargs="+", metavar="ID", help="Render only these persona ids.")
    parser.add_argument("--force", action="store_true", help="Re-render even if the PNG exists.")
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)

    ids = args.ids if args.ids else list(_PERSONA_PROMPTS)
    unknown = [pid for pid in ids if pid not in _PERSONA_PROMPTS]
    if unknown:
        _logger.error("unknown persona id(s): %s; valid: %s", unknown, list(_PERSONA_PROMPTS))
        return 2

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    pipe: Any = None
    rendered = 0
    failed: list[str] = []
    started = time.monotonic()

    for index, pid in enumerate(ids, start=1):
        out_path = output_dir / f"{pid}.png"
        if out_path.exists() and not args.force:
            _logger.info("[%d/%d] %s -> skip (exists)", index, len(ids), pid)
            continue
        if pipe is None:
            try:
                pipe = _build_pipeline()
            except RuntimeError as exc:
                _logger.error("pipeline construction failed: %s", exc)
                return 3
        seed = _derive_seed(pid)
        t0 = time.monotonic()
        try:
            png = _render_one(pipe, _PERSONA_PROMPTS[pid], seed)
        except Exception as exc:  # noqa: BLE001 -- per-persona resilience
            if "OutOfMemory" in type(exc).__name__ or "OOM" in type(exc).__name__:
                _logger.error("[%d/%d] %s: GPU OUT OF MEMORY — aborting", index, len(ids), pid)
                raise
            _logger.exception("[%d/%d] %s render failed", index, len(ids), pid)
            failed.append(pid)
            continue
        out_path.write_bytes(png)
        rendered += 1
        _logger.info(
            "[%d/%d] %s -> %s (%.1fs)", index, len(ids), pid, out_path, time.monotonic() - t0
        )

    _logger.info(
        "summary: rendered=%d failed=%d total=%d wall=%.1fs",
        rendered,
        len(failed),
        len(ids),
        time.monotonic() - started,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
