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

- `src/toybox/` — backend (`api/`, `core/`, `db/migrations/`, `ai/`, `audio/`, `activities/`, `triggers/`, `ws/`)
- `src/toybox/activities/templates/branching/` — 200 branching templates (~50 per intent)
- `frontend/src/parent/` — Parent route (App, api, ws, store, components)
- `frontend/src/child/` — Child kiosk route (PWA, persona avatar, step cards, sprites)
- `frontend/src/shared/` — `types.ts`, `errors.ts` (pydantic→typescript codegen)
- `frontend/public/sfx/` — sound effects
- `frontend/playwright/` — e2e smoke tests
- `tests/` — pytest unit + integration
- `documentation/plan.md` — canonical plan + status
- `documentation/plan/` — sub-docs (architecture, data-model, api, runtime, activity-loop, how-to-run, risks)
- `documentation/runs/` — phase verification artifacts (UAT pass docs, soak runs)
- `data/` — runtime state (db, images, models, gitignored)

## Gotchas

- **Single uvicorn worker required.** SQLite WAL is single-writer; `--workers >1` silently corrupts. Do not change.
- **LAN binding requires parent PIN.** Startup guard refuses non-loopback host without one — that's intentional.
- **Optimistic concurrency.** Every activity mutation requires `If-Match-Version`; mismatch returns 409.
- **Capability gate.** Every Claude call goes through the capability gate for offline degradation.
- **Frontend dev port is `:4000`** (proxies `/api` + `/ws` to `:8000`). NOT the typical `:3000`. See [`.claude/rules/frontend-ui.md`](.claude/rules/frontend-ui.md).
- **Claude auth is OAuth-bearer + `urllib` only — no `anthropic` SDK, no API key.** See [`.claude/rules/claude-auth.md`](.claude/rules/claude-auth.md). The SDK was added once and reverted (`32e96f4` → `5bbdefb`); don't re-add it.
- Migrate the DB before running the backend, or DB-backed routes return 500 with "unable to open database file".

## Pointers

- Plan: `documentation/plan.md` (12 KB index)
- Plan sub-docs: `documentation/plan/architecture.md`, `data-model.md`, `api.md`, `runtime.md`, `activity-loop.md`, `how-to-run.md`, `risks.md`, `appendix.md`
- Operator runbooks: `documentation/operator/`
- README: `README.md`
