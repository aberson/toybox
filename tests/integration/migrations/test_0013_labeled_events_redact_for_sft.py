"""Coverage for Phase E3 Step 1 migration 0013 (``redact_for_sft`` opt-out).

Pins:

* Fresh DB → ``labeled_events.redact_for_sft`` column exists with
  ``INTEGER NOT NULL DEFAULT 0``.
* Mid-stream DB (already at 0012) → applying 0013 backfills existing
  rows with the default ``0``.
* Migration is idempotent (re-running is a no-op; ``schema_migrations``
  has exactly one row for version 13).
* :func:`toybox.ai.labeled_events.count_rows` with ``sft_filter=True``
  excludes rows flagged ``redact_for_sft = 1``; with ``sft_filter=False``
  it counts them.
* ``--sft-filter`` CLI help text is pinned character-for-character to
  the documented string (em-dash included).
* The 0013 SQL file has NO own ``BEGIN``/``COMMIT``/``ROLLBACK`` — the
  migration runner wraps every file in a transaction; nested ones raise.
* All three forward-compat TODO comments referencing
  ``redact_for_sft lands`` are removed from ``labeled_events.py``.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.ai.labeled_events import count_rows
from toybox.db.connection import connect
from toybox.db.migrations import (
    Migration,
    current_version,
    discover_migrations,
    run_migrations,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_pre_0013(tmp_path: Path) -> Path:
    """Apply migrations 1..12 to a fresh DB and return its path.

    Stages the pre-0013 .sql files into a private directory so the
    runner sees a frozen "before" snapshot, then we drive 0013 in via
    the real package directory in the caller.
    """
    pre_dir = tmp_path / "pre_0013_migrations"
    pre_dir.mkdir()
    available = discover_migrations()
    pre: list[Migration] = [m for m in available if m.version <= 12]
    for m in pre:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 12
    finally:
        conn.close()
    return db_path


def _migration_0013_path() -> Path:
    """Return the absolute path to the 0013 .sql file (may not exist yet)."""
    from toybox.db import migrations as migrations_pkg

    return Path(migrations_pkg.__file__).resolve().parent / "0013_labeled_events_redact_for_sft.sql"


def _labeled_events_module_path() -> Path:
    """Return the absolute path to the labeled_events.py source file."""
    from toybox.ai import labeled_events as le

    return Path(le.__file__).resolve()


@pytest.fixture
def fresh_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Connection where every migration (1..N) has run on a fresh DB."""
    c = connect(tmp_path / "toybox.db")
    try:
        run_migrations(c)
        yield c
    finally:
        c.close()


def _insert_labeled_event(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    generator_path: str = "offline",
    generated_at: str = "2026-05-13T00:00:00Z",
    parent_signal: float | None = 1.0,
    judge_scores_json: str | None = (
        '{"safety": 5, "schema": 4, "age_appropriateness": 4,'
        ' "doability": 4, "persona_fidelity": 4, "coherence": 4}'
    ),
    redact_for_sft: int = 0,
) -> None:
    """Raw INSERT helper for seeding labeled_events rows in tests.

    Defaults satisfy every SFT-filter clause: ``parent_signal=1.0`` (not
    -1), judge scores present with ``safety=5`` (>=4) and rubric sum
    ``4*5+5 = 25`` (>= 18).
    """
    with conn:
        conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, "
            " inputs_chatml_json, activity_json, "
            " parent_signal, judge_scores_json, redact_for_sft) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                activity_id,
                generated_at,
                generator_path,
                "[]",
                f'{{"id":"{activity_id}"}}',
                parent_signal,
                judge_scores_json,
                redact_for_sft,
            ),
        )


# ---------------------------------------------------------------------------
# 1. Fresh-DB column shape
# ---------------------------------------------------------------------------


def test_0013_adds_redact_for_sft_column(fresh_conn: sqlite3.Connection) -> None:
    """Fresh DB → ``redact_for_sft`` column is INTEGER NOT NULL DEFAULT 0."""
    cols = {row["name"]: row for row in fresh_conn.execute("PRAGMA table_info(labeled_events)")}
    assert "redact_for_sft" in cols, list(cols)
    col = cols["redact_for_sft"]
    # SQLite affinity for the declared INTEGER type → "INTEGER".
    assert col["type"].upper() == "INTEGER", col["type"]
    # NOT NULL must be enforced (notnull=1).
    assert col["notnull"] == 1, col["notnull"]
    # DEFAULT 0 — PRAGMA returns it as a string "0".
    assert str(col["dflt_value"]) == "0", col["dflt_value"]


# ---------------------------------------------------------------------------
# 2. Forward-apply on a populated pre-0013 DB
# ---------------------------------------------------------------------------


def test_0013_backfills_existing_rows_with_default_zero(tmp_path: Path) -> None:
    """A row inserted at version 12 has ``redact_for_sft = 0`` after 0013."""
    db_path = _apply_pre_0013(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO labeled_events "
                "(activity_id, generated_at, generator_path, "
                " inputs_chatml_json, activity_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "a-pre-0013",
                    "2026-05-12T15:00:00Z",
                    "offline",
                    "[]",
                    '{"id":"a-pre-0013"}',
                ),
            )

        applied = run_migrations(conn)
        assert any(m.version == 13 for m in applied)

        row = conn.execute(
            "SELECT activity_id, redact_for_sft FROM labeled_events WHERE activity_id = ?",
            ("a-pre-0013",),
        ).fetchone()
        assert row is not None
        # The DEFAULT 0 must have been backfilled into the existing row.
        assert row["redact_for_sft"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Idempotency
# ---------------------------------------------------------------------------


def test_0013_is_idempotent(fresh_conn: sqlite3.Connection) -> None:
    """Re-running the runner is a no-op; schema_migrations has one row for v13."""
    starting = current_version(fresh_conn)
    second = run_migrations(fresh_conn)
    assert second == []
    assert current_version(fresh_conn) == starting

    # Exactly one schema_migrations entry for version 13 — not two.
    count_row = fresh_conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version = ?",
        (13,),
    ).fetchone()
    assert count_row is not None
    assert count_row["n"] == 1

    # The column survives the no-op re-run.
    cols = {row["name"]: row for row in fresh_conn.execute("PRAGMA table_info(labeled_events)")}
    assert "redact_for_sft" in cols


