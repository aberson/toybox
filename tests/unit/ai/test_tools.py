"""Unit tests for the Phase E Step 28 tool registry.

Covers:
- Adversarial args (path traversal, non-UUID strings, oversized strings,
  wrong types) returning the structured recovery error shape.
- Happy-path resolvers against an in-memory migrated SQLite DB.
- ``asyncio.timeout`` cap returning ``{"error": "timeout"}`` instead of raising.
- Cancellation safety: cancelling the surrounding task does not leak DB
  connections.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from toybox.ai.tools import (
    REGISTERED_TOOLS,
    ToolContext,
    call_tool,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def seeded_db(db_path: Path) -> Path:
    """Migrate + seed a small set of catalog rows for the resolvers."""
    conn = connect(db_path)
    try:
        with conn:
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO personas "
                "(id, display_name, archetype, system_prompt, behavior_tags, "
                " age_range_min, age_range_max, default_voice_tone, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "wizard",
                    "Wizard",
                    "magician",
                    "You are a friendly wizard.",
                    "playful, gentle",
                    3,
                    8,
                    "warm",
                    "library",
                    now,
                ),
            )
            persona_uuid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO personas "
                "(id, display_name, archetype, system_prompt, behavior_tags, "
                " age_range_min, age_range_max, default_voice_tone, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    persona_uuid,
                    "Custom",
                    None,
                    "Custom prompt.",
                    None,
                    None,
                    None,
                    None,
                    "user",
                    now,
                ),
            )
            room_uuid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO rooms (id, display_name, image_path) VALUES (?, ?, ?)",
                (room_uuid, "Kitchen", "rooms/kitchen.png"),
            )
            conn.execute(
                "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), room_uuid, "counter"),
            )
            conn.execute(
                "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), room_uuid, "fridge"),
            )
            child_uuid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO children (id, display_name) VALUES (?, ?)",
                (child_uuid, "Child"),
            )
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "Lego", "toys/lego.png", "h1", now),
            )
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "Unicorn", "toys/unicorn.png", "h2", now),
            )
            session_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (session_id, now),
            )
            activity_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, intent_source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (activity_id, session_id, "running", 1, "boredom", now),
            )
            conn.execute(
                "INSERT INTO activity_steps (id, activity_id, seq, body) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), activity_id, 1, "Find a comfy spot."),
            )
            conn.execute(
                "INSERT INTO activity_steps (id, activity_id, seq, body) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), activity_id, 2, "Take a deep breath."),
            )
            conn.execute(
                "INSERT INTO transcripts (id, session_id, ended_at, text) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), session_id, now, "I want to play"),
            )
        # Stash UUIDs on a sidecar dict so the test can read them.
        conn.row_factory = sqlite3.Row
    finally:
        conn.close()
    return db_path


def _read_seed_uuids(db_path: Path) -> dict[str, str]:
    """Read back the UUIDs the seed inserted, by display_name lookups."""
    conn = connect(db_path)
    try:
        out: dict[str, str] = {}
        row = conn.execute("SELECT id FROM personas WHERE display_name = ?", ("Custom",)).fetchone()
        out["persona_uuid"] = str(row["id"])
        row = conn.execute("SELECT id FROM rooms WHERE display_name = ?", ("Kitchen",)).fetchone()
        out["room_uuid"] = str(row["id"])
        row = conn.execute("SELECT id FROM children WHERE display_name = ?", ("Child",)).fetchone()
        out["child_uuid"] = str(row["id"])
        row = conn.execute(
            "SELECT id, session_id FROM activities ORDER BY created_at LIMIT 1"
        ).fetchone()
        out["activity_uuid"] = str(row["id"])
        out["session_id"] = str(row["session_id"])
    finally:
        conn.close()
    return out


def _connection_is_closed(conn: sqlite3.Connection) -> bool:
    """Probe whether ``conn`` is closed by issuing a no-op query.

    SQLite connections raise :class:`sqlite3.ProgrammingError` when
    methods are invoked after ``.close()``, so we use that as the
    closure signal. Any other exception from a healthy connection
    surfaces; absence of an exception means the connection is open.
    """
    try:
        conn.execute("SELECT 1").fetchone()
    except sqlite3.ProgrammingError:
        return True
    return False


# Module-level reference to the most recent fixture's "opened
# connections" list — populated by ``make_ctx`` so the cancellation
# test (and any other test that wants to inspect connection lifecycle)
# can read closure state without needing to rewrap the fixture's
# return shape.
_LAST_OPENED_CONNS: list[sqlite3.Connection] = []


@pytest.fixture
def make_ctx(seeded_db: Path) -> Iterator[ToolContext]:
    """Yield a :class:`ToolContext` with a connection-tracking factory.

    Each opened connection is appended to the module-level
    :data:`_LAST_OPENED_CONNS` list (re-initialised per fixture
    invocation) so cancellation/closure tests can inspect lifecycle
    state without rewrapping the fixture's return shape.
    """
    opened: list[sqlite3.Connection] = []
    # Reset the module-level tracker for this fixture invocation.
    _LAST_OPENED_CONNS.clear()
    _LAST_OPENED_CONNS.extend(opened)  # share identity via mutation

    def _factory() -> sqlite3.Connection:
        c = connect(seeded_db, check_same_thread=False)
        opened.append(c)
        _LAST_OPENED_CONNS.append(c)
        return c

    uuids = _read_seed_uuids(seeded_db)
    ctx = ToolContext(
        connection_factory=_factory,
        activity_id=uuids["activity_uuid"],
        child_id=uuids["child_uuid"],
        session_id=uuids["session_id"],
    )
    yield ctx
    # Mirror the test-level resource cleanup: the resolvers all close
    # their own conn on exit so this list should already be drained.
    for c in opened:
        try:
            c.close()
        except sqlite3.ProgrammingError:
            pass


# --------------------------------------------------------------------- adversarial


@pytest.mark.parametrize(
    "tool_name, args, field_hint",
    [
        ("get_room", {"room_id": "../../../etc/passwd"}, "uuid"),
        ("get_room", {"room_id": "not-a-uuid"}, "uuid"),
        ("get_persona", {"persona_id": "Bad Slug With Spaces!"}, "library slug"),
        ("get_inventory", {"child_id": "not-a-uuid"}, "uuid"),
        ("get_prior_steps", {"activity_id": "../etc/hostname"}, "uuid"),
    ],
)
async def test_invalid_uuid_returns_recovery_error(
    make_ctx: ToolContext, tool_name: str, args: dict[str, str], field_hint: str
) -> None:
    result = await call_tool(tool_name, args, make_ctx)
    assert result["error"] is not None
    assert result["error"].startswith("invalid_args:")
    assert "uuid" in result["reason"].lower() or field_hint in result["reason"].lower()
    assert result["data"] is None


async def test_oversized_string_caught_by_length_cap(
    make_ctx: ToolContext,
) -> None:
    """1000-char string exceeds the 256 char cap on str fields."""
    huge = "a" * 1000
    result = await call_tool("get_persona", {"persona_id": huge}, make_ctx)
    assert result["error"] is not None
    assert result["error"].startswith("invalid_args:")
    # The reason must reference the length cap; without that hint, a
    # model wouldn't know whether to truncate or to retry with a
    # different shape entirely.
    reason = result["reason"].lower()
    assert "long" in reason or "256" in reason or "max_length" in reason


async def test_failed_validation_args_are_truncated_in_telemetry(
    make_ctx: ToolContext,
) -> None:
    """M2: An oversized arg value is bounded BEFORE going into telemetry.

    A model emitting ``{"x": "A"*1_000_000}`` (or any other
    pathological shape) shouldn't blow up the labeled_events row.
    The recovery dict's ``args`` block is projected to a truncated
    shape — strings capped at 256 chars, total keys capped at 32.
    """
    huge = "A" * 1_000_000
    result = await call_tool("get_persona", {"persona_id": huge}, make_ctx)
    assert result["error"] is not None
    assert result["error"].startswith("invalid_args:")
    # The args field surfaced in the result must be bounded.
    bounded = result["args"]
    assert isinstance(bounded, dict)
    assert "persona_id" in bounded
    assert isinstance(bounded["persona_id"], str)
    # Truncated to 256 chars (the documented cap).
    assert len(bounded["persona_id"]) == 256


async def test_failed_validation_args_key_count_capped_to_32(
    make_ctx: ToolContext,
) -> None:
    """M2: ``args`` with many keys is capped at 32 entries in telemetry."""
    args = {f"k{i}": "v" for i in range(100)}
    args["persona_id"] = "Bad Slug With Spaces!"
    result = await call_tool("get_persona", args, make_ctx)
    assert result["error"] is not None
    bounded = result["args"]
    assert isinstance(bounded, dict)
    assert len(bounded) <= 32


async def test_wrong_type_for_window_sec_on_get_recent_transcript(
    make_ctx: ToolContext,
) -> None:
    """``window_sec="thirty"`` is the model's most natural typo.

    (Replaces the old ``recency_window`` test — that field was dropped
    from ``GetInventoryArgs`` because the resolver never read it. The
    ``get_recent_transcript`` resolver DOES use ``window_sec``, so we
    pin the same wrong-type rejection there.)
    """
    result = await call_tool(
        "get_recent_transcript",
        {"window_sec": "thirty"},
        make_ctx,
    )
    assert result["error"] is not None
    assert result["error"].startswith("invalid_args:")


def _seeded_uuids_via_ctx(ctx: ToolContext) -> dict[str, str]:
    """Helper: resolve UUIDs using the ctx's connection factory."""
    conn = ctx.connection_factory()
    try:
        out: dict[str, str] = {}
        row = conn.execute("SELECT id FROM rooms WHERE display_name = ?", ("Kitchen",)).fetchone()
        out["room_uuid"] = str(row["id"])
        row = conn.execute("SELECT id FROM children WHERE display_name = ?", ("Child",)).fetchone()
        out["child_uuid"] = str(row["id"])
        row = conn.execute(
            "SELECT id, session_id FROM activities ORDER BY created_at LIMIT 1"
        ).fetchone()
        out["activity_uuid"] = str(row["id"])
        return out
    finally:
        conn.close()


