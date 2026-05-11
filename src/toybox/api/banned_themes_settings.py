"""Household-global banned-themes HTTP API.

``GET /api/settings/banned-themes`` returns the persisted value or
``null``. ``PUT`` accepts ``{themes: string | null}``, persists it via
:func:`toybox.core.banned_themes.set_banned_themes_global` (which
deletes the row on null / empty-after-strip), and returns the persisted
value. The PUT is parent-scope only.

Mirrors :mod:`toybox.api.image_gen_settings` minus the WebSocket
publisher: the escalation pipeline reads the value per-request, not
per-job, so no broadcast is required. See
``documentation/plan/phase-h-parent-ux-revamp.md`` (decision log) for
the rationale.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.auth import TokenScope
from ..core.banned_themes import current_banned_themes_global, set_banned_themes_global
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["banned_themes_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class BannedThemesResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/banned-themes``."""

    themes: str | None


class BannedThemesUpdate(BaseModel):
    """Request body for ``PUT /api/settings/banned-themes``."""

    themes: str | None


@router.get("/banned-themes", response_model=BannedThemesResponse)
def get_banned_themes_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> BannedThemesResponse:
    """Return the household global banned-themes string, or ``null``."""
    return BannedThemesResponse(themes=current_banned_themes_global(conn))


@router.put("/banned-themes", response_model=BannedThemesResponse)
def put_banned_themes_endpoint(
    body: BannedThemesUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> BannedThemesResponse:
    """Persist ``themes`` and return the round-tripped value.

    ``null`` or an empty/whitespace string clears the row; subsequent
    GET returns ``null``. The handler reads the persisted value back
    (rather than echoing the request body) so the operator sees the
    actual stored state — useful when an empty-after-strip input
    became a row deletion.
    """
    set_banned_themes_global(conn, body.themes)
    return BannedThemesResponse(themes=current_banned_themes_global(conn))


__all__ = [
    "BannedThemesResponse",
    "BannedThemesUpdate",
    "get_db",
    "router",
]
