"""``play_endings_enabled`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import play_endings_enabled as core_play_endings_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["play_endings_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class PlayEndingsEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/play-endings-enabled``."""

    value: bool


class PlayEndingsEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/play-endings-enabled``."""

    value: bool


@router.get("/play-endings-enabled", response_model=PlayEndingsEnabledResponse)
def get_play_endings_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PlayEndingsEnabledResponse:
    """Return the persisted ``play_endings_enabled`` flag."""
    value = core_play_endings_enabled.get(conn)
    return PlayEndingsEnabledResponse(value=value)


@router.put("/play-endings-enabled", response_model=PlayEndingsEnabledResponse)
def put_play_endings_enabled_endpoint(
    body: PlayEndingsEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> PlayEndingsEnabledResponse:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_play_endings_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_play_endings_enabled"},
        ) from exc
    return PlayEndingsEnabledResponse(value=new_value)


__all__ = [
    "PlayEndingsEnabledResponse",
    "PlayEndingsEnabledUpdate",
    "get_db",
    "router",
]