async def test_extra_unknown_field_rejected(make_ctx: ToolContext) -> None:
    uuids = _seeded_uuids_via_ctx(make_ctx)
    result = await call_tool(
        "get_room",
        {"room_id": uuids["room_uuid"], "unexpected": "value"},
        make_ctx,
    )
    assert result["error"] is not None
    assert result["error"].startswith("invalid_args:")


async def test_unknown_tool_name_returns_recovery(make_ctx: ToolContext) -> None:
    result = await call_tool("delete_database", {}, make_ctx)
    assert result["error"] is not None
    assert result["error"].startswith("unknown_tool:")
    assert result["data"] is None


# --------------------------------------------------------------------- happy paths


async def test_get_persona_library_slug(make_ctx: ToolContext) -> None:
    result = await call_tool("get_persona", {"persona_id": "wizard"}, make_ctx)
    assert result["error"] is None
    data = result["data"]
    assert data["display_name"] == "Wizard"
    assert data["archetype"] == "magician"
    assert "playful" in data["behavior_tags"]
    assert data["age_range"] == [3, 8]
    assert data["default_voice_tone"] == "warm"
    assert "Wizard" in result["result_summary"]


async def test_get_persona_uuid(make_ctx: ToolContext) -> None:
    conn = make_ctx.connection_factory()
    try:
        row = conn.execute("SELECT id FROM personas WHERE display_name = ?", ("Custom",)).fetchone()
        persona_id = str(row["id"])
    finally:
        conn.close()
    result = await call_tool("get_persona", {"persona_id": persona_id}, make_ctx)
    assert result["error"] is None
    assert result["data"]["display_name"] == "Custom"


