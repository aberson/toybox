# Phase K — Roles, Songs, Jokes, Voice (feature plan)

## 1. What this feature does

Phase K layers four play-enhancement features onto the post-Phase-J toybox:

1. **Toy roles.** Activity templates declare named role slots (`{quest_giver}`, `{hero}`, `{frenemy}`, etc.) and the slot-fill engine assigns toys to each role at proposal time. The parent sees the cast on the suggestion card and can re-roll either roles ("new cast") or template ("new activity") **while the activity is still in `proposed` state**. Persona × role compatibility is a soft-scoring bias, not a hard ban — Princess can still narrate a Big Bad Boss, just less often.

2. **Songs as part of play.** A bundled corpus of ~50 short pre-rendered `.mp3` tracks plays via the kiosk audio surface. Each song carries a single `theme` tag (adventure / magic / space / animals / vehicles / food / friendship / pirates / knights / weather / music / silly — 12 themes for v1).

3. **Jokes as part of play.** A bundled corpus of ~50 short text jokes (setup/punchline) delivered via Web Speech TTS. Same `theme` taxonomy as songs.

4. **Click-to-read on the kiosk.** Two distinct affordances. (a) Tap a word, hear that word. (b) A watermarked "?" bubble in the bottom-left of each text-bearing step (labeled "Read Me") reads the full sentence on tap. Robot voice via the browser's `speechSynthesis` API (chosen because it's browser-native, zero install, and works on iPad PWA — the kid's primary surface; trade-off: per-device voice quality variance, acceptable for v1's robot voice). Each persona maps to a `{rate, pitch, voice_name?}` profile so the wizard sounds different from the detective. The "Read Me" button reads only the step's main text — not the choice labels on fork steps (choices use word-level reading for individual words).

5. **Five delivery surfaces for songs + jokes** — independently parent-toggleable except parent-inserted:
   - **A — Standalone activities.** "Tell me a joke" / "Sing me a song" trigger phrases produce single-step activities that flow through the normal propose → approve → play pipeline.
   - **B — Theme-tagged embedded.** Branching templates declare `recommended_themes: [...]`. A step with `kind: "song"|"joke"` and `auto: true` picks the corpus entry whose `theme` matches one of the recommended themes (seed-deterministic, alphabetical tie-break).
   - **E — Endings.** Templates declare an optional `ending_step: {kind: "song"|"joke", auto: true}`. When present (and the surface flag is on), the engine appends a themed interjection after the template's last step at activity-creation time.
   - **P — Parent-inserted.** Two icon buttons on the running ActivityPanel — "+ song" / "+ joke" — call `POST /api/activities/{id}/insert-{joke,song}` to insert a themed interjection at `current_step+1`. **No toggle** — always available.
   - **S — Persona + character spontaneity.** Both **personas** (kiosk presenter) AND **roles** (character types) carry a `spontaneity_rates: {jokes: 0.0-1.0, songs: 0.0-1.0}` pair. On advance, the engine takes the max rate per content type across `(persona ∪ every cast role)` and rolls. On hit, it inserts a themed interjection. **Attribution:** when a cast role drives the max, the kiosk narrates the interjection as that toy ("Captain Bear giggles: …"); when the persona drives the max, the persona narrates. Audio + voice still come from the persona's `voice_profile`. Template position pointer doesn't advance through interjections. Per-toy override of role rates is a v2 idea.

6. **Parent-controlled feature flags (8 total).** Two content masters (`jokes_enabled`, `songs_enabled`) plus four surface flags (`play_standalone_enabled`, `play_embedded_enabled`, `play_endings_enabled`, `play_spontaneity_enabled`) plus two kiosk flags (`clickable_words_enabled`, `read_me_button_enabled`). Defaults: all ON except `play_spontaneity_enabled` (default OFF — interjections can disrupt flow, parents opt-in). A surface delivers content only when `(content_master AND surface_flag)` are both true.

**Why now.** The post-Phase-J system runs a real cadence loop with multiple suggestions queued. Adding roles deepens replay value of each template (the same "rocket launch" can star different toys in different roles). Adding songs/jokes/voice closes the literacy + audio engagement gap operators have flagged. Five delivery surfaces give parents independently-tuneable variety — explicit on-demand (A), structured story moments (B, E), real-time on-the-fly (P), and emergent surprise (S). Feature flags let each household tune the experience.

## 2. Existing context

### Reference (glossary, wire shapes, invariants, rules)

