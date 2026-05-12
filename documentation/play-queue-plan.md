# Play Queue — feature plan

## 1. What this feature does

Convert the parent-app **Play** surface from "one suggestion at a time" into a
live scrolling queue of play suggestions, fed both by an autonomous backend
cadence loop AND by transcripts arriving from the listening pipeline. The
target queue depth (1 / 3 / 5) and cadence (10s / 30s / 1m) are user-settable
in Settings. The approved activity pins at the top of the same list as a full
ActivityPanel surface; new suggestions continue to scroll in below it. Parents
can switch to a different suggestion while one is active (with a confirmation
prompt) — the old active ends neutrally and the new one becomes active.

**Why now.** Production today calls `propose()` only from the manual "Trigger"
button. The audio pipeline already produces transcripts, the intent registry
already classifies them, and the EscalationDispatcher already exists — but
`on_intent=None` in production's lifespan means none of that flows into the
proposal stream. V1 closes that loop and gives the parent multiple options to
pick from so the proposal stream feels responsive instead of single-shot.

## 2. Existing context

Background a fresh-context model needs to understand the impact:

- **Single-slot Play UX today.** `frontend/src/parent/store.ts` carries one
  `activity: Activity | null`. `frontend/src/parent/App.tsx` gates a
  `SuggestionCard` (state `proposed`) or an `ActivityPanel` (state
  `approved/running/paused/completed`) on it. A manual `TriggerButton` calls
  `api.propose({intent:"request_play", slot:"freeplay", hour, seed})`.
- **Backend already has a queue cap.** `src/toybox/core/queue.py` defines
  `PROPOSED_QUEUE_CAP = 5` and `evict_oldest_for_capacity()` drop-oldest
  dismisses excess `proposed` rows. The cap is currently hardcoded and the
  parent UI never renders the extras.
- **Transcript → proposal wiring is dormant.** `src/toybox/audio/pipeline.py`
  exposes an `on_intent` hook. `src/toybox/main.py`'s smoke lifespan wires
  the `EscalationDispatcher` (`src/toybox/core/escalation.py`) to it; the
  production `_metrics_lifespan` passes `on_intent=None` (see project memory
  `project_audio_runtime_status.md`). Wiring it in production has been a
  known gap since 2026-05-08.
- **Settings precedent — Phase I (`transcript_retention`).** Household-scoped
  scalar setting model: backend module `src/toybox/core/transcript_retention.py`
  + endpoint `src/toybox/api/transcript_retention_settings.py` + migration
  seed + `SettingsPanel` segmented control + seeded by App.tsx on bootstrap
  and threaded down as a prop. **Copy this pattern verbatim** for the two
  new keys.
- **Scrolling list precedent — `TranscriptsManager.tsx`.** Cursor-paginated
  list, 1s `setInterval` tick, fade-out animation via `fadingIds` set +
  600ms CSS transition (TTL = Time To Live; rows fade after expiry),
  `removalTimeoutsRef` cleanup on unmount, ws live prepend via
  `subscribeToTranscripts` fanout. `PlayQueueList` reuses the **tick + fade
  + cleanup machinery** but **does NOT add a ws fanout** — it reads from
  the store, which already routes `activity.state` envelopes through
  `applyEnvelope` (see §4 / §5).
- **WS topics.** `activity.state` envelopes already broadcast every state
  transition. The store's `applyEnvelope` currently treats them as
  "update single active activity." This expands to "route by id and state
  into either the proposed list or the active slot."

### Existing primitives (one-line glosses)

Recurring symbols from the codebase the rest of this plan references:

| Symbol | Where | What it does |
|---|---|---|
| `Activity` | `src/toybox/api/activities.py:238` (Pydantic) + `frontend/src/parent/api.ts` (TS) | Wire shape for an activity. Fields: `id` (UUID), `state` (proposed/approved/running/paused/completed/dismissed/didnt_work/ended), `version` (int, ≥1, ++ each mutation), `title`, `summary`, `persona_id`, `intent_source`, `child_ids[]`, `toy_ids[]`, `created_at` (UTC ISO seconds, `...Z`), `started_at`, `ended_at`, `steps[]` (each `{seq, body, sfx?, expected_action?, current?, action_slot?}`), `metadata{}`, `trigger_phrase?`, `persona_reasoning?`. |
| `Intent` | `src/toybox/triggers/registry.py` | NLP-trigger match emitted by the pipeline. Fields: `name` (e.g., `"request_play"`), `slot` (optional string like `"freeplay"`). |
| `ProposeRequest` | `src/toybox/api/activities.py:268` | Body for `POST /api/activities/propose`. Fields: `intent` (str), `slot` (str?), `hour` (int 0-23), `seed` (int ≥0), `persona_id?`, `session_id?`, `context?`, `trigger_phrase?`, `persona_reasoning?`. |
| `activity.state` ws envelope | `src/toybox/ws/topics.py` + `_emit_state` | Payload mirrors `Activity` wire shape (id, state, version, title, persona_id, intent_source, child_ids, created_at, steps[], metadata). |
| `ListeningMode` | `src/toybox/core/listening.py:32` | IntEnum 1-5: **1 OFFLINE** = trigger-only, never Claude; **2 LOW** = trigger-only, never Claude; **3 DEFAULT** = trigger-driven Claude with offline fallback (the standard mode); **4 HIGH** = mode 3 + spontaneous timer (currently env-driven); **5 INTENSE** = every above-floor transcript escalates, synthesizes "boredom" intent if no trigger matched. |
| `current_mode(conn)` | `src/toybox/core/listening.py:70` | Reads `settings.listening_mode` (TEXT, parses to int) and returns the `ListeningMode` enum. Cadence + on_intent paths read this fresh per dispatch. |
| `is_capable(breaker)` | `src/toybox/ai/capability.py` | Async returns `(bool, CapabilityReason | None)`: true only when token present + breaker closed + network reachable + listening mode permits Claude. |
| `_do_propose(body, conn, pubsub, judge_call)` | `src/toybox/api/activities.py:1117` | Shared propose-and-persist helper. Resolves catalog, dispatches generator (offline/Claude/local), persists `activities` + `activity_steps` rows, emits `activity.state` envelope, schedules judge sample. Returns `ActivityResponse`. **Production cadence + on_intent both call this directly** — no parallel persist helper. |
| `_persist_smoke_activity` | `src/toybox/main.py:304` | Smoke-only parallel persist helper. Exists because smoke's deterministic UUIDs collide on re-run; production has no collision risk and does NOT use this. |
| `EscalationDispatcher.on_transcript(transcript, mode, intents)` | `src/toybox/core/escalation.py:419` | Per-mode dispatch. Returns an **in-memory `Activity`** (or `None`). **Does NOT persist** — the caller persists. Smoke calls `_persist_smoke_activity` after; production calls `_do_propose` after. |
| `evict_oldest_for_capacity(conn, *, cap)` | `src/toybox/core/queue.py:47` | Drop-oldest dismisses excess `proposed` rows until count < `cap`. Returns dismissed ids so callers can emit envelopes. |
| `record_generation(conn_factory, ...)` | `src/toybox/ai/labeled_events.py` | Writes a `labeled_events` row when a new activity is generated. Feeds the learning loop + judge sampler. |
| `judge_and_persist(ai_client, db_path_resolver, ...)` | `src/toybox/ai/judge.py` | Awaitable factory the dispatcher invokes for in-sample rows; runs Claude as a quality judge and persists scores to `labeled_events.judge_scores_json`. |
| `default_conn_factory()` | `src/toybox/db/__init__.py` | Returns a `Callable[[], sqlite3.Connection]` bound to the resolved DB path. |
| `withConflictHandler<T>({mutation, refetch, onConflict})` | `frontend/src/parent/api.ts:1689` | Wraps a mutating API call. On `VersionConflictError` (409), calls `refetch()`, fires `onConflict(conflict, fresh)`, returns `null`. Other errors propagate. Used for every `If-Match-Version`-gated mutation. |
| `applyMutationResult(fresh)` | `frontend/src/parent/store.ts` | Adopts a mutation's fresh `Activity` into the store UNLESS a newer-version envelope already arrived for the same id (version-guarded). Prevents an in-flight mutation regressing state when ws delivers a newer version mid-roundtrip. |
| `withConflictHandler` + `applyMutationResult` together | both files | Standard mutation pattern: call `withConflictHandler({mutation: () => api.X(id, version), refetch, onConflict: store.applyVersionConflict})`, then on non-null result call `store.applyMutationResult(result)`. |

