"""Top-level codegen entrypoint for backend → frontend type sync.

Phase A Step 1 spike outcome: **case 2** — ``pydantic2ts`` requires the
``json2ts`` Node CLI (``json-schema-to-typescript``) which is not present
on the developer machine and not declared as a project dependency. Until
that dependency story is settled, this script delegates to the fallback
``gen_error_codes_ts.py`` which produces a deterministic TS file by
walking ``ErrorCode`` directly.

When Pydantic models are added in Step 2+ that need TS counterparts,
either:

1. install ``json2ts`` and uncomment the pydantic2ts call below, or
2. write per-module fallbacks alongside this script.

Either way, this file remains the single entrypoint that pre-commit and
CI invoke. ``frontend/src/shared/errors.ts`` MUST round-trip cleanly:

    python tools/gen_types_ts.py
    git diff --exit-code frontend/src/shared/errors.ts
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_error_codes_fallback() -> int:
    """Run the deterministic ErrorCode → TS generator."""
    script = REPO_ROOT / "tools" / "gen_error_codes_ts.py"
    result = subprocess.run([sys.executable, str(script)], cwd=str(REPO_ROOT))
    return result.returncode


def main() -> int:
    rc = run_error_codes_fallback()
    if rc != 0:
        return rc
    # When pydantic2ts/json2ts is wired up, additional codegen calls land here
    # for the Pydantic schema modules (see tools/README.md).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
