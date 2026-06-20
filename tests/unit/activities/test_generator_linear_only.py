"""Phase W Step W2 — ``generate(..., linear_only=...)`` template filtering.

Pure-Python unit tests against
:func:`toybox.activities.generator.generate`: no DB, no FastAPI client.
They stage an isolated templates dir holding BOTH a branching template
(a step with ``choices``) and a linear template (no ``choices`` anywhere)
in the same intent file, then prove:

* ``linear_only=True`` NEVER selects the branching template, across many
  seeds — only the choice-free template is eligible.
* ``linear_only=False`` (default) CAN select the branching template —
  i.e. the filter is genuinely off by default and the branching
  template is otherwise reachable.
* ``linear_only=False`` is byte-identical to omitting the kwarg.
* When the requested intent has ONLY branching templates, ``linear_only``
  falls back cleanly to the boredom ``always`` linear pool rather than
  starving the picker / returning a branching activity.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from toybox.activities import generate
from toybox.activities.generator import (
    TEMPLATES_DIR,
    clear_template_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    clear_template_cache()
    yield
    clear_template_cache()


def _linear_template(template_id: str, *, title: str = "A calm quest") -> dict[str, Any]:
    """A choice-free 3-step linear template (all ``always`` bucket)."""
    return {
        "id": template_id,
        "title": title,
        "buckets": ["always"],
        "steps": [
            {"text": "Find a cozy spot to sit."},
            {"text": "Take three slow breaths."},
            {"text": "Stretch up tall like a tree."},
        ],
    }


def _branching_template(template_id: str) -> dict[str, Any]:
    """A valid branching template — ``steps[0]`` exposes ``choices``."""
    return {
        "id": template_id,
        "title": "A choosy quest",
        "buckets": ["always"],
        "steps": [
            {
                "id": "open",
                "text": "A door appears. Which way?",
                "choices": [
                    {"label": "Go left", "next": "left_end"},
                    {"label": "Go right", "next": "right_end"},
                ],
            },
            {"id": "left_end", "text": "You step left and find a garden."},
            {"id": "right_end", "text": "You step right and find a pond."},
        ],
    }


def _stage_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    boredom_templates: list[dict[str, Any]],
) -> None:
    """Write a fresh templates dir with one boredom.json file + the schema."""
    isolated = tmp_path / "templates"
    isolated.mkdir()
    (isolated / "boredom.json").write_text(
        json.dumps({"intent": "boredom", "templates": boredom_templates}),
        encoding="utf-8",
    )
    (isolated / "_schema.json").write_text(
        (TEMPLATES_DIR / "_schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", isolated)
    clear_template_cache()


def test_linear_only_excludes_branching_template_across_seeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With both a linear and branching template eligible, ``linear_only=True``
    only ever picks the choice-free one — for every seed in a wide sweep."""
    _stage_templates(
        tmp_path,
        monkeypatch,
        boredom_templates=[
            _linear_template("calm_quest"),
            _branching_template("choosy_quest"),
        ],
    )

    for seed in range(50):
        activity = generate(
            intent="boredom",
            slot=None,
            context=None,
            hour=12,
            seed=seed,
            linear_only=True,
        )
        assert activity.template_id == "calm_quest", (
            f"linear_only picked branching template at seed={seed}: {activity.template_id}"
        )
        for step in activity.steps:
            assert step.choices_rendered is None, (
                f"linear_only produced a branching step at seed={seed}: {step.choices_rendered!r}"
            )


def test_default_can_select_branching_template(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With the filter OFF (default), the branching template is reachable —
    proving the exclusion in the prior test is the filter at work, not
    an unreachable fixture."""
    _stage_templates(
        tmp_path,
        monkeypatch,
        boredom_templates=[
            _linear_template("calm_quest"),
            _branching_template("choosy_quest"),
        ],
    )

    picked = {
        generate(
            intent="boredom",
            slot=None,
            context=None,
            hour=12,
            seed=seed,
        ).template_id
        for seed in range(50)
    }
    # Both templates appear at least once across the seed sweep when the
    # filter is off — the branching one is NOT excluded by default.
    assert "choosy_quest" in picked
    assert "calm_quest" in picked


def test_linear_only_false_is_byte_identical_to_omitting_kwarg(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Passing ``linear_only=False`` explicitly must match omitting it."""
    _stage_templates(
        tmp_path,
        monkeypatch,
        boredom_templates=[
            _linear_template("calm_quest"),
            _branching_template("choosy_quest"),
        ],
    )

    for seed in range(20):
        a_default = generate(intent="boredom", slot=None, context=None, hour=12, seed=seed)
        a_explicit = generate(
            intent="boredom",
            slot=None,
            context=None,
            hour=12,
            seed=seed,
            linear_only=False,
        )
        assert a_default.id == a_explicit.id
        assert a_default.template_id == a_explicit.template_id


def test_linear_only_falls_back_when_intent_has_no_linear_template(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When the requested intent pool is ALL branching, ``linear_only`` must
    fall back to the boredom ``always`` linear pool — not starve or return
    a branching activity."""
    isolated = tmp_path / "templates"
    isolated.mkdir()
    (isolated / "_schema.json").write_text(
        (TEMPLATES_DIR / "_schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # request_play: branching only -> nothing linear-eligible here.
    (isolated / "request_play.json").write_text(
        json.dumps(
            {
                "intent": "request_play",
                "templates": [_branching_template("rp_branch")],
            }
        ),
        encoding="utf-8",
    )
    # boredom: a linear always template is the safety-net fallback pool.
    (isolated / "boredom.json").write_text(
        json.dumps(
            {
                "intent": "boredom",
                "templates": [_linear_template("boredom_calm")],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", isolated)
    clear_template_cache()

    activity = generate(
        intent="request_play",
        slot=None,
        context=None,
        hour=12,
        seed=3,
        linear_only=True,
    )
    # Fell back to the boredom linear pool — choice-free.
    assert activity.template_id == "boredom_calm"
    for step in activity.steps:
        assert step.choices_rendered is None
