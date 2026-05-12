"""Production audio → play-queue persistence seam.

The production lifespan builds an :class:`EscalationDispatcher` with the
D9 injection set and hands its ``on_intent`` closure to the
:class:`TranscriptPipeline`. When the pipeline fires an :class:`Intent`,
the closure dispatches through the escalation gate; on a non-``None``
return it persists the dispatcher's in-memory :class:`Activity` via
:func:`_persist_dispatcher_activity` and emits the matching
``activity.state`` envelope.

Critical invariants pinned here:

* The persisted ``activities.id`` matches the dispatcher's chosen UUID
  (so the dispatcher's ``labeled_events`` row is NOT orphan).
* ``intent_source`` matches the originating ``Intent.name``.
* Exactly ONE ``labeled_events`` row lands per dispatch (the dispatcher
  writes it via the injected recorder; the persist helper does not
  double-write).
* OFFLINE / LOW modes still produce a row — the dispatcher's contract
  for those modes WITH an intent is "offline path returns an Activity",
  and the pipeline's ``not intents`` guard already short-circuits before
  reaching ``on_intent`` for the no-intents case.
* The connection opened per dispatch closes even if persistence raises.

The :class:`~toybox.ai.client.AnthropicClient` would require an OAuth
token on disk; production already falls through to the offline path when
the capability gate reports ``token_missing``. The fixture pins that
path by pointing ``TOYBOX_SECRETS_PATH`` at a non-existent file.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from toybox.ai.breaker import CircuitBreaker
from toybox.core.listening import ListeningMode, set_mode
from toybox.core.pubsub import PubSub
from toybox.core.queue import PROPOSED_STATE
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.main import (
    PRODUCTION_SESSION_ID,
    _build_production_dispatcher,
    _build_production_on_intent,
    _ensure_production_session,
)
from toybox.triggers.registry import Intent
from toybox.ws.envelope import Envelope
from toybox.ws.topics import Topic

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test SQLite file with migrations + production session row.

    The production builder reads ``TOYBOX_DB_PATH`` via :func:`resolve_db_path`
    for both the dispatcher's ``connection_factory`` and (indirectly) the
    judge-call factory. Pinning the env var per test keeps the production
    code paths byte-identical to the daemon's wiring; no monkeypatching of
    internal seams required.
    """
    db_path = tmp_path / "toybox.db"
    monkeypatch.setenv("TOYBOX_DB_PATH", str(db_path))
    # Force a no-token capability shape — guarantees the offline
    # fallback path inside the dispatcher even on developer machines
    # that have ``~/.toybox/secrets.json`` populated. The env var the
    # OAuth loader honours is ``TOYBOX_SECRETS_PATH``; pointing it at
    # a non-existent file resolves to ``token=None`` everywhere.
    monkeypatch.setenv("TOYBOX_SECRETS_PATH", str(tmp_path / "no-such-secrets.json"))
    conn = connect(db_path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    _ensure_production_session(db_path)
    return db_path


@pytest.fixture(autouse=True)
def _reset_pubsub_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the process-singleton :func:`toybox.ws.server.get_pubsub`.

    The production ``on_intent`` builder captures ``get_pubsub()`` at
    construction time and the dispatcher's publisher does the same.
    Pointing both at a per-test hub means a test's published envelopes
    don't leak into the real singleton (and other tests don't bleed
    state into us).
    """
    test_hub = PubSub(max_per_subscriber=64, coalesce_window_ms=0)
    monkeypatch.setattr("toybox.ws.server.get_pubsub", lambda: test_hub)
    monkeypatch.setattr("toybox.main.get_pubsub", lambda: test_hub)


def _conn_factory(db_path: Path) -> Callable[[], sqlite3.Connection]:
    def _factory() -> sqlite3.Connection:
        return connect(db_path, check_same_thread=False)

    return _factory


def _intent(name: str = "request_play", slot: str | None = "blocks") -> Intent:
    return Intent(name=name, slot=slot, pattern_id=f"pat_{name}")


def _set_listening_mode(db_path: Path, mode: ListeningMode) -> None:
    conn = connect(db_path, check_same_thread=False)
    try:
        set_mode(conn, mode)
    finally:
        conn.close()


def _proposed_activity_rows(db_path: Path) -> list[sqlite3.Row]:
    """Return the per-test DB's ``proposed``-state activity rows."""
    conn = connect(db_path, check_same_thread=False)
    try:
        return conn.execute(
            "SELECT id, session_id, state, intent_source, summary "
            "FROM activities WHERE state = ?",
            (PROPOSED_STATE,),
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Happy path: an intent on mode DEFAULT lands a proposed row + envelope
# ---------------------------------------------------------------------------


async def test_on_intent_dispatch_persists_proposed_row_and_emits_envelope(
    isolated_db: Path,
) -> None:
    """An :class:`Intent` dispatched through the production handler MUST:

    1. Hit the dispatcher's offline-fallback path (no real Anthropic call).
    2. Persist a ``proposed`` row in ``activities`` with
       ``session_id == PRODUCTION_SESSION_ID``.
    3. Publish an ``activity.state`` envelope whose ``id`` matches the
       persisted row (proves the dispatcher's chosen UUID survives —
       no orphan ``labeled_events`` row).
    4. Carry ``intent_source == intent.name`` (proves the originating
       intent is preserved).
    5. Carry a ``title`` matching the persisted summary (proves the
       dispatcher's chosen Activity content survived).
    6. Produce exactly ONE ``labeled_events`` row FK'd to the same id
       (proves the dispatcher's recorder + persist seam dual-write
       resolved to a single row).
    """
    _set_listening_mode(isolated_db, ListeningMode.DEFAULT)

    from toybox.ws.server import get_pubsub

    pubsub = get_pubsub()
    subscriber = pubsub.subscribe([Topic.activity_state])
    try:
        dispatcher, _ = _build_production_dispatcher(
            breaker=CircuitBreaker(),
            publisher=lambda env: pubsub.publish(env),
            conn_factory=_conn_factory(isolated_db),
        )
        on_intent = _build_production_on_intent(
            dispatcher, _conn_factory(isolated_db)
        )

        intent = _intent()
        await on_intent(intent)

        # Drain the queue with a short timeout — persistence emits
        # synchronously inside the helper so the envelope is there.
        env: Envelope = await asyncio.wait_for(subscriber.get(), timeout=2.0)
        assert env is not None
        payload = env.payload
        assert payload["state"] == PROPOSED_STATE
    finally:
        subscriber.close()

    rows = _proposed_activity_rows(isolated_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == PRODUCTION_SESSION_ID
    assert row["state"] == PROPOSED_STATE

    # The envelope's id must match the persisted row's id — the
    # dispatcher's chosen UUID is what landed in BOTH places (and in
    # ``labeled_events``).
    assert payload["id"] == row["id"]

    # Intent preservation: ``intent_source`` is what Phase E SFT exports
    # key on. The handler MUST persist the originating intent name.
    assert row["intent_source"] == intent.name

    # The dispatcher's chosen title (from the offline generator) MUST
    # match the envelope's title. ``activities.summary`` is a JSON
    # envelope carrying the title; the WS payload surfaces it at the
    # top level via ``_row_to_response``.
    import json

    persisted_summary = json.loads(row["summary"])
    assert persisted_summary["title"] == payload["title"]
    assert isinstance(payload["title"], str)
    assert payload["title"]  # non-empty

    # The dispatcher's ``labeled_events`` recorder fires BEFORE
    # ``on_transcript`` returns (see ``escalation.py:_record``). After
    # ``_persist_dispatcher_activity`` runs with the same id, the row
    # is no longer orphan — exactly ONE labeled_events row references it.
    conn = connect(isolated_db, check_same_thread=False)
    try:
        labeled_count = conn.execute(
            "SELECT COUNT(*) AS n FROM labeled_events WHERE activity_id = ?",
            (row["id"],),
        ).fetchone()["n"]
    finally:
        conn.close()
    assert labeled_count == 1


# ---------------------------------------------------------------------------
# OFFLINE / LOW modes WITH an intent → real dispatcher routes offline →
# the offline-fallback persists a row (the no-op state is only reachable
# via the pipeline's ``not intents`` short-circuit upstream).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode",
    [ListeningMode.OFFLINE, ListeningMode.LOW],
)
async def test_on_intent_offline_path_persists_in_low_modes(
    isolated_db: Path,
    mode: ListeningMode,
) -> None:
    """Modes 1-2 are the offline-only contract.

    The real dispatcher's behavior in OFFLINE / LOW WITH an intent
    (``core/escalation.py:430-445``) is to call the offline generator
    and return its Activity — NO Claude call. The production handler
    must therefore persist a row + emit an envelope in these modes,
    same as DEFAULT but with the offline generator as the source.

    This pins that Claude is NOT called (no OAuth, no SDK init) AND
    that the offline path is wired end-to-end through the handler. The
    "dispatcher returns None" case only happens when the upstream
    pipeline gives the handler no intents to dispatch — and the pipeline
    short-circuits before reaching ``on_intent`` in that case.
    """
    _set_listening_mode(isolated_db, mode)

    from toybox.ws.server import get_pubsub

    pubsub = get_pubsub()
    subscriber = pubsub.subscribe([Topic.activity_state])
    try:
        dispatcher, _ = _build_production_dispatcher(
            breaker=CircuitBreaker(),
            publisher=lambda env: pubsub.publish(env),
            conn_factory=_conn_factory(isolated_db),
        )
        on_intent = _build_production_on_intent(
            dispatcher, _conn_factory(isolated_db)
        )

        await on_intent(_intent())

        env: Envelope = await asyncio.wait_for(subscriber.get(), timeout=2.0)
        assert env.payload["state"] == PROPOSED_STATE
    finally:
        subscriber.close()

    rows = _proposed_activity_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0]["session_id"] == PRODUCTION_SESSION_ID


