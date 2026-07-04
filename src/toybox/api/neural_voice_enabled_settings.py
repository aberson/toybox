"""``neural_voice_enabled`` setting HTTP API.

Mirrors :mod:`toybox.api.jokes_enabled_settings`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import neural_voice_enabled as core_neural_voice_enabled
from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["neural_voice_enabled_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class NeuralVoiceEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/neural-voice-enabled``."""

    value: bool


class NeuralVoiceEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/settings/neural-voice-enabled``."""

    value: bool


@router.get("/neural-voice-enabled", response_model=NeuralVoiceEnabledResponse)
def get_neural_voice_enabled_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> NeuralVoiceEnabledResponse:
    """Return the persisted ``neural_voice_enabled`` flag."""
    value = core_neural_voice_enabled.get(conn)
    return NeuralVoiceEnabledResponse(value=value)


@router.put("/neural-voice-enabled", response_model=NeuralVoiceEnabledResponse)
def put_neural_voice_enabled_endpoint(
    body: NeuralVoiceEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> NeuralVoiceEnabledResponse:
    """Persist ``value`` and return the canonical bool."""
    try:
        new_value = core_neural_voice_enabled.set(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_neural_voice_enabled"},
        ) from exc
    return NeuralVoiceEnabledResponse(value=new_value)


__all__ = [
    "NeuralVoiceEnabledResponse",
    "NeuralVoiceEnabledUpdate",
    "get_db",
    "router",
]
