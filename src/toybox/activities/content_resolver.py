"""Resolve real catalog content (toys, rooms, child profiles) for the generator.

Step 19 wires the v1 catalog tables (``toys``, ``rooms``, ``room_features``,
``children``) into the activity generator. The generator's
:func:`build_generator_context` already accepts ``available_toys``,
``available_rooms``, and ``child_profile`` arguments — this module is the
producer for those values.

Why a separate module: the activities API call site already has plenty
to do (proposing + persisting + labeled_events + feedback). Pushing
content resolution into a dedicated module keeps each layer focused and
makes the resolver unit-testable in isolation.

Sampling determinism
--------------------

Every selection step is deterministic:

* ``resolve_toys`` orders by ``last_used_at DESC`` (NULL → epoch 0)
  with id-tiebreak; Python's ``sorted`` is stable.
* ``resolve_rooms`` orders by ``display_name COLLATE NOCASE``.
* ``aggregate_child_constraints`` performs union/min on already-sorted
  inputs.

No ``random.sample`` calls — tests assert byte-identical output across
runs given the same seed.

Banned-themes filter
--------------------

Two layers:

1. Offline template filter (:func:`apply_banned_themes_filter`) drops
   templates whose ``id`` or ``title`` text contains a banned theme
   substring (case-insensitive, both directions). When ALL templates
   filter out, returns the built-in :data:`SAFE_DEFAULT_TEMPLATE` plus
   a WARNING log so observability surfaces the slip.

2. Claude system-prompt directive (:func:`build_claude_directive`) —
   plain-English ``"Do NOT include any of: ..."`` insert that the
   escalation path appends to the model's system prompt.

Reading-level handling
----------------------

Maps a child's ``reading_level`` to a single English directive line
appended to the Claude system prompt. The offline path doesn't need a
parallel mapping today (templates lack a complexity tag); when a future
template ships with ``"complexity": "simple"`` the offline filter can
prefer those, but until then reading-level is Claude-only and the
banned-themes filter is the load-bearing safety net.

Multi-child semantics
---------------------

When an activity has more than one ``child_id``:

* Banned themes: UNION (most restrictive wins; banned by ANY child = banned).
* Reading level: MINIMUM (lowest = most restrictive).
  ``pre-reader`` < ``early-reader`` < ``fluent``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, cast

_logger = logging.getLogger(__name__)

# Reading level enum mirrors :class:`toybox.api.children.ReadingLevel`
# kept local to avoid pulling the API package into a generator-side import.
ReadingLevel = Literal["pre-reader", "early-reader", "fluent"]
_VALID_READING_LEVELS: Final[frozenset[str]] = frozenset({"pre-reader", "early-reader", "fluent"})
_READING_LEVEL_ORDER: Final[dict[str, int]] = {
    "pre-reader": 0,
    "early-reader": 1,
    "fluent": 2,
}

# Env-tunable caps. The default of 12 toys × ~50 char names + 6 rooms
# × ~30 char names is comfortably under 1KB — well within the Claude
# system-prompt budget even with a verbose persona card.
TOYS_LIMIT_ENV: Final[str] = "TOYBOX_GENERATOR_MAX_TOYS"
ROOMS_LIMIT_ENV: Final[str] = "TOYBOX_GENERATOR_MAX_ROOMS"
DEFAULT_TOYS_LIMIT: Final[int] = 12
DEFAULT_ROOMS_LIMIT: Final[int] = 6

# Reading-level prompt directives. Returned verbatim from
# :func:`build_claude_directive`. See module docstring for the policy.
_READING_LEVEL_DIRECTIVES: Final[dict[str, str]] = {
    "pre-reader": (
        "Use very simple words. Each step is one short sentence. No more than 6 words per step."
    ),
    "early-reader": ("Use simple words. Each step is one or two short sentences."),
    # ``fluent`` is the default rich-language case — no extra directive.
    "fluent": "",
}


@dataclass(frozen=True, slots=True)
class ResolvedToy:
    """A toy as the generator sees it.

    ``last_used_at`` is included so the call site can also record toy
    recency on the labeled_events row if it wants — the resolver itself
    doesn't update the column.
    """

    id: str
    display_name: str
    tags: tuple[str, ...] = ()
    persona_id: str | None = None
    last_used_at: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedRoom:
    """A room + its feature names."""

    id: str
    display_name: str
    features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedChildren:
    """Aggregated child-constraints across an activity's child_ids."""

    banned_themes: tuple[str, ...] = ()
    reading_level: ReadingLevel | None = None


