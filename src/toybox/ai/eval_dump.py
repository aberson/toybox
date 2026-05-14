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

``--sft-export`` bundles the SFT-quality filter with PII redaction and
the ``redact_for_sft = 0`` predicate (the SQL-level operator opt-out
added by migration 0013). Under ``--sft-export``:

* The ``redact_for_sft = 0`` predicate is added to the SQL ``WHERE``
  clause of :func:`fetch_rows` (NOT an in-memory post-filter — keeps
  excluded rows from ever hitting Python).
* ``child_names`` are sourced ONCE per CLI invocation via
  ``SELECT display_name FROM children``; the result is cached and
  passed into the row stream.
* The user-message content of ``inputs_chatml_json`` (the message at
  index ``>= 1``; the system message at index 0 is template text and
  stays untouched) is scrubbed via :func:`toybox.ai.redact.redact_pii`.
* The full ``activity_json`` content is scrubbed the same way.
* Metadata fields stay verbatim (no PII in metadata — they're
  operational signals).
* ``metadata.pii_filter_version`` is added to the per-row envelope.
* When the ``children`` table is empty, a single startup WARNING
  fires on stderr; regex scrubs still run.

``--sft-export`` is mutually exclusive with ``--all`` — argparse raises
the standard parse error.
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
from typing import Any, Final

from ..db import resolve_db_path
from ..db.connection import connect
from .redact import PII_FILTER_VERSION, redact_pii
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

