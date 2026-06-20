"""Integration test for Phase W Step W2: the ``game_linearity`` dial
drives the propose path's template selection.

The wiring under test:

    settings.game_linearity == "linear"
      → _do_propose reads get_game_linearity(conn) == "linear"
      → generate(..., linear_only=True)
      → the picker excludes every template with a branching step

This is the producer→consumer round trip required by code-quality.md §4
(new behavior must be exercised through the production caller, not just
the generator unit). If the ``linear_only=linear_only`` thread-through
were dropped from ``_do_propose``, the ``linear`` case below would
sometimes return a branching activity and fail.

Strategy: stage a single intent pool containing BOTH a branching and a
linear template (so the unforced picker could land on either), set the
dial via the DB, and assert the returned step shape per dial value.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from toybox.db.connection import connect

# Same intent pool, two shapes. The branching template's step[0] has
# ``choices``; the linear one never does.
_MIXED_FIXTURE: dict[str, Any] = {
    "intent": "request_play",
    "templates": [
        {
            "id": "linearity_linear_fixture",
            "title": "A calm play",
            "buckets": ["always"],
            "steps": [
                {"text": "Pick a toy to play with."},
                {"text": "Take three steps forward."},
                {"text": "Take a bow."},
            ],
        },
        {
            "id": "linearity_branching_fixture",
            "title": "A choosy play",
            "buckets": ["always"],
            "steps": [
                {
                    "id": "open",
                    "text": "A fork in the path. Which way?",
                    "choices": [
                        {"label": "Go left", "next": "left_end"},
                        {"label": "Go right", "next": "right_end"},
                    ],
                },
                {"id": "left_end", "text": "You find a garden."},
                {"id": "right_end", "text": "You find a pond."},
            ],
        },
    ],
}


@pytest.fixture
def templates_mixed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Stage a request_play pool with one linear + one branching template.

    Overrides the conftest autouse production-only isolation for this test.
    """
    from toybox.activities import generator

    staged = tmp_path / "templates_linearity_mixed"
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
    yield staged
    generator.clear_template_cache()


def _set_linearity(db_path: Path, value: str) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('game_linearity', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (value,),
            )
    finally:
        conn.close()


def _propose(
    client: TestClient,
    headers: dict[str, str],
    *,
    seed: int,
) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json={
            "intent": "request_play",
            "slot": None,
            "hour": 12,
            "seed": seed,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _has_branching_step(body: dict[str, Any]) -> bool:
    """True iff any returned step carries non-empty ``choices``."""
    for step in body["steps"]:
        choices = step.get("choices")
        if choices:
            return True
    return False


def test_linear_dial_yields_only_linear_activities(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    templates_mixed: Path,
) -> None:
    """game_linearity='linear' -> every propose lands on the choice-free
    template, never the branching one — across a seed sweep."""
    _set_linearity(db_path, "linear")

    for seed in range(30):
        body = _propose(client, parent_headers, seed=seed)
        assert body["template_id"] == "linearity_linear_fixture", (
            f"linear dial returned branching template at seed={seed}: {body['template_id']}"
        )
        assert not _has_branching_step(body), (
            f"linear dial returned a branching step at seed={seed}"
        )


def test_nonlinear_dial_allows_branching_activities(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    templates_mixed: Path,
) -> None:
    """game_linearity='nonlinear' (the default) -> the branching template
    is reachable; over a seed sweep at least one branching activity appears
    AND at least one linear one (filter genuinely off)."""
    _set_linearity(db_path, "nonlinear")

    picked_ids: set[str] = set()
    saw_branching = False
    for seed in range(30):
        body = _propose(client, parent_headers, seed=seed)
        picked_ids.add(body["template_id"])
        if _has_branching_step(body):
            saw_branching = True

    assert "linearity_branching_fixture" in picked_ids, (
        "nonlinear dial never selected the branching template — the filter "
        "may be excluding it unconditionally"
    )
    assert saw_branching, "nonlinear dial never produced a branching step"
    assert "linearity_linear_fixture" in picked_ids


def test_default_dial_is_nonlinear(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    templates_mixed: Path,
) -> None:
    """With NO game_linearity row at all (legacy DB), propose behaves as
    nonlinear: the branching template stays reachable."""
    # Wipe the seeded row so the getter falls back to the default.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM settings WHERE key = 'game_linearity'")
    finally:
        conn.close()

    picked_ids: set[str] = set()
    for seed in range(30):
        body = _propose(client, parent_headers, seed=seed)
        picked_ids.add(body["template_id"])

    assert "linearity_branching_fixture" in picked_ids


# A targeted regression guard for code-quality.md §4: prove the dial is
# threaded all the way from settings → _do_propose → generate. Confirm
# the same seed produces a NON-branching activity under "linear" and a
# branching one under "nonlinear" — i.e. the dial actually changed the
# selection at a fixed seed.
def test_dial_changes_selection_at_fixed_seed(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    templates_mixed: Path,
) -> None:
    """Find a seed where nonlinear picks the branching template, then show
    that flipping the dial to linear forces the linear template at that
    same seed."""
    # Locate a seed that lands on the branching template under nonlinear.
    _set_linearity(db_path, "nonlinear")
    branching_seed: int | None = None
    for seed in range(30):
        body = _propose(client, parent_headers, seed=seed)
        if body["template_id"] == "linearity_branching_fixture":
            branching_seed = seed
            break
    assert branching_seed is not None, "no seed selected the branching template"

    # Same seed, dial flipped to linear -> must now land on the linear one.
    _set_linearity(db_path, "linear")
    body = _propose(client, parent_headers, seed=branching_seed)
    assert body["template_id"] == "linearity_linear_fixture"
    assert not _has_branching_step(body)


def test_db_helper_signature_is_two_args(db_path: Path) -> None:
    """Trivial guard that the settings row write helper used by the other
    tests writes a readable row (keeps a sqlite connection import live)."""
    _set_linearity(db_path, "linear")
    conn = connect(db_path)
    try:
        row: sqlite3.Row | None = conn.execute(
            "SELECT value FROM settings WHERE key = 'game_linearity'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["value"] == "linear"
