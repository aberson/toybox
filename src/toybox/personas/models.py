"""Phase K Step K1 — Pydantic models for the new persona JSON columns.

Migration 0014 adds three JSON-typed TEXT columns to ``personas``:

* ``role_weights`` -> :class:`RoleWeights`
* ``voice_profile`` -> :class:`VoiceProfile` | None
* ``spontaneity_rates`` -> :class:`SpontaneityRates`

These models validate at the API + loader boundaries (per the
migration's docstring: "Numeric ranges are NOT enforced at the SQL
layer"). :class:`VoiceProfile` is additionally the source of truth for
the Pydantic-to-TS codegen hook (invariant 9): as of Phase Z Z3,
``tools/gen_types_ts.py`` walks its fields and emits the interface
into ``frontend/src/shared/types.ts``. :class:`SpontaneityRates` is
NOT emitted — it is consumed Python-side only (the K15 advance
engine); the kiosk never reads it from the wire.

Module placement decision (per problem statement): persona JSON
column shapes live alongside the persona library, not under
``toybox.activities``. The role + theme + interjection-kind
*taxonomies* (which are content-authoring vocabularies) live under
``toybox.activities``; the persona-side *attribute* models live here.
"""

from __future__ import annotations

import json
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from ..activities.roles import Role

# Per documentation/phase-k-plan.md §5: role_weights values are bounded
# to [0.0, 2.0]; weights are relative — the engine normalizes them per
# role-slot pick.
_ROLE_WEIGHT_MIN: Final[float] = 0.0
_ROLE_WEIGHT_MAX: Final[float] = 2.0

# Per phase-k-plan.md §5: voice_profile rate ∈ [0.5, 2.0]; pitch ∈ [0.0, 2.0].
_VOICE_RATE_MIN: Final[float] = 0.5
_VOICE_RATE_MAX: Final[float] = 2.0
_VOICE_PITCH_MIN: Final[float] = 0.0
_VOICE_PITCH_MAX: Final[float] = 2.0


class RoleWeights(RootModel[dict[str, float]]):
    """Persona-side ``role_weights`` JSON object.

    Maps role-name string -> 0.0..2.0 float bias. Keys MUST be valid
    :class:`~toybox.activities.roles.Role` member values; unknown keys
    raise. Empty mapping means "uniform pick" — the K4 slot-fill
    engine treats every eligible toy as equally likely.

    Defined as a :class:`pydantic.RootModel` so the JSON shape on disk
    is a bare object (no wrapping field).
    """

    model_config = ConfigDict(frozen=True)

    @field_validator("root")
    @classmethod
    def _validate_keys_and_bounds(cls, v: dict[str, float]) -> dict[str, float]:
        valid = {member.value for member in Role}
        for key, weight in v.items():
            if key not in valid:
                raise ValueError(
                    f"role_weights key {key!r} is not a member of Role; "
                    f"valid keys: {sorted(valid)!r}"
                )
            if not isinstance(weight, int | float) or isinstance(weight, bool):
                raise ValueError(
                    f"role_weights[{key!r}] must be a number, got {type(weight).__name__}"
                )
            if weight < _ROLE_WEIGHT_MIN or weight > _ROLE_WEIGHT_MAX:
                raise ValueError(
                    f"role_weights[{key!r}] = {weight} out of range "
                    f"[{_ROLE_WEIGHT_MIN}, {_ROLE_WEIGHT_MAX}]"
                )
        return v


