# Phase B — Hearing

> **ARCHIVED 2026-05-11: phase shipped.** See [plan.md status](../../plan.md#status) for the authoritative completion record. Internal cross-refs in this doc are frozen as of archival.

> **Scope:** the 5 steps + Manual M2 that wired audio capture → VAD → faster-whisper → trigger registry → mode-aware Claude escalation, plus a synthetic-audio E2E smoke. **Status: COMPLETE (2026-05-03).** Read this for module-level orientation when touching `audio/`, `core/escalation`, or `core/throttle`.

**Issues #15–#19 closed. 610 backend pytest passing (1 slow E2E excluded from default `pytest`). Zero type errors. Zero lint violations.**

Phase B code-complete (2026-05-03). Manual M1 (Claude OAuth setup) DONE via Claude-CLI-creds bridge; Manual M2 (mic hardware test) DONE on the M2 chassis. The slow E2E `tests/e2e/test_smoke_pipeline.py::test_smoke_synthetic_audio_full_loop` is the regression defense — it runs `uv run --with playwright pytest -m slow tests/e2e/test_smoke_pipeline.py` end-to-end in ~10 s with a port-collision pre-flight that bails fast (~1 s) when an operator's local dev session is bound to `:8000` or `:4000`.

## Steps

### Step 11: Audio capture daemon + VAD gate

- **Problem:** Use `sounddevice` to capture 16 kHz mono int16 audio in a callback bridged to asyncio (callback enqueues into a bounded asyncio queue; overflow logs `mic_queue_overflow` and drops oldest). Maintain a ring buffer of ~2 min so transcript context can include recent audio. Apply `silero-vad` (ONNX) to gate downstream STT — only speech chunks emit; non-speech is dropped at the gate. Mic device selection honors `TOYBOX_MIC_DEVICE_INDEX` (default = system default). Synthetic-buffer tests cover capture queueing, ring rotation, VAD gating thresholds, and overflow behavior. Operator script `uv run python -m toybox.audio.capture --test 5` captures 5 seconds, prints detected device name, peak dB level, and any overflow events. Manual M2 hardware test runs after this step. See issue #15 for component file list and test matrix.
- **Type:** code
- **Issue:** #15
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-03)

### Step 12: faster-whisper integration

- **Problem:** Wire `faster-whisper` (`small` model by default; configurable via `TOYBOX_WHISPER_MODEL`). GPU auto-detect (CUDA when available + cuDNN 8.x present), CPU fallback otherwise. First-run downloads the model to `data/models/` (~500 MB); subsequent runs load from cache. A bundled fixture WAV transcribes within edit-distance tolerance (test asserts decoded text vs. reference using `Levenshtein.ratio >= 0.85`). Each transcript carries `confidence` (mean segment log-probability mapped to 0–1). `transcribe()` runs in `asyncio.to_thread` so the mic capture loop is never blocked by inference. Operator flag `uv run python -m toybox.audio.stt --download` pre-fetches the model. See issue #16 for model-cache layout and confidence mapping.
- **Type:** code
- **Issue:** #16
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-03)

### Step 13: Transcript pipeline + persistence + ws

- **Problem:** Wire VAD-gated speech chunks → `faster-whisper` → `Transcript` records. Persist each transcript to the `transcripts` table with `text`, `confidence`, `started_at`, `ended_at`, and `language`. The live `transcripts` ws topic emits a per-transcript envelope `{topic, ts, payload, schema_version}` with `payload = {text, confidence, started_at, ended_at}`. Transcripts below `TOYBOX_TRANSCRIPT_CONFIDENCE_FLOOR` (default 0.55) are stored (so the audit trail captures everything) but bypass trigger evaluation, preventing spurious matches on garbage decodes. Synthetic transcript stream tests cover trigger firing on curated phrases, DB row insertion, ws emission shape, and confidence-floor gating. See issue #17 for the transcript-to-trigger flow diagram.
- **Type:** code
- **Issue:** #17
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-03)

### Step 14: Mode-aware Claude escalation + rate-limit handling

- **Problem:** Wire transcript → trigger match → mode-aware Claude escalation per the five listening modes. Mode 1 (offline only): no Claude calls ever. Mode 2 (curated only): trigger matches use the offline generator only. Mode 3 (curated → Claude): curated trigger matches escalate to Claude when `is_capable()`; offline fallback when capability gate is closed or breaker is open. Mode 4 (curated + spontaneous): mode 3 behavior plus a spontaneous timer (`TOYBOX_SPONTANEOUS_INTERVAL_SEC`) that fires Claude calls when no triggers have matched recently. Mode 5 (always-on): every transcript above the confidence floor escalates to Claude. A min-interval throttle (`TOYBOX_CLAUDE_MIN_INTERVAL_SEC`, default 30s) prevents hammering Claude regardless of mode. 429 responses open the circuit breaker honoring `Retry-After` (per Step 5's breaker spec); queued escalations route to the offline generator until the breaker closes. Malformed Claude output (Pydantic validation failure) → fall back to offline + emit `system` ws warning with `code=claude_output_invalid`. Tests use a mocked Claude client; assert exact call counts per mode under synthetic input; assert breaker state transitions on injected 429s; assert offline fallback engages on validation failure. See issue #18 for the mode-by-mode call-count expectation table.
- **Type:** code
- **Issue:** #18
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-03)

