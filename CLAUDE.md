# toybox — Project Instructions

## Project overview

Local-first family-private home AI assistant for play with children. Passive-listening home device suggests structured activity scripts to a parent; approved activities run on a child kiosk with persona avatars. All on-device by default.

## Stack

- Python 3.12, uv
- FastAPI, uvicorn (single worker), SQLite (WAL mode)
- Audio: faster-whisper (STT), silero-vad (VAD), ONNX, sounddevice
- Neural TTS (Phase Z): Kokoro-82M via kokoro-onnx, CPU in-process — `tts` optional extra, stub mode `TOYBOX_TTS_STUB=1`
- Auth: argon2-cffi
- Frontend: React + TypeScript + Vite, Zustand, Playwright (e2e)
- Tests: pytest + vitest. Lint: ruff. Typecheck: mypy strict.

## Package manager

uv (backend) + npm (frontend)

## Commands

```powershell
uv sync                                                    # Backend deps
cd frontend; npm install; cd ..                            # Frontend deps
uv run python -m toybox.db.migrate                         # Init DB (REQUIRED before first run)
uv run python -m toybox.audio.stt --download               # Pre-download models (optional)
uv run python -m toybox.tts --download                     # Kokoro TTS models (needs `uv sync --extra tts` first)
uv run python -m toybox.main --check                       # Verify setup
uv run python -m toybox.main --host 127.0.0.1 --port 8000  # Backend (loopback)
cd frontend; npm run dev                                   # Frontend dev (:4000 -> :8000)
uv run pytest                                              # Backend tests
uv run ruff check .                                        # Lint
uv run ruff format --check .                               # Format check
uv run mypy src                                            # Type check
```

Frontend checks:

```powershell
cd frontend; npm run typecheck; npm run lint; npm run test
```

## Directory layout

- `src/toybox/` — backend (`api/`, `core/`, `db/migrations/`, `ai/`, `audio/`, `activities/`, `triggers/`, `ws/`, `image_gen/`, `personas/`, `storage/`, `metrics/`, `tts/`)
- `src/toybox/activities/templates/branching/` — branching templates, one JSON per intent (~1360 across 4 intents: request_play / request_story / request_activity / boredom)
- `frontend/src/parent/` — Parent route (App, api, ws, store, components)
- `frontend/src/child/` — Child kiosk route (PWA, persona avatar, step cards, sprites)
- `frontend/src/shared/` — `types.ts`, `errors.ts` (pydantic→typescript codegen)
- `frontend/public/sfx/` — sound effects
- `frontend/playwright/` — e2e smoke tests
- `tests/` — pytest unit + integration
- `documentation/master-plan.md` — canonical plan + status (the only plan file in `documentation/` root)
- `documentation/plan/` — reference sub-docs (architecture, data-model, api, runtime, activity-loop, how-to-run, risks, appendix) + active/unbuilt phase plans (`phase-e.md`)
- `documentation/plan/awaiting-uat/` — phases whose code shipped but operator UAT is still open (O, P, Q, R, S, T, V, W, X, Z)
- `documentation/plan/archive/` — completed/superseded phase plans (snapshots)
- `documentation/runs/` — phase verification artifacts (UAT pass docs, soak runs)
- `data/` — runtime state (db, images, models, gitignored)

## Gotchas

