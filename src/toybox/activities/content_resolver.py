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

import hashlib
import json
import logging
import os
import random
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol, cast

from ..core import jokes_enabled as _jokes_enabled
from ..core import songs_enabled as _songs_enabled
from ..core.banned_themes import current_banned_themes_global
from .generic_descriptors import GENERIC_DESCRIPTORS
from .joke_corpus import pick_joke
from .models import Animation
from .roles import Role
from .song_corpus import pick_song
from .themes import Theme
from .topic_extract import extract_themes

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

    ``allowed_roles`` carries the per-toy role restriction added in
    migration 0017. The canonical "unrestricted" representation is the
    empty tuple ``()``; :func:`resolve_role_slots` reads it to filter
    each role-slot's candidate pool. Empty tuple means the toy is
    eligible for every Phase K role (backwards compatible for existing
    rows whose DB column is NULL).
    """

    id: str
    display_name: str
    tags: tuple[str, ...] = ()
    persona_id: str | None = None
    last_used_at: str | None = None
    allowed_roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedRoom:
    """A room + its feature names."""

    id: str
    display_name: str
    features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedChildren:
    """Aggregated child-constraints for a single dispatch.

    ``banned_themes`` is sourced from the household-global setting
    (``settings.banned_themes_global``) via
    :func:`toybox.core.banned_themes.current_banned_themes_global` —
    NOT from per-child aggregation any more. Phase H Step H4 migration
    0009 promoted the value out of the ``children`` table.
    :func:`resolve_child_profiles` is the single producer that does the
    one read per request and populates this field; downstream consumers
    (escalation, generator, propose API) treat the field as already
    UNION-of-the-household.

    ``reading_level`` is still aggregated per-child (MINIMUM wins).
    """

    banned_themes: tuple[str, ...] = ()
    reading_level: ReadingLevel | None = None


@dataclass(frozen=True, slots=True)
class ChildProfileRow:
    """One child profile fetched for aggregation.

    Note: ``banned_themes`` was removed in Phase H Step H4 (migration
    0009 promoted the value to a household-global setting). The field
    no longer lives on individual rows; :func:`resolve_child_profiles`
    populates :class:`ResolvedChildren.banned_themes` from
    :func:`toybox.core.banned_themes.current_banned_themes_global`.
    """

    id: str
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
    """Fetch non-archived, active toys, recency-sorted, capped at ``limit``.

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

    ``active = 0`` rows are excluded so the parent's "deactivate this
    toy" toggle (migration 0018) keeps the toy out of role-casting for
    propose/recast — that's the whole point of the toggle.
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
        "SELECT id, display_name, tags, persona_id, last_used_at, allowed_roles "
        "FROM toys "
        "WHERE archived = 0 AND active = 1 "
        "ORDER BY last_used_at IS NULL ASC, last_used_at DESC, id COLLATE BINARY ASC "
        "LIMIT ?",
        (cap,),
    ).fetchall()
    return [_row_to_resolved_toy(r) for r in rows]


def _decode_toy_allowed_roles(raw: object) -> tuple[str, ...]:
    """Decode the ``toys.allowed_roles`` JSON column to a normalized tuple.

    NULL / empty string / malformed JSON / non-array all normalise to
    the empty tuple (unrestricted). Unknown role names are dropped
    silently — the picker side won't match an entry that isn't a real
    role, but we don't want a stale DB row to crash propose either.
    Mirror of the wire-side decoder in :mod:`toybox.api.toys`.
    """
    if raw is None:
        return ()
    if not isinstance(raw, str):
        return ()
    stripped = raw.strip()
    if not stripped:
        return ()
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        _logger.warning(
            "toys.allowed_roles: malformed JSON %r; treating as unrestricted",
            raw,
        )
        return ()
    if not isinstance(decoded, list):
        return ()
    out: list[str] = []
    for entry in decoded:
        if isinstance(entry, str) and entry:
            out.append(entry)
    return tuple(out)


