"""Single-toy ingest pipeline + toy CRUD REST API.

Step 16 ships the parent-facing flow:

* ``POST /api/toys/upload`` (multipart) — validates the bytes, runs
  the SHA-256 dedup check, stages the file, optionally calls Claude
  vision to suggest fields. Returns ``{staging_id, image_hash,
  suggested, vision_error?, vision_skipped?}`` or 409 on dedup hit
  with ``existing_toy``.
* ``POST /api/toys`` (JSON) — confirms the upload by moving the
  staged file to ``data/images/toys/`` and inserting the ``toys``
  row. Body carries the (parent-edited) suggested fields.
* ``GET /api/toys`` / ``GET /api/toys/{id}`` — list / get.
* ``PATCH /api/toys/{id}`` — partial update.
* ``DELETE /api/toys/{id}`` — soft archive (file kept on disk).

The shared :mod:`toybox.storage.images` helper carries the
validate/stage/commit primitives so step 17 (room bulk ingest) can
reuse them with ``subdir="rooms"``. The vision call is gated by
:func:`toybox.ai.capability.is_capable` — when offline (Claude not
capable, breaker open, no token) we skip the call and the parent UI
gets ``vision_skipped: true``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
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
from ..ai.toy_vision import (
    ToyVisionSuggestion,
    suggest_fields,
)
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from ..image_gen.capability import CapabilityReason, is_image_gen_capable
from ..image_gen.models import ACTION_SLOTS, ToyActionStatus
from ..image_gen.worker import get_image_gen_worker
from ..storage.images import (
    StagingId,
    StagingLockedError,
    UploadValidationError,
    commit_staging,
    compute_hash,
    downscale_for_vision,
    find_dedup,
    max_upload_bytes,
    on_disk_image_path,
    relative_committed_path,
    stage,
    sweep_stale_staging,
    validate_upload,
)
from ..storage.toy_actions import list_for_toy
from ..triggers.dynamic import refresh_mention_toys
from .auth_dep import RequireScope

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/toys", tags=["toys"])

# Module-singleton breaker for the toy-vision call site. Mirrors the
# pattern used by the activities/judge call: one in-process breaker
# guards every Claude vision request from this app.
_VISION_BREAKER: CircuitBreaker = CircuitBreaker()


# ---------------------------------------------------------------------
# DI: DB connection + AI client + capability check
# ---------------------------------------------------------------------


def get_toys_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dep: yield a toys-scoped SQLite connection.

    ``check_same_thread=False`` matches the children/activities pattern
    — FastAPI's threadpool may dispatch generator setup, the handler
    body, and teardown on different anyio worker threads.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def get_vision_client() -> AIClient | None:
    """FastAPI dep: build an AIClient if a token is on disk, else None.

    Mirrors the lazy pattern in
    :func:`toybox.api.activities.get_judge_call`. Tests override this
    dep with a stub that returns a deterministic
    :class:`~toybox.ai.client.StubClient`.
    """
    # Late imports keep AnthropicClient's lazy SDK import off the
    # module-load path.
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


class ToyResponse(BaseModel):
    """Wire shape for a toy row."""

    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str
    image_path: str
    image_hash: str
    tags: list[str]
    persona_id: str | None
    archived: bool
    created_at: str
    last_used_at: str | None


class ToyListResponse(BaseModel):
    """Envelope for ``GET /api/toys``."""

    model_config = ConfigDict(frozen=True)

    toys: list[ToyResponse]


class ToyVisionSuggestionWire(BaseModel):
    """Pydantic mirror of :class:`~toybox.ai.toy_vision.ToyVisionSuggestion`.

    We keep a separate wire model so the wire schema is stable even if
    the AI module's parsed shape evolves (e.g. adding a confidence
    field that the UI doesn't render yet).
    """

    model_config = ConfigDict(frozen=True)

    display_name: str
    tags: list[str]
    persona_match_id: str | None


class UploadResponse(BaseModel):
    """Wire shape for ``POST /api/toys/upload`` success."""

    model_config = ConfigDict(frozen=True)

    staging_id: str
    image_hash: str
    suggested: ToyVisionSuggestionWire | None
    vision_error: str | None = None
    vision_skipped: bool = False
    media_type: str
    width: int
    height: int


class ToyConfirmRequest(BaseModel):
    """Body for ``POST /api/toys`` (commit a staged upload)."""

    model_config = ConfigDict(frozen=True)

    staging_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list, max_length=20)
    persona_id: str | None = None

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
        out: list[str] = []
        seen: set[str] = set()
        for tag in value:
            stripped = tag.strip()
            if not stripped:
                raise ValueError("tag must be non-empty after trimming")
            if len(stripped) > 40:
                raise ValueError("tag must be at most 40 characters")
            key = stripped.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(stripped)
        return out


class ToyUpdateRequest(BaseModel):
    """Body for ``PATCH /api/toys/{id}``. All fields optional."""

    model_config = ConfigDict(frozen=True)

    display_name: str | None = None
    tags: list[str] | None = None
    persona_id: str | None = None
    archived: bool | None = None

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str:
        # PATCH cannot clear the column — ``toys.display_name`` is
        # NOT NULL, so a literal ``null`` body would otherwise make us
        # try ``SET display_name = NULL`` and 500 on IntegrityError.
        # Reject explicit nulls at the schema layer instead.
        if value is None:
            raise ValueError("display_name cannot be cleared")
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must be non-empty after trimming")
        if len(stripped) > 40:
            raise ValueError("display_name must be at most 40 characters")
        return stripped

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        out: list[str] = []
        seen: set[str] = set()
        for tag in value:
            stripped = tag.strip()
            if not stripped:
                raise ValueError("tag must be non-empty after trimming")
            if len(stripped) > 40:
                raise ValueError("tag must be at most 40 characters")
            key = stripped.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(stripped)
        return out


class DeleteResponse(BaseModel):
    """Envelope for ``DELETE /api/toys/{id}`` (soft archive)."""

    model_config = ConfigDict(frozen=True)

    ok: bool = True
    archived: bool = True


# ---------------------------------------------------------------------
# Phase F Step F5: action-sprite REST shapes
# ---------------------------------------------------------------------


class ToyActionResponse(BaseModel):
    """Wire shape for one row in :class:`ToyActionsResponse`.

    Mirrors :class:`toybox.image_gen.models.ToyActionRow` with one
    behavioural difference: ``status`` is serialised as the underlying
    enum value (a plain string) so the parent UI can switch on the
    canonical literals (``not_started``, ``queued``, ``running``,
    ``done``, ``failed``, ``superseded``) without a runtime import of
    the StrEnum.
    """

    model_config = ConfigDict(frozen=True)

    toy_id: str
    slot: str
    status: ToyActionStatus
    image_path: str | None = None
    seed: int | None = None
    error_msg: str | None = None
    updated_at: str


class CapabilityInfo(BaseModel):
    """Snapshot of :func:`is_image_gen_capable` for the parent banner.

    Bundled into :class:`ToyActionsResponse` so the grid can render the
    disabled-banner without a second round-trip; ``capable=False`` also
    means the regenerate buttons should be disabled, with ``reason``
    surfaced verbatim.
    """

    model_config = ConfigDict(frozen=True)

    capable: bool
    reason: str


class ToyActionsResponse(BaseModel):
    """Envelope for ``GET /api/toys/{id}/actions``.

    ``actions`` always carries exactly 10 rows in
    :data:`toybox.image_gen.models.ACTION_SLOTS` order — slots without
    a DB row are synthesized as ``not_started`` placeholders by
    :func:`toybox.storage.toy_actions.list_for_toy`.

    ``mode`` is ``"composite_only"`` when the capability gate returned
    False with a non-env-disabled reason (no CUDA / low VRAM / missing
    checkpoints) — the worker is dispatching the Tier C composite
    fallback rather than the full Tier B diffusion pipeline. The parent
    UI surfaces this as a "running in composite-only mode" banner.
    Otherwise ``mode`` is ``None``.
    """

    model_config = ConfigDict(frozen=True)

    actions: list[ToyActionResponse]
    capability: CapabilityInfo
    mode: str | None = None


class RegenerateResponse(BaseModel):
    """Envelope for the two regenerate POST endpoints.

    ``queued`` is the list of slot keys that were just enqueued. For
    ``POST /api/toys/{id}/actions/regenerate`` it equals
    :data:`ACTION_SLOTS` (all 10); for the per-slot variant it is a
    one-element list ``[slot]``.

    ``mode`` is ``"composite_only"`` when the capability gate returned
    False with a non-env-disabled reason (Tier C fallback active);
    otherwise ``None``. See :class:`ToyActionsResponse`.
    """

    model_config = ConfigDict(frozen=True)

    queued: list[str]
    mode: str | None = None


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _split_tags(raw: str | None) -> list[str]:
    """Decode the comma-separated ``toys.tags`` TEXT column into a list."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _join_tags(tags: list[str]) -> str | None:
    """Encode a list back into the ``toys.tags`` TEXT column shape."""
    if not tags:
        return None
    return ",".join(tags)


