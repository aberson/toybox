"""Activity lifecycle REST API.

Implements the propose / approve / dismiss / regenerate / advance / end
/ didn't-work transitions described in the Step 8 problem statement.
Mutations enforce optimistic concurrency through the ``If-Match-Version``
header (see :mod:`toybox.core.version_check`) and emit
``activity.state`` envelopes via the process-singleton
:class:`toybox.core.pubsub.PubSub`.

The persisted shape (``activities`` + ``activity_steps`` rows) is
serialized to a wire model :class:`ActivityResponse` that combines the
persisted columns and the generator's in-memory step list. Step text
is stored in ``activity_steps.body``; the response uses the same
``body`` field for clarity (matching the migration's column name).
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..activities.generator import generate
from ..core.auth import TokenScope
from ..core.pubsub import PubSub
from ..core.queue import (
    DISMISSED_STATE,
    PROPOSED_QUEUE_CAP,
    PROPOSED_STATE,
    evict_oldest_for_capacity,
)
from ..core.version_check import (
    VersionConflictError,
    if_match_version_dependency,
)
from ..db import connect, resolve_db_path
from ..ws.envelope import build_envelope
from ..ws.server import get_pubsub
from ..ws.topics import Topic
from .auth_dep import RequireScope

router = APIRouter(prefix="/api/activities", tags=["activities"])

# Activity state literals (pinned by tests + frontend).
STATE_PROPOSED = "proposed"
STATE_APPROVED = "approved"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_ENDED = "ended"
STATE_DISMISSED = "dismissed"
STATE_DIDNT_WORK = "didnt_work"

# Valid transition map: source state -> set of target states.
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_PROPOSED: frozenset({STATE_APPROVED, STATE_DISMISSED}),
    STATE_APPROVED: frozenset({STATE_RUNNING, STATE_ENDED, STATE_DISMISSED}),
    STATE_RUNNING: frozenset(
        {STATE_RUNNING, STATE_COMPLETED, STATE_ENDED, STATE_DIDNT_WORK, STATE_DISMISSED}
    ),
    STATE_COMPLETED: frozenset({STATE_DIDNT_WORK}),
    STATE_ENDED: frozenset({STATE_DIDNT_WORK}),
    STATE_DISMISSED: frozenset(),
    STATE_DIDNT_WORK: frozenset(),
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def get_activities_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield an activities-scoped SQLite connection.

    ``check_same_thread=False`` because FastAPI dispatches sync
    generator setup, the handler body, and teardown via
    ``run_in_threadpool``; anyio may pick a different worker for each
    leg, which would otherwise trip
    ``sqlite3.ProgrammingError`` in ``conn.close()``.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class ActivityStepResponse(BaseModel):
    """Wire shape for one activity step."""

    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=1)
    body: str = Field(min_length=1)
    sfx: str | None = None
    expected_action: str | None = None
    current: bool = False


class ActivityResponse(BaseModel):
    """Wire shape for an activity returned by the REST API."""

    model_config = ConfigDict(frozen=True)

    id: str
    state: str
    version: int = Field(ge=1)
    title: str | None = None
    summary: str | None = None
    persona_id: str | None = None
    intent_source: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    steps: list[ActivityStepResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProposeRequest(BaseModel):
    """Body for ``POST /api/activities/propose``."""

    intent: str = Field(min_length=1)
    slot: str | None = None
    hour: int = Field(ge=0, le=23)
    seed: int = Field(ge=0)
    persona_id: str | None = None
    session_id: str | None = None
    context: dict[str, Any] | None = None


class ApproveRequest(BaseModel):
    """Body for ``POST /api/activities/{id}/approve``."""

    child_ids: list[str] | None = None


class RegenerateRequest(BaseModel):
    """Body for ``POST /api/activities/{id}/regenerate``."""

    intent: str | None = None
    slot: str | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    seed: int | None = Field(default=None, ge=0)
    persona_id: str | None = None
    context: dict[str, Any] | None = None


class DidntWorkRequest(BaseModel):
    """Body for ``POST /api/activities/{id}/didnt-work``."""

    reason: str | None = None


def _ensure_session(conn: sqlite3.Connection, session_id: str | None) -> str:
    """Resolve or create a session id. Phase A keeps this trivial."""
    if session_id:
        row = conn.execute(
            "SELECT id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is not None:
            return session_id
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (session_id, _now_iso()),
            )
        return session_id
    new_id = str(uuid.uuid4())
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            (new_id, _now_iso()),
        )
    return new_id


def _resolve_only_child(conn: sqlite3.Connection) -> list[str]:
    """If exactly one child profile exists, return it; else empty."""
    rows = conn.execute("SELECT id FROM children LIMIT 2").fetchall()
    if len(rows) == 1:
        return [str(rows[0]["id"])]
    return []


def _fetch_activity_row(conn: sqlite3.Connection, activity_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM activities WHERE id = ?",
        (activity_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "activity_not_found", "id": activity_id},
        )
    return row


def _fetch_steps(conn: sqlite3.Connection, activity_id: str) -> list[ActivityStepResponse]:
    rows = conn.execute(
        "SELECT seq, body, sfx, expected_action, current "
        "FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    return [
        ActivityStepResponse(
            seq=int(r["seq"]),
            body=str(r["body"]),
            sfx=r["sfx"],
            expected_action=r["expected_action"],
            current=bool(r["current"]),
        )
        for r in rows
    ]


def _row_to_response(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> ActivityResponse:
    activity_id = str(row["id"])
    # NOTE: ``activities.summary`` is overloaded as a JSON envelope
    # (``{"title": ..., "metadata": ..., "template_id": ...}``). The
    # column is declared free-form by the migration; we parse it here
    # with a graceful fallback for plaintext rows. A future migration
    # may split this into dedicated columns; until then the write side
    # in :func:`_do_propose` mirrors this contract.
    summary_raw = row["summary"]
    metadata: dict[str, Any] = {}
    title: str | None = None
    if summary_raw:
        try:
            payload = json.loads(summary_raw)
            if isinstance(payload, dict):
                title = payload.get("title")
                metadata = payload.get("metadata") or {}
        except json.JSONDecodeError:
            title = summary_raw
    child_ids_raw = row["child_ids"]
    child_ids: list[str]
    if child_ids_raw:
        try:
            decoded = json.loads(child_ids_raw)
            child_ids = [str(c) for c in decoded] if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            child_ids = []
    else:
        child_ids = []

    return ActivityResponse(
        id=activity_id,
        state=str(row["state"]),
        version=int(row["version"]),
        title=title,
        summary=summary_raw if not title else None,
        persona_id=row["persona_id"],
        intent_source=row["intent_source"],
        child_ids=child_ids,
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        steps=_fetch_steps(conn, activity_id),
        metadata=metadata,
    )


def _emit_state(pubsub: PubSub, response: ActivityResponse) -> None:
    pubsub.publish(
        build_envelope(
            topic=Topic.activity_state,
            payload=response.model_dump(mode="json"),
        )
    )


def _persist_activity(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    session_id: str,
    persona_id: str | None,
    intent_source: str,
    summary_payload: dict[str, Any],
    steps: list[dict[str, Any]],
    state: str,
) -> None:
    summary_blob = json.dumps(summary_payload, sort_keys=True)
    created_at = _now_iso()
    with conn:
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, persona_id, child_ids, room_ids, "
            " toy_ids, intent_source, created_at, started_at, ended_at) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, NULL, NULL, ?, ?, NULL, NULL)",
            (
                activity_id,
                session_id,
                state,
                summary_blob,
                persona_id,
                None,
                intent_source,
                created_at,
            ),
        )
        for step in steps:
            conn.execute(
                "INSERT INTO activity_steps "
                "(id, activity_id, seq, body, sfx, expected_action, current) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    activity_id,
                    step["seq"],
                    step["body"],
                    step.get("sfx"),
                    step.get("expected_action"),
                    1 if step.get("current") else 0,
                ),
            )


def _attempt_transition(
    conn: sqlite3.Connection,
    *,
    activity_id: str,
    expected_version: int,
    new_state: str,
    additional_sets: tuple[tuple[str, Any], ...] = (),
) -> tuple[bool, sqlite3.Row]:
    """Atomically transition state + bump version when the version matches.

    Returns ``(ok, latest_row)``. When ``ok`` is ``False`` the latest
    row is the post-failure read so the caller can craft a 409.
    """
    set_clauses = ["state = ?", "version = version + 1"]
    params: list[Any] = [new_state]
    for column, value in additional_sets:
        set_clauses.append(f"{column} = ?")
        params.append(value)
    sql = f"UPDATE activities SET {', '.join(set_clauses)} WHERE id = ? AND version = ?"
    params.extend([activity_id, expected_version])
    with conn:
        cur = conn.execute(sql, params)
    ok = cur.rowcount == 1
    row = _fetch_activity_row(conn, activity_id)
    return ok, row


def _enforce_transition(current_state: str, target: str) -> None:
    allowed = _VALID_TRANSITIONS.get(current_state, frozenset())
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_transition",
                "current_state": current_state,
                "target_state": target,
            },
        )


def _pick_random_library_persona(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Pick a random ``source='library'`` persona for variety on propose.

    Returns a small dict (id, display_name, archetype, avatar_image_path)
    or ``None`` when the personas table has no library rows (e.g. fresh
    DB before the loader ran). Used to drive avatar variety on the
    kiosk; activity content is still template-driven.
    """
    row = conn.execute(
        "SELECT id, display_name, archetype, avatar_image_path "
        "FROM personas WHERE source = 'library' "
        "ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "display_name": str(row["display_name"]),
        "archetype": row["archetype"],
        "avatar_image_path": row["avatar_image_path"],
    }


