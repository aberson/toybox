# Appendix

> **Scope:** reference material — persona JSON shape, frontend / Python / CI configs, test fixtures inventory, doc stubs, future scope. Read selectively — most sections are only relevant when you're configuring that specific surface.

## Persona JSON shape

```json
{
  "id": "wizard",
  "display_name": "Marvelous the Wizard",
  "archetype": "wizard",
  "system_prompt": "You are Marvelous, a kindly old wizard who speaks in rhymes and treats every problem as a magical puzzle. You never frighten children. You love nature, riddles, and small kindnesses. When invited to play, you propose challenges that involve searching, naming, or making things up.",
  "avatar_image_path": "library/avatars/wizard.png",
  "behavior_tags": ["kind", "rhyming", "puzzling", "gentle"],
  "age_range_min": 3,
  "age_range_max": 12,
  "source": "library",
  "default_voice_tone": "warm-and-slow"
}
```

The four shipped library personas:

- **Princess Lyra** — brave-and-curious archetype, treats the house as a kingdom, fond of quests.
- **Marvelous the Wizard** — kindly riddler, magical puzzles.
- **Inspector Pip (Detective)** — questions everything, "the case of the missing X" framing.
- **Professor Iridia (Periodic Table Professor)** — every element has a personality; chemistry as a play motif. Custom for the user's son.

## Trigger registry shape

```json
{
  "version": 1,
  "patterns": [
    {
      "regex": "(?i)let'?s play\\s+(.+)",
      "intent": "request_play",
      "slot_group": 1
    }
  ]
}
```

## `frontend/package.json` outline

```jsonc
{
  "name": "toybox-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "typecheck": "tsc -b --noEmit",
    "lint": "eslint src --max-warnings=0",
    "test": "vitest run",
    "test:ui": "playwright test"
  },
  "dependencies": {
    "react": "^18",
    "react-dom": "^18",
    "react-router-dom": "^6",
    "zustand": "^4"
  },
  "devDependencies": {
    "typescript": "^5",
    "@types/react": "^18",
    "@types/react-dom": "^18",
    "@vitejs/plugin-react": "^4",
    "vite": "^5",
    "vitest": "^1",
    "@playwright/test": "^1",
    "eslint": "^9",
    "@typescript-eslint/parser": "^7",
    "@typescript-eslint/eslint-plugin": "^7"
  }
}
```

## `tsconfig.json`

```jsonc
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitOverride": true,
    "exactOptionalPropertyTypes": true,
    "noFallthroughCasesInSwitch": true,
    "isolatedModules": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "allowImportingTsExtensions": false,
    "outDir": "./dist",
    "baseUrl": "./src",
    "paths": {
      "@shared/*": ["shared/*"],
      "@parent/*": ["parent/*"],
      "@child/*": ["child/*"]
    }
  },
  "include": ["src/**/*", "tests/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

## `vite.config.ts` proxy

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 4000,
    strictPort: true,
    host: true,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
```

## `pyproject.toml` dependency outline

```toml
[project]
name = "toybox"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    "python-multipart>=0.0.9",
    "sounddevice>=0.4",
    "numpy>=1.26",
    "faster-whisper>=1.0",
    "onnxruntime>=1.17",
    "argon2-cffi>=23",
    "python-slugify>=8",
    "anthropic>=0.25",
    "httpx>=0.27",
    "Pillow>=10",
    "pillow-heif>=0.16",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
    "ruff>=0.4",
    "mypy>=1.10",
    "pydantic-to-typescript>=2",
    "pre-commit>=3",
]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]   # dev/AGENTS.md standard

[tool.mypy]
strict = true
disallow_untyped_defs = true
no_implicit_optional = true

[tool.pytest.ini_options]
markers = [
    "requires_claude: integration test that hits Claude OAuth",
    "requires_gpu: needs CUDA",
    "slow: end-to-end pipeline test, runs in nightly CI only",
]
```

## Playwright config (`frontend/playwright.config.ts`)

```ts
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/ui',
  fullyParallel: false,           // backend has shared mic state
  retries: 1,
  use: {
    baseURL: 'http://localhost:4000',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'tablet',   use: { ...devices['iPad (gen 7)'] } },
  ],
  webServer: [
    {
      command: 'cd .. && uv run python -m toybox.main --host 127.0.0.1 --port 8765',
      port: 8765,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'npm run dev -- --port 4000',
      port: 4000,
      reuseExistingServer: !process.env.CI,
    },
  ],
})
```

Backend port shifted to 8765 in tests so CI runs don't collide with a dev backend on 8000.

