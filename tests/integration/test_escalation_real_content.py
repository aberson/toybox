"""Step 19 wiring: dispatcher resolves real catalog content for BOTH paths.

Iter-1 of step 19 only wired ``/api/activities/propose`` (the kiosk REST
path). The dispatcher's offline trigger path AND the Claude directive
plumbing both shipped as dead code. Iter-2 fixes that by injecting a
``connection_factory`` into :class:`EscalationDispatcher` and passing
the resolved toys/rooms/children through to:

* ``_offline_activity`` → the offline generator (real toy names + the
  banned-themes filter both fire),
* ``_try_claude``       → :func:`build_claude_directive` builds the
  ``"Do NOT include any of: ..."`` insert and threads it into
  :func:`_claude_system_prompt`.

These tests pin both ends.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from toybox.activities.models import Activity, ActivityStep
from toybox.ai.breaker import CircuitBreaker
from toybox.ai.client import AIResponse, StubClient
from toybox.audio.stt import Transcript
from toybox.core.capability import CapabilityReason
from toybox.core.escalation import EscalationDispatcher
from toybox.core.listening import ListeningMode
from toybox.core.throttle import MinIntervalThrottle
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.triggers.registry import Intent

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _migrated_db(path: Path) -> Path:
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    return path


def _seed_toys(db_path: Path, toys: list[tuple[str, str]]) -> None:
    conn = connect(db_path)
    try:
        with conn:
            for tid, name in toys:
                conn.execute(
                    "INSERT INTO toys "
                    "(id, display_name, image_path, image_hash, type, tags, "
                    " persona_id, archived, created_at, last_used_at) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
                    " '2026-01-01T00:00:00Z', NULL)",
                    (tid, name, f"img/{tid}.png", f"hash-{tid}"),
                )
    finally:
        conn.close()


def _seed_child(
    db_path: Path,
    *,
    child_id: str,
    banned_themes: str | None = None,
    reading_level: str | None = None,
) -> None:
    """Seed a child + (optionally) the household-global banned-themes setting.

    Phase H Step H4: ``banned_themes`` is no longer per-child. When the
    caller passes ``banned_themes``, we write the value to
    ``settings.banned_themes_global`` (UPSERT — repeated calls overwrite,
    matching the previous "last-seed-wins" semantic the per-child column
    happened to have when only one child was seeded).
    """
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children "
                "(id, display_name, birthdate, pronouns, reading_level, "
                " interests, comfort, notes) "
                "VALUES (?, 'Child', NULL, NULL, ?, NULL, NULL, NULL)",
                (child_id, reading_level),
            )
        if banned_themes is not None:
            from toybox.core.banned_themes import set_banned_themes_global

            set_banned_themes_global(conn, banned_themes)
    finally:
        conn.close()


def _capable_check() -> Callable[[], Awaitable[tuple[bool, CapabilityReason | None]]]:
    async def check() -> tuple[bool, CapabilityReason | None]:
        return True, None

    return check


def _conn_factory(db_path: Path) -> Callable[[], sqlite3.Connection]:
    def _factory() -> sqlite3.Connection:
        return connect(db_path, check_same_thread=False)

    return _factory


def _make_transcript(text: str) -> Transcript:
    return Transcript(text=text, confidence=0.9, language="en", duration_ms=1000)


def _trigger(name: str, slot: str | None = None) -> Intent:
    return Intent(name=name, slot=slot, pattern_id=f"pat_{name}")


def _claude_activity_json() -> str:
    activity = Activity(
        id="00000000-0000-4000-8000-000000000001",
        template_id="claude_dynamic",
        title="Claude activity",
        steps=[ActivityStep(step_index=i, text=f"claude step {i}") for i in range(5)],
    )
    return activity.model_dump_json()


# ---------------------------------------------------------------------------
# H2: trigger-driven offline path resolves real toys + applies banned themes
# ---------------------------------------------------------------------------


async def test_dispatcher_offline_path_substitutes_real_toy_names(
    tmp_path: Path,
) -> None:
    """Mode 1 (OFFLINE) trigger → ``_offline_activity`` MUST substitute a
    real toy name from the seeded catalog, not the legacy
    ``Mr. Unicorn`` placeholder.
    """
    db_path = _migrated_db(tmp_path / "toybox.db")
    _seed_toys(db_path, [("toy-a", "Bluey"), ("toy-b", "Buzz Lightyear")])

    stub = StubClient()
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        connection_factory=_conn_factory(db_path),
    )
    activity = await dispatcher.on_transcript(
        _make_transcript("let's play"),
        ListeningMode.OFFLINE,
        [_trigger("request_play", "blocks")],
    )
    assert activity is not None
    step_text = " ".join(s.text for s in activity.steps)
    title = activity.title
    blob = f"{title} {step_text}"
    # The substitution MUST surface a real toy. Either it appears
    # (templates that use {toy}) or the placeholder is gone.
    assert "Mr. Unicorn" not in blob, f"placeholder leaked into output: {blob!r}"


async def test_dispatcher_offline_path_records_resolved_toys_in_labeled_events(
    tmp_path: Path,
) -> None:
    """The labeled_events row written from the dispatcher's offline path
    MUST carry the resolved catalog content — Phase E SFT exports rely
    on this.
    """
    db_path = _migrated_db(tmp_path / "toybox.db")
    _seed_toys(db_path, [("toy-a", "Apollo"), ("toy-b", "Buzz")])
    _seed_child(db_path, child_id="c1", banned_themes="scary")

    captured_ctx: list[Any] = []

    def _recorder(activity: Activity, ctx: Any, generator_path: str) -> int:
        captured_ctx.append((ctx, generator_path))
        return 0  # row_id 0 disables judge sampling

    dispatcher = EscalationDispatcher(
        ai_client=StubClient(),
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        connection_factory=_conn_factory(db_path),
        labeled_event_recorder=_recorder,
    )
    activity = await dispatcher.on_transcript(
        _make_transcript("let's play"),
        ListeningMode.OFFLINE,
        [_trigger("request_play", "blocks")],
    )
    assert activity is not None
    assert len(captured_ctx) == 1
    ctx, gen_path = captured_ctx[0]
    assert gen_path == "offline"
    assert set(ctx.available_toys) == {"Apollo", "Buzz"}
    assert ctx.child_profile is not None
    assert "scary" in ctx.child_profile["banned_themes"]


# ---------------------------------------------------------------------------
# H1: Claude directive is built + threaded into the system prompt
# ---------------------------------------------------------------------------


async def test_claude_system_prompt_contains_banned_themes_directive(
    tmp_path: Path,
) -> None:
    """A child seeded with ``banned_themes=["scary","loud"]`` and
    ``reading_level="pre-reader"`` MUST cause the Claude system prompt
    to contain ``"Do NOT include"`` plus both themes plus the
    pre-reader reading-level guidance text.
    """
    db_path = _migrated_db(tmp_path / "toybox.db")
    _seed_child(
        db_path,
        child_id="c1",
        banned_themes="scary, loud",
        reading_level="pre-reader",
    )
    stub = StubClient(responses=[_claude_activity_json()])
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        connection_factory=_conn_factory(db_path),
    )
    result = await dispatcher.on_transcript(
        _make_transcript("tell me a story"),
        ListeningMode.DEFAULT,
        [_trigger("request_story", "knights")],
    )
    assert result is not None
    # StubClient records ``("complete_text", messages, kwargs)``.
    assert len(stub.calls) == 1
    method, _messages, kwargs = stub.calls[0]
    assert method == "complete_text"
    system = kwargs["system"]
    assert "Do NOT include" in system, system
    assert "scary" in system, system
    assert "loud" in system, system
    # Pre-reader directive surface.
    assert "very simple words" in system.lower(), system


async def test_claude_system_prompt_no_directive_when_no_constraints(
    tmp_path: Path,
) -> None:
    """No child profiles → no banned-themes line, no reading-level line.

    The directive helper returns the empty string, and the system
    prompt falls back to the schema-only base. This pins the "default
    is invisible" contract — wiring the directive must not affect the
    no-child case.
    """
    db_path = _migrated_db(tmp_path / "toybox.db")
    stub = StubClient(responses=[_claude_activity_json()])
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        connection_factory=_conn_factory(db_path),
    )
    await dispatcher.on_transcript(
        _make_transcript("tell me a story"),
        ListeningMode.DEFAULT,
        [_trigger("request_story", "knights")],
    )
    method, _messages, kwargs = stub.calls[0]
    assert method == "complete_text"
    system = kwargs["system"]
    assert "Do NOT include" not in system


async def test_dispatcher_no_connection_factory_falls_back_to_placeholders(
    tmp_path: Path,
) -> None:
    """Existing tests + the smoke harness construct the dispatcher
    without a ``connection_factory``. That degraded path must keep
    working — empty content + empty directive.
    """
    stub = StubClient(responses=[_claude_activity_json()])
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        # connection_factory NOT provided
    )
    result = await dispatcher.on_transcript(
        _make_transcript("tell me a story"),
        ListeningMode.DEFAULT,
        [_trigger("request_story", "knights")],
    )
    assert result is not None
    method, _messages, kwargs = stub.calls[0]
    assert method == "complete_text"
    system = kwargs["system"]
    assert "Do NOT include" not in system


async def test_dispatcher_resolver_db_failure_degrades_silently(
    tmp_path: Path,
) -> None:
    """A connection_factory that raises (or queries failing) must not
    break dispatch. The dispatcher logs WARNING and serves a
    placeholder activity.
    """

    def _broken_factory() -> sqlite3.Connection:
        raise sqlite3.OperationalError("simulated DB failure")

    stub = StubClient(responses=[_claude_activity_json()])
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        connection_factory=_broken_factory,
    )
    result = await dispatcher.on_transcript(
        _make_transcript("let's play"),
        ListeningMode.OFFLINE,
        [_trigger("request_play", "blocks")],
    )
    # Offline path still returns something playable.
    assert result is not None
    assert len(result.steps) == 5


# ---------------------------------------------------------------------------
# Sanity: a second above-floor transcript builds a fresh AIResponse
# ---------------------------------------------------------------------------


async def test_claude_directive_re_resolves_per_dispatch(
    tmp_path: Path,
) -> None:
    """A second dispatch picks up child-profile edits without restarting
    the dispatcher (the resolver runs per-event, not at construction).
    """
    db_path = _migrated_db(tmp_path / "toybox.db")
    _seed_child(db_path, child_id="c1", banned_themes="scary")

    stub = StubClient(
        responses=[_claude_activity_json(), _claude_activity_json()],
    )
    dispatcher = EscalationDispatcher(
        ai_client=stub,
        breaker=CircuitBreaker(),
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        connection_factory=_conn_factory(db_path),
    )
    await dispatcher.on_transcript(
        _make_transcript("tell me a story"),
        ListeningMode.DEFAULT,
        [_trigger("request_story")],
    )
    first_system = stub.calls[0][2]["system"]
    assert "scary" in first_system

    # Edit the household-global banned_themes mid-test.
    # Phase H Step H4: banned_themes is no longer per-child; the
    # canonical home is ``settings.banned_themes_global``, accessed via
    # :func:`toybox.core.banned_themes.set_banned_themes_global`.
    conn = connect(db_path)
    try:
        from toybox.core.banned_themes import set_banned_themes_global

        set_banned_themes_global(conn, "loud")
    finally:
        conn.close()

    await dispatcher.on_transcript(
        _make_transcript("tell me a story"),
        ListeningMode.DEFAULT,
        [_trigger("request_story")],
    )
    second_system = stub.calls[1][2]["system"]
    assert "loud" in second_system
    # And the previous theme is gone.
    assert "scary" not in second_system


# ---------------------------------------------------------------------------
# Defensive: an AIResponse-yielding stub doesn't actually hit Claude
# when the breaker is forced (offline path still reads content).
# ---------------------------------------------------------------------------


async def test_breaker_open_offline_path_still_resolves_content(
    tmp_path: Path,
) -> None:
    """When the breaker is open, mode 3 falls through to offline. The
    dispatcher must STILL resolve content for that offline call so the
    banned-themes filter / real toy substitution apply on the
    non-Claude path.
    """
    db_path = _migrated_db(tmp_path / "toybox.db")
    _seed_toys(db_path, [("toy-a", "Apollo")])
    _seed_child(db_path, child_id="c1", banned_themes="scary")

    captured_ctx: list[Any] = []

    def _recorder(activity: Activity, ctx: Any, generator_path: str) -> int:
        captured_ctx.append((ctx, generator_path))
        return 0

    breaker = CircuitBreaker(threshold=1, cooldown_sec=60.0)
    breaker.record_failure()  # opens immediately at threshold=1
    assert breaker.is_open()

    dispatcher = EscalationDispatcher(
        ai_client=StubClient(),  # never used; breaker is open
        breaker=breaker,
        throttle=MinIntervalThrottle(0.0),
        capability_check=_capable_check(),
        connection_factory=_conn_factory(db_path),
        labeled_event_recorder=_recorder,
    )
    result = await dispatcher.on_transcript(
        _make_transcript("let's play"),
        ListeningMode.DEFAULT,
        [_trigger("request_play", "blocks")],
    )
    assert result is not None
    assert len(captured_ctx) == 1
    ctx, gen_path = captured_ctx[0]
    assert gen_path == "offline"
    assert "Apollo" in ctx.available_toys
    assert ctx.child_profile is not None
    assert "scary" in ctx.child_profile["banned_themes"]


def _scripted_response(text: str) -> Callable[[], AIResponse]:
    """Return a no-arg callable that materialises an AIResponse on call."""

    def _make() -> AIResponse:
        from toybox.ai.client import text_model  # noqa: PLC0415

        return AIResponse(text=text, model=text_model())

    return _make