@dataclass(frozen=True, slots=True)
class ChildProfileRow:
    """One child profile fetched for aggregation."""

    id: str
    banned_themes: tuple[str, ...] = ()
    reading_level: ReadingLevel | None = None


@dataclass(frozen=True, slots=True)
class SafeDefaultTemplate:
    """Tag-free safe-default template for the all-banned fallback path.

    Shape mirrors the offline generator's internal ``_Template`` so the
    caller can swap it in without re-shaping the value. Kept simple on
    purpose: 5 steps, no slot/toy placeholders that could surface a
    banned theme.
    """

    id: str
    title: str
    buckets: frozenset[str]
    steps: tuple[dict[str, str | None], ...] = field(default_factory=tuple)


# Hard-coded SAFE-DEFAULT used when banned-themes wipe out every
# eligible template. The text deliberately avoids any persona-specific
# vocabulary — it is the last resort, not a "good" suggestion.
#
# The safe-default DELIBERATELY bypasses ``apply_banned_themes_filter``:
# it is the *producer* of the safe-default, not a candidate for
# filtering. A parent whose banned_themes happen to substring-match
# "quiet" or "moment" still gets this template — the alternative is
# returning zero templates (which would crash dispatch). If you change
# the title/id below, keep the new words equally neutral so this
# bypass remains intuitively safe; any future "real" filtering of the
# safe-default should happen by tagging it with a banned-theme-aware
# attribute, not by re-running the substring filter.
SAFE_DEFAULT_TEMPLATE: Final[SafeDefaultTemplate] = SafeDefaultTemplate(
    id="safe_default_quiet_moment",
    title="A quiet moment together",
    buckets=frozenset({"always"}),
    steps=(
        {"text": "Find a comfy spot to sit.", "sfx": None, "expected_action": None},
        {"text": "Take three slow, deep breaths.", "sfx": None, "expected_action": None},
        {"text": "Look around and name one thing you see.", "sfx": None, "expected_action": None},
        {"text": "Name one thing you hear.", "sfx": None, "expected_action": None},
        {"text": "Stretch your arms up high, then relax.", "sfx": None, "expected_action": None},
    ),
)


def _toys_limit() -> int:
    """Read ``TOYBOX_GENERATOR_MAX_TOYS`` from env; default 12.

    Malformed values (non-int, negative) emit a WARNING and fall back
    to :data:`DEFAULT_TOYS_LIMIT` so a typo'd env var doesn't silently
    disable the resolver. ``"0"`` is treated as the operator's
    explicit "disable" knob and is honoured (the caller short-circuits
    to an empty list).
    """
    raw = os.environ.get(TOYS_LIMIT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_TOYS_LIMIT
    try:
        n = int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int; using default %d",
            TOYS_LIMIT_ENV,
            raw,
            DEFAULT_TOYS_LIMIT,
        )
        return DEFAULT_TOYS_LIMIT
    if n < 0:
        _logger.warning(
            "%s=%r is negative; using default %d",
            TOYS_LIMIT_ENV,
            raw,
            DEFAULT_TOYS_LIMIT,
        )
        return DEFAULT_TOYS_LIMIT
    return n


