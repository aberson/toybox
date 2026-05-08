"""Bridge ``~/.claude/.credentials.json`` to ``~/.toybox/secrets.json``.

The Claude CLI persists OAuth tokens at ``~/.claude/.credentials.json``
in its own shape (camelCase keys, ``expiresAt`` in milliseconds). Toybox
reads its OAuth token from ``~/.toybox/secrets.json`` in a different
shape (snake_case keys, ``expires_at`` in seconds since epoch).

The original Manual M1 setup ("Claude-CLI-creds bridge") was an
undocumented manual PowerShell ritual; tokens rotate roughly daily so
re-bridging happens often during active development. This helper
performs the conversion in one command, using ``toybox.ai.oauth``'s
existing ``save_token`` so the on-disk shape and atomic-write
guarantees match the rest of the codebase.

Usage::

    uv run python scripts/uat/bridge_claude_creds.py

Optional flags::

    --src PATH    override claude creds path (default: ~/.claude/.credentials.json)
    --dst PATH    override toybox secrets path (default: ~/.toybox/secrets.json,
                  honors TOYBOX_SECRETS_PATH env var)
    --dry-run     read + parse + transform but do not write; prints
                  the resulting expires_at delta-from-now in seconds.

Exit codes:
    0 — wrote (or would write, in --dry-run); destination is valid.
    1 — source missing, malformed, or claudeAiOauth subobject absent.
    2 — IO error writing destination.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from toybox.ai.oauth import OAuthToken, save_token, secrets_path


def _read_cli_creds(src: Path) -> dict[str, object]:
    if not src.is_file():
        print(f"claude CLI creds not found at {src}", file=sys.stderr)
        sys.exit(1)
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"could not read {src}: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(raw, dict) or "claudeAiOauth" not in raw:
        print(f"{src} is missing the claudeAiOauth subobject", file=sys.stderr)
        sys.exit(1)
    inner = raw["claudeAiOauth"]
    if not isinstance(inner, dict):
        print(f"{src}.claudeAiOauth is not an object", file=sys.stderr)
        sys.exit(1)
    for required in ("accessToken", "refreshToken", "expiresAt"):
        if required not in inner:
            print(f"{src}.claudeAiOauth missing key {required}", file=sys.stderr)
            sys.exit(1)
    return inner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--src", type=Path, default=Path.home() / ".claude" / ".credentials.json")
    parser.add_argument("--dst", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cli = _read_cli_creds(args.src)
    token = OAuthToken(
        access_token=str(cli["accessToken"]),
        refresh_token=str(cli["refreshToken"]),
        # CLI stores ms since epoch; toybox stores seconds.
        expires_at=int(cli["expiresAt"]) // 1000,
    )
    delta_h = round((token.expires_at - int(time.time())) / 3600, 2)

    dst = args.dst if args.dst is not None else secrets_path()
    if args.dry_run:
        print(f"DRY RUN: would write {dst}")
        print(f"  expires_at={token.expires_at} (delta_h={delta_h})")
        return 0

    try:
        save_token(token, path=dst)
    except OSError as exc:
        print(f"could not write {dst}: {exc}", file=sys.stderr)
        return 2

    print(f"bridged -> {dst} (delta_h={delta_h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
