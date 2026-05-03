"""``uv run python -m toybox.ai.eval_dump --since <ISO>`` CLI.

Exports ``labeled_events`` rows as ChatML JSONL — the canonical Phase E
SFT input format. Each output line is a complete fine-tuning example::

    {
      "messages": [
        {"role": "system", "content": "..."},
        {"role": "user",   "content": "..."},
        {"role": "assistant", "content": "<activity_json>"}
      ],
      "metadata": {
        "activity_id": "...",
        "generated_at": "...",
        "generator_path": "claude" | "offline" | "local",
        "parent_signal": -1 | -0.5 | 0 | 1 | null,
        "parent_signal_set_at": "..." | null,
        "ended_at_step": <int> | null,
        "judge_scores": {...} | null,
        "judge_run_at": "..." | null
      }
    }

Default behaviour matches the Phase E forward-compat query::

    safety >= 4 AND mean_quality >= 3.5 AND parent_signal != -1

Use ``--all`` to dump everything (audit / debug). The filter is applied
in-memory after the SQL fetch so the SQL stays simple — the row volume
is bounded by ``--since`` and is never huge.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..db import resolve_db_path
from ..db.connection import connect
from .rubric import (
    DIMENSION_KEYS,
    QUALITY_DIMENSION_KEYS,
    SAFETY_AUTOFAIL,
    InvalidRubricScoresError,
    from_mapping,
)

_logger = logging.getLogger(__name__)

DEFAULT_SAFETY_FLOOR = SAFETY_AUTOFAIL + 3  # >= 4
DEFAULT_MEAN_QUALITY_FLOOR = 3.5


def _normalize_since(raw: str) -> str:
    """Parse ``raw`` as an ISO8601 timestamp; return canonical ``...Z`` form.

    The ``labeled_events.generated_at`` column stores UTC timestamps as
    ``YYYY-MM-DDTHH:MM:SSZ``. SQLite compares these lexically, so a
    user passing ``--since 2026-05-03`` (date-only) would lex-compare
    weirdly against ``2026-05-03T00:00:00Z`` — a date string sorts
    BELOW the timestamp form, sometimes silently dropping rows. We
    parse via :func:`datetime.fromisoformat` (which accepts
    ``YYYY-MM-DD``) and re-serialize so the SQL bind is always
    canonical.

    Raises:
        ValueError: if ``raw`` is not a valid ISO8601 timestamp.
    """
    candidate = raw.strip()
    # ``fromisoformat`` doesn't accept the trailing ``Z`` until Python
    # 3.11; we strip it for compatibility and re-add it on the way out.
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            f"--since {raw!r} is not a valid ISO8601 timestamp ({exc})"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed_utc = parsed.astimezone(UTC)
    return parsed_utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="toybox.ai.eval_dump",
        description=(
            "Export labeled_events as ChatML JSONL — the SFT input "
            "format for Phase E iterations."
        ),
    )
    parser.add_argument(
        "--since",
        required=True,
        help="ISO8601 timestamp; only events with generated_at >= this are exported.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSONL path. Defaults to stdout.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite DB path (defaults to TOYBOX_DB_PATH or data/toybox.db).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Skip the Phase E SFT-quality filter; dump every row matching --since.",
    )
    parser.add_argument(
        "--generator-path",
        choices=("claude", "offline", "local"),
        default=None,
        help="Restrict to a single generator path.",
    )
    parser.add_argument(
        "--safety-floor",
        type=int,
        default=DEFAULT_SAFETY_FLOOR,
        help=f"Drop rows with safety < this. Default {DEFAULT_SAFETY_FLOOR}.",
    )
    parser.add_argument(
        "--mean-quality-floor",
        type=float,
        default=DEFAULT_MEAN_QUALITY_FLOOR,
        help=(
            "Drop rows with mean_quality < this. Default "
            f"{DEFAULT_MEAN_QUALITY_FLOOR} (matches Phase E SFT filter)."
        ),
    )
    return parser.parse_args(argv)


def _row_passes_filter(
    row: sqlite3.Row,
    *,
    safety_floor: int,
    mean_quality_floor: float,
) -> bool:
    """Apply the Phase E SFT filter: safety + mean_quality + parent_signal.

    A row with no judge_scores_json is skipped under the SFT filter — we
    can't certify quality without scores. Use ``--all`` to dump them.
    """
    parent_signal = row["parent_signal"]
    if parent_signal is not None and parent_signal == -1.0:
        return False
    raw_scores = row["judge_scores_json"]
    if raw_scores is None:
        return False
    try:
        payload = json.loads(raw_scores)
        scores = from_mapping(payload)
    except (json.JSONDecodeError, InvalidRubricScoresError):
        return False
    if scores.safety < safety_floor:
        return False
    if scores.mean_quality < mean_quality_floor:
        return False
    return True


def _row_to_jsonl(row: sqlite3.Row) -> dict[str, Any]:
    """Convert one labeled_events row to the ChatML JSONL shape."""
    chatml: list[dict[str, str]] = json.loads(row["inputs_chatml_json"])
    activity_text = row["activity_json"]
    chatml.append({"role": "assistant", "content": activity_text})

    judge_scores: dict[str, Any] | None = None
    raw_scores = row["judge_scores_json"]
    if raw_scores is not None:
        try:
            judge_scores = json.loads(raw_scores)
        except json.JSONDecodeError:
            judge_scores = None

    return {
        "messages": chatml,
        "metadata": {
            "activity_id": row["activity_id"],
            "generated_at": row["generated_at"],
            "generator_path": row["generator_path"],
            "parent_signal": row["parent_signal"],
            "parent_signal_set_at": row["parent_signal_set_at"],
            "ended_at_step": row["ended_at_step"],
            "judge_scores": judge_scores,
            "judge_run_at": row["judge_run_at"],
        },
    }


def fetch_rows(
    conn: sqlite3.Connection,
    *,
    since: str,
    generator_path: str | None,
) -> Iterator[sqlite3.Row]:
    """Iterate labeled_events rows since ``since`` (ISO8601), oldest first.

    Yields one :class:`sqlite3.Row` at a time. The cursor is iterated
    lazily — memory usage is O(1) regardless of result-set size, which
    matters on long-running deployments where ``--since`` may match
    millions of rows. Callers that need a list can wrap the result in
    ``list(...)``; the streaming path is the default.
    """
    if generator_path is None:
        cursor = conn.execute(
            "SELECT * FROM labeled_events "
            "WHERE generated_at >= ? "
            "ORDER BY generated_at ASC, id ASC",
            (since,),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM labeled_events "
            "WHERE generated_at >= ? AND generator_path = ? "
            "ORDER BY generated_at ASC, id ASC",
            (since, generator_path),
        )
    yield from cursor


def stream_export(
    conn: sqlite3.Connection,
    *,
    since: str,
    generator_path: str | None,
    apply_sft_filter: bool,
    safety_floor: int,
    mean_quality_floor: float,
) -> Iterator[dict[str, Any]]:
    """Yield JSONL-shaped dicts for each row that passes the filter.

    The underlying ``fetch_rows`` iterator is lazy, so this whole
    pipeline is O(1) memory — only one row + its decoded JSON
    representation is in memory at a time.
    """
    for row in fetch_rows(conn, since=since, generator_path=generator_path):
        if apply_sft_filter and not _row_passes_filter(
            row,
            safety_floor=safety_floor,
            mean_quality_floor=mean_quality_floor,
        ):
            continue
        yield _row_to_jsonl(row)


def write_jsonl(stream: Iterable[dict[str, Any]], out: Any) -> int:
    """Write JSONL lines to ``out`` (file-like with ``.write``). Returns count."""
    count = 0
    for record in stream:
        out.write(json.dumps(record, ensure_ascii=False))
        out.write("\n")
        count += 1
    return count


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        since = _normalize_since(args.since)
    except ValueError as exc:
        print(f"toybox.ai.eval_dump: {exc}", file=sys.stderr)
        return 2
    db_path = args.db if args.db is not None else resolve_db_path()
    if not db_path.is_file():
        print(f"toybox.ai.eval_dump: db not found at {db_path}", file=sys.stderr)
        return 2

    conn = connect(db_path)
    try:
        stream = stream_export(
            conn,
            since=since,
            generator_path=args.generator_path,
            apply_sft_filter=not args.all,
            safety_floor=args.safety_floor,
            mean_quality_floor=args.mean_quality_floor,
        )
        if args.out is None:
            count = write_jsonl(stream, sys.stdout)
        else:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("w", encoding="utf-8") as fh:
                count = write_jsonl(stream, fh)
    finally:
        conn.close()

    print(
        f"toybox.ai.eval_dump: exported {count} row(s) "
        f"(since={since}, all={args.all})",
        file=sys.stderr,
    )
    return 0


__all__ = [
    "DEFAULT_MEAN_QUALITY_FLOOR",
    "DEFAULT_SAFETY_FLOOR",
    "DIMENSION_KEYS",
    "QUALITY_DIMENSION_KEYS",
    "fetch_rows",
    "main",
    "stream_export",
    "write_jsonl",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
