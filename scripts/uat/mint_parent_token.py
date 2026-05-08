"""Mint a parent-scope ws/REST token for Manual M2.5 agent-side verification.

Plan.md M2.5.1 step 6 instructs the operator to capture the UI-issued
parent token into ``$env:PARENT_TOKEN`` for later verify (agent) calls.
When an agent (rather than the operator) is running those calls, each
subprocess gets a fresh shell — the operator's env var is not visible —
so the agent needs to source a token a different way.

This helper mints a parent-scope token via the same ``issue_token``
codepath the production PIN-login flow uses. Functionally identical for
all M2.5 verify (agent) blocks (which only need a valid parent bearer
for read-only API calls).

Usage (PowerShell):

    uv run python scripts/uat/mint_parent_token.py > data/.uat-backups/parent_token.txt

Default TTL is 4 hours (covers a full M2.5 pass with margin).
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from toybox.core.auth import TokenScope, issue_token
from toybox.db import connect, resolve_db_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a parent-scope token for M2.5.")
    parser.add_argument("--ttl-minutes", type=int, default=240, help="Token lifetime (default: 240).")
    args = parser.parse_args(argv)

    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        issued = issue_token(conn, TokenScope.parent, ttl=timedelta(minutes=args.ttl_minutes))
    finally:
        conn.close()

    sys.stdout.write(issued.token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
