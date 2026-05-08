# Architecture

> **Scope:** module map, project layout, and the load-bearing design decisions. Read this when adding a new module, navigating the tree, or arguing for/against a structural change. Schema details live in [data-model.md](data-model.md); HTTP/WS surface in [api.md](api.md); runtime behavior in [runtime.md](runtime.md).

## Process model

Single uvicorn worker. SQLite + multi-worker leads to silent corruption under contention; the listening loop, AI calls, and mic capture all live in one async process anyway.

Vite config pins `server.port: 4000, strictPort: true` (per dev/ memory `feedback_vite_dev_port`); proxies `/api` and `/ws` to backend at `:8000` in dev.

## Modules

### `src/toybox/main.py`
FastAPI entrypoint. Mounts the React build for production; in dev, frontend runs on Vite at `:4000` and proxies to backend at `:8000`. Mic-capture loop runs as an asyncio background task started during app lifespan.

**Lifespan & shutdown:** `@asynccontextmanager` lifespan starts (1) DB migrations, (2) persona loader, (3) Claude OAuth client + refresh task, (4) STT/VAD model load, (5) mic capture, (6) ws subscriber pruner. On shutdown (SIGINT or uvicorn graceful), an `asyncio.Event` is set; each task observes it, drains its in-flight work (≤5 sec), and exits. After 5 sec, remaining tasks are cancelled. Then connections close. Order is reverse of startup.

**`--check` mode:** `python -m toybox.main --check` runs lifespan startup, prints a status table (db ok, models loaded, claude capable, mic detected), and exits 0/1. No network listeners.

**`--smoke` mode:** `python -m toybox.main --smoke` runs lifespan startup, plays a synthetic WAV through the audio pipeline (`tests/fixtures/audio/lets_play_unicorns.wav`), asserts that a suggestion is generated, exits 0/1. CI smoke without Playwright.

**Tools:** `python -m toybox.tools.gc_images` removes orphan images; `python -m toybox.tools.lint_templates` validates that all `{slot}` placeholders in `personas/templates/` correspond to known slot generators.

### `src/toybox/api/`
- `activities.py` — REST: suggest/approve/dismiss/advance/pause/resume/regenerate/end/feedback.
- `toys.py` — REST: CRUD + photo upload.
- `personas.py` — REST: list/create/edit/delete (delete blocked for `source=library`).
- `house.py` — REST: rooms + features + bulk photo upload.
- `children.py` — REST: CRUD.
- `transcripts.py` — REST: list (cursor-paginated) + search (Phase B Step 13). Delete-one + wipe-all are Phase D Step 22.
- `settings.py` — REST: mode get/set; PIN setup; Claude OAuth status; mic mute toggle.
- `metrics.py` — REST `/api/metrics` + in-memory counter aggregation.
- `ws.py` — WebSocket router with topic subscription; envelope shape; topics: `transcript`, `activity.state`, `activity`, `listening.mode`, `system`, `triggers.invalidate`. (`metrics` topic is Phase D Step 24.)

### `src/toybox/audio/`
- `capture.py` — sounddevice mic loop. PortAudio invokes the mic callback on its own native thread; the callback bridges to asyncio via `loop.call_soon_threadsafe(self._handle_frame, frame)`. Frames land in a bounded asyncio queue (configurable via constructor + `TOYBOX_AUDIO_SPEECH_QUEUE_MAXSIZE`, default 64); overflow drops OLDEST and logs `mic queue overflow` printf-style. `--test 5` operator script captures 5s and prints device + peak dB.
- `ring_buffer.py` — thread-safe int16 ring (~2 min, 16 kHz mono; `TOYBOX_AUDIO_RING_SECONDS`, default 120). Snapshot-by-seconds API for STT context.
- `vad.py` — silero-vad ONNX gate with injectable predictor (real `SileroVadPredictor` lazy-loads `data/models/silero_vad.onnx`; tests use stubs). Threshold via `TOYBOX_VAD_THRESHOLD` (default 0.5); hangover frames trim trailing silence; `reset_state()` called per closed segment so LSTM state doesn't bleed across utterances.
- `devices.py` — device enumeration; `resolve_device(env)` honors `TOYBOX_MIC_DEVICE_INDEX` (default = system default).
- `stt.py` — faster-whisper wrapper. `WhisperTranscriber.transcribe(audio)` async via `asyncio.to_thread` + `asyncio.Lock` (CTranslate2 is not thread-safe for concurrent calls). GPU autodetect (CUDA→CPU on init failure with `exc_info`), model selection via `TOYBOX_WHISPER_MODEL` (default `small`), cache at `data/models/`. `Transcript` Pydantic model with `text`, `confidence` (`exp(mean_logprob)` clamped), `language` (`UNKNOWN_LANGUAGE` sentinel for empty audio), `duration_ms`. Operator: `--download` pre-fetches model.
- `pipeline.py` — `TranscriptPipeline` orchestrator (capture → STT → persist → emit on `Topic.transcript` → confidence-floor-gated trigger evaluation). Per-collaborator try/except so a single failure never kills the consumer loop. Confidence floor via `TOYBOX_TRANSCRIPT_CONFIDENCE_FLOOR` (default 0.55). Whitespace-only decodes dropped at the gate.

