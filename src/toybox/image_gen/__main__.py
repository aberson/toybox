"""CLI entry point — F1's smoke probe + post-driver-bump regression seam.

Usage::

    uv run python -m toybox.image_gen --probe <toy_id> --slot <slot> [--use-stub]

Behavior:

1. Resolve the toy via :func:`toybox.db.connect` +
   :func:`toybox.db.resolve_db_path`.
2. Build a :class:`GenerationContext` from the toy row (display
   name, persona display-name JOIN, tags).
3. Read the source photo bytes via
   :func:`toybox.storage.images.on_disk_image_path`.
4. Call :func:`generate_action` and time it.
5. Save PNG to ``data/images/toy_actions/<toy_id>/<slot>.png``.
6. Write marker file ``data/models/image_gen/.probe-pass-<iso>.json``.
7. Exit 0 on success, non-zero with a structured stderr envelope on
   failure.

``--use-stub`` sets ``TOYBOX_IMAGE_GEN_STUB=1`` BEFORE the pipeline
import so the stub-injection envelope short-circuits the heavy
imports. Used by the CLI-end-to-end test in
``tests/unit/image_gen/test_cli.py`` plus operators on hosts that
don't have a GPU yet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from ..db import connect, resolve_db_path
from ..storage.images import on_disk_image_path
from .models import ACTION_SLOTS, GenerationContext

_logger = logging.getLogger(__name__)

# Default subdir under data/images/ for action sprites. Mirrors plan
# §"File layout" — the static-files mount serves these to the kiosk.
_TOY_ACTIONS_SUBDIR = "toy_actions"
_DEFAULT_DATA_ROOT = Path("data")

# UUIDv4 regex (case-insensitive). argparse accepts any string for
# ``--probe``, so we re-validate before letting ``toy_id`` reach the
# filesystem. A value like ``../../etc`` would otherwise escape
# ``data/images/toy_actions/``. The canonical helper for the storage
# layer arrives in F3; F2 inlines the check here.
_UUID4_RE: re.Pattern[str] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _validate_toy_id(toy_id: str) -> None:
    """Reject any ``toy_id`` that isn't a canonical UUIDv4.

    Raises:
        ValueError: When ``toy_id`` doesn't match the UUIDv4 pattern.
            The CLI maps ``ValueError`` to exit code 2 (same as
            argparse's invalid-argument exit, since both are
            "user-supplied bad input").
    """
    if not _UUID4_RE.match(toy_id):
        raise ValueError(f"toy_id {toy_id!r} is not a valid UUIDv4")


def _data_root() -> Path:
    raw = os.environ.get("TOYBOX_DATA_DIR")
    return Path(raw) if raw else _DEFAULT_DATA_ROOT


def _output_dir(toy_id: str) -> Path:
    return _data_root() / "images" / _TOY_ACTIONS_SUBDIR / toy_id


def _model_dir() -> Path:
    raw = os.environ.get("TOYBOX_IMAGE_GEN_MODEL_DIR")
    return Path(raw) if raw else _data_root() / "models" / "image_gen"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toybox.image_gen",
        description=(
            "Generate one toy-action sprite end-to-end. F1's smoke probe "
            "+ post-driver-bump regression seam."
        ),
    )
    parser.add_argument(
        "--probe",
        required=True,
        metavar="TOY_ID",
        help="UUID of an existing toy row in toybox.db.",
    )
    parser.add_argument(
        "--slot",
        required=True,
        choices=ACTION_SLOTS,
        help="Action slot (one of the 10 fixed vocabulary keys).",
    )
    parser.add_argument(
        "--use-stub",
        action="store_true",
        help=(
            "Use the deterministic test-stub pipeline (no GPU required). "
            "Sets TOYBOX_IMAGE_GEN_STUB=1 for this process."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional explicit seed; defaults to secrets.randbits(64).",
    )
    return parser


def _load_toy_context(
    conn: sqlite3.Connection,
    toy_id: str,
) -> tuple[GenerationContext, str]:
    """Return ``(GenerationContext, image_path)`` for the given toy.

    Raises:
        LookupError: When no row matches.
    """
    row = conn.execute(
        """
        SELECT
            t.display_name AS toy_display_name,
            t.image_path   AS image_path,
            t.tags         AS tags,
            p.display_name AS persona_display_name
        FROM toys AS t
        LEFT JOIN personas AS p ON p.id = t.persona_id
        WHERE t.id = ? AND t.archived = 0
        LIMIT 1
        """,
        (toy_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"no live toy with id={toy_id!r}")
    if row["image_path"] is None:
        # NULL image_path means the toy row exists but has no
        # committed reference photo — actionable for the operator.
        # Raising LookupError keeps the CLI's exit-code mapping
        # consistent with the no-row-found branch.
        raise LookupError(f"toy {toy_id!r} has no committed image")
    raw_tags = row["tags"]
    tags: tuple[str, ...]
    if raw_tags:
        # Tags are stored as a JSON array per Phase A schema.
        try:
            parsed = json.loads(raw_tags)
            tags = tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()
        except (ValueError, TypeError):
            tags = ()
    else:
        tags = ()
    ctx = GenerationContext(
        toy_display_name=str(row["toy_display_name"]),
        persona_display_name=(
            str(row["persona_display_name"]) if row["persona_display_name"] else None
        ),
        tags=tags,
    )
    return ctx, str(row["image_path"])


async def _run(args: argparse.Namespace) -> int:
    toy_id: str = args.probe
    slot: str = args.slot
    seed: int = args.seed if args.seed is not None else secrets.randbits(64)

    # Validate BEFORE any path construction or DB lookup so a
    # traversal-shaped ``toy_id`` cannot escape ``data/images/toy_actions/``.
    _validate_toy_id(toy_id)

    if args.use_stub:
        # MUST be set before the pipeline import path executes its
        # stub-active branch. Pipeline reads the env per-call, so
        # setting it here is sufficient.
        os.environ["TOYBOX_IMAGE_GEN_STUB"] = "1"

    # Defer the pipeline import until after the env knob is set so
    # the stub-active branch fires correctly.
    from .pipeline import generate_action

    db_path = resolve_db_path()
    conn = connect(db_path)
    try:
        ctx, stored_image_path = _load_toy_context(conn, toy_id)
    finally:
        conn.close()

    on_disk = on_disk_image_path(stored_image_path)
    reference_bytes = on_disk.read_bytes()

    started = time.monotonic()
    try:
        png_bytes = await generate_action(reference_bytes, slot, seed, ctx)
    except Exception as exc:
        _logger.error("image_gen probe failed for toy=%s slot=%s: %s", toy_id, slot, exc)
        envelope = {
            "ok": False,
            "toy_id": toy_id,
            "slot": slot,
            "seed": seed,
            "error": type(exc).__name__,
            "message": str(exc),
        }
        sys.stderr.write(json.dumps(envelope) + "\n")
        return 1
    wall_clock_secs = time.monotonic() - started

    out_dir = _output_dir(toy_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slot}.png"
    out_path.write_bytes(png_bytes)

    marker_dir = _model_dir()
    marker_dir.mkdir(parents=True, exist_ok=True)
    iso_now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    marker_path = marker_dir / f".probe-pass-{iso_now}.json"
    marker_path.write_text(
        json.dumps(
            {
                "toy_id": toy_id,
                "slot": slot,
                "seed": seed,
                "peak_vram_gb": None if args.use_stub else _peak_vram_gb_safe(),
                "wall_clock_secs": round(wall_clock_secs, 3),
                "output_path": str(out_path),
                "stub": bool(args.use_stub),
                "iso_utc": iso_now,
            },
            indent=2,
        )
        + "\n"
    )
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "toy_id": toy_id,
                "slot": slot,
                "seed": seed,
                "wall_clock_secs": round(wall_clock_secs, 3),
                "output_path": str(out_path),
                "marker_path": str(marker_path),
            }
        )
        + "\n"
    )
    return 0


def _peak_vram_gb_safe() -> float | None:
    """Return torch's peak-allocated VRAM in GB, or ``None`` on miss.

    Imported lazily so the stub-only CLI invocation never pulls torch.
    """
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        if not torch.cuda.is_available():
            return None
        peak_bytes = torch.cuda.max_memory_allocated()
    except Exception:  # pragma: no cover — defensive
        return None
    return float(peak_bytes) / float(1024**3)


def main(argv: list[str] | None = None) -> int:
    """Module entry point. Returns the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except ValueError as exc:
        # Bad user input (e.g. non-UUIDv4 ``--probe``). Mirror
        # argparse's exit-code-2 convention for invalid arguments.
        sys.stderr.write(
            json.dumps({"ok": False, "error": "invalid_argument", "message": str(exc)}) + "\n"
        )
        return 2
    except LookupError as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": "lookup", "message": str(exc)}) + "\n")
        return 2
    except FileNotFoundError as exc:
        sys.stderr.write(
            json.dumps({"ok": False, "error": "missing_file", "message": str(exc)}) + "\n"
        )
        return 3


if __name__ == "__main__":
    sys.exit(main())
