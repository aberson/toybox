"""Transcript pipeline: VAD-gated speech -> STT -> persist -> emit -> trigger.

This module orchestrates the Step 13 surface. The :class:`TranscriptPipeline`
consumer drives a single async loop:

1. Pull a VAD-gated speech chunk from :class:`MicCapture`.
2. Hand it to :class:`WhisperTranscriber.transcribe` (already async +
   thread-offloaded). Errors here are logged + skipped so a one-off
   inference crash doesn't kill the daemon.
3. Persist the resulting :class:`Transcript` row (always — the audit
   trail keeps every utterance, including low-confidence ones). The
   sqlite3 write goes through :func:`asyncio.to_thread` so the consumer
   doesn't block other tasks.
4. Build a ``Topic.transcript`` ws envelope and call the publisher (if
   provided). Emit happens for every persisted row, low- or
   high-confidence.
5. **Confidence-floor gate:** when ``confidence >= floor`` (env override
   ``TOYBOX_TRANSCRIPT_CONFIDENCE_FLOOR``, default 0.55) feed the
   transcript text through the trigger registry's :func:`match` and
   dispatch each :class:`Intent` to the optional ``on_intent`` handler.

Lifecycle: :meth:`start` spawns the consumer task; :meth:`stop` cancels
and awaits its termination. Both are idempotent.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sqlite3
import uuid
from collections.abc import AsyncIterable, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

from numpy import int16
from numpy.typing import NDArray

from ..core.listening import Publisher
from ..core.mic_state import current_mic_enabled
from ..db import resolve_db_path
from ..db.connection import connect
from ..triggers.registry import Intent
from ..triggers.registry import match as trigger_match
from ..ws.envelope import build_envelope
from ..ws.topics import Topic
from .stt import UNKNOWN_LANGUAGE, Transcript

_logger = logging.getLogger(__name__)

CONFIDENCE_FLOOR_ENV: Final[str] = "TOYBOX_TRANSCRIPT_CONFIDENCE_FLOOR"
DEFAULT_CONFIDENCE_FLOOR: Final[float] = 0.55

# Shape of the speech source the pipeline consumes. ``MicCapture`` and
# the test stubs both satisfy ``AsyncIterable[NDArray[int16]]`` -- the
# pipeline only ever uses ``async for`` over it.
CaptureSource = AsyncIterable[NDArray[int16]]


class _TranscriberLike(Protocol):
    """Minimal transcriber surface (matches :class:`WhisperTranscriber`)."""

    async def transcribe(self, audio: NDArray[int16]) -> Transcript: ...


# Shape of the trigger-evaluation hook. Signature matches
# :func:`toybox.triggers.registry.match` so production code can pass it
# directly; tests substitute a list-collecting stub. The ``db_path``
# argument is always supplied by the pipeline so the dynamic toy-name
# source resolves against the right SQLite file.
TriggerMatcher = Callable[[str, Path | None], list[Intent]]


# Shape of the mic-enabled gate. Production passes a closure that reads
# ``settings.mic_enabled`` from SQLite per-call; tests pass a constant
# lambda (or a list-toggle for state transitions). Per-call read is
# cheap (settings table is tiny + sqlite caches the page) and avoids a
# cache-invalidation hop when the parent toggles mute mid-utterance.
MicEnabledCheck = Callable[[], bool]


def _confidence_floor_from_env() -> float:
    """Resolve the confidence floor from ``TOYBOX_TRANSCRIPT_CONFIDENCE_FLOOR``.

    Mirrors the env-var pattern used by the rest of the audio module:
    parse failure / out-of-range / non-finite all warn and fall back to
    :data:`DEFAULT_CONFIDENCE_FLOOR`. The valid range is ``[0.0, 1.0]``
    to match :class:`Transcript.confidence`.
    """
    raw = os.environ.get(CONFIDENCE_FLOOR_ENV)
    if raw is None:
        return DEFAULT_CONFIDENCE_FLOOR
    try:
        value = float(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not a float; falling back to %.2f",
            CONFIDENCE_FLOOR_ENV,
            raw,
            DEFAULT_CONFIDENCE_FLOOR,
        )
        return DEFAULT_CONFIDENCE_FLOOR
    if not math.isfinite(value):
        _logger.warning(
            "%s=%r is not finite; falling back to %.2f",
            CONFIDENCE_FLOOR_ENV,
            raw,
            DEFAULT_CONFIDENCE_FLOOR,
        )
        return DEFAULT_CONFIDENCE_FLOOR
    if not 0.0 <= value <= 1.0:
        _logger.warning(
            "%s=%.3f outside 0..1; falling back to %.2f",
            CONFIDENCE_FLOOR_ENV,
            value,
            DEFAULT_CONFIDENCE_FLOOR,
        )
        return DEFAULT_CONFIDENCE_FLOOR
    return value


def _isoformat(ts: datetime) -> str:
    """Render a datetime as the project's canonical UTC ISO-8601 string."""
    return ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_trigger_matcher(text: str, db_path: Path | None) -> list[Intent]:
    """Adapter so production callers can pass the registry's ``match`` directly."""
    return trigger_match(text, db_path=db_path)


