# How to run

> **Scope:** system requirements, first-time setup, dev workflow, test commands, configuration env vars, audio capture spec. Read this when bootstrapping a fresh dev machine, debugging the run loop, or adjusting an env knob. Manual runbooks live under `documentation/operator/`.

## System requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Windows 11 (primary), macOS 13+, Linux (Ubuntu 22.04+) | Windows 11 |
| RAM | 8 GB | 16 GB |
| Disk | 5 GB free (incl. ~500 MB whisper-small download + room for transcripts) | 20 GB |
| CPU | 4 cores ≥3.0 GHz | 8+ cores |
| GPU | not required (CPU `small` is faster than realtime) | NVIDIA, ≥4 GB VRAM. GPU mode requires **CUDA Toolkit 11.8 or 12.x AND cuDNN 8.x** (faster-whisper / ctranslate2 needs both). Test via `python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cuda')"` |
| Mic | any USB or built-in mic with 16 kHz mono support | conference USB mic in play area |
| Network | only required first-run (model download, OAuth) and for Claude calls | persistent LAN, optional WAN |

**Child tablet browser:** Chrome 100+ or Safari 16+. Must support WebSocket and Web Audio API. iPad Safari, Chromebook Chrome, and Fire HD Silk Browser have all been verified target platforms.

**First run downloads:** ~500 MB faster-whisper `small` model + ~1 MB silero-vad ONNX from HuggingFace. Cached to `data/models/` afterward; subsequent runs are offline-clean.

## First-time setup

```powershell
cd c:\Users\abero\dev\toybox

# Python deps
uv sync

# Frontend deps
cd frontend; npm install
# Playwright browsers (UI smoke tests; ~300 MB on first install)
npx playwright install
cd ..

# Initialize DB (applies migrations, copies trigger registry to data/, copies persona avatars)
uv run python -m toybox.db.migrate

# Pre-download whisper + VAD models (optional; happens lazily on first transcription otherwise)
uv run python -m toybox.audio.stt --download

# Set up Claude OAuth (see operator/claude-oauth-setup.md)
# Token written to ~/.toybox/secrets.json (Windows: %USERPROFILE%\.toybox\secrets.json)

# Verify
uv run python -m toybox.main --check
# Expected output: ok, db ready, whisper model loaded, vad model loaded, claude capable, mic detected
```

## Run dev

```powershell
# Terminal 1 - backend (loopback only by default; LAN binding requires PIN)
uv run python -m toybox.main --host 127.0.0.1 --port 8000

# Terminal 2 - frontend
cd frontend; npm run dev

# Open http://localhost:4000/parent on the home machine
```

## Run dev — child tablet on LAN (Phase D and later only)

> See also: [phase-ipad-kiosk.md](phase-ipad-kiosk.md) for iPad-specific setup (Add-to-Home-Screen install, Guided Access, Wake Lock, audio unlock troubleshooting). Generic LAN tablet pairing is below; iPad operators should follow this section first to confirm reachability, then jump to the iPad doc.

After Phase D step 21 sets a parent PIN, LAN binding is unlocked:

```powershell
# Find the home machine's LAN IP
ipconfig                          # look for IPv4 Address under your Wi-Fi adapter
$env:TOYBOX_LAN_IP = "192.168.1.42"

# Backend on LAN
uv run python -m toybox.main --host 0.0.0.0 --port 8000

# Frontend on LAN
cd frontend; npm run dev -- --host 0.0.0.0

# Pair the tablet from the parent UI; tablet opens http://<lan-ip>:4000/child
```

**LAN trust assumption:** binding `0.0.0.0` exposes toybox to anyone on your home Wi-Fi. The LAN-binding startup guard prevents it without a PIN; the PIN gate + Origin check are the actual controls. Do not run toybox on a public, hotel, or shared Wi-Fi even with a PIN — these have no defense against pairing-flow phishing.

## Run tests

```powershell
uv run pytest                                    # unit + integration
uv run pytest -m "not requires_claude"           # offline-only suite
cd frontend; npm run test                        # vitest
cd frontend; npm run test:ui                     # playwright smoke
```

