"""Bulk room-photo ingest pipeline + room CRUD REST API.

Step 17 ships the parent-facing bulk-photo flow:

* ``POST /api/rooms/upload-bulk`` (multipart, ≤50 files) — validates
  each file independently, stages each valid file, runs Claude vision
  per photo (concurrency-bounded by ``TOYBOX_VISION_CONCURRENCY``),
  returns ``{batch_id, photos: [...]}``.
* ``POST /api/rooms/confirm-bulk`` (JSON) — atomically commits a
  reviewed batch: parent's per-photo assignments either pick an
  existing room (``room_id``) or create a new one
  (``new_room_label``). On any per-assignment failure we roll the
  whole batch back: every committed file is moved back to staging
  and the DB transaction aborts.
* ``GET /api/rooms`` / ``GET /api/rooms/{id}`` — list / get.
* ``PATCH /api/rooms/{id}`` — partial update (display_name, notes).
* ``DELETE /api/rooms/{id}`` — hard delete; refuses (409) when any
  ``room_features`` row references the room (FK is RESTRICT).
* ``GET /api/rooms/{id}/features`` — list features for one room.

The shared :mod:`toybox.storage.images` helper carries the
validate/stage/commit primitives, with ``subdir="rooms"`` (vs
``subdir="toys"`` for step 16). The vision call is gated by
:func:`toybox.ai.capability.is_capable` — when offline (Claude not
capable, breaker open, no token) we skip the call and the response
sets ``vision_skipped=true``; the parent UI then uses the
``Unassigned`` tab to assign rooms manually.

**Multi-photo same-room policy.** The ``rooms`` schema has a single
``image_path`` per room. When the parent confirms 3 photos all
assigned to a NEW "Living Room", the FIRST committed photo's path
is the canonical ``rooms.image_path``; subsequent photos are
committed to ``data/images/rooms/`` so the dedup index still
applies, but their paths are NOT stored anywhere. They're
effectively gallery siblings that v1 doesn't surface. v1.5 may add
a ``room_photos`` table; for v1 the spec calls this the simplest
correct behaviour and the disk leak is bounded by the bulk-cap.

When the parent assigns photos to an EXISTING room, however, the
existing room's ``image_path`` is already populated and there's no
schema slot for siblings — those staging files are dropped post-
success rather than committed (L9 fix), keeping the committed
directory in 1:1 sync with the canonical row paths.

**Atomic confirm-bulk.** Either every assignment in a batch
succeeds and is persisted, or none are. The implementation:

1. Validate every assignment shape up-front (no mixed
   ``room_id`` + ``new_room_label``, every ``staging_id`` is known).
2. Resolve each assignment to a target ``rooms`` row — either
   existing-by-id, existing-by-name (case-insensitive collision),
   or a freshly minted row.
3. ``commit_staging`` each file in turn, recording the (src, dst)
   pairs so we can roll back on later failures.
4. Insert ``room_features`` rows inside a single ``with conn:``
   transaction.
5. On any error, move each committed file back to staging and let
   the DB transaction abort. The client retries the confirm with
   the same ``batch_id``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..ai.breaker import CircuitBreaker
from ..ai.capability import is_capable
from ..ai.client import AIClient
from ..ai.house_vision import (
    HouseVisionSuggestion,
    suggest_room,
)
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from ..storage.images import (
    StagingId,
    StagingLockedError,
    UploadValidationError,
    commit_staging,
    compute_hash,
    discard_staging,
    downscale_for_vision,
    find_dedup,
    max_upload_bytes,
    on_disk_image_path,
    relative_committed_path,
    stage,
    staging_path,
    sweep_stale_staging,
    validate_upload,
)
from .auth_dep import RequireScope

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])

# Module-singleton breaker for the house-vision call site. Using a
# distinct instance from the toy-vision breaker so a 429 burst on one
# pipeline doesn't open the other; both record into the same
# capability matrix at the dep layer.
_VISION_BREAKER: CircuitBreaker = CircuitBreaker()

BULK_UPLOAD_CAP_ENV: Final[str] = "TOYBOX_BULK_UPLOAD_CAP"
DEFAULT_BULK_UPLOAD_CAP: Final[int] = 50

VISION_CONCURRENCY_ENV: Final[str] = "TOYBOX_VISION_CONCURRENCY"
DEFAULT_VISION_CONCURRENCY: Final[int] = 4


def bulk_upload_cap() -> int:
    """Return the configured bulk-upload file count cap (env-overrideable)."""
    raw = os.environ.get(BULK_UPLOAD_CAP_ENV)
    if raw is None:
        return DEFAULT_BULK_UPLOAD_CAP
    try:
        parsed = int(raw)
    except ValueError:
        _logger.warning("%s=%r is not an int; using default", BULK_UPLOAD_CAP_ENV, raw)
        return DEFAULT_BULK_UPLOAD_CAP
    if parsed <= 0:
        _logger.warning("%s=%d <= 0; using default", BULK_UPLOAD_CAP_ENV, parsed)
        return DEFAULT_BULK_UPLOAD_CAP
    return parsed


def vision_concurrency() -> int:
    """Return the configured vision concurrency cap (env-overrideable)."""
    raw = os.environ.get(VISION_CONCURRENCY_ENV)
    if raw is None:
        return DEFAULT_VISION_CONCURRENCY
    try:
        parsed = int(raw)
    except ValueError:
        _logger.warning("%s=%r is not an int; using default", VISION_CONCURRENCY_ENV, raw)
        return DEFAULT_VISION_CONCURRENCY
    if parsed <= 0:
        _logger.warning("%s=%d <= 0; using default", VISION_CONCURRENCY_ENV, parsed)
        return DEFAULT_VISION_CONCURRENCY
    return parsed


# ---------------------------------------------------------------------
# DI: DB connection + AI client + capability check
# ---------------------------------------------------------------------


def get_rooms_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dep: yield a rooms-scoped SQLite connection.

    ``check_same_thread=False`` matches the toys/children pattern.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def get_vision_client() -> AIClient | None:
    """FastAPI dep: build an AIClient if a token is on disk, else None.

    Mirrors :func:`toybox.api.toys.get_vision_client`. Tests override
    this dep with a stub that returns a deterministic
    :class:`~toybox.ai.client.StubClient`.
    """
    from ..ai.client import AnthropicClient  # noqa: PLC0415
    from ..ai.oauth import load_token  # noqa: PLC0415

    token = load_token()
    if token is None:
        return None
    return AnthropicClient(token)


def get_vision_breaker() -> CircuitBreaker:
    """FastAPI dep returning the module-singleton breaker."""
    return _VISION_BREAKER


# ---------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------


class RoomResponse(BaseModel):
    """Wire shape for a ``rooms`` row."""

    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str
    image_path: str | None
    image_hash: str | None
    notes: str | None


class RoomFeatureResponse(BaseModel):
    """Wire shape for a ``room_features`` row."""

    model_config = ConfigDict(frozen=True)

    id: str
    room_id: str
    name: str


class RoomListResponse(BaseModel):
    """Envelope for ``GET /api/rooms``."""

    model_config = ConfigDict(frozen=True)

    rooms: list[RoomResponse]


class RoomFeatureListResponse(BaseModel):
    """Envelope for ``GET /api/rooms/{id}/features``."""

    model_config = ConfigDict(frozen=True)

    features: list[RoomFeatureResponse]


class FeatureSuggestionWire(BaseModel):
    """Pydantic wire mirror of
    :class:`~toybox.ai.house_vision.FeatureSuggestion`."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("feature name must be non-empty after trimming")
        return stripped


