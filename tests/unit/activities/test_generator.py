"""Phase G G2 — generator surface for lazy insertion + slot-fill persistence.

These are pure-Python unit tests against
:func:`toybox.activities.generator.generate`: no DB, no FastAPI
client. The persistence-side coverage (lazy ``activity_steps``
INSERT, ``slot_fills_json`` on the row, regression for pre-G2
activities) lives at ``tests/integration/test_g2_lazy_insertion.py``.

Pinned here:

* Anti-signal ``signature`` is stable across the G2 generator
  changes (still hashes ``{template_id}:{slot_fingerprint}`` —
  ``slot_fills`` is added but does not feed the signature).
* ``Activity.metadata["slot_fills"]`` is populated with the
  resolved slot-name → value map (the load-bearing input the
  persistence layer reads to write ``activities.slot_fills_json``).
* ``ActivityStep.step_id`` and ``ActivityStep.choices_rendered``
  carry the template's optional ``Step.id`` and rendered choice
  labels through to the runtime row, so the persistence layer
  can populate ``activity_steps.step_template_id`` and
  ``activity_steps.choices_json`` without needing the template back.
* For a branching template fixture where ``steps[0]`` has
  ``choices``, ``choices_rendered`` is the rendered list (no
  ``{slot}`` placeholders remaining).
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from toybox.activities import generate
from toybox.activities.feedback import compute_signature
from toybox.activities.generator import (
    TEMPLATES_DIR,
    _parse_template,
    _resolve_template_slots,
    clear_template_cache,
)
from toybox.activities.slots import ADJECTIVES, SlotRegistry


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    """Clear the per-process template cache between tests so a
    fixture-template monkeypatch (where present) doesn't leak."""
    clear_template_cache()
    yield
    clear_template_cache()


def test_generate_populates_slot_fills_metadata() -> None:
    """The generator threads the resolved slot map onto
    ``Activity.metadata['slot_fills']`` so the persistence layer can
    write ``activities.slot_fills_json``. Distinct from
    ``slot_values`` (the deduped sorted tuple used by anti-signal):
    ``slot_fills`` is keyed by slot name and includes word-list
    fills like ``{adjective}`` that the renderer needs but
    anti-signal ignores.
    """
    activity = generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=7,
    )
    fills = activity.metadata["slot_fills"]
    assert isinstance(fills, dict)
    # The shipped templates use at least one resolvable slot — we
    # don't pin which keys appear (varies by which template the
    # seeded picker selected) but at least one fill must be present
    # OR the template uses no slots at all (in which case the dict
    # is empty and the persistence layer writes ``{}``). Either is
    # correct; we pin only the type contract here.
    for key, value in fills.items():
        assert isinstance(key, str)
        assert isinstance(value, str)


def test_slot_fills_is_serializable_with_sort_keys() -> None:
    """The persistence layer encodes ``slot_fills`` with
    ``json.dumps(..., sort_keys=True)``. Smoke-check that the
    generator's output is JSON-serializable (no non-string values,
    no nested mutable types) so the persistence call doesn't trip
    a TypeError at runtime.
    """
    activity = generate(
        intent="request_play",
        slot="unicorns",
        context=None,
        hour=12,
        seed=42,
    )
    blob = json.dumps(activity.metadata["slot_fills"], sort_keys=True)
    decoded = json.loads(blob)
    assert decoded == activity.metadata["slot_fills"]


def test_signature_stable_across_g2_for_same_seed() -> None:
    """Anti-signal signature stays template-level (hashes
    ``{template_id}:{slot_fingerprint}``); G2's ``slot_fills``
    addition does NOT feed the signature. Two calls with identical
    inputs MUST produce identical signatures, AND that signature
    MUST equal the canonical
    :func:`compute_signature(template_id, slot_values)` value —
    proving the formula is unchanged from pre-G2.
    """
    a1 = generate(
        intent="request_play",
        slot="unicorns",
        context=None,
        hour=12,
        seed=42,
    )
    a2 = generate(
        intent="request_play",
        slot="unicorns",
        context=None,
        hour=12,
        seed=42,
    )
    assert a1.metadata["signature"] == a2.metadata["signature"]
    # Cross-check: re-compute from the public formula. If anything
    # in the signature pipeline drifts (e.g. someone folds
    # ``slot_fills`` into the hash by mistake), this assertion is
    # the canary.
    expected = compute_signature(a1.template_id, a1.metadata["slot_values"])
    assert a1.metadata["signature"] == expected


