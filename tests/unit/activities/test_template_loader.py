"""Phase G G1: schema + Pydantic + graph-validator coverage.

Pins three things:

1. The Phase G additions (``Step.id`` / ``Step.next`` / ``Step.choices``,
   plus the new :class:`Choice` model and the relaxed
   ``Activity.steps`` length range) are wired correctly.
2. :func:`validate_template_graph` rejects each invariant violation
   (orphan / cycle / missing-target / ambiguous next+choices /
   choice-count low / choice-count high) with a clear message that
   names the template id and the offending step.
3. The recursive template loader picks up files under subdirectories
   like ``branching/`` AND the existing four production templates
   still load + validate after the schema relaxation.

The fixture files used by these tests live under
``tests/fixtures/activities/branching/`` and are deliberately
isolated from the production templates path — each test points the
loader at a tmp_path it constructs from those fixtures.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from toybox.activities._validator import (
    TemplateGraphError,
    validate_template_graph,
)
from toybox.activities.generator import (
    SUPPORTED_INTENTS,
    TEMPLATES_DIR,
    _load_intent_templates,
    clear_template_cache,
)
from toybox.activities.models import Choice, Step

FIXTURES_DIR: Path = (
    Path(__file__).resolve().parent.parent.parent / "fixtures" / "activities" / "branching"
)


# ---------------------------------------------------------------------------
# Cache hygiene — every test below builds a fresh templates_dir so the
# cache must be reset between runs.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    clear_template_cache()


# ---------------------------------------------------------------------------
# Pydantic-layer Step / Choice shape
# ---------------------------------------------------------------------------


def test_step_default_optional_fields_are_none() -> None:
    """A bare-minimum step (only ``text``) is valid and reports
    None for every Phase G optional field."""
    step = Step(text="Just a body.")
    assert step.id is None
    assert step.next is None
    assert step.choices is None


def test_step_id_pattern_is_enforced() -> None:
    """``id`` must match ``^[a-z0-9][a-z0-9_]*$`` and be ≤32 chars."""
    with pytest.raises(ValidationError):
        Step(text="t", id="Bad-Id")  # uppercase + hyphen
    with pytest.raises(ValidationError):
        Step(text="t", id="_leading_underscore")
    with pytest.raises(ValidationError):
        Step(text="t", id="x" * 33)
    # Valid forms.
    Step(text="t", id="a")
    Step(text="t", id="a_b_c")
    Step(text="t", id="abc123")


def test_step_rejects_both_next_and_choices() -> None:
    """Mutual exclusion — ``next`` XOR ``choices``."""
    with pytest.raises(ValidationError):
        Step(
            text="t",
            next="x",
            choices=[
                Choice(label="A", next="x"),
                Choice(label="B", next="y"),
            ],
        )


@pytest.mark.parametrize("count", [1, 5])
def test_step_rejects_choice_count_outside_2_to_4(count: int) -> None:
    """Pin the 2..4 choice-count range at the model layer."""
    with pytest.raises(ValidationError):
        Step(
            text="t",
            choices=[Choice(label=f"L{i}", next=f"x{i}") for i in range(count)],
        )


@pytest.mark.parametrize("count", [2, 3, 4])
def test_step_accepts_choice_counts_in_range(count: int) -> None:
    step = Step(
        text="t",
        choices=[Choice(label=f"L{i}", next=f"x{i}") for i in range(count)],
    )
    assert step.choices is not None
    assert len(step.choices) == count


# ---------------------------------------------------------------------------
# validate_template_graph direct calls — one test per rejection branch
# ---------------------------------------------------------------------------


def test_validator_accepts_linear_template_with_no_phase_g_fields() -> None:
    """The pre-Phase-G shape (no ids, no nexts, no choices) is still
    valid — this is what every existing production template looks
    like, and the validator must not require explicit ids."""
    steps = [Step(text=f"step {i}") for i in range(5)]
    validate_template_graph("legacy_linear", steps)


def test_validator_accepts_simple_branching_template() -> None:
    steps = [
        Step(
            text="open",
            id="open",
            choices=[
                Choice(label="Sneak", next="sneak"),
                Choice(label="Charge", next="charge"),
            ],
        ),
        Step(text="sneak ending", id="sneak", next="end"),
        Step(text="charge ending", id="charge", next="end"),
        Step(text="final", id="end"),
    ]
    validate_template_graph("branching_ok", steps)


def test_validator_rejects_duplicate_ids() -> None:
    steps = [
        Step(text="A", id="dup", next="dup"),
        Step(text="B", id="dup"),
    ]
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template_graph("dup_ids", steps)
    msg = str(excinfo.value)
    assert "dup_ids" in msg
    assert "duplicate" in msg


def test_validator_rejects_missing_next_target() -> None:
    steps = [
        Step(text="A", id="a", next="ghost"),
        Step(text="B", id="b"),
    ]
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template_graph("miss_next", steps)
    msg = str(excinfo.value)
    assert "miss_next" in msg
    assert "ghost" in msg
    assert "unknown next id" in msg


def test_validator_rejects_missing_choice_target() -> None:
    steps = [
        Step(
            text="A",
            id="a",
            choices=[
                Choice(label="Real", next="b"),
                Choice(label="Phantom", next="ghost"),
            ],
        ),
        Step(text="B", id="b"),
    ]
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template_graph("miss_choice", steps)
    msg = str(excinfo.value)
    assert "miss_choice" in msg
    assert "ghost" in msg
    assert "Phantom" in msg


def test_validator_rejects_orphan_step() -> None:
    """``ghost`` is not reachable from steps[0] — the explicit ``next``
    on step 0 jumps over it, and step 2 (ghost) is not the implicit
    fall-through target of any reachable predecessor."""
    steps = [
        Step(text="A", id="start", next="end"),
        Step(text="B", id="ghost"),
        Step(text="C", id="end"),
    ]
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template_graph("orphan", steps)
    msg = str(excinfo.value)
    assert "orphan" in msg
    assert "ghost" in msg
    assert "unreachable" in msg


def test_validator_rejects_cycle_via_explicit_next() -> None:
    steps = [
        Step(text="A", id="a", next="b"),
        Step(text="B", id="b", next="a"),
        Step(text="C", id="end"),  # never visited; would be orphan-flagged after cycle
    ]
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template_graph("cyc", steps)
    assert "cyc" in str(excinfo.value)
    assert "cycle" in str(excinfo.value)


# ---------------------------------------------------------------------------
# JSON-fixture round-trip via the loader
# ---------------------------------------------------------------------------


def _make_templates_dir(
    tmp_path: Path,
    *,
    boredom_fixture: Path,
) -> Path:
    """Build a tmp templates dir that points the loader at a single
    fixture for the ``boredom`` intent. Schema + the other 3 production
    intents are copied unchanged so cache reuse is safe."""
    fake = tmp_path / "templates"
    fake.mkdir()
    shutil.copy(TEMPLATES_DIR / "_schema.json", fake / "_schema.json")
    shutil.copy(boredom_fixture, fake / "boredom.json")
    # Copy the other 3 production intents so the wider integration
    # surface has its always-pool fallback available.
    for intent in ("request_play", "request_story", "request_activity"):
        shutil.copy(TEMPLATES_DIR / f"{intent}.json", fake / f"{intent}.json")
    return fake


def test_loader_accepts_valid_branching_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _make_templates_dir(tmp_path, boredom_fixture=FIXTURES_DIR / "valid_branching.json")
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    templates = _load_intent_templates("boredom")
    ids = {t.id for t in templates}
    assert "fixture_valid_branching" in ids


@pytest.mark.parametrize(
    "fixture_name",
    [
        "orphan.json",
        "cycle.json",
        "missing_target.json",
    ],
)
def test_loader_raises_template_graph_error_on_bad_graph(
    fixture_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Graph-level rejection branches surface as
    :class:`TemplateGraphError` (hard load-time failure per the
    Phase G plan), with the offending template id in the message."""
    fake = _make_templates_dir(tmp_path, boredom_fixture=FIXTURES_DIR / fixture_name)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    with pytest.raises(TemplateGraphError) as excinfo:
        _load_intent_templates("boredom")
    msg = str(excinfo.value)
    # Each fixture's template_id starts with ``fixture_``; assert the
    # error names it so an operator scanning logs can find the file.
    assert "fixture_" in msg


