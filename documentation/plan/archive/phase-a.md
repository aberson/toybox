# Phase A — Closed-loop skeleton (v1 ship)

> **ARCHIVED 2026-05-11: phase shipped.** See [plan.md status](../../plan.md#status) for the authoritative completion record. Internal cross-refs in this doc are frozen as of archival.

> **Scope:** the 10 automated steps + Manual M1 that took toybox from empty repo to "trigger demo → suggestion → approve → child runs activity to completion." **Status: COMPLETE (2026-05-02).** Read this for archival/regression context, or when extending one of the modules these steps stood up.

Goal: parent clicks "trigger demo," sees suggestion, approves, child app runs activity to completion. No audio. No Claude.

## Automated steps

| # | Step | Issue | Reviewers | Done-when |
|---|------|-------|-----------|-----------|
| 1 | Project skeleton | #1 | `--reviewers full --start-cmd "uv run python -m toybox.main" --url http://localhost:8000/api/health` | Backend serves `/api/health` returning `capability_reason`; default bind is `127.0.0.1`; LAN-binding startup guard refuses non-loopback host when PIN unset (test: `TOYBOX_HOST=0.0.0.0` → exit non-zero with `code=lan_bind_requires_pin`); vite serves `/parent` and `/child` placeholder pages; vite proxies `/api` and `/ws` to :8000; ruff/mypy/pytest configured and clean; `pydantic-to-typescript` codegen wired with verified `ErrorCode` StrEnum emission (see Phase A step 1 spike note below) |
| 2 | SQLite schema + migrations | #2 | `--reviewers code` | All tables created via `0001_initial.sql` including `auth_tokens`, `image_hash` (toys/rooms), `avatar_image_hash` (personas), `version` (activities), `signature` (feedback), `language` (personas), `UNIQUE(room_id, name)` on `room_features`, all settings keys; partial UNIQUE indexes for image hashes match the spec; FK ON DELETE clauses applied (RESTRICT default, CASCADE for `feedback.activity_id` and `activity_steps.activity_id`); WAL + foreign_keys + busy_timeout pragmas applied at connection open; slug derivation utility + collision-rule tests; round-trip insert/read tests pass for every table; concurrent-write smoke test passes without corruption; migration test pattern wired (every future migration ships `tests/integration/migrations/test_NNNN_*.py` loading a v=N-1 fixture DB and asserting v=N schema) |
| 3 | Persona library JSON + loader | #3 | `--reviewers code` | 4 archetype JSONs validated against `_schema.json`; 4 avatar PNGs shipped + credited in `_credits.md`; loader idempotent (second startup is no-op); avatars copied to `data/images/personas/` on first run |
| 4 | Listening mode state machine | #4 | `--reviewers code` | Modes 1–5 persist; ws emits on change with envelope shape; capability composition tested; `capability_reason` reachable from each path |
| 5 | Claude OAuth client + capability gate + circuit breaker | #5 | `--reviewers code` | `is_capable()` False in offline / missing-token / expired / breaker-open / rate-limited cases each emit correct `capability_reason`; background refresh task implemented; AI call sites stubbed for tests |
| 6 | Curated NLP trigger registry | #6 | `--reviewers code` | 20+ trigger patterns parse correctly; dynamic toy-name trigger registers; user-editable copy seeded to `data/triggers.json` on first run; loader merges shipped defaults into user file |
| 7 | Offline activity generator | #7 | `--reviewers code` | Given (intent, slot, context, hour-of-day) returns a 5-step activity; deterministic given seed; 10 sample inputs produce coherent outputs; time-of-day routing tested |
| 8 | Activity API + ws + auth scaffolding | #8 | `--reviewers code` | Full state machine enforced; `If-Match-Version` enforced on all mutations (409 on mismatch, response body includes current `version`); proposed-queue capped at 5 (oldest auto-dismissed); ws envelope shape matches contract; ws auth requires session token (pre-Phase-D `/api/auth/parent` returns a token without PIN check, but LAN-bind guard from step 1 blocks LAN exposure); Origin header check enforced on `/ws` upgrade + state-changing REST handlers (allow-list test: `Origin: http://evil.example` rejected with 403); per-subscriber bounded queue with drop-oldest + emits `system` notice (`code=ws_backpressure_drop`); ws heartbeat: server pings every 20s, closes connection if no pong within 30s; tests cover happy path + invalid transitions + version conflicts + auth-required topics + Origin reject + backpressure drop-oldest under synthetic burst (200 messages to a stalled subscriber) + concurrent `If-Match-Version` race (two clients, same version, exactly one 409); `child_ids` selected at approval (server fills if 1 child profile) |
| 9 | Parent UI — suggestion + activity panel + mic-hot indicator | #9 | `--reviewers full --start-cmd "<see step 1>" --url "http://localhost:4000/parent" --ui` | Mic-hot indicator visible in header (green/red/grey states); trigger button creates suggestion; approve transitions to running; skip/regenerate/end work; "didn't work" persists; capability banner appears when offline; mic mute toggle works |
| 10 | Child UI — kiosk activity view | #10 | `--reviewers full --start-cmd "<see step 1>" --url "http://localhost:4000/child" --ui` | Persona avatar + current step render; sfx fires on transition (silence stub OK); next-step button advances; ws auto-reconnect tested with state resync on reconnect |

**Phase A step 1 spike — pydantic2ts + StrEnum:** before declaring step 1 done, write a 30-line scratch script that defines `class ErrorCode(StrEnum)` with two members, runs `pydantic2ts` on the module, and inspects the generated TS. If `pydantic2ts` emits the enum as a TS string-literal union (`type ErrorCode = "upload_too_large" | ...`), the codegen path works as planned. If not (older pydantic2ts versions skip non-Pydantic exports), fall back to a 20-line `tools/gen_error_codes_ts.py` that walks `ErrorCode` and writes `frontend/src/shared/errors.ts` directly; wire it into the same pre-commit hook slot. Either way, `errors.ts` must regenerate from `core/errors.py` deterministically.

### Step 1: Project skeleton

- **Problem:** Stand up the backend (FastAPI + uvicorn entrypoint) and frontend (Vite, two routes `/parent` and `/child`) scaffolds, plus the toolchain (ruff line-length=100, mypy strict, pytest) and the pydantic-to-typescript codegen path. Backend serves `GET /api/health` returning `capability_reason`. Default bind is `127.0.0.1`; LAN-bind startup guard refuses non-loopback host without a parent PIN (`TOYBOX_HOST=0.0.0.0` → exit non-zero with `code=lan_bind_requires_pin`). Vite pins `server.port: 4000, strictPort: true` and proxies `/api` + `/ws` to `:8000`. The pydantic2ts + StrEnum spike must verify the codegen path emits a string-literal union (or activate the `tools/gen_error_codes_ts.py` fallback) before this step is "done." See issue #1 for full file list, Done-when, and spike procedure.
- **Type:** code
- **Issue:** #1
- **Flags:** --reviewers full --start-cmd "uv run python -m toybox.main" --url "http://localhost:8000/api/health"
- **Status:** DONE (2026-05-01)

### Step 2: SQLite schema + migrations

- **Problem:** Implement the full v1 SQLite schema in `0001_initial.sql` (toys, personas, children, rooms, room_features, activities, activity_steps, feedback, transcripts, sessions, auth_tokens, settings, schema_migrations) plus the migration runner, a connection helper that applies WAL/synchronous/foreign_keys/busy_timeout pragmas at every open, the slug-derivation utility (`python-slugify` with collision rule and `invalid_display_name` rejection), and the migration test pattern that every future migration must follow. Forward-only — no rollback path. Includes partial UNIQUE indexes for image hashes, FK ON DELETE clauses (RESTRICT default; CASCADE on `feedback.activity_id` and `activity_steps.activity_id`), `auth_tokens` columns, `version` on activities, `signature` on feedback, `language` on personas, and `UNIQUE(room_id, name)` on `room_features`. See issue #2 for required columns and full constraint list.
- **Type:** code
- **Issue:** #2
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

### Step 3: Persona library JSON + loader

- **Problem:** Ship the four library personas (Princess Lyra, Marvelous the Wizard, Inspector Pip, Professor Iridia) as JSON files validated against `_schema.json`, plus their PNG avatars credited in `_credits.md`. The loader runs at startup, is idempotent on second run (no duplicate inserts, no avatar re-copy), and copies avatars to `data/images/personas/` on first run. Library personas can be edited (system_prompt, behavior_tags) but not deleted (only hidden). `avatar_image_hash` is null for library personas; user-uploaded persona avatars participate in the partial UNIQUE index. See issue #3 for persona JSON shape and IP boundary notes.
- **Type:** code
- **Issue:** #3
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

### Step 4: Listening mode state machine

- **Problem:** Implement modes 1–5 with persistent settings (read from `settings` on startup), ws-emit on mode change with the typed envelope shape `{topic, ts, payload, schema_version}`, and a capability composition module whose `capability_reason` enum values (`config_missing`, `token_missing`, `token_expired`, `breaker_open`, `rate_limited`, `network_offline`) are each reachable from at least one path. The state machine is dumb at this step — actual mic + STT + Claude wiring lands in Phase B. Mode 4's spontaneous timer is owned by this layer; the actual Claude call dispatch lands in step 5. Default mode = `TOYBOX_DEFAULT_MODE` env var (default `3`). See issue #4 for mode behaviors and required test coverage.
- **Type:** code
- **Issue:** #4
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

### Step 5: Claude OAuth client + capability gate + circuit breaker

- **Problem:** Wrap the `claude-oauth-auth` flow in an async runtime client pinned to `TOYBOX_CLAUDE_TEXT_MODEL`/`TOYBOX_CLAUDE_VISION_MODEL` env vars (do not hard-code a model). `is_capable()` returns False with the correct `capability_reason` for missing-token / expired / breaker-open / rate-limited / network-offline / config-missing cases. Add a background refresh task that polls token expiry and refreshes within `TOYBOX_OAUTH_REFRESH_LEAD_SEC` of expiry, logging WARNING on refresh failure without crashing. Circuit breaker opens on consecutive failures (default 3) or any 429 (honors `Retry-After`); cooldown default 60s; half-open probe one trial call, success closes / failure reopens. AI call sites are stubbed for tests so steps 7–9 can land without live Claude. See issue #5 for capability-reason matrix and breaker spec.
- **Type:** code
- **Issue:** #5
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

### Step 6: Curated NLP trigger registry

- **Problem:** Build the regex-based trigger registry — 20+ patterns shipped in `defaults.json` plus a dynamic toy-name trigger source (queries `toys` table, refreshes when toys are added/removed; for v1 stub as "rebuild on each match call"). On first run, seed `data/triggers.json` with shipped defaults; on package upgrade, merge new shipped fields into the user file using `version` markers on each pattern. Loader exposes `match(text) -> list[Intent]` API. Deterministic and offline — no Claude calls. User edits to `data/triggers.json` survive package upgrades; the merge logic is the load-bearing piece. See issue #6 for trigger registry shape.
- **Type:** code
- **Issue:** #6
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

### Step 7: Offline activity generator

- **Problem:** Given (intent, slot, context, hour-of-day) return a 5-step linear `Activity`. Deterministic given a seed (same inputs + seed → identical output). Time-of-day routing affects template selection (`morning`, `afternoon`, `evening`, `wind_down`; e.g., `wind_down` excluded outside 19:00–21:00). Output Activity carries `template_id` + sorted slot values for `signature` computation in Phase D step 20 (anti-signal feedback). For Phase A use placeholder content (toys = `["Mr. Unicorn"]`); banned-themes filtering and real toys/rooms wire in Phase C step 19. Linear scripts only — no tree branching. This is the path for modes 1, 3 (when Claude not capable), and the fallback for 4–5 when breaker is open. See issue #7 for activity output shape.
- **Type:** code
- **Issue:** #7
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

### Step 8: Activity API + ws + auth scaffolding

- **Problem:** REST + ws contract for activity lifecycle: propose / approve / skip / regenerate / advance / end / "didn't work." Optimistic concurrency via `If-Match-Version` (decimal integer header) returning 409 + current version on mismatch. Proposed-queue capped at 5 (drop-oldest). ws envelope shape `{topic, ts, payload, schema_version}`. ws auth requires session token; `/api/auth/parent` issues tokens without PIN check pre-Phase-D, but the LAN-bind guard from step 1 blocks LAN exposure regardless. Origin allow-list (`http://localhost:4000`, `http://127.0.0.1:4000`, optional `http://${TOYBOX_LAN_IP}:4000`) enforced on `/ws` upgrade + state-changing REST handlers. Per-subscriber bounded queue (drop-oldest + emits `system` notice with `code=ws_backpressure_drop`). Heartbeat: server pings every 20s, closes on 30s no-pong. Internal pub/sub: publish never blocks; coalesce `triggers.invalidate`. `child_ids` selected at approval time (server fills if exactly 1 child profile exists). Tests cover happy path, invalid transitions, version conflicts, auth-required topics, Origin reject, backpressure drop-oldest under 200-message burst, concurrent `If-Match-Version` race (exactly one 409). See issue #8 for full test matrix.
- **Type:** code
- **Issue:** #8
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-01)

