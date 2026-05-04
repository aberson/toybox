"""Unit tests for the parent PIN argon2id helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from toybox.core.pin import (
    ARGON2_MEMORY_COST,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    PIN_HASH_SETTING_KEY,
    PinFormatError,
    clear_pin_hash,
    get_pin_hash,
    hash_pin,
    pin_is_set,
    set_pin_hash,
    validate_pin_format,
    verify_pin,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Migrated SQLite file used by the storage round-trip tests."""
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    return path


# ---- format validation ------------------------------------------------


@pytest.mark.parametrize("pin", ["1234", "999999", "012345678901"])
def test_validate_pin_format_accepts_valid(pin: str) -> None:
    validate_pin_format(pin)  # does not raise


@pytest.mark.parametrize(
    "pin",
    [
        "",  # empty
        "1",  # too short
        "12",  # too short
        "123",  # too short
        "abcd",  # non-digit
        "12 4",  # space
        "12.4",  # punctuation
        "1234567890123",  # 13 digits — past max
    ],
)
def test_validate_pin_format_rejects_invalid(pin: str) -> None:
    with pytest.raises(PinFormatError):
        validate_pin_format(pin)


@pytest.mark.parametrize(
    "pin",
    [
        "١٢٣٤",  # Arabic-Indic ١٢٣٤
        "１２３４",  # full-width １２３４
        "१२३४",  # Devanagari १२३४
    ],
)
def test_validate_pin_format_rejects_non_ascii_digits(pin: str) -> None:
    """Spec invariant: only ASCII ``[0-9]`` survives validation.

    Python's ``\\d`` matches any Unicode digit, which would let a parent
    bootstrap with a PIN they can't re-enter on a numeric keypad. The
    regex must be ASCII-only.
    """
    with pytest.raises(PinFormatError):
        validate_pin_format(pin)


# ---- hash + verify ----------------------------------------------------


def test_argon2_parameters_are_pinned() -> None:
    """Spec: ``m=65536, t=3, p=4`` — verified by a probe hash."""
    assert ARGON2_TIME_COST == 3
    assert ARGON2_MEMORY_COST == 65536
    assert ARGON2_PARALLELISM == 4
    digest = hash_pin("1234")
    # PHC string format: ``$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>``.
    assert digest.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_hash_pin_then_verify_matches() -> None:
    digest = hash_pin("4321")
    assert verify_pin("4321", digest) is True


def test_verify_pin_rejects_wrong_pin() -> None:
    digest = hash_pin("1234")
    assert verify_pin("4321", digest) is False


def test_verify_pin_rejects_empty_string() -> None:
    digest = hash_pin("1234")
    assert verify_pin("", digest) is False


def test_hash_pin_uses_random_salt() -> None:
    """Same input → distinct hashes; both verify correctly."""
    a = hash_pin("1234")
    b = hash_pin("1234")
    assert a != b
    assert verify_pin("1234", a)
    assert verify_pin("1234", b)


def test_verify_pin_against_corrupt_hash_returns_false() -> None:
    """Defensive: a malformed stored hash fails-closed instead of crashing."""
    assert verify_pin("1234", "not-a-real-argon2-hash") is False


def test_hash_pin_rejects_bad_format() -> None:
    with pytest.raises(PinFormatError):
        hash_pin("12")
    with pytest.raises(PinFormatError):
        hash_pin("abcd")


# ---- DB round-trip ----------------------------------------------------


def test_get_pin_hash_returns_none_initially(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        assert get_pin_hash(conn) is None
        assert pin_is_set(conn) is False
    finally:
        conn.close()


def test_set_pin_hash_persists_hash(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        digest = set_pin_hash(conn, "1234")
        assert digest.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
        # Read back via the helper.
        assert get_pin_hash(conn) == digest
        assert pin_is_set(conn) is True
        # And verify the round-trip via the raw row.
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (PIN_HASH_SETTING_KEY,),
        ).fetchone()
        assert row is not None
        assert row["value"] == digest
    finally:
        conn.close()


def test_set_pin_hash_overwrites_existing(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        first = set_pin_hash(conn, "1234")
        second = set_pin_hash(conn, "9876")
        assert first != second
        # The latest write wins.
        assert get_pin_hash(conn) == second
        assert verify_pin("9876", second)
        assert verify_pin("1234", second) is False
    finally:
        conn.close()


def test_clear_pin_hash_removes_row(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, "1234")
        assert pin_is_set(conn) is True
        clear_pin_hash(conn)
        assert pin_is_set(conn) is False
        assert get_pin_hash(conn) is None
    finally:
        conn.close()
