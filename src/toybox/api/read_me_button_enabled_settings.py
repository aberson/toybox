"""``read_me_button_enabled`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import read_me_button_enabled as core_read_me_button_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["read_me_button_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class ReadMeButtonEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/read-me-button-enabled``."""

    value: bool


class ReadMeButtonEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/read-me-button-enabled``."""

    value: bool


@router.get("/read-me-button-enabled", response_model=ReadMeButtonEnabledResponse)
def get_read_me_button_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ReadMeButtonEnabledResponse:
    """Return the persisted ``read_me_button_enabled`` flag."""
    value = core_read_me_button_enabled.get(conn)
    return ReadMeButtonEnabledResponse(value=value)


@router.put("/read-me-button-enabled", response_model=ReadMeButtonEnabledResponse)
def put_read_me_button_enabled_endpoint(
    body: ReadMeButtonEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ReadMeButtonEnabledResponse:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_read_me_button_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_read_me_button_enabled"},
        ) from exc
    return ReadMeButtonEnabledResponse(value=new_value)


__all__ = [
    "ReadMeButtonEnabledResponse",
    "ReadMeButtonEnabledUpdate",
    "get_db",
    "router",
]
