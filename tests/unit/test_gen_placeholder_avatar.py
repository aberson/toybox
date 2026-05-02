"""Smoke + determinism tests for the placeholder PNG generator.

Mirrors ``tests/unit/test_gen_error_codes_ts.py``: pin the byte-level
contract so we can trust the committed avatar PNGs are reproducible.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from gen_placeholder_avatar import (  # type: ignore[import-not-found]  # noqa: E402
    PNG_SIGNATURE,
    write_solid_png,
)


def test_write_solid_png_produces_valid_png(tmp_path: Path) -> None:
    """Header + IHDR are well-formed for a tiny solid-color image."""
    out = tmp_path / "x.png"
    write_solid_png(out, width=4, height=4, rgb=(1, 2, 3))

    assert out.is_file()
    data = out.read_bytes()
    assert data.startswith(PNG_SIGNATURE), "PNG signature missing"

    # The first chunk after the 8-byte signature must be IHDR. Layout:
    #   length (4) | "IHDR" (4) | payload (13) | CRC (4)
    chunk_length = struct.unpack(">I", data[8:12])[0]
    chunk_type = data[12:16]
    assert chunk_type == b"IHDR"
    assert chunk_length == 13

    payload = data[16:29]
    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", payload
    )
    assert width == 4
    assert height == 4
    assert bit_depth == 8
    assert color_type == 2  # RGB, no alpha
    assert compression == 0
    assert filter_method == 0
    assert interlace == 0


def test_write_solid_png_is_deterministic(tmp_path: Path) -> None:
    """Two back-to-back regens of the same PNG must be byte-identical."""
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    write_solid_png(a, width=16, height=16, rgb=(91, 58, 142))
    write_solid_png(b, width=16, height=16, rgb=(91, 58, 142))
    assert a.read_bytes() == b.read_bytes()
