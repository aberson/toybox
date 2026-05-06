"""Subscribe to a toybox WebSocket topic and print envelopes inline.

Used by the Manual M2.5 UAT to verify ws-PII guarantees (e.g., that
``trigger_phrase`` is stripped from envelopes published on the child
topic). Run for a fixed duration, then exit with the count of matching /
filter-violating envelopes.

Usage:

    uv run python scripts/uat/ws_inspect.py \\
        --token <ws_token> \\
        --topic activity.state \\
        --duration 5 \\
        --filter trigger_phrase

Exit codes:
    0 — ran for the full duration; no envelopes contained the filter field.
    1 — at least one envelope contained the filter field (PII leak).
    2 — connection / auth failure.

Token: pass a valid ws token via ``--token`` or ``WS_TOKEN`` env var. The
parent UAT setup script captures one after the PIN-login step. For
child-scoped checks (the trigger_phrase strip lives on the child
boundary), issue a child token via the kiosk pairing endpoint or by
writing a short helper that mints one through ``core.auth.issue_token``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

try:
    import websockets
except ImportError as exc:
    print(f"websockets not importable: {exc}", file=sys.stderr)
    sys.exit(2)


def _payload_contains_field(envelope: dict[str, Any], field: str) -> bool:
    """Return True if ``field`` appears anywhere in the envelope payload."""

    def _walk(node: Any) -> bool:
        if isinstance(node, dict):
            if field in node:
                return True
            return any(_walk(v) for v in node.values())
        if isinstance(node, list):
            return any(_walk(item) for item in node)
        return False

    return _walk(envelope.get("payload", {}))


async def _run(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get("WS_TOKEN")
    if not token:
        print("no token: pass --token or set WS_TOKEN", file=sys.stderr)
        return 2

    topics = [t.strip() for t in args.topic.split(",") if t.strip()]
    headers = {"Origin": args.origin}

    matches = 0
    seen = 0

    try:
        async with websockets.connect(args.url, additional_headers=headers) as ws:
            await ws.send(json.dumps({"type": "auth", "token": token}))
            ready_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            ready = json.loads(ready_raw)
            if ready.get("type") != "ready":
                print(f"unexpected first frame: {ready}", file=sys.stderr)
                return 2
            allowed = set(ready.get("topics", []))
            requested = set(topics)
            missing = requested - allowed
            if missing:
                print(
                    f"requested topics not in scope: {sorted(missing)} "
                    f"(allowed: {sorted(allowed)})",
                    file=sys.stderr,
                )
                return 2

            await ws.send(json.dumps({"type": "subscribe", "topics": topics}))
            try:
                ack_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                ack = json.loads(ack_raw)
                if ack.get("type") != "subscribed":
                    print(f"unexpected ack frame: {ack}", file=sys.stderr)
            except asyncio.TimeoutError:
                pass

            print(
                f"listening on {topics} for {args.duration}s "
                f"(filter={args.filter or 'none'})",
                file=sys.stderr,
            )

            deadline = asyncio.get_event_loop().time() + args.duration
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue

                if frame.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                    continue

                if "topic" not in frame:
                    continue

                if frame["topic"] not in topics:
                    continue

                seen += 1
                hit = bool(args.filter) and _payload_contains_field(frame, args.filter)
                if hit:
                    matches += 1
                    marker = "LEAK"
                else:
                    marker = "ok"
                print(
                    f"[{marker}] topic={frame['topic']} "
                    f"payload={json.dumps(frame.get('payload', {}))[:200]}"
                )
    except (websockets.WebSocketException, OSError) as exc:
        print(f"ws error: {exc}", file=sys.stderr)
        return 2

    print(
        f"summary: seen={seen} matches={matches} filter={args.filter or 'none'}",
        file=sys.stderr,
    )
    return 1 if matches > 0 else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Subscribe to a toybox ws topic and inspect envelopes.",
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument(
        "--origin",
        default="http://127.0.0.1:4000",
        help="Origin header (must be in the backend allow-list)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="WS auth token; falls back to WS_TOKEN env var",
    )
    parser.add_argument(
        "--topic",
        required=True,
        help="Comma-separated topic names to subscribe to (e.g., activity.state)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=5.0,
        help="Seconds to listen before exiting",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help=(
            "Optional payload field name; exit 1 if any envelope's payload "
            "contains this field (e.g., trigger_phrase for PII checks)"
        ),
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
