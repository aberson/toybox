# Phase C — Content

> **Scope:** the 5 steps + Manual M3 that wired real catalog content (toys, rooms, child profiles) into both offline and Claude generation paths, plus the activity-quality telemetry / eval scaffold. **Status: COMPLETE (2026-05-03).** Read this when touching ingestion, the eval rubric, or the content resolver.

| # | Step | Reviewers | Done-when |
|---|------|-----------|-----------|
| 15 | Activity-quality telemetry & eval scaffold | `--reviewers code` | New `labeled_events` table records every generation (Claude or offline) with structured `inputs_json`, `activity_json`, `generator_path` (`claude`/`offline`), `parent_signal` (nullable, filled when known), `judge_scores` (nullable). Generator I/O refactored so `inputs` are emitted as ChatML messages — same record format usable as SFT input later. Six-dimension rubric (`schema`, `age_appropriateness`, `doability`, `persona_fidelity`, `coherence`, `safety`) lives in `src/toybox/ai/rubric.py`; safety score 1 auto-fails the activity. Held-out fixture set (≥5 cases covering ages × personas × edge cases per `documentation/eval-fixtures.md`) under `tests/fixtures/eval/`. Claude-as-judge runs async on 1-in-N live generations (default N=5, env-tunable via `TOYBOX_EVAL_JUDGE_RATE`); never blocks the kid-facing path. CI regression run on the held-out set fails the build if mean dimension score drops >0.5 from baseline OR any safety auto-fail appears. CLI `uv run python -m toybox.ai.eval_dump --since <ISO>` exports labeled events as ChatML JSONL (the SFT input format for Phase E). Parent-signal capture wires existing actions: thumbs-up = +1, dismiss-before-start = -1, end-early = -0.5 (with `ended_at_step`); thumbs-up button is added to live activity panel if not present. **Critical:** judge is a cost-saving proxy, not ground truth — parent_signal is the only real label and must be queryable independently. |
| 16 | Toy ingest (vision + UI) | `--reviewers full --ui` | All upload validation rules enforced (size, MIME-sniff, dimensions, UUID-rename, atomic staging); SHA-256 dedup returns existing toy on collision; vision → suggested fields → parent confirms → row inserted with `image_hash`; offline path skips vision; mention_toy registry refreshes |
| 17 | Room ingest bulk (vision + UI) | `--reviewers full --ui` | Bulk-cap of 50 enforced; per-file validation per Upload validation rules; per-photo vision → tabbed review UI → rooms + features inserted; dedup applied |
| 18 | Child profile editor | `--reviewers full --ui` | Full CRUD; banned-themes flow into activity generator (offline filter + Claude prompt); reading_level affects step text complexity |
| 19 | Activity generator uses real content | `--reviewers code` | Real toys/rooms appear in generated steps; tests use fixture catalog; banned-themes filtering tested; anti-signal feedback consulted; every generation continues to write a `labeled_events` row per step 15 |

**Issues:** Phase C umbrella #22 · step 15 → #23 · step 16 → #30 · step 17 → #31 · step 18 → #32 · step 19 → #33

> **Operating mode (2026-05-03):** Steps 16, 17, 18 are run autonomously with `--reviewers code` (no `--ui` runtime reviewer). Visual UI verification is batched across steps 16–19 in a single end-to-end UI test pass after step 19 lands (see [phase-d-uat-m2.5.md](phase-d-uat-m2.5.md)). The plan's per-step "Recommended flags" line below remains the canonical view; the override is a session-level operating decision driven by the user's preference for autonomous build + bundled UI testing (see memory `feedback_autonomous_build_bundled_ui.md`).

### Step 15: Activity-quality telemetry & eval scaffold

- **Problem:** Add a new `labeled_events` table that records every activity generation (offline OR Claude) with structured ChatML inputs, the generated `activity_json`, the `generator_path` (`claude`/`offline`/`local`), `parent_signal` (-1 / -0.5 / 0 / +1, nullable), `ended_at_step` (nullable), and `judge_scores_json` (nullable). Generator inputs are emitted as ChatML system + user messages so the same record format flows into Phase E SFT iterations without a shape change. Build a 6-dimension rubric (`schema`, `age_appropriateness`, `doability`, `persona_fidelity`, `coherence`, `safety`) in `src/toybox/ai/rubric.py` with 1-5 anchors per `documentation/eval-fixtures.md`; safety = 1 auto-fails the activity. Wire a Claude-as-judge async caller (`src/toybox/ai/judge.py`) sampled at 1-in-N (default N=5, env-tunable via `TOYBOX_EVAL_JUDGE_RATE`); the judge call is fully async and never blocks the kid-facing path — failures (timeout, 429, malformed output) log WARNING and leave `judge_scores_json` NULL. Ship 20 fixtures under `tests/fixtures/eval/prompts.jsonl` covering the documented age × persona × trigger × room × edge-case matrix; pin 5 IDs in `holdout.json` for CI regression. Provide `uv run python -m toybox.ai.eval_dump --since <ISO>` (ChatML JSONL export of `labeled_events`) and `uv run python -m toybox.ai.eval_run` (fixture batch + judge + baseline regen / CI regression check). Wire parent thumbs-up button (parent_signal=+1), dismiss-before-start (parent_signal=-1), and end-early (parent_signal=-0.5 with `ended_at_step`) to update the matching `labeled_events` row by `activity_id`. **Critical:** judge is a cost-saving proxy, NOT ground truth — `parent_signal` is the only real label and remains queryable independently from `judge_scores_json`. The schema supports the Phase E SFT export query (`safety>=4 AND mean_quality>=3.5 AND parent_signal != -1`) without further migration.
- **Type:** code
- **Issue:** #23
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-03, commit `20c9b99`)

