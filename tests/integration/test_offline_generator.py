"""Coverage for the Phase A Step 7 offline activity generator."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from toybox.activities import (
    Activity,
    ActivityStep,
    HourBucket,
    generate,
    hour_bucket,
    is_eligible,
)
from toybox.activities.generator import (
    DEFAULT_TOY_NAME,
    SUPPORTED_INTENTS,
    TEMPLATES_DIR,
    _load_intent_templates,
    clear_template_cache,
)
from toybox.activities.time_of_day import (
    ALWAYS_BUCKET,
    WIND_DOWN_HOUR_MAX,
    WIND_DOWN_HOUR_MIN,
    eligible_buckets,
)

# ---------------------------------------------------------------------------
# Cache hygiene
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Reset the in-process template cache between tests so file changes
    or fault-injection in any single test don't leak across the suite."""
    clear_template_cache()


# ---------------------------------------------------------------------------
# Time-of-day mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, HourBucket.wind_down),
        (5, HourBucket.wind_down),
        (6, HourBucket.morning),
        (11, HourBucket.morning),
        (12, HourBucket.afternoon),
        (16, HourBucket.afternoon),
        (17, HourBucket.evening),
        (21, HourBucket.evening),
        (22, HourBucket.wind_down),
        (23, HourBucket.wind_down),
    ],
)
def test_hour_bucket_table(hour: int, expected: HourBucket) -> None:
    assert hour_bucket(hour) is expected


@pytest.mark.parametrize("hour", [-1, 24, 100])
def test_hour_bucket_rejects_out_of_range(hour: int) -> None:
    with pytest.raises(ValueError):
        hour_bucket(hour)


def test_eligible_buckets_at_19_includes_wind_down_and_evening() -> None:
    assert eligible_buckets(19) == {"evening", "wind_down"}


def test_eligible_buckets_at_3_excludes_wind_down() -> None:
    # Hour 3's natural bucket is "wind_down" but per plan it's NOT
    # eligible outside 19..21 — eligible set is empty. Hour 22 is the
    # symmetric case (natural wind_down, outside the gate window) and
    # is covered by the same rule.
    assert eligible_buckets(3) == set()


def test_eligible_buckets_at_21_includes_both() -> None:
    assert eligible_buckets(21) == {"evening", "wind_down"}


def test_is_eligible_always_bypasses_window() -> None:
    # Empty bucket sets are also treated as "no preference" — same
    # codepath, both branches return True at every hour.
    for h in range(24):
        assert is_eligible({ALWAYS_BUCKET}, h)
        assert is_eligible(set(), h)


def test_is_eligible_wind_down_only_template_is_gated() -> None:
    template = {"wind_down"}
    eligible_hours = [h for h in range(24) if is_eligible(template, h)]
    expected = list(range(WIND_DOWN_HOUR_MIN, WIND_DOWN_HOUR_MAX + 1))
    assert eligible_hours == expected


def test_is_eligible_morning_template_only_morning() -> None:
    template = {"morning"}
    eligible_hours = [h for h in range(24) if is_eligible(template, h)]
    assert eligible_hours == list(range(6, 12))


def test_is_eligible_evening_template_picks_up_wind_down_window_too() -> None:
    # An "evening" template is naturally eligible 17..21 (those are
    # the hours whose natural bucket is "evening"). The window logic
    # doesn't extend it further.
    template = {"evening"}
    eligible_hours = [h for h in range(24) if is_eligible(template, h)]
    assert eligible_hours == list(range(17, 22))


# ---------------------------------------------------------------------------
# Template library: schema + IDs
# ---------------------------------------------------------------------------