### `src/toybox/nlp/`
- `triggers.py` — loads `triggers.json`, exposes `match(text) → list[Intent]`. Subscribes to `core/state.py` topic `triggers.invalidate` and rebuilds compiled patterns when toys/personas change. No polling.
- `intents.py` — `Intent` dataclass, slot extraction.

### `src/toybox/ai/`
- `client.py` — Claude OAuth client; capability gate; offline-safe wrappers.
- `activity_gen.py` — Claude path for activity generation.
- `toy_vision.py` — Claude vision for toy cataloging.
- `house_vision.py` — Claude vision for room/feature extraction.

### `src/toybox/core/`
- `activities.py` — state machine, offline template generator, regenerate-from-here logic.
- `modes.py` — listening mode persistence + capability composition.
- `state.py` — in-memory pub/sub for ws topics + capability flips. Interface:
  ```python
  async def publish(topic: str, payload: dict) -> None     # never blocks; drops on overflow
  def subscribe(topics: list[str]) -> AsyncIterator[Envelope]   # bounded queue per subscriber
  def unsubscribe(token: SubscribeToken) -> None
  def current(topic: str) -> dict | None                         # last-seen payload, for resync
  ```
  **Backpressure policy (uniform across external ws subscribers and internal subscribers):** every subscriber owns a single `asyncio.Queue(maxsize=TOYBOX_WS_QUEUE_BOUND)`. `publish` calls `queue.put_nowait` and on `QueueFull` drops the oldest message and emits a `system` notice (`code=ws_backpressure_drop`, includes topic name). Publishers never block — the mic loop, OAuth refresh task, and toy-add handler all stay responsive when a slow subscriber falls behind.

  Internal topic for cross-module signals: `triggers.invalidate` — published by `api/toys.py` on toy add/archive; subscribed by `nlp/triggers.py` to rebuild compiled patterns. Subscriber rebuilds asynchronously; if rebuild is in progress when a second invalidate arrives, the queued event coalesces (deduped) so a burst of toy uploads triggers at most one extra rebuild.
- `feedback.py` — anti-signal storage and lookup for the activity generator.
- `errors.py` — central registry. `class ErrorCode(StrEnum)` lists every code referenced in the plan (`upload_too_large`, `upload_bad_mime`, `duplicate_image`, `invalid_display_name`, `version_conflict`, `ws_backpressure_drop`, `mic_queue_overflow`, `pin_locked`, `token_invalid`, `claude_output_invalid`, …). The `pydantic-to-typescript` codegen also emits `frontend/src/shared/errors.ts` from this enum so server and client share one source of truth.
- `throttle.py` — `MinIntervalThrottle` (Phase B Step 14): bounds Claude call frequency. `TOYBOX_CLAUDE_MIN_INTERVAL_SEC` (default 30s). Injectable clock for tests; thread-safe via `threading.Lock`.
- `escalation.py` — `EscalationDispatcher` (Phase B Step 14): per-mode dispatch table for the 5 listening modes. Gate ordering capability → breaker (state-aware: open → offline; half_open → claim probe slot via `try_half_open()`; closed → proceed) → throttle, so closed gates don't burn the throttle ticket. Cancellation-safe (`except asyncio.CancelledError: raise` then narrow `except Exception`). 429 detection by duck-typing on `status_code == 429` plus class-name fallback (`RateLimitError`/`APIStatusError`, only when `status_code` is None or 429). Honors `Retry-After`. Malformed Claude output → offline + `Topic.system` envelope `code=claude_output_invalid`. `TOYBOX_SPONTANEOUS_INTERVAL_SEC` (default 180s) for mode 4.

### `src/toybox/db/`
- `connection.py` — connection factory, transaction context manager.
- `models.py` — Pydantic models matching schema.
- `migrations/` — `0001_initial.sql`, `0002_*.sql`, …
- `migrate.py` — applies pending migrations on startup.

