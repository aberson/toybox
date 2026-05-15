"""``play_embedded_enabled`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import play_embedded_enabled as core_play_embedded_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["play_embedded_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class PlayEmbeddedEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/play-embedded-enabled``."""

    value: bool


class PlayEmbeddedEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/play-embedded-enabled``."""

    value: bool


@router.get("/play-embedded-enabled", response_model=PlayEmbeddedEnabledResponse)
def get_play_embedded_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PlayEmbeddedEnabledResponse:
    """Return the persisted ``play_embedded_enabled`` flag."""
    value = core_play_embedded_enabled.get(conn)
    return PlayEmbeddedEnabledResponse(value=value)


@router.put("/play-embedded-enabled", response_model=PlayEmbeddedEnabledResponse)
def put_play_embedded_enabled_endpoint(
    body: PlayEmbeddedEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> PlayEmbeddedEnabledResponse:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_play_embedded_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_play_embedded_enabled"},
        ) from exc
    return PlayEmbeddedEnabledResponse(value=new_value)


__all__ = [
    "PlayEmbeddedEnabledResponse",
    "PlayEmbeddedEnabledUpdate",
    "get_db",
    "router",
]
