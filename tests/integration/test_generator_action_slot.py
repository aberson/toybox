"""Phase F Step F6 — generator + template-loader coverage for ``action_slot``.

The Claude single-shot path is exercised by stubbing
:func:`toybox.ai.adapters.claude.parse_activity_from_text` end-to-end —
when the model emits a good slot the parsed Activity carries it; when
the model emits a bad slot the Pydantic validator rejects, mirroring
the existing malformed-output → offline-fallback path
(``toybox.core.escalation.EscalationDispatcher`` already catches
``ValidationError``).

The offline-template path is tested by:

* loading the shipped templates and asserting every step has a valid
  ``action_slot``;
* injecting a synthetic template with ``action_slot="banana"`` and
  asserting the loader rejects at boot.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from toybox.activities.generator import (
    SUPPORTED_INTENTS,
    TEMPLATES_DIR,
    _load_intent_templates,
    _parse_template,
    clear_template_cache,
    generate,
)
from toybox.ai.adapters.claude import parse_activity_from_text
from toybox.image_gen.models import ACTION_SLOTS


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    clear_template_cache()


# ---------------------------------------------------------------------------
# Claude single-shot parser — good slot, bad slot, missing field
# ---------------------------------------------------------------------------


def _claude_payload(*, action_slot_override: object = "__keep__") -> str:
    """Build a Claude single-shot reply with five well-formed steps.

    ``action_slot_override``:
      * ``"__keep__"`` (default): use ``"pointing"`` for every step.
      * ``"__omit__"``: drop the ``action_slot`` key entirely (missing).
      * any other value: set every step's ``action_slot`` to that value.
    """
    steps: list[dict[str, object]] = []
    for i in range(5):
        step: dict[str, object] = {
            "step_index": i,
            "text": f"step text {i}",
            "sfx": None,
            "expected_action": None,
        }
        if action_slot_override == "__keep__":
            step["action_slot"] = "pointing"
        elif action_slot_override == "__omit__":
            pass
        else:
            step["action_slot"] = action_slot_override
        steps.append(step)
    return json.dumps(
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "template_id": "claude_dynamic",
            "persona_id": None,
            "title": "claude title",
            "steps": steps,
            "version": 1,
            "metadata": {},
        }
    )


def test_claude_single_shot_good_slot_round_trips() -> None:
    """A well-formed Claude reply with valid action_slot parses into an Activity
    whose every step has the slot set."""
    activity = parse_activity_from_text(_claude_payload())
    assert all(step.action_slot == "pointing" for step in activity.steps)


def test_claude_single_shot_bad_slot_raises_validation_error() -> None:
    """An out-of-vocab action_slot triggers the Pydantic validator. The
    production fallback path (``EscalationDispatcher``) catches
    :class:`pydantic.ValidationError` and falls through to the offline
    generator — same path as any other malformed-output rejection."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        parse_activity_from_text(_claude_payload(action_slot_override="banana"))


def test_claude_single_shot_missing_slot_defaults_to_none() -> None:
    """A Claude reply that omits ``action_slot`` parses cleanly and every
    step's ``action_slot`` is ``None``. The kiosk renders no sprite —
    the default for legacy / pre-F6 rows. This is the documented
    "missing-field also falls back to None" behavior from plan §F6."""
    activity = parse_activity_from_text(_claude_payload(action_slot_override="__omit__"))
    assert all(step.action_slot is None for step in activity.steps)


# ---------------------------------------------------------------------------
# Offline-template loader — boot-time validation
# ---------------------------------------------------------------------------


def test_offline_templates_all_have_valid_action_slot() -> None:
    """Every shipped template's every step must declare a valid action_slot.

    The plan calls out hand-authoring slots per template step in F6;
    this test pins the result so a future template author can't ship a
    step with NULL or out-of-vocab action_slot and silently break the
    kiosk render path."""
    for intent in SUPPORTED_INTENTS:
        path = TEMPLATES_DIR / f"{intent}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for tpl in payload["templates"]:
            for step in tpl["steps"]:
                slot = step.get("action_slot")
                assert slot is not None, (
                    f"{intent}.json template {tpl['id']!r} has a step with "
                    f"no action_slot: {step!r}"
                )
                assert slot in ACTION_SLOTS, (
                    f"{intent}.json template {tpl['id']!r}: action_slot={slot!r} "
                    f"is not in ACTION_SLOTS={ACTION_SLOTS!r}"
                )


def test_template_loader_rejects_bad_slot_at_boot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Loading a template file whose step carries action_slot='banana'
    is treated as a malformed-template error. The in-memory parse path
    (``_parse_template``) raises :class:`ValueError` directly, and the
    file-driven loader path (``_load_intent_templates``) catches that
    same ValueError, logs a WARNING that names the offending template
    and the bad slot, and skips the template — Phase G plan contract:
    bad-shape errors warn + skip rather than crash startup, so a
    single bad hand-edited template can't take down the whole kiosk
    boot.
    """
    bad_template = {
        "id": "bad_one",
        "title": "Bad template",
        "buckets": ["always"],
        "steps": [
            {"text": f"step {i}", "action_slot": "banana"} for i in range(5)
        ],
    }
    # In-memory parse path still raises directly — tools/lint_templates
    # and similar one-shot tools rely on this loud failure.
    with pytest.raises(ValueError, match="banana"):
        _parse_template(bad_template, source="<test>")

    # File-driven path: write a complete intent file with the bad
    # template, point TEMPLATES_DIR at the temp dir, and assert the
    # loader catches+skips with a WARNING (no crash).
    bad_dir = tmp_path / "bad_templates"
    bad_dir.mkdir()
    schema_src = TEMPLATES_DIR / "_schema.json"
    (bad_dir / "_schema.json").write_text(schema_src.read_text(encoding="utf-8"), encoding="utf-8")
    payload = {"intent": "boredom", "templates": [bad_template]}
    (bad_dir / "boredom.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", bad_dir)
    clear_template_cache()
    with caplog.at_level(logging.WARNING, logger="toybox.activities.generator"):
        templates = _load_intent_templates("boredom")
    # Bad template was skipped, not loaded.
    assert all(t.id != "bad_one" for t in templates)
    # WARNING log named the offending template id and the bad slot.
    warning_messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "bad_one" in warning_messages
    assert "banana" in warning_messages


# ---------------------------------------------------------------------------
# Offline path: generated activity carries the per-step slot
# ---------------------------------------------------------------------------


def test_offline_generate_threads_action_slot_to_activity_step() -> None:
    """The offline generator's output ActivityStep instances must carry
    the slot from the matching template step. This is the load-bearing
    "offline-path step emission carries the static slot through to the
    DB" gate from plan §F6 done-when #5 — without this, the propose
    path would persist NULL even when the template author specified a
    slot."""
    activity = generate("boredom", None, None, hour=10, seed=1)
    for step in activity.steps:
        assert step.action_slot is not None
        assert step.action_slot in ACTION_SLOTS