def test_branching_template_renders_choices_on_steps_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Drop a branching fixture into the templates dir, generate, and
    assert ``ActivityStep.choices_rendered`` carries the rendered
    button labels — with ``{toy}`` etc. substituted. This is the
    fixture the G2 persistence layer turns into ``choices_json``.

    Iter-2: uses ``slot_substituted_choices.json`` (NOT
    ``valid_branching.json``) because the latter has no ``{slot}``
    placeholders inside its choice labels — so the load-bearing
    ``_substitute(label, slot_values)`` path was never actually
    exercised by the assertion ``"{" not in label`` (trivially true).
    The new fixture's labels are ``"Sneak past {toy}"`` and
    ``"Charge into {room}"`` — we now pin BOTH that no placeholder
    remains AND that the resolved slot values appear verbatim.
    """
    fixture_root = Path(__file__).resolve().parents[2] / "fixtures" / "activities" / "branching"
    valid_path = fixture_root / "slot_substituted_choices.json"
    payload = json.loads(valid_path.read_text(encoding="utf-8"))

    # Point the generator at a fresh ``tmp_path``-rooted templates
    # dir whose only content is this single intent file (so the
    # seeded picker MUST land on it). pytest tears tmp_path down
    # at end of test → no leftover state.
    isolated = tmp_path / "templates"
    isolated.mkdir()
    intent_file = isolated / "boredom.json"
    intent_file.write_text(json.dumps(payload), encoding="utf-8")
    schema_src = TEMPLATES_DIR / "_schema.json"
    schema_dst = isolated / "_schema.json"
    schema_dst.write_text(schema_src.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", isolated)
    clear_template_cache()

    activity = generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=11,
    )
    # First step has ``choices`` per fixture; runtime row carries
    # the rendered labels. The labels reference ``{toy}`` and
    # ``{room}`` which are resolved against the (empty-catalog)
    # default toy name + a word-list room fill — so they're rendered,
    # not raw.
    first = activity.steps[0]
    assert first.choices_rendered is not None
    assert len(first.choices_rendered) == 2
    fills = activity.metadata["slot_fills"]
    assert isinstance(fills, dict)
    toy_value = fills["toy"]
    room_value = fills["room"]
    # Iter-2 load-bearing assertion: the rendered labels MUST contain
    # the resolved slot values (not the literal ``{toy}`` /
    # ``{room}`` placeholders). A bug in ``_substitute`` that left
    # placeholders un-replaced would fail here, where iter-1's
    # ``"{" not in label`` check trivially passed on a placeholder-
    # free fixture.
    expected_labels = {
        f"Sneak past {toy_value}",
        f"Charge into {room_value}",
    }
    assert set(first.choices_rendered) == expected_labels, (
        f"choice labels did not render slot fills: got {first.choices_rendered!r}, "
        f"expected {expected_labels!r}"
    )
    for label in first.choices_rendered:
        assert "{" not in label, f"unrendered slot in choice label: {label!r}"
    # Step ids round-trip from the template (the fixture's first
    # step has id="open"; the generator must preserve it on the
    # runtime row so the persistence layer can write
    # ``step_template_id``).
    assert first.step_id == "open"
    # Steps without ``choices`` keep ``choices_rendered=None``.
    for step in activity.steps[1:]:
        assert step.choices_rendered is None


def test_linear_template_step_id_and_choices_default_to_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A linear template (no ``id`` / ``next`` / ``choices`` per step)
    must produce runtime ``ActivityStep`` rows with ``step_id=None``
    and ``choices_rendered=None`` — otherwise the persistence layer
    would write garbage into the new G2 columns.

    Pins to a fresh templates dir containing ONLY one of the 4 shipped
    production linear templates (``boredom.json``) so the seeded picker
    can't land on a soak branching template.
    """
    src_root = Path(__file__).resolve().parents[3] / "src" / "toybox" / "activities" / "templates"
    isolated = tmp_path / "templates"
    isolated.mkdir()
    (isolated / "_schema.json").write_text(
        (src_root / "_schema.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (isolated / "boredom.json").write_text(
        (src_root / "boredom.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", isolated)
    clear_template_cache()

    activity = generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=7,
    )
    for step in activity.steps:
        assert step.step_id is None, (
            f"linear template {activity.template_id} produced step_id={step.step_id!r}; "
            "shipped templates have no id field"
        )
        assert step.choices_rendered is None, (
            f"linear template {activity.template_id} produced choices_rendered="
            f"{step.choices_rendered!r}; shipped templates have no choices field"
        )


# ---------------------------------------------------------------------------
# Bug #84 regression: _resolve_template_slots MUST scan choice labels.
#
# Pre-fix, the haystack was built from `template.title + step.text` only,
# so a placeholder that lived ONLY in a `choices[i].label` (and nowhere
# in any title/step body) was never resolved. The persisted
# slot_fills_json then omitted the key, and at advance time the kiosk
# rendered the literal `{placeholder}` in the choice button label.
#
# Concrete shipped repro (do NOT couple tests to it — synthetic templates
# below are minimal and isolated):
#   src/toybox/activities/templates/branching/request_play.json — the
#   "fight_fork" step has choices `[{label: "Strike with the {prop}", ...},
#   {label: "Tell it a {adjective} joke", ...}]`. `{prop}` is resolved
#   because it also appears in another step body in the same template;
#   `{adjective}` is NOT resolved because it appears ONLY in the joke
#   choice label.
# ---------------------------------------------------------------------------


def _label_only_template_payload() -> dict[str, object]:
    """Build a synthetic branching template whose `{adjective}` placeholder
    appears EXCLUSIVELY inside a choice label.

    Title + every step body are deliberately placeholder-free (apart from
    `{toy}` in the opening line) so the only path by which the resolver
    can discover `{adjective}` is by walking `choices[i].label`. Keeping
    the fixture inline (vs. a permanent JSON file) makes the test
    independent of any future production-template edits.
    """
    return {
        "id": "fixture_bug84_label_only_adjective",
        "title": "A quick test quest",
        "buckets": ["always"],
        "steps": [
            {
                "id": "open",
                "text": "{toy} meets a creature blocking the path.",
                "choices": [
                    {"label": "Push past it", "next": "push_end"},
                    {"label": "Tell it a {adjective} joke", "next": "joke_end"},
                ],
            },
            {
                "id": "push_end",
                "text": "You shove past and the path opens up.",
            },
            {
                "id": "joke_end",
                "text": "The creature giggles and lets you by.",
            },
        ],
    }


def test_resolve_template_slots_includes_label_only_placeholder() -> None:
    """Bug #84 (direct): `_resolve_template_slots` must include
    `{adjective}` in its returned dict when the placeholder appears ONLY
    in a choice label and nowhere in the title or any step body.

    Pre-fix this FAILS because the haystack omits choice labels entirely:
    `_resolve_template_slots` returns a dict with `toy` but no `adjective`
    key. Post-fix, the dict must contain `adjective` mapped to one of the
    documented :data:`ADJECTIVES` word-list values.
    """
    template = _parse_template(_label_only_template_payload(), source="<test>")

    # Sanity: title and every step body have no `{adjective}` — only the
    # joke choice label does. If this precondition ever drifts the test
    # is no longer testing the bug.
    assert "{adjective}" not in template.title
    assert all("{adjective}" not in step.text for step in template.steps)
    label_only_labels = [
        label
        for step in template.steps
        if step.choices is not None
        for label, _next in step.choices
    ]
    assert any("{adjective}" in label for label in label_only_labels), (
        "fixture must put `{adjective}` exclusively in a choice label"
    )

    registry = SlotRegistry.from_resolved(())
    rng = random.Random(1234)
    resolved = _resolve_template_slots(
        template,
        slot=None,
        toy="Mr. Unicorn",
        registry=registry,
        rng=rng,
    )

    # Load-bearing assertion: the resolver MUST have discovered the
    # placeholder by walking choice labels. Pre-fix this key is missing.
    assert "adjective" in resolved, (
        f"_resolve_template_slots missed a label-only placeholder; "
        f"got keys={sorted(resolved.keys())!r} — choice labels were "
        f"{label_only_labels!r}"
    )
    assert resolved["adjective"] in ADJECTIVES


def test_generate_renders_label_only_placeholder_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bug #84 (end-to-end through `generate()`): when a branching
    template carries a placeholder that appears ONLY in a choice label,
    the rendered choice label must NOT leak the literal `{placeholder}`
    string. The whole point of the bug is that the kid kiosk shows
    "Tell it a {adjective} joke" verbatim instead of an actual adjective.

    Pre-fix, `slot_values` (the resolved fill map) omits `adjective`,
    so `_substitute(label, slot_values)` leaves `{adjective}` alone and
    the assertion below fails on the literal string remaining in the
    rendered label.
    """
    # Stage a templates dir whose only content is the synthetic
    # label-only fixture, plus the production schema. Seeded picker
    # MUST land on this template since it's the sole eligible one.
    isolated = tmp_path / "templates"
    isolated.mkdir()
    intent_payload = {
        "intent": "boredom",
        "templates": [_label_only_template_payload()],
    }
    (isolated / "boredom.json").write_text(json.dumps(intent_payload), encoding="utf-8")
    schema_src = TEMPLATES_DIR / "_schema.json"
    (isolated / "_schema.json").write_text(
        schema_src.read_text(encoding="utf-8"), encoding="utf-8"
    )

    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", isolated)
    clear_template_cache()

    activity = generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=11,
    )

    # Title + every step body must be placeholder-free.
    assert "{" not in activity.title, (
        f"title leaks an unrendered placeholder: {activity.title!r}"
    )
    for step in activity.steps:
        assert "{" not in step.text, (
            f"step body leaks an unrendered placeholder: {step.text!r}"
        )
        if step.choices_rendered is not None:
            for label in step.choices_rendered:
                assert "{" not in label, (
                    f"choice label leaks an unrendered placeholder: {label!r}"
                )

    # And the load-bearing positive assertion: the joke-choice label
    # must now end with "<an-adjective> joke" — i.e. an actual word
    # from the documented ADJECTIVES list, NOT the literal
    # `{adjective}`. Find the choice step (it's `steps[0]` per the
    # fixture) and check the second button's rendered text.
    first = activity.steps[0]
    assert first.choices_rendered is not None
    assert len(first.choices_rendered) == 2
    joke_label = first.choices_rendered[1]
    assert joke_label.startswith("Tell it a ")
    assert joke_label.endswith(" joke")
    # The middle word must be a documented adjective. We can also
    # cross-check against the slot_fills map for verbatim equality.
    fills = activity.metadata["slot_fills"]
    assert isinstance(fills, dict)
    assert "adjective" in fills, (
        f"slot_fills missing 'adjective' — got keys={sorted(fills.keys())!r}"
    )
    assert fills["adjective"] in ADJECTIVES
    assert joke_label == f"Tell it a {fills['adjective']} joke"


# ---------------------------------------------------------------------------
# Bug #84 audit: for EVERY shipped branching template, drive the real
# `_resolve_template_slots` and assert that every placeholder appearing
# in any choice label is actually resolved by the production function.
# Pre-fix the resolver's haystack omits choice labels, so label-only
# placeholders (e.g. `{adjective}` in request_play "fight_fork") are
# missing from the returned dict and the audit fails; post-fix every
# label placeholder has a corresponding key in the resolved map.
# ---------------------------------------------------------------------------


_SLOT_RE: Final = re.compile(r"\{([a-z_][a-z_]*)\}")


def _placeholders_in(text: str) -> set[str]:
    """Return the set of `{name}` placeholder names found in `text`."""
    return set(_SLOT_RE.findall(text))


def _choice_label_placeholders(payload: dict[str, object]) -> set[str]:
    """Collect every placeholder name that appears in any choice label
    across the template's steps."""
    names: set[str] = set()
    steps = payload["steps"]
    assert isinstance(steps, list)
    for step in steps:
        assert isinstance(step, dict)
        choices = step.get("choices")
        if not choices:
            continue
        assert isinstance(choices, list)
        for choice in choices:
            assert isinstance(choice, dict)
            label = str(choice["label"])
            names.update(_placeholders_in(label))
    return names


_BRANCHING_DIR: Final[Path] = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "toybox"
    / "activities"
    / "templates"
    / "branching"
)


