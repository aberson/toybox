# toybox

Local-first home device that watches for play opportunities, suggests structured activity scripts to a parent, and runs approved activities through a kiosk-style child app featuring AI-driven personas (Wizard, Princess, Detective, Periodic Table Professor, plus a user-grown library).

Runs entirely on home hardware. Internet is optional — Claude is reached over the user's subscription OAuth, and the system degrades to a fully-offline mode without it.

**v1 ship point:** end of Phase A — the closed-loop demo with a manual "trigger" button instead of a real mic. v1 testing is **adult-only** before children participate.

## Build status

**Phase I complete 2026-05-11.** Issues #86–#91 closed (umbrella + 5 steps). Transcripts are now ephemeral by default: a household-scoped `transcript_retention_seconds` setting (presets 1m / 3m / 5m / 10m / 15m, default 1m) drives a 10s-cadence backend sweep that hard-deletes rows past their `ended_at + retention` cutoff, plus a matching filter-on-read clause on the list + search endpoints. Parent UI gains a "Transcript retention" segmented control in `SettingsPanel`; the per-row delete button + `DELETE /api/transcripts/{id}` endpoint + `deleteTranscript` API method are gone (wipe-all + PIN stays). Expired rows fade out client-side via a 1s tick + 600ms CSS transition. 1337 backend pytest + 314 frontend vitest passing. 0 type errors, 0 lint violations. iPad UAT PASS — run doc at [`documentation/runs/2026-05-11-phase-i-uat.md`](documentation/runs/2026-05-11-phase-i-uat.md).

### Phase history (post-v1)

- **v1 ship — Phase A** (2026-05-02): closed-loop demo with manual "trigger" button.
- **Phase B** (2026-05-03): audio capture + silero-VAD + faster-whisper + mode-aware Claude escalation. Real mic + STT live in production.
- **Phase C + D** (2026-05-03): toy/room/child ingestion, activity-quality eval scaffold, anti-signal feedback (commits `20c9b97` + `87e692b`).
- **iPad-Kiosk** (2026-05-04 → 2026-05-10): child kiosk as PWA on real iPad over LAN.
- **Phase F → F.5** (2026-05-06 → 2026-05-09): toy action sprites — F archived after c10.dll crash class ([#61](https://github.com/aberson/toybox/issues/61)); replaced by F.5 (SD 1.5 + LCM-LoRA + Tier C composite). All 5 F.5 steps shipped; #61 closed via F.5-5 soft-pass soak.
- **Phase G** (2026-05-10): branching gameplay — 200 branching templates (50 per intent) via overnight 4-agent soak (50× scope, 0% validation failures); catalog grew 25 → 225 templates.
- **Phase H** (2026-05-10): parent UX revamp — panel-toggle nav → two-level tabbed shell; `banned_themes` promoted from per-child column to global setting.
- **Phase I** (2026-05-11): transcript retention + display refresh — this commit.

### In flight

- **Phase E** (local model + tool-loop): two backend substrate carve-outs shipped ahead of the gated-on-data full ship — Step 28 carve-out 2026-05-05 (commit `33a4b3c`: tool registry + ClaudeActivityGenerator wrapper + env-var dispatch), Step 27 (E3) carve-out 2026-05-13 (commit `4f735a0`: PII redactor `src/toybox/ai/redact.py` + migration 0013 `redact_for_sft` opt-out + `eval_dump.py --sft-export` mode + `data/models/lora/REGISTRY.md` template + end-to-end smoke gate). Full ship remainder gated on ≥50 SFT-filter rows in `labeled_events` — populated naturally as parents tag activities.

Backend modules live: `audio/{capture,vad,ring_buffer,devices,stt,pipeline}`, `core/{escalation,throttle,banned_themes,image_gen_mode,…}`, `image_gen/{worker,capability,…}`, `activities/{generator,content_resolver,slots,_validator,…}`, `api/{listening,activities,auth,auth_dep,transcripts,children,toys,rooms,metrics,image_gen_settings,banned_themes_settings,…}`, `ws/{server,heartbeat,envelope,topics}`. Frontend parent app (`/parent`) is now a two-level tab shell; child kiosk (`/child`) is the full-bleed kiosk with persona avatar + step cards + persona-specific toy action sprites + branching choice buttons.

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

## Run on iPad (kiosk)

The child kiosk runs on a real iPad over the home Wi-Fi LAN — no proxy, no cloud, no app-store install. Full procedure with troubleshooting is in [`documentation/operator/ipad-setup.md`](documentation/operator/ipad-setup.md). Quick path:

**Prereqs (on the home machine):**
- Parent PIN is set (LAN binding is gated on this — confirm via `GET /api/auth/parent/status`).
- Find the home machine's LAN IP (`ipconfig`, IPv4 under the Wi-Fi adapter — not Ethernet, not Hyper-V/Docker/WSL virtual switches).
- **Set `TOYBOX_LAN_IP` and bind backend to `0.0.0.0` in the same shell** — without this env var the backend's WS Origin allow-list is loopback-only and the iPad's WS handshake will be rejected with HTTP 403:
  ```powershell
  $env:TOYBOX_LAN_IP = "192.168.x.x"   # your LAN IP from ipconfig
  uv run python -m toybox.main --host 0.0.0.0 --port 8000
  ```
- Frontend dev server bound to `0.0.0.0`:
  ```powershell
  cd frontend; npm run dev -- --host 0.0.0.0
  ```
- iPad is on the **same Wi-Fi SSID** as the home machine. Guest networks, AP-isolated SSIDs, and corporate networks that block client-to-client traffic do **not** work.

**On the iPad:**
1. Open Safari → navigate to `http://<lan-ip>:4000/child`.
2. Enter the parent PIN once. This confirms LAN reachability and primes iOS audio unlock.
3. Share button → **Add to Home Screen** → name it `toybox` → Add.
4. (Recommended) Lock the kiosk to a single app: Settings → Accessibility → Guided Access → toggle On + set a passcode. Open the home-screen icon, then triple-click the side button to start Guided Access. Triple-click + passcode to exit.

**Dev iteration tip:** desktop Safari → Develop → Enter Responsive Design Mode → pick an iPad preset. Catches viewport / orientation / touch issues without a real iPad in front of you. Audio unlock and Guided Access do require the real device.

If something doesn't work (silent audio, WS won't connect, iPad sleeps mid-activity, home-screen icon disappears), the troubleshooting matrix is in [`documentation/operator/ipad-setup.md#troubleshooting`](documentation/operator/ipad-setup.md#troubleshooting).

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
