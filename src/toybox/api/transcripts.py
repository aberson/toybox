"""Transcripts REST API.

Step 13 (Phase B) shipped the read-only surface:

* ``GET /api/transcripts?limit=50&before=<iso>`` — paginated list
  ordered by ``ended_at DESC`` (most recent first). The ``before``
  cursor is an ISO timestamp; rows with ``ended_at < before`` are
  returned. Pagination is cursor-based so a parent UI scrolling the
  audit log doesn't drift across inserts.
* ``GET /api/transcripts/search?q=<substring>&limit=50`` —
  case-insensitive substring search over ``text``. ``q`` is required
  and must be non-empty (HTTP 400 otherwise).

Step 22 (Phase D) adds the destructive surface, gated behind a
parent-scope token:

* ``DELETE /api/transcripts/{id}`` — single delete. 404 with
  ``transcript_not_found`` when the row is missing.
* ``DELETE /api/transcripts`` — wipe all rows. PIN re-confirm body
  required on top of the parent token; the wire shape and lock
  precedence mirror ``POST /api/auth/parent`` so a hot-recently-failed
  login session can't be bypassed by switching to the wipe-all surface.
  Wipe-all is intentionally a single ``DELETE FROM transcripts`` —
  ``transcripts`` has no FKs pointing at it (only its own FK to
  ``sessions``) so the operation does NOT cascade to other tables.
  The action CANNOT be undone.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..core.auth import TokenScope
from ..core.pin import PIN_MAX_LENGTH, PIN_MIN_LENGTH
from ..core.pin_rate_limit import PinRateLimiter
from ..db import connect, resolve_db_path
from .auth import enforce_pin_check, get_pin_rate_limiter
from .auth_dep import RequireScope, get_auth_db

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transcripts", tags=["transcripts"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def get_transcripts_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield a transcripts-scoped SQLite connection.

    ``check_same_thread=False`` because FastAPI dispatches sync
    generator setup, the handler body, and teardown via
    ``run_in_threadpool``; anyio may pick a different worker for each
    leg, which would otherwise trip ``sqlite3.ProgrammingError`` in
    ``conn.close()``.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class TranscriptRow(BaseModel):
    """Wire shape for one transcript row."""

    id: str
    session_id: str
    mic_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    text: str | None = None
    confidence: float | None = None
    language: str = "unknown"
    triggered_intent: str | None = None


class TranscriptListResponse(BaseModel):
    """Wire shape for the list/search endpoints."""

    items: list[TranscriptRow] = Field(default_factory=list)


class DeleteOneResponse(BaseModel):
    """Wire shape for ``DELETE /api/transcripts/{id}``."""

    ok: bool = True


class WipeAllRequest(BaseModel):
    """Body for ``DELETE /api/transcripts`` (wipe-all).

    Wipe is destructive and shares the global PIN rate limiter, so the
    body shape mirrors :class:`toybox.api.auth.ParentLoginRequest` —
    digits-only PIN, 4-:data:`PIN_MAX_LENGTH` chars. A missing or
    malformed body is rejected with 422 by Pydantic before the limiter
    sees anything.
    """

    pin: str = Field(min_length=PIN_MIN_LENGTH, max_length=PIN_MAX_LENGTH)


class WipeAllResponse(BaseModel):
    """Wire shape for the wipe-all endpoint.

    ``deleted`` is the number of rows removed by the single
    ``DELETE FROM transcripts`` — surfaced to the parent UI so the
    confirmation toast can read "deleted N transcripts" honestly.
    """

    deleted: int = Field(ge=0)


def _row_to_model(row: sqlite3.Row) -> TranscriptRow:
    return TranscriptRow(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        mic_id=row["mic_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        text=row["text"],
        confidence=row["confidence"],
        language=str(row["language"]) if row["language"] is not None else "unknown",
        triggered_intent=row["triggered_intent"],
    )


@router.get("", response_model=TranscriptListResponse)
def list_transcripts(
    conn: Annotated[sqlite3.Connection, Depends(get_transcripts_db)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    before: Annotated[str | None, Query()] = None,
) -> TranscriptListResponse:
    """Return up to ``limit`` transcripts, most recent ``ended_at`` first.

    When ``before`` is supplied, rows with ``ended_at < before`` are
    returned (cursor-based pagination so concurrent inserts don't
    shift the page). ``ended_at`` is a free-form text column at the
    schema level; SQLite's lexical comparison on the ISO-8601 strings
    we write produces the expected chronological ordering.

    The ``before`` cursor is parsed as ISO-8601 (both the trailing
    ``Z`` form we serialize and the explicit ``+00:00`` form are
    accepted) and rejected with HTTP 400 + ``invalid_before_cursor``
    on parse failure -- otherwise an unparseable string would silently
    compare lexically and return the wrong window.

    The ``ORDER BY ended_at DESC, id DESC`` adds a stable tiebreaker
    so two rows with identical timestamps return in a deterministic
    order. The cursor itself is timestamp-only for v1, so rows with
    identical ``ended_at`` may straddle a page boundary -- a future
    iteration can extend the cursor to ``(ended_at, id)`` to fully
    eliminate that.
    """
    if before is None:
        rows = conn.execute(
            "SELECT id, session_id, mic_id, started_at, ended_at, text, "
            "       confidence, language, triggered_intent "
            "FROM transcripts "
            "ORDER BY ended_at DESC, id DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        try:
            datetime.fromisoformat(before.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_before_cursor"},
            ) from exc
        rows = conn.execute(
            "SELECT id, session_id, mic_id, started_at, ended_at, text, "
            "       confidence, language, triggered_intent "
            "FROM transcripts "
            "WHERE ended_at IS NOT NULL AND ended_at < ? "
            "ORDER BY ended_at DESC, id DESC "
            "LIMIT ?",
            (before, limit),
        ).fetchall()
    return TranscriptListResponse(items=[_row_to_model(r) for r in rows])


@router.get("/search", response_model=TranscriptListResponse)
def search_transcripts(
    conn: Annotated[sqlite3.Connection, Depends(get_transcripts_db)],
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> TranscriptListResponse:
    """Case-insensitive substring search over ``text``.

    Empty ``q`` is rejected by Pydantic at the query layer (HTTP 422).
    Whitespace-only ``q`` is rejected here with HTTP 400 because the
    ``min_length`` constraint counts whitespace as content.
    """
    needle = q.strip()
    if not needle:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "search_query_required"},
        )
    pattern = f"%{needle}%"
    rows = conn.execute(
        "SELECT id, session_id, mic_id, started_at, ended_at, text, "
        "       confidence, language, triggered_intent "
        "FROM transcripts "
        "WHERE text IS NOT NULL AND LOWER(text) LIKE LOWER(?) "
        "ORDER BY ended_at DESC, id DESC "
        "LIMIT ?",
        (pattern, limit),
    ).fetchall()
    return TranscriptListResponse(items=[_row_to_model(r) for r in rows])


@router.delete("/{transcript_id}", response_model=DeleteOneResponse)
def delete_transcript(
    transcript_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_transcripts_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> DeleteOneResponse:
    """Delete one transcript row by id, or 404 if missing.

    Parent token only — the same scope guard as the rest of the
    parent-managed surfaces (children/toys/rooms). Single-row delete
    doesn't require a PIN re-confirm because the blast radius is one
    row; wipe-all is the high-stakes case that does.

    The ``404 transcript_not_found`` shape mirrors the sibling
    ``children``/``toys``/``rooms`` 404s so the frontend's typed-error
    helper can dispatch on ``code``.
    """
    # Single-statement atomic delete. ``rowcount == 0`` distinguishes
    # "row never existed" from a successful delete without a separate
    # SELECT round-trip (and without a tiny TOCTOU window between SELECT
    # and DELETE that an admin script racing on the same DB could land
    # in). ``with conn`` keeps the txn boundary so a crash mid-statement
    # leaves the row count consistent.
    with conn:
        cursor = conn.execute("DELETE FROM transcripts WHERE id = ?", (transcript_id,))
    if cursor.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "transcript_not_found", "id": transcript_id},
        )
    return DeleteOneResponse(ok=True)


# ``DELETE`` with a request body is unusual but FastAPI / starlette
# both accept it, and the alternative (PIN-in-header) is awkward to
# type-check on the frontend. The body carries the PIN re-confirm only;
# no other fields, so the wipe-all wire is unambiguously "operator
# typed their PIN to consent".
@router.delete("", response_model=WipeAllResponse)
def wipe_transcripts(
    body: WipeAllRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_transcripts_db)],
    auth_conn: Annotated[sqlite3.Connection, Depends(get_auth_db)],
    rate_limiter: Annotated[PinRateLimiter, Depends(get_pin_rate_limiter)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> WipeAllResponse:
    """Wipe every transcript row after PIN re-confirm.

    The PIN check is delegated to :func:`toybox.api.auth.enforce_pin_check`
    so this endpoint shares the global rate limiter with
    ``POST /api/auth/parent``. A burst of failed login attempts already
    near the lock threshold will lock the wipe surface as well — the
    spec invariant that "5 wrong attempts in 5 min across the PIN
    surface" cannot be sidestepped by switching endpoints.

    Failure modes (matching the auth spec):

    * 423 ``pin_locked`` (+ ``Retry-After``) — limiter engaged.
    * 401 ``pin_invalid`` (+ ``attempts_remaining``) — wrong PIN.
    * 412 ``pin_not_set`` — no stored hash. Defensive; the bind guard
      prevents normal boot from reaching here without setup, but a
      hand-edited DB could.
    * 422 — missing/malformed body (Pydantic, before this handler).

    On success the limiter does NOT record success — the wipe-all
    endpoint isn't an authentication event in the way that login is,
    so we leave the counter alone and let the next successful login
    reset it. The single ``DELETE FROM transcripts`` runs inside a
    transaction (``with conn:``) so a mid-transaction crash leaves
    the table consistent.
    """
    enforce_pin_check(body.pin, auth_conn, rate_limiter)
    # ``DELETE FROM transcripts`` returns the rowcount via cursor.
    # Wrapping in ``with conn`` makes the deletion atomic — the
    # pre-count + delete pair doesn't need atomicity since we only use
    # the rowcount for the response, but a partial delete would
    # otherwise leak rows on a crash mid-statement.
    with conn:
        cursor = conn.execute("DELETE FROM transcripts")
    deleted = cursor.rowcount if cursor.rowcount is not None else 0
    # ``rowcount`` returns -1 in some SQLite paths when undetermined;
    # clamp to 0 so the wire shape never carries a negative count.
    if deleted < 0:
        deleted = 0
    return WipeAllResponse(deleted=deleted)


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "DeleteOneResponse",
    "TranscriptListResponse",
    "TranscriptRow",
    "WipeAllRequest",
    "WipeAllResponse",
    "get_transcripts_db",
    "router",
]
