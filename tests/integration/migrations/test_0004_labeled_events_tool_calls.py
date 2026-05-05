"""Coverage for the Phase E Step 28 ``labeled_events.tool_calls`` migration.

Tests the column lands on a fresh DB, that re-running migrations is a
no-op (idempotent), and that ``get_tool_calls`` round-trips a JSON
list through the column (and surfaces ``None`` when the row was
persisted with ``tool_calls = NULL`` — the v1 single-shot contract).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.ai.labeled_events import get_tool_calls
from toybox.db.connection import connect
from toybox.db.migrations import current_version, run_migrations


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "toybox.db")
    try:
        yield c
    finally:
        c.close()


def test_tool_calls_column_added(conn: sqlite3.Connection) -> None:
    """Forward-apply on a fresh DB → ``tool_calls`` column exists."""
    run_migrations(conn)
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(labeled_events)")}
    assert "tool_calls" in cols, list(cols)
    # SQLite's affinity for the declared TEXT type → "TEXT".
    assert cols["tool_calls"]["type"].upper() == "TEXT"
    # Nullable by design — single-shot (v1) rows leave the column NULL.
    assert cols["tool_calls"]["notnull"] == 0


def test_migration_recorded(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 4 in versions
    assert current_version(conn) >= 4


def test_migration_idempotent_on_second_run(conn: sqlite3.Connection) -> None:
    """Re-running migrations after they've all applied must be a no-op.

    A second ``run_migrations`` call must NOT re-execute the
    ``ALTER TABLE`` (which would itself fail with ``duplicate column
    name``); it returns ``[]`` instead.
    """
    first = run_migrations(conn)
    versions = [m.version for m in first]
    assert 4 in versions
    starting_version = current_version(conn)
    assert starting_version >= 4

    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting_version

    # Column survives intact.
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(labeled_events)")}
    assert "tool_calls" in cols


def test_tool_calls_round_trip_via_helper(conn: sqlite3.Connection) -> None:
    """JSON-encoded tool-call list round-trips through the column.

    Pins the documented telemetry shape: each entry is a dict with
    ``tool``, ``args``, ``result_summary``, ``latency_ms``, ``error``,
    and ``ts`` fields. ``get_tool_calls`` decodes the column back into
    a Python list.
    """
    run_migrations(conn)
    payload = [
        {
            "tool": "get_room",
            "args": {"room_id": "550e8400-e29b-41d4-a716-446655440000"},
            "result_summary": "kitchen -- features: counter, fridge",
            "latency_ms": 12,
            "error": None,
            "ts": "2026-05-12T14:30:01.234Z",
        },
        {
            "tool": "get_persona",
            "args": {"persona_id": "wizard"},
            "result_summary": "Wizard (magician)",
            "latency_ms": 8,
            "error": None,
            "ts": "2026-05-12T14:30:01.250Z",
        },
    ]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    with conn:
        conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, "
            " inputs_chatml_json, activity_json, tool_calls) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "a-tool",
                "2026-05-12T14:30:01Z",
                "claude",
                "[]",
                '{"id":"a-tool"}',
                encoded,
            ),
        )
    decoded = get_tool_calls(conn, activity_id="a-tool")
    assert decoded is not None
    assert len(decoded) == 2
    # Field-level round-trip check on the first entry.
    assert decoded[0]["tool"] == "get_room"
    assert decoded[0]["args"]["room_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert decoded[0]["latency_ms"] == 12
    assert decoded[0]["error"] is None
    assert decoded[1]["tool"] == "get_persona"
    assert decoded[1]["args"]["persona_id"] == "wizard"


def test_tool_calls_null_returns_none(conn: sqlite3.Connection) -> None:
    """Row inserted with ``tool_calls = NULL`` reads back as Python ``None``.

    The v1 single-shot path doesn't populate the column, so existing
    rows + every offline-path row stays at NULL. ``get_tool_calls``
    must surface that as ``None`` (not ``[]``) so callers can
    distinguish "loop mode emitted zero calls" from "single-shot path
    didn't write anything".
    """
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, "
            " inputs_chatml_json, activity_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "a-null",
                "2026-05-12T14:30:01Z",
                "offline",
                "[]",
                '{"id":"a-null"}',
            ),
        )
    decoded = get_tool_calls(conn, activity_id="a-null")
    assert decoded is None
