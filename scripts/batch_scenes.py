"""Offline batch driver for the Phase Y scene-backdrop library.

Iterates the canonical scene set (:data:`toybox.activities.scene_catalog.SCENE_IDS`)
and renders one opaque cartoon backdrop PNG per scene via
:func:`toybox.image_gen.pipeline.generate_scene` (SD 1.5 + cartoon LoRA + LCM,
NO IP-Adapter, NO transparency). Output lands at
``data/images/scenes/<scene_id>.png`` — served at runtime by the existing
``/api/static/images`` mount with zero new wiring.

Usage:
    uv run python scripts/batch_scenes.py [--dry-run] [--scene SCENE_ID]
        [--force] [--seed N] [--out-dir PATH]

Flags:
    --dry-run     List planned work without generating anything; exits 0.
    --scene       Restrict to one scene id (must be in SCENE_IDS).
    --force       Overwrite existing PNGs (default: skip ones already present).
    --seed        Fixed seed for reproducibility (default: 0).
    --out-dir     Override output dir (default: data/images/scenes).

Notes:
    - Server must be stopped before running: the scene pipeline and the live
      sprite pipeline both load to CUDA and will OOM if both run at once
      (Phase U U3 lesson).
    - Standalone CLI — uses asyncio.run(); NOT called from a uvicorn loop.
    - Per-scene errors are caught and logged; the batch continues.
    - CI exercises this via TOYBOX_IMAGE_GEN_STUB=1 (deterministic placeholder
      PNGs), so no GPU is needed for the wiring test.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger(__name__)

_DEFAULT_OUT_DIR = Path("data/images/scenes")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="batch_scenes.py",
        description="Pre-render the kiosk scene-backdrop library offline.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned work and exit without generating any files.",
    )
    parser.add_argument(
        "--scene",
        metavar="SCENE_ID",
        help="Restrict to one scene id (must be in SCENE_IDS).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing PNGs instead of skipping them.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Fixed seed for reproducibility (default: 0).",
    )
    parser.add_argument(
        "--out-dir",
        metavar="PATH",
        default=str(_DEFAULT_OUT_DIR),
        help="Output directory (default: data/images/scenes).",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    """Render the scene library. Returns a process exit code (0 = success).

    Heavy generation deps load lazily inside ``generate_scene``; the imports
    here (scene_catalog, pipeline) stay cheap so ``--dry-run`` is fast and the
    standalone script imports without a GPU present.
    """
    args = _parse_args(argv)

    from toybox.activities.scene_catalog import SCENE_IDS, SCENE_PROMPTS
    from toybox.image_gen.pipeline import generate_scene

    if args.scene is not None and args.scene not in SCENE_IDS:
        _logger.error("unknown scene id %r; valid ids: %s", args.scene, ", ".join(SCENE_IDS))
        return 2

    scenes = (args.scene,) if args.scene is not None else SCENE_IDS
    out_dir = Path(args.out_dir)

    planned = 0
    generated = 0
    skipped = 0
    failed = 0

    for scene_id in scenes:
        out_path = out_dir / f"{scene_id}.png"
        if out_path.exists() and not args.force:
            _logger.info("skip %s (exists; use --force to overwrite)", out_path)
            skipped += 1
            continue
        planned += 1
        if args.dry_run:
            _logger.info("[dry-run] would render %s -> %s", scene_id, out_path)
            continue
        try:
            png_bytes = asyncio.run(generate_scene(scene_id, SCENE_PROMPTS[scene_id], args.seed))
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(png_bytes)
            _logger.info("rendered %s -> %s (%d bytes)", scene_id, out_path, len(png_bytes))
            generated += 1
        except Exception:  # noqa: BLE001 — one bad scene shouldn't kill the batch
            _logger.exception("failed to render scene %s", scene_id)
            failed += 1

    if args.dry_run:
        _logger.info("dry-run: %d scene(s) would render, %d already present", planned, skipped)
        return 0

    _logger.info(
        "done: %d generated, %d skipped, %d failed (of %d scenes)",
        generated,
        skipped,
        failed,
        len(scenes),
    )
    return 1 if failed else 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
