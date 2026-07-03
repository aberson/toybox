"""Coverage for the Phase A Step 3 persona library + loader."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.personas.loader import (
    AVATAR_SUBDIR,
    LIBRARY_DIR,
    SCHEMA_FILENAME,
    load_library_personas,
)

EXPECTED_PERSONA_IDS: tuple[str, ...] = ("detective", "periodic_table", "princess", "wizard")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


def _load_shipped_personas() -> list[dict[str, object]]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(LIBRARY_DIR.glob("*.json"))
        if not p.name.startswith("_")
    ]


def _mirror_shipped_library(dest: Path) -> None:
    """Copy shipped library JSONs + schema + avatars into ``dest``."""
    (dest / "avatars").mkdir(parents=True, exist_ok=True)
    shutil.copy2(LIBRARY_DIR / SCHEMA_FILENAME, dest / SCHEMA_FILENAME)
    for persona_path in LIBRARY_DIR.glob("*.json"):
        if persona_path.name.startswith("_"):
            continue
        shutil.copy2(persona_path, dest / persona_path.name)
    for avatar_path in (LIBRARY_DIR / "avatars").glob("*.png"):
        shutil.copy2(avatar_path, dest / "avatars" / avatar_path.name)


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_inserts_all_four_personas(db: sqlite3.Connection, tmp_path: Path) -> None:
    count = load_library_personas(db, tmp_path)
    assert count == 4

    rows = list(db.execute("SELECT id, source FROM personas ORDER BY id").fetchall())
    assert [r["id"] for r in rows] == list(EXPECTED_PERSONA_IDS)
    assert all(r["source"] == "library" for r in rows)


def test_load_is_idempotent(db: sqlite3.Connection, tmp_path: Path) -> None:
    first = load_library_personas(db, tmp_path)
    avatar_dir = tmp_path / AVATAR_SUBDIR
    avatar_paths = sorted(avatar_dir.glob("*.png"))
    hashes_before = {p.name: _sha256_path(p) for p in avatar_paths}

    # Patch shutil.copy2 inside the loader module so a second-pass copy
    # would be detectable. The sha256-equality short-circuit in
    # _copy_avatar_if_changed should mean copy2 is never called.
    with patch("toybox.personas.loader.shutil.copy2") as mock_copy:
        second = load_library_personas(db, tmp_path)

    assert first == 4
    assert second == 4
    total = db.execute("SELECT COUNT(*) FROM personas").fetchone()[0]
    assert total == 4
    assert mock_copy.call_count == 0, "library avatars should not be re-copied on the second load"

    hashes_after = {p.name: _sha256_path(p) for p in avatar_dir.glob("*.png")}
    assert hashes_before == hashes_after


def test_invalid_json_skipped_not_fatal(
    db: sqlite3.Connection,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_library = tmp_path / "library"
    _mirror_shipped_library(fake_library)

    # Case 1: schema-invalid (missing required fields).
    bad_payload = {
        "id": "broken",
        "display_name": "Broken Persona",
        "system_prompt": "missing required fields on purpose",
    }
    (fake_library / "broken.json").write_text(json.dumps(bad_payload), encoding="utf-8")

    # Case 2: malformed JSON (JSONDecodeError).
    (fake_library / "garbage.json").write_text("not json{", encoding="utf-8")

    data_dir = tmp_path / "data"
    with caplog.at_level(logging.WARNING, logger="toybox.personas.loader"):
        count = load_library_personas(db, data_dir, library_dir=fake_library)

    assert count == 4
    rows = [r["id"] for r in db.execute("SELECT id FROM personas ORDER BY id")]
    assert rows == list(EXPECTED_PERSONA_IDS)

    # Pin both the level and the logger name; pin both filenames in messages.
    relevant = [
        r for r in caplog.records if r.name == "toybox.personas.loader" and r.levelname == "WARNING"
    ]
    broken_msgs = [r.getMessage() for r in relevant if "broken.json" in r.getMessage()]
    garbage_msgs = [r.getMessage() for r in relevant if "garbage.json" in r.getMessage()]
    assert broken_msgs, "expected a WARNING from toybox.personas.loader mentioning broken.json"
    assert garbage_msgs, "expected a WARNING from toybox.personas.loader mentioning garbage.json"


def test_avatars_copied_to_data_dir(db: sqlite3.Connection, tmp_path: Path) -> None:
    load_library_personas(db, tmp_path)

    avatars_dir = tmp_path / AVATAR_SUBDIR
    for persona_id in EXPECTED_PERSONA_IDS:
        copied = avatars_dir / f"{persona_id}.png"
        source = LIBRARY_DIR / "avatars" / f"{persona_id}.png"
        assert copied.is_file(), f"missing copied avatar: {copied}"
        # SHA-256 byte-equality is the right check; size-equality is too
        # weak for solid-color PNGs of identical dimensions.
        assert _sha256_path(copied) == _sha256_path(source), (
            f"copied avatar bytes differ from source for persona {persona_id}"
        )


def test_persona_jsons_match_schema() -> None:
    schema = json.loads((LIBRARY_DIR / SCHEMA_FILENAME).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for persona in _load_shipped_personas():
        validator.validate(persona)
        # Phase Z Z3: every shipped persona carries a Kokoro casting
        # default. The values are pinned (loader round-trip) in
        # test_persona_library_phase_k_attrs.py; here we pin presence so
        # schema validation above provably covers the new key on all 4.
        voice_profile = persona["voice_profile"]
        assert isinstance(voice_profile, dict), persona["id"]
        assert isinstance(voice_profile.get("neural_voice"), str), persona["id"]


def test_voice_profile_schema_still_rejects_unknown_keys() -> None:
    """``voice_profile`` keeps ``additionalProperties: false`` after the
    Z3 ``neural_voice`` addition — a typo'd key (e.g. ``nueral_voice``)
    must fail validation, not silently ship."""
    from jsonschema.exceptions import ValidationError

    schema = json.loads((LIBRARY_DIR / SCHEMA_FILENAME).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    persona = json.loads((LIBRARY_DIR / "wizard.json").read_text(encoding="utf-8"))
    persona["voice_profile"]["nueral_voice"] = "am_michael"
    with pytest.raises(ValidationError):
        validator.validate(persona)


def test_library_personas_have_null_avatar_hash(db: sqlite3.Connection, tmp_path: Path) -> None:
    """Per plan §personas table: library personas keep avatar_image_hash NULL.

    They are intentionally excluded from the partial UNIQUE dedup index. This
    pins the contract so a future "fix" cannot start populating the column.
    """
    load_library_personas(db, tmp_path)
    rows = list(
        db.execute("SELECT id, avatar_image_hash FROM personas WHERE source = 'library'").fetchall()
    )
    assert len(rows) == 4
    for row in rows:
        assert row["avatar_image_hash"] is None, (
            f"library persona {row['id']} has non-null avatar_image_hash"
        )


def test_underscore_prefixed_json_not_loaded(db: sqlite3.Connection, tmp_path: Path) -> None:
    """Files like ``_schema.json`` (and any ``_decoy.json``) are skipped."""
    fake_library = tmp_path / "library"
    _mirror_shipped_library(fake_library)

    decoy_payload = {
        "id": "decoy",
        "display_name": "Decoy Persona",
        "archetype": "custom",
        "system_prompt": "I should not be loaded.",
        "avatar_image_path": "library/avatars/wizard.png",
        "behavior_tags": ["ignored"],
        "age_range_min": 3,
        "age_range_max": 12,
        "language": "en",
        "source": "library",
    }
    (fake_library / "_decoy.json").write_text(json.dumps(decoy_payload), encoding="utf-8")

    data_dir = tmp_path / "data"
    count = load_library_personas(db, data_dir, library_dir=fake_library)

    assert count == 4
    decoy_row = db.execute("SELECT id FROM personas WHERE id = 'decoy'").fetchone()
    assert decoy_row is None


def test_missing_avatar_file_is_non_fatal(
    db: sqlite3.Connection,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If a persona's avatar file is missing on disk, load the row anyway.

    Pinned behavior: persona row IS inserted, a WARNING is logged, no avatar
    bytes are copied, and other personas continue to load. Re-running the
    loader once the file appears will populate the avatar.
    """
    fake_library = tmp_path / "library"
    _mirror_shipped_library(fake_library)

    # Delete one avatar, leaving the persona JSON in place.
    (fake_library / "avatars" / "wizard.png").unlink()

    data_dir = tmp_path / "data"
    with caplog.at_level(logging.WARNING, logger="toybox.personas.loader"):
        count = load_library_personas(db, data_dir, library_dir=fake_library)

    assert count == 4
    ids = [r["id"] for r in db.execute("SELECT id FROM personas ORDER BY id")]
    assert ids == list(EXPECTED_PERSONA_IDS)

    # No avatar copied for wizard.
    assert not (data_dir / AVATAR_SUBDIR / "wizard.png").exists()

    relevant = [
        r
        for r in caplog.records
        if r.name == "toybox.personas.loader"
        and r.levelname == "WARNING"
        and "wizard.png" in r.getMessage()
    ]
    assert relevant, "expected a WARNING mentioning the missing wizard.png"