### Step 9: Parent UI — suggestion + activity panel + mic-hot indicator

- **Problem:** Build the parent route (`/parent`) in React + TypeScript + Vite + Zustand: mic-hot indicator (green = capturing, red = error, grey = paused) in header, mic mute toggle, manual trigger button (replaces real mic until Phase B), suggestion card with approve/skip/dismiss, activity panel with regenerate-from-here / end / "didn't work," capability banner that surfaces `capability_reason` when offline. ws auto-reconnect with exponential backoff (1s → 2s → 4s → 8s → 16s → cap at 30s, jitter ±25%) and state resync via REST on reconnect. 409 handling refetches activity and surfaces a toast (no blind retry). Suggestion card "why this?" expandable panel ships in Phase D step 23 — leave a stub. See issue #9 for component file list.
- **Type:** code
- **Issue:** #9
- **Flags:** --reviewers full --start-cmd "uv run python -m toybox.main" --url "http://localhost:4000/parent" --ui
- **Status:** DONE (2026-05-02)

### Step 10: Child UI — kiosk activity view

- **Problem:** Build the child kiosk route (`/child`) in React + TypeScript: full-bleed persona avatar + current step text, next-step button (calls `POST /api/activities/{id}/advance` with `If-Match-Version`), SFX firing on step transition (silence stub acceptable for v1; M4 sources the real WAVs in `frontend/public/sfx/`). ws auto-reconnect with state resync — child page recovers active step + persona without parent intervention. Activity-end transitions to a friendly "all done" state. End of this step closes the v1 Phase A loop: trigger → suggestion → approve → child runs activity → completion. Adult-only smoke test before Phase B starts. See issue #10 for component file list and SFX format spec.
- **Type:** code
- **Issue:** #10
- **Flags:** --reviewers full --start-cmd "uv run python -m toybox.main" --url "http://localhost:4000/child" --ui
- **Status:** DONE (2026-05-02)