def _row_to_response(row: sqlite3.Row | dict[str, Any]) -> ToyResponse:
    # Both ``sqlite3.Row`` and ``dict`` expose a ``__getitem__`` that
    # accepts a string key and returns the column value, so we just
    # bind once and call uniformly.
    getter: Any = row.__getitem__
    return ToyResponse(
        id=str(getter("id")),
        display_name=str(getter("display_name")),
        image_path=str(getter("image_path")),
        image_hash=str(getter("image_hash")),
        tags=_split_tags(getter("tags")),
        persona_id=getter("persona_id"),
        archived=bool(getter("archived")),
        created_at=str(getter("created_at")),
        last_used_at=getter("last_used_at"),
    )


def _fetch_toy_row(conn: sqlite3.Connection, toy_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM toys WHERE id = ?",
        (toy_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "toy_not_found", "id": toy_id},
        )
    return row


# ---------------------------------------------------------------------
# Per-call staging registry: pair the staging UUID with its extension.
# We store the extension in a small in-memory dict because the client
# only sends back the staging_id string on confirm. An alternative is
# to encode the extension in the id itself; the dict is simpler.
#
# Entries carry a created-at timestamp so the on-disk janitor's TTL
# also evicts the dict — without this, abandoned uploads (parent
# closes the tab without confirming) would leak entries for the
# process lifetime. We piggy-back on ``sweep_stale_staging`` so the
# in-memory and on-disk views stay aligned.
# ---------------------------------------------------------------------