def test_all_intent_template_files_validate() -> None:
    schema = json.loads((TEMPLATES_DIR / "_schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for intent in SUPPORTED_INTENTS:
        path = TEMPLATES_DIR / f"{intent}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        validator.validate(payload)
        assert payload["intent"] == intent


def test_every_intent_has_at_least_one_always_template() -> None:
    """The always-pool fallback assumes every intent ships at least
    one always template — pin that contract."""
    for intent in SUPPORTED_INTENTS:
        templates = _load_intent_templates(intent)
        always = [t for t in templates if ALWAYS_BUCKET in t.buckets]
        assert always, f"intent {intent} has no always-bucket template"


def test_every_intent_has_one_template_per_bucket() -> None:
    for intent in SUPPORTED_INTENTS:
        templates = _load_intent_templates(intent)
        all_buckets: set[str] = set()
        for t in templates:
            all_buckets.update(t.buckets)
        assert {"morning", "afternoon", "evening", "wind_down", "always"}.issubset(all_buckets), (
            f"intent {intent} missing bucket coverage: {all_buckets}"
        )


def test_template_ids_are_unique_across_intents() -> None:
    seen: dict[str, str] = {}
    for intent in SUPPORTED_INTENTS:
        for t in _load_intent_templates(intent):
            assert t.id not in seen, (
                f"duplicate template id {t.id!r} in {intent} (also in {seen[t.id]})"
            )
            seen[t.id] = intent


# ---------------------------------------------------------------------------
# Generator: required Activity shape
# ---------------------------------------------------------------------------


def test_generate_returns_5_steps() -> None:
    a = generate("request_play", "unicorns", None, 10, 42)
    assert isinstance(a, Activity)
    assert len(a.steps) == 5
    # Step indices are 0..4 in order.
    assert [s.step_index for s in a.steps] == [0, 1, 2, 3, 4]
    for s in a.steps:
        assert isinstance(s, ActivityStep)
        assert s.text


def test_generate_metadata_carries_sorted_slot_values_and_hour_bucket() -> None:
    a = generate("request_play", "unicorns", None, 10, 42)
    md = a.metadata
    assert "slot_values" in md
    assert "hour_bucket" in md
    assert md["hour_bucket"] == "morning"
    # Sorted + immutable tuple: pin the contract for Phase D step 19's
    # signature. Tuple (not list) so accidental post-construction
    # mutation raises rather than silently changing future signatures.
    sv = md["slot_values"]
    assert isinstance(sv, tuple)
    assert sv == tuple(sorted(sv))


def test_generate_default_toy_substituted_when_no_real_toy() -> None:
    # Phase A placeholder — must literally inject "Mr. Unicorn".
    a = generate("request_story", "dragons", None, 8, 7)
    body = " ".join(s.text for s in a.steps)
    assert DEFAULT_TOY_NAME in body or DEFAULT_TOY_NAME in a.title


def test_generate_persona_id_passed_through() -> None:
    a = generate("request_play", None, None, 10, 1, persona_id="wizard")
    assert a.persona_id == "wizard"


def test_activity_is_frozen() -> None:
    from pydantic import ValidationError

    a = generate("request_play", "unicorns", None, 10, 1)
    with pytest.raises(ValidationError):
        a.title = "mutated"


# ---------------------------------------------------------------------------
# Generator: determinism
# ---------------------------------------------------------------------------


def test_generate_is_deterministic_with_same_inputs() -> None:
    a = generate("request_play", "unicorns", {"key": "v"}, 10, 42)
    b = generate("request_play", "unicorns", {"key": "v"}, 10, 42)
    assert a == b
    assert a.id == b.id


def test_generate_different_seeds_can_produce_different_uuids() -> None:
    """Same intent/slot/hour but different seeds must derive different
    UUIDs (otherwise the seed isn't part of the determinism key)."""
    a = generate("request_play", "unicorns", None, 10, 1)
    b = generate("request_play", "unicorns", None, 10, 2)
    # Either the template differs or the UUID differs (or both). The
    # load-bearing claim is that the inputs are part of the determinism
    # key — so the activities must not be byte-identical.
    assert a != b


def test_generate_different_context_produces_different_uuid() -> None:
    a = generate("request_play", "unicorns", {"k": 1}, 10, 1)
    b = generate("request_play", "unicorns", {"k": 2}, 10, 1)
    # Same template selection (no randomness here from context), but
    # the UUID derivation feeds context in.
    assert a.id != b.id


# ---------------------------------------------------------------------------
# Generator: time-of-day routing
# ---------------------------------------------------------------------------


def test_wind_down_eligible_at_hour_21() -> None:
    """At hour 21, wind_down templates are eligible — across many
    seeds at least one must pick a wind_down template (alongside
    evening + always candidates)."""
    wind_down_ids: set[str] = set()
    for t in _load_intent_templates("request_story"):
        if "wind_down" in t.buckets:
            wind_down_ids.add(t.id)
    assert wind_down_ids

    seen_wind_down = False
    for seed in range(100):
        a = generate("request_story", "dragons", None, 21, seed)
        if a.template_id in wind_down_ids:
            seen_wind_down = True
            break
    assert seen_wind_down, "wind_down template never selected across 100 seeds at hour 21"


def test_wind_down_not_eligible_at_hour_3() -> None:
    """At hour 3 (natural bucket: wind_down) the wind_down templates
    are STILL not eligible per plan — the gate is hours 19..21
    inclusive. The fallback should kick in to an always template."""
    for seed in range(20):
        a = generate("request_story", "dragons", None, 3, seed)
        # The picked template must NOT have 'wind_down' as its only
        # bucket — i.e. its bucket set must contain "always" OR be
        # empty (since natural bucket at hour 3 has empty eligible
        # set per the plan rule).
        templates_by_id = {
            t.id: t for intent in SUPPORTED_INTENTS for t in _load_intent_templates(intent)
        }
        chosen = templates_by_id[a.template_id]
        assert ALWAYS_BUCKET in chosen.buckets or not chosen.buckets, (
            f"at hour 3 picked non-always template {chosen.id} with buckets {chosen.buckets}"
        )


# ---------------------------------------------------------------------------
# Generator: slot substitution
# ---------------------------------------------------------------------------


def test_generate_substitutes_slot_into_text() -> None:
    a = generate("request_play", "unicorns", None, 10, 42)
    full_text = a.title + " " + " ".join(s.text for s in a.steps)
    # If any step had {slot} it should now contain "unicorns".
    # Templates may or may not include {slot}; assert the placeholder
    # is at least gone everywhere.
    assert "{slot}" not in full_text
    assert "{toy}" not in full_text


def test_generate_with_none_slot_still_works() -> None:
    # No {slot} placeholder filled by a real value, but the activity
    # still has 5 steps and no leftover placeholders.
    a = generate("boredom", None, None, 20, 1)
    assert len(a.steps) == 5
    full_text = a.title + " " + " ".join(s.text for s in a.steps)
    assert "{slot}" not in full_text
    assert "{toy}" not in full_text
    # When slot is None, slot_values is empty.
    assert a.metadata["slot_values"] == ()


def test_parametric_template_yields_variety_across_seeds() -> None:
    """Same parametric template + different seeds = different surface text.

    Pin the system end-to-end: when the picker lands on a parametric
    template (one that uses ``{action_verb}`` / ``{adjective}`` /
    ``{prop}`` / etc.), filling the same template at different seeds
    must produce visibly different titles. This is the property that
    makes "few templates × parametric slots = nearly infinite outputs"
    real, and a regression here would silently collapse offline
    variety back to the pre-step-21 lock-in feel.
    """
    from toybox.activities.content_resolver import ResolvedRoom, ResolvedToy

    toys = (
        ResolvedToy(
            id="t1", display_name="Bluey", tags=("dog",), last_used_at=None,
        ),
    )
    rooms = (
        ResolvedRoom(id="r1", display_name="Living Room", features=()),
    )

    # Force the parametric template by filtering activities to it. The
    # picker is weighted-random across eligible templates, so we
    # sample widely and keep the parametric ones.
    target_template = "play_anytime_silly_walk"
    titles: set[str] = set()
    for seed in range(200):
        a = generate(
            "request_play", "freeplay", None, 22, seed,
            available_toys=toys, available_rooms=rooms,
        )
        if a.template_id == target_template:
            titles.add(a.title)
        if len(titles) >= 5:
            break
    # At least 3 distinct titles for the same template across seeds —
    # proves slot fills actually vary.
    assert len(titles) >= 3, sorted(titles)


def test_parametric_template_signature_is_stable_across_word_fills() -> None:
    """``{action_verb}`` / ``{adjective}`` / etc. must NOT contribute to
    the activity signature.

    Surface variety should aggregate under one signature so feedback
    accumulates per (template, slot, toy) regardless of which silly
    word the picker happened to use this time. Pin the contract end-
    to-end.
    """
    from toybox.activities.content_resolver import ResolvedRoom, ResolvedToy

    toys = (
        ResolvedToy(
            id="t1", display_name="Bluey", tags=("dog",), last_used_at=None,
        ),
    )
    rooms = (
        ResolvedRoom(id="r1", display_name="Living Room", features=()),
    )
    target_template = "play_anytime_silly_walk"
    sigs: set[str] = set()
    titles_seen: set[str] = set()
    for seed in range(200):
        a = generate(
            "request_play", "freeplay", None, 22, seed,
            available_toys=toys, available_rooms=rooms,
        )
        if a.template_id == target_template:
            sigs.add(a.metadata["signature"])
            titles_seen.add(a.title)
    # Multiple distinct titles (real variety), but exactly ONE
    # signature (word fills don't fragment feedback).
    assert len(titles_seen) >= 3, sorted(titles_seen)
    assert len(sigs) == 1, sigs


# ---------------------------------------------------------------------------
# 10 sample inputs that must produce coherent activities
# ---------------------------------------------------------------------------


SAMPLE_INPUTS: list[tuple[str, str | None, int, str]] = [
    # (intent, slot, hour, label)
    ("request_play", "unicorns", 10, "play_morning_unicorns"),
    ("request_play", "dinosaurs", 14, "play_afternoon_dinos"),
    ("request_story", None, 20, "story_evening_winddown"),
    ("request_story", "dragons", 8, "story_morning_dragons"),
    ("request_activity", None, 15, "activity_afternoon"),
    ("request_activity", "cooking", 11, "activity_morning_cooking"),
    ("boredom", None, 10, "boredom_morning"),
    ("boredom", None, 20, "boredom_evening_winddown"),
    ("request_play", "robots", 21, "play_winddown_eligible"),
    ("request_activity", "art", 9, "activity_morning_art"),
]


@pytest.mark.parametrize(("intent", "slot", "hour", "label"), SAMPLE_INPUTS)
def test_sample_inputs_produce_coherent_activities(
    intent: str,
    slot: str | None,
    hour: int,
    label: str,
) -> None:
    a = generate(intent, slot, None, hour, 17)

    assert len(a.steps) == 5
    # No leftover placeholders anywhere in title or step text.
    haystack = a.title + " " + " ".join(s.text for s in a.steps)
    assert "{" not in haystack, f"{label}: leftover brace in {haystack!r}"
    assert "}" not in haystack, f"{label}: leftover brace in {haystack!r}"

    # Metadata shape.
    assert a.metadata["hour_bucket"] in {"morning", "afternoon", "evening", "wind_down"}
    assert a.metadata["slot_values"] == tuple(sorted(a.metadata["slot_values"]))

    # If slot was provided AND any template text used {slot}, the slot
    # value must appear in slot_values. If slot is None, slot_values is
    # always empty.
    if slot is None:
        assert a.metadata["slot_values"] == ()
    else:
        # slot_values is a subset of {slot} (or empty if the chosen
        # template didn't reference {slot}).
        for v in a.metadata["slot_values"]:
            assert v == slot

    # Determinism check baked into the sample loop.
    again = generate(intent, slot, None, hour, 17)
    assert a == again, f"{label}: not deterministic"


def test_sample_inputs_uuid_format_is_valid() -> None:
    for intent, slot, hour, _label in SAMPLE_INPUTS:
        a = generate(intent, slot, None, hour, 17)
        # Valid UUID4 string format.
        parsed = uuid.UUID(a.id)
        assert parsed.version == 4
        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            a.id,
        )


# ---------------------------------------------------------------------------
# Fallback rules
# ---------------------------------------------------------------------------


def test_unknown_intent_falls_back_to_boredom_always() -> None:
    """An unsupported intent string still produces an activity by
    falling through the fallback chain to the boredom always pool."""
    a = generate("totally_unknown_intent", None, None, 10, 1)
    assert len(a.steps) == 5
    boredom_always_ids = {
        t.id for t in _load_intent_templates("boredom") if ALWAYS_BUCKET in t.buckets
    }
    assert a.template_id in boredom_always_ids


def test_metadata_slot_values_dedupes_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a template uses {slot} multiple times the slot_values list
    must NOT contain duplicate entries — Phase D's signature relies
    on this stability. We inject a fixture template with FIVE {slot}
    occurrences (one in title, four in steps) so the dedupe path is
    actually exercised."""
    fake_dir = tmp_path / "templates"
    fake_dir.mkdir()
    (fake_dir / "_schema.json").write_text(
        (TEMPLATES_DIR / "_schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    fixture = {
        "intent": "boredom",
        "templates": [
            {
                "id": "boredom_dedupe_fixture",
                "title": "Tale of {slot}",
                "buckets": ["always"],
                "steps": [
                    {"text": "Step one with {slot}."},
                    {"text": "Step two with {slot} again."},
                    {"text": "Step three with {slot} once more."},
                    {"text": "Step four with {slot}, repeating."},
                    {"text": "Step five — final mention of {slot}."},
                ],
            }
        ],
    }
    (fake_dir / "boredom.json").write_text(json.dumps(fixture), encoding="utf-8")

    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake_dir)
    clear_template_cache()

    a = generate("boredom", "unicorns", None, 10, 1)
    sv = a.metadata["slot_values"]
    # The slot was substituted SIX times (1 title + 5 steps); the
    # result must still be a single deduped entry.
    assert sv == ("unicorns",)


# ---------------------------------------------------------------------------
# Generator: hour_bucket metadata matches helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hour", [0, 6, 12, 17, 19, 22])
def test_metadata_hour_bucket_matches_helper(hour: int) -> None:
    a = generate("boredom", None, None, hour, 1)
    assert a.metadata["hour_bucket"] == hour_bucket(hour).value


# ---------------------------------------------------------------------------
# Generator: out-of-range hour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hour", [-1, 24, 99])
def test_generate_rejects_out_of_range_hour(hour: int) -> None:
    with pytest.raises(ValueError):
        generate("request_play", "unicorns", None, hour, 1)


# ---------------------------------------------------------------------------
# Schema integrity
# ---------------------------------------------------------------------------


def test_invalid_template_file_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin: a schema-invalid template file at runtime is skipped at
    load (logged WARNING), not fatal."""
    fake_dir = tmp_path / "templates"
    fake_dir.mkdir()
    # Copy schema.
    (fake_dir / "_schema.json").write_text(
        (TEMPLATES_DIR / "_schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # Write a malformed boredom file (missing required "templates" key).
    (fake_dir / "boredom.json").write_text(
        json.dumps({"intent": "boredom"}),
        encoding="utf-8",
    )

    # Patch the constants used by the generator.
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", fake_dir)
    clear_template_cache()

    # The boredom file is invalid — _load_intent_templates returns
    # empty. With no boredom always pool either, generate() should
    # raise.
    with pytest.raises(RuntimeError):
        generate("boredom", None, None, 10, 1)


# ---------------------------------------------------------------------------
# 5-step invariant: file-level + model-level
# ---------------------------------------------------------------------------


def test_every_shipped_template_file_has_5_step_arrays() -> None:
    """Pin the 5-step contract at the FILE level by reading each shipped
    template JSON directly (not via ``generate``). If the schema regex
    drifts (or someone edits a file by hand and bypasses CI), this is the
    last line of defence."""
    for intent in SUPPORTED_INTENTS:
        path = TEMPLATES_DIR / f"{intent}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for tpl in payload["templates"]:
            assert len(tpl["steps"]) == 5, (
                f"{intent}.json template {tpl.get('id')!r} has "
                f"{len(tpl['steps'])} steps; must be exactly 5"
            )


@pytest.mark.parametrize("step_count", [4, 6])
def test_activity_model_rejects_non_5_step_lists(step_count: int) -> None:
    """Pin the ``min_length=5, max_length=5`` constraint on the
    ``Activity.steps`` field directly. If a future refactor loosens this
    on the model, this test fails before any callers can ship a
    non-conforming activity."""
    from pydantic import ValidationError

    steps = [
        ActivityStep(step_index=i, text=f"step {i}", sfx=None, expected_action=None)
        for i in range(step_count)
    ]
    with pytest.raises(ValidationError):
        Activity(
            id="00000000-0000-4000-8000-000000000000",
            template_id="x",
            persona_id=None,
            title="t",
            steps=steps,
            version=1,
            metadata={},
        )


# ---------------------------------------------------------------------------
# Seed influences UUID (not just in-Activity content)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "slot"),
    [
        ("request_play", "unicorns"),
        ("request_story", "dragons"),
        ("request_activity", "art"),
    ],
)
def test_different_seed_produces_different_activity_id(intent: str, slot: str) -> None:
    """The seed is part of the determinism key for ``Activity.id``, not
    only the in-Activity content. Pin: same intent / slot / hour with
    different seeds yields distinct ids across at least three intents.

    This pins the load-bearing claim that the seed is mixed into the
    UUID derivation — a regression where seed only affected template
    *selection* (and two seeds happened to pick the same template)
    would give matching ids and quietly break the determinism key."""
    a = generate(intent, slot, None, 10, seed=1)
    b = generate(intent, slot, None, 10, seed=2)
    assert a.id != b.id, f"intent={intent} slot={slot}: seeds 1 and 2 produced the same id"
