"""Determinism + drift tests for the ErrorCode → TypeScript generator.

These tests pin two contracts:

1. ``render_typescript()`` is deterministic — calling it twice produces
   byte-identical output. Guards against accidental nondeterminism (dict
   ordering, timestamp insertion, line-ending drift).
2. The committed ``frontend/src/shared/errors.ts`` matches what the
   generator currently emits — an in-pytest equivalent of
   ``git diff --exit-code`` after running the codegen.
"""

from __future__ import annotations

import sys
from pathlib import Path

from toybox.core.errors import ErrorCode

# tools/ is not on sys.path, so add it before importing the generator.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from gen_error_codes_ts import render_typescript  # type: ignore[import-not-found]  # noqa: E402

COMMITTED_TS_PATH = REPO_ROOT / "frontend" / "src" / "shared" / "errors.ts"


def test_render_typescript_is_deterministic() -> None:
    """Two back-to-back renders must produce byte-identical output."""
    first = render_typescript(ErrorCode)
    second = render_typescript(ErrorCode)
    assert first == second
    assert first.encode("utf-8") == second.encode("utf-8")


def test_committed_errors_ts_matches_generator() -> None:
    """The checked-in errors.ts must equal a fresh render — drift gate."""
    fresh = render_typescript(ErrorCode)
    committed = COMMITTED_TS_PATH.read_text(encoding="utf-8")
    assert committed == fresh, (
        "frontend/src/shared/errors.ts has drifted from the generator output. "
        "Run `uv run python tools/gen_types_ts.py` and commit the result."
    )
