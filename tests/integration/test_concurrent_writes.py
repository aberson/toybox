"""Smoke test that WAL + busy_timeout survives multi-thread INSERTs."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

THREADS = 5
INSERTS_PER_THREAD = 10


def _seed_session(db_path: Path, session_id: str) -> None:
    conn = connect(db_path)
    try:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO sessions (id, started_at, mode, mic_id) VALUES (?, ?, ?, ?)",
            (session_id, "2026-01-01T00:00:00Z", 3, "home"),
        )
        conn.commit()
    finally:
        conn.close()


def _writer(
    db_path: Path,
    session_id: str,
    errors: list[Exception],
    barrier: threading.Barrier,
) -> None:
    try:
        conn = connect(db_path)
        try:
            barrier.wait()
            for _ in range(INSERTS_PER_THREAD):
                conn.execute(
                    "INSERT INTO transcripts "
                    "(id, session_id, mic_id, started_at, ended_at, text) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        session_id,
                        "home",
                        "2026-01-01T00:00:00Z",
                        "2026-01-01T00:00:01Z",
                        "hi",
                    ),
                )
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        errors.append(exc)


def test_concurrent_inserts_no_locks(tmp_path: Path) -> None:
    db_path = tmp_path / "toybox.db"
    session_id = "s1"
    _seed_session(db_path, session_id)

    errors: list[Exception] = []
    barrier = threading.Barrier(THREADS)
    threads = [
        threading.Thread(target=_writer, args=(db_path, session_id, errors, barrier))
        for _ in range(THREADS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], f"unexpected exceptions in writers: {errors!r}"

    conn = connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
    finally:
        conn.close()
    assert count == THREADS * INSERTS_PER_THREAD
