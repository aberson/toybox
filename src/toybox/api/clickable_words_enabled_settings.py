"""``clickable_words_enabled`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import clickable_words_enabled as core_clickable_words_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["clickable_words_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class ClickableWordsEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/clickable-words-enabled``."""

    value: bool


class ClickableWordsEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/clickable-words-enabled``."""

    value: bool


@router.get("/clickable-words-enabled", response_model=ClickableWordsEnabledResponse)
def get_clickable_words_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ClickableWordsEnabledResponse:
    """Return the persisted ``clickable_words_enabled`` flag."""
    value = core_clickable_words_enabled.get(conn)
    return ClickableWordsEnabledResponse(value=value)


@router.put("/clickable-words-enabled", response_model=ClickableWordsEnabledResponse)
def put_clickable_words_enabled_endpoint(
    body: ClickableWordsEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ClickableWordsEnabledResponse:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_clickable_words_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_clickable_words_enabled"},
        ) from exc
    return ClickableWordsEnabledResponse(value=new_value)


__all__ = [
    "ClickableWordsEnabledResponse",
    "ClickableWordsEnabledUpdate",
    "get_db",
    "router",
]
