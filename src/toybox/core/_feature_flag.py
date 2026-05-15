"""Internal helper for Phase K boolean feature-flag settings.

The eight Phase K feature flags
(``jokes_enabled``, ``songs_enabled``, ``play_standalone_enabled``,
``play_embedded_enabled``, ``play_endings_enabled``,
``play_spontaneity_enabled``, ``clickable_words_enabled``,
``read_me_button_enabled``) share an identical storage shape: a TEXT
``settings.<key>`` row holding ``'true'`` or ``'false'``, with a
defensive read that falls back to a per-flag default on missing /
unparseable / out-of-set values.

This private module factors that shared shape out into one
:class:`FeatureFlagSetting` instance per flag. The eight per-setting
modules in :mod:`toybox.core.<name>_enabled` re-export ``get`` /
``set`` / the default constant from their bound instance, preserving
the per-setting-module convention from Phase H/I/J while keeping the
storage logic in a single source of truth (cf.
[code-quality.md §2](.claude/rules/code-quality.md) — one source of
truth for data-shape constants).

The module is underscore-prefixed because its surface is an
implementation detail; callers must go through the eight public
per-setting modules so a future shape change (e.g. moving to
INTEGER 0/1, or to a dedicated feature_flags table) is a one-file
edit. ``__all__`` is intentionally empty for the same reason.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

_logger = logging.getLogger(__name__)


# Canonical wire shape used by the eight per-setting modules: stored as
# ``'true'`` / ``'false'`` strings (matching the seed migration 0015),
# returned to callers as Python ``bool``. The :data:`_VALID_RAW` set is
# the membership check applied to the persisted value; out-of-set
# strings log a warning and fall back to the default rather than
# raising (mirrors :mod:`toybox.core.play_cadence_seconds`'s
# defensive-read pattern).
_TRUE_RAW = "true"
_FALSE_RAW = "false"
_VALID_RAW: frozenset[str] = frozenset({_TRUE_RAW, _FALSE_RAW})


def _to_raw(value: bool) -> str:
    return _TRUE_RAW if value else _FALSE_RAW


@dataclass(frozen=True)
class FeatureFlagSetting:
    """Per-flag binding of settings-table key + default.

    The dataclass is frozen so a flag instance can be a module-level
    constant in each per-setting module (and so two modules can never
    accidentally hold the same instance with different defaults).
    """

    key: str
    default: bool

    def get(self, conn: sqlite3.Connection) -> bool:
        """Return the persisted flag, defaulting to :attr:`default`.

        Falls back to :attr:`default` in three cases:

        1. The settings row is absent (legacy DB that predates
           migration 0015, or a deleted seed row).
        2. The value is not a string (corrupt blob, hand-edit).
        3. The string is not in :data:`_VALID_RAW`.

        Cases 2 and 3 log at WARNING with the offending value truncated
        to 64 chars — mirrors :mod:`toybox.core.play_cadence_seconds`
        so a corrupt blob can't flood the logs.
        """
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (self.key,),
        ).fetchone()
        if row is None:
            return self.default
        raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
        if not isinstance(raw, str):
            truncated = str(raw)[:64]
            _logger.warning(
                "settings.%s=%r non-string; falling back to %s",
                self.key,
                truncated,
                self.default,
            )
            return self.default
        if raw not in _VALID_RAW:
            truncated = raw if len(raw) <= 64 else f"{raw[:64]}..."
            _logger.warning(
                "settings.%s=%r outside canonical set; falling back to %s",
                self.key,
                truncated,
                self.default,
            )
            return self.default
        return raw == _TRUE_RAW

    def set(self, conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors per-setting public API
        """Persist ``value`` and return the canonical bool.

        ``value`` must be a real ``bool`` — callers pass through their
        own Pydantic model which enforces the type before reaching this
        helper. Truthiness coercion would defeat the type guarantee, so
        a non-bool raises :class:`ValueError`. The API layer translates
        this into HTTP 422 (mirrors
        :mod:`toybox.api.transcript_retention_settings`).
        """
        if not isinstance(value, bool):
            raise ValueError(f"invalid {self.key}: {value!r}; expected bool")
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (self.key, _to_raw(value)),
            )
        return value


# Module-private: callers must go through the eight public per-setting
# modules so a future shape change stays a one-file edit.
__all__: list[str] = []