_staging_extensions: dict[str, tuple[str, float]] = {}


def _record_staging(handle: StagingId) -> None:
    _staging_extensions[handle.id] = (handle.extension, time.time())


def _resolve_staging(staging_id: str) -> StagingId | None:
    entry = _staging_extensions.get(staging_id)
    if entry is None:
        return None
    return StagingId(id=staging_id, extension=entry[0])


def _drop_staging(staging_id: str) -> None:
    _staging_extensions.pop(staging_id, None)


def _sweep_staging_registry(ttl_sec: int | None = None) -> int:
    """Evict registry entries older than the configured staging TTL.

    Mirrors :func:`toybox.storage.images.sweep_stale_staging` so the
    in-memory map ages out at the same rate as the on-disk files.
    Returns the count of entries removed.
    """
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
    a hostile multi-GB body. The router rejects anyway on the actual
    size check below; this is defense in depth.
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
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    breaker: Annotated[CircuitBreaker, Depends(get_vision_breaker)],
    ai_client: Annotated[AIClient | None, Depends(get_vision_client)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> UploadResponse:
    """Validate, dedup, stage, then run vision to suggest fields."""
    # Janitor: sweep stale staging files before writing a fresh one.
    # This runs every request, but each invocation only stat()s the
    # files in the staging dir — cheap, and concurrent uploads can't
    # collide because each generates its own UUID path.
    try:
        sweep_stale_staging()
    except Exception:  # noqa: BLE001
        _logger.warning("toy upload: staging sweep failed; continuing", exc_info=True)
    # Same TTL governs the in-memory registry — evict abandoned
    # confirms (parent closed the tab) so the dict can't grow unbounded.
    try:
        _sweep_staging_registry()
    except Exception:  # noqa: BLE001
        _logger.warning("toy upload: registry sweep failed; continuing", exc_info=True)

    cap = max_upload_bytes()
    try:
        raw = await _read_upload_bytes(file, cap)
    except UploadValidationError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, **exc.detail},
        ) from exc

    # Validate bytes (MIME-sniff, dimension, size).
    try:
        validated = validate_upload(raw, file.content_type)
    except UploadValidationError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, **exc.detail},
        ) from exc

    image_hash = compute_hash(raw)

    # Dedup: a non-archived toy with this hash short-circuits before
    # we stage anything. The frontend uses ``existing_toy`` to offer a
    # "view existing toy" link.
    existing = find_dedup(conn, "toys", image_hash)
    if existing is not None:
        existing_response = _row_to_response(existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "image_already_exists",
                "existing_toy": existing_response.model_dump(),
            },
        )

    handle = stage(raw, validated)
    _record_staging(handle)

    # Vision call: gated by capability + breaker. Failures degrade
    # gracefully — the parent always sees a usable response with the
    # staging_id so they can confirm with manual fields.
    suggestion: ToyVisionSuggestion | None = None
    vision_error: str | None = None
    vision_skipped = False

    if ai_client is None:
        vision_skipped = True
    else:
        capable, _reason = await is_capable(breaker, listening_mode=None)
        if not capable:
            vision_skipped = True
        else:
            downscaled = downscale_for_vision(raw)
            result = await suggest_fields(ai_client, downscaled)
            if isinstance(result, ToyVisionSuggestion):
                suggestion = result
                breaker.record_success()
            else:
                _none, reason = result
                vision_error = reason
                if reason == "rate_limited":
                    breaker.record_429(retry_after=None)
                else:
                    breaker.record_failure()

    suggested_wire: ToyVisionSuggestionWire | None
    if suggestion is None:
        suggested_wire = None
    else:
        suggested_wire = ToyVisionSuggestionWire(
            display_name=suggestion.display_name,
            tags=suggestion.tags,
            persona_match_id=suggestion.persona_match_id,
        )

    return UploadResponse(
        staging_id=handle.id,
        image_hash=image_hash,
        suggested=suggested_wire,
        vision_error=vision_error,
        vision_skipped=vision_skipped,
        media_type=validated.media_type,
        width=validated.width,
        height=validated.height,
    )


