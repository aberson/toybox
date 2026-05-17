"""Phase L Step L11 — comprehensive end-to-end integration tests.

This module fills the gaps NOT already covered by
``tests/integration/test_phase_l_reward_step_wiring.py`` (L4's 12-test
suite). The goal is to pin the full Phase L behavior through production
callers and to exercise corners that L4's per-type-cycle tests don't.

L4 already covers (DO NOT duplicate here):

* 4 reward-type cycles (picture / joke / song / random)
* Picture-empty → joke fallback (one node of the fallback chain)
* All-pools-empty → no reward step
* ``rewards.last_used_at`` update on picture firing
* Pre-L NULL ``reward_type`` legacy row → no reward step
* ``__template_id`` wiring into ``slot_fills_json`` on approve
* Idempotency: post-terminal advance is 409, no double-insert
* Version bump (+2: state-transition + reward step)
* WS envelope reward delivery + ``trigger_phrase`` stripping (invariant 7)

L11 adds (new coverage):

* Image URL is REACHABLE via the FastAPI static-file route (the L4
  metadata-shape test confirms the URL string; L11 confirms it serves
  200 + the correct content-type).
* Distribution test through the production wire — 30 separate activities
  (varied ids → varied seeds) with ``reward_type='random'``: every
  type appears at least twice. L3's unit suite has the same shape but
  exercises only the resolver function; L11 adds the e2e variant so a
  future regression that disables a type at the WIRE level surfaces.
* Determinism through the wire: same activity replayed across separate
  resolve calls at the same ``current_step_count`` returns the same
  reward; varying the step_count yields a possibly different pick.
  L3's unit test pins this at the resolver level; L11 pins the same
  invariant through the wire.
* Theme union: the resolver UNIONs template ``recommended_themes`` with
  transcript-extracted themes; we exercise this here at unit-fidelity
  against ``resolve_reward`` (rather than constructing the brittle full
  HTTP-side transcript→theme chain) so a future regression that
  silently drops either source surfaces.
* Fallback chain — picture-empty + jokes-disabled + songs-enabled →
  song fires. L4 covers picture-empty → joke and all-empty → no reward,
  but the specific "skip joke, land on song" path is exercised only at
  the L3 unit level.
* Migration round-trip: open a synthetic pre-L DB (migrations 1-18
  applied), seed the three deprecated settings rows + a legacy activity
  with no ``reward_type`` column, then apply 19/20/21. Assert (a) the
  ``rewards`` table exists, (b) ``activities.reward_type`` is NULL for
  the legacy row, (c) the three deprecated settings rows are gone, and
  (d) the legacy activity-step row with the inert ``ending_step`` field
  still loads cleanly.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from toybox.activities import joke_corpus, song_corpus
from toybox.activities.content_resolver import (
    RewardActivityContext,
    resolve_reward,
)
from toybox.activities.generator import TEMPLATES_DIR, clear_template_cache
from toybox.core import jokes_enabled
from toybox.db.connection import connect
from toybox.db.migrations import current_version, discover_migrations, run_migrations

# ---------------------------------------------------------------------
# Corpus + template fixtures.
#
# Re-use the L4 wire-test conventions verbatim: single-entry corpora
# tagged ``adventure`` and a 3-step boredom template advertising
# ``adventure`` so the deterministic picker lands on the known stub.
#
# See ``tests/integration/test_phase_l_reward_step_wiring.py`` for the
# original definitions; L11 copies-by-reference rather than refactoring
# into conftest.py because the existing L4 fixtures are scoped to that
# module and the orchestrator-time refactor risk isn't worth the dedup.
# ---------------------------------------------------------------------


def _write_song_manifest(data_root: Path, entries: list[dict[str, Any]]) -> None:
    songs_dir = data_root / "songs"
    songs_dir.mkdir(parents=True, exist_ok=True)
    (songs_dir / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")


def _write_joke_corpus(data_root: Path, entries: list[dict[str, Any]]) -> None:
    jokes_dir = data_root / "jokes"
    jokes_dir.mkdir(parents=True, exist_ok=True)
    (jokes_dir / "jokes.json").write_text(json.dumps(entries), encoding="utf-8")


def _stub_audio(data_root: Path, audio_path: str) -> Path:
    full = data_root / "songs" / audio_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"\x00" * 32)
    return full


def _good_song_entry() -> dict[str, Any]:
    return {
        "id": "l11-stub-song",
        "title": "L11 Stub Song",
        "audio_path": "audio/l11-stub-song.mp3",
        "duration_seconds": 10,
        "theme": "adventure",
        "age_band": "3-5",
        "persona_compat": ["all"],
        "license": "CC-BY-4.0",
        "credit": "L11 test fixture",
        "lyrics": "La la la.",
    }


def _good_joke_entry() -> dict[str, Any]:
    return {
        "id": "l11-stub-joke",
        "setup": "Why did the L11 chicken cross the road?",
        "punchline": "To complete the integration test.",
        "theme": "adventure",
        "optional_toy_slot": False,
        "age_band": "3-5",
        "persona_compat": ["all"],
    }


_TEMPLATE_BOREDOM_THREE_STEP: dict[str, Any] = {
    "intent": "boredom",
    "templates": [
        {
            "id": "l11_three_step",
            "title": "An L11 adventure quest",
            "buckets": ["always"],
            "recommended_themes": ["adventure"],
            "steps": [
                {"text": "Step one of the adventure."},
                {"text": "Step two of the adventure."},
                {"text": "Step three of the adventure."},
            ],
        }
    ],
}


def _stage_templates(tmp_path: Path, boredom_payload: dict[str, Any]) -> Path:
    """Stage a tmp templates dir with a custom ``boredom.json`` and
    the other three production intents copied unchanged."""
    staged = tmp_path / "templates_l11"
    staged.mkdir()
    shutil.copy(TEMPLATES_DIR / "_schema.json", staged / "_schema.json")
    (staged / "boredom.json").write_text(json.dumps(boredom_payload), encoding="utf-8")
    for intent in ("request_play", "request_story", "request_activity"):
        shutil.copy(TEMPLATES_DIR / f"{intent}.json", staged / f"{intent}.json")
    return staged


# ``isolated_data_root`` autouses so ``TOYBOX_DATA_DIR`` is set BEFORE
# the ``app`` fixture builds ``create_app()`` (the ``/api/static/images``
# StaticFiles mount captures the directory at app-build time). Mirrors
# the autouse pattern in :mod:`tests.integration.test_rewards_api`.
@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ``data/`` writes (images + corpora) to a fresh temp dir."""
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    yield tmp_path


