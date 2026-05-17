"""Picture-reward CRUD pipeline + REST API (Phase L Step L2).

Mirrors the spine of :mod:`toybox.api.toys`:

* ``POST /api/rewards/upload`` (multipart, parent scope) — validates
  the bytes, stages the file under ``data/images/.staging/``, returns
  ``{staging_key, image_hash, mime_type, width, height}``. Dedup
  against active rewards short-circuits with 409
  ``image_already_exists`` BEFORE staging. There is no Claude vision
  suggestion phase: rewards are simple uploads (display_name, tags,
  animation, active toggle).
* ``POST /api/rewards`` (JSON, parent scope) — confirms the upload by
  moving the staged file to ``data/images/rewards/<id>.<ext>`` and
  inserting the ``rewards`` row. The id is server-derived from
  ``display_name`` per invariant 8 (see
  :func:`toybox.db.slugs.derive_slug`). After ``derive_slug`` produces
  the final id, the committed file is renamed from
  ``<staging_uuid>.<ext>`` to ``<reward_id>.<ext>`` BEFORE the DB
  insert so ``rewards.image_path`` records the slug-named path on disk.
* ``GET /api/rewards`` / ``GET /api/rewards/{id}`` (parent scope) — list
  (active-first ordering by ``last_used_at DESC NULLS LAST``, archived
  hidden by default) / get (visible even when archived, so the
  parent's archive button can show the row immediately after archiving
  it). Parent scope is required on the GETs — matches the existing
  ``toys.py`` / ``rooms.py`` convention; the plan §8 table said "none"
  but the codebase pattern wins per code-quality.md §1.
* ``PATCH /api/rewards/{id}`` (parent scope) — partial update.
* ``DELETE /api/rewards/{id}`` (parent scope) — soft archive (sets
  ``archived = 1``; image file stays on disk).

Image storage: per documentation/phase-l-plan.md §"Image storage path",
rewards live at ``data/images/rewards/<id>.<ext>``. The L2 build extends
:func:`toybox.storage.images.committed_dir`'s allow-list to include
``"rewards"``; one staging registry + one janitor sweep across all
image kinds keeps the producer/consumer surface stable
(code-quality.md §2). No sibling rewards-specific storage helper.

Tag normalisation (load-bearing for L3's resolver): every write path
that accepts tags routes through :func:`_normalise_tags` which strips
whitespace, lowercases, NFKC-normalises, drops empties, dedupes
(preserving first-seen order), and validates ``len(tag) <= 24`` and
``len(tags) <= 10``. Persisted to the ``rewards.tags`` column as a
JSON-encoded array (matches the migration's ``DEFAULT '[]'`` shape).

There is no UNIQUE index on ``rewards.image_hash`` (per migration 0019);
the API enforces dedup against non-archived rows at upload time and
again on the confirm-insert as a defense-in-depth IntegrityError catch.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import unicodedata
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..activities.models import Animation
from ..core.auth import TokenScope
from ..core.errors import ErrorCode
from ..db import connect, resolve_db_path
from ..db.slugs import InvalidDisplayNameError, derive_slug
from ..storage.images import (
    StagingId,
    StagingLockedError,
    UploadValidationError,
    commit_staging,
    committed_dir,
    compute_hash,
    find_dedup,
    max_upload_bytes,
    rename_committed_image,
    stage,
    sweep_stale_staging,
    validate_upload,
)
from .auth_dep import RequireScope

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rewards", tags=["rewards"])


# ---------------------------------------------------------------------
# Tag normalisation (shared with L3 if/when the resolver needs to
# canonicalise incoming theme strings the same way). Kept local to L2
# for now per the build prompt; L3 can move/share if necessary.
# British spelling matches the rest of the codebase (toys.py, rooms.py,
# children.py, etc. all use ``_normalise``).
# ---------------------------------------------------------------------


# Caps per documentation/phase-l-plan.md §1 ("max 24 chars per tag,
# max 10 tags per reward"). Anything over these raises ``ValueError``
# which Pydantic surfaces as HTTP 422.
_MAX_TAG_LENGTH: int = 24
_MAX_TAGS_PER_REWARD: int = 10


def _normalise_tags(raw: list[str]) -> list[str]:
    """Canonicalise a tag list per the Phase L tag-normalisation spec.

    Steps (per documentation/phase-l-plan.md §"Tag normalisation"):

    1. Strip whitespace.
    2. Lowercase.
    3. NFKC normalise via :func:`unicodedata.normalize`.
    4. Drop empties post-strip.
    5. Dedupe (preserve first-seen order).
    6. Validate: max 24 chars per tag, max 10 tags per reward.

    Raises :class:`ValueError` on validation failure (Pydantic field-
    validator pattern; surfaces as 422 to the client).

    Persisted shape downstream: JSON-encoded array (matches migration
    0019's ``tags TEXT NOT NULL DEFAULT '[]'``).
    """
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            raise ValueError(f"tag must be a string, got {type(entry).__name__}")
        # Steps 1-3: strip → lowercase → NFKC.
        normalised = unicodedata.normalize("NFKC", entry.strip().lower())
        # Step 4: drop empties post-strip.
        if not normalised:
            continue
        # Step 6 (per-tag): max 24 chars per tag.
        if len(normalised) > _MAX_TAG_LENGTH:
            raise ValueError(f"tag {normalised!r} exceeds {_MAX_TAG_LENGTH}-char cap")
        # Step 5: dedupe (preserve first-seen order).
        if normalised in seen:
            continue
        seen.add(normalised)
        out.append(normalised)
    # Step 6 (per-list): max 10 tags per reward, AFTER dedupe so a
    # parent typing ten variants of "pirate" doesn't trip the cap.
    if len(out) > _MAX_TAGS_PER_REWARD:
        raise ValueError(f"too many tags ({len(out)} > {_MAX_TAGS_PER_REWARD})")
    return out


def _encode_tags_for_db(tags: list[str]) -> str:
    """Encode a normalised tag list for storage as a JSON array.

    Matches migration 0019's ``tags TEXT NOT NULL DEFAULT '[]'``. We
    pin ``separators=(",", ":")`` so the on-disk encoding is byte-
    deterministic (avoids spurious diffs when the parent UI round-
    trips a row).
    """
    return json.dumps(tags, separators=(",", ":"))


def _decode_tags(raw: object) -> list[str]:
    """Decode the ``rewards.tags`` JSON column into the wire list.

    Unlike the toys ``tags`` column (CSV-encoded, mostly-untrusted free
    text), the rewards column is JSON-encoded by this module's own
    write path — so the only "malformed" branch is a hand-edited DB
    row. Tolerated with a WARNING log rather than a 500 so a parent
    fiddling at the SQLite shell can still load the parent UI.
    """
    if raw is None:
        return []
    if not isinstance(raw, str):
        _logger.warning(
            "rewards.tags: expected TEXT, got %s; treating as empty",
            type(raw).__name__,
        )
        return []
    stripped = raw.strip()
    if not stripped:
        return []
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        _logger.warning("rewards.tags: malformed JSON %r; treating as empty", raw)
        return []
    if not isinstance(decoded, list):
        _logger.warning(
            "rewards.tags: expected JSON array, got %s; treating as empty",
            type(decoded).__name__,
        )
        return []
    return [entry for entry in decoded if isinstance(entry, str)]


# ---------------------------------------------------------------------
# DI: DB connection
# ---------------------------------------------------------------------


def get_rewards_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dep: yield a rewards-scoped SQLite connection.

    ``check_same_thread=False`` matches the toys / children pattern —
    FastAPI's threadpool may dispatch generator setup, the handler
    body, and teardown on different anyio worker threads.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------


class RewardResponse(BaseModel):
    """Wire shape for a reward row.

    Mirrors documentation/phase-l-plan.md §8 exactly. ``id`` is the
    server-derived kebab-slug (invariant 8); ``image_path`` is the
    relative path under ``data/images/rewards/``; ``image_hash`` is the
    SHA-256 hex of the committed bytes; ``tags`` is the
    lowercase + NFKC-normalised + deduped list; ``animation`` is one
    of the six :class:`Animation` enum values.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str
    image_path: str
    image_hash: str
    tags: list[str]
    animation: Animation
    active: bool
    archived: bool
    created_at: str
    last_used_at: str | None


class RewardListResponse(BaseModel):
    """Envelope for ``GET /api/rewards``.

    Plan §8 shorthand notation says ``list[RewardResponse]``; codebase
    convention (``ToyListResponse``, ``RoomListResponse``,
    ``ChildProfileListResponse``) wraps every list in an envelope so
    the response shape can extend later (pagination, totals) without a
    wire-breaking change. Codebase pattern wins per code-quality.md §1.
    """

    model_config = ConfigDict(frozen=True)

    rewards: list[RewardResponse]


class RewardConfirmRequest(BaseModel):
    """Body for ``POST /api/rewards`` (commit a staged upload).

    Wire field ``staging_key`` matches the plan §8 contract (NOT
    ``staging_id`` from toys — Phase L uses ``staging_key`` for the
    rewards wire surface to leave the door open for a future
    base64-encoded handle without renaming a shipped field).
    """

    model_config = ConfigDict(frozen=True)

    staging_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    animation: Animation
    active: bool = True

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must be non-empty after trimming")
        if len(stripped) > 40:
            raise ValueError("display_name must be at most 40 characters")
        return stripped

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, value: list[str]) -> list[str]:
        return _normalise_tags(value)