# ---------------------------------------------------------------------
# Confirm (commit) endpoint
# ---------------------------------------------------------------------


@router.post(
    "",
    response_model=ToyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_confirm(
    body: ToyConfirmRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ToyResponse:
    """Commit a staged upload: move file + insert row + refresh triggers."""
    handle = _resolve_staging(body.staging_id)
    if handle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "staging_not_found", "staging_id": body.staging_id},
        )

    # Move the staging file to ``data/images/toys/``. We do this BEFORE
    # the DB insert so the ``image_path`` we record is the real on-disk
    # location. If the insert fails afterwards we delete the moved file
    # to keep the filesystem clean (cleaner than the orphan-row case
    # because the row insert is the cheaper, more reversible step).
    try:
        committed = commit_staging(handle, target_subdir="toys")
    except FileNotFoundError as exc:
        # The staging file is gone — most likely the janitor swept it,
        # OR the client confirmed twice. Drop the registry entry so the
        # second call's 404 is honest.
        _drop_staging(body.staging_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "staging_not_found", "staging_id": body.staging_id},
        ) from exc
    except StagingLockedError as exc:
        # Windows AV / Search Indexer briefly holds the file. We've
        # already retried inside commit_staging; surface 503 so the
        # client can prompt the parent to retry.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "staging_locked", "staging_id": body.staging_id},
        ) from exc

    new_id = uuid.uuid4().hex
    image_path = relative_committed_path("toys", handle.filename)
    # Recompute the hash from the committed file so the DB always
    # reflects what's on disk (even if the client lied earlier — they
    # can't because we computed the dedup hash ourselves on upload, but
    # this keeps the invariant local to commit).
    image_hash = compute_hash(committed.read_bytes())
    tags_blob = _join_tags(body.tags)
    created_at = _now_iso()
    try:
        with conn:
            conn.execute(
                "INSERT INTO toys "
                "(id, display_name, image_path, image_hash, type, tags, "
                " persona_id, archived, created_at, last_used_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, 0, ?, NULL)",
                (
                    new_id,
                    body.display_name,
                    image_path,
                    image_hash,
                    tags_blob,
                    body.persona_id,
                    created_at,
                ),
            )
    except sqlite3.IntegrityError as exc:
        # Two integrity-violation buckets fire here:
        #   (a) the dedup index fired because a concurrent upload
        #       raced us to insert the same hash → 409 image_already_exists
        #   (b) the FK on persona_id rejected a non-existent persona →
        #       422 invalid_persona_id (FK is RESTRICT, foreign_keys=ON)
        # SQLite reports both as IntegrityError; we disambiguate by
        # checking the message + a dedup probe.
        try:
            committed.unlink()
        except OSError:
            _logger.warning("commit rollback: could not unlink %s", committed)
        _drop_staging(body.staging_id)
        msg = str(exc).lower()
        if "foreign key" in msg:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_persona_id"},
            ) from exc
        existing = find_dedup(conn, "toys", image_hash)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "image_already_exists",
                    "existing_toy": _row_to_response(existing).model_dump(),
                },
            ) from exc
        # Unknown IntegrityError — surface as a generic 422 so we don't
        # mask the failure as a 500 or misleading 409.
        raise HTTPException(
            status_code=422,
            detail={"code": "db_constraint_violation"},
        ) from exc
    except sqlite3.Error as exc:
        # Unknown DB error — keep the filesystem consistent with the
        # absence of a row by deleting the just-moved file.
        try:
            committed.unlink()
        except OSError:
            _logger.warning("commit rollback: could not unlink %s", committed)
        _drop_staging(body.staging_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "toy_insert_failed", "reason": str(exc)},
        ) from exc

    _drop_staging(body.staging_id)
    # Refresh the dynamic mention_toy trigger registry so the new toy
    # is picked up on the next transcript scan. Best-effort — the
    # current dynamic source rebuilds on every match() call so a
    # failure here is benign, but we surface it in logs in case a
    # future event-driven cache makes this load-bearing.
    try:
        refresh_mention_toys(conn)
    except Exception:  # noqa: BLE001
        _logger.warning("refresh_mention_toys failed after toy insert", exc_info=True)

    # Phase F Step F5: enqueue 10 image-gen jobs (one per ACTION_SLOTS
    # entry) so the parent UI's 2x5 sprite grid populates without an
    # explicit "regenerate all" click. Best-effort: a worker that isn't
    # running, an offline capability gate, or an enqueue exception are
    # all logged and swallowed so toy creation still succeeds — the toy
    # is fully usable without sprites and the parent can hit the
    # explicit regenerate-all endpoint to retry.
    await _maybe_enqueue_action_jobs_for_toy(new_id)

    row = _fetch_toy_row(conn, new_id)
    return _row_to_response(row)


