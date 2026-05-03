"""Uvicorn entrypoint.

Run as ``python -m toybox.main`` (or ``uv run python -m toybox.main``).

CLI flags:

* ``--host``     bind host (default ``127.0.0.1``; honors ``TOYBOX_HOST``)
* ``--port``     bind port (default ``8000``; honors ``TOYBOX_PORT``)
* ``--check``    run startup validation and exit 0 without starting uvicorn
* ``--smoke``    boot the backend with a synthetic-audio adapter that
                 plays ``--smoke-wav`` (or
                 ``tests/fixtures/audio/lets_play_unicorns.wav``) through
                 the real VAD + STT + trigger pipeline. Used by the
                 ``tests/e2e/test_smoke_pipeline.py`` E2E harness; not a
                 production codepath.

The LAN-bind guard runs unconditionally before uvicorn starts so a
misconfigured ``TOYBOX_HOST=0.0.0.0`` exits non-zero with the documented
``code=lan_bind_requires_pin`` error code rather than silently exposing
the API.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .activities.models import Activity
from .ai.breaker import CircuitBreaker
from .ai.client import AIClient, StubClient
from .app import create_app
from .audio.capture import MicCapture
from .audio.pipeline import TranscriptPipeline
from .audio.stt import WhisperTranscriber
from .audio.test_adapter import WavToBufferStream
from .core.bind_guard import BindGuardError, check_bind_safe
from .core.capability import CapabilityReason
from .core.escalation import EscalationDispatcher
from .core.listening import ListeningMode
from .core.queue import PROPOSED_QUEUE_CAP, PROPOSED_STATE, evict_oldest_for_capacity
from .core.throttle import MinIntervalThrottle
from .db import connect, resolve_db_path
from .db.migrations import run_migrations
from .triggers.registry import Intent
from .ws.envelope import build_envelope
from .ws.server import get_pubsub
from .ws.topics import Topic

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

DEFAULT_SMOKE_WAV: Path = Path("tests") / "fixtures" / "audio" / "lets_play_unicorns.wav"
SMOKE_SESSION_ID = "smoke-session"
SMOKE_MIC_ID = "smoke-mic"

# --smoke is a controlled CPU-only test path -- the host running it
# (CI, an iter validation, the build-step orchestrator) is not assumed
# to ship a working CUDA toolchain. ``WhisperTranscriber`` defaults to
# probing CUDA first; on Windows a probe can succeed at model-load time
# (CTranslate2 thinks CUDA is available) but the inference call later
# blows up with ``Library cublas64_12.dll is not found or cannot be
# loaded``, which silently kills every chunk in the pipeline. Pinning
# CPU here removes that variability for --smoke without touching the
# default behaviour of WhisperTranscriber elsewhere. Operators with a
# real GPU smoke target can override via ``TOYBOX_WHISPER_DEVICE``.
SMOKE_WHISPER_DEVICE_ENV: str = "TOYBOX_WHISPER_DEVICE"
SMOKE_WHISPER_DEVICE_DEFAULT: str = "cpu"
# Allow-list mirrors the values WhisperTranscriber accepts (``cpu``,
# ``cuda``, ``auto``). An unknown value is silently coerced to the
# CPU default with a WARNING — same defensive pattern as
# ``audio.capture._ring_seconds_from_env``.
_SMOKE_WHISPER_DEVICE_ALLOWED: frozenset[str] = frozenset({"cpu", "cuda", "auto"})

_logger = logging.getLogger(__name__)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="toybox.main", description="Toybox backend entrypoint.")
    parser.add_argument(
        "--host",
        default=os.environ.get("TOYBOX_HOST", DEFAULT_HOST),
        help="Bind host (default: 127.0.0.1; env TOYBOX_HOST).",
    )
    # TODO(phase-a-step-4): catch ValueError from int() and emit code=invalid_port_env
    # via the settings store. Phase A Step 1 scope = scaffold; settings store ships in Step 4.
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TOYBOX_PORT", str(DEFAULT_PORT))),
        help="Bind port (default: 8000; env TOYBOX_PORT).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run startup validation and exit 0 without starting uvicorn.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "Boot the backend in synthetic-audio smoke mode for the E2E harness "
            "(tests/e2e/test_smoke_pipeline.py). Replaces the live mic with a "
            "WAV-to-buffer adapter feeding the real VAD + STT + trigger pipeline."
        ),
    )
    parser.add_argument(
        "--smoke-wav",
        type=Path,
        default=DEFAULT_SMOKE_WAV,
        help=(f"Path to the fixture WAV played in --smoke mode (default: {DEFAULT_SMOKE_WAV})."),
    )
    return parser.parse_args(argv)


def _pin_is_set() -> bool:
    """Phase A: there is no PIN yet. Step 4+ wires this to the settings store."""
    return False


# ----------------------------------------------------------------------
# --smoke lifespan wiring
# ----------------------------------------------------------------------


def _ensure_smoke_session(db_path: Path, session_id: str) -> None:
    """Insert the smoke ``sessions`` row if it isn't already present.

    Transcript inserts FK to ``sessions.id``; without a row the pipeline
    crashes on the first chunk. The migration runner is invoked
    unconditionally so a fresh worktree boots cleanly.
    """
    conn = connect(db_path, check_same_thread=False)
    try:
        run_migrations(conn)
        existing = conn.execute(
            "SELECT id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if existing is None:
            with conn:
                conn.execute(
                    "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                    (
                        session_id,
                        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    ),
                )
    finally:
        conn.close()


# Activity states the parent UI considers "active" -- any of these in the
# DB at smoke startup means the parent will hydrate them on bootstrap and
# fetch ``GET /api/activities/{id}`` before the WAV pipeline has produced
# its first proposal. ``completed`` is intentionally excluded: it's a
# terminal state that represents real history (the kid finished all the
# steps), and rewriting completed rows to ``dismissed`` would corrupt
# the audit trail. The parent UI is happy to hydrate completed rows
# without blocking the new proposal — the queue capacity check in
# ``evict_oldest_for_capacity`` only counts ``proposed``-state rows.
# Defined locally (rather than imported from ``api.activities``) so the
# smoke wiring stays self-contained.
_SMOKE_ACTIVE_STATES: tuple[str, ...] = ("proposed", "approved", "running")


def _purge_active_smoke_activities(db_path: Path, *, session_id: str) -> int:
    """Delete every active smoke-session activity row + its steps.

    The smoke must exercise the full WAV->VAD->STT->trigger->propose
    flow end-to-end. If a prior smoke run (the project's own
    ``data/toybox.db`` persists across runs) left a ``proposed`` /
    ``approved`` / ``running`` activity in the DB, the parent UI
    hydrates that row on bootstrap and the suggestion-card the test
    waits on may show the *stale* activity rather than the one the WAV
    is supposed to drive -- or worse, the pre-existing activity blocks
    queue capacity.

    We DELETE rather than mark ``dismissed`` for two reasons:

    1. The offline generator the smoke uses produces a *deterministic*
       UUID from ``(intent, slot, context, seed, template_id)``. So a
       prior run's row and the new run's row share the same activity.id.
       Marking the prior row dismissed leaves it in the table; the new
       INSERT then collides with the SAME id and the
       ``UNIQUE constraint failed: activities.id`` error silently kills
       the suggestion path (caught by the smoke's defensive
       ``except Exception``). Deleting is the only way to break the
       collision.
    2. Smoke-session rows aren't real audit history -- they're test
       detritus from a hermetic CI/dev test mode. Real audit history
       (``completed`` rows + any row in any other ``session_id``) is
       untouched: ``completed`` is excluded from ``_SMOKE_ACTIVE_STATES``
       and the ``WHERE session_id = ?`` predicate keeps the purge from
       reaching a developer's in-flight real activities even if they
       point ``--smoke`` at their primary ``data/toybox.db``.

    Returns the number of rows deleted, for the startup log.
    """
    placeholders = ",".join("?" for _ in _SMOKE_ACTIVE_STATES)
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            # Delete the steps first to honour the FK cascade order.
            # SQLite's default config does NOT enforce FKs unless
            # ``PRAGMA foreign_keys = ON``, so we delete steps
            # explicitly to keep an orphan-free state regardless of the
            # connection's pragma setting.
            conn.execute(
                f"DELETE FROM activity_steps WHERE activity_id IN ("  # noqa: S608 -- static
                f"  SELECT id FROM activities "
                f"  WHERE session_id = ? AND state IN ({placeholders})"
                f")",
                (session_id, *_SMOKE_ACTIVE_STATES),
            )
            cur = conn.execute(
                f"DELETE FROM activities "  # noqa: S608 -- placeholders are static
                f"WHERE session_id = ? AND state IN ({placeholders})",
                (session_id, *_SMOKE_ACTIVE_STATES),
            )
            return int(cur.rowcount or 0)
    finally:
        conn.close()


def _persist_smoke_activity(
    db_path: Path,
    *,
    activity: Activity,
    intent_source: str,
    session_id: str,
) -> None:
    """Insert an activity + steps row analogous to ``api.activities._do_propose``.

    Mirrors the wire shape :func:`toybox.api.activities._row_to_response`
    expects so the parent UI fetching ``GET /api/activities/{id}`` after
    seeing the ws envelope sees the same payload.
    """
    summary_payload = {
        "title": activity.title,
        "metadata": dict(activity.metadata),
        "template_id": activity.template_id,
    }
    summary_blob = json.dumps(summary_payload, sort_keys=True)
    created_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    conn = connect(db_path, check_same_thread=False)
    try:
        # Make room for the new proposal so the queue cap holds.
        evict_oldest_for_capacity(conn, cap=PROPOSED_QUEUE_CAP)
        with conn:
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, summary, persona_id, child_ids, "
                " room_ids, toy_ids, intent_source, created_at, started_at, ended_at) "
                "VALUES (?, ?, ?, 1, ?, ?, NULL, NULL, NULL, ?, ?, NULL, NULL)",
                (
                    activity.id,
                    session_id,
                    PROPOSED_STATE,
                    summary_blob,
                    activity.persona_id,
                    intent_source,
                    created_at,
                ),
            )
            for idx, step in enumerate(activity.steps):
                conn.execute(
                    "INSERT INTO activity_steps "
                    "(id, activity_id, seq, body, sfx, expected_action, current) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (
                        str(uuid.uuid4()),
                        activity.id,
                        idx + 1,
                        step.text,
                        step.sfx,
                        step.expected_action,
                    ),
                )
    finally:
        conn.close()


def _build_smoke_lifespan(
    *,
    wav_path: Path,
) -> Any:
    """Return an async-context-manager lifespan handler for ``--smoke``.

    The handler:

    1. Migrates the DB and seeds the smoke ``sessions`` row.
    2. Constructs a :class:`MicCapture` whose ``stream_factory`` is a
       :class:`WavToBufferStream` bound to ``wav_path``.
    3. Constructs a real :class:`WhisperTranscriber` (it loads the
       pre-fetched model from ``data/models/`` via the existing cache
       path).
    4. Constructs an :class:`EscalationDispatcher` wired with a
       :class:`StubClient` + a non-capable check so the OFFLINE generator
       always wins (no Anthropic call is ever made — smoke is hermetic).
    5. Wires the dispatcher's output as the pipeline's ``on_intent``:
       each above-floor intent fires the offline generator, persists the
       resulting activity as a ``proposed`` row, and publishes an
       ``activity.state`` envelope on the process pubsub so the parent
       UI sees the suggestion.
    6. Starts capture + pipeline on app startup; stops them on shutdown.
    """

    @contextlib.asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not wav_path.is_file():
            raise FileNotFoundError(
                f"--smoke WAV not found at {wav_path!s}. Run "
                "`python scripts/gen_smoke_wav.py` to (re)generate the fixture."
            )

        db_path = resolve_db_path()
        _ensure_smoke_session(db_path, SMOKE_SESSION_ID)
        purged = _purge_active_smoke_activities(db_path, session_id=SMOKE_SESSION_ID)
        if purged:
            _logger.info(
                "smoke startup deleted stale active activity rows "
                "(count=%d, session_id=%s, db=%s)",
                purged,
                SMOKE_SESSION_ID,
                db_path,
            )

        pubsub = get_pubsub()

        def _publish(envelope: Any) -> None:
            pubsub.publish(envelope)

        # --- escalation dispatcher (offline-only; no live Claude) -----
        ai_client: AIClient = StubClient()
        breaker = CircuitBreaker()
        throttle = MinIntervalThrottle(0.0)

        async def _never_capable() -> tuple[bool, CapabilityReason | None]:
            # --smoke must never escalate to Anthropic. Reporting
            # ``token_missing`` is the standard incapable-but-unhealthy
            # shape used by the offline-fallback path.
            return False, CapabilityReason.token_missing

        dispatcher = EscalationDispatcher(
            ai_client=ai_client,
            breaker=breaker,
            throttle=throttle,
            capability_check=_never_capable,
            publisher=_publish,
        )

        async def _on_intent(intent: Intent) -> None:
            """Run the dispatcher in mode 3 and publish ``activity.state``.

            Mode 3 (DEFAULT) routes through the
            ``_maybe_claude_with_offline_fallback`` path; the
            ``_never_capable`` check shorts to offline so no SDK call is
            attempted. The resulting Activity is persisted as a
            ``proposed`` row + an ``activity.state`` envelope is
            published on the parent ws topic.
            """
            try:
                activity = await dispatcher.on_transcript(
                    transcript=_synthetic_transcript_for(intent),
                    mode=ListeningMode.DEFAULT,
                    intents=[intent],
                )
            except Exception as exc:  # noqa: BLE001 -- defensive
                _logger.warning(
                    "smoke on_intent dispatch failed (%s: %s)",
                    type(exc).__name__,
                    exc,
                )
                return
            if activity is None:
                return
            try:
                await asyncio.to_thread(
                    _persist_smoke_activity,
                    db_path,
                    activity=activity,
                    intent_source=intent.name,
                    session_id=SMOKE_SESSION_ID,
                )
            except Exception as exc:  # noqa: BLE001 -- defensive
                _logger.warning(
                    "smoke activity persist failed (%s: %s)",
                    type(exc).__name__,
                    exc,
                )
                return
            envelope = build_envelope(
                topic=Topic.activity_state,
                payload={
                    "id": activity.id,
                    "state": PROPOSED_STATE,
                    "version": 1,
                    "title": activity.title,
                    "persona_id": activity.persona_id,
                    "intent_source": intent.name,
                    "child_ids": [],
                    "created_at": (
                        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                    ),
                    "steps": [
                        {
                            "seq": idx + 1,
                            "body": step.text,
                            "sfx": step.sfx,
                            "expected_action": step.expected_action,
                            "current": False,
                        }
                        for idx, step in enumerate(activity.steps)
                    ],
                    "metadata": dict(activity.metadata),
                },
            )
            try:
                pubsub.publish(envelope)
            except Exception as exc:  # noqa: BLE001 -- defensive
                _logger.warning(
                    "smoke envelope publish failed (%s: %s)",
                    type(exc).__name__,
                    exc,
                )

        # --- mic + transcriber + pipeline ----------------------------
        mic = MicCapture(
            stream_factory=WavToBufferStream.factory_for(wav_path),
        )
        # Pin CPU explicitly for --smoke (see SMOKE_WHISPER_DEVICE_ENV
        # docstring above): the default CUDA-probe path can succeed at
        # load-time on Windows hosts that lack the cublas runtime, then
        # crash inside ``transcribe()`` and silently drop every chunk.
        whisper_device = os.environ.get(
            SMOKE_WHISPER_DEVICE_ENV,
            SMOKE_WHISPER_DEVICE_DEFAULT,
        )
        if whisper_device not in _SMOKE_WHISPER_DEVICE_ALLOWED:
            _logger.warning(
                "ignoring unknown %s value (got=%r, allowed=%s) — falling back to %s",
                SMOKE_WHISPER_DEVICE_ENV,
                whisper_device,
                sorted(_SMOKE_WHISPER_DEVICE_ALLOWED),
                SMOKE_WHISPER_DEVICE_DEFAULT,
            )
            whisper_device = SMOKE_WHISPER_DEVICE_DEFAULT
        transcriber = WhisperTranscriber(device=whisper_device)
        pipeline = TranscriptPipeline(
            capture=mic,
            transcriber=transcriber,
            session_id=SMOKE_SESSION_ID,
            publisher=_publish,
            on_intent=_on_intent,
            db_path=db_path,
            mic_id=SMOKE_MIC_ID,
        )

        # Partial-init cleanup: if pipeline.start() fails, mic.start()
        # has already begun running the WAV pump thread; without the
        # try/except the lifespan would propagate the failure and leak
        # both mic + transcriber resources (the ``finally`` block below
        # never runs because the lifespan never reached its ``yield``).
        try:
            await mic.start()
        except BaseException:
            with contextlib.suppress(Exception):
                await transcriber.close()
            raise
        try:
            await pipeline.start()
        except BaseException:
            with contextlib.suppress(Exception):
                await mic.stop()
            with contextlib.suppress(Exception):
                await transcriber.close()
            raise

        _logger.info(
            "smoke pipeline started (wav=%s, session_id=%s)",
            wav_path,
            SMOKE_SESSION_ID,
        )
        try:
            yield
        finally:
            # Sequential cleanup: each stop() in its own suppressed
            # block so a failure in one component doesn't strand the
            # others. Order is reverse-of-start: pipeline first (so it
            # stops pulling from the mic queue), mic next, transcriber
            # last (the pipeline holds a reference to it).
            with contextlib.suppress(Exception):
                await pipeline.stop()
            with contextlib.suppress(Exception):
                await mic.stop()
            with contextlib.suppress(Exception):
                await transcriber.close()
            _logger.info("smoke pipeline stopped")

    return _lifespan


def _synthetic_transcript_for(intent: Intent) -> Any:
    """Build a minimal :class:`Transcript` for the dispatcher call.

    The dispatcher only reads ``transcript.text`` for the user prompt
    when escalating to Claude; under ``--smoke`` the offline path wins
    so the value is informational only. Constructed lazily here to keep
    the import surface tight.
    """
    from .audio.stt import Transcript  # noqa: PLC0415 -- lazy

    return Transcript(
        text=intent.slot or intent.name,
        confidence=1.0,
        language="en",
        duration_ms=0,
    )


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run startup validation and (unless ``--check``) start uvicorn.

    Returns the integer exit code. ``--check`` returns 0 on success;
    a guard failure prints the error to stderr and returns ``1``.
    """
    # Configure the toybox application logger at INFO. Uvicorn's own
    # ``log_level="info"`` only configures the ``uvicorn``/``uvicorn.*``
    # loggers; without an explicit basicConfig here every
    # ``_logger.info(...)`` from MicCapture, TranscriptPipeline, and the
    # smoke purge would be silently dropped. Match the format used in
    # ``audio.capture.main`` so multi-process logs stay readable.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    args = _parse_args(argv)

    try:
        check_bind_safe(args.host, pin_set=_pin_is_set())
    except BindGuardError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.smoke:
        lifespan = _build_smoke_lifespan(wav_path=args.smoke_wav)
        app = create_app()
        # FastAPI's lifespan is set on the constructor; we re-attach
        # via the router-level event so the pre-built app still picks
        # the smoke handler up.
        app.router.lifespan_context = lifespan
    else:
        app = create_app()

    if args.check:
        print(f"toybox: --check ok (host={args.host} port={args.port} smoke={args.smoke})")
        return 0

    # Imported lazily so unit tests don't need uvicorn loaded.
    import uvicorn  # noqa: PLC0415

    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
