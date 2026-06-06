"""Spoken-text-limit setting HTTP API.

``GET /api/settings/spoken-text-limit`` returns the persisted character
limit; ``PUT`` accepts a body of ``{value: int}`` where ``int`` must be
one of ``{0, 50, 100, 150, 250}``, persists it, and returns the
canonical value.

The GET is unauthenticated (household read, mirrors
``GET /api/settings/transcript-retention``); the PUT is parent-scope
only — operator-controlled household setting, not a session action a
child could trigger.

No WS broadcast: single-parent kiosk model. The next ``App.tsx`` mount
fetches the fresh value.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.auth import TokenScope
from ..core.spoken_text_limit import (
    SPOKEN_TEXT_LIMIT_VALID,
    get_spoken_text_limit,
    set_spoken_text_limit,
)
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["spoken_text_limit_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class SpokenTextLimitResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/spoken-text-limit``."""

    value: int


class SpokenTextLimitUpdate(BaseModel):
    """Request body for ``PUT /api/settings/spoken-text-limit``."""

    value: int


@router.get("/spoken-text-limit", response_model=SpokenTextLimitResponse)
def get_spoken_text_limit_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> SpokenTextLimitResponse:
    """Return the persisted spoken text character limit."""
    value = get_spoken_text_limit(conn)
    return SpokenTextLimitResponse(value=value)


@router.put("/spoken-text-limit", response_model=SpokenTextLimitResponse)
def put_spoken_text_limit_endpoint(
    body: SpokenTextLimitUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> SpokenTextLimitResponse:
    """Persist ``value`` and return the canonical int.

    Translates :class:`ValueError` from the helper into HTTP 422 with
    the full canonical set in the error body, so the frontend can
    surface "valid values are 0, 50, 100, 150, 250" without having to
    hard-code the list.
    """
    try:
        new_value = set_spoken_text_limit(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_spoken_text_limit",
                "valid": sorted(SPOKEN_TEXT_LIMIT_VALID),
            },
        ) from exc
    return SpokenTextLimitResponse(value=new_value)


__all__ = [
    "SpokenTextLimitResponse",
    "SpokenTextLimitUpdate",
    "get_db",
    "router",
]