async def _maybe_enqueue_action_jobs_for_toy(toy_id: str) -> None:
    """Best-effort post-commit hook: enqueue 10 image-gen jobs.

    Step F5's "after-commit" wiring per the plan §F5. Runs through the
    capability gate and the singleton worker accessor; logs and
    swallows every failure so a toy-create response is never blocked
    on the image-gen subsystem.
    """
    worker = get_image_gen_worker()
    if worker is None:
        # Worker hasn't been started (e.g. run mode without the
        # image-gen lifespan) — toy still saved, parent can ingest;
        # just nothing to enqueue.
        _logger.info(
            "toy %s: image-gen worker not running; skipping enqueue",
            toy_id,
        )
        return
    capable, reason_enum, reason = is_image_gen_capable(check_free_vram=False)
    # ENV_DISABLED is the only branch that suppresses enqueue. The
    # other False branches (NO_CUDA / LOW_VRAM / MISSING_CHECKPOINTS)
    # still enqueue — the worker dispatches to the Tier C composite
    # path per F.5-3a.
    if not capable and reason_enum is CapabilityReason.env_disabled:
        _logger.info(
            "toy %s: image-gen disabled (%s); skipping enqueue",
            toy_id,
            reason,
        )
        return
    try:
        for slot in ACTION_SLOTS:
            await worker.enqueue(toy_id, slot)
    except Exception as exc:  # noqa: BLE001 -- best-effort
        # Anything from a transient queue-full to a malformed slot
        # (shouldn't happen — ACTION_SLOTS is the canonical vocab) is
        # logged but doesn't break the toy create. The toy is still
        # usable; parent can hit the regenerate-all endpoint.
        _logger.warning(
            "toy %s: image-gen enqueue failed: %s",
            toy_id,
            exc,
            exc_info=True,
        )
        return
    _logger.info("toy %s: enqueued %d image-gen jobs", toy_id, len(ACTION_SLOTS))