**End of Phase A = v1 — COMPLETE (2026-05-02). Smoke-test polish = v1.1 (2026-05-03).** All 10 steps DONE. 288 backend pytest + 99 frontend vitest + 2 Playwright specs passing. Phase B (audio capture + STT) follows.

Step 10 also fixed a pre-existing SQLite cross-thread bug in three FastAPI deps (`api/auth_dep.py`, `api/activities.py`, `api/listening.py`) surfaced by the v1-loop runtime test, plus closed Step 9's open MEDIUM follow-up (reconnect REST refetch race) via new version-aware `applyMutationResult` / `applyReconnectResync` reducers in both child and parent stores. Frontend bootstrap path now retries on transient 5xx via `retryWithBackoff`. See commit `0e55576` and the Step 10 issue (#10) for the full iteration history.

**v1.1 smoke-test polish (commits `c8f85de` → `bcf878a`):** dev port moved 3000→4000 (collision avoidance with another dev/ project); regenerate UUID collision fixed (deterministic seed `(version+1)*31+7` collapsed every v=2 regenerate to the same UUID); regenerate fallback seed switched to `secrets.randbits(31)` so each "skip & try another" yields varied template content; `ended`/`completed` → regenerate now propose-only without dismissing the source (preserves analytics signal); random library persona picked on every propose (`_pick_random_library_persona` in activities.py); persona library now loaded by `python -m toybox.db.migrate` (was previously written but never wired); `metadata.persona` (display_name + archetype + avatar_image_path) spliced into activity payload; parent UI shows `persona: <name>` line on suggestion + activity cards; kiosk avatar letter sources from `metadata.persona.display_name` first char.

## Manual steps

### M1 — Claude OAuth setup (run before Phase B step 12 needs it)

Procedure documented at `documentation/operator/claude-oauth-setup.md`.

```powershell
# Use the claude-oauth-auth skill flow
# Token saved to ~/.toybox/secrets.json
uv run python -m toybox.ai.client --check
```

What to look for:

| Check | Expected |
|-------|----------|
| `~/.toybox/secrets.json` exists | yes |
| `--check` output includes `claude_capable=True` | yes |
| Token expiration shown is in the future | yes |
