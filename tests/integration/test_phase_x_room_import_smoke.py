"""Phase X Step X7 — no-mock room-import pipeline SMOKE GATE.

One real end-to-end cycle through the REAL production handlers, with NO
mocks beyond the two injectable boundaries that are model/network seams:

* ``get_photo_fetcher`` → a stub returning local fixture image bytes
  (so the commit path runs the REAL ``storage.images``
  validate→stage→commit, just without the network), and
* ``get_room_classifier`` → a model-free fake (no ONNX).

Everything else is production code: ``POST /api/rooms/import/parse``
(X2 listing parser → X3 room naming), ``POST /api/rooms/import/commit``
(X4 fetch/validate/stage/commit + X1 room_type/active persistence), the
play-time ``resolve_rooms`` selector (the X1↔X5 cross-seam), and the
``get_room`` tool the generator calls mid-propose.

The point is BREADTH, not depth: a single parse→fetch→validate→stage→
commit→persist→play-exclusion cycle that proves the wiring holds across
X1–X5. Per-feature edge coverage lives in the per-step suites
(``test_rooms_api.py``); this gate exists to surface producer→consumer
SHAPE drift those unit tests can't see because they mock either side.

Reuses the X5 import-test machinery (the conftest ``app`` + parent
token + the ``get_photo_fetcher`` / ``get_room_classifier`` dependency
overrides) — see ``test_rooms_api.py`` for the per-feature variants.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from toybox.activities.content_resolver import resolve_rooms
from toybox.ai.tools import ToolContext, call_tool
from toybox.api.rooms import get_photo_fetcher, get_room_classifier
from toybox.db.connection import connect

# The X2 Redfin HTML fixture (shared with the X5 parse tests).
_REDFIN_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "listings" / "redfin_sample.html"
)


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin ``TOYBOX_DATA_DIR`` at the per-test tmp dir so committed photo
    files land under a sandboxed ``images/rooms/`` we can assert against.

    Mirrors the autouse fixture in ``test_rooms_api.py`` (the X5 suite).
    """
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    return tmp_path


def _png_bytes(
    size: tuple[int, int] = (48, 48),
    color: tuple[int, int, int] = (40, 120, 200),
) -> bytes:
    """A tiny valid PNG, generated in-memory (no on-disk fixture needed).

    Mirrors the in-memory-Pillow approach the X4/storage tests use. PNG
    (rather than the X5 helper's JPEG) so the stub fetcher exercises the
    PNG branch of ``storage.images.validate_upload`` → ``stage`` →
    ``commit_staging``.
    """
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


class _FakeClassifier:
    """Model-free room classifier (the ``room_match._Classifier`` shape).

    Records calls so the smoke test can assert the injected advisory
    classifier was actually reached end-to-end through the real commit
    handler. Returns a canned high-confidence score map.
    """

    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def classify(self, image_bytes: bytes) -> dict[str, float]:
        self.calls.append(image_bytes)
        return {"living_room": 0.9}


def _commit_app(app: FastAPI, fetcher: Any, classifier: Any) -> FastAPI:
    """Override the import-commit injectable seams (no network, no model)."""
    app.dependency_overrides[get_photo_fetcher] = lambda: fetcher
    app.dependency_overrides[get_room_classifier] = lambda: classifier
    return app


