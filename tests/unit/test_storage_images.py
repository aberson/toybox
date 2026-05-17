"""Unit tests for the shared :mod:`toybox.storage.images` helper.

These exercise the primitives without booting FastAPI; the integration
tests in ``tests/integration/test_toys_api.py`` cover the wiring.
"""

from __future__ import annotations

import io
import os
import struct
import time
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from toybox.storage import images


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the storage helper's data root to a per-test tmp dir."""
    monkeypatch.setenv(images.DATA_ROOT_ENV, str(tmp_path))
    return tmp_path


def _jpeg_bytes(
    size: tuple[int, int] = (64, 64),
    color: tuple[int, int, int] = (200, 100, 50),
) -> bytes:
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def _png_bytes(size: tuple[int, int] = (50, 40)) -> bytes:
    img = Image.new("RGB", size, (10, 200, 100))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ---------------------------------------------------------------------
# validate_upload — happy paths (parametrised over the three formats so
# we don't ship three near-identical happy-path tests)
# ---------------------------------------------------------------------


def _webp_bytes(size: tuple[int, int] = (60, 60)) -> bytes:
    img = Image.new("RGB", size, (50, 100, 200))
    out = io.BytesIO()
    img.save(out, format="WEBP")
    return out.getvalue()


@pytest.mark.parametrize(
    ("payload_factory", "mime", "extension"),
    [
        (lambda: _jpeg_bytes(), "image/jpeg", "jpg"),
        (lambda: _png_bytes(), "image/png", "png"),
        (lambda: _webp_bytes(), "image/webp", "webp"),
    ],
)
def test_validate_upload_accepts_supported_formats(
    payload_factory: Any, mime: str, extension: str
) -> None:
    """Source-of-truth happy path for each supported MIME."""
    bytes_ = payload_factory()
    result = images.validate_upload(bytes_, mime)
    assert result.media_type == mime
    assert result.extension == extension
    assert result.byte_size == len(bytes_)


def test_validate_upload_rejects_text_with_jpeg_extension() -> None:
    """Extension trust is a security hole — sniff is the source of truth."""
    text_bytes = b"This is just text pretending to be a JPEG.\n"
    with pytest.raises(images.UploadValidationError) as exc_info:
        images.validate_upload(text_bytes, "image/jpeg")
    assert exc_info.value.code == "upload_bad_mime"
    assert exc_info.value.http_status == 415


def test_validate_upload_rejects_heic_with_clear_message() -> None:
    """HEIC is sniffed but not decodable — we reject with a friendly note."""
    # Minimal HEIC magic bytes: ftyp box at offset 4, brand 'heic'.
    fake_heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
    with pytest.raises(images.UploadValidationError) as exc_info:
        images.validate_upload(fake_heic, "image/heic")
    assert exc_info.value.code == "upload_bad_mime"
    assert "HEIC" in str(exc_info.value)


def test_sniff_mime_does_not_match_hevc_video() -> None:
    """HEVC video brands must not be classified as image/heic.

    Regression for L1: iter-1 included ``hevc`` and ``hevx`` in the
    HEIC brand list, which would have caused a video upload to surface
    as ``upload_bad_mime`` with the misleading "HEIC" message instead
    of the generic bad-mime message used for anything else.
    """
    fake_hevc = b"\x00\x00\x00\x18ftyphevc" + b"\x00" * 64
    # Either ``None`` (preferred — sniffer didn't recognise it) or a
    # non-HEIC MIME would be acceptable, but iter-2's narrower brand
    # list returns ``None``.
    assert images._sniff_mime(fake_hevc) is None


@pytest.mark.parametrize("size", [0, 1, 4, 11])
def test_sniff_mime_handles_short_bytes(size: int) -> None:
    """Length-guards must not IndexError on truncated payloads (M6).

    The sniffer's branches each carry ``len(...) >= N`` guards. This
    pins the contract: tiny payloads return ``None`` cleanly and the
    validate_upload wrapper turns that into a ``upload_bad_mime``
    rejection — never a 500 / IndexError.
    """
    payload = b"\x00" * size
    assert images._sniff_mime(payload) is None
    with pytest.raises(images.UploadValidationError) as exc_info:
        images.validate_upload(payload)
    assert exc_info.value.code == "upload_bad_mime"