## `.pre-commit-config.yaml`

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: uv run ruff check
        language: system
        types: [python]
      - id: ruff-format-check
        name: ruff format --check
        entry: uv run ruff format --check
        language: system
        types: [python]
      - id: mypy
        name: mypy
        entry: uv run mypy src
        language: system
        types: [python]
        pass_filenames: false
      - id: pydantic-to-typescript
        name: regenerate frontend types
        entry: uv run pydantic2ts --module toybox.api.dto --output frontend/src/shared/types.ts
        language: system
        types: [python]
        pass_filenames: false
      - id: no-transcript-in-info-logs
        name: no transcript text in INFO+ logs
        entry: uv run python -m toybox.tools.check_no_transcript_in_info
        language: system
        types: [python]
```

## Test stubbing strategy

- **Unit tests** use `pytest-mock` (`mocker.patch.object`) for narrow stubbing.
- **Claude calls** stubbed via FastAPI dependency override: `app.dependency_overrides[get_claude_client] = lambda: FakeClaudeClient(canned_responses)`. Canned responses live in `tests/fixtures/claude/<scenario>.json`.
- **STT calls** stubbed by injecting a fake `Transcriber` that returns canned `Transcript` objects from a queue.
- **Vision calls** stubbed similarly; Anthropic vision is non-deterministic so live calls are gated behind `@pytest.mark.requires_claude` and skipped in default runs.
- **Audio fixtures** are real WAVs; STT runs end-to-end against them in `slow`-marked tests only.
- **DB tests** use a fresh on-disk SQLite per test (NOT in-memory — must validate WAL pragmas).

## Test fixtures inventory (`tests/fixtures/`)

| File | Purpose | License / source |
|------|---------|------------------|
| `audio/silence_3s.wav` | VAD negative test | generated by `tests/fixtures/_gen_silence.py` (committed); regenerated as needed |
| `audio/lets_play_unicorns.wav` | trigger positive test (E2E smoke) | recorded by author or freesound.org CC0 |
| `audio/im_bored.wav` | boredom intent positive | as above |
| `audio/multi_speaker.wav` | overlap robustness | as above |
| `photos/toys/plush_unicorn.jpg` | toy ingest happy path | CC0 (e.g. unsplash.com — record exact URL) |
| `photos/toys/blurry.jpg` | low-confidence vision | as above |
| `photos/rooms/living_room.jpg` | room ingest happy path | CC0 |
| `photos/rooms/kitchen.jpg` | second room | CC0 |
| `claude/activity_request_play_unicorns.json` | canned activity-gen response | hand-authored |
| `claude/activity_boredom.json` | canned activity-gen response | hand-authored |
| `claude/vision_toy_unicorn.json` | canned toy vision response | hand-authored |
| `claude/vision_room_living.json` | canned room vision response | hand-authored |
| `claude/error_429.json` | rate-limit response | synthetic |
| `claude/error_malformed.json` | schema-validation failure | synthetic |
| `feedback/signatures.json` | anti-signal test rows: 3 `didnt_work` + 2 `loved_it` covering matching `template_id` / slot fingerprints, plus 1 near-miss (different slot fill) for negative test | hand-authored |

`tests/fixtures/README.md` records exact source URL + license per asset.

## Asset `_credits.md` schema

Both `src/toybox/personas/library/_credits.md` and `frontend/public/sfx/_credits.md` follow this format:

```markdown
| File | Title | Author | Source URL | License |
|------|-------|--------|------------|---------|
| `wizard.png` | "Friendly Wizard" | (commissioned) Jane Doe | https://example.com/portfolio/123 | CC-BY-4.0 |
```

## Project root document outlines

`README.md` (skeleton — fleshed out by `/repo-init`):

```markdown
# toybox
AI assistant for play with children. Local-first, family-private.

See `documentation/plan.md` for full architecture + build plan.

## Quick start
- Python 3.12 + `uv sync`
- Frontend: `cd frontend && npm install`
- DB: `uv run python -m toybox.db.migrate`
- Run: `uv run python -m toybox.main --host 0.0.0.0` + `cd frontend && npm run dev`

## Status
v1 builds in 4 phases (A–D); 24 automated steps + 5 manual.
```

`AGENTS.md` (project-specific overrides over `dev/AGENTS.md`):

```markdown
# toybox agent instructions

Inherits from `dev/AGENTS.md`. Project-specific:

## Setup
- Python 3.12, `uv sync` (extras: `dev`)
- Frontend: `cd frontend && npm install`
- DB migrations: `uv run python -m toybox.db.migrate`

## Architecture pointers
- See `documentation/plan.md` for full architecture
- See `documentation/operator/` for runbooks
- Single uvicorn worker — never `--workers >1` (SQLite WAL is single-writer)

## Working rules
- Never log transcript text at INFO+
- Every Claude call goes through the capability gate
- Every activity mutation requires `If-Match-Version`
- Photo uploads always go through the validation pipeline (no direct `Image.open` on user bytes outside it)
```

`CLAUDE.md`:

```markdown
@AGENTS.md

