"""Health endpoint.

Phase A contract: ``GET /api/health`` returns ``{ok, capability_reason}``.
``ok`` is always ``True`` for now; ``capability_reason`` is wired through
the capability composition module so Step 4 can flip it on without
changing the route signature.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.capability import current_capability_reason

router = APIRouter(prefix="/api", tags=["health"])


class HealthResponse(BaseModel):
    """Wire shape for ``GET /api/health``."""

    ok: bool
    capability_reason: str | None


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Return liveness + capability state."""
    return HealthResponse(ok=True, capability_reason=current_capability_reason())


__all__ = ["router", "HealthResponse"]
