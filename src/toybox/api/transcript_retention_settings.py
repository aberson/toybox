"""Transcript-retention setting HTTP API.

``GET /api/settings/transcript-retention`` returns the persisted
retention window in seconds; ``PUT`` accepts a body of ``{seconds: int}``
where ``int`` must be one of ``{60, 180, 300, 600, 900}``, persists it,
and returns the canonical value.

The GET is unauthenticated (household read, mirrors
``GET /api/settings/image-gen-mode``); the PUT is parent-scope only —
operator-controlled household setting, not a session action a child
could trigger.

No WS broadcast: single-parent kiosk model. The next ``App.tsx`` mount
fetches the fresh value. A second parent tab open on the same kiosk
would see a stale value until reload — acceptable for v1.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.auth import TokenScope
from ..core.transcript_retention import (
    RETENTION_SECONDS_VALID,
    current_retention_seconds,
    set_retention_seconds,
)
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["transcript_retention_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class TranscriptRetentionResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/transcript-retention``."""

    seconds: int


class TranscriptRetentionUpdate(BaseModel):
    """Request body for ``PUT /api/settings/transcript-retention``."""

    seconds: int


@router.get("/transcript-retention", response_model=TranscriptRetentionResponse)
def get_transcript_retention_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> TranscriptRetentionResponse:
    """Return the persisted retention window in seconds."""
    seconds = current_retention_seconds(conn)
    return TranscriptRetentionResponse(seconds=seconds)


@router.put("/transcript-retention", response_model=TranscriptRetentionResponse)
def put_transcript_retention_endpoint(
    body: TranscriptRetentionUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> TranscriptRetentionResponse:
    """Persist ``seconds`` and return the canonical int.

    Translates :class:`ValueError` from the helper into HTTP 422 with
    the full canonical set in the error body, so the frontend can
    surface "valid values are 60, 180, 300, 600, 900" without having to
    hard-code the list.
    """
    try:
        new_value = set_retention_seconds(conn, body.seconds)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_retention",
                "valid": sorted(RETENTION_SECONDS_VALID),
            },
        ) from exc
    return TranscriptRetentionResponse(seconds=new_value)


__all__ = [
    "TranscriptRetentionResponse",
    "TranscriptRetentionUpdate",
    "get_db",
    "router",
]
