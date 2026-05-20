"""Integration coverage for the Step 13 :class:`TranscriptPipeline`.

Every test stubs the capture + transcriber so we never touch real audio,
real STT, or PortAudio. The DB is the real per-test SQLite file (so the
INSERT path is exercised end-to-end) and the trigger registry is a
list-collecting stub so the suite stays deterministic.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from toybox.audio.pipeline import (
    CONFIDENCE_FLOOR_ENV,
    DEFAULT_CONFIDENCE_FLOOR,
    TranscriptPipeline,
)
from toybox.audio.stt import UNKNOWN_LANGUAGE, Transcript
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.triggers.registry import Intent
from toybox.ws.envelope import Envelope
from toybox.ws.topics import Topic

# ---------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Iterator[Path]:
    """Per-test migrated SQLite file. Closes bootstrap connection before yield."""
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    yield path


@pytest.fixture
def session_id(db_path: Path) -> str:
    """Insert a session row and return its id (FK target for transcripts)."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("session-1", "2026-01-01T00:00:00Z"),
            )
    finally:
        conn.close()
    return "session-1"


def _silent_chunk(samples: int = 512) -> NDArray[np.int16]:
    """Synthetic int16 mono chunk -- shape only matters for the stub
    transcriber; the value is ignored.
    """
    return np.zeros(samples, dtype=np.int16)


class _ScriptedCapture:
    """Async-iterable stub that yields a fixed sequence of chunks then stops."""

    def __init__(self, chunks: list[NDArray[np.int16]]) -> None:
        self._chunks = list(chunks)
        # Block forever after the scripted chunks so the consumer task
        # is still alive when the test calls ``stop()`` -- mirrors the
        # production behaviour where MicCapture only ends on stop().
        self._idle = asyncio.Event()

    def __aiter__(self) -> AsyncIterator[NDArray[np.int16]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[NDArray[np.int16]]:
        for chunk in self._chunks:
            yield chunk
        # Park until cancellation.
        await self._idle.wait()


class _ScriptedTranscriber:
    """Returns scripted :class:`Transcript` payloads in order."""

    def __init__(self, results: list[Transcript | Exception]) -> None:
        self._results = list(results)
        self.calls = 0

    async def transcribe(self, audio: NDArray[np.int16]) -> Transcript:
        self.calls += 1
        if not self._results:
            # Block once we exhaust scripted results so subsequent
            # capture chunks (if any) don't land on a popped queue.
            await asyncio.Event().wait()
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _count_transcripts(db_path: Path) -> int:
    conn = connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0])
    finally:
        conn.close()


def _fetch_all(db_path: Path) -> list[sqlite3.Row]:
    conn = connect(db_path)
    try:
        return list(conn.execute("SELECT * FROM transcripts ORDER BY ended_at"))
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Happy-path coverage
# ---------------------------------------------------------------------


async def test_high_confidence_transcript_persists_emits_and_fires_trigger(
    db_path: Path,
    session_id: str,
) -> None:
    captured_envelopes: list[Envelope] = []
    fired: list[Intent] = []

    async def _on_intent(intent: Intent) -> None:
        fired.append(intent)

    transcript = Transcript(
        text="let's play with unicorns",
        confidence=0.9,
        language="en",
        duration_ms=1500,
    )
    capture = _ScriptedCapture([_silent_chunk()])
    transcriber = _ScriptedTranscriber([transcript])

    def _matcher(text: str, _db: Path | None) -> list[Intent]:
        return [
            Intent(
                name="request_play",
                slot="unicorns",
                pattern_id="lets_play_with_X",
                confidence=1.0,
            )
        ]

    pipeline = TranscriptPipeline(
        capture=capture,
        transcriber=transcriber,
        session_id=session_id,
        publisher=captured_envelopes.append,
        on_intent=_on_intent,
        db_path=db_path,
        mic_id="mic-1",
        confidence_floor=0.55,
        trigger_matcher=_matcher,
    )
    await pipeline.start()
    try:
        # Wait for the transcribe call + downstream work.
        for _ in range(200):
            if transcriber.calls >= 1 and fired:
                break
            await asyncio.sleep(0.01)
    finally:
        await pipeline.stop()

    assert _count_transcripts(db_path) == 1
    rows = _fetch_all(db_path)
    assert rows[0]["text"] == "let's play with unicorns"
    assert rows[0]["confidence"] == pytest.approx(0.9)
    assert rows[0]["language"] == "en"
    assert rows[0]["mic_id"] == "mic-1"
    assert rows[0]["session_id"] == session_id

    assert len(captured_envelopes) == 1
    envelope = captured_envelopes[0]
    assert envelope.topic is Topic.transcript
    assert envelope.payload["text"] == "let's play with unicorns"
    assert envelope.payload["confidence"] == pytest.approx(0.9)
    assert envelope.payload["language"] == "en"

    assert len(fired) == 1
    assert fired[0].name == "request_play"
    assert fired[0].slot == "unicorns"


