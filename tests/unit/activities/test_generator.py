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
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.activities import generate
from toybox.activities.feedback import compute_signature
from toybox.activities.generator import (
    TEMPLATES_DIR,
    clear_template_cache,
)


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
