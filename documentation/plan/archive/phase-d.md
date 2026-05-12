# Phase D — Polish

> **ARCHIVED 2026-05-11: phase shipped.** See [plan.md status](../../plan.md#status) for the authoritative completion record. Internal cross-refs in this doc are frozen as of archival.

> **Scope:** the 5 steps + Manuals M4 + M5 that closed v1 — anti-signal feedback, parent PIN gate, transcript management, live activity polish, metrics dashboard. **Status: COMPLETE (2026-05-03).** The bundled UAT release gate ([phase-d-uat-m2.5.md](phase-d-uat-m2.5.md)) covers visual verification for steps 16/17/18/21/22/23/24. Read this when extending one of these surfaces or arguing about PIN/auth invariants.

| # | Step | Reviewers | Done-when |
|---|------|-----------|-----------|
| 20 | Anti-signal feedback in generator | `--reviewers code` | Generator computes `signature = sha256("{template_id}:{sorted slot k=v}")` for every candidate; `feedback.signature` matches with `kind='didnt_work'` cause re-pick; `kind='loved_it'` boosts ranking; dismissed-pre-approval is soft anti-signal; tests cover the matching logic |
| 21 | Parent PIN gate (argon2id + rate-limit) | `--reviewers full --ui` | First-run flow sets PIN; argon2id with `m=65536,t=3,p=4`; `/api/auth/parent` validates PIN against stored hash; rate-limit: 5 wrong attempts in 5 min locks PIN entry for 15 min; failed attempts logged at WARNING with count only; PIN reset path documented in operator/recovery.md; gated routes 403 without token; settings/wipe/persona-edit screens require parent token |
| 22 | Transcript management UI | `--reviewers full --ui` | List + search + delete one + wipe all (PIN-gated); confirmation dialog on wipe |
| 23 | Live activity polish + suggestion "why this?" | `--reviewers full --ui` | Pause/resume idempotent; regenerate-from-here replaces remaining steps with version bump; end requires confirm dialog; suggestion card has expandable "why this?" panel showing trigger phrase + persona match reasoning |
| 24 | Metrics endpoint + ws topic + parent operator dashboard | `--reviewers full --ui` | `/api/metrics` returns counters + averages + breaker state + mic device + queue depth; `metrics` ws topic snapshots every 30 sec; in-memory counters survive ws reconnects; parent UI "Operator" tab renders all metrics with auto-refresh; eval-judge metrics surfaced (mean dimension scores over last 24h, judge-vs-parent agreement on overlap) |

**Issues:** Phase D umbrella #24 · step 20 → #25 · step 21 → #26 · step 22 → #27 · step 23 → #28 · step 24 → #29

### Step 20: Anti-signal feedback in generator

- **Problem:** Generator computes `signature = sha256("{template_id}:{sorted slot k=v}")` for every candidate template (lives in `src/toybox/activities/feedback.py`). Selection consults the `feedback` table by signature: `kind='didnt_work'` is a hard veto (re-pick from siblings; degrade to uniform pick only when every candidate is blocked); `kind='loved_it'` adds a positive weight; `kind='dismissed_pre_approval'` adds a smaller negative weight (soft anti-signal). Decay is by weight multiplier (not time window) — single source of truth, no clock dependency. The signature is emitted on `Activity.metadata["signature"]` and persisted in the activity's `summary` JSON. Parent UI feedback paths (`POST /dismiss` while proposed, `POST /thumbs-up`, `POST /didnt-work`) write `feedback` rows keyed by that signature so the loop closes end-to-end. Best-effort throughout: a sqlite blip during consultation degrades to a uniform pick and a logged WARNING; missing signatures on legacy activity rows skip the feedback write rather than 500. See issue #25.
- **Type:** code
- **Issue:** #25
- **Flags:** --reviewers code

### Step 21: Parent PIN gate (argon2id + rate-limit)

- **Problem:** Build a parent PIN gate using argon2id with documented hardness, rate-limited login endpoint, and PIN-gated route protection. First-run flow sets the PIN; subsequent boots require the PIN to issue a parent token. Hash params pinned at `m=65536, t=3, p=4`. Rate-limit: 5 wrong attempts in 5 min → lock PIN entry for 15 min. Failed attempts log at WARNING with attempt count only — never the attempted PIN value. PIN is digits-only, 4-12 chars (max configurable via `TOYBOX_PIN_MAX_LENGTH`). PIN reset is a manual operator step documented in `documentation/operator/recovery.md` (no web UI for reset). `core.bind_guard.pin_is_set(conn)` reads the `settings.parent_pin_hash` row so `TOYBOX_HOST=0.0.0.0` is unlocked once first-run setup completes. New endpoints: `POST /api/auth/parent` requires `{pin}` body (401 `pin_invalid` with attempts_remaining; 423 `pin_locked` with Retry-After + seconds_until_unlock; 412 `pin_not_set`); `POST /api/auth/parent/setup` for first-run (409 `pin_already_set`, 422 mismatched-confirm or non-digit); `GET /api/auth/parent/status` (no auth) returns `{pin_set, locked, seconds_until_unlock}`. Pre-PIN scaffolding (anonymous parent token) is removed — backwards-compat NONE. Frontend: `<PinSetup>` and `<PinLogin>` components plus an App.tsx bootstrap that probes status first then routes between them; locked-state UI shows a tick-down countdown that re-enables input on expiry.
- **Type:** code
- **Issue:** #26
- **Flags:** --reviewers full --ui
- **Status:** DONE (2026-05-03, commit `72f530f`) — backend gate + frontend setup/login/countdown all green; visual UI verification of the gate-first bootstrap pending bundled handoff. Notable: PIN regex is `[0-9]+` (NOT `\d+`) to reject Unicode digits (Arabic-Indic / full-width / Devanagari) that would otherwise lock the parent out. Rate-limit state is in-memory (resets on process restart, acceptable for v1 per spec); `pin_is_set` re-exported from `core.bind_guard` so `main.py`'s startup invariant reads as a one-liner. AbortController is constructed inside each useEffect (not as a shared ref) so React 18 StrictMode's double-mount cycle creates a fresh controller per mount — without that fix the second mount silently reused an aborted controller and stranded the UI. Lock takes precedence over PIN correctness even with the right PIN. Kiosk `issueParentToken` takes `{pin}`; production kiosks still need a pairing flow (out of v1 scope).

### Step 22: Transcript management UI

- **Problem:** Build the parent-facing transcript management surface: backend DELETE endpoints for one row + wipe-all (PIN-gated re-confirm) plus the React UI that lists, searches, deletes per-row, and surfaces a "wipe all" modal. `DELETE /api/transcripts/{id}` requires a parent token only and returns `{ok}` on 200 / 404 `transcript_not_found` on missing id. `DELETE /api/transcripts` requires the parent token AND a `{pin}` body re-validated against the stored hash, sharing the global PIN rate limiter with `POST /api/auth/parent` so a flurry of failed logins counts toward the wipe lock too (lock takes precedence over PIN correctness; 423 + Retry-After + `seconds_until_unlock`; 401 + `attempts_remaining`; 412 if no PIN configured). Wipe is a single `DELETE FROM transcripts` with no cascade — `transcripts` has no FKs pointing at it (only its own FK to `sessions`), so other tables (sessions, activities, labeled_events) survive untouched. Frontend: `<TranscriptsManager>` mounts on parent UI behind a "transcripts" toggle; debounced search (250ms via `setTimeout` in a `useEffect` — no library); cursor-paginated list with "Load more" using the row's `ended_at` as the cursor; per-row delete is optimistic with restore-on-error and "already deleted" notice on 404; "Wipe all" opens a modal with a numeric-only PIN field and surfaces 401 attempts-remaining + 423 lock countdown inline. AbortController constructed inside each `useEffect` (NOT a shared ref) per the Step 21 lesson.
- **Type:** code
- **Issue:** #27
- **Flags:** --reviewers full --ui
- **Status:** DONE (2026-05-03, commit `8c2ddde`) — backend DELETE endpoints + frontend manager + 22 backend tests + 13 frontend tests all green. Notable: extracted `enforce_pin_check(pin, conn, rate_limiter)` from `auth.py::post_parent` so the wipe-all endpoint shares the same lock-precedence + verify + counter logic; the helper does NOT call `record_successful_attempt` (login still does — wipe success isn't an auth event). Wipe-all uses `DELETE` with JSON body — unconventional but FastAPI accepts it; the alternative (PIN-in-header) is harder to type-check on the frontend. Delete-one is single-statement atomic (`DELETE` + `cursor.rowcount == 0` → 404, no SELECT-then-DELETE). Optimistic delete restores the row on non-404 errors; 404 keeps the row removed with a "already deleted" notice. Cursor pagination only enables "Load more" for the unfiltered list (search returns up to `limit` matches without a cursor in v1). PIN re-validation does NOT log the PIN value — covered by an explicit `caplog` audit on the wipe handler. Per-search `AbortController` cancels older in-flight requests so fast typing doesn't surface stale results. Wipe-all does NOT cascade — schema review + explicit "before/after row counts on sessions/activities/labeled_events" test pin the invariant.

### Step 23: Live activity polish + suggestion "why this?"

- **Problem:** Polish the live activity controls and fill in the suggestion-card "why this?" panel that was scaffolded in Phase A step 9. Backend: `POST /api/activities/{id}/pause` and `/resume` endpoints with idempotent semantics — pause-when-already-paused returns 200 with no version bump and no state envelope publish; the state-equality check fires BEFORE the version check so concurrent same-version double-taps return 200 instead of 409. Pause from terminal states (ended, completed, dismissed) and from proposed return 409 invalid_transition. New `STATE_PAUSED` constant added to the lifecycle; `_VALID_TRANSITIONS` includes paused→{running, ended, didnt_work, dismissed}. `ProposeRequest`/`RegenerateRequest` accept optional `trigger_phrase` and `persona_reasoning` (capped at 512 chars each); `_build_persona_reasoning` falls back through caller-supplied → `"<persona_display_name> picked for <intent>"` → `"matched on intent"`. Both fields persist in the activity's existing summary JSON envelope (alongside step 20's signature, no schema change) and surface on `ActivityResponse`. Regenerate inherits trigger_phrase + persona_reasoning from the source. Frontend: SuggestionCard "why this?" panel renders trigger phrase + persona reasoning + intent with kind null-safe fallbacks; ActivityPanel End button gates on `window.confirm` (matches ChildProfileEditor / TranscriptsManager pattern). `paused` added to the ActivityState union and PANEL_STATES so a paused activity stays visible.
- **Type:** code
- **Issue:** #28
- **Flags:** --reviewers full --ui
- **Status:** DONE (2026-05-03, commit `3b6287e`) — visual UI verification of why-panel + End-confirm pending bundled handoff. Notable: trigger_phrase is a literal substring of a child-spoken transcript = PII; the activity.state WS topic is shared with the child kiosk, so `_emit_state` strips `trigger_phrase` AND `persona_reasoning` from the WS payload before publishing. REST GET path remains parent-scoped and full-fidelity. Both fields capped at 512 chars on the request models. Regenerate semantics intentionally preserved as "skip & try another" (creates new activity_id, dismisses source) rather than the spec's "preserve activity_id mutate-in-place" — required because labeled_events has a UNIQUE index on activity_id and step 20 anti-signal feedback flows through the dismissed-source signature. The fresh activity inherits why-telemetry from the source so cycles stay coherent.

### Step 24: Metrics endpoint + ws topic + parent operator dashboard

- **Problem:** Add `/api/metrics` (parent-token GET) and a `metrics` ws topic (30 s snapshot publisher) so an operator can see system + activity-quality + breaker + audio-pipeline state at a glance. Snapshot shape includes activities counts (totals + last-24h breakdown), transcripts counts, audio pipeline (mic device, queue depth, buffer-overruns over the last 24h), AI status (breaker state + retry-after, Claude capability check + reason, listening mode, min-interval throttle), activity-quality (per-dimension judge means over the last 24h, judge-vs-parent agreement on the overlap, safety auto-fail count), and eval-gate status (last run timestamp, mean baseline scores, regressions count, placeholder flag). Parent UI gains an "Operator" toggle that renders the snapshot; the tab subscribes to the `metrics` ws topic for push updates and falls back to a 30 s REST poll when ws is unavailable. Counter persistence: load-bearing counters come from DB COUNT(*) so they survive process restart; only the buffer-overrun counter is process-local (acceptable noise floor). Judge-parent agreement metric is the simpler `sign_agreement_rate` (sign of `parent_signal` vs sign of `mean(judge_scores) - 3.0`); Cohen's kappa was deferred as out of scope for v1.
- **Type:** code
- **Issue:** #29
- **Flags:** --reviewers full --ui
- **Status:** DONE (2026-05-03, commit `d1bee35`) — visual UI verification of Operator tab pending bundled handoff. Notable: 24h-window queries use `datetime(col) >= datetime('now', '-1 day')` to avoid the production T-Z vs SQLite space-separator lexicographic-compare bug (centralized via `_LAST_24H_PREDICATE`). Activity counts surface CURRENT-state per-state (`*_current`), not cumulative — running/completed/didnt_work all visible. Buffer-overrun counter is process-lifetime (renamed from misleading `_last_24h`). WS publisher awaits `resolve_capability` per tick so REST and ws agree on `claude_capable`.

## Manual M4 — Sound effect sourcing (any time before Phase A step 10 final review)

| Asset | Purpose | Source |
|-------|---------|--------|
| `transition.wav` | step → next step | royalty-free (e.g. freesound.org CC0) |
| `success.wav` | "this worked" | royalty-free |
| `persona_enter.wav` | persona appears | royalty-free |
| `persona_leave.wav` | activity ends | royalty-free |
| `tada.wav` | optional flourish | royalty-free |

**Format spec:** 16-bit PCM WAV, mono, 22.05 kHz, peak normalized to -3 dBFS, ≤2 seconds, no leading silence > 50 ms. Drop in `frontend/public/sfx/`. Silence works as a Phase A placeholder. Track licenses in `frontend/public/sfx/_credits.md`.

## Manual M5 — Operator recovery procedures (referenced from `documentation/operator/recovery.md`)

What to look for table is "if X happens, run Y"; the operator doc holds the full procedures. Stub recipes:

| Symptom | Recovery |
|---------|----------|
| DB corrupt or wedged | Stop backend; `mv data/toybox.db data/toybox.db.broken-$(date +%s)`; restart (re-applies migrations into a fresh DB). **v1 has no backups** — toy/room/persona/transcript data is lost. v1.5 will add nightly snapshots; until then, ad-hoc manual copies of `data/toybox.db` (backend stopped) are the only fallback. |
| Migration failed at startup | Stop backend; copy `data/toybox.db` aside (`cp data/toybox.db data/toybox.db.pre-failed-migration`); inspect logged migration filename + traceback; either fix the migration SQL and restart, or factory-reset per below |
| Whisper model load fails | `rm -rf data/models/`; restart (re-downloads on first transcription) |
| Claude OAuth wedged | `rm ~/.toybox/secrets.json`; re-run `claude-oauth-auth` flow; restart |
| Forgot parent PIN | Stop backend; `sqlite3 data/toybox.db "DELETE FROM settings WHERE key='parent_pin_hash'"`; restart; first-run PIN prompt re-appears (this is the documented reset path) |
| Mic dropouts / wrong device | Set `TOYBOX_MIC_DEVICE_INDEX=N` per `python -m sounddevice` device list output; restart |
| Image storage runaway | Stop backend; archive unwanted toys via parent UI; periodic cron at v1.5 will delete orphan files (manual: `python -m toybox.tools.gc_images`) |
| "Factory reset" | Stop backend; remove `data/`; restart (re-runs migrations + first-run setup; all photos, transcripts, profiles, custom personas lost) |
