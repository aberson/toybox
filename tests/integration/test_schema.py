"""Round-trip + constraint coverage for the v1 schema."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a fresh, migrated connection; close it on teardown.

    Closing matters on Windows: ``tmp_path`` cleanup runs ``shutil.rmtree``
    which can fail with ``PermissionError: [WinError 32]`` while the
    sqlite handle is holding WAL/SHM files.
    """
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


def _seed_session(conn: sqlite3.Connection, session_id: str = "s1") -> str:
    conn.execute(
        "INSERT INTO sessions (id, started_at, mode, mic_id) VALUES (?, ?, ?, ?)",
        (session_id, "2026-01-01T00:00:00Z", 3, "home"),
    )
    return session_id


def _seed_persona(conn: sqlite3.Connection, persona_id: str = "wizard") -> str:
    conn.execute(
        "INSERT INTO personas (id, display_name, system_prompt, source, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (persona_id, "Wizard", "You are a wizard.", "library", "2026-01-01T00:00:00Z"),
    )
    return persona_id


def _seed_activity(
    conn: sqlite3.Connection,
    activity_id: str = "a1",
    session_id: str = "s1",
) -> str:
    conn.execute(
        "INSERT INTO activities "
        "(id, session_id, state, version, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (activity_id, session_id, "proposed", 1, "2026-01-01T00:00:00Z"),
    )
    return activity_id


def test_pragmas_applied(db: sqlite3.Connection) -> None:
    fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
    journal = db.execute("PRAGMA journal_mode").fetchone()[0]
    busy = db.execute("PRAGMA busy_timeout").fetchone()[0]
    sync = db.execute("PRAGMA synchronous").fetchone()[0]
    assert fk == 1
    assert str(journal).lower() == "wal"
    assert busy == 5000
    assert sync == 1


def test_pragmas_applied_per_connection(tmp_path: Path) -> None:
    """foreign_keys defaults to OFF on every new connection.

    Pins that ``connect()`` re-applies pragmas on each new handle. Without
    this assertion, moving the pragma logic outside ``connect()`` (e.g.
    into a one-time setup hook on first migrate) would silently regress
    foreign-key enforcement on subsequent connections. ``busy_timeout``
    is also per-connection; ``journal_mode=WAL`` is persistent so one
    assertion is enough for it.
    """
    db_path = tmp_path / "toybox.db"
    conn1 = connect(db_path)
    try:
        run_migrations(conn1)
        assert conn1.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn1.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn1.close()

    conn2 = connect(db_path)
    try:
        assert conn2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn2.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert str(conn2.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
    finally:
        conn2.close()


def test_settings_seeded(db: sqlite3.Connection) -> None:
    rows = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}
    assert rows["listening_mode"] == "3"
    assert rows["claude_call_min_interval_sec"] == "30"
    assert rows["claude_spontaneous_interval_sec"] == "300"
    assert rows["vad_aggressiveness"] == "2"
    assert rows["log_level"] == "INFO"
    assert rows["mic_enabled"] == "true"
    assert rows["time_of_day_aware"] == "true"
    assert "parent_pin_hash" not in rows
    assert "parent_pin_set_at" not in rows


def test_round_trip_personas(db: sqlite3.Connection) -> None:
    _seed_persona(db)
    row = db.execute("SELECT * FROM personas WHERE id='wizard'").fetchone()
    assert row["display_name"] == "Wizard"
    assert row["language"] == "en"


def test_round_trip_toys(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("mr-unicorn", "Mr. Unicorn", "toys/uuid.jpg", "hash-1", "2026-01-01T00:00:00Z"),
    )
    row = db.execute("SELECT * FROM toys WHERE id='mr-unicorn'").fetchone()
    assert row["archived"] == 0
    assert row["display_name"] == "Mr. Unicorn"


def test_round_trip_children(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO children (id, display_name) VALUES (?, ?)",
        ("ada", "Ada"),
    )
    assert db.execute("SELECT display_name FROM children WHERE id='ada'").fetchone()[0] == "Ada"


