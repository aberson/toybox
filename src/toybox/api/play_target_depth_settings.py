"""Play-target-depth setting HTTP API.

``GET /api/settings/play-target-depth`` returns the persisted play
target depth; ``PUT`` accepts a body of ``{value: int}`` where ``int``
must be one of ``{1, 3, 5}``, persists it, and returns the canonical
value.

The GET is unauthenticated (household read, mirrors
``GET /api/settings/image-gen-mode`` and Phase I's transcript-retention
GET); the PUT is parent-scope only — operator-controlled household
setting, not a session action a child could trigger.

No WS broadcast: single-parent kiosk model. The next ``App.tsx`` mount
fetches the fresh value. A second parent tab open on the same kiosk
would see a stale value until reload — acceptable for v1.

Shape mirrors :mod:`toybox.api.transcript_retention_settings` exactly:
same prefix (``/api/settings``), same ``get_db`` dependency layout,
same manual ``ValueError → HTTPException(422)`` translation with the
full canonical set in the error body so the frontend doesn't have to
hard-code the preset list.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import play_target_depth as core_play_target_depth
from ..core.auth import TokenScope
from ..core.play_target_depth import PLAY_TARGET_DEPTH_VALID
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["play_target_depth_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class PlayTargetDepthResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/play-target-depth``."""

    value: int


class PlayTargetDepthUpdate(BaseModel):
    """Request body for ``PUT /api/settings/play-target-depth``."""

    value: int


@router.get("/play-target-depth", response_model=PlayTargetDepthResponse)
def get_play_target_depth_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PlayTargetDepthResponse:
    """Return the persisted play target depth."""
    value = core_play_target_depth.get(conn)
    return PlayTargetDepthResponse(value=value)


@router.put("/play-target-depth", response_model=PlayTargetDepthResponse)
def put_play_target_depth_endpoint(
    body: PlayTargetDepthUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> PlayTargetDepthResponse:
    """Persist ``value`` and return the canonical int.

    Translates :class:`ValueError` from the helper into HTTP 422 with
    the full canonical set in the error body, so the frontend can
    surface "valid values are 1, 3, 5" without having to hard-code the
    list.
    """
    try:
        new_value = core_play_target_depth.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_play_target_depth",
                "valid": sorted(PLAY_TARGET_DEPTH_VALID),
            },
        ) from exc
    return PlayTargetDepthResponse(value=new_value)


__all__ = [
    "PlayTargetDepthResponse",
    "PlayTargetDepthUpdate",
    "get_db",
    "router",
]