def _do_propose(
    body: ProposeRequest,
    conn: sqlite3.Connection,
    pubsub: PubSub,
) -> ActivityResponse:
    """Shared propose-and-persist helper.

    Carries no auth — both ``post_propose`` and ``post_regenerate``
    funnel through here and keep the auth check on the route handler.
    """
    # Caller-pinned persona wins; otherwise pick a fresh library one
    # so the kiosk avatar varies across propose calls. Falls through to
    # no persona when the library is empty (kiosk fallback letter
    # handles that case).
    effective_persona_id = body.persona_id
    persona_meta: dict[str, Any] | None = None
    if effective_persona_id is None:
        picked = _pick_random_library_persona(conn)
        if picked is not None:
            effective_persona_id = picked["id"]
            persona_meta = picked
    activity = generate(
        intent=body.intent,
        slot=body.slot,
        context=body.context,
        hour=body.hour,
        seed=body.seed,
        persona_id=effective_persona_id,
    )
    session_id = _ensure_session(conn, body.session_id)

    # Evict oldest first so the cap of 5 holds for the new row.
    evicted_ids = evict_oldest_for_capacity(conn, cap=PROPOSED_QUEUE_CAP)
    for eid in evicted_ids:
        evicted_row = _fetch_activity_row(conn, eid)
        _emit_state(pubsub, _row_to_response(conn, evicted_row))

    # NOTE: ``activities.summary`` is overloaded as a JSON envelope
    # carrying ``title``, ``metadata``, and ``template_id``. The
    # migration declares the column free-form, so this is schema-legal
    # but invisible to schema readers. A future migration may split
    # this into dedicated ``title``/``metadata_json`` columns; until
    # then ``_row_to_response`` parses the same envelope.
    # Splice persona metadata into the activity's metadata envelope so
    # the kiosk can render the persona's display name + avatar path
    # without an extra round-trip. ``activity.metadata`` is a frozen
    # dict, so build a fresh copy.
    metadata = dict(activity.metadata)
    if persona_meta is not None:
        metadata["persona"] = persona_meta
    summary_payload = {
        "title": activity.title,
        "metadata": metadata,
        "template_id": activity.template_id,
    }
    steps = [
        {
            "seq": idx + 1,
            "body": step.text,
            "sfx": step.sfx,
            "expected_action": step.expected_action,
            "current": False,
        }
        for idx, step in enumerate(activity.steps)
    ]
    _persist_activity(
        conn,
        activity_id=activity.id,
        session_id=session_id,
        persona_id=activity.persona_id,
        intent_source=body.intent,
        summary_payload=summary_payload,
        steps=steps,
        state=PROPOSED_STATE,
    )

    row = _fetch_activity_row(conn, activity.id)
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/propose", response_model=ActivityResponse, status_code=status.HTTP_201_CREATED)
def post_propose(
    body: ProposeRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Generate a new activity at ``proposed``. Drops oldest if cap reached."""
    return _do_propose(body, conn, pubsub)


@router.post("/{activity_id}/approve", response_model=ActivityResponse)
def post_approve(
    activity_id: str,
    body: ApproveRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """proposed → approved (optimistically)."""
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, STATE_APPROVED)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)

    child_ids = body.child_ids or _resolve_only_child(conn)
    encoded_children = json.dumps(child_ids) if child_ids else None
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_APPROVED,
        additional_sets=(("child_ids", encoded_children),),
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/dismiss", response_model=ActivityResponse)
def post_dismiss(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """proposed → dismissed."""
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, DISMISSED_STATE)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=DISMISSED_STATE,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/regenerate", response_model=ActivityResponse)
def post_regenerate(
    activity_id: str,
    body: RegenerateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Mark the existing activity ``dismissed`` and propose a fresh one."""
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, DISMISSED_STATE)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    ok, dismissed_row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=DISMISSED_STATE,
    )
    if not ok:
        raise VersionConflictError(int(dismissed_row["version"]), str(dismissed_row["state"]))
    _emit_state(pubsub, _row_to_response(conn, dismissed_row))

    intent = body.intent or str(row["intent_source"]) or "boredom"
    # Random seed when caller doesn't supply one — the seed feeds both
    # the template-pick rng AND the deterministic UUID hash, so a fixed
    # fallback meant every regenerate produced the same suggestion (and
    # collided on activities.id the second time around). Tests that
    # need determinism still pass an explicit seed.
    seed = body.seed if body.seed is not None else secrets.randbits(31)
    hour = body.hour if body.hour is not None else datetime.now(UTC).hour
    # Don't inherit the source's persona — let _do_propose pick a fresh
    # library persona so each "skip & try another" gives the kiosk a
    # different character. Caller can pin a persona via body.persona_id.
    persona_id = body.persona_id
    # Fold source identity into the UUID hash too — defense in depth so
    # that even an unlikely seed collision doesn't surface as a 500.
    context = dict(body.context) if body.context is not None else {}
    context.setdefault("regen_source", activity_id)
    context.setdefault("regen_source_version", current_version)
    return _do_propose(
        ProposeRequest(
            intent=intent,
            slot=body.slot,
            hour=hour,
            seed=seed,
            persona_id=persona_id,
            session_id=str(row["session_id"]),
            context=context,
        ),
        conn,
        pubsub,
    )


