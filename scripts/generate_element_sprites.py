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

# Mirror the prompt template from plan §5.2 verbatim. The placeholders
# {symbol}, {atomic_number}, {color_description} are substituted from
# each Element's fields.
_PROMPT_TEMPLATE = (
    'Professor Iridia, a friendly cartoon scientist with curly hair and '
    'round glasses, holding up a glowing card showing the element symbol '
    '"{symbol}" and the number {atomic_number}. The card glows in '
    '{color_description}. Soft watercolor background, friendly atmosphere, '
    "children's book illustration style."
)

# Match toybox.image_gen.pipeline.DEFAULT_NEGATIVE_PROMPT so the
# rendered style is consistent with the rest of the F.5 outputs.
_NEGATIVE_PROMPT = (
    "photorealistic, 3d, blurry, smooth shading, antialiased, gradient"
)

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
        symbol=element.symbol,
        atomic_number=element.atomic_number,
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


def _render_one(pipe: Any, element: Element, seed: int) -> bytes:
    """Generate one PNG for the element. Returns PNG bytes.

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
    image = result.images[0]
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
