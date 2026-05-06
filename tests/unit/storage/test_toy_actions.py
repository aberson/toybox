"""Unit coverage for :mod:`toybox.storage.toy_actions`.

In-memory SQLite is sufficient for the table-level CRUD checks; the
full integration-suite path through migrations + FK cascade is
covered by ``tests/integration/migrations/test_0005_toy_actions.py``.

Each test opens its own connection through
:func:`toybox.db.connection.connect` (so ``foreign_keys = ON`` is
applied) and runs migrations forward to the latest version.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.image_gen.models import ACTION_SLOTS, ToyActionStatus
from toybox.storage import toy_actions
from toybox.storage.toy_actions import (
    _validate_slot,
    _validate_toy_id,
    delete_for_toy_archived,
    get_image_path,
    list_for_toy,
    upsert_status,
)

# A canonical UUIDv4 we can reuse across tests.
_TOY_ID = "550e8400-e29b-41d4-a716-446655440000"
_OTHER_TOY_ID = "660e8400-e29b-41d4-a716-446655440111"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a migrated SQLite connection on a tmp file.

    Tmp file (rather than ``:memory:``) so the WAL pragma applies
    cleanly and the test posture matches the integration suite. The
    file is dropped by ``tmp_path`` cleanup at teardown.
    """
    c = connect(tmp_path / "toybox.db")
    try:
        run_migrations(c)
        # Seed the parent toys row so FK inserts succeed.
        with c:
            c.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (_TOY_ID, "Bunny", "data/images/toys/bunny.jpg", "h1", "2026-05-06T00:00:00Z"),
            )
            c.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    _OTHER_TOY_ID,
                    "Wiz",
                    "data/images/toys/wiz.jpg",
                    "h2",
                    "2026-05-06T00:00:00Z",
                ),
            )
        yield c
    finally:
        c.close()


# ----------------------------------------------------------------------
# _validate_toy_id / _validate_slot
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",  # empty
        "../../etc/passwd",  # path traversal shape
        "12345678-1234-3234-8234-123456789012",  # wrong version (not v4)
        "12345678-1234-4234-c234-123456789012",  # wrong variant (not 8/9/a/b)
        "12345678-1234-4234-8234-123456789012-x",  # extra suffix
    ],
)
def test_validate_toy_id_rejects_non_uuid_v4(bad: str) -> None:
    with pytest.raises(ValueError, match="UUIDv4"):
        _validate_toy_id(bad)


def test_validate_toy_id_accepts_canonical_lower_and_upper() -> None:
    _validate_toy_id(_TOY_ID)
    # Case-insensitive — UUIDs in the wild come both ways.
    _validate_toy_id(_TOY_ID.upper())


@pytest.mark.parametrize("bad", ["", "foo", "IDLE", "  idle  ", "smiling"])
def test_validate_slot_rejects_unknown(bad: str) -> None:
    with pytest.raises(ValueError, match="ACTION_SLOTS"):
        _validate_slot(bad)


def test_validate_slot_accepts_all_canonical() -> None:
    for slot in ACTION_SLOTS:
        _validate_slot(slot)  # must not raise


# ----------------------------------------------------------------------
# upsert_status
# ----------------------------------------------------------------------


def test_upsert_status_insert_path(conn: sqlite3.Connection) -> None:
    """Insert path: row didn't exist, upsert creates it with all fields."""
    row = upsert_status(
        conn,
        _TOY_ID,
        "idle",
        ToyActionStatus.queued,
        seed=12345,
    )
    assert row.toy_id == _TOY_ID
    assert row.slot == "idle"
    assert row.status is ToyActionStatus.queued
    assert row.image_path is None
    assert row.seed == 12345
    assert row.error_msg is None
    assert row.updated_at  # ISO-8601 string; non-empty.

    # Verify it actually landed in the DB.
    db_row = conn.execute(
        "SELECT * FROM toy_actions WHERE toy_id = ? AND slot = ?",
        (_TOY_ID, "idle"),
    ).fetchone()
    assert db_row is not None
    assert db_row["status"] == "queued"
    assert db_row["seed"] == 12345