## 3. Scope

**In scope (V1):**

- Two new household settings:
  - `play_target_depth` ∈ {1, 3, 5}, default 3.
  - `play_cadence_seconds` ∈ {0, 10, 30, 60}, default 30. `0` = cadence
    disabled (no autonomous proposals; transcripts + manual Trigger still
    fire). Stored in their own per-setting modules per project convention
    (see §6 D8).
- Backend cadence task in `main.py` lifespan that ticks every
  `play_cadence_seconds` and fires a default-seed proposal if
  `proposed_count() < play_target_depth`. Skips entirely under
  `ListeningMode.OFFLINE` (1) and `ListeningMode.LOW` (2) to respect
  the privacy-slider semantics — under those modes, the dispatcher itself
  refuses Claude and the cadence loop respects the same boundary.
- Wire the production `on_intent` to the `EscalationDispatcher` so transcripts
  produce proposals (subject to the same cap), reading `current_mode(conn)`
  fresh per dispatch.
- Deprecate `TOYBOX_SPONTANEOUS_INTERVAL_SEC` env var — the new
  `play_cadence_seconds` setting is the single source of truth for cadence
  timing. The env var's usage in
  `core/escalation.py:spontaneous_interval_from_env` becomes dead code; J3
  wires production around the env var (still in place), J4 deletes it.
- TTL on proposed rows: `expires_at = created_at + 3 × play_cadence_seconds`.
  When `play_cadence_seconds == 0`, suggestions have **no TTL** (only
  evicted by cap or manual dismiss). Backend 10s-tick sweep dismisses
  expired proposed rows + emits `activity.state`.
- `GET /api/activities/proposed` list endpoint, optionally with
  `?include_active=true` so the frontend's bootstrap can hydrate
  `proposedList` + `active` in one round trip.
- Parent store + App refactor to carry `proposedList: Activity[]` + `active:
  Activity | null`.
- `PlayQueueList` component: scrolling list, fade-out machinery copied from
  TranscriptsManager, pinned active row at top rendering the full
  `ActivityPanel` inline, suggestion rows below rendering `SuggestionCard`.
- Switch-confirm flow: approving a suggestion while one is active triggers
  `window.confirm`, then **ends old active** → **approves new** in that order
  (two client-side mutations, both with `withConflictHandler`). On end
  failure the approve does not fire; on approve failure the parent has no
  active activity but the queue still shows the candidate they tried.
- SettingsPanel: new segmented controls — target depth (1/3/5) and cadence
  (off/10s/30s/1m).
- `TriggerButton` is **kept but de-emphasized** — rendered as a small
  "+ trigger now" link below the queue list, not the prominent top-of-tab
  button it is today.
- Operator smoke + iPad UAT.

**Out of scope (V1 — recorded as V2 ideas in §10):**

- Diversity / anti-back-to-back-repeat ranking.
- Multi-utterance transcript context (V1 treats each `Intent` independently).
- Per-suggestion TTL setting (V1 derives TTL from cadence).
- Server-side atomic "switch" endpoint (V1 does two client-side mutations;
  acceptable because the conflict-handler refetch covers the race window).
- Per-child or per-mode cadence (V1 is household-global, matching
  `transcript_retention` and `banned_themes_global`).
- WS-driven settings hot-reload across multiple parent tabs (V1 re-reads
  settings every tick on the backend; a setting change made on desktop
  takes effect on the **next backend tick** — worst case 60s for a 1m
  cadence. The iPad UAT in J12 explicitly exercises this two-tab case).
- Reusing `EscalationDispatcher.maybe_fire_spontaneous` — V1 builds a
  parallel cadence path. The dispatcher's spontaneous method stays
  but is unused after this phase; deprecation tracked as a V2 cleanup.

## 4. Impact analysis