# Shared message for the PATCH explicit-null guards below. PATCH treats
# "field omitted" as "leave unchanged"; an explicit ``null`` body would
# otherwise either (a) 500 on a NOT NULL column, (b) write the literal
# string ``"null"`` to a tag column, or (c) silently flip a bool to 0.
# Reject at the schema layer so the wire contract stays unambiguous.
_PATCH_NULL_REJECT_MSG: str = "explicit null not accepted; omit the field to leave it unchanged"


def _reject_explicit_null(value: object) -> object:
    """Pydantic ``mode="before"`` helper: reject explicit ``null`` bodies."""
    if value is None:
        raise ValueError(_PATCH_NULL_REJECT_MSG)
    return value


class RewardUpdateRequest(BaseModel):
    """Body for ``PATCH /api/rewards/{id}``. All fields optional.

    Every field defaults to ``None`` so the parent can send a partial
    update. Pydantic's ``exclude_unset=True`` filters fields the client
    never sent, but does NOT filter fields explicitly set to ``null``.
    To avoid 500s / silent data corruption on
    ``PATCH {"animation": null}`` / ``{"tags": null}`` / ``{"active":
    null}`` / etc., a ``mode="before"`` field validator on each field
    rejects explicit null with a 422 ValueError.
    """

    model_config = ConfigDict(frozen=True)

    display_name: str | None = None
    tags: list[str] | None = None
    animation: Animation | None = None
    active: bool | None = None
    archived: bool | None = None

    # Explicit-null guards — see module-level ``_reject_explicit_null``.
    @field_validator("display_name", mode="before")
    @classmethod
    def _reject_null_display_name(cls, value: object) -> object:
        return _reject_explicit_null(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _reject_null_tags(cls, value: object) -> object:
        return _reject_explicit_null(value)

    @field_validator("animation", mode="before")
    @classmethod
    def _reject_null_animation(cls, value: object) -> object:
        return _reject_explicit_null(value)

    @field_validator("active", mode="before")
    @classmethod
    def _reject_null_active(cls, value: object) -> object:
        return _reject_explicit_null(value)

    @field_validator("archived", mode="before")
    @classmethod
    def _reject_null_archived(cls, value: object) -> object:
        return _reject_explicit_null(value)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        # Reaching this validator means the value is not None (the
        # ``mode="before"`` guard above rejects explicit null first).
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must be non-empty after trimming")
        if len(stripped) > 40:
            raise ValueError("display_name must be at most 40 characters")
        return stripped

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, value: list[str]) -> list[str]:
        # Reaching this validator means the value is not None.
        return _normalise_tags(value)


