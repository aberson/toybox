"""Uvicorn entrypoint.

Run as ``python -m toybox.main`` (or ``uv run python -m toybox.main``).

CLI flags:

* ``--host``     bind host (default ``127.0.0.1``; honors ``TOYBOX_HOST``)
* ``--port``     bind port (default ``8000``; honors ``TOYBOX_PORT``)
* ``--check``    run startup validation and exit 0 without starting uvicorn

The LAN-bind guard runs unconditionally before uvicorn starts so a
misconfigured ``TOYBOX_HOST=0.0.0.0`` exits non-zero with the documented
``code=lan_bind_requires_pin`` error code rather than silently exposing
the API.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from .app import create_app
from .core.bind_guard import BindGuardError, check_bind_safe

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="toybox.main", description="Toybox backend entrypoint.")
    parser.add_argument(
        "--host",
        default=os.environ.get("TOYBOX_HOST", DEFAULT_HOST),
        help="Bind host (default: 127.0.0.1; env TOYBOX_HOST).",
    )
    # TODO(phase-a-step-4): catch ValueError from int() and emit code=invalid_port_env
    # via the settings store. Phase A Step 1 scope = scaffold; settings store ships in Step 4.
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TOYBOX_PORT", str(DEFAULT_PORT))),
        help="Bind port (default: 8000; env TOYBOX_PORT).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run startup validation and exit 0 without starting uvicorn.",
    )
    return parser.parse_args(argv)


def _pin_is_set() -> bool:
    """Phase A: there is no PIN yet. Step 4+ wires this to the settings store."""
    return False


def main(argv: Sequence[str] | None = None) -> int:
    """Run startup validation and (unless ``--check``) start uvicorn.

    Returns the integer exit code. ``--check`` returns 0 on success;
    a guard failure prints the error to stderr and returns ``1``.
    """
    args = _parse_args(argv)

    try:
        check_bind_safe(args.host, pin_set=_pin_is_set())
    except BindGuardError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Build the app eagerly so import-time errors fail --check too.
    app = create_app()

    if args.check:
        print(f"toybox: --check ok (host={args.host} port={args.port})")
        return 0

    # Imported lazily so unit tests don't need uvicorn loaded.
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