def test_validate_upload_rejects_oversized_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(images.MAX_BYTES_ENV, "100")
    bytes_ = _jpeg_bytes((128, 128))
    with pytest.raises(images.UploadValidationError) as exc_info:
        images.validate_upload(bytes_)
    assert exc_info.value.code == "upload_too_large"
    assert exc_info.value.http_status == 413


def _craft_png_with_huge_header(width: int, height: int) -> bytes:
    """Synthesise a small PNG whose IHDR claims absurd dimensions.

    The pixel payload is junk — we never get past the header read in
    :func:`validate_upload` because Pillow's
    :class:`Image.DecompressionBombError` fires on ``Image.open`` when
    width*height exceeds ``MAX_IMAGE_PIXELS``.
    """
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(
        ">IIBBBBB",
        width,
        height,
        8,  # bit depth
        2,  # colour type (RGB)
        0,  # compression
        0,  # filter
        0,  # interlace
    )
    ihdr = (
        struct.pack(">I", len(ihdr_data))
        + b"IHDR"
        + ihdr_data
        + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
    )
    # Empty IDAT + IEND so Pillow at least opens the file.
    idat_data = b""
    idat = (
        struct.pack(">I", len(idat_data))
        + b"IDAT"
        + idat_data
        + struct.pack(">I", zlib.crc32(b"IDAT" + idat_data) & 0xFFFFFFFF)
    )
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return sig + ihdr + idat + iend


def test_validate_upload_handles_decompression_bomb() -> None:
    """A small file whose header claims 30000x30000 trips Pillow's bomb-guard.

    Regression for H2: ``Image.DecompressionBombError`` inherits
    directly from ``Exception`` (not ``OSError`` / ``ValueError``), so
    iter-1 leaked it as a 500. We now translate to
    ``upload_too_large_dimensions``.
    """
    payload = _craft_png_with_huge_header(30000, 30000)
    with pytest.raises(images.UploadValidationError) as exc_info:
        images.validate_upload(payload)
    assert exc_info.value.code == "upload_too_large_dimensions"
    assert exc_info.value.http_status == 413


# ---------------------------------------------------------------------
# compute_hash
# ---------------------------------------------------------------------


def test_compute_hash_is_deterministic() -> None:
    payload = _jpeg_bytes()
    h1 = images.compute_hash(payload)
    h2 = images.compute_hash(payload)
    assert h1 == h2
    # SHA-256 hex is 64 chars
    assert len(h1) == 64
    assert h1 != images.compute_hash(_jpeg_bytes(color=(0, 0, 0)))


# ---------------------------------------------------------------------
# find_dedup
# ---------------------------------------------------------------------


def test_find_dedup_matches_non_archived_toy(tmp_path: Path) -> None:
    from toybox.db.connection import connect
    from toybox.db.migrations import run_migrations

    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    try:
        run_migrations(conn)
        with conn:
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, "
                "tags, archived, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "t1",
                    "Sparkle",
                    "data/images/toys/x.jpg",
                    "abc123",
                    "plush",
                    0,
                    "2026-01-01T00:00:00Z",
                ),
            )
        match = images.find_dedup(conn, "toys", "abc123")
        assert match is not None
        assert match["id"] == "t1"

        # Archived toy with same hash → no match.
        with conn:
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, "
                "tags, archived, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "t2",
                    "Old",
                    "data/images/toys/y.jpg",
                    "def456",
                    "plush",
                    1,
                    "2026-01-01T00:00:00Z",
                ),
            )
        assert images.find_dedup(conn, "toys", "def456") is None

        # Hash that doesn't exist
        assert images.find_dedup(conn, "toys", "no-such-hash") is None
    finally:
        conn.close()