### `src/toybox/personas/`
- `library/<archetype>.json` — Princess, Wizard, Detective, Periodic Table Professor.
- `library/avatars/<archetype>.png` — **shipped with the package**. Four CC0 / commissioned PNGs, 512×512, transparent background. Sourced via [openclipart.org](https://openclipart.org) (CC0) or commissioned from a freelance illustrator before Phase A step 3 starts. Listed in `library/_credits.md`.
- `library/_schema.json` — JSON Schema for persona files; loader validates against it on startup.
- `templates/<archetype>/<intent>.json` — offline activity templates per archetype × intent.
- `loader.py` — idempotent startup loader; copies avatars from `library/avatars/` into `data/images/personas/` on first run so they're served from the same path as user-created personas.

### `src/toybox/config.py`
Pydantic Settings: paths, model size/device, port, OAuth path, Claude min-interval, default mode.

### `frontend/src/parent/`
Parent app — settings page, listening mode slider, suggestion queue, live activity panel, content management (toys/personas/rooms/kids/transcripts), PIN gate (Phase D).

### `frontend/src/child/`
Child app — kiosk mode: persona avatar, current step card, sfx player, "next step" button.

### `frontend/src/shared/`
- `api.ts` — REST client.
- `ws.ts` — WebSocket client + topic subscription.
- `types.ts` — TypeScript types matching Pydantic models.
- `components/` — shared UI primitives.

## Project structure

```
toybox/
├── pyproject.toml
├── README.md
├── AGENTS.md                       # references parent dev/AGENTS.md + project specifics
├── CLAUDE.md                       # @AGENTS.md
├── documentation/
│   ├── plan.md                     # top-level index (see also plan/*.md)
│   ├── plan/                       # progressive-disclosure sub-plans
│   ├── architecture.md             # design decisions, deeper than plan
│   └── operator/                   # manual step procedures
│       ├── claude-oauth-setup.md
│       ├── mic-hardware-test.md
│       ├── play-session-template.md
│       ├── recovery.md             # DB reset, image cleanup, factory reset, OAuth rotate
│       └── troubleshooting.md      # common errors and fixes
├── src/toybox/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── api/
│   ├── audio/
│   ├── nlp/
│   ├── ai/
│   ├── core/
│   ├── db/
│   │   └── migrations/
│   ├── personas/
│   │   ├── library/
│   │   │   ├── _schema.json
│   │   │   ├── _credits.md
│   │   │   ├── avatars/
│   │   │   └── *.json
│   │   └── templates/
│   └── tools/
│       ├── gc_images.py
│       ├── lint_templates.py
│       ├── check_no_transcript_in_info.py   # pre-commit hook; grep-based
│       └── gen_error_codes_ts.py            # fallback if pydantic2ts can't emit StrEnum (per Phase A step 1 spike)
├── frontend/
│   ├── package.json
│   ├── vite.config.ts              # port 4000, strictPort
│   ├── tsconfig.json
│   ├── index.html
│   ├── public/
│   │   └── sfx/                    # M4 manual step
│   └── src/
│       ├── main.tsx                # routes /parent, /child
│       ├── parent/
│       ├── child/
│       └── shared/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── ui/                         # Playwright smoke
│   └── fixtures/
│       ├── audio/                  # bundled WAVs: silence, "lets_play_unicorns", multi-speaker
│       ├── photos/{toys,rooms}/    # CC0 sample photos
│       ├── claude/                 # canned Claude response JSONs (matched by stub)
│       └── README.md               # license + provenance per asset
├── data/                           # gitignored
│   ├── README.md                   # what's in here, what's safe to delete
│   ├── toybox.db
│   ├── images/{toys,personas,rooms}/
│   ├── images/.staging/            # in-flight uploads, atomic rename on success
│   ├── models/                     # whisper + silero-vad caches; gitignored
│   ├── triggers.json               # user-editable copy of trigger registry
│   └── audio/                      # ephemeral
├── .pre-commit-config.yaml
├── .gitignore
└── .claude/
    └── settings.local.json
```

`data/README.md` documents:
- `toybox.db` — primary DB; safe to back up; do not concurrent-write
- `images/{toys,personas,rooms}/` — UUID-named files, referenced by `*.image_path` in DB; orphan files cleaned by `python -m toybox.tools.gc_images`
- `images/.staging/` — in-flight uploads; safe to wipe when backend is stopped
- `models/` — downloaded ML models; safe to wipe (re-downloads on next run)
- `triggers.json` — user-editable trigger registry; deleting reverts to shipped defaults
- `audio/` — ephemeral; never persists across runs

**`.gitignore` contents:**

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Frontend
frontend/node_modules/
frontend/dist/
frontend/.vite/

# Runtime data (never committed)
data/

# Editor / OS
.vscode/
.idea/
.DS_Store
Thumbs.db

# Local Claude config
.claude/settings.local.json
```

OAuth secrets live at `~/.toybox/secrets.json` (outside the repo); no exclusion rule needed.

## Key design decisions

### Two-path NLP
Curated regex/intent layer is the always-on watchdog; Claude is invoked behind a capability gate. This gives offline-mode parity for the basic loop, hard cost ceilings on Claude usage, and a place to land deterministic behavior the user can audit. Trade-off: the curated layer needs maintenance as kid vocabulary shifts. Mitigation: trigger registry is a JSON file the parent can edit.

### Single FastAPI process for everything
Mic capture, STT, NLP, AI calls, REST, and WebSockets all live in one async process. Simpler ops, single source of truth for state, no IPC complexity. Trade-off: a slow Claude call could starve the mic loop. Mitigation: AI calls run via `asyncio.to_thread` with a circuit breaker; mic loop is its own task that cannot be blocked by API requests.

### Claude via OAuth, not API key
Per the `claude-oauth-auth` skill. Aligns billing with the user's existing subscription, removes API-key management, scales with Anthropic's subscription tiers. Trade-off: refresh logic + token rot. Mitigation: capability gate falls back to offline cleanly; parent UI surfaces token status.

### Linear activity scripts for v1
Tree branching and freeform per-step generation are deferred. Linear scripts are easy to author, render, persist, edit, and learn from. Trade-off: less responsive to in-the-moment kid behavior. Mitigation: "regenerate from here" parent action effectively branches when needed.

### Adult-only initial testing
The user and spouse exercise v1 (and likely v1.5) before any child uses it. This relaxes some UX concerns for the first build (no kid-readability gate on parent UI text) and is the rationale for deferring the PIN gate to Phase D.

### Single Vite project, two routes
Parent and child apps share types and persona/activity rendering primitives. Bundling them together avoids duplication. Trade-off: child bundle is slightly larger than a dedicated build. Mitigation: lazy-load parent-only routes; child kiosk loads the smaller chunk.

### House map = rooms + tagged features
Spatial geometry deferred. Rooms-as-nodes + named features ("couch," "toy chest") gives the activity generator enough hooks ("hide behind the couch in the living room") without requiring vision-derived geometry. Trade-off: no path planning. Acceptable — this isn't a robotics project.

### Anti-signal feedback model
"This didn't work" stored as a `feedback` row, threaded into the activity generator as both an offline filter (block exact step patterns) and a Claude-prompt addendum (free text "things this family doesn't like"). Trade-off: requires the parent to actually press the button. Mitigation: dismissed-pre-approval also counts as soft anti-signal.

### Single-worker SQLite + WAL
SQLite + multi-worker writes corrupt silently. Toybox runs as one uvicorn worker; mic, STT, NLP, AI calls, and HTTP all live in one async process. Trade-off: no horizontal scale. Acceptable — a household device serves a household.

### Optimistic concurrency on activities
Activity state mutations require `If-Match-Version` matching the row's current `version` integer. Two parent tabs racing approve/dismiss → second loses with 409 + current state. Cheap to add now; would require schema migration + every mutation site to update later. Justified by the multi-tab parent-app expectation.

### Curated NLP registry copied into `data/`
Shipped registry seeds a user-editable copy in `data/triggers.json`. Survives package upgrades; lets parents tune triggers without editing source. Trade-off: schema migrations on the registry need a merge step. Mitigation: pattern entries carry a `version` field; loader fills missing fields from shipped defaults.

### Mic-hot indicator as a first-class UI element
A passively-listening device aimed at children needs a constant-on visual indicator that mic capture is live. Independent of mode (mode 1 still records). Trust + consent posture, and a hard-to-ignore sanity check during development.

### Pydantic ↔ TypeScript codegen
Manual type sync between Pydantic models and `frontend/src/shared/types.ts` drifts. `pydantic-to-typescript` runs as a Make target / pre-commit hook on backend changes. Trade-off: an extra build step. Worth it — drift here corrupts both sides silently.

## Listening pipeline data flow

```
mic (sounddevice)
  → ring buffer (in-memory, ~2 min)
  → chunked emit (every ~3 sec)
  → faster-whisper.transcribe()
  → Transcript {text, confidence, timestamps}
  → triggers.match(text)
  → on hit: Intent {slug, slot} → core.activities.handle_intent(intent, mode)
       ├─ mode 1: offline template generator
       ├─ mode 2: no-op (await parent solicit)
       ├─ mode 3: claude path if capable, else offline
       ├─ mode 4: claude path + spontaneous timer
       └─ mode 5: every-utterance claude path (throttled)
  → ws emit on `activity.state` topic
  → parent UI surfaces suggestion card
```
