"""Token issuance and validation backed by the ``auth_tokens`` table.

Tokens are random URL-safe strings (``secrets.token_urlsafe(32)``).
Only the SHA-256 hash of the token is persisted; the plaintext is
returned to the caller exactly once and never stored. Validation
hashes the incoming plaintext, looks up the row, and rejects anything
that is missing, revoked, or expired.

The ``auth_tokens`` schema (see ``0001_initial.sql``) uses ``scope``
plus an optional ``child_session_label`` rather than a separate
``subject_kind``/``subject_id`` pair. Phase A only needs three scopes:

* ``parent`` — issued by ``POST /api/auth/parent``
* ``child`` — issued by ``POST /api/auth/pair``
* ``admin`` — reserved; not issued in Phase A but checked here so
  future tooling can lean on the same validation path.

Times are stored as ISO-8601 UTC strings with a trailing ``Z`` to
match the rest of the schema (see how ``schema_migrations.applied_at``
is written by the migration runner).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

DEFAULT_TOKEN_TTL = timedelta(hours=24)


class TokenScope(StrEnum):
    """Scopes recognised by :func:`validate_token`."""

    parent = "parent"
    child = "child"
    admin = "admin"


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """Plaintext token plus the persisted metadata.

    The plaintext is returned exactly once — by :func:`issue_token` —
    and never stored. Subsequent validations re-hash incoming tokens
    and look up by ``token_hash``.
    """

    token: str
    token_hash: str
    scope: TokenScope
    child_session_label: str | None
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TokenSubject:
    """The subject identified by a validated token."""

    scope: TokenScope
    child_session_label: str | None


class TokenError(Exception):
    """Raised when a presented token is missing, revoked, or expired."""


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _isoformat_z(value: datetime) -> str:
    """ISO-8601 UTC with a trailing ``Z`` (matches the migration runner)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    """Parse the ``Z``-suffixed ISO timestamps written by :func:`_isoformat_z`."""
    raw = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def hash_token(token: str) -> str:
    """Return the lowercase hex SHA-256 of ``token``.

    Exposed so tests and admin tooling can compute the same hash without
    re-implementing the algorithm.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(
    conn: sqlite3.Connection,
    scope: TokenScope | str,
    *,
    child_session_label: str | None = None,
    ttl: timedelta = DEFAULT_TOKEN_TTL,
    now: datetime | None = None,
) -> IssuedToken:
    """Generate a new token, persist its hash, and return the plaintext bundle.

    Args:
        conn: Open SQLite connection.
        scope: ``TokenScope`` or its string value.
        child_session_label: Optional human-friendly child label
            persisted with child-scope tokens.
        ttl: Token lifetime; defaults to :data:`DEFAULT_TOKEN_TTL`.
        now: Override the wall clock (used by tests).

    Returns:
        An :class:`IssuedToken` whose ``token`` field is the only place
        the plaintext is exposed; future validations must re-hash.
    """
    canonical = TokenScope(scope)
    issued_at = _now(now)
    expires_at = issued_at + ttl
    plaintext = secrets.token_urlsafe(32)
    digest = hash_token(plaintext)

    with conn:
        conn.execute(
            "INSERT INTO auth_tokens "
            "(token_hash, scope, child_session_label, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                digest,
                canonical.value,
                child_session_label,
                _isoformat_z(issued_at),
                _isoformat_z(expires_at),
            ),
        )

    return IssuedToken(
        token=plaintext,
        token_hash=digest,
        scope=canonical,
        child_session_label=child_session_label,
        expires_at=expires_at,
        created_at=issued_at,
    )


def validate_token(
    conn: sqlite3.Connection,
    token: str,
    *,
    now: datetime | None = None,
) -> TokenSubject:
    """Look up ``token`` (plaintext) and return its subject metadata.

    Raises :class:`TokenError` if the token is unknown, revoked, or
    past its ``expires_at``. ``last_used_at`` is bumped on success so
    Phase D admin tooling can surface "this token has gone idle."
    """
    if not token:
        raise TokenError("missing token")
    digest = hash_token(token)
    row = conn.execute(
        "SELECT scope, child_session_label, expires_at, revoked_at "
        "FROM auth_tokens WHERE token_hash = ?",
        (digest,),
    ).fetchone()
    if row is None:
        raise TokenError("unknown token")
    if row["revoked_at"] is not None:
        raise TokenError("token revoked")
    expires_at = _parse_iso(row["expires_at"])
    current = _now(now)
    if expires_at <= current:
        raise TokenError("token expired")

    with conn:
        conn.execute(
            "UPDATE auth_tokens SET last_used_at = ? WHERE token_hash = ?",
            (_isoformat_z(current), digest),
        )

    scope = TokenScope(row["scope"])
    label = row["child_session_label"]
    return TokenSubject(scope=scope, child_session_label=label)


def revoke_token(conn: sqlite3.Connection, token_hash: str, *, now: datetime | None = None) -> bool:
    """Set ``revoked_at`` on ``token_hash``. Returns True if a row was updated."""
    revoked_at = _isoformat_z(_now(now))
    with conn:
        cur = conn.execute(
            "UPDATE auth_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (revoked_at, token_hash),
        )
    return cur.rowcount > 0


__all__ = [
    "DEFAULT_TOKEN_TTL",
    "IssuedToken",
    "TokenError",
    "TokenScope",
    "TokenSubject",
    "hash_token",
    "issue_token",
    "revoke_token",
    "validate_token",
]