def test_phase_x_room_import_full_cycle_smoke(
    app: FastAPI,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    db_path: Path,
) -> None:
    """parse → fetch(stub) → validate → stage → commit → persist → play-exclusion.

    ONE real cycle through the production handlers. The only non-real
    pieces are the photo-fetch boundary (returns local bytes) and the
    classifier (no model) — both are injected, both are exercised.
    """
    # A keyword-FREE URL on purpose: ``room_match._match_filename`` would
    # short-circuit on a filename containing a room keyword (e.g.
    # "...bedroom-1.jpg") and NEVER call the classifier. Using a neutral
    # segment forces the commit path through the real advisory CLIP seam,
    # so this smoke gate actually exercises the injected classifier.
    photo_url = "https://ssl.cdn-redfin.com/genMid.photo-42.jpg"
    photo_bytes = _png_bytes(color=(40, 120, 200))
    fetcher_calls: list[str] = []

    def _fetcher(url: str) -> bytes:
        fetcher_calls.append(url)
        return photo_bytes

    classifier = _FakeClassifier()
    _commit_app(app, _fetcher, classifier)

    with TestClient(app) as client:
        # 1) PARSE — the real X2 listing parser + X3 room naming. No DB
        #    write happens here (pure/offline endpoint).
        content = _REDFIN_FIXTURE.read_text(encoding="utf-8")
        parse_resp = client.post(
            "/api/rooms/import/parse",
            json={"content": content},
            headers=parent_headers,
        )
        assert parse_resp.status_code == 200, parse_resp.text
        parsed = parse_resp.json()

        # Sensible proposed_rooms (3 beds + 2 baths + named rooms) and a
        # de-duplicated photo_urls list.
        by_type: dict[str, int] = {}
        for room in parsed["proposed_rooms"]:
            by_type[room["room_type"]] = by_type.get(room["room_type"], 0) + 1
        assert by_type.get("bedroom") == 3
        assert by_type.get("bathroom") == 2
        assert "kitchen" in by_type
        assert "living_room" in by_type
        photo_urls = parsed["photo_urls"]
        assert any("genMid.bedroom-1.jpg" in u for u in photo_urls)
        assert len(photo_urls) == len(set(photo_urls))  # de-duped

        # No DB write from parse.
        conn = connect(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0
        finally:
            conn.close()

        # 2) COMMIT — a reviewed plan built off the parse result. Three
        #    rooms, mixing:
        #      - a photo'd room (exercises validate→stage→commit on disk),
        #      - an N/A room (photo_url=None),
        #      - a "stay out" room (active=False).
        commit_body = {
            "rooms": [
                {
                    "display_name": "Main Bedroom",
                    "room_type": "bedroom",
                    "active": True,
                    "photo_url": photo_url,
                },
                {
                    "display_name": "Hall Closet",
                    "room_type": None,
                    "active": True,
                    "photo_url": None,
                },
                {
                    "display_name": "Primary Bath",
                    "room_type": "bathroom",
                    "active": False,  # "stay out" — must not leak into play
                    "photo_url": None,
                },
            ]
        }
        commit_resp = client.post(
            "/api/rooms/import/commit",
            json=commit_body,
            headers=parent_headers,
        )
        assert commit_resp.status_code == 201, commit_resp.text
        out_rooms = {r["display_name"]: r for r in commit_resp.json()["rooms"]}
        assert set(out_rooms) == {"Main Bedroom", "Hall Closet", "Primary Bath"}

    # The stub fetcher was reached exactly once (only the one photo'd room).
    assert fetcher_calls == [photo_url]
    # The advisory classifier was reached end-to-end for the photo'd room.
    assert len(classifier.calls) == 1

    # ---- Persistence assertions (read the DB directly) -----------------
    conn = connect(db_path)
    try:
        rows = {
            r["display_name"]: r
            for r in conn.execute(
                "SELECT display_name, room_type, active, image_path FROM rooms"
            ).fetchall()
        }
        assert set(rows) == {"Main Bedroom", "Hall Closet", "Primary Bath"}

        # display_name / room_type / active persisted correctly.
        assert rows["Main Bedroom"]["room_type"] == "bedroom"
        assert bool(rows["Main Bedroom"]["active"]) is True
        assert rows["Hall Closet"]["room_type"] is None
        assert bool(rows["Hall Closet"]["active"]) is True
        assert rows["Primary Bath"]["room_type"] == "bathroom"
        assert bool(rows["Primary Bath"]["active"]) is False

        # The photo'd room has a real image_path + the file exists on disk
        # under data/images/rooms/.
        bedroom_path = rows["Main Bedroom"]["image_path"]
        assert bedroom_path is not None
        assert bedroom_path.startswith("data/images/rooms/")
        on_disk = isolated_data_root / bedroom_path.removeprefix("data/")
        assert on_disk.is_file()

        # The N/A room and the "stay out" room have NULL image_path.
        assert rows["Hall Closet"]["image_path"] is None
        assert rows["Primary Bath"]["image_path"] is None

        # 3) PLAY-SELECTION DRIFT CATCH (X1↔X5 cross-seam) ---------------
        # The active=False "stay out" room must be EXCLUDED from the
        # play-time selector (resolve_rooms) AND from the get_room tool,
        # but still PRESENT in the parent-facing GET /api/rooms listing.
        play_rooms = {r.display_name for r in resolve_rooms(conn, limit=10_000)}
        assert "Main Bedroom" in play_rooms
        assert "Hall Closet" in play_rooms
        assert "Primary Bath" not in play_rooms, (
            "a 'stay out' (active=false) room imported via X5 leaked into "
            "the play-time resolve_rooms selector"
        )

        # Map display_name -> id for the get_room cross-check.
        id_by_name = {
            r["display_name"]: r["id"]
            for r in conn.execute("SELECT id, display_name FROM rooms").fetchall()
        }
        active_room_id = id_by_name["Main Bedroom"]
        inactive_room_id = id_by_name["Primary Bath"]
    finally:
        conn.close()

    # get_room via the REAL async tool-dispatch entry point (validates
    # args + runs the production resolver). The active room resolves; the
    # inactive "stay out" room must NOT (play-time active=1 filter).
    ctx = ToolContext(connection_factory=lambda: connect(db_path, check_same_thread=False))

    active_result = _run_get_room(ctx, active_room_id)
    assert active_result["error"] is None, active_result
    assert active_result["data"] is not None
    assert active_result["data"]["name"] == "Main Bedroom"

    inactive_result = _run_get_room(ctx, inactive_room_id)
    # Not an error shape — a clean "room not found" miss (the active=1
    # filter hid it), proving the inactive room is unreachable at play time.
    assert inactive_result["error"] is None, inactive_result
    assert inactive_result["data"] is None, (
        "get_room resurfaced a 'stay out' (active=false) room imported via X5"
    )

    # The parent-facing listing still shows ALL three rooms (the "stay
    # out" room is managed there, just excluded from play).
    with TestClient(app) as client:
        list_resp = client.get("/api/rooms", headers=parent_headers)
    assert list_resp.status_code == 200, list_resp.text
    listed = {r["display_name"] for r in list_resp.json()["rooms"]}
    assert listed == {"Main Bedroom", "Hall Closet", "Primary Bath"}


def _run_get_room(ctx: ToolContext, room_id: str) -> dict[str, Any]:
    """Invoke the async ``call_tool('get_room', ...)`` from sync test code."""
    import asyncio  # noqa: PLC0415

    return asyncio.run(call_tool("get_room", {"room_id": room_id}, ctx))