def _row_to_resolved_toy(row: sqlite3.Row) -> ResolvedToy:
    # ``allowed_roles`` was added by migration 0017; rows from older
    # schemas (or hand-built dicts in tests) may not carry the column.
    # Defensively probe via ``row.keys()`` so the resolver doesn't
    # crash if a caller hands us a partial row.
    try:
        raw_allowed = row["allowed_roles"]
    except (IndexError, KeyError):
        raw_allowed = None
    return ResolvedToy(
        id=str(row["id"]),
        display_name=str(row["display_name"]),
        tags=_split_csv(row["tags"]),
        persona_id=row["persona_id"],
        last_used_at=row["last_used_at"],
        allowed_roles=_decode_toy_allowed_roles(raw_allowed),
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

    ``banned_themes`` comes from the household-global setting (one read
    per request, NOT per child) — see
    :func:`toybox.core.banned_themes.current_banned_themes_global`.
    The Phase H Step H4 migration (0009) promoted the value out of the
    ``children`` table; readers used to UNION the per-child column,
    which is now expressed as a single canonical setting.

    ``reading_level`` is still aggregated per-child (MINIMUM wins) since
    it remains a per-child attribute.

    Empty ``child_ids`` still returns a :class:`ResolvedChildren` whose
    ``banned_themes`` reflects the global setting (no children does not
    mean "no global ban list" — the operator's value still applies for
    trigger-driven activities).
    """
    banned_themes = _banned_themes_from_settings(conn)
    if not child_ids:
        return ResolvedChildren(banned_themes=banned_themes)
    unique = list(dict.fromkeys(child_ids))  # preserve order, dedupe
    placeholders = ",".join("?" * len(unique))
    rows = conn.execute(
        f"SELECT id, reading_level FROM children WHERE id IN ({placeholders})",
        unique,
    ).fetchall()
    profiles: list[ChildProfileRow] = []
    for row in rows:
        reading_level_raw = row["reading_level"]
        reading_level: ReadingLevel | None = _coerce_reading_level(reading_level_raw)
        profiles.append(
            ChildProfileRow(
                id=str(row["id"]),
                reading_level=reading_level,
            )
        )
    aggregated = aggregate_child_constraints(profiles)
    # Splice the global banned_themes onto the aggregated reading-level
    # result. aggregate_child_constraints no longer touches banned_themes
    # (it operates on the per-child reading_level only) so this is the
    # single seam between the two sources.
    return ResolvedChildren(
        banned_themes=banned_themes,
        reading_level=aggregated.reading_level,
    )


def _banned_themes_from_settings(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Read ``settings.banned_themes_global`` and split into normalised tokens.

    Returns an empty tuple when the setting is absent or empty. Tokens
    are trimmed, lowercased, deduped, and order-preserved (first-seen
    wins). Mirrors the per-child decoder behavior the per-child CSV
    column had pre-migration-0009.
    """
    raw = current_banned_themes_global(conn)
    return _split_csv(raw)


def aggregate_child_constraints(
    profiles: Sequence[ChildProfileRow],
) -> ResolvedChildren:
    """Aggregate reading_level (MINIMUM) across child profiles.

    Phase H Step H4 (migration 0009) moved banned_themes out of the
    per-child rows into a household-global setting, so this helper no
    longer touches them — :func:`resolve_child_profiles` is the seam
    that splices the global value onto the aggregated reading-level
    result.

    Reading level: minimum of present values
    (``pre-reader`` < ``early-reader`` < ``fluent``); unknown/null
    values do NOT participate (a child with no level set doesn't drag
    the rest down to "no constraint").

    Empty ``profiles`` returns a default :class:`ResolvedChildren`.
    """
    if not profiles:
        return ResolvedChildren()
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


# ---------------------------------------------------------------------------
# Phase K Step K4 — role slot-fill engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GenericDescriptor:
    """Fallback filler for an unfilled ``optional_roles`` slot.

    Returned by :func:`resolve_role_slots` when the toy pool is exhausted
    before every optional role has been assigned a real toy. The
    ``display_name`` is the string from
    :data:`toybox.activities.generic_descriptors.GENERIC_DESCRIPTORS`
    for that role and lands in the rendered step body via the standard
    ``{role_name}`` substitution path.

    The ``kind`` discriminator literal distinguishes this from
    :class:`ResolvedToy` for callers that pattern-match on the union
    type; the absence of an ``id`` field is the other obvious tag
    (``GenericDescriptor`` is not a row in the ``toys`` table).
    """

    display_name: str
    kind: Literal["generic_descriptor"] = "generic_descriptor"


class _PersonaLike(Protocol):
    """Minimal persona shape needed by :func:`resolve_role_slots`.

    The slot-fill engine reads two attributes:

    * ``id`` — string id, mixed into the deterministic seed so two
      personas with the same ``role_weights`` but distinct ids produce
      distinct casts.
    * ``role_weights`` — mapping ``{role_name: float}``. Keys SHOULD be
      :class:`Role` member values (lowercase snake_case); unknown keys
      are silently ignored at picking time so a stale persona JSON
      doesn't crash propose. Empty mapping → uniform pick across the
      candidate pool.

    A Protocol (not a concrete dataclass) so the call site can pass
    either a Pydantic persona model, a sqlite Row-backed object, or a
    plain dataclass — anything with the two attrs.
    """

    @property
    def id(self) -> str: ...

    @property
    def role_weights(self) -> Mapping[str, float]: ...


# The role-slot dict's value type. Exported on the module surface so
# downstream consumers (K5's render path, K7's parent UI serializer)
# can name the union directly.
RoleSlotValue = ResolvedToy | GenericDescriptor


def _seed_role_picks(
    template_id: str,
    available_toy_ids: Sequence[str],
    persona_id: str,
    seed: int,
) -> random.Random:
    """Build a :class:`random.Random` seeded on the full input fingerprint.

    The fingerprint is the sha-256 of a canonical string built from
    ``(template_id, sorted(available_toy_ids), persona_id, seed)``. Sorting
    the toy ids inside the fingerprint matches the determinism contract
    documented on :func:`resolve_role_slots` — caller-order on
    ``available_toys`` does NOT change the output.

    A fresh seeded :class:`random.Random` per call keeps the picker
    independent of any caller-supplied generator state; consumers can
    use the function from inside another seeded RNG without worrying
    about consuming draws from the wrong stream.
    """
    canonical = "|".join(
        [
            template_id,
            ",".join(sorted(available_toy_ids)),
            persona_id,
            str(int(seed)),
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    # Use the first 8 bytes of the digest as a 64-bit seed — plenty of
    # entropy for the small numbers of draws the slot-fill engine makes
    # per call (≤ 10 roles in v1).
    return random.Random(int.from_bytes(digest[:8], "big", signed=False))


def _pick_weighted(
    candidates: Sequence[ResolvedToy],
    weight_for_role: float | None,
    rng: random.Random,
) -> ResolvedToy:
    """Pick one toy from ``candidates`` weighted by the role's persona bias.

    Semantics per documentation/phase-k-plan.md §6:

    * ``weight_for_role`` is the persona's relative preference for this
      role (e.g. wizard.json: ``quest_giver: 1.5``).
    * Within a single role slot, the persona's preference for the role
      maps to a bias toward the sorted-first candidate id — the
      "primary" toy. With no per-toy role-affinity data in v1, this is
      the only signal available; the alphabetical tie-break on ``id``
      (per the K4 spec) is the deterministic-first index.
    * Concrete distribution: candidate at sorted-index 0 gets
      ``max(weight_for_role, 1.0)`` mass; every other candidate gets
      ``1.0``. ``random.Random.choices`` normalizes the sum to 1.0 for
      us.
    * When ``weight_for_role`` is ``None`` / ``0`` / negative, the
      distribution collapses to uniform (every candidate gets 1.0).

    ``candidates`` MUST be pre-sorted by ``id`` ASC so the bias
    target is deterministic across calls.

    Caller is responsible for ensuring ``candidates`` is non-empty —
    this helper is a building block, not an entry point.
    """
    if not candidates:
        raise ValueError("_pick_weighted: candidates is empty (caller bug)")
    if len(candidates) == 1:
        # No actual choice — short-circuit so the RNG stream doesn't
        # consume a draw on a one-element pool. The next role's draw
        # is therefore unaffected by the size of the previous role's
        # one-element pool, which keeps tests that compose multiple
        # roles tractable.
        return candidates[0]
    if weight_for_role is None or weight_for_role <= 0:
        return rng.choice(list(candidates))
    # Bias mass: first candidate gets the persona weight (≥1.0), rest
    # get 1.0. Uniform when weight <= 1.0 since both masses become
    # equal. ``random.Random.choices`` normalizes the weights for us.
    primary_mass = max(weight_for_role, 1.0)
    masses: list[float] = [primary_mass] + [1.0] * (len(candidates) - 1)
    picked_list = rng.choices(list(candidates), weights=masses, k=1)
    return picked_list[0]


def resolve_role_slots(
    template: object,
    available_toys: Sequence[ResolvedToy],
    persona: _PersonaLike,
    seed: int,
) -> dict[str, RoleSlotValue] | None:
    """Assign one toy or :class:`GenericDescriptor` per declared template role.

    Phase K Step K4 entry point. Walks the template's ``required_roles``
    then ``optional_roles`` (both sorted by :class:`Role` member value
    for determinism) and assigns each one either:

    * A :class:`ResolvedToy` from ``available_toys`` (no toy is used
      twice within a single call — a toy assigned to ``quest_giver``
      will not also fill ``hero``).
    * A :class:`GenericDescriptor` from
      :data:`toybox.activities.generic_descriptors.GENERIC_DESCRIPTORS`
      when the toy pool is exhausted before every optional role is
      filled. Required roles MUST be filled by a real toy — see the
      eligibility filter below.

    Args:
        template: A :class:`toybox.activities.models.Template` (or any
            object exposing ``id``, ``required_roles``, ``optional_roles``
            attributes — the picker only reads those three). Untyped on
            purpose so the K5 wire-up can pass either the Pydantic model
            or the lightweight generator-side dataclass without an
            import dance.
        available_toys: Pool of toys the catalog resolver returned. Order
            does NOT matter — the picker sorts internally by ``id`` ASC
            for the determinism contract.
        persona: Object exposing ``id`` (str) and ``role_weights``
            (mapping ``{role_name: float}``). See :class:`_PersonaLike`.
        seed: Integer seed; same ``(template_id, sorted(available_toy_ids),
            persona_id, seed)`` MUST produce byte-identical output.

    Returns:
        A dict keyed by :class:`Role` member *value* (e.g. ``"quest_giver"``)
        whose values are either a :class:`ResolvedToy` (real catalog
        pick) or a :class:`GenericDescriptor` (fallback flavor string).
        Returns ``None`` when ``len(required_roles) > len(available_toys)``
        — i.e. the template is not eligible for this dispatch and the
        caller (K5's propose path) should skip it / re-pick. Mirrors the
        ``return None on empty pool`` pattern from
        :func:`toybox.activities.generator._pick_toy_entry`; the
        codebase does not have a dedicated eligibility-exception class
        for this case.

    Backward-compat: when ``template.required_roles`` and
    ``template.optional_roles`` are both empty (the shipping 200
    branching templates), returns an empty dict. The legacy ``{toy}``
    substitution path is unaffected — this is an additive helper for
    K5 to wire in, not a replacement for the existing substitutor.
    """
    template_id = str(getattr(template, "id", ""))
    required_roles: Sequence[object] = tuple(getattr(template, "required_roles", ()) or ())
    optional_roles: Sequence[object] = tuple(getattr(template, "optional_roles", ()) or ())

    # ----- eligibility gate ------------------------------------------------
    # Mirrors the K3 validator's distinct-toy-ceiling reasoning at runtime:
    # a template that NEEDS 3 quest-giver/hero/villain toys cannot run on
    # a 2-toy household. Returning ``None`` lets the caller skip + re-pick.
    # The "return None on empty pool" precedent is
    # ``generator._pick_toy_entry`` — the codebase does not have a
    # dedicated eligibility-exception class for "template doesn't fit".
    if len(required_roles) > len(available_toys):
        return None

    # Build the deterministic RNG once. The fingerprint includes the
    # persona id so two personas with different role_weights produce
    # different casts even at the same seed; the sorted toy-id list is
    # part of the canonical key so caller-order on ``available_toys``
    # never changes the output.
    available_toy_ids = [t.id for t in available_toys]
    rng = _seed_role_picks(template_id, available_toy_ids, persona.id, seed)

    # Process roles in a deterministic order: required first (sorted by
    # role-name value), then optional (also sorted). The plan calls
    # this "deterministic given (template_id, sorted_toys, persona_id,
    # seed)" — sorted role-name iteration is the order in which draws
    # are made, so flipping required-role order in a template's JSON
    # cannot perturb subsequent draws.
    role_weights = persona.role_weights or {}

    # Pool of toys still available (no toy fills two roles in the same
    # cast — mirrors the K3 distinct-toy-ceiling intent). Sorted by
    # ``id`` ASC so the tie-break contract holds: when weights collapse
    # to uniform, ``rng.choice`` over a sorted list is byte-stable.
    remaining_toys: list[ResolvedToy] = sorted(available_toys, key=lambda t: t.id)

    assignments: dict[str, RoleSlotValue] = {}

    # Required: every entry MUST be filled by a real toy. We already
    # gated ``len(required_roles) > len(available_toys)`` above, so the
    # pool size is guaranteed adequate.
    for role in _ordered_roles(required_roles):
        candidate_pool = _filter_pool_for_role(remaining_toys, role, template_id)
        weight = _coerce_weight(role_weights.get(role.value))
        picked = _pick_weighted(candidate_pool, weight, rng)
        assignments[role.value] = picked
        remaining_toys = [t for t in remaining_toys if t.id != picked.id]

    # Optional: fill from the pool while toys remain; once exhausted,
    # fall back to GENERIC_DESCRIPTORS for the rest. GENERIC_DESCRIPTORS
    # has one string per role — alphabetical tie-break is trivially the
    # single value, but the lookup is deterministic regardless.
    for role in _ordered_roles(optional_roles):
        if remaining_toys:
            candidate_pool = _filter_pool_for_role(remaining_toys, role, template_id)
            weight = _coerce_weight(role_weights.get(role.value))
            picked = _pick_weighted(candidate_pool, weight, rng)
            assignments[role.value] = picked
            remaining_toys = [t for t in remaining_toys if t.id != picked.id]
        else:
            descriptor_text = GENERIC_DESCRIPTORS.get(role.value)
            if descriptor_text is None:
                # Defensive: every Role member is keyed in
                # GENERIC_DESCRIPTORS (taxonomy-completeness test in
                # tests/unit/test_roles.py enforces this). A miss here
                # would indicate a future Role added without updating
                # the descriptor table — surface visibly rather than
                # silently dropping the slot.
                _logger.warning(
                    "resolve_role_slots: no GENERIC_DESCRIPTORS entry for role %r; "
                    "skipping optional slot in template %r",
                    role.value,
                    template_id,
                )
                continue
            assignments[role.value] = GenericDescriptor(display_name=descriptor_text)

    return assignments


def _filter_pool_for_role(
    pool: Sequence[ResolvedToy],
    role: Role,
    template_id: str,
) -> list[ResolvedToy]:
    """Filter ``pool`` to toys eligible for ``role`` per ``allowed_roles``.

    A toy is eligible when:

    * Its :attr:`ResolvedToy.allowed_roles` tuple is empty (unrestricted),
      OR
    * The target ``role.value`` is in :attr:`ResolvedToy.allowed_roles`.

    Soft fallback (NOT hard skip): if the filtered pool is empty,
    returns ``pool`` unchanged and logs one info-level message per
    fallback. The filter never causes a role slot to fail when the
    unfiltered pool has candidates — the worst case is "we wanted to
    honour the operator's role restriction but couldn't, so we picked
    from the full pool instead". This matches the per-toy restriction's
    advisory nature: it expresses a preference, not a hard constraint
    that could starve the cast.

    Returns a fresh ``list[ResolvedToy]`` so the caller can drop the
    picked toy without mutating the input.
    """
    if not pool:
        return list(pool)
    filtered = [toy for toy in pool if not toy.allowed_roles or role.value in toy.allowed_roles]
    if not filtered:
        _logger.info(
            "toy role restriction had no candidates for role=%s; "
            "falling back to unrestricted pool (template=%r)",
            role.value,
            template_id,
        )
        return list(pool)
    return filtered


def _ordered_roles(raw_roles: Sequence[object]) -> list[Role]:
    """Coerce a sequence of ``Role``-or-str entries to a sorted ``list[Role]``.

    Templates parsed via Pydantic carry :class:`Role` members directly;
    the lightweight generator-side ``_Template`` dataclass carries the
    raw JSON strings. Both shapes flow into :func:`resolve_role_slots`,
    so the picker normalises here. Unknown values (a hand-built dict
    with a typo) are dropped with a WARNING — the picker doesn't crash
    on stale data, but the operator sees the issue.
    """
    out: list[Role] = []
    for entry in raw_roles:
        if isinstance(entry, Role):
            out.append(entry)
            continue
        if isinstance(entry, str):
            try:
                out.append(Role(entry))
            except ValueError:
                _logger.warning("resolve_role_slots: ignoring unknown role name %r", entry)
                continue
            continue
        _logger.warning(
            "resolve_role_slots: ignoring non-role entry %r (type %s)",
            entry,
            type(entry).__name__,
        )
    # Sorted by the member value (lowercase snake_case) for determinism.
    return sorted(out, key=lambda r: r.value)


def _coerce_weight(raw: object) -> float | None:
    """Best-effort numeric coercion for a persona ``role_weights`` value.

    The persona JSON shape is gated by :class:`RoleWeights` at the load
    boundary, but the picker accepts the raw mapping too (tests, future
    in-memory callers) so it tolerates a non-numeric value by treating
    it as "no preference" (uniform). A negative value is also clamped
    to "no preference" — the picker's contract is 0+, and negative
    weights are meaningless.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        # Python's ``bool`` is a subtype of ``int``; an accidental
        # ``True``/``False`` weight would otherwise coerce to 1.0/0.0
        # which silently bypasses the "no preference" branch.
        return None
    if isinstance(raw, int | float):
        weight = float(raw)
        if weight <= 0:
            return None
        return weight
    return None


# ---------------------------------------------------------------------------
# Phase L Step L3 — reward resolver
# ---------------------------------------------------------------------------
#
# ``resolve_reward`` is the server-side picker the kiosk advance handler
# (L4) calls when the parent-approved reward step fires. The function is
# pure: takes a connection + a :class:`RewardActivityContext` snapshot
# of the activity + a requested type, returns a :class:`ResolvedReward`
# (or ``None`` when no reward is available across the fallback chain).
#
# Algorithm (locked per documentation/phase-l-plan.md §7 L3):
#
# 1. Compute the activity's themes as the UNION of (a) the template's
#    ``recommended_themes`` and (b) themes extracted from the most
#    recent 50 transcripts in this session. Both sources are lowercased
#    + NFKC-normalised (the inputs are already in canonical form: Theme
#    enum values are lowercase ASCII; transcript-extracted themes flow
#    through :func:`topic_extract.extract_themes` which returns Theme
#    members).
#
# 2. When ``requested_type == "random"``, roll among eligible types
#    (picture if any active rewards exist, joke if jokes_enabled +
#    non-empty corpus, song if songs_enabled + non-empty corpus). Roll
#    uniformly across eligible types. The roll uses the deterministic
#    seed below.
#
# 3. Try the chosen (or rolled) type:
#    - picture: SELECT active+non-archived rewards, order by
#      (overlap_count DESC, last_used_at ASC NULLS FIRST, id ASC),
#      pick the head. Empty intersection -> uniform random (seeded).
#    - joke / song: corpus pickers seeded on the same hash, theme=first
#      activity theme (lowest-id Theme member for stability), with a
#      ``theme=None`` fallback when the themed pick returns None.
#
# 4. On empty pool / no result, fall through ``picture -> joke -> song
#    -> None``. The starting type is determined by the requested type;
#    fallback walks the remaining types in chain order, skipping the
#    type we already tried.
#
# Determinism: the seed for every random/uniform pick within one call
# is ``sha256((activity_id, current_step_count))`` so repeated calls
# within one advance return the same result. Tests assert exact
# outcomes given fixed inputs.


@dataclass(frozen=True, slots=True)
class RewardActivityContext:
    """Snapshot of the activity fields :func:`resolve_reward` reads.

    Decouples the resolver from the activity persistence row shape; L4
    builds this from the joined SQL row and passes it in. Keeping the
    shape narrow makes the function trivially unit-testable.

    Fields:

    * ``id`` — the ``activities.id`` PK. Mixed into the deterministic
      seed so two activities pick different rewards at the same step
      count.
    * ``session_id`` — the activity's session, used to scope the
      transcript SQL in :func:`recent_transcript_texts`.
    * ``persona_id`` — passed through to :func:`pick_joke` /
      :func:`pick_song`'s persona-compat filter. ``None`` means "no
      persona constraint" (matches the corpus picker's contract).
    * ``slot_fills_json`` — the raw ``activities.slot_fills_json`` TEXT
      column value. The resolver decodes it to look up the reserved
      ``"__template_id"`` key (L4 writes this on approve). When the key
      is absent the template-themes source contributes nothing — the
      resolver falls through to transcript-only theme extraction.
    * ``current_step_count`` — the step seq the reward fires after.
      Mixed into the deterministic seed so a hypothetical re-advance
      at a different step count picks a different reward (a stable
      contract tests can assert).
    """

    id: str
    session_id: str
    persona_id: str | None
    slot_fills_json: str | None
    current_step_count: int


@dataclass(frozen=True, slots=True)
class ResolvedReward:
    """The picked reward in a uniform shape for the kiosk wire envelope.

    One dataclass for all three kinds so the L4 handler can return it
    verbatim. Per documentation/phase-l-plan.md §8 the wire shape is:

    * ``kind`` — the type discriminator (also the L4 step row's
      ``kind`` column value).
    * ``reward_id`` — the catalog entry id (``rewards.id`` /
      ``Joke.id`` / ``Song.id``). The kiosk uses it for logging and
      the parent UI uses it for cross-reference.
    * ``image_url`` — set for ``kind="picture"``, ``None`` otherwise.
    * ``animation`` — set for ``kind="picture"``, ``None`` otherwise.
    * ``audio_url`` — set for ``kind="song"``, ``None`` otherwise.
    * ``body`` — display text. For ``picture`` this is the display
      name; for ``joke`` this duplicates the punchline so the kiosk
      can render uniformly; for ``song`` this is the title.
    * ``setup`` / ``punchline`` — set for ``kind="joke"``, ``None``
      otherwise. Duplicating ``punchline`` into ``body`` keeps the
      uniform-shape contract while preserving the structured fields.
    """

    kind: Literal["picture", "joke", "song"]
    reward_id: str
    image_url: str | None
    animation: Animation | None
    audio_url: str | None
    body: str
    setup: str | None
    punchline: str | None


# Public type alias for the requested-type wire string. The resolver
# accepts the four-member Literal from :data:`toybox.activities.models.RewardType`
# but exposes it locally so callers don't have to thread the import.
_RewardTypeRequest = Literal["picture", "joke", "song", "random"]

# Reserved key the L4 approve handler writes into ``slot_fills_json``
# to carry the template id through to the resolver. Reads tolerate its
# absence gracefully.
_TEMPLATE_ID_KEY: Final[str] = "__template_id"

# Default cap on transcripts pulled per resolve call. Mirrors the
# ``LIMIT 50`` in ``ai/tools.py:404`` (the producer this duplicates by
# design — see :func:`recent_transcript_texts` docstring).
_TRANSCRIPT_LIMIT: Final[int] = 50


def recent_transcript_texts(
    conn: sqlite3.Connection,
    session_id: str,
    limit: int = _TRANSCRIPT_LIMIT,
) -> list[str]:
    """Return up to ``limit`` recent non-null transcript bodies for ``session_id``.

    Duplicates the 5-line SQL from
    :mod:`toybox.ai.tools` (the ``_resolve_recent_transcripts`` body).
    Dependency direction is one-way (``api`` / advance handler →
    ``content_resolver``; ``content_resolver`` does NOT import from
    ``ai/tools``), so the duplication is deliberate per
    code-quality.md §1.

    Ordered by ``ended_at DESC`` (most-recent first) which matches both
    the producer and the consumer side's "show me what was just said"
    intent. ``ended_at`` is the canonical recency timestamp because the
    transcript row writes finalize when the utterance ends.
    """
    rows = conn.execute(
        "SELECT text FROM transcripts "
        "WHERE session_id = ? AND text IS NOT NULL "
        "ORDER BY ended_at DESC LIMIT ?",
        (session_id, int(limit)),
    ).fetchall()
    return [str(row["text"]) for row in rows]


def _decode_template_id(slot_fills_json: str | None) -> str | None:
    """Extract the reserved ``__template_id`` key from ``slot_fills_json``.

    L4 writes ``slot_fills["__template_id"] = activity.template_id`` at
    approve time so the resolver can find the template's
    ``recommended_themes`` at advance time. Pre-L4 rows do not carry
    the key — return ``None`` and let the caller fall through to the
    transcript-only theme source.

    Tolerant of malformed JSON / non-dict payloads / non-string values
    (logs a WARNING and returns ``None``) to mirror the defensive
    decoding pattern in :mod:`toybox.api.activities`.
    """
    if not slot_fills_json:
        return None
    try:
        decoded = json.loads(slot_fills_json)
    except json.JSONDecodeError:
        _logger.warning(
            "reward resolver: slot_fills_json malformed; ignoring template-themes source",
        )
        return None
    if not isinstance(decoded, dict):
        return None
    raw = decoded.get(_TEMPLATE_ID_KEY)
    if isinstance(raw, str) and raw:
        return raw
    return None


def _template_recommended_themes(template_id: str | None) -> list[str]:
    """Look up the template and return its ``recommended_themes`` as strings.

    Returns an empty list when:

    * ``template_id`` is ``None`` (pre-L4 activity row).
    * The template isn't currently loaded (renamed / removed between
      approve and advance).

    Imported lazily to avoid a module-load-time cycle:
    ``activities.generator`` already imports indirectly from
    ``content_resolver`` (via :func:`resolve_toys`); importing at
    module scope would form a graph cycle. The lazy import is paid
    once per resolve call.
    """
    if not template_id:
        return []
    from .generator import find_template_by_id  # noqa: PLC0415

    template = find_template_by_id(template_id)
    if template is None:
        return []
    # ``_Template.recommended_themes`` is ``tuple[Theme, ...]``; Theme
    # is a StrEnum so the .value is already lowercased ASCII. We expose
    # plain strings here so the intersection with reward.tags (also
    # strings) is straightforward.
    return [theme.value for theme in template.recommended_themes]


def _compute_activity_themes(
    conn: sqlite3.Connection,
    ctx: RewardActivityContext,
) -> list[str]:
    """Union of (template recommended_themes) ∪ (extracted transcript themes).

    Both sources contribute lowercased + NFKC-canonical strings:

    * Template themes: :class:`Theme` enum values are lowercase ASCII
      (NFKC is a no-op on ASCII), so no extra normalisation needed.
    * Transcript themes: :func:`topic_extract.extract_themes` returns
      :class:`Theme` members; same canonical-form guarantee.

    Order: union preserves first-seen across the concatenated source
    order (template first, then transcripts). The downstream picker
    sorts deterministically anyway, so order here is informational
    only — :func:`_first_theme_for_corpus` re-sorts by Theme-enum
    declaration order before picking, so the union-input order doesn't
    affect the corpus-picker's theme choice.
    """
    template_id = _decode_template_id(ctx.slot_fills_json)
    template_themes = _template_recommended_themes(template_id)
    texts = recent_transcript_texts(conn, ctx.session_id)
    transcript_themes = [theme.value for theme in extract_themes(texts)]
    seen: set[str] = set()
    out: list[str] = []
    for theme in (*template_themes, *transcript_themes):
        if theme in seen:
            continue
        seen.add(theme)
        out.append(theme)
    return out


def _seed_for_activity(activity_id: str, current_step_count: int) -> int:
    """Deterministic 64-bit seed derived from ``(activity_id, step_count)``.

    Per phase-l-plan §7 L3: ``sha256((activity_id, current_step_count))``.
    The 64-bit truncation matches :func:`_seed_role_picks` precedent —
    plenty of entropy for the small number of picks the resolver makes
    per call.
    """
    canonical = f"{activity_id}|{int(current_step_count)}"
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _seeded_rng(seed: int) -> random.Random:
    """Build a :class:`random.Random` from the canonical seed.

    Used for the ``random`` requested-type uniform roll across eligible
    types AND for the empty-intersection uniform fallback over the
    picture pool. Each pick path uses a fresh :class:`random.Random`
    instance seeded from ``(activity_id, step_count)``, so they're
    independently deterministic — the type-roll and the picture-pool
    uniform pick don't share RNG state.
    """
    return random.Random(seed)


def _theme_overlap_count(reward_tags: list[str], activity_themes: list[str]) -> int:
    """Set-intersection cardinality of reward tags vs. activity themes.

    Both inputs are already lowercased + NFKC-canonical (rewards via
    the L2 write-time normaliser; activity themes via the StrEnum
    guarantee + transcript extractor's Theme output).
    """
    if not reward_tags or not activity_themes:
        return 0
    return len(set(reward_tags) & set(activity_themes))


def _load_active_rewards(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Return all eligible reward rows as plain dicts (active + non-archived).

    Dict (not :class:`sqlite3.Row`) because the picker sorts the list in
    Python with a multi-key tuple — Row's column-by-name access still
    works but converting once keeps the sort key tuple readable.
    """
    rows = conn.execute(
        "SELECT id, display_name, image_path, animation, tags, last_used_at "
        "FROM rewards WHERE active = 1 AND archived = 0"
    ).fetchall()
    out: list[dict[str, object]] = []
    for row in rows:
        # Decode tags inline so the picker doesn't depend on the
        # rewards API module (one-way dependency: api → resolver).
        raw_tags = row["tags"]
        tags: list[str] = []
        if isinstance(raw_tags, str) and raw_tags.strip():
            try:
                decoded = json.loads(raw_tags)
            except json.JSONDecodeError:
                _logger.warning(
                    "reward resolver: rewards.tags malformed for id=%r; treating as empty",
                    row["id"],
                )
                decoded = []
            if isinstance(decoded, list):
                tags = [entry for entry in decoded if isinstance(entry, str)]
        out.append(
            {
                "id": str(row["id"]),
                "display_name": str(row["display_name"]),
                "image_path": str(row["image_path"]),
                "animation": str(row["animation"]),
                "tags": tags,
                "last_used_at": row["last_used_at"],
            }
        )
    return out


def _image_url_from_image_path(image_path: str) -> str:
    """Derive the static-mount URL from a stored ``rewards.image_path``.

    Stored values follow ``data/images/rewards/<filename>`` (see
    :func:`toybox.storage.images.relative_committed_path`). The static
    mount in :mod:`toybox.app` exposes ``data/images/`` at
    ``/api/static/images/``, so the URL is the stored path with the
    ``data/images/`` prefix swapped for ``/api/static/images/``.

    Defensive against legacy / hand-edited rows whose ``image_path``
    does NOT carry the expected prefix: returns the value as-is with a
    WARNING. The kiosk falls back to a placeholder for missing files,
    so a malformed URL is still preferred over crashing the advance.
    """
    expected_prefix = "data/images/"
    normalised = image_path.replace("\\", "/")
    if normalised.startswith(expected_prefix):
        return "/api/static/images/" + normalised[len(expected_prefix) :]
    _logger.warning(
        "reward resolver: image_path %r does not start with %r; returning as-is",
        image_path,
        expected_prefix,
    )
    return image_path


def _pick_picture(
    rewards: list[dict[str, object]],
    activity_themes: list[str],
    rng: random.Random,
) -> ResolvedReward | None:
    """Pick one picture reward from ``rewards`` per the L3 sort rules.

    Empty input → ``None`` (caller falls through to the next type).

    Sort keys (per phase-l-plan §7 L3):

    1. overlap_count DESC
    2. last_used_at ASC NULLS FIRST
    3. id ASC (deterministic tiebreak)

    Empty-intersection special case (every reward has overlap_count =
    0): the plan says "Empty intersection falls back to uniform random
    over the type pool." Implemented as ``rng.choice`` over the
    overlap=0 cohort. Mixed pools (some rewards overlap, some don't)
    fall through to the primary sort — the overlap-DESC key surfaces
    the overlapping rewards above the non-overlapping ones.
    """
    if not rewards:
        return None
    # Single pass: compute overlap counts and find the max.
    enriched = [
        (_theme_overlap_count(cast("list[str]", r["tags"]), activity_themes), r) for r in rewards
    ]
    max_overlap = max(count for count, _ in enriched)
    if max_overlap == 0:
        # No theme intersects any reward — uniform random over the
        # whole pool (seeded). Sort by id ASC first so the seeded pick
        # is byte-stable across DB row-order changes.
        sorted_pool = sorted(rewards, key=lambda r: str(r["id"]))
        picked = rng.choice(sorted_pool)
        return _reward_row_to_resolved(picked)
    # Primary sort path. ``last_used_at IS NULL`` → 0 (NULLS FIRST) so
    # the never-used reward wins the recency tiebreak.
    sorted_rewards = sorted(
        enriched,
        key=lambda entry: (
            -entry[0],  # overlap_count DESC
            entry[1]["last_used_at"] is not None,  # False (NULL) sorts first
            str(entry[1]["last_used_at"] or ""),  # last_used_at ASC
            str(entry[1]["id"]),  # id ASC
        ),
    )
    return _reward_row_to_resolved(sorted_rewards[0][1])


def _reward_row_to_resolved(row: dict[str, object]) -> ResolvedReward:
    """Coerce a reward dict-row into a :class:`ResolvedReward` for ``kind="picture"``."""
    raw_animation = str(row["animation"])
    try:
        animation = Animation(raw_animation)
    except ValueError:
        # Defense-in-depth: a hand-edited row with a stale animation
        # value would otherwise blow up the wire shape. Fall back to
        # the first enum member (matches the rewards API decoder).
        _logger.warning(
            "reward resolver: rewards.animation %r is not a valid Animation; "
            "falling back to first enum member",
            raw_animation,
        )
        animation = next(iter(Animation))
    return ResolvedReward(
        kind="picture",
        reward_id=str(row["id"]),
        image_url=_image_url_from_image_path(str(row["image_path"])),
        animation=animation,
        audio_url=None,
        body=str(row["display_name"]),
        setup=None,
        punchline=None,
    )


def _first_theme_for_corpus(activity_themes: list[str]) -> Theme | None:
    """Pick a deterministic activity theme to seed the corpus picker.

    Policy: lowest-by-id Theme member among the activity themes (the
    StrEnum's declared order is the canonical ordering used elsewhere
    in :mod:`toybox.activities.topic_extract`). Returns ``None`` when
    no activity theme maps to a Theme member — the caller then passes
    ``theme=None`` to the picker, which broadens the candidate pool to
    every entry.

    "Lowest-by-id" is the deterministic policy documented in the L3
    plan: tests can assert exact picks given fixed inputs without
    having to reason about transcript order or template-author intent.
    """
    if not activity_themes:
        return None
    theme_order = {t.value: i for i, t in enumerate(Theme)}
    valid = [t for t in activity_themes if t in theme_order]
    if not valid:
        return None
    valid.sort(key=lambda t: theme_order[t])
    # Coerce back to Theme. ``Theme(value)`` is guaranteed to succeed
    # because ``valid`` was filtered against ``theme_order`` keys.
    return Theme(valid[0])


def _try_pick_joke(
    seed: int,
    persona_id: str | None,
    activity_themes: list[str],
) -> ResolvedReward | None:
    """Pick one joke; theme-first, untheme fallback. Returns ``None`` on miss.

    Per phase-l-plan §7 L3: "Try with one of the activity themes first
    (lowest-id theme...). If returns None, fall back to ``theme=None``."

    Corpus-load failures during the pick (the picker eagerly loads the
    corpus on first call) are caught and treated as a missed pick so
    the resolver's fallback chain proceeds. A malformed bundle is a
    packaging error, not a crash-the-advance-handler moment.
    """
    theme = _first_theme_for_corpus(activity_themes)
    try:
        joke = pick_joke(seed, persona_id=persona_id, theme=theme) if theme is not None else None
        if joke is None:
            joke = pick_joke(seed, persona_id=persona_id, theme=None)
    except (ValueError, OSError) as exc:
        _logger.warning("reward resolver: pick_joke failed: %s", exc)
        return None
    if joke is None:
        return None
    return ResolvedReward(
        kind="joke",
        reward_id=joke.id,
        image_url=None,
        animation=None,
        audio_url=None,
        body=joke.punchline,
        setup=joke.setup,
        punchline=joke.punchline,
    )


def _try_pick_song(
    seed: int,
    persona_id: str | None,
    activity_themes: list[str],
) -> ResolvedReward | None:
    """Pick one song with audio present; theme-first, untheme fallback.

    Corpus-load / audio-probe failures during the pick are caught and
    treated as a missed pick so the resolver's fallback chain proceeds.
    See :func:`_try_pick_joke` for the same rationale.
    """
    theme = _first_theme_for_corpus(activity_themes)
    try:
        song = (
            pick_song(seed, persona_id=persona_id, theme=theme, require_audio=True)
            if theme is not None
            else None
        )
        if song is None:
            song = pick_song(seed, persona_id=persona_id, theme=None, require_audio=True)
    except (ValueError, OSError) as exc:
        _logger.warning("reward resolver: pick_song failed: %s", exc)
        return None
    if song is None:
        return None
    return ResolvedReward(
        kind="song",
        reward_id=song.id,
        image_url=None,
        animation=None,
        audio_url=f"/api/static/songs/audio/{song.id}.mp3",
        body=song.title,
        setup=None,
        punchline=None,
    )


# Canonical fallback chain (per phase-l-plan §7 L3): picture → joke →
# song → None. The starting point is set by the requested type and the
# fallback walks the remaining types in this order, skipping any type
# already tried in the same call.
_FALLBACK_ORDER: Final[tuple[Literal["picture", "joke", "song"], ...]] = (
    "picture",
    "joke",
    "song",
)


def _type_is_eligible(
    conn: sqlite3.Connection,
    kind: Literal["picture", "joke", "song"],
    rewards: list[dict[str, object]],
) -> bool:
    """Per-type eligibility gate, applied on EVERY type-try.

    Unified entry point for the eligibility logic (per the L3 plan §1.3
    "If the chosen reward type has nothing eligible to fire ... fall
    through ``picture → joke → song → no reward``"). Used both when the
    ``random`` requested type rolls among eligible types and when the
    fallback chain steps into an explicit/fallback type — the
    ``jokes_enabled`` / ``songs_enabled`` flags MUST hard-gate the
    explicit-type and fallback paths, not just random-roll eligibility.

    Decisions per kind:

    * ``picture`` — at least one active+non-archived reward row exists
      (the caller passes the already-loaded ``rewards`` list so we
      don't re-query).
    * ``joke`` — household flag ``jokes_enabled = true`` AND the joke
      corpus is non-empty. Corpus load failures (malformed bundle)
      treat the type as ineligible and log a WARNING.
    * ``song`` — household flag ``songs_enabled = true`` AND the song
      corpus is non-empty. Corpus load failures treat the type as
      ineligible.

    Note (random-distribution skew, intentional): when audio MP3s have
    NOT yet been generated (fresh dev install pre-``generate_song_corpus.py``),
    the song corpus is non-empty but every per-song ``require_audio``
    check at pick time returns ``None``. The ``random`` roll still
    treats ``song`` as eligible — so it gets rolled, the pick returns
    None, and the fallback chain salvages with a picture/joke. The
    operator-visible behaviour is correct (salvage works); only the
    random distribution is skewed (song's share is wasted on the
    fallback). A v2 nice-to-have: filter ``song`` out of the random
    pool by audio-availability so the distribution stays uniform.
    """
    # Corpus emptiness is a packaging-time concern (the bundled corpora
    # ship validated). Use the loaders' tuple length to handle the rare
    # test fixture that points TOYBOX_DATA_DIR at an empty corpus.
    from .joke_corpus import load_jokes  # noqa: PLC0415
    from .song_corpus import load_songs  # noqa: PLC0415

    if kind == "picture":
        return bool(rewards)
    if kind == "joke":
        if not _jokes_enabled.get(conn):
            return False
        try:
            return len(load_jokes()) > 0
        except (ValueError, OSError) as exc:
            _logger.warning("reward resolver: jokes corpus load failed: %s", exc)
            return False
    if kind == "song":
        if not _songs_enabled.get(conn):
            return False
        try:
            return len(load_songs()) > 0
        except (ValueError, OSError) as exc:
            _logger.warning("reward resolver: songs corpus load failed: %s", exc)
            return False
    return False


def _eligible_types(
    conn: sqlite3.Connection,
    rewards: list[dict[str, object]],
) -> list[Literal["picture", "joke", "song"]]:
    """Compute the eligible reward types for the ``random`` roll.

    Thin wrapper over :func:`_type_is_eligible` — calls the per-type
    gate for each of the three kinds and collects the ones that pass.
    """
    return [k for k in _FALLBACK_ORDER if _type_is_eligible(conn, k, rewards)]


def resolve_reward(
    conn: sqlite3.Connection,
    activity: RewardActivityContext,
    requested_type: _RewardTypeRequest,
) -> ResolvedReward | None:
    """Pick the reward for one activity-advance.

    Returns ``None`` when no type yields a result. See module
    docstring + the L3 plan section for the algorithm.

    ``requested_type`` may be ``"random"`` to roll among eligible types
    or one of the three concrete types; either way the function walks
    the fallback chain ``picture → joke → song`` when the starting
    type yields no result.

    The function is deterministic given a fixed ``(activity.id,
    activity.current_step_count)`` — repeated calls within one advance
    return the same :class:`ResolvedReward`. The contract is what L4
    relies on for idempotency.
    """
    activity_themes = _compute_activity_themes(conn, activity)
    rewards = _load_active_rewards(conn)
    seed = _seed_for_activity(activity.id, activity.current_step_count)

    # Determine starting type.
    if requested_type == "random":
        eligible = _eligible_types(conn, rewards)
        if not eligible:
            return None
        rng_for_type = _seeded_rng(seed)
        starting_type: Literal["picture", "joke", "song"] = rng_for_type.choice(eligible)
    else:
        starting_type = requested_type

    # Build the ordered try-chain starting from ``starting_type``, then
    # walking the canonical fallback chain skipping the starting type.
    try_order: list[Literal["picture", "joke", "song"]] = [starting_type]
    for kind in _FALLBACK_ORDER:
        if kind != starting_type:
            try_order.append(kind)

    rng_for_picture = _seeded_rng(seed)
    for kind in try_order:
        # Per-type gate: ``jokes_enabled`` / ``songs_enabled`` flags
        # MUST hard-gate the explicit-type and fallback paths, not just
        # the ``random`` roll. If the flag for the type is off (or the
        # corpus failed to load), skip the type entirely and let the
        # fallback chain proceed to the next eligible type.
        if not _type_is_eligible(conn, kind, rewards):
            continue
        if kind == "picture":
            picked = _pick_picture(rewards, activity_themes, rng_for_picture)
            if picked is not None:
                return picked
        elif kind == "joke":
            picked = _try_pick_joke(seed, activity.persona_id, activity_themes)
            if picked is not None:
                return picked
        elif kind == "song":
            picked = _try_pick_song(seed, activity.persona_id, activity_themes)
            if picked is not None:
                return picked
    return None


__all__ = [
    "DEFAULT_ROOMS_LIMIT",
    "DEFAULT_TOYS_LIMIT",
    "ROOMS_LIMIT_ENV",
    "SAFE_DEFAULT_TEMPLATE",
    "TOYS_LIMIT_ENV",
    "ChildProfileRow",
    "GenericDescriptor",
    "ReadingLevel",
    "ResolvedChildren",
    "ResolvedReward",
    "ResolvedRoom",
    "ResolvedToy",
    "RewardActivityContext",
    "RoleSlotValue",
    "SafeDefaultTemplate",
    "aggregate_child_constraints",
    "apply_banned_themes_filter",
    "build_claude_directive",
    "recent_transcript_texts",
    "resolve_child_profiles",
    "resolve_reward",
    "resolve_role_slots",
    "resolve_rooms",
    "resolve_toys",
]