async def test_get_room_happy(make_ctx: ToolContext) -> None:
    uuids = _seeded_uuids_via_ctx(make_ctx)
    result = await call_tool("get_room", {"room_id": uuids["room_uuid"]}, make_ctx)
    assert result["error"] is None
    data = result["data"]
    assert data["name"] == "Kitchen"
    assert "counter" in data["features"]
    assert "fridge" in data["features"]
    assert data["image_path"] == "rooms/kitchen.png"


async def test_get_inventory_happy(make_ctx: ToolContext) -> None:
    uuids = _seeded_uuids_via_ctx(make_ctx)
    result = await call_tool(
        "get_inventory",
        {"child_id": uuids["child_uuid"]},
        make_ctx,
    )
    assert result["error"] is None
    names = {row["display_name"] for row in result["data"]}
    assert "Lego" in names
    assert "Unicorn" in names


async def test_get_recent_transcript_happy(make_ctx: ToolContext) -> None:
    result = await call_tool("get_recent_transcript", {"window_sec": 300}, make_ctx)
    assert result["error"] is None
    assert any("play" in s.lower() for s in result["data"])


async def test_get_prior_steps_happy(make_ctx: ToolContext) -> None:
    uuids = _seeded_uuids_via_ctx(make_ctx)
    result = await call_tool(
        "get_prior_steps",
        {"activity_id": uuids["activity_uuid"]},
        make_ctx,
    )
    assert result["error"] is None
    assert len(result["data"]) == 2
    assert "comfy" in result["data"][0]


