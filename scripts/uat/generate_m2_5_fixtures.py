"""Generate the test image fixtures Manual M2.5 needs.

Outputs (under ``tests/fixtures/uat/m2-5/``):

* ``toy-1.png`` (800x600) — toy-ingest happy path
* ``toy-1-dup.png`` — byte-copy of ``toy-1.png`` for the dedup check
* ``room-1.jpg`` … ``room-5.jpg`` (1024x768) — bulk room-ingest
* ``room-bulk-51/photo-{1..51}.jpg`` (640x480) — bulk-cap negative path

Idempotent: skips files that already exist. Pass ``--force`` to regenerate
everything from scratch (useful when the validation rules change).

Run: ``uv run python scripts/uat/generate_m2_5_fixtures.py``
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "uat" / "m2-5"
BULK_DIR = FIXTURE_DIR / "room-bulk-51"

ROOM_PALETTE: list[tuple[str, tuple[int, int, int]]] = [
    ("living room", (200, 180, 140)),
    ("kitchen", (180, 210, 200)),
    ("bedroom", (170, 160, 200)),
    ("playroom", (220, 180, 180)),
    ("hallway", (190, 190, 190)),
]


def _draw_toy(path: Path) -> None:
    img = Image.new("RGB", (800, 600), (240, 230, 210))
    draw = ImageDraw.Draw(img)
    draw.ellipse((280, 180, 520, 420), fill=(170, 110, 70))
    draw.ellipse((310, 220, 360, 270), fill=(255, 255, 255))
    draw.ellipse((440, 220, 490, 270), fill=(255, 255, 255))
    draw.ellipse((325, 235, 345, 255), fill=(20, 20, 20))
    draw.ellipse((455, 235, 475, 255), fill=(20, 20, 20))
    draw.ellipse((380, 320, 420, 360), fill=(50, 30, 20))
    draw.text((250, 460), "M2.5 fixture: toy", fill=(80, 60, 40))
    img.save(path, "PNG")


def _draw_room(path: Path, label: str, color: tuple[int, int, int], idx: int) -> None:
    img = Image.new("RGB", (1024, 768), color)
    draw = ImageDraw.Draw(img)
    floor_color = tuple(max(0, c - 40) for c in color)
    draw.rectangle((0, 500, 1024, 768), fill=floor_color)
    draw.rectangle((100, 250, 350, 550), fill=tuple(min(255, c + 20) for c in color))
    draw.rectangle((600, 200, 900, 550), fill=tuple(min(255, c + 30) for c in color))
    draw.text((40, 40), f"M2.5 fixture #{idx}: {label}", fill=(20, 20, 20))
    img.save(path, "JPEG", quality=85)


def _draw_bulk(path: Path, idx: int) -> None:
    shade = 80 + (idx * 3) % 140
    img = Image.new("RGB", (640, 480), (shade, shade, 200))
    draw = ImageDraw.Draw(img)
    draw.text((20, 220), f"bulk #{idx}", fill=(255, 255, 255))
    img.save(path, "JPEG", quality=70)


def _ensure(path: Path, factory, force: bool) -> str:
    if path.exists() and not force:
        return f"skip {path.relative_to(REPO_ROOT)}"
    factory()
    return f"wrote {path.relative_to(REPO_ROOT)} ({path.stat().st_size} bytes)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Regenerate even if fixtures exist")
    args = parser.parse_args(argv)

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    BULK_DIR.mkdir(parents=True, exist_ok=True)

    toy = FIXTURE_DIR / "toy-1.png"
    toy_dup = FIXTURE_DIR / "toy-1-dup.png"

    print(_ensure(toy, lambda: _draw_toy(toy), args.force))
    if args.force or not toy_dup.exists():
        shutil.copyfile(toy, toy_dup)
        print(f"wrote {toy_dup.relative_to(REPO_ROOT)} (byte-copy of toy-1.png)")
    else:
        print(f"skip {toy_dup.relative_to(REPO_ROOT)}")

    for idx, (label, color) in enumerate(ROOM_PALETTE, start=1):
        room = FIXTURE_DIR / f"room-{idx}.jpg"
        print(
            _ensure(
                room,
                lambda r=room, lbl=label, c=color, i=idx: _draw_room(r, lbl, c, i),
                args.force,
            )
        )

    for idx in range(1, 52):
        bulk = BULK_DIR / f"photo-{idx:02d}.jpg"
        print(_ensure(bulk, lambda b=bulk, i=idx: _draw_bulk(b, i), args.force))

    print(f"\nfixtures ready at {FIXTURE_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
