"""Phase K Step K1 — interjection-kind taxonomy.

Phase K originally defined four interjection kinds (``embedded``,
``ending``, ``parent``, ``spontaneity``). Phase L Step L5 deleted the
three implicit surfaces (embedded mid-activity picker, ending append,
spontaneity advance-hook) when jokes/songs moved from emergent
interjections to per-activity reward types. The ``parent`` member
remains because Surface P (``POST /api/activities/{id}/insert-{joke,
song}``) is the manual control the parent uses to drop an interjection
into a running activity.

All callers MUST import :class:`InterjectionKind` and
:data:`INTERJECTION_DISPLAY_NAMES` from this module (code-quality.md
§2 single-source-of-truth).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class InterjectionKind(StrEnum):
    """The interjection sources still in use after Phase L Step L5.

    Stored verbatim on inserted-step ``metadata.interjection`` (see
    ``ActivityStepResponse`` wire shape in phase-k-plan.md §2). The
    enum is intentionally kept as a StrEnum (rather than dropped to a
    bare string literal) so a future reintroduction of another
    interjection surface lands as one enum-member addition rather than
    a new producer-consumer string pair.
    """

    parent = "parent"


INTERJECTION_DISPLAY_NAMES: Final[dict[InterjectionKind, str]] = {
    InterjectionKind.parent: "Parent-inserted",
}


__all__ = ["INTERJECTION_DISPLAY_NAMES", "InterjectionKind"]
