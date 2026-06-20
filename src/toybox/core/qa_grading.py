"""Household-scoped Q&A answer-grading tolerance setting + offline grader.

Phase W Step W3 extends the Phase R R3 Q&A gate. R3 made a step's
``question`` block advance until the parent tapped "Good answer" / "Skip".
W3 adds an OPTIONAL auto-grade: when ``settings.qa_grading != "off"`` and
the current step carries both a ``question`` and an ``expected_answer``,
the advance handler reads the last ~30 seconds of transcript text and
grades it. A confident match auto-resolves the gate (reusing the R3
approve-question resolution path); anything else falls through to the
existing parent-tap 409.

This module owns two things:

1. The household dial quartet (``get_``/``set_`` helpers + the canonical
   set), mirroring :mod:`toybox.core.parent_involvement` (W1). Stored at
   ``settings.qa_grading`` (TEXT, one of ``{"off","lenient","strict"}``),
   defaulting to ``"off"`` — with grading off the R3 parent-tap flow is
   byte-identical to today.

2. :func:`grade_answer` — a PURE, deterministic, no-I/O token-overlap
   grader. It is both the offline grader (used when Claude is not
   capable) and the unit-testable core of the whole feature.
"""

from __future__ import annotations

import logging
import re
import sqlite3

_logger = logging.getLogger(__name__)


QA_GRADING_VALID: frozenset[str] = frozenset({"off", "lenient", "strict"})
DEFAULT: str = "off"

_SETTINGS_KEY = "qa_grading"

# ``lenient`` accepts a partial overlap — at least this fraction of the
# expected tokens must appear in the transcript window. ``strict`` demands
# every expected token be present (overlap == 1.0). Both thresholds are
# applied to the SAME ratio (matched_expected_tokens / total_expected_tokens)
# so the two tolerance levels are a single knob, not two code paths.
_LENIENT_THRESHOLD: float = 0.5
_STRICT_THRESHOLD: float = 1.0

# Tokenization: lowercase, then split on any run of non-alphanumeric
# characters. This strips punctuation ("red!" -> "red") and collapses
# whitespace, so "Red, please." and "red please" tokenize identically.
#
# CAVEAT — ASCII-only. The character class ``[^a-z0-9]`` treats every
# accented / non-Latin character as a SEPARATOR, so "café" tokenizes to
# ["caf"] and "ñoño" to []. Non-English or accented expected answers will
# therefore UNDER-MATCH in this offline grader (the answer may be graded
# "not confident" even when the kid said it correctly). This is an
# accepted limitation of the deterministic offline path: when Claude is
# online the capability-gated judge in ``api/activities._grade_via_claude``
# handles accented / non-English answers via meaning-based grading, and
# the offline grader is only the fallback. Widening this to a Unicode
# word-boundary split (``\w`` with ``re.UNICODE``) is the future fix if
# households need offline non-English grading.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def get_qa_grading(conn: sqlite3.Connection) -> str:
    """Return the persisted Q&A grading tolerance, defaulting to "off".

    Falls back to :data:`DEFAULT` in two cases (mirrors
    :func:`toybox.core.parent_involvement.get_parent_involvement`):

    1. The settings row is absent (legacy DBs that predate migration
       0026, or a deleted seed row).
    2. The stored value is not in :data:`QA_GRADING_VALID` (preset list
       shrunk, or a free-form value snuck in).

    Case 2 logs at WARNING with the offending value truncated to 64
    chars.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        return DEFAULT
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    if raw not in QA_GRADING_VALID:
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r outside canonical set; falling back to %r",
            _SETTINGS_KEY,
            truncated,
            DEFAULT,
        )
        return DEFAULT
    return str(raw)


def set_qa_grading(conn: sqlite3.Connection, value: str) -> str:
    """Persist ``value`` and return the canonical string.

    Raises :class:`ValueError` when ``value`` is not in
    :data:`QA_GRADING_VALID`. The API layer translates this into HTTP 422
    with the full canonical list in the error body.
    """
    if value not in QA_GRADING_VALID:
        raise ValueError(f"invalid qa grading: {value!r}")
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, value),
        )
    return value


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into non-empty tokens."""
    return [tok for tok in _TOKEN_SPLIT.split(text.lower()) if tok]


def grade_answer(transcript_window: str, expected: str, tolerance: str) -> bool:
    """Return True iff ``transcript_window`` is a confident answer match.

    PURE + deterministic + no I/O. This is the offline grader and the
    unit-testable core of the W3 auto-grade feature.

    Normalization: both sides are lowercased and stripped of punctuation,
    then split into tokens (see :func:`_tokenize`).

    Matching: compute the fraction of EXPECTED tokens that also appear in
    the transcript-window token set.

    * ``"strict"``  — every expected token must be present (overlap == 1.0).
    * ``"lenient"`` — at least :data:`_LENIENT_THRESHOLD` of the expected
      tokens must be present.
    * Any other tolerance (including ``"off"``) — returns False. ``"off"``
      should never reach here (the caller short-circuits), but treating it
      as "no confident match" keeps the function total.

    Edge cases:

    * Empty ``expected`` (no tokens) — returns False. There is nothing to
      match against, so we cannot be confident; fall through to parent tap.
    * Empty ``transcript_window`` (no tokens) — overlap is 0.0, so False
      unless ``expected`` is also empty (already handled above).
    """
    expected_tokens = _tokenize(expected)
    if not expected_tokens:
        return False

    threshold = (
        _LENIENT_THRESHOLD
        if tolerance == "lenient"
        else (_STRICT_THRESHOLD if tolerance == "strict" else None)
    )
    if threshold is None:
        return False

    window_tokens = set(_tokenize(transcript_window))
    if not window_tokens:
        return False

    # Use a unique-expected-token set for the ratio so a repeated expected
    # token ("red red") doesn't double-count and skew the fraction.
    unique_expected = set(expected_tokens)
    matched = sum(1 for tok in unique_expected if tok in window_tokens)
    overlap = matched / len(unique_expected)
    return overlap >= threshold


__all__ = [
    "DEFAULT",
    "QA_GRADING_VALID",
    "get_qa_grading",
    "grade_answer",
    "set_qa_grading",
]
