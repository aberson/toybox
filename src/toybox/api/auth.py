"""Auth surface: ``/api/auth/parent`` (issue parent token) and
``/api/auth/pair`` (issue child/kiosk token).

Phase A scaffolding only: the parent-side PIN check lands in Phase D.
The Step 1 LAN-bind guard already prevents non-loopback exposure, so
the absence of a PIN there is safe. The ``/pair`` route, however,
mints a child-scope token that grants ws + kiosk access, so it must
be initiated from an authenticated parent UI: it requires a
parent-scope token. The kiosk pairing flow becomes:

1. Parent UI obtains a parent token via ``POST /api/auth/parent``.
2. Parent UI calls ``POST /api/auth/pair`` with the child id and the
   parent token.
3. The response carries a child-scope token the parent UI hands to
   the kiosk.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..core.auth import IssuedToken, TokenScope, issue_token
from .auth_dep import RequireScope, get_auth_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class ParentTokenSubject(BaseModel):
    """Tiny object describing the authenticated parent."""

    kind: str = Field(default="parent", min_length=1)


class ParentTokenResponse(BaseModel):
    """Wire shape for ``POST /api/auth/parent``."""

    token: str = Field(min_length=1)
    expires_at: float = Field(gt=0)
    subject: ParentTokenSubject = Field(default_factory=ParentTokenSubject)


def _to_parent_response(issued: IssuedToken) -> ParentTokenResponse:
    return ParentTokenResponse(
        token=issued.token,
        expires_at=issued.expires_at.timestamp(),
        subject=ParentTokenSubject(kind="parent"),
    )


@router.post("/parent", response_model=ParentTokenResponse)
def post_parent(
    conn: Annotated[sqlite3.Connection, Depends(get_auth_db)],
) -> ParentTokenResponse:
    """Issue a parent-scope token. PIN check arrives in Phase D."""
    issued = issue_token(conn, TokenScope.parent)
    return _to_parent_response(issued)


class PairRequest(BaseModel):
    """Body for ``POST /api/auth/pair``."""

    child_id: str = Field(min_length=1)


class PairSubject(BaseModel):
    """Tiny object describing the authenticated child."""

    kind: str = Field(default="child", min_length=1)
    id: str = Field(min_length=1)


class PairResponse(BaseModel):
    """Wire shape for ``POST /api/auth/pair``."""

    token: str = Field(min_length=1)
    expires_at: float = Field(gt=0)
    subject: PairSubject


def _to_pair_response(issued: IssuedToken, child_id: str) -> PairResponse:
    return PairResponse(
        token=issued.token,
        expires_at=issued.expires_at.timestamp(),
        subject=PairSubject(kind="child", id=child_id),
    )


@router.post("/pair", response_model=PairResponse)
def post_pair(
    body: PairRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_auth_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> PairResponse:
    """Issue a child-scope token bound to the named child profile.

    Gated behind a parent-scope token so kiosk pairing must originate
    in the authenticated parent UI.
    """
    row = conn.execute(
        "SELECT id FROM children WHERE id = ?",
        (body.child_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "child_not_found", "child_id": body.child_id},
        )
    issued = issue_token(
        conn,
        TokenScope.child,
        child_session_label=body.child_id,
    )
    return _to_pair_response(issued, body.child_id)


__all__ = [
    "PairRequest",
    "PairResponse",
    "PairSubject",
    "ParentTokenResponse",
    "ParentTokenSubject",
    "get_auth_db",
    "router",
]
