"""Unit coverage for :mod:`toybox.core.qa_grading`.

Two halves:

* The household dial quartet (get/set helpers + canonical set), mirroring
  the W1 ``test_parent_involvement`` shape.
* The PURE :func:`grade_answer` token-overlap grader — lenient vs strict
  thresholds, normalization, no-match, and empty edge cases.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.core.qa_grading import (
    DEFAULT,
    QA_GRADING_VALID,
    grade_answer,
)
from toybox.core.qa_grading import (
    get_qa_grading as get,
)
from toybox.core.qa_grading import (
    set_qa_grading as set,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a fresh, migrated connection; close on teardown (Windows-safe)."""
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dial quartet.
# ---------------------------------------------------------------------------


def test_get_default_when_row_missing(db: sqlite3.Connection) -> None:
    """Absent settings row -> silent fallback to the default ("off")."""
    db.execute("DELETE FROM settings WHERE key = 'qa_grading'")
    assert get(db) == DEFAULT
    assert DEFAULT == "off"


def test_get_returns_seeded_default(db: sqlite3.Connection) -> None:
    """Fresh migrated DB has the seed row at "off"."""
    assert get(db) == "off"


def test_get_corrupt_value_logs_warning(
    db: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Out-of-set stored value -> exactly one WARNING + fallback."""
    with db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("qa_grading", "extreme"),
        )
    with caplog.at_level(logging.WARNING, logger="toybox.core.qa_grading"):
        value = get(db)
    assert value == DEFAULT
    warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and rec.name == "toybox.core.qa_grading"
    ]
    assert len(warnings) == 1


def test_set_round_trips_every_value(db: sqlite3.Connection) -> None:
    """Every canonical value round-trips cleanly through set -> get."""
    for value in sorted(QA_GRADING_VALID):
        assert set(db, value) == value
        assert get(db) == value


def test_set_rejects_invalid_values(db: sqlite3.Connection) -> None:
    """Out-of-set strings raise ValueError with a value-bearing message."""
    for invalid in ["", "OFF", "extreme", "len", "5"]:
        with pytest.raises(ValueError, match=r"invalid qa grading"):
            set(db, invalid)


# ---------------------------------------------------------------------------
# grade_answer — strict.
# ---------------------------------------------------------------------------


def test_strict_exact_match() -> None:
    assert grade_answer("red", "red", "strict") is True


def test_strict_requires_all_expected_tokens() -> None:
    # Expected has two tokens; window only has one -> strict fails.
    assert grade_answer("blue", "blue whale", "strict") is False
    # All expected tokens present (extra window words are fine).
    assert grade_answer("a big blue whale swims", "blue whale", "strict") is True


def test_strict_normalizes_case_and_punctuation() -> None:
    assert grade_answer("RED!", "red", "strict") is True
    assert grade_answer("Blue, please.", "blue please", "strict") is True


# ---------------------------------------------------------------------------
# grade_answer — lenient.
# ---------------------------------------------------------------------------


def test_lenient_accepts_partial_overlap() -> None:
    # 1 of 2 expected tokens = 0.5 overlap, which meets the lenient
    # threshold but NOT the strict one.
    assert grade_answer("blue", "blue whale", "lenient") is True
    assert grade_answer("blue", "blue whale", "strict") is False


def test_lenient_below_threshold_fails() -> None:
    # 1 of 3 expected tokens = 0.33 overlap < 0.5 lenient threshold.
    assert grade_answer("red", "red green blue", "lenient") is False


# ---------------------------------------------------------------------------
# grade_answer — no-match + empty edges.
# ---------------------------------------------------------------------------


def test_no_overlap_fails() -> None:
    assert grade_answer("purple", "red", "lenient") is False
    assert grade_answer("purple", "red", "strict") is False


def test_empty_expected_returns_false() -> None:
    # Nothing to match against -> never confident.
    assert grade_answer("anything", "", "lenient") is False
    assert grade_answer("anything", "   ", "strict") is False
    assert grade_answer("anything", "!!!", "strict") is False


def test_empty_window_returns_false() -> None:
    assert grade_answer("", "red", "lenient") is False
    assert grade_answer("   ", "red", "strict") is False


def test_off_or_unknown_tolerance_returns_false() -> None:
    # "off" should never reach grade_answer (caller short-circuits), but
    # the function stays total: any non-{lenient,strict} tolerance is
    # treated as "no confident match".
    assert grade_answer("red", "red", "off") is False
    assert grade_answer("red", "red", "bogus") is False


def test_repeated_expected_token_not_double_counted() -> None:
    # "red red" has one UNIQUE expected token; a window with "red" is a
    # full match under strict (overlap 1.0), not 0.5.
    assert grade_answer("red", "red red", "strict") is True
