"""Unit coverage for :mod:`toybox.core.play_cadence`.

The loop is the autonomous play-queue tick — wakes every
``play_cadence_seconds``, reads the live target depth + listening mode
each iteration, and fires a default-seed propose call via
:func:`toybox.api.activities._do_propose` when the proposed queue is
under depth.

The full ``_do_propose`` stack drags the generator, ai client, and
labeled-events machinery, so most tests below monkeypatch the late-
imported ``_do_propose`` symbol on :mod:`toybox.api.activities` to a
thin stub that just records the call + inserts a single ``proposed``
row. Sleeps are intercepted via a monkeypatched ``asyncio.sleep`` that
resolves immediately so the test runs in < 1s without wall-clock delay.

Key tests:

* ``test_loop_converges_to_target_depth`` — target=3, cadence=10s, mode
  DEFAULT → the loop fires exactly 3 proposes then plateaus (subsequent
  ticks are short-circuited by the ``proposed_count >= target`` gate).
* ``test_loop_skips_when_cadence_zero`` — cadence=0 → zero proposes over
  multiple ticks; the disabled-poll branch parks and continues.
* ``test_loop_skips_when_mode_offline`` — mode=OFFLINE, cadence=10 →
  zero proposes (the listening-mode gate fires before the propose
  branch).
* ``test_propose_body_fields_per_tick`` — body construction is
  exercised end-to-end: two consecutive ticks produce two distinct
  ``ProposeRequest`` bodies with ``intent='request_play'``,
  ``slot='freeplay'``, ``hour==now.hour``, and distinct ``seed`` values.
* ``test_dynamic_cap_evicts_older_proposed`` — with
  ``play_target_depth=1`` and one pre-existing ``proposed`` row, a
  fresh propose evicts the older row (proves
  :func:`activities._do_propose` reads ``play_target_depth`` per call,
  not the legacy ``PROPOSED_QUEUE_CAP=5``).
* ``test_loop_picks_up_settings_change_between_ticks`` — settings hot-
  reload: a ``play_cadence_seconds`` change between ticks is honoured
  on the next sleep without restart.
* ``test_judge_call_factory_resolved_per_tick`` — the factory is
  invoked once per propose tick (proves a token added/removed
  mid-process is honoured on the next tick).
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from toybox.api import activities as activities_module
from toybox.core import play_cadence, play_cadence_seconds, play_target_depth
from toybox.core.listening import ListeningMode, set_mode
from toybox.core.queue import PROPOSED_STATE, proposed_count
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

# Test session id seeded in the per-test DB so ``_do_propose`` stub
# inserts FK-valid ``activities`` rows.
_TEST_SESSION_ID = "test-cadence-session"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh migrated SQLite file with a seeded test session row."""
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (_TEST_SESSION_ID, "2026-01-01T00:00:00Z"),
            )
    finally:
        conn.close()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """One open connection scoped to the test body."""
    connection = connect(db_path, check_same_thread=False)
    try:
        yield connection
    finally:
        connection.close()


def _insert_proposed_row(
    conn: sqlite3.Connection,
    row_id: str | None = None,
    created_at: str = "2026-05-11T00:00:00Z",
) -> str:
    """Insert one ``proposed`` activity row directly, bypassing the API.

    Stand-in for what ``_do_propose`` would write in production. The
    cadence loop's contract is to keep ``proposed_count(conn) >=
    target`` true once it's reached; this helper lets the stub satisfy
    that contract without dragging the full generator.
    """
    activity_id = row_id or str(uuid.uuid4())
    with conn:
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, persona_id, "
            " child_ids, room_ids, toy_ids, intent_source, created_at, "
            " started_at, ended_at) "
            "VALUES (?, ?, ?, 1, '{}', NULL, NULL, NULL, NULL, "
            "'cadence-test', ?, NULL, NULL)",
            (
                activity_id,
                _TEST_SESSION_ID,
                PROPOSED_STATE,
                created_at,
            ),
        )
    return activity_id


