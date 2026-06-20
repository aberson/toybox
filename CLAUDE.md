# toybox — Project Instructions

## Project overview

Local-first family-private home AI assistant for play with children. Passive-listening home device suggests structured activity scripts to a parent; approved activities run on a child kiosk with persona avatars. All on-device by default.

## Stack

- Python 3.12, uv
- FastAPI, uvicorn (single worker), SQLite (WAL mode)
- Audio: faster-whisper (STT), silero-vad (VAD), ONNX, sounddevice
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

- `src/toybox/` — backend (`api/`, `core/`, `db/migrations/`, `ai/`, `audio/`, `activities/`, `triggers/`, `ws/`, `image_gen/`, `personas/`, `storage/`, `metrics/`)
- `src/toybox/activities/templates/branching/` — branching templates, one JSON per intent (~1360 across 4 intents: request_play / request_story / request_activity / boredom)
- `frontend/src/parent/` — Parent route (App, api, ws, store, components)
- `frontend/src/child/` — Child kiosk route (PWA, persona avatar, step cards, sprites)
- `frontend/src/shared/` — `types.ts`, `errors.ts` (pydantic→typescript codegen)
- `frontend/public/sfx/` — sound effects
- `frontend/playwright/` — e2e smoke tests
- `tests/` — pytest unit + integration
- `documentation/master-plan.md` — canonical plan + status (the only plan file in `documentation/` root)
- `documentation/plan/` — reference sub-docs (architecture, data-model, api, runtime, activity-loop, how-to-run, risks, appendix) + active/unbuilt phase plans (`phase-e.md`)
- `documentation/plan/awaiting-uat/` — phases whose code shipped but operator UAT is still open (O, P, Q, R, S, T, V, W, X)
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

## Pointers

- Plan: `documentation/master-plan.md` (index)
- Plan sub-docs: `documentation/plan/architecture.md`, `data-model.md`, `api.md`, `runtime.md`, `activity-loop.md`, `how-to-run.md`, `risks.md`, `appendix.md`
- Phase W plan: `documentation/plan/awaiting-uat/phase-w-plan.md` (play depth: dials, STT grading, adventures, boss fights) — W1-W6 CODE-SHIPPED 2026-06-20 (`3b5df17`); W7 UAT in bundle #223
- Phase X plan: `documentation/plan/awaiting-uat/phase-x-room-import-plan.md` (room import from listing, local-CLIP photo match) — X1-X7 CODE-SHIPPED 2026-06-20 (`87af523`); X8 UAT in bundle #223 (needs `room_classifier --download` first)
- SWR re-review (latest, 2026-06-17): `documentation/plan/archive/sonnet-window-revisit-plan.md` + `sonnet-window-revisit-findings.md` — Opus re-review of the Sonnet-window phases (R/S/T/U/V + launcher) at master `f6db361`; #238-243 closed; open follow-ups #244 (kiosk parent-token) + #245 (OFFLINE Claude-call bypass)
- Phase V plan: `documentation/plan/awaiting-uat/phase-v-plan.md` — hybrid SVD-idle + CSS slot-entry; V1/V2 code DONE, V3 iPad UAT (#237) pending operator. (Phase U `documentation/plan/archive/phase-u-plan.md` superseded — AnimateDiff abandoned.)
- Phase T plan: `documentation/plan/awaiting-uat/phase-t-plan.md` — T1 bundled iPad UAT (#223) + T4 catalog UAT (#226) pending; umbrella #222 open
- Operator runbooks: `documentation/operator/`
- README: `README.md`
