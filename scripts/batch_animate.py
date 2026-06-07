"""Offline batch driver for toy action animations.

Two approaches are supported via ``--approach``:

  animatediff (default)
      Iterates all non-archived toys × 10 action slots and generates an
      animated WebP for each using
      :func:`toybox.image_gen.animate.generate_animation`.

  svd
      Stable Video Diffusion idle-only batch.  Reads the existing static
      ``idle.png`` cartoon sprite for each toy (NOT the raw reference
      photo), runs ``StableVideoDiffusionPipeline``, and writes the
      animated WebP to ``data/images/toy_actions/<toy_id>/idle.webp``.
      Only works with ``--slot idle`` (or when ``--slot`` is omitted,
      since SVD produces natural motion without action conditioning).
      Uses the model already on disk at ``data/models/image_gen/svd/``
      (downloaded during Phase U comparison).

Output files land at ``data/images/toy_actions/<toy_id>/<slot>.webp``.

Usage:
    uv run python scripts/batch_animate.py [--dry-run] [--toy-id UUID]
        [--slot SLOT] [--force] [--seed N] [--db PATH]
        [--approach {animatediff,svd}] [--svd-path PATH]

Flags:
    --dry-run     List planned work without generating anything; exits 0.
    --toy-id      Restrict to one toy UUID.
    --slot        Restrict to one action slot (e.g. "idle").
    --force       Overwrite existing .webp files (default: skip present ones).
    --seed        Fixed seed for reproducibility (default: 0).
    --db          Override DB path (default: TOYBOX_DB_PATH env or data/toybox.db).
    --approach    Animation approach: animatediff (default) or svd.
    --svd-path    Override SVD model dir (default: data/models/image_gen/svd/).

Notes:
    - Server must be stopped before running: the animate pipeline and static
      pipeline both load to CUDA and will OOM if both run simultaneously.
    - animatediff uses asyncio.run() — it is a standalone CLI, NOT called
      from inside a uvicorn event loop.
    - Per-job errors are caught and logged; the batch continues.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger(__name__)

# Re-export for mypy — actual import happens inside run() so top-level
# import of this standalone script stays fast.
_TOYBOX_ROOT = Path(__file__).parent.parent


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="batch_animate.py",
        description="Generate animated WebP sprites for all toys offline.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned work and exit without generating any files.",
    )
    parser.add_argument(
        "--toy-id",
        metavar="UUID",
        help="Restrict to one toy UUID.",
    )
    parser.add_argument(
        "--slot",
        metavar="SLOT",
        help="Restrict to one action slot (e.g. 'idle').",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing .webp files instead of skipping them.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        metavar="N",
        help="Fixed seed for reproducibility (default: 0).",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="Override DB path (default: TOYBOX_DB_PATH env or data/toybox.db).",
    )
    parser.add_argument(
        "--approach",
        choices=["animatediff", "svd"],
        default="animatediff",
        help="Animation approach: animatediff (default) or svd (idle slot only).",
    )
    parser.add_argument(
        "--svd-path",
        metavar="PATH",
        help="SVD model dir (default: data/models/image_gen/svd/).",
    )
    return parser.parse_args(argv)


def _query_toys(
    db_path: Path,
    toy_id: str | None,
) -> list[dict[str, str]]:
    """Return rows from the toys table: toy_id, display_name, image_path.

    Filters: archived=0, image_path IS NOT NULL.
    Optionally restricts to a single toy_id.
    """
    # Import here so module-level import stays cheap.
    sys.path.insert(0, str(_TOYBOX_ROOT / "src"))
    from toybox.db import connect

    conn = connect(db_path)
    try:
        sql = (
            "SELECT id AS toy_id, display_name, image_path "
            "FROM toys "
            "WHERE archived = 0 AND image_path IS NOT NULL"
        )
        params: list[str] = []
        if toy_id is not None:
            sql += " AND id = ?"
            params.append(toy_id)
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


_DEFAULT_SVD_PATH = _TOYBOX_ROOT / "data" / "models" / "image_gen" / "svd"


def _run_svd(args: argparse.Namespace, toys: list[dict[str, str]]) -> int:
    """SVD idle-only batch.  Sync (no asyncio) — called directly from _run()."""
    import torch
    from diffusers import StableVideoDiffusionPipeline
    from PIL import Image

    # SVD only produces natural motion; action conditioning is not possible.
    if args.slot is not None and args.slot != "idle":
        _logger.error("--approach svd only supports --slot idle (got %r)", args.slot)
        return 1

    svd_path = Path(args.svd_path) if args.svd_path else _DEFAULT_SVD_PATH
    if not svd_path.exists():
        _logger.error("SVD model not found at %s — aborting", svd_path)
        return 1

    output_root = Path("data") / "images" / "toy_actions"
    static_root = output_root  # idle.png lives at <toy_id>/idle.png

    jobs: list[dict[str, str]] = [t for t in toys]
    if args.dry_run:
        pending = 0
        for toy in jobs:
            out_path = output_root / toy["toy_id"] / "idle.webp"
            action = "skip" if out_path.exists() and not args.force else "generate"
            if action == "generate":
                pending += 1
            _logger.info("%-12s toy=%-20s slot=idle (svd)", action, toy["display_name"])
        _logger.info(
            "dry-run: %d to generate, %d to skip (--force=%s)",
            pending,
            len(jobs) - pending,
            args.force,
        )
        return 0

    _logger.info("Loading SVD pipeline from %s …", svd_path)
    pipe = StableVideoDiffusionPipeline.from_pretrained(  # type: ignore[no-untyped-call]
        str(svd_path),
        torch_dtype=torch.float16,
        variant="fp16",
        local_files_only=True,
    )
    pipe.enable_model_cpu_offload()

    generated = 0
    skipped = 0
    failed = 0

    for toy in jobs:
        toy_id = toy["toy_id"]
        display_name = toy["display_name"]

        static_png = static_root / toy_id / "idle.png"
        out_path = output_root / toy_id / "idle.webp"

        if out_path.exists() and not args.force:
            skipped += 1
            _logger.info("skip toy=%s slot=idle (already present)", display_name)
            continue

        if not static_png.exists():
            _logger.error("static PNG not found: %s — skipping", static_png)
            failed += 1
            continue

        # Composite over white background (PNG has alpha; SVD expects RGB).
        img = Image.open(static_png).convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        input_img = bg.resize((512, 512))

        t0 = time.monotonic()
        try:
            output = pipe(
                input_img,
                num_frames=16,
                num_inference_steps=25,
                decode_chunk_size=4,
                generator=torch.Generator("cpu").manual_seed(args.seed),
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            _logger.error(
                "FAILED toy=%s slot=idle elapsed=%.1fs error=%s",
                display_name,
                elapsed,
                exc,
            )
            failed += 1
            continue

        elapsed = time.monotonic() - t0
        frames = output.frames[0]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        duration_ms = 1000 // 8  # 8 fps
        frames[0].save(
            out_path,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            loop=0,
            duration=duration_ms,
        )
        generated += 1
        _logger.info(
            "OK toy=%s slot=idle seed=%d elapsed=%.1fs frames=%d",
            display_name,
            args.seed,
            elapsed,
            len(frames),
        )

    _logger.info(
        "svd batch complete: %d generated, %d skipped, %d failed",
        generated,
        skipped,
        failed,
    )
    return 0 if failed == 0 else 1


async def _run(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(_TOYBOX_ROOT / "src"))

    from toybox.db import resolve_db_path
    from toybox.image_gen.models import ACTION_SLOTS

    db_path = Path(args.db) if args.db else resolve_db_path()
    toys = _query_toys(db_path, args.toy_id)

    if not toys:
        _logger.info("No matching toys found in %s", db_path)
        return 0

    if args.approach == "svd":
        return _run_svd(args, toys)

    from toybox.image_gen.animate import generate_animation
    from toybox.image_gen.models import GenerationContext

    # Build the job list: (toy_row, slot) pairs to process.
    slots = [args.slot] if args.slot else list(ACTION_SLOTS)
    jobs: list[tuple[dict[str, str], str]] = [
        (toy, slot) for toy in toys for slot in slots
    ]

    # All paths are relative to CWD (the project root when run normally).
    output_root = Path("data") / "images" / "toy_actions"

    if args.dry_run:
        pending = 0
        for toy, slot in jobs:
            out_path = output_root / toy["toy_id"] / f"{slot}.webp"
            already = out_path.exists()
            action = "skip" if already and not args.force else "generate"
            if action == "generate":
                pending += 1
            _logger.info(
                "%-12s toy=%-20s slot=%s",
                action,
                toy["display_name"],
                slot,
            )
        _logger.info(
            "dry-run: %d to generate, %d to skip (--force=%s)",
            pending,
            len(jobs) - pending,
            args.force,
        )
        return 0

    generated = 0
    skipped = 0
    failed = 0

    for toy, slot in jobs:
        toy_id = toy["toy_id"]
        display_name = toy["display_name"]
        image_path = toy["image_path"]

        out_path = output_root / toy_id / f"{slot}.webp"
        if out_path.exists() and not args.force:
            skipped += 1
            _logger.info("skip toy=%s slot=%s (already present)", display_name, slot)
            continue

        # Read reference image from disk (image_path is project-root-relative).
        ref_path = Path(image_path)
        if not ref_path.exists():
            _logger.error(
                "reference image not found: %s — skipping slot=%s", ref_path, slot
            )
            failed += 1
            continue

        reference_bytes = ref_path.read_bytes()
        ctx = GenerationContext(
            toy_display_name=display_name,
            persona_display_name=None,
            tags=frozenset(),
        )

        t0 = time.monotonic()
        try:
            webp_bytes = await generate_animation(
                reference_bytes, slot, args.seed, ctx
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            _logger.error(
                "FAILED toy=%s slot=%s elapsed=%.1fs error=%s",
                display_name,
                slot,
                elapsed,
                exc,
            )
            failed += 1
            continue

        elapsed = time.monotonic() - t0
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(webp_bytes)
        generated += 1
        _logger.info(
            "OK toy=%s slot=%s seed=%d elapsed=%.1fs",
            display_name,
            slot,
            args.seed,
            elapsed,
        )

    _logger.info(
        "batch complete: %d generated, %d skipped, %d failed",
        generated,
        skipped,
        failed,
    )
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
