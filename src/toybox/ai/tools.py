"""Tool registry for the Phase E loop-mode generator.

Each tool is a typed read-only callable returning JSON-serializable data.
Adapters (Claude or local) call tools via the uniform :func:`call_tool`
entry point, which validates args via Pydantic schemas BEFORE dispatch
and runs the tool inside an :func:`asyncio.timeout` capped by
``TOYBOX_TOOL_LOOP_TIMEOUT_SEC`` (default 30 s).

Validation / timeout / DB failures NEVER raise out of :func:`call_tool` —
they return a structured recovery error::

    {"error": "invalid_args", "tool": "get_room",
     "reason": "room_id must be a UUIDv4 string"}

So the model-loop adapter can feed the error back to the model and ask
it to retry with corrected args. See
``documentation/phase-e-plan.md`` §"Tool-call telemetry shape" for the
companion telemetry shape that is recorded on the labeled_events row.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..activities.content_resolver import (
    ResolvedRoom,
    ResolvedToy,
    resolve_rooms,
    resolve_toys,
)
from ..activities.feedback import compute_signature, fetch_counts

_logger = logging.getLogger(__name__)

# Aggregate tool-loop timeout. Caps both the per-tool resolver execution
# AND (callers may also use it as the wall-clock bound on a full
# generation's worth of tool calls). Default 30 s matches the judge
# timeout — short enough that a stuck DB read doesn't pile up forever,
# long enough that a real query against a few hundred rows comfortably
# completes.
TOOL_LOOP_TIMEOUT_ENV: Final[str] = "TOYBOX_TOOL_LOOP_TIMEOUT_SEC"
DEFAULT_TOOL_LOOP_TIMEOUT_SEC: Final[float] = 30.0

# Slug regex for library persona ids. Matches the existing library
# convention (`wizard`, `princess_lyra`) — short lowercase identifiers
# starting with a letter, with letters/digits/underscores/hyphens.
_LIBRARY_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Bounded length on free-form string args so a model that emits a 100k
# char "args" doesn't blow up the validator or the labeled_events row.
_STR_FIELD_MAX: Final[int] = 256

# Recency / windowing args. Callers may legitimately ask for "last 5
# minutes" (300 s) up to "last hour" (3600 s); narrower than 1 second
# is meaningless.
_WINDOW_MIN: Final[int] = 1
_WINDOW_MAX: Final[int] = 3600

# Default cap on inventory results. Mirrors content_resolver.DEFAULT_TOYS_LIMIT
# but stays local so a future split between "loop tool cap" and "single-shot
# cap" doesn't require co-changing both files.
DEFAULT_INVENTORY_LIMIT: Final[int] = 12

# Result-summary cap. Stored on labeled_events.tool_calls; longer
# summaries inflate the row without giving the SFT exporter
# proportionally more signal.
RESULT_SUMMARY_MAX: Final[int] = 200


def tool_loop_timeout_sec() -> float:
    """Read ``TOYBOX_TOOL_LOOP_TIMEOUT_SEC`` from env, with default + clamp.

    Malformed values (non-float, non-positive) emit a WARNING and fall
    back to :data:`DEFAULT_TOOL_LOOP_TIMEOUT_SEC` — a stuck loop with a
    zero or negative timeout would either short-circuit every call or
    deadlock waiting for an immediate cancel.
    """
    raw = os.environ.get(TOOL_LOOP_TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_TOOL_LOOP_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not a float; using default %.1f",
            TOOL_LOOP_TIMEOUT_ENV,
            raw,
            DEFAULT_TOOL_LOOP_TIMEOUT_SEC,
        )
        return DEFAULT_TOOL_LOOP_TIMEOUT_SEC
    if value <= 0:
        _logger.warning(
            "%s=%r is non-positive; using default %.1f",
            TOOL_LOOP_TIMEOUT_ENV,
            raw,
            DEFAULT_TOOL_LOOP_TIMEOUT_SEC,
        )
        return DEFAULT_TOOL_LOOP_TIMEOUT_SEC
    return value


# --------------------------------------------------------------------- ctx

ConnectionFactory = Callable[[], sqlite3.Connection]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Carrier for the dependencies a tool resolver needs.

    Attributes:
        connection_factory: Returns a fresh SQLite connection. The
            resolver opens its own short-lived connection so the loop
            doesn't share state across cancellation boundaries — a
            cancelled :func:`call_tool` reliably closes its connection
            via the ``finally`` block in :func:`_dispatch`.
        activity_id: Current activity-id (UUID string), threaded through
            for ``get_prior_steps`` when the model omits it.
        child_id: Current child-id (UUID string), used as the default
            for ``get_inventory``.
        session_id: Current session-id, used as the default scope for
            ``get_recent_transcript``.
    """

    connection_factory: ConnectionFactory
    activity_id: str | None = None
    child_id: str | None = None
    session_id: str | None = None


