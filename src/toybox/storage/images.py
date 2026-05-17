"""Reusable image-upload pipeline for the toy and room ingest flows.

Step 16 (single-toy ingest) is the first caller. Step 17 (bulk room
ingest) reuses the same primitives — every public function takes a
``subdir`` arg ("toys" or "rooms") so the final committed path is
``data/images/<subdir>/<uuid>.<ext>`` while sharing one staging area
under ``data/images/.staging/``.

The pipeline is split into discrete primitives so the routers can
sequence them around DB lookups (dedup happens after hashing, before
staging) and around the optional vision call (downscale + describe
happens after staging, before commit). Each primitive is independent:

* :func:`validate_upload` — MIME-sniff via magic bytes (NOT extension
  trust), Pillow dimension check, size cap. Returns the sniffed
  media type + canonical extension on success, raises
  :class:`UploadValidationError` on rejection.
* :func:`compute_hash` — SHA-256 hex of the raw bytes (used for dedup).
* :func:`find_dedup` — look up an existing non-archived row in
  ``toys`` or ``rooms`` by ``image_hash``.
* :func:`stage` — write the bytes to a server-generated staging path.
* :func:`commit_staging` — move staging file to the final
  ``data/images/<subdir>/`` directory (atomic rename when on the
  same filesystem).
* :func:`sweep_stale_staging` — janitor that purges staging files
  older than ``ttl_sec`` seconds. Called opportunistically on each
  upload.
* :func:`downscale_for_vision` — produce a ≤``max_edge`` long-edge
  in-memory copy for the Claude vision call (the original on disk
  stays untouched).

MIME sniff: we deliberately avoid ``python-magic`` because it depends
on the system ``libmagic`` shared library, which isn't reliably
installed on Windows (the ``python-magic-bin`` ABI shim has been
stale for years). Instead we detect by leading magic bytes — JPEG
``FF D8 FF``, PNG ``89 50 4E 47``, WebP ``RIFF....WEBP``, and HEIC
``....ftypheic`` / ``ftypheix``. This is sufficient for our four
allowed types and keeps the codebase platform-portable.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from PIL import Image, UnidentifiedImageError

_logger = logging.getLogger(__name__)

# Maximum bytes we'll accept on a single image upload. Configurable via
# env so deployments can tune; the default matches the v1 plan
# §"Upload validation rules" (15 MB).
MAX_BYTES_ENV: Final[str] = "TOYBOX_MAX_UPLOAD_BYTES"
DEFAULT_MAX_BYTES: Final[int] = 15 * 1024 * 1024

# Maximum decoded dimensions. Either edge above this → reject.
MAX_DIMENSION: Final[int] = 8000

# Default downscale long-edge for the vision call (the original on
# disk is the canonical copy; this is a transient in-memory shrink).
DEFAULT_VISION_MAX_EDGE: Final[int] = 1600

# Staging janitor TTL.
STAGING_TTL_ENV: Final[str] = "TOYBOX_STAGING_TTL_SEC"
DEFAULT_STAGING_TTL_SEC: Final[int] = 3600

# Where the staging area + final committed roots live. The roots are
# overrideable via env so a test fixture can redirect to ``tmp_path``.
DATA_ROOT_ENV: Final[str] = "TOYBOX_DATA_DIR"
DEFAULT_DATA_ROOT: Final[Path] = Path("data")

_STAGING_SUBDIR: Final[str] = ".staging"
_IMAGES_SUBDIR: Final[str] = "images"

# Allowed sniffed MIME types and their canonical file extensions.
# The mapping is the source of truth; the frontend lists the same set
# in its file picker accept= attribute.
#
# HEIC support: Pillow needs the ``pillow-heif`` plugin to decode HEIC,
# which has flaky Windows wheels. Rather than ship an optional dep we
# narrow v1 to JPEG / PNG / WebP — Apple Photos can export to those
# three on share. The MIME-sniffer still detects HEIC magic bytes so
# we reject with a clear message rather than letting Pillow throw.
_ALLOWED_MIMES: Final[dict[str, str]] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_HEIC_MIME: Final[str] = "image/heic"


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """Bytes that passed MIME-sniff + dimension + size validation."""

    media_type: str
    """Sniffed MIME type — one of the keys in ``_ALLOWED_MIMES``."""

    extension: str
    """Canonical file extension (no dot), e.g. ``"jpg"``."""

    width: int
    """Pillow-decoded image width."""

    height: int
    """Pillow-decoded image height."""

    byte_size: int
    """Length of the raw bytes; recorded for telemetry/error envelopes."""


@dataclass(frozen=True, slots=True)
class StagingId:
    """Opaque handle to a staged file.

    Carries enough info for :func:`commit_staging` to find and move
    the file; the API hands the wire form (``id`` string) back to the
    client on the upload response.
    """

    id: str
    """UUID hex string used as the on-disk filename stem."""

    extension: str
    """Canonical extension (no dot)."""

    @property
    def filename(self) -> str:
        """Return ``<id>.<ext>`` — the on-disk filename."""
        return f"{self.id}.{self.extension}"


class UploadValidationError(Exception):
    """Raised when an upload fails MIME / dimension / size validation.

    The ``code`` and ``http_status`` map directly onto the FastAPI
    HTTPException the router emits. Using a dedicated exception keeps
    the helper independent of FastAPI so step 17's room-bulk path can
    convert per-photo failures into JSON error envelopes without
    raising.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int,
        detail: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.detail = detail or {}