def _rooms_limit() -> int:
    """Read ``TOYBOX_GENERATOR_MAX_ROOMS`` from env; default 6.

    Malformed values (non-int, negative) emit a WARNING and fall back
    to :data:`DEFAULT_ROOMS_LIMIT`. ``"0"`` is honoured (explicit
    disable).
    """
    raw = os.environ.get(ROOMS_LIMIT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_ROOMS_LIMIT
    try:
        n = int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int; using default %d",
            ROOMS_LIMIT_ENV,
            raw,
            DEFAULT_ROOMS_LIMIT,
        )
        return DEFAULT_ROOMS_LIMIT
    if n < 0:
        _logger.warning(
            "%s=%r is negative; using default %d",
            ROOMS_LIMIT_ENV,
            raw,
            DEFAULT_ROOMS_LIMIT,
        )
        return DEFAULT_ROOMS_LIMIT
    return n


def _coerce_reading_level(raw: object) -> ReadingLevel | None:
    """Narrow a freeform DB string to the :data:`ReadingLevel` literal.

    Returns ``None`` for unknown values — the parent UI can re-save with
    a valid pick. Mirrors the defensive coercion in
    :func:`toybox.api.children._row_to_profile`.
    """
    if isinstance(raw, str) and raw in _VALID_READING_LEVELS:
        return cast("ReadingLevel", raw)
    return None


def _split_csv(raw: str | None) -> tuple[str, ...]:
    """Decode a comma-separated TEXT column into a deduped lowercased tuple.

    Used for ``toys.tags`` and ``children.banned_themes``. Whitespace
    is trimmed; empty entries are dropped. Order preserved (after dedup
    by lowercased form) so callers that care about display order get
    something reasonable.
    """
    if not raw:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for item in raw.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        key = stripped.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(stripped)
    return tuple(out)


def resolve_toys(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    child_ids: Sequence[str] | None = None,
) -> list[ResolvedToy]:
    """Fetch non-archived toys, recency-sorted, capped at ``limit``.

    Args:
        conn: SQLite connection.
        limit: Optional cap on the number of toys returned. Defaults to
            :data:`DEFAULT_TOYS_LIMIT` (overridable via env).
        child_ids: Reserved for future per-child filtering (e.g. a
            "this toy is hers" link table). Currently unused; the
            argument is kept on the signature so callers don't have to
            change when filtering lands.

    Returns:
        A list of :class:`ResolvedToy`, sorted by ``last_used_at DESC``
        (null = oldest) with ``id`` ASC tiebreak. Empty when the toys
        table is empty.
    """
    del child_ids  # Reserved; see docstring.
    cap = limit if limit is not None else _toys_limit()
    if cap <= 0:
        return []
    # ``ORDER BY last_used_at IS NULL ASC`` puts the rows with a real
    # timestamp first (``IS NULL`` is 0/1 in SQLite), then ``last_used_at
    # DESC`` orders those by recency, with id-ASC tiebreak. NULL rows
    # sort after, also tiebroken by id ASC. ``COLLATE BINARY`` keeps the
    # tiebreak deterministic across SQLite builds (BINARY is the default
    # for TEXT but pinning it makes the contract explicit).
    rows = conn.execute(
        "SELECT id, display_name, tags, persona_id, last_used_at FROM toys "
        "WHERE archived = 0 "
        "ORDER BY last_used_at IS NULL ASC, last_used_at DESC, id COLLATE BINARY ASC "
        "LIMIT ?",
        (cap,),
    ).fetchall()
    return [_row_to_resolved_toy(r) for r in rows]


def _row_to_resolved_toy(row: sqlite3.Row) -> ResolvedToy:
    return ResolvedToy(
        id=str(row["id"]),
        display_name=str(row["display_name"]),
        tags=_split_csv(row["tags"]),
        persona_id=row["persona_id"],
        last_used_at=row["last_used_at"],
    )


