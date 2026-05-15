"""``play_spontaneity_enabled`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings`. The default at the
core layer is ``False`` (the only Phase K flag that defaults off);
the wire shape itself is identical.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import play_spontaneity_enabled as core_play_spontaneity_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["play_spontaneity_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class PlaySpontaneityEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/play-spontaneity-enabled``."""

    value: bool


class PlaySpontaneityEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/play-spontaneity-enabled``."""

    value: bool


@router.get(
    "/play-spontaneity-enabled",
    response_model=PlaySpontaneityEnabledResponse,
)
def get_play_spontaneity_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PlaySpontaneityEnabledResponse:
    """Return the persisted ``play_spontaneity_enabled`` flag."""
    value = core_play_spontaneity_enabled.get(conn)
    return PlaySpontaneityEnabledResponse(value=value)


@router.put(
    "/play-spontaneity-enabled",
    response_model=PlaySpontaneityEnabledResponse,
)
def put_play_spontaneity_enabled_endpoint(
    body: PlaySpontaneityEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> PlaySpontaneityEnabledResponse:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_play_spontaneity_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_play_spontaneity_enabled"},
        ) from exc
    return PlaySpontaneityEnabledResponse(value=new_value)


__all__ = [
    "PlaySpontaneityEnabledResponse",
    "PlaySpontaneityEnabledUpdate",
    "get_db",
    "router",
]
