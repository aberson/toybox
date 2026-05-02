"""Forward-only SQLite migration runner.

Walks ``src/toybox/db/migrations/*.sql`` files in numerical order. Each
filename must start with a zero-padded version (``0001_initial.sql``,
``0002_foo.sql``). Files whose version exceeds the current
``MAX(version)`` in ``schema_migrations`` are applied in a single
``BEGIN``/``COMMIT`` transaction per file and recorded on success.

If a migration crashes mid-apply, the transaction rolls back and the
file is NOT recorded; the next process restart retries it.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

_FILENAME_RE = re.compile(r"^(\d+)_[A-Za-z0-9_]+\.sql$")

_BOOTSTRAP_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    filename   TEXT    NOT NULL,
    applied_at TEXT    NOT NULL
);
"""


@dataclass(frozen=True)
class Migration:
    """A single migration file."""

    version: int
    filename: str
    path: Path

    def read_sql(self) -> str:
        """Return the SQL text of this migration file (UTF-8)."""
        return self.path.read_text(encoding="utf-8")


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parent


def _split_sql(sql: str) -> list[str]:
    """Split a SQL script into individual statements.

    Uses :func:`sqlite3.complete_statement` to detect statement boundaries
    so we can run each statement via ``cursor.execute()`` inside an
    explicit transaction. ``Connection.executescript()`` is unsafe here
    because it issues an implicit ``COMMIT`` before running and runs each
    statement in autocommit mode, which breaks per-migration atomicity.
    """
    statements: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            non_comment = "\n".join(
                ln for ln in stmt.splitlines() if not ln.lstrip().startswith("--")
            ).strip()
            if non_comment:
                statements.append(stmt)
            buf = ""
    if buf.strip():
        # Trailing content without a closing semicolon — let sqlite raise a
        # clear error when we try to execute it.
        statements.append(buf.strip())
    return statements


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    """Return all migrations in ``directory`` ordered by version ascending.

    Raises:
        ValueError: If two files share a version, or a filename doesn't
            match the ``NNNN_name.sql`` pattern.
    """
    base = directory if directory is not None else _migrations_dir()
    found: list[Migration] = []
    seen: dict[int, str] = {}
    for entry in sorted(base.iterdir()):
        if entry.suffix != ".sql" or not entry.is_file():
            continue
        match = _FILENAME_RE.match(entry.name)
        if not match:
            raise ValueError(f"migration filename {entry.name!r} does not match NNNN_name.sql")
        version = int(match.group(1))
        if version in seen:
            raise ValueError(
                f"duplicate migration version {version}: {seen[version]!r} and {entry.name!r}"
            )
        seen[version] = entry.name
        found.append(Migration(version=version, filename=entry.name, path=entry))
    return found


def current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    conn.execute(_BOOTSTRAP_SCHEMA_MIGRATIONS)
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    if row is None or row["v"] is None:
        return 0
    return cast(int, row["v"])


def _apply_one(conn: sqlite3.Connection, migration: Migration) -> None:
    sql = migration.read_sql()
    statements = _split_sql(sql)
    applied_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        conn.execute("BEGIN")
        for stmt in statements:
            conn.execute(stmt)
        conn.execute(
            "INSERT INTO schema_migrations (version, filename, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.filename, applied_at),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def run_migrations(
    conn: sqlite3.Connection,
    *,
    directory: Path | None = None,
) -> list[Migration]:
    """Apply every pending migration from ``directory`` to ``conn``.

    Args:
        conn: Open SQLite connection (typically from
            :func:`toybox.db.connection.connect`).
        directory: Override the migrations directory (defaults to the
            package's own ``migrations/`` folder).

    Returns:
        The list of migrations applied during this call, in order.
    """
    available = discover_migrations(directory)
    applied_now: list[Migration] = []
    have = current_version(conn)
    pending: Iterable[Migration] = (m for m in available if m.version > have)
    for migration in pending:
        _apply_one(conn, migration)
        applied_now.append(migration)
    return applied_now


__all__ = [
    "Migration",
    "current_version",
    "discover_migrations",
    "run_migrations",
]