def test_round_trip_rooms_and_features(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO rooms (id, display_name) VALUES (?, ?)", ("living", "Living Room"))
    db.execute(
        "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
        ("feat-1", "living", "couch"),
    )
    row = db.execute("SELECT name FROM room_features WHERE room_id='living'").fetchone()
    assert row["name"] == "couch"


def test_room_features_unique_room_id_name(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO rooms (id, display_name) VALUES (?, ?)", ("living", "Living Room"))
    db.execute(
        "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
        ("feat-1", "living", "couch"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
            ("feat-2", "living", "couch"),
        )


def test_round_trip_session_and_activity(db: sqlite3.Connection) -> None:
    _seed_session(db)
    _seed_activity(db)
    row = db.execute("SELECT version, state FROM activities WHERE id='a1'").fetchone()
    assert row["version"] == 1
    assert row["state"] == "proposed"


def test_round_trip_activity_steps(db: sqlite3.Connection) -> None:
    _seed_session(db)
    _seed_activity(db)
    db.execute(
        "INSERT INTO activity_steps (id, activity_id, seq, body) VALUES (?, ?, ?, ?)",
        ("step-1", "a1", 1, "Find the unicorn."),
    )
    row = db.execute("SELECT body FROM activity_steps WHERE id='step-1'").fetchone()
    assert row["body"] == "Find the unicorn."


def test_round_trip_transcripts(db: sqlite3.Connection) -> None:
    _seed_session(db)
    db.execute(
        "INSERT INTO transcripts "
        "(id, session_id, mic_id, started_at, ended_at, text, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "t-1",
            "s1",
            "home",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:05Z",
            "let's play",
            -0.4,
        ),
    )
    row = db.execute("SELECT text, confidence FROM transcripts WHERE id='t-1'").fetchone()
    assert row["text"] == "let's play"
    assert row["confidence"] == pytest.approx(-0.4)


def test_round_trip_feedback(db: sqlite3.Connection) -> None:
    _seed_session(db)
    _seed_activity(db)
    db.execute(
        "INSERT INTO feedback (id, activity_id, kind, signature, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("fb-1", "a1", "didnt_work", "tmpl:fp", "2026-01-01T00:00:00Z"),
    )
    row = db.execute("SELECT signature FROM feedback WHERE id='fb-1'").fetchone()
    assert row["signature"] == "tmpl:fp"


def test_round_trip_auth_tokens(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO auth_tokens (token_hash, scope, created_at, expires_at) VALUES (?, ?, ?, ?)",
        ("hash-1", "parent", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),
    )
    row = db.execute("SELECT scope FROM auth_tokens WHERE token_hash='hash-1'").fetchone()
    assert row["scope"] == "parent"


def test_round_trip_schema_migrations(db: sqlite3.Connection) -> None:
    rows = list(
        db.execute("SELECT version, filename FROM schema_migrations ORDER BY version")
    )
    # 0001 is the v1 schema and must always be the first applied row;
    # subsequent migrations may stack on top.
    assert rows[0]["version"] == 1
    assert rows[0]["filename"] == "0001_initial.sql"
    assert len(rows) >= 1


def test_feedback_cascade_on_activity_delete(db: sqlite3.Connection) -> None:
    _seed_session(db)
    _seed_activity(db)
    db.execute(
        "INSERT INTO feedback (id, activity_id, kind, signature, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("fb-1", "a1", "didnt_work", "tmpl:fp", "2026-01-01T00:00:00Z"),
    )
    db.execute("DELETE FROM activities WHERE id='a1'")
    assert db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0


def test_activity_steps_cascade_on_activity_delete(db: sqlite3.Connection) -> None:
    _seed_session(db)
    _seed_activity(db)
    db.execute(
        "INSERT INTO activity_steps (id, activity_id, seq, body) VALUES (?, ?, ?, ?)",
        ("s-1", "a1", 1, "Do thing"),
    )
    db.execute("DELETE FROM activities WHERE id='a1'")
    assert db.execute("SELECT COUNT(*) FROM activity_steps").fetchone()[0] == 0


def test_transcripts_restrict_blocks_session_delete(db: sqlite3.Connection) -> None:
    _seed_session(db)
    db.execute(
        "INSERT INTO transcripts (id, session_id, started_at, ended_at, text) "
        "VALUES (?, ?, ?, ?, ?)",
        ("t-1", "s1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", "hi"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM sessions WHERE id='s1'")


def test_persona_restrict_blocks_delete_when_toy_references_it(db: sqlite3.Connection) -> None:
    _seed_persona(db)
    db.execute(
        "INSERT INTO toys "
        "(id, display_name, image_path, image_hash, persona_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "mr-unicorn",
            "Mr. Unicorn",
            "toys/uuid.jpg",
            "hash-1",
            "wizard",
            "2026-01-01T00:00:00Z",
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM personas WHERE id='wizard'")


def test_room_restrict_blocks_delete_when_feature_exists(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO rooms (id, display_name) VALUES (?, ?)", ("living", "Living Room"))
    db.execute(
        "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
        ("feat-1", "living", "couch"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM rooms WHERE id='living'")


def test_partial_unique_toy_image_hash(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO toys "
        "(id, display_name, image_path, image_hash, archived, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("mr-unicorn", "Mr. Unicorn", "a.jpg", "hash-shared", 0, "2026-01-01T00:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO toys "
            "(id, display_name, image_path, image_hash, archived, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ms-unicorn", "Ms. Unicorn", "b.jpg", "hash-shared", 0, "2026-01-01T00:00:00Z"),
        )


def test_partial_unique_toy_image_hash_archived_can_share(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO toys "
        "(id, display_name, image_path, image_hash, archived, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("old-unicorn", "Old Unicorn", "a.jpg", "hash-shared", 1, "2026-01-01T00:00:00Z"),
    )
    db.execute(
        "INSERT INTO toys "
        "(id, display_name, image_path, image_hash, archived, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("new-unicorn", "New Unicorn", "b.jpg", "hash-shared", 0, "2026-01-01T00:00:00Z"),
    )
    assert db.execute("SELECT COUNT(*) FROM toys").fetchone()[0] == 2


def test_partial_unique_room_image_hash(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO rooms (id, display_name, image_path, image_hash) VALUES (?, ?, ?, ?)",
        ("living", "Living", "p.jpg", "h-shared"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO rooms (id, display_name, image_path, image_hash) VALUES (?, ?, ?, ?)",
            ("kitchen", "Kitchen", "p2.jpg", "h-shared"),
        )


def test_partial_unique_room_image_hash_null_allowed(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO rooms (id, display_name) VALUES (?, ?)",
        ("living", "Living"),
    )
    db.execute(
        "INSERT INTO rooms (id, display_name) VALUES (?, ?)",
        ("kitchen", "Kitchen"),
    )
    assert db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 2


def test_partial_unique_persona_avatar_excludes_library(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO personas "
        "(id, display_name, system_prompt, avatar_image_hash, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("wiz1", "Wiz1", "p", "h-shared", "library", "2026-01-01T00:00:00Z"),
    )
    db.execute(
        "INSERT INTO personas "
        "(id, display_name, system_prompt, avatar_image_hash, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("wiz2", "Wiz2", "p", "h-shared", "library", "2026-01-01T00:00:00Z"),
    )
    assert db.execute("SELECT COUNT(*) FROM personas").fetchone()[0] == 2


def test_partial_unique_persona_avatar_blocks_non_library_dup(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO personas "
        "(id, display_name, system_prompt, avatar_image_hash, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("wiz1", "Wiz1", "p", "h-shared", "manual", "2026-01-01T00:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO personas "
            "(id, display_name, system_prompt, avatar_image_hash, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("wiz2", "Wiz2", "p", "h-shared", "ai_generated", "2026-01-01T00:00:00Z"),
        )