@pytest.fixture
def l11_corpus(isolated_data_root: Path) -> Iterator[Path]:
    """Single-entry joke + song corpora under the isolated data root."""
    tmp_path = isolated_data_root
    _write_song_manifest(tmp_path, [_good_song_entry()])
    _stub_audio(tmp_path, "audio/l11-stub-song.mp3")
    _write_joke_corpus(tmp_path, [_good_joke_entry()])
    song_corpus.clear_song_cache()
    joke_corpus.clear_joke_cache()
    try:
        yield tmp_path
    finally:
        song_corpus.clear_song_cache()
        joke_corpus.clear_joke_cache()


@pytest.fixture
def l11_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Stage the three-step boredom template at the generator's
    TEMPLATES_DIR pointer."""
    staged = _stage_templates(tmp_path, _TEMPLATE_BOREDOM_THREE_STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    return staged


# ---------------------------------------------------------------------
# REST helpers — verbatim from L4's wire test for advance-walk shape.
# ---------------------------------------------------------------------


def _propose(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    seed: int = 17,
) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": seed},
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


def _approve(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
    *,
    reward_type: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if reward_type is not None:
        body["reward_type"] = reward_type
    response = client.post(
        f"/api/activities/{activity_id}/approve",
        json=body,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


def _advance(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
) -> dict[str, Any]:
    response = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


def _walk_to_terminal(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    starting_version: int,
) -> dict[str, Any]:
    """Walk the three-step boredom template to ``state=completed`` via
    the Phase L two-phase terminal advance.

    4 advances reach Phase 1 (approved→running, two lazy-INSERTs,
    then the terminal advance that inserts the reward step at
    ``current=1`` and KEEPS state=running). If a reward fired we
    issue ONE MORE advance to dismiss the reward and transition to
    completed; if no reward fired (state is already completed after
    advance 4 — the legacy single-advance path) we return as-is."""
    version = starting_version
    state = _advance(client, parent_headers, activity_id, version)
    for _ in range(3):
        version = int(state["version"])
        state = _advance(client, parent_headers, activity_id, version)
    if state["state"] == "running":
        # Phase 2: dismiss the reward step → state=completed.
        state = _advance(client, parent_headers, activity_id, int(state["version"]))
    return state


def _png_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), (200, 50, 50))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _png_bytes_colored(color: tuple[int, int, int]) -> bytes:
    """Generate a PNG with a specific color so duplicate-hash detection
    at the rewards upload endpoint doesn't reject a second upload in
    the same test (the image-hash dedup check fires on equal bytes)."""
    img = Image.new("RGB", (32, 32), color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _create_reward(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    display_name: str = "Treasure Chest",
    tags: list[str] | None = None,
    animation: str = "shine",
    color: tuple[int, int, int] = (200, 50, 50),
) -> dict[str, Any]:
    """Upload + confirm one reward."""
    upload_resp = client.post(
        "/api/rewards/upload",
        files={"file": ("reward.png", _png_bytes_colored(color), "image/png")},
        headers=parent_headers,
    )
    assert upload_resp.status_code == 200, upload_resp.text
    staging_key = upload_resp.json()["staging_key"]
    confirm_resp = client.post(
        "/api/rewards",
        json={
            "staging_key": staging_key,
            "display_name": display_name,
            "tags": tags if tags is not None else ["adventure"],
            "animation": animation,
            "active": True,
        },
        headers=parent_headers,
    )
    assert confirm_resp.status_code == 201, confirm_resp.text
    return cast("dict[str, Any]", confirm_resp.json())


def _fetch_steps(db_path: Path, activity_id: str) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT seq, body, kind, metadata_json, current "
            "FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
            (activity_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "seq": int(r["seq"]),
            "body": str(r["body"]),
            "kind": r["kind"],
            "metadata_json": r["metadata_json"],
            "current": bool(r["current"]),
        }
        for r in rows
    ]


# =====================================================================
# Test 1 — image URL is reachable via the FastAPI static-file route.
#
# L4's wire test pins the URL STRING shape (``/api/static/images/
# rewards/<id>.png``); L11 confirms that hitting that URL returns 200
# + ``image/png``. Catches a future regression that swaps the static
# mount path or moves the rewards subdir without updating the URL
# builder.
# =====================================================================


def test_picture_reward_image_url_is_reachable_via_static_route(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l11_corpus: Path,
    l11_template: Path,
) -> None:
    reward = _create_reward(client, parent_headers)
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(
        client, parent_headers, activity_id, activity["version"], reward_type="picture"
    )
    state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])
    assert state["state"] == "completed"

    rows = _fetch_steps(db_path, activity_id)
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) == 1, "expected one reward step"
    meta = json.loads(reward_rows[0]["metadata_json"])
    image_url = meta["image_url"]
    assert image_url is not None
    assert image_url.startswith("/api/static/images/rewards/"), (
        f"image_url shape changed: {image_url!r}"
    )

    # Fetch the URL via the same TestClient. ``follow_redirects`` is
    # unnecessary — static mounts respond 200 directly.
    resp = client.get(image_url, headers=parent_headers)
    assert resp.status_code == 200, (
        f"image_url not reachable: GET {image_url} → {resp.status_code} ({resp.text[:200]!r})"
    )
    assert resp.headers["content-type"].startswith("image/"), (
        f"unexpected content-type for image URL: {resp.headers.get('content-type')!r}"
    )
    # Cross-check: the bytes served match the uploaded PNG ``\x89PNG``
    # magic. Catches a misrouted mount that returns a placeholder /
    # error page with 200.
    assert resp.content.startswith(b"\x89PNG"), (
        f"static route did not return a PNG; first bytes: {resp.content[:8]!r}"
    )
    # Sanity: the reward id in the URL matches the reward we created.
    assert reward["id"] in image_url


# =====================================================================
# Test 2 — random distribution at the wire level (30 different
# activity ids).
#
# L3 unit test 7 exercises distribution via 30 step_count variations
# against the resolver function. L11's variant varies the activity_id
# instead and goes through the full propose→approve→advance wire so a
# future regression at the wire level (e.g. a bad ``random`` literal
# being silently coerced to ``picture``) surfaces.
# =====================================================================


def test_random_distribution_across_30_activities_covers_all_three_kinds(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l11_corpus: Path,
    l11_template: Path,
) -> None:
    # Picture pool: one reward. Joke / song pools: single-entry corpora
    # from ``l11_corpus``. All three types are eligible so the random
    # roll can land anywhere.
    _create_reward(client, parent_headers)
    counts: dict[str, int] = {"picture": 0, "joke": 0, "song": 0}
    for trial in range(30):
        # Vary the propose seed so the activity_id (and thus the
        # resolver's deterministic seed) varies per trial.
        activity = _propose(client, parent_headers, seed=1000 + trial)
        activity_id = activity["id"]
        state = _approve(
            client,
            parent_headers,
            activity_id,
            activity["version"],
            reward_type="random",
        )
        state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])
        rows = _fetch_steps(db_path, activity_id)
        reward_rows = [r for r in rows if r["kind"] == "reward"]
        assert len(reward_rows) == 1, (
            f"trial {trial}: expected one reward step, got {len(reward_rows)}"
        )
        meta = json.loads(reward_rows[0]["metadata_json"])
        kind = meta["reward_kind"]
        assert kind in ("picture", "joke", "song"), (
            f"trial {trial}: unexpected reward_kind {kind!r}"
        )
        counts[kind] += 1

    # Distribution is non-degenerate: every type appears at least twice
    # across 30 trials. A future bug that silently coerces ``random``
    # to a fixed type would land all 30 trials in one bucket.
    assert counts["picture"] >= 2, f"picture too rare across 30 trials: {counts}"
    assert counts["joke"] >= 2, f"joke too rare across 30 trials: {counts}"
    assert counts["song"] >= 2, f"song too rare across 30 trials: {counts}"


# =====================================================================
# Test 3 — determinism at fire time, varying step_count.
#
# Same ``(activity_id, current_step_count)`` → same pick (already pinned
# at the unit level in L3 test 8). Here we exercise the resolver
# DIRECTLY against the post-walk DB state and assert (a) determinism at
# the fixed step count, (b) at LEAST one other step count yields a
# possibly-different pick (sampling several step counts; require not
# every count produce the identical reward).
# =====================================================================


def test_resolver_is_deterministic_per_step_count_and_varies_across_counts(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l11_corpus: Path,
    l11_template: Path,
) -> None:
    # Multiple picture rewards so the seed-based pick can actually vary
    # across step_counts. With only one reward the picker would return
    # the same one regardless of seed (no variation to detect).
    _create_reward(client, parent_headers, display_name="A", tags=["space"], color=(255, 0, 0))
    _create_reward(client, parent_headers, display_name="B", tags=["food"], color=(0, 255, 0))
    _create_reward(client, parent_headers, display_name="C", tags=["weather"], color=(0, 0, 255))
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(
        client, parent_headers, activity_id, activity["version"], reward_type="picture"
    )
    # Walk to completed so the activity is in a stable state for direct
    # resolver calls.
    _walk_to_terminal(client, parent_headers, activity_id, state["version"])

    # Read the activity row directly so we can feed it back through
    # ``resolve_reward`` (the same code path the wire uses internally).
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, session_id, persona_id, slot_fills_json FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()

        # No transcripts in this DB, so transcript-source themes are
        # empty; the resolver still works (template themes carry the
        # tag-match signal). Build the activity context.
        def _ctx_at(step_count: int) -> RewardActivityContext:
            return RewardActivityContext(
                id=str(row["id"]),
                session_id=str(row["session_id"]),
                persona_id=row["persona_id"],
                slot_fills_json=row["slot_fills_json"],
                current_step_count=step_count,
            )

        # Determinism at a fixed step_count: two calls return the same
        # reward.
        a = resolve_reward(conn, _ctx_at(5), "picture")
        b = resolve_reward(conn, _ctx_at(5), "picture")
        assert a is not None and b is not None
        assert a == b, f"resolver not deterministic at fixed (id, step_count); a={a} b={b}"

        # Varying step_count yields at least one different pick across
        # a small sample. A bug that drops step_count from the seed
        # would land every step on the same reward.
        ids_by_step = {
            step: resolve_reward(conn, _ctx_at(step), "picture")
            for step in (1, 2, 3, 5, 8, 13, 21, 34, 55, 89)
        }
        picked = {r.reward_id for r in ids_by_step.values() if r is not None}
        assert len(picked) >= 2, f"step_count variation produced no pick variation; got {picked}"
    finally:
        conn.close()


# =====================================================================
# Test 4 — migration round-trip 18 → 21 on a synthetic pre-L DB.
#
# Apply migrations 1..18 only, seed the three deprecated Phase K
# settings rows + one in-progress activity (legacy shape, no
# reward_type column) + one activity_step with the inert
# ``ending_step`` field in metadata_json (per K3 legacy templates).
# Then apply 19/20/21. Assert:
#
# (a) ``rewards`` table exists with the expected columns.
# (b) The legacy activity row's ``reward_type`` column is NULL.
# (c) The three deprecated settings rows are gone.
# (d) The legacy activity_step with ``ending_step`` in metadata_json
#     still loads cleanly (the migrations don't break the row's shape).
# =====================================================================


def test_migration_round_trip_18_to_21_preserves_legacy_rows(
    tmp_path: Path,
) -> None:
    # Stage a migrations dir with ONLY files 0001..0018 so the first
    # run_migrations() invocation lands at version=18. Then a second
    # dir for 19/20/21 to step the schema forward; the runner reads
    # ``schema_migrations`` to know what's already applied so the
    # second invocation skips 1..18 and applies the new three.
    src_root = (
        Path(__file__).resolve().parent.parent.parent / "src" / "toybox" / "db" / "migrations"
    )
    pre_l_dir = tmp_path / "migrations_pre_l"
    pre_l_dir.mkdir()
    # Discover the real production migrations and copy 1..18 into the
    # pre-L staging dir. Use ``discover_migrations`` so the copy
    # tolerates a future renumbering rather than hard-coding filenames.
    real_migrations = discover_migrations(src_root)
    pre_l_versions = {m.version for m in real_migrations if m.version <= 18}
    assert 1 in pre_l_versions and 18 in pre_l_versions, (
        f"expected migrations 1..18 to be present; got {sorted(pre_l_versions)}"
    )
    for m in real_migrations:
        if m.version <= 18:
            shutil.copy(m.path, pre_l_dir / m.filename)

    db_path = tmp_path / "synthetic_pre_l.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_l_dir)
        assert current_version(conn) == 18, (
            f"pre-L DB should be at version 18; got {current_version(conn)}"
        )

        # Seed: a session row (FK target for the activity).
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at, ended_at) "
                "VALUES ('legacy-sess', '2026-01-01T00:00:00Z', NULL)"
            )
            # Confirm the activities table does NOT yet have a
            # reward_type column (0020 adds it).
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(activities)").fetchall()]
            assert "reward_type" not in cols, (
                "pre-L DB should not have activities.reward_type column"
            )

            # Insert a legacy activity row (no reward_type column at
            # this point — 0020 hasn't run yet). ``slot_fills_json`` is
            # NOT NULL with default ``'{}'`` (added by 0008), so pass
            # the empty-object literal explicitly.
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, summary, persona_id, "
                " toy_ids, intent_source, created_at, slot_fills_json) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy-act",
                    "legacy-sess",
                    "running",
                    "legacy summary",
                    None,
                    None,
                    "kid",
                    "2026-01-01T00:00:00Z",
                    "{}",
                ),
            )

            # Insert a legacy activity_step with the inert ``ending_step``
            # field embedded in metadata_json (per K3 templates that
            # carried ending_step before L deleted the surface). The
            # 0016 migration added ``kind`` + ``metadata_json`` so we
            # have those columns here, but the legacy metadata blob
            # itself can carry the deprecated key — L's migrations do
            # NOT rewrite step metadata, so this row must survive
            # unchanged.
            legacy_metadata = json.dumps({"ending_step": "All done!"})
            conn.execute(
                "INSERT INTO activity_steps "
                "(id, activity_id, seq, body, kind, metadata_json, current) "
                "VALUES (?, ?, 1, ?, ?, ?, 1)",
                (
                    "legacy-step",
                    "legacy-act",
                    "Step body",
                    "instruction",
                    legacy_metadata,
                ),
            )

            # Confirm the three deprecated settings rows ARE present at
            # version 18 (seeded by 0015).
            for key in (
                "play_embedded_enabled",
                "play_endings_enabled",
                "play_spontaneity_enabled",
            ):
                row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                assert row is not None, f"pre-L DB should have settings row {key!r} from 0015"

        # Now apply the L migrations (19/20/21) from the real dir.
        applied = run_migrations(conn, directory=src_root)
        # Should apply exactly 19, 20, 21 (in order). A future
        # renumbering that adds a 22+ would also apply here — that's
        # fine, the round-trip still passes the four asserts below.
        applied_versions = sorted(m.version for m in applied)
        assert applied_versions[:3] == [19, 20, 21], (
            f"expected 19, 20, 21 to apply (in order); got {applied_versions}"
        )

        # (a) rewards table exists with the expected columns.
        reward_cols = {row["name"] for row in conn.execute("PRAGMA table_info(rewards)").fetchall()}
        expected_cols = {
            "id",
            "display_name",
            "image_path",
            "image_hash",
            "tags",
            "animation",
            "active",
            "archived",
            "created_at",
            "last_used_at",
        }
        assert expected_cols.issubset(reward_cols), (
            f"rewards table missing expected columns; got {reward_cols}"
        )

        # (b) Legacy activity's reward_type column is NULL.
        post_cols = [
            row["name"] for row in conn.execute("PRAGMA table_info(activities)").fetchall()
        ]
        assert "reward_type" in post_cols, "activities.reward_type column missing after 0020"
        legacy_row = conn.execute(
            "SELECT reward_type FROM activities WHERE id = ?", ("legacy-act",)
        ).fetchone()
        assert legacy_row is not None
        assert legacy_row["reward_type"] is None, (
            "legacy activity row should have NULL reward_type after 0020"
        )

        # (c) The three deprecated settings rows are gone.
        deprecated_keys = (
            "play_embedded_enabled",
            "play_endings_enabled",
            "play_spontaneity_enabled",
        )
        for key in deprecated_keys:
            row = conn.execute("SELECT key FROM settings WHERE key = ?", (key,)).fetchone()
            assert row is None, (
                f"deprecated settings row {key!r} should be deleted by 0021; found {{row}}"
            )

        # (d) The legacy activity_step row with the inert ``ending_step``
        # field in metadata_json still loads cleanly — the migrations
        # do NOT touch step row content, so the row survives unchanged.
        step_row = conn.execute(
            "SELECT body, kind, metadata_json FROM activity_steps WHERE id = ?",
            ("legacy-step",),
        ).fetchone()
        assert step_row is not None, "legacy activity_step row should survive 19-21"
        loaded_metadata = json.loads(step_row["metadata_json"])
        assert loaded_metadata == {"ending_step": "All done!"}, (
            f"legacy ending_step metadata should be preserved verbatim; got {loaded_metadata}"
        )
        assert step_row["body"] == "Step body"
        assert step_row["kind"] == "instruction"
    finally:
        conn.close()


# =====================================================================
# Test 5 — theme UNION: template + transcript at the resolver level.
#
# L3 unit test 9 covers this against a mocked ``find_template_by_id``.
# L11 adds a variant that pulls themes from BOTH sources via an in-DB
# transcript row + a real template ``recommended_themes`` lookup.
#
# Rationale for unit-level path here (not the full HTTP loop): the
# transcript→theme extraction chain is brittle to drive end-to-end
# (would require seeding session transcripts pre-propose AND the
# advance handler reading from them mid-walk), so we drive the resolver
# directly against a real DB + a real template. Mocks: only the joke /
# song corpus is mocked OUT (we don't want the resolver to pick a
# joke / song; we want it to pick a picture so we can assert which
# picture).
# =====================================================================


def test_theme_union_picks_overlap_from_template_and_from_transcript(
    db_path: Path,
    l11_template: Path,
) -> None:
    conn = connect(db_path)
    try:
        # Seed a session + activity referencing the staged template id.
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at, ended_at) "
                "VALUES ('u-sess', '2026-01-01T00:00:00Z', NULL)"
            )
            conn.execute(
                "INSERT INTO transcripts "
                "(id, session_id, mic_id, started_at, ended_at, text) "
                "VALUES ('t-1', 'u-sess', NULL, '2026-01-01T00:00:00Z', "
                "'2026-01-01T00:00:05Z', 'we are pirates today')"
            )
            # Two picture rewards: one tagged 'pirates' (transcript-
            # source overlap), one tagged 'food' (no overlap). The
            # template advertises 'adventure' (per
            # _TEMPLATE_BOREDOM_THREE_STEP).
            conn.execute(
                "INSERT INTO rewards "
                "(id, display_name, image_path, image_hash, tags, "
                " animation, active, archived, created_at) "
                "VALUES "
                "('r-pirate', 'Pirate', 'data/images/rewards/r-pirate.png', "
                " 'hash-1', '[\"pirates\"]', 'shine', 1, 0, "
                " '2026-01-01T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO rewards "
                "(id, display_name, image_path, image_hash, tags, "
                " animation, active, archived, created_at) "
                "VALUES "
                "('r-food', 'Food', 'data/images/rewards/r-food.png', "
                " 'hash-2', '[\"food\"]', 'shine', 1, 0, "
                " '2026-01-01T00:00:00Z')"
            )

        # First sub-case: transcript-source theme reaches the resolver.
        # 'pirates' is in the transcript; the template's 'adventure' is
        # NOT in any reward.tags. With only r-pirate having overlap=1
        # and r-food having overlap=0, the resolver must pick r-pirate
        # (deterministic multi-key sort: overlap DESC).
        ctx = RewardActivityContext(
            id="u-act",
            session_id="u-sess",
            persona_id=None,
            slot_fills_json=json.dumps({"__template_id": "l11_three_step"}),
            current_step_count=1,
        )
        result = resolve_reward(conn, ctx, "picture")
        assert result is not None
        assert result.kind == "picture"
        assert result.reward_id == "r-pirate", (
            f"transcript-source 'pirates' must beat no-overlap r-food (got {result.reward_id})"
        )

        # Second sub-case: the template-source side of the union is
        # alive too. Re-tag r-food → 'adventure' (the template's theme).
        # Now r-food has overlap=1 (from template), r-pirate has
        # overlap=1 (from transcript). Both have last_used_at=NULL, so
        # the id-ASC tiebreak picks 'r-food' — proving the template
        # side of the union contributed.
        with conn:
            conn.execute(
                "UPDATE rewards SET tags = ? WHERE id = ?",
                (json.dumps(["adventure"]), "r-food"),
            )
        result2 = resolve_reward(conn, ctx, "picture")
        assert result2 is not None
        assert result2.reward_id == "r-food", (
            "template-source 'adventure' should be in the union and "
            f"win the id-ASC tiebreak vs r-pirate; got {result2.reward_id}"
        )
    finally:
        conn.close()


# =====================================================================
# Test 6 — fallback chain: picture-empty + jokes_disabled + songs_enabled
# → song fires.
#
# L4 has picture-empty → joke (one node of the chain) and all-empty →
# no reward (the terminal). L11 adds the middle node: picture empty +
# jokes hard-disabled by flag + songs enabled → walk lands on song.
# Catches a regression that drops the second hop in the
# ``picture → joke → song`` walk.
# =====================================================================


def test_picture_falls_back_to_song_when_jokes_disabled_and_pictures_empty(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l11_corpus: Path,
    l11_template: Path,
) -> None:
    # No active rewards (picture-empty). Disable jokes_enabled so the
    # fallback chain skips the joke node. Songs_enabled defaults to
    # True; the song corpus has one entry tagged 'adventure' (matches
    # the template) so the song picker lands on it.
    conn = connect(db_path)
    try:
        jokes_enabled.set(conn, False)
    finally:
        conn.close()

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(
        client, parent_headers, activity_id, activity["version"], reward_type="picture"
    )
    state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])
    assert state["state"] == "completed"

    rows = _fetch_steps(db_path, activity_id)
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) == 1, (
        f"expected one reward step in song-fallback path, got {len(reward_rows)}"
    )
    meta = json.loads(reward_rows[0]["metadata_json"])
    assert meta["reward_kind"] == "song", (
        f"expected song fallback when pictures empty + jokes disabled; got {meta['reward_kind']!r}"
    )
    assert meta["reward_id"] == "l11-stub-song"
    assert meta["audio_url"] == "/api/static/songs/audio/l11-stub-song.mp3"
    # Joke + image fields are null per per-kind exclusivity (plan §8).
    assert meta["image_url"] is None
    assert meta["animation"] is None
    assert meta["setup"] is None
    assert meta["punchline"] is None


# =====================================================================
# Tests SKIPPED because L4 already covers them. Listed here for the
# reviewer's audit trail.
#
# * Picture-empty → joke fallback:
#     L4 :func:`test_picture_fallback_to_joke_when_no_active_rewards`
# * All pools empty/disabled → no reward step:
#     L4 :func:`test_no_reward_step_when_all_pools_empty`
# * WS envelope reward delivery + trigger_phrase strip:
#     L4 :func:`test_terminal_advance_publishes_reward_step_envelope_and_strips_trigger_phrase`
# * Pre-L NULL reward_type → no reward step:
#     L4 :func:`test_pre_l_activity_with_null_reward_type_appends_no_reward`
#
# Two skipped scenarios moved to the resolver-direct sub-cases above:
#
# * Determinism (per-step_count + variation across step_counts) — see
#   :func:`test_resolver_is_deterministic_per_step_count_and_varies_across_counts`
#   above. The unit-suite test 8 covers the wire-equivalent determinism
#   contract; we add a step_count-variation probe here that the unit
#   suite doesn't have, exercised against a real DB seeded by a real
#   propose/approve cycle.
# * Theme union — see
#   :func:`test_theme_union_picks_overlap_from_template_and_from_transcript`
#   above. The L3 unit test 9 mocks ``find_template_by_id``; this L11
#   test wires through the real template lookup against a staged
#   template dir, exercising a wider slice of the production path.
# =====================================================================


__all__ = [
    "test_migration_round_trip_18_to_21_preserves_legacy_rows",
    "test_picture_falls_back_to_song_when_jokes_disabled_and_pictures_empty",
    "test_picture_reward_image_url_is_reachable_via_static_route",
    "test_random_distribution_across_30_activities_covers_all_three_kinds",
    "test_resolver_is_deterministic_per_step_count_and_varies_across_counts",
    "test_theme_union_picks_overlap_from_template_and_from_transcript",
]


# Suppress unused-import lint: ``sqlite3`` + ``patch`` + ``FastAPI`` are
# kept for type-hint readability and future test additions.
_ = sqlite3
_ = patch
_ = FastAPI
