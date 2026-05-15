"""Phase K K3: template schema + validator coverage.

Pins:

1. The K3 Pydantic additions (``Step.kind`` / ``Step.corpus_id`` /
   ``Step.auto``, the new :class:`EndingStep` and :class:`Template`
   models, the role / theme / ending-step top-level fields) round-trip
   correctly through Pydantic + jsonschema.
2. :func:`validate_template` rejects each new K3 invariant violation
   (undeclared role placeholder, ``required_roles`` count over the
   distinct-toy ceiling, song/joke step with neither ``corpus_id``
   nor ``auto=True``, song/joke step with BOTH, song/joke step on a
   non-song/joke kind, ending_step out-of-range kind via direct
   instantiation) with a clear message that names the template id
   and the offending field / placeholder.
3. Backward-compat: existing pre-K3 fixtures still load + validate,
   and ALL 200 production branching templates plus the 25 linear
   templates parse cleanly through the new validator path
   (regression assertion for the 200-template no-touch guarantee).
4. Loader-level integration: K3 violations surface as Pydantic
   ValidationError / TemplateGraphError that the loader catches
   as a logged WARNING + skip (Pydantic path) or hard-raise
   (TemplateGraphError path), matching the Phase G precedent.

Fixture files live under ``tests/fixtures/activities/templates_phase_k/``.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from toybox.activities._validator import (
    TemplateGraphError,
    _collect_role_placeholders,
    _distinct_toy_ceiling,
    validate_template,
)
from toybox.activities.generator import (
    SUPPORTED_INTENTS,
    TEMPLATES_DIR,
    _load_intent_templates,
    clear_template_cache,
)
from toybox.activities.models import (
    Choice,
    EndingStep,
    Step,
    Template,
)
from toybox.activities.roles import Role
from toybox.activities.themes import Theme

K3_FIXTURES_DIR: Path = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures"
    / "activities"
    / "templates_phase_k"
)
BRANCHING_FIXTURES_DIR: Path = (
    Path(__file__).resolve().parent.parent.parent / "fixtures" / "activities" / "branching"
)


# ---------------------------------------------------------------------------
# Cache hygiene
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    clear_template_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_template(
    *,
    template_id: str = "t",
    title: str = "Title",
    steps: list[Step] | None = None,
    required_roles: list[Role] | None = None,
    optional_roles: list[Role] | None = None,
    recommended_themes: list[Theme] | None = None,
    ending_step: EndingStep | None = None,
) -> Template:
    return Template(
        id=template_id,
        title=title,
        buckets=["always"],
        steps=steps or [Step(text="a"), Step(text="b"), Step(text="c")],
        required_roles=required_roles or [],
        optional_roles=optional_roles or [],
        recommended_themes=recommended_themes or [],
        ending_step=ending_step,
    )


def _make_templates_dir(tmp_path: Path, *, boredom_fixture: Path) -> Path:
    """Build a tmp templates dir pointing the loader at a K3 fixture
    for the ``boredom`` intent. Other intents copied from production
    so the wider integration surface remains valid."""
    fake = tmp_path / "templates"
    fake.mkdir()
    shutil.copy(TEMPLATES_DIR / "_schema.json", fake / "_schema.json")
    shutil.copy(boredom_fixture, fake / "boredom.json")
    for intent in ("request_play", "request_story", "request_activity"):
        shutil.copy(TEMPLATES_DIR / f"{intent}.json", fake / f"{intent}.json")
    return fake


# ---------------------------------------------------------------------------
# Pydantic-layer Step kind + corpus_id + auto shape
# ---------------------------------------------------------------------------


def test_step_kind_defaults_to_text() -> None:
    """Pre-K3 templates that omit ``kind`` parse as ``kind='text'``
    so the 200 existing branching templates remain valid."""
    step = Step(text="hello")
    assert step.kind == "text"
    assert step.corpus_id is None
    assert step.auto is None


@pytest.mark.parametrize("kind", ["text", "fork", "song", "joke"])
def test_step_kind_accepts_all_four_values(kind: str) -> None:
    if kind in ("song", "joke"):
        step = Step(text="t", kind=kind, auto=True)  # type: ignore[arg-type]
    else:
        step = Step(text="t", kind=kind)  # type: ignore[arg-type]
    assert step.kind == kind


def test_step_kind_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Step(text="t", kind="dance")  # type: ignore[arg-type]


def test_step_song_requires_corpus_or_auto() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Step(text="sing", kind="song")
    assert "corpus_id" in str(excinfo.value) or "auto" in str(excinfo.value)


def test_step_joke_requires_corpus_or_auto() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Step(text="joke", kind="joke")
    assert "corpus_id" in str(excinfo.value) or "auto" in str(excinfo.value)


def test_step_song_rejects_both_corpus_and_auto() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Step(text="sing", kind="song", corpus_id="x", auto=True)
    msg = str(excinfo.value)
    assert "corpus_id" in msg or "auto" in msg


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("text", "corpus_id"),
        ("text", "auto"),
        ("fork", "corpus_id"),
        ("fork", "auto"),
    ],
)
def test_step_non_song_joke_kind_rejects_corpus_or_auto(kind: str, field: str) -> None:
    """``corpus_id`` and ``auto`` are only meaningful on song / joke
    steps — setting either on a text or fork step is a typo in ``kind``."""
    kwargs: dict[str, object] = {"text": "t", "kind": kind}
    if field == "corpus_id":
        kwargs["corpus_id"] = "x"
    else:
        kwargs["auto"] = True
    if kind == "fork":
        kwargs["choices"] = [Choice(label="A", next="a"), Choice(label="B", next="b")]
    with pytest.raises(ValidationError):
        Step(**kwargs)  # type: ignore[arg-type]


def test_step_song_accepts_corpus_id_only() -> None:
    step = Step(text="sing", kind="song", corpus_id="rhyme-01")
    assert step.kind == "song"
    assert step.corpus_id == "rhyme-01"
    assert step.auto is None


def test_step_joke_accepts_auto_only() -> None:
    step = Step(text="joke", kind="joke", auto=True)
    assert step.kind == "joke"
    assert step.corpus_id is None
    assert step.auto is True


# ---------------------------------------------------------------------------
# Pydantic-layer EndingStep shape
# ---------------------------------------------------------------------------


def test_ending_step_accepts_song_and_joke() -> None:
    assert EndingStep(kind="song").kind == "song"
    assert EndingStep(kind="joke").kind == "joke"


def test_ending_step_rejects_text_kind() -> None:
    with pytest.raises(ValidationError):
        EndingStep(kind="text")  # type: ignore[arg-type]


def test_ending_step_rejects_fork_kind() -> None:
    with pytest.raises(ValidationError):
        EndingStep(kind="fork")  # type: ignore[arg-type]


def test_ending_step_auto_defaults_true() -> None:
    es = EndingStep(kind="song")
    assert es.auto is True


def test_ending_step_rejects_auto_false() -> None:
    with pytest.raises(ValidationError):
        EndingStep(kind="song", auto=False)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Pydantic-layer Template shape
# ---------------------------------------------------------------------------


def test_template_minimal_fields_only() -> None:
    """The pre-K3 shape (id + title + steps; no roles, themes, or
    ending_step) still parses — backward-compat for the 200 existing
    branching templates."""
    template = Template(
        id="legacy",
        title="Legacy",
        buckets=["always"],
        steps=[Step(text=f"s{i}") for i in range(3)],
    )
    assert template.required_roles == []
    assert template.optional_roles == []
    assert template.recommended_themes == []
    assert template.ending_step is None


def test_template_round_trip_with_k3_fields() -> None:
    template = Template(
        id="rich",
        title="Rich {quest_giver}",
        buckets=["always"],
        steps=[
            Step(text="{quest_giver} speaks"),
            Step(text="{sidekick} listens"),
            Step(text="end"),
        ],
        required_roles=[Role.quest_giver],
        optional_roles=[Role.sidekick],
        recommended_themes=[Theme.adventure, Theme.magic],
        ending_step=EndingStep(kind="song"),
    )
    assert template.required_roles == [Role.quest_giver]
    assert template.optional_roles == [Role.sidekick]
    assert template.recommended_themes == [Theme.adventure, Theme.magic]
    assert template.ending_step is not None
    assert template.ending_step.kind == "song"


def test_template_rejects_duplicate_required_roles() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Template(
            id="dup",
            title="t",
            buckets=["always"],
            steps=[Step(text="a"), Step(text="b"), Step(text="c")],
            required_roles=[Role.quest_giver, Role.quest_giver],
        )
    assert "duplicates" in str(excinfo.value)


def test_template_rejects_role_in_both_required_and_optional() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Template(
            id="overlap",
            title="t",
            buckets=["always"],
            steps=[Step(text="a"), Step(text="b"), Step(text="c")],
            required_roles=[Role.quest_giver],
            optional_roles=[Role.quest_giver],
        )
    msg = str(excinfo.value)
    assert "quest_giver" in msg


def test_template_rejects_unknown_role_string() -> None:
    """Pydantic gates that every entry is a valid :class:`Role`."""
    with pytest.raises(ValidationError):
        Template(
            id="bad_role",
            title="t",
            buckets=["always"],
            steps=[Step(text="a"), Step(text="b"), Step(text="c")],
            required_roles=["hero"],  # type: ignore[list-item]
        )


def test_template_rejects_unknown_theme_string() -> None:
    with pytest.raises(ValidationError):
        Template(
            id="bad_theme",
            title="t",
            buckets=["always"],
            steps=[Step(text="a"), Step(text="b"), Step(text="c")],
            recommended_themes=["weird"],  # type: ignore[list-item]
        )


def test_template_extra_forbid() -> None:
    """``extra='forbid'`` so an unknown top-level field (e.g. a
    stale rename) fails loudly rather than silently no-oping."""
    with pytest.raises(ValidationError):
        Template.model_validate(
            {
                "id": "weird",
                "title": "t",
                "buckets": ["always"],
                "steps": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                "unknown_field": 1,
            }
        )


# ---------------------------------------------------------------------------
# validate_template direct calls — each K3 gate's rejection branch
# ---------------------------------------------------------------------------


def test_validate_template_accepts_legacy_no_k3_fields() -> None:
    """No roles, no themes, no ending_step — same shape as every
    one of the 200 existing branching templates."""
    template = _build_template()
    validate_template(template)


def test_validate_template_accepts_role_placeholder_in_declared_role() -> None:
    template = _build_template(
        steps=[
            Step(text="{quest_giver} arrives"),
            Step(text="b"),
            Step(text="c"),
        ],
        required_roles=[Role.quest_giver],
    )
    validate_template(template)


def test_validate_template_rejects_undeclared_role_placeholder() -> None:
    template = _build_template(
        steps=[
            Step(text="{quest_giver} and {frenemy} talk"),
            Step(text="b"),
            Step(text="c"),
        ],
        required_roles=[Role.quest_giver],
    )
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "frenemy" in msg
    assert "required_roles" in msg or "optional_roles" in msg


def test_validate_template_accepts_role_placeholder_in_optional_role() -> None:
    template = _build_template(
        steps=[
            Step(text="{sidekick} arrives"),
            Step(text="b"),
            Step(text="c"),
        ],
        optional_roles=[Role.sidekick],
    )
    validate_template(template)


def test_validate_template_accepts_role_placeholder_in_choice_label() -> None:
    """Role placeholders are permitted inside choice labels too —
    the substitutor walks choice labels alongside step text."""
    template = _build_template(
        steps=[
            Step(
                text="open",
                id="open",
                choices=[
                    Choice(label="Greet {sidekick}", next="a"),
                    Choice(label="Ignore them", next="b"),
                ],
            ),
            Step(text="a", id="a"),
            Step(text="b", id="b"),
        ],
        optional_roles=[Role.sidekick],
    )
    validate_template(template)


def test_validate_template_accepts_role_placeholder_in_title() -> None:
    template = _build_template(
        title="{quest_giver} announces",
        required_roles=[Role.quest_giver],
        steps=[Step(text="a"), Step(text="b"), Step(text="c")],
    )
    validate_template(template)


def test_validate_template_legacy_toy_placeholder_still_allowed() -> None:
    """``{toy}`` (the pre-Phase-K single-toy placeholder) is in the
    canonical non-role known-slot list, so it is permitted even when
    no roles are declared. This is what keeps the 200 existing
    branching templates valid."""
    template = _build_template(
        steps=[
            Step(text="{toy} jumps in {room}"),
            Step(text="b"),
            Step(text="c"),
        ],
    )
    validate_template(template)


def test_validate_template_rejects_required_roles_over_ceiling() -> None:
    """Declares 3 required roles but only references 1 in step text —
    distinct-toy ceiling is 1, so the declaration is over the cap.
    Also pins that the TemplateGraphError message names the offending
    ``template.id`` so an operator can find the file from log output."""
    template = _build_template(
        template_id="ceiling_target_abc",
        steps=[
            Step(text="{quest_giver} stands alone"),
            Step(text="b"),
            Step(text="c"),
        ],
        required_roles=[Role.quest_giver, Role.sidekick, Role.frenemy],
    )
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "ceiling_target_abc" in msg
    assert "required_roles" in msg
    assert "distinct" in msg or "ceiling" in msg or "placeholder" in msg


def test_distinct_toy_ceiling_counts_role_placeholders() -> None:
    """Direct helper test so the count semantics are pinned
    independent of the gate that uses them."""
    template = _build_template(
        title="{quest_giver} announces",
        steps=[
            Step(text="{quest_giver} and {sidekick} march"),
            Step(text="{frenemy} blocks the path"),
            Step(text="end"),
        ],
    )
    assert _distinct_toy_ceiling(template) == 3
    role_placeholders = _collect_role_placeholders(template)
    assert role_placeholders == {"quest_giver", "sidekick", "frenemy"}


def test_distinct_toy_ceiling_excludes_legacy_toy_placeholder() -> None:
    """``{toy}`` is the pre-K3 single-toy slot, not a Role —
    ceiling computation excludes it."""
    template = _build_template(
        steps=[
            Step(text="{toy} in {room}"),
            Step(text="b"),
            Step(text="c"),
        ],
    )
    assert _distinct_toy_ceiling(template) == 0


def test_validate_template_accepts_required_roles_equal_to_ceiling() -> None:
    """Edge case: ceiling == required_roles count is fine
    (boundary inclusion test)."""
    template = _build_template(
        steps=[
            Step(text="{quest_giver} and {sidekick}"),
            Step(text="b"),
            Step(text="c"),
        ],
        required_roles=[Role.quest_giver, Role.sidekick],
    )
    validate_template(template)


def test_validate_template_accepts_song_step_with_corpus_id() -> None:
    template = _build_template(
        steps=[
            Step(text="a"),
            Step(text="sing", kind="song", corpus_id="rhyme-01"),
            Step(text="c"),
        ],
    )
    validate_template(template)


def test_validate_template_accepts_joke_step_with_auto() -> None:
    template = _build_template(
        steps=[
            Step(text="a"),
            Step(text="joke", kind="joke", auto=True),
            Step(text="c"),
        ],
    )
    validate_template(template)


def test_validate_template_accepts_ending_step_song() -> None:
    template = _build_template(ending_step=EndingStep(kind="song"))
    validate_template(template)


def test_validate_template_accepts_ending_step_joke() -> None:
    template = _build_template(ending_step=EndingStep(kind="joke"))
    validate_template(template)


def test_validate_template_ending_step_kind_invalid_via_construct() -> None:
    """Hand-construct a Template bypassing Pydantic by directly mutating
    a model attr to simulate a future caller that uses ``model_construct``.
    The defense-in-depth gate inside :func:`validate_template` must
    catch a kind outside {'song', 'joke'} even when Pydantic was
    bypassed.
    """

    # Build a normal template, then swap ``ending_step`` with a stub
    # whose ``kind`` is out of range. Pydantic's ``frozen=True`` on
    # the Template model blocks direct attr assignment, so we use
    # ``model_construct`` to bypass validation entirely.
    bad_ending_step = EndingStep.model_construct(kind="weird", auto=True)  # type: ignore[arg-type]
    template = _build_template(ending_step=bad_ending_step)
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "ending_step" in msg


# ---------------------------------------------------------------------------
# JSON-fixture round-trip through the loader (integration: the
# producer/consumer wire shape — file → schema → Pydantic → validator)
# ---------------------------------------------------------------------------


def test_loader_accepts_valid_role_template_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K3 role-bearing template parses end-to-end through the loader."""
    fake = _make_templates_dir(
        tmp_path, boredom_fixture=K3_FIXTURES_DIR / "valid_role_template.json"
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    templates = _load_intent_templates("boredom")
    ids = {t.id for t in templates}
    assert "fixture_valid_role" in ids


def test_loader_raises_on_undeclared_role_placeholder_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``validate_template`` raises :class:`TemplateGraphError` which
    propagates unchanged through the loader (hard load-time error)."""
    fake = _make_templates_dir(
        tmp_path, boredom_fixture=K3_FIXTURES_DIR / "undeclared_role_placeholder.json"
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    with pytest.raises(TemplateGraphError) as excinfo:
        _load_intent_templates("boredom")
    msg = str(excinfo.value)
    assert "fixture_undeclared_role" in msg
    assert "frenemy" in msg


def test_loader_raises_on_required_roles_over_ceiling_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _make_templates_dir(
        tmp_path, boredom_fixture=K3_FIXTURES_DIR / "required_roles_over_ceiling.json"
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    with pytest.raises(TemplateGraphError) as excinfo:
        _load_intent_templates("boredom")
    msg = str(excinfo.value)
    assert "fixture_over_ceiling" in msg
    assert "required_roles" in msg


def test_loader_accepts_song_step_with_corpus_id_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _make_templates_dir(
        tmp_path, boredom_fixture=K3_FIXTURES_DIR / "song_step_with_corpus_id.json"
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    templates = _load_intent_templates("boredom")
    assert any(t.id == "fixture_song_corpus" for t in templates)


def test_loader_accepts_joke_step_with_auto_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _make_templates_dir(tmp_path, boredom_fixture=K3_FIXTURES_DIR / "joke_step_auto.json")
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    templates = _load_intent_templates("boredom")
    assert any(t.id == "fixture_joke_auto" for t in templates)


def test_loader_skips_song_step_without_source_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A song step missing both ``corpus_id`` and ``auto=true`` is
    caught at the Pydantic layer (``Step._check_song_joke_shape``),
    which the loader catches as a logged WARNING + skip — matching
    the Phase G precedent for per-template Pydantic shape errors."""
    fake = _make_templates_dir(
        tmp_path, boredom_fixture=K3_FIXTURES_DIR / "song_step_missing_source.json"
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    with caplog.at_level(logging.WARNING, logger="toybox.activities.generator"):
        templates = _load_intent_templates("boredom")
    ids = {t.id for t in templates}
    assert "fixture_song_missing" not in ids
    # The Phase G precedent promises a WARNING-level log naming the
    # skipped template id; verify it actually fires so a future
    # silent-drop regression trips this test.
    assert any(
        rec.levelno == logging.WARNING and "fixture_song_missing" in rec.getMessage()
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# 200-template regression: every production branching template + every
# linear template still loads cleanly post-K3.
# ---------------------------------------------------------------------------


def test_all_production_templates_still_load_post_k3() -> None:
    """Regression for the K3 backward-compat guarantee: the 200
    branching templates (50 per intent × 4 intents) plus the 25
    linear templates parse cleanly through the new validator path.

    A future edit that tightens any K3 gate in a way that breaks an
    existing template trips this test rather than silently dropping
    templates at runtime.
    """
    expected_minimums = {
        "request_play": 60,  # 10 linear + 50 branching
        "request_story": 55,  # 5 linear + 50 branching
        "request_activity": 55,  # 5 linear + 50 branching
        "boredom": 55,  # 5 linear + 50 branching
    }
    total = 0
    for intent in SUPPORTED_INTENTS:
        templates = _load_intent_templates(intent)
        assert len(templates) >= expected_minimums[intent], (
            f"intent={intent!r} produced {len(templates)} templates; "
            f"expected ≥ {expected_minimums[intent]} (a K3 regression "
            f"dropped a production template)"
        )
        total += len(templates)
    # 25 linear + 200 branching = 225 minimum.
    assert total >= 225, (
        f"total templates {total} below the 225 backward-compat baseline; "
        f"K3 regressed a production template"
    )


def test_all_production_templates_pass_validate_template() -> None:
    """Stronger regression: explicitly run :func:`validate_template`
    against every production template's reconstructed Pydantic
    :class:`Template`. Pins that the new K3 placeholder / ceiling /
    kind-shape gates do not fire on any existing template.
    """
    import json

    schema_path = TEMPLATES_DIR / "_schema.json"
    assert schema_path.exists()
    # Walk every production JSON file and parse each template through
    # the same Pydantic + validator path the loader uses.
    seen = 0
    for json_path in TEMPLATES_DIR.rglob("*.json"):
        if json_path.name == "_schema.json":
            continue
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        for raw in payload["templates"]:
            template = Template.model_validate(raw)
            # validate_template MUST NOT raise on any production template.
            validate_template(template)
            seen += 1
    assert seen >= 225, (
        f"only {seen} production templates iterated; expected ≥ 225 (200 branching + 25 linear)"
    )


# ---------------------------------------------------------------------------
# Pre-K3 fixtures still load post-K3 (regression for the K3 backward-
# compat guarantee at the fixture level)
# ---------------------------------------------------------------------------


def test_pre_k3_valid_branching_fixture_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase G valid-branching fixture (no K3 fields) still
    loads + validates after the K3 validator was wired into the
    parse path."""
    fake = _make_templates_dir(
        tmp_path, boredom_fixture=BRANCHING_FIXTURES_DIR / "valid_branching.json"
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    templates = _load_intent_templates("boredom")
    ids = {t.id for t in templates}
    assert "fixture_valid_branching" in ids
