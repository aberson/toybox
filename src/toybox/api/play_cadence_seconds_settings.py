"""Play-cadence-seconds setting HTTP API.

``GET /api/settings/play-cadence-seconds`` returns the persisted play
cadence in seconds; ``PUT`` accepts a body of ``{value: int}`` where
``int`` must be one of ``{0, 10, 30, 60}``, persists it, and returns
the canonical value.

**``0`` is a valid in-set value meaning "cadence disabled" — NOT a
sentinel for unset.** The Pydantic model accepts ``value: int`` as-is
and forwards to the helper, which validates set-membership via explicit
``value in PLAY_CADENCE_SECONDS_VALID``. Truthiness shortcuts on the
wire path would silently coerce a legitimate "disabled" choice back to
the default.

The GET is unauthenticated (household read, mirrors
``GET /api/settings/image-gen-mode`` and Phase I's transcript-retention
GET); the PUT is parent-scope only.

No WS broadcast: single-parent kiosk model. The next ``App.tsx`` mount
fetches the fresh value.

Shape mirrors :mod:`toybox.api.transcript_retention_settings` exactly:
same prefix (``/api/settings``), same ``get_db`` dependency layout,
same manual ``ValueError → HTTPException(422)`` translation with the
full canonical set in the error body.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import play_cadence_seconds as core_play_cadence_seconds
from ..core.auth import TokenScope
from ..core.play_cadence_seconds import PLAY_CADENCE_SECONDS_VALID
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["play_cadence_seconds_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class PlayCadenceSecondsResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/play-cadence-seconds``."""

    value: int


class PlayCadenceSecondsUpdate(BaseModel):
    """Request body for ``PUT /api/settings/play-cadence-seconds``."""

    value: int


@router.get("/play-cadence-seconds", response_model=PlayCadenceSecondsResponse)
def get_play_cadence_seconds_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PlayCadenceSecondsResponse:
    """Return the persisted play cadence in seconds."""
    value = core_play_cadence_seconds.get(conn)
    return PlayCadenceSecondsResponse(value=value)


@router.put("/play-cadence-seconds", response_model=PlayCadenceSecondsResponse)
def put_play_cadence_seconds_endpoint(
    body: PlayCadenceSecondsUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> PlayCadenceSecondsResponse:
    """Persist ``value`` and return the canonical int.

    Translates :class:`ValueError` from the helper into HTTP 422 with
    the full canonical set in the error body, so the frontend can
    surface "valid values are 0, 10, 30, 60" without having to hard-code
    the list. ``0`` is a valid in-set value — do NOT shortcut on
    falsiness anywhere on this path.
    """
    try:
        new_value = core_play_cadence_seconds.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_play_cadence_seconds",
                "valid": sorted(PLAY_CADENCE_SECONDS_VALID),
            },
        ) from exc
    return PlayCadenceSecondsResponse(value=new_value)


__all__ = [
    "PlayCadenceSecondsResponse",
    "PlayCadenceSecondsUpdate",
    "get_db",
    "router",
]