# --------------------------------------------------------------------- args

# We use string-with-validator (rather than ``pydantic.UUID4``) so the
# error reason mentions UUID explicitly — Pydantic's UUID type produces
# a generic "Input should be a valid UUID" reason that doesn't help a
# model debug "you sent a path traversal".


def _validate_uuid_str(value: str, *, field: str) -> str:
    """Coerce ``value`` to a valid UUID string or raise ValueError.

    Used inside Pydantic field validators so failures surface as
    structured Pydantic errors that :func:`call_tool` then reshapes.
    """
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{field} must be a UUIDv4 string") from exc
    return value


def _validate_persona_id(value: str) -> str:
    """Accept either a UUIDv4 string OR a library slug.

    Library personas (e.g. ``wizard``) are referenced by slug rather
    than UUID — see ``src/toybox/personas/`` library loader. The dual
    acceptance is documented on :class:`GetPersonaArgs`.
    """
    try:
        UUID(value)
        return value
    except (ValueError, AttributeError, TypeError):
        pass
    if _LIBRARY_SLUG_RE.match(value):
        return value
    raise ValueError(
        "persona_id must be a UUIDv4 string or a library slug "
        "(lowercase letters, digits, underscore or hyphen, "
        "starting with a letter, max 64 chars)"
    )


_BoundedStr = Annotated[str, Field(min_length=1, max_length=_STR_FIELD_MAX)]
_BoundedWindowInt = Annotated[int, Field(ge=_WINDOW_MIN, le=_WINDOW_MAX)]


class _ArgsBase(BaseModel):
    """Common config — strict types, no extra fields, no mutation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class GetPersonaArgs(_ArgsBase):
    persona_id: _BoundedStr

    @field_validator("persona_id")
    @classmethod
    def _check_persona_id(cls, v: str) -> str:
        return _validate_persona_id(v)


class GetRoomArgs(_ArgsBase):
    room_id: _BoundedStr

    @field_validator("room_id")
    @classmethod
    def _check_room_id(cls, v: str) -> str:
        return _validate_uuid_str(v, field="room_id")


class GetInventoryArgs(_ArgsBase):
    """Args for ``get_inventory``.

    ``recency_window`` was previously declared + validated but never
    consumed by the resolver (``resolve_toys`` already orders by
    recency). The unused field has been dropped — keeping a validated-
    then-ignored arg encourages model misuse and inflates the args
    surface in telemetry. If a future caller actually wants a
    wall-clock filter, add a real ``window_sec`` field here AND wire
    it into ``_resolve_inventory``.
    """

    child_id: _BoundedStr

    @field_validator("child_id")
    @classmethod
    def _check_child_id(cls, v: str) -> str:
        return _validate_uuid_str(v, field="child_id")


class GetRecentTranscriptArgs(_ArgsBase):
    window_sec: _BoundedWindowInt = 300


class GetPriorStepsArgs(_ArgsBase):
    activity_id: _BoundedStr

    @field_validator("activity_id")
    @classmethod
    def _check_activity_id(cls, v: str) -> str:
        return _validate_uuid_str(v, field="activity_id")


class GetAntiSignalArgs(_ArgsBase):
    template_id: _BoundedStr
    slot_dict: dict[str, _BoundedStr] = Field(default_factory=dict)

    @field_validator("slot_dict")
    @classmethod
    def _check_slot_dict_size(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 32:
            raise ValueError("slot_dict has too many entries (max 32)")
        for key in v:
            if len(key) > _STR_FIELD_MAX:
                raise ValueError(f"slot_dict key length must be <= {_STR_FIELD_MAX} chars")
        return v


# --------------------------------------------------------------------- result


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Normalised resolver output.

    ``data`` is the JSON-serialisable payload returned to the model.
    ``summary`` is the short human-readable string captured into
    ``labeled_events.tool_calls.result_summary`` — bounded at
    :data:`RESULT_SUMMARY_MAX` chars.
    """

    data: Any
    summary: str