class UploadResponse(BaseModel):
    """Wire shape for ``POST /api/rewards/upload`` success.

    Field names match documentation/phase-l-plan.md §8 verbatim:
    ``staging_key`` (NOT ``staging_id``), ``mime_type`` (NOT
    ``media_type``).
    """

    model_config = ConfigDict(frozen=True)

    staging_key: str
    image_hash: str
    mime_type: str
    width: int
    height: int


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _row_to_response(row: sqlite3.Row | dict[str, Any]) -> RewardResponse:
    """Project a ``rewards`` table row into the wire shape."""
    getter: Any = row.__getitem__
    raw_animation = str(getter("animation"))
    # Defense-in-depth: a hand-edited row with a stale animation value
    # would otherwise blow up Pydantic's StrEnum coercion on read. Log
    # a WARNING and pick the first enum member; the API write paths
    # constrain animation to the Animation enum so this can only fire
    # on operator DB-shell edits.
    try:
        animation = Animation(raw_animation)
    except ValueError:
        _logger.warning(
            "rewards.animation: unknown value %r; falling back to first enum member",
            raw_animation,
        )
        animation = next(iter(Animation))
    return RewardResponse(
        id=str(getter("id")),
        display_name=str(getter("display_name")),
        image_path=str(getter("image_path")),
        image_hash=str(getter("image_hash")),
        tags=_decode_tags(getter("tags")),
        animation=animation,
        active=bool(getter("active")),
        archived=bool(getter("archived")),
        created_at=str(getter("created_at")),
        last_used_at=getter("last_used_at"),
    )