class HouseVisionSuggestionWire(BaseModel):
    """Pydantic wire mirror of
    :class:`~toybox.ai.house_vision.HouseVisionSuggestion`."""

    model_config = ConfigDict(frozen=True)

    suggested_room_label: str
    features: list[FeatureSuggestionWire]


class BulkPhoto(BaseModel):
    """Per-file outcome inside :class:`BulkUploadResponse`.

    Either ``suggested`` is populated (vision succeeded), OR
    ``vision_error`` carries a short reason, OR ``error`` carries a
    rejection code (validation / dedup) — the parent UI reads
    ``error`` first, then ``vision_error``, then ``suggested``.
    """

    model_config = ConfigDict(frozen=True)

    staging_id: str
    image_hash: str
    filename: str
    suggested: HouseVisionSuggestionWire | None
    vision_error: str | None = None
    error: str | None = None
    existing_room: RoomResponse | None = None


class BulkUploadResponse(BaseModel):
    """Wire shape for ``POST /api/rooms/upload-bulk`` success."""

    model_config = ConfigDict(frozen=True)

    batch_id: str
    photos: list[BulkPhoto]
    vision_skipped: bool = False


class Assignment(BaseModel):
    """One photo's parent-edited assignment in a confirm-bulk request.

    Exactly one of ``room_id`` / ``new_room_label`` must be present —
    the field validator below enforces it.
    """

    model_config = ConfigDict(frozen=True)

    staging_id: str = Field(min_length=1)
    room_id: str | None = None
    new_room_label: str | None = None
    features: list[FeatureSuggestionWire] = Field(default_factory=list, max_length=20)

    @field_validator("new_room_label")
    @classmethod
    def _strip_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("new_room_label must be non-empty after trimming")
        if len(stripped) > 40:
            raise ValueError("new_room_label must be at most 40 characters")
        return stripped