EMPTY_CHILDREN_WARNING: Final[str] = (
    "PII redaction running with no child-name deny-list; only regex scrubs apply"
)


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
        raise ValueError(f"--since {raw!r} is not a valid ISO8601 timestamp ({exc})") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed_utc = parsed.astimezone(UTC)
    return parsed_utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="toybox.ai.eval_dump",
        description=(
            "Export labeled_events as ChatML JSONL — the SFT input format for Phase E iterations."
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
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--all",
        action="store_true",
        help="Skip the Phase E SFT-quality filter; dump every row matching --since.",
    )
    mode_group.add_argument(
        "--sft-export",
        action="store_true",
        help=(
            "Apply the SFT-export pipeline: existing quality filter + "
            "redact_for_sft=0 (SQL-level) + PII redaction (child names "
            "from children.display_name; emails, phones, addresses by "
            "regex). Metadata fields are NOT scrubbed. Emits "
            "pii_filter_version into per-row metadata. Mutually "
            "exclusive with --all."
        ),
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


def _fetch_child_names(conn: sqlite3.Connection) -> list[str]:
    """Return all ``children.display_name`` values, in insertion order.

    Called ONCE per CLI invocation (the result is cached in a local
    variable and passed into the row stream). Returns an empty list
    when the ``children`` table is empty — callers are responsible for
    emitting the operator warning in that case.
    """
    cursor = conn.execute("SELECT display_name FROM children")
    return [row["display_name"] for row in cursor]


def _build_fetch_query(
    *,
    generator_path: str | None,
    sft_export: bool,
) -> tuple[str, tuple[str, ...]]:
    """Build the SQL + parameter tuple for :func:`fetch_rows`.

    Separated so tests can introspect the WHERE clause without spinning
    up a DB. The parameter tuple is positional — the caller prepends
    ``(since,)`` (always present) before passing to :meth:`execute`.

    When ``sft_export`` is True, the ``redact_for_sft = 0`` predicate
    is added to the SQL WHERE clause (NOT an in-memory post-filter) so
    operator-flagged rows never hit Python.
    """
    clauses: list[str] = ["generated_at >= ?"]
    extra_params: list[str] = []
    if generator_path is not None:
        clauses.append("generator_path = ?")
        extra_params.append(generator_path)
    if sft_export:
        clauses.append("redact_for_sft = 0")
    sql = (
        "SELECT * FROM labeled_events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY generated_at ASC, id ASC"
    )
    return sql, tuple(extra_params)


def _redact_chatml(chatml: list[dict[str, str]], *, child_names: Sequence[str]) -> None:
    """In-place: scrub user-message content (index >= 1) of a ChatML list.

    The system message at index 0 is persona/rubric template text and
    stays untouched per the carve-out spec. Mutates ``chatml`` so the
    caller's serialization picks up the scrubbed values.
    """
    for idx, message in enumerate(chatml):
        if idx == 0:
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = redact_pii(content, child_names=child_names)


def _row_to_jsonl(
    row: sqlite3.Row,
    *,
    sft_export: bool = False,
    child_names: Sequence[str] = (),
) -> dict[str, Any]:
    """Convert one labeled_events row to the ChatML JSONL shape.

    When ``sft_export`` is True:

    * The user-message content of the ChatML list (index >= 1) is
      scrubbed via :func:`toybox.ai.redact.redact_pii`; the system
      message at index 0 is left verbatim.
    * The ``activity_json`` content is scrubbed as a single string
      (cheaper + simpler than parse-redact-reserialize, and matches
      what the assistant turn carries verbatim).
    * ``metadata.pii_filter_version`` is added to the envelope.

    Malformed ``inputs_chatml_json`` raises :class:`json.JSONDecodeError`
    to the caller — data corruption deserves loud failure; we do NOT
    add a silent skip-on-decode-error path.
    """
    chatml: list[dict[str, str]] = json.loads(row["inputs_chatml_json"])
    activity_text = row["activity_json"]
    if sft_export:
        _redact_chatml(chatml, child_names=child_names)
        activity_text = redact_pii(activity_text, child_names=child_names)
    chatml.append({"role": "assistant", "content": activity_text})

    judge_scores: dict[str, Any] | None = None
    raw_scores = row["judge_scores_json"]
    if raw_scores is not None:
        try:
            judge_scores = json.loads(raw_scores)
        except json.JSONDecodeError:
            judge_scores = None

    metadata: dict[str, Any] = {
        "activity_id": row["activity_id"],
        "generated_at": row["generated_at"],
        "generator_path": row["generator_path"],
        "parent_signal": row["parent_signal"],
        "parent_signal_set_at": row["parent_signal_set_at"],
        "ended_at_step": row["ended_at_step"],
        "judge_scores": judge_scores,
        "judge_run_at": row["judge_run_at"],
    }
    if sft_export:
        metadata["pii_filter_version"] = PII_FILTER_VERSION
    return {"messages": chatml, "metadata": metadata}


def fetch_rows(
    conn: sqlite3.Connection,
    *,
    since: str,
    generator_path: str | None,
    sft_export: bool = False,
) -> Iterator[sqlite3.Row]:
    """Iterate labeled_events rows since ``since`` (ISO8601), oldest first.

    Yields one :class:`sqlite3.Row` at a time. The cursor is iterated
    lazily — memory usage is O(1) regardless of result-set size, which
    matters on long-running deployments where ``--since`` may match
    millions of rows. Callers that need a list can wrap the result in
    ``list(...)``; the streaming path is the default.

    When ``sft_export`` is True, the SQL ``WHERE`` clause includes
    ``redact_for_sft = 0`` so operator-flagged rows are excluded at
    the SQL level (NOT an in-memory post-filter).
    """
    sql, extra_params = _build_fetch_query(generator_path=generator_path, sft_export=sft_export)
    cursor = conn.execute(sql, (since, *extra_params))
    yield from cursor


def stream_export(
    conn: sqlite3.Connection,
    *,
    since: str,
    generator_path: str | None,
    apply_sft_filter: bool,
    safety_floor: int,
    mean_quality_floor: float,
    sft_export: bool = False,
    child_names: Sequence[str] = (),
) -> Iterator[dict[str, Any]]:
    """Yield JSONL-shaped dicts for each row that passes the filter.

    The underlying ``fetch_rows`` iterator is lazy, so this whole
    pipeline is O(1) memory — only one row + its decoded JSON
    representation is in memory at a time.

    When ``sft_export`` is True the SQL fetch already excludes
    ``redact_for_sft = 1`` rows AND the in-memory quality filter is
    still applied (so this layer covers BOTH the operator opt-out
    AND the judge-score quality gate). Per-row PII redaction happens
    inside :func:`_row_to_jsonl`.
    """
    for row in fetch_rows(
        conn,
        since=since,
        generator_path=generator_path,
        sft_export=sft_export,
    ):
        if apply_sft_filter and not _row_passes_filter(
            row,
            safety_floor=safety_floor,
            mean_quality_floor=mean_quality_floor,
        ):
            continue
        yield _row_to_jsonl(row, sft_export=sft_export, child_names=child_names)


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
        child_names: list[str] = []
        if args.sft_export:
            child_names = _fetch_child_names(conn)
            if not child_names:
                print(
                    f"toybox.ai.eval_dump: WARNING {EMPTY_CHILDREN_WARNING}",
                    file=sys.stderr,
                )
        stream = stream_export(
            conn,
            since=since,
            generator_path=args.generator_path,
            apply_sft_filter=not args.all,
            safety_floor=args.safety_floor,
            mean_quality_floor=args.mean_quality_floor,
            sft_export=args.sft_export,
            child_names=child_names,
        )
        if args.out is None:
            count = write_jsonl(stream, sys.stdout)
        else:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("w", encoding="utf-8") as fh:
                count = write_jsonl(stream, fh)
    finally:
        conn.close()

    summary = (
        f"toybox.ai.eval_dump: exported {count} row(s) "
        f"(since={since}, all={args.all}, sft_export={args.sft_export}"
    )
    if args.sft_export:
        summary += f", pii_filter_version={PII_FILTER_VERSION}"
    summary += ")"
    print(summary, file=sys.stderr)
    return 0


__all__ = [
    "DEFAULT_MEAN_QUALITY_FLOOR",
    "DEFAULT_SAFETY_FLOOR",
    "DIMENSION_KEYS",
    "EMPTY_CHILDREN_WARNING",
    "QUALITY_DIMENSION_KEYS",
    "fetch_rows",
    "main",
    "stream_export",
    "write_jsonl",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
