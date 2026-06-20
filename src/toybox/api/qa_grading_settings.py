"""Q&A answer-grading tolerance setting HTTP API.

``GET /api/settings/qa-grading`` returns the persisted tolerance;
``PUT`` accepts a body of ``{value: str}`` where ``str`` must be one of
``{"off", "lenient", "strict"}``, persists it, and returns the canonical
value.

The GET is unauthenticated (household read, mirrors
``GET /api/settings/parent-involvement``); the PUT is parent-scope only —
operator-controlled household setting, not a session action a child
could trigger.

Phase W Step W3: WIRED — the advance handler reads this value and, when
it is not ``"off"`` and the current step has a ``question`` +
``expected_answer``, attempts an auto-grade before the R3
``question_pending`` 409 (see :func:`toybox.api.activities.post_advance`).

No WS broadcast: single-parent kiosk model. The next ``App.tsx`` mount
fetches the fresh value.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.auth import TokenScope
from ..core.qa_grading import (
    QA_GRADING_VALID,
    get_qa_grading,
    set_qa_grading,
)
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/settings", tags=["qa_grading_settings"])


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class QaGradingResponse(BaseModel):
    """Wire shape for ``GET`` and ``PUT`` ``/api/settings/qa-grading``."""

    value: str


class QaGradingUpdate(BaseModel):
    """Request body for ``PUT /api/settings/qa-grading``."""

    value: str


@router.get("/qa-grading", response_model=QaGradingResponse)
def get_qa_grading_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> QaGradingResponse:
    """Return the persisted Q&A grading tolerance."""
    value = get_qa_grading(conn)
    return QaGradingResponse(value=value)


@router.put("/qa-grading", response_model=QaGradingResponse)
def put_qa_grading_endpoint(
    body: QaGradingUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> QaGradingResponse:
    """Persist ``value`` and return the canonical string.

    Translates :class:`ValueError` from the helper into HTTP 422 with the
    full canonical set in the error body, so the frontend can surface
    "valid values are off, lenient, strict" without hard-coding the list.
    """
    try:
        new_value = set_qa_grading(conn, body.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_qa_grading",
                "valid": sorted(QA_GRADING_VALID),
            },
        ) from exc
    return QaGradingResponse(value=new_value)


__all__ = [
    "QaGradingResponse",
    "QaGradingUpdate",
    "get_db",
    "router",
]