### Step 16: Toy ingest (vision + UI)

- **Problem:** Single-toy ingest pipeline. Parent uploads one photo → backend validates against §"Upload validation rules" (size, MIME-sniff via libmagic, dimensions, UUID-rename) and stages atomically to `data/images/.staging/<uuid>.<ext>`. SHA-256 dedup against existing non-archived `toys.image_hash` returns 409 with the existing toy on collision. On unique upload, downscale to ≤1600 long edge and call `ai.toy_vision(bytes)` (Claude Haiku via OAuth, `TOYBOX_CLAUDE_VISION_MODEL`) for suggested `display_name`, `tags`, `persona_match_id`. Vision failures (timeout/429/malformed) return `suggested: null` plus `vision_error`; parent fills manually. Parent confirms → file moved to `data/images/toys/<uuid>.<ext>`, `toys` row inserted with `image_hash`, `mention_toy` trigger registry refreshes. Offline mode (Claude not capable) skips vision entirely. Janitor sweeps `.staging/` files older than 1h. Shared `src/toybox/storage/images.py` helper handles validate/dedup/stage/commit (reused by step 17). All vision calls mocked in default tests; live calls gated `@pytest.mark.requires_claude`. See issue #30 for full pipeline order, API shape, and pitfalls.
- **Type:** full-stack
- **Issue:** #30
- **Flags:** --reviewers full --ui (canonical) — running autonomously this session as `--reviewers code` per operating mode note above
- **Status:** DONE (2026-05-03, commit `1dc55ac`) — visual UI verification pending bundled test pass after steps 17, 19 land. Notable: HEIC narrowed to heic/heix only (HEVC video brands rejected); MIME sniffer is custom magic-byte detector (python-magic unreliable on Windows); `_staging_extensions` in-memory registry ages entries on the same TTL as on-disk janitor.

### Step 17: Room ingest bulk (vision + UI)

- **Problem:** Bulk ingest pipeline accepting up to 50 photos in one upload. Per-file validation per §"Upload validation rules" — failed files return individual `error` shape; valid files continue. Per-photo Claude vision via `ai.house_vision` (concurrency-bounded by `TOYBOX_VISION_CONCURRENCY`, default 4) returns `{suggested_room_label, features: [{name, ...}]}`. Tabbed review UI groups suggestions by suggested room; parent assigns photos to existing rooms or creates new ones inline (case-insensitive collision returns 409 with existing room id), confirms feature lists, submits. Backend inserts `rooms` + `room_features` rows; staging files moved to `data/images/rooms/<uuid>.<ext>`. SHA-256 dedup applied per photo (within batch and against existing rooms). Reuses `src/toybox/storage/images.py` from step 16 with `subdir="rooms"`. Bulk-cap of 50 enforced (51st → 413 `bulk_cap_exceeded`). Vision mocked in tests; live calls gated. See issue #31 for upload contract, edge cases, and pitfalls.
- **Type:** full-stack
- **Issue:** #31
- **Flags:** --reviewers full --ui (canonical) — running autonomously this session as `--reviewers code` per operating mode note above
- **Depends on:** Step 16 (shared `storage/images.py` helper lands first)
- **Status:** DONE (2026-05-03, commit `3bb2ef5`) — visual UI verification pending bundled test pass after step 19 lands. Notable: `rooms.image_path` ambiguity for multi-photo same-NEW-room resolved as "first-committed wins" (gallery siblings on disk, dedup-protected); existing-room assignments skip commit_staging entirely (no orphan files). `confirm-bulk` is atomic — any per-assignment failure (FK / OSError / IntegrityError) rolls back via the same Windows-AV `os.replace` retry primitive used in commit. Reused shared `storage/images.py` (no duplicated validate/dedup/stage/commit). FK violations on rooms + room_features inserts surface as 422 `invalid_room_id`; UNIQUE collisions on `room_features.name` silently dedup. Vision concurrency capped at `asyncio.Semaphore(TOYBOX_VISION_CONCURRENCY)` default 4; per-photo failure isolation via try/except in `_run_vision_for_photo`. DELETE keeps the on-disk file for toys-symmetry.