def _fetch_reward_row(conn: sqlite3.Connection, reward_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM rewards WHERE id = ?",
        (reward_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "reward_not_found", "id": reward_id},
        )
    return row


def _existing_slugs(conn: sqlite3.Connection) -> list[str]:
    """Return every ``rewards.id`` currently in the table.

    Used as the ``existing_slugs`` argument to :func:`derive_slug` so
    collision-suffixing (``-2``, ``-3``, ...) is applied across active
    AND archived rows — re-uploading "Treasure Chest" after archiving
    the previous one yields ``treasure-chest-2``, not a reused id that
    would 409 on the PRIMARY KEY constraint.
    """
    cursor = conn.execute("SELECT id FROM rewards")
    return [str(row["id"]) for row in cursor.fetchall()]


# ---------------------------------------------------------------------
# Per-call staging registry (extension pairing) — same pattern as toys.
# We store the extension in a small in-memory dict because the client
# only sends back the staging_key string on confirm. The dict ages
# out at the same rate as the on-disk janitor sweep so abandoned
# uploads can't leak entries for the process lifetime.
# ---------------------------------------------------------------------


_staging_extensions: dict[str, tuple[str, float]] = {}


def _record_staging(handle: StagingId) -> None:
    _staging_extensions[handle.id] = (handle.extension, time.time())


def _resolve_staging(staging_key: str) -> StagingId | None:
    entry = _staging_extensions.get(staging_key)
    if entry is None:
        return None
    return StagingId(id=staging_key, extension=entry[0])


def _drop_staging(staging_key: str) -> None:
    _staging_extensions.pop(staging_key, None)


def _sweep_staging_registry(ttl_sec: int | None = None) -> int:
    """Evict registry entries older than the configured staging TTL."""
    from ..storage.images import staging_ttl_sec  # noqa: PLC0415

    ttl = ttl_sec if ttl_sec is not None else staging_ttl_sec()
    cutoff = time.time() - ttl
    expired = [sid for sid, (_, ts) in _staging_extensions.items() if ts <= cutoff]
    for sid in expired:
        _staging_extensions.pop(sid, None)
    return len(expired)


# ---------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------