def test_upsert_status_update_path(conn: sqlite3.Connection) -> None:
    """Update path: existing row → status + fields update; PK survives."""
    upsert_status(conn, _TOY_ID, "pointing", ToyActionStatus.queued, seed=42)
    upsert_status(conn, _TOY_ID, "pointing", ToyActionStatus.running, seed=42)
    final = upsert_status(
        conn,
        _TOY_ID,
        "pointing",
        ToyActionStatus.done,
        image_path="data/images/toy_actions/" + _TOY_ID + "/pointing.png",
        seed=42,
    )
    assert final.status is ToyActionStatus.done
    assert final.image_path is not None
    assert final.image_path.endswith("/pointing.png")
    assert final.seed == 42

    # Exactly one row for this (toy_id, slot) — confirming we
    # exercised the ON CONFLICT update path, not three inserts.
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM toy_actions WHERE toy_id = ? AND slot = ?",
        (_TOY_ID, "pointing"),
    ).fetchone()["n"]
    assert n == 1


def test_upsert_status_rejects_not_started(conn: sqlite3.Connection) -> None:
    """``not_started`` is a UI-only placeholder; persisting it is rejected."""
    with pytest.raises(ValueError, match="not_started"):
        upsert_status(conn, _TOY_ID, "idle", ToyActionStatus.not_started)


def test_upsert_status_rejects_invalid_toy_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="UUIDv4"):
        upsert_status(conn, "../bad", "idle", ToyActionStatus.queued)


def test_upsert_status_rejects_invalid_slot(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="ACTION_SLOTS"):
        upsert_status(conn, _TOY_ID, "smiling", ToyActionStatus.queued)


# ----------------------------------------------------------------------
# list_for_toy
# ----------------------------------------------------------------------


def test_list_for_toy_synthesizes_all_ten_when_no_rows(conn: sqlite3.Connection) -> None:
    """Zero DB rows → 10 ``not_started`` placeholders in canonical order."""
    rows = list_for_toy(conn, _TOY_ID)
    assert len(rows) == 10
    assert [r.slot for r in rows] == list(ACTION_SLOTS)
    for r in rows:
        assert r.toy_id == _TOY_ID
        assert r.status is ToyActionStatus.not_started
        assert r.image_path is None
        assert r.seed is None
        assert r.error_msg is None
        assert r.updated_at == ""


def test_list_for_toy_mixes_real_and_synthesized(conn: sqlite3.Connection) -> None:
    """Two real rows + eight synthesized; canonical-order preserved."""
    upsert_status(
        conn,
        _TOY_ID,
        "idle",
        ToyActionStatus.done,
        image_path="data/images/toy_actions/" + _TOY_ID + "/idle.png",
        seed=11,
    )
    upsert_status(
        conn,
        _TOY_ID,
        "running",
        ToyActionStatus.failed,
        error_msg="timeout",
    )

    rows = list_for_toy(conn, _TOY_ID)
    assert len(rows) == 10
    assert [r.slot for r in rows] == list(ACTION_SLOTS)

    by_slot = {r.slot: r for r in rows}
    assert by_slot["idle"].status is ToyActionStatus.done
    assert by_slot["idle"].image_path is not None
    assert by_slot["idle"].seed == 11
    assert by_slot["running"].status is ToyActionStatus.failed
    assert by_slot["running"].error_msg == "timeout"

    # Every other slot is a synthesized placeholder.
    for slot in ACTION_SLOTS:
        if slot in {"idle", "running"}:
            continue
        assert by_slot[slot].status is ToyActionStatus.not_started
        assert by_slot[slot].image_path is None
        assert by_slot[slot].updated_at == ""


def test_list_for_toy_isolates_per_toy(conn: sqlite3.Connection) -> None:
    """Rows for one toy don't leak into another toy's list."""
    upsert_status(conn, _TOY_ID, "idle", ToyActionStatus.done)
    rows_other = list_for_toy(conn, _OTHER_TOY_ID)
    # All synthesized for the other toy — no leakage from _TOY_ID.
    assert all(r.status is ToyActionStatus.not_started for r in rows_other)
    assert all(r.toy_id == _OTHER_TOY_ID for r in rows_other)


def test_list_for_toy_rejects_invalid_toy_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="UUIDv4"):
        list_for_toy(conn, "../bad")


