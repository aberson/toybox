"""``python -m toybox.db.migrate`` entrypoint.

Resolves the DB path from ``TOYBOX_DB_PATH`` (default ``data/toybox.db``),
creates parent directories if needed, runs the migration runner, loads
the shipped library personas (idempotent upsert), and prints a one-line
summary. Exits 0 on success; any exception bubbles up to a non-zero
exit with a traceback.
"""

from __future__ import annotations

import sys

from ..personas import load_library_personas
from . import resolve_db_path
from .connection import connect
from .migrations import current_version, run_migrations


def main() -> int:
    db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        applied = run_migrations(conn)
        version = current_version(conn)
        # Load the shipped persona library into the personas table.
        # Idempotent upsert; safe to re-run on every migrate. Without
        # this, the activity proposal flow has no library personas to
        # pick from and every kiosk renders the same fallback letter.
        personas_loaded = load_library_personas(conn, db_path.parent)
    finally:
        conn.close()

    print(
        f"toybox.db.migrate: applied {len(applied)} migration(s); "
        f"db={db_path} version={version} personas_loaded={personas_loaded}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