## Quality gates (per build-step defaults)

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
cd frontend; npm run typecheck; npm run lint; npm run test
```

## Configuration (env / settings)

| Key | Default | Notes |
|-----|---------|-------|
| `TOYBOX_HOST` | `127.0.0.1` | bind address. Default loopback-only. To bind LAN (`0.0.0.0` or specific IP), the parent PIN must be set first — startup guard refuses non-loopback bind without PIN. |
| `TOYBOX_LAN_IP` | unset | optional; when set, added to the `Origin` allow-list as `http://<value>:4000`. Set this to the home machine's LAN IP after the PIN is configured (Phase D). |
| `TOYBOX_PORT` | 8000 | backend |
| `TOYBOX_DATA_DIR` | `./data` | |
| `TOYBOX_OAUTH_PATH` | `~/.toybox/secrets.json` | Windows: `%USERPROFILE%\.toybox\secrets.json` |
| `TOYBOX_WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3` |
| `TOYBOX_WHISPER_DEVICE` | `auto` | `auto`, `cpu`, `cuda` |
| `TOYBOX_VAD_AGGRESSIVENESS` | 2 | silero-vad threshold, 0 (permissive) – 3 (strict) |
| `TOYBOX_VAD_MIN_SPEECH_MS` | 300 | minimum sustained speech to trigger STT |
| `TOYBOX_MIC_DEVICE_INDEX` | unset (default device) | sounddevice device index; see `python -m sounddevice` |
| `TOYBOX_AUDIO_RING_SECONDS` | 120 | ring buffer of recent audio for STT context (16 kHz mono int16) |
| `TOYBOX_AUDIO_SPEECH_QUEUE_MAXSIZE` | 64 | bounded asyncio queue for VAD-gated speech chunks; drop-oldest on overflow with `mic queue overflow` log |
| `TOYBOX_VAD_THRESHOLD` | 0.5 | silero-vad probability threshold (0.0–1.0); higher = stricter |
| `TOYBOX_VAD_MODEL_PATH` | `data/models/silero_vad.onnx` | override path for the silero ONNX model |
| `TOYBOX_TRANSCRIPT_CONFIDENCE_FLOOR` | 0.55 | `exp(mean_logprob)`-based threshold (0.0–1.0); transcripts below this persist + emit but skip trigger evaluation |
| `TOYBOX_DEFAULT_MODE` | 3 | 1–5 |
| `TOYBOX_CLAUDE_TEXT_MODEL` | `claude-sonnet-4-6` | activity generation, vision-free reasoning. Sonnet 4.6 is the cost/quality default; bump to Opus 4.7 for richer activities once cost is understood. |
| `TOYBOX_CLAUDE_VISION_MODEL` | `claude-haiku-4-5-20251001` | toy + room photo understanding. Haiku is fast and cheap for one-shot vision; sufficient for "name the toy / list room features." |
| `TOYBOX_CLAUDE_MIN_INTERVAL_SEC` | 30 | global Claude min-interval throttle (all modes) |
| `TOYBOX_SPONTANEOUS_INTERVAL_SEC` | 180 | mode 4 spontaneous-Claude-call cadence when no triggers matched recently |
| `TOYBOX_CLAUDE_BREAKER_COOLDOWN_SEC` | 60 | breaker default cooldown when 429 carries no `Retry-After` |
| `TOYBOX_CLAUDE_BREAKER_THRESHOLD` | 3 | consecutive non-429 failures before the breaker opens |
| `TOYBOX_WS_PING_INTERVAL_SEC` | 20 | server-side ping cadence |
| `TOYBOX_WS_PING_TIMEOUT_SEC` | 30 | close if no pong within this window |
| `TOYBOX_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`; logs to stdout, structured JSON when not a TTY |
| `TOYBOX_TIME_OF_DAY_AWARE` | `true` | inject local hour into activity generator context |
| `TOYBOX_PIN_MAX_ATTEMPTS` | 5 | failed PIN attempts before lockout |
| `TOYBOX_PIN_LOCKOUT_SEC` | 900 | lockout duration after exceeding max attempts |
| `TOYBOX_WS_QUEUE_BOUND` | 100 | per-subscriber outbound message queue size |
| `TOYBOX_PARENT_TOKEN_TTL_SEC` | 86400 | sliding expiry for parent session token |
| `TOYBOX_CHILD_TOKEN_TTL_SEC` | 2592000 | child kiosk pairing token TTL (30 days) |

## Audio capture spec

| Property | Value |
|----------|-------|
| Sample rate | 16 kHz (whisper-native) |
| Channels | 1 (mono) |
| Format | int16 PCM |
| Block size | 1024 samples (~64 ms) |
| Ring buffer | 2 minutes (1.92 M samples, ~3.7 MB) |
| VAD chunk | 30 ms windows fed to silero-vad |
| STT chunk | accumulated speech segments, max 3 sec |

## Development process

Use `/build-phase --plan documentation/plan.md` per phase. Steps within a phase use `/build-step` (or `/build-step-tdd` where TDD makes sense — schema/CRUD steps are good TDD candidates).

**Prerequisite before the first `/build-phase` run:** run `/repo-init` to create the GitHub repo + per-step issues, then `/repo-sync` to populate the `**Issue:** #` lines in each step (currently `TBD`). `/build-phase` posts progress to those issues; missing issue numbers break the audit trail. Re-run `/repo-sync` after any plan-doc edits that change step shape or numbering.

Build order: Phase A → B → C → D. Manual steps interleave as marked. See [phase-a.md](phase-a.md) through [phase-e.md](phase-e.md).
