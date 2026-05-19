"""Phase O Step O2 — wire-shape additions integration test.

Pins three typed-field additions per ``documentation/phase-o-plan.md`` §2:

1. :class:`toybox.api.activities.ActivityResponse` gains
   ``template_id: str | None`` — surfaced from
   ``metadata["template_id"]`` (already persisted server-side per
   :file:`src/toybox/api/activities.py` ~line 2306). ``None`` when
   metadata has no template_id.
2. :class:`toybox.api.activities.ActivityResponse` gains
   ``recommended_themes: list[str]`` — derived from the template's
   ``recommended_themes`` field; empty list when the template lacks
   the field.
3. :class:`toybox.api.activities.ActivityStepResponse` already carries
   ``element_id`` (M3); Phase O re-asserts the wire shape end-to-end
   for both element-bearing and non-element activities, pinning the
   value as ``null`` (not missing) on non-element steps.

Pattern: mirror :file:`tests/integration/test_element_id_wire_shape.py`
— stage a per-test fixture templates dir, drive a propose, then assert
the REST GET + propose response include the new fields with the correct
values. The wire-shape test is the producer→consumer round-trip
required by ``.claude/rules/code-quality.md`` §"Audit wire shape when
storage representation changes".

The model-field assertions (``model_fields`` introspection) sit
alongside the wire-flow assertions so a future silent removal of a
field surfaces here with a clear "model doesn't expose it" diagnosis
BEFORE the wire-shape tests fire with a misleading "field absent on
response" failure.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixture templates
# ---------------------------------------------------------------------------
#
# Two single-template intents staged into a tmp templates dir so the
# seeded picker MUST land on them. Conftest's autouse fixture
# (_isolate_to_production_templates) is overridden per-test by setting
# generator.TEMPLATES_DIR via monkeypatch — same pattern as the M3 wire
# shape test.
#
# ``with_themes`` template: declares ``recommended_themes: ["feelings"]``
# so the wire envelope must reflect that exact list.
# ``without_themes`` template: omits ``recommended_themes`` entirely so
# the wire envelope must reflect ``[]`` (NOT null, NOT missing).
# ``with_element`` template: every step has ``element_id`` set so the
# wire envelope must reflect ``element_id: "au-79"`` on each step.

_FIXTURE_WITH_THEMES: dict[str, Any] = {
    "intent": "request_play",
    "templates": [
        {
            "id": "phase_o_with_themes_fixture",
            "title": "Talk about big feelings",
            "buckets": ["always"],
            "recommended_themes": ["feelings"],
            "steps": [
                {"text": "Name one feeling you had today."},
                {"text": "Tell your toy why you felt that way."},
                {"text": "Give your toy a hug."},
            ],
        }
    ],
}

_FIXTURE_WITHOUT_THEMES: dict[str, Any] = {
    "intent": "request_play",
    "templates": [
        {
            "id": "phase_o_without_themes_fixture",
            "title": "Quick play",
            "buckets": ["always"],
            "steps": [
                {"text": "Pick a toy."},
                {"text": "Tell your toy a joke."},
                {"text": "Take a tiny bow."},
            ],
        }
    ],
}

_FIXTURE_WITH_ELEMENT: dict[str, Any] = {
    "intent": "request_play",
    "templates": [
        {
            "id": "phase_o_element_fixture",
            "title": "Look at the Gold sprite",
            "buckets": ["always"],
            "steps": [
                {
                    "id": "open",
                    "text": "Find the shiny element on your screen.",
                    "element_id": "au-79",
                },
                {"text": "Tell your toy what color it is."},
                {"text": "Wave goodbye to the element."},
            ],
        }
    ],
}


def _stage_templates_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, Any],
    *,
    subdir: str,
) -> Path:
    """Stage a per-test templates dir containing exactly one intent file
    + the schema, point generator.TEMPLATES_DIR at it, and return the
    path. Mirrors the ``element_template_dir`` pattern in
    ``test_element_id_wire_shape.py``.
    """
    from toybox.activities import generator

    staged = tmp_path / subdir
    staged.mkdir()
    (staged / "request_play.json").write_text(json.dumps(fixture), encoding="utf-8")
    src_schema = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "toybox"
        / "activities"
        / "templates"
        / "_schema.json"
    )
    shutil.copy(src_schema, staged / "_schema.json")
    monkeypatch.setattr(generator, "TEMPLATES_DIR", staged)
    generator.clear_template_cache()
    return staged


@pytest.fixture
def templates_with_themes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    yield _stage_templates_dir(
        tmp_path,
        monkeypatch,
        _FIXTURE_WITH_THEMES,
        subdir="templates_phase_o_with_themes",
    )
    from toybox.activities import generator

    generator.clear_template_cache()


@pytest.fixture
def templates_without_themes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    yield _stage_templates_dir(
        tmp_path,
        monkeypatch,
        _FIXTURE_WITHOUT_THEMES,
        subdir="templates_phase_o_without_themes",
    )
    from toybox.activities import generator

    generator.clear_template_cache()


@pytest.fixture
def templates_with_element(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    yield _stage_templates_dir(
        tmp_path,
        monkeypatch,
        _FIXTURE_WITH_ELEMENT,
        subdir="templates_phase_o_with_element",
    )
    from toybox.activities import generator

    generator.clear_template_cache()


def _propose(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json={"intent": "request_play", "slot": None, "hour": 12, "seed": 11},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# ---------------------------------------------------------------------------
# Deliverable A — ActivityResponse gains template_id + recommended_themes
# ---------------------------------------------------------------------------


def test_activity_response_model_has_template_id_field() -> None:
    """``ActivityResponse.model_fields`` must expose ``template_id``.

    Without the field on the Pydantic model, ``model_dump`` drops the
    value on the floor and the wire-flow tests below would fail with a
    misleading "field absent on response" diagnosis instead of the
    real "model doesn't have it" cause. Pin both surfaces.
    """
    from toybox.api.activities import ActivityResponse

    assert "template_id" in ActivityResponse.model_fields, (
        "ActivityResponse.model_fields must include 'template_id' so "
        "the wire envelope surfaces the template attribution that the "
        "backend already persists in metadata. Currently fields = "
        f"{sorted(ActivityResponse.model_fields.keys())!r}."
    )
    field = ActivityResponse.model_fields["template_id"]
    # Type must be Optional[str] — surfaces None for legacy / ad-hoc
    # rows whose metadata never had a template_id.
    annotation = field.annotation
    annotation_str = str(annotation)
    assert "str" in annotation_str and ("None" in annotation_str or "Optional" in annotation_str), (
        f"ActivityResponse.template_id must be Optional[str]; got {annotation!r}"
    )


def test_activity_response_model_has_recommended_themes_field() -> None:
    """``ActivityResponse.model_fields`` must expose ``recommended_themes``.

    Type must be ``list[str]`` (not ``list[Theme]``) so the wire shape
    is a JSON array of plain strings — the frontend's ``categorize()``
    helper intersects this with a hard-coded ``"feelings"`` literal and
    a Theme enum on the wire would either leak the Python class name
    into TypeScript or force a runtime cast.
    """
    from toybox.api.activities import ActivityResponse

    assert "recommended_themes" in ActivityResponse.model_fields, (
        "ActivityResponse.model_fields must include 'recommended_themes' "
        "so the parent UI's categorize() helper can read template "
        "themes off the wire. Currently fields = "
        f"{sorted(ActivityResponse.model_fields.keys())!r}."
    )
    field = ActivityResponse.model_fields["recommended_themes"]
    annotation_str = str(field.annotation)
    assert "list" in annotation_str.lower() or "List" in annotation_str, (
        f"ActivityResponse.recommended_themes must be a list type; "
        f"got {field.annotation!r}"
    )
    assert "str" in annotation_str, (
        f"ActivityResponse.recommended_themes must be list[str] (not "
        f"list[Theme] — Theme would leak the Python class into wire "
        f"shape); got {field.annotation!r}"
    )


def test_propose_response_carries_recommended_themes_from_template(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_with_themes: Path,
) -> None:
    """Propose against a template with ``recommended_themes: ["feelings"]``;
    the response envelope must include ``recommended_themes: ["feelings"]``.
    """
    body = _propose(client, parent_headers)
    assert "recommended_themes" in body, (
        f"propose response missing 'recommended_themes' key; body keys = "
        f"{sorted(body.keys())!r}"
    )
    assert body["recommended_themes"] == ["feelings"], (
        f"expected recommended_themes=['feelings'] from template; "
        f"got {body['recommended_themes']!r}"
    )


def test_propose_response_recommended_themes_empty_when_template_omits(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_without_themes: Path,
) -> None:
    """A template that omits ``recommended_themes`` must serialize as
    ``recommended_themes: []`` — NOT null, NOT missing.
    """
    body = _propose(client, parent_headers)
    assert "recommended_themes" in body, (
        f"propose response missing 'recommended_themes' key (must be "
        f"present even when template omits the field); body keys = "
        f"{sorted(body.keys())!r}"
    )
    assert body["recommended_themes"] == [], (
        f"expected recommended_themes=[] when template omits the field; "
        f"got {body['recommended_themes']!r}"
    )


def test_propose_response_template_id_round_trips(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_with_themes: Path,
) -> None:
    """``template_id`` on the typed Activity envelope must equal the
    template's ``id`` (which is also persisted to
    ``activity.metadata["template_id"]`` per
    ``src/toybox/api/activities.py:2306``).
    """
    body = _propose(client, parent_headers)
    assert "template_id" in body, (
        f"propose response missing 'template_id' key; body keys = "
        f"{sorted(body.keys())!r}"
    )
    assert body["template_id"] == "phase_o_with_themes_fixture", (
        f"expected template_id='phase_o_with_themes_fixture'; got "
        f"{body['template_id']!r}"
    )
    # Producer→consumer round-trip pin: the typed top-level
    # ``template_id`` field must match what the existing
    # ``metadata["template_id"]`` carries (the two surfaces must NEVER
    # diverge — they're two views of the same persisted value).
    metadata = body.get("metadata") or {}
    assert metadata.get("template_id") == body["template_id"], (
        f"top-level template_id ({body['template_id']!r}) does not "
        f"match metadata.template_id ({metadata.get('template_id')!r}); "
        "the two surfaces must mirror the same persisted value"
    )


def test_get_response_carries_template_id_and_themes(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_with_themes: Path,
) -> None:
    """GET ``/api/activities/<id>`` returns the same wire envelope as the
    propose response — verify the new fields ride the GET path too
    (the parent UI re-reads activities via this route on refresh).
    """
    proposed = _propose(client, parent_headers)
    activity_id = proposed["id"]
    fetched = client.get(
        f"/api/activities/{activity_id}",
        headers=parent_headers,
    )
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["template_id"] == "phase_o_with_themes_fixture"
    assert body["recommended_themes"] == ["feelings"]


# ---------------------------------------------------------------------------
# Deliverable B — ActivityStepResponse.element_id pin
# ---------------------------------------------------------------------------


def test_activity_step_response_model_has_element_id_field() -> None:
    """``ActivityStepResponse.model_fields`` must expose ``element_id``
    as ``str | None``. M3 already added the field; this test pins the
    Phase O re-assertion so a future silent removal trips here before
    the wire-flow tests below fire with a misleading "missing field"
    diagnosis.
    """
    from toybox.api.activities import ActivityStepResponse

    assert "element_id" in ActivityStepResponse.model_fields, (
        "ActivityStepResponse.model_fields must include 'element_id'. "
        f"Currently fields = {sorted(ActivityStepResponse.model_fields.keys())!r}."
    )
    field = ActivityStepResponse.model_fields["element_id"]
    annotation_str = str(field.annotation)
    assert "str" in annotation_str and ("None" in annotation_str or "Optional" in annotation_str), (
        f"ActivityStepResponse.element_id must be Optional[str]; "
        f"got {field.annotation!r}"
    )


def test_propose_response_element_bearing_step_carries_element_id(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_with_element: Path,
) -> None:
    """An activity proposed from a template whose ``steps[0].element_id``
    is set must serialize that step with ``element_id: "au-79"`` on the
    wire. Mirrors ``test_element_id_wire_shape.py`` but pins the Phase
    O re-assertion so the deliverable B contract has its own surface.
    """
    body = _propose(client, parent_headers)
    steps = body["steps"]
    # The element-bearing step's index isn't guaranteed (preview-plan
    # render may insert/drop steps), so search by element_id rather
    # than by index.
    matches = [s for s in steps if s.get("element_id") == "au-79"]
    assert len(matches) == 1, (
        f"expected exactly one step with element_id='au-79'; got "
        f"{len(matches)} matches in steps={steps!r}"
    )


def test_propose_response_non_element_step_serializes_element_id_null(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_without_themes: Path,
) -> None:
    """A non-element activity's steps must serialize ``element_id: null``
    (NOT missing) so the frontend's ``categorize()`` helper can read
    the field unconditionally.
    """
    body = _propose(client, parent_headers)
    steps = body["steps"]
    assert len(steps) > 0, "propose response must include at least one step"
    for step in steps:
        # Per Pydantic + the M3 contract, the field MUST be present on
        # every step. The value is null for non-element steps.
        assert "element_id" in step, (
            f"step missing 'element_id' key (must be present, value=null "
            f"on non-element steps): step={step!r}"
        )
        assert step["element_id"] is None, (
            f"non-element step has unexpected element_id="
            f"{step['element_id']!r}: step={step!r}"
        )
