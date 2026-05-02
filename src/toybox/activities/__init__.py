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

from .generator import generate
from .models import Activity, ActivityStep
from .time_of_day import HourBucket, hour_bucket, is_eligible

__all__ = [
    "Activity",
    "ActivityStep",
    "HourBucket",
    "generate",
    "hour_bucket",
    "is_eligible",
]
