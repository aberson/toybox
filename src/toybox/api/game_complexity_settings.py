"""Game-complexity dial setting HTTP API.

``GET /api/settings/game-complexity`` returns the persisted dial;
``PUT`` accepts a body of ``{value: str}`` where ``str`` must be one of
``{"low", "medium", "high"}``, persists it, and returns the canonical
value.

The GET is unauthenticated (household read, mirrors
``GET /api/settings/spoken-text-limit``); the PUT is parent-scope only —
operator-controlled household setting, not a session action a child
could trigger.

Phase W Step W1 true-stub: PERSIST ONLY, wired to no behavior yet.

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
from ..core.game_complexity import (
    GAME_COMPLEXITY_VALID,
    get_game_complexity,
    set_game_complexity,
)
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["game_complexity_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class GameComplexityResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/game-complexity``."""

    value: str


class GameComplexityUpdate(BaseModel):
    """Request body for ``PUT /api/settings/game-complexity``."""

    value: str


@router.get("/game-complexity", response_model=GameComplexityResponse)
def get_game_complexity_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> GameComplexityResponse:
    """Return the persisted game-complexity dial."""
    value = get_game_complexity(conn)
    return GameComplexityResponse(value=value)


@router.put("/game-complexity", response_model=GameComplexityResponse)
def put_game_complexity_endpoint(
    body: GameComplexityUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> GameComplexityResponse:
    """Persist ``value`` and return the canonical string.

    Translates :class:`ValueError` from the helper into HTTP 422 with
    the full canonical set in the error body, so the frontend can
    surface "valid values are low, medium, high" without having to
    hard-code the list.
    """
    try:
        new_value = set_game_complexity(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_game_complexity",
                "valid": sorted(GAME_COMPLEXITY_VALID),
            },
        ) from exc
    return GameComplexityResponse(value=new_value)


__all__ = [
    "GameComplexityResponse",
    "GameComplexityUpdate",
    "get_db",
    "router",
]