async def test_low_confidence_transcript_persists_and_emits_but_does_not_fire_trigger(
    db_path: Path,
    session_id: str,
) -> None:
    captured_envelopes: list[Envelope] = []
    fired: list[Intent] = []

    async def _on_intent(intent: Intent) -> None:
        fired.append(intent)

    transcript = Transcript(
        text="muffled chatter",
        confidence=0.2,
        language="en",
        duration_ms=900,
    )
    transcriber = _ScriptedTranscriber([transcript])

    matcher_calls: list[str] = []

    def _matcher(text: str, _db: Path | None) -> list[Intent]:
        matcher_calls.append(text)
        return []

    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=transcriber,
        session_id=session_id,
        publisher=captured_envelopes.append,
        on_intent=_on_intent,
        db_path=db_path,
        confidence_floor=0.55,
        trigger_matcher=_matcher,
    )
    await pipeline.start()
    try:
        for _ in range(200):
            if transcriber.calls >= 1 and captured_envelopes:
                break
            await asyncio.sleep(0.01)
    finally:
        await pipeline.stop()

    assert _count_transcripts(db_path) == 1
    assert len(captured_envelopes) == 1
    assert fired == []
    # Below-floor short-circuits before the matcher even runs.
    assert matcher_calls == []


async def test_envelope_shape_matches_topic_transcript(
    db_path: Path,
    session_id: str,
) -> None:
    captured_envelopes: list[Envelope] = []
    transcript = Transcript(
        text="hello there",
        confidence=0.8,
        language="en",
        duration_ms=400,
    )
    transcriber = _ScriptedTranscriber([transcript])
    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=transcriber,
        session_id=session_id,
        publisher=captured_envelopes.append,
        db_path=db_path,
        confidence_floor=0.5,
        trigger_matcher=lambda _t, _d: [],
    )
    await pipeline.start()
    try:
        for _ in range(200):
            if captured_envelopes:
                break
            await asyncio.sleep(0.01)
    finally:
        await pipeline.stop()

    assert len(captured_envelopes) == 1
    envelope = captured_envelopes[0]
    assert envelope.topic is Topic.transcript
    assert envelope.schema_version == 1
    assert set(envelope.payload.keys()) >= {
        "text",
        "confidence",
        "started_at",
        "ended_at",
        "language",
    }
    assert envelope.ts.tzinfo is not None  # UTC, not naive


async def test_pipeline_stops_cleanly(db_path: Path, session_id: str) -> None:
    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([]),
        transcriber=_ScriptedTranscriber([]),
        session_id=session_id,
        db_path=db_path,
        trigger_matcher=lambda _t, _d: [],
    )
    # Idempotent before start.
    await pipeline.stop()
    assert pipeline.is_running is False

    await pipeline.start()
    assert pipeline.is_running is True
    # Idempotent during running.
    await pipeline.start()
    assert pipeline.is_running is True

    await pipeline.stop()
    assert pipeline.is_running is False
    # Idempotent after stop.
    await pipeline.stop()
    assert pipeline.is_running is False