async def _read_upload_bytes(file: UploadFile, max_bytes: int) -> bytes:
    """Read the entire upload into memory, capping at ``max_bytes + 1``.

    Reading one byte over the cap lets us reject early without buffering
    a hostile multi-GB body.
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
            # Drain to avoid a hung connection then reject.
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


@router.post("/upload", response_model=UploadResponse)
async def post_upload(
    file: Annotated[UploadFile, File()],
    conn: Annotated[sqlite3.Connection, Depends(get_rewards_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> UploadResponse:
    """Validate, dedup, then stage. No vision call — rewards are simple."""
    # Janitor: sweep stale staging files before writing a fresh one.
    try:
        sweep_stale_staging()
    except Exception:  # noqa: BLE001
        _logger.warning("reward upload: staging sweep failed; continuing", exc_info=True)
    try:
        _sweep_staging_registry()
    except Exception:  # noqa: BLE001
        _logger.warning("reward upload: registry sweep failed; continuing", exc_info=True)

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

    image_hash = compute_hash(raw)

    # Dedup against non-archived rewards. The migration ships no UNIQUE
    # index on ``rewards.image_hash`` (per 0019 docstring); this API-
    # layer check is the soft equivalent. Re-uploading the same image
    # after archiving the previous reward is allowed (parent legit use
    # case: "I deleted that reward but want it back"). Routes through
    # the shared ``find_dedup`` helper rather than a sibling reimpl
    # (code-quality.md §2 — one source of truth).
    existing = find_dedup(conn, "rewards", image_hash)
    if existing is not None:
        existing_response = _row_to_response(existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "image_already_exists",
                "existing_reward": existing_response.model_dump(),
            },
        )

    handle = stage(raw, validated)
    _record_staging(handle)

    return UploadResponse(
        staging_key=handle.id,
        image_hash=image_hash,
        mime_type=validated.media_type,
        width=validated.width,
        height=validated.height,
    )


# ---------------------------------------------------------------------
# Confirm (commit) endpoint
# ---------------------------------------------------------------------


@router.post(
    "",
    response_model=RewardResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_confirm(
    body: RewardConfirmRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_rewards_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RewardResponse:
    """Commit a staged upload: move file + insert row."""
    handle = _resolve_staging(body.staging_key)
    if handle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "staging_not_found", "staging_key": body.staging_key},
        )

    # Derive the slug server-side per invariant 8. Empty/all-symbol
    # display_names hit InvalidDisplayNameError → 422 with the canonical
    # ErrorCode.invalid_display_name envelope.
    #
    # NOTE: ``derive_slug`` auto-suffixes (``-2``, ``-3``, ...) on
    # collision rather than returning the bare slug. This diverges from
    # plan §8 which says "400 duplicate slug" on collision; the auto-
    # suffix UX is correct (parent doesn't have to invent a unique name
    # for every reward). The plan correction is handled in the
    # orchestrator's post-merge plan-update step.
    try:
        new_id = derive_slug(body.display_name, _existing_slugs(conn))
    except InvalidDisplayNameError as exc:
        # The Pydantic field validator already strips + length-checks,
        # but a name that's all punctuation ("@@@") survives that and
        # slugifies to "". Surface here so callers see the proper code.
        _drop_staging(body.staging_key)
        raise HTTPException(
            status_code=422,
            detail={"code": ErrorCode.invalid_display_name.value, "display_name": exc.display_name},
        ) from exc

    # Move the staging file to ``data/images/rewards/``. Done BEFORE the
    # DB insert so the recorded ``image_path`` reflects what's on disk.
    try:
        committed = commit_staging(handle, target_subdir="rewards")
    except FileNotFoundError as exc:
        # The staging file is gone — janitor swept it, OR the client
        # confirmed twice. Drop the registry entry so the second call's
        # 404 is honest.
        _drop_staging(body.staging_key)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "staging_not_found", "staging_key": body.staging_key},
        ) from exc
    except StagingLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "staging_locked", "staging_key": body.staging_key},
        ) from exc

    # Rename the committed file from ``<staging_uuid>.<ext>`` to
    # ``<reward_id>.<ext>`` so the on-disk filename matches the slug
    # invariant 8 (plan §"Image storage path"). Both names live in
    # ``data/images/rewards/`` so ``os.replace`` is atomic-within-volume.
    slug_filename = f"{new_id}.{handle.extension}"
    try:
        image_path = rename_committed_image("rewards", handle.filename, slug_filename)
        committed = committed_dir("rewards") / slug_filename
    except OSError as exc:
        # Roll back: drop whichever file exists, drop the staging entry.
        for candidate in (
            committed_dir("rewards") / handle.filename,
            committed_dir("rewards") / slug_filename,
        ):
            try:
                candidate.unlink()
            except OSError:
                continue
        _drop_staging(body.staging_key)
        _logger.warning(
            "rewards confirm: rename %s -> %s failed: %s",
            handle.filename,
            slug_filename,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "rename_failed", "reason": str(exc)},
        ) from exc

    image_hash = compute_hash(committed.read_bytes())
    tags_blob = _encode_tags_for_db(body.tags)
    created_at = _now_iso()

    try:
        with conn:
            conn.execute(
                "INSERT INTO rewards "
                "(id, display_name, image_path, image_hash, tags, "
                " animation, active, archived, created_at, last_used_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL)",
                (
                    new_id,
                    body.display_name,
                    image_path,
                    image_hash,
                    tags_blob,
                    body.animation.value,
                    1 if body.active else 0,
                    created_at,
                ),
            )
    except sqlite3.IntegrityError as exc:
        # The only realistic IntegrityError on this insert is a race
        # against another concurrent confirm picking the same slug
        # (derive_slug computed the suffix from a stale read). Surface
        # as 409; the client can retry which will see the now-taken
        # slug and pick the next suffix.
        try:
            committed.unlink()
        except OSError:
            _logger.warning("commit rollback: could not unlink %s", committed)
        _drop_staging(body.staging_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "reward_slug_collision", "id": new_id},
        ) from exc
    except sqlite3.Error as exc:
        try:
            committed.unlink()
        except OSError:
            _logger.warning("commit rollback: could not unlink %s", committed)
        _drop_staging(body.staging_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "reward_insert_failed", "reason": str(exc)},
        ) from exc

    _drop_staging(body.staging_key)
    row = _fetch_reward_row(conn, new_id)
    return _row_to_response(row)


# ---------------------------------------------------------------------
# CRUD (read / update / archive)
# ---------------------------------------------------------------------


@router.get("", response_model=RewardListResponse)
def list_rewards(
    conn: Annotated[sqlite3.Connection, Depends(get_rewards_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RewardListResponse:
    """Return non-archived rewards, ordered active-first by recency.

    Sort per documentation/phase-l-plan.md §8: active-first, then by
    ``last_used_at DESC`` within the active partition. NULL
    ``last_used_at`` is treated as "never used" — sorted AFTER any
    used reward so freshly-uploaded items don't immediately dominate
    the top of the list.

    Archived rows are filtered out by default (no ``include_archived``
    query param: toys.py's list_toys hides archived rows without a
    toggle, and the build prompt says "match exactly").

    Parent scope required — matches ``toys.py`` / ``rooms.py``.
    """
    rows = conn.execute(
        "SELECT * FROM rewards "
        "WHERE archived = 0 "
        # Active rows first (active=1 sorts higher than active=0 with
        # DESC); within each active partition, recently-used first,
        # NULL last_used_at last via ``last_used_at IS NULL`` priming
        # (1 sorts after 0 with ASC).
        "ORDER BY active DESC, "
        "         last_used_at IS NULL ASC, "
        "         last_used_at DESC, "
        "         created_at DESC"
    ).fetchall()
    return RewardListResponse(rewards=[_row_to_response(r) for r in rows])


@router.get("/{reward_id}", response_model=RewardResponse)
def get_reward(
    reward_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_rewards_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RewardResponse:
    """Return one reward by id. Archived rows are visible by id.

    Parent scope required — matches ``toys.py`` / ``rooms.py``.
    """
    row = _fetch_reward_row(conn, reward_id)
    return _row_to_response(row)


@router.patch("/{reward_id}", response_model=RewardResponse)
def patch_reward(
    reward_id: str,
    body: RewardUpdateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_rewards_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RewardResponse:
    """Partial update — only fields present in the body are written."""
    existing = _fetch_reward_row(conn, reward_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        return _row_to_response(existing)
    columns: list[str] = []
    params: list[Any] = []
    for col, value in data.items():
        if col == "tags":
            columns.append("tags = ?")
            # Field validator already normalized; encode for storage.
            # ``exclude_unset`` ensures None never reaches here.
            params.append(_encode_tags_for_db(value))
        elif col == "animation":
            columns.append("animation = ?")
            # Pydantic gives us an :class:`Animation` enum instance; the
            # column is TEXT so store the .value.
            params.append(value.value)
        elif col == "archived":
            columns.append("archived = ?")
            params.append(1 if value else 0)
        elif col == "active":
            columns.append("active = ?")
            params.append(1 if value else 0)
        else:
            columns.append(f"{col} = ?")
            params.append(value)
    params.append(reward_id)
    set_clause = ", ".join(columns)
    with conn:
        conn.execute(f"UPDATE rewards SET {set_clause} WHERE id = ?", params)
    row = _fetch_reward_row(conn, reward_id)
    return _row_to_response(row)


@router.delete("/{reward_id}", response_model=RewardResponse)
def delete_reward(
    reward_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_rewards_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RewardResponse:
    """Soft delete — set ``archived = 1``. The image file stays on disk.

    Returns the now-archived row so the parent UI can render it briefly
    (with an "archived" indicator) before the next list refresh hides
    it. Plan §8 contract: response is ``RewardResponse`` with
    ``archived=true``.
    """
    _fetch_reward_row(conn, reward_id)  # 404 if missing
    with conn:
        conn.execute("UPDATE rewards SET archived = 1 WHERE id = ?", (reward_id,))
    row = _fetch_reward_row(conn, reward_id)
    return _row_to_response(row)


__all__ = [
    "RewardConfirmRequest",
    "RewardListResponse",
    "RewardResponse",
    "RewardUpdateRequest",
    "UploadResponse",
    "get_rewards_db",
    "router",
]
