"""Persona library + loader.

Phase A Step 3. Ships the four built-in archetype personas (wizard, princess,
detective, periodic_table) as JSON-Schema-validated JSON files alongside
solid-color placeholder PNG avatars. The :mod:`toybox.personas.loader` module
walks the library directory at startup and upserts rows into the ``personas``
table.
"""

from __future__ import annotations

from .loader import LIBRARY_DIR, load_library_personas
from .models import (
    DEFAULT_ROLE_WEIGHTS_JSON,
    DEFAULT_SPONTANEITY_RATES_JSON,
    RoleWeights,
    SpontaneityRates,
    VoiceProfile,
    parse_role_weights,
    parse_spontaneity_rates,
    parse_voice_profile,
)

__all__ = [
    "DEFAULT_ROLE_WEIGHTS_JSON",
    "DEFAULT_SPONTANEITY_RATES_JSON",
    "LIBRARY_DIR",
    "RoleWeights",
    "SpontaneityRates",
    "VoiceProfile",
    "load_library_personas",
    "parse_role_weights",
    "parse_spontaneity_rates",
    "parse_voice_profile",
]