### Step 14b: E2E pipeline test (synthetic audio → child UI)

- **Problem:** End-to-end smoke test exercising the full v1 listening loop with the real backend in synthetic-audio mode. `uv run python -m toybox.main --smoke` boots the backend with a test-only WAV-to-buffer adapter (replaces the live mic device), plays `tests/fixtures/audio/lets_play_unicorns.wav` through the audio pipeline → silero-vad → faster-whisper → trigger registry → suggestion fires on the parent ws topic. Playwright then drives the parent UI: connects, observes the suggestion card, clicks approve. The child UI route (loaded in a second browser context) recovers the active activity via reconnect-resync and renders step 1 with the persona avatar. The test is marked `@pytest.mark.slow` (excluded from default `pytest`; included in CI nightly). It defends the v1-loop architecture against regressions in audio capture, VAD, STT, trigger registry, activity API, ws envelopes, and both UI routes simultaneously. End of Step 14b closes Phase B. See issue #19 for the fixture WAV spec and the full Playwright assertion list.
- **Type:** code
- **Issue:** #19
- **Flags:** --reviewers full --start-cmd "uv run python -m toybox.main" --url "http://localhost:4000/parent" --ui
- **Status:** DONE (2026-05-03, commits `b523a67` + `7977378`)

## Manual M2 — Mic hardware test (after step 11)

```powershell
uv run python -m toybox.audio.capture --test 5
```

What to look for:

| Check | Expected |
|-------|----------|
| Default mic detected and named in output | yes (USB or laptop mic) |
| 5 seconds of audio captured | yes |
| Peak level > -40 dB while speaking | yes |
| No buffer overruns logged | none |

## What was built