def _default_mic_enabled_check(db_path: Path) -> MicEnabledCheck:
    """Build a per-call settings reader. Failures fail-open (mic on)."""

    def _check() -> bool:
        try:
            conn = connect(db_path, check_same_thread=False)
        except sqlite3.Error as exc:
            _logger.warning("mic_enabled probe: cannot open DB: %s", exc)
            return True
        try:
            return current_mic_enabled(conn)
        except sqlite3.Error as exc:
            _logger.warning("mic_enabled probe: query failed: %s", exc)
            return True
        finally:
            conn.close()

    return _check


class TranscriptPipeline:
    """Wire VAD-gated speech to STT, persistence, ws emit, and triggers.

    The pipeline is **not** wired into application startup at this step —
    Phase B Step 14 (smoke test) wires the live pipeline. This class
    exists so the API + module are importable and unit-testable now.

    Args:
        capture: Async iterable yielding int16 speech chunks. Production
            passes :class:`toybox.audio.capture.MicCapture`; tests pass
            a scripted stub.
        transcriber: Object exposing ``async transcribe(audio)``.
        session_id: The active ``sessions`` row id. Foreign key target
            for transcript inserts.
        publisher: Optional ws publisher (matches the
            :class:`toybox.core.listening.Publisher` shape). ``None``
            disables emission.
        on_intent: Optional async handler invoked once per :class:`Intent`
            yielded by the trigger registry.
        db_path: SQLite path for transcript inserts + dynamic-toy lookup.
            Defaults to :func:`toybox.db.resolve_db_path`.
        mic_id: Best-effort mic identifier persisted on every row.
        confidence_floor: Override the env-driven floor; ``None`` honors
            ``TOYBOX_TRANSCRIPT_CONFIDENCE_FLOOR`` (default 0.55).
        trigger_matcher: Test hook for the trigger lookup; defaults to
            :func:`toybox.triggers.registry.match`.
    """

    def __init__(
        self,
        *,
        capture: CaptureSource,
        transcriber: _TranscriberLike,
        session_id: str,
        publisher: Publisher | None = None,
        on_intent: Callable[[Intent], Awaitable[None]] | None = None,
        db_path: Path | None = None,
        mic_id: str | None = None,
        confidence_floor: float | None = None,
        trigger_matcher: TriggerMatcher | None = None,
        mic_enabled_check: MicEnabledCheck | None = None,
    ) -> None:
        if confidence_floor is None:
            confidence_floor = _confidence_floor_from_env()
        if not 0.0 <= confidence_floor <= 1.0:
            raise ValueError(
                f"confidence_floor must be in 0..1, got {confidence_floor}"
            )
        self._capture = capture
        self._transcriber = transcriber
        self._session_id = session_id
        self._publisher = publisher
        self._on_intent = on_intent
        self._db_path = db_path if db_path is not None else resolve_db_path()
        self._mic_id = mic_id
        self._confidence_floor = confidence_floor
        self._trigger_matcher = (
            trigger_matcher if trigger_matcher is not None else _default_trigger_matcher
        )
        self._mic_enabled_check = (
            mic_enabled_check
            if mic_enabled_check is not None
            else _default_mic_enabled_check(self._db_path)
        )
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Properties (mostly for tests + diagnostics)
    # ------------------------------------------------------------------

    @property
    def confidence_floor(self) -> float:
        return self._confidence_floor

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def session_id(self) -> str:
        return self._session_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the consumer task. Idempotent."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._run(), name="toybox-transcript-pipeline"
        )
        _logger.info(
            "transcript pipeline started (session_id=%s, mic_id=%s, floor=%.2f)",
            self._session_id,
            self._mic_id,
            self._confidence_floor,
        )

    async def stop(self) -> None:
        """Cancel the consumer task and await its termination. Idempotent."""
        if not self._running and self._task is None:
            return
        self._running = False
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover -- defensive
            _logger.exception("transcript pipeline task raised during stop")
        _logger.info("transcript pipeline stopped (session_id=%s)", self._session_id)

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        try:
            async for chunk in self._capture:
                await self._handle_chunk(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover -- defensive
            _logger.exception(
                "transcript pipeline consumer crashed; pipeline is shutting down"
            )
            self._running = False
            raise

    async def _handle_chunk(self, chunk: NDArray[int16]) -> None:
        # Mute gate runs BEFORE transcription so faster-whisper never
        # sees muted audio. The capture loop keeps draining the speech
        # queue (so PortAudio doesn't back up), and we still skip
        # persistence, ws emit, and trigger dispatch. Read fresh
        # per-utterance so a mid-conversation toggle takes effect on
        # the next chunk. As a side effect, a session that starts muted
        # never lazy-loads the Whisper model -- first-unmute pays that
        # one-time cost.
        try:
            mic_on = self._mic_enabled_check()
        except Exception as exc:  # noqa: BLE001 -- defensive: gate fails open
            _logger.warning(
                "mic_enabled_check raised; treating mic as on (exc=%s: %s)",
                type(exc).__name__,
                exc,
            )
            mic_on = True
        if not mic_on:
            return

        try:
            transcript = await self._transcriber.transcribe(chunk)
        except Exception as exc:  # noqa: BLE001 -- log + skip per spec
            # Don't bring the pipeline down for a single inference
            # failure -- the next chunk may transcribe fine. Surface the
            # exception class + message so an operator sees what crashed.
            _logger.warning(
                "transcribe failed; skipping chunk (exc=%s: %s)",
                type(exc).__name__,
                exc,
            )
            return

        # Skip empty / whitespace-only transcripts entirely -- they're
        # noise (capture delivered a chunk but the model had nothing
        # meaningful to say). Persisting them adds zero audit value and
        # would just bloat the table. We intentionally use ``.strip()``
        # so a row like ``" "`` or ``"\n"`` doesn't sneak through the
        # plain ``not text`` falsiness check.
        if not transcript.text.strip():
            return

        ended_at = datetime.now(UTC)
        started_at = ended_at - timedelta(milliseconds=transcript.duration_ms)
        row_id = str(uuid.uuid4())

        try:
            await asyncio.to_thread(
                self._insert_transcript,
                row_id=row_id,
                transcript=transcript,
                started_at=_isoformat(started_at),
                ended_at=_isoformat(ended_at),
            )
        except Exception as exc:  # noqa: BLE001 -- log + skip per spec
            # DB insert failed -- skip the ws emit too so the wire and
            # the table can't disagree about whether a row exists.
            _logger.warning(
                "transcript insert failed; skipping emit (exc=%s: %s)",
                type(exc).__name__,
                exc,
            )
            return

        if self._publisher is not None:
            envelope = build_envelope(
                topic=Topic.transcript,
                payload={
                    "id": row_id,
                    "text": transcript.text,
                    "confidence": transcript.confidence,
                    "started_at": _isoformat(started_at),
                    "ended_at": _isoformat(ended_at),
                    "language": transcript.language,
                },
            )
            try:
                self._publisher(envelope)
            except Exception as exc:  # noqa: BLE001 -- defensive
                _logger.warning(
                    "transcript publisher raised; continuing (exc=%s: %s)",
                    type(exc).__name__,
                    exc,
                )

        if transcript.confidence < self._confidence_floor:
            _logger.debug(
                "transcript confidence %.3f < floor %.3f; skipping triggers",
                transcript.confidence,
                self._confidence_floor,
            )
            return

        try:
            intents = self._trigger_matcher(transcript.text, self._db_path)
        except Exception as exc:  # noqa: BLE001 -- defensive
            _logger.warning(
                "trigger matcher raised; skipping (exc=%s: %s)",
                type(exc).__name__,
                exc,
            )
            return

        if not intents or self._on_intent is None:
            return

        for intent in intents:
            try:
                await self._on_intent(intent)
            except Exception as exc:  # noqa: BLE001 -- defensive
                _logger.warning(
                    "on_intent handler raised for %s; continuing (exc=%s: %s)",
                    intent.pattern_id,
                    type(exc).__name__,
                    exc,
                )

    def _insert_transcript(
        self,
        *,
        row_id: str,
        transcript: Transcript,
        started_at: str,
        ended_at: str,
    ) -> None:
        """Synchronous SQLite write. Runs inside :func:`asyncio.to_thread`."""
        conn: sqlite3.Connection = connect(self._db_path, check_same_thread=False)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO transcripts "
                    "(id, session_id, mic_id, started_at, ended_at, text, "
                    " confidence, language, triggered_intent) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        row_id,
                        self._session_id,
                        self._mic_id,
                        started_at,
                        ended_at,
                        transcript.text,
                        transcript.confidence,
                        transcript.language,
                    ),
                )
        finally:
            conn.close()


__all__ = [
    "CONFIDENCE_FLOOR_ENV",
    "CaptureSource",
    "DEFAULT_CONFIDENCE_FLOOR",
    "TranscriptPipeline",
    "TriggerMatcher",
    "UNKNOWN_LANGUAGE",
]
