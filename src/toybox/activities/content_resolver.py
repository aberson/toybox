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

from ..core.banned_themes import current_banned_themes_global
from .generic_descriptors import GENERIC_DESCRIPTORS
from .roles import Role

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
    "ResolvedRoom",
    "ResolvedToy",
    "RoleSlotValue",
    "SafeDefaultTemplate",
    "aggregate_child_constraints",
    "apply_banned_themes_filter",
    "build_claude_directive",
    "resolve_child_profiles",
    "resolve_role_slots",
    "resolve_rooms",
    "resolve_toys",
]