def _load_all_branching_templates() -> list[tuple[str, str, dict[str, object]]]:
    """Walk every shipped branching template file and return
    `(file_name, template_id, raw_payload)` triples — flattened across
    files so each template gets its own audit row.
    """
    out: list[tuple[str, str, dict[str, object]]] = []
    for json_path in sorted(_BRANCHING_DIR.glob("*.json")):
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        for tpl in payload["templates"]:
            out.append((json_path.name, str(tpl["id"]), tpl))
    return out


def test_no_branching_template_has_label_only_placeholder() -> None:
    """Bug #84 (audit): every placeholder that appears inside any
    `choices[i].label` of a shipped branching template MUST be
    resolvable by the production `_resolve_template_slots` function.

    This audit drives the REAL resolver against every shipped
    branching template and asserts that every placeholder name found
    in any choice label appears as a key in the returned dict. Pre-fix
    `_resolve_template_slots` walks only title + step bodies, so
    label-only placeholders (e.g. `{adjective}` inside a `joke_end`
    choice label of `request_play_soak_long_quest_for_lost_crown`)
    are missing from the returned dict and the audit fails with the
    offending template(s) named. Post-fix the resolver also walks
    choice labels, so every label placeholder is resolved and the
    audit passes silently.

    `{toy}` and `{slot}` are caller-supplied and resolved
    unconditionally; the resolver only inserts them into the returned
    dict when it sees them in the haystack walk, so we exclude them
    from the audit set (a label that mentions only `{toy}` would
    false-positive otherwise).
    """
    offenders: list[dict[str, object]] = []
    for file_name, template_id, payload in _load_all_branching_templates():
        labels = _choice_label_placeholders(payload)
        # `{toy}` / `{slot}` are caller-supplied and always available
        # to the renderer — they're not the class of placeholder this
        # audit is policing (label-only word-list slots like
        # `{adjective}` that the resolver might miss).
        labels_to_check = labels - {"toy", "slot"}
        if not labels_to_check:
            continue

        # Drive the REAL production resolver — if a future regression
        # drops choice labels from its haystack, the returned dict
        # will be missing the label-only placeholder keys and this
        # audit catches it.
        template = _parse_template(payload, source=file_name)
        registry = SlotRegistry.from_resolved(())
        rng = random.Random(0xB84A)
        resolved = _resolve_template_slots(
            template,
            slot=None,
            toy="TestToy",
            registry=registry,
            rng=rng,
        )

        missing = sorted(labels_to_check - resolved.keys())
        if missing:
            offenders.append(
                {
                    "file": file_name,
                    "template_id": template_id,
                    "unresolved_label_placeholders": missing,
                }
            )

    assert offenders == [], (
        f"{len(offenders)} branching template(s) have choice-label "
        f"placeholders that `_resolve_template_slots` failed to "
        f"resolve — at advance time these leak the literal "
        f"`{{placeholder}}` to the kiosk. Offenders: {offenders!r}"
    )


