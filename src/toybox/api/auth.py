"""Auth surface: ``/api/auth/parent`` (issue parent token, PIN-gated),
``/api/auth/parent/setup`` (first-run PIN setup), ``/api/auth/parent/status``
(pre-token bootstrap probe), and ``/api/auth/pair`` (mint a child token).

Step 21 lands the real PIN gate. The pre-Phase-D auth path that issued a
parent token without a PIN check is gone; existing callers must include a
PIN body. ``POST /api/auth/parent/setup`` is the first-run shim — it
accepts a fresh PIN + confirmation, persists the argon2id hash via
``settings.parent_pin_hash``, and returns a token immediately so the UI
doesn't need a second login round-trip.

Failed attempts log at WARNING with the failure count only — never the
attempted PIN.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..core.auth import IssuedToken, TokenScope, issue_token
from ..core.pin import (
    PIN_MAX_LENGTH,
    PIN_MIN_LENGTH,
    PinFormatError,
    get_pin_hash,
    pin_is_set,
    set_pin_hash,
    validate_pin_format,
    verify_pin,
)
from ..core.pin_rate_limit import PinRateLimiter, get_rate_limiter
from .auth_dep import RequireScope, get_auth_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

_logger = logging.getLogger(__name__)


# ---- shared models ---------------------------------------------------


class ParentTokenSubject(BaseModel):
    """Tiny object describing the authenticated parent."""

    kind: str = Field(default="parent", min_length=1)


class ParentTokenResponse(BaseModel):
    """Wire shape for the token-issuing parent endpoints.

    Used by ``POST /api/auth/parent`` and ``POST /api/auth/parent/setup``
    so the frontend can re-use one parser.
    """

    token: str = Field(min_length=1)
    expires_at: float = Field(gt=0)
    subject: ParentTokenSubject = Field(default_factory=ParentTokenSubject)


def _to_parent_response(issued: IssuedToken) -> ParentTokenResponse:
    return ParentTokenResponse(
        token=issued.token,
        expires_at=issued.expires_at.timestamp(),
        subject=ParentTokenSubject(kind="parent"),
    )


# ---- pin login / setup / status --------------------------------------


class ParentLoginRequest(BaseModel):
    """Body for ``POST /api/auth/parent``.

    The PIN is digits-only, 4-:data:`PIN_MAX_LENGTH` characters. Pydantic
    enforces type + length here so the rate limiter never sees garbage
    that didn't even pass the basic shape check.
    """

    pin: str = Field(min_length=PIN_MIN_LENGTH, max_length=PIN_MAX_LENGTH)


class ParentSetupRequest(BaseModel):
    """Body for ``POST /api/auth/parent/setup`` (first-run flow)."""

    pin: str = Field(min_length=PIN_MIN_LENGTH, max_length=PIN_MAX_LENGTH)
    confirm: str = Field(min_length=PIN_MIN_LENGTH, max_length=PIN_MAX_LENGTH)


class ParentAuthStatus(BaseModel):
    """Wire shape for ``GET /api/auth/parent/status``.

    Returned without auth so the bootstrap UI can decide setup vs login
    before it has a token.
    """

    pin_set: bool
    locked: bool
    seconds_until_unlock: int


def get_pin_rate_limiter() -> PinRateLimiter:
    """FastAPI dependency: yield the process-wide PIN rate limiter.

    Exposed as a separate function so tests can override the dep with
    a fresh :class:`PinRateLimiter` instance per case (the
    module-level singleton would otherwise leak attempt state across
    tests).
    """
    return get_rate_limiter()


def _seconds_until_unlock_int(seconds: float) -> int:
    """Round ``seconds`` up so a partial-second lock still surfaces ≥1."""
    return max(0, math.ceil(seconds))


@router.get("/parent/status", response_model=ParentAuthStatus)
def get_parent_status(
    conn: Annotated[sqlite3.Connection, Depends(get_auth_db)],
    rate_limiter: Annotated[PinRateLimiter, Depends(get_pin_rate_limiter)],
) -> ParentAuthStatus:
    """Pre-token bootstrap probe: report whether the PIN is set and locked."""
    return ParentAuthStatus(
        pin_set=pin_is_set(conn),
        locked=rate_limiter.is_locked(),
        seconds_until_unlock=_seconds_until_unlock_int(rate_limiter.seconds_until_unlock()),
    )


@router.post("/parent/setup", response_model=ParentTokenResponse)
def post_parent_setup(
    body: ParentSetupRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_auth_db)],
) -> ParentTokenResponse:
    """First-run PIN setup. 409 if a PIN is already stored.

    Returns a parent token on success so the UI can transition to the
    main flow without a second login round-trip.
    """
    if pin_is_set(conn):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "pin_already_set"},
        )
    if body.pin != body.confirm:
        # Pydantic enforces length + presence; mismatch is a semantic
        # failure surfaced as 422. The detail shape is the ``{code: ...}``
        # dict used by sibling routes (children/toys/rooms/metrics) so
        # the frontend can dispatch on ``code`` rather than parsing
        # FastAPI's auto-validation array layout. The auto-validation
        # 422 (Pydantic field errors) still uses the array shape; the
        # frontend helper recognises both.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "pin_confirm_mismatch"},
        )
    try:
        validate_pin_format(body.pin, max_length=PIN_MAX_LENGTH)
    except PinFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "pin_format_invalid", "field": "pin"},
        ) from exc
    set_pin_hash(conn, body.pin)
    issued = issue_token(conn, TokenScope.parent)
    return _to_parent_response(issued)


@router.post("/parent", response_model=ParentTokenResponse)
def post_parent(
    body: ParentLoginRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_auth_db)],
    rate_limiter: Annotated[PinRateLimiter, Depends(get_pin_rate_limiter)],
) -> ParentTokenResponse:
    """Issue a parent-scope token after verifying the PIN.

    Failure modes:

    * 423 ``pin_locked`` — too many recent failures; ``Retry-After``
      carries the integer seconds until unlock. Lock takes precedence
      over PIN correctness.
    * 401 ``pin_invalid`` — wrong PIN; ``attempts_remaining`` carries
      the count before the next attempt would lock.
    * 412 ``pin_not_set`` — the operator hasn't run setup yet; the UI
      should redirect to the setup screen.
    """
    # Lock check first — even a correct PIN can't bypass an active
    # lock per the spec's "during lock, all attempts return 423".
    if rate_limiter.is_locked():
        seconds = _seconds_until_unlock_int(rate_limiter.seconds_until_unlock())
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={"code": "pin_locked", "seconds_until_unlock": seconds},
            headers={"Retry-After": str(seconds)},
        )

    stored = get_pin_hash(conn)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "pin_not_set"},
        )

    if not verify_pin(body.pin, stored):
        rl_status = rate_limiter.record_failed_attempt()
        # Log the failure without revealing the attempted PIN. Count
        # is the only payload — this is the spec invariant.
        _logger.warning(
            "parent pin verification failed (attempts=%d, attempts_remaining=%d, locked=%s)",
            rl_status.attempts,
            rl_status.attempts_remaining,
            rl_status.locked,
        )
        if rl_status.locked:
            seconds = _seconds_until_unlock_int(rl_status.seconds_until_unlock)
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={"code": "pin_locked", "seconds_until_unlock": seconds},
                headers={"Retry-After": str(seconds)},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "pin_invalid",
                "attempts_remaining": rl_status.attempts_remaining,
            },
        )

    rate_limiter.record_successful_attempt()
    issued = issue_token(conn, TokenScope.parent)
    return _to_parent_response(issued)


# ---- pair (existing) -------------------------------------------------


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
    "ParentAuthStatus",
    "ParentLoginRequest",
    "ParentSetupRequest",
    "ParentTokenResponse",
    "ParentTokenSubject",
    "get_pin_rate_limiter",
    "router",
]