async def test_transcribe_failure_does_not_kill_pipeline(
    db_path: Path,
    session_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # First chunk explodes; second chunk succeeds.
    chunks = [_silent_chunk(), _silent_chunk()]
    transcript_ok = Transcript(
        text="i recovered",
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    transcriber = _ScriptedTranscriber(
        [RuntimeError("inference exploded"), transcript_ok]
    )
    capture = _ScriptedCapture(chunks)
    captured_envelopes: list[Envelope] = []

    pipeline = TranscriptPipeline(
        capture=capture,
        transcriber=transcriber,
        session_id=session_id,
        publisher=captured_envelopes.append,
        db_path=db_path,
        trigger_matcher=lambda _t, _d: [],
    )

    with caplog.at_level(logging.WARNING, logger="toybox.audio.pipeline"):
        await pipeline.start()
        try:
            for _ in range(200):
                if transcriber.calls >= 2 and captured_envelopes:
                    break
                await asyncio.sleep(0.01)
        finally:
            await pipeline.stop()

    assert transcriber.calls == 2
    assert _count_transcripts(db_path) == 1
    rows = _fetch_all(db_path)
    assert rows[0]["text"] == "i recovered"
    assert any(
        "transcribe failed" in record.getMessage() for record in caplog.records
    )


async def test_db_insert_failure_does_not_kill_pipeline(
    db_path: Path,
    session_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed INSERT must not crash the pipeline -- subsequent chunks
    must keep flowing and successfully land in the DB.

    We swap ``_insert_transcript`` for a wrapper that raises on the
    first call (simulating e.g. a transient sqlite ``OperationalError``
    or a FK violation) and then delegates to the real implementation
    on every subsequent call. After the first chunk is dropped + logged,
    the second chunk is expected to insert normally and emit its
    envelope, proving recovery rather than just survival.
    """
    captured_envelopes: list[Envelope] = []

    transcript_first = Transcript(
        text="will fail to insert",
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    transcript_second = Transcript(
        text="will insert fine",
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    transcriber = _ScriptedTranscriber([transcript_first, transcript_second])
    capture = _ScriptedCapture([_silent_chunk(), _silent_chunk()])

    pipeline = TranscriptPipeline(
        capture=capture,
        transcriber=transcriber,
        session_id=session_id,
        publisher=captured_envelopes.append,
        db_path=db_path,
        trigger_matcher=lambda _t, _d: [],
    )

    real_insert = pipeline._insert_transcript
    insert_calls = {"n": 0}

    def _flaky_insert(**kwargs: object) -> None:
        insert_calls["n"] += 1
        if insert_calls["n"] == 1:
            raise sqlite3.OperationalError("simulated transient insert failure")
        real_insert(**kwargs)  # type: ignore[arg-type]

    pipeline._insert_transcript = _flaky_insert  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="toybox.audio.pipeline"):
        await pipeline.start()
        try:
            for _ in range(200):
                if transcriber.calls >= 2 and captured_envelopes:
                    break
                await asyncio.sleep(0.01)
        finally:
            await pipeline.stop()

    assert transcriber.calls == 2
    # First insert raised + was skipped; second succeeded -> exactly
    # one row in the DB and exactly one envelope on the wire.
    assert _count_transcripts(db_path) == 1
    rows = _fetch_all(db_path)
    assert rows[0]["text"] == "will insert fine"
    assert len(captured_envelopes) == 1
    assert captured_envelopes[0].payload["text"] == "will insert fine"
    assert any(
        "transcript insert failed" in record.getMessage() for record in caplog.records
    )


async def test_confidence_floor_env_override(
    db_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CONFIDENCE_FLOOR_ENV, "0.9")
    fired: list[Intent] = []

    async def _on_intent(intent: Intent) -> None:
        fired.append(intent)

    transcript = Transcript(
        text="ambiguous",
        confidence=0.7,  # above default 0.55, below env-set 0.9
        language="en",
        duration_ms=500,
    )

    def _matcher(text: str, _db: Path | None) -> list[Intent]:
        return [
            Intent(
                name="request_play",
                slot=None,
                pattern_id="lets_play_X",
                confidence=1.0,
            )
        ]

    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=_ScriptedTranscriber([transcript]),
        session_id=session_id,
        on_intent=_on_intent,
        db_path=db_path,
        trigger_matcher=_matcher,
        # confidence_floor=None forces env resolution
    )
    assert pipeline.confidence_floor == pytest.approx(0.9)

    await pipeline.start()
    try:
        for _ in range(200):
            if _count_transcripts(db_path) >= 1:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)
    finally:
        await pipeline.stop()

    assert _count_transcripts(db_path) == 1
    assert fired == []  # 0.7 < 0.9 → trigger gated


async def test_unknown_language_round_trips_through_db(
    db_path: Path,
    session_id: str,
) -> None:
    transcript = Transcript(
        text="muffled",
        confidence=0.4,
        language=UNKNOWN_LANGUAGE,
        duration_ms=200,
    )
    captured_envelopes: list[Envelope] = []
    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=_ScriptedTranscriber([transcript]),
        session_id=session_id,
        publisher=captured_envelopes.append,
        db_path=db_path,
        confidence_floor=0.55,
        trigger_matcher=lambda _t, _d: [],
    )
    await pipeline.start()
    try:
        for _ in range(200):
            if captured_envelopes:
                break
            await asyncio.sleep(0.01)
    finally:
        await pipeline.stop()

    rows = _fetch_all(db_path)
    assert len(rows) == 1
    assert rows[0]["language"] == UNKNOWN_LANGUAGE
    assert captured_envelopes[0].payload["language"] == UNKNOWN_LANGUAGE


async def test_default_floor_is_055(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONFIDENCE_FLOOR_ENV, raising=False)
    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([]),
        transcriber=_ScriptedTranscriber([]),
        session_id="s",
        db_path=Path("/nonexistent.db"),
        trigger_matcher=lambda _t, _d: [],
    )
    assert pipeline.confidence_floor == pytest.approx(DEFAULT_CONFIDENCE_FLOOR)


async def test_invalid_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(CONFIDENCE_FLOOR_ENV, "not-a-float")
    with caplog.at_level(logging.WARNING, logger="toybox.audio.pipeline"):
        pipeline = TranscriptPipeline(
            capture=_ScriptedCapture([]),
            transcriber=_ScriptedTranscriber([]),
            session_id="s",
            db_path=Path("/nonexistent.db"),
            trigger_matcher=lambda _t, _d: [],
        )
    assert pipeline.confidence_floor == pytest.approx(DEFAULT_CONFIDENCE_FLOOR)
    assert any(
        "is not a float" in record.getMessage() for record in caplog.records
    )


@pytest.mark.parametrize("blank_text", ["", " ", "\n", "\t  "])
async def test_empty_transcript_text_is_dropped(
    db_path: Path,
    session_id: str,
    blank_text: str,
) -> None:
    """Empty / whitespace-only model output is noise -- drop it so the
    audit log stays signal. ``" "``, ``"\\n"``, ``"\\t  "`` etc are all
    falsy under ``.strip()`` even though plain ``not text`` would let
    them through (they're truthy strings).
    """
    captured: list[Envelope] = []
    transcript = Transcript(
        text=blank_text,
        confidence=0.0,
        language=UNKNOWN_LANGUAGE,
        duration_ms=0,
    )
    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=_ScriptedTranscriber([transcript]),
        session_id=session_id,
        publisher=captured.append,
        db_path=db_path,
        trigger_matcher=lambda _t, _d: [],
    )
    await pipeline.start()
    try:
        # Wait long enough for the consumer to ingest + drop.
        for _ in range(50):
            await asyncio.sleep(0.01)
    finally:
        await pipeline.stop()

    assert _count_transcripts(db_path) == 0
    assert captured == []


@pytest.mark.parametrize("text_with_content", ["hello", " hello "])
async def test_non_blank_transcript_text_is_kept(
    db_path: Path,
    session_id: str,
    text_with_content: str,
) -> None:
    """Counterpart to the blank-text drop test: any whitespace-padded
    string that has *some* non-whitespace content must persist.
    """
    captured: list[Envelope] = []
    transcript = Transcript(
        text=text_with_content,
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=_ScriptedTranscriber([transcript]),
        session_id=session_id,
        publisher=captured.append,
        db_path=db_path,
        trigger_matcher=lambda _t, _d: [],
    )
    await pipeline.start()
    try:
        for _ in range(200):
            if _count_transcripts(db_path) >= 1 and captured:
                break
            await asyncio.sleep(0.01)
    finally:
        await pipeline.stop()

    assert _count_transcripts(db_path) == 1
    assert len(captured) == 1
    rows = _fetch_all(db_path)
    assert rows[0]["text"] == text_with_content


async def test_publisher_exception_does_not_kill_pipeline(
    db_path: Path,
    session_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Publisher raises on the first envelope, succeeds on the second.

    The pipeline must keep consuming -- a flaky pubsub call cannot
    take the daemon down. Both transcripts persist (DB write happens
    *before* the publisher), and the second envelope still ships.
    """
    transcript_a = Transcript(
        text="will fail to publish",
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    transcript_b = Transcript(
        text="will publish fine",
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    transcriber = _ScriptedTranscriber([transcript_a, transcript_b])

    captured: list[Envelope] = []
    publisher_calls = {"n": 0}

    def _flaky_publisher(envelope: Envelope) -> None:
        publisher_calls["n"] += 1
        if publisher_calls["n"] == 1:
            raise RuntimeError("publisher exploded")
        captured.append(envelope)

    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk(), _silent_chunk()]),
        transcriber=transcriber,
        session_id=session_id,
        publisher=_flaky_publisher,
        db_path=db_path,
        trigger_matcher=lambda _t, _d: [],
    )

    with caplog.at_level(logging.WARNING, logger="toybox.audio.pipeline"):
        await pipeline.start()
        try:
            for _ in range(200):
                if transcriber.calls >= 2 and captured:
                    break
                await asyncio.sleep(0.01)
        finally:
            await pipeline.stop()

    assert transcriber.calls == 2
    # Both rows persist (DB write happens before publish).
    assert _count_transcripts(db_path) == 2
    # Only the second envelope made it past the flaky publisher.
    assert len(captured) == 1
    assert captured[0].payload["text"] == "will publish fine"
    assert any(
        "transcript publisher raised" in record.getMessage()
        for record in caplog.records
    )


async def test_trigger_matcher_exception_does_not_kill_pipeline(
    db_path: Path,
    session_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Trigger matcher raises -- transcript still persists + emits, only
    the trigger evaluation is skipped. Subsequent chunks must still flow.
    """
    transcript = Transcript(
        text="boom matcher",
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    captured: list[Envelope] = []
    fired: list[Intent] = []

    async def _on_intent(intent: Intent) -> None:
        fired.append(intent)

    def _exploding_matcher(_text: str, _db: Path | None) -> list[Intent]:
        raise RuntimeError("matcher exploded")

    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=_ScriptedTranscriber([transcript]),
        session_id=session_id,
        publisher=captured.append,
        on_intent=_on_intent,
        db_path=db_path,
        confidence_floor=0.55,
        trigger_matcher=_exploding_matcher,
    )

    with caplog.at_level(logging.WARNING, logger="toybox.audio.pipeline"):
        await pipeline.start()
        try:
            for _ in range(200):
                if captured:
                    break
                await asyncio.sleep(0.01)
            # Give the matcher a chance to raise.
            await asyncio.sleep(0.05)
        finally:
            await pipeline.stop()

    # Transcript persisted + envelope emitted -- matcher failure happens
    # *after* both, so neither is affected.
    assert _count_transcripts(db_path) == 1
    assert len(captured) == 1
    # No intents fired -- matcher raised, evaluation skipped.
    assert fired == []
    assert any(
        "trigger matcher raised" in record.getMessage()
        for record in caplog.records
    )


async def test_on_intent_exception_does_not_kill_pipeline(
    db_path: Path,
    session_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``on_intent`` raises on the first invocation, succeeds on the
    second. The exception must not propagate up the consumer loop --
    a single intent-handler crash cannot kill the pipeline.
    """
    transcript = Transcript(
        text="two intents please",
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    intents = [
        Intent(name="a", slot=None, pattern_id="p1", confidence=1.0),
        Intent(name="b", slot=None, pattern_id="p2", confidence=1.0),
    ]
    received: list[Intent] = []
    on_intent_calls = {"n": 0}

    async def _flaky_on_intent(intent: Intent) -> None:
        on_intent_calls["n"] += 1
        if on_intent_calls["n"] == 1:
            raise RuntimeError("handler exploded")
        received.append(intent)

    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=_ScriptedTranscriber([transcript]),
        session_id=session_id,
        on_intent=_flaky_on_intent,
        db_path=db_path,
        confidence_floor=0.55,
        trigger_matcher=lambda _t, _d: list(intents),
    )

    with caplog.at_level(logging.WARNING, logger="toybox.audio.pipeline"):
        await pipeline.start()
        try:
            for _ in range(200):
                if on_intent_calls["n"] >= 2:
                    break
                await asyncio.sleep(0.01)
        finally:
            await pipeline.stop()

    assert on_intent_calls["n"] == 2
    # Only the second intent landed; the first raised + was logged.
    assert len(received) == 1
    assert received[0].name == "b"
    assert any(
        "on_intent handler raised" in record.getMessage()
        for record in caplog.records
    )


async def test_confidence_at_floor_fires_trigger(
    db_path: Path,
    session_id: str,
) -> None:
    """Boundary check: confidence == floor must fire (the gate is ``<``).

    Spec-load-bearing — equality at the floor is the most ambiguous
    case, and silently dropping it would surprise both operators tuning
    the floor and tests pinning it.
    """
    fired: list[Intent] = []

    async def _on_intent(intent: Intent) -> None:
        fired.append(intent)

    transcript = Transcript(
        text="exactly at the floor",
        confidence=0.55,
        language="en",
        duration_ms=300,
    )

    def _matcher(_text: str, _db: Path | None) -> list[Intent]:
        return [Intent(name="floor", slot=None, pattern_id="p", confidence=1.0)]

    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=_ScriptedTranscriber([transcript]),
        session_id=session_id,
        on_intent=_on_intent,
        db_path=db_path,
        confidence_floor=0.55,
        trigger_matcher=_matcher,
    )
    await pipeline.start()
    try:
        for _ in range(200):
            if fired:
                break
            await asyncio.sleep(0.01)
    finally:
        await pipeline.stop()

    assert _count_transcripts(db_path) == 1
    assert len(fired) == 1
    assert fired[0].name == "floor"


async def test_muted_mic_short_circuits_before_transcribe(
    db_path: Path,
    session_id: str,
) -> None:
    """When the mic is muted, the pipeline must NOT call the transcriber.

    Whisper inference is the most expensive step in the chunk handler;
    muting must skip it (not just the persistence + emit). Counts the
    transcriber's ``calls`` to prove inference never ran.
    """
    transcript = Transcript(
        text="should never be returned",
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    transcriber = _ScriptedTranscriber([transcript])
    captured: list[Envelope] = []

    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk()]),
        transcriber=transcriber,
        session_id=session_id,
        publisher=captured.append,
        db_path=db_path,
        confidence_floor=0.55,
        trigger_matcher=lambda _t, _d: [],
        mic_enabled_check=lambda: False,
    )
    await pipeline.start()
    try:
        # Drain whatever the scripted capture has to offer.
        for _ in range(50):
            await asyncio.sleep(0.01)
    finally:
        await pipeline.stop()

    assert transcriber.calls == 0
    assert _count_transcripts(db_path) == 0
    assert captured == []


async def test_mute_toggle_resumes_transcribe(
    db_path: Path,
    session_id: str,
) -> None:
    """Toggling mute back on must let the next chunk reach the transcriber.

    The mute check is read fresh per-chunk; this test pins that a
    mid-stream un-mute resumes Whisper inference on the very next
    speech segment.
    """
    transcript = Transcript(
        text="i am back",
        confidence=0.9,
        language="en",
        duration_ms=300,
    )
    transcriber = _ScriptedTranscriber([transcript])
    captured: list[Envelope] = []

    # Mic toggle: muted for the first chunk, on for the second.
    mic_states = iter([False, True])

    def _check() -> bool:
        return next(mic_states, True)

    pipeline = TranscriptPipeline(
        capture=_ScriptedCapture([_silent_chunk(), _silent_chunk()]),
        transcriber=transcriber,
        session_id=session_id,
        publisher=captured.append,
        db_path=db_path,
        confidence_floor=0.55,
        trigger_matcher=lambda _t, _d: [],
        mic_enabled_check=_check,
    )
    await pipeline.start()
    try:
        for _ in range(200):
            if transcriber.calls >= 1 and captured:
                break
            await asyncio.sleep(0.01)
    finally:
        await pipeline.stop()

    # Exactly one transcribe call -- the first (muted) chunk was skipped,
    # the second (unmuted) chunk ran through Whisper and landed in the DB.
    assert transcriber.calls == 1
    assert _count_transcripts(db_path) == 1
    assert len(captured) == 1
    assert captured[0].payload["text"] == "i am back"
