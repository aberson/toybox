"""Listening-mode HTTP API.

``GET /api/listening/mode`` returns the persisted mode; ``PUT`` accepts
a body of ``{mode: 1-5}``, persists it, and (when wired) emits the
``listening.mode`` ws envelope. FastAPI handles the 422 for out-of-range
input via :class:`pydantic.Field` constraints.

The ``get_publisher`` dependency returns ``None`` for Step 4 — the
broadcast hub lands in Step 8. Step 8 will swap this dependency to
return the live hub's ``publish`` method without changing the route.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.listening import Publisher, current_mode, set_mode
from ..db import connect, resolve_db_path

router = APIRouter(prefix="/api/listening", tags=["listening"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close.

    ``check_same_thread=False`` because FastAPI dispatches sync
    generator setup, the handler body, and teardown via
    ``run_in_threadpool``; anyio may pick a different worker for each
    leg, which would otherwise trip
    ``sqlite3.ProgrammingError`` in ``conn.close()``.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def get_publisher() -> Publisher | None:
    """FastAPI dependency for the ws publisher.

    Returns ``None`` for Step 4 — the broadcast hub lands in Step 8.
    Tests override this dependency with a list-collecting stub when they
    need to assert against the emitted envelope.
    """
    return None


class ModeResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/listening/mode``."""

    mode: int = Field(ge=1, le=5)


class ModeUpdate(BaseModel):
    """Request body for ``PUT /api/listening/mode``."""

    mode: int = Field(ge=1, le=5)


@router.get("/mode", response_model=ModeResponse)
def get_mode(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ModeResponse:
    """Return the persisted listening mode."""
    return ModeResponse(mode=int(current_mode(conn)))


@router.put("/mode", response_model=ModeResponse)
def put_mode(
    body: ModeUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    publisher: Annotated[Publisher | None, Depends(get_publisher)] = None,
) -> ModeResponse:
    """Persist a new mode and emit the ``listening.mode`` envelope."""
    new_mode = set_mode(conn, body.mode, publisher=publisher)
    return ModeResponse(mode=int(new_mode))


__all__ = [
    "ModeResponse",
    "ModeUpdate",
    "get_db",
    "get_publisher",
    "router",
]
