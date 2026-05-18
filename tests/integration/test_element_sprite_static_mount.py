"""Phase M Step M3 — element sprite static mount.

The kiosk's ``ElementCard`` loads sprites via
``GET /api/static/elements/<element_id>.png``; the mount is wired in
``app.py`` against the ``elements_root()`` helper in
``activities/element_corpus.py`` (resolves to ``<data_root>/images/
elements`` with the ``TOYBOX_DATA_DIR`` env override honoured).

This test does NOT depend on ``data/images/elements/h-1.png`` existing
on the production tree — M2b's sprite generation is deferred to
alongside M14. The test points ``TOYBOX_DATA_DIR`` at a tmp dir and
copies the bundled fixture (``tests/fixtures/element_sprite.png``) to
``<tmp>/images/elements/au-79.png`` before building the app. The
fixture is a 1×1 red PNG (~75 bytes) generated once via Pillow; pinning
it as a real PNG keeps the response content-type assertion meaningful.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from toybox.app import create_app

FIXTURE_PNG: Path = Path(__file__).resolve().parent.parent / "fixtures" / "element_sprite.png"


@pytest.fixture
def element_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Point ``TOYBOX_DATA_DIR`` at a tmp dir whose ``images/elements/``
    contains a single ``au-79.png`` (the bundled fixture). Reset on
    teardown so other tests aren't affected."""
    elements_dir = tmp_path / "images" / "elements"
    elements_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_PNG, elements_dir / "au-79.png")
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    yield tmp_path


def test_element_sprite_static_mount_serves_png(
    element_data_dir: Path,
) -> None:
    """``GET /api/static/elements/au-79.png`` returns 200 + image/png +
    the fixture bytes. The mount must be evaluated AT APP-BUILD time
    against the env-overridden data root — we instantiate ``create_app``
    here (NOT through the integration ``client`` fixture, which builds
    against the default data root before the env override fires).
    """
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/static/elements/au-79.png")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    fixture_bytes = FIXTURE_PNG.read_bytes()
    assert len(fixture_bytes) > 0
    assert response.content == fixture_bytes


def test_element_sprite_static_mount_404s_on_missing(
    element_data_dir: Path,
) -> None:
    """A request for a missing element sprite must 404 rather than 500
    so the kiosk's onError fallback (swap to the periodic-table avatar)
    fires cleanly."""
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/static/elements/zz-999.png")
    assert response.status_code == 404