**Acronyms.** TTS = Text-To-Speech. PWA = Progressive Web App (iPad-installable web app). HIG = Human Interface Guidelines (Apple's UI conventions). ws = WebSocket. Pydantic = Python data-validation + serialization library. Coqui TTS = open-source neural text-to-speech framework (operator-installed; not a runtime dep). STT = Speech-To-Text.

**Project invariants this plan must respect** (full list in [`documentation/plan.md`](plan.md), summarized inline so the plan is self-contained):

| # | Invariant |
|---|---|
| 1 | Single uvicorn worker — SQLite WAL is single-writer; `--workers >1` corrupts silently. |
| 2 | Default bind is `127.0.0.1`; LAN binding requires parent PIN (startup guard exits non-zero otherwise). |
| 3 | Every activity mutation requires `If-Match-Version` header; 409 + current version on mismatch (see §7 API contracts). |
| 4 | Every Claude call goes through the capability gate (`is_capable()`); offline fallback when false. |
| 5 | Photo uploads always go through the validation pipeline. |
| 6 | Transcript text never logged at INFO+ (pre-commit hook enforces). |
| 7 | `trigger_phrase` + `persona_reasoning` are PII-stripped from `activity.state` ws topic. |
| 8 | Slugs are server-derived from `display_name`; client cannot supply them. |
| 9 | Pydantic ↔ TypeScript codegen is a pre-commit hook; drift in `frontend/src/shared/types.ts` is a check failure. |
| 10 | **Forward-only migrations.** v1 has no rollback path; abort + preserve DB on failure. |

**`If-Match-Version` semantics.** Client sends header with the activity `version` it last saw. Server compares to current DB version: on match, executes mutation and increments version; on mismatch, returns 409 with the current `ActivityResponse` body. Frontend's [`withConflictHandler`](../frontend/src/parent/api.ts#L1689) wraps the round-trip — on 409 it refetches, fires an `onConflict` callback, then returns `null` so caller can no-op. Standard for every Phase K mutation endpoint.

**Identifier formats.**

| Entity | Format | Example |
|---|---|---|
| Activity ID | UUIDv4, deterministic via SHA-256 of `(intent, slot, seed, hour, template_id)` → version-nibble-4 → RFC-4122 | `7c9e6b1a-3d2f-4a8b-9c1e-5f6d7e8a9b0c` |
| Activity step `seq` | Integer, 0-indexed, monotonic within an activity | `0`, `1`, `2` |
| Template step `id` | Author-chosen kebab-slug, per-template-unique | `jungle_end`, `cliff_end` |
| Toy ID | Server-derived kebab-slug from `display_name` (invariant 8) | `captain-bear`, `wise-owl` |
| Persona ID | Author-chosen lowercase snake | `princess`, `wizard`, `detective`, `periodic_table` |
| Corpus entry ID (joke / song) | Author-chosen kebab-slug | `space-rhyme-01`, `why-chicken` |

**Wire shape: `ActivityResponse`** ([api/activities.py:238](../src/toybox/api/activities.py#L238)). Full Pydantic class. Phase K adds the bold rows.

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | UUIDv4 per above |
| `state` | `"proposed" \| "approved" \| "running" \| "paused" \| "completed" \| "dismissed" \| "didnt_work" \| "ended"` | State machine |
| `version` | `int ≥ 1` | Increments on every mutation |
| `title` | `str \| None` | Display title |
| `summary` | `str \| None` | Short description |
| `persona_id` | `str \| None` | Library-resolved kiosk persona |
| `intent_source` | `str \| None` | Originating intent (`request_play`, `request_song`, etc.) |
| `child_ids` | `list[str]` | Child profiles in scope |
| `toy_ids` | `list[str]` | Hydrated toy IDs (Phase F sprite resolution) |
| `created_at` / `started_at` / `ended_at` | `str` (ISO `…Z`) | Pipeline-pinned format |
| `steps` | `list[ActivityStepResponse]` | See below |
| `metadata` | `dict[str, Any]` | Carries `slot_values`, `signature`, etc. |
| `trigger_phrase` / `persona_reasoning` | `str \| None` | "Why this?" telemetry (PII-stripped on ws topic per invariant 7) |
| **`roles`** (NEW K1) | `dict[str, RoleAssignment]` | Map role-name → assignment; `{}` for role-less activities |
| **`cast_summary`** (NEW K7) | `str` | "Quest Giver: Wise Owl, Hero: Captain Bear" — parent UI label |
| **`interjection_pending`** (NEW K15) | `bool` | True when last advance produced a spontaneity step |

`RoleAssignment` = `{role_name: str, toy_id: str | None, generic_descriptor: str | None, display_name: str}` — exactly one of `toy_id` / `generic_descriptor` is set; `display_name` is what gets rendered.

**Wire shape: `ActivityStepResponse`** — per-step wire shape.

| Field | Type | Notes |
|---|---|---|
| `seq` | `int` | 0-indexed position |
| `body` | `str` | Rendered step text (slot-substituted server-side via `render_with_slot_fills`) |
| `kind` | `"text" \| "fork" \| "song" \| "joke"` | NEW K3; defaults to `"text"` for legacy rows |
| `sfx` | `str \| None` | SFX cue name |
| `expected_action` | `str \| None` | Optional input prompt |
| `action_slot` | `str \| None` | Persona sprite slot (idle, cheering, etc.) |
| `id` | `str \| None` | Per-template kebab-slug (Phase G) |
| `next` | `str \| None` | Linear-advance target step id |
| `choices` | `list[{label, next}] \| None` | Fork choices |
| `chosen_label` | `str \| None` | Set on prior step when a fork resolved (Phase G3) |
| `current` | `bool` | True when this is the active step (ws envelope only) |
| `metadata` | `dict[str, Any]` | Carries `{"interjection": "embedded" \| "ending" \| "parent" \| "spontaneity", "source_id": <corpus_entry_id>}` on interjection steps; absent on regular template steps |

**Code-quality rules** ([code-quality.md](../../.claude/rules/code-quality.md)) summarized inline so reviewers don't need to fetch them.

| § | Rule | Phase K touch points |
|---|---|---|
| §1 | Grep every downstream consumer when changing a key/id/shape constant. Attach grep results to the PR. | K3 (template slot syntax change), K16 (200-template backfill IS the consumer audit). |
| §2 | One source of truth for data-shape constants. Tests assert `is`, not `==`, so future re-duplication fails CI. | K1 (`Role` + `Theme` + `InterjectionKind` StrEnums + descriptor tables); K2 (per-setting modules); K14 (`build_interjection_step` is the one step-shape source). |
| §3 | Audit wire shape when storage representation changes (lazy insert, normalization, new constraint). | K6 (recast writes new `slot_fills_json` + re-renders `activity_steps.body`); K15 (insert endpoints add rows). |
| §4 | New components require an integration test through the production caller. Unit tests with mocks miss producer-consumer drift. | K5 (`_do_propose` for role engine); K13 (standalone intents through propose); K15 (spontaneity hook + insert endpoints through advance). |

---

Background a fresh-context model needs to understand the impact.

- **Toys today are single-slotted via `{toy}` substitution.** Slot substitution lives in `_substitute(text, slot_fills)` and is wrapped by the public `render_with_slot_fills(text: str, slot_fills: dict[str, str]) -> str` ([generator.py:654, :689](../src/toybox/activities/generator.py#L654)). The function is the lazy-advance entry point — Phase G persists step bodies for `steps[0]` at creation time and renders later step bodies on advance via `render_with_slot_fills(template_step_text, activities.slot_fills_json)`. Phase K's role placeholders (`{quest_giver}`, etc.) flow through this same dict.

- **Activity slot fills are persisted as a JSON column, not a separate table.** Migration `0008_activity_slot_fills.sql` adds **`activities.slot_fills_json TEXT NOT NULL DEFAULT '{}'`** ([0008_activity_slot_fills.sql:38](../src/toybox/db/migrations/0008_activity_slot_fills.sql#L38)). The JSON object accepts arbitrary keys, so role placeholders (`quest_giver`, `hero`, etc.) join the existing `toy` / `room` / `slot` keys without altering the schema.

- **`_do_propose(body, conn, pubsub, judge_call)` is at [activities.py:1181](../src/toybox/api/activities.py#L1181).** Plan-K's recast endpoint + spontaneity hook + parent-insert endpoint are all built around this entry point. **Code-quality §4 ([code-quality.md](../../.claude/rules/code-quality.md)) requires** new components to be integration-tested through this caller, not in isolation.

- **The wire-shape class is `ActivityResponse`** at [activities.py:238](../src/toybox/api/activities.py#L238). Plan refers to it as "Activity" for brevity but the Pydantic class is `ActivityResponse`. Same applies to `ActivityStepResponse` for steps.

- **Personas are kiosk avatars, not toys.** [personas/library/_schema.json](../src/toybox/personas/library/_schema.json) currently has `id`, `display_name`, `archetype`, `system_prompt`, `avatar_image_path`, `behavior_tags`, age range. **No voice profile, no role weights, no spontaneity rates today.** All three are new in Phase K (K8 / K1 / K1). The `spontaneity_rates` field is a `{jokes, songs}` object pair, mirrored on roles in `roles.py` so the spontaneity engine has the same shape on both sides of the max-rate computation.

- **Branching templates have only three step kinds today.** Reading [request_play.json](../src/toybox/activities/templates/branching/request_play.json): each step is either a plain text step (`text`, optional `sfx`, optional `action_slot`), a `fork` step (`text` + `choices[]` with `next` ids), or a named end step. Phase K adds `kind: "song"` and `kind: "joke"`, plus `kind` defaults to `"text"` for legacy template entries.

- **The trigger registry is JSON-driven + version-merged.** [triggers/registry.py:1-27](../src/toybox/triggers/registry.py#L1-L27) describes the merge semantics — shipped patterns in `src/toybox/triggers/defaults.json` are merged into the user-editable copy on load, keyed on `id` field, version-bumped patterns overwrite, user-only patterns survive. K13 adds new entries to `defaults.json` for `request_song` and `request_joke`, bumping the file's pattern versions monotonically.

- **No TTS exists.** The `tts|speech|synthesize` grep across the repo only hits STT files (`faster-whisper`, `audio/stt.py`, `audio/vad.py`, `audio/pipeline.py`). The web kiosk has no `speechSynthesis` call site. K8 adds the first one.

- **No `settings.changed` ws envelope exists.** A grep for `settings.changed|broadcast.*settings|emit.*settings` returns zero matches. Phase I's precedent is that **the backend** reads settings fresh per tick ([transcript_retention.py:12](../src/toybox/core/transcript_retention.py#L12)); the frontend gets new values on next API call. **Phase K does NOT introduce a live-propagation envelope** — flag changes take effect on the next kiosk bootstrap / refresh. This is the v1 contract; live propagation is a v2 idea.

- **Kiosk renders steps via `StepCard.tsx`.** [StepCard.tsx](../frontend/src/child/components/StepCard.tsx) is the step renderer; choices via [ChoiceButton.tsx](../frontend/src/child/components/ChoiceButton.tsx); persona via [PersonaAvatar.tsx](../frontend/src/child/components/PersonaAvatar.tsx). Click-to-read handlers attach to step bubble + each word span in K9; voice profile is read from the resolved persona in K8.

- **Suggestion card shows the cast.** [SuggestionCard.tsx](../frontend/src/parent/components/SuggestionCard.tsx) currently shows toy names as a flat list. K7 adds role labels next to each toy ("Quest Giver: Wise Owl") plus two re-roll buttons. Phase J shipped `PlayQueueSettingsControls.tsx` as a dedicated SettingsPanel sub-component ([SettingsPanel.tsx:35,524](../frontend/src/parent/components/SettingsPanel.tsx)); K2 mirrors this pattern with a new `PlayFeaturesControls.tsx` for the eight new flag controls.

- **Phase J is partially-shipped (J1-J10) but J11 smoke + J12 iPad UAT have not yet been operator-run.** Phase K's SettingsPanel additions will sit alongside Phase J's `<PlayQueueSettingsControls>` and Phase I's `<TranscriptRetentionControl>`. If J11/J12 surface a defect in the settings pattern, K may need to absorb the fix. Recommend (out-of-band): operator runs J11 + J12 before K kicks off.

- **Phase E is concurrently in flight.** Latest master commit at plan-time is `e90d027 Step 25c-pre (#112)` (E1c benchmark CLI). Phase E touches `api/activities.py` for env-var dispatch ([activities.py:1199-1203](../src/toybox/api/activities.py#L1199-L1203)). Phase K's recast + insert + spontaneity-hook endpoints will also touch this file. **Mitigation:** sequence Phase K to start after the next Phase E checkpoint merges to master, OR accept merge-resolution work in K6 (recast) and K15 (interjection endpoints).

- **`documentation/plan.md` status table is stale** — does not list Phase J or in-flight Phase E. A pre-K1 housekeeping task should update plan.md so `/repo-sync` (which mints fresh-context issue bodies straight from the plan) sees current truth.

- **Code-quality landmines this phase is exposed to.** Three of the four rules in [`code-quality.md`](../../.claude/rules/code-quality.md) apply directly. K1 (single source of truth for role names + themes + interjection types), K5 (integration test through `_do_propose`), and K16's per-template validator all exist specifically to prevent these. See §10 for the explicit mapping.

### Existing primitives (one-line glosses)

| Symbol | Where | What it does |
|---|---|---|
| `ActivityResponse` | [api/activities.py:238](../src/toybox/api/activities.py#L238) | Wire shape. K1 adds `roles: dict[str, RoleAssignment]` + `cast_summary: str`. K15 adds `interjection_pending: bool` for parent-UI gating. |
| `_do_propose(body, conn, pubsub, judge_call)` | [api/activities.py:1181](../src/toybox/api/activities.py#L1181) | Shared propose-and-persist helper. **K5 integration-tests new role engine through this entry point**, per [code-quality.md §4](../../.claude/rules/code-quality.md). |
| `render_with_slot_fills(text, slot_fills)` | [activities/generator.py:689](../src/toybox/activities/generator.py#L689) | Lazy slot substitution. K6 recast re-renders persisted `activity_steps.body` rows via this function (new `slot_fills_json` → new bodies). K14 + K15 reuse it for interjection step rendering. |
| `ResolvedChildren` / `ResolvedToy` | [activities/content_resolver.py](../src/toybox/activities/content_resolver.py) | Single-seam cache for child + toy resolution per propose. K4 adds `resolve_role_slots(template, available_toys, persona, seed)` to the same module. |
| `is_capable(breaker)` | [ai/capability.py](../src/toybox/ai/capability.py) | Capability gate. Songs / jokes / roles all work offline — corpus is bundled, role engine is deterministic. No new capability dependency. |
| `withConflictHandler<T>` | [frontend/src/parent/api.ts:1689](../frontend/src/parent/api.ts#L1689) | 409 retry wrapper. K6's recast and K15's parent-insert endpoints both use this. |
| `StepCard.tsx` props.step | [child/components/StepCard.tsx](../frontend/src/child/components/StepCard.tsx) | Step renderer. K12 switches on `step.kind` to render text/song/joke; K9 adds click-to-read affordances. |
| `PersonaAvatar.tsx` props.personaId | [child/components/PersonaAvatar.tsx](../frontend/src/child/components/PersonaAvatar.tsx) | Maps persona id → avatar. K8 extends to map persona id → `VoiceProfile`. |
| `triggers/defaults.json` | [src/toybox/triggers/defaults.json](../src/toybox/triggers/defaults.json) | Shipped trigger patterns, version-merged into user file on load. K13 adds `request_song` + `request_joke` entries. |
| `transcript_retention_seconds` setting precedent | [core/transcript_retention.py](../src/toybox/core/transcript_retention.py) | Per-setting backend module pattern. K2's 8 flag modules mirror this. |
| `play_target_depth` / `play_cadence_seconds` setting precedent | [core/play_target_depth.py](../src/toybox/core/play_target_depth.py) (Phase J) | Per-setting backend module with canonical-set validation. Mirror for boolean parsing in K2. |
| `<PlayQueueSettingsControls>` / `<TranscriptRetentionControl>` | [SettingsPanel.tsx:35,524](../frontend/src/parent/components/SettingsPanel.tsx) | Dedicated sub-component pattern in SettingsPanel. K2 ships `<PlayFeaturesControls>` for the 8 new toggles. |
| migrations `0001`-`0013` | [db/migrations/](../src/toybox/db/migrations/) | Latest is `0013_labeled_events_redact_for_sft.sql` (Phase E3 carve-out). K1 migration is `0014`; K2 migration is `0015`. |

## 3. Scope

**In scope (V1):**

- Single source of truth for the role taxonomy + theme taxonomy + interjection-type taxonomy. Python `Role`, `Theme`, `InterjectionKind` StrEnums in `src/toybox/activities/roles.py` + `themes.py` + `interjections.py`. Drive: Pydantic fields, TS codegen, JSON schema validators, descriptor tables. **Tests assert `is`, not `==`** per [code-quality.md §2](../../.claude/rules/code-quality.md).
- 10 roles: Friend, Quest Giver, Guide / Mentor, Needs Saving, Boss / Mini-Boss, Big Bad Boss, Frenemy, Sidekick, Trickster, Helper / Townsperson.
- 12 themes: adventure, magic, space, animals, vehicles, food, friendship, pirates, knights, weather, music, silly.
- 4 interjection kinds (metadata): `embedded`, `ending`, `parent`, `spontaneity`.
- Migration `0014` adds `personas.role_weights TEXT` (default `'{}'`), `personas.voice_profile TEXT` (default `NULL`), `personas.spontaneity_rates TEXT` (default `'{"jokes":0.0,"songs":0.0}'`). Roles also carry default spontaneity rates baked into `roles.py` (no DB column needed — role taxonomy is code, not data).
- Migration `0015` seeds **8 boolean settings** (see §4 + §5).
- Template schema extension: each step's `text` may reference any `{role_name}` placeholder; templates declare top-level `required_roles: [...]`, `optional_roles: [...]`, `recommended_themes: [...]`, and an optional `ending_step: {kind: "song"|"joke", auto: true}`. Eligibility filter rejects templates whose `required_roles` count exceeds the available toy pool. Unfilled optional slots resolve to bundled generic descriptors ("a mysterious stranger", "a friendly villager") — descriptor table is part of K1's single-source module.
- Slot-fill engine extended in `content_resolver.py`: persona-weighted random pick per role, deterministic given `(seed, available_toys, persona, template_id)`. Tie-breaking by sorted toy id.
- Recast API: `POST /api/activities/{id}/recast` re-runs `resolve_role_slots` with a new seed, updates `slot_fills_json`, re-renders persisted `activity_steps.body` rows via existing `render_with_slot_fills`, increments version, emits `activity.state` envelope. **Only valid when activity is in `proposed` state** — endpoint returns 409 otherwise. Re-propose ("new activity") uses the existing `_do_propose` path.
- Parent suggestion card: role labels next to each cast toy; two re-roll buttons; conflict handling via existing `withConflictHandler`.
- Kiosk TTS: `frontend/src/child/tts.ts` wraps `speechSynthesis` with iOS-PWA gesture-unlock handling; per-persona `VoiceProfile` JSON pulled from persona library schema (new optional field `voice_profile: {rate, pitch, voice_name?}` in K1's migration).
- Click-to-read on the kiosk: **word-level** — tap a word → speak that word. **Step-level** — a watermarked "?" bubble (the "Read Me" button) sits in the bottom-left of every text-bearing step (kinds: text, fork, joke; NOT song); tap it → speak the step's main text. Visual hint on words (subtle underline on hover; brief highlight on tap). Read Me button is low-contrast watermark style so it doesn't distract from the step content but is discoverable. Hit-target ≥44pt per Apple HIG; aria-label "Read Me"; Tab-reachable.
- **8 household-scoped boolean feature flags** in `settings`. Defaults: all ON except `play_spontaneity_enabled` (default OFF — opt-in). Per-setting backend modules mirror Phase H/I/J convention. Single migration `0015` seeds all 8 via `INSERT OR IGNORE`. Frontend propagation: kiosk App.tsx fetches the flags on bootstrap and threads them via props down to consumers (no React context; matches Phase I/J's prop-drilling precedent).
- Joke corpus: `data/jokes/jokes.json` — ~50 entries shaped `{id, setup, punchline, theme: <one of 12>, optional_toy_slot: bool, age_band: "3-5"|"6-8"|"9-12", persona_compat: [...]}`. When `optional_toy_slot` is true, joke uses `{toy}` substitution if a toy is available in the activity context, else degrades to a toy-free reading. Validator gate on load; cached after first read.
- Song corpus: `data/songs/manifest.json` + bundled `.mp3` files under `data/songs/audio/`. Manifest entry `{id, title, audio_path, duration_seconds, theme: <one of 12>, age_band, persona_compat: [...], license, credit}`. License credits in `data/songs/_credits.md`.
- New step kinds: `kind: "song"` (with `song_id` or `auto: true` for engine-pick) and `kind: "joke"` (same shape). Kiosk audio player surfaces playback state via existing `activity.state` envelope's `current` flag.
- New standalone intents: `request_song` + `request_joke` triggers added to [src/toybox/triggers/defaults.json](../src/toybox/triggers/defaults.json) with monotonically-increasing pattern versions; generators select from corpus by age band / persona compat / seed; suggestion card surfaces them like any other activity. Gated on `(jokes_enabled OR songs_enabled) AND play_standalone_enabled`. When the surface is disabled, the trigger phrase classifies but the generator returns no activity (`HTTP 200` with `{state: "dismissed", reason: "surface_disabled"}` payload, kept consistent with the no-eligible-template flow today).
- Theme-tagged embedded surface: when a template includes a step with `kind: "song"|"joke"` and `auto: true`, the engine (at activity-creation OR advance time) picks a corpus entry whose `theme ∈ recommended_themes`. Gated on `<content_master> AND play_embedded_enabled`. When the surface is disabled, the step silently auto-skips (kid never sees a "skipped" indicator — UX is "step doesn't exist").
- Endings surface: when a template includes `ending_step: {...}` and the surface is enabled, the engine appends one themed interjection step after the template's last step at activity-creation time. Step metadata `interjection: "ending"`.
- Parent-inserted surface: two new endpoints `POST /api/activities/{id}/insert-joke` and `POST /api/activities/{id}/insert-song`, both honoring `If-Match-Version`. Insert a themed interjection at `current_step+1` position with metadata `interjection: "parent"`. Valid in `running` and `paused` states only — rejected for `proposed` (parent would just dismiss + re-propose). New parent ActivityPanel sidebar component with two icon buttons. Each button is greyed out when its content master is OFF. Logs to `labeled_events` with `source: "parent_insert"` for the learning loop.
- Spontaneity surface: each persona and each role carries `spontaneity_rates: {jokes, songs}` (both 0.0-1.0). On advance, engine computes per-content-type `effective_rate = max(persona.rate, max(role.rate for role in cast))`. Rolls combined: `r = random(advance_seed)`; if `r < effective_jokes` AND jokes-enabled AND surface-enabled → insert joke; elif `r < effective_jokes + effective_songs` AND songs-enabled AND surface-enabled → insert song; else no interjection (caps total at sum, prevents double-fire same advance). **Attribution:** the participant whose rate matched the chosen content type wins narration (display_name shown as speaker); ties broken by sorted toy_id then persona last. Pointer to template position NOT advanced.
- Catalog backfill: all 200 templates in [src/toybox/activities/templates/branching/](../src/toybox/activities/templates/branching/) rewritten to declare `required_roles` + `recommended_themes` + reference role placeholders in step text + (where the soak agent decides appropriate) an `ending_step`. Soak runs after the engine is stable (K16, after K1-K15 land) — same overnight 4-agent pattern Phase G used.
- Smoke gate: propose → recast → approve → kiosk plays activity with persona voice + an embedded song + an embedded joke + click-to-read on word and Read Me + parent inserts a joke mid-activity + 8 settings toggles round-trip.
- iPad operator UAT (K18 / M1): verifies iOS Safari PWA Web Speech gesture-unlock works, `.mp3` plays, click-to-read responds, all 8 feature flags toggle correctly, parent-insert + spontaneity surfaces behave as specified.

**Out of scope (V1 — recorded as V2 ideas in §11):**

- Recast on `running` / `paused` activities (v1 is `proposed`-only; v2 toggle planned — see §11).
- Live propagation of settings changes to in-flight kiosk session (v1 = next-bootstrap; v2 = build a `settings.changed` ws envelope).
- Hard persona × role bans (v1 ships soft scoring only).
- Static per-toy role assignment in toy CRUD (v1 is dynamic-only).
- Claude-generated songs/jokes (v1 is corpus-only; deferred until eval data justifies the capability cost).
- Per-persona voice selection beyond `(rate, pitch)` — `voice_name` optional but no curated mapping; defaults to system default voice.
- Human-recorded song tracks (v1 ships Coqui TTS-generated `.mp3`s; v2 swaps in better vocals).
- Multi-language corpus (v1 is English-only).
- Word-level highlight-as-spoken sync animation during sentence playback.
- Click-to-read inside the parent app (v1 is kiosk-only).
- Picker UI for parent-insert (v1 is random pick from corpus; v2 lets parent choose specific joke/song from a sidebar list).

## 4. Impact analysis

| File / module | Nature of change |
|---|---|
| `src/toybox/activities/roles.py` | **NEW** — `Role` StrEnum (10 role names); `GENERIC_DESCRIPTORS: dict[Role, list[str]]` fallback table; `ROLE_DISPLAY_NAMES: dict[Role, str]` for parent UI labels; `DEFAULT_ROLE_SPONTANEITY_RATES: dict[Role, dict[str, float]]` per-role default rates (concrete values in §5). Imported by every role consumer. |
| `src/toybox/activities/themes.py` | **NEW** — `Theme` StrEnum (12 themes); `THEME_DISPLAY_NAMES: dict[Theme, str]` for parent UI / corpus authoring. |
| `src/toybox/activities/interjections.py` | **NEW** — `InterjectionKind` StrEnum (`embedded`, `ending`, `parent`, `spontaneity`); `INTERJECTION_DISPLAY_NAMES` for labeled_events / parent telemetry. |
| `src/toybox/db/migrations/0014_persona_roles_voice_spontaneity.sql` | **NEW** — adds `personas.role_weights TEXT NOT NULL DEFAULT '{}'`, `personas.voice_profile TEXT NULL`, `personas.spontaneity_rates TEXT NOT NULL DEFAULT '{"jokes":0.0,"songs":0.0}'` (each rate bounded 0.0-1.0, validated at API layer). Forward-only per invariant 10. |
| `src/toybox/db/migrations/0015_phase_k_feature_flags.sql` | **NEW** — seeds 8 settings rows: `jokes_enabled='true'`, `songs_enabled='true'`, `play_standalone_enabled='true'`, `play_embedded_enabled='true'`, `play_endings_enabled='true'`, `play_spontaneity_enabled='false'`, `clickable_words_enabled='true'`, `read_me_button_enabled='true'`. `INSERT OR IGNORE`. |
| `src/toybox/personas/library/_schema.json` | **EXTEND** — add optional `role_weights` (object, role-name keys → 0.0-2.0 floats), `voice_profile` (object: `rate` 0.5-2.0, `pitch` 0.0-2.0, optional `voice_name` string), `spontaneity_rates` (object: `jokes` 0.0-1.0, `songs` 0.0-1.0). All optional. |
| `src/toybox/personas/library/*.json` | **EDIT** — K1 ships default `role_weights` + `voice_profile` + `spontaneity_rates` for the 4 built-in personas (princess, wizard, detective, periodic_table). Concrete defaults in §5. |
| `src/toybox/activities/_schema.json` (template schema) | **EXTEND** — add `required_roles: [Role]`, `optional_roles: [Role]`, `recommended_themes: [Theme]` at template top level (all defaultable to `[]`); `ending_step: {kind, auto}|null`; allow `kind: "text"\|"song"\|"joke"\|"fork"` on steps; permit `{role_name}` placeholders in step `text`; allow `song_id`/`joke_id`/`auto:true` discriminator on song+joke steps. |
| `src/toybox/activities/generator.py` slot substitution | **EXTEND** — `_substitute` loop walks all role placeholders in addition to `{toy}` + `{slot}`. Backward-compat: `{toy}` still works for non-role-aware templates. |
| `src/toybox/activities/content_resolver.py` | **EXTEND** — add `resolve_role_slots(template, available_toys, persona, seed)` → `{role_name: ResolvedToy \| GenericDescriptor}`. Persona-weighted shuffle; deterministic. Cached per propose via existing ResolvedChildren seam. |
| `src/toybox/activities/_validator.py` | **EXTEND** — validate role placeholders ⊆ `required_roles ∪ optional_roles`; validate `required_roles` ≤ template's distinct-toy ceiling; validate `song`/`joke` step kinds reference corpus IDs or `auto: true`; validate `recommended_themes` entries are valid `Theme`s; validate `ending_step.kind` ∈ {song, joke}. |
| `src/toybox/activities/interjection.py` | **NEW** — pure helper. `build_interjection_step(interjection: InterjectionKind, corpus_entry, slot_fills) -> ActivityStep` with `metadata = {"interjection": interjection.value, "source_id": corpus_entry.id}`. Used by K14 (ending appender at creation + advance-time embedded picker) and K15 (parent + spontaneity insertion). One shared step-build path so all four surfaces produce byte-identical step shape. |
| `src/toybox/api/activities.py` | **EXTEND** — `ActivityResponse` adds `roles: dict[str, RoleAssignment]` + `cast_summary: str` + `interjection_pending: bool`. `RoleAssignment` is `{role_name, toy_id?, generic_descriptor?, display_name}`. Add `POST /api/activities/{id}/recast` (proposed-only, 409 otherwise; re-runs `resolve_role_slots`, mutates slot_fills_json + re-renders persisted step bodies via `render_with_slot_fills`, version++). Add `POST /api/activities/{id}/insert-joke` + `POST /api/activities/{id}/insert-song` (running/paused only; insert interjection at current_step+1; version++). Extend advance handler with the spontaneity roll. |
| `src/toybox/triggers/defaults.json` | **EXTEND** — add patterns for `request_song` ("sing me a song", "play a song", "play a tune") + `request_joke` ("tell me a joke", "say a joke"). Bump file's overall pattern versions monotonically. |
| `src/toybox/activities/song_corpus.py` | **NEW** — corpus loader + validator + age-band/theme/persona-compat filter + seeded pick. |
| `src/toybox/activities/joke_corpus.py` | **NEW** — same shape as song corpus minus audio. Handles `optional_toy_slot` substitution in setup/punchline. |
| `data/songs/manifest.json` | **NEW** — array of `{id, title, audio_path, duration_seconds, theme, age_band, persona_compat, license, credit}`. |
| `data/songs/audio/*.mp3` | **NEW** — ~50 short pre-rendered tracks. Land in K11 via the operator-run Coqui script. |
| `data/songs/_credits.md` | **NEW** — license + source credits per track. |
| `data/jokes/jokes.json` | **NEW** — array of `{id, setup, punchline, theme, optional_toy_slot, age_band, persona_compat}`. |
| `scripts/generate_song_corpus.py` | **NEW** — one-shot CLI walking `data/songs/manifest.json`, running **Coqui TTS** (operator-installed; pinned to a specific TTS model version in the script docstring) to render each `audio_path`. Output committed to repo. Lives in `scripts/` so install-time runtime stays unchanged. |
| `src/toybox/core/jokes_enabled.py` | **NEW** — per-setting module (read/write helpers + canonical set `{'true','false'}` + default `'true'`). Mirrors `core/transcript_retention.py`. |
| `src/toybox/core/songs_enabled.py` | **NEW** — same shape. |
| `src/toybox/core/play_standalone_enabled.py` | **NEW** — same shape. |
| `src/toybox/core/play_embedded_enabled.py` | **NEW** — same shape. |
| `src/toybox/core/play_endings_enabled.py` | **NEW** — same shape. |
| `src/toybox/core/play_spontaneity_enabled.py` | **NEW** — same shape; default `'false'`. |
| `src/toybox/core/clickable_words_enabled.py` | **NEW** — same shape. |
| `src/toybox/core/read_me_button_enabled.py` | **NEW** — same shape. |
| `src/toybox/api/*_settings.py` (8 modules) | **NEW** — `GET / PUT /api/settings/<kebab-key>` for each of the 8 flags. Each mirrors `api/transcript_retention_settings.py`. |
| `frontend/src/shared/types.ts` | **AUTOGEN** — pydantic-to-typescript codegen re-runs (pre-commit hook). New types: `Role`, `RoleAssignment`, `Theme`, `InterjectionKind`, `VoiceProfile`, `SongStep`, `JokeStep`, `RecastResponse`, `InsertResponse`, 8 boolean setting types. |
| `frontend/src/child/tts.ts` | **NEW** — `speak(text: string, profile: VoiceProfile): Promise<void>`; `cancel()`; iOS-PWA gesture-unlock state. Vitest with `speechSynthesis` mock. |
| `frontend/src/child/persona-voice.ts` | **NEW** — `getVoiceProfile(personaId): VoiceProfile` reads from persona library. Default `{rate: 1.0, pitch: 1.0}` when persona has no profile. |
| `frontend/src/child/components/StepCard.tsx` | **EXTEND** — switch on `step.kind`: text/fork → existing render wrapped in `ClickableText` + `<ReadMeButton>` in bottom-left (gated on `read_me_button_enabled` prop); song → `SongPlayer` (no Read Me button); joke → setup line + 1.5s delayed punchline + Web Speech auto-play + ReadMeButton for re-play. Interjection metadata is invisible to the kid (no badge). |
| `frontend/src/child/components/ClickableText.tsx` | **NEW** — word-level `<span>`s with `onClick`. CSS visual hints. Render gated on `clickable_words_enabled` prop — when false, renders plain `<span>{text}</span>`. |
| `frontend/src/child/components/ReadMeButton.tsx` | **NEW** — watermarked "?" bubble bottom-left of step card. Absolute-positioned. Hit-target ≥44pt. CSS opacity ~0.6 baseline, full on hover/tap. aria-label "Read Me", Tab-reachable. Calls `tts.cancel()` then `tts.speak(stepText, profile)`. |
| `frontend/src/child/components/ChoiceButton.tsx` | **EXTEND** — render label via `ClickableText`. Word taps `stopPropagation` so they don't submit the choice. When `clickable_words_enabled` is false, choice behaves exactly as today. |
| `frontend/src/child/components/SongPlayer.tsx` | **NEW** — `<audio>` element with `playing/paused/done` state. Auto-play on step focus (after gesture unlock); next button enables on `onended`. |
| `frontend/src/child/App.tsx` | **EXTEND** — bootstrap fetches the 8 feature flags in parallel and threads each as a prop down through `StepCard` etc. **Prop drilling, not React context** — matches Phase I/J precedent. No `settings.changed` ws subscription (v1 contract; flag changes need refresh). |
| `frontend/src/parent/components/SuggestionCard.tsx` | **EXTEND** — render role list ("Quest Giver: Wise Owl") under the title; two re-roll buttons ("New cast" → `POST /api/activities/{id}/recast`, "New activity" → dismiss + `propose`). Both via `withConflictHandler`. Greyed out when activity is not `proposed`. |
| `frontend/src/parent/components/ActivityPanel.tsx` | **EXTEND** — add a sidebar control row with two icon buttons: "+ joke" / "+ song". Buttons call `POST /api/activities/{id}/insert-{joke,song}` via `withConflictHandler`. Each greyed out when its content master flag is OFF. Shown only when activity state ∈ {`running`, `paused`}. |
| `frontend/src/parent/components/PlayFeaturesControls.tsx` | **NEW** — dedicated SettingsPanel sub-component for the 8 toggle switches. Mirrors `PlayQueueSettingsControls.tsx` (Phase J). Toggles persist via per-setting endpoints with optimistic update + revert on failure. |
| `frontend/src/parent/components/SettingsPanel.tsx` | **EXTEND** — host `<PlayFeaturesControls>` below the existing `<PlayQueueSettingsControls>` and `<TranscriptRetentionControl>`. |
| `frontend/src/parent/api.ts` | **EXTEND** — `recastActivity(id, version)`, `insertJoke(id, version)`, `insertSong(id, version)`, and 8 getter/setter pairs for the new settings. |
| `frontend/src/parent/store.ts` | **EXTEND** — `applyMutationResult` already handles the new wire shape via version guard; no reducer change. Verify in K6 tests. |
| `documentation/plan.md` | **PRE-K1 HOUSEKEEPING** — add Phase J row (per memory: J1-J10 shipped, J11/J12 pending), add Phase E IN-FLIGHT row. K18/repo-update appends Phase K row. |
| `documentation/plan/activity-loop.md` | **MODIFY** — append "Phase K — roles, songs, jokes, voice, surfaces" section. |
| `documentation/plan/data-model.md` | **MODIFY** — document migrations `0014` + `0015`. |
| `tests/fixtures/personas/role_weighted.json` | **NEW** — deterministic-test fixture used by K5. Persona with explicit `role_weights` so the integration test is byte-stable across runs. |

## 5. Data model — migrations 0014 + 0015

```sql
-- 0014_persona_roles_voice_spontaneity.sql
ALTER TABLE personas ADD COLUMN role_weights TEXT NOT NULL DEFAULT '{}';
ALTER TABLE personas ADD COLUMN voice_profile TEXT NULL;
ALTER TABLE personas ADD COLUMN spontaneity_rates TEXT NOT NULL DEFAULT '{"jokes":0.0,"songs":0.0}';
-- role_weights: JSON object, role-name keys → 0.0-2.0 weight. Empty = uniform.
-- voice_profile: JSON {rate: 0.5-2.0, pitch: 0.0-2.0, voice_name?: str} or NULL = system default.
-- spontaneity_rates: JSON {jokes: 0.0-1.0, songs: 0.0-1.0}. 0.0/0.0 = never interject. Defaults at K1.
-- No backfill: K1 also edits each built-in persona JSON in src/toybox/personas/library/*.json.
-- Role-side spontaneity defaults live in src/toybox/activities/roles.py (DEFAULT_ROLE_SPONTANEITY_RATES).
```

```sql
-- 0015_phase_k_feature_flags.sql
INSERT OR IGNORE INTO settings (key, value) VALUES ('jokes_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('songs_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('play_standalone_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('play_embedded_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('play_endings_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('play_spontaneity_enabled', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('clickable_words_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('read_me_button_enabled', 'true');
-- Defaults: all true except play_spontaneity_enabled (opt-in — interjections disruptive).
```

No additions to `activities.slot_fills_json` (JSON column accepts new role-name keys without alter). No additions to `activity_steps` (interjection metadata fits in existing `metadata` JSON field). Confirm by reading [0008_activity_slot_fills.sql](../src/toybox/db/migrations/0008_activity_slot_fills.sql).

### Default persona attribute values (K1 ships these)

| Persona | role_weights | voice_profile | spontaneity_rates `{jokes, songs}` |
|---|---|---|---|
| princess | `{friend:1.5, sidekick:1.5, helper_townsperson:1.2, big_bad_boss:0.3}` | `{rate:1.0, pitch:1.4}` | `{0.05, 0.15}` |
| wizard | `{quest_giver:1.5, guide_mentor:1.5, big_bad_boss:1.2, frenemy:1.1}` | `{rate:0.9, pitch:0.7}` | `{0.10, 0.05}` |
| detective | `{quest_giver:1.3, helper_townsperson:1.2, frenemy:1.3, sidekick:1.0}` | `{rate:1.1, pitch:0.9}` | `{0.00, 0.00}` |
| periodic_table | `{guide_mentor:1.5, helper_townsperson:1.3, friend:1.0}` | `{rate:1.2, pitch:1.0}` | `{0.10, 0.00}` |
| (custom personas) | `{}` (uniform) | NULL (system default) | `{0.0, 0.0}` (never interject) |

Weights are relative — engine normalizes to a probability distribution per role-slot pick.

### Default role spontaneity rates (K1 ships these in `roles.py`)

| Role | jokes | songs | Why |
|---|---|---|---|
| Trickster | 0.30 | 0.10 | Maximally silly; jokes much more than songs |
| Frenemy | 0.20 | 0.05 | Sarcastic; occasional zinger |
| Sidekick | 0.15 | 0.15 | Cheerful, balanced |
| Needs Saving | 0.10 | 0.20 | Sings for help and celebration |
| Friend | 0.10 | 0.10 | Balanced companion |
| Boss / Mini-Boss | 0.10 | 0.00 | Taunts but never sings |
| Helper / Townsperson | 0.05 | 0.10 | Background flavor |
| Quest Giver | 0.05 | 0.10 | Mostly serious; ceremonial song occasionally |
| Big Bad Boss | 0.05 | 0.00 | Menacing; rare dark joke |
| Guide / Mentor | 0.05 | 0.05 | Dignified |

Engine logic at advance: `effective_jokes = max(persona.rate_jokes, max(role.rate_jokes for role in cast))`; same for songs. Combined roll `r = random(seed)`: if `r < effective_jokes` → joke; elif `r < effective_jokes + effective_songs` → song; else no interjection. Attribution = the participant whose rate matched the chosen content type (ties → sorted toy_id, persona last).

## 6. Build steps

Per [`plan-and-issue-flow.md`](../../.claude/rules/plan-and-issue-flow.md), each step below is dispatched by `/build-phase --plan documentation/phase-k-plan.md`. Issue numbers populated by `/repo-sync` after `/plan-review` + `/plan-wrap`.

### Build process (inline summary so a fresh-context model can execute)

`/build-phase --plan <path>` walks each `### Step KN: <title>` block sequentially. For each, it dispatches `/build-step` with the step's `**Type:**` value and `**Flags:**` line, posts progress to the GitHub issue listed in `**Issue:** #<num>` (populated by `/repo-sync`), and runs a quality gate (unit tests + lint + typecheck + reviewer pass) before advancing.

**`Type:` semantics** (selects reviewer + test surface):

| Type | What it triggers | Use for |
|---|---|---|
| `code` | Unit tests + lint + typecheck. Reviewers focus on correctness + style. No running server. | Pure-logic + data-shape + corpus-loader steps. |
| `runtime` | All of `code` plus a live server bring-up. Flags MUST include `--reviewers full --start-cmd "<backend cmd>" --url "<frontend url>"`. Reviewers exercise the UI. | Any step that touches API behavior, frontend rendering, or kiosk audio. |
| `full` | Alias for `runtime`. | Same as above. |
| `tdd` | TDD variant of `code` — failing tests first, then implementation. | Steps with clear test-first opportunity (pure functions, validators). |

**`/repo-sync`** runs once before the first `/build-phase` invocation. It reads each `### Step KN:` block, creates a GitHub issue with the Problem text + flags + type, and writes the issue number back into the `**Issue:** #` line. Re-run after any plan edit that changes step shape or numbering.

**Manual section** (here: K18 / M1). `/build-phase` does NOT dispatch these. They're operator-only — for a human to run at the end as the acceptance gate. Format follows [`plan-and-issue-flow.md`](../../.claude/rules/plan-and-issue-flow.md) §"Automated vs manual split": (1) copy-paste commands, (2) separate "what to look for" table, (3) explicit name (M1/M2/...) plus end-of-orchestration ask.

### Automated section

### Step K1: Role + theme + interjection taxonomies + persona migration

**Problem:** Establish single source of truth for the 10-role taxonomy, 12-theme taxonomy, 4-interjection-kind taxonomy, generic-descriptor fallback table, and per-role default `spontaneity_rates` (concrete values in §5). Migration 0014 adds persona columns (`role_weights`, `voice_profile`, `spontaneity_rates` JSON). Edit each of the 4 built-in persona JSONs at `src/toybox/personas/library/*.json` with the default attribute values in §5. Existing custom personas inherit `{}` role_weights, NULL voice, and `{jokes:0.0, songs:0.0}` rates.

**Type:** code

**Issue:** #114

**Flags:** `--reviewers code`

**Status:** DONE (2026-05-14)

### Step K2: Phase-K feature flags (8 settings)

**Problem:** Migration `0015_phase_k_feature_flags.sql` seeds 8 boolean settings (`jokes_enabled`, `songs_enabled`, `play_standalone_enabled`, `play_embedded_enabled`, `play_endings_enabled`, `play_spontaneity_enabled`, `clickable_words_enabled`, `read_me_button_enabled`), defaults per §5. Eight backend modules under `src/toybox/core/` follow the per-setting convention from Phase H/I/J. Eight API endpoints under `src/toybox/api/`. New `PlayFeaturesControls.tsx` parent component (mirrors `PlayQueueSettingsControls.tsx`) with 8 toggle switches. Frontend `api.ts` adds getters/setters. Kiosk `App.tsx` bootstrap fetches all 8 flags and threads via props (no React context).

**Type:** runtime

**Issue:** #115

**Flags:** `--reviewers full --start-cmd "uv run python -m toybox.main --host 127.0.0.1 --port 8000" --url "http://127.0.0.1:4000/parent"`

**Status:** DONE (2026-05-15)

### Step K3: Template schema extension + validator

**Problem:** Extend template JSON schema to support `required_roles: [Role]`, `optional_roles: [Role]`, `recommended_themes: [Theme]` top-level fields; allow optional `ending_step: {kind: "song"|"joke", auto: true}`; permit `kind: "text"|"song"|"joke"|"fork"` on steps; allow `{role_name}` placeholders in step `text`. Validator gates that placeholders ⊆ declared roles, `required_roles` count ≤ template's distinct-toy ceiling, `recommended_themes` entries are valid `Theme`s, `song`/`joke` step kinds reference corpus or `auto: true`, `ending_step.kind` ∈ {song, joke}. **Does not touch the 200 existing templates** — backward-compat path keeps `{toy}` and missing fields working as defaults.

**Type:** code

**Issue:** #116

**Flags:** `--reviewers code`

**Status:** DONE (2026-05-15)

### Step K4: Slot-fill engine extension

**Problem:** Extend `content_resolver.py` with `resolve_role_slots(template, available_toys, persona, seed)`. Persona `role_weights` bias the normalized random pick; deterministic given inputs; unfilled optional slots fall back to `GENERIC_DESCRIPTORS`. Skips templates whose `required_roles` count exceeds the toy pool.

**Type:** code

**Issue:** #117

**Flags:** `--reviewers code`

**Status:** DONE (2026-05-15)

### Step K5: Integration test through `_do_propose`

**Problem:** End-to-end test asserting a role-aware template flows through the production propose path: `POST /api/activities/propose` → `_do_propose` → resolved roles persisted in `activities.slot_fills_json` → step bodies rendered via `render_with_slot_fills` → `activity.state` envelope carries the cast. **Required by [`code-quality.md`](../../.claude/rules/code-quality.md) §4** — covers the silent-wiring failure mode (engine builds, never called). Uses pytest-asyncio with `tests/fixtures/personas/role_weighted.json` fixture so the test is byte-deterministic.

**Type:** code

**Issue:** #118

**Flags:** `--reviewers code`

**Status:** DONE (2026-05-15)

### Step K6: Recast API endpoint (proposed-only)

**Problem:** `POST /api/activities/{id}/recast` re-runs `resolve_role_slots` with a new seed, writes new `slot_fills_json`, re-renders the **already-persisted** `activity_steps.body` rows via existing `render_with_slot_fills(template_step_text, new_slot_fills)`, increments version, emits `activity.state` envelope. Honors `If-Match-Version`. **Returns 409 `{code: "recast_only_when_proposed"}` if activity state is not `proposed`**. Frontend `api.ts` gets `recastActivity(id, version)`. Store handles via existing `applyMutationResult` + `withConflictHandler`. (Note: v2 idea in §11 to allow recast on `running`/`paused` once the mid-activity re-render UX is designed.)

**Type:** code

**Issue:** #119

**Flags:** `--reviewers code`

**Status:** DONE (2026-05-15)

### Step K7: Suggestion card — role labels + two re-roll buttons

**Problem:** Parent suggestion card renders role assignments under the title ("Quest Giver: Wise Owl", "Hero: Captain Bear"). Two buttons: "New cast" calls `recastActivity`; "New activity" dismisses + calls `propose` with a fresh seed. Both wrap in `withConflictHandler`. Buttons greyed out when activity state ≠ `proposed`. Touch test via vitest; runtime smoke verifies the card renders + recast round-trips.

**Type:** runtime

**Issue:** #120

**Flags:** `--reviewers full --start-cmd "uv run python -m toybox.main --host 127.0.0.1 --port 8000" --url "http://127.0.0.1:4000/parent"`

**Status:** DONE (2026-05-15) — code reviewers + frontend gates; visual evidence deferred to K17 + K18

### Step K8: Kiosk TTS substrate + per-persona voice profile

**Problem:** `frontend/src/child/tts.ts` wraps `speechSynthesis` with iOS-PWA gesture-unlock state. First call inside a user gesture primes `unlocked = true`; subsequent calls work without gesture. `cancel()` interrupts in-flight speech. `getVoiceProfile(personaId)` reads from persona library JSON. Includes vitest mock-based tests for the unlock state machine. **No auto-narration on text-step focus** — text steps are silent until the kid taps something. Consumers in later steps: K9 (click-to-read), K12 (joke step auto-play).

**Type:** runtime

**Issue:** #121

**Flags:** `--reviewers full --start-cmd "uv run python -m toybox.main --host 127.0.0.1 --port 8000" --url "http://127.0.0.1:4000/child"`

**Status:** DONE (2026-05-15) — code reviewers + frontend gates; visual evidence deferred to K17 + K18

### Step K9: Click-to-read — word-level taps + Read Me button

**Problem:** Two new components, two distinct affordances, no gesture-disambiguation needed:

- **`ClickableText.tsx`** — wraps text in word-level `<span>`s. Tap a word → `tts.cancel()` then `tts.speak(word, profile)`. Visual hint via CSS (subtle hover underline + 200ms outline on tap). Render gated on `clickable_words_enabled` prop; when false, renders plain `<span>{text}</span>`. Applied to `StepCard` main text + `ChoiceButton` labels. Word taps in `ChoiceButton` call `stopPropagation` so they don't fire the choice's submit handler.
- **`ReadMeButton.tsx`** — watermarked "?" bubble positioned absolutely in the bottom-left of the step card container. Hit-target ≥44pt per Apple HIG. CSS opacity ~0.6 baseline, full opacity on hover/tap. aria-label "Read Me"; Tab-reachable. On tap: `tts.cancel()` then `tts.speak(stepText, profile)`. Render gated on `read_me_button_enabled` prop. Mounted by `StepCard` only on `kind ∈ {text, fork, joke}` — never on `song`.

**Type:** runtime

**Issue:** #122

**Flags:** `--reviewers full --start-cmd "uv run python -m toybox.main --host 127.0.0.1 --port 8000" --url "http://127.0.0.1:4000/child"`

**Status:** DONE (2026-05-15) — code reviewers + frontend gates; visual evidence deferred to K17 + K18

### Step K10: Joke corpus + loader + theme tagging

**Problem:** `data/jokes/jokes.json` ships with ~50 entries (`{id, setup, punchline, theme: <one of 12>, optional_toy_slot: bool, age_band, persona_compat}`). `src/toybox/activities/joke_corpus.py` loads + validates + filters by `(age_band, persona_compat, theme?)`; seeded pick with alphabetical tie-break. `optional_toy_slot: true` means joke uses `{toy}` substitution when a toy is available; degrades to literal text otherwise. TDD candidate — pure-function corpus picker is the perfect TDD shape.

**Type:** code

**Issue:** #123

**Flags:** `--reviewers code`

**Status:** DONE (2026-05-15)

### Step K11: Song corpus + bundled audio + theme tagging + Coqui render script

**Problem:** `data/songs/manifest.json` + `data/songs/audio/*.mp3` (~50 tracks, mono, ≤64 kbps, ≤25s). `src/toybox/activities/song_corpus.py` mirrors `joke_corpus.py`. **One-shot operator render**: `scripts/generate_song_corpus.py` uses **Coqui TTS** (chosen over Piper for higher voice quality at acceptable install footprint; operator-installed once via `pip install TTS`; NOT a runtime dep; specific model version pinned in the script docstring) to render audio files from the manifest. Output committed to repo (zero runtime impact — kiosk just plays the `.mp3`s). License credits in `data/songs/_credits.md`. Validator asserts every manifest `audio_path` exists at load time AND total `data/songs/audio/` size < 50 MB.

**Type:** code

**Issue:** #124

**Flags:** `--reviewers code`

**Status:** DONE (2026-05-15) — manifest + loader + script shipped; audio render is one-shot operator action (run `python scripts/generate_song_corpus.py` once after `pip install TTS`); 50MB total-audio assertion deferred to K17 smoke gate per §8 risk #6

### Step K12: New step kinds in kiosk — SongPlayer + joke step delivery

**Problem:** `StepCard.tsx` switches on `step.kind`. `kind: "song"` renders new `SongPlayer.tsx` (`<audio>` element with `playing/paused/done` state; next button enables on `onended`) — no Read Me button. `kind: "joke"` renders setup line, auto-plays via Web Speech, reveals punchline after a 1.5s pause + Web Speech delivery; Read Me button re-plays both lines on tap. Backward-compat: `kind: "text"` (default) renders as today plus K9 affordances. Existing `kind: "fork"` unchanged behavior plus K9 affordances. **When `songs_enabled` is false AND a song step is encountered, kiosk auto-advances silently to the next step** (no meta-message — interjection metadata makes the step transparent). Same for jokes.

**Type:** runtime

**Issue:** #125

**Flags:** `--reviewers full --start-cmd "uv run python -m toybox.main --host 127.0.0.1 --port 8000" --url "http://127.0.0.1:4000/child"`

**Status:** DONE (2026-05-15) — code reviewers + frontend gates; visual evidence deferred to K17 + K18

### Step K13: Standalone intents (Surface A) wired into trigger registry

**Problem:** Add `request_song` + `request_joke` patterns to `src/toybox/triggers/defaults.json` (bump pattern versions monotonically so version-merge picks them up on existing installs). Generators produce single-step activities from corpus. Parent suggestion card surfaces them like any other suggestion. Gated on `(<content_master> AND play_standalone_enabled)`. When surface or content disabled, `POST /api/activities/propose` returns `HTTP 200 {state: "dismissed", reason: "surface_disabled"}` and emits no `activity.state` envelope. Integration test asserts trigger phrase → propose → kiosk flow end-to-end with the surface flag both enabled and disabled.

**Type:** code

**Issue:** #126

**Flags:** `--reviewers code`

### Step K14: Embedded + endings surfaces (B + E)

**Problem:** Shared interjection-step builder `src/toybox/activities/interjection.py:build_interjection_step()` produces byte-identical step shape for embedded / ending picks. Embedded path: when an activity-step has `kind: "song"|"joke"` with `auto: true`, the engine (at the appropriate render time — creation for already-persisted step, advance for lazy steps) picks a corpus entry whose `theme ∈ template.recommended_themes`. Endings path: when a template has `ending_step` and the surface is on, the engine appends an interjection-marked step after the last template step at activity-creation time. Both gated on `<content_master> AND <surface_flag>`. When disabled, embedded steps silently skip; ending steps simply aren't appended.

**Type:** code

**Issue:** #127

**Flags:** `--reviewers code`

### Step K15: Active surfaces — parent-insert API + spontaneity advance-hook (P + S)

**Problem:** Two new endpoints `POST /api/activities/{id}/insert-joke` + `POST /api/activities/{id}/insert-song`, both honoring `If-Match-Version`. Allowed states: `running` or `paused` only (return 409 otherwise). Both call `build_interjection_step()` with `kind=parent` metadata, insert at `current_step+1` in `activity_steps`, version++, emit `activity.state` envelope, log `labeled_events` row with `source: "parent_insert"`. Spontaneity hook: in the activity advance handler, after computing the next template step but BEFORE returning to client, gated on `play_spontaneity_enabled`, compute `effective_jokes = max(persona.spontaneity_rates.jokes, max(role.spontaneity_rates.jokes for role in cast))` and same for songs. Roll `r = random(advance_seed)`: if `r < effective_jokes AND jokes_enabled` → joke interjection; elif `r < effective_jokes + effective_songs AND songs_enabled` → song interjection; else no interjection. Attribution: the participant whose rate matched the chosen content type wins narration (display_name shown as speaker; persona shown when persona drives; toy display_name shown when a cast role drives; ties broken by sorted toy_id then persona-last). Call `build_interjection_step()` with `kind=spontaneity` + attribution metadata, insert + emit + log. Template position pointer NOT advanced through interjection. Parent `ActivityPanel.tsx` gets a sidebar control row with two icon buttons ("+ joke", "+ song"); each greyed when its content master is off; shown only in `running`/`paused` states.

**Type:** runtime

**Issue:** #128

**Flags:** `--reviewers full --start-cmd "uv run python -m toybox.main --host 127.0.0.1 --port 8000" --url "http://127.0.0.1:4000/parent"`

### Step K16: Catalog backfill — 200 templates to role-aware + themed + ending steps (soak)

**Problem:** Overnight 4-agent soak (one agent per intent file: `boredom.json`, `request_play.json`, `request_story.json`, `request_activity.json` — 50 templates each). Each agent rewrites templates to declare `required_roles` + `recommended_themes` + use role placeholders + (where the agent decides appropriate) add `ending_step: {kind: "song"|"joke", auto: true}`. Each agent validates each template through `_validator.py`, reports a per-template pass/fail row. **Engine must be stable before this step** — any post-K5 schema change invalidates the soak output. Acceptance: 100% of templates pass `_validator.py`; manual spot-check on 5 per intent for narrative coherence; at least 60% of templates have an `ending_step` (engaging templates — rocket launch, pirate adventure, etc.; quiet story templates may not need one).

**Soak agent prompt seed** (inline so K16 is self-contained — adapted from Phase G's 2026-05-10 4-agent overnight soak; full prior prompt is in `documentation/plan/archive/phase-g-branching-gameplay.md` for reference):

> You are one of 4 parallel agents rewriting the 50 templates in `<intent>.json` from `{toy}`-only to role-aware + themed. For each template:
> 1. Read the existing template structure (text, fork, sfx, action_slot).
> 2. Determine `required_roles` (1-3 from the 10-role taxonomy in `src/toybox/activities/roles.py` — toys the story can't function without).
> 3. Determine `optional_roles` (0-4 from the same taxonomy — toys the story can use if available; engine fills with generic descriptors when missing).
> 4. Add `recommended_themes` (1-3 from the 12 themes in `src/toybox/activities/themes.py` — what genre/topic the story evokes; drives K14 embedded picker).
> 5. Replace `{toy}` placeholders in step text with role placeholders (`{quest_giver}`, `{hero}`, etc.); pick which role each `{toy}` mention represents based on narrative context.
> 6. Decide whether the template should have an `ending_step`:
>    - Engaging templates (rocket launch, pirate adventure, castle defense) — yes, with a celebratory song or joke matching the strongest `recommended_themes` entry.
>    - Quiet / story templates (a learning scene, a calm moment) — usually no.
>    - Target: ≥60% of templates across all 4 intents have an `ending_step`.
> 7. Run `python -m toybox.activities._validator <template_path>` and fix any errors.
> 8. Output: rewritten JSON inline + a one-line verdict per template (`OK` / `NEEDS REVIEW: <reason>`).
>
> Do NOT change step `text` semantics (story beats stay intact). Do NOT introduce new step `kind`s except `song` / `joke` for the `ending_step`. Do NOT modify existing fork structure (`choices`, `next` pointers). Do NOT touch sibling intents' JSON files.

**Type:** code

**Issue:** #129

**Flags:** `--reviewers code`

### Step K17: End-to-end smoke gate

**Problem:** Full-stack smoke: backend on 127.0.0.1:8000, kiosk on :4000. Sequence: (a) propose role-aware activity from backfilled catalog, (b) recast it pre-approval, (c) approve, (d) kiosk renders persona avatar with voice profile, (e) walk through steps including one embedded joke step + one embedded song step, (f) tap a word and the Read Me button on a text step, hear both, (g) parent inserts a joke mid-activity, kiosk shows it next, (h) finish through an ending song step, (i) toggle each of the 8 settings from SettingsPanel, refresh kiosk, verify behavior change. Acceptance: all 9 sub-steps green; no kiosk console errors; activity reaches `completed` state; `data/songs/audio/` size assertion passes.

**Type:** runtime

**Issue:** #130

**Flags:** `--reviewers full --start-cmd "uv run python -m toybox.main --host 127.0.0.1 --port 8000" --url "http://127.0.0.1:4000/child"`

### Manual section

### Step K18 (M1): iPad operator UAT

**Tracking issue:** #131 (manual-section step — `/build-phase` does not dispatch; issue exists for operator tracking + audit trail)

**Problem:** Operator-only step. Verifies on real iPad PWA hardware that:

1. iOS Safari `speechSynthesis` activates after first user gesture (open kiosk, tap a word or the Read Me button, hear it — silent before tap is expected).
2. `.mp3` audio plays through the kiosk persona — no autoplay blocker.
3. Click-to-read works on words AND the Read Me button independently; second tap interrupts the prior speech.
4. Re-roll round-trips: parent re-casts a proposed activity, kiosk receives the updated role assignments via ws.
5. Standalone `request_song` triggered via parent trigger button, audio plays on kiosk.
6. Standalone `request_joke` triggered, punchline reveal + TTS works.
7. Backfilled template proposes + runs with role cast + embedded joke + embedded song + ending step all displayed correctly.
8. Parent inserts a joke from ActivityPanel sidebar during a running activity; kid sees it as the next step.
9. With `play_spontaneity_enabled` toggled ON, Wizard persona active, **and at least one Trickster-cast toy in the activity** (effective_jokes = 0.30 from Trickster), run through 5 activities; at least one spontaneity interjection fires. Verify attribution — when the Trickster drives the rate, the kiosk shows the toy's display_name as speaker, not the persona.
10. Each of the 8 feature flags toggles from SettingsPanel and produces the expected kiosk behavior change after refresh.

**Prerequisites** (one-time per fresh checkout — do once before the commands below):

```powershell
# From c:\Users\abero\dev\toybox\
uv sync                          # backend deps
cd frontend; npm install; cd ..  # frontend deps
# Set the parent PIN (required by invariant 2 for LAN bind). The CLI for this was
# introduced in Phase D step 21; if no `toybox.auth.set_pin` module exists, set it
# via the parent UI's first-run flow (visit /parent on loopback before LAN bind).
# Set TOYBOX_LAN_IP to the host's LAN-routable IP — find via `ipconfig`. Example:
$env:TOYBOX_LAN_IP = "192.168.1.42"
```

**Operator commands** (run from `c:\Users\abero\dev\toybox\` each session):

```powershell
uv run python -m toybox.db.migrate
uv run python -m toybox.main --host 0.0.0.0 --port 8000  # requires parent PIN already set
cd frontend; npm run dev  # serves :4000
# Open http://$env:TOYBOX_LAN_IP:4000/parent on desktop
# Open http://$env:TOYBOX_LAN_IP:4000/child on iPad
```

**What to look for** (separate from commands per [`plan-and-issue-flow.md`](../../.claude/rules/plan-and-issue-flow.md)):

| Check | Pass condition |
|---|---|
| iOS gesture unlock | First word or Read Me tap produces audio. Subsequent click-to-read works without re-tap. |
| Song audio playback | `.mp3` plays through to end; next button enables on done. |
| Joke reveal | Punchline shows ~1.5s after setup; TTS plays setup then punchline in order. |
| Re-roll round-trip | Parent presses "New cast" on `proposed` → roles update on suggestion card. Buttons greyed once approved. |
| Click-to-read isolation | Tapping one word interrupts the prior. Tapping Read Me interrupts a mid-word speak. |
| Read Me button position | Watermarked "?" visible bottom-left of text/fork/joke step cards; absent from song step cards; subtle styling. |
| Backfilled template | Role labels display in suggestion card; kiosk step text substitutes correctly; ending song plays if template has one. |
| Parent insert | Sidebar buttons appear on running ActivityPanel; tapping inserts the chosen interjection as the next step on the kiosk. |
| Spontaneity (when enabled) | At least one interjection observed across 5 activities with Wizard persona + Trickster-cast toy. Speaker attribution matches the participant whose rate drove the roll. |
| Feature flag toggles | All 8 toggles produce the expected behavior change on kiosk refresh (refresh required — no live propagation in v1). |

Please run M1 after K17 reports green.

## 7. API contracts

All Phase K endpoints accept JSON, return JSON, and (where noted) honor the `If-Match-Version` header described in §2. Frontend wrappers use `withConflictHandler` to retry on 409.

### New activity-mutation endpoints

| Method + Path | Headers | Request body | Response (200) | Other status codes |
|---|---|---|---|---|
| `POST /api/activities/{id}/recast` | `If-Match-Version: <int>` (required) | `{}` (empty; server picks fresh seed) | full `ActivityResponse` with new `roles`, re-rendered `steps[].body`, `version += 1` | 404 (no such id); 409 `{code: "recast_only_when_proposed"}` if state ≠ `proposed`; 409 `{code: "version_conflict", current_version, current: ActivityResponse}` if version mismatch |
| `POST /api/activities/{id}/insert-joke` | `If-Match-Version: <int>` (required) | `{}` (empty; server picks themed corpus joke from `data/jokes/jokes.json`) | full `ActivityResponse` with a new `activity_steps` row at `current_step+1` (with `metadata: {"interjection": "parent", "source_id": <joke_id>}`), `version += 1` | 404; 409 `{code: "insert_only_when_running_or_paused"}` if state ∉ {`running`, `paused`}; 409 version conflict (same shape as above) |
| `POST /api/activities/{id}/insert-song` | same as insert-joke | same | same (with `source_id: <song_id>`) | same |
| `POST /api/activities/propose` (Phase K extension) | none | existing `ProposeRequest` (unchanged) | `ActivityResponse` (normal path) OR `{state: "dismissed", reason: "surface_disabled", id: <UUIDv4>, version: 1}` when `intent ∈ {request_song, request_joke}` AND the relevant surface flag or content master is off (no `activity.state` ws envelope emitted on dismissed path) | unchanged from pre-K |

Side effects of `insert-{joke,song}`: server writes a `labeled_events` row with `source: "parent_insert"` for the learning loop. Side effect of the spontaneity advance hook (no endpoint — fires inside the existing advance handler): identical step shape via `build_interjection_step`, `source: "spontaneity"` in `labeled_events`.

### Settings endpoints (eight pairs, same shape per pair)

All eight settings share identical request/response shape; only the key differs.

| Method + Path | Request body | Response (200) | Status codes |
|---|---|---|---|
| `GET /api/settings/<kebab-key>` | none | `{value: true}` or `{value: false}` | 200; 404 if key not in the canonical 8 |
| `PUT /api/settings/<kebab-key>` | `{value: <bool>}` | `{value: <bool>}` (echo) | 200; 400 if body invalid; 422 if value is not a bool |

The eight kebab keys: `jokes-enabled`, `songs-enabled`, `play-standalone-enabled`, `play-embedded-enabled`, `play-endings-enabled`, `play-spontaneity-enabled`, `clickable-words-enabled`, `read-me-button-enabled`. Settings have no version field, so no `If-Match-Version`. Auth: parent-scope token required for PUT (same precedent as Phase H/I/J).

### Wire-shape extensions to `ActivityResponse`

`ActivityResponse`'s existing fields are summarized in §2. Phase K adds three:

| Field | Type | When set |
|---|---|---|
| `roles` | `dict[str, RoleAssignment]` | Always present; `{}` for role-less activities |
| `cast_summary` | `string` | One-line "Quest Giver: Wise Owl, Hero: Captain Bear" rendered for parent UI |
| `interjection_pending` | `bool` | True when the most recent advance returned a spontaneity step; false otherwise |

`RoleAssignment` defined in §2.

## 8. Risks

1. **Single source of truth for role + theme + interjection names ([code-quality.md §2](../../.claude/rules/code-quality.md)).** Three taxonomies × multiple consumers each. **Mitigation:** K1 establishes 3 separate StrEnum modules (`roles.py`, `themes.py`, `interjections.py`). Tests assert `is`, not `==`. Pydantic-to-TS codegen carries to frontend. Any "let's just hardcode this string" comment in review is a yellow card.

2. **Producer-consumer grep on slot syntax change ([code-quality.md §1](../../.claude/rules/code-quality.md)).** Substitution grammar extends from `{toy}` to also accept `{role_name}` for 10 roles. Known consumers: `generator.py:_substitute`, `content_resolver.py` slot resolver, `_validator.py` placeholder checker, kiosk step renderer, image-gen action sprite lookup. **Mitigation:** K3 includes a grep-attachment for each call site with a pass/fail verdict.

3. **Integration test through `_do_propose` ([code-quality.md §4](../../.claude/rules/code-quality.md)).** Role engine + interjection builder are both silently-wiring-prone. **Mitigation:** K5 (role engine) and K15's integration test (interjection builders called from advance handler + insert endpoints) both required. K13 also integration-tests standalone intent → propose path.

4. **iOS Safari PWA Web Speech quirks.** `speechSynthesis.speak()` outside user gesture is silently no-op on iOS. Voice list takes ~200ms to populate on first load. **Mitigation:** K8 implements the unlock state machine; K18 verifies on real iPad. If unlock fails on iPad: fall back to a "tap to enable narration" prompt on first kiosk load.

5. **K16 soak destabilization risk.** Per [Phase G postmortem](../runs/), the 4-agent backfill pattern relies on a stable engine — any schema change after the soak starts invalidates output. **Mitigation:** K16 explicitly blocks on K1-K15 landing green. If a K1-K15 hotfix is needed during/after the soak, re-soak.

6. **Bundled audio repo size.** ~50 `.mp3` files × ~150 KB each at 64 kbps mono = ~7.5 MB. Manageable; if it grows past 50 MB across phases, move `data/songs/audio/` to a release asset pack downloaded by `scripts/fetch_assets.py`. **Mitigation:** K11 enforces mono+64 kbps+≤25s; K17 smoke gate asserts total size <50 MB.

7. **Persona × role weight tuning.** Defaults in §5 are seed values. They will need iteration based on what activities feel right in family use. **Mitigation:** K1 ships the defaults; K18 UAT spot-checks tone (do Princess activities feel Princess-y?); tuning is operator-driven and lives in `personas/library/*.json` per the existing per-persona file pattern.

8. **External-content prompt-injection ([security.md](../../.claude/rules/security.md)).** Corpus files are bundled in-repo and reviewed at PR time. **Mitigation:** loaders treat corpus content as data, not instructions; validator rejects entries containing `<system-reminder>` or "ignore prior instructions" as defense-in-depth.

9. **Feature flag refresh latency.** Kiosk reads flags on bootstrap (v1 contract). A parent toggling a flag mid-session must refresh the kiosk for it to take effect. **Mitigation:** documented in K18 UAT explicitly; live propagation is a v2 idea (would require building a `settings.changed` ws envelope — does not exist today).

10. **Phase J pending UAT (J11/J12) coupling.** Phase K's SettingsPanel additions sit alongside Phase J's `<PlayQueueSettingsControls>`. If J11/J12 surface a defect in the per-setting precedent, Phase K may need to absorb the fix. **Mitigation:** recommend J11 + J12 operator UAT runs before K1 kicks off. Otherwise document any J-side defect found mid-K and reconcile.

11. **Phase E concurrent merge surface.** Phase E touches [activities.py:1199-1203](../src/toybox/api/activities.py#L1199-L1203) (env-var dispatch in `_do_propose`); Phase K's recast + insert endpoints + spontaneity hook also touch `activities.py`. **Mitigation:** sequence Phase K1 to start after the next Phase E checkpoint merges to master; or absorb merge-resolution work in K6 + K15 + plan acknowledges the risk.

12. **Spontaneity validation flakiness.** With Wizard `{jokes:0.10}` + a Trickster toy in cast `{jokes:0.30}` → effective_jokes = 0.30. Over 5-advance activity ≈ 83% per activity ≈ 99.9% across 5 activities. Without a Trickster, effective rate drops to persona's. UAT step 9 needs a guaranteed Trickster in cast. **Mitigation:** K18 step 9 explicitly seeds a Trickster-cast activity (parent's toy library must include at least one toy; UAT prerequisites list this); if zero fires after 5 activities with Trickster in cast, surface the rate-multiplication bug rather than declaring pass.

## 9. Test plan

| Layer | Tests |
|---|---|
| Unit (pytest) | `roles.py` + `themes.py` + `interjections.py` enums; `content_resolver.resolve_role_slots` determinism; `_validator` placeholder/role/theme/ending checks; song_corpus + joke_corpus loaders with theme filtering; `build_interjection_step` byte-identity across surfaces; recast endpoint pure logic; spontaneity roll math; each of the 8 feature-flag setting modules. |
| Integration (pytest) | K5's `_do_propose` round-trip; recast endpoint `If-Match-Version` + proposed-only enforcement (409 on running); insert-joke/insert-song endpoints + running/paused state enforcement; spontaneity hook fires on advance under controlled seed; standalone intent generators wired into propose with both flag states; each of the 8 settings GET/PUT endpoints + value validation; ending appender attaches step at creation; embedded auto:true picks themed corpus entry deterministically. |
| Frontend unit (vitest) | `tts.ts` unlock state machine; `ClickableText` word click + flag gating; `ReadMeButton` render + click + flag gating; `SuggestionCard` role rendering + button enable/disable by state; `SongPlayer` audio state; `StepCard` kind dispatch; `PlayFeaturesControls` 8-toggle wiring with optimistic update + revert on failure; `ActivityPanel` sidebar insert-buttons + greyed-out logic. |
| Frontend smoke (Playwright) | Existing kiosk smoke + new: tap word → assert `speechSynthesis.speak` called; tap Read Me → assert full step text; play song step → assert audio transitions to `playing`; parent-insert button → assert ws envelope received + kiosk renders next step as the interjection; toggle each of the 8 flags + assert behavior change after refresh. |
| Soak (K16) | Per-template validator + manual spot-check sample (5 per intent). |
| Smoke gate (K17) | Full backend + frontend; scripted clickthrough of 9 sub-steps including all 8 flag toggles + parent insert + ending step. |
| iPad UAT (K18) | Operator-driven, real hardware, 10 checks. |

Type + lint + format gates: `uv run mypy src`, `uv run ruff check .`, `uv run ruff format --check .`, `cd frontend; npm run typecheck; npm run lint`. All steps that change code must pass before merge.

## 10. Code-quality rule mapping

Explicit map of which [`code-quality.md`](../../.claude/rules/code-quality.md) rules apply to which step:

| Rule | Step(s) that satisfy it |
|---|---|
| §1 Grep all downstream consumers when changing a key/id shape | K3 (slot syntax change), K16 (backfill = explicit consumer audit across all 200 templates) |
| §2 One source of truth for data-shape constants | K1 (`Role` + `Theme` + `InterjectionKind` StrEnums + descriptor tables in dedicated modules); K2 (each of 8 feature flags has a single per-setting module — no duplicate `'true'/'false'` parsing across endpoint and consumer); K14 (`build_interjection_step` is the one source of truth for interjection-step shape, called by every surface) |
| §3 Audit wire shape when storage representation changes | K6 (recast updates persisted step bodies via `render_with_slot_fills`), K7 (suggestion card consumes new `roles` field), K2 (settings table seed), K15 (insert endpoints add new step rows + advance handler emits new envelope shape) |
| §4 New components require an integration test through the production caller | K5 (`_do_propose` integration test for role engine), K13 (standalone intents wired through trigger registry + propose path), K15 (advance handler spontaneity hook + insert endpoints integration-tested through full activity flow) |

## 11. V2 ideas (out of scope, captured for the followup phase)

- Extend recast to `running` / `paused` activities — requires mid-activity re-render UX design (does kid see step text change while looking at it? jarring or fine?). Flagged as a deliberate v2 deferral.
- Live propagation of settings changes: build a `settings.changed` ws envelope so kiosk gets new flag values without refresh.
- Hard persona × role bans + UI to author per-persona role allowlists.
- Static per-toy role assignment in toy CRUD (parent: "this bear is always the Hero").
- Claude-generated songs/jokes when eval data shows corpus monotony.
- Human-recorded song tracks (parent-recorded family voices; persona-voiced studio takes).
- Per-persona curated voice mapping (Wizard → specific iOS voice name when available).
- Multi-language corpus + persona language switching.
- Word-level highlight-as-spoken sync animation during sentence playback.
- Click-to-read in the parent app.
- Recast affordance on the kiosk itself.
- Joke/song favoriting + repeat-suppression on the next play.
- Read Me button reads choice labels too (v1 reads only main step text; v2 could chain main + choices).
- Parent-insert picker UI: parent chooses a specific song/joke from a sidebar list, not just random.
- Theme tuning UI: parent sees engagement stats per theme, can disable themes they don't want.
- Per-toy spontaneity override: toy CRUD adds a `spontaneity_modifier: float (0.0-2.0)` field that multiplies the role's default rates. Lets parents say "this Captain Bear is especially silly even though he's a Hero" without inventing a new role.
- Per-persona / per-role spontaneity dial UI: SettingsPanel surfaces the rates so parents can tune without editing JSON. V1 ships defaults; V2 exposes the dial.

## 12. Status table row for plan.md

Draft entry to append to the Status table in `documentation/plan.md` once K18 is operator-passed:

| **K** — roles, songs, jokes, voice | toy role taxonomy + slot-fill engine + proposed-only recast; song + joke corpora across 5 delivery surfaces (standalone, theme-tagged embedded, endings, parent-inserted, persona spontaneity); click-to-read on kiosk (word taps + Read Me button); 8 parent feature flags; 200 existing templates backfilled via overnight 4-agent soak | (IN FLIGHT once K1 starts; ✅ COMPLETE on K18 pass) |
