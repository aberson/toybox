"""Child-profile CRUD REST API.

Step 18: parent UI editor for the existing ``children`` table. The
schema landed in :mod:`toybox.db.migrations.0001_initial`; this router
adds the HTTP surface (list / get / create / update / delete) without
introducing a new migration.

All endpoints require a parent-scope token via the existing
:class:`toybox.api.auth_dep.RequireScope` dependency, matching the
style in :mod:`toybox.api.activities`.

The DELETE handler refuses to delete a profile that is referenced by an
``activities`` row. Because ``activities.child_ids`` is a JSON-encoded
TEXT column (no normalised join table), the conflict check uses a SQL
``LIKE`` substring match on the encoded id token. This is a known
schema limitation; a future migration may split it into a join table.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.auth import TokenScope
from ..db import connect, resolve_db_path
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/children", tags=["children"])

# Allowed values for the ``reading_level`` column. Stored as a free-form
# TEXT in the schema; the API constrains it to this enum (or ``None``)
# so the parent UI's <select> can render a fixed list.
ReadingLevel = Literal["pre-reader", "early-reader", "fluent"]
_VALID_READING_LEVELS: frozenset[str] = frozenset({"pre-reader", "early-reader", "fluent"})


def get_children_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield a children-scoped SQLite connection.

    ``check_same_thread=False`` because FastAPI dispatches the sync
    generator setup, the handler body, and teardown via
    ``run_in_threadpool``; anyio may pick a different worker for each
    leg, which would otherwise trip ``sqlite3.ProgrammingError`` in
    ``conn.close()``. Mirrors the pattern in
    :func:`toybox.api.activities.get_activities_db`.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def _validate_birthdate(value: str | None) -> str | None:
    """Reject anything that isn't a valid ISO ``YYYY-MM-DD`` date.

    ``date.fromisoformat`` is strict in 3.11+ and accepts only the
    canonical form, which is exactly what the parent UI's ``<input
    type="date">`` produces.
    """
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - re-raised below
        raise ValueError("birthdate must be ISO YYYY-MM-DD") from exc
    return value


class ChildProfileBase(BaseModel):
    """Shared fields for the wire shapes.

    All fields are optional at the base — ``ChildProfileCreate`` and
    ``ChildProfile`` re-declare ``display_name`` as required.
    """

    model_config = ConfigDict(frozen=True)

    birthdate: str | None = None
    pronouns: str | None = Field(default=None, max_length=40)
    reading_level: ReadingLevel | None = None
    interests: str | None = Field(default=None, max_length=1000)
    comfort: str | None = Field(default=None, max_length=1000)
    banned_themes: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("birthdate")
    @classmethod
    def _check_birthdate(cls, value: str | None) -> str | None:
        return _validate_birthdate(value)


class ChildProfileCreate(ChildProfileBase):
    """Body for ``POST /api/children``. ``display_name`` required."""

    # ``min_length=1`` rejects an empty string up front; the post-strip
    # length check in ``_strip_display_name`` is the real ceiling so a
    # value like "  " + "a"*40 (41 raw, 40 after strip) is accepted.
    display_name: str = Field(min_length=1)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must be non-empty after trimming")
        if len(stripped) > 40:
            raise ValueError("display_name must be at most 40 characters")
        return stripped


class ChildProfileUpdate(ChildProfileBase):
    """Body for ``PATCH /api/children/{id}``. All fields optional."""

    display_name: str | None = None

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must be non-empty after trimming")
        if len(stripped) > 40:
            raise ValueError("display_name must be at most 40 characters")
        return stripped


class ChildProfile(ChildProfileBase):
    """Wire shape for a child profile returned by the REST API."""

    id: str
    display_name: str = Field(min_length=1, max_length=40)


class ChildProfileListResponse(BaseModel):
    """Envelope for ``GET /api/children``."""

    model_config = ConfigDict(frozen=True)

    children: list[ChildProfile]


class DeleteResponse(BaseModel):
    """Envelope for ``DELETE /api/children/{id}``."""

    model_config = ConfigDict(frozen=True)

    ok: bool = True


def _row_to_profile(row: sqlite3.Row) -> ChildProfile:
    """Convert a ``children`` row to the wire shape."""
    reading_level_raw = row["reading_level"]
    # Defensive: the DB column is free-form TEXT; if a row was hand-
    # inserted with an invalid value, surface ``None`` rather than
    # raising — the parent UI can re-save with a valid pick.
    reading_level: ReadingLevel | None
    if reading_level_raw in _VALID_READING_LEVELS:
        reading_level = reading_level_raw
    else:
        reading_level = None
    return ChildProfile(
        id=str(row["id"]),
        display_name=str(row["display_name"]),
        birthdate=row["birthdate"],
        pronouns=row["pronouns"],
        reading_level=reading_level,
        interests=row["interests"],
        comfort=row["comfort"],
        banned_themes=row["banned_themes"],
        notes=row["notes"],
    )


def _fetch_child_row(conn: sqlite3.Connection, child_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, display_name, birthdate, pronouns, reading_level, "
        "interests, comfort, banned_themes, notes "
        "FROM children WHERE id = ?",
        (child_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "child_not_found", "id": child_id},
        )
    return row


def _count_referring_activities(conn: sqlite3.Connection, child_id: str) -> int:
    """Count ``activities`` rows whose ``child_ids`` JSON contains ``child_id``.

    ``activities.child_ids`` is a JSON-encoded TEXT list (e.g.
    ``["abc","def"]``). There's no normalised join table, so we use a
    LIKE substring match on the quoted-id token. This is a known
    schema limitation, not a new bug — see the module docstring.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM activities WHERE child_ids LIKE ?",
        (f'%"{child_id}"%',),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


