"""Time-of-day buckets for offline activity template selection.

Hour-to-bucket mapping (24-hour clock, integers 0..23) — used by
:func:`hour_bucket` to LABEL the hour:

::

    hour range       bucket
    --------------   -------------
    00:00 - 05:59    wind_down
    06:00 - 11:59    morning
    12:00 - 16:59    afternoon
    17:00 - 21:59    evening
    22:00 - 23:59    wind_down

Template *eligibility* is a separate question handled by
:func:`is_eligible`. It works in terms of an "eligible bucket set"
that depends on the hour, and intersects that with the template's
declared buckets. The eligibility rules are:

* The natural bucket for the hour (table above) is in the eligible
  set, EXCEPT that ``wind_down`` is eligible ONLY in the inclusive
  hour window ``19..21`` (per ``documentation/plan.md`` §451:
  *"wind_down excluded outside 19:00-21:00"*). So at 03:00 or 23:00,
  even though the natural bucket is ``wind_down``, the eligible bucket
  set is empty.
* Hours 19..21 add ``wind_down`` to the eligible set in addition to
  their natural bucket (``evening``). This lets a wind-down-only
  template fire at 19:00, 20:00, or 21:00 even though those hours'
  natural label is ``evening``.

A template that declares ``"always"`` (the special pseudo-bucket) or
that declares an empty / missing ``buckets`` array bypasses time-of-day
routing entirely.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class HourBucket(StrEnum):
    """Time-of-day bucket labels (the four "real" buckets)."""

    morning = "morning"
    afternoon = "afternoon"
    evening = "evening"
    wind_down = "wind_down"


# The "always" pseudo-bucket lets a template opt out of hour gating
# entirely. It is intentionally NOT a member of HourBucket — it never
# names an actual time of day, only a selection mode for templates.
ALWAYS_BUCKET: Final[str] = "always"

# Inclusive hour window during which templates that declare wind_down
# are eligible. Per plan.md §451: "19:00-21:00".
WIND_DOWN_HOUR_MIN: Final[int] = 19
WIND_DOWN_HOUR_MAX: Final[int] = 21


def _validate_hour(hour: int) -> None:
    if not isinstance(hour, int) or isinstance(hour, bool):
        raise TypeError(f"hour must be int, got {type(hour).__name__}")
    if hour < 0 or hour > 23:
        raise ValueError(f"hour must be in 0..23, got {hour}")


def hour_bucket(hour: int) -> HourBucket:
    """Return the natural bucket label for the given hour (0..23).

    See module docstring for the full table. This is the label only;
    use :func:`is_eligible` to determine selection.
    """
    _validate_hour(hour)
    if 6 <= hour < 12:
        return HourBucket.morning
    if 12 <= hour < 17:
        return HourBucket.afternoon
    if 17 <= hour < 22:
        return HourBucket.evening
    # 0..5 and 22..23
    return HourBucket.wind_down


def eligible_buckets(hour: int) -> set[str]:
    """Return the set of bucket labels eligible for selection at ``hour``.

    A template's ``buckets`` set must intersect this set (or contain
    ``"always"``) for the template to be eligible.
    """
    _validate_hour(hour)
    natural = hour_bucket(hour)
    out: set[str] = set()
    if natural is not HourBucket.wind_down:
        out.add(natural.value)
    if WIND_DOWN_HOUR_MIN <= hour <= WIND_DOWN_HOUR_MAX:
        out.add(HourBucket.wind_down.value)
    return out


def is_eligible(template_buckets: set[str], hour: int) -> bool:
    """Return True if ``template_buckets`` makes the template selectable at ``hour``.

    A template is eligible iff any of the following hold:

    * The bucket set is empty or contains ``"always"`` — the template
      opts out of time-of-day routing entirely.
    * The bucket set intersects :func:`eligible_buckets` for ``hour``.
    """
    _validate_hour(hour)

    if not template_buckets or ALWAYS_BUCKET in template_buckets:
        return True

    return bool(template_buckets & eligible_buckets(hour))


__all__ = [
    "ALWAYS_BUCKET",
    "HourBucket",
    "WIND_DOWN_HOUR_MAX",
    "WIND_DOWN_HOUR_MIN",
    "eligible_buckets",
    "hour_bucket",
    "is_eligible",
]
