"""``songs_enabled`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings` — see that module's
docstring for the auth + wire-shape rationale.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import songs_enabled as core_songs_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["songs_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class SongsEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/songs-enabled``."""

    value: bool


class SongsEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/songs-enabled``."""

    value: bool


@router.get("/songs-enabled", response_model=SongsEnabledResponse)
def get_songs_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> SongsEnabledResponse:
    """Return the persisted ``songs_enabled`` flag."""
    value = core_songs_enabled.get(conn)
    return SongsEnabledResponse(value=value)


@router.put("/songs-enabled", response_model=SongsEnabledResponse)
def put_songs_enabled_endpoint(
    body: SongsEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> SongsEnabledResponse:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_songs_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_songs_enabled"},
        ) from exc
    return SongsEnabledResponse(value=new_value)


__all__ = [
    "SongsEnabledResponse",
    "SongsEnabledUpdate",
    "get_db",
    "router",
]
