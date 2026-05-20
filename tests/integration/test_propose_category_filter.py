"""Integration test for ``ProposeRequest.category`` (Phase O follow-up).

When the parent triggers from a category sub-tab (Adventures / Elements /
Feelings & Friends), the propose handler must restrict the template pool
so the returned activity lands in the same sub-tab post-categorization.

Pattern: stage a per-test templates dir with three single-template intent
candidates (one element, one SEL, one adventure) so the picker has
exactly one match per category. Then drive a propose with each category
value and assert the returned ``template_id`` matches the expected
fixture id.

Mirrors :file:`tests/integration/test_phase_o_wire_shape.py` for fixture
plumbing.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Three single-template intent fixtures, one per category bucket. All
# share the same intent (``request_play``) so the picker's pool contains
# all three; the category filter is what selects which one wins.
_MIXED_FIXTURE: dict[str, Any] = {
    "intent": "request_play",
    "templates": [
        {
            "id": "category_filter_adv_fixture",
            "title": "Adventure with your toy",
            "buckets": ["always"],
            "steps": [
                {"text": "Pick a toy to be your adventurer."},
                {"text": "Take three steps forward."},
                {"text": "Take a bow."},
            ],
        },
        {
            "id": "category_filter_elem_fixture",
            "title": "Meet Gold",
            "buckets": ["always"],
            "steps": [
                {
                    "id": "open",
                    "text": "Find the shiny element.",
                    "element_id": "au-79",
                },
                {"text": "Tell your toy what color it is."},
                {"text": "Wave goodbye."},
            ],
        },
        {
            "id": "category_filter_sel_fixture",
            "title": "Name a feeling",
            "buckets": ["always"],
            "recommended_themes": ["feelings"],
            "steps": [
                {"text": "Name one feeling you had today."},
                {"text": "Tell your toy why you felt that way."},
                {"text": "Give your toy a hug."},
            ],
        },
    ],
}


def _stage_mixed_templates_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    from toybox.activities import generator

    staged = tmp_path / "templates_propose_category_mixed"
    staged.mkdir()
    (staged / "request_play.json").write_text(
        json.dumps(_MIXED_FIXTURE),
        encoding="utf-8",
    )
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
def templates_mixed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    yield _stage_mixed_templates_dir(tmp_path, monkeypatch)
    from toybox.activities import generator

    generator.clear_template_cache()


def _propose(
    client: TestClient,
    headers: dict[str, str],
    *,
    category: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "intent": "request_play",
        "slot": None,
        "hour": 12,
        "seed": 11,
    }
    if category is not None:
        payload["category"] = category
    response = client.post(
        "/api/activities/propose",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_category_elements_returns_element_template(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_mixed: Path,
) -> None:
    headers = parent_headers
    body = _propose(client, headers, category="elements")
    assert body["template_id"] == "category_filter_elem_fixture"
    # And the returned step carries element_id.
    assert body["steps"][0]["element_id"] == "au-79"


def test_category_feelings_friends_returns_sel_template(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_mixed: Path,
) -> None:
    headers = parent_headers
    body = _propose(client, headers, category="feelings-friends")
    assert body["template_id"] == "category_filter_sel_fixture"
    assert "feelings" in body["recommended_themes"]


def test_category_adventures_returns_adventure_template(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_mixed: Path,
) -> None:
    headers = parent_headers
    body = _propose(client, headers, category="adventures")
    assert body["template_id"] == "category_filter_adv_fixture"
    # And the returned activity has no element steps + no feelings theme.
    assert all(step["element_id"] is None for step in body["steps"])
    assert "feelings" not in body["recommended_themes"]


def test_category_none_falls_through_to_picker_default(
    client: TestClient,
    parent_headers: dict[str, str],
    templates_mixed: Path,
) -> None:
    headers = parent_headers
    body = _propose(client, headers, category=None)
    # No filter → picker draws from full pool. Just assert it lands on
    # one of the three fixtures (deterministic by seed but we don't pin
    # which one, since the test is about the filter being a no-op when
    # absent).
    assert body["template_id"] in {
        "category_filter_adv_fixture",
        "category_filter_elem_fixture",
        "category_filter_sel_fixture",
    }


def test_category_elements_with_no_element_template_falls_back(
    client: TestClient,
    parent_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft-fallback: when no template matches the category, picker draws
    from the unfiltered pool rather than starving."""
    headers = parent_headers

    only_adv_fixture: dict[str, Any] = {
        "intent": "request_play",
        "templates": [
            {
                "id": "category_filter_adv_only",
                "title": "Adventure only",
                "buckets": ["always"],
                "steps": [
                    {"text": "Pick a toy."},
                    {"text": "Take three steps."},
                    {"text": "Bow."},
                ],
            }
        ],
    }
    from toybox.activities import generator

    staged = tmp_path / "templates_propose_category_only_adv"
    staged.mkdir()
    (staged / "request_play.json").write_text(
        json.dumps(only_adv_fixture),
        encoding="utf-8",
    )
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

    body = _propose(client, headers, category="elements")
    # Soft-fallback: returns the adventure template even though category
    # asked for an element. The frontend categorize() will bucket this
    # under Adventures on display — that's the soft-fallback contract.
    assert body["template_id"] == "category_filter_adv_only"

    generator.clear_template_cache()
