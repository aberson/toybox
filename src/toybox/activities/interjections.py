"""Phase K Step K1 — single source of truth for the 4 interjection kinds.

Songs / jokes can be inserted into an activity by one of four surfaces
(see documentation/phase-k-plan.md §1):

* ``embedded`` — a template's own step with ``kind: "song"|"joke"`` and
  ``auto: true``; engine picks a theme-matching corpus entry at advance
  time (K12).
* ``ending`` — a template's optional ``ending_step`` appended after the
  last regular step at activity-creation time (K14).
* ``parent`` — inserted live by the parent via
  ``POST /api/activities/{id}/insert-{joke,song}`` (K15).
* ``spontaneity`` — emergent insert driven by max persona/role rate on
  advance (K15).

Each inserted step carries ``metadata.interjection`` ∈ this enum so the
labeled-events learning loop, parent telemetry, and the kiosk's
narration-attribution layer can distinguish them. All consumers MUST
import :class:`InterjectionKind` and :data:`INTERJECTION_DISPLAY_NAMES`
from this module (code-quality.md §2).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class InterjectionKind(StrEnum):
    """The 4 canonical interjection sources from phase-k-plan §2.

    Stored verbatim on inserted-step ``metadata.interjection`` (see
    ``ActivityStepResponse`` wire shape in phase-k-plan.md §2).
    """

    embedded = "embedded"
    ending = "ending"
    parent = "parent"
    spontaneity = "spontaneity"


INTERJECTION_DISPLAY_NAMES: Final[dict[InterjectionKind, str]] = {
    InterjectionKind.embedded: "Embedded",
    InterjectionKind.ending: "Ending",
    InterjectionKind.parent: "Parent-inserted",
    InterjectionKind.spontaneity: "Spontaneous",
}


__all__ = ["INTERJECTION_DISPLAY_NAMES", "InterjectionKind"]
