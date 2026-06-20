"""Game-linearity dial setting HTTP API.

``GET /api/settings/game-linearity`` returns the persisted dial;
``PUT`` accepts a body of ``{value: str}`` where ``str`` must be one of
``{"linear", "nonlinear"}``, persists it, and returns the canonical
value.

The GET is unauthenticated (household read, mirrors
``GET /api/settings/spoken-text-limit``); the PUT is parent-scope only —
operator-controlled household setting, not a session action a child
could trigger.

Phase W Step W2: WIRED — the propose path reads this dial and passes
``linear_only=(value == "linear")`` into the offline generator so a
``linear`` household never sees a branching (choice-bearing) activity.

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
from ..core.game_linearity import (
    GAME_LINEARITY_VALID,
    get_game_linearity,
    set_game_linearity,
)
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["game_linearity_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class GameLinearityResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/game-linearity``."""

    value: str


class GameLinearityUpdate(BaseModel):
    """Request body for ``PUT /api/settings/game-linearity``."""

    value: str


@router.get("/game-linearity", response_model=GameLinearityResponse)
def get_game_linearity_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> GameLinearityResponse:
    """Return the persisted game-linearity dial."""
    value = get_game_linearity(conn)
    return GameLinearityResponse(value=value)


@router.put("/game-linearity", response_model=GameLinearityResponse)
def put_game_linearity_endpoint(
    body: GameLinearityUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> GameLinearityResponse:
    """Persist ``value`` and return the canonical string.

    Translates :class:`ValueError` from the helper into HTTP 422 with
    the full canonical set in the error body, so the frontend can
    surface "valid values are linear, nonlinear" without having to
    hard-code the list.
    """
    try:
        new_value = set_game_linearity(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_game_linearity",
                "valid": sorted(GAME_LINEARITY_VALID),
            },
        ) from exc
    return GameLinearityResponse(value=new_value)


__all__ = [
    "GameLinearityResponse",
    "GameLinearityUpdate",
    "get_db",
    "router",
]