### Step 18: Child profile editor

- **Problem:** Full CRUD HTTP API (`/api/children`) + parent UI editor for the existing `children` table (no new schema; fields already in migration 0001). UI: list view shows all profiles sorted by `display_name COLLATE NOCASE`; "new" opens create form; click-to-edit opens same form; delete prompts for confirmation. Server-side validation: `display_name` required (1–40 chars); `birthdate` ISO date if present; `reading_level` validated against enum (`pre-reader`, `early-reader`, `fluent`); total field bytes capped under 4 KB. Delete returns 409 `child_in_use` with referring activity count when `activities.child_ids` JSON column references the child (LIKE match — known activities-schema limitation, do not fix in this step). `banned_themes` round-trips comma-separated text (chips UI is stretch-goal — textarea acceptable for v1). All endpoints require parent token (existing). The dict shape passed to `build_generator_context(child_profile=...)` is unchanged; this step only enables population. See issue #32 for API shape, fields, and pitfalls.
- **Type:** full-stack
- **Issue:** #32
- **Flags:** --reviewers full --ui (canonical) — running autonomously this session as `--reviewers code` per operating mode note above
- **Status:** DONE (2026-05-03, commit `aa584be`) — visual UI verification pending bundled test pass after steps 16, 17, 19 land

### Step 19: Activity generator uses real content

- **Problem:** Wire the catalog (real toys, rooms, child profiles from steps 16–18) into both the offline template fill AND the Claude prompt. New `src/toybox/activities/content_resolver.py` samples a deterministic subset (e.g. 12 toys prioritizing `last_used_at` recency) at generation time so prompt size stays bounded for households with hundreds of toys. `api/activities.py::_do_propose` calls the resolver, passes results into `build_generator_context`. Banned-themes filter applies at two layers: (1) offline template selection drops templates whose tags substring-match (case-insensitive) any banned theme of the active child(ren); (2) Claude system prompt receives a positive `Do NOT include any of: <list>` directive. Reading-level threads into prompt directives (`pre-reader` → simpler vocabulary + shorter sentences; verified by prompt-content tests, not by judging output sophistication). Multi-child activities take the most-restrictive intersection of banned-themes and the lowest reading-level. Anti-signal feedback (step 20) continues to apply against the new richer slot values; existing pre-step-19 feedback rows simply don't match new signatures (acceptable noise floor; documented). Empty-catalog gracefulness preserved (placeholder vocabulary still works). Every generation continues to write a `labeled_events` row (step 15 regression check). See issue #33 for resolver design, edge cases, and Phase E forward-compat.
- **Type:** code
- **Issue:** #33
- **Flags:** --reviewers code
- **Depends on:** Steps 16, 17, 18 (consumes their data); also re-validates step 20 still works
- **Status:** DONE (2026-05-03, commit `952be3f`) — kiosk REST critical path (`/api/activities/propose`) wired end-to-end. `EscalationDispatcher` extended with optional `connection_factory` injection so the trigger-driven offline path AND the Claude system-prompt directive both resolve real catalog when a factory is provided; smoke composition intentionally remains placeholder-only. Anti-signal signature now incorporates picked toy name (only when non-default), so pre-step-19 feedback rows simply don't match new richer signatures (acceptable noise floor). When the dispatcher graduates from smoke to a non-smoke listening loop, the composition root in `main.py` must pass a `connection_factory` to enable banned-themes + reading-level safety on the trigger path.

## Manual M3 — Real play session (after Phase C)

```powershell
# Start backend + frontend per "Run dev"
# Set listening mode to 3 via parent UI slider
# Run for 30 minutes during real play (adult-only for v1 / v1.5)
# File one issue per friction point
```

What to look for:

| Check | Where | Expected |
|-------|-------|----------|
| Suggestions trigger when expected | parent UI suggestion panel | within 10 sec of curated phrase |
| Suggestions don't trigger when not | parent UI | < 2 spurious per 30 min |
| Approved activities run cleanly | child UI | all 5 steps render, sfx fires |
| "Didn't work" feedback persists | DB `feedback` table | row inserted with reason |
| No mic dropouts | backend log | no `mic_queue_overflow` events |
| Claude calls fire on curated triggers only | backend log (`grep "claude call"`) | mode 3: zero spontaneous calls; one call per matched trigger |

> **Note:** M3 runs before Phase D step 24 ships the metrics dashboard, so all observability above is via DB queries + backend log grep. The dashboard makes this nicer in v1.5.
