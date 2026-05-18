"""Phase M Step M2 — render one cartoon sprite per chemical element.

One-shot CLI that walks the 118-element corpus loaded by
:mod:`toybox.activities.element_corpus` and renders one 512×512 PNG per
element using the F.5 SD 1.5 + LCM-LoRA Tier B pipeline.

Output: ``data/images/elements/<element.id>.png`` (e.g. ``au-79.png``).

Mirrors the loader pattern in :mod:`scripts.f5_load_smoke` and the
generation parameters in :mod:`toybox.image_gen.pipeline` (4 steps,
guidance_scale=1.0, 512×512, fp16, ``safety_checker=None``). Unlike
the worker's per-toy pipeline this script does NOT do rembg cutout or
palette extraction — element sprites are pure text-to-image and need
no reference photo.

The operator runs this on F.5-capable hardware (see
``documentation/operator/image-gen-runtime.md``). Pre-render gate
(plan §5.2): ``--ids h-1 au-79 u-92`` first, then the full 118-element
soak unflagged.

Seed derivation (plan §8 "118 sprites style drift" mitigation):
``seed = int(sha256(element.id).hexdigest(), 16) % (2**31)`` — fixed
per-element so re-renders are visually consistent across runs and
the operator can iterate on prompt tweaks without losing the canonical
style anchor. SQLite's signed-INTEGER range is not relevant here (no
DB row); ``2**31`` keeps it inside ``torch.Generator.manual_seed``'s
practical range without overflow concerns.

The 14 canonical sprites committed to git are listed in
``.gitignore`` (one per :class:`toybox.activities.element_corpus.Family`
enum value plus the four "popular individual" elements:
gold / helium / oxygen / iron, plus carbon as the everywhere-essential).
Other 104 sprites are rendered locally and stay out of the repo.

Error handling per plan M2 done-when:

* GPU OOM is logged + re-raised; the script does NOT silently swallow
  OOM (the operator needs to see it to drop batch size or accept the
  Tier C fallback).
* Per-element failures (prompt-length issues, transient diffusers
  errors) log + continue to the next element so one bad seed doesn't
  abort a 118-element soak.
* Missing model weights surface a pointer to ``scripts/f5_download_sd15.py``
  + ``scripts/f5_download_lcm.py`` at the top of the error message.
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

# The project's pyproject.toml restricts mypy to src/ + tests/, so a
# per-file invocation against this script doesn't see the toybox
# package's py.typed marker; suppress the resulting import-untyped
# warning locally. Production imports inside src/ are unaffected.
from toybox.activities.element_corpus import (  # type: ignore[import-untyped]
    Element,
    load_elements,
)

# Mirror the F.5 pipeline's model-dir defaults so the script honours
# the same on-disk layout the worker uses. We deliberately do NOT read
# the env overrides (TOYBOX_IMAGE_GEN_BASE_MODEL_PATH etc.) — this is
# operator-tooling, not the production pipeline, and the env knobs are
# meant for the running service.
_MODEL_DIR = Path("data/models/image_gen")
_SD15_BASE_DIR = _MODEL_DIR / "sd15" / "base"
_LCM_LORA_DIR = _MODEL_DIR / "sd15" / "lcm_lora"
_CARTOON_CHECKPOINT_DIR = _MODEL_DIR / "cartoon_checkpoint"

_DEFAULT_OUTPUT_DIR = Path("data/images/elements")
_OUTPUT_DIM = 512  # plan §5.2 spec — 512×512 PNG per element

# Prompt template per plan §5.2 (revised 2026-05-18 M2b operator session).
# The original plan-frozen prompt asked SD to render a card showing the
# element symbol + atomic number; SD 1.5 + 4-step LCM cannot render
# legible glyphs at 512², so the card-text reliably came out as mush
# (operator-spot-checked au-79.png pre-revision). Revised approach: SD
# renders a clean Iridia-with-element composition with NO text; the
# symbol + atomic number + name are post-rendered via Pillow as a
# rounded white overlay panel (see :func:`_overlay_text`).
#
# Placeholders {name} and {color_description} are substituted from
# each :class:`Element`'s fields. The element name (not symbol) is used
# to bias SD toward depicting the substance itself rather than a
# letterform.
_PROMPT_TEMPLATE = (
    "Professor Iridia, a friendly cartoon scientist with curly hair and "
    "round glasses, smiling warmly, holding up a glowing orb of "
    "{name} in {color_description}. Soft watercolor background, "
    "friendly atmosphere, children's book illustration style."
)

# Extends toybox.image_gen.pipeline.DEFAULT_NEGATIVE_PROMPT with explicit
# text/glyph suppression terms. SD's tendency to scatter pseudo-letters
# across clothing and backgrounds is a separate failure mode from the
# card text — these terms cut both. The Pillow overlay (post-render) is
# the canonical text layer.
_NEGATIVE_PROMPT = (
    "photorealistic, 3d, blurry, smooth shading, antialiased, gradient, "
    "text, letters, numbers, writing, symbols, watermark"
)

# Font filenames resolved via Pillow's font path search (Windows: walks
# C:\Windows\Fonts; Linux/macOS: walks platform-standard dirs).
# Comic Sans is shipped on every Windows install and is the kid-friendly
# default for the kiosk persona avatars; staying consistent here keeps
# the overlay legible at iPad-mounted sprite sizes.
_FONT_SYMBOL = "comicbd.ttf"  # bold, big — element symbol
_FONT_NAME = "comicbd.ttf"  # bold, medium — element name
_FONT_NUMBER = "comic.ttf"  # regular, small — atomic number

# Plan §8 risk row "118 sprites style drift" mitigation: derive a
# stable per-element seed from the element id so re-runs are
# byte-comparable. 2**31 keeps the seed in a comfortable range for
# torch.Generator.manual_seed.
_SEED_MODULUS = 2**31

_MISSING_WEIGHTS_HINT = (
    "F.5 model weights appear to be missing or unreadable. Run the per-component "
    "download scripts first:\n"
    "  uv run python scripts/f5_download_sd15.py\n"
    "  uv run python scripts/f5_download_lcm.py\n"
    "  uv run python scripts/f5_download_cartoon_checkpoint.py\n"
    "See documentation/operator/image-gen-runtime.md for the full setup."
)

_logger = logging.getLogger("generate_element_sprites")


def _derive_seed(element_id: str) -> int:
    """Deterministic id → seed mapping per plan §8 style-drift mitigation."""
    digest = hashlib.sha256(element_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % _SEED_MODULUS


def _format_prompt(element: Element) -> str:
    """Substitute the element fields into the plan §5.2 prompt template."""
    return _PROMPT_TEMPLATE.format(
        name=element.name,
        color_description=element.color_description,
    )


def _build_pipeline() -> Any:
    """Construct the SD 1.5 + LCM-LoRA pipeline (Mode A — checkpoint).

    Mirrors :func:`scripts.f5_load_smoke.smoke_mode_a` and
    :func:`toybox.image_gen.pipeline._build_pipeline` (checkpoint
    branch). Heavy imports are local so ``--help`` works without
    diffusers / torch installed.

    Raises:
        RuntimeError: If model weights are missing on disk. The error
            message points at the F.5 download scripts.
    """
    try:
        import torch
        from diffusers import LCMScheduler, StableDiffusionPipeline
    except ImportError as exc:
        raise RuntimeError(
            "image_gen extras not installed. Run `uv sync --extra image_gen` "
            "and retry. See documentation/operator/image-gen-runtime.md."
        ) from exc

    if not _CARTOON_CHECKPOINT_DIR.exists() and not _SD15_BASE_DIR.exists():
        raise RuntimeError(_MISSING_WEIGHTS_HINT)
    if not _LCM_LORA_DIR.exists():
        raise RuntimeError(_MISSING_WEIGHTS_HINT)

    # Prefer the cartoon checkpoint (Mode A — same as production
    # default cartoon_mode). Fall back to SD 1.5 base if the cartoon
    # checkpoint isn't present; the prompt explicitly names "children's
    # book illustration style" so plain SD 1.5 still produces useful
    # output for the operator gate.
    base_path = (
        _CARTOON_CHECKPOINT_DIR
        if _CARTOON_CHECKPOINT_DIR.exists()
        else _SD15_BASE_DIR
    )
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


def _overlay_text(image: Any, element: Element) -> Any:
    """Composite the periodic-table-cell text overlay onto a rendered sprite.

    Layout (bottom-left rounded white panel, 42% × 30% of the canvas):

    * Atomic number small, top-left of panel (e.g. ``79``, no hash).
    * Element symbol big, horizontally + vertically centered (e.g. ``Au``).
    * Element name medium, bottom-centered (e.g. ``Gold``).

    The panel carries a soft drop shadow so the text reads cleanly over
    any background (gold-orb yellow, hydrogen blue, uranium grey all
    spot-checked). PIL is imported locally per the module convention
    (see :func:`_build_pipeline` docstring) so ``--help`` works
    without Pillow installed.
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    base = image.convert("RGBA")
    canvas_w, canvas_h = base.size

    panel_w = int(canvas_w * 0.42)
    panel_h = int(canvas_h * 0.30)
    pad = int(canvas_w * 0.04)
    x0, y0 = pad, canvas_h - panel_h - pad

    # Soft drop-shadow (blurred black rounded rect offset down-right by 6px).
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x0 + 6, y0 + 6, x0 + panel_w + 6, y0 + panel_h + 6),
        radius=24,
        fill=(0, 0, 0, 110),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))

    # Mostly-opaque white panel (240/255 alpha — a touch of background
    # bleed-through keeps the panel from looking pasted-on).
    panel = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        (x0, y0, x0 + panel_w, y0 + panel_h),
        radius=24,
        fill=(255, 255, 255, 240),
    )

    base = Image.alpha_composite(base, shadow)
    base = Image.alpha_composite(base, panel)

    draw = ImageDraw.Draw(base)
    sym_font = ImageFont.truetype(_FONT_SYMBOL, int(panel_h * 0.55))
    name_font = ImageFont.truetype(_FONT_NAME, int(panel_h * 0.22))
    num_font = ImageFont.truetype(_FONT_NUMBER, int(panel_h * 0.18))

    # Atomic number, top-left of panel, no hash prefix.
    draw.text(
        (x0 + int(panel_w * 0.08), y0 + int(panel_h * 0.08)),
        str(element.atomic_number),
        fill=(120, 120, 120, 255),
        font=num_font,
    )

    # Element symbol, h+v centered. Subtract textbbox[1] so the rendered
    # glyph is visually centered (not its metric box).
    sym_bb = draw.textbbox((0, 0), element.symbol, font=sym_font)
    sym_w, sym_h = sym_bb[2] - sym_bb[0], sym_bb[3] - sym_bb[1]
    draw.text(
        (
            x0 + (panel_w - sym_w) // 2 - sym_bb[0],
            y0 + (panel_h - sym_h) // 2 - sym_bb[1],
        ),
        element.symbol,
        fill=(20, 20, 20, 255),
        font=sym_font,
    )

    # Element name, bottom-centered.
    name_bb = draw.textbbox((0, 0), element.name, font=name_font)
    name_w = name_bb[2] - name_bb[0]
    name_h = name_bb[3] - name_bb[1]
    draw.text(
        (x0 + (panel_w - name_w) // 2, y0 + panel_h - name_h - int(panel_h * 0.10)),
        element.name,
        fill=(60, 60, 60, 255),
        font=name_font,
    )

    return base.convert("RGB")


def _render_one(pipe: Any, element: Element, seed: int) -> bytes:
    """Generate one PNG for the element. Returns PNG bytes.

    Two-step pipeline: SD 1.5 + LCM renders the cartoon composition,
    then :func:`_overlay_text` composites the periodic-table-cell text
    on a rounded white panel (see that function's docstring for why
    text is overlaid rather than prompted).

    Raises whatever the underlying pipeline raises (CUDA OOM,
    diffusers internal errors); the caller decides whether to abort
    or continue.
    """
    import torch

    prompt = _format_prompt(element)
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
    image = _overlay_text(result.images[0], element)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return bytes(buffer.getvalue())


def _select_targets(
    *,
    ids: list[str] | None,
    sample: int | None,
) -> tuple[Element, ...]:
    """Apply --ids / --sample filters to the corpus.

    --ids and --sample are mutually exclusive (argparse enforces it).
    With no flags, all 118 elements are returned. Sort order:

    * --ids: stable input order (operator typed ``h-1 au-79 u-92``).
    * --sample N: by atomic_number ascending (so ``--sample 3`` picks
      hydrogen / helium / lithium reliably).
    * No flag: by atomic_number ascending for stable progress logging.
    """
    corpus = load_elements()
    if ids is not None:
        by_id = {e.id: e for e in corpus}
        missing = [eid for eid in ids if eid not in by_id]
        if missing:
            raise ValueError(
                f"unknown element id(s): {missing!r}; "
                f"valid ids look like 'h-1', 'au-79'"
            )
        return tuple(by_id[eid] for eid in ids)
    sorted_corpus = sorted(corpus, key=lambda e: e.atomic_number)
    if sample is not None:
        if sample <= 0:
            raise ValueError(f"--sample must be positive, got {sample}")
        return tuple(sorted_corpus[:sample])
    return tuple(sorted_corpus)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render one cartoon sprite per chemical element (Phase M M2). "
            "Default: render all 118 elements, skipping ones that already "
            "have a PNG on disk. Output: data/images/elements/<id>.png."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-render every selected element, even if the PNG exists.",
    )
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--ids",
        nargs="+",
        metavar="ID",
        help=(
            "Render only the specified element ids (e.g. h-1 au-79 u-92). "
            "Used for the pre-render gate per plan §5.2."
        ),
    )
    target_group.add_argument(
        "--sample",
        type=int,
        metavar="N",
        help=(
            "Render only the first N elements sorted by atomic_number. "
            "Combine with --force to retry."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=(
            f"Output directory for rendered PNGs. "
            f"Default: {_DEFAULT_OUTPUT_DIR}."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        targets = _select_targets(ids=args.ids, sample=args.sample)
    except ValueError as exc:
        _logger.error("target selection failed: %s", exc)
        return 2

    total = len(targets)
    _logger.info(
        "selected %d element(s); output_dir=%s force=%s",
        total,
        output_dir,
        args.force,
    )

    pipe: Any = None
    rendered = 0
    skipped = 0
    failed_ids: list[str] = []
    started_at = time.monotonic()

    for index, element in enumerate(targets, start=1):
        out_path = output_dir / f"{element.id}.png"
        if out_path.exists() and not args.force:
            skipped += 1
            _logger.info(
                "[%d/%d] %s (%s) -> skip (exists)",
                index,
                total,
                element.id,
                element.name,
            )
            continue

        # Lazy pipeline construction: a run that ends up entirely
        # skipped (re-run with all PNGs present) should never pay the
        # multi-second weight-load cost.
        if pipe is None:
            try:
                pipe = _build_pipeline()
            except RuntimeError as exc:
                _logger.error("pipeline construction failed: %s", exc)
                return 3
            except Exception as exc:  # noqa: BLE001 -- surface to operator
                _logger.exception("unexpected pipeline construction failure")
                _logger.error("hint: %s", _MISSING_WEIGHTS_HINT)
                raise SystemExit(3) from exc

        seed = _derive_seed(element.id)
        per_started_at = time.monotonic()
        try:
            png_bytes = _render_one(pipe, element, seed)
        except Exception as exc:  # noqa: BLE001 -- per-element resilience
            # Surface CUDA OOM loudly and re-raise so the operator sees
            # it; plan M2 done-when forbids silent OOM. Detect by class
            # name to avoid a hard torch import dependency in this
            # except clause.
            exc_class = type(exc).__name__
            if "OutOfMemory" in exc_class or "OOM" in exc_class:
                _logger.error(
                    "[%d/%d] %s: GPU OUT OF MEMORY (%s) — aborting run; "
                    "drop batch size or move to Tier C composite mode",
                    index,
                    total,
                    element.id,
                    exc_class,
                )
                raise
            _logger.exception(
                "[%d/%d] %s render failed: %s",
                index,
                total,
                element.id,
                exc,
            )
            failed_ids.append(element.id)
            continue

        out_path.write_bytes(png_bytes)
        rendered += 1
        elapsed = time.monotonic() - per_started_at
        _logger.info(
            "[%d/%d] %s (%s) -> %s (%.1fs)",
            index,
            total,
            element.id,
            element.name,
            out_path,
            elapsed,
        )

    wall_clock = time.monotonic() - started_at
    _logger.info(
        "summary: rendered=%d skipped=%d failed=%d total=%d wall=%.1fs",
        rendered,
        skipped,
        len(failed_ids),
        total,
        wall_clock,
    )
    if failed_ids:
        _logger.warning("failed ids: %s", ", ".join(failed_ids))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
