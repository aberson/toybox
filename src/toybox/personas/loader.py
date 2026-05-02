"""Persona library loader.

Walks ``src/toybox/personas/library/*.json`` (skipping files whose name
starts with ``_``), validates each entry against ``_schema.json``, and
upserts a row into the ``personas`` table with ``source='library'``. The
matching avatar is copied to ``<data_dir>/images/personas/<id>.png`` if not
already present with the same SHA-256 content.

Per ``documentation/plan.md`` §Failure Modes: a malformed or invalid
persona JSON is logged and skipped — startup continues.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent
LIBRARY_DIR: Final[Path] = _PACKAGE_DIR / "library"
SCHEMA_FILENAME: Final[str] = "_schema.json"
AVATAR_SUBDIR: Final[Path] = Path("images") / "personas"

_logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_schema(library_dir: Path) -> Draft202012Validator:
    schema_path = library_dir / SCHEMA_FILENAME
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _iter_persona_files(library_dir: Path) -> list[Path]:
    return sorted(
        p for p in library_dir.glob("*.json") if p.is_file() and not p.name.startswith("_")
    )


def _upsert_persona(conn: sqlite3.Connection, persona: dict[str, Any], now: str) -> None:
    conn.execute(
        """
        INSERT INTO personas (
            id, display_name, archetype, system_prompt, avatar_image_path,
            avatar_image_hash, behavior_tags, age_range_min, age_range_max,
            language, source, default_voice_tone, created_at
        ) VALUES (
            :id, :display_name, :archetype, :system_prompt, :avatar_image_path,
            NULL, :behavior_tags, :age_range_min, :age_range_max,
            :language, :source, :default_voice_tone, :created_at
        )
        ON CONFLICT(id) DO UPDATE SET
            display_name        = excluded.display_name,
            archetype           = excluded.archetype,
            system_prompt       = excluded.system_prompt,
            avatar_image_path   = excluded.avatar_image_path,
            avatar_image_hash   = NULL,
            behavior_tags       = excluded.behavior_tags,
            age_range_min       = excluded.age_range_min,
            age_range_max       = excluded.age_range_max,
            language            = excluded.language,
            source              = excluded.source,
            default_voice_tone  = excluded.default_voice_tone
        """,
        {
            "id": persona["id"],
            "display_name": persona["display_name"],
            "archetype": persona["archetype"],
            "system_prompt": persona["system_prompt"],
            "avatar_image_path": persona["avatar_image_path"],
            "behavior_tags": json.dumps(persona["behavior_tags"], ensure_ascii=False),
            "age_range_min": persona["age_range_min"],
            "age_range_max": persona["age_range_max"],
            "language": persona.get("language", "en"),
            "source": persona["source"],
            "default_voice_tone": persona.get("default_voice_tone"),
            "created_at": now,
        },
    )


def _copy_avatar_if_changed(src: Path, dst: Path) -> bool:
    if not src.is_file():
        _logger.warning("avatar source missing: %s", src)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and _sha256_file(src) == _sha256_file(dst):
        return False
    shutil.copy2(src, dst)
    return True


def load_library_personas(
    conn: sqlite3.Connection,
    data_dir: Path,
    *,
    library_dir: Path | None = None,
) -> int:
    """Load all library personas into the personas table.

    Args:
        conn: Open SQLite connection (typically from
            :func:`toybox.db.connection.connect`) with the v1 schema applied.
        data_dir: Filesystem root for the toybox data tree. Avatar copies
            land at ``<data_dir>/images/personas/<id>.png``.
        library_dir: Override for the source library directory. Defaults to
            the package's own ``library/`` folder. Tests use this to point
            at temp dirs containing deliberately-broken JSON.

    Returns:
        Number of personas upserted (validation failures are skipped, not
        counted).

    Behavior notes:
        * Malformed JSON (``JSONDecodeError``) and schema-invalid persona
          payloads are logged at WARNING and skipped — startup continues.
        * Avatars whose ``avatar_image_path`` resolves outside the library
          tree (path-traversal defense; should be caught earlier by the
          schema pattern) are logged at WARNING; the persona row is NOT
          inserted and no bytes are copied.
        * If the persona row was inserted but its avatar source file is
          missing on disk, a WARNING is logged; the persona row stays in
          the DB and no bytes are copied (the avatar can be repopulated by
          re-running the loader once the file exists).
        * If ``_schema.json`` itself is malformed, unreadable, or missing,
          this function raises (this is treated as a startup precondition,
          not a per-persona error).
    """
    base = library_dir if library_dir is not None else LIBRARY_DIR
    package_root = base.parent
    validator = _load_schema(base)
    persona_files = _iter_persona_files(base)
    avatars_dest = data_dir / AVATAR_SUBDIR
    library_root = base.resolve()

    processed = 0
    now = _utcnow_iso()
    for persona_path in persona_files:
        try:
            persona = json.loads(persona_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _logger.warning("persona JSON malformed, skipping: %s (%s)", persona_path.name, exc)
            continue

        try:
            validator.validate(persona)
        except ValidationError as exc:
            _logger.warning(
                "persona JSON failed schema validation, skipping: %s (%s)",
                persona_path.name,
                exc.message,
            )
            continue

        # Defense-in-depth: even if a permissive schema slipped past
        # validation, refuse to read avatars from outside the library tree.
        avatar_src = (package_root / persona["avatar_image_path"]).resolve()
        if not avatar_src.is_relative_to(library_root):
            _logger.warning(
                "persona %s avatar_image_path escapes library tree, skipping: %s",
                persona["id"],
                persona["avatar_image_path"],
            )
            continue

        with conn:
            _upsert_persona(conn, persona, now)

        avatar_dst = avatars_dest / f"{persona['id']}.png"
        _copy_avatar_if_changed(avatar_src, avatar_dst)

        processed += 1

    return processed


__all__ = ["AVATAR_SUBDIR", "LIBRARY_DIR", "SCHEMA_FILENAME", "load_library_personas"]
