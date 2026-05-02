"""``python -m toybox.db.migrate`` entrypoint.

Resolves the DB path from ``TOYBOX_DB_PATH`` (default ``data/toybox.db``),
creates parent directories if needed, runs the migration runner, and
prints a one-line summary. Exits 0 on success; any exception bubbles up
to a non-zero exit with a traceback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .connection import connect
from .migrations import current_version, run_migrations

DEFAULT_DB_PATH = Path("data") / "toybox.db"


def _resolve_db_path() -> Path:
    raw = os.environ.get("TOYBOX_DB_PATH")
    return Path(raw) if raw else DEFAULT_DB_PATH


def main() -> int:
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        applied = run_migrations(conn)
        version = current_version(conn)
    finally:
        conn.close()

    print(f"toybox.db.migrate: applied {len(applied)} migration(s); db={db_path} version={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
