"""Operator metrics REST API.

Single ``GET /api/metrics`` endpoint. Returns the same
:class:`~toybox.metrics.MetricsSnapshot` shape the ``metrics`` ws topic
publishes — the parent UI's Operator tab can poll this when its ws
connection is unavailable, or use it as the first-render value before
the ws snapshot arrives.

Auth: parent-scope token only. The ``RequireScope`` dep mirrors the
contract in :mod:`toybox.api.children` / :mod:`toybox.api.activities`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..ai.breaker import CircuitBreaker
from ..core.auth import TokenScope
from ..core.pubsub import PubSub
from ..db import connect, resolve_db_path
from ..metrics import (
    SnapshotInputs,
    get_metrics_snapshot,
    resolve_capability,
)
from ..ws.server import get_pubsub
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


# ---------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------
#
# Mirrors the dataclass shape in :mod:`toybox.metrics`. Defined here so
# the endpoint can declare ``response_model=`` and FastAPI generates the
# OpenAPI schema for it; the dataclass is the source of truth for the
# runtime values, and ``MetricsSnapshot.to_dict()`` produces a payload
# that satisfies these models 1:1.


class _MetricsActivityCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposed_current: int
    approved_current: int
    running_current: int
    completed_current: int
    ended_current: int
    dismissed_current: int
    didnt_work_current: int
    last_24h: dict[str, int]


class _MetricsTranscriptCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int
    last_24h: int


class _MetricsAudioStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    mic_device: str | None
    queue_depth: int
    buffer_overruns_total: int
    mic_enabled: bool


class _MetricsAIStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    breaker_state: str
    breaker_retry_after_iso: str | None
    claude_capable: bool
    claude_capability_reason: str | None
    listening_mode: int
    min_interval_throttle_seconds: float


class _MetricsJudgeParentAgreement(BaseModel):
    model_config = ConfigDict(frozen=True)

    overlap_count: int
    agreement_rate: float | None
    metric_name: str


class _MetricsActivityQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    last_24h_mean_scores: dict[str, float | None]
    judge_parent_agreement: _MetricsJudgeParentAgreement
    safety_autofails_last_24h: int


class _MetricsEvalGateStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    last_run_at: str | None
    mean_dimension_scores: dict[str, float] | None
    regressions_detected: int
    placeholder_baseline: bool


class MetricsSnapshotResponse(BaseModel):
    """Wire shape for ``GET /api/metrics``.

    Mirrors :class:`toybox.metrics.MetricsSnapshot` field-for-field. The
    same shape is published as the payload of ``metrics`` ws envelopes,
    so the parent UI uses one wire definition for both transports.
    """

    model_config = ConfigDict(frozen=True)

    generated_at: str
    ws_subscribers: int
    activities: _MetricsActivityCounts
    transcripts: _MetricsTranscriptCounts
    audio: _MetricsAudioStatus
    ai: _MetricsAIStatus
    activity_quality: _MetricsActivityQuality
    eval_gate: _MetricsEvalGateStatus


# Process-singleton breaker for the metrics endpoint. The runtime gate
# in :mod:`toybox.ai.capability` reads its own breaker (also a process
# singleton in production); for v1 we keep the metrics breaker isolated
# so the endpoint always reflects the *metrics view's* breaker history,
# not a transient state held by an in-flight Claude call. A future
# refactor may wire a single shared breaker; until then this is the
# documented invariant.
_METRICS_BREAKER: CircuitBreaker | None = None


def _process_breaker() -> CircuitBreaker:
    """Lazy-init the metrics-view breaker singleton."""
    global _METRICS_BREAKER
    if _METRICS_BREAKER is None:
        _METRICS_BREAKER = CircuitBreaker()
    return _METRICS_BREAKER


def get_metrics_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield a metrics-scoped SQLite connection.

    ``check_same_thread=False`` matches the other API deps — FastAPI's
    threadpool may schedule generator setup, the handler body, and
    teardown on different worker threads.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def get_metrics_breaker() -> CircuitBreaker:
    """FastAPI dependency: the metrics-view breaker singleton.

    Tests override this to inject a fresh breaker per case so a
    parametrize-over-state run can pin closed/open/half_open without
    leaking across tests.
    """
    return _process_breaker()


@router.get("", response_model=MetricsSnapshotResponse)
async def get_metrics(
    conn: Annotated[sqlite3.Connection, Depends(get_metrics_db)],
    breaker: Annotated[CircuitBreaker, Depends(get_metrics_breaker)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> MetricsSnapshotResponse:
    """Return a fresh :class:`MetricsSnapshot` as JSON.

    Async handler so the capability probe (network reachability check)
    can run without stalling the asyncio loop. The DB queries inside
    :func:`get_metrics_snapshot` are sync but tiny; running them on the
    asyncio loop is the same trade-off the listening-mode endpoint makes.
    """
    capability_result = await resolve_capability(breaker, listening_mode=None)
    inputs = SnapshotInputs(
        breaker=breaker,
        # The REST endpoint isn't holding a live mic handle, so the audio
        # block surfaces ``None`` / ``0`` for device/queue. The ws
        # publisher (started from the lifespan with the live MicCapture)
        # is where those values come through. For v1 we accept this
        # asymmetry — the dashboard prefers the ws snapshot when
        # available; the REST poll is a fallback.
        mic_device=None,
        mic_queue_depth=0,
        ws_subscribers=pubsub.subscriber_count,
        listening_mode=None,
        capability_check_result=capability_result,
    )

    # Run the snapshot in a thread so a slow sqlite scan can't block
    # the event loop. The function is pure (no mutation) so it's
    # threadpool-safe.
    snapshot = await asyncio.to_thread(get_metrics_snapshot, conn, inputs)
    return MetricsSnapshotResponse.model_validate(snapshot.to_dict())


__all__ = ["MetricsSnapshotResponse", "get_metrics_breaker", "get_metrics_db", "router"]
