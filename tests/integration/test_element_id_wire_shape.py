"""Phase M Step M3 — element_id wire-shape integration test.

The canonical wire-shape test for the M3 element-card surface. Pins the
full producer → consumer round trip per
``.claude/rules/code-quality.md`` § "Audit wire shape when storage
representation changes": a real propose / approve / advance flow that
asserts ``steps[N].element_id`` AND the denormalized
``element_*`` metadata fields are present on the REST GET response at
EVERY state the kiosk can render in. Iter-1 review found the iter-1
``_row_to_response`` gated element-id resolution on
``state in (proposed, approved)``, so element_id silently disappeared
once the activity entered ``running``. A wire-shape test like this one
would have caught it immediately — mock-heavy unit tests can't see
producer / consumer drift.

The test stages a single-template fixture with the element_id pinned to
``au-79`` (Gold — present in the shipped corpus), drives a propose →
approve → advance lifecycle, and asserts the wire envelope at each
state. Uses the real element corpus (no mocks).
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
# Fixture: a 3-step linear template whose steps[0] carries
# ``element_id: "au-79"`` (Gold). steps[0] needs an ``id`` so the
# template lookup in ``_resolve_element_id_for_persisted_step`` resolves
# the persisted ``activity_steps.step_template_id`` back to the template
# step that owns the ``element_id``.
# ---------------------------------------------------------------------------

_FIXTURE_TEMPLATE: dict[str, Any] = {
    "intent": "request_play",
    "templates": [
        {
            "id": "m3_wire_shape_fixture",
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


@pytest.fixture
def element_template_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Stage a templates dir containing ONLY our element-bearing template
    (+ the schema) and point :data:`toybox.activities.generator.TEMPLATES_DIR`
    at it so the seeded picker MUST land on it. Mirrors the
    ``branching_templates_dir`` pattern from ``test_g2_lazy_insertion``.
    """
    from toybox.activities import generator

    staged = tmp_path / "templates_m3_wire"
    staged.mkdir()
    (staged / "request_play.json").write_text(json.dumps(_FIXTURE_TEMPLATE), encoding="utf-8")
    # Copy the schema so the loader's jsonschema validator resolves.
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
    yield staged
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


def _find_step_with_element_id(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the step whose ``element_id`` is ``"au-79"``. Lifts the
    needle out of the haystack so the wire-shape assertions don't care
    whether the rendering uses the full preview plan (proposed/approved)
    or the activity_steps row (running/completed) — at every state the
    element-bearing step is present in ``steps`` exactly once.
    """
    matches = [s for s in steps if s.get("element_id") == "au-79"]
    assert len(matches) == 1, (
        f"expected exactly one step with element_id='au-79', got {len(matches)}; steps={steps!r}"
    )
    return matches[0]


def _assert_element_metadata(step: dict[str, Any], *, state: str) -> None:
    """Assert the four element fields ride end-to-end through the wire
    envelope at the given ``state``. State name is folded into the
    failure message so the iter-1 regression ("present at proposed but
    missing at running") would surface its exact transition point.
    """
    assert step.get("element_id") == "au-79", (
        f"[{state}] step.element_id missing or wrong: {step!r}"
    )
    metadata = step.get("metadata") or {}
    assert metadata.get("element_symbol") == "Au", (
        f"[{state}] metadata.element_symbol missing or wrong: {metadata!r}"
    )
    assert metadata.get("element_name") == "Gold", (
        f"[{state}] metadata.element_name missing or wrong: {metadata!r}"
    )
    assert metadata.get("element_atomic_number") == 79, (
        f"[{state}] metadata.element_atomic_number missing or wrong: {metadata!r}"
    )


def test_element_id_present_on_wire_through_running_state(
    client: TestClient,
    parent_headers: dict[str, str],
    element_template_dir: Path,
) -> None:
    """Propose → approve → start play, asserting element_id + denormalized
    fields are on the wire at proposed AND approved AND running.

    Iter-1 review found that the running-state path called
    ``_fetch_steps(template_id=None)`` (state-gated decoupling miss), so
    element_id evaporated for the kiosk. This test goes red→green at the
    HIGH #1 fix: resolving the template_id off ``activities.summary``
    UNCONDITIONALLY (not just for the preview states).
    """
    # State: proposed — preview plan path.
    proposed = _propose(client, parent_headers)
    assert proposed["state"] == "proposed"
    element_step = _find_step_with_element_id(proposed["steps"])
    _assert_element_metadata(element_step, state="proposed")

    activity_id = proposed["id"]
    version = proposed["version"]

    # State: approved — still preview plan path.
    approve = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert approve.status_code == 200, approve.text
    approved = approve.json()
    assert approved["state"] == "approved"
    element_step = _find_step_with_element_id(approved["steps"])
    _assert_element_metadata(element_step, state="approved")
    version = approved["version"]

    # State: running — first advance flips approved → running, narrowing
    # the steps list to the kid's actually-played path
    # (activity_steps). The HIGH #1 fix MUST resolve element_id from
    # ``activities.summary``'s template_id on this path too.
    advance = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert advance.status_code == 200, advance.text
    running = advance.json()
    assert running["state"] == "running"
    element_step = _find_step_with_element_id(running["steps"])
    _assert_element_metadata(element_step, state="running")


def test_element_id_present_on_rest_get_at_running_state(
    client: TestClient,
    parent_headers: dict[str, str],
    element_template_dir: Path,
) -> None:
    """GET ``/api/activities/<id>`` returns the same wire envelope as
    the propose/advance responses — pin element_id at running state via
    a fresh GET (not just via the advance response chain) so the wire
    shape is verified through the exact code path the parent UI re-reads
    on refresh.
    """
    proposed = _propose(client, parent_headers)
    activity_id = proposed["id"]
    version = proposed["version"]

    approve = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert approve.status_code == 200, approve.text
    version = approve.json()["version"]

    advance = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert advance.status_code == 200, advance.text
    assert advance.json()["state"] == "running"

    # Fresh GET — separately exercises _row_to_response without riding
    # the advance handler's return value.
    fetched = client.get(
        f"/api/activities/{activity_id}",
        headers=parent_headers,
    )
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["state"] == "running"
    element_step = _find_step_with_element_id(body["steps"])
    _assert_element_metadata(element_step, state="running-via-rest-get")