def test_advance_time_render_leaks_label_only_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bug #84 (advance-time render contract): the persisted
    `slot_fills` from `generate()` is what the lazy advance handler
    feeds to `render_with_slot_fills(label, fills)` to render later
    step bodies + choice button labels. If `slot_fills` is missing a
    key that the template's choice label references, the rendered
    label leaks the literal `{placeholder}` to the kiosk.

    This is the unit-level proof of the user-facing symptom. The
    full HTTP propose → advance integration loop adds substantial
    DB plumbing for no extra signal — the contract this test pins
    is the same one the advance handler depends on at
    ``src/toybox/api/activities.py`` around line 630
    (``render_with_slot_fills(label, fills)`` for each choice in
    ``step.choices``).
    """
    from toybox.activities.generator import render_with_slot_fills

    # Stage the synthetic label-only fixture as the only template.
    isolated = tmp_path / "templates"
    isolated.mkdir()
    intent_payload = {
        "intent": "boredom",
        "templates": [_label_only_template_payload()],
    }
    (isolated / "boredom.json").write_text(json.dumps(intent_payload), encoding="utf-8")
    (isolated / "_schema.json").write_text(
        (TEMPLATES_DIR / "_schema.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", isolated)
    clear_template_cache()

    activity = generate(
        intent="boredom",
        slot=None,
        context=None,
        hour=12,
        seed=11,
    )

    # The persisted-shape `slot_fills` is what the advance handler
    # reads back out of `activities.slot_fills_json`. Take it and feed
    # the original template's joke choice label through the SAME
    # render function the advance handler uses.
    persisted_fills = activity.metadata["slot_fills"]
    assert isinstance(persisted_fills, dict)

    raw_joke_label = "Tell it a {adjective} joke"
    rendered = render_with_slot_fills(raw_joke_label, persisted_fills)

    # Post-fix: `persisted_fills` carries the `adjective` key (because
    # the resolver scanned the choice label) and the rendered string
    # is `"Tell it a sparkly joke"` (or whatever the rng picked).
    # Pre-fix: `persisted_fills` is missing the `adjective` key and
    # `render_with_slot_fills` leaves the literal placeholder in place.
    assert "{adjective}" not in rendered, (
        f"advance-time render leaked a label-only placeholder: "
        f"{rendered!r} (persisted fills={persisted_fills!r})"
    )
    assert "adjective" in persisted_fills, (
        f"persisted slot_fills missing the `adjective` key required to "
        f"render a choice label that references it; got "
        f"keys={sorted(persisted_fills.keys())!r}"
    )