- **Single uvicorn worker required.** SQLite WAL is single-writer; `--workers >1` silently corrupts. Do not change.
- **LAN binding requires parent PIN.** Startup guard refuses non-loopback host without one — that's intentional.
- **Optimistic concurrency.** Every activity mutation requires `If-Match-Version`; mismatch returns 409.
- **Capability gate.** The primary generation path goes through the async capability gate for offline degradation. Background calls (S2 step-animator, judge) intentionally bypass it and only fail-degrade on outage — see open #245 re: honoring the OFFLINE `listening_mode` setting.
- **Frontend dev port is `:4000`** (proxies `/api` + `/ws` to `:8000`). NOT the typical `:3000`. See [`.claude/rules/frontend-ui.md`](.claude/rules/frontend-ui.md).
- **Claude auth is OAuth-bearer + `urllib` only — no `anthropic` SDK, no API key.** See [`.claude/rules/claude-auth.md`](.claude/rules/claude-auth.md). The SDK was added once and reverted (`32e96f4` → `5bbdefb`); don't re-add it.
- Migrate the DB before running the backend, or DB-backed routes return 500 with "unable to open database file".
- **Bare `uv sync` STRIPS optional extras from the venv** (removes torch/diffusers → phantom mypy `unused-ignore` errors in `image_gen/animate.py` + GPU-test skips). After any pyproject/lock change: `uv sync --extra image_gen`. The `tts` extra stays UNINSTALLED until the operator runs Phase Z M1 (stub tests + lazy-import tests assume the base env).
- **TTS clips are best-effort by design.** `spoken_audio_url` may 404 until the background worker renders; the kiosk falls back to Web Speech (sentence-boundary truncation). Never enqueue TTS in the propose path.

## Pointers

- Plan: `documentation/master-plan.md` (index)
- Plan sub-docs: `documentation/plan/architecture.md`, `data-model.md`, `api.md`, `runtime.md`, `activity-loop.md`, `how-to-run.md`, `risks.md`, `appendix.md`
- Phase Z plan: `documentation/plan/awaiting-uat/phase-z-persona-voices-plan.md` (persona voices: Kokoro-82M neural TTS clips + voice wire-through + sentence-aware fallback truncation) — Z1-Z7-prep CODE-SHIPPED 2026-07-03 (`41e04fe`), #3-#9 closed; operator span = plan `## Manual UAT` M1 audition (#10) → M2 real-engine smoke (#11) → M3 iPad UAT (#12); migration 0031 `neural_voice_enabled`; wire keys `spoken_audio_url` / setup+punchline pair / `spoken_choice_audio_urls` / `spoken_question_audio_url` (unconsumed — question text is never spoken today)
- Phase W plan: `documentation/plan/awaiting-uat/phase-w-plan.md` (play depth: dials, STT grading, adventures, boss fights) — W1-W6 CODE-SHIPPED 2026-06-20 (`3b5df17`); W7 UAT in bundle #223
- Phase X plan: `documentation/plan/awaiting-uat/phase-x-room-import-plan.md` (room import from listing, local-CLIP photo match) — X1-X7 CODE-SHIPPED 2026-06-20 (`87af523`); X8 UAT in bundle #223 (needs `room_classifier --download` first)
- Phase Y plan: `documentation/plan/archive/phase-y-scene-backdrops-plan.md` (scene backdrops behind step card + per-child scene selection) — ✅ COMPLETE 2026-06-23: Y1-Y8 CODE-SHIPPED 2026-06-22 (`f878eb7`) + Manual UAT M1 (#267 render) + M2 (#274 iPad) BOTH PASS (run doc `documentation/runs/2026-06-23-phase-y-uat.md`); umbrella #264 closed. migration 0030 `activities.scene_id`; `scene_catalog` single source of truth + offline `scripts/batch_scenes.py`; resolver chain template scene_id → child interests → default
- SWR re-review (latest, 2026-06-17): `documentation/plan/archive/sonnet-window-revisit-plan.md` + `sonnet-window-revisit-findings.md` — Opus re-review of the Sonnet-window phases (R/S/T/U/V + launcher) at master `f6db361`; #238-243 closed; open follow-ups #244 (kiosk parent-token) + #245 (OFFLINE Claude-call bypass)
- Phase V plan: `documentation/plan/awaiting-uat/phase-v-plan.md` — hybrid SVD-idle + CSS slot-entry; V1/V2 code DONE, V3 iPad UAT (#237) pending operator. (Phase U `documentation/plan/archive/phase-u-plan.md` superseded — AnimateDiff abandoned.)
- Phase T plan: `documentation/plan/awaiting-uat/phase-t-plan.md` — T1 bundled iPad UAT (#223) + T4 catalog UAT (#226) pending; umbrella #222 open
- Operator runbooks: `documentation/operator/`
- README: `README.md`