def test_find_dedup_matches_non_archived_reward(tmp_path: Path) -> None:
    """Phase L L2 mirror of ``test_find_dedup_matches_non_archived_toy``.

    Confirms the shared helper's whitelist extension to ``"rewards"``
    behaves the same as the ``"toys"`` branch: matches a live row,
    skips an archived row with the same hash, returns ``None`` for an
    unknown hash. Pins the contract per code-quality.md §2.
    """
    from toybox.db.connection import connect
    from toybox.db.migrations import run_migrations

    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    try:
        run_migrations(conn)
        with conn:
            conn.execute(
                "INSERT INTO rewards (id, display_name, image_path, image_hash, "
                "tags, animation, active, archived, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "r1",
                    "Sparkle",
                    "data/images/rewards/r1.png",
                    "abc123",
                    "[]",
                    "shine",
                    1,
                    0,
                    "2026-01-01T00:00:00Z",
                ),
            )
        match = images.find_dedup(conn, "rewards", "abc123")
        assert match is not None
        assert match["id"] == "r1"

        # Archived reward with same hash → no match.
        with conn:
            conn.execute(
                "INSERT INTO rewards (id, display_name, image_path, image_hash, "
                "tags, animation, active, archived, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "r2",
                    "Old",
                    "data/images/rewards/r2.png",
                    "def456",
                    "[]",
                    "spin",
                    1,
                    1,
                    "2026-01-01T00:00:00Z",
                ),
            )
        assert images.find_dedup(conn, "rewards", "def456") is None

        # Hash that doesn't exist
        assert images.find_dedup(conn, "rewards", "no-such-hash") is None
    finally:
        conn.close()