- **Audio capture daemon (#15):** `src/toybox/audio/{capture,vad,ring_buffer,devices}.py`. `MicCapture` bridges sounddevice's PortAudio thread → asyncio via `loop.call_soon_threadsafe` with bounded frame + speech queues (drop-oldest + structured `mic_queue_overflow` log). `VadGate` runs silero-vad with an injectable predictor (real `SileroVadPredictor` lazy-loads `data/models/silero_vad.onnx`; tests use stubs). `RingBuffer` keeps ~2 min of recent int16 audio for STT context. Async iterator yields VAD-gated speech chunks. Operator: `uv run python -m toybox.audio.capture --test 5`.
- **faster-whisper STT (#16):** `src/toybox/audio/stt.py`. `WhisperTranscriber` wraps `WhisperModel` with `asyncio.to_thread` + `asyncio.Lock` serialization (CTranslate2 isn't thread-safe for concurrent calls). GPU autodetect with CPU fallback, env-driven model selection, lifecycle `close()`, int16→float32 normalization with clip. `Transcript` Pydantic model carries `text`, `confidence` (`exp(mean_logprob)` clamped), `language` (`UNKNOWN_LANGUAGE` sentinel), `duration_ms`. Operator: `uv run python -m toybox.audio.stt --download`.
- **Transcript pipeline + persistence + ws (#17):** `src/toybox/audio/pipeline.py` orchestrator (capture → STT → persist → emit on `Topic.transcript` → confidence-floor-gated trigger evaluation). Per-collaborator try/except so a single failure (transcribe / db / publisher / matcher / on_intent) never kills the consumer loop. `src/toybox/api/transcripts.py` ships read-only `GET /api/transcripts` (paginated, ISO `before` cursor with `fromisoformat` validation) and `GET /api/transcripts/search?q=` (case-insensitive, parameterized LIKE). Migration 0002 adds `language TEXT NOT NULL DEFAULT 'unknown'` to the `transcripts` table.
- **Mode-aware Claude escalation (#18):** `src/toybox/core/{escalation,throttle}.py`. `EscalationDispatcher` implements the per-mode dispatch table (offline-only, curated-only, curated→Claude, mode-3+spontaneous, always-on). Gate ordering is **capability → breaker (state-aware: open → offline; half_open → claim probe slot via `try_half_open()`; closed → proceed) → throttle**, so closed gates don't burn the throttle ticket. Cancellation-safe: explicit `except asyncio.CancelledError: raise` then narrow `except Exception`. 429 detection by duck-typing on `status_code == 429` plus class-name fallback (`RateLimitError`/`APIStatusError`, only when `status_code` is None or 429 — prevents 5xx APIStatusError from being mis-classified). Honors `Retry-After`. Malformed Claude output → offline fallback + `Topic.system` envelope `{code: "claude_output_invalid", model, preview}`. Stable offline seed via `zlib.crc32`.

## Files changed

| File | Change |
|---|---|
| `src/toybox/audio/{__init__,capture,vad,ring_buffer,devices}.py` | NEW — audio capture pipeline (#15) |
| `src/toybox/audio/stt.py` | NEW — `WhisperTranscriber` (#16) |
| `src/toybox/audio/pipeline.py` | NEW — `TranscriptPipeline` orchestrator (#17) |
| `src/toybox/api/transcripts.py` | NEW — read-only transcripts REST routes (#17) |
| `src/toybox/db/migrations/0002_transcript_language.sql` | NEW — adds `language` column |
| `src/toybox/core/{escalation,throttle}.py` | NEW — mode dispatcher + min-interval throttle (#18) |
| `src/toybox/app.py` | MODIFIED — wires the transcripts router |
| `pyproject.toml` + `uv.lock` | new deps: `sounddevice`, `numpy`, `onnxruntime`, `faster-whisper`, `python-Levenshtein` (dev) |
| `.gitignore` | switched `data/` → `data/*` with `!data/.gitkeep`, `!data/models/`, `!data/models/.gitkeep` re-includes |
| `data/.gitkeep`, `data/models/.gitkeep` | NEW — cache directory placeholders |
| `tests/unit/audio/{test_capture,test_vad,test_ring_buffer,test_stt}.py` | NEW — 116 unit tests |
| `tests/unit/test_throttle.py` | NEW — 14 throttle tests |
| `tests/integration/test_transcript_pipeline.py` | NEW — pipeline integration (16 tests) |
| `tests/integration/test_transcripts_api.py` | NEW — REST integration (28 tests) |
| `tests/integration/migrations/test_0002_transcript_language.py` | NEW — migration tests (4) |
| `tests/integration/test_escalation_modes.py` | NEW — per-mode call-count assertions (29 tests) |
| `tests/integration/test_breaker_429_escalation.py` | NEW — 429 → breaker → offline (5 tests) |
| `tests/integration/test_claude_output_invalid.py` | NEW — malformed-output fallback (6 tests) |
| `tests/integration/migrations/test_0001_initial.py`, `tests/integration/test_schema.py` | MODIFIED — relaxed assertions to accommodate added migration |

## Fresh-context notes for Phase B

| Issue | Detail |
|---|---|
| Audio module is testable without hardware | Tests inject synthetic int16 buffers into `MicCapture._handle_frame` and stub the silero-vad predictor; never opens a real PortAudio stream. The `_FakeStream` factory pattern is the test seam. |
| STT module never downloads in CI | Tests inject a `_FakeWhisperModel` via `model_factory`. The real model only downloads via the operator's `--download` command or first live `transcribe()` call. |
| `data/models/` ships in repo via gitignore re-includes | `.gitignore` was changed from `data/` to `data/*` so cache directory placeholders can ship. Model binaries themselves remain untracked. |
| Transcript table has `triggered_intent` column from 0001 | Pipeline doesn't currently populate it (matched intents are dispatched to the `on_intent` callback, not stored alongside the transcript row). Future step can wire this if needed. |
| Live pipeline is NOT wired into app startup | `TranscriptPipeline` is constructible but Step 14b (E2E smoke) is what boots the daemon. `app.py` only mounts the transcripts API router. |
| `EscalationDispatcher` consumes capability via async callable | Pass `is_capable_from_state(...)` or a curried `is_capable(...)` so the dispatcher doesn't depend on the capability module's full signature. |
| Mode-5 with no triggers synthesizes `intent="boredom"` | The `boredom.json` template pool exists, so the offline fallback always lands somewhere. |
| Min-interval throttle is global, not per-mode | One `MinIntervalThrottle` instance per dispatcher. The throttle ticket is consumed only after capability + breaker pass — closed gates don't burn it. |
| `_is_rate_limit_error` excludes non-429 `APIStatusError` | The class-name fallback was tightened in iter-2 polish: `APIStatusError` with `status_code=500` (server error) is no longer mis-classified as a rate limit. |
| 4 build-step iterations had pre-existing flaky `test_ws_heartbeat::test_server_pings_periodically` | Passes in isolation and on re-run. Not introduced by Phase B. Worth investigating in v1.5 polish. |
