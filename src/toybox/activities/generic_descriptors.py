"""Phase K Step K1 — generic-descriptor fallback table.

When the slot-fill engine (K4) cannot fill an ``optional_roles`` slot
because the toy pool is too small, the role assignment falls back to
a generic descriptor (RoleAssignment.generic_descriptor) and that
string lands in the rendered step body via the standard slot-fill
substitution path. Plan-doc references (e.g. documentation/phase-k-plan.md
§"Existing context": "a mysterious stranger", "a friendly villager")
describe the *flavor* of these fallbacks.

The table is a 1:1 ``Role -> str`` mapping so every role has a deterministic,
kid-appropriate fallback. The slot-fill engine treats it as the only
fallback source (no plural list, no randomization in v1) — keeps the
test surface small and the render byte-stable per seed.

Imported by :mod:`toybox.activities.roles` so callers can write either
``from toybox.activities.roles import GENERIC_DESCRIPTORS`` or
``from toybox.activities.generic_descriptors import GENERIC_DESCRIPTORS``
and get the SAME object (``is`` equality per code-quality.md §2).
"""

from __future__ import annotations

# NOTE: do NOT import :class:`Role` from ``.roles`` at module level —
# ``roles.py`` imports from this module, and a back-edge would create a
# circular import. The mapping uses the bare string values (which are
# the StrEnum member values verbatim). The taxonomy-completeness test
# asserts every Role member is keyed here.
from typing import Final

GENERIC_DESCRIPTORS: Final[dict[str, str]] = {
    "friend": "a friendly stranger",
    "quest_giver": "a wise old traveler",
    "guide_mentor": "a kindly mentor",
    "needs_saving": "a worried villager",
    "boss_mini_boss": "a grumpy guardian",
    "big_bad_boss": "a shadowy villain",
    "frenemy": "a mischievous rival",
    "sidekick": "a cheerful helper",
    "trickster": "a silly jester",
    "helper_townsperson": "a friendly villager",
}


__all__ = ["GENERIC_DESCRIPTORS"]
