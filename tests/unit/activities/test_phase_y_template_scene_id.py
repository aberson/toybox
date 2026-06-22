"""Phase Y Step Y3 — optional template ``scene_id`` field + validator.

Pins that:
* ``Template`` accepts an optional ``scene_id`` (typed str | None).
* :func:`validate_template` raises when ``scene_id`` is set but not a member of
  ``scene_catalog.SCENE_IDS``; ``None``/absent and valid ids pass.
* the generator-side ``_Template`` dataclass carries ``scene_id`` through
  ``_parse_template`` so the propose path (Y5) can read it without re-parsing.
* legacy templates (no ``scene_id``) are unaffected.
"""

from __future__ import annotations

from typing import Any

import pytest

from toybox.activities._validator import TemplateGraphError, validate_template
from toybox.activities.generator import _parse_template
from toybox.activities.models import Template
from toybox.activities.scene_catalog import DEFAULT_SCENE_ID, SCENE_IDS


def _minimal_template(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "scene_test_tpl",
        "title": "Scene Test Template",
        "buckets": ["always"],
        "steps": [
            {"id": "s1", "text": "Step one.", "next": "s2"},
            {"id": "s2", "text": "Step two.", "next": "s3"},
            {"id": "s3", "text": "Step three."},
        ],
    }
    base.update(overrides)
    return base


def test_scene_id_absent_is_allowed() -> None:
    template = Template.model_validate(_minimal_template())
    assert template.scene_id is None
    validate_template(template)  # must not raise


def test_valid_scene_id_passes() -> None:
    template = Template.model_validate(_minimal_template(scene_id=DEFAULT_SCENE_ID))
    assert template.scene_id == DEFAULT_SCENE_ID
    validate_template(template)  # must not raise


def test_every_scene_id_is_individually_valid() -> None:
    for scene in SCENE_IDS:
        template = Template.model_validate(_minimal_template(scene_id=scene))
        validate_template(template)  # must not raise for any catalog id


def test_unknown_scene_id_rejected() -> None:
    template = Template.model_validate(_minimal_template(scene_id="atlantis"))
    with pytest.raises(TemplateGraphError, match="atlantis"):
        validate_template(template)


def test_parse_template_carries_scene_id() -> None:
    parsed = _parse_template(_minimal_template(scene_id="forest"), source="test")
    assert parsed.scene_id == "forest"


def test_parse_template_defaults_scene_id_to_none() -> None:
    parsed = _parse_template(_minimal_template(), source="test")
    assert parsed.scene_id is None