# ---------------------------------------------------------------------
# CRUD (read / update / archive)
# ---------------------------------------------------------------------


@router.get("", response_model=ToyListResponse)
def list_toys(
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ToyListResponse:
    """Return non-archived toys, sorted case-insensitively by display name."""
    rows = conn.execute(
        "SELECT * FROM toys WHERE archived = 0 ORDER BY display_name COLLATE NOCASE ASC"
    ).fetchall()
    return ToyListResponse(toys=[_row_to_response(r) for r in rows])


@router.get("/{toy_id}", response_model=ToyResponse)
def get_toy(
    toy_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ToyResponse:
    row = _fetch_toy_row(conn, toy_id)
    return _row_to_response(row)


@router.patch("/{toy_id}", response_model=ToyResponse)
def patch_toy(
    toy_id: str,
    body: ToyUpdateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ToyResponse:
    """Partial update — only fields present in the body are written."""
    existing = _fetch_toy_row(conn, toy_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        return _row_to_response(existing)
    columns: list[str] = []
    params: list[Any] = []
    for col, value in data.items():
        if col == "tags":
            # Field validator already coerced to ``list[str] | None`` and
            # the ``not data`` short-circuit + ``exclude_unset`` filter
            # mean None values can't reach this branch.
            columns.append("tags = ?")
            params.append(_join_tags(value))
        elif col == "archived":
            columns.append("archived = ?")
            params.append(1 if value else 0)
        else:
            columns.append(f"{col} = ?")
            params.append(value)
    params.append(toy_id)
    set_clause = ", ".join(columns)
    with conn:
        conn.execute(f"UPDATE toys SET {set_clause} WHERE id = ?", params)
    # If display_name or archived changed, the trigger registry needs a
    # refresh so the new mention pattern (or the absence of an archived
    # toy's pattern) takes effect on the next transcript scan.
    if "display_name" in data or "archived" in data:
        try:
            refresh_mention_toys(conn)
        except Exception:  # noqa: BLE001
            _logger.warning("refresh_mention_toys failed after patch", exc_info=True)
    row = _fetch_toy_row(conn, toy_id)
    return _row_to_response(row)


@router.post("/{toy_id}/image", response_model=ToyResponse)
async def post_replace_image(
    toy_id: str,
    file: Annotated[UploadFile, File()],
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ToyResponse:
    """Replace the toy's image with an uploaded file.

    Mirrors the validate-then-stage-then-commit flow of ``/upload`` +
    ``POST /api/toys`` but in a single step: there's no vision
    suggestion phase (the parent already named the toy on first
    create). Re-uploading the toy's *current* image is a no-op
    success — the dedup check excludes the row we're editing so the
    parent doesn't get a 409 against themselves.
    """
    existing = _fetch_toy_row(conn, toy_id)

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

    # No-op: client re-picked the same image. Skip the disk churn.
    if new_hash == str(existing["image_hash"]):
        return _row_to_response(existing)

    # Dedup against OTHER non-archived toys. Allow self because the
    # ``new_hash == existing hash`` path already short-circuited above.
    dup = find_dedup(conn, "toys", new_hash)
    if dup is not None and str(dup["id"]) != toy_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "image_already_exists",
                "existing_toy": _row_to_response(dup).model_dump(),
            },
        )

    handle = stage(raw, validated)
    try:
        committed = commit_staging(handle, target_subdir="toys")
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

    new_image_path = relative_committed_path("toys", handle.filename)
    new_image_hash = compute_hash(committed.read_bytes())
    old_image_path = str(existing["image_path"])

    try:
        with conn:
            conn.execute(
                "UPDATE toys SET image_path = ?, image_hash = ? WHERE id = ?",
                (new_image_path, new_image_hash, toy_id),
            )
    except sqlite3.IntegrityError as exc:
        # Concurrent insert raced us to the same hash. Roll back the
        # just-committed file so the FS doesn't accumulate orphans.
        try:
            committed.unlink()
        except OSError:
            _logger.warning("replace rollback: could not unlink %s", committed)
        race_dup = find_dedup(conn, "toys", new_image_hash)
        if race_dup is not None and str(race_dup["id"]) != toy_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "image_already_exists",
                    "existing_toy": _row_to_response(race_dup).model_dump(),
                },
            ) from exc
        raise HTTPException(
            status_code=422,
            detail={"code": "db_constraint_violation"},
        ) from exc

    # Best-effort old-file cleanup. Failure here is non-fatal — the
    # row already points at the new file, the old one just lingers
    # for the future janitor.
    if old_image_path and old_image_path != new_image_path:
        try:
            old_disk = on_disk_image_path(old_image_path)
            old_disk.unlink(missing_ok=True)
        except (ValueError, OSError):
            _logger.warning(
                "replace: failed to unlink old image %s",
                old_image_path,
                exc_info=True,
            )

    row = _fetch_toy_row(conn, toy_id)
    return _row_to_response(row)


@router.delete("/{toy_id}", response_model=DeleteResponse)
def delete_toy(
    toy_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> DeleteResponse:
    """Soft delete — set ``archived = 1``. The image file stays on disk."""
    _fetch_toy_row(conn, toy_id)
    with conn:
        conn.execute("UPDATE toys SET archived = 1 WHERE id = ?", (toy_id,))
    try:
        refresh_mention_toys(conn)
    except Exception:  # noqa: BLE001
        _logger.warning("refresh_mention_toys failed after archive", exc_info=True)
    return DeleteResponse(ok=True, archived=True)


# ---------------------------------------------------------------------
# Phase F Step F5: action-sprite endpoints
# ---------------------------------------------------------------------


def _validate_toy_id_or_404(toy_id: str) -> None:
    """Defensive UUIDv4 check at the REST boundary.

    The storage seam re-validates, but raising HTTP 404 with the
    canonical ``toy_not_found`` envelope here lets callers handle a
    bogus path segment (e.g. ``../foo``) the same way they handle a
    missing row, rather than a 500 from the storage seam's
    ``ValueError``.
    """
    from ..storage.toy_actions import _validate_toy_id  # noqa: PLC0415

    try:
        _validate_toy_id(toy_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "toy_not_found", "id": toy_id},
        ) from exc


def _action_row_to_response(
    row: Any,  # ToyActionRow — kept loose to avoid the import in the signature
) -> ToyActionResponse:
    """Project a :class:`ToyActionRow` into the wire shape."""
    return ToyActionResponse(
        toy_id=row.toy_id,
        slot=row.slot,
        status=row.status,
        image_path=row.image_path,
        seed=row.seed,
        error_msg=row.error_msg,
        updated_at=row.updated_at,
    )


@router.get("/{toy_id}/actions", response_model=ToyActionsResponse)
def get_toy_actions(
    toy_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ToyActionsResponse:
    """Return the 10-row action list + capability snapshot.

    Even when :func:`is_image_gen_capable` returns ``False`` we still
    return the existing rows + the capability info — per the plan
    §"is_image_gen_capable() as graceful degradation": existing
    ``done`` rows remain visible regardless of the capability gate
    state. The ``409 image_gen_disabled`` envelope only applies to
    the enqueue-side endpoints below.
    """
    _validate_toy_id_or_404(toy_id)
    _fetch_toy_row(conn, toy_id)  # 404 if toy doesn't exist.
    rows = list_for_toy(conn, toy_id)
    # Skip the live-VRAM branch: this endpoint informs the parent UI's
    # disabled banner, and a flickering "VRAM 1.5GB < 6GB" reason while
    # a sprite gen is mid-flight is misleading. See is_image_gen_capable
    # docstring for the full rationale.
    capable, reason_enum, reason = is_image_gen_capable(check_free_vram=False)
    return ToyActionsResponse(
        actions=[_action_row_to_response(r) for r in rows],
        capability=CapabilityInfo(capable=capable, reason=reason),
        mode=_mode_for_reason(capable, reason_enum),
    )


_COMPOSITE_ONLY_MODE: Final[str] = "composite_only"


def _mode_for_reason(capable: bool, reason_enum: CapabilityReason) -> str | None:
    """Return the response-body ``mode`` value for one capability state.

    F.5-3a wire contract: ``"composite_only"`` when the gate returned
    False with a non-env-disabled reason (Tier C fallback active);
    ``None`` otherwise (capable, or env-disabled which 409s instead).
    """
    if capable:
        return None
    if reason_enum is CapabilityReason.env_disabled:
        return None
    return _COMPOSITE_ONLY_MODE


def _check_capability_or_409() -> tuple[bool, CapabilityReason, str]:
    """Enforce the F.5-3a capability gate; return the full probe tuple.

    * ``CapabilityReason.capable`` → no-op; caller proceeds normally.
    * ``CapabilityReason.env_disabled`` → 409 ``image_gen_disabled``
      (operator explicitly off; no Tier C either).
    * Any other False reason (no_cuda / low_vram / missing_checkpoints)
      → no-op; the worker will dispatch to Tier C composite. Caller
      uses the returned ``(capable, reason_enum)`` to decide whether
      to set ``mode="composite_only"`` on the response.

    Returns ``(capable, reason_enum, reason)`` to mirror
    :func:`is_image_gen_capable`'s shape so callers don't have to
    reconstruct the bool from the enum.

    Skip the live-VRAM branch at request time: SDXL peaks at ~6 GB
    mid-gen on the 8 GB host, which drops free VRAM below the 6 GB
    floor while a sprite is being generated. Re-checking here would
    409 every regenerate click that lands during another slot's run.
    Real OOM is caught + breaker-tripped inside the worker.
    """
    capable, reason_enum, reason = is_image_gen_capable(check_free_vram=False)
    if not capable and reason_enum is CapabilityReason.env_disabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "image_gen_disabled", "reason": reason},
        )
    return capable, reason_enum, reason