class ConfirmBulkRequest(BaseModel):
    """Body for ``POST /api/rooms/confirm-bulk``."""

    model_config = ConfigDict(frozen=True)

    batch_id: str = Field(min_length=1)
    assignments: list[Assignment]


class ConfirmBulkResponse(BaseModel):
    """Wire shape for ``POST /api/rooms/confirm-bulk`` success."""

    model_config = ConfigDict(frozen=True)

    rooms: list[RoomResponse]
    features: list[RoomFeatureResponse]


class RoomUpdateRequest(BaseModel):
    """Body for ``PATCH /api/rooms/{id}``. All fields optional."""

    model_config = ConfigDict(frozen=True)

    display_name: str | None = None
    notes: str | None = None

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str:
        # PATCH cannot clear ``rooms.display_name`` to empty/whitespace —
        # the column is nullable in the schema but the wire contract says
        # parent-managed rooms always have a name. A literal ``null`` is
        # allowed (rare, lets parent un-name a room they want to merge).
        # Wait: spec says rooms keep a label at all times, so reject.
        if value is None:
            raise ValueError("display_name cannot be cleared")
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must be non-empty after trimming")
        if len(stripped) > 40:
            raise ValueError("display_name must be at most 40 characters")
        return stripped


class DeleteResponse(BaseModel):
    """Envelope for ``DELETE /api/rooms/{id}``."""

    model_config = ConfigDict(frozen=True)

    ok: bool = True


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _row_to_room(row: sqlite3.Row | dict[str, Any]) -> RoomResponse:
    getter: Any = row.__getitem__
    name_raw = getter("display_name")
    return RoomResponse(
        id=str(getter("id")),
        display_name=str(name_raw) if name_raw is not None else "",
        image_path=getter("image_path"),
        image_hash=getter("image_hash"),
        notes=getter("notes"),
    )


def _row_to_feature(row: sqlite3.Row | dict[str, Any]) -> RoomFeatureResponse:
    getter: Any = row.__getitem__
    return RoomFeatureResponse(
        id=str(getter("id")),
        room_id=str(getter("room_id")),
        name=str(getter("name")) if getter("name") is not None else "",
    )


def _fetch_room_row(conn: sqlite3.Connection, room_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM rooms WHERE id = ?",
        (room_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "room_not_found", "id": room_id},
        )
    return row


