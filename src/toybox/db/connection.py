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


def connect(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a SQLite connection at ``path`` with the toybox pragmas applied.

    Args:
        path: Filesystem path to the SQLite database file.
        check_same_thread: When ``False``, allow the connection to be
            used from a thread other than the one that created it. The
            FastAPI WebSocket entry point passes ``False`` so the
            background-thread Starlette TestClient (and uvicorn's
            asyncio + threadpool dispatch) can share the connection
            opened in the request handler.

    Returns:
        A ``sqlite3.Connection`` with ``row_factory=sqlite3.Row`` and the
        four required pragmas applied.
    """
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


__all__ = ["connect"]