# Toybox-specific notes for Claude Code
- Project root: `c:\Users\abero\dev\toybox\`
- Plan: `documentation/plan.md` is the source of truth
- Phase boundaries: never mix phase-A work with phase-B work in one PR
- When in doubt about ws topic shapes or DB schema, re-read the plan section, don't infer
```

## Operator markdown stubs

Each file lives in `documentation/operator/`.

**`claude-oauth-setup.md`:**
- Run `claude-oauth-auth` skill flow
- Paste resulting token into `~/.toybox/secrets.json` (Windows: `%USERPROFILE%\.toybox\secrets.json`)
- Run `uv run python -m toybox.main --check`; expect `claude_capable=True`
- Token rotation: just re-run; the file is overwritten

**`mic-hardware-test.md`:**
- List devices: `uv run python -m sounddevice`
- Quick test: `uv run python -m toybox.audio.capture --test 5`
- Pin a specific device: `setx TOYBOX_MIC_DEVICE_INDEX <N>` (Windows) and restart
- Troubleshooting: device permissions, sample-rate negotiation, USB hub power

**`play-session-template.md`:**
- Pre-flight: backend running, parent UI shows mic-hot green, mode set
- During session: parent UI suggestion approvals, child UI on tablet
- Post-session: skim transcripts for false negatives; tag flop activities with "didn't work"
- Issue template for friction reports (text body)

**`recovery.md`:**
- Recovery recipes from the [phase-d.md "Manual M5"](archive/phase-d.md#manual-m5--operator-recovery-procedures-referenced-from-documentationoperatorrecoverymd) table expanded with full commands
- Each recipe lists: symptom, prerequisites (backup first?), exact commands, verification step

**`troubleshooting.md`:**
- Common error codes and what to do (cross-reference to `core/errors.py`)
- Mic dropouts, Claude rate-limit, ws disconnects, Pillow CVE updates
- "When to escalate to opening an issue" decision tree

## `.github/workflows/ci.yml` (outline; v1 ship optional)

```yaml
name: ci
on: [push, pull_request]
jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --extra dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src
      - run: uv run pytest -m "not slow and not requires_claude and not requires_gpu"
      - name: pydantic2ts drift check
        run: |
          uv run pydantic2ts --module toybox.api.dto --output frontend/src/shared/types.ts
          git diff --exit-code frontend/src/shared/types.ts
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd frontend && npm ci
      - run: cd frontend && npm run typecheck
      - run: cd frontend && npm run lint
      - run: cd frontend && npm run test
```

## Persona library JSON Schema

`src/toybox/personas/library/_schema.json` validates every file in `library/*.json` (excluding `_*.json`). Required fields: `id`, `display_name`, `archetype`, `system_prompt`, `avatar_image_path`, `behavior_tags`, `age_range_min`, `age_range_max`, `source`. Schema enforced by loader on startup; malformed files logged and skipped.

## Future scope (out of v1)

- Voice synthesis (TTS) for personas
- Camera observation
- Phone-app mic source
- Multi-mic with child apps as sources
- Native child kiosk app
- Redfin/Zillow URL scraping
- Spatial house map with geometry
- Tree-branching activities
- Real-time freeform activity generation (vs current "regenerate from here")
- Persona voice library (recorded clips)
- Backups: nightly DB snapshot with 14-day retention
- Auto-start on boot (Windows service / systemd unit)
- Localization / i18n (currently English-only in persona prompts and UI strings; `personas.language` field already in v1 schema)
- Dark mode + accessibility audit on parent app
- Persona-image regeneration from prompts (so users can recommission their library art)
- `mDNS` / zeroconf discovery (`toybox.local`) so child tablet finds backend without manual IP
- Multi-worker SQLite migration (likely Postgres) if device ever leaves single-host deployment
- Transcript archive policy: auto-archive transcripts older than 30 days into `data/transcripts-archive-YYYY-MM.jsonl.gz` and drop from main DB to keep the live table snappy
- Secret-question PIN reset path so the SQL-DELETE recovery isn't the only one
- Audit log of admin actions (PIN changes, transcript wipes, persona library overrides)
- Dependabot / renovate config for Pillow + pillow-heif CVE hygiene; promote to v1 if not addressed by `/repo-init`
- `CHANGELOG.md` (Keep-a-Changelog format) — track schema migrations and behavioral changes
- Process supervision (Windows service / systemd unit) so the backend survives terminal close
- Disk-quota enforcement (warn/halt at configured cap on `data/` size)
- AI-authored offline templates: one-shot Claude pass that generates N parametric templates per intent (using the slot registry vocabulary), validates them against `_schema.json`, and persists them as new JSON files under `src/toybox/activities/templates/`. Companion `tools/lint_templates` extension to enforce the slot whitelist on every reference. Goal: rich offline variety without per-propose Claude calls.
