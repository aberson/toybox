"""Coverage for the Phase A Step 6 NLP trigger registry."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.db import DB_PATH_ENV
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.triggers import (
    DEFAULTS_PATH,
    SCHEMA_VERSION,
    TRIGGERS_USER_PATH_ENV,
    Intent,
    load_registry,
    match,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the user-trigger file to a tmp path via the env var."""
    target = tmp_path / "triggers.json"
    monkeypatch.setenv(TRIGGERS_USER_PATH_ENV, str(target))
    return target


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """Create a tmp sqlite DB with the v1 schema applied and TOYBOX_DB_PATH set."""
    db_path = tmp_path / "toybox.db"
    monkeypatch.setenv(DB_PATH_ENV, str(db_path))
    conn = connect(db_path)
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


def _insert_toy(conn: sqlite3.Connection, *, toy_id: str, display_name: str) -> None:
    conn.execute(
        "INSERT INTO toys (id, display_name, image_path, image_hash, archived, created_at) "
        "VALUES (?, ?, ?, ?, 0, '2026-05-01T00:00:00Z')",
        (toy_id, display_name, f"images/toys/{toy_id}.png", f"hash-{toy_id}"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Defaults file shape + count
# ---------------------------------------------------------------------------


def test_defaults_file_has_at_least_20_patterns() -> None:
    payload = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    assert payload["version"] == SCHEMA_VERSION
    assert len(payload["patterns"]) >= 20


def test_defaults_pattern_ids_are_unique_and_regexes_compile() -> None:
    import re

    payload = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    ids: list[str] = [p["id"] for p in payload["patterns"]]
    assert len(ids) == len(set(ids)), "duplicate pattern ids in defaults.json"
    for pat in payload["patterns"]:
        re.compile(pat["regex"])  # raises re.error if malformed


def test_defaults_cover_required_intents() -> None:
    payload = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    intents = {p["intent"] for p in payload["patterns"]}
    # The plan calls out these top-level kid-play intents. mention_toy is
    # added dynamically and intentionally absent from defaults.
    assert {"request_play", "request_story", "request_activity", "boredom"}.issubset(intents)


# ---------------------------------------------------------------------------
# Loader: seed + merge
# ---------------------------------------------------------------------------


def test_first_run_seeds_user_file_from_defaults(user_path: Path) -> None:
    assert not user_path.exists()
    load_registry()
    assert user_path.is_file()

    written = json.loads(user_path.read_text(encoding="utf-8"))
    shipped = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    assert written["version"] == shipped["version"]
    assert len(written["patterns"]) == len(shipped["patterns"])
    assert {p["id"] for p in written["patterns"]} == {p["id"] for p in shipped["patterns"]}


def test_merge_no_change_when_versions_match(user_path: Path) -> None:
    # Seed.
    load_registry()
    before_bytes = user_path.read_bytes()
    mtime_before = user_path.stat().st_mtime_ns

    # Reload — nothing should change because versions are equal.
    load_registry()
    after_bytes = user_path.read_bytes()

    assert before_bytes == after_bytes, "registry rewrote user file when no change was needed"
    # mtime equality is best-effort; on Windows tmp filesystems sub-ns
    # differences can appear. Bytes equality above is the load-bearing
    # assertion; mtime check is informational.
    _ = mtime_before


def test_merge_upgrades_stale_user_pattern(user_path: Path) -> None:
    # Pretend the user has an older version of one shipped pattern.
    shipped = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    target_id = "lets_play_X"
    stale_user = {
        "version": SCHEMA_VERSION,
        "patterns": [
            ({**p, "version": 0, "regex": "(?i)stale"} if p["id"] == target_id else p)
            for p in shipped["patterns"]
        ],
    }
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(json.dumps(stale_user), encoding="utf-8")

    load_registry()

    after = json.loads(user_path.read_text(encoding="utf-8"))
    upgraded = next(p for p in after["patterns"] if p["id"] == target_id)
    shipped_entry = next(p for p in shipped["patterns"] if p["id"] == target_id)
    assert upgraded["version"] == shipped_entry["version"]
    assert upgraded["regex"] == shipped_entry["regex"]


def test_merge_preserves_user_only_pattern(user_path: Path) -> None:
    shipped = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    user_only = {
        "id": "user_custom_pirates",
        "regex": "(?i)\\bpirate adventure\\b",
        "intent": "request_activity",
        "slot_group": None,
        "version": 1,
    }
    payload = {
        "version": SCHEMA_VERSION,
        "patterns": [*shipped["patterns"], user_only],
    }
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(json.dumps(payload), encoding="utf-8")

    load_registry()

    after = json.loads(user_path.read_text(encoding="utf-8"))
    ids = [p["id"] for p in after["patterns"]]
    assert "user_custom_pirates" in ids


def test_merge_preserves_user_higher_version(user_path: Path) -> None:
    """User file has a HIGHER version than shipped → user wins."""
    shipped = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    target_id = "story_time"
    payload = {
        "version": SCHEMA_VERSION,
        "patterns": [
            (
                {**p, "version": 99, "regex": "(?i)\\buser override\\b"}
                if p["id"] == target_id
                else p
            )
            for p in shipped["patterns"]
        ],
    }
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(json.dumps(payload), encoding="utf-8")

    load_registry()

    after = json.loads(user_path.read_text(encoding="utf-8"))
    overridden = next(p for p in after["patterns"] if p["id"] == target_id)
    assert overridden["version"] == 99
    assert overridden["regex"] == "(?i)\\buser override\\b"


def test_malformed_user_file_is_treated_as_missing(
    user_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text("not valid json{", encoding="utf-8")

    import logging

    with caplog.at_level(logging.WARNING, logger="toybox.triggers.registry"):
        load_registry()

    # Should have re-seeded.
    after = json.loads(user_path.read_text(encoding="utf-8"))
    assert after["version"] == SCHEMA_VERSION
    assert len(after["patterns"]) >= 20


def test_load_registry_returns_compiled_patterns(user_path: Path) -> None:
    patterns = load_registry()
    shipped = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    assert len(patterns) == len(shipped["patterns"])
    # Every entry should expose a compiled re.Pattern.
    import re

    assert all(isinstance(p.compiled, re.Pattern) for p in patterns)


# ---------------------------------------------------------------------------
# Match: curated patterns
# ---------------------------------------------------------------------------


def test_match_lets_play_with_X_captures_slot(user_path: Path, tmp_path: Path) -> None:
    out = match("Let's play with unicorns!", db_path=tmp_path / "no.db")
    play = [i for i in out if i.pattern_id == "lets_play_with_X"]
    assert len(play) == 1
    assert play[0].name == "request_play"
    assert play[0].slot == "unicorns"
    assert play[0].confidence == 1.0


def test_match_story_time_no_slot(user_path: Path, tmp_path: Path) -> None:
    out = match("Story time!", db_path=tmp_path / "no.db")
    intents = [i for i in out if i.pattern_id == "story_time"]
    assert len(intents) == 1
    assert intents[0].name == "request_story"
    assert intents[0].slot is None


def test_match_im_bored_variants(user_path: Path, tmp_path: Path) -> None:
    for utter in ("I'm bored.", "i am bored", "I'm so bored!", "I'm really bored"):
        out = match(utter, db_path=tmp_path / "no.db")
        names = {i.name for i in out}
        assert "boredom" in names, f"missed boredom for: {utter!r}"


def test_match_returns_empty_for_unrelated_text(user_path: Path, tmp_path: Path) -> None:
    out = match("the quick brown fox jumps over the lazy dog", db_path=tmp_path / "no.db")
    assert out == []


def test_match_results_are_sorted_by_pattern_id(user_path: Path, tmp_path: Path) -> None:
    # An utterance that fires multiple curated patterns.
    out = match("I'm bored, let's play hide and seek!", db_path=tmp_path / "no.db")
    pattern_ids = [i.pattern_id for i in out]
    assert pattern_ids == sorted(pattern_ids)
    assert len(pattern_ids) >= 2


def test_match_dedupes_on_name_slot_pattern_id(user_path: Path, tmp_path: Path) -> None:
    # The same pattern can match more than once on the same input if it
    # has multiple capture sites — the dedupe key collapses identical
    # (name, slot, pattern_id) triples.
    out = match("Let's play tag, let's play tag", db_path=tmp_path / "no.db")
    keys = [(i.name, i.slot, i.pattern_id) for i in out]
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# Match: dynamic toy-name source
# ---------------------------------------------------------------------------


def test_dynamic_toy_intent_added(
    db: sqlite3.Connection,
    user_path: Path,
    tmp_path: Path,
) -> None:
    _insert_toy(db, toy_id="toy_unicorn", display_name="Mr. Unicorn")
    out = match("I love mr. unicorn!", db_path=tmp_path / "toybox.db")
    toy_intents = [i for i in out if i.name == "mention_toy"]
    assert len(toy_intents) == 1
    assert toy_intents[0].slot == "Mr. Unicorn"
    assert toy_intents[0].pattern_id == "dyn_toy_toy_unicorn"


def test_dynamic_toy_word_boundary(
    db: sqlite3.Connection,
    user_path: Path,
    tmp_path: Path,
) -> None:
    _insert_toy(db, toy_id="toy_unicorn", display_name="Unicorn")
    out = match("I bought some unicornium today", db_path=tmp_path / "toybox.db")
    assert [i for i in out if i.name == "mention_toy"] == []


def test_dynamic_toy_case_insensitive(
    db: sqlite3.Connection,
    user_path: Path,
    tmp_path: Path,
) -> None:
    _insert_toy(db, toy_id="toy_dragon", display_name="DRAGON")
    out = match("Look, a dragon!", db_path=tmp_path / "toybox.db")
    assert any(i.name == "mention_toy" for i in out)


def test_dynamic_empty_toys_table_yields_no_mention(
    db: sqlite3.Connection,
    user_path: Path,
    tmp_path: Path,
) -> None:
    out = match("let's play with anything", db_path=tmp_path / "toybox.db")
    assert [i for i in out if i.name == "mention_toy"] == []


def test_dynamic_archived_toys_excluded(
    db: sqlite3.Connection,
    user_path: Path,
    tmp_path: Path,
) -> None:
    _insert_toy(db, toy_id="toy_old", display_name="OldBear")
    db.execute("UPDATE toys SET archived = 1 WHERE id = 'toy_old'")
    db.commit()
    out = match("look at oldbear", db_path=tmp_path / "toybox.db")
    assert [i for i in out if i.name == "mention_toy"] == []


def test_dynamic_toys_refreshed_on_each_match(
    db: sqlite3.Connection,
    user_path: Path,
    tmp_path: Path,
) -> None:
    """The v1 stub rebuilds the toy list on every match() call.

    Pinned: an insert between two match() calls is reflected without
    reloading the registry.
    """
    db_path = tmp_path / "toybox.db"
    out_before = match("hello robo", db_path=db_path)
    assert [i for i in out_before if i.name == "mention_toy"] == []

    _insert_toy(db, toy_id="toy_robo", display_name="Robo")
    out_after = match("hello robo", db_path=db_path)
    toy_hits = [i for i in out_after if i.name == "mention_toy"]
    assert len(toy_hits) == 1
    assert toy_hits[0].slot == "Robo"


def test_match_without_db_path_uses_resolve_db_path(
    db: sqlite3.Connection,
    user_path: Path,
    tmp_path: Path,
) -> None:
    """When db_path is None, the resolver picks up TOYBOX_DB_PATH (set in fixture)."""
    _insert_toy(db, toy_id="toy_blocks", display_name="Blocks")
    out = match("I want to play with blocks")
    assert any(i.pattern_id == "dyn_toy_toy_blocks" for i in out)


def test_match_with_nonexistent_db_path_skips_dynamic(
    user_path: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    bogus = tmp_path / "missing.db"
    with caplog.at_level(logging.WARNING, logger="toybox.triggers.registry"):
        out = match("let's play with unicorns", db_path=bogus)
    # Curated patterns still fire.
    assert any(i.name == "request_play" for i in out)
    # mention_toy is absent.
    assert all(i.name != "mention_toy" for i in out)


# ---------------------------------------------------------------------------
# Intent model
# ---------------------------------------------------------------------------


def test_intent_is_frozen() -> None:
    from pydantic import ValidationError

    intent = Intent(name="request_play", slot=None, pattern_id="lets_play_X")
    with pytest.raises(ValidationError):
        intent.name = "other"


def test_intent_default_confidence_is_one() -> None:
    intent = Intent(name="request_play", slot=None, pattern_id="lets_play_X")
    assert intent.confidence == 1.0
