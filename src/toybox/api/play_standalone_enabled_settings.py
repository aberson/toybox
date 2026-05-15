"""``play_standalone_enabled`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import play_standalone_enabled as core_play_standalone_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["play_standalone_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class PlayStandaloneEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/play-standalone-enabled``."""

    value: bool


class PlayStandaloneEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/play-standalone-enabled``."""

    value: bool


@router.get("/play-standalone-enabled", response_model=PlayStandaloneEnabledResponse)
def get_play_standalone_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PlayStandaloneEnabledResponse:
    """Return the persisted ``play_standalone_enabled`` flag."""
    value = core_play_standalone_enabled.get(conn)
    return PlayStandaloneEnabledResponse(value=value)


@router.put("/play-standalone-enabled", response_model=PlayStandaloneEnabledResponse)
def put_play_standalone_enabled_endpoint(
    body: PlayStandaloneEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> PlayStandaloneEnabledResponse:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_play_standalone_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_play_standalone_enabled"},
        ) from exc
    return PlayStandaloneEnabledResponse(value=new_value)


__all__ = [
    "PlayStandaloneEnabledResponse",
    "PlayStandaloneEnabledUpdate",
    "get_db",
    "router",
]