class _RecordingPropose:
    """Stub matching ``activities._do_propose`` shape.

    Production ``_do_propose`` is ``(body, conn, pubsub, judge_call=)`` —
    the recorder captures the same arguments + inserts a ``proposed``
    row so the proposed-count gate in the cadence loop behaves the
    same way as the real implementation. Capturing ``body`` lets a
    test assert on intent / slot / hour / seed without monkeypatching
    the body builder.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    def __call__(
        self,
        body: object,
        conn: sqlite3.Connection,
        pubsub: object,
        judge_call: object = None,
    ) -> None:
        # Insert FIRST, record AFTER. Tests poll on ``len(self.calls)``
        # to know when to cancel the task; appending before the insert
        # would let the poller cancel mid-INSERT and the loop's
        # ``finally: conn.close()`` would race the worker thread's
        # sqlite write (Windows access violation).
        _insert_proposed_row(conn)
        self.calls.append((body, pubsub, judge_call))


def _patch_do_propose(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _RecordingPropose,
) -> None:
    """Monkeypatch the late-imported ``activities._do_propose`` symbol.

    ``play_cadence._do_propose_blocking`` does
    ``from ..api.activities import _do_propose`` per call, so replacing
    the attribute on the source module is what the late import resolves
    to. Tests that go through this seam exercise the real
    :func:`play_cadence._build_propose_body` — that's the whole point
    after HIGH-1 review: body construction must be on the test path.
    """
    monkeypatch.setattr(activities_module, "_do_propose", recorder)


class _FakePubSub:
    """Stand-in pubsub object — the cadence loop only passes it through."""


async def _drive_loop_until(
    task: asyncio.Task[None],
    predicate,  # type: ignore[no-untyped-def]
    *,
    timeout: float = 1.0,
) -> None:
    """Wait until ``predicate()`` is true or ``timeout`` seconds elapse.

    The loop is driven by a monkeypatched ``sleep`` that resolves
    immediately, so the predicate flips quickly. The wall-clock cap
    is purely a defence against an infinite loop in a regression.
    """

    async def _wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(_wait(), timeout=timeout)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------
# Core behaviour: queue convergence + gating
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_converges_to_target_depth(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cadence loop fires propose until the queue reaches target depth.

    Setup: target=3, cadence=10s, mode=DEFAULT. The recording stub
    inserts one ``proposed`` row per call. After three ticks the
    proposed queue hits depth and the loop's
    ``proposed_count >= target`` guard short-circuits subsequent
    ticks — the count plateaus at 3.
    """
    play_target_depth.set(conn, 3)
    play_cadence_seconds.set(conn, 10)
    set_mode(conn, ListeningMode.DEFAULT)

    recorder = _RecordingPropose()
    _patch_do_propose(monkeypatch, recorder)

    fake_pubsub = _FakePubSub()

    tick_count = {"n": 0}

    # ``_counting_sleep`` doubles as the test's progress counter:
    # incrementing per sleep call lets the predicate wait for "N
    # ticks have fired" without polling wall-clock time. The
    # ``await asyncio.sleep(0)`` yield is what lets the predicate
    # poller in ``_drive_loop_until`` interleave with the cadence
    # task — without it the tight tick body could starve the poller.

    async def _counting_sleep(_seconds: float) -> None:
        tick_count["n"] += 1
        await asyncio.sleep(0)

    task = play_cadence.start_cadence_loop(
        lambda: fake_pubsub,  # type: ignore[arg-type]
        db_path,
        sleep=_counting_sleep,
    )
    try:
        # Drive ticks well past the target — once the queue reaches
        # 3, subsequent ticks short-circuit and ``recorder.calls``
        # stops growing. Waiting for 10+ ticks (well past 3) proves
        # the plateau holds.
        await _drive_loop_until(
            task,
            lambda: tick_count["n"] >= 10,
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Exactly 3 inserts — the loop stopped emitting once the count
    # hit target. The stub inserts before returning, so the post-
    # call queue count equals the call count exactly.
    assert proposed_count(conn) == 3
    assert len(recorder.calls) == 3


@pytest.mark.asyncio
async def test_loop_skips_when_cadence_zero(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``play_cadence_seconds == 0`` disables propose firing.

    The loop still wakes (on the disabled-poll interval) so a
    settings flip back to non-zero is honoured, but ``cadence == 0``
    short-circuits the post-sleep branch before any propose call.
    """
    play_target_depth.set(conn, 3)
    play_cadence_seconds.set(conn, 0)
    set_mode(conn, ListeningMode.DEFAULT)

    recorder = _RecordingPropose()
    _patch_do_propose(monkeypatch, recorder)

    sleep_calls: list[float] = []

    async def _instant_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        await asyncio.sleep(0)

    fake_pubsub = _FakePubSub()

    task = play_cadence.start_cadence_loop(
        lambda: fake_pubsub,  # type: ignore[arg-type]
        db_path,
        sleep=_instant_sleep,
    )
    try:
        # Let the loop tick at least 5 times — well past the point
        # any propose would have fired if the cadence==0 gate were
        # broken.
        await _drive_loop_until(
            task,
            lambda: len(sleep_calls) >= 5,
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert recorder.calls == []
    assert proposed_count(conn) == 0


@pytest.mark.asyncio
async def test_loop_skips_when_mode_offline(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OFFLINE mode keeps the cadence loop silent regardless of cadence.

    Modes 1-2 (OFFLINE / LOW) are the "no autonomous propose" range —
    the dispatcher refuses Claude there and the cadence loop must
    respect the same boundary. Even with cadence=10 (enabled), zero
    propose calls fire while the mode is OFFLINE.
    """
    play_target_depth.set(conn, 3)
    play_cadence_seconds.set(conn, 10)
    set_mode(conn, ListeningMode.OFFLINE)

    recorder = _RecordingPropose()
    _patch_do_propose(monkeypatch, recorder)

    sleep_calls: list[float] = []

    async def _instant_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        await asyncio.sleep(0)

    fake_pubsub = _FakePubSub()

    task = play_cadence.start_cadence_loop(
        lambda: fake_pubsub,  # type: ignore[arg-type]
        db_path,
        sleep=_instant_sleep,
    )
    try:
        await _drive_loop_until(
            task,
            lambda: len(sleep_calls) >= 5,
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert recorder.calls == []
    assert proposed_count(conn) == 0


@pytest.mark.asyncio
async def test_loop_skips_low_mode(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOW mode is also in the no-autonomous-propose range.

    Companion to ``test_loop_skips_when_mode_offline`` — LOW (mode 2)
    is the second mode the dispatcher's documentation pins as
    "trigger match → offline only; NEVER call Claude." The cadence
    loop honours the same boundary, so a LOW-mode household never
    sees an autonomous propose either.
    """
    play_target_depth.set(conn, 3)
    play_cadence_seconds.set(conn, 10)
    set_mode(conn, ListeningMode.LOW)

    recorder = _RecordingPropose()
    _patch_do_propose(monkeypatch, recorder)

    sleep_calls: list[float] = []

    async def _instant_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        await asyncio.sleep(0)

    fake_pubsub = _FakePubSub()

    task = play_cadence.start_cadence_loop(
        lambda: fake_pubsub,  # type: ignore[arg-type]
        db_path,
        sleep=_instant_sleep,
    )
    try:
        await _drive_loop_until(
            task,
            lambda: len(sleep_calls) >= 5,
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert recorder.calls == []
    assert proposed_count(conn) == 0


@pytest.mark.asyncio
async def test_loop_resumes_after_target_drained(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the queue drops below target, the loop fires propose again.

    Pins the "read settings + count every tick" contract: after the
    queue hits depth (3) the loop pauses, but as soon as the parent
    dismisses a row (count → 2), the next tick fires a fresh propose
    (count → 3 again).
    """
    play_target_depth.set(conn, 3)
    play_cadence_seconds.set(conn, 10)
    set_mode(conn, ListeningMode.DEFAULT)

    recorder = _RecordingPropose()
    _patch_do_propose(monkeypatch, recorder)

    async def _instant_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    fake_pubsub = _FakePubSub()

    task = play_cadence.start_cadence_loop(
        lambda: fake_pubsub,  # type: ignore[arg-type]
        db_path,
        sleep=_instant_sleep,
    )
    try:
        # Wait for the initial fill to plateau at 3.
        await asyncio.wait_for(
            _wait_for_count(conn, 3),
            timeout=1.0,
        )
        plateau_calls = len(recorder.calls)
        assert plateau_calls == 3

        # Drain one row — the next loop iteration should fire a
        # propose because count (2) is now below target (3). sqlite
        # has no LIMIT on DELETE without a compile flag, so use a
        # subquery-bounded delete to drop exactly one row.
        with conn:
            conn.execute(
                "DELETE FROM activities WHERE id = "
                "(SELECT id FROM activities WHERE state = ? "
                " ORDER BY created_at ASC LIMIT 1)",
                (PROPOSED_STATE,),
            )

        await asyncio.wait_for(
            _wait_for_count(conn, 3),
            timeout=1.0,
        )
        assert len(recorder.calls) == plateau_calls + 1
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_loop_survives_propose_failure(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A propose-call exception logs and the loop continues to the next tick.

    Without the broad-except in the propose-phase block, a single
    bad tick (e.g. a transient sqlite error inside ``_do_propose``)
    would crash the task and stop all subsequent ticks. This pins
    the contract that the loop keeps running after a fault.
    """
    play_target_depth.set(conn, 3)
    play_cadence_seconds.set(conn, 10)
    set_mode(conn, ListeningMode.DEFAULT)

    call_count = {"n": 0}

    def _faulty_blocking(
        _conn: sqlite3.Connection,
        _pubsub: object,
        _judge_call: object,
    ) -> None:
        # The stub deliberately does NOT touch the passed-in
        # connection — opening a sqlite cursor on a worker thread
        # and racing the main thread's per-tick reads triggers a
        # Windows ``sqlite3`` access violation. The fault path only
        # cares about exception propagation, not row-level side
        # effects, so we keep this thread-isolated and assert via
        # the call counter.
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("induced propose fault")

    monkeypatch.setattr(play_cadence, "_do_propose_blocking", _faulty_blocking)

    async def _instant_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    fake_pubsub = _FakePubSub()

    task = play_cadence.start_cadence_loop(
        lambda: fake_pubsub,  # type: ignore[arg-type]
        db_path,
        sleep=_instant_sleep,
    )
    try:
        # Wait until we've seen at least one fault AND one successful
        # tick — proves the loop survived the fault.
        await asyncio.wait_for(
            _wait_for_predicate(lambda: call_count["n"] >= 2),
            timeout=1.0,
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert call_count["n"] >= 2


# ---------------------------------------------------------------------
# Body construction (HIGH-1): exercised end-to-end through the real
# ``_build_propose_body`` helper. Patches the late-imported
# ``activities._do_propose`` so the body builder is on the test path.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_body_fields_per_tick(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive ticks produce well-formed, distinct propose bodies.

    Pins the J2 spec: every autonomous tick builds a
    :class:`ProposeRequest` with ``intent='request_play'``,
    ``slot='freeplay'``, ``hour==now.hour``, and a fresh per-tick
    ``seed``. A regression that hardcoded the seed, dropped ``hour``,
    or flipped the intent/slot would be caught here.
    """
    play_target_depth.set(conn, 5)  # Allow at least two distinct propose ticks.
    play_cadence_seconds.set(conn, 10)
    set_mode(conn, ListeningMode.DEFAULT)

    recorder = _RecordingPropose()
    _patch_do_propose(monkeypatch, recorder)

    async def _instant_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    fake_pubsub = _FakePubSub()

    task = play_cadence.start_cadence_loop(
        lambda: fake_pubsub,  # type: ignore[arg-type]
        db_path,
        sleep=_instant_sleep,
    )
    try:
        # Wait by ``proposed_count`` (read on the main thread, sees
        # the worker thread's committed inserts in WAL mode) rather
        # than ``len(recorder.calls)``: the count is incremented
        # AFTER the worker thread's sqlite COMMIT, so the loop is
        # guaranteed to be past the to_thread await before we
        # cancel. Avoids a Windows access violation where cancel +
        # ``conn.close()`` races a worker still inside INSERT.
        await asyncio.wait_for(
            _wait_for_count(conn, 2),
            timeout=1.0,
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    body_a = recorder.calls[0][0]
    body_b = recorder.calls[1][0]
    # ``intent`` + ``slot`` are the static contract per plan §5.
    assert body_a.intent == "request_play"
    assert body_a.slot == "freeplay"
    assert body_b.intent == "request_play"
    assert body_b.slot == "freeplay"
    # ``hour`` must reflect wall-clock; tolerate the test crossing an
    # hour boundary mid-run by accepting either ``now.hour`` value.
    now_hour = datetime.now(UTC).hour
    assert body_a.hour in (now_hour, (now_hour - 1) % 24)
    assert body_b.hour in (now_hour, (now_hour - 1) % 24)
    # ``seed`` must be fresh per tick — a regression that pinned the
    # seed to 0 (or any constant) would surface here. Two draws from a
    # ``secrets.randbelow(2**31)`` distribution collide with vanishing
    # probability so a single-shot inequality assertion is safe.
    assert body_a.seed != body_b.seed


# ---------------------------------------------------------------------
# Dynamic cap (HIGH-2): the activities.py change reads
# ``play_target_depth.get(conn)`` per propose call instead of the legacy
# constant ``PROPOSED_QUEUE_CAP=5``. Drive ``_do_propose`` end-to-end
# with a stubbed generator and verify the older ``proposed`` row is
# evicted when target is 1.
# ---------------------------------------------------------------------


def test_dynamic_cap_evicts_older_proposed(
    db_path: Path,
    conn: sqlite3.Connection,
) -> None:
    """``_do_propose`` reads ``play_target_depth`` for eviction cap.

    Pins the activities.py:1258 change: a regression that re-pinned
    the literal ``PROPOSED_QUEUE_CAP=5`` would leave older proposed
    rows in place when ``play_target_depth=1`` is set. This test
    drives ``_do_propose`` directly (not through the cadence loop —
    the loop's ``proposed_count >= target`` gate would short-circuit
    before eviction has a chance to run) with a stubbed offline
    generator so the rest of the propose path stays on production
    code, and asserts that:

    * the older ``proposed`` row is transitioned to ``dismissed``;
    * exactly one ``proposed`` row remains (the freshly-inserted one).

    A regression that hardcoded ``cap=5`` would let both the old + new
    rows live as ``proposed`` and ``proposed_count`` would be 2.

    Synchronous test — ``_do_propose`` is not async; no event loop is
    needed.
    """
    play_target_depth.set(conn, 1)

    # Pre-seed an old ``proposed`` row. Its ``created_at`` is in the
    # past so ``oldest_proposed_ids`` orders it first.
    older_id = _insert_proposed_row(
        conn,
        row_id="older-row",
        created_at="2026-01-01T00:00:00Z",
    )
    assert proposed_count(conn) == 1

    # Drive ``_do_propose`` directly with a no-op pubsub so the
    # cadence-loop gate doesn't interfere. The propose path itself
    # runs end-to-end through the real ``evict_oldest_for_capacity``
    # + insert, so the cap behaviour under test actually fires.
    from toybox.api.activities import _do_propose
    from toybox.core.pubsub import PubSub

    pubsub = PubSub()
    body = activities_module.ProposeRequest(
        intent="boredom",
        slot=None,
        hour=12,
        seed=7,
        session_id=_TEST_SESSION_ID,
    )
    _do_propose(body, conn, pubsub, judge_call=None)

    # Older row dismissed by eviction; exactly one ``proposed`` row
    # remains (the freshly-inserted one). A regression that pinned
    # ``cap=5`` would leave both rows ``proposed`` (count==2).
    assert _state_of(conn, older_id) == "dismissed"
    assert proposed_count(conn) == 1


# ---------------------------------------------------------------------
# Settings hot-reload (MEDIUM-2): the loop re-reads cadence / target /
# mode every tick. A change between ticks is honoured on the next
# sleep without restart.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_picks_up_settings_change_between_ticks(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``play_cadence_seconds`` change between ticks is honoured next tick.

    Both ``cadence`` and ``target`` (and ``mode``) flow through the
    same ``_read_tick_settings`` helper, so proving cadence is re-read
    each tick is sufficient evidence the helper is doing its job — we
    don't need separate tests for target/mode.

    Setup: start with cadence=10s, target=5; observe first sleep arg
    is 10. Flip cadence to 30s before the second tick fires; observe
    the second sleep arg is 30.
    """
    play_target_depth.set(conn, 5)
    play_cadence_seconds.set(conn, 10)
    set_mode(conn, ListeningMode.DEFAULT)

    recorder = _RecordingPropose()
    _patch_do_propose(monkeypatch, recorder)

    sleep_calls: list[float] = []
    sleep_gate = asyncio.Event()
    flipped = {"v": False}

    async def _gated_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        # After the first sleep arg is recorded, flip the setting in
        # a side connection so the second tick reads the new value.
        # Using a separate connection mirrors how the parent UI
        # writes settings while the cadence task runs concurrently.
        if not flipped["v"]:
            flipped["v"] = True
            flip_conn = connect(db_path, check_same_thread=False)
            try:
                play_cadence_seconds.set(flip_conn, 30)
            finally:
                flip_conn.close()
        sleep_gate.set()
        await asyncio.sleep(0)

    fake_pubsub = _FakePubSub()

    task = play_cadence.start_cadence_loop(
        lambda: fake_pubsub,  # type: ignore[arg-type]
        db_path,
        sleep=_gated_sleep,
    )
    try:
        await asyncio.wait_for(
            _wait_for_predicate(lambda: len(sleep_calls) >= 2),
            timeout=1.0,
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # First sleep used the pre-flip value (10); second used post-flip (30).
    assert sleep_calls[0] == 10
    assert sleep_calls[1] == 30


# ---------------------------------------------------------------------
# Judge_call factory (MEDIUM-1): the factory is invoked once per
# propose tick so a token added/removed mid-process is honoured on the
# next tick without a restart.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_call_factory_resolved_per_tick(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``judge_call_factory`` runs once per propose tick.

    Pins the MEDIUM-1 contract: the factory is invoked PER TICK (not
    captured once at startup), so an OAuth token swap during the
    process's lifetime is picked up by the very next propose. Asserts
    on call count + ordering — the factory's nth return is what the
    nth ``_do_propose`` sees as ``judge_call``.
    """
    play_target_depth.set(conn, 5)
    play_cadence_seconds.set(conn, 10)
    set_mode(conn, ListeningMode.DEFAULT)

    recorder = _RecordingPropose()
    _patch_do_propose(monkeypatch, recorder)

    factory_calls = {"n": 0}

    def _factory() -> object:
        factory_calls["n"] += 1
        # Return a distinguishable sentinel per call so the test can
        # check that the nth ``_do_propose`` saw the nth factory
        # result — not some captured first value.
        return f"judge-{factory_calls['n']}"

    async def _instant_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    fake_pubsub = _FakePubSub()

    task = play_cadence.start_cadence_loop(
        lambda: fake_pubsub,  # type: ignore[arg-type]
        db_path,
        sleep=_instant_sleep,
        judge_call_factory=_factory,
    )
    try:
        # See ``test_propose_body_fields_per_tick`` — poll on the
        # post-COMMIT count, not ``recorder.calls``, to avoid the
        # cancel-races-INSERT access violation on Windows.
        await asyncio.wait_for(
            _wait_for_count(conn, 2),
            timeout=1.0,
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Factory invoked once per propose tick — at least 2 calls seen.
    assert factory_calls["n"] >= 2
    # And the nth propose received the nth factory result, not a
    # captured first value.
    judge_calls = [c[2] for c in recorder.calls]
    assert judge_calls[0] == "judge-1"
    assert judge_calls[1] == "judge-2"


# ---------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------


def _state_of(conn: sqlite3.Connection, activity_id: str) -> str:
    """Return the ``state`` of a given activity row, or ``''`` if absent."""
    row = conn.execute(
        "SELECT state FROM activities WHERE id = ?",
        (activity_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(row["state"])


async def _wait_for_count(conn: sqlite3.Connection, target: int) -> None:
    """Poll the proposed-count until it hits ``target``."""
    while proposed_count(conn) < target:
        await asyncio.sleep(0)


async def _wait_for_predicate(predicate) -> None:  # type: ignore[no-untyped-def]
    """Poll until ``predicate()`` returns truthy."""
    while not predicate():
        await asyncio.sleep(0)