def test_find_dedup_rejects_unknown_table(tmp_path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(tmp_path / "x.db")
    try:
        with pytest.raises(ValueError, match="unsupported"):
            images.find_dedup(conn, "evil_table", "abc")
    finally:
        conn.close()


# ---------------------------------------------------------------------
# stage / commit_staging
# ---------------------------------------------------------------------


def test_stage_writes_to_staging_dir() -> None:
    payload = _jpeg_bytes()
    validated = images.validate_upload(payload, "image/jpeg")
    handle = images.stage(payload, validated)
    on_disk = images.staging_path(handle)
    assert on_disk.is_file()
    assert on_disk.read_bytes() == payload
    assert handle.extension == "jpg"
    assert handle.filename == f"{handle.id}.jpg"


def test_commit_staging_moves_to_target_dir_for_toys() -> None:
    payload = _jpeg_bytes()
    validated = images.validate_upload(payload, "image/jpeg")
    handle = images.stage(payload, validated)
    final = images.commit_staging(handle, target_subdir="toys")
    assert final.is_file()
    assert "toys" in final.parts
    # Staging file is gone.
    assert not images.staging_path(handle).exists()


def test_commit_staging_supports_rooms_subdir() -> None:
    """The shared helper must work for step 17 too.

    Load-bearing forward-compat test (Reviewer 3 flagged this as a
    trim candidate; iter-2 keeps it because step 17 reuses the same
    helper for the bulk room-photo ingest).
    """
    payload = _png_bytes()
    validated = images.validate_upload(payload, "image/png")
    handle = images.stage(payload, validated)
    final = images.commit_staging(handle, target_subdir="rooms")
    assert final.is_file()
    assert "rooms" in final.parts


def test_commit_staging_supports_rewards_subdir() -> None:
    """Phase L L2 extends the whitelist for ``rewards`` images.

    Mirrors ``test_commit_staging_supports_rooms_subdir`` so the
    whitelist extension has direct unit coverage (the rewards CRUD
    integration test exercises the same path end-to-end but this
    keeps storage/images.py's contract self-documenting).
    """
    payload = _png_bytes()
    validated = images.validate_upload(payload, "image/png")
    handle = images.stage(payload, validated)
    final = images.commit_staging(handle, target_subdir="rewards")
    assert final.is_file()
    assert "rewards" in final.parts


def test_commit_staging_rejects_unknown_subdir() -> None:
    payload = _jpeg_bytes()
    validated = images.validate_upload(payload, "image/jpeg")
    handle = images.stage(payload, validated)
    with pytest.raises(ValueError, match="unsupported"):
        images.commit_staging(handle, target_subdir="../etc")


def test_commit_staging_raises_when_file_missing() -> None:
    handle = images.StagingId(id="bogus-id-no-file", extension="jpg")
    with pytest.raises(FileNotFoundError):
        images.commit_staging(handle, target_subdir="toys")


def test_commit_staging_retries_then_raises_locked() -> None:
    """Persistent ``PermissionError`` → :class:`StagingLockedError` (M3).

    Windows AV / Search Indexer can briefly hold the staging file open;
    we retry 3× before giving up. We don't sleep in the test — patching
    ``time.sleep`` is enough to keep it fast.
    """
    payload = _jpeg_bytes()
    validated = images.validate_upload(payload, "image/jpeg")
    handle = images.stage(payload, validated)

    with (
        patch("toybox.storage.images.os.replace", side_effect=PermissionError("AV")),
        patch("toybox.storage.images.time.sleep"),
    ):
        with pytest.raises(images.StagingLockedError):
            images.commit_staging(handle, target_subdir="toys")


def test_commit_staging_recovers_on_transient_permission_error() -> None:
    """One transient PermissionError → retry succeeds (M3 happy retry)."""
    payload = _jpeg_bytes()
    validated = images.validate_upload(payload, "image/jpeg")
    handle = images.stage(payload, validated)
    real_replace = os.replace
    calls = {"n": 0}

    def _flaky_replace(src: Any, dst: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("flake")
        real_replace(src, dst)

    with (
        patch("toybox.storage.images.os.replace", side_effect=_flaky_replace),
        patch("toybox.storage.images.time.sleep"),
    ):
        final = images.commit_staging(handle, target_subdir="toys")
    assert final.is_file()
    assert calls["n"] == 2


# ---------------------------------------------------------------------
# sweep_stale_staging
# ---------------------------------------------------------------------


def test_sweep_stale_staging_purges_old_files() -> None:
    payload = _jpeg_bytes()
    validated = images.validate_upload(payload, "image/jpeg")
    handle = images.stage(payload, validated)
    staged_path = images.staging_path(handle)
    # Backdate the file by 2 hours.
    old_time = time.time() - 7200
    os.utime(staged_path, (old_time, old_time))

    purged = images.sweep_stale_staging(ttl_sec=3600)
    assert purged == 1
    assert not staged_path.exists()


def test_sweep_stale_staging_preserves_fresh_files() -> None:
    payload = _jpeg_bytes()
    validated = images.validate_upload(payload, "image/jpeg")
    handle = images.stage(payload, validated)
    staged_path = images.staging_path(handle)

    purged = images.sweep_stale_staging(ttl_sec=3600)
    assert purged == 0
    assert staged_path.exists()


# ---------------------------------------------------------------------
# downscale_for_vision
# ---------------------------------------------------------------------


def test_downscale_for_vision_shrinks_to_max_edge() -> None:
    payload = _jpeg_bytes((3000, 1500))
    out = images.downscale_for_vision(payload, max_edge=1600)
    with Image.open(io.BytesIO(out)) as img:
        long_edge = max(img.size)
    assert long_edge <= 1600


def test_downscale_for_vision_preserves_small_images() -> None:
    payload = _jpeg_bytes((100, 80))
    out = images.downscale_for_vision(payload, max_edge=1600)
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (100, 80)


def test_downscale_for_vision_handles_png_with_alpha() -> None:
    img = Image.new("RGBA", (200, 100), (255, 0, 0, 128))
    out = io.BytesIO()
    img.save(out, format="PNG")
    payload = out.getvalue()
    # No exception even though source has alpha.
    result = images.downscale_for_vision(payload, max_edge=1600)
    with Image.open(io.BytesIO(result)) as decoded:
        assert decoded.format == "JPEG"


def test_downscale_for_vision_rejects_invalid_max_edge() -> None:
    payload = _jpeg_bytes()
    with pytest.raises(ValueError):
        images.downscale_for_vision(payload, max_edge=0)
