"""``If-Match-Version`` decoding + 409 response shape helpers.

Every mutating activity endpoint reads the ``If-Match-Version`` header,
validates that it parses cleanly, and (after the SQL UPDATE) compares
the affected row count against 1 to decide between 200 and 409. The
shapes here are imported by :mod:`toybox.api.activities` and pinned by
``tests/integration/test_version_conflicts.py``.

The wire shape for a conflict is load-bearing: parent UI (Step 9)
reads ``current_version`` to refetch and ``current_state`` to render
a useful error.
"""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from .errors import ErrorCode

HEADER_NAME = "If-Match-Version"


class MissingVersionError(HTTPException):
    """400: caller forgot the ``If-Match-Version`` header."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "missing_version", "header": HEADER_NAME},
        )


class InvalidVersionError(HTTPException):
    """400: ``If-Match-Version`` was not a base-10 integer."""

    def __init__(self, raw: str) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_version", "header": HEADER_NAME, "value": raw},
        )


class VersionConflictError(HTTPException):
    """409: caller's version did not match the row's current version.

    The body is consumed by the parent UI to refetch and recover, so
    the shape matches :class:`VersionConflictBody` exactly.
    """

    def __init__(self, current_version: int, current_state: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": ErrorCode.version_conflict.value,
                "current_version": current_version,
                "current_state": current_state,
            },
        )


class VersionConflictBody(BaseModel):
    """Pydantic model for the 409 response body shape.

    Exposed so OpenAPI consumers (including the typescript generator)
    pick up the canonical shape. The actual error is raised via
    :class:`VersionConflictError`, which uses the same dict layout.
    """

    code: str
    current_version: int
    current_state: str


def parse_if_match_version(raw: str | None) -> int:
    """Validate the header value and return it as ``int``.

    * Missing header → :class:`MissingVersionError` (400)
    * Non-decimal-integer → :class:`InvalidVersionError` (400)

    Negative integers are rejected; the schema guarantees
    ``activities.version >= 1``, so a negative client value can never
    win.
    """
    if raw is None:
        raise MissingVersionError()
    stripped = raw.strip()
    if not stripped or not stripped.isdigit():
        # ``isdigit`` rejects ``+1``, ``-1``, ``1.0`` and the empty
        # string, which is exactly the validation contract we want.
        raise InvalidVersionError(raw)
    return int(stripped)


def if_match_version_dependency(
    if_match_version: str | None = Header(default=None, alias=HEADER_NAME),
) -> int:
    """FastAPI dependency: pull the header out of the request."""
    return parse_if_match_version(if_match_version)


def conflict_payload(current_version: int, current_state: str) -> dict[str, Any]:
    """Return the dict body used in :class:`VersionConflictError` 409 responses."""
    return {
        "code": ErrorCode.version_conflict.value,
        "current_version": current_version,
        "current_state": current_state,
    }


__all__ = [
    "HEADER_NAME",
    "InvalidVersionError",
    "MissingVersionError",
    "VersionConflictBody",
    "VersionConflictError",
    "conflict_payload",
    "if_match_version_dependency",
    "parse_if_match_version",
]
