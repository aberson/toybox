"""Phase K K3: template schema + validator coverage.

Pins:

1. The K3 Pydantic additions (``Step.kind`` / ``Step.corpus_id`` /
   ``Step.auto``, the :class:`Template` model, the role / theme
   top-level fields) round-trip correctly through Pydantic + jsonschema.
2. :func:`validate_template` rejects each surviving K3 invariant
   violation (undeclared role placeholder, ``required_roles`` count
   over the distinct-toy ceiling, song/joke step missing ``corpus_id``,
   song/joke step with ``auto=True`` after the L5 picker deletion,
   song/joke fields on a non-song/joke kind) with a clear message that
   names the template id and the offending field / placeholder. Phase
   L Step L5 removed the ``ending_step`` shape gate and the K14.1
   ``recommended_themes``-required gate when the embedded/ending
   surfaces were deleted, and additionally now refuses ``auto=True`` on
   song/joke steps because the advance-time embedded picker that
   consumed it is gone — without the picker the placeholder body text
   would render verbatim on the kiosk.
3. Backward-compat: existing pre-K3 fixtures still load + validate,
   and ALL 200 production branching templates plus the 25 linear
   templates parse cleanly through the new validator path. After
   Phase L Step L5 the templates' lingering ``ending_step`` keys are
   silently ignored by Pydantic.
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
) -> Template:
    return Template(
        id=template_id,
        title=title,
        buckets=["always"],
        steps=steps or [Step(text="a"), Step(text="b"), Step(text="c")],
        required_roles=required_roles or [],
        optional_roles=optional_roles or [],
        recommended_themes=recommended_themes or [],
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
        # Phase L Step L5: ``auto=true`` no longer valid — pin a corpus_id.
        step = Step(text="t", kind=kind, corpus_id="rhyme-01")  # type: ignore[arg-type]
    else:
        step = Step(text="t", kind=kind)  # type: ignore[arg-type]
    assert step.kind == kind


def test_step_kind_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Step(text="t", kind="dance")  # type: ignore[arg-type]


def test_step_song_requires_corpus_id() -> None:
    """Phase L Step L5: ``auto=true`` was the only alternative to
    ``corpus_id``; with the embedded picker gone, ``corpus_id`` is
    now the sole valid source-of-content for song steps."""
    with pytest.raises(ValidationError) as excinfo:
        Step(text="sing", kind="song")
    assert "corpus_id" in str(excinfo.value)


def test_step_joke_requires_corpus_id() -> None:
    """Phase L Step L5: ``auto=true`` was the only alternative to
    ``corpus_id``; with the embedded picker gone, ``corpus_id`` is
    now the sole valid source-of-content for joke steps."""
    with pytest.raises(ValidationError) as excinfo:
        Step(text="joke", kind="joke")
    assert "corpus_id" in str(excinfo.value)


def test_step_song_rejects_auto_true() -> None:
    """Phase L Step L5: ``auto=true`` on a song step is rejected
    because the advance-time embedded picker that consumed it
    (``_pick_embedded_corpus_step`` in ``api/activities.py``) was
    deleted. Without that picker, the template's placeholder body
    text would render verbatim on the kiosk."""
    with pytest.raises(ValidationError) as excinfo:
        Step(text="sing", kind="song", auto=True)
    msg = str(excinfo.value)
    assert "auto" in msg
    # Error message must point at the L5 plan section so the operator
    # can find the deletion that motivated the rejection.
    assert "L5" in msg


def test_step_joke_rejects_auto_true() -> None:
    """Phase L Step L5: ``auto=true`` on a joke step is rejected (same
    rationale as the song-step rejection above)."""
    with pytest.raises(ValidationError) as excinfo:
        Step(text="joke", kind="joke", auto=True)
    msg = str(excinfo.value)
    assert "auto" in msg
    assert "L5" in msg


def test_step_song_with_corpus_id_and_auto_true_rejected() -> None:
    """``auto=true`` is rejected regardless of whether ``corpus_id`` is
    also set; the auto field is dead under Phase L Step L5."""
    with pytest.raises(ValidationError) as excinfo:
        Step(text="sing", kind="song", corpus_id="x", auto=True)
    msg = str(excinfo.value)
    assert "auto" in msg


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


def test_step_joke_accepts_corpus_id_only() -> None:
    """Phase L Step L5: ``auto=true`` is dead; ``corpus_id`` is the
    sole valid source-of-content for joke steps."""
    step = Step(text="joke", kind="joke", corpus_id="silly-01")
    assert step.kind == "joke"
    assert step.corpus_id == "silly-01"
    assert step.auto is None


# ---------------------------------------------------------------------------
# Pydantic-layer Template shape
# ---------------------------------------------------------------------------


def test_template_minimal_fields_only() -> None:
    """The pre-K3 shape (id + title + steps; no roles, themes) still
    parses — backward-compat for the 200 existing branching templates."""
    template = Template(
        id="legacy",
        title="Legacy",
        buckets=["always"],
        steps=[Step(text=f"s{i}") for i in range(3)],
    )
    assert template.required_roles == []
    assert template.optional_roles == []
    assert template.recommended_themes == []


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
    )
    assert template.required_roles == [Role.quest_giver]
    assert template.optional_roles == [Role.sidekick]
    assert template.recommended_themes == [Theme.adventure, Theme.magic]


def test_template_parses_legacy_ending_step_field() -> None:
    """Phase N N2 re-introduced the ``ending_step`` field on ``Template``
    (Phase L Step L5 had dropped it) so the element_microgame
    structural validator can gate ``ending_step.kind == "song"``. This
    test pins the new behavior: the field parses cleanly onto the
    model for any template that carries it, with a minimal typed
    ``EndingStep`` shape that exposes ``kind``.
    """
    template = Template.model_validate(
        {
            "id": "legacy_ending",
            "title": "Legacy with ending",
            "buckets": ["always"],
            "steps": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
            "ending_step": {"kind": "song", "auto": True},
        }
    )
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


def test_template_extra_ignore() -> None:
    """Phase L Step L5 relaxed ``extra='forbid'`` to ``extra='ignore'``
    so existing template JSONs that still carry the deprecated
    ``ending_step`` key parse cleanly. The trade-off is that an
    unknown top-level field is silently dropped rather than raised.
    Future drift on the schema's required-field list still fails
    loudly via missing-field validation.
    """
    template = Template.model_validate(
        {
            "id": "weird",
            "title": "t",
            "buckets": ["always"],
            "steps": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
            "unknown_field": 1,
        }
    )
    assert not hasattr(template, "unknown_field")


# ---------------------------------------------------------------------------
# validate_template direct calls — each K3 gate's rejection branch
# ---------------------------------------------------------------------------


def test_validate_template_accepts_legacy_no_k3_fields() -> None:
    """No roles, no themes — same shape as every one of the 200
    existing branching templates (the lingering ``ending_step`` key on
    those JSONs is dropped by Pydantic's ``extra="ignore"`` config)."""
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


def test_validate_template_accepts_joke_step_with_corpus_id() -> None:
    """Phase L Step L5: the previous ``auto=true`` variant of this
    test was retired alongside the embedded picker. ``corpus_id`` is
    the sole valid source-of-content for joke steps; this test pins
    the validator accepts the (now sole) shape end-to-end."""
    template = _build_template(
        steps=[
            Step(text="a"),
            Step(text="joke", kind="joke", corpus_id="silly-01"),
            Step(text="c"),
        ],
    )
    validate_template(template)


def test_validate_template_rejects_joke_step_with_auto_true() -> None:
    """Phase L Step L5: ``auto=true`` on a joke step must be rejected
    at the :func:`validate_template` layer as well as the Pydantic
    layer — defense-in-depth per code-quality.md §1 (one place gates
    once Step is constructed via ``model_validate``, the other
    catches callers that bypass the Pydantic gate). Pydantic catches
    this at ``Step`` construction, so the assertion target is the
    Pydantic ``ValidationError`` raised inside ``_build_template`` /
    ``Step(...)``."""
    with pytest.raises(ValidationError) as excinfo:
        _build_template(
            steps=[
                Step(text="a"),
                Step(text="joke", kind="joke", auto=True),
                Step(text="c"),
            ],
        )
    assert "auto" in str(excinfo.value)


def test_validate_template_rejects_song_step_with_auto_true() -> None:
    """Same shape as the joke-step rejection — pin both for symmetry."""
    with pytest.raises(ValidationError) as excinfo:
        _build_template(
            steps=[
                Step(text="a"),
                Step(text="sing", kind="song", auto=True),
                Step(text="c"),
            ],
        )
    assert "auto" in str(excinfo.value)


# Phase L Step L5: the K14.1 ``recommended_themes`` gate for ``auto:
# true`` song/joke steps and the K3.3 ``ending_step`` gate were both
# removed alongside the deletion of the embedded mid-activity picker
# and the ending auto-append. The test cases that exercised those
# gates have been deleted accordingly. The remaining validator checks
# (K3.1 placeholder / K3.2 ceiling / K3.4 corpus-id XOR auto) still
# fire and are covered by tests above.


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


def test_loader_skips_joke_step_with_auto_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Phase L Step L5: a joke step with ``auto: true`` is now caught
    at the Pydantic layer (``Step._check_song_joke_shape``), which the
    loader catches as a logged WARNING + skip — matching the Phase G
    precedent for per-template Pydantic shape errors. Before L5 this
    fixture was the happy-path test; it is now the regression guard
    for the picker-removal."""
    fake = _make_templates_dir(tmp_path, boredom_fixture=K3_FIXTURES_DIR / "joke_step_auto.json")
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    with caplog.at_level(logging.WARNING, logger="toybox.activities.generator"):
        templates = _load_intent_templates("boredom")
    ids = {t.id for t in templates}
    assert "fixture_joke_auto" not in ids
    assert any(
        rec.levelno == logging.WARNING and "fixture_joke_auto" in rec.getMessage()
        for rec in caplog.records
    )


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