async def test_get_anti_signal_no_feedback(make_ctx: ToolContext) -> None:
    result = await call_tool(
        "get_anti_signal",
        {"template_id": "play_anytime_invent", "slot_dict": {"toy": "lego"}},
        make_ctx,
    )
    assert result["error"] is None
    assert result["data"]["blocked"] is False
    assert result["data"]["weight"] == 0.0


async def test_get_recent_transcript_default_window(
    make_ctx: ToolContext,
) -> None:
    """Default window_sec=300 is used when caller omits the field."""
    result = await call_tool("get_recent_transcript", {}, make_ctx)
    assert result["error"] is None


# --------------------------------------------------------------------- timeout / cancel


async def test_timeout_returns_recovery_not_raises(
    make_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolver that takes too long must return error='timeout'.

    M7: Pin the structured shape (``error`` + ``tool`` + a ``reason``
    that mentions timeout-or-exceeded) rather than substring-matching
    the formatted float of the configured timeout. The float string
    formatting can change without breaking the contract.
    """
    import toybox.ai.tools as tools_module

    async def _slow_dispatch(*args: object, **kwargs: object) -> object:
        await asyncio.sleep(2.0)
        raise AssertionError("dispatch should have been cancelled")

    monkeypatch.setattr(tools_module, "_dispatch", _slow_dispatch)
    result = await call_tool(
        "get_persona",
        {"persona_id": "wizard"},
        make_ctx,
        timeout_sec=0.05,
    )
    assert result["error"] == "timeout"
    assert result["tool"] == "get_persona"
    assert isinstance(result["reason"], str) and result["reason"]
    reason_lc = result["reason"].lower()
    assert "timeout" in reason_lc or "timed out" in reason_lc or "exceeded" in reason_lc, (
        f"reason must reference timeout/exceeded: {result['reason']!r}"
    )
    # Recovery shape contract: failure paths must surface no ``data``
    # and no ``result_summary`` content.
    assert result["data"] is None
    assert result["result_summary"] == ""


async def test_cancellation_is_safe(make_ctx: ToolContext) -> None:
    """Cancelling the surrounding task must not leak connections.

    The make_ctx fixture appends every opened connection to a tracking
    list. We assert:

    1. The cancelled call surfaces ``CancelledError`` cleanly (no
       leaked traceback into an unrelated frame).
    2. Every connection that was opened during the cancelled call is
       closed by the time the cancel propagates — proves the
       ``finally`` block in :func:`_dispatch._run` ran via
       ``to_thread`` teardown and didn't leak a row-level write lock
       that would block subsequent calls.
    3. A subsequent call still succeeds — proves no DB lock held by
       an unfinalised transaction.
    """

    async def _runner() -> object:
        return await call_tool("get_persona", {"persona_id": "wizard"}, make_ctx)

    task = asyncio.create_task(_runner())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # M3: the connection-cleanup contract — every connection opened
    # by the dispatcher's ``to_thread`` worker must be closed by the
    # time the cancel propagates back to us. The fixture's connection
    # factory tracks every connection it hands out; we walk that list
    # and assert each is closed.
    #
    # Note: the cancel may arrive before the to_thread worker even
    # spawned (e.g. before the validator returned), in which case
    # _LAST_OPENED_CONNS is empty and the loop is vacuous. We follow
    # up with an explicit synchronous call below — it ALWAYS opens
    # at least one connection, and the same closure contract applies.
    pre_call_conn_count = len(_LAST_OPENED_CONNS)
    for conn in list(_LAST_OPENED_CONNS):
        assert _connection_is_closed(conn), (
            "dispatcher leaked an open connection across cancellation"
        )

    # A subsequent call must still succeed (no DB lock held by an
    # unfinalised transaction). This call is guaranteed to open at
    # least one connection — pin closure on it as well so the
    # closure-on-success path is exercised even when the cancel
    # races ahead of the to_thread worker.
    result = await call_tool("get_persona", {"persona_id": "wizard"}, make_ctx)
    assert result["error"] is None
    assert len(_LAST_OPENED_CONNS) > pre_call_conn_count, (
        "follow-up call did not open a fresh connection"
    )
    for conn in _LAST_OPENED_CONNS[pre_call_conn_count:]:
        assert _connection_is_closed(conn), (
            "dispatcher left a connection open after a successful call"
        )


# --------------------------------------------------------------------- registry


def test_registered_tools_includes_all_carve_out_tools() -> None:
    expected = {
        "get_persona",
        "get_room",
        "get_inventory",
        "get_recent_transcript",
        "get_prior_steps",
        "get_anti_signal",
    }
    assert expected.issubset(set(REGISTERED_TOOLS))
