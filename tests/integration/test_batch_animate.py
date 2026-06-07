"""Integration tests for scripts/batch_animate.py.

All tests run without GPU: --dry-run uses no image-gen pipeline, and
stub-mode tests use TOYBOX_IMAGE_GEN_STUB=1.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "batch_animate.py"

_TOY_A = "aaaaaaaa-0000-0000-0000-000000000001"
_TOY_B = "bbbbbbbb-0000-0000-0000-000000000002"


def _setup_db(tmp_path: Path) -> Path:
    """Create a migrated DB with 2 non-archived toys that have image_paths."""
    db = tmp_path / "toybox.db"
    conn = connect(db)
    try:
        run_migrations(conn)
        with conn:
            for toy_id, name, img in [
                (_TOY_A, "Bunny", "data/images/toys/bunny.jpg"),
                (_TOY_B, "Dragon", "data/images/toys/dragon.jpg"),
            ]:
                conn.execute(
                    "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (toy_id, name, img, "h" + toy_id[:4], "2026-06-06T00:00:00Z"),
                )
    finally:
        conn.close()
    return db


def _seed_ref_images(tmp_path: Path) -> None:
    """Write minimal JPEG bytes at the toy image_paths under tmp_path."""
    # Minimal JFIF header that any image reader will recognize.
    fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 8
    for name in ("bunny", "dragon"):
        p = tmp_path / "data" / "images" / "toys" / f"{name}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(fake_jpeg)


def _run_script(
    *extra_args: str,
    tmp_path: Path,
    db: Path,
    stub: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TOYBOX_DB_PATH"] = str(db)
    # Resolve the script's relative paths against tmp_path by cd'ing there.
    if stub:
        env["TOYBOX_IMAGE_GEN_STUB"] = "1"
    else:
        env.pop("TOYBOX_IMAGE_GEN_STUB", None)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *extra_args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
        env=env,
    )


def test_dry_run_no_files_written(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    _seed_ref_images(tmp_path)

    result = _run_script("--dry-run", tmp_path=tmp_path, db=db)

    assert result.returncode == 0, f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    # No .webp files should exist after a dry-run.
    webps = list(tmp_path.rglob("*.webp"))
    assert webps == [], f"dry-run wrote unexpected files: {webps}"
    # Output mentions planned work for both toys.
    combined = result.stdout + result.stderr
    assert "Bunny" in combined or "bunny" in combined.lower()
    assert "Dragon" in combined or "dragon" in combined.lower()


def test_skip_existing_webp(tmp_path: Path) -> None:
    """Already-present .webp is not overwritten without --force."""
    db = _setup_db(tmp_path)
    _seed_ref_images(tmp_path)

    # Pre-populate toy A's idle slot.
    out_path = tmp_path / "data" / "images" / "toy_actions" / _TOY_A / "idle.webp"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    original_content = b"ORIGINAL"
    out_path.write_bytes(original_content)
    original_mtime = out_path.stat().st_mtime

    result = _run_script(
        "--toy-id", _TOY_A, "--slot", "idle",
        tmp_path=tmp_path, db=db, stub=True,
    )
    # The script exits 0 even if the slot was skipped.
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    # File must be unchanged.
    assert out_path.read_bytes() == original_content
    assert out_path.stat().st_mtime == original_mtime


def test_force_overwrites_existing(tmp_path: Path) -> None:
    """--force regenerates even if .webp already exists."""
    db = _setup_db(tmp_path)
    _seed_ref_images(tmp_path)

    out_path = tmp_path / "data" / "images" / "toy_actions" / _TOY_A / "idle.webp"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"OLD_CONTENT")

    result = _run_script(
        "--toy-id", _TOY_A, "--slot", "idle", "--force",
        tmp_path=tmp_path, db=db, stub=True,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert out_path.exists()
    # File must have been replaced (stub writes new WebP bytes).
    assert out_path.read_bytes() != b"OLD_CONTENT"