# ---------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------


def _data_root() -> Path:
    """Root directory for staging + committed images. Env-overrideable."""
    raw = os.environ.get(DATA_ROOT_ENV)
    return Path(raw) if raw else DEFAULT_DATA_ROOT


def images_root() -> Path:
    """Public accessor for ``<data_root>/images``.

    Used by the FastAPI app factory to mount the directory as a
    StaticFiles route. Kept separate from :func:`committed_dir` so
    callers that just need the parent dir don't need a subdir name.
    """
    return _data_root() / _IMAGES_SUBDIR


def staging_dir() -> Path:
    """Return ``data/images/.staging`` (creating it if missing)."""
    path = _data_root() / _IMAGES_SUBDIR / _STAGING_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def committed_dir(subdir: str) -> Path:
    """Return ``data/images/<subdir>`` (creating it if missing).

    ``subdir`` is restricted to a small whitelist so a caller can't
    accidentally write into ``../`` or absolute paths via path traversal.

    Phase L L2 adds ``"rewards"`` to the whitelist so the rewards CRUD
    API can reuse the same staging → commit pipeline as the toys
    ingest. The choice here over a sibling rewards-specific function
    (per documentation/phase-l-plan.md §"Image storage path"): a
    single-line whitelist extension preserves one staging registry,
    one validation routine, and one janitor sweep across all image
    kinds, which keeps the producer/consumer surface stable per
    code-quality.md §2.
    """
    if subdir not in {"toys", "rooms", "rewards"}:
        raise ValueError(f"unsupported committed subdir {subdir!r}")
    path = _data_root() / _IMAGES_SUBDIR / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def relative_committed_path(subdir: str, filename: str) -> str:
    """Return the relative path stored in DB rows.

    Separated out so step 17's bulk router writes the same shape as
    step 16's single-toy router. We keep it forward-slash for
    DB-portability across Windows / Linux dev machines.
    """
    return f"data/{_IMAGES_SUBDIR}/{subdir}/{filename}"


def on_disk_image_path(stored: str) -> Path:
    """Resolve a DB ``image_path`` to its on-disk :class:`Path`.

    DB rows store ``data/images/<subdir>/<filename>`` literally for
    portability — but the actual data root may live elsewhere when
    ``TOYBOX_DATA_DIR`` is set (tests, custom installs). This helper
    centralises the prefix-stripping + root-rebinding so callers don't
    open-code the conversion (and accidentally bypass the env override).
    Raises :class:`ValueError` if the stored path doesn't match the
    expected ``data/<images-subdir>/`` shape.
    """
    expected_prefix = f"data/{_IMAGES_SUBDIR}/"
    normalized = stored.replace("\\", "/")
    if not normalized.startswith(expected_prefix):
        raise ValueError(
            f"image_path {stored!r} does not start with {expected_prefix!r}",
        )
    relative = normalized[len(expected_prefix) :]
    return images_root() / relative