# ---------------------------------------------------------------------------
# Connection lifecycle: persist failure must still close the connection.
# ---------------------------------------------------------------------------


class _TrackingConn:
    """Lightweight delegating wrapper that counts ``close()`` calls.

    :class:`sqlite3.Connection` exposes ``close`` as a read-only slot, so
    we cannot rebind it on an instance. Wrapping the connection in a
    small proxy lets us count closes without touching internals. Only
    the methods the production handler actually invokes are forwarded —
    keeps the wrapper small and the test pinned to the real call shape.
    """

    def __init__(self, inner: sqlite3.Connection, closed: dict[str, int]) -> None:
        self._inner = inner
        self._closed = closed

    def close(self) -> None:
        self._closed["n"] += 1
        self._inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


async def test_on_intent_propose_failure_closes_conn(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persist-helper raise MUST NOT leak the per-intent SQLite handle.

    The handler wraps the dispatch + persist seam in ``try / finally`` so
    the connection opened at the top of ``_on_intent`` always closes.
    Stub the persist helper to raise, then assert the close count
    incremented by exactly one (tracked via a delegating wrapper around
    the real :class:`sqlite3.Connection`).
    """
    _set_listening_mode(isolated_db, ListeningMode.DEFAULT)

    from toybox.ws.server import get_pubsub

    pubsub = get_pubsub()
    base_factory = _conn_factory(isolated_db)
    closed_count: dict[str, int] = {"n": 0}

    def _tracking_factory() -> sqlite3.Connection:
        # ``_TrackingConn`` is structurally a Connection for the
        # handler's call sites (``current_mode``, ``close``,
        # ``asyncio.to_thread(_persist_dispatcher_activity, ...)``); the
        # type ignore here documents that this is intentional.
        return _TrackingConn(base_factory(), closed_count)  # type: ignore[return-value]

    dispatcher, _ = _build_production_dispatcher(
        breaker=CircuitBreaker(),
        publisher=lambda env: pubsub.publish(env),
        conn_factory=base_factory,
    )

    def _boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("persist exploded")

    monkeypatch.setattr("toybox.main._persist_dispatcher_activity", _boom)

    on_intent = _build_production_on_intent(dispatcher, _tracking_factory)

    # The handler must swallow the persist exception — the kid-facing
    # pipeline cannot break because of a downstream persistence bug.
    await on_intent(_intent())

    # The one connection opened by ``_on_intent`` MUST have been closed
    # exactly once.
    assert closed_count["n"] == 1, (
        f"connection close count = {closed_count['n']}, expected 1; "
        "the handler's finally block did not close the per-intent conn"
    )

    # And nothing landed in the DB (persist raised before any INSERT).
    assert _proposed_activity_rows(isolated_db) == []


# ---------------------------------------------------------------------------
# slot_fills_json invariant: the dispatcher's offline-path generator
# resolves a slot map (toy / room / adjective), and the persist seam MUST
# carry it into ``activities.slot_fills_json``. The migration default
# ``'{}'`` would leave ``_row_to_response`` → ``_render_template_plan_steps``
# rendering un-substituted ``{toy}`` / ``{room}`` placeholders in the
# parent's suggestion-card preview.
# ---------------------------------------------------------------------------


async def test_on_intent_persists_resolved_slot_fills(isolated_db: Path) -> None:
    """The dispatcher's resolved ``slot_fills`` MUST land in the persisted row.

    The offline generator populates ``activity.metadata['slot_fills']``
    with the slot-name → resolved-value map for every template slot
    (toy / room / adjective). The dispatcher seam used to omit this
    column from its INSERT, falling back to the migration default
    ``'{}'``; that broke the parent's suggestion preview because the
    template body was rendered with no fills, leaving raw ``{toy}`` /
    ``{room}`` placeholders visible to the parent.

    Locking the assertion here pins the byte-identical write shape
    against the canonical ``api/activities.py:_persist_activity`` —
    any regression that drops the column again fails this test.
    """
    import json

    _set_listening_mode(isolated_db, ListeningMode.DEFAULT)

    from toybox.ws.server import get_pubsub

    pubsub = get_pubsub()
    dispatcher, _ = _build_production_dispatcher(
        breaker=CircuitBreaker(),
        publisher=lambda env: pubsub.publish(env),
        conn_factory=_conn_factory(isolated_db),
    )
    on_intent = _build_production_on_intent(
        dispatcher, _conn_factory(isolated_db)
    )

    # ``request_play`` resolves through the freeplay template family
    # which has ``{toy}`` / ``{room}`` slots — the offline generator
    # will fill them in.
    await on_intent(_intent())

    conn = connect(isolated_db, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT slot_fills_json FROM activities WHERE state = ?",
            (PROPOSED_STATE,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "no proposed row was persisted"

    # The migration default is ``'{}'`` — anything other than that
    # proves the dispatcher seam carried the generator's slot map
    # through to persistence.
    assert row["slot_fills_json"] != "{}", (
        "slot_fills_json is the migration default; the persist helper "
        "omitted the column from its INSERT (regression of the iter-3 fix)"
    )

    # Stronger: the parsed payload is a non-empty dict (the offline
    # generator populated at least one slot for this intent).
    parsed = json.loads(row["slot_fills_json"])
    assert isinstance(parsed, dict)
    assert parsed, "slot_fills_json parsed to an empty dict — no slots resolved"


# ---------------------------------------------------------------------------
# Architectural lock: one intent = exactly one labeled_events row.
# ---------------------------------------------------------------------------


async def test_labeled_events_row_count_per_intent(isolated_db: Path) -> None:
    """One dispatched intent MUST produce EXACTLY ONE ``labeled_events`` row.

    The prior iter-1 architecture called ``_do_propose`` after the
    dispatcher, which double-wrote: the dispatcher's recorder fired
    once (orphan row), then ``_do_propose`` fired a second row keyed on
    its own regenerated Activity. Locking the count at 1 here is the
    structural invariant for the pivoted architecture — any future
    regression that re-introduces a second write fails this test.
    """
    _set_listening_mode(isolated_db, ListeningMode.DEFAULT)

    from toybox.ws.server import get_pubsub

    pubsub = get_pubsub()
    dispatcher, _ = _build_production_dispatcher(
        breaker=CircuitBreaker(),
        publisher=lambda env: pubsub.publish(env),
        conn_factory=_conn_factory(isolated_db),
    )
    on_intent = _build_production_on_intent(
        dispatcher, _conn_factory(isolated_db)
    )

    await on_intent(_intent())

    conn = connect(isolated_db, check_same_thread=False)
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM labeled_events"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert total == 1, (
        f"expected exactly 1 labeled_events row per dispatch, got {total}; "
        "double-write architecture has regressed"
    )
