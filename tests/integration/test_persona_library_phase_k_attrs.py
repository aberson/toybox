"""Phase K K1 — verify the 4 built-in persona JSONs round-trip through
the loader with the documented role_weights / voice_profile /
spontaneity_rates defaults from documentation/phase-k-plan.md §5.

Acceptance #7: every built-in persona JSON loads and the loader writes
the expected defaults to the DB row. The loader path (vs. raw JSON
file-read) is the meaningful one — it exercises both the on-disk JSON
shape AND the loader's JSON-encode step.

The acceptance #8 custom-persona-with-NULL-columns hydration path is
covered by tests/integration/migrations/test_0014_*.py
::test_old_custom_personas_backfill_to_documented_defaults, which
exercises the stronger pre-existing-row + migration-applies path.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.personas.loader import load_library_personas
from toybox.personas.models import (
    DEFAULT_ROLE_WEIGHTS_JSON,
    DEFAULT_SPONTANEITY_RATES_JSON,
    parse_role_weights,
    parse_spontaneity_rates,
    parse_voice_profile,
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


# Plan §5 verbatim table — these are the acceptance numerics.
EXPECTED_PERSONA_ATTRS: dict[str, dict[str, object]] = {
    "princess": {
        "role_weights": {
            "friend": 1.5,
            "sidekick": 1.5,
            "helper_townsperson": 1.2,
            "big_bad_boss": 0.3,
        },
        "voice_profile": {"rate": 1.0, "pitch": 1.4},
        "spontaneity_rates": {"jokes": 0.05, "songs": 0.15},
    },
    "wizard": {
        "role_weights": {
            "quest_giver": 1.5,
            "guide_mentor": 1.5,
            "big_bad_boss": 1.2,
            "frenemy": 1.1,
        },
        "voice_profile": {"rate": 0.9, "pitch": 0.7},
        "spontaneity_rates": {"jokes": 0.10, "songs": 0.05},
    },
    "detective": {
        "role_weights": {
            "quest_giver": 1.3,
            "helper_townsperson": 1.2,
            "frenemy": 1.3,
            "sidekick": 1.0,
        },
        "voice_profile": {"rate": 1.1, "pitch": 0.9},
        "spontaneity_rates": {"jokes": 0.0, "songs": 0.0},
    },
    "periodic_table": {
        "role_weights": {
            "guide_mentor": 1.5,
            "helper_townsperson": 1.3,
            "friend": 1.0,
        },
        "voice_profile": {"rate": 1.2, "pitch": 1.0},
        "spontaneity_rates": {"jokes": 0.10, "songs": 0.0},
    },
}


def test_loader_persists_new_columns_for_built_in_personas(
    db: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    """After ``load_library_personas`` runs, the DB row for each built-in
    persona has the documented role_weights / voice_profile /
    spontaneity_rates JSON. Parsing via the canonical helpers must
    round-trip to the same Pydantic shapes the engine consumes."""
    count = load_library_personas(db, tmp_path)
    assert count == 4

    for persona_id, expected in EXPECTED_PERSONA_ATTRS.items():
        row = db.execute(
            "SELECT role_weights, voice_profile, spontaneity_rates FROM personas WHERE id = ?",
            (persona_id,),
        ).fetchone()
        assert row is not None, persona_id

        role_weights = parse_role_weights(row["role_weights"])
        assert role_weights.root == expected["role_weights"], persona_id

        voice_profile = parse_voice_profile(row["voice_profile"])
        assert voice_profile is not None, persona_id
        expected_voice = expected["voice_profile"]
        assert isinstance(expected_voice, dict)
        assert voice_profile.rate == expected_voice["rate"], persona_id
        assert voice_profile.pitch == expected_voice["pitch"], persona_id

        spontaneity = parse_spontaneity_rates(row["spontaneity_rates"])
        expected_spontaneity = expected["spontaneity_rates"]
        assert isinstance(expected_spontaneity, dict)
        assert spontaneity.jokes == expected_spontaneity["jokes"], persona_id
        assert spontaneity.songs == expected_spontaneity["songs"], persona_id


def test_loader_default_path_is_byte_identical_to_migration_and_module_defaults(
    db: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    """A library persona JSON that OMITS role_weights / voice_profile /
    spontaneity_rates must persist DB values byte-identical to both
    the migration 0014 DEFAULT literals AND the DEFAULT_*_JSON module
    constants.

    Pins the wire shape across three producers (loader fallback path,
    migration column DEFAULT for backfilled custom rows, module
    constants used by future "is this row at its default?" checks).
    See .claude/rules/code-quality.md §3 — same data, different bytes
    is a latent producer-consumer drift bug.
    """
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    # Copy the real schema next to the synthetic persona so the loader
    # finds it where it expects (the library_dir override only swaps
    # the search root).
    from toybox.personas.loader import LIBRARY_DIR as REAL_LIBRARY_DIR
    from toybox.personas.loader import SCHEMA_FILENAME

    (library_dir / SCHEMA_FILENAME).write_text(
        (REAL_LIBRARY_DIR / SCHEMA_FILENAME).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # Avatar dir + a placeholder PNG so the loader's path-traversal
    # check passes; missing-file warning is acceptable, persona row
    # still inserts.
    avatars_dir = library_dir / "avatars"
    avatars_dir.mkdir()
    (avatars_dir / "minimal.png").write_bytes(b"")

    minimal = {
        "id": "minimal",
        "display_name": "Minimal",
        "archetype": "custom",
        "system_prompt": "A minimal library persona that omits the K1 columns.",
        "avatar_image_path": "library/avatars/minimal.png",
        "behavior_tags": ["minimal"],
        "age_range_min": 3,
        "age_range_max": 10,
        "language": "en",
        "source": "library",
    }
    (library_dir / "minimal.json").write_text(json.dumps(minimal), encoding="utf-8")

    count = load_library_personas(db, tmp_path / "data", library_dir=library_dir)
    assert count == 1

    row = db.execute(
        "SELECT role_weights, voice_profile, spontaneity_rates FROM personas WHERE id = ?",
        ("minimal",),
    ).fetchone()
    assert row is not None

    # Byte-identical to the DEFAULT_*_JSON module constants.
    assert row["role_weights"] == DEFAULT_ROLE_WEIGHTS_JSON
    assert row["voice_profile"] is None
    assert row["spontaneity_rates"] == DEFAULT_SPONTANEITY_RATES_JSON

    # Byte-identical to migration 0014's DEFAULT literals. The migration
    # already writes these defaults onto backfilled rows; this assertion
    # pins the loader path to the same bytes so a future
    # ``row["spontaneity_rates"] == DEFAULT_SPONTANEITY_RATES_JSON``
    # check passes uniformly across library + custom + backfilled rows.
    assert row["role_weights"] == "{}"
    assert row["spontaneity_rates"] == '{"jokes":0.0,"songs":0.0}'
