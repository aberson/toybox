"""Curated NLP trigger registry.

The registry is a JSON file shipped with the package
(``defaults.json``) plus a user-editable copy seeded to
``data/triggers.json`` (or wherever ``TOYBOX_TRIGGERS_USER_PATH``
points). On every load we merge the shipped defaults into the user
file:

* User file missing → seed from defaults verbatim.
* Shipped pattern with HIGHER ``version`` than the user's same-id entry
  → user entry is updated to the new shipped fields.
* Shipped pattern not present in user file at all → appended as new.
* User-only patterns (no matching id in defaults) → preserved unchanged.
* Same id, same version → no change.

The merge is keyed on the ``id`` field of each pattern — a stable slug
chosen by the developer (e.g. ``"lets_play_X"``). Using the regex text
as the key would re-seed every time we tweak a regex without bumping
the version, which is wrong; using a separate id makes the intent of
the bump explicit.

``match()`` is deterministic and offline. It evaluates every curated
pattern against the input plus every dynamic toy-name pattern from
:mod:`toybox.triggers.dynamic`, deduplicates on
``(name, slot, pattern_id)``, and sorts the result list by
``pattern_id`` for stable ordering.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from ..db import connect, resolve_db_path
from .dynamic import MENTION_TOY_INTENT, ToyTrigger, load_toy_triggers

_logger = logging.getLogger(__name__)

_PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent
DEFAULTS_PATH: Final[Path] = _PACKAGE_DIR / "defaults.json"

TRIGGERS_USER_PATH_ENV: Final[str] = "TOYBOX_TRIGGERS_USER_PATH"
DEFAULT_USER_PATH: Final[Path] = Path("data") / "triggers.json"

SCHEMA_VERSION: Final[int] = 1


class Intent(BaseModel):
    """One match emitted by :func:`match`.

    ``confidence`` is reserved for future weighting; v1 curated and
    dynamic patterns are always ``1.0``.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    slot: str | None
    pattern_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


@dataclass(frozen=True, slots=True)
class _Pattern:
    """A loaded, compiled curated pattern."""

    id: str
    regex: str
    intent: str
    slot_group: int | None
    version: int
    compiled: re.Pattern[str]


def user_path() -> Path:
    """Return the user trigger-file path, honoring the env override."""
    raw = os.environ.get(TRIGGERS_USER_PATH_ENV)
    return Path(raw) if raw else DEFAULT_USER_PATH


def _read_json(path: Path) -> dict[str, Any] | None:
    """Return the parsed JSON object at ``path`` or ``None`` on any error.

    Malformed files are logged at WARNING and treated as missing —
    consistent with the OAuth secrets loader convention.
    """
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("triggers file unreadable, treating as missing: %s (%s)", path, exc)
        return None
    if not isinstance(raw, dict):
        _logger.warning("triggers file is not a JSON object, treating as missing: %s", path)
        return None
    return raw


