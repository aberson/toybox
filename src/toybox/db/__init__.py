"""SQLite persistence layer.

Step 2 of Phase A. Exposes the connection helper, slug derivation, the
migration runner used by ``python -m toybox.db.migrate``, and the
single-source-of-truth DB path resolution helpers used by both the CLI
migration entrypoint and the FastAPI ``get_db`` dependency.
"""

from __future__ import annotations

import os
from pathlib import Path

from .connection import connect
from .slugs import InvalidDisplayNameError, derive_slug

DB_PATH_ENV = "TOYBOX_DB_PATH"
DEFAULT_DB_PATH = Path("data") / "toybox.db"


def resolve_db_path() -> Path:
    """Return the SQLite path from ``TOYBOX_DB_PATH`` or the default."""
    raw = os.environ.get(DB_PATH_ENV)
    return Path(raw) if raw else DEFAULT_DB_PATH


__all__ = [
    "DB_PATH_ENV",
    "DEFAULT_DB_PATH",
    "InvalidDisplayNameError",
    "connect",
    "derive_slug",
    "resolve_db_path",
]