| File / module | Nature of change |
|---|---|
| `src/toybox/db/migrations/0011_play_target_depth.sql` | **NEW** — seed `play_target_depth = '3'` in `settings` |
| `src/toybox/db/migrations/0012_play_cadence_seconds.sql` | **NEW** — seed `play_cadence_seconds = '30'` in `settings` |
| `src/toybox/core/play_target_depth.py` | **NEW** — read/write helpers + canonical set + default. Mirror `core/transcript_retention.py` structure. |
| `src/toybox/core/play_cadence_seconds.py` | **NEW** — read/write helpers + canonical set ({0, 10, 30, 60}) + default. Mirror `core/transcript_retention.py` structure. |
| `src/toybox/api/play_target_depth_settings.py` | **NEW** — `GET / PUT /api/settings/play-target-depth`. Mirrors `api/transcript_retention_settings.py`. |
| `src/toybox/api/play_cadence_seconds_settings.py` | **NEW** — `GET / PUT /api/settings/play-cadence-seconds`. Mirrors `api/transcript_retention_settings.py`. |
| `src/toybox/core/queue.py` | **EXTEND** — `evict_oldest_for_capacity(conn, *, cap)` already takes `cap`. Callers must pass `play_target_depth` instead of `PROPOSED_QUEUE_CAP`. Keep `PROPOSED_QUEUE_CAP=5` as an absolute hard cap (settings validation rejects values > 5). |
| `src/toybox/api/activities.py` | **EXTEND** — call sites in `_do_propose` (~line 1117) read `play_target_depth` and pass to `evict_oldest_for_capacity`. **ADD** `GET /api/activities/proposed[?include_active=true]` list endpoint that returns proposed rows ordered by `created_at DESC`, optionally bundling the current active activity (most recent `approved/running/paused/completed` row for the session). |
| `src/toybox/main.py:_persist_smoke_activity` (~line 304) | **LEAVE AT HARD CAP** (resolved in J2). The second call site for `evict_oldest_for_capacity` at line 329 stays `cap=PROPOSED_QUEUE_CAP`. Add a one-line code comment explaining smoke is a hermetic fixture and the dynamic cap adds no value. |
| `src/toybox/core/play_cadence.py` | **NEW** — asyncio cadence task. Reads `play_cadence_seconds` + `play_target_depth` + `current_mode(conn)` each tick. Skips entirely when `cadence_seconds == 0` OR `mode ∈ {OFFLINE, LOW}`. When firing, calls `_do_propose(body, conn, pubsub, judge_call)` directly with a default-seed `ProposeRequest`. |
| `src/toybox/core/proposed_ttl.py` | **NEW** — TTL sweep task (mirrors `core/transcript_retention.py`'s sweep). Every 10s: read live `play_cadence_seconds`; if 0, skip (no TTL when cadence off); else dismiss proposed rows where `created_at + 3 × cadence < now`, emit `activity.state` envelopes per dismissed row. |
| `src/toybox/main.py:_metrics_lifespan` (~line 796) | **MODIFY** — (a) `_start_production_audio` (~line 749) must construct `TranscriptPipeline` with `on_intent=<dispatcher.on_transcript bound to current_mode>`. (b) Build a production `EscalationDispatcher` with the full injection set (see §6 D9 for the explicit checklist). (c) Start the new cadence + TTL tasks alongside existing metrics / image-gen workers. (d) Smoke wiring at `main.py:433` (`_on_intent`) + `main.py:535` (`on_intent=_on_intent`) is the reference shape. |
| `src/toybox/core/escalation.py:spontaneous_interval_from_env` | **DELETE** — `TOYBOX_SPONTANEOUS_INTERVAL_SEC` is deprecated. Remove the function + the call sites in the dispatcher constructor. `maybe_fire_spontaneous` itself stays but is unused; deprecation tracked as a V2 cleanup. |
| `src/toybox/audio/pipeline.py` | No change. Already exposes `on_intent`. |
| `src/toybox/ws/envelope.py` / `api/activities.py:_emit_state` | **VERIFY** — the `activity.state` envelope payload must include `created_at` so the frontend can compute TTL on a freshly-pushed row without a REST refetch. Spot-check + add if missing in J5. |
| `frontend/src/parent/api.ts` | **EXTEND** — add `listProposedActivities({include_active?})`, `getPlayTargetDepth()`, `setPlayTargetDepth(value)`, `getPlayCadenceSeconds()`, `setPlayCadenceSeconds(value)`. Types `PlayTargetDepth = 1 \| 3 \| 5`, `PlayCadenceSeconds = 0 \| 10 \| 30 \| 60`. |
| `frontend/src/parent/store.ts` | **REFACTOR** — replace `activity: Activity \| null` with `proposedList: Activity[]` + `active: Activity \| null`. Rewrite `applyEnvelope` for `activity.state` to route by **state and id together**: state `proposed` → upsert into `proposedList`; state ∈ approved/running/paused/completed → set `active` (newer-version-of-same-id wins), and **remove from proposedList if id is present**; state ∈ dismissed/didnt_work/ended → remove from `proposedList` AND clear `active` if id matches. New reducers: `applyProposedExpired(id)`, `applySwitch(oldEndResult, newApproveResult)`. |
| `frontend/src/parent/App.tsx` | **MODIFY** — bootstrap calls `listProposedActivities({include_active: true})` + `getPlayTargetDepth()` + `getPlayCadenceSeconds()` in parallel with existing seeding. Thread `cadenceSeconds` down to `PlayQueueList` for TTL math + to `SettingsPanel` for the control state. Replace `showSuggestion / showPanel` block with `<PlayQueueList ... />`. Approve handler branches: no active → simple approve; active → confirm + end-old → approve-new. Move `<TriggerButton>` to a small de-emphasized affordance below the list. **No new ws fanout** — `PlayQueueList` reads from the store (envelope handling already lives in `store.applyEnvelope`); avoids the double-update trap of having a parallel `subscribeToActivityState`. |
| `frontend/src/parent/components/PlayQueueList.tsx` | **NEW** — scrolling list container. Reads `proposedList` + `active` + `cadenceSeconds` from props (App.tsx pulls from store + state). Renders pinned active row as `<ActivityPanel>` at top when `active !== null`, then proposed rows as `<SuggestionCard>` below. TTL fade machinery copied from `TranscriptsManager.tsx` (1s tick, `fadingIds` set, `removalTimeoutsRef` cleanup). When `cadenceSeconds === 0`, fade machinery is disabled (no TTL). |
| `frontend/src/parent/components/SuggestionCard.tsx` | **MINOR EDIT** — relabel "skip" button to "try a different one" (functionality unchanged: calls `api.regenerate()` for that row, replacing this slot's suggestion with a fresh one). Keep approve + dismiss + "why this?" |
| `frontend/src/parent/components/ActivityPanel.tsx` | No change. Renders as-is inside the new list. |
| `frontend/src/parent/components/SettingsPanel.tsx` | **EXTEND** — add two segmented controls: target depth (1/3/5) and cadence (off/10s/30s/1m). Each persisted via its own endpoint. Snap-to-nearest defensive `aria-pressed` for non-canonical values (copy from `TranscriptRetentionControl`). |
| `frontend/src/parent/components/TriggerButton.tsx` | **KEEP** with restyled de-emphasis (smaller link-style affordance "+ trigger now" rendered below the queue list, not the top-of-tab button). |
| `frontend/src/parent/App.test.tsx`, `*.retention.test.tsx` | **UPDATE** — store shape change breaks any test that asserts on `state.activity`. Grep finds matches in `App.tsx`, `ActivityPanel.tsx`, `store.ts`, `SuggestionCard.tsx`. |
| `frontend/src/parent/store.test.ts` | **UPDATE** — new reducers + envelope routing across all six terminal-state transitions. |
| `frontend/src/parent/components/PlayQueueList.test.tsx` | **NEW** — equivalent coverage to `TranscriptsManager.test.tsx` (pytest-asyncio + monkey-patched timers for the Python side; vitest fake timers for the React side). |
| `documentation/plan.md` | **MODIFY** — add Phase J row to status table (see §11 for the draft). |
| `documentation/plan/activity-loop.md` | **MODIFY** — append a "Phase J — autonomous play queue" section linking back to this plan. |
| `README.md` | **MODIFY** — update Phase history + "in flight" block. |

## 5. New components

- **`core/play_target_depth.py`** — `get(conn) -> int` returns the current
  target depth; `set(conn, value)` validates against `{1, 3, 5}` and persists.
  Default 3. Fallback on invalid/missing row: default. Mirrors
  `core/transcript_retention.py` shape exactly.
- **`core/play_cadence_seconds.py`** — `get(conn) -> int`; `set(conn, value)`
  validates against `{0, 10, 30, 60}`. Default 30. `0` means "cadence
  disabled." Mirrors `core/transcript_retention.py` shape.
- **`api/play_target_depth_settings.py`** — `GET / PUT
  /api/settings/play-target-depth`. Parent-token scope. Validation surfaces
  422 on out-of-set values. Mirrors `api/transcript_retention_settings.py`.
  - `GET` response: `{"value": 1 | 3 | 5}`
  - `PUT` body: `{"value": 1 | 3 | 5}` → response: `{"value": <persisted>}`
- **`api/play_cadence_seconds_settings.py`** — `GET / PUT
  /api/settings/play-cadence-seconds`. Same pattern.
  - `GET` response: `{"value": 0 | 10 | 30 | 60}`
  - `PUT` body: `{"value": 0 | 10 | 30 | 60}` → response: `{"value": <persisted>}`
- **`GET /api/activities/proposed[?include_active=true]`**
  (in `api/activities.py`) — parent-token scope.
  - Without `include_active`: response `{"items": Activity[]}`,
    ordered `created_at DESC`, max 5.
  - With `include_active=true`: response
    `{"items": Activity[], "active": Activity | null}`. `active` is the
    most recent non-terminal `approved/running/paused/completed` row for
    the production session (or `null` if none).
- **`core/play_cadence.py`** — `start_cadence_loop(get_pubsub, db_path) ->
  asyncio.Task`. Loop body (in pseudo):
  ```
  while True:
      cadence = play_cadence_seconds.get(conn)         # 0 / 10 / 30 / 60
      target  = play_target_depth.get(conn)            # 1 / 3 / 5
      mode    = current_mode(conn)                     # 1..5
      sleep_s = max(5, cadence) if cadence > 0 else 30 # never spin
      await asyncio.sleep(sleep_s)
      if cadence == 0: continue
      if mode in (OFFLINE, LOW): continue
      if proposed_count(conn) >= target: continue
      body = ProposeRequest(
          intent="request_play", slot="freeplay",
          hour=now.hour, seed=random_int(),
      )
      await asyncio.to_thread(_do_propose, body, conn, pubsub, judge_call)
  ```
  Settings + mode re-read every tick — no restart needed for a settings
  change. `_do_propose` emits the same `activity.state` envelope the
  manual Trigger does, so the frontend doesn't need to know cadence
  proposals are special.
- **`core/proposed_ttl.py`** — `start_proposed_ttl_sweep(get_pubsub, db_path)
  -> asyncio.Task`. 10s tick. Reads live `play_cadence_seconds`; if `0`,
  skip (no TTL when cadence disabled). Else:
  `UPDATE activities SET state='dismissed', version = version + 1
   WHERE state='proposed' AND created_at < ?` where the bind is `now -
  3 × cadence` formatted as UTC ISO seconds (`...Z`, mirroring the
  timestamp pinning in `core/transcript_retention.py`'s `ENDED_AT_ISO_FORMAT_NOTE`).
  Emit `activity.state` for each dismissed id via the pubsub published
  envelope. The envelope payload includes `created_at` so the frontend
  can confirm TTL alignment (see S9 in §4).
- **`components/PlayQueueList.tsx`** — list container. Props: `api`,
  `active: Activity | null`, `proposedList: Activity[]`,
  `cadenceSeconds: number` (for TTL fade math; `0` disables fade),
  `onApprove(id) / onDismiss(id) / onRegenerate(id)` for proposed rows,
  the full ActivityPanel handler bundle for the active row,
  per-action busy flags keyed by (action, id) to allow multiple
  in-flight requests for different rows. Renders pinned active row at
  top + proposed rows below. Fade-out machinery is a direct port of
  `TranscriptsManager.tsx`'s `fadingIds` + `removalTimeoutsRef` pattern
  with `expires_at = created_at + 3 × cadenceSeconds` when
  `cadenceSeconds > 0`. **Does NOT subscribe to ws directly** — reads
  state from the store; `App.tsx` already routes envelopes through
  `store.applyEnvelope` which the refactored reducer handles
  (see §4 store row).

## 6. Design decisions

**D1 — Each setting is its own module + endpoint.**
Per-setting-module convention (carried forward from Phase H, recorded in
project memory) means `play_target_depth` and `play_cadence_seconds` each
get their own `core/<key>.py` + `api/<key>_settings.py`. Two endpoints not
one. The frontend issues two GETs at bootstrap (parallel) and two PUTs
on independent setting changes. Marginally more boilerplate vs. a bundled
"play_queue" endpoint, but matches `transcript_retention` exactly and
keeps the per-setting modules diff-isolatable for future review.

**D2 — TTL is derived, not its own setting.**
TTL = 3 × cadence_seconds. So 30s / 90s / 3min for cadences 10s / 30s / 1m.
Considered a fourth setting; rejected because it adds choice without
clear user value — a suggestion that's been sitting three full cadence
cycles unread is stale by definition, and the derivation keeps the
Settings panel small. **Recorded as a V2 follow-up** if real-world use
exposes the need.

**D3 — Switch flow is two client-side mutations, not one server-side
endpoint.** Approving a suggestion while another is active does:
`end(old.id, old.version)` then `approve(new.id, new.version)`. Both use
`withConflictHandler`. Considered adding `POST /api/activities/{id}/approve?switch=true`
that does both atomically server-side; rejected because the race window
is one client roundtrip (the parent isn't going to fire 17 mutations
in 50ms) and the conflict-handler refetch already covers the case where
a ws envelope races the mutation. Keeps the API smaller.

**D4 — Cadence task in lifespan, not per-request.**
Single asyncio task in `main.py` lifespan, mirroring the metrics publisher
and image-gen worker. Two open parent tabs do NOT each spin up their own
ticker — there is one cadence loop per process. Settings are re-read every
tick so a change from SettingsPanel takes effect on the next tick (≤ 1m
worst case).

**D5 — TTL is server-side, not client-side.**
The TTL sweep runs on the backend (10s tick) and emits `activity.state`
envelopes when a row expires. Frontend just renders the fade-out animation
on receipt. This is intentionally different from how `TranscriptsManager`
handles transcript retention: there, the frontend tick computes expiry
locally because every parent UI computes the same answer. For
**proposed activities**, we MUST evict server-side because the
EscalationDispatcher and the cadence loop both check
`proposed_count < target_depth` before generating — without server-side
TTL, an "expired" suggestion would still occupy a slot from the
cadence-loop's POV.

**D6 — Pinned active rendered as the existing `ActivityPanel` unchanged.**
The PlayQueueList just nests `<ActivityPanel>` at the top of its list.
No refactor of ActivityPanel itself. Suggestion rows nest `<SuggestionCard>`
(with the `skip` button removed; see §4).

**D7 — Switch ends old active as `ended`, not `didnt_work`.**
Confirmed in §2 questions: parents switch for many reasons (kid's attention
shifted, doorbell, switching to a more appropriate template they spotted)
that aren't "this didn't work." Tagging the switch as `didnt_work` would
pollute the `labeled_events` learning signal.

**D8 — Cadence `0` = disabled; no separate enable flag.**
`play_cadence_seconds: 0` means the cadence loop never fires. Transcripts
+ manual Trigger still produce proposals; only the autonomous timer is
off. Considered a separate `play_cadence_enabled: bool` setting; rejected
because two correlated settings ("enabled but at speed 0?") invite contradiction.
A four-option segmented control "off / 10s / 30s / 1m" is unambiguous.

**D9 — Production EscalationDispatcher injection set.**
J3 builds the dispatcher with:
- `ai_client = AnthropicClient(...)` — real client, not `StubClient`.
- `breaker` — process-singleton `CircuitBreaker` (existing).
- `throttle` — `MinIntervalThrottle` configured from
  `TOYBOX_CLAUDE_MIN_INTERVAL_SEC` (existing).
- `capability_check = functools.partial(is_capable, breaker=breaker)` —
  real check, not the smoke-only `_never_capable` shim.
- `publisher = lambda env: get_pubsub().publish(env)` — same as smoke.
- `labeled_event_recorder = functools.partial(record_generation,
  conn_factory=default_conn_factory())` — required for the learning loop.
- `judge_call_factory = functools.partial(judge_and_persist,
  ai_client=ai_client, db_path_resolver=resolve_db_path)` — required for
  judge sampling.
- `connection_factory = default_conn_factory()` — required so the
  dispatcher resolves real catalog content for both offline + Claude paths.

The `on_intent` callback wraps `dispatcher.on_transcript(transcript, mode,
intents)` where `mode = current_mode(open_conn())` is read fresh per
intent. Smoke hardcodes `ListeningMode.DEFAULT` — production must NOT
hardcode.

**D10 — Switch flow ordering: end old then approve new.**
The switch sequence is `api.end(old.id, old.version)` first, then
`api.approve(new.id, new.version)`. On end failure (version conflict,
network), the approve does not fire — surfacing the conflict to the
parent via the existing toast. On approve failure, the parent has no
active activity but the queue still shows the candidate; they can retry.
Considered the inverse (approve-first); rejected because the brief window
where two activities are technically "active" server-side is harder
for the frontend store to reason about than "no active right now."

**D11 — Cadence task calls `_do_propose` directly; production `_on_intent`
extracts `_persist_dispatcher_activity` (sibling-of-smoke).**
Two seams in this phase persist into `activities`, and they take different
paths:

- **Cadence loop (`core/play_cadence.py`)** opens a SQLite connection per
  tick (via `asyncio.to_thread`) and calls `_do_propose(body, conn, pubsub,
  judge_call)` directly. The loop has no in-memory Activity to honour — it
  is synthesizing a `ProposeRequest` from scratch — so reusing `_do_propose`
  is the natural fit (eviction, content resolution, judge scheduling,
  envelope emission all run unmodified).
- **Transcript-driven `_on_intent` (`main.py`)** ALREADY HOLDS a chosen
  `Activity` returned from `EscalationDispatcher.on_transcript`. Re-running
  `_do_propose` would (a) regenerate from a different random seed and
  discard the dispatcher's chosen template/persona, (b) under mode ≥ 3
  with capability, fire Claude TWICE per intent (dispatcher + propose),
  and (c) leave the dispatcher's `labeled_events` row keyed on an
  `activity.id` that never lands in `activities` — orphan rows that
  pollute Phase E SFT exports.

  J3 therefore extracts `_persist_dispatcher_activity(activity, intent,
  conn, pubsub)` — a sibling of `_persist_smoke_activity`. The shape
  mirrors smoke (INSERT one `activities` row + step rows, emit
  `activity.state` envelope) but with three differences:
  (1) `cap=play_target_depth.get(conn)` for `evict_oldest_for_capacity`
      (J2's dynamic cap, not the smoke-fixture's `PROPOSED_QUEUE_CAP`);
  (2) **no UUID dedup** — smoke uses a deterministic offline UUID that
      collides on re-runs; production has random seeds, so no dedup is
      needed;
  (3) envelope emission routes through `_emit_state` + `_row_to_response`
      so the WS payload matches the REST `GET /api/activities/{id}`
      contract byte-for-byte.

  The dispatcher writes its `labeled_events` row BEFORE returning the
  Activity (see `escalation.py:_record`, called at line 437 for offline
  and line 594 for Claude path). Persisting the `activities` row with
  the same `activity.id` produces a valid FK reference, eliminating
  the orphan-row class entirely. The earlier draft of this decision
  said "no helper extraction needed" — revised in iter-2 after a code
  review surfaced the orphan / double-call defects.

**D12 — Skip button stays, renamed for clarity.**
The `SuggestionCard` button currently labeled "skip" calls
`api.regenerate(id, version)` to swap a row's suggestion for a fresh one.
In the multi-suggestion world this is still useful (parent wants this
slot to surface something different *now* rather than dismissing and
waiting for the next cadence tick). Relabel to "try a different one"
to clarify the action's scope is the row, not the activity flow.

## 7. Build steps

**Build-step flag legend** (passed to `/build-step` per the project's build skill):

- `--tdd` — invokes `/build-step-tdd` (write failing tests first, red-green-refactor).
- `--reviewers code` — runs the 4-agent code-review gauntlet
  (correctness, bugs, test quality, style) after the diff lands.
- `--reviewers code --ui` — adds Playwright screenshot capture for UI evidence.
- (no flags) — defaults: worktree isolation, auto reviewer (tests only).
- `Type: code` — default; build-phase spawns `/build-step`. Explicit for clarity.
- `Type: operator` — manual smoke/UAT step; no code diff. Build-phase halts and
  presents commands + check table to the user.

**Phase J orchestration notes for `/build-phase`:**

- All 12 steps are **serial**. J3 and J4 both depend on J1 and could superficially
  look parallelizable, but both modify `core/escalation.py` and `main.py`; J6
  and J7 both touch `frontend/src/parent/store.ts`. Skip the parallel-pair prompt.
- Issue numbers are blank; `/repo-sync` fills them in from the umbrella issue
  (`Phase J — play queue …`) before `/build-phase` runs.
- `--ui` is intentionally **omitted from J6/J7** (store/API refactor + ripout —
  no rendering surface introduced); rendering lands in J8. Build-phase's
  UI-MISSING check will flag these; answer **N** when prompted.

### Step J1: Play-queue settings modules + endpoints
- **Problem:** Add two household-scoped settings, each in its own module +
  endpoint per project convention.
  - `play_target_depth` ∈ {1, 3, 5}, default 3.
  - `play_cadence_seconds` ∈ {0, 10, 30, 60}, default 30. `0` = disabled.
  Migrations 0011 (target depth) + 0012 (cadence) seed the rows in
  `settings`. Modules `core/play_target_depth.py` and
  `core/play_cadence_seconds.py` each expose `get(conn) / set(conn, value)`
  with canonical-set validation + default-on-invalid fallback, mirroring
  `core/transcript_retention.py`. Endpoints
  `api/play_target_depth_settings.py` and
  `api/play_cadence_seconds_settings.py` each expose
  `GET / PUT /api/settings/<key-kebab>` with parent-token scope, mirroring
  `api/transcript_retention_settings.py`. Register both routers in `main.py`.
- **Type:** code
- **Issue:** #93
- **Flags:** `--tdd`
- **Status:** DONE (2026-05-11)
- **Produces:** 2 migrations, 2 core modules, 2 API modules, router
  registration in `main.py`, pytest coverage for read/write/validation/
  invalid-row fallback on each module.
- **Done when:** `GET /api/settings/play-target-depth` and
  `GET /api/settings/play-cadence-seconds` each return defaults on a fresh
  DB; `PUT` with a valid value persists; `PUT` with an invalid value
  returns 422; full pytest passes.
- **Depends on:** none.

### Step J2: Cadence loop task + queue-cap rewiring
- **Problem:** Add `core/play_cadence.py` exporting
  `start_cadence_loop(get_pubsub, db_path) -> asyncio.Task` with the pseudo-
  code shape from §5. Loop:
  - read `play_cadence_seconds` + `play_target_depth` + `current_mode(conn)`
    every tick;
  - sleep `max(5, cadence_seconds) if cadence > 0 else 30`;
  - `continue` if `cadence == 0`, `mode ∈ {OFFLINE, LOW}`, or
    `proposed_count(conn) >= target_depth`;
  - otherwise: build a `ProposeRequest(intent="request_play", slot="freeplay",
    hour=now.hour, seed=random)` and call `_do_propose(body, conn, pubsub,
    judge_call)` directly (no helper extraction needed — production has no
    UUID-collision concern; cf. D11).
  Update the `evict_oldest_for_capacity` call site in
  `src/toybox/api/activities.py:_do_propose` (~line 1117) to pass
  `cap=play_target_depth.get(conn)`. Leave the
  `src/toybox/main.py:_persist_smoke_activity` call site (~line 329) at
  `cap=PROPOSED_QUEUE_CAP` with a one-line comment that smoke is a
  hermetic fixture.
  Wire the cadence task in `_metrics_lifespan` (~line 796) alongside the
  existing metrics + image-gen workers. Use pytest-asyncio for the unit
  test, monkey-patching `asyncio.sleep` and using an in-memory DB.
- **Type:** code
- **Issue:** #94
- **Flags:** `--reviewers code`
- **Status:** DONE (2026-05-12)
- **Produces:** `core/play_cadence.py`; modified `_do_propose` (dynamic
  cap); modified `_metrics_lifespan` (task wiring); pytest-asyncio
  coverage that asserts the loop converges to `target_depth` within ~3
  ticks at `cadence_seconds=0.01`, AND that the loop skips when
  `cadence_seconds=0`, AND that the loop skips when `mode=OFFLINE`.
- **Done when:** unit tests pass; full pytest suite passes; `uv run
  python -m toybox.main --check` exits 0.
- **Depends on:** J1.

### Step J3: Wire production `EscalationDispatcher` + `on_intent`
- **Problem:** Production's `_metrics_lifespan` (`main.py:796`) currently
  constructs `TranscriptPipeline` at `main.py:749` without an `on_intent`
  argument. Mirror the smoke-lifespan reference at `main.py:433` (handler
  body) + `main.py:535` (`on_intent=_on_intent` argument). For production:
  1. Build the dispatcher with the full injection set from D9 — real
     `AnthropicClient`, real `is_capable`, real `record_generation` +
     `judge_and_persist` + `default_conn_factory`. NOT the smoke shims.
  2. Define `async def _on_intent(intent: Intent) -> None` that:
     (a) opens a short-lived connection via `default_conn_factory()`,
     (b) reads `mode = current_mode(conn)`,
     (c) calls `activity = await dispatcher.on_transcript(
         transcript=_synthetic_transcript_for(intent), mode=mode,
         intents=[intent])`,
     (d) **on `activity is None`, returns** (mode 1-2 with no intent, or
         mode 3-4 with no intent, or capability-closed Claude path with
         no offline fallback hit — see existing dispatcher behaviour in
         `core/escalation.py:430-479`),
     (e) **on non-None, calls `_do_propose` to persist + emit envelope.**
     `EscalationDispatcher.on_transcript` returns an in-memory `Activity`
     and does NOT persist — verified in `core/escalation.py:419-479`.
     The smoke harness uses `_persist_smoke_activity` because smoke's
     deterministic UUIDs would otherwise collide on re-run; production
     uses random seeds (no collision risk) so `_do_propose` is the
     correct persist path.
  3. Pass `on_intent=_on_intent` to the `TranscriptPipeline(...)` call at
     `main.py:749`.
  J3 does **NOT** delete the deprecated `spontaneous_interval_from_env`
  code path — that's J4. Production still constructs the dispatcher with
  the default arg's env value in J3; J4 rips both out.
- **Type:** code
- **Issue:** #95
- **Flags:** `--reviewers code`
- **Status:** DONE (2026-05-12) — extracted `_persist_dispatcher_activity` helper instead of `_do_propose` regen path; revised D11 to match.
- **Produces:** modified `main.py` (`_metrics_lifespan` wiring +
  `_on_intent` definition); integration test that feeds a synthetic
  transcript through the pipeline and asserts a `proposed` row +
  `activity.state` envelope land; integration test that confirms modes
  1-2 produce no proposal when no intent matches (preserved behavior).
- **Done when:** integration tests pass; full pytest suite passes; `uv
  run python -m toybox.main --check` exits 0.
- **Depends on:** J1.

### Step J4: Delete dead `TOYBOX_SPONTANEOUS_INTERVAL_SEC` env-var path
- **Status:** DONE (2026-05-12)
- **Problem:** With J3 landed, the production cadence is driven by
  `play_cadence_seconds`. The env-var path in `core/escalation.py` is
  dead code. Delete:
  1. `spontaneous_interval_from_env` function.
  2. `SPONTANEOUS_INTERVAL_SEC_ENV` constant.
  3. `DEFAULT_SPONTANEOUS_INTERVAL_SEC` constant (if unused after the
     deletion).
  4. Dispatcher constructor's `spontaneous_interval_sec` parameter +
     `self._spontaneous_interval_sec` attribute.
  Mark `maybe_fire_spontaneous` itself with a deprecation comment ("Phase
  J cadence loop replaced this; method retained for test back-compat
  only — remove in V2 cleanup"). Grep for callers of the deleted symbols
  before deleting; current callers are inside `core/escalation.py` only,
  but external tests may import the constants.
- **Type:** code
- **Issue:** #96
- **Flags:** `--reviewers code`
- **Produces:** modified `core/escalation.py` (dead-code removal);
  updated tests if any import the deleted constants.
- **Done when:** full pytest passes; ruff + mypy clean; the symbol
  search confirms zero remaining references to the deleted names.
- **Depends on:** J3.

### Step J5: List endpoint + TTL sweep + envelope payload audit
- **Status:** DONE (2026-05-12)
- **Problem:** Add `GET /api/activities/proposed?include_active=true` to
  `api/activities.py`. Returns proposed rows ordered by `created_at DESC`
  (limit default 5, max 5). When `include_active=true`, also returns
  the most recent non-terminal `approved/running/paused/completed` row
  in a sibling `active` field — letting the frontend's bootstrap hydrate
  both lists in one round trip. Add `core/proposed_ttl.py` —
  `start_proposed_ttl_sweep(...) -> asyncio.Task` that ticks every 10s,
  reads live `play_cadence_seconds`; if `0`, skip; else dismiss proposed
  rows where `created_at + 3 × cadence < now()` (UTC ISO-seconds
  formatted; pin format per `core/transcript_retention.py`'s
  `ENDED_AT_ISO_FORMAT_NOTE` to keep lexicographic comparison correct).
  Emit `activity.state` envelope per dismissal. Wire the task in
  `_metrics_lifespan` alongside J2. **Verify** the `activity.state`
  envelope payload includes `created_at` (smoke's envelope at
  `main.py:483` does; confirm `api/activities.py:_emit_state` does too,
  and add if missing).
- **Type:** code
- **Issue:** #97
- **Flags:** `--reviewers code`
- **Produces:** new endpoint in `api/activities.py` + Pydantic response
  model; `core/proposed_ttl.py`; lifespan wiring; pytest coverage for
  the endpoint (empty + populated + with/without active) + the sweep
  (no-op when cadence=0; multi-row dismiss when cadence>0; envelope
  emission per row).
- **Done when:** GET returns the right rows; sweep test with
  `asyncio.sleep` monkey-patched dismisses an expired row and emits
  the envelope; envelope-payload audit confirms `created_at` is
  present; pytest passes.
- **Depends on:** J1, J2.

### Step J6: Frontend store reducers + API methods (additive)
- **Status:** DONE (2026-05-12)
- **Problem:** Additive-only changes to the frontend store + API.
  Existing `activity: Activity | null` slot **stays in place** so
  `App.tsx` keeps compiling. Add alongside it:
  - New store slots: `proposedList: Activity[]`, `active: Activity | null`
    (mirror of the existing `activity` slot — same value, new name).
    Reducers populate both `activity` (back-compat) AND `active`/`proposedList`
    during this step. The old slot is removed in J7.
  - New `applyEnvelope` routing for `activity.state` with explicit
    per-state behaviour (writes to BOTH the old `activity` slot AND the
    new `active`/`proposedList` slots during the transition):
    - `state === "proposed"` → upsert into `proposedList`; also remove
      from `active` if id matches.
    - `state ∈ {approved, running, paused, completed}` → set `active`;
      remove from `proposedList` if id present.
    - `state ∈ {dismissed, didnt_work, ended}` → remove from
      `proposedList` if present; clear `active` if id matches.
  - New reducers: `applyProposedExpired(id)`, `applySwitch(endResult,
    approveResult)`.
  - New API methods: `listProposedActivities({include_active?: boolean})`,
    `getPlayTargetDepth()`, `setPlayTargetDepth(value: 1 | 3 | 5)`,
    `getPlayCadenceSeconds()`, `setPlayCadenceSeconds(value: 0 | 10 | 30 | 60)`.
    Types `PlayTargetDepth`, `PlayCadenceSeconds`.
  This step is `--tdd`: write the reducer test cases first (covering all
  6 terminal-state transitions + upsert + expired + switch), then build
  the reducers green. No UI changes; consumers continue reading
  `state.activity` until J7.
- **Type:** code
- **Issue:** #98
- **Flags:** `--tdd`
- **Produces:** extended `store.ts` (additive); extended `api.ts` + types;
  extended `store.test.ts`. No consumer changes.
- **Done when:** vitest passes (new tests green + existing tests
  unchanged); typecheck clean.
- **Depends on:** J5.

### Step J7: Rip out old `activity` slot; rewire consumers
- **Status:** DONE (2026-05-12)
- **Problem:** Delete `activity: Activity | null` from the store. Update
  every consumer that read `state.activity` to read `state.active`
  instead (grep found matches in `App.tsx`, `ActivityPanel.tsx`,
  `SuggestionCard.tsx`). The reducer `setActivity` becomes `setActive`.
  Update affected tests (`App.test.tsx`, `App.retention.test.tsx`,
  `store.test.ts` — drop the back-compat assertions, keep the new ones).
  App.tsx still renders the same UI as today (single suggestion OR
  panel) — the queue list itself lands in J8. After J7, the store has
  only the new shape; App.tsx renders against the new shape via the
  existing `showSuggestion / showPanel` gates (`state.active === null` +
  `proposedList[0]` for the suggestion-card case, `state.active` for the
  panel case).
- **Type:** code
- **Issue:** #99
- **Flags:** `--reviewers code`
- **Produces:** rewritten `store.ts` (old slot gone); rewritten
  consumers; updated tests. Visible behaviour identical to pre-J7
  (single-suggestion UX preserved until J8 introduces the queue).
- **Done when:** vitest passes; typecheck clean; Playwright smoke
  passes against the existing single-suggestion UI.
- **Depends on:** J6.

### Step J8: PlayQueueList component + App.tsx wiring
- **Status:** DONE (2026-05-12)
- **Problem:** New `frontend/src/parent/components/PlayQueueList.tsx`.
  Copy the structure of `TranscriptsManager.tsx`: a scrolling list with
  1s tick, `fadingIds` set, `removalTimeoutsRef` cleanup. **No separate
  ws subscription** — `PlayQueueList` reads `proposedList` + `active` from
  the store (already wired in J6/J7). Component renders: row 0 = pinned
  active (rendered as `<ActivityPanel>` with all its current props) when
  `active !== null`, then proposed rows below rendered as
  `<SuggestionCard>` (with the `skip` button **relabeled to "try a
  different one"** but functionally unchanged — calls `api.regenerate(id,
  version)` to swap that row's suggestion). Expiry math: `expires_at =
  created_at + 3 × cadence_seconds` when `cadence_seconds > 0`; when
  `cadence_seconds === 0`, fade machinery is disabled (no TTL).
  Bootstrap in `App.tsx` calls `listProposedActivities({include_active:
  true})` + `getPlayTargetDepth()` + `getPlayCadenceSeconds()` in
  parallel with the existing seeding block. Thread `cadenceSeconds` +
  `targetDepth` down to the list. Remove the `showSuggestion /
  showPanel` block in App.tsx; replace with a single
  `<PlayQueueList ... />`. Render `<TriggerButton>` **below** the list
  with restyled de-emphasis (small "+ trigger now" link affordance,
  not the top-of-tab button it is today).
- **Type:** code
- **Issue:** #100
- **Flags:** `--reviewers code --ui`
- **Produces:** new component, modified App.tsx, restyled
  `TriggerButton.tsx`, `PlayQueueList.test.tsx` (vitest with fake
  timers covering: empty / proposed-only / active-only / both /
  TTL fade / TTL fade disabled at cadence=0), Playwright smoke
  `playwright/parent.spec.ts` updated to assert the queue renders +
  the pinned-active surface still works.
- **Done when:** vitest + Playwright pass; typecheck + lint clean.
- **Depends on:** J7.

### Step J9: Switch-confirm flow
- **Problem:** When the parent clicks Approve on a suggestion while
  `active !== null`, fire `window.confirm("Switch from '<active.title>' to
  '<new.title>'? The current activity will end.")`. On confirm: call
  `api.end(active.id, active.version)` first, then `api.approve(new.id,
  new.version)`. Both wrapped in `withConflictHandler` with refetch. On
  conflict in the `end` call: refetch active, retry once with the fresh
  version (if `active.state` is already terminal, skip the `end`).
  Pattern is the same shape `ActivityPanel`'s `handleEndClick` already
  uses for the End button. On cancel: no-op, the approve doesn't fire.
- **Type:** code
- **Issue:** #101
- **Flags:** `--reviewers code --ui`
- **Produces:** modified `App.tsx` approve handler, unit test in
  `App.test.tsx` covering: no-active → simple approve; active → confirm
  fires + on-confirm → end-then-approve sequence + on-cancel → nothing;
  Playwright smoke updated.
- **Done when:** vitest + Playwright pass.
- **Depends on:** J8.

### Step J10: SettingsPanel additions for target depth + cadence
- **Problem:** Add two segmented controls to `SettingsPanel.tsx`:
  - `play_target_depth` with labels "1", "3", "5".
  - `play_cadence_seconds` with labels **"off", "10s", "30s", "1m"**
    (four options; `off` maps to value `0`).
  Seeding + state lift mirrors how `transcript_retention` works
  (App.tsx owns the state and pulls cadence seconds through to
  PlayQueueList for TTL math; SettingsPanel receives current value +
  `onChanged` callback). Optimistic update on click; rollback + toast
  on server error. Snap-to-nearest defensive aria-pressed for
  non-canonical values (carry-over pattern from Phase I's
  `TranscriptRetentionControl`). Acknowledge in the UI copy that with
  cadence set to "off," only transcripts + the manual Trigger fire.
- **Type:** code
- **Issue:** #102
- **Flags:** `--reviewers code --ui`
- **Produces:** modified `SettingsPanel.tsx`, modified `App.tsx` (state
  + callback wiring + threading `cadenceSeconds` down to PlayQueueList
  for TTL math), vitest coverage equivalent to
  `TranscriptRetentionControl.test.tsx` for each new control, +
  explicit coverage of the cadence-off case (fade disabled, no
  cadence-driven proposals).
- **Done when:** vitest + Playwright pass.
- **Depends on:** J9.

### Step J11: End-to-end smoke gate (60s, real components, no mocks)
- **Type:** operator
- **Issue:** #103
- **Problem:** Run the full play-queue pipeline for 60s and observe
  seven behaviors. See the **Commands to run** + **What you're looking
  for** sections below.
- **Produces:** run doc at
  `documentation/runs/<YYYY-MM-DD>-play-queue-smoke.md` capturing the
  observed result for each check.
- **Done when:** all seven checks pass; any failure becomes a follow-up
  issue before declaring the phase done.
- **Depends on:** J10.

#### Commands to run

```powershell
# (1) Start the backend (terminal 1).
$env:TOYBOX_LAN_IP = "127.0.0.1"
uv run python -m toybox.main --host 127.0.0.1 --port 8000

# (2) Start the frontend (terminal 2).
cd frontend; npm run dev

# (3) Open http://localhost:4000/parent in a browser.
#     Log in with the parent PIN.
#     In Settings → Settings: set cadence to 10s, target depth to 3.
#     Confirm mic is unmuted (Header should show green capturing state).
#     Return to Play → Play Ideas. Start the 60-second observation.

# (4) During seconds 0-30: speak a phrase that matches an intent registry
#     entry (e.g., "I'm bored" / "let's play a game" / "I want a story")
#     into the home machine's mic.

# (5) During seconds 30-60: approve any proposed row (click "approve").
#     Wait 10s for a new proposed row to appear below the pinned active.
#     Click approve on that new row — confirm modal should fire.
#     Click OK in the modal.
```

#### What you're looking for

| Check | Expected outcome |
|---|---|
| (a) Cadence fires every 10s | Queue fills to 3 proposed rows then stops growing. Backend log shows one INSERT per ~10s tick. |
| (b) Transcript intent fires a proposal | Speaking a matched-trigger phrase produces an additional proposed row; queue stays at cap 3 (oldest evicted via `dismissed` envelope). |
| (c) TTL fades old proposals | A proposed row passes its 30s TTL (= 3 × 10s cadence) and fades out client-side via the 600ms opacity transition. |
| (d) Approve pins active at top | Approving any row: pinned active appears at top as full ActivityPanel; other proposed rows clear from the list. |
| (e) Cadence continues while active | After approval, wait 10s — a new proposed row appears below the pinned active. |
| (f) Switch-confirm flow | Approving the new row fires `window.confirm`. On confirm: old active transitions to `ended` (visible briefly then disappears); new is now pinned at top. |
| (g) No errors | No console errors in browser DevTools; no 4xx/5xx in backend log; no failed envelopes (no `system` topic warnings). |

### Step J12: iPad UAT + close
- **Type:** operator
- **Issue:** #104
- **Problem:** Repeat J11 on a real iPad over LAN — speech-driven
  proposals from the home machine's microphone, observed on the parent
  UI on the iPad. Verify the scrolling list + fade-out animations
  render smoothly on iPad Safari (transitions are CSS — should be fine,
  but the `setInterval` cadence has historically had focus-loss issues
  on Safari; flag if observed). Verify switch-confirm uses the
  iOS-native confirm dialog and behaves correctly.
- **Produces:** run doc at
  `documentation/runs/<YYYY-MM-DD>-play-queue-ipad-uat.md`; closes
  the umbrella issue + child step issues; README + plan.md Phase
  history updated; project memory entry.
- **Done when:** iPad UAT PASS; docs updated; commit pushed.
- **Depends on:** J11.

#### Commands to run

```powershell
# (1) Start backend bound to LAN (terminal 1).
$env:TOYBOX_LAN_IP = "<your-LAN-IPv4>"
uv run python -m toybox.main --host 0.0.0.0 --port 8000

# (2) Start frontend bound to LAN (terminal 2).
cd frontend; npm run dev -- --host 0.0.0.0

# (3) On the iPad: open Safari → http://<LAN-IPv4>:4000/parent.
#     Log in with parent PIN.
#     Repeat J11's observation protocol (cadence to 10s, target depth to 3,
#     speak matched intents, approve, switch, observe TTL fade).
```

#### What you're looking for

| Check | Expected outcome |
|---|---|
| All J11 checks (a-g) | Identical outcomes on iPad. |
| (h) Transition smoothness on Safari | Fade animations are visually smooth at 60fps; no visible jank or stuttered timer cadence. |
| (i) Backgrounded-tab cadence | Backgrounding the parent tab for >30s does not break the cadence tick when foregrounded (regression check for Safari focus-loss). |
| (j) iOS-native confirm | Switch-confirm uses Safari's modal confirm dialog (not the custom-styled web modal). Confirm + cancel both behave correctly. |

## 8. Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| `EscalationDispatcher` production wiring drifted from smoke | The smoke path has been the only consumer for months. Wiring it in production could surface an interface drift not caught by smoke tests. | J9 smoke gate exercises the full path with real audio. Failures become follow-up issues. |
| Cadence + Claude token cost spike | A 10s cadence at mode 4 HIGH listening could fire up to ~360 Claude calls/hour (cadence ticks + transcript intents). The throttle gates Claude per-call but doesn't cap aggregate spend. | Cadence skips under modes 1-2 by design. Throttle + breaker absorb burst load. Operator can drop cadence to 1m or off in SettingsPanel. V2 candidate: aggregate-spend gauge in StatsPanel. |
| Cadence loop floods the proposed-queue under noisy mic | A chatty room with many transcript intents + a 10s cadence could pin the queue at cap and churn through templates with high eviction. | Eviction is `dismissed` (terminal) — no data integrity issue. Visually the parent sees a queue that's always at cap and rotates fast; that's the spec, not a bug. V2 diversity ranking will address "same template keeps re-appearing." |
| Two open tabs (desktop SettingsPanel + iPad parent UI) see different lists momentarily | A setting change on desktop takes effect on the next backend cadence tick (≤ 60s for the slowest cadence). Until then, the iPad's local `cadenceSeconds` prop is stale and TTL math is briefly inconsistent. | Document as "next-tick-wins convergence." iPad UAT in J10 explicitly exercises this two-tab case + records observed convergence delay. WS-driven settings push is a V2 candidate if the delay is jarring in practice. |
| Switch flow non-atomic (end + approve as two calls) | A ws envelope landing between `end` and `approve` could push the store into an inconsistent state (no active, no proposed for the new id). | Both calls go through `withConflictHandler` with refetch. The store's `applyMutationResult` is version-guarded. On end-conflict, the approve does not fire (D10). Acceptable. |
| TTL derivation hides a tunable | Power users may want longer TTLs at fast cadences. | Recorded as V2 follow-up. Real signal needed before adding a third setting. |
| Cadence task survives a settings PUT with invalid values | If `set_play_cadence_seconds` mistakenly accepts a value outside `{0, 10, 30, 60}`, the loop's `await asyncio.sleep(cadence_seconds)` could receive a negative value. | J1 validates strictly server-side (rejects with 422). Defensive `max(5, cadence) if cadence > 0 else 30` inside the loop body as belt + suspenders. |
| Cadence-off interaction with TTL | If parent sets cadence to 0 while proposed rows are present with derived TTLs already passed, the TTL sweep would still want to dismiss them on the next tick. | J4 sweep reads live `cadence_seconds`; when `0`, sweep skips. Existing proposed rows persist until cap eviction or manual dismiss. Acceptable. |
| Existing `App.test.tsx` + `App.retention.test.tsx` break | Store shape change `activity → proposedList + active` ripples through every test that asserts on the active activity. Grep finds matches in `App.tsx`, `ActivityPanel.tsx`, `store.ts`, `SuggestionCard.tsx`. | Update tests in J5 alongside the refactor. Run full vitest suite at the end of J5. |
| Deleting `spontaneous_interval_from_env` breaks downstream callers | Any code reading the function or the env var would now fail. | Grep before J4 — current callers are inside `core/escalation.py` only (the dispatcher constructor's default). External tests may read the constant; J4 audits + updates them. |

## 9. Testing strategy

**Unit (pytest):**
- `core/play_settings.py` — defaults, valid/invalid round-trip, fallback
  on missing row, fallback on out-of-range stored value.
- `api/play_settings.py` — GET defaults, PUT both fields, PUT single
  field, PUT invalid → 422, scope enforcement.
- `core/play_cadence.py` — loop with sub-second cadence + in-memory DB:
  proposed_count converges to target_depth; respects updated settings
  on subsequent ticks.
- `core/proposed_ttl.py` — expired row dismissed + envelope emitted;
  non-expired row left alone; honour live cadence value for TTL math.
- `api/activities.py` (new endpoint) — `GET /api/activities/proposed`
  returns proposed rows ordered DESC; honours `limit`.

**Unit (vitest):**
- `store.test.ts` — new reducers + envelope routing across all
  state transitions.
- `PlayQueueList.test.tsx` — fade-out tick + cleanup mirror of
  `TranscriptsManager.test.tsx`'s patterns; pinned active rendering;
  proposed rows rendering; no-active vs. active layouts.
- `SettingsPanel.test.tsx` — two new controls each with optimistic
  update + rollback on error.
- `App.test.tsx` — bootstrap seeds both settings + proposed list;
  approve with active → confirm fires; approve without active →
  simple approve.

**Integration (pytest):**
- Full audio-pipeline → intent → escalation → propose path with a
  synthetic transcript (already covered as a pattern in
  `tests/audio/test_pipeline.py` — extend or copy).

**Existing tests at risk of regression:**
- Any test in `App.test.tsx` / `App.retention.test.tsx` that asserts
  on `state.activity` directly. Updated during J7 (slot ripout); J6
  preserves the slot for back-compat during reducer additions.
- Any test that imports `PROPOSED_QUEUE_CAP` and asserts a hardcoded 5
  cap. Update during J2 to test against the dynamic cap.

**End-to-end (Playwright + operator):**
- `playwright/parent.spec.ts` updated to exercise the queue list
  rendering + approve-while-active confirm flow.
- J11 operator smoke gate — 60s real-component cycle.
- J12 iPad UAT — full LAN smoke with real mic.

## 10. V2 — ideas to investigate

Recorded for V2 research (not V1 build steps). These are the directions to
explore once V1 ships and we have real-world usage signal:

- **V2-A — Anti-back-to-back-repeat diversity.** Track the last K templates
  the queue surfaced (in-memory rolling window) and de-rank repeats during
  template selection in `activities.generator.generate()`. Should reduce the
  "same Wizard adventure keeps appearing" failure mode that emerges naturally
  when the offline catalog is small and the cadence is fast. Possible
  refinement: also de-rank same-persona repeats.
- **V2-B — Multi-utterance transcript context.** V1 treats each `Intent`
  the EscalationDispatcher receives independently. V2 could maintain a
  rolling N-utterance window per session and synthesise a richer intent
  (e.g., "kid said dragon then said princess" → propose a dragon-princess
  crossover template). Architecturally: a new `core/transcript_context.py`
  that aggregates intents from the last K transcripts before handing off
  to the generator. Compatible with V2-A.
- **V2-C** (parked) — TTL as its own setting, ranking by `parent_signal`,
  per-mode cadence — recorded but not selected for V2 investigation.

## 11. Naming

Phase letter and slug:

- Plan file: `documentation/play-queue-plan.md` (this file).
- Steps: J1 through J12 (verified: Phase I is the most recent shipped phase
  per `documentation/plan.md` status table; J is the next available letter).
  Step count grew from 10 → 12 during plan-wrap: J3 split into J3 (wire
  dispatcher) + J4 (delete dead env-var path); J5 split into J6 (additive
  store reducers via TDD) + J7 (rip out old slot). See §7 orchestration
  notes for `/build-phase` parsing details.
- Branch: `phase-j/play-queue` per existing convention.
- GitHub umbrella issue title: "Phase J — play queue (multi-suggestion +
  cadence + transcript-driven proposals)".

Draft row to add to the status table in `documentation/plan.md`:

```
| **J** — play queue | multi-suggestion scrolling queue (target depth 1/3/5) + autonomous cadence loop (off/10s/30s/1m) + transcript-driven proposals (production `on_intent` wired) + per-row TTL fade + switch-confirm flow | PLANNED — see [`play-queue-plan.md`](play-queue-plan.md) |
```
