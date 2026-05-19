"""Phase N Step N2 — ``template_type: "element_microgame"`` structural validator.

Pins the 7-rule structural shape enforced by
:func:`toybox.activities._validator.validate_template` when a template
carries ``template_type === "element_microgame"``:

1. Exactly 4 steps.
2. ``steps[1].kind === "fork"`` with ``choices.length === 2``.
3. ``steps[2].kind === "fork"`` with ``choices.length === 2``.
4. ``steps[0]`` and ``steps[3]`` are ``kind === "text"``.
5. ``element_id`` non-null on every step.
6. ``required_roles`` includes ``"guide_mentor"``.
7. ``ending_step.kind === "song"``.

Plus the pydantic/codegen contract additions that the dev MUST make for
the field to round-trip through codegen into ``frontend/src/shared/types.ts``
so Phase O can read ``template_type`` as a typed field:

* :class:`toybox.activities.models.Template` MUST expose
  ``template_type`` as a model field (otherwise ``extra="ignore"``
  drops it on the floor before the validator sees it).
* ``tools/gen_types_ts.py`` MUST emit ``element_microgame`` into
  ``frontend/src/shared/types.ts``; running codegen a second time must
  be byte-identical (idempotence — the pre-commit hook gates drift via
  ``git diff --exit-code``).

A production-catalog regression test pins that adding the new field +
gate doesn't trip any of the 1243 existing templates — they all parse
through ``Template.model_validate`` and pass ``validate_template``
unchanged because none of them carry ``template_type``.

Fixture placement matches the Phase K / Phase G convention:
``tests/fixtures/activities/<feature>/*.json``. Fixtures are JSON files
so the loader-integration path could be exercised by a future test
without rewriting them; the unit tests in this file load the inner
``templates[0]`` dict directly and round-trip it through
``Template.model_validate`` + :func:`validate_template`.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from toybox.activities._validator import (
    TemplateGraphError,
    validate_template,
)
from toybox.activities.models import Template

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
FIXTURES_DIR: Path = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures"
    / "activities"
    / "element_microgame"
)
PRODUCTION_TEMPLATES_DIR: Path = (
    REPO_ROOT / "src" / "toybox" / "activities" / "templates" / "branching"
)
TYPES_TS_PATH: Path = REPO_ROOT / "frontend" / "src" / "shared" / "types.ts"
GEN_TYPES_TS_SCRIPT: Path = REPO_ROOT / "tools" / "gen_types_ts.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_first_template(fixture_filename: str) -> dict[str, object]:
    """Load ``templates[0]`` from a fixture file as a raw dict.

    The fixtures use the on-disk file-shape (intent + templates list)
    so they can also be dropped into a tmp templates dir for a future
    loader-integration test without rewriting.
    """
    path = FIXTURES_DIR / fixture_filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    templates = payload["templates"]
    assert isinstance(templates, list) and len(templates) == 1
    raw = templates[0]
    assert isinstance(raw, dict)
    return raw


# ---------------------------------------------------------------------------
# Production-catalog regression: no existing template breaks
# ---------------------------------------------------------------------------


def test_existing_production_templates_all_pass_validator() -> None:
    """Adding ``template_type`` + the element_microgame structural gate
    must NOT trip any of the 1243 production templates.

    None of the shipped templates carry ``template_type`` today, so
    the validator's element_microgame branch must be a no-op for
    them. This test acts as the lock-in: any future broadening of the
    gate that accidentally catches a non-element_microgame template
    trips this regression.
    """
    seen = 0
    for json_path in PRODUCTION_TEMPLATES_DIR.rglob("*.json"):
        if json_path.name == "_schema.json":
            continue
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        for raw in payload["templates"]:
            template = Template.model_validate(raw)
            # MUST NOT raise. If a future gate-broadening trips a
            # legacy template, this regression fires with the offending
            # template_id in the TemplateGraphError message.
            validate_template(template)
            seen += 1
    # Phase M shipped 1243 templates; floor at the documented Phase K
    # baseline so this assertion doesn't go stale as Phase N appends
    # more templates in N4.
    assert seen >= 225, (
        f"only {seen} production templates iterated; "
        f"expected ≥ 225 (200 branching + 25 linear baseline)"
    )


# ---------------------------------------------------------------------------
# Valid element_microgame template passes
# ---------------------------------------------------------------------------


def test_valid_element_microgame_template_passes() -> None:
    """A hand-built valid fixture satisfying all 7 rules must
    round-trip through ``Template.model_validate`` and
    :func:`validate_template` without raising.
    """
    raw = _load_first_template("element_microgame_valid.json")
    template = Template.model_validate(raw)
    # Spot-check the field made it onto the model — without this the
    # validator's dispatch on ``template_type`` cannot fire.
    assert getattr(template, "template_type", None) == "element_microgame"
    validate_template(template)  # must not raise


# ---------------------------------------------------------------------------
# Invalid fixtures — one per rule cluster, each raising with a message
# that names the specific violation
# ---------------------------------------------------------------------------


def test_invalid_element_microgame_wrong_step_count() -> None:
    """Rule 1: exactly 4 steps. The fixture ships 3 — gate must fire
    with a message naming the step-count rule and the template id."""
    raw = _load_first_template("element_microgame_invalid_step_count.json")
    template = Template.model_validate(raw)
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "fixture_em_bad_step_count" in msg
    # Message must point at the step-count rule. Accept either phrasing
    # so the dev has wording flexibility — the operator-relevant bit is
    # "4 steps" (the target count).
    assert "4 steps" in msg or "step count" in msg or "exactly 4" in msg


def test_invalid_element_microgame_wrong_fork_position() -> None:
    """Rules 2-4: steps[1] + steps[2] must be forks, steps[0] + steps[3]
    must be text. The fixture puts the first fork at index 0 and a
    plain text step at index 1 (and inverts step 3 too for good
    measure) — gate must fire with a message naming the fork-position
    rule and the offending index."""
    raw = _load_first_template("element_microgame_invalid_fork_position.json")
    template = Template.model_validate(raw)
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "fixture_em_bad_fork_position" in msg
    # Message must mention "fork" + an offending step (either index 0
    # or step 1). Accept multiple phrasings so the dev can write
    # whichever reads naturally.
    assert "fork" in msg
    assert "step 0" in msg or "step 1" in msg or "index 0" in msg or "index 1" in msg


def test_invalid_element_microgame_missing_element_id() -> None:
    """Rule 5: every step must have a non-null element_id. The fixture
    drops element_id on step 2 — gate must fire with a message naming
    ``element_id`` and the offending step index."""
    raw = _load_first_template("element_microgame_invalid_missing_element_id.json")
    template = Template.model_validate(raw)
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "fixture_em_missing_element_id" in msg
    assert "element_id" in msg
    # Offending step is index 2 — message must name it so an operator
    # can find the row.
    assert "step 2" in msg or "index 2" in msg or "fork_b" in msg


def test_invalid_element_microgame_missing_guide_mentor() -> None:
    """Rule 6: required_roles must include ``"guide_mentor"``. The
    fixture declares ``required_roles: ["friend"]`` — gate must fire
    with a message naming ``guide_mentor`` and the rule context."""
    raw = _load_first_template("element_microgame_invalid_missing_guide_mentor.json")
    template = Template.model_validate(raw)
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "fixture_em_missing_guide_mentor" in msg
    assert "guide_mentor" in msg
    assert "required_roles" in msg


# ---------------------------------------------------------------------------
# Inline negative variants — one inline fixture per remaining rule so
# the regression set is symmetric. Each inline variant mutates the
# valid fixture so the test is self-explanatory.
# ---------------------------------------------------------------------------


def test_invalid_element_microgame_ending_step_kind_not_song() -> None:
    """Rule 7: ``ending_step.kind === "song"``. Mutating to ``"joke"``
    must fail with a message naming ``ending_step`` and ``song``."""
    raw = _load_first_template("element_microgame_valid.json")
    raw_mut = copy.deepcopy(raw)
    # Replace the ending_step kind. Phase L removed the runtime
    # ``ending_step`` consumer, but Phase N re-introduces a typed shape
    # so the validator can gate it. The dev's implementation must
    # surface this field somehow (re-add to Template OR walk raw model
    # dump pre-extra-ignore) — exactly how is the dev's call.
    raw_mut["ending_step"] = {"kind": "joke", "auto": True, "element_id": "au-79"}
    template = Template.model_validate(raw_mut)
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "fixture_em_valid" in msg
    assert "ending_step" in msg
    # Message must name the required value so the operator knows what
    # to change to.
    assert "song" in msg


def test_invalid_element_microgame_fork_with_3_choices() -> None:
    """Rules 2 & 3: each fork must have EXACTLY 2 choices. Adding a
    third choice to ``steps[1]`` must trip the gate with a message
    naming the choice-count rule."""
    raw = _load_first_template("element_microgame_valid.json")
    raw_mut = copy.deepcopy(raw)
    # Append a third choice to steps[1]. This still satisfies the
    # underlying jsonschema (``choices: minItems 2, maxItems 4``) so
    # the element_microgame gate is the only thing that can catch it.
    raw_mut["steps"][1]["choices"].append({"label": "Third", "next": "fork_b"})
    template = Template.model_validate(raw_mut)
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "fixture_em_valid" in msg
    # Message must name "choices" and either "2" (the required count)
    # or the offending step.
    assert "choices" in msg
    assert "2" in msg or "step 1" in msg or "index 1" in msg


# ---------------------------------------------------------------------------
# Pydantic model: the field must exist on Template
# ---------------------------------------------------------------------------


def test_template_pydantic_model_has_template_type_field() -> None:
    """The dispatch in ``validate_template`` reads ``template.template_type``;
    without the field on the Pydantic model, ``extra="ignore"`` drops
    the value on the floor and the gate can never fire. This test
    asserts the field exists in ``Template.model_fields`` so a future
    silent removal trips here BEFORE the structural tests fire with
    a misleading "gate never triggers" diagnosis.
    """
    assert "template_type" in Template.model_fields, (
        "Template.model_fields must include 'template_type' so the "
        "validator's element_microgame dispatch can read it. Currently "
        f"fields = {sorted(Template.model_fields.keys())!r}."
    )


def test_template_template_type_defaults_to_none() -> None:
    """Backward-compat: every existing template omits ``template_type``,
    so the default must be a falsy value (``None``) — otherwise the
    1243-template regression above would fail."""
    template = Template.model_validate(
        {
            "id": "no_template_type",
            "title": "t",
            "buckets": ["always"],
            "steps": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        }
    )
    assert template.template_type is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Codegen: types.ts gains element_microgame + is idempotent
# ---------------------------------------------------------------------------


def test_types_ts_has_element_microgame_template_type() -> None:
    """``frontend/src/shared/types.ts`` must contain the substring
    ``element_microgame`` after the codegen runs. Loose assertion —
    the exact emitted form (string literal in a union, optional field
    on the Step or Template interface, etc.) is the dev's call. What
    matters for Phase O is that ``categorize()`` in the frontend can
    distinguish on the typed value rather than a freeform string.
    """
    # Snapshot whatever the codegen most-recently produced. The dev's
    # ``gen_types_ts.py`` change should emit the field; this test
    # reads what's on disk.
    content = TYPES_TS_PATH.read_text(encoding="utf-8")
    assert "element_microgame" in content, (
        "frontend/src/shared/types.ts must include 'element_microgame'. "
        "Run `uv run python tools/gen_types_ts.py` to regenerate."
    )


def test_codegen_is_idempotent() -> None:
    """Running ``tools/gen_types_ts.py`` twice in a row must produce
    byte-identical output. The pre-commit hook gates drift via
    ``git diff --exit-code``; this test guards the same property at
    unit-test scope so a non-determinism regression trips before the
    pre-commit hook does.

    Note: we run the codegen TWICE and compare the two outputs (rather
    than comparing the post-codegen state against the on-disk state)
    because on Windows the on-disk file may carry autocrlf'd CRLFs
    while the codegen writes LF — that delta is environmental, not a
    codegen non-determinism. The test we actually need is "codegen
    output is stable" — which is gen→gen, not disk→gen.
    """
    # First run — normalize the on-disk content to whatever the
    # codegen produces.
    result1 = subprocess.run(
        [sys.executable, str(GEN_TYPES_TS_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result1.returncode == 0, (
        f"gen_types_ts.py (first run) exited {result1.returncode}; "
        f"stdout={result1.stdout!r} stderr={result1.stderr!r}"
    )
    after_first = TYPES_TS_PATH.read_bytes()
    # Second run — must produce the same bytes.
    result2 = subprocess.run(
        [sys.executable, str(GEN_TYPES_TS_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result2.returncode == 0, (
        f"gen_types_ts.py (second run) exited {result2.returncode}; "
        f"stdout={result2.stdout!r} stderr={result2.stderr!r}"
    )
    after_second = TYPES_TS_PATH.read_bytes()
    assert after_first == after_second, (
        "gen_types_ts.py is not idempotent — re-running produced a "
        "different types.ts. Fix the non-determinism in the codegen."
    )
