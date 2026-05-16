# Phase L — Rewards system; jokes/songs become reward types (feature plan)

## 1. What this feature does

Phase L re-frames Phase K's joke and song delivery model and adds a new picture-reward content type.

1. **Rewards as a first-class content type.** Parents upload images (treasure chest, jewel, star, balloon, …) into a new "Rewards" section under "Kids & Toyboxes". Each reward carries a display name, free-form tags, one fixed animation (Shine / Jump / Spin / Pulse / Wobble / Float), and an active flag. UI and storage clone the existing toys pattern — one image per reward (no sprite grid), simpler than [`ToyActionGrid`](../frontend/src/parent/components/ToyActionGrid.tsx).

2. **Reward TYPE per activity.** Every activity carries a `reward_type ∈ {picture, joke, song, random}` chosen by the parent on the suggestion card before approve. Default is `random`. The dropdown lives next to the existing approve button. Once the activity advances past its last regular step, the server resolves a concrete reward (picture row, joke row, or song row) using the activity's tags vs. the reward's tags via fuzzy overlap, and appends a `kind: "reward"` step. The kiosk renders it with the configured animation and auto-advances on dismiss/timeout.

3. **Fall-back chain.** If the chosen reward type has nothing eligible to fire (no active picture rewards uploaded, or `jokes_enabled=false`, or empty pool after tag-match), fall through `picture → joke → song → no reward`. Never blocks activity completion.

4. **Themed match via free-form tags.** Rewards carry a free-form `tags: list[str]` field (parent types comma-separated; server lowercases + strips + NFKC-normalizes + dedupes; max 24 chars per tag, max 10 tags per reward, empty-after-strip dropped). Match logic: lowercase-normalized **set-intersection** between `activity_themes` (the union of the activity's template `recommended_themes` and the output of the existing `extract_themes()` over the activity's recent transcripts — see [`src/toybox/api/activities.py:~2128`](../src/toybox/api/activities.py)) and `reward.tags`. Ordering: overlap count DESC, then `last_used_at` ASC NULLS FIRST. Empty intersection falls back to uniform random over the type pool. Role names are NOT in the match input — different vocabulary.

5. **Removed surfaces.** Phase K's `embedded`, `ending`, and `spontaneity` interjection kinds are removed: `embedded` and `spontaneity` because operators reported jokes/songs landing mid-activity feel like non-sequiturs; `ending` because the new reward step replaces it cleanly. Their parent toggles (`play_embedded_enabled`, `play_endings_enabled`, `play_spontaneity_enabled`) are deleted from the settings table; their backend modules + API endpoints + tests are deleted.

6. **Kept surfaces.** `play_standalone_enabled` (parent uses "Tell me a joke" / "Sing me a song" trigger phrases to queue a single-step activity) stays — it's a parent-controlled standalone, not an interjection. Parent-insert (`POST /api/activities/{id}/insert-{joke,song}`) stays — no toggle, manual control. `jokes_enabled` + `songs_enabled` master toggles move from the "Play features" section of [`SettingsPanel`](../frontend/src/parent/components/SettingsPanel.tsx) into the new Rewards section header (they gate eligibility as reward types AND eligibility for the kept surfaces).

**Why now.** Phase K UAT (run doc [`runs/2026-05-16-phase-k-uat.md`](runs/2026-05-16-phase-k-uat.md)) flagged a real-corpus collision and several "this joke felt random" operator notes. Re-framing jokes/songs as explicit per-activity rewards trades emergent surprise for predictable celebration and unlocks picture-rewards as a new modality. The simplification (3 surface flags + their plumbing deleted) more than offsets the new code volume.

## 2. Existing context

### Glossary

| Term | Definition |
|---|---|
| **Kiosk** | The `/child` route, run as an installed PWA on iPad in the kid's room. Renders one step at a time with a persona avatar. |
| **Persona** | The kiosk's animated presenter (Wizard, Detective, Princess, Periodic Table, …). Each persona is a JSON file at [`src/toybox/personas/library/<id>.json`](../src/toybox/personas/library/) carrying `display_name, archetype, system_prompt, avatar_image_path, role_weights, voice_profile, spontaneity_rates`. |
| **Suggestion card** | The proposed-activity card the parent approves; component at [`SuggestionCard.tsx`](../frontend/src/parent/components/SuggestionCard.tsx). |
| **ActivityPanel** | The running-activity surface in the parent UI ([`ActivityPanel.tsx`](../frontend/src/parent/components/ActivityPanel.tsx)) — shows current step + sidebar buttons for parent-insert. |
| **Trigger phrase** | An utterance Whisper STT picks up and maps to an intent. User-editable patterns at [`data/triggers.json`](../data/triggers.json); shipped defaults at [`src/toybox/triggers/defaults.json`](../src/toybox/triggers/defaults.json); registry at [`src/toybox/triggers/registry.py`](../src/toybox/triggers/registry.py). `request_joke` + `request_song` are hardcoded standalone intents at `_STANDALONE_JOKE_INTENT` / `_STANDALONE_SONG_INTENT` in [`api/activities.py`](../src/toybox/api/activities.py). |
| **Standalone activity** | A single-step activity proposed via `request_joke` / `request_song` triggers; flows through normal propose → approve → play. |
| **PWA** | Progressive Web App — iPad-installable web app. |
| **STT / TTS** | Speech-To-Text (Whisper) / Text-To-Speech (browser `speechSynthesis` for click-to-read; Coqui TTS for pre-rendered song MP3s). |
| **ws envelope** | WebSocket message wrapping `{topic, payload}` published via `pubsub.publish(build_envelope(...))`. |
| **NFKC** | Unicode Normalization Form KC — Python `unicodedata.normalize("NFKC", s)`. |
| **PIN** | 4–12 digit parent PIN; argon2-cffi hash stored in `settings` table under key `parent_pin_hash` (constants in [`src/toybox/core/pin.py`](../src/toybox/core/pin.py)). Required when binding non-loopback (invariant 2). |
| **Corpus** | A bundle of pre-rendered content (jokes JSON, song manifest + MP3s) shipped with the project; loaded into memory on demand. |

### Phase K context (what Phase L modifies)

Phase K (shipped 2026-05-16) layered four play features on top of toybox:

1. **Roles** — templates declare `required_roles` / `optional_roles`; the slot-fill engine assigns toys to roles at propose time. Cast is part of the suggestion card.
2. **Themes** — a 12-theme taxonomy (`adventure / magic / space / animals / vehicles / food / friendship / pirates / knights / weather / music / silly`) tagged on songs, jokes, and template `recommended_themes`.
3. **Jokes + songs corpora** — bundled JSON + MP3s with five delivery surfaces (A standalone / B embedded / E ending / P parent-insert / S spontaneity).
4. **Click-to-read kiosk TTS** — tap-a-word + "Read Me" button.

