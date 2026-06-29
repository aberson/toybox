"""Phase Y end-to-end smoke gate (real components, no boundary mocks).

The producer -> consumer gate for scene backdrops (code-quality.md §15.5):

* **Static serve** — a scene PNG under ``data/images/scenes/`` is served at
  ``GET /api/static/images/scenes/<id>.png`` (200 + image/png) through the real
  ``create_app`` mount, with a 404 on a missing scene.
* **Template scene_id -> propose** — a template carrying ``scene_id`` drives the
  proposed activity's persisted ``scene_id`` AND the wire ``scene_url``.
* **Interest selection -> propose** — when the template has no ``scene_id``, a
  child whose ``interests`` map to a scene drives the resolved scene end-to-end.

Mocked unit tests can't catch producer/consumer drift here — these wire the real
propose handler, real migrated DB, real resolver, and real static mount.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.activities import generator
from toybox.activities.scene_catalog import DEFAULT_SCENE_ID
from toybox.app import create_app
from toybox.db.connection import connect

FIXTURE_PNG: Path = Path(__file__).resolve().parent.parent / "fixtures" / "element_sprite.png"
PROD_TEMPLATES_DIR: Path = (
    Path(__file__).resolve().parents[2] / "src" / "toybox" / "activities" / "templates"
)


# ---------------------------------------------------------------------------
# (A) Static serve — create_app mount against an env-overridden data root
# ---------------------------------------------------------------------------


@pytest.fixture
def scene_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """``TOYBOX_DATA_DIR`` -> tmp with ``images/scenes/lab.png`` (fixture PNG)."""
    scenes_dir = tmp_path / "images" / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_PNG, scenes_dir / "lab.png")
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    yield tmp_path


def test_scene_png_is_served(scene_data_dir: Path) -> None:
    # The mount captures the dir at app-build time, so build create_app AFTER
    # the env override (the integration ``client`` fixture builds too early).
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/static/images/scenes/lab.png")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.content == FIXTURE_PNG.read_bytes()


def test_missing_scene_png_404s(scene_data_dir: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/static/images/scenes/atlantis.png")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# (B) Template scene_id -> propose persists + serializes the authored scene
# ---------------------------------------------------------------------------


def _write_single_template(
    templates_dir: Path,
    *,
    intent: str,
    template_id: str,
    scene_id: str | None,
) -> None:
    """Write a one-template intent file (+ schema) with an optional scene_id."""
    templates_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(PROD_TEMPLATES_DIR / "_schema.json", templates_dir / "_schema.json")
    template: dict[str, Any] = {
        "id": template_id,
        "title": "Smoke template",
        "buckets": ["always"],
        "steps": [
            {"text": "One.", "action_slot": "idle"},
            {"text": "Two.", "action_slot": "idle"},
            {"text": "Three.", "action_slot": "idle"},
        ],
    }
    if scene_id is not None:
        template["scene_id"] = scene_id
    (templates_dir / f"{intent}.json").write_text(
        json.dumps({"intent": intent, "templates": [template]}),
        encoding="utf-8",
    )


def test_template_scene_id_drives_propose(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    templates_dir = tmp_path / "tpl_scene"
    _write_single_template(
        templates_dir, intent="boredom", template_id="smoke_scene_tpl", scene_id="forest"
    )
    monkeypatch.setattr(generator, "TEMPLATES_DIR", templates_dir)
    generator.clear_template_cache()

    response = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": 3},
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())
    assert body["scene_url"] == "/api/static/images/scenes/forest.png"

    conn = connect(db_path)
    try:
        row = conn.execute("SELECT scene_id FROM activities WHERE id = ?", (body["id"],)).fetchone()
    finally:
        conn.close()
    assert row is not None and row["scene_id"] == "forest"


# ---------------------------------------------------------------------------
# (C) Interest selection -> propose (template has no scene_id)
# ---------------------------------------------------------------------------


def test_child_interest_drives_scene_when_template_has_none(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Template WITHOUT a scene_id, so the resolver falls to the child's interest.
    templates_dir = tmp_path / "tpl_no_scene"
    _write_single_template(
        templates_dir, intent="boredom", template_id="smoke_plain_tpl", scene_id=None
    )
    monkeypatch.setattr(generator, "TEMPLATES_DIR", templates_dir)
    generator.clear_template_cache()

    # A child whose free-text interests map to the "lab" scene.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children "
                "(id, display_name, birthdate, pronouns, reading_level, "
                " interests, comfort, notes) "
                "VALUES (?, ?, NULL, NULL, NULL, ?, NULL, NULL)",
                ("child-b", "Child B", "loves the periodic table"),
            )
    finally:
        conn.close()

    response = client.post(
        "/api/activities/propose",
        json={
            "intent": "boredom",
            "slot": None,
            "hour": 12,
            "seed": 3,
            "context": {"child_ids": ["child-b"]},
        },
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())
    assert body["scene_url"] == "/api/static/images/scenes/lab.png"


def test_no_child_no_template_scene_falls_to_default(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    # Sanity: the production default templates (autouse) carry no scene_id and a
    # no-child propose has no interests -> DEFAULT_SCENE_ID.
    response = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": 3},
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())
    assert body["scene_url"] == f"/api/static/images/scenes/{DEFAULT_SCENE_ID}.png"