# ---------------------------------------------------------------------
# Magic-byte MIME sniffing
# ---------------------------------------------------------------------


def _sniff_mime(bytes_: bytes) -> str | None:
    """Detect MIME by leading magic bytes.

    Returns ``None`` for unknown signatures. Order matters because
    WebP and HEIC are both RIFF/ISO-BMFF containers — we identify by
    the brand inside, not just the outer container header.
    """
    # JPEG: ``FF D8 FF`` followed by application marker (E0/E1/EE/etc.).
    if len(bytes_) >= 3 and bytes_[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    # PNG: ``89 50 4E 47 0D 0A 1A 0A``.
    if len(bytes_) >= 8 and bytes_[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # WebP: RIFF....WEBP at offsets 0..3 + 8..11.
    if len(bytes_) >= 12 and bytes_[:4] == b"RIFF" and bytes_[8:12] == b"WEBP":
        return "image/webp"
    # HEIC: ISO-BMFF (``ftyp`` box at offset 4) with the ``heic``/``heix``
    # major brand. Detected so we can return a clear "HEIC unsupported"
    # error rather than letting Pillow throw an opaque format error.
    # We deliberately exclude HEVC video brands (``hevc``/``hevx``) —
    # those are video files, not still images, so they should fall
    # through to the generic ``upload_bad_mime`` path. We also exclude
    # ``mif1``/``msf1`` (MIAF/MSF) because they may not match the
    # ``image/heic`` MIME spec; if a future encoder produces one we'll
    # fall through to bad-mime, which is the conservative behaviour.
    if len(bytes_) >= 12 and bytes_[4:8] == b"ftyp":
        brand = bytes_[8:12]
        if brand in (b"heic", b"heix"):
            return _HEIC_MIME
    return None


# ---------------------------------------------------------------------
# Public pipeline primitives
# ---------------------------------------------------------------------


def max_upload_bytes() -> int:
    """Return the configured upload size cap (env-overrideable)."""
    raw = os.environ.get(MAX_BYTES_ENV)
    if raw is None:
        return DEFAULT_MAX_BYTES
    try:
        parsed = int(raw)
    except ValueError:
        _logger.warning("%s=%r is not an int; using default", MAX_BYTES_ENV, raw)
        return DEFAULT_MAX_BYTES
    if parsed <= 0:
        _logger.warning("%s=%d <= 0; using default", MAX_BYTES_ENV, parsed)
        return DEFAULT_MAX_BYTES
    return parsed


def staging_ttl_sec() -> int:
    """Return the configured staging janitor TTL."""
    raw = os.environ.get(STAGING_TTL_ENV)
    if raw is None:
        return DEFAULT_STAGING_TTL_SEC
    try:
        parsed = int(raw)
    except ValueError:
        _logger.warning("%s=%r is not an int; using default", STAGING_TTL_ENV, raw)
        return DEFAULT_STAGING_TTL_SEC
    # Zero is a legal "purge everything immediately" value (used in
    # janitor tests); negative is rejected.
    if parsed < 0:
        _logger.warning("%s=%d < 0; using default", STAGING_TTL_ENV, parsed)
        return DEFAULT_STAGING_TTL_SEC
    return parsed


def validate_upload(
    bytes_: bytes,
    declared_mime: str | None = None,
    *,
    max_bytes: int | None = None,
) -> ValidatedUpload:
    """Reject anything that's not a small, well-formed image.

    The ``declared_mime`` arg is logged when present but never trusted;
    a malicious caller can always lie about Content-Type, so the
    sniffed value is the source of truth.

    Raises :class:`UploadValidationError` on any rejection. The router
    converts that to an HTTPException; step 17's bulk path catches it
    per-file and renders an error envelope.
    """
    cap = max_bytes if max_bytes is not None else max_upload_bytes()
    actual_size = len(bytes_)
    if actual_size > cap:
        raise UploadValidationError(
            code="upload_too_large",
            message=f"upload {actual_size} bytes exceeds cap {cap}",
            http_status=413,
            detail={"byte_size": actual_size, "max_bytes": cap},
        )

    sniffed = _sniff_mime(bytes_)
    if sniffed is None:
        raise UploadValidationError(
            code="upload_bad_mime",
            message="upload is not a recognised image format",
            http_status=415,
            detail={"declared_mime": declared_mime},
        )
    if sniffed == _HEIC_MIME:
        # HEIC detected but Pillow can't decode it without pillow-heif.
        raise UploadValidationError(
            code="upload_bad_mime",
            message="HEIC uploads are not supported; please share as JPEG/PNG/WebP",
            http_status=415,
            detail={"declared_mime": declared_mime, "sniffed_mime": sniffed},
        )
    if sniffed not in _ALLOWED_MIMES:
        # Future-proof: a sniffer extension might detect a new type
        # before we add it to the allow-list.
        raise UploadValidationError(
            code="upload_bad_mime",
            message=f"upload mime {sniffed} is not allowed",
            http_status=415,
            detail={"declared_mime": declared_mime, "sniffed_mime": sniffed},
        )

    extension = _ALLOWED_MIMES[sniffed]

    # Decode dimensions via Pillow. ``Image.open`` is lazy — calling
    # ``.size`` forces the header read but not the full pixel decode,
    # which is what we want for the dimension gate.
    #
    # ``Image.DecompressionBombError`` inherits straight from
    # ``Exception`` (NOT OSError or ValueError), so we catch it
    # explicitly. A small file with a header claiming dimensions over
    # ``Image.MAX_IMAGE_PIXELS`` (~89 MP by default) raises this — we
    # surface it as ``upload_too_large_dimensions`` rather than letting
    # the API 500.
    try:
        with Image.open(io.BytesIO(bytes_)) as img:
            width, height = img.size
    except Image.DecompressionBombError as exc:
        raise UploadValidationError(
            code="upload_too_large_dimensions",
            message=f"image dimensions exceed Pillow's bomb-guard: {exc}",
            http_status=413,
            detail={
                "declared_mime": declared_mime,
                "sniffed_mime": sniffed,
                "max_dimension": MAX_DIMENSION,
            },
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UploadValidationError(
            code="upload_bad_mime",
            message=f"image could not be decoded: {exc}",
            http_status=415,
            detail={"declared_mime": declared_mime, "sniffed_mime": sniffed},
        ) from exc

    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise UploadValidationError(
            code="upload_too_large_dimensions",
            message=(
                f"image dimensions {width}x{height} exceed cap {MAX_DIMENSION}x{MAX_DIMENSION}"
            ),
            http_status=413,
            detail={
                "width": width,
                "height": height,
                "max_dimension": MAX_DIMENSION,
            },
        )

    return ValidatedUpload(
        media_type=sniffed,
        extension=extension,
        width=width,
        height=height,
        byte_size=actual_size,
    )


def compute_hash(bytes_: bytes) -> str:
    """Return SHA-256 hex digest of the raw bytes.

    Called *after* validation but *before* staging. The hash is the
    dedup key against ``toys.image_hash`` / ``rooms.image_hash``.
    """
    return hashlib.sha256(bytes_).hexdigest()


def find_dedup(
    conn: sqlite3.Connection,
    table: str,
    image_hash: str,
) -> dict[str, object] | None:
    """Look up a non-archived row in ``table`` whose ``image_hash`` matches.

    Returns the row as a plain dict, or ``None`` when no match is
    found. The router uses this to short-circuit a 409
    ``image_already_exists`` before it ever stages bytes.

    ``table`` is restricted to ``"toys"`` / ``"rooms"`` to keep the
    SQL safe — the value is interpolated (sqlite parameter binding
    doesn't support identifiers).

    For ``rooms``, the ``archived`` column doesn't exist in the v1
    schema; we treat every row as live.

    Phase L L2 adds ``"rewards"`` to the whitelist so the rewards CRUD
    API uses the same shared helper rather than reimplementing the
    same ``archived = 0`` lookup (code-quality.md §2 — one source of
    truth). ``rewards`` ships an ``archived`` column (migration 0019)
    so it follows the same shape as ``toys``.
    """
    if table not in {"toys", "rooms", "rewards"}:
        raise ValueError(f"unsupported dedup table {table!r}")
    if table == "toys":
        sql = "SELECT * FROM toys WHERE image_hash = ? AND archived = 0 LIMIT 1"
    elif table == "rewards":
        sql = "SELECT * FROM rewards WHERE image_hash = ? AND archived = 0 LIMIT 1"
    else:
        sql = "SELECT * FROM rooms WHERE image_hash = ? LIMIT 1"
    row = conn.execute(sql, (image_hash,)).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def stage(bytes_: bytes, validated: ValidatedUpload) -> StagingId:
    """Write the bytes into the staging area under a fresh UUID name.

    Each call generates an independent UUID, so two concurrent uploads
    can never collide — even when the bytes are identical (the dedup
    short-circuit upstream is by hash, not by filename).
    """
    new_id = uuid.uuid4().hex
    handle = StagingId(id=new_id, extension=validated.extension)
    target = staging_dir() / handle.filename
    # Write atomically through a tmp + rename so a crash mid-write
    # doesn't leave a partial file the janitor would later "succeed"
    # at moving.
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(bytes_)
    os.replace(tmp, target)
    return handle


def staging_path(staging_id: StagingId) -> Path:
    """Return the on-disk staging path for a handle."""
    return staging_dir() / staging_id.filename


class StagingLockedError(Exception):
    """Raised when a staging file can't be moved after retries.

    On Windows, antivirus and the Search Indexer briefly hold open
    file handles after a write — a foreground rename then fails with
    ``PermissionError``. Surfaced as 503 ``staging_locked`` by the
    router so the client can offer a retry.
    """


def commit_staging(
    staging_id: StagingId,
    *,
    target_subdir: str,
) -> Path:
    """Move a staged file to ``data/images/<target_subdir>/``.

    Returns the final on-disk path. Raises :class:`FileNotFoundError`
    when the staged file doesn't exist (likely the janitor swept it
    out from under us, or the staging_id was forged by a client).
    Raises :class:`StagingLockedError` when ``os.replace`` keeps
    hitting ``PermissionError`` (Windows AV / Search Indexer scenario).

    The target filename keeps the same UUID stem so the SHA-256 in the
    DB row stays the canonical identifier — the filename is just a
    cache key on disk.
    """
    src = staging_path(staging_id)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    dst_dir = committed_dir(target_subdir)
    dst = dst_dir / staging_id.filename
    # ``os.replace`` is atomic when src+dst are on the same filesystem
    # (which they are by construction — both under data/images/). On
    # Windows the rename can transiently fail with PermissionError when
    # AV / Search Indexer holds the handle; retry a few times on a
    # short backoff before giving up.
    last_exc: PermissionError | None = None
    for attempt in range(3):
        try:
            os.replace(src, dst)
            return dst
        except PermissionError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.05)
    assert last_exc is not None
    _logger.warning("commit_staging: PermissionError on os.replace after 3 attempts (%s)", last_exc)
    raise StagingLockedError(str(last_exc)) from last_exc


def rename_committed_image(
    subdir: str,
    old_filename: str,
    new_filename: str,
) -> str:
    """Rename a committed file within the same ``data/images/<subdir>/`` dir.

    Phase L L2 caller: after :func:`commit_staging` parks a file under
    its UUID stem (``<staging_uuid>.<ext>``), the rewards API derives a
    slug-based id from ``display_name`` and renames the file to
    ``<slug>.<ext>`` BEFORE inserting the DB row. This keeps the on-disk
    name aligned with ``rewards.image_path`` per invariant 8.

    Returns the relative DB-portable path (``data/images/<subdir>/<new>``)
    on success. Raises :class:`FileNotFoundError` when the source file
    doesn't exist, and propagates :class:`OSError` for any other rename
    failure — the rewards API catches and rolls back the commit.

    The rename uses :func:`os.replace` which is atomic when source and
    destination are on the same filesystem (which they are by
    construction — both under ``data/images/<subdir>/``).
    """
    dst_dir = committed_dir(subdir)
    src = dst_dir / old_filename
    dst = dst_dir / new_filename
    if not src.is_file():
        raise FileNotFoundError(str(src))
    os.replace(src, dst)
    return relative_committed_path(subdir, new_filename)


def discard_staging(staging_id: StagingId) -> None:
    """Best-effort delete of a staged file. No error if already gone."""
    src = staging_path(staging_id)
    try:
        src.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        _logger.warning("discard_staging failed for %s: %s", src, exc)


def sweep_stale_staging(ttl_sec: int | None = None) -> int:
    """Delete staging files older than ``ttl_sec`` seconds.

    Called opportunistically on every upload — concurrent uploads
    each generate their own UUID staging path, so we can never sweep
    an in-flight peer's bytes (the peer's mtime is fresh by
    construction). Returns the count of files purged.

    ``ttl_sec=None`` reads the env var; explicit ``0`` purges
    everything (used in tests). The function is best-effort: an
    OSError on one file logs WARNING but doesn't stop the sweep.
    """
    ttl = ttl_sec if ttl_sec is not None else staging_ttl_sec()
    now = time.time()
    cutoff = now - ttl
    purged = 0
    root = staging_dir()
    try:
        children = list(root.iterdir())
    except OSError as exc:
        _logger.warning("sweep_stale_staging cannot iterate %s: %s", root, exc)
        return 0
    for child in children:
        try:
            if not child.is_file():
                continue
            mtime = child.stat().st_mtime
            if mtime > cutoff:
                continue
            child.unlink()
            purged += 1
        except OSError as exc:
            _logger.warning("sweep_stale_staging skipping %s: %s", child, exc)
            continue
    if purged > 0:
        _logger.info("sweep_stale_staging purged %d stale staging file(s)", purged)
    return purged


def downscale_for_vision(
    bytes_: bytes,
    *,
    max_edge: int = DEFAULT_VISION_MAX_EDGE,
) -> bytes:
    """Return a JPEG-encoded copy resized to ≤``max_edge`` long edge.

    The downscale is in-memory only — the file we keep on disk after
    commit is always the parent-supplied original. Encoding to JPEG
    lets us send any of our supported types to Claude as a single
    media-type, which keeps the prompt template simple.
    """
    if max_edge <= 0:
        raise ValueError("max_edge must be positive")
    with Image.open(io.BytesIO(bytes_)) as opened:
        # Convert to RGB so JPEG encoding works for PNG/WebP with
        # alpha — alpha is dropped, which is fine for the vision call
        # (the model gets the same scene either way). We bind the
        # working image to a fresh local so PIL type stubs don't
        # complain about the ImageFile→Image narrowing.
        working: Any = opened.convert("RGB") if opened.mode not in ("RGB", "L") else opened
        width, height = working.size
        long_edge = max(width, height)
        if long_edge > max_edge:
            scale = max_edge / float(long_edge)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            working = working.resize(new_size, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        working.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()


def copy_for_test(src: Path, dst: Path) -> None:
    """Test helper: shutil.copy with a tracking-friendly name.

    Used by integration tests that pre-seed a staging file (e.g. the
    janitor sweep test). Production code never calls this.
    """
    shutil.copy(src, dst)


__all__ = [
    "DATA_ROOT_ENV",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_STAGING_TTL_SEC",
    "DEFAULT_VISION_MAX_EDGE",
    "MAX_BYTES_ENV",
    "MAX_DIMENSION",
    "STAGING_TTL_ENV",
    "StagingId",
    "StagingLockedError",
    "UploadValidationError",
    "ValidatedUpload",
    "commit_staging",
    "committed_dir",
    "compute_hash",
    "copy_for_test",
    "discard_staging",
    "downscale_for_vision",
    "find_dedup",
    "images_root",
    "max_upload_bytes",
    "on_disk_image_path",
    "relative_committed_path",
    "rename_committed_image",
    "stage",
    "staging_dir",
    "staging_path",
    "staging_ttl_sec",
    "sweep_stale_staging",
    "validate_upload",
]
