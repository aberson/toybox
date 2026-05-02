"""``python -m toybox.ai.client --check`` CLI entrypoint.

Prints a one-shot diagnostic of token + capability state for the M1
manual-setup step in ``documentation/plan.md``. Output is plain text,
one fact per line, designed to be eyeballed not parsed — the
``claude_capable=<True|False>`` line is the canonical happy/sad signal
the plan references.

Run as either:

* ``python -m toybox.ai`` — package-level ``__main__``.
* ``python -m toybox.ai.client --check`` — alias documented in the plan;
  see ``--check`` flag handling below.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence

from .breaker import CircuitBreaker
from .capability import is_capable
from .oauth import load_token, secrets_path


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="toybox.ai",
        description="Print Claude OAuth + capability diagnostics, then exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run the diagnostic (default behavior; flag exists for plan parity).",
    )
    return parser.parse_args(argv)


async def _run() -> int:
    breaker = CircuitBreaker()
    capable, reason = await is_capable(breaker)

    token = load_token()
    print(f"secrets_path={secrets_path()}")
    if token is None:
        print("token_present=False")
        print("token_expires_at=None")
        print("token_expired=N/A")
    else:
        now = int(time.time())
        print("token_present=True")
        print(f"token_expires_at={token.expires_at}")
        print(f"token_expired={token.is_expired(now)}")
    print(f"breaker_state={breaker.state.value}")
    print(f"claude_capable={capable}")
    if not capable:
        print(f"capability_reason={reason.value if reason else 'unknown'}")
    return 0 if capable else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns exit code (0 if capable, 1 otherwise)."""
    _parse_args(argv)
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