def resolve_rooms(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> list[ResolvedRoom]:
    """Fetch all rooms + their features, capped at ``limit``.

    Args:
        conn: SQLite connection.
        limit: Optional cap; defaults to :data:`DEFAULT_ROOMS_LIMIT`.

    Returns:
        Rooms sorted by ``display_name COLLATE NOCASE`` ASC. Each room
        carries its features in ``room_features.name`` order
        (ASC, NOCASE) as a tuple.
    """
    cap = limit if limit is not None else _rooms_limit()
    if cap <= 0:
        return []
    rows = conn.execute(
        "SELECT id, display_name FROM rooms "
        "WHERE display_name IS NOT NULL "
        "ORDER BY display_name COLLATE NOCASE ASC"
    ).fetchall()
    rooms: list[ResolvedRoom] = []
    for row in rows[:cap]:
        feature_rows = conn.execute(
            "SELECT name FROM room_features "
            "WHERE room_id = ? AND name IS NOT NULL "
            "ORDER BY name COLLATE NOCASE ASC",
            (str(row["id"]),),
        ).fetchall()
        features = tuple(str(f["name"]) for f in feature_rows)
        rooms.append(
            ResolvedRoom(
                id=str(row["id"]),
                display_name=str(row["display_name"]),
                features=features,
            )
        )
    return rooms


def resolve_child_profiles(
    conn: sqlite3.Connection,
    child_ids: Sequence[str],
) -> ResolvedChildren:
    """Fetch the child rows for ``child_ids`` and aggregate constraints.

    Empty ``child_ids`` returns a no-constraint :class:`ResolvedChildren`
    (no banned themes, no reading-level constraint). Unknown ids are
    silently dropped — the caller may not have a child profile yet.
    """
    if not child_ids:
        return ResolvedChildren()
    unique = list(dict.fromkeys(child_ids))  # preserve order, dedupe
    placeholders = ",".join("?" * len(unique))
    rows = conn.execute(
        f"SELECT id, banned_themes, reading_level FROM children WHERE id IN ({placeholders})",
        unique,
    ).fetchall()
    profiles: list[ChildProfileRow] = []
    for row in rows:
        reading_level_raw = row["reading_level"]
        reading_level: ReadingLevel | None = _coerce_reading_level(reading_level_raw)
        banned = _split_csv(row["banned_themes"])
        profiles.append(
            ChildProfileRow(
                id=str(row["id"]),
                banned_themes=banned,
                reading_level=reading_level,
            )
        )
    return aggregate_child_constraints(profiles)


def aggregate_child_constraints(
    profiles: Sequence[ChildProfileRow],
) -> ResolvedChildren:
    """Aggregate banned_themes (UNION) and reading_level (MINIMUM).

    "Most restrictive wins":

    * Banned themes: union across children, lowercased, deduplicated, sorted.
    * Reading level: minimum of present values
      (``pre-reader`` < ``early-reader`` < ``fluent``); unknown/null
      values do NOT participate (a child with no level set doesn't
      drag the rest down to "no constraint").

    Empty ``profiles`` returns a default :class:`ResolvedChildren`.
    """
    if not profiles:
        return ResolvedChildren()
    banned: set[str] = set()
    for p in profiles:
        for theme in p.banned_themes:
            banned.add(theme.strip().lower())
    chosen: ReadingLevel | None = None
    best_rank: int | None = None
    for p in profiles:
        cur = p.reading_level
        if cur is None:
            continue
        rank = _READING_LEVEL_ORDER[cur]
        if best_rank is None or rank < best_rank:
            best_rank = rank
            chosen = cur
    return ResolvedChildren(
        banned_themes=tuple(sorted(banned)),
        reading_level=chosen,
    )


def _template_haystack(template_id: str, template_title: str) -> str:
    """Combine the fields a banned-theme substring filter inspects."""
    return f"{template_id} {template_title}".lower()


def apply_banned_themes_filter(
    templates: Sequence[object],
    banned_themes: Sequence[str],
    *,
    safe_default: object | None = None,
) -> list[object]:
    """Drop templates that case-insensitively substring-match any banned theme.

    Each template is expected to expose ``id`` and ``title`` attributes
    (matches the offline generator's ``_Template`` dataclass; a
    ``SafeDefaultTemplate`` is also acceptable). Substring is checked
    in both directions:

    * theme ``"scary"`` matches a template whose id/title contains ``"scary"``.
    * theme ``"scary monster"`` matches a template tagged ``"scary"``
      (the shorter side is contained in the longer, so the dual-direction
      check fires).

    Empty ``banned_themes`` returns ``list(templates)`` unchanged.

    When the filter empties the list, returns ``[safe_default]`` (or
    ``[SAFE_DEFAULT_TEMPLATE]`` if ``safe_default`` is None) and emits
    a WARNING log so the surfacing is observable.
    """
    if not banned_themes:
        return list(templates)
    # Defensive isinstance(t, str) guards against a non-string entry
    # slipping through from a hand-built dict (e.g. a future API client
    # passing a list[Any]) — ``.strip()`` on ``None`` / ``int`` would
    # crash propose. In-tree callers always produce strings, but the
    # filter is exported on the public surface.
    lowered = [t.strip().lower() for t in banned_themes if isinstance(t, str) and t.strip()]
    if not lowered:
        return list(templates)

    kept: list[object] = []
    for tpl in templates:
        tpl_id = str(getattr(tpl, "id", ""))
        tpl_title = str(getattr(tpl, "title", ""))
        haystack = _template_haystack(tpl_id, tpl_title)
        blocked = False
        for theme in lowered:
            if not theme:
                continue
            if theme in haystack or haystack in theme:
                blocked = True
                break
        if not blocked:
            kept.append(tpl)

    if not kept:
        fallback = safe_default if safe_default is not None else SAFE_DEFAULT_TEMPLATE
        _logger.warning(
            "all %d candidate templates filtered by banned_themes=%r; "
            "falling back to safe-default %r",
            len(templates),
            sorted(set(lowered)),
            getattr(fallback, "id", "?"),
        )
        return [fallback]
    return kept


def build_claude_directive(
    banned_themes: Sequence[str],
    reading_level: ReadingLevel | None,
) -> str:
    """Build the system-prompt insert for banned themes + reading level.

    Returns:
        A possibly-empty string. Empty when there are no constraints
        to express (caller can append it unconditionally and trust the
        no-op for the default case).

        Format::

            Do NOT include any of: <comma-separated themes>.
            <reading-level directive line>

        Lines are only added when their input is non-empty.
    """
    lines: list[str] = []
    # Re-split on commas defensively: the resolver pipeline normally
    # passes already-split tokens, but the helper is exported and a
    # caller might pass a single ``"scary, loud"`` entry by mistake.
    # Without this split the directive would render that as a single
    # long banned-theme name, which doesn't help the model.
    cleaned: list[str] = []
    for raw in banned_themes:
        if not isinstance(raw, str):
            continue
        for part in raw.split(","):
            stripped = part.strip()
            if stripped:
                cleaned.append(stripped)
    if cleaned:
        # Dedup on lowercased form so "Scary"/"scary" merge; the
        # display surface keeps the lowercased canonical form.
        unique = sorted({c.lower() for c in cleaned})
        lines.append("Do NOT include any of: " + ", ".join(unique) + ".")
    if reading_level is not None:
        directive = _READING_LEVEL_DIRECTIVES.get(reading_level, "")
        if directive:
            lines.append(directive)
    return "\n".join(lines)


__all__ = [
    "DEFAULT_ROOMS_LIMIT",
    "DEFAULT_TOYS_LIMIT",
    "ROOMS_LIMIT_ENV",
    "SAFE_DEFAULT_TEMPLATE",
    "TOYS_LIMIT_ENV",
    "ChildProfileRow",
    "ReadingLevel",
    "ResolvedChildren",
    "ResolvedRoom",
    "ResolvedToy",
    "SafeDefaultTemplate",
    "aggregate_child_constraints",
    "apply_banned_themes_filter",
    "build_claude_directive",
    "resolve_child_profiles",
    "resolve_rooms",
    "resolve_toys",
]
