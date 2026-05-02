"""FastAPI auth dependency: require a token whose scope is in a set.

The dependency reads the ``Authorization: Bearer <token>`` header (or
the ``X-Toybox-Token`` header as a fallback for the kiosk client where
``Authorization`` would clash with proxy auth) and validates the token
against the ``auth_tokens`` table. On success it returns the
:class:`toybox.core.auth.TokenSubject`. On failure it raises 401.

This module also owns :func:`get_auth_db` because the dep needs an
SQLite connection and we want :mod:`toybox.api.auth` to depend on
this module rather than the reverse (avoids an import cycle).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from ..core.auth import TokenError, TokenScope, TokenSubject, validate_token
from ..db import connect, resolve_db_path


def get_auth_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield an auth-scoped SQLite connection."""
    conn = connect(resolve_db_path())
    try:
        yield conn
    finally:
        conn.close()


def _extract_token(authorization: str | None, x_toybox_token: str | None) -> str:
    if x_toybox_token is not None:
        candidate = x_toybox_token.strip()
        if candidate:
            return candidate
    if authorization and authorization.lower().startswith("bearer "):
        candidate = authorization[7:].strip()
        if candidate:
            return candidate
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "auth_required"},
    )


class RequireScope:
    """Callable FastAPI dependency that enforces a scope set."""

    def __init__(self, allowed: Iterable[TokenScope]) -> None:
        self._allowed = frozenset(allowed)

    def __call__(
        self,
        conn: Annotated[sqlite3.Connection, Depends(get_auth_db)],
        authorization: Annotated[str | None, Header()] = None,
        x_toybox_token: Annotated[str | None, Header(alias="X-Toybox-Token")] = None,
    ) -> TokenSubject:
        plaintext = _extract_token(authorization, x_toybox_token)
        try:
            subject = validate_token(conn, plaintext)
        except TokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "auth_invalid", "reason": str(exc)},
            ) from exc
        if subject.scope not in self._allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "auth_scope_forbidden", "scope": subject.scope.value},
            )
        return subject


__all__ = ["RequireScope", "get_auth_db"]