# ---------------------------------------------------------------------------
# 4. count_rows(sft_filter=True) respects the new column
# ---------------------------------------------------------------------------


def test_count_rows_sft_filter_excludes_redacted(fresh_conn: sqlite3.Connection) -> None:
    """Two otherwise-SFT-eligible rows; one flagged → filter counts 1, not 2.

    Without the column-aware filter clause, this assertion fails: the
    pre-0013 ``count_rows`` ignores ``redact_for_sft`` entirely and
    returns 2 even when one row is flagged for opt-out.
    """
    _insert_labeled_event(fresh_conn, activity_id="ok", redact_for_sft=0)
    _insert_labeled_event(fresh_conn, activity_id="opt-out", redact_for_sft=1)

    # Without the SFT filter, both rows count.
    assert count_rows(fresh_conn, sft_filter=False) == 2
    # With the SFT filter, the opt-out row is excluded.
    assert count_rows(fresh_conn, sft_filter=True) == 1


# ---------------------------------------------------------------------------
# 5. CLI --sft-filter help text is pinned exactly
# ---------------------------------------------------------------------------


# The exact (post-Step-1) help string for --sft-filter, per the plan.
# Note the em-dash (—, U+2014).
_EXPECTED_SFT_FILTER_HELP = (
    "Apply the SFT-export filter (parent_signal != -1 AND, when judge scores "
    "are present, safety >= 4 AND mean_quality >= 3.6 — i.e. strictly above "
    "3.5; the rubric scores are 1..5 ints, so the sum of the five rubric "
    "fields is gated at >= 18; AND redact_for_sft = 0)."
)


def test_cli_sft_filter_help_text_exact() -> None:
    """``python -m toybox.ai.labeled_events --help`` contains the pinned string.

    Invoked via subprocess so we exercise the real argparse rendering
    (argparse wraps long help strings; we normalize whitespace before
    asserting containment). PYTHONIOENCODING=utf-8 is set so the em-dash
    in the help string round-trips through Windows cp1252 cleanly.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "toybox.ai.labeled_events", "--help"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    # argparse will line-wrap long help text and prefix continuation
    # lines with whitespace. Collapse all runs of whitespace to a single
    # space so the comparison is over the underlying string content.
    normalized_stdout = re.sub(r"\s+", " ", result.stdout)
    normalized_expected = re.sub(r"\s+", " ", _EXPECTED_SFT_FILTER_HELP)
    assert normalized_expected in normalized_stdout, (
        f"expected substring not found.\n"
        f"expected (normalized): {normalized_expected!r}\n"
        f"got stdout (normalized): {normalized_stdout!r}"
    )


# ---------------------------------------------------------------------------
# 6. Migration file owns no transactions
# ---------------------------------------------------------------------------


def test_0013_sql_file_has_no_own_transaction() -> None:
    """0013.sql must not contain ``begin``/``commit``/``rollback`` keywords.

    The runner wraps every migration file in a BEGIN/COMMIT; nested
    transactions raise. We strip SQL line-comments first so a comment
    mentioning the word doesn't false-positive, then match each keyword
    on word boundaries.
    """
    sql_path = _migration_0013_path()
    assert sql_path.exists(), f"missing migration file: {sql_path}"

    raw = sql_path.read_text(encoding="utf-8")
    # Drop SQL line-comments (anything from -- to end-of-line).
    stripped_lines = []
    for line in raw.splitlines():
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        stripped_lines.append(line)
    cleaned = "\n".join(stripped_lines).lower()

    for keyword in ("begin", "commit", "rollback"):
        assert re.search(rf"\b{keyword}\b", cleaned) is None, (
            f"migration 0013 must not contain its own {keyword!r} statement; "
            f"the runner already wraps every file in a transaction."
        )


# ---------------------------------------------------------------------------
# 7. All three stale TODO comments are removed
# ---------------------------------------------------------------------------


def test_labeled_events_module_stale_todos_removed() -> None:
    """All three forward-compat TODO comments are gone from labeled_events.py.

    The three stale TODOs at L504-L507, L569-L571, L601-L603 all
    reference the (now-stale) migration number ``0005`` and Step 27 as
    a future task. After Step 1 lands, ``redact_for_sft`` IS the column
    (added by migration 0013), so:

    * ``"migration 0005"`` must not appear anywhere in the module — that
      string was the unique marker shared by the three stale TODOs.
    * The "Step 27 work should extend" phrasing must be gone — the
      extension landed, it isn't future work anymore.
    * The "is not yet supported" CLI help fragment must be gone — the
      flag now does enforce ``redact_for_sft = 0``.
    """
    src = _labeled_events_module_path().read_text(encoding="utf-8")

    assert "migration 0005" not in src, (
        "stale reference to 'migration 0005' (the column now lands via "
        "migration 0013); all three forward-compat TODOs must be removed."
    )
    assert "Future Step 27 work" not in src, (
        "stale 'Future Step 27 work should extend this filter' TODO must "
        "be removed; the extension landed with this Step."
    )
    assert "is not yet supported" not in src, (
        "stale '--sft-filter ... redact_for_sft is not yet supported' "
        "fragment in argparse help must be removed."
    )
