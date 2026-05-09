"""Image-gen mode toggle HTTP API.

``GET /api/settings/image-gen-mode`` returns the persisted mode;
``PUT`` accepts a body of ``{mode: "cartoon" | "composite"}``,
persists it, and emits an ``image_gen.mode`` ws envelope. The PUT is
parent-scope only — operator-controlled household setting, not a
session action a child could trigger.

Distinct from the per-toy regenerate endpoints in :mod:`toybox.api.toys`
— this only changes the dispatch branch the worker picks for the next
job. The env-disabled hard-off (``TOYBOX_IMAGE_GEN_ENABLED=false``)
still wins regardless of mode (see :mod:`toybox.image_gen.worker` for
the matrix).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.auth import TokenScope
from ..core.image_gen_mode import (
    ImageGenMode,
    Publisher,
    current_image_gen_mode,
    set_image_gen_mode,
)
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["image_gen_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def get_publisher() -> Publisher | None:
    """FastAPI dependency for the ws publisher.

    Returns ``None`` when no publisher is wired (the current production
    state — ``app.py`` does not override this); tests override with a
    list-collecting stub to assert against the emitted envelope. Sibling
    settings endpoints (``audio.py`` / ``listening.py``) ship the same
    placeholder shape; a follow-up step will wire all three to the live
    pubsub at once.
    """
    return None


class ImageGenModeResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/image-gen-mode``."""

    mode: Literal["cartoon", "composite"]


class ImageGenModeUpdate(BaseModel):
    """Request body for ``PUT /api/settings/image-gen-mode``."""

    mode: Literal["cartoon", "composite"]


@router.get("/image-gen-mode", response_model=ImageGenModeResponse)
def get_image_gen_mode_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ImageGenModeResponse:
    """Return the persisted image-gen mode."""
    mode: ImageGenMode = current_image_gen_mode(conn)
    return ImageGenModeResponse(mode=mode)


@router.put("/image-gen-mode", response_model=ImageGenModeResponse)
def put_image_gen_mode_endpoint(
    body: ImageGenModeUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
    publisher: Annotated[
        Publisher | None,
        Depends(get_publisher),
    ] = None,
) -> ImageGenModeResponse:
    """Persist ``mode`` and emit the ``image_gen.mode`` envelope."""
    new_value = set_image_gen_mode(conn, body.mode, publisher=publisher)
    return ImageGenModeResponse(mode=new_value)


__all__ = [
    "ImageGenModeResponse",
    "ImageGenModeUpdate",
    "get_db",
    "get_publisher",
    "router",
]