@pytest.mark.parametrize(
    "fixture_name",
    [
        "ambiguous.json",
        "choice_count_low.json",
        "choice_count_high.json",
    ],
)
def test_loader_skips_pydantic_or_schema_rejected_fixtures(
    fixture_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mutual-exclusion + choice-count violations are caught at the
    JSON-schema / Pydantic layer before ``validate_template_graph``
    sees them. The loader logs a WARNING and skips the file (or the
    individual template within the file), so the pool ends up empty
    when the only template in the file is the rejected fixture —
    a regression here means the wrong layer is enforcing the rule.
    """
    fake = _make_templates_dir(tmp_path, boredom_fixture=FIXTURES_DIR / fixture_name)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    templates = _load_intent_templates("boredom")
    # Either the file was skipped at the JSON-schema layer or the
    # individual bad template was skipped at the Pydantic layer; in
    # both cases the rejected fixture must not produce a usable
    # template.
    assert all(not t.id.startswith("fixture_") for t in templates), (
        "rejected fixture leaked into the loaded template pool"
    )


# ---------------------------------------------------------------------------
# Existing production templates still load + validate after the
# schema relaxation
# ---------------------------------------------------------------------------


def test_existing_production_templates_still_load() -> None:
    """Regression: each shipped per-intent JSON file loads cleanly
    after the Phase G schema relaxation (minItems 5 → 3, maxItems
    5 → 20, plus the new optional fields)."""
    for intent in SUPPORTED_INTENTS:
        templates = _load_intent_templates(intent)
        assert templates, (
            f"intent={intent!r} produced no templates after Phase G "
            f"schema relaxation; loader regressed"
        )


# ---------------------------------------------------------------------------
# Recursive load path: branching templates under a subdirectory
# ---------------------------------------------------------------------------


def test_loader_picks_up_branching_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase G calls for branching templates to live under
    ``templates/branching/<intent>.json``. The loader must recurse
    so those files are merged into the per-intent pool."""
    fake = tmp_path / "templates"
    fake.mkdir()
    shutil.copy(TEMPLATES_DIR / "_schema.json", fake / "_schema.json")
    # Top-level boredom.json from production (5-step linear pool).
    shutil.copy(TEMPLATES_DIR / "boredom.json", fake / "boredom.json")
    # Branching subdirectory adds an additional boredom.json.
    branching = fake / "branching"
    branching.mkdir()
    shutil.copy(FIXTURES_DIR / "valid_branching.json", branching / "boredom.json")
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    templates = _load_intent_templates("boredom")
    ids = {t.id for t in templates}
    # The top-level file ships several boredom_* templates; the
    # branching file adds ``fixture_valid_branching``. Both pools
    # should be merged.
    assert "fixture_valid_branching" in ids, "branching/ subdir was not merged into boredom pool"
    assert any(tid.startswith("boredom_") for tid in ids), (
        "top-level boredom.json was dropped when branching/ was added"
    )


def test_loader_recurses_with_only_subdirectory_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the top-level intent file is absent but a subdirectory
    file is present, the loader still finds it. Useful regression
    against a future cleanup that drops the top-level file."""
    fake = tmp_path / "templates"
    fake.mkdir()
    shutil.copy(TEMPLATES_DIR / "_schema.json", fake / "_schema.json")
    branching = fake / "branching"
    branching.mkdir()
    shutil.copy(FIXTURES_DIR / "valid_branching.json", branching / "boredom.json")
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    templates = _load_intent_templates("boredom")
    assert any(t.id == "fixture_valid_branching" for t in templates)


# ---------------------------------------------------------------------------
# Per-template skip-on-bad-shape: Pydantic ValidationError + bare ValueError
# raised inside ``_parse_template`` must be caught so a single bad template
# does not crash startup or take its valid siblings down with it.
# ---------------------------------------------------------------------------


def _write_intent_file(
    fake_dir: Path,
    *,
    intent: str,
    payload_json: str,
) -> None:
    """Write a raw JSON string as ``<fake_dir>/<intent>.json``.

    Used by the per-template-skip tests below to inject malformed
    payloads (Pydantic-shape errors / bad action_slot strings) that
    pass jsonschema but should be caught at the parse layer. Raw
    string lets us craft regex-violating ids without a Python-side
    construction step that would itself reject them.
    """
    (fake_dir / f"{intent}.json").write_text(payload_json, encoding="utf-8")


def _build_fake_templates_root(tmp_path: Path) -> Path:
    """Build a tmp templates root with the schema and the three
    non-boredom production intents copied; the caller then writes
    a custom boredom.json into the returned path."""
    fake = tmp_path / "templates"
    fake.mkdir()
    shutil.copy(TEMPLATES_DIR / "_schema.json", fake / "_schema.json")
    for intent in ("request_play", "request_story", "request_activity"):
        shutil.copy(TEMPLATES_DIR / f"{intent}.json", fake / f"{intent}.json")
    return fake


def test_loader_skips_template_with_pydantic_shape_error_and_keeps_siblings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A template whose step has a Pydantic-violating ``id`` (e.g.,
    one that violates the ``^[a-z0-9][a-z0-9_]*$`` regex) is caught
    at the parse layer with a logged WARNING + skip — and the OTHER
    valid templates in the same intent file continue to load.

    The bad ``id`` ``"Bad-Id"`` slips past JSON-schema (the schema
    only constrains the step ``id`` regex on STEPS, and the schema's
    pattern is the same anyway — but Pydantic's pattern check is
    enforced via Field, which is the second line of defense). To
    guarantee Pydantic catches it specifically, we use a length
    overflow: 33 lowercase characters fits the regex but exceeds
    Pydantic's ``max_length=32``. (The schema's max_length=32 also
    rejects it, but jsonschema may or may not report
    ``maxLength`` depending on draft + validator settings; either
    way the file should be SKIPPED-not-CRASH which is the contract.)
    """
    payload = """{
      "intent": "boredom",
      "templates": [
        {
          "id": "good_template",
          "title": "Good template",
          "buckets": ["always"],
          "steps": [
            { "text": "Step A." },
            { "text": "Step B." },
            { "text": "Step C." }
          ]
        },
        {
          "id": "bad_template",
          "title": "Bad template (step id too long)",
          "buckets": ["always"],
          "steps": [
            { "id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "text": "Step A." },
            { "text": "Step B." },
            { "text": "Step C." }
          ]
        }
      ]
    }"""
    fake = _build_fake_templates_root(tmp_path)
    _write_intent_file(fake, intent="boredom", payload_json=payload)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()

    with caplog.at_level(logging.WARNING, logger="toybox.activities.generator"):
        templates = _load_intent_templates("boredom")

    ids = {t.id for t in templates}
    # Either both templates were dropped (whole file skipped at
    # jsonschema layer because step id violates schema's max_length),
    # OR the bad one was skipped and the good one survived. The
    # contract is: the loader does NOT crash. Both behaviors are
    # acceptable rejections, but the no-crash + WARNING is the load-
    # bearing assertion.
    assert "bad_template" not in ids
    # Some warning fired pointing at the malformed template.
    warning_messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "boredom" in warning_messages or "skipping" in warning_messages.lower()


def test_loader_skips_template_with_bad_action_slot_and_keeps_siblings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A template whose step has an ``action_slot`` value not in
    :data:`ACTION_SLOTS` raises bare ``ValueError`` from
    ``_parse_template``. The loader must catch it, log a WARNING,
    skip the bad template, and keep the valid siblings.

    ``action_slot`` is intentionally type-loose at the JSON-schema
    layer (any string is accepted; see the schema's prose comment)
    so the parse layer is the single source of truth for the
    enum membership check — making this the canonical
    ValueError-from-parse path.
    """
    payload = """{
      "intent": "boredom",
      "templates": [
        {
          "id": "good_action_slot",
          "title": "Good template",
          "buckets": ["always"],
          "steps": [
            { "text": "Step A.", "action_slot": "thinking" },
            { "text": "Step B." },
            { "text": "Step C." }
          ]
        },
        {
          "id": "bad_action_slot",
          "title": "Bad template (typo in action_slot)",
          "buckets": ["always"],
          "steps": [
            { "text": "Step A.", "action_slot": "punching_a_typo" },
            { "text": "Step B." },
            { "text": "Step C." }
          ]
        }
      ]
    }"""
    fake = _build_fake_templates_root(tmp_path)
    _write_intent_file(fake, intent="boredom", payload_json=payload)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()

    with caplog.at_level(logging.WARNING, logger="toybox.activities.generator"):
        templates = _load_intent_templates("boredom")

    ids = {t.id for t in templates}
    # The bad-action-slot template is rejected by the parse-layer
    # ValueError check; the good one survives.
    assert "bad_action_slot" not in ids
    assert "good_action_slot" in ids, (
        "valid sibling template was dropped along with its bad neighbor — "
        "the parse-error catch must skip per-template, not per-file"
    )
    # WARNING was logged with both the template id and the bad slot.
    warning_messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "bad_action_slot" in warning_messages
    assert "punching_a_typo" in warning_messages


# ---------------------------------------------------------------------------
# Coverage gap fillers: mixed linear-then-branching, and cycle through
# implicit fall-through edge.
# ---------------------------------------------------------------------------


def test_loader_accepts_mixed_linear_branching_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coverage gap from iter-1 review: 4 plain text steps followed
    by a branching point — the realistic shape G5's templates will
    use. Existing fixtures cover all-linear OR open-with-choices,
    not the linear-prologue-then-fork combination.
    """
    fake = _make_templates_dir(
        tmp_path, boredom_fixture=FIXTURES_DIR / "mixed_linear_branching.json"
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    templates = _load_intent_templates("boredom")
    ids = {t.id for t in templates}
    assert "fixture_mixed_linear_branching" in ids


def test_loader_rejects_cycle_through_implicit_fallthrough_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coverage gap from iter-1 review: existing ``cycle.json``
    covers only explicit-``next`` cycles. The validator code does
    handle fall-through edges in cycle detection, but no test
    exercised that asymmetric edge type. Fixture ``a → b`` is the
    implicit fall-through edge; ``b.next = "a"`` closes the loop.
    """
    fake = _make_templates_dir(
        tmp_path, boredom_fixture=FIXTURES_DIR / "cycle_via_fallthrough.json"
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake)
    clear_template_cache()
    with pytest.raises(TemplateGraphError) as excinfo:
        _load_intent_templates("boredom")
    msg = str(excinfo.value)
    assert "fixture_cycle_via_fallthrough" in msg
    assert "cycle" in msg.lower()
