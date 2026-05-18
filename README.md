# toybox

Local-first home device that watches for play opportunities, suggests structured activity scripts to a parent, and runs approved activities through a kiosk-style child app featuring AI-driven personas (Wizard, Princess, Detective, Periodic Table Professor, plus a user-grown library).

Runs entirely on home hardware. Internet is optional — Claude is reached over the user's subscription OAuth, and the system degrades to a fully-offline mode without it.

**v1 ship point:** end of Phase A — the closed-loop demo with a manual "trigger" button instead of a real mic. v1 testing is **adult-only** before children participate.

## Build status

**Phase M autonomous block + M2b complete 2026-05-18.** 11 autonomous-block step issues closed (#153 M1, #155 M3, #156 M4, #157 M5, #158 M6, #160 M8, #161 M9, #162 M10, #163 M11, #164 M12, #165 M13) + M2b sprite render shipped at master `ad5c5f7` with a mid-session prompt+overlay redesign (SD 1.5 + 4-step LCM cannot render legible glyphs at 512², so the original card-text-in-prompt design was swapped for a Pillow text overlay — rounded white panel, Comic Sans Bold, periodic-table-cell layout — composited post-diffusion; 14 canonical sprites committed, other 104 rendered locally + gitignored). M2 + M7 split into code subtasks (M2a #154 + M7a #159 — both shipped) plus operator subtasks. M7b Coqui TTS audio render in flight; M14 iPad UAT staged with run-doc at [`documentation/runs/2026-05-18-phase-m-uat.md`](documentation/runs/2026-05-18-phase-m-uat.md) (12 curated activities pre-filled). Umbrella [#152](https://github.com/aberson/toybox/issues/152) stays open until M7b + M14 land.

Phase M delivers two parallel content-depth tracks through the existing branching-template + corpus + persona substrate (Phase G/K/L); zero new step kinds, one schema addition (`step.element_id`), one new theme value (`feelings`):

- **Track 1 — Periodic Table Professor expansion** (direct serve for Child B, 4yo Periodic-Table-fascinated pre-reader): 118-entry element corpus + loader + injection guard (M1) → 118-element sprite render script (M2a, M2b runtime deferred) → kiosk `ElementCard` + `step.element_id` cross-stack wire + new `/api/static/elements/` mount (M3) → 118 "Meet an Element" single-step templates (M4) → 30 element-family pretend-play templates (M5) → 15 Magic-School-Bus shrink-down journeys (M6) → 25 element-themed song manifest entries (M7a, M7b TTS render deferred).
- **Track 2 — SEL templates** (direct serve for Child A, 6yo early-reader, social play): new `Theme.feelings` enum + 87-row downstream grep audit (M8) → 20 feelings-naming branching templates (M9) → 20 two-act perspective-taking templates (M10) → 25 conflict-resolution templates split across `request_play` + `request_activity` (M11) → 15 friendship/repair templates with mandatory "first-try-fails, second-try-works" recovery in all 45 forks (M12).
- **Cross-cutting:** 8-sub-test Phase M smoke gate (M13) — propose → approve → advance → reward through real corpora + real DB, no mocks. Caught a latent M4 step-id bug that would have broken the kiosk's ElementCard at running state; fixed inline.

**Catalog growth: 1000 → 1243 templates** (+243). Backend **1983 pytest pass / 3 skipped** (+51 net from 1932 at Phase L close), **frontend 598 vitest pass** (unchanged net after M3 trim). 0 type errors, 0 lint violations. M13 smoke gate runs in 1.65s. iPad UAT (M14) + sprite render (M2b) + TTS render (M7b) pending the bundled operator session per the new operator-step-shape rule at [`.claude/rules/plan-and-issue-flow.md`](../.claude/rules/plan-and-issue-flow.md) § "Operator-type steps must not produce code artifacts" (promoted from a per-project memory to a workspace rule mid-session after the M2 mid-build halt — wired into `/plan-review` Section 22 + `/plan-wrap` Section 11 to prevent recurrence).

**Phase L (rewards system) shipped 2026-05-17 at master `5aaf8ed`** — formerly missing from this README. L1-L12 + 2 emergent fix rounds; 1932 pytest + 592 vitest at close; UAT iter 3 all PASS. Load-bearing pattern: two-phase terminal advance keeps `state=running` while the reward step renders, Phase 2 dismiss transitions to `completed`. Jokes/songs reframed as per-activity reward TYPES; embedded/ending/spontaneity interjection surfaces deleted in favor of set-intersection tag matching.

### Phase history (post-v1)

- **v1 ship — Phase A** (2026-05-02): closed-loop demo with manual "trigger" button.
- **Phase B** (2026-05-03): audio capture + silero-VAD + faster-whisper + mode-aware Claude escalation. Real mic + STT live in production.
- **Phase C + D** (2026-05-03): toy/room/child ingestion, activity-quality eval scaffold, anti-signal feedback (commits `20c9b97` + `87e692b`).
- **iPad-Kiosk** (2026-05-04 → 2026-05-10): child kiosk as PWA on real iPad over LAN.
- **Phase F → F.5** (2026-05-06 → 2026-05-09): toy action sprites — F archived after c10.dll crash class ([#61](https://github.com/aberson/toybox/issues/61)); replaced by F.5 (SD 1.5 + LCM-LoRA + Tier C composite). All 5 F.5 steps shipped; #61 closed via F.5-5 soft-pass soak.
- **Phase G** (2026-05-10): branching gameplay — 200 branching templates (50 per intent) via overnight 4-agent soak (50× scope, 0% validation failures); catalog grew 25 → 225 templates.
- **Phase H** (2026-05-10): parent UX revamp — panel-toggle nav → two-level tabbed shell; `banned_themes` promoted from per-child column to global setting.
- **Phase I** (2026-05-11): transcript retention + display refresh — household-scoped retention preset, 10s-cadence sweep, fade-out animation.
- **Phase J** (2026-05-14): autonomous play queue — parent Play surface becomes a scrolling queue fed by an autonomous cadence task + transcript-driven `on_intent` wire; tunable `play_target_depth` ∈ {1, 3, 5} and `play_cadence_seconds` ∈ {0, 10, 30, 60}; ActivityPanel pins as queue head when one is approved.
- **Phase K** (2026-05-15 → 2026-05-16): roles + songs + jokes + voice — K1-K15 substrate 2026-05-15; K16 + K16b template backfill brought the catalog to 1000 templates (250 × 4 intents); K17 smoke gate green; K18 iPad UAT 12/14 PASS 2026-05-16; two cosmetic defects filed as follow-ups ([#137](https://github.com/aberson/toybox/issues/137), [#138](https://github.com/aberson/toybox/issues/138)).
- **Phase L** (2026-05-17): rewards system + jokes/songs as per-activity reward TYPES — L1-L12 + 2 emergent fix rounds; embedded/ending/spontaneity surfaces deleted in favor of set-intersection tag matching; load-bearing two-phase terminal advance pattern keeps `state=running` while reward step renders. 1932 pytest + 592 vitest at close. UAT iter 3 all PASS. Master `5aaf8ed`.
- **Phase M** (2026-05-18): content depth — Periodic Table Professor expansion (Track 1: all 118 elements + ElementCard + 118 Meet templates + 30 family-pretend + 15 shrink-down + 25 element-themed songs) and SEL templates (Track 2: new `Theme.feelings` + 80 templates across feelings-naming + perspective-taking + conflict-resolution + friendship-repair). Autonomous block (M1, M2a, M3-M13) shipped 2026-05-18 at master `768ad1d`; M2b sprite render shipped at `ad5c5f7` with mid-session prompt+overlay redesign (Pillow text overlay over orb-of-element diffusion; 14 canonical sprites in git, 104 local-only); M7b TTS audio render + M14 iPad UAT staged for operator session (run doc: [`documentation/runs/2026-05-18-phase-m-uat.md`](documentation/runs/2026-05-18-phase-m-uat.md)). Catalog 1000 → 1243 templates. 1983 pytest + 598 vitest. M13 8-sub-test smoke gate runs in 1.65s with no mocks. Umbrella [#152](https://github.com/aberson/toybox/issues/152) stays open until the operator session closes M7b + M14.

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
