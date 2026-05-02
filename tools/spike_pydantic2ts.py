"""Spike: does pydantic2ts emit a StrEnum as a TS string-literal union?

Phase A Step 1 prerequisite. Defines a tiny StrEnum and a Pydantic model
that uses it (so pydantic2ts has a Pydantic model to anchor on), runs
``pydantic2ts``, and prints the generated TS so we can decide between:

* Case 1: pydantic2ts emits the enum natively → use it for ``errors.ts``.
* Case 2: pydantic2ts skips the enum → fall back to ``gen_error_codes_ts.py``.

This file is throwaway — it lives in ``tools/`` for traceability but is
not wired into the pre-commit pipeline.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SOURCE = '''\
"""Spike module fed to pydantic2ts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class SpikeError(StrEnum):
    upload_too_large = "upload_too_large"
    version_conflict = "version_conflict"


class SpikeEnvelope(BaseModel):
    code: SpikeError
'''


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "spike_models.py"
        src.write_text(SOURCE, encoding="utf-8")
        out = Path(tmp) / "spike.ts"

        # Make the temp dir importable as a package root.
        sys.path.insert(0, tmp)

        from pydantic2ts import generate_typescript_defs

        generate_typescript_defs("spike_models", str(out))
        rendered = out.read_text(encoding="utf-8")
        print("=" * 60)
        print("PYDANTIC2TS SPIKE OUTPUT")
        print("=" * 60)
        print(rendered)
        print("=" * 60)

        if "upload_too_large" in rendered and "version_conflict" in rendered:
            print("RESULT: case 1 — values emitted somewhere in TS output.")
        else:
            print("RESULT: case 2 — enum members missing from TS output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
