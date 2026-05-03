"""HTTP coverage for the Step 13 transcripts read-only API.

These tests share the per-test app + db fixtures from
``tests/integration/conftest.py`` (the same wiring activities and
listening-mode tests use). The transcripts API is not auth-gated in v1
— the parent UI runs on the LAN — matching the convention of
``/api/listening`` and ``/api/health``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
    _insert_transcripts(
        db_path,
        [
            ("t-1", "first", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
            ("t-2", "second", "2026-01-01T00:00:03Z", "2026-01-01T00:00:04Z", 0.8, "en"),
            ("t-3", "third", "2026-01-01T00:00:05Z", "2026-01-01T00:00:06Z", 0.9, "en"),
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
    rows = [
        (
            f"t-{i:03d}",
            f"row {i}",
            f"2026-01-01T00:{i:02d}:00Z",
            f"2026-01-01T00:{i:02d}:01Z",
            0.6,
            "en",
        )
        for i in range(60)
    ]
    _insert_transcripts(db_path, rows)

    response = client.get("/api/transcripts", params={"limit": 10})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 10
    # Most recent (highest minute) first.
    assert items[0]["text"] == "row 59"
    assert items[-1]["text"] == "row 50"


def test_list_before_cursor_paginates(client: TestClient, db_path: Path) -> None:
    _seed_session(db_path)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "old",    "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
            ("t-2", "middle", "2026-01-01T00:00:03Z", "2026-01-01T00:00:04Z", 0.7, "en"),
            ("t-3", "new",    "2026-01-01T00:00:05Z", "2026-01-01T00:00:06Z", 0.7, "en"),
        ],
    )

    response = client.get(
        "/api/transcripts",
        params={"before": "2026-01-01T00:00:05Z"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert [r["text"] for r in items] == ["middle", "old"]


def test_list_default_limit_is_50(client: TestClient, db_path: Path) -> None:
    _seed_session(db_path)
    rows = [
        (
            f"t-{i:03d}",
            f"row {i}",
            f"2026-01-01T01:{i:02d}:00Z",
            f"2026-01-01T01:{i:02d}:01Z",
            0.6,
            "en",
        )
        for i in range(60)
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
# Search endpoint
# ---------------------------------------------------------------------


def test_search_substring_case_insensitive(
    client: TestClient,
    db_path: Path,
) -> None:
    _seed_session(db_path)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "Hello World",   "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
            ("t-2", "goodbye there", "2026-01-01T00:00:03Z", "2026-01-01T00:00:04Z", 0.7, "en"),
            ("t-3", "say HELLO",     "2026-01-01T00:00:05Z", "2026-01-01T00:00:06Z", 0.7, "en"),
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
    rows = [
        (
            f"t-{i:03d}",
            f"unicorn {i}",
            f"2026-01-01T02:{i:02d}:00Z",
            f"2026-01-01T02:{i:02d}:01Z",
            0.6,
            "en",
        )
        for i in range(40)
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
    assert items[0]["text"] == "unicorn 39"


def test_search_no_match_returns_empty_list(
    client: TestClient,
    db_path: Path,
) -> None:
    _seed_session(db_path)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "Hello",   "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
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
    _insert_transcripts(
        db_path,
        [
            ("t-1", "muffled", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.4, "unknown"),
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
    _insert_transcripts(
        db_path,
        [
            ("t-1", "old", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
            ("t-2", "new", "2026-01-01T00:00:03Z", "2026-01-01T00:00:04Z", 0.7, "en"),
        ],
    )
    response = client.get(
        "/api/transcripts",
        params={"before": "2026-01-01T00:00:03+00:00"},
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
    _insert_transcripts(
        db_path,
        [
            (
                tied_ids[0],
                "tie A",
                "2026-01-01T00:00:01Z",
                "2026-01-01T00:00:05Z",
                0.7,
                "en",
            ),
            (
                tied_ids[1],
                "tie B",
                "2026-01-01T00:00:01Z",
                "2026-01-01T00:00:05Z",
                0.7,
                "en",
            ),
            (
                "t-newer",
                "newer",
                "2026-01-01T00:00:06Z",
                "2026-01-01T00:00:07Z",
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
        params={"before": "2026-01-01T00:00:07Z"},
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
    _insert_transcripts(
        db_path,
        [
            ("t-1", "hello", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
        ],
    )
    response = client.get(
        "/api/transcripts/search",
        params={"q": "' OR 1=1 --"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}