@router.post("/{activity_id}/advance", response_model=ActivityResponse)
def post_advance(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent, TokenScope.child}))],
) -> ActivityResponse:
    """Advance one step. approved → running on first call; running → running/completed otherwise."""
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    if current_state == STATE_APPROVED:
        target = STATE_RUNNING
    elif current_state == STATE_RUNNING:
        target = STATE_RUNNING
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_transition",
                "current_state": current_state,
                "target_state": STATE_RUNNING,
            },
        )
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)

    steps = conn.execute(
        "SELECT id, seq, current FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
        (activity_id,),
    ).fetchall()
    if not steps:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "activity_has_no_steps", "id": activity_id},
        )

    if current_state == STATE_APPROVED:
        next_index = 0
    else:
        current_index = next((i for i, s in enumerate(steps) if int(s["current"]) == 1), -1)
        next_index = current_index + 1

    if next_index >= len(steps):
        target = STATE_COMPLETED
        ok, row = _attempt_transition(
            conn,
            activity_id=activity_id,
            expected_version=expected_version,
            new_state=target,
            additional_sets=(("ended_at", _now_iso()),),
        )
        if not ok:
            raise VersionConflictError(int(row["version"]), str(row["state"]))
        with conn:
            conn.execute(
                "UPDATE activity_steps SET current = 0 WHERE activity_id = ?",
                (activity_id,),
            )
        response = _row_to_response(conn, row)
        _emit_state(pubsub, response)
        return response

    additional: tuple[tuple[str, Any], ...] = ()
    if current_state == STATE_APPROVED:
        additional = (("started_at", _now_iso()),)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=target,
        additional_sets=additional,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    target_seq = int(steps[next_index]["seq"])
    with conn:
        conn.execute(
            "UPDATE activity_steps SET current = 0 WHERE activity_id = ?",
            (activity_id,),
        )
        conn.execute(
            "UPDATE activity_steps SET current = 1 WHERE activity_id = ? AND seq = ?",
            (activity_id, target_seq),
        )
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/end", response_model=ActivityResponse)
def post_end(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """approved/running → ended."""
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, STATE_ENDED)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_ENDED,
        additional_sets=(("ended_at", _now_iso()),),
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.post("/{activity_id}/didnt-work", response_model=ActivityResponse)
def post_didnt_work(
    activity_id: str,
    body: DidntWorkRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    pubsub: Annotated[PubSub, Depends(get_pubsub)],
    expected_version: Annotated[int, Depends(if_match_version_dependency)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent}))],
) -> ActivityResponse:
    """Flag any state as ``didnt_work`` (anti-signal feedback for Phase D)."""
    row = _fetch_activity_row(conn, activity_id)
    current_state = str(row["state"])
    current_version = int(row["version"])
    _enforce_transition(current_state, STATE_DIDNT_WORK)
    if current_version != expected_version:
        raise VersionConflictError(current_version, current_state)
    ok, row = _attempt_transition(
        conn,
        activity_id=activity_id,
        expected_version=expected_version,
        new_state=STATE_DIDNT_WORK,
    )
    if not ok:
        raise VersionConflictError(int(row["version"]), str(row["state"]))
    if body.reason:
        with conn:
            conn.execute(
                "INSERT INTO feedback "
                "(id, activity_id, step_seq, kind, signature, reason, created_at) "
                "VALUES (?, ?, NULL, 'didnt_work', '', ?, ?)",
                (str(uuid.uuid4()), activity_id, body.reason, _now_iso()),
            )
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)
    return response


@router.get("/{activity_id}", response_model=ActivityResponse)
def get_activity(
    activity_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_activities_db)],
    _: Annotated[Any, Depends(RequireScope({TokenScope.parent, TokenScope.child}))],
) -> ActivityResponse:
    """Read-back endpoint used by tests + parent UI."""
    row = _fetch_activity_row(conn, activity_id)
    return _row_to_response(conn, row)


__all__ = [
    "ActivityResponse",
    "ActivityStepResponse",
    "ApproveRequest",
    "DidntWorkRequest",
    "ProposeRequest",
    "RegenerateRequest",
    "STATE_APPROVED",
    "STATE_COMPLETED",
    "STATE_DIDNT_WORK",
    "STATE_DISMISSED",
    "STATE_ENDED",
    "STATE_PROPOSED",
    "STATE_RUNNING",
    "get_activities_db",
    "router",
]