class VoiceProfile(BaseModel):
    """Kiosk TTS voice profile read by ``frontend/src/child/tts.ts``.

    Defaults are NOT enforced here — a NULL DB value short-circuits to
    the browser's system default via the K8 loader. When non-NULL,
    ``rate`` and ``pitch`` are required and constrained to the
    phase-k-plan §5 bounds; ``voice_name`` is optional (some browsers
    expose named voices, others only the system default).

    Phase Z Z3: ``neural_voice`` is the persona's Kokoro voice id
    (e.g. ``am_michael``) for the server-rendered clip path. Optional
    with default ``None`` so every pre-Z3 persisted voice_profile JSON
    (rate/pitch[/voice_name] only) still parses under
    ``extra="forbid"`` — the field is additive-safe. ``None`` means
    "use :data:`toybox.tts.engine.DEFAULT_NEURAL_VOICE`" (resolved by
    the Z4 consumer, not here, so the fallback has one home).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rate: float = Field(ge=_VOICE_RATE_MIN, le=_VOICE_RATE_MAX)
    pitch: float = Field(ge=_VOICE_PITCH_MIN, le=_VOICE_PITCH_MAX)
    voice_name: str | None = Field(default=None, min_length=1, max_length=128)
    neural_voice: str | None = Field(default=None, min_length=1, max_length=64)


class SpontaneityRates(BaseModel):
    """Persona-side spontaneity rate pair.

    Mirrors :class:`toybox.activities.roles.SpontaneityRatePair` but
    keyed under the ``{jokes, songs}`` JSON keys that match the on-disk
    persona JSON shape (the role-side TypedDict uses ``{jokes_rate,
    songs_rate}`` to disambiguate inside Python-only code). The K15
    advance engine computes ``effective_rate = max(persona.<x>,
    max(role.<x>_rate for role in cast))`` per content type, so the
    two shapes must encode the same numeric semantics.

    Defaults to ``{0.0, 0.0}`` — "never interject" — matching the
    migration 0014 default for custom personas.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    jokes: float = Field(default=0.0, ge=0.0, le=1.0)
    songs: float = Field(default=0.0, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# JSON-string helpers — pulled out so the loader + API + tests use one
# canonical encode/decode path. Keeps the "what does NULL voice_profile
# mean" contract in one spot.
# ---------------------------------------------------------------------------


DEFAULT_ROLE_WEIGHTS_JSON: Final[str] = "{}"
DEFAULT_SPONTANEITY_RATES_JSON: Final[str] = '{"jokes":0.0,"songs":0.0}'


def parse_role_weights(raw: str | None) -> RoleWeights:
    """Decode the ``personas.role_weights`` column to :class:`RoleWeights`.

    NULL or empty input becomes an empty mapping (uniform pick).
    """
    if raw is None or raw == "":
        return RoleWeights(root={})
    payload: Any = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"role_weights JSON must decode to an object, got {type(payload).__name__}"
        )
    return RoleWeights(root=payload)


def parse_voice_profile(raw: str | None) -> VoiceProfile | None:
    """Decode the ``personas.voice_profile`` column.

    NULL means "system default" per migration 0014 — return ``None``
    so the kiosk loader's defaulting path stays the single source of
    truth for what "no override" looks like at render time.
    """
    if raw is None or raw == "":
        return None
    payload: Any = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"voice_profile JSON must decode to an object, got {type(payload).__name__}"
        )
    return VoiceProfile.model_validate(payload)


def parse_spontaneity_rates(raw: str | None) -> SpontaneityRates:
    """Decode the ``personas.spontaneity_rates`` column.

    NULL or empty input becomes the default ``{jokes: 0.0, songs: 0.0}``
    — matches migration 0014's column default and the "never interject"
    contract for custom personas.
    """
    if raw is None or raw == "":
        return SpontaneityRates()
    payload: Any = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"spontaneity_rates JSON must decode to an object, got {type(payload).__name__}"
        )
    return SpontaneityRates.model_validate(payload)


__all__ = [
    "DEFAULT_ROLE_WEIGHTS_JSON",
    "DEFAULT_SPONTANEITY_RATES_JSON",
    "RoleWeights",
    "SpontaneityRates",
    "VoiceProfile",
    "parse_role_weights",
    "parse_spontaneity_rates",
    "parse_voice_profile",
]
