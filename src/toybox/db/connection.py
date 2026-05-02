"""SQLite connection helper.

Every connection opened through :func:`connect` applies the four pragmas
required by ``documentation/plan.md`` (WAL, synchronous=NORMAL,
foreign_keys=ON, busy_timeout=5000).

We rely on the stdlib default ``isolation_level=""`` (Python opens an
implicit transaction before the first DML statement and commits on the
next ``COMMIT``/DDL). That keeps per-request handlers idiomatic
``with conn: ...`` blocks while still letting the migration runner drive
explicit ``BEGIN``/``COMMIT`` via ``conn.execute("BEGIN")`` when atomicity
across multiple statements matters.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA busy_timeout=5000;",
)


def connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection at ``path`` with the toybox pragmas applied.

    Args:
        path: Filesystem path to the SQLite database file.

    Returns:
        A ``sqlite3.Connection`` with ``row_factory=sqlite3.Row`` and the
        four required pragmas applied.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


__all__ = ["connect"]
