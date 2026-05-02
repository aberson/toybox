"""SQLite persistence layer.

Step 2 of Phase A. Exposes the connection helper, slug derivation, and
the migration runner used by ``python -m toybox.db.migrate``.
"""

from __future__ import annotations

from .connection import connect
from .slugs import InvalidDisplayNameError, derive_slug

__all__ = ["InvalidDisplayNameError", "connect", "derive_slug"]
