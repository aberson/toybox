"""Household-scoped ``boss_fights_enabled`` feature flag.

Phase W Step W5. When ``True`` (the default), a dynamic adventure's
CLIMAX beat (the final generated beat) is emitted as a distinct
``kind="boss_fight"`` encounter casting a boss-role toy; when ``False``
the climax is an ordinary ``adventure_beat`` and the adventure runs
W4-identical to termination (no boss).

Follows the feature-flag settings convention — defensive get with
WARNING-and-fallback, set with type validation, UPSERT semantics.
Implementation lives in :mod:`toybox.core._feature_flag`. This flag is
NOT part of the Phase K five-flag cohort (it has its own SettingsPanel
toggle + api method rather than the bundled ``PhaseKFeatureFlags`` list),
so it is intentionally absent from
``tests/integration/test_phase_k_feature_flag_lists_agree.py``.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

BOSS_FIGHTS_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(key="boss_fights_enabled", default=BOSS_FIGHTS_ENABLED_DEFAULT)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``boss_fights_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool.

    Raises :class:`ValueError` when ``value`` is not a ``bool``. The
    API layer translates this into HTTP 422.
    """
    return _SETTING.set(conn, value)


__all__ = ["BOSS_FIGHTS_ENABLED_DEFAULT", "get", "set"]
