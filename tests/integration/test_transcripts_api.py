"""HTTP coverage for the Step 13 transcripts read-only API.

These tests share the per-test app + db fixtures from
``tests/integration/conftest.py`` (the same wiring activities and
listening-mode tests use). The transcripts API is not auth-gated in v1
— the parent UI runs on the LAN — matching the convention of
``/api/listening`` and ``/api/health``.

Phase I Step I2 added a retention-aware filter-on-read to both list and
search. Fixture timestamps must be fresh relative to wall-clock ``now``
so they sit inside the retention window (default 60s) — otherwise the
filter excludes them and the legacy assertions about row ordering would
flip. The :func:`_recent_iso` helper generates pipeline-format strings
``offset_seconds`` ago.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from toybox.core.transcript_retention import _format_ended_at_cutoff
from toybox.db.connection import connect

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _seed_session(db_path: Path, session_id: str = "s1") -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (session_id, "2026-01-01T00:00:00Z"),
            )
    finally:
        conn.close()


def _recent_iso(offset_seconds: float, *, base: datetime | None = None) -> str:
    """Format ``base - offset_seconds`` in the pipeline-matched ISO shape.

    Uses :func:`_format_ended_at_cutoff` so test fixtures emit the same
    byte sequence the production pipeline writes to ``ended_at`` — and
    so lexicographic comparison against the read filter's cutoff matches
    numeric comparison. Defaults ``base`` to ``datetime.now(UTC)``.
    """
    when = (base or datetime.now(UTC)) - timedelta(seconds=offset_seconds)
    return _format_ended_at_cutoff(when)


def _insert_transcripts(
    db_path: Path,
    rows: Iterable[tuple[str, str, str, str, float, str]],
    *,
    session_id: str = "s1",
) -> None:
    """Bulk-insert ``(id, text, started_at, ended_at, confidence, language)``."""
    conn = connect(db_path)
    try:
        with conn:
            for row_id, text, started, ended, confidence, language in rows:
                conn.execute(
                    "INSERT INTO transcripts "
                    "(id, session_id, mic_id, started_at, ended_at, text, "
                    " confidence, language) "
                    "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
                    (row_id, session_id, started, ended, text, confidence, language),
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------


def test_list_returns_recent_transcripts(client: TestClient, db_path: Path) -> None:
    _seed_session(db_path)
    base = datetime.now(UTC)
    # Three rows spaced 1s apart, all comfortably inside the default 60s
    # retention window. ``oldest`` is the largest offset so it sorts last
    # in the DESC-by-ended_at view.
    _insert_transcripts(
        db_path,
        [
            ("t-1", "first",  _recent_iso(8, base=base), _recent_iso(7, base=base), 0.7, "en"),
            ("t-2", "second", _recent_iso(6, base=base), _recent_iso(5, base=base), 0.8, "en"),
            ("t-3", "third",  _recent_iso(4, base=base), _recent_iso(3, base=base), 0.9, "en"),
        ],
    )

    response = client.get("/api/transcripts")
    assert response.status_code == 200, response.text
    payload = response.json()
    items = payload["items"]
    assert [r["text"] for r in items] == ["third", "second", "first"]
    assert items[0]["language"] == "en"
    assert items[0]["session_id"] == "s1"


def test_list_respects_limit(client: TestClient, db_path: Path) -> None:
    _seed_session(db_path)
    base = datetime.now(UTC)
    # 30 rows, each 1s newer than the previous; all sit inside the 60s
    # retention window. ``i=0`` is the oldest (offset=30s); ``i=29`` is
    # the newest (offset=1s). ``limit=10`` should return rows 29..20.
    rows = [
        (
            f"t-{i:03d}",
            f"row {i}",
            _recent_iso(31 - i, base=base),
            _recent_iso(30 - i, base=base),
            0.6,
            "en",
        )
        for i in range(30)
    ]
    _insert_transcripts(db_path, rows)

    response = client.get("/api/transcripts", params={"limit": 10})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 10
    # Most recent (highest i, smallest offset) first.
    assert items[0]["text"] == "row 29"
    assert items[-1]["text"] == "row 20"


def test_list_before_cursor_paginates(client: TestClient, db_path: Path) -> None:
    _seed_session(db_path)
    base = datetime.now(UTC)
    old_end = _recent_iso(10, base=base)
    mid_end = _recent_iso(6, base=base)
    new_end = _recent_iso(2, base=base)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "old",    _recent_iso(11, base=base), old_end, 0.7, "en"),
            ("t-2", "middle", _recent_iso(7,  base=base), mid_end, 0.7, "en"),
            ("t-3", "new",    _recent_iso(3,  base=base), new_end, 0.7, "en"),
        ],
    )

    response = client.get(
        "/api/transcripts",
        params={"before": new_end},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert [r["text"] for r in items] == ["middle", "old"]


def test_list_default_limit_is_50(client: TestClient, db_path: Path) -> None:
    _seed_session(db_path)
    base = datetime.now(UTC)
    # 55 rows packed into a 55s window so the default limit-50 path
    # produces a full page without spilling outside retention.
    rows = [
        (
            f"t-{i:03d}",
            f"row {i}",
            _recent_iso(56 - i, base=base),
            _recent_iso(55 - i, base=base),
            0.6,
            "en",
        )
        for i in range(55)
    ]
    _insert_transcripts(db_path, rows)
    response = client.get("/api/transcripts")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 50


def test_list_empty_table_returns_empty_list(client: TestClient) -> None:
    response = client.get("/api/transcripts")
    assert response.status_code == 200
    assert response.json() == {"items": []}


# ---------------------------------------------------------------------
# Phase I Step I2 — filter-on-read
# ---------------------------------------------------------------------


def test_list_excludes_rows_older_than_retention(
    client: TestClient,
    db_path: Path,
) -> None:
    """Default retention=60s — rows >60s old must NOT appear in the list.

    Inserts one fresh row + one row 600s past expiry (using
    pipeline-format ISO timestamps). Asserts only the fresh row comes
    back, proving the ``AND ended_at >= ?`` clause is wired correctly
    against the same cutoff format the sweep uses.
    """
    _seed_session(db_path)
    base = datetime.now(UTC)
    _insert_transcripts(
        db_path,
        [
            (
                "t-fresh",
                "still here",
                _recent_iso(11, base=base),
                _recent_iso(10, base=base),
                0.7,
                "en",
            ),
            (
                "t-expired",
                "gone",
                _recent_iso(601, base=base),
                _recent_iso(600, base=base),
                0.7,
                "en",
            ),
        ],
    )

    response = client.get("/api/transcripts")
    assert response.status_code == 200
    items = response.json()["items"]
    ids = [r["id"] for r in items]
    assert ids == ["t-fresh"]


def test_search_excludes_rows_older_than_retention(
    client: TestClient,
    db_path: Path,
) -> None:
    """Search must honour the same retention-aware filter as list.

    Both rows match the substring query ``"hello"``, but only the fresh
    one sits inside the 60s window; the 10-minute-old row is excluded
    by ``AND ended_at >= ?`` in the search SQL.
    """
    _seed_session(db_path)
    base = datetime.now(UTC)
    _insert_transcripts(
        db_path,
        [
            (
                "t-fresh",
                "hello fresh",
                _recent_iso(7, base=base),
                _recent_iso(6, base=base),
                0.7,
                "en",
            ),
            (
                "t-expired",
                "hello expired",
                _recent_iso(601, base=base),
                _recent_iso(600, base=base),
                0.7,
                "en",
            ),
        ],
    )

    response = client.get("/api/transcripts/search", params={"q": "hello"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert [r["id"] for r in items] == ["t-fresh"]


# ---------------------------------------------------------------------
# Search endpoint
# ---------------------------------------------------------------------


def test_search_substring_case_insensitive(
    client: TestClient,
    db_path: Path,
) -> None:
    _seed_session(db_path)
    base = datetime.now(UTC)
    _insert_transcripts(
        db_path,
        [
            (
                "t-1",
                "Hello World",
                _recent_iso(9, base=base),
                _recent_iso(8, base=base),
                0.7,
                "en",
            ),
            (
                "t-2",
                "goodbye there",
                _recent_iso(7, base=base),
                _recent_iso(6, base=base),
                0.7,
                "en",
            ),
            (
                "t-3",
                "say HELLO",
                _recent_iso(5, base=base),
                _recent_iso(4, base=base),
                0.7,
                "en",
            ),
        ],
    )

    response = client.get("/api/transcripts/search", params={"q": "hello"})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    texts = sorted(r["text"] for r in items)
    assert texts == ["Hello World", "say HELLO"]


def test_search_empty_query_rejected(client: TestClient) -> None:
    """``q`` is required + min_length=1 → a missing or empty query is 422."""
    missing = client.get("/api/transcripts/search")
    assert missing.status_code == 422
    empty = client.get("/api/transcripts/search", params={"q": ""})
    assert empty.status_code == 422


def test_search_whitespace_only_query_rejected(client: TestClient) -> None:
    response = client.get("/api/transcripts/search", params={"q": "   "})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "search_query_required"


def test_search_limit_enforced(client: TestClient, db_path: Path) -> None:
    _seed_session(db_path)
    base = datetime.now(UTC)
    # 30 rows in a 30s window — all fit inside default retention.
    rows = [
        (
            f"t-{i:03d}",
            f"unicorn {i}",
            _recent_iso(31 - i, base=base),
            _recent_iso(30 - i, base=base),
            0.6,
            "en",
        )
        for i in range(30)
    ]
    _insert_transcripts(db_path, rows)

    response = client.get(
        "/api/transcripts/search",
        params={"q": "unicorn", "limit": 5},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 5
    # Results are most-recent first.
    assert items[0]["text"] == "unicorn 29"


def test_search_no_match_returns_empty_list(
    client: TestClient,
    db_path: Path,
) -> None:
    _seed_session(db_path)
    base = datetime.now(UTC)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "Hello",   _recent_iso(5, base=base), _recent_iso(4, base=base), 0.7, "en"),
        ],
    )
    response = client.get("/api/transcripts/search", params={"q": "nonexistent"})
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_list_limit_out_of_range_422(client: TestClient) -> None:
    too_big = client.get("/api/transcripts", params={"limit": 9999})
    assert too_big.status_code == 422
    too_small = client.get("/api/transcripts", params={"limit": 0})
    assert too_small.status_code == 422


def test_search_limit_out_of_range_422(client: TestClient) -> None:
    response = client.get(
        "/api/transcripts/search",
        params={"q": "x", "limit": 9999},
    )
    assert response.status_code == 422


def test_search_returns_language_field(client: TestClient, db_path: Path) -> None:
    """Default ``unknown`` language round-trips through the API."""
    _seed_session(db_path)
    base = datetime.now(UTC)
    _insert_transcripts(
        db_path,
        [
            (
                "t-1",
                "muffled",
                _recent_iso(5, base=base),
                _recent_iso(4, base=base),
                0.4,
                "unknown",
            ),
        ],
    )
    response = client.get("/api/transcripts/search", params={"q": "muffled"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["language"] == "unknown"


@pytest.mark.parametrize("invalid_limit", ["abc", "-1"])
def test_list_invalid_limit_format_422(client: TestClient, invalid_limit: str) -> None:
    response = client.get("/api/transcripts", params={"limit": invalid_limit})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "bad_before",
    [
        "garbage",
        "2026-13-01T00:00:00Z",   # invalid month
        "not-a-date",
        "",                        # empty string slips past Query and fails fromisoformat
    ],
)
def test_list_invalid_before_cursor_returns_400(
    client: TestClient,
    bad_before: str,
) -> None:
    """Bad ``before`` cursors must hard-fail rather than silently
    returning the wrong window. We reject with 400 +
    ``invalid_before_cursor`` so the parent UI can surface a meaningful
    error instead of "why is my list empty".
    """
    response = client.get("/api/transcripts", params={"before": bad_before})
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["detail"]["code"] == "invalid_before_cursor"


def test_list_before_cursor_accepts_offset_form(
    client: TestClient,
    db_path: Path,
) -> None:
    """The cursor must accept the ``+00:00`` offset form in addition to
    the ``Z`` form we serialize, since the API takes a free-form ISO
    string and clients legitimately use either spelling.
    """
    _seed_session(db_path)
    base = datetime.now(UTC)
    new_end = _recent_iso(4, base=base)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "old", _recent_iso(9, base=base), _recent_iso(8, base=base), 0.7, "en"),
            ("t-2", "new", _recent_iso(5, base=base), new_end,                   0.7, "en"),
        ],
    )
    # Spell the cursor in ``+00:00`` form rather than ``Z`` to exercise
    # both supported shapes.
    before_offset = new_end.replace("Z", "+00:00")
    response = client.get(
        "/api/transcripts",
        params={"before": before_offset},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert [r["text"] for r in items] == ["old"]


@pytest.mark.parametrize(
    "tied_ids",
    [("t-a", "t-b"), ("t-b", "t-a")],
)
def test_list_pagination_ties_returned_across_pages(
    client: TestClient,
    db_path: Path,
    tied_ids: tuple[str, str],
) -> None:
    """Two rows with identical ``ended_at`` must be returned in a
    deterministic order (``id DESC`` tiebreak) and must both appear
    when paged across with ``limit=1`` + ``before=<ts>``.

    The cursor is timestamp-only for v1 — see the route docstring's
    note about identical-timestamp rows straddling a page boundary —
    so this test pins both the tiebreaker rule and the contract that
    no row vanishes during pagination.
    """
    _seed_session(db_path)
    base = datetime.now(UTC)
    tied_end = _recent_iso(8, base=base)
    newer_end = _recent_iso(3, base=base)
    _insert_transcripts(
        db_path,
        [
            (
                tied_ids[0],
                "tie A",
                _recent_iso(9, base=base),
                tied_end,
                0.7,
                "en",
            ),
            (
                tied_ids[1],
                "tie B",
                _recent_iso(9, base=base),
                tied_end,
                0.7,
                "en",
            ),
            (
                "t-newer",
                "newer",
                _recent_iso(4, base=base),
                newer_end,
                0.7,
                "en",
            ),
        ],
    )

    # Page 1: should return the *newer* row first (limit=1).
    page1 = client.get("/api/transcripts", params={"limit": 1})
    assert page1.status_code == 200
    items1 = page1.json()["items"]
    assert len(items1) == 1
    assert items1[0]["text"] == "newer"

    # Page 2: ask for everything strictly before the newer row.
    page2 = client.get(
        "/api/transcripts",
        params={"before": newer_end},
    )
    assert page2.status_code == 200
    items2 = page2.json()["items"]
    # Both tied rows present, ordered by id DESC tiebreak.
    expected_order = sorted(tied_ids, reverse=True)
    assert [r["id"] for r in items2] == expected_order


def test_search_sql_injection_in_q_is_safe(
    client: TestClient,
    db_path: Path,
) -> None:
    """Classic injection probe: the LIKE binding must treat the entire
    ``q`` value as a literal pattern, not as SQL. With zero rows
    matching the literal string ``"' OR 1=1 --"``, the response must
    be 200 with an empty items list — proving the parameterized query
    is doing its job.
    """
    _seed_session(db_path)
    base = datetime.now(UTC)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "hello", _recent_iso(5, base=base), _recent_iso(4, base=base), 0.7, "en"),
        ],
    )
    response = client.get(
        "/api/transcripts/search",
        params={"q": "' OR 1=1 --"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}