def test_list_for_toy_skips_rows_with_invalid_status(
    conn: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An out-of-vocab status in the DB is logged + falls back to placeholder."""
    # Bypass the helper's validation by writing the bad row directly.
    with conn:
        conn.execute(
            "INSERT INTO toy_actions (toy_id, slot, status, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (_TOY_ID, "idle", "garbage_value", "2026-05-06T00:00:00Z"),
        )

    import logging

    with caplog.at_level(logging.WARNING, logger=toy_actions.__name__):
        rows = list_for_toy(conn, _TOY_ID)

    assert len(rows) == 10
    by_slot = {r.slot: r for r in rows}
    assert by_slot["idle"].status is ToyActionStatus.not_started
    assert any("invalid status" in r.message for r in caplog.records)


def test_list_for_toy_warns_on_unknown_slot(
    conn: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An out-of-vocab slot in the DB is logged + falls back to placeholder.

    Bypass the upsert_status validators by inserting the bad slot row
    directly via SQL. The canonical 10-slot grid must still come back
    with the missing canonical slot synthesized as ``not_started`` and
    a WARNING with ``unknown slot`` substring must fire so operators
    can trace the bad write.
    """
    import logging

    # Bypass the helper validators by hitting SQL directly.
    with conn:
        conn.execute(
            "INSERT INTO toy_actions (toy_id, slot, status, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (_TOY_ID, "smiling", "done", "2026-05-06T00:00:00Z"),
        )

    with caplog.at_level(logging.WARNING, logger=toy_actions.__name__):
        rows = list_for_toy(conn, _TOY_ID)

    # All 10 canonical slots present, "smiling" is not one of them.
    assert len(rows) == 10
    assert [r.slot for r in rows] == list(ACTION_SLOTS)

    # WARNING fired with the bad slot name + a clear marker.
    matches = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "unknown slot" in r.getMessage()
    ]
    assert matches, [r.getMessage() for r in caplog.records]
    assert "smiling" in matches[-1].getMessage()


# ----------------------------------------------------------------------
# get_image_path
# ----------------------------------------------------------------------


def test_get_image_path_returns_path_on_done(conn: sqlite3.Connection) -> None:
    expected = "data/images/toy_actions/" + _TOY_ID + "/idle.png"
    upsert_status(
        conn,
        _TOY_ID,
        "idle",
        ToyActionStatus.done,
        image_path=expected,
    )
    assert get_image_path(conn, _TOY_ID, "idle") == expected


def test_get_image_path_none_when_no_row(conn: sqlite3.Connection) -> None:
    assert get_image_path(conn, _TOY_ID, "idle") is None


def test_get_image_path_rejects_invalid_inputs(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="UUIDv4"):
        get_image_path(conn, "../bad", "idle")
    with pytest.raises(ValueError, match="ACTION_SLOTS"):
        get_image_path(conn, _TOY_ID, "smiling")


# ----------------------------------------------------------------------
# delete_for_toy_archived
# ----------------------------------------------------------------------


def test_delete_for_toy_archived_removes_all_rows_and_returns_count(
    conn: sqlite3.Connection,
) -> None:
    upsert_status(conn, _TOY_ID, "idle", ToyActionStatus.done)
    upsert_status(conn, _TOY_ID, "pointing", ToyActionStatus.queued)
    upsert_status(conn, _TOY_ID, "looking", ToyActionStatus.running)
    # An untouched toy whose rows must NOT be deleted.
    upsert_status(conn, _OTHER_TOY_ID, "idle", ToyActionStatus.done)

    deleted = delete_for_toy_archived(conn, _TOY_ID)
    assert deleted == 3

    remaining_target = conn.execute(
        "SELECT COUNT(*) AS n FROM toy_actions WHERE toy_id = ?",
        (_TOY_ID,),
    ).fetchone()["n"]
    assert remaining_target == 0

    remaining_other = conn.execute(
        "SELECT COUNT(*) AS n FROM toy_actions WHERE toy_id = ?",
        (_OTHER_TOY_ID,),
    ).fetchone()["n"]
    assert remaining_other == 1


def test_delete_for_toy_archived_returns_zero_when_no_rows(
    conn: sqlite3.Connection,
) -> None:
    # No rows for the toy → 0 deleted, no error.
    assert delete_for_toy_archived(conn, _TOY_ID) == 0


def test_delete_for_toy_archived_rejects_invalid_toy_id(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError, match="UUIDv4"):
        delete_for_toy_archived(conn, "../bad")