Phase L touches Phase K's surface model and the reward/celebration step:
- **Deletes** the `embedded`, `ending`, `spontaneity` surfaces + their toggles + their generator/advance hooks.
- **Keeps** `standalone` + `parent-insert` unchanged.
- **Adds** picture-rewards as a new content type and a per-activity `reward_type` that drives an end-of-activity reward step (replaces the deleted "ending" surface with a richer model).

Themes, roles, and the corpora themselves are unchanged — Phase L re-uses `recommended_themes` and the joke/song pickers as inputs to the reward resolver.

### Reference

**Activities table** ([`src/toybox/db/migrations/0001_initial.sql:68-82`](../src/toybox/db/migrations/0001_initial.sql#L68-L82) + [`0008_activity_slot_fills.sql:38`](../src/toybox/db/migrations/0008_activity_slot_fills.sql#L38)) has no `metadata` JSON column — runtime `metadata` is a Pydantic-only field in [`activities/models.py:291`](../src/toybox/activities/models.py#L291). Phase L adds a `reward_type TEXT` column directly (no JSON envelope).

**Activity step kinds.** Pre-L: `"text" | "fork" | "song" | "joke"` ([`activities/models.py`](../src/toybox/activities/models.py), see Phase K §2 wire shape). Phase L adds `"reward"`.

**Interjection kinds.** Pre-L: `embedded | ending | parent | spontaneity` ([`activities/interjections.py:29-47`](../src/toybox/activities/interjections.py#L29-L47)). Post-L: `parent` only. Step `metadata.interjection` field stays so already-completed activities in history still parse.

**ApproveRequest** ([`api/activities.py:410-413`](../src/toybox/api/activities.py#L410-L413)) currently accepts `child_ids: list[str] | None = None`. Phase L extends it with `reward_type: Literal["picture", "joke", "song", "random"] | None = None` (omitted → server defaults to `"random"`).

**Toys ingest pattern to clone** (the spine of L7 / L8):
- API: [`src/toybox/api/toys.py:80-82`](../src/toybox/api/toys.py#L80-L82) (router `/api/toys`, endpoints `upload | confirm | list | get | patch | delete`)
- Pydantic shapes: [`api/toys.py:136-156`](../src/toybox/api/toys.py#L136-L156) (`ToyResponse`), [`224-268`](../src/toybox/api/toys.py#L224-L268) (`ToyConfirmRequest`)
- UI: [`frontend/src/parent/components/ToyIngest.tsx`](../frontend/src/parent/components/ToyIngest.tsx)
- Image storage: `data/images/toys/<id>.<ext>`, staging at `data/images/.staging/`. Reward equivalent: `data/images/rewards/<id>.<ext>`.

**`ActivityResponse` full wire shape** ([`api/activities.py:313-372`](../src/toybox/api/activities.py#L313-L372)) — the shape `/api/activities/{id}/approve` and `/advance` return:

```python
class ActivityResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    state: str                                          # proposed | approved | running | paused | completed | dismissed | didnt_work | ended
    version: int = Field(ge=1)
    title: str | None = None
    summary: str | None = None
    persona_id: str | None = None
    intent_source: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    toy_ids: list[str] = Field(default_factory=list)
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    steps: list[ActivityStepResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trigger_phrase: str | None = None
    persona_reasoning: str | None = None
    roles: dict[str, RoleAssignment] = Field(default_factory=dict)
    cast_summary: str = ""
    interjection_pending: bool = False
    reason: str | None = None
    # Phase L adds:
    reward_type: Literal["picture","joke","song","random"] | None = None  # NULL for pre-L rows
```

**`activity.state` ws envelope** ([`_emit_state` at `api/activities.py:975-1003`](../src/toybox/api/activities.py#L975-L1003)) wraps `ActivityResponse.model_dump(mode="json")` with `trigger_phrase` + `persona_reasoning` stripped for child-side privacy (invariant 7), published on topic `activity.state` ([`ws/topics.py:26`](../src/toybox/ws/topics.py#L26)). Phase L's L4 must call `_emit_state(pubsub, refreshed_response)` after inserting the reward step.

**Joke corpus** ([`src/toybox/activities/joke_corpus.py`](../src/toybox/activities/joke_corpus.py)) — entries in [`data/jokes/jokes.json`](../data/jokes/jokes.json) follow shape `{id: kebab-slug, setup: str, punchline: str, theme: Theme, optional_toy_slot: bool, age_band: str, persona_compat: list[str]}` (e.g. `id="why-chicken-crossed"`). Loader exports `load_jokes()`, `pick_joke(seed, *, age_band=None, persona_id=None, theme=None) -> Joke | None`, `apply_toy_substitution()`, `clear_joke_cache()`. Reward resolver uses `pick_joke(seed=sha256(activity.id, step_count), theme=<one of activity_themes>)`.

**Song corpus** ([`src/toybox/activities/song_corpus.py`](../src/toybox/activities/song_corpus.py)) — manifest at [`data/songs/manifest.json`](../data/songs/manifest.json), entries follow shape `{id: kebab-slug, title: str, audio_path: str (relative under data/songs/), duration_seconds: int, theme: Theme, age_band: str, persona_compat: list[str], license: str, credit: str, lyrics: str}`. Loader exports `pick_song(seed, *, age_band=None, persona_id=None, theme=None, require_audio=False) -> Song | None`. Audio served at `/api/static/songs/audio/<song_id>.mp3`. `<song_id>` and `<joke_id>` formats: kebab-slug per Phase K convention (e.g. `rocket-launch-countdown`, `why-chicken-crossed`).

**`slot_fills_json` column shape** ([`0008_activity_slot_fills.sql:38`](../src/toybox/db/migrations/0008_activity_slot_fills.sql#L38)). DDL: `ALTER TABLE activities ADD COLUMN slot_fills_json TEXT NOT NULL DEFAULT '{}'`. JSON shape is **flat `{slot_name: resolved_value}`** — e.g. `{"toy": "Penguin", "room": "kitchen", "hero": "Captain Bear"}`. **It does NOT carry `template_id` directly** — Phase L's reward resolver gets the template ID from `activities.intent_source` or a sibling column (build agent: grep the propose path for how the template id is persisted; if no column carries it, store it in `slot_fills_json` under reserved key `__template_id` as part of L4). Existing reader pattern at [`api/activities.py:2818`](../src/toybox/api/activities.py#L2818):

```python
slot_fills_raw = row["slot_fills_json"]
slot_fills: dict[str, str] = {}
if slot_fills_raw:
    slot_fills = json.loads(slot_fills_raw)
```

**`transcripts` table** ([`0001_initial.sql:94-101`](../src/toybox/db/migrations/0001_initial.sql#L94-L101) + `0002` adds `language`):

```sql
CREATE TABLE transcripts (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    mic_id            TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    text              TEXT,
    confidence        REAL,
    triggered_intent  TEXT,
    language          TEXT NOT NULL DEFAULT 'unknown'  -- 0002
);
```

**Recent-transcript fetcher** — existing helper at [`src/toybox/ai/tools.py:404`](../src/toybox/ai/tools.py#L404) issues `SELECT text FROM transcripts WHERE session_id = ? AND text IS NOT NULL ORDER BY ended_at DESC LIMIT ?`. Phase L's reward resolver uses the same SQL pattern (lifted into a small helper in [`content_resolver.py`](../src/toybox/activities/content_resolver.py) — `recent_transcript_texts(conn, session_id, limit=50) -> list[str]` — if the existing one isn't directly importable from `ai/tools.py`).

**`extract_themes` signature** ([`src/toybox/activities/topic_extract.py:300`](../src/toybox/activities/topic_extract.py#L300)):

```python
def extract_themes(texts: Iterable[str]) -> list[Theme]:
    """Return Themes mentioned across texts, ranked by vote count.
    Empty list means no-bias signal — caller falls back."""
```

`Theme` is a StrEnum with the 12-theme taxonomy values listed in the Phase K context above.

**`labeled_events` table** ([`0003_labeled_events.sql:16-28`](../src/toybox/db/migrations/0003_labeled_events.sql#L16-L28)):

```sql
CREATE TABLE labeled_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id           TEXT NOT NULL,
    generated_at          TEXT NOT NULL,
    generator_path        TEXT NOT NULL CHECK (generator_path IN ('claude','offline','local')),
    inputs_chatml_json    TEXT NOT NULL,
    activity_json         TEXT NOT NULL,
    parent_signal         REAL,
    parent_signal_set_at  TEXT,
    ended_at_step         INTEGER,
    judge_scores_json     TEXT,
    judge_run_at          TEXT
);
```

This is an **activity-generation-level** event log (one row per generated activity, not per step). There is no per-step `source` column; the Phase K plan's "`source: parent_insert`" / "`source: spontaneity`" descriptions referred to a label that lives inside the `inputs_chatml_json` blob, not a top-level column. **L4 does NOT need to write to this table** — see L4's revised problem statement.

**Reviewer-flag policy** — Toybox's parent UI is PIN-gated by design (invariant 2). `/build-step --reviewers full` runtime reviewers cannot enter the PIN and therefore always report INCOMPLETE. Every Phase L step uses `--reviewers code`; the L12 iPad UAT covers all runtime verification.

**Parent PIN setup** — fresh checkouts need a PIN set before LAN binding. Module: [`src/toybox/core/pin.py`](../src/toybox/core/pin.py). For an in-place dev workstation (this one) the PIN survives the new migrations — no re-set needed for L1–L11. For a completely fresh checkout, set the PIN via the parent UI's first-run flow on loopback (visit `http://127.0.0.1:4000/parent`) before attempting any `--host 0.0.0.0` run.

**Project invariants** ([`plan.md`](plan.md)) — Phase L respects all ten unchanged; relevant subset (invariants 2/4/6 omitted as not directly load-bearing for this phase):
- (1) Single uvicorn worker.
- (3) `If-Match-Version` on every activity mutation — the extended approve endpoint already enforces this.
- (5) Photo uploads through validation pipeline — reward image upload reuses [`storage/images.py`](../src/toybox/storage/images.py).
- (8) Slugs are server-derived from `display_name` — reward IDs follow.
- (9) Pydantic↔TS codegen pre-commit hook — every new wire shape must regenerate [`frontend/src/shared/types.ts`](../frontend/src/shared/types.ts).
- (10) Forward-only migrations.

**Code-quality rules** ([`code-quality.md`](../../.claude/rules/code-quality.md)) called out at relevant steps:
- §1 (grep downstream consumers when key shape changes) → applies at L5/L6 (removing flags); call sites of the three removed flags must be deleted, not left dangling.
- §2 (one source of truth for shape constants) → the animation enum has ONE definition (Python `Animation` StrEnum) re-exported through codegen to TS. No duplicate string literals in the frontend.
- §4 (new components require integration test through production caller) → L4 (reward picker wired into advance handler) must have an integration test that exercises the production advance path through to a fired reward step. L11 (kiosk RewardStep) must be exercised by L12's e2e fixture, not unit-tested in isolation.

## 3. Scope decisions (locked)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Reward firing model | Reward TYPE per activity; parent picks at approve | Predictable celebration; avoids "what just happened?" non-sequiturs |
| D2 | Animation assignment | Per-reward fixed (one animation set at upload time) | Keeps the parent UI simple; treasure_chest always shines, jewel always spins |
| D3 | Non-reward joke/song surfaces | Keep `standalone` + `parent-insert`; remove `embedded`, `ending`, `spontaneity` | Standalone is parent-initiated (intentional); parent-insert is parent-initiated (intentional); the three removed were emergent (felt random) |
| D4 | Pool selection within a type | Themed fuzzy match via free-form tags; tie-break `last_used_at DESC`; fall back uniform | Free-form lets parents tag idiosyncratically without canonical taxonomy lookups |
| D5 | reward_type=random resolve timing | Server resolves at advance time (when kiosk crosses past last regular step) | Fresh each replay; keeps kiosk thin |
| D6 | Empty-pool behavior | Fall through chain: picture → joke → song → no reward | Never blocks activity completion; degrades gracefully |
| D7 | Removed-flag migration | Drop the three rows + delete modules + delete tests in one PR | No backward-compat; cleaner cut; K-era code is internal-only |
| D8 | Default reward_type | `random` | Variety out of the box; parent can pin per activity |

## 4. Out of scope

- **Per-kid reward weights or favorites.** Considered and rejected — adds a parent-tuning surface for unclear v1 payoff; revisit if operators ask.
- **Per-reward multi-animation sets** (e.g. treasure_chest allowed to fire `shine` OR `jump` randomly). Considered and rejected — D2 chose one-fixed-animation-per-reward for parent-UI simplicity.
- **Image-gen worker for reward sprites.** Rewards are static parent uploads, like base toy images before sprite generation. No 10-pose grid like [`ToyActionGrid`](../frontend/src/parent/components/ToyActionGrid.tsx).
- **Reward sound effects / TTS narration.** Kiosk plays only the existing persona voice on advance; no per-reward audio (song-reward audio comes from the song corpus, not a per-reward asset).
- **Reward leaderboards, streaks, or progression.** Pure celebration moments, not gamification.
- **Per-room or per-time-of-day reward biasing.**
- **Per-entry `last_used_at` for jokes/songs picked as rewards.** Corpora are small (~50 entries each) — uniform random fallback is adequate; per-entry recency tracking would require new schema. v2 candidate.

## 5. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Removing `embedded` / `ending` / `spontaneity` orphans data in finished activities | Old activity_steps rows still carry `metadata.interjection ∈ {embedded, ending, spontaneity}` | Keep the field schema; just stop writing those values. Read paths must tolerate unknown values (already do today via dict-get). |
| `recommended_themes` on 200+ templates becomes orphaned data | None — Phase K templates already have these; we re-use them for reward tag-match input | L3 reads existing `recommended_themes` from template metadata as one input to the union; no template re-edit needed |
| Animation CSS interacts with iOS Safari quirks | Reward looks broken on actual kiosk hardware | L11 spec includes the six CSS keyframes; L12 iPad UAT step #9 tests all six on real Safari |
| Free-form tags drift across rewards (parent uses "pirate" once, "Pirates" next) | Tag-match fails silently | Server lowercases + strips + NFKC-normalizes on save; client renders the normalized version in the chip UI for round-trip clarity |
| Default `reward_type=random` produces picture-rewards before parent uploads any | First-run kid sees fallback chain in action — no reward fires | Acceptable: graceful degradation. Operator UAT verifies fallback chain visible (a no-reward outro is fine). L9 dropdown can be tested with `joke` to confirm. |
| Phase K's K14 templates carry `ending_step` blocks that will no longer fire | Template field becomes dead data + validator continues enforcing its shape needlessly | **L5 strips the `ending_step` property from the template schema** at [`_schema.json`](../src/toybox/activities/templates/_schema.json) AND removes the validator branch at [`_validator.py:413-428`](../src/toybox/activities/_validator.py#L413-L428). Old templates carrying the property are accepted (extra properties ignored). One-shot cleanup. |
| K validator branch enforcing non-empty `recommended_themes` for `auto:true` embedded steps becomes dead code | Branch at [`_validator.py:462-467`](../src/toybox/activities/_validator.py#L462-L467) references a step pattern (`auto: true` embedded songs/jokes) that L5 removes from generator paths | L5 also deletes the validator branch; templates retain `recommended_themes` as a required-non-empty field at the top level, since L3 consumes it. |

## 6. Architecture sketch

```
[Parent uploads reward.png]
      │
      ▼
POST /api/rewards/upload  ──► data/images/.staging/<sha>.png (existing pipeline)
      │
      ▼
POST /api/rewards (confirm) ──► INSERT rewards (display_name, tags, animation, image_path, active=1)
                                  data/images/rewards/<id>.<ext>


[Parent approves activity]
      │
POST /api/activities/{id}/approve   {child_ids: [...], reward_type: "random"}
      │
      ▼
activity row gets reward_type='random' written; state → 'approved'


[Kid plays; advance past last regular step]
      │
POST /api/activities/{id}/advance
      │
      ▼
post_advance() → _terminal_advance() detects "no next regular step
                                              + no kind='reward' step yet in this activity"
      │
      ▼
resolve_reward(activity, reward_type)  ──┐
   1. If 'random': roll among enabled    │
      types (picture if any active rows; │
      joke if jokes_enabled+pool;         │  free-form tag overlap
      song if songs_enabled+pool)         │  vs activity.themes;
   2. Try chosen type. If pool empty,    │  tie-break last_used_at DESC;
      fall through picture → joke → song │  uniform random fallback.
      → no_reward.                       │
   3. Insert activity_steps row:         │
      kind='reward',                     │
      metadata={                          │
        reward_kind: picture|joke|song,  │
        reward_id, image_url, animation, │
        body (display name or punchline),│
        setup, punchline                 │
      }                                  │
      version += 1                       │
      ws activity.state envelope         │
      ▼                                  │
[Kiosk receives advance response]        │
   <RewardStep> renders:                 │
     picture → <img> + CSS keyframe      │
     joke    → existing <JokeStep>        │
     song    → existing <SongPlayer>      │
   Auto-advances on dismiss/timeout       │
   Activity state → 'completed'          ◄┘
```

## 7. Step list

**Note on step format:** every step follows the `/build-phase` contract per [`plan-and-issue-flow.md`](../../.claude/rules/plan-and-issue-flow.md) — `### Step LN: <title>` heading, `**Problem:** …`, `**Type:** code`, `**Issue:** #N`, `**Flags:** --reviewers code`. Issue numbers were minted by `/repo-sync` on 2026-05-16: umbrella #139, step issues #140–#151. `Type: code` and `--reviewers code` are used for every step because Toybox's parent UI is PIN-gated (see §2 reviewer-flag policy); runtime + UI verification happens in L12 (manual iPad UAT).

### Step L1: Migrations + animation taxonomy + reward model

**Problem:** Migration `0019_rewards_table.sql` creates `rewards` table: `id TEXT PRIMARY KEY, display_name TEXT NOT NULL, image_path TEXT NOT NULL, image_hash TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]' (JSON array of lowercased NFKC-normalized strings), animation TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_used_at TEXT`. Migration `0020_activity_reward_type.sql` adds `reward_type TEXT` to `activities` (no default — NULL means legacy pre-L activity). Migration `0021_drop_deprecated_play_flags.sql` deletes the three rows (`play_embedded_enabled`, `play_endings_enabled`, `play_spontaneity_enabled`) from `settings`. Add `Animation` StrEnum (`shine | jump | spin | pulse | wobble | float`) and `RewardType` Literal (`picture | joke | song | random`) — both in [`src/toybox/activities/models.py`](../src/toybox/activities/models.py) so the existing `toybox-gen-types` pre-commit hook at [`.pre-commit-config.yaml:13-18`](../.pre-commit-config.yaml#L13-L18) regenerates [`frontend/src/shared/types.ts`](../frontend/src/shared/types.ts) automatically (the hook's `files:` regex already covers `models.py`). Single source of truth for both enum string lists (code-quality §2) — every consumer (server picker, parent UI dropdown, kiosk RewardStep) imports from this single place.

**Type:** code

**Issue:** #140

**Flags:** `--reviewers code`

### Step L2: Rewards CRUD API

**Problem:** New module [`src/toybox/api/rewards.py`](../src/toybox/api/rewards.py) mirroring [`toys.py`](../src/toybox/api/toys.py:80-324). Endpoints: `POST /api/rewards/upload` (stages image, returns staging key), `POST /api/rewards` (confirms — body: `RewardConfirmRequest { staging_key, display_name, tags, animation, active=True }`), `GET /api/rewards`, `GET /api/rewards/{id}`, `PATCH /api/rewards/{id}` (`RewardUpdateRequest { display_name?, tags?, animation?, active?, archived? }`), `DELETE /api/rewards/{id}` (sets archived=1, never hard-delete). Slug derivation from `display_name` per invariant 8. Tag normalization: lowercase + strip + dedupe on every write path. Pydantic `RewardResponse` shape mirrored to TS. **Image storage:** under `data/images/rewards/<id>.<ext>` via existing [`storage/images.py`](../src/toybox/storage/images.py) entry points. Parent-scope auth via `RequireScope({TokenScope.parent})`.

**Type:** code

**Issue:** #141

**Flags:** `--reviewers code`

### Step L3: Reward picker (server-side resolver)

**Problem:** New function `resolve_reward(conn, activity, requested_type) -> ResolvedReward | None` in [`src/toybox/activities/content_resolver.py`](../src/toybox/activities/content_resolver.py). `ResolvedReward` is a dataclass: `kind ∈ {picture, joke, song}`, `reward_id`, `image_url | None`, `animation | None`, `audio_url | None`, `body`, `setup | None`, `punchline | None`.

Algorithm:

1. **Compute `activity_themes`** as the union of (a) the activity's template `recommended_themes` and (b) `extract_themes(recent_texts)`. Sources:
   - (a) Template `recommended_themes`: requires resolving the activity → template. **Pre-requisite scoped into L3:** grep the propose path for how the template id is persisted on the activity row today; if no column carries it, add a reserved key `__template_id` to `slot_fills_json` as part of L4's approve-path write. Then load the template via the existing template loader and read its `recommended_themes` field.
   - (b) Recent transcripts: lift the SQL from [`src/toybox/ai/tools.py:404`](../src/toybox/ai/tools.py#L404) — `SELECT text FROM transcripts WHERE session_id = ? AND text IS NOT NULL ORDER BY ended_at DESC LIMIT 50` — into a small `recent_transcript_texts(conn, session_id, limit=50) -> list[str]` helper in `content_resolver.py` (do NOT import from `ai/tools.py` to keep the dependency direction one-way: api → content_resolver, not the reverse). Pipe through `extract_themes()` ([`topic_extract.py:300`](../src/toybox/activities/topic_extract.py#L300)) for the second input.
   - Both sources lowercased + NFKC-normalized before union.
2. **If `requested_type='random'`**, roll among types eligible (picture: at least one active reward row; joke: `jokes_enabled` AND non-empty joke corpus; song: `songs_enabled` AND non-empty song corpus).
3. **Try chosen type:** query pool, **lowercase-normalized set-intersection** of `reward.tags` with `activity_themes`; order by overlap count DESC, then `last_used_at` ASC NULLS FIRST; pick first. For joke/song types, "tags" is the entry's `theme` field projected to a singleton set. If empty intersection, fall back uniform random over the type pool (use `pick_joke` / `pick_song` with `theme=None` for jokes/songs).
4. **On empty pool**, fall through `picture → joke → song → None`.

Pure function, deterministic given `(activity_id, db state, current time)` — for `random` rolls AND for uniform-random fallback, seed via SHA-256 of `(activity.id, current_step_count)` so repeated calls within one advance are stable. Unit-test the fallback chain across all 8 emptiness combinations + the union-of-sources theme path (mock `extract_themes`).

**Type:** code

**Issue:** #142

**Flags:** `--reviewers code`

### Step L4: Wire reward step into advance handler (integration test required)

**Problem:** Extend `_terminal_advance` in [`src/toybox/api/activities.py:4712`](../src/toybox/api/activities.py#L4712) (the helper `post_advance` calls when the kid crosses past the last regular step — verifier confirmed function names; `_do_advance` does not exist). Detection logic: if the activity has no `activity_steps` row with `kind='reward'` yet, call `resolve_reward(activity, activity.reward_type or 'random')`, then INSERT a new `activity_steps` row at `seq = max(seq)+1` with `kind='reward'` and metadata `{reward_kind, reward_id, image_url, animation, audio_url, body, setup, punchline}` — see §8 for per-`reward_kind` field population rules. Update `rewards.last_used_at` for `reward_kind='picture'`. **For `joke`/`song` reward kinds there is NO per-entry `last_used_at` tracking in v1** — corpora are JSON-on-disk and the existing `labeled_events` table is activity-generation-level, not per-step-source-level, so a uniform-random fallback when tags don't match is fine for the ~50-entry corpora. Per-entry recency tracking is a v2 nice-to-have requiring its own schema. Emit `activity.state` ws envelope via `_emit_state(pubsub, refreshed_response)` ([`api/activities.py:975-1003`](../src/toybox/api/activities.py#L975-L1003)). Activity ends naturally after the kid dismisses the reward step (existing end-of-steps logic fires `state='completed'`). Extend `ApproveRequest` ([`api/activities.py:410-413`](../src/toybox/api/activities.py#L410-L413)) with `reward_type: Literal["picture","joke","song","random"] | None = None` defaulting to `"random"` server-side; persist to `activities.reward_type` column. **Required integration test** (per code-quality §4): test goes through `POST /api/activities/propose → POST /api/activities/{id}/approve {reward_type: 'picture'} → POST /api/activities/{id}/advance × N` and asserts a `kind='reward'` step with `metadata.reward_kind='picture'` appears at the end. Run the assertion for each of `picture | joke | song | random`. Add a separate test for the fallback chain (no picture rewards in DB → assert `reward_kind='joke'` fired).

**Type:** code

**Issue:** #143

**Flags:** `--reviewers code`

### Step L5: Remove embedded / ending / spontaneity surfaces — backend

**Problem:** Delete the three core modules entirely: [`src/toybox/core/play_embedded_enabled.py`](../src/toybox/core/play_embedded_enabled.py), [`play_endings_enabled.py`](../src/toybox/core/play_endings_enabled.py), [`play_spontaneity_enabled.py`](../src/toybox/core/play_spontaneity_enabled.py); and their API counterparts: [`src/toybox/api/play_embedded_enabled_settings.py`](../src/toybox/api/play_embedded_enabled_settings.py), [`play_endings_enabled_settings.py`](../src/toybox/api/play_endings_enabled_settings.py), [`play_spontaneity_enabled_settings.py`](../src/toybox/api/play_spontaneity_enabled_settings.py); plus their tests under [`tests/`](../tests/). Remove the three surfaces' code paths from the generator + advance handler:

1. **K14 embedded-step injection** at [`src/toybox/activities/generator.py:164,283`](../src/toybox/activities/generator.py) (the picker reading `recommended_themes` + injecting a `kind:"song"|"joke" auto:true` step) — delete.
2. **K14 ending-step append at activity creation** — `_build_ending_row` at [`src/toybox/api/activities.py:1694`](../src/toybox/api/activities.py#L1694) plus its call sites in `_advance_to_ending_or_terminal` at [4633-4709](../src/toybox/api/activities.py#L4633-L4709) — delete the function, replace `_advance_to_ending_or_terminal` body's ending-step branch with a direct call to `_terminal_advance` (the path L4 extends).
3. **K15 spontaneity advance-hook** — the `build_interjection_step(..., interjection=InterjectionKind.spontaneity, ...)` call site at [`api/activities.py:4034`](../src/toybox/api/activities.py#L4034) — delete.

Drop the three registrations from the FastAPI router list. Drop the `InterjectionKind.embedded | ending | spontaneity` enum values from [`src/toybox/activities/interjections.py:29-47`](../src/toybox/activities/interjections.py#L29-L47) (the `parent` value stays for parent-insert). `build_interjection_step` at [`src/toybox/activities/interjection.py:92`](../src/toybox/activities/interjection.py#L92) remains — its `parent` callers are kept.

**Template schema + validator cleanup (B3 decision: strip now):** drop the `ending_step` property from [`src/toybox/activities/templates/_schema.json`](../src/toybox/activities/templates/_schema.json); remove the `EndingStep` model + the validation branch at [`_validator.py:413-428`](../src/toybox/activities/_validator.py#L413-L428); remove the K14 `auto:true` embedded-step validator branch at [`_validator.py:462-467`](../src/toybox/activities/_validator.py#L462-L467). Templates still in the catalog with `ending_step:` keys are accepted (extra-properties tolerance) — their data is now inert. **Do NOT** scrub the existing 1000 template JSONs; let the field rot quietly.

**Grep all downstream consumers** (code-quality §1) of (a) the three flag names, (b) the three deleted enum values, (c) the deleted helper names — attach the grep results to the issue as an OK/needs-fix/done table; the test suite is the safety net for missed callers.

**Sub-audits to run as part of this step:**
- **Operator docs:** grep [`documentation/operator/`](../documentation/operator/) + [`documentation/plan/`](../documentation/plan/) for `play_embedded_enabled`, `play_endings_enabled`, `play_spontaneity_enabled`; remove or update references.
- **Trigger registry:** confirm `request_joke` / `request_song` intents in the trigger registry still route to the standalone propose path post-L (they should — standalone surface is kept). Grep `trigger_phrase` + `request_joke` to verify the route survives the deletions.

**Type:** code

**Issue:** #144

**Flags:** `--reviewers code`

### Step L6: Remove embedded / ending / spontaneity surfaces — frontend

**Problem:** From [`PlayFeaturesControls.tsx:87-100`](../frontend/src/parent/components/PlayFeaturesControls.tsx#L87-L100) delete the three rows from the `FEATURE_TOGGLES` array (single source of truth — no other consumers per code-quality §2). Remove the corresponding [`ApiClient`](../frontend/src/parent/api.ts) methods. Update the section heading/copy to reflect five remaining flags (standalone + clickable_words + read_me_button + the two masters until L8 moves them — order matters: this step lands first, then L8 moves masters out).

**Type:** code

**Issue:** #145

**Flags:** `--reviewers code`

### Step L7: RewardIngest component (parent UI)

**Problem:** New [`frontend/src/parent/components/RewardIngest.tsx`](../frontend/src/parent/components/RewardIngest.tsx) cloned from [`ToyIngest.tsx`](../frontend/src/parent/components/ToyIngest.tsx) but stripped of `allowed_roles` and `ToyActionGrid`. Form state: `display_name, tags (chip input, comma-separated), animation (segmented control over the six Animation enum values), active (toggle)`. Upload pipeline reuses existing staging → confirm pattern. Edit mode for existing rewards. Archive button (sets `archived=true` via PATCH, hides from list). Use the same sort-active-first list pattern shipped in the recent toys-sortable commit ([`d89b6d1`](https://github.com/aberson/toybox/commit/d89b6d1) per memory). **Animation preview:** when the parent picks an animation in the segmented control, the uploaded reward image previews with that animation playing in a card to the right (re-use the L10 CSS module).

**Type:** code

**Issue:** #146

**Flags:** `--reviewers code`

### Step L8: Rewards section in Kids & Toyboxes tab; masters move

**Problem:** Add a "Rewards" sub-section to the Kids & Toyboxes parent tab housing [`RewardIngest`](../frontend/src/parent/components/RewardIngest.tsx) + the rewards list. Section header carries the two master toggles `jokes_enabled` + `songs_enabled` with one-line hints ("Jokes can fire as activity-end rewards (and standalone if enabled)" / "Songs same"). Remove those two toggles from [`PlayFeaturesControls.tsx`](../frontend/src/parent/components/PlayFeaturesControls.tsx) (their state is now owned by the Rewards section, but the underlying API endpoints + DB rows stay — only the UI placement moves). Confirm via grep that no other UI component reads `jokesEnabled` / `songsEnabled` from a now-stale parent context.

**Type:** code

**Issue:** #147

**Flags:** `--reviewers code`

### Step L9: Reward dropdown on activity approval card

**Problem:** [`SuggestionCard.tsx`](../frontend/src/parent/components/SuggestionCard.tsx) gets a small native HTML `<select>` element labeled "Reward:" with options `Random | Picture | Joke | Song`. Default `Random`. (Segmented control was considered; the dropdown wins on touch surface + screen-reader behavior on the parent's desktop, which is the primary surface for activity approval.) Selection threads through `onApprove` callback up to `ApiClient.approve` which now passes `reward_type`. Disable selection if `(picture: 0 active rewards) AND (joke: jokes_enabled=false OR empty corpus) AND (song: songs_enabled=false OR empty corpus)` — i.e. nothing can possibly fire — and show a hint "No rewards configured" (rare, mostly day-0). For mid-pool selections that would fall back, no UI warning needed; the fallback chain (L3) handles it silently and the kid just sees something else.

**Type:** code

**Issue:** #148

**Flags:** `--reviewers code`

### Step L10: Kiosk RewardStep + CSS animation primitives

**Problem:** CSS animations require **two artifacts**: (a) `@keyframes` blocks defined at the document level, and (b) per-element `animation:` shorthand strings that reference those keyframes by name. Phase L ships both:

- (a) [`frontend/src/child/animations/rewardAnimations.css`](../frontend/src/child/animations/rewardAnimations.css) — a CSS module containing six `@keyframes` blocks (`@keyframes shine { ... }`, `@keyframes jump { ... }`, etc.). Imported once by `RewardStep.tsx` via `import "./rewardAnimations.css"`.
- (b) [`frontend/src/child/animations/rewardAnimations.ts`](../frontend/src/child/animations/rewardAnimations.ts) — a `Record<Animation, CSSProperties>` mapping each Animation enum value to the inline-style object that applies the keyframe (e.g. `{ animation: "shine 2s ease-in-out infinite" }`). `RewardStep.tsx` reads this map and spreads into the `<img>` `style` prop.

Each animation runs on a `<img>` element styled like [`ToyActionSprite`](../frontend/src/child/components/ToyActionSprite.tsx#L47-L85) (240px, `imageRendering: "pixelated"`, transparent bg). Keyframes:

| Animation | Spec |
|---|---|
| shine | `box-shadow` pulse + `filter: brightness()` cycle (1.0 → 1.4 → 1.0), 2s ease-in-out infinite |
| jump | `transform: translateY(0 → -40px → 0)`, 1.5s cubic-bezier(0.34, 1.56, 0.64, 1) infinite |
| spin | `transform: rotate(0 → 360deg)`, 2s linear infinite |
| pulse | `transform: scale(1 → 1.15 → 1)`, 1.2s ease-in-out infinite |
| wobble | `transform: rotate(-10deg → 10deg → -10deg)`, 1s ease-in-out infinite |
| float | `transform: translateY(0 → -8px → 0)`, 3s ease-in-out infinite (slow drift) |

New component [`frontend/src/child/components/RewardStep.tsx`](../frontend/src/child/components/RewardStep.tsx). Props: `step.metadata = {reward_kind, image_url?, animation?, body, setup?, punchline?}`. Branches on `reward_kind`:
- `picture`: render `<img src={image_url}>` with the `animation` style applied; body text as caption underneath; auto-advance after 6s OR on tap.
- `joke`: delegate to existing [`<JokeStep>`](../frontend/src/child/components/JokeStep.tsx) (props pulled from `metadata.setup` / `metadata.punchline`).
- `song`: delegate to existing [`<SongPlayer>`](../frontend/src/child/components/SongPlayer.tsx) (`src` ← `metadata.audio_url`, `title` ← `metadata.body`). Persona avatar from the parent `<StepCard>` provides the cover art surface — no per-song image needed.

[`StepCard.tsx`](../frontend/src/child/components/StepCard.tsx) gains a `kind === 'reward'` branch dispatching to `<RewardStep>`.

**Type:** code

**Issue:** #149

**Flags:** `--reviewers code`

### Step L11: End-to-end integration test through production caller

**Problem:** New pytest module [`tests/integration/test_phase_l_reward_e2e.py`](../tests/integration/test_phase_l_reward_e2e.py) covering production paths (code-quality §4):

1. propose → approve `{reward_type: 'picture'}` → 5× advance → assert final step `kind='reward'`, `metadata.reward_kind='picture'`, `metadata.image_url` reachable, `metadata.animation ∈ Animation`.
2. Same flow with `reward_type: 'joke'` → assert `reward_kind='joke'` with setup + punchline metadata.
3. Same flow with `reward_type: 'song'` → assert `reward_kind='song'` with `audio_url` populated and `image_url=null` (kiosk renders persona avatar fallback).
4. Empty picture-rewards table → `reward_type: 'picture'` → assert fallback to `joke`.
5. `jokes_enabled=false` AND empty pictures → `reward_type: 'picture'` → assert fallback to `song`.
6. All three pools empty/disabled → assert no reward step appended; activity completes cleanly.
7. `reward_type: 'random'` × 30 trials → assert distribution across the three types is non-degenerate (every type appears at least twice).
8. `random` resolved at fire time: same activity replayed across two separate `advance` sequences yields possibly different reward types (use deterministic seeding by `(activity_id, current_step_count)` to make this provable rather than flaky — assert that re-advancing produces a different outcome when a "step" counter changes).
9. **Migration round-trip:** open a synthetic pre-L DB fixture (one with the three deprecated setting rows + K templates + no `activities.reward_type` column), run migrations 0019/0020/0021 through `python -m toybox.db.migrate`, assert (a) `rewards` table exists with expected columns, (b) `SELECT reward_type FROM activities` returns NULL for legacy rows, (c) `SELECT key FROM settings WHERE key IN ('play_embedded_enabled','play_endings_enabled','play_spontaneity_enabled')` returns empty, (d) the schema cleanup (L5 strip of `ending_step`) accepts an old template JSON with the inert field present.
10. **Theme union:** mock `extract_themes` to return `['pirate']` and use a template with `recommended_themes: ['adventure']`; insert two picture rewards (one tagged `pirate`, one tagged `adventure`); call `resolve_reward(activity, 'picture')` 10× and assert both appear (the union-of-sources path works, not just the template path).

**Type:** code

**Issue:** #150

**Flags:** `--reviewers code`

### Step L12 (M1): iPad operator UAT

**Tracking issue:** #151 (manual-section step — `/build-phase` does not dispatch)

**Problem:** Operator-only step. Verifies on real iPad PWA hardware that:

1. Parent uploads two picture rewards (treasure_chest with `shine`, jewel with `spin`) via the new Rewards section. List renders with active-first sort.
2. Parent approves an activity with `Reward: Random` — across 5 replays sees at least one of each (picture / joke / song).
3. Parent approves an activity with `Reward: Picture` — kiosk fires a picture reward at end, with the configured animation playing visibly. Animation persists across full step duration.
4. Parent archives the only picture reward, leaving an empty picture pool. Approves a new activity `Reward: Picture` → kiosk falls back to joke. No error toast; degrades gracefully.
5. Settings → confirm `embedded`, `ending`, `spontaneity` toggles are absent from PlayFeaturesControls. Confirm `jokes_enabled` + `songs_enabled` toggles appear at the Rewards section header instead.
6. Toggle `jokes_enabled=false`, approve `Reward: Joke`, advance — kiosk falls back to song or skips reward.
7. Standalone joke trigger phrase still works ("Tell me a joke"); standalone song trigger phrase works ("Sing me a song").
8. Parent-insert via running ActivityPanel sidebar buttons still works.
9. Animation rendering on iOS Safari — `shine`, `jump`, `spin`, `pulse`, `wobble`, `float` all visibly distinct and smooth. No layout shift / overflow. No GPU hitching on a multi-step activity.
10. Pre-L (legacy) activities in history still load and render. No 500s when fetching an activity row with `reward_type=NULL`.

**Prerequisites** (one-time per fresh checkout):

```powershell
# From c:\Users\abero\dev\toybox\
uv sync
cd frontend; npm install; cd ..
# Parent PIN already set from earlier phases. LAN IP via ipconfig:
$env:TOYBOX_LAN_IP = "192.168.1.42"
```

**Operator commands** (run from `c:\Users\abero\dev\toybox\` each session):

```powershell
uv run python -m toybox.db.migrate
uv run python -m toybox.main --host 0.0.0.0 --port 8000
cd frontend; npm run dev
# Open http://$env:TOYBOX_LAN_IP:4000/parent on desktop
# Open http://$env:TOYBOX_LAN_IP:4000/child on iPad
```

**What to look for:**

| # | Pass condition |
|---|---|
| 1 | Two rewards visible in list. Edit + archive controls work. Sort by active-first preserved. |
| 2 | Across 5 replays, all three reward types fire at least once. |
| 3 | Picture reward fires; animation visibly running (chest is shimmering / pulsing) for at least 4s before auto-dismiss. |
| 4 | No picture rewards uploaded → next `Reward: Picture` activity falls back to joke without error. |
| 5 | Settings tab has no `play_embedded_enabled` / `play_endings_enabled` / `play_spontaneity_enabled` toggles. `jokes_enabled` + `songs_enabled` are in the Rewards header, not Play Features. |
| 6 | jokes off + `Reward: Joke` → song fires, or no reward fires gracefully. No 500. |
| 7 | "Tell me a joke" still queues a single-step joke activity. "Sing me a song" queues a song activity. |
| 8 | "+ joke" / "+ song" buttons on running ActivityPanel still insert mid-play. |
| 9 | Each animation feels distinct on iPad Safari; no jank, no overflow. |
| 10 | History view scrolls through pre-L activities without 500 / blank cards. |

Please run M1 after L11 reports green.

## 8. API contracts

### New endpoints (Rewards CRUD)

| Method + Path | Headers | Request body | Response (200) | Other status codes |
|---|---|---|---|---|
| `POST /api/rewards/upload` | parent | multipart `file` | `{staging_key, image_hash, mime_type, width, height}` | 400 invalid image; 401/403 auth |
| `POST /api/rewards` | parent | `RewardConfirmRequest {staging_key, display_name, tags: list[str], animation: Animation, active: bool = true}` | `RewardResponse` | 400 duplicate slug; 404 staging key missing |
| `GET /api/rewards` | none | — | `list[RewardResponse]` (active-first by `last_used_at` desc within active partition) | — |
| `GET /api/rewards/{id}` | none | — | `RewardResponse` | 404 |
| `PATCH /api/rewards/{id}` | parent | `RewardUpdateRequest {display_name?, tags?, animation?, active?, archived?}` | `RewardResponse` | 404; 400 invalid animation |
| `DELETE /api/rewards/{id}` | parent | — | `RewardResponse` (with `archived=true`) | 404 |

### Modified endpoint

| Method + Path | Headers | Change |
|---|---|---|
| `POST /api/activities/{id}/approve` | `If-Match-Version: <int>` (required, per invariant 3) | `ApproveRequest` gains `reward_type: Literal["picture","joke","song","random"] \| None = None` (defaults to `"random"` server-side; persisted to `activities.reward_type`). Response: `ActivityResponse` (full shape in §2) gains a `reward_type: Literal["picture","joke","song","random"] \| None` field — `None` for pre-L legacy rows where the column is NULL (do NOT coerce NULL to `"random"` — that would lie about a never-set value). Returns 409 on version mismatch via standard `withConflictHandler` round-trip. |
| `POST /api/activities/{id}/advance` | `If-Match-Version: <int>` (required) | Wire shape unchanged. New behavior: when crossing the last regular step boundary, the server appends a `kind='reward'` step before returning. |

### Removed endpoints

`/api/settings/play-embedded-enabled`, `/api/settings/play-endings-enabled`, `/api/settings/play-spontaneity-enabled` (GET + PUT each) — all return 404 post-L5.

### Wire shapes

**`RewardResponse`** (mirrored to TS via codegen):

```python
class RewardResponse(BaseModel):
    id: str                          # kebab-slug from display_name
    display_name: str
    image_path: str                  # relative under data/images/rewards/
    image_hash: str                  # sha256 hex
    tags: list[str]                  # lowercased, deduped
    animation: Literal["shine","jump","spin","pulse","wobble","float"]
    active: bool
    archived: bool
    created_at: str                  # ISO …Z
    last_used_at: str | None
```

**`ActivityStepResponse.kind`** extension: pre-L `"text" | "fork" | "song" | "joke"` → post-L `"text" | "fork" | "song" | "joke" | "reward"`.

**`ActivityStepResponse.metadata`** for reward steps:

```jsonc
{
  "reward_kind": "picture" | "joke" | "song",
  "reward_id": "<rewards.id or jokes corpus id or songs corpus id>",
  "image_url": "/api/static/images/rewards/<id>.png" | null,
  "animation": "shine" | "jump" | "spin" | "pulse" | "wobble" | "float" | null,
  "audio_url": "/api/static/songs/audio/<song_id>.mp3" | null,
  "body": "<display name or joke setup>",
  "setup": "<joke setup or null>",
  "punchline": "<joke punchline or null>"
}
```

Per-`reward_kind` populated fields:
- `picture`: `reward_id, image_url, animation, body` (display_name). `audio_url=null, setup=null, punchline=null`.
- `joke`: `reward_id, body, setup, punchline`. `image_url=null, animation=null, audio_url=null`.
- `song`: `reward_id, body, audio_url`. `image_url=null` (kiosk falls back to persona avatar — no per-song cover art route; see §5 risk row). `animation=null, setup=null, punchline=null`.

## 9. Open questions

None remaining; D1–D8 all locked.

## 10. Status

Plan drafted 2026-05-16. `/plan-review` pass 1 (2026-05-16): 3 blockers + 8 gaps + 4 missing items all resolved inline. `/plan-wrap` pass 1 (2026-05-16): 3 blockers + 10 gaps + 5 minor items all resolved inline. `/repo-sync` 2026-05-16 minted umbrella #139 + 12 step issues #140–#151 (mapping in the §7 note); reciprocal parallel-safe pairs back-edited (#141⇄#142, #144⇄#145, #146⇄#149). Ready for `/build-phase --plan documentation/phase-l-plan.md`.