@router.get("", response_model=ChildProfileListResponse)
def list_children(
    conn: Annotated[sqlite3.Connection, Depends(get_children_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ChildProfileListResponse:
    """Return every child profile, sorted case-insensitively by display name."""
    rows = conn.execute(
        "SELECT id, display_name, birthdate, pronouns, reading_level, "
        "interests, comfort, banned_themes, notes "
        "FROM children ORDER BY display_name COLLATE NOCASE ASC"
    ).fetchall()
    return ChildProfileListResponse(
        children=[_row_to_profile(r) for r in rows],
    )


@router.get("/{child_id}", response_model=ChildProfile)
def get_child(
    child_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_children_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ChildProfile:
    """Return one child profile by id, or 404 if missing."""
    row = _fetch_child_row(conn, child_id)
    return _row_to_profile(row)


@router.post(
    "",
    response_model=ChildProfile,
    status_code=status.HTTP_201_CREATED,
)
def create_child(
    body: ChildProfileCreate,
    conn: Annotated[sqlite3.Connection, Depends(get_children_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ChildProfile:
    """Create a new child profile. id is server-generated (uuid4 hex)."""
    new_id = uuid.uuid4().hex
    with conn:
        conn.execute(
            "INSERT INTO children "
            "(id, display_name, birthdate, pronouns, reading_level, "
            " interests, comfort, banned_themes, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id,
                body.display_name,
                body.birthdate,
                body.pronouns,
                body.reading_level,
                body.interests,
                body.comfort,
                body.banned_themes,
                body.notes,
            ),
        )
    row = _fetch_child_row(conn, new_id)
    return _row_to_profile(row)


@router.patch("/{child_id}", response_model=ChildProfile)
def update_child(
    child_id: str,
    body: ChildProfileUpdate,
    conn: Annotated[sqlite3.Connection, Depends(get_children_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ChildProfile:
    """Partial update — only fields present in the body are written.

    ``model_dump(exclude_unset=True)`` returns the fields the client
    actually sent (``None`` is a real value the client can use to clear
    a column; absent keys are skipped).
    """
    _fetch_child_row(conn, child_id)  # raises 404 when missing
    data = body.model_dump(exclude_unset=True)
    if data:
        columns = list(data.keys())
        set_clause = ", ".join(f"{col} = ?" for col in columns)
        params: list[Any] = [data[col] for col in columns]
        params.append(child_id)
        with conn:
            conn.execute(
                f"UPDATE children SET {set_clause} WHERE id = ?",
                params,
            )
    row = _fetch_child_row(conn, child_id)
    return _row_to_profile(row)


@router.delete("/{child_id}", response_model=DeleteResponse)
def delete_child(
    child_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_children_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> DeleteResponse:
    """Delete a child profile, refusing if any activity references it."""
    _fetch_child_row(conn, child_id)  # raises 404 when missing
    referring = _count_referring_activities(conn, child_id)
    if referring > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "child_in_use",
                "child_id": child_id,
                "referring_activity_count": referring,
            },
        )
    with conn:
        conn.execute("DELETE FROM children WHERE id = ?", (child_id,))
    return DeleteResponse(ok=True)


__all__ = [
    "ChildProfile",
    "ChildProfileCreate",
    "ChildProfileListResponse",
    "ChildProfileUpdate",
    "DeleteResponse",
    "ReadingLevel",
    "get_children_db",
    "router",
]
