"""Audio control HTTP API.

``GET /api/audio/mic-enabled`` returns the persisted mute state;
``PUT`` accepts a body of ``{enabled: bool}``, persists it, and emits a
``mic.enabled`` ws envelope.

Distinct from the listening-mode endpoint: ``mic_enabled=false`` mutes
transcript persistence + ws emit at the pipeline level. Listening mode
is unaffected by mute and continues to gate AI escalation independently.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.mic_state import Publisher, current_mic_enabled, set_mic_enabled
from ..db import connect, resolve_db_path

router = APIRouter(prefix="/api/audio", tags=["audio"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def get_publisher() -> Publisher | None:
    """FastAPI dependency for the ws publisher.

    Returns ``None`` by default; tests override with a list-collecting
    stub. Production wires this to the live pubsub in ``app.py``.
    """
    return None


class MicEnabledResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/audio/mic-enabled``."""

    enabled: bool


class MicEnabledUpdate(BaseModel):
    """Request body for ``PUT /api/audio/mic-enabled``."""

    enabled: bool


@router.get("/mic-enabled", response_model=MicEnabledResponse)
def get_mic_enabled(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> MicEnabledResponse:
    """Return the persisted mic-enabled flag."""
    return MicEnabledResponse(enabled=current_mic_enabled(conn))


@router.put("/mic-enabled", response_model=MicEnabledResponse)
def put_mic_enabled(
    body: MicEnabledUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    publisher: Annotated[Publisher | None, Depends(get_publisher)] = None,
) -> MicEnabledResponse:
    """Persist ``enabled`` and emit the ``mic.enabled`` envelope."""
    new_value = set_mic_enabled(conn, body.enabled, publisher=publisher)
    return MicEnabledResponse(enabled=new_value)


__all__ = [
    "MicEnabledResponse",
    "MicEnabledUpdate",
    "get_db",
    "get_publisher",
    "router",
]