# --------------------------------------------------------------------- resolvers


def _truncate(text: str, *, limit: int = RESULT_SUMMARY_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _resolve_persona(conn: sqlite3.Connection, persona_id: str) -> ToolResult:
    row = conn.execute(
        "SELECT id, display_name, archetype, system_prompt, behavior_tags, "
        "       age_range_min, age_range_max, default_voice_tone "
        "FROM personas WHERE id = ?",
        (persona_id,),
    ).fetchone()
    if row is None:
        return ToolResult(
            data=None,
            summary=f"persona not found: {persona_id}",
        )
    age_range: list[int] | None = None
    if row["age_range_min"] is not None and row["age_range_max"] is not None:
        age_range = [int(row["age_range_min"]), int(row["age_range_max"])]
    behavior_tags: list[str] = []
    raw_tags = row["behavior_tags"]
    if raw_tags:
        behavior_tags = [t.strip() for t in str(raw_tags).split(",") if t.strip()]
    data = {
        "id": str(row["id"]),
        "display_name": str(row["display_name"]),
        "archetype": row["archetype"],
        "system_prompt": str(row["system_prompt"]),
        "behavior_tags": behavior_tags,
        "age_range": age_range,
        "default_voice_tone": row["default_voice_tone"],
    }
    summary = _truncate(f"{data['display_name']} ({data['archetype'] or 'no archetype'})")
    return ToolResult(data=data, summary=summary)


def _resolve_room(conn: sqlite3.Connection, room_id: str) -> ToolResult:
    rooms = resolve_rooms(conn, limit=10_000)
    found: ResolvedRoom | None = next((r for r in rooms if r.id == room_id), None)
    if found is None:
        # Fall through to direct lookup so callers don't pay the
        # full-list cost when the id is known-good but the row is older
        # than the resolver's display-name filter.
        row = conn.execute(
            "SELECT id, display_name, image_path FROM rooms WHERE id = ?",
            (room_id,),
        ).fetchone()
        if row is None:
            return ToolResult(data=None, summary=f"room not found: {room_id}")
        feature_rows = conn.execute(
            "SELECT name FROM room_features WHERE room_id = ? ORDER BY name COLLATE NOCASE ASC",
            (room_id,),
        ).fetchall()
        features = [str(f["name"]) for f in feature_rows if f["name"]]
        data = {
            "id": str(row["id"]),
            "name": row["display_name"],
            "features": features,
            "image_path": row["image_path"],
        }
        summary = _truncate(
            f"{row['display_name'] or '(no name)'} -- features: "
            f"{', '.join(features[:5]) if features else 'none'}"
        )
        return ToolResult(data=data, summary=summary)

    image_row = conn.execute(
        "SELECT image_path FROM rooms WHERE id = ?",
        (room_id,),
    ).fetchone()
    image_path = image_row["image_path"] if image_row is not None else None
    data = {
        "id": found.id,
        "name": found.display_name,
        "features": list(found.features),
        "image_path": image_path,
    }
    summary = _truncate(
        f"{found.display_name} -- features: "
        f"{', '.join(found.features[:5]) if found.features else 'none'}"
    )
    return ToolResult(data=data, summary=summary)


def _resolve_inventory(conn: sqlite3.Connection, child_id: str) -> ToolResult:
    toys: list[ResolvedToy] = resolve_toys(conn, limit=DEFAULT_INVENTORY_LIMIT)
    data = [
        {
            "id": t.id,
            "display_name": t.display_name,
            "tags": list(t.tags),
            "persona_id": t.persona_id,
            "last_used_at": t.last_used_at,
        }
        for t in toys
    ]
    if not toys:
        summary = f"no toys for child {child_id}"
    else:
        summary = _truncate(
            f"{len(toys)} toys (most recent: {', '.join(t.display_name for t in toys[:3])})"
        )
    return ToolResult(data=data, summary=summary)


def _resolve_recent_transcript(
    conn: sqlite3.Connection, window_sec: int, session_id: str | None
) -> ToolResult:
    # The recency window is interpreted as "last N transcripts ordered
    # by ended_at" rather than a wall-clock filter — wall-clock filters
    # would require a sqlite-side time function and a stable test seam,
    # and the model only needs the most recent text snippets either way.
    # ``window_sec`` caps the row count loosely (one snippet per ~5 s
    # is a reasonable upper bound on naturalistic speech).
    cap = max(1, min(20, window_sec // 5 or 1))
    if session_id is not None:
        rows = conn.execute(
            "SELECT text FROM transcripts WHERE session_id = ? AND text IS NOT NULL "
            "ORDER BY ended_at DESC LIMIT ?",
            (session_id, cap),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT text FROM transcripts WHERE text IS NOT NULL ORDER BY ended_at DESC LIMIT ?",
            (cap,),
        ).fetchall()
    snippets = [str(r["text"]) for r in rows]
    if not snippets:
        return ToolResult(data=[], summary="no recent transcripts")
    summary = _truncate(f"{len(snippets)} snippet(s); latest: {snippets[0][:80]}")
    return ToolResult(data=snippets, summary=summary)


def _resolve_prior_steps(conn: sqlite3.Connection, activity_id: str) -> ToolResult:
    rows = conn.execute(
        "SELECT seq, body FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    bodies = [str(r["body"]) for r in rows]
    if not bodies:
        return ToolResult(
            data=[],
            summary=f"no prior steps for activity {activity_id}",
        )
    summary = _truncate(f"{len(bodies)} prior step(s); first: {bodies[0][:80]}")
    return ToolResult(data=bodies, summary=summary)


def _resolve_anti_signal(
    conn: sqlite3.Connection, template_id: str, slot_dict: dict[str, str]
) -> ToolResult:
    # Match the offline generator's signature pipeline so a tool-mode
    # candidate's anti-signal lookup reuses the same hashes the offline
    # path writes when the parent later flags the activity. Slot keys
    # other than ``slot``/``toy`` are dropped from the fingerprint
    # because compute_signature emits ``slot=<v>`` pairs and is
    # documented as the load-bearing format for the feedback table.
    slot_values: list[str] = []
    for key in ("slot", "toy"):
        v = slot_dict.get(key)
        if isinstance(v, str) and v:
            slot_values.append(v)
    signature = compute_signature(template_id, slot_values)
    counts = fetch_counts(conn, [signature])
    fc = counts.get(signature)
    if fc is None:
        return ToolResult(
            data={
                "blocked": False,
                "weight": 0.0,
                "didnt_work": 0,
                "loved_it": 0,
                "dismissed": 0,
            },
            summary="no anti-signal hits",
        )
    data = {
        "blocked": fc.is_blocked(),
        "weight": fc.weight(),
        "didnt_work": fc.didnt_work,
        "loved_it": fc.loved_it,
        "dismissed": fc.dismissed,
    }
    summary = _truncate(
        f"blocked={fc.is_blocked()} weight={fc.weight():.2f} "
        f"(didnt_work={fc.didnt_work}, loved_it={fc.loved_it}, dismissed={fc.dismissed})"
    )
    return ToolResult(data=data, summary=summary)


# --------------------------------------------------------------------- registry


@dataclass(frozen=True, slots=True)
class _ToolEntry:
    name: str
    args_model: type[_ArgsBase]
    resolver: Callable[..., ToolResult]
    needs_session: bool = False


def _resolve_get_persona(conn: sqlite3.Connection, args: GetPersonaArgs) -> ToolResult:
    return _resolve_persona(conn, args.persona_id)


def _resolve_get_room(conn: sqlite3.Connection, args: GetRoomArgs) -> ToolResult:
    return _resolve_room(conn, args.room_id)


def _resolve_get_inventory(conn: sqlite3.Connection, args: GetInventoryArgs) -> ToolResult:
    return _resolve_inventory(conn, args.child_id)


def _resolve_get_recent_transcript(
    conn: sqlite3.Connection, args: GetRecentTranscriptArgs, session_id: str | None
) -> ToolResult:
    return _resolve_recent_transcript(conn, args.window_sec, session_id)


def _resolve_get_prior_steps(conn: sqlite3.Connection, args: GetPriorStepsArgs) -> ToolResult:
    return _resolve_prior_steps(conn, args.activity_id)


def _resolve_get_anti_signal(conn: sqlite3.Connection, args: GetAntiSignalArgs) -> ToolResult:
    return _resolve_anti_signal(conn, args.template_id, args.slot_dict)


_REGISTRY: Final[dict[str, _ToolEntry]] = {
    "get_persona": _ToolEntry(
        name="get_persona",
        args_model=GetPersonaArgs,
        resolver=_resolve_get_persona,
    ),
    "get_room": _ToolEntry(
        name="get_room",
        args_model=GetRoomArgs,
        resolver=_resolve_get_room,
    ),
    "get_inventory": _ToolEntry(
        name="get_inventory",
        args_model=GetInventoryArgs,
        resolver=_resolve_get_inventory,
    ),
    "get_recent_transcript": _ToolEntry(
        name="get_recent_transcript",
        args_model=GetRecentTranscriptArgs,
        resolver=_resolve_get_recent_transcript,
        needs_session=True,
    ),
    "get_prior_steps": _ToolEntry(
        name="get_prior_steps",
        args_model=GetPriorStepsArgs,
        resolver=_resolve_get_prior_steps,
    ),
    "get_anti_signal": _ToolEntry(
        name="get_anti_signal",
        args_model=GetAntiSignalArgs,
        resolver=_resolve_get_anti_signal,
    ),
}


REGISTERED_TOOLS: Final[tuple[str, ...]] = tuple(sorted(_REGISTRY))


# --------------------------------------------------------------------- entrypoint


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _make_invalid_args_error(
    *, tool: str, args: dict[str, Any], reason: str, code: str
) -> dict[str, Any]:
    return {
        "error": "invalid_args",
        "code": code,
        "tool": tool,
        "args": args,
        "reason": reason,
    }


# Bounded shape for failed-validation args going into telemetry. The
# raw dict is the exact (unvalidated) input from the model — Pydantic
# rejected it, but the rejected dict still gets stored on
# ``labeled_events.tool_calls.args`` verbatim. A model emitting
# ``{"x": "A" * 1_000_000}`` would blow up the row, so we project to
# truncated strings + cap the total key count.
_TELEMETRY_FAIL_ARGS_KEY_CAP: Final[int] = 32
_TELEMETRY_FAIL_ARGS_STR_CAP: Final[int] = 256


def _truncate_failed_args(raw_args: Any) -> dict[str, Any]:
    """Project an unvalidated args dict onto a bounded telemetry shape.

    Mirrors the success-path's bounded shape: only the first
    :data:`_TELEMETRY_FAIL_ARGS_KEY_CAP` keys are kept, and string
    values are truncated to :data:`_TELEMETRY_FAIL_ARGS_STR_CAP`
    chars. Non-string values pass through unchanged (they're bounded
    by their type already — int/bool/None — and lists/dicts here are
    rare on the failure path; if a model emits a 1MB list, that's a
    separate row-blow-up bug we'd want to see in the telemetry dump
    rather than silently truncate).
    """
    if not isinstance(raw_args, dict):
        return {}
    out: dict[str, Any] = {}
    for idx, (k, v) in enumerate(raw_args.items()):
        if idx >= _TELEMETRY_FAIL_ARGS_KEY_CAP:
            break
        key = str(k)[:_TELEMETRY_FAIL_ARGS_STR_CAP]
        if isinstance(v, str):
            out[key] = v[:_TELEMETRY_FAIL_ARGS_STR_CAP]
        else:
            out[key] = v
    return out


def _format_validation_reason(exc: ValidationError) -> tuple[str, str]:
    """Return ``(reason_text, error_code_suffix)`` from a ValidationError.

    The error code suffix is used for ``labeled_events.tool_calls.error``
    so the row's error column is grep-able (e.g.
    ``"invalid_args:room_id_not_uuid"``).

    Drive-by: classification is driven off Pydantic's structured error
    ``type`` field rather than substring-matching the human-readable
    ``msg``. Pydantic's error ``type`` codes are part of the public
    API (and stable across patch releases); ``msg`` text isn't. UUID-
    or library-slug rejections come from our own custom validators
    (which raise ``ValueError("... must be a UUIDv4 string")``) and
    surface as Pydantic ``value_error`` with a ``ctx.error`` carrying
    the original ``ValueError`` — we inspect that path directly so
    the classification doesn't ride on prose.
    """
    errors = exc.errors()
    if not errors:
        return ("invalid args", "unknown")
    first = errors[0]
    loc = ".".join(str(p) for p in first.get("loc", ()) if p != "__root__")
    msg = str(first.get("msg", "invalid"))
    type_id = str(first.get("type", "value_error"))
    reason = f"{loc}: {msg}" if loc else msg
    code_field = loc or "value"

    # Map Pydantic's structured error ``type`` codes to grep-able
    # suffix codes. Each branch tests on the ``type_id`` (a stable
    # public-API string) rather than the ``msg`` (free-form).
    code: str
    if type_id == "value_error":
        # value_error is what our custom field validators raise (via
        # ``raise ValueError(...)``). ``_validate_uuid_str`` and
        # ``_validate_persona_id`` are the only producers, so this
        # branch is the UUID/library-slug rejection path. We still
        # peek at ``ctx.error`` if present — Pydantic v2 attaches
        # the original ValueError there — but fall back to the
        # field-name-based default if it's missing.
        ctx = first.get("ctx", {}) or {}
        original = ctx.get("error")
        original_msg = str(original) if original is not None else msg
        original_lc = original_msg.lower()
        if "uuid" in original_lc or "library slug" in original_lc:
            code = f"{code_field}_not_uuid"
        else:
            code = code_field or "invalid"
    elif type_id in {"string_too_long"} or type_id.startswith("string_too_long"):
        code = f"{code_field}_too_long"
    elif type_id in {"string_too_short"} or type_id.startswith("string_too_short"):
        code = f"{code_field}_too_short"
    elif type_id in {
        "int_parsing",
        "int_type",
        "less_than",
        "less_than_equal",
        "greater_than",
        "greater_than_equal",
    }:
        code = f"{code_field}_out_of_range"
    elif type_id == "extra_forbidden":
        code = f"{code_field}_unknown"
    elif type_id.startswith("missing"):
        code = f"{code_field}_missing"
    else:
        code = code_field or "invalid"
    return reason, code


async def _dispatch(
    entry: _ToolEntry,
    args_model: _ArgsBase,
    ctx: ToolContext,
) -> ToolResult:
    """Run the resolver inside ``asyncio.to_thread`` with proper conn cleanup.

    A single short-lived connection per call is the project pattern for
    background work (see ``judge.py::judge_and_persist``). Wrapping in
    ``to_thread`` keeps the event loop responsive while SQLite blocks.
    """

    def _run() -> ToolResult:
        conn = ctx.connection_factory()
        try:
            if entry.needs_session:
                resolver: Callable[..., ToolResult] = entry.resolver
                return resolver(conn, args_model, ctx.session_id)
            return entry.resolver(conn, args_model)
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


async def call_tool(
    name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    """Dispatch ``name`` with ``args`` against ``ctx``.

    Returns a dict with one of these shapes:

    * Success::

          {
            "tool": "get_room",
            "args": {...validated...},
            "data": {...resolver output...},
            "result_summary": "...",
            "latency_ms": 12,
            "error": None,
            "ts": "2026-05-12T14:30:01.234Z",
          }

    * Failure::

          {
            "tool": "get_room",
            "args": {...raw...},
            "data": None,
            "result_summary": "",
            "latency_ms": <int>,
            "error": "invalid_args:room_id_not_uuid",
            "reason": "room_id must be a UUIDv4 string",
            "ts": "...",
          }

    Failures NEVER raise — they always return the recovery dict so the
    caller can feed the structured error back to the model. The only
    exception passed through is :class:`asyncio.CancelledError`, which
    is re-raised so the surrounding task's cancellation semantics are
    preserved.
    """
    started = time.monotonic()
    ts = _now_iso()
    entry = _REGISTRY.get(name)
    if entry is None:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "tool": name,
            "args": dict(args),
            "data": None,
            "result_summary": "",
            "latency_ms": latency_ms,
            "error": f"unknown_tool:{name}",
            "reason": f"unknown tool {name!r}; registered: {sorted(_REGISTRY)}",
            "ts": ts,
        }

    try:
        validated = entry.args_model.model_validate(args)
    except ValidationError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        reason, code_suffix = _format_validation_reason(exc)
        # M2: project the unvalidated args onto a bounded shape before
        # storing them on labeled_events.tool_calls. A model emitting
        # ``{"x": "A" * 1_000_000}`` should NOT blow up the row.
        bounded_args = _truncate_failed_args(args)
        err = _make_invalid_args_error(
            tool=name, args=bounded_args, reason=reason, code=code_suffix
        )
        return {
            "tool": name,
            "args": bounded_args,
            "data": None,
            "result_summary": "",
            "latency_ms": latency_ms,
            "error": f"invalid_args:{code_suffix}",
            "reason": err["reason"],
            "ts": ts,
        }

    timeout = timeout_sec if timeout_sec is not None else tool_loop_timeout_sec()
    try:
        async with asyncio.timeout(timeout):
            result = await _dispatch(entry, validated, ctx)
    except asyncio.CancelledError:
        # Propagate cancellation; the caller's surrounding task is
        # being torn down. The connection-closing finally inside
        # ``_dispatch._run`` runs as part of the to_thread teardown,
        # so no DB connection leaks.
        raise
    except TimeoutError:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "tool": name,
            "args": validated.model_dump(),
            "data": None,
            "result_summary": "",
            "latency_ms": latency_ms,
            "error": "timeout",
            "reason": f"tool {name!r} timed out after {timeout:.1f}s",
            "ts": ts,
        }
    except sqlite3.Error as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "tool": name,
            "args": validated.model_dump(),
            "data": None,
            "result_summary": "",
            "latency_ms": latency_ms,
            "error": "db_error",
            "reason": f"{type(exc).__name__}: {exc}",
            "ts": ts,
        }

    latency_ms = int((time.monotonic() - started) * 1000)
    return {
        "tool": name,
        "args": validated.model_dump(),
        "data": result.data,
        "result_summary": _truncate(result.summary),
        "latency_ms": latency_ms,
        "error": None,
        "ts": ts,
    }


# --------------------------------------------------------------------- helpers


def telemetry_entry(call_result: dict[str, Any]) -> dict[str, Any]:
    """Project a :func:`call_tool` result onto the labeled_events shape.

    Used by adapters that already have the dispatcher result and want
    the storage-side projection without re-running the call. Keeps the
    shape pinned in one place — see
    ``documentation/phase-e-plan.md`` §"Tool-call telemetry shape".
    """
    return {
        "tool": call_result["tool"],
        "args": call_result["args"],
        "result_summary": call_result["result_summary"],
        "latency_ms": call_result["latency_ms"],
        "error": call_result["error"],
        "ts": call_result["ts"],
    }


# --------------------------------------------------------------------- protocol


class ToolDispatcher:
    """Convenience facade bundling :func:`call_tool` with a fixed ``ctx``.

    Adapters receive a :class:`ToolDispatcher` so the loop body reads
    ``await tools.call_tool(name, args)`` rather than re-passing the
    context dict every turn.
    """

    def __init__(self, ctx: ToolContext, *, timeout_sec: float | None = None) -> None:
        self._ctx = ctx
        self._timeout_sec = timeout_sec

    @property
    def ctx(self) -> ToolContext:
        return self._ctx

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return await call_tool(name, args, self._ctx, timeout_sec=self._timeout_sec)


# --------------------------------------------------------------------- public

ToolCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def registered_tools() -> Sequence[str]:
    """Return the sorted tuple of registered tool names."""
    return REGISTERED_TOOLS


__all__ = [
    "DEFAULT_INVENTORY_LIMIT",
    "DEFAULT_TOOL_LOOP_TIMEOUT_SEC",
    "GetAntiSignalArgs",
    "GetInventoryArgs",
    "GetPersonaArgs",
    "GetPriorStepsArgs",
    "GetRecentTranscriptArgs",
    "GetRoomArgs",
    "REGISTERED_TOOLS",
    "RESULT_SUMMARY_MAX",
    "TOOL_LOOP_TIMEOUT_ENV",
    "ToolCall",
    "ToolContext",
    "ToolDispatcher",
    "ToolResult",
    "call_tool",
    "registered_tools",
    "telemetry_entry",
    "tool_loop_timeout_sec",
]