def _write_user_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _merge_patterns(
    shipped: list[dict[str, Any]],
    user: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Merge shipped defaults into user patterns by ``id``.

    Returns ``(merged_patterns, changed)`` — ``changed`` flags whether
    the merged list differs from ``user`` so the caller can avoid
    rewriting the file when it's already current.
    """
    user_by_id = {p["id"]: p for p in user if isinstance(p, dict) and "id" in p}
    shipped_by_id = {p["id"]: p for p in shipped if isinstance(p, dict) and "id" in p}

    changed = False
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Walk shipped first so newly-added patterns are added in the
    # order they appear in defaults.json. Then preserve user-only
    # patterns at the end.
    for sid, sentry in shipped_by_id.items():
        if sid in user_by_id:
            uentry = user_by_id[sid]
            if int(uentry.get("version", 0)) < int(sentry["version"]):
                merged.append(dict(sentry))
                changed = True
            else:
                merged.append(uentry)
        else:
            merged.append(dict(sentry))
            changed = True
        seen.add(sid)

    # Preserve user-only patterns (no matching shipped id).
    for uid, uentry in user_by_id.items():
        if uid in seen:
            continue
        merged.append(uentry)
    # If user file had patterns missing the ``id`` field (malformed),
    # they're dropped — log so an operator can repair them.
    bad = [p for p in user if not (isinstance(p, dict) and "id" in p)]
    if bad:
        _logger.warning("dropping %d malformed user trigger pattern(s) (no id field)", len(bad))
        changed = True

    return merged, changed


def _load_shipped_defaults() -> dict[str, Any]:
    raw = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"shipped defaults at {DEFAULTS_PATH} is not a JSON object")
    return raw


def load_registry(
    *,
    user_path_override: Path | None = None,
) -> list[_Pattern]:
    """Load the curated trigger registry.

    On first run (user file missing) the user file is seeded from the
    shipped defaults. On every load the shipped defaults are merged
    into the user file by ``id``+``version`` (see module docstring).

    Args:
        user_path_override: Test hook to redirect the user file. In
            production code, prefer the ``TOYBOX_TRIGGERS_USER_PATH``
            env var so the path resolution is uniform across all
            entry points.
    """
    target = user_path_override if user_path_override is not None else user_path()
    shipped = _load_shipped_defaults()
    shipped_patterns = list(shipped.get("patterns", []))

    user_payload = _read_json(target)
    if user_payload is None:
        # Seed the user file from defaults on first run.
        _write_user_file(target, shipped)
        merged_patterns = shipped_patterns
    else:
        user_patterns = list(user_payload.get("patterns", []))
        merged_patterns, changed = _merge_patterns(shipped_patterns, user_patterns)
        if changed or int(user_payload.get("version", 0)) != SCHEMA_VERSION:
            _write_user_file(
                target,
                {"version": SCHEMA_VERSION, "patterns": merged_patterns},
            )

    compiled: list[_Pattern] = []
    for entry in merged_patterns:
        try:
            pat = _Pattern(
                id=str(entry["id"]),
                regex=str(entry["regex"]),
                intent=str(entry["intent"]),
                slot_group=(
                    int(entry["slot_group"]) if entry.get("slot_group") is not None else None
                ),
                version=int(entry.get("version", 1)),
                compiled=re.compile(str(entry["regex"])),
            )
        except (KeyError, re.error, TypeError, ValueError) as exc:
            _logger.warning(
                "skipping malformed trigger pattern: %s (%s)", entry.get("id", "<?>"), exc
            )
            continue
        compiled.append(pat)
    return compiled


def _slot_text(match: re.Match[str], slot_group: int | None) -> str | None:
    if slot_group is None:
        return None
    try:
        captured = match.group(slot_group)
    except IndexError:
        return None
    if captured is None:
        return None
    text = captured.strip()
    return text or None


def _curated_intents(text: str, patterns: list[_Pattern]) -> list[Intent]:
    out: list[Intent] = []
    for pat in patterns:
        for m in pat.compiled.finditer(text):
            slot = _slot_text(m, pat.slot_group)
            out.append(
                Intent(
                    name=pat.intent,
                    slot=slot,
                    pattern_id=pat.id,
                    confidence=1.0,
                )
            )
    return out


def _toy_intents(text: str, triggers: list[ToyTrigger]) -> list[Intent]:
    out: list[Intent] = []
    for trig in triggers:
        if trig.pattern.search(text) is None:
            continue
        out.append(
            Intent(
                name=MENTION_TOY_INTENT,
                slot=trig.display_name,
                pattern_id=trig.pattern_id,
                confidence=1.0,
            )
        )
    return out


def match(
    text: str,
    db_path: Path | None = None,
    *,
    user_path_override: Path | None = None,
) -> list[Intent]:
    """Return all intents that match ``text``. Deterministic, offline.

    Args:
        text: The user utterance / transcript to scan.
        db_path: Override the SQLite path used for the dynamic
            toy-name source. ``None`` resolves via
            :func:`toybox.db.resolve_db_path`. If the resolved path
            doesn't exist, dynamic triggers are silently skipped (a
            fresh install with no DB is still expected to return
            curated matches).
        user_path_override: Test hook to redirect the user trigger
            file (see :func:`load_registry`).

    Returns:
        Deduplicated list of :class:`Intent` ordered by
        ``pattern_id``. Duplicates are detected by the
        ``(name, slot, pattern_id)`` tuple.
    """
    patterns = load_registry(user_path_override=user_path_override)
    intents = _curated_intents(text, patterns)

    resolved_db = db_path if db_path is not None else resolve_db_path()
    if resolved_db.is_file():
        conn = connect(resolved_db)
        try:
            triggers = load_toy_triggers(conn)
        finally:
            conn.close()
        intents.extend(_toy_intents(text, triggers))
    elif db_path is not None:
        # Caller passed a path that doesn't exist — surface it loudly
        # so a misconfigured caller doesn't silently miss toy intents.
        _logger.warning("trigger match db_path %s does not exist; skipping toy intents", db_path)

    # Dedupe on (name, slot, pattern_id) preserving first occurrence,
    # then sort by pattern_id for stable output.
    seen: set[tuple[str, str | None, str]] = set()
    deduped: list[Intent] = []
    for intent in intents:
        key = (intent.name, intent.slot, intent.pattern_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(intent)
    deduped.sort(key=lambda i: (i.pattern_id, i.name, i.slot or ""))
    return deduped


__all__ = [
    "DEFAULTS_PATH",
    "DEFAULT_USER_PATH",
    "Intent",
    "SCHEMA_VERSION",
    "TRIGGERS_USER_PATH_ENV",
    "load_registry",
    "match",
    "user_path",
]
