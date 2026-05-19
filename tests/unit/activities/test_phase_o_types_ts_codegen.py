"""Phase O Step O2 — codegen surface for the new typed fields.

Per ``documentation/phase-o-plan.md`` §5 O2: the pydantic→TS codegen
(``tools/gen_types_ts.py``) must emit the three new typed fields into
``frontend/src/shared/types.ts`` so the frontend's ``categorize()``
helper can read them as typed values (rather than via untyped
``metadata`` lookups).

The three new fields:

* ``Activity.template_id``           (Optional[str] → ``string | null``)
* ``Activity.recommended_themes``    (list[str]      → ``string[]``)
* ``ActivityStep.element_id``        (Optional[str] → ``string | null``)

Codegen idempotence is already pinned by
:file:`tests/unit/activities/test_phase_n_template_type.py::test_codegen_is_idempotent`
— do NOT duplicate here. This module only checks the field-emission
substrings.

Substring assertions accept the two emitter conventions for Optional
fields (``foo: string | null`` vs ``foo?: string | null``) and the
two emitter conventions for list fields (``foo: string[]`` vs
``foo: Array<string>``). Tightening the assertion to one form would
constrain the dev's emitter choice unnecessarily; loose substring
match catches the load-bearing thing (the field name is on the typed
shape).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
TYPES_TS_PATH: Path = REPO_ROOT / "frontend" / "src" / "shared" / "types.ts"


def _types_ts_content() -> str:
    return TYPES_TS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Deliverable C — types.ts gains the three new typed fields
# ---------------------------------------------------------------------------


def test_types_ts_emits_activity_template_id_field() -> None:
    """``types.ts`` must include a ``template_id`` field declaration of
    type ``string | null`` (or ``string | undefined``) on the Activity
    interface. The exact emitter convention is the dev's call — what
    matters is that ``categorize()`` can read a typed value.
    """
    content = _types_ts_content()
    assert "template_id" in content, (
        "frontend/src/shared/types.ts must include a 'template_id' "
        "field declaration on the Activity interface so the parent UI "
        "can read template attribution as a typed value. Run "
        "`uv run python tools/gen_types_ts.py` to regenerate."
    )
    # Loose: a string-typed declaration of any nullable shape.
    has_string_decl = (
        "template_id?: string" in content
        or "template_id: string" in content
    )
    assert has_string_decl, (
        "types.ts has 'template_id' substring but not as a typed "
        "string field declaration. Expected one of: "
        "'template_id: string | null', 'template_id?: string | null', "
        "or the `| undefined` variant. Current content excerpt around "
        f"'template_id':\n"
        f"...{content[max(0, content.find('template_id') - 80):content.find('template_id') + 120]}..."
    )


def test_types_ts_emits_activity_recommended_themes_field() -> None:
    """``types.ts`` must include a ``recommended_themes: string[]`` (or
    ``Array<string>``) field on the Activity interface.
    """
    content = _types_ts_content()
    assert "recommended_themes" in content, (
        "frontend/src/shared/types.ts must include a "
        "'recommended_themes' field on the Activity interface. Run "
        "`uv run python tools/gen_types_ts.py` to regenerate."
    )
    has_array_decl = (
        "recommended_themes: string[]" in content
        or "recommended_themes?: string[]" in content
        or "recommended_themes: Array<string>" in content
        or "recommended_themes?: Array<string>" in content
        or "recommended_themes: ReadonlyArray<string>" in content
    )
    assert has_array_decl, (
        "types.ts has 'recommended_themes' substring but not as a "
        "typed string-array field declaration. Expected "
        "'recommended_themes: string[]' or the Array<string> "
        "equivalent. Excerpt:\n"
        f"...{content[max(0, content.find('recommended_themes') - 80):content.find('recommended_themes') + 120]}..."
    )


def test_types_ts_emits_activity_step_element_id_field() -> None:
    """``types.ts`` must include an ``element_id`` field of type
    ``string | null`` on the ActivityStep interface (NOT just on the
    template-time ``Step`` interface that already exists).

    Existing pre-O2 ``types.ts`` includes ``element_id?: string | null``
    on the ``Step`` template interface (Phase N seeded that). What
    Phase O O2 needs is the same field on the RUNTIME ``ActivityStep``
    interface — the wire shape the parent UI's PlayQueueList consumes.
    A loose substring match would pass on the Phase N emission and
    miss the load-bearing addition; we count occurrences instead so
    a Phase O regen MUST add at least one more.

    Two occurrences is the floor: one on Step (Phase N) + one on
    ActivityStep (Phase O). If a future codegen surfaces element_id
    in additional shapes this floor still holds.
    """
    content = _types_ts_content()
    # All ``element_id`` declarations in the emitted file. Each
    # field-declaration line contains ``element_id`` once.
    occurrences = content.count("element_id")
    assert occurrences >= 2, (
        f"types.ts has 'element_id' only {occurrences} time(s) — "
        "Phase O O2 must add the field to the runtime ActivityStep "
        "interface in addition to the pre-existing template-time Step "
        "interface. Run `uv run python tools/gen_types_ts.py` after "
        "widening the Pydantic ActivityStepResponse model."
    )


def test_types_ts_emits_activity_interface() -> None:
    """The Activity interface itself must appear in types.ts. Pre-O2
    the file does NOT include an Activity interface (the only emitted
    interfaces are Choice + Step, plus the role/template_type/reward
    unions). Phase O O2 either widens the emitter to include
    ActivityResponse-derived shapes or otherwise surfaces the typed
    Activity shape so the frontend can import it from
    ``../../shared/types`` per the plan's categorize.ts signature.
    """
    content = _types_ts_content()
    has_activity_interface = (
        "export interface Activity" in content
        or "export type Activity " in content
        or "export type Activity\n" in content
        or "export type Activity=" in content
    )
    assert has_activity_interface, (
        "frontend/src/shared/types.ts must export an 'Activity' "
        "interface (or type alias) so categorize.ts can import it "
        "from '../../shared/types' per the plan's signature. Today "
        "the Activity shape lives in frontend/src/parent/api.ts as a "
        "hand-rolled interface; Phase O O2 either widens "
        "tools/gen_types_ts.py to emit it from the Pydantic "
        "ActivityResponse model OR re-exports the hand-rolled "
        "interface from shared/types.ts."
    )