def _require_worker_or_503() -> Any:
    """Return the singleton worker, raising 503 if it hasn't started.

    Distinguishes "image-gen disabled by the capability gate" (409 —
    actionable: free VRAM, enable env var) from "worker not started"
    (503 — operational: backend run mode without the image-gen
    lifespan, or the lifespan failed to start).
    """
    worker = get_image_gen_worker()
    if worker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "image_gen_worker_unavailable",
                "reason": "image-gen worker not running",
            },
        )
    return worker


@router.post("/{toy_id}/actions/regenerate", response_model=RegenerateResponse)
async def regenerate_all_actions(
    toy_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RegenerateResponse:
    """Enqueue all 10 :data:`ACTION_SLOTS` jobs for ``toy_id``.

    Returns 200 + queued list. F.5-3a: when the capability gate is
    closed for a non-env-disabled reason, the response also carries
    ``mode="composite_only"`` so the parent UI can render a banner;
    the worker dispatches to the Tier C composite path.
    """
    _validate_toy_id_or_404(toy_id)
    _fetch_toy_row(conn, toy_id)  # 404 if missing.
    capable, reason_enum, _reason = _check_capability_or_409()
    worker = _require_worker_or_503()
    for slot in ACTION_SLOTS:
        await worker.enqueue(toy_id, slot)
    return RegenerateResponse(
        queued=list(ACTION_SLOTS),
        mode=_mode_for_reason(capable, reason_enum),
    )


@router.post(
    "/{toy_id}/actions/{slot}/regenerate",
    response_model=RegenerateResponse,
)
async def regenerate_one_action(
    toy_id: str,
    slot: str,
    conn: Annotated[sqlite3.Connection, Depends(get_toys_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> RegenerateResponse:
    """Enqueue one slot — supersede semantics handled by the worker.

    F.5-3a: same composite-only mode signalling as
    :func:`regenerate_all_actions`.
    """
    _validate_toy_id_or_404(toy_id)
    _fetch_toy_row(conn, toy_id)
    if slot not in ACTION_SLOTS:
        # Plan §F5: out-of-vocab slot → 404 with code="slot_not_in_vocab"
        # so the parent UI can distinguish "you typo'd a slot in the URL"
        # from "the toy doesn't exist".
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "slot_not_in_vocab", "slot": slot},
        )
    capable, reason_enum, _reason = _check_capability_or_409()
    worker = _require_worker_or_503()
    await worker.enqueue(toy_id, slot)
    return RegenerateResponse(
        queued=[slot],
        mode=_mode_for_reason(capable, reason_enum),
    )


__all__ = [
    "CapabilityInfo",
    "DeleteResponse",
    "RegenerateResponse",
    "ToyActionResponse",
    "ToyActionsResponse",
    "ToyConfirmRequest",
    "ToyListResponse",
    "ToyResponse",
    "ToyUpdateRequest",
    "ToyVisionSuggestionWire",
    "UploadResponse",
    "get_toys_db",
    "get_vision_breaker",
    "get_vision_client",
    "router",
]
