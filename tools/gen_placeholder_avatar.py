"""Hand-rolled solid-color RGB PNG writer.

Phase A Step 3 ships placeholder persona avatars before artist work lands.
Pillow is a heavy dependency for what amounts to "a square of one color", so
this script writes the file directly with :mod:`struct` + :mod:`zlib`.

Usage::

    python tools/gen_placeholder_avatar.py \
        --out src/toybox/personas/library/avatars/wizard.png \
        --color 5b3a8e --size 256

The generator is the audit trail for the committed PNG bytes; rerun it any
time and the output should be byte-identical (zlib's deterministic default
compression on the same raw stream).
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path
from typing import Final

PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    cleaned = value.lstrip("#").strip()
    if len(cleaned) != 6:
        raise ValueError(f"--color must be 6 hex digits (e.g. 5b3a8e); got {value!r}")
    try:
        red = int(cleaned[0:2], 16)
        green = int(cleaned[2:4], 16)
        blue = int(cleaned[4:6], 16)
    except ValueError as exc:
        raise ValueError(f"--color must be valid hex; got {value!r}") from exc
    return red, green, blue


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def write_solid_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """Write a solid-color RGB PNG of ``width`` x ``height`` to ``path``.

    Bit depth 8, color type 2 (RGB, no alpha), no interlace, filter type 0
    (None) on every row. The raw image stream is one filter byte per row
    followed by ``width`` RGB triples, then zlib-compressed as the IDAT
    payload.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width and height must be positive; got {width}x{height}")

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    idat = zlib.compress(raw)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(PNG_SIGNATURE)
        fh.write(_png_chunk(b"IHDR", ihdr))
        fh.write(_png_chunk(b"IDAT", idat))
        fh.write(_png_chunk(b"IEND", b""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_placeholder_avatar.py",
        description="Write a solid-color RGB PNG using only stdlib (struct + zlib).",
    )
    parser.add_argument("--out", required=True, type=Path, help="Output PNG path.")
    parser.add_argument(
        "--color",
        required=True,
        type=str,
        help="Fill color as 6 hex digits, e.g. 5b3a8e (with or without leading '#').",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=256,
        help="Square edge length in pixels (default: 256).",
    )
    args = parser.parse_args(argv)

    rgb = _parse_hex_color(args.color)
    write_solid_png(args.out, args.size, args.size, rgb)
    print(f"wrote {args.out} ({args.size}x{args.size}, rgb={rgb})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PNG_SIGNATURE", "main", "write_solid_png"]
