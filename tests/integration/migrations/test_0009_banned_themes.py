"""Coverage for Phase H Step H4 migration 0009 (banned-themes promotion).

Pins:

* The ``banned_themes`` column is removed from ``children``.
* All other ``children`` columns + rows survive the table rebuild.
* Per-child values UNION / trim / lowercase / dedupe / sort into a
  single ``settings.banned_themes_global`` row.
* All-NULL / all-empty inputs leave the global key absent (not an
  empty-string row — ``None`` is the canonical "no constraint" state).
* The ``children`` API zombie field reads back as ``null`` post-migration.
* Migration is forward and idempotent.

Test pattern mirrors :mod:`tests.integration.migrations.test_0008_activity_slot_fills`:
apply migrations 1-8 against a private dir, seed the old schema, then
run the real migration runner to bring in 0009.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import (
    Migration,
    current_version,
    discover_migrations,
    run_migrations,
)


def _apply_pre_h4(tmp_path: Path) -> Path:
    """Apply migrations 1..8 to a fresh DB and return its path.

    Stages the pre-0009 .sql files into a private directory so the
    runner sees a frozen "before" snapshot, then we drive 0009 in via
    the real package directory below.
    """
    pre_dir = tmp_path / "pre_h4_migrations"
    pre_dir.mkdir()
    available = discover_migrations()
    pre_h4: list[Migration] = [m for m in available if m.version <= 8]
    for m in pre_h4:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 8
    finally:
        conn.close()
    return db_path


@pytest.fixture
def fresh_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Connection where every migration (1..N) has run on a fresh DB."""
    c = connect(tmp_path / "toybox.db")
    try:
        run_migrations(c)
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Schema shape after 0009
# ---------------------------------------------------------------------------


def test_0009_removes_banned_themes_column(fresh_conn: sqlite3.Connection) -> None:
    """``children.banned_themes`` is gone after migration 0009 runs."""
    applied = [m.version for m in run_migrations(fresh_conn)]
    # ``fresh_conn`` already ran migrations once on setup; this re-run
    # should be a no-op (idempotency check).
    assert applied == []

    cols = [r["name"] for r in fresh_conn.execute("PRAGMA table_info(children)")]
    assert "banned_themes" not in cols
    # Every other column from the pre-0009 schema survives.
    assert set(cols) == {
        "id",
        "display_name",
        "birthdate",
        "pronouns",
        "reading_level",
        "interests",
        "comfort",
        "notes",
    }


def test_0009_is_idempotent(fresh_conn: sqlite3.Connection) -> None:
    """Running migrations a second time is a no-op."""
    starting = current_version(fresh_conn)
    second = run_migrations(fresh_conn)
    assert second == []
    assert current_version(fresh_conn) == starting


# ---------------------------------------------------------------------------
# Union / normalisation
# ---------------------------------------------------------------------------


def test_0009_unions_overlapping_themes_across_children(tmp_path: Path) -> None:
    """Three children with overlapping themes → unioned, sorted, lowercased."""
    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("a", "Alice", "monsters, spiders"),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("b", "Bob", "spiders, ghosts"),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("c", "Carol", "Monsters, GHOSTS"),
            )
        applied = run_migrations(conn)
        assert any(m.version == 9 for m in applied)

        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", ("banned_themes_global",)
        ).fetchone()
        assert row is not None
        # Lowercased, deduped, sorted, joined with ", ".
        assert row["value"] == "ghosts, monsters, spiders"
    finally:
        conn.close()


def test_0009_handles_case_sensitivity(tmp_path: Path) -> None:
    """Mixed-case duplicates collapse to a single lowercased entry."""
    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("a", "Alice", "Monsters"),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("b", "Bob", "monsters"),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("c", "Carol", "MONSTERS"),
            )
        run_migrations(conn)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", ("banned_themes_global",)
        ).fetchone()
        assert row is not None
        assert row["value"] == "monsters"
    finally:
        conn.close()


def test_0009_trims_whitespace_around_tokens(tmp_path: Path) -> None:
    """``"  scary  , loud  "`` → ``"loud, scary"``."""
    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("a", "Alice", "  scary  , loud  "),
            )
        run_migrations(conn)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", ("banned_themes_global",)
        ).fetchone()
        assert row is not None
        assert row["value"] == "loud, scary"
    finally:
        conn.close()


def test_0009_skips_empty_tokens(tmp_path: Path) -> None:
    """Consecutive commas + empty tokens are dropped."""
    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("a", "Alice", "scary,,loud,,,"),
            )
        run_migrations(conn)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", ("banned_themes_global",)
        ).fetchone()
        assert row is not None
        assert row["value"] == "loud, scary"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Empty / NULL handling
# ---------------------------------------------------------------------------


