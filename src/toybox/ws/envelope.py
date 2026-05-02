"""Typed WebSocket envelope.

Every ws message shares this shape so Step 8's broadcast hub and the
frontend client can both parse without per-topic dispatch logic. The
``schema_version`` field is reserved for future migrations; v1 events
all carry ``schema_version=1``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .topics import Topic


class Envelope(BaseModel):
    """Wire shape for every WebSocket event."""

    model_config = ConfigDict(frozen=True)

    topic: Topic
    ts: datetime
    payload: dict[str, Any]
    schema_version: int = Field(default=1, ge=1)


def build_envelope(
    topic: Topic,
    payload: dict[str, Any],
    ts: datetime | None = None,
) -> Envelope:
    """Construct an :class:`Envelope` with a UTC timestamp default."""
    return Envelope(
        topic=topic,
        ts=ts if ts is not None else datetime.now(UTC),
        payload=payload,
    )


__all__ = ["Envelope", "build_envelope"]