def _find_room_by_label(conn: sqlite3.Connection, label: str) -> sqlite3.Row | None:
    """Case-insensitive lookup of a room by ``display_name``."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM rooms WHERE display_name IS NOT NULL "
        "AND lower(display_name) = lower(?) LIMIT 1",
        (label,),
    ).fetchone()
    return row


# ---------------------------------------------------------------------
# Bulk staging registry: same shape as toys but a separate dict so the
# two pipelines don't clobber each other and an ID issued by one router
# can't be confirmed by the other. Entries age on the same TTL as the
# on-disk janitor.
# ---------------------------------------------------------------------


# staging_id -> (extension, original_filename, batch_id, image_hash, created_at)
# L7: image_hash is captured once at upload time so confirm-bulk doesn't
# have to re-read every file off disk to recompute it.
_bulk_staging_extensions: dict[str, tuple[str, str, str, str, float]] = {}


def _record_bulk_staging(handle: StagingId, filename: str, batch_id: str, image_hash: str) -> None:
    _bulk_staging_extensions[handle.id] = (
        handle.extension,
        filename,
        batch_id,
        image_hash,
        time.time(),
    )


def _drop_bulk_staging(staging_id: str) -> None:
    _bulk_staging_extensions.pop(staging_id, None)


def _sweep_bulk_staging_registry(ttl_sec: int | None = None) -> int:
    """Evict registry entries older than the configured staging TTL.

    Mirrors :func:`toybox.storage.images.sweep_stale_staging` so the
    in-memory map ages out at the same rate as the on-disk files.
    """
    from ..storage.images import staging_ttl_sec  # noqa: PLC0415

    ttl = ttl_sec if ttl_sec is not None else staging_ttl_sec()
    cutoff = time.time() - ttl
    expired = [
        sid
        for sid, (_ext, _fn, _bid, _hash, ts) in _bulk_staging_extensions.items()
        if ts <= cutoff
    ]
    for sid in expired:
        _bulk_staging_extensions.pop(sid, None)
    return len(expired)


# ---------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------


async def _read_upload_bytes(file: UploadFile, max_bytes: int) -> bytes:
    """Read one upload into memory, capping at ``max_bytes + 1``.

    Mirrors the toys helper. The bulk caller catches the resulting
    ``UploadValidationError`` per-file rather than aborting the whole
    batch.
    """
    chunks: list[bytes] = []
    total = 0
    chunk_size = 64 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        chunks.append(chunk)
        if total > max_bytes:
            while True:
                tail = await file.read(chunk_size)
                if not tail:
                    break
            raise UploadValidationError(
                code="upload_too_large",
                message=f"upload exceeds cap {max_bytes}",
                http_status=413,
                detail={"max_bytes": max_bytes},
            )
    return b"".join(chunks)


async def _run_vision_for_photo(
    semaphore: asyncio.Semaphore,
    client: AIClient,
    raw_bytes: bytes,
) -> HouseVisionSuggestion | tuple[None, str]:
    """Bound the per-photo vision call by ``semaphore``.

    The downscale runs in a thread because Pillow is CPU-bound, then
    the actual call is awaited under the semaphore. A blocking
    downscale held under the semaphore would needlessly serialise
    pixel work.

    Per-photo failure isolation (M1): any exception below — including
    Pillow raising on a partial decode that header-only validation
    let through — is caught here and surfaced as ``(None, "error")``.
    A bare ``asyncio.gather(*tasks)`` would otherwise propagate one
    bad photo and cancel the in-flight siblings, breaking the
    documented per-photo isolation contract.
    """
    try:
        downscaled = await asyncio.to_thread(downscale_for_vision, raw_bytes)
        async with semaphore:
            return await suggest_room(client, downscaled)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("rooms upload: per-photo vision raised %s", exc, exc_info=True)
        return (None, "error")


@router.post("/upload-bulk", response_model=BulkUploadResponse)
async def post_upload_bulk(
    files: Annotated[list[UploadFile], File()],
    conn: Annotated[sqlite3.Connection, Depends(get_rooms_db)],
    breaker: Annotated[CircuitBreaker, Depends(get_vision_breaker)],
    ai_client: Annotated[AIClient | None, Depends(get_vision_client)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> BulkUploadResponse:
    """Validate, dedup, stage, and (when capable) run vision per-photo.

    Per-file failures (validation, dedup-in-batch, dedup-vs-existing,
    vision) are surfaced inside the response per-photo entries; only
    the batch-level cap raises a top-level HTTPException.
    """
    cap = bulk_upload_cap()
    if len(files) > cap:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "bulk_cap_exceeded",
                "max_files": cap,
                "received": len(files),
            },
        )
    if len(files) == 0:
        raise HTTPException(
            status_code=400,
            detail={"code": "bulk_empty"},
        )

    # Janitor: sweep stale on-disk + in-memory entries before we add any
    # new ones, mirroring the toy upload path.
    try:
        sweep_stale_staging()
    except Exception:  # noqa: BLE001
        _logger.warning("rooms upload: staging sweep failed; continuing", exc_info=True)
    try:
        _sweep_bulk_staging_registry()
    except Exception:  # noqa: BLE001
        _logger.warning("rooms upload: registry sweep failed; continuing", exc_info=True)

    batch_id = uuid.uuid4().hex
    per_file_max = max_upload_bytes()
    photos: list[BulkPhoto] = []
    # First pass: validate + dedup + stage. Track which staged entries
    # need vision so we can run them together under the semaphore.
    seen_hashes_in_batch: set[str] = set()
    pending_vision: list[tuple[int, bytes]] = []  # (photos index, raw bytes)

    for file in files:
        original_filename = file.filename or "photo.jpg"
        try:
            raw = await _read_upload_bytes(file, per_file_max)
        except UploadValidationError as exc:
            photos.append(
                BulkPhoto(
                    staging_id="",
                    image_hash="",
                    filename=original_filename,
                    suggested=None,
                    error=f"validation_failed:{exc.code}",
                )
            )
            continue

        try:
            validated = validate_upload(raw, file.content_type)
        except UploadValidationError as exc:
            photos.append(
                BulkPhoto(
                    staging_id="",
                    image_hash="",
                    filename=original_filename,
                    suggested=None,
                    error=f"validation_failed:{exc.code}",
                )
            )
            continue

        h = compute_hash(raw)

        # Dedup-within-batch (earliest-by-position wins).
        if h in seen_hashes_in_batch:
            photos.append(
                BulkPhoto(
                    staging_id="",
                    image_hash=h,
                    filename=original_filename,
                    suggested=None,
                    error="duplicate_in_batch",
                )
            )
            continue
        seen_hashes_in_batch.add(h)

        # Dedup-against-existing rooms.
        existing = find_dedup(conn, "rooms", h)
        if existing is not None:
            existing_resp = _row_to_room(existing)
            photos.append(
                BulkPhoto(
                    staging_id="",
                    image_hash=h,
                    filename=original_filename,
                    suggested=None,
                    error="duplicate_existing_room",
                    existing_room=existing_resp,
                )
            )
            continue

        # Stage the bytes; the staging_id is what the client confirms with.
        handle = stage(raw, validated)
        _record_bulk_staging(handle, original_filename, batch_id, h)
        photo_index = len(photos)
        photos.append(
            BulkPhoto(
                staging_id=handle.id,
                image_hash=h,
                filename=original_filename,
                suggested=None,
                error=None,
            )
        )
        pending_vision.append((photo_index, raw))

    # Second pass: vision. Skipped entirely when offline.
    vision_skipped = False
    if ai_client is None:
        vision_skipped = True
    else:
        capable, _reason = await is_capable(breaker, listening_mode=None)
        if not capable:
            vision_skipped = True
        elif pending_vision:
            sem = asyncio.Semaphore(vision_concurrency())
            tasks = [_run_vision_for_photo(sem, ai_client, raw) for _idx, raw in pending_vision]
            results = await asyncio.gather(*tasks, return_exceptions=False)
            for (idx, _raw), result in zip(pending_vision, results, strict=True):
                photo = photos[idx]
                if isinstance(result, HouseVisionSuggestion):
                    photos[idx] = photo.model_copy(
                        update={
                            "suggested": HouseVisionSuggestionWire(
                                suggested_room_label=result.suggested_room_label,
                                features=[
                                    FeatureSuggestionWire(name=f.name) for f in result.features
                                ],
                            ),
                        },
                    )
                    breaker.record_success()
                else:
                    _none, reason = result
                    photos[idx] = photo.model_copy(update={"vision_error": reason})
                    if reason == "rate_limited":
                        breaker.record_429(retry_after=None)
                    else:
                        breaker.record_failure()

    return BulkUploadResponse(
        batch_id=batch_id,
        photos=photos,
        vision_skipped=vision_skipped,
    )


# ---------------------------------------------------------------------
# Confirm endpoint
# ---------------------------------------------------------------------


def _move_back_to_staging(committed: Path, original: Path) -> None:
    """Move a committed file back to its staging path.

    Best-effort — used inside the rollback loop. We mirror
    :func:`commit_staging`'s Windows-AV retry primitive (L8) so a
    transient PermissionError from the indexer doesn't strand the
    file in the committed directory.
    """
    last_exc: OSError | None = None
    for attempt in range(3):
        try:
            os.replace(str(committed), str(original))
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.05)
        except OSError as exc:
            last_exc = exc
            break
    _logger.warning(
        "confirm rollback: could not move %s back to %s: %s",
        committed,
        original,
        last_exc,
    )


@router.post(
    "/confirm-bulk",
    response_model=ConfirmBulkResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_confirm_bulk(
    body: ConfirmBulkRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_rooms_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ConfirmBulkResponse:
    """Atomically commit a reviewed bulk-upload batch.

    Validation strategy (fail-fast before any disk move):

    1. Each assignment must reference a known staging_id from THIS
       batch (registry lookup keyed by ``batch_id``).
    2. Each assignment must specify exactly one of ``room_id`` /
       ``new_room_label``.
    3. Resolve ``room_id`` references against ``rooms`` (404 if
       missing).
    4. Resolve ``new_room_label`` against the case-insensitive
       collision check (409 with the existing room id).
    5. Group multi-photo same-room assignments so the FIRST committed
       photo's path is the one we record on the new ``rooms`` row.

    Then the disk + DB pass:

    6. Commit each photo, recording (committed_path, original_staging_path)
       for rollback.
    7. Inside a single ``with conn:`` transaction, INSERT new rooms +
       INSERT all features.
    8. On any failure, roll back: move every committed file back to
       its staging path; the DB transaction aborts on context exit.
    """
    # 1) staging_id known + same batch
    handles: dict[str, StagingId] = {}
    staging_hashes: dict[str, str] = {}
    for assignment in body.assignments:
        entry = _bulk_staging_extensions.get(assignment.staging_id)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "staging_not_found",
                    "staging_id": assignment.staging_id,
                },
            )
        ext, _filename, ent_batch, ent_hash, _ts = entry
        if ent_batch != body.batch_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "staging_not_found",
                    "staging_id": assignment.staging_id,
                },
            )
        handles[assignment.staging_id] = StagingId(id=assignment.staging_id, extension=ext)
        staging_hashes[assignment.staging_id] = ent_hash

    # 2) Exactly-one-of room_id / new_room_label
    for assignment in body.assignments:
        if (assignment.room_id is None) == (assignment.new_room_label is None):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "assignment_invalid",
                    "staging_id": assignment.staging_id,
                    "reason": "exactly one of room_id / new_room_label required",
                },
            )

    # 3) room_id references must exist.
    for assignment in body.assignments:
        if assignment.room_id is None:
            continue
        existing = conn.execute(
            "SELECT id FROM rooms WHERE id = ?", (assignment.room_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "room_not_found", "id": assignment.room_id},
            )

    # 4) new_room_label collision check (case-insensitive). Also
    #    detect within-request collisions: two assignments minting the
    #    same new label are folded onto a single fresh room id.
    new_label_to_id: dict[str, str] = {}  # normalised label -> fresh room id
    for assignment in body.assignments:
        if assignment.new_room_label is None:
            continue
        normalised = assignment.new_room_label.strip().lower()
        existing_row = _find_room_by_label(conn, assignment.new_room_label)
        if existing_row is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "room_label_collision",
                    "label": assignment.new_room_label,
                    "existing_room": _row_to_room(existing_row).model_dump(),
                },
            )
        if normalised not in new_label_to_id:
            new_label_to_id[normalised] = uuid.uuid4().hex

    # 5) Resolve target room ids and group by them.
    # ``per_room`` maps room_id -> list of (assignment, handle).
    per_room: dict[str, list[tuple[Assignment, StagingId]]] = {}
    new_rooms_to_create: dict[str, str] = {}  # room_id -> display_name (preserve case)
    assignment_target: list[tuple[Assignment, StagingId, str]] = []

    for assignment in body.assignments:
        handle = handles[assignment.staging_id]
        if assignment.room_id is not None:
            target = assignment.room_id
        else:
            assert assignment.new_room_label is not None
            normalised = assignment.new_room_label.strip().lower()
            target = new_label_to_id[normalised]
            if target not in new_rooms_to_create:
                new_rooms_to_create[target] = assignment.new_room_label
        assignment_target.append((assignment, handle, target))
        per_room.setdefault(target, []).append((assignment, handle))

    # 6) Commit each file. Record (committed, original) for rollback.
    #
    # L9: assignments to an EXISTING room have nowhere to be referenced
    # (the schema has a single image_path per room and that row is
    # already populated), so we'd be moving them into
    # ``data/images/rooms/`` as pure orphans. Skip the move for those
    # — discard the staging file after the DB pass succeeds. Photos
    # for NEW rooms still all land on disk (the gallery-sibling policy
    # documented at the top of this module), with the FIRST one's path
    # recorded on the new ``rooms`` row.
    moved: list[tuple[Path, Path]] = []
    committed_paths: dict[str, Path] = {}
    discard_after_success: list[StagingId] = []
    # Targets of new rooms — anything else is an existing room id.
    new_room_targets = set(new_rooms_to_create.keys())

    try:
        for _assignment, handle, target in assignment_target:
            if target not in new_room_targets:
                # Existing-room assignment — never moves into the
                # committed dir. On rollback the staging file is still
                # in place for a retry.
                discard_after_success.append(handle)
                continue
            try:
                committed = commit_staging(handle, target_subdir="rooms")
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "staging_not_found",
                        "staging_id": handle.id,
                    },
                ) from exc
            except StagingLockedError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "staging_locked",
                        "staging_id": handle.id,
                    },
                ) from exc
            except OSError as exc:
                # M3: any other OSError (ENOSPC, raw permission
                # failures the PermissionError retry didn't catch)
                # routes through the HTTPException rollback path so
                # committed siblings get moved back to staging.
                # Without this, the OSError would propagate past the
                # rollback try block and leak partially-committed
                # orphans.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "commit_failed", "reason": str(exc)},
                ) from exc
            moved.append((committed, staging_path(handle)))
            committed_paths[handle.id] = committed

        # 7) DB pass.
        new_room_ids: list[str] = []
        new_feature_ids: list[str] = []
        try:
            with conn:
                # Insert new rooms first; image_path = first committed
                # photo for that room.
                for target_id, display_name in new_rooms_to_create.items():
                    first_assignment_handle: StagingId | None = None
                    for _, handle_ in per_room.get(target_id, []):
                        first_assignment_handle = handle_
                        break
                    if first_assignment_handle is None:  # pragma: no cover - defensive
                        raise HTTPException(
                            status_code=500,
                            detail={
                                "code": "bulk_internal_inconsistency",
                                "reason": "new room had no assignment",
                            },
                        )
                    image_path = relative_committed_path("rooms", first_assignment_handle.filename)
                    # L7: hash captured at upload time is reused — no
                    # disk re-read here, which also dodges the OSError
                    # surface during the DB transaction.
                    image_hash = staging_hashes[first_assignment_handle.id]
                    conn.execute(
                        "INSERT INTO rooms (id, display_name, image_path, "
                        "image_hash, notes) VALUES (?, ?, ?, ?, NULL)",
                        (target_id, display_name, image_path, image_hash),
                    )
                    new_room_ids.append(target_id)

                # Insert features for every assignment.
                for assignment_, _, target_id in assignment_target:
                    for feature in assignment_.features:
                        feature_id = uuid.uuid4().hex
                        try:
                            conn.execute(
                                "INSERT INTO room_features "
                                "(id, room_id, name, tags) "
                                "VALUES (?, ?, ?, NULL)",
                                (feature_id, target_id, feature.name),
                            )
                        except sqlite3.IntegrityError as exc:
                            # M2: distinguish UNIQUE(room_id, name)
                            # collisions (silent dedup — the parent
                            # double-tagged a benign feature) from
                            # everything else (FK / NOT NULL / unknown
                            # constraint failures), which must surface
                            # to abort the batch via outer rollback.
                            msg = str(exc).lower()
                            if "unique" in msg:
                                continue
                            raise
                        new_feature_ids.append(feature_id)
        except sqlite3.IntegrityError as exc:
            # M2 (cont'd): an integrity error that bubbled out of the
            # ``with conn:`` block — almost always a FK violation on
            # ``room_features.room_id``. Surface as 422 so the client
            # sees a different code than the unique-collision dedup.
            msg = str(exc).lower()
            if "foreign key" in msg:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "invalid_room_id", "reason": str(exc)},
                ) from exc
            raise HTTPException(
                status_code=422,
                detail={"code": "db_constraint_violation", "reason": str(exc)},
            ) from exc
        except sqlite3.Error as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "rooms_insert_failed", "reason": str(exc)},
            ) from exc
    except HTTPException:
        # 8) Rollback — move every committed file back to staging so the
        # client can retry the confirm. The DB transaction has already
        # aborted (if it was even opened) by the ``with conn:`` exit.
        for committed, original in moved:
            _move_back_to_staging(committed, original)
        raise

    # Drop registry entries + staging files for ids that were
    # successfully accounted for. ``discard_after_success`` covers
    # gallery siblings + existing-room photos (no commit move
    # happened); the canonical handles are already moved.
    for handle in discard_after_success:
        discard_staging(handle)
    for assignment in body.assignments:
        _drop_bulk_staging(assignment.staging_id)

    # Build response.
    touched_room_ids: set[str] = {target_id for _a, _h, target_id in assignment_target}
    rooms_out: list[RoomResponse] = []
    for rid in touched_room_ids:
        row = conn.execute("SELECT * FROM rooms WHERE id = ?", (rid,)).fetchone()
        if row is not None:
            rooms_out.append(_row_to_room(row))
    features_out: list[RoomFeatureResponse] = []
    if new_feature_ids:
        # Bind the new feature ids in a single SELECT to keep the
        # response ordering deterministic.
        placeholders = ",".join("?" * len(new_feature_ids))
        rows = conn.execute(
            f"SELECT * FROM room_features WHERE id IN ({placeholders})",
            new_feature_ids,
        ).fetchall()
        features_out = [_row_to_feature(r) for r in rows]

    return ConfirmBulkResponse(rooms=rooms_out, features=features_out)


# ---------------------------------------------------------------------
# CRUD (read / update / delete)
# ---------------------------------------------------------------------


@router.get("", response_model=RoomListResponse)
def list_rooms(
    conn: Annotated[sqlite3.Connection, Depends(get_rooms_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RoomListResponse:
    """Return all rooms, sorted case-insensitively by display_name."""
    rows = conn.execute("SELECT * FROM rooms ORDER BY display_name COLLATE NOCASE ASC").fetchall()
    return RoomListResponse(rooms=[_row_to_room(r) for r in rows])


@router.get("/{room_id}", response_model=RoomResponse)
def get_room(
    room_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_rooms_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RoomResponse:
    row = _fetch_room_row(conn, room_id)
    return _row_to_room(row)


@router.get("/{room_id}/features", response_model=RoomFeatureListResponse)
def list_room_features(
    room_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_rooms_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RoomFeatureListResponse:
    _fetch_room_row(conn, room_id)  # 404 if missing
    rows = conn.execute(
        "SELECT * FROM room_features WHERE room_id = ? ORDER BY name COLLATE NOCASE ASC",
        (room_id,),
    ).fetchall()
    return RoomFeatureListResponse(features=[_row_to_feature(r) for r in rows])


@router.patch("/{room_id}", response_model=RoomResponse)
def patch_room(
    room_id: str,
    body: RoomUpdateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_rooms_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RoomResponse:
    """Partial update — only fields present in the body are written."""
    existing = _fetch_room_row(conn, room_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        return _row_to_room(existing)

    # Detect new-name collision before writing.
    if "display_name" in data:
        new_name = data["display_name"]
        collide = conn.execute(
            "SELECT id FROM rooms WHERE display_name IS NOT NULL "
            "AND lower(display_name) = lower(?) AND id != ? LIMIT 1",
            (new_name, room_id),
        ).fetchone()
        if collide is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "room_label_collision",
                    "label": new_name,
                    "existing_id": collide["id"],
                },
            )

    columns = list(data.keys())
    set_clause = ", ".join(f"{col} = ?" for col in columns)
    params: list[Any] = [data[col] for col in columns]
    params.append(room_id)
    with conn:
        conn.execute(f"UPDATE rooms SET {set_clause} WHERE id = ?", params)
    row = _fetch_room_row(conn, room_id)
    return _row_to_room(row)


@router.post("/{room_id}/image", response_model=RoomResponse)
async def post_replace_room_image(
    room_id: str,
    file: Annotated[UploadFile, File()],
    conn: Annotated[sqlite3.Connection, Depends(get_rooms_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RoomResponse:
    """Replace the room's primary image with an uploaded file.

    Single-file flow (vs the bulk ingest endpoint) — the room already
    exists, we're just swapping its canonical ``image_path``. Re-
    uploading the room's *current* image is a no-op success: dedup
    excludes the room being edited so the parent doesn't 409 against
    themselves. Mirrors the toy image-replace endpoint's shape.
    """
    existing = _fetch_room_row(conn, room_id)

    cap = max_upload_bytes()
    try:
        raw = await _read_upload_bytes(file, cap)
    except UploadValidationError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, **exc.detail},
        ) from exc

    try:
        validated = validate_upload(raw, file.content_type)
    except UploadValidationError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, **exc.detail},
        ) from exc

    new_hash = compute_hash(raw)

    existing_hash = existing["image_hash"]
    if existing_hash is not None and new_hash == str(existing_hash):
        return _row_to_room(existing)

    dup = find_dedup(conn, "rooms", new_hash)
    if dup is not None and str(dup["id"]) != room_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "image_already_exists",
                "existing_room": _row_to_room(dup).model_dump(),
            },
        )

    handle = stage(raw, validated)
    try:
        committed = commit_staging(handle, target_subdir="rooms")
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "stage_lost"},
        ) from exc
    except StagingLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "staging_locked"},
        ) from exc

    new_image_path = relative_committed_path("rooms", handle.filename)
    new_image_hash = compute_hash(committed.read_bytes())
    old_image_path = existing["image_path"]

    try:
        with conn:
            conn.execute(
                "UPDATE rooms SET image_path = ?, image_hash = ? WHERE id = ?",
                (new_image_path, new_image_hash, room_id),
            )
    except sqlite3.IntegrityError as exc:
        try:
            committed.unlink()
        except OSError:
            _logger.warning("replace rollback: could not unlink %s", committed)
        race_dup = find_dedup(conn, "rooms", new_image_hash)
        if race_dup is not None and str(race_dup["id"]) != room_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "image_already_exists",
                    "existing_room": _row_to_room(race_dup).model_dump(),
                },
            ) from exc
        raise HTTPException(
            status_code=422,
            detail={"code": "db_constraint_violation"},
        ) from exc

    if old_image_path and str(old_image_path) != new_image_path:
        try:
            old_disk = on_disk_image_path(str(old_image_path))
            old_disk.unlink(missing_ok=True)
        except (ValueError, OSError):
            _logger.warning(
                "replace: failed to unlink old image %s",
                old_image_path,
                exc_info=True,
            )

    row = _fetch_room_row(conn, room_id)
    return _row_to_room(row)


@router.delete("/{room_id}", response_model=DeleteResponse)
def delete_room(
    room_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_rooms_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> DeleteResponse:
    """Hard-delete a room, cascading its features.

    Features (the ``room_features`` chips like "couch", "rug") are
    conceptually owned by the room — nothing else joins them in. The
    initial spec made the FK ``RESTRICT`` and surfaced a 409
    ``room_in_use`` to the parent, but that left the parent unable to
    delete an auto-suggested room that vision had populated with even
    one feature, with no cascade affordance. The FK stays RESTRICT at
    the schema level (defence against unintended code paths that
    delete rooms without going through this handler) and we cascade
    explicitly inside one transaction: features first, then the row.
    The image file is left on disk for the future janitor — same
    behaviour as before, same as toy archive.
    """
    _fetch_room_row(conn, room_id)
    with conn:
        conn.execute("DELETE FROM room_features WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
    return DeleteResponse(ok=True)


# ---------------------------------------------------------------------
# Public re-exports for tests.
# ---------------------------------------------------------------------

__all__ = [
    "Assignment",
    "BULK_UPLOAD_CAP_ENV",
    "BulkPhoto",
    "BulkUploadResponse",
    "ConfirmBulkRequest",
    "ConfirmBulkResponse",
    "DeleteResponse",
    "FeatureSuggestionWire",
    "HouseVisionSuggestionWire",
    "RoomFeatureListResponse",
    "RoomFeatureResponse",
    "RoomListResponse",
    "RoomResponse",
    "RoomUpdateRequest",
    "VISION_CONCURRENCY_ENV",
    "bulk_upload_cap",
    "get_rooms_db",
    "get_vision_breaker",
    "get_vision_client",
    "router",
    "vision_concurrency",
]
