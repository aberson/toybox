"""Offline activity generator (Phase A Step 7).

Deterministic, offline 5-step activity generator. Given
``(intent, slot, context, hour, seed)`` returns an :class:`Activity`
with exactly five :class:`ActivityStep` items. Used by listening modes
1 and 3 (when Claude is not capable) and by modes 4-5 when the breaker
is open.

The template library lives under :mod:`toybox.activities.templates` —
one JSON file per intent, validated at load time against
``templates/_schema.json``.
"""

from __future__ import annotations

from .feedback import compute_signature
from .generator import generate
from .generic_descriptors import GENERIC_DESCRIPTORS
from .interjections import INTERJECTION_DISPLAY_NAMES, InterjectionKind
from .models import Activity, ActivityStep
from .roles import (
    DEFAULT_ROLE_SPONTANEITY_RATES,
    ROLE_DEFAULTS,
    ROLE_DISPLAY_NAMES,
    Role,
    SpontaneityRatePair,
)
from .themes import THEME_DISPLAY_NAMES, Theme
from .time_of_day import HourBucket, hour_bucket, is_eligible

__all__ = [
    "DEFAULT_ROLE_SPONTANEITY_RATES",
    "GENERIC_DESCRIPTORS",
    "INTERJECTION_DISPLAY_NAMES",
    "ROLE_DEFAULTS",
    "ROLE_DISPLAY_NAMES",
    "THEME_DISPLAY_NAMES",
    "Activity",
    "ActivityStep",
    "HourBucket",
    "InterjectionKind",
    "Role",
    "SpontaneityRatePair",
    "Theme",
    "compute_signature",
    "generate",
    "hour_bucket",
    "is_eligible",
]
