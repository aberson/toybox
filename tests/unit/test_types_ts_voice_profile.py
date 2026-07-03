"""Phase Z Z3 — codegen surface for the VoiceProfile wire shape.

Per phase-z-persona-voices-plan.md §5/§7 Z3: the pydantic→TS codegen
(``tools/gen_types_ts.py``) emits :class:`toybox.personas.models.VoiceProfile`
into ``frontend/src/shared/types.ts``, including the new
``neural_voice`` field, so the kiosk's Z5 passthrough reads a typed
value instead of a hand-mirrored shape.

Codegen idempotence is already pinned by
tests/unit/activities/test_phase_n_template_type.py::test_codegen_is_idempotent
— do NOT duplicate here. Substring assertions follow the loose-match
convention of test_phase_o_types_ts_codegen.py (field presence is the
load-bearing thing, not the emitter's exact optional/null spelling).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
TYPES_TS_PATH: Path = REPO_ROOT / "frontend" / "src" / "shared" / "types.ts"


def _types_ts_content() -> str:
    return TYPES_TS_PATH.read_text(encoding="utf-8")


def test_types_ts_emits_voice_profile_interface() -> None:
    content = _types_ts_content()
    assert "export interface VoiceProfile" in content, (
        "frontend/src/shared/types.ts must export a 'VoiceProfile' "
        "interface derived from toybox.personas.models.VoiceProfile. "
        "Run `uv run python tools/gen_types_ts.py` to regenerate."
    )


def test_types_ts_voice_profile_has_neural_voice_field() -> None:
    content = _types_ts_content()
    assert "neural_voice" in content, (
        "frontend/src/shared/types.ts must include the Z3 'neural_voice' "
        "field on the VoiceProfile interface. Run "
        "`uv run python tools/gen_types_ts.py` to regenerate."
    )
    has_string_decl = "neural_voice?: string" in content or "neural_voice: string" in content
    assert has_string_decl, (
        "types.ts has 'neural_voice' substring but not as a typed string "
        "field declaration (expected 'neural_voice?: string | null' or a "
        "nullable variant)."
    )


def test_types_ts_voice_profile_keeps_required_scalars() -> None:
    """rate/pitch are required (no '?') — the pydantic model requires
    them when a profile is non-null, and the emitter derives
    optionality from ``field.is_required()``."""
    content = _types_ts_content()
    assert "rate: number;" in content
    assert "pitch: number;" in content
