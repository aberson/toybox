# toybox

Local-first home device that watches for play opportunities, suggests structured activity scripts to a parent, and runs approved activities through a kiosk-style child app featuring AI-driven personas (Wizard, Princess, Detective, Periodic Table Professor, plus a user-grown library).

Runs entirely on home hardware. Internet is optional — Claude is reached over the user's subscription OAuth, and the system degrades to a fully-offline mode without it.

**v1 ship point:** end of Phase A — the closed-loop demo with a manual "trigger" button instead of a real mic. v1 testing is **adult-only** before children participate.

## Build status

**Phase A complete (10/10) — v1 ship point reached.** Issues #1–#10 closed. 286 backend pytest + 99 frontend vitest + 2 Playwright specs passing. 0 type errors, 0 lint violations, 0 format violations.

Backend modules live: `core/`, `db/`, `personas/`, `ai/`, `triggers/`, `activities/`, `api/{listening,activities,auth,auth_dep}`, `ws/{server,heartbeat}`, plus `core/{auth,version_check,queue,pubsub}`. Frontend ships both routes: `/parent` (suggestion + activity panel + trigger button) and `/child` (full-bleed kiosk with persona avatar, step card, advance button, "all done" terminal state). Adult-only smoke testing now; Phase B (audio capture + STT) follows.

## Stack

| Layer | Tool | Why |
|-------|------|-----|
| Backend | Python 3.12 + FastAPI | async-native, ws built-in |
| ASR | faster-whisper (`small`) | local STT; GPU when available, CPU fallback |
| VAD | silero-vad (ONNX) | gates STT on detected speech only |
| AI | Claude (subscription OAuth) | capability-gated; offline mode supported |
| Curated NLP | Python regex + intent registry | fast, deterministic, offline-capable |
| DB | SQLite (WAL mode) | local, file-based, single-writer |
| Mic capture | sounddevice | callback-based, bridged to asyncio |
| Frontend | React + TypeScript + Vite | one project, two routes (`/parent`, `/child`) |
| Real-time | WebSockets (FastAPI) | parent ↔ backend ↔ child |
| Type sync | pydantic-to-typescript | TS types codegen from Pydantic models |
| Tests | pytest + Playwright | unit + integration + UI smoke |
| Lint/format | ruff (line-length=100) | dev/ standard |
| Type check | mypy strict | dev/ standard |
| Package mgmt | uv | dev/ standard |

**Process model:** single uvicorn worker. SQLite + multi-worker leads to silent corruption under contention; mic capture, AI calls, and ws all live in one async process anyway.

## Prerequisites

| Component | Minimum |
|-----------|---------|
| OS | Windows 11 (primary), macOS 13+, Linux (Ubuntu 22.04+) |
| RAM | 8 GB |
| Disk | 5 GB free (incl. ~500 MB whisper-small download) |
| CPU | 4 cores ≥3.0 GHz |
| GPU | not required; `auto`-detected. CUDA path needs CUDA Toolkit 11.8/12.x **and** cuDNN 8.x |
| Mic | any 16 kHz mono-capable USB or built-in mic |
| Network | only required first-run (model download, OAuth) and for Claude calls |

## First-time setup

```powershell
# Python deps
uv sync

# Frontend deps
cd frontend; npm install
npx playwright install
cd ..

# Initialize DB (applies migrations, copies trigger registry, copies persona avatars)
uv run python -m toybox.db.migrate

# Pre-download whisper + VAD models (optional; lazy on first use otherwise)
uv run python -m toybox.audio.stt --download

# Set up Claude OAuth (see documentation/operator/claude-oauth-setup.md)
# Token written to %USERPROFILE%\.toybox\secrets.json on Windows

# Verify
uv run python -m toybox.main --check
```

## Run dev

```powershell
# Terminal 1 — backend (loopback only by default; LAN binding requires PIN)
uv run python -m toybox.main --host 127.0.0.1 --port 8000

# Terminal 2 — frontend
cd frontend; npm run dev

# Open http://localhost:4000/parent
```

Vite pins `server.port: 4000, strictPort: true` and proxies `/api` + `/ws` to the backend at `:8000`.

## Quality gates

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
cd frontend; npm run typecheck; npm run lint; npm run test
```

## Key design decisions

- **Local-first, family-private.** All state stays on one home machine. Internet optional.
- **Single FastAPI process for everything.** Mic capture, STT, NLP, AI calls, REST, ws — all one async process. Trade-off: a slow Claude call could starve the mic loop. Mitigated with `asyncio.to_thread` + circuit breaker.
- **Claude via OAuth, not API key.** Aligns billing with the user's subscription; capability gate falls back to offline cleanly.
- **Linear activity scripts for v1.** Tree branching deferred. "Regenerate from here" effectively branches when needed.
- **Single Vite project, two routes.** Parent and child share types and rendering primitives; child loads the smaller chunk.
- **Single-worker SQLite + WAL.** Multi-worker silently corrupts; one worker is fine for a household device.
- **Optimistic concurrency on activities.** `If-Match-Version` on every mutation; multi-tab races resolve cleanly with 409.
- **Default bind 127.0.0.1.** LAN binding requires a parent PIN (Phase D); startup guard refuses non-loopback host without one.
- **Mic-hot indicator as a first-class UI element.** Constant-on visual signal that mic capture is live.

Full design rationale, schema, and listening-pipeline data flow live in [`documentation/plan.md`](documentation/plan.md).

## Development process

`/build-phase --plan documentation/plan.md` per phase. Steps within a phase use `/build-step` (or `/build-step-tdd` for schema/CRUD work).

Build order: Phase A → B → C → D. Manual operator steps (M1–M5) interleave as marked in the plan.

- **Phase A** — closed-loop skeleton (steps 1–10): project skeleton, schema, persona library, listening state machine, Claude client, NLP registry, offline activity generator, activity API + ws, parent UI, child UI. End of Phase A = v1.
- **Phase B** — hearing (steps 11–14b): audio capture + VAD, faster-whisper, transcript pipeline, mode-aware Claude escalation, end-to-end synthetic-audio test.
- **Phase C** — content (steps 15–18): toy ingest, room ingest bulk, child profile editor, generator wired to real content.
- **Phase D** — polish (steps 19–23): anti-signal feedback, parent PIN gate, transcript management, live activity polish, metrics + operator dashboard.

## Project structure

```
toybox/
├── documentation/
│   ├── plan.md                       # canonical plan
│   └── operator/                     # recovery + setup runbooks
├── src/toybox/                       # backend (Phase A step 1+)
│   ├── api/                          # FastAPI routes + ws
│   ├── core/                         # state machines, errors, capability gate
│   ├── db/migrations/                # forward-only SQL migrations
│   ├── ai/                           # Claude OAuth client + circuit breaker
│   ├── audio/                        # sounddevice + VAD + STT
│   └── triggers/                     # curated NLP registry
├── frontend/
│   ├── src/parent/                   # parent route (App + api + ws + store + components)
│   ├── src/child/                    # child kiosk route (App + api + ws + store + sfx + components)
│   ├── src/shared/                   # types.ts, errors.ts (codegen)
│   ├── public/sfx/                   # SFX assets (silence-stub for v1; real WAVs in M4)
│   └── playwright/                   # e2e smoke specs (parent.spec.ts + child.spec.ts)
├── data/                             # runtime: db, images, models (gitignored)
└── tests/                            # pytest unit + integration
```

## License

Family-private project; not currently licensed for redistribution.
