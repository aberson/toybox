"""``boss_fights_enabled`` setting HTTP API.

``GET /api/settings/boss-fights-enabled`` returns the persisted flag;
``PUT`` accepts ``{value: bool}`` and persists it.

The GET is unauthenticated (household read, mirrors Phase J's
play-queue settings GET); the PUT is parent-scope only — operator-
controlled household setting.

No WS broadcast: single-parent kiosk model. The next ``App.tsx`` mount
fetches the fresh value.

Shape mirrors :mod:`toybox.api.jokes_enabled_settings` — same prefix,
same ``get_db`` dependency layout, same manual
``ValueError → HTTPException(422)`` translation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import boss_fights_enabled as core_boss_fights_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["boss_fights_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class BossFightsEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/boss-fights-enabled``."""

    value: bool


class BossFightsEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/boss-fights-enabled``."""

    value: bool


@router.get("/boss-fights-enabled", response_model=BossFightsEnabledResponse)
def get_boss_fights_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> BossFightsEnabledResponse:
    """Return the persisted ``boss_fights_enabled`` flag."""
    value = core_boss_fights_enabled.get(conn)
    return BossFightsEnabledResponse(value=value)


@router.put("/boss-fights-enabled", response_model=BossFightsEnabledResponse)
def put_boss_fights_enabled_endpoint(
    body: BossFightsEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> BossFightsEnabledResponse:
    """Persist ``value`` and return the canonical bool.

    Translates :class:`ValueError` from the helper into HTTP 422.
    """
    try:
        new_value = core_boss_fights_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_boss_fights_enabled"},
        ) from exc
    return BossFightsEnabledResponse(value=new_value)


__all__ = [
    "BossFightsEnabledResponse",
    "BossFightsEnabledUpdate",
    "get_db",
    "router",
]
