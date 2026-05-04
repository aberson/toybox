"""Parent PIN hashing + storage backed by argon2id.

The hash parameters are pinned at ``m=65536, t=3, p=4`` per Step 21
of ``documentation/plan.md``. The hash is persisted as a single row in
the ``settings`` table under the key :data:`PIN_HASH_SETTING_KEY`; no
schema migration is needed because ``settings`` is an existing
key/value store from ``0001_initial.sql``.

The PIN itself is digits-only, 4-12 characters long. Validation is
done in the API layer (Pydantic) before reaching this module so the
hashing helpers stay focused on argon2 mechanics.

Failed verification at the API layer logs a WARNING with the failure
count only — never the attempted PIN. This module is responsible only
for hash + verify; the rate-limit state lives in
:mod:`toybox.core.pin_rate_limit`.
"""

from __future__ import annotations

import re
import sqlite3

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

# argon2id parameters pinned by the spec. Kept as module constants so
# the unit test can assert the resulting hash string contains them.
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536  # KiB
ARGON2_PARALLELISM = 4

# PIN format. ``MIN`` keeps the keypad UX honest (no 1-2-3 placeholder
# PINs) and ``MAX`` is configurable via ``TOYBOX_PIN_MAX_LENGTH`` per
# the spec — 12 is a generous default that still fits a numeric keypad.
PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 12

PIN_HASH_SETTING_KEY = "parent_pin_hash"

# ASCII-only digit class. Python's ``\d`` matches every Unicode digit
# category — Arabic-Indic ``١٢٣٤``, full-width ``１２３４``, devanagari
# ``१२३४``, etc. A parent who bootstraps with one of those would be
# unable to re-enter the same PIN on a numeric keypad and would be
# locked out. Restricting the character class to ``[0-9]`` keeps the
# stored PIN inside the keypad-reproducible alphabet.
_DIGIT_RE = re.compile(r"^[0-9]+$")

# A single PasswordHasher instance is safe to share — it is a thin
# wrapper around argon2-cffi's low-level library and holds no
# per-call state. Constructing it once also avoids repeating the
# parameter wiring at every call site.
_HASHER = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST,
    parallelism=ARGON2_PARALLELISM,
)


class PinFormatError(ValueError):
    """Raised when a candidate PIN fails the digits-only / length check."""


def validate_pin_format(pin: str, *, max_length: int = PIN_MAX_LENGTH) -> None:
    """Raise :class:`PinFormatError` if ``pin`` isn't 4-N digits.

    Centralised here so the hashing path and the API layer share a
    single notion of "valid PIN shape".
    """
    # The Pydantic layer already enforces ``str`` typing, but defensive
    # callers (e.g. tests, Python REPL) might pass ``None`` or bytes.
    if not isinstance(pin, str):
        raise PinFormatError("pin must be a string")
    if len(pin) < PIN_MIN_LENGTH:
        raise PinFormatError(f"pin must be at least {PIN_MIN_LENGTH} digits")
    if len(pin) > max_length:
        raise PinFormatError(f"pin must be at most {max_length} digits")
    if not _DIGIT_RE.match(pin):
        raise PinFormatError("pin must contain only digits 0-9")


def hash_pin(pin: str) -> str:
    """Hash ``pin`` with argon2id and return the encoded ``$argon2id$...$`` string.

    The PIN is validated for format before hashing so a caller that
    skipped the API-layer Pydantic check still gets a clear error
    instead of an opaque hash of garbage input.
    """
    validate_pin_format(pin)
    return _HASHER.hash(pin)


def verify_pin(pin: str, stored_hash: str) -> bool:
    """Return True iff ``pin`` matches ``stored_hash``.

    A mismatch returns ``False``; a malformed stored hash also returns
    ``False`` (defensive — a corrupt DB row should fail-closed rather
    than crash the auth path).
    """
    try:
        return bool(_HASHER.verify(stored_hash, pin))
    except VerifyMismatchError:
        return False
    except (InvalidHashError, VerificationError):
        # Malformed / unparseable hash. Treat as a verification
        # failure rather than letting the exception escape — the
        # endpoint already returns a generic ``pin_invalid`` so the
        # operator can recover via the documented reset path.
        return False


def get_pin_hash(conn: sqlite3.Connection) -> str | None:
    """Return the stored PIN hash, or ``None`` if no PIN has been set."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (PIN_HASH_SETTING_KEY,),
    ).fetchone()
    if row is None:
        return None
    value = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    if value is None or value == "":
        return None
    return str(value)


def set_pin_hash(conn: sqlite3.Connection, pin: str) -> str:
    """Hash ``pin`` and upsert it under :data:`PIN_HASH_SETTING_KEY`.

    Returns the stored hash so the caller can log / round-trip without
    a second DB read.
    """
    digest = hash_pin(pin)
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (PIN_HASH_SETTING_KEY, digest),
        )
    return digest


def clear_pin_hash(conn: sqlite3.Connection) -> None:
    """Drop the stored hash. Documented operator-recovery path.

    Used by ``documentation/operator/recovery.md`` — the operator runs
    this manually (or via DB edit) to re-enter first-run setup.
    """
    with conn:
        conn.execute(
            "DELETE FROM settings WHERE key = ?",
            (PIN_HASH_SETTING_KEY,),
        )


def pin_is_set(conn: sqlite3.Connection) -> bool:
    """Return True iff a PIN hash is stored.

    Used by :mod:`toybox.core.bind_guard` at startup so a non-loopback
    bind only succeeds after first-run PIN setup.
    """
    return get_pin_hash(conn) is not None


__all__ = [
    "ARGON2_MEMORY_COST",
    "ARGON2_PARALLELISM",
    "ARGON2_TIME_COST",
    "PIN_HASH_SETTING_KEY",
    "PIN_MAX_LENGTH",
    "PIN_MIN_LENGTH",
    "PinFormatError",
    "clear_pin_hash",
    "get_pin_hash",
    "hash_pin",
    "pin_is_set",
    "set_pin_hash",
    "validate_pin_format",
    "verify_pin",
]
