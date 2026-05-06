"""Mint a child-scope ws token for Manual M2.5.

Step M2.5.5 verifies that ``trigger_phrase`` is stripped from envelopes
published on the child WebSocket boundary. ``_emit_state`` strips
parent-only fields *per recipient scope* — so a parent-scope subscriber
sees the unstripped envelope by design. Proving the strip needs a
child-scope token, which the parent kiosk issues via pairing flows in
production but isn't available as a CLI surface.

This helper writes a child token directly into ``auth_tokens`` via
``core.auth.issue_token`` so the UAT can subscribe with child scope and
assert the strip.

Usage (PowerShell):

    $env:WS_TOKEN = (uv run python scripts/uat/mint_child_token.py)
    uv run python scripts/uat/ws_inspect.py --topic activity.state --duration 5 --filter trigger_phrase

Default TTL is 1 hour (more than enough for a UAT pass). Pass
``--label`` to record a human-friendly label on the token row.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from toybox.core.auth import TokenScope, issue_token
from toybox.db import connect, resolve_db_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a child-scope token for M2.5.")
    parser.add_argument(
        "--label",
        default="m2-5-uat",
        help="child_session_label persisted with the token (default: m2-5-uat)",
    )
    parser.add_argument(
        "--ttl-minutes",
        type=int,
        default=60,
        help="Token lifetime in minutes (default: 60)",
    )
    args = parser.parse_args(argv)

    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        issued = issue_token(
            conn,
            TokenScope.child,
            child_session_label=args.label,
            ttl=timedelta(minutes=args.ttl_minutes),
        )
    finally:
        conn.close()

    sys.stdout.write(issued.token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