def test_upsert_overwrites_mutated_fields(db: sqlite3.Connection, tmp_path: Path) -> None:
    """Re-loading the library overwrites any drift in persona fields.

    Pins the documented "full UPSERT every load" contract.
    """
    load_library_personas(db, tmp_path)
    db.execute("UPDATE personas SET system_prompt = 'mutated' WHERE id = 'wizard'")
    db.commit()
    pre = db.execute("SELECT system_prompt FROM personas WHERE id = 'wizard'").fetchone()[
        "system_prompt"
    ]
    assert pre == "mutated"

    load_library_personas(db, tmp_path)
    post = db.execute("SELECT system_prompt FROM personas WHERE id = 'wizard'").fetchone()[
        "system_prompt"
    ]

    expected = json.loads((LIBRARY_DIR / "wizard.json").read_text(encoding="utf-8"))[
        "system_prompt"
    ]
    assert post == expected
    assert post != "mutated"


def test_avatar_image_path_traversal_is_rejected(
    db: sqlite3.Connection,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Defense-in-depth: a persona JSON whose avatar path escapes the library
    tree must be skipped. Persona row not inserted, no bytes copied.

    The shipped strict ``_schema.json`` would reject such a JSON outright via
    the regex pattern. To exercise the runtime ``is_relative_to`` check we
    swap in a permissive schema in the temp library_dir, mirroring the
    threat model where ``library_dir`` points at user-controlled paths.
    """
    fake_library = tmp_path / "library"
    fake_library.mkdir(parents=True)
    (fake_library / "avatars").mkdir()

    permissive_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": True,
        "required": [
            "id",
            "display_name",
            "archetype",
            "system_prompt",
            "avatar_image_path",
            "behavior_tags",
            "age_range_min",
            "age_range_max",
            "source",
        ],
        "properties": {
            "avatar_image_path": {"type": "string"},
        },
    }
    (fake_library / SCHEMA_FILENAME).write_text(json.dumps(permissive_schema), encoding="utf-8")

    # Place a sentinel file outside the library tree that the malicious
    # persona points at. If the defense fails, we'll see this byte content
    # show up under data_dir/images/personas/.
    sentinel = tmp_path / "escape.png"
    sentinel.write_bytes(b"SENTINEL-NOT-A-REAL-AVATAR")

    malicious_payload = {
        "id": "evil",
        "display_name": "Evil Persona",
        "archetype": "custom",
        "system_prompt": "would love to read /etc/hosts",
        "avatar_image_path": "../escape.png",
        "behavior_tags": ["malicious"],
        "age_range_min": 0,
        "age_range_max": 99,
        "language": "en",
        "source": "library",
    }
    (fake_library / "evil.json").write_text(json.dumps(malicious_payload), encoding="utf-8")

    data_dir = tmp_path / "data"
    with caplog.at_level(logging.WARNING, logger="toybox.personas.loader"):
        count = load_library_personas(db, data_dir, library_dir=fake_library)

    # No persona was loaded.
    assert count == 0
    evil_row = db.execute("SELECT id FROM personas WHERE id = 'evil'").fetchone()
    assert evil_row is None

    # No file was copied into data_dir.
    avatars_dir = data_dir / AVATAR_SUBDIR
    if avatars_dir.exists():
        assert list(avatars_dir.iterdir()) == []

    # WARNING was logged from the loader.
    relevant = [
        r
        for r in caplog.records
        if r.name == "toybox.personas.loader"
        and r.levelname == "WARNING"
        and "escape" in r.getMessage().lower()
    ]
    assert relevant, "expected a WARNING about the escaping avatar_image_path"
