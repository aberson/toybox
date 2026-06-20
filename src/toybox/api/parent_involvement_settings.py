"""Parent-involvement dial setting HTTP API.

``GET /api/settings/parent-involvement`` returns the persisted dial;
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
from ..core.parent_involvement import (
    PARENT_INVOLVEMENT_VALID,
    get_parent_involvement,
    set_parent_involvement,
)
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["parent_involvement_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class ParentInvolvementResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/parent-involvement``."""

    value: str


class ParentInvolvementUpdate(BaseModel):
    """Request body for ``PUT /api/settings/parent-involvement``."""

    value: str


@router.get("/parent-involvement", response_model=ParentInvolvementResponse)
def get_parent_involvement_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ParentInvolvementResponse:
    """Return the persisted parent-involvement dial."""
    value = get_parent_involvement(conn)
    return ParentInvolvementResponse(value=value)


@router.put("/parent-involvement", response_model=ParentInvolvementResponse)
def put_parent_involvement_endpoint(
    body: ParentInvolvementUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ParentInvolvementResponse:
    """Persist ``value`` and return the canonical string.

    Translates :class:`ValueError` from the helper into HTTP 422 with
    the full canonical set in the error body, so the frontend can
    surface "valid values are low, medium, high" without having to
    hard-code the list.
    """
    try:
        new_value = set_parent_involvement(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_parent_involvement",
                "valid": sorted(PARENT_INVOLVEMENT_VALID),
            },
        ) from exc
    return ParentInvolvementResponse(value=new_value)


__all__ = [
    "ParentInvolvementResponse",
    "ParentInvolvementUpdate",
    "get_db",
    "router",
]
