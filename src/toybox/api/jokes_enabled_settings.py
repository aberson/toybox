"""``jokes_enabled`` setting HTTP API.

``GET /api/settings/jokes-enabled`` returns the persisted flag;
``PUT`` accepts ``{value: bool}`` and persists it.

The GET is unauthenticated (household read, mirrors Phase J's
play-queue settings GET); the PUT is parent-scope only — operator-
controlled household setting.

No WS broadcast: single-parent kiosk model. The next ``App.tsx`` mount
fetches the fresh value.

Shape follows the feature-flag settings convention — same prefix,
same ``get_db`` dependency layout, same manual
``ValueError → HTTPException(422)`` translation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import jokes_enabled as core_jokes_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["jokes_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class JokesEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/jokes-enabled``."""

    value: bool


class JokesEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/jokes-enabled``."""

    value: bool


@router.get("/jokes-enabled", response_model=JokesEnabledResponse)
def get_jokes_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> JokesEnabledResponse:
    """Return the persisted ``jokes_enabled`` flag."""
    value = core_jokes_enabled.get(conn)
    return JokesEnabledResponse(value=value)


@router.put("/jokes-enabled", response_model=JokesEnabledResponse)
def put_jokes_enabled_endpoint(
    body: JokesEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> JokesEnabledResponse:
    """Persist ``value`` and return the canonical bool.

    Translates :class:`ValueError` from the helper into HTTP 422.
    """
    try:
        new_value = core_jokes_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_jokes_enabled"},
        ) from exc
    return JokesEnabledResponse(value=new_value)


__all__ = [
    "JokesEnabledResponse",
    "JokesEnabledUpdate",
    "get_db",
    "router",
]
