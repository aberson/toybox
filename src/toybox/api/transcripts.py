"""Transcripts read-only REST API.

Step 13 surface:

* ``GET /api/transcripts?limit=50&before=<iso>`` — paginated list
  ordered by ``ended_at DESC`` (most recent first). The ``before``
  cursor is an ISO timestamp; rows with ``ended_at < before`` are
  returned. Pagination is cursor-based so a parent UI scrolling the
  audit log doesn't drift across inserts.
* ``GET /api/transcripts/search?q=<substring>&limit=50`` —
  case-insensitive substring search over ``text``. ``q`` is required
  and must be non-empty (HTTP 400 otherwise).

Read-only by design: Step 21 (Phase D) ships the wipe / delete surface.
The routes follow the rest of the v1 LAN-only API: no auth (matches
``/api/listening`` and ``/api/health``); a future hardening pass can
gate them through :class:`toybox.api.auth_dep.RequireScope` without
changing the wire shape.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..db import connect, resolve_db_path

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


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "TranscriptListResponse",
    "TranscriptRow",
    "get_transcripts_db",
    "router",
]