def test_0009_all_null_leaves_key_absent(tmp_path: Path) -> None:
    """No child has any banned themes → no settings row inserted.

    Distinct from "row with empty value" — ``current_banned_themes_global``
    reads the absent row as ``None``, which the resolver treats as
    "no household ban list".
    """
    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("a", "Alice", None),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("b", "Bob", None),
            )
        run_migrations(conn)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", ("banned_themes_global",)
        ).fetchone()
        assert row is None
    finally:
        conn.close()


def test_0009_mixed_null_empty_and_non_empty(tmp_path: Path) -> None:
    """Empty / whitespace-only rows are skipped; non-empty rows union."""
    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("a", "Alice", "monsters"),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("b", "Bob", None),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("c", "Carol", ""),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("d", "Dee", "   "),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("e", "Eve", "spiders"),
            )
        run_migrations(conn)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", ("banned_themes_global",)
        ).fetchone()
        assert row is not None
        assert row["value"] == "monsters, spiders"
    finally:
        conn.close()


def test_0009_no_children_at_all_leaves_key_absent(tmp_path: Path) -> None:
    """Fresh DB with zero children rows → no settings row inserted."""
    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        run_migrations(conn)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", ("banned_themes_global",)
        ).fetchone()
        assert row is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Children table integrity post-rebuild
# ---------------------------------------------------------------------------


def test_0009_preserves_all_other_children_columns_and_rows(tmp_path: Path) -> None:
    """Rebuilt ``children`` carries every non-banned_themes column intact."""
    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children "
                "(id, display_name, birthdate, pronouns, reading_level, "
                " interests, comfort, banned_themes, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "alice",
                    "Alice",
                    "2018-03-15",
                    "she/her",
                    "early-reader",
                    "dinosaurs",
                    "stuffed bunny",
                    "monsters",
                    "carries a note",
                ),
            )
            conn.execute(
                "INSERT INTO children "
                "(id, display_name, birthdate, pronouns, reading_level, "
                " interests, comfort, banned_themes, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("bob", "Bob", None, None, None, None, None, None, None),
            )
        run_migrations(conn)

        rows = conn.execute(
            "SELECT id, display_name, birthdate, pronouns, reading_level, "
            "interests, comfort, notes FROM children ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        alice = dict(rows[0])
        assert alice == {
            "id": "alice",
            "display_name": "Alice",
            "birthdate": "2018-03-15",
            "pronouns": "she/her",
            "reading_level": "early-reader",
            "interests": "dinosaurs",
            "comfort": "stuffed bunny",
            "notes": "carries a note",
        }
        bob = dict(rows[1])
        assert bob == {
            "id": "bob",
            "display_name": "Bob",
            "birthdate": None,
            "pronouns": None,
            "reading_level": None,
            "interests": None,
            "comfort": None,
            "notes": None,
        }
    finally:
        conn.close()


def test_0009_zombie_field_returns_null_on_get_children(tmp_path: Path) -> None:
    """After 0009 + the H4 API patch, ``GET /api/children`` carries the
    zombie ``banned_themes: null`` field.

    Pins the seam: pre-H5 frontend keeps deserialising the field, the
    column is gone, and the value is consistently ``null`` regardless
    of what the pre-migration row carried.
    """
    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children "
                "(id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("c1", "Cee", "monsters"),
            )
        run_migrations(conn)
    finally:
        conn.close()

    # Now hit the API. The zombie field must be present + null.
    from collections.abc import Iterator as _Iterator

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from toybox.api.auth_dep import get_auth_db
    from toybox.api.children import get_children_db
    from toybox.app import create_app
    from toybox.core.auth import TokenScope, issue_token

    app: FastAPI = create_app()

    def _override_db() -> _Iterator[sqlite3.Connection]:
        c = connect(db_path, check_same_thread=False)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_children_db] = _override_db
    app.dependency_overrides[get_auth_db] = _override_db

    conn = connect(db_path)
    try:
        token = issue_token(conn, TokenScope.parent).token
    finally:
        conn.close()
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        response = client.get("/api/children", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert len(body["children"]) == 1
        child = body["children"][0]
        # Zombie field present, always null.
        assert "banned_themes" in child
        assert child["banned_themes"] is None


# ---------------------------------------------------------------------------
# End-to-end: the helper reads the migrated value back
# ---------------------------------------------------------------------------


def test_0009_current_banned_themes_global_reads_migrated_value(tmp_path: Path) -> None:
    """After migration, the helper surfaces the same UNIONed string."""
    from toybox.core.banned_themes import current_banned_themes_global

    db_path = _apply_pre_h4(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("a", "Alice", "scary, loud"),
            )
            conn.execute(
                "INSERT INTO children (id, display_name, banned_themes) VALUES (?, ?, ?)",
                ("b", "Bob", "Scary, ghosts"),
            )
        run_migrations(conn)
        value = current_banned_themes_global(conn)
        assert value == "ghosts, loud, scary"
    finally:
        conn.close()
