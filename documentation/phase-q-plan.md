# Phase Q — Element-specific rewards

## 1. What This Feature Does

When a child completes an element-themed activity (Periodic Table Professor activity, `element_microgame_*`, or any future activity tagged with an `element_id`), the reward step delivers a song or joke specifically about that element rather than a tangentially-themed one. M7a shipped 25 element-themed songs as a starting point; Phase Q fills the catalog to 1:1 coverage — 118 songs + 118 jokes, one per element in [data/elements/elements.json](data/elements/elements.json) — and extends the reward picker to prefer by `element_id` with `family → theme → untheme` fallbacks. Corpus is LLM-authored via the existing toybox OAuth + urllib Claude path, operator skim-reviewed, and audio is rendered through the K11 Coqui TTS pipeline.

Why now: the [Phase O followup #194](https://github.com/aberson/toybox/issues/194) issue tracks this gap; UAT of Phase M element activities surfaced the "Mountain Peak Cheer" reward on a Titanium spaceship template — an unrelated song that broke the kid's learning loop.

## 2. Existing Context

A fresh-context model should know:

- **Reward picker entry point.** [`content_resolver.py:1736 resolve_reward()`](src/toybox/activities/content_resolver.py#L1736) walks a `picture → joke → song` fallback chain. `_try_pick_song` ([line 1611](src/toybox/activities/content_resolver.py#L1611)) and `_try_pick_joke` ([line 1574](src/toybox/activities/content_resolver.py#L1574)) currently filter by `theme + persona_id` only. Activity themes are computed by `_compute_activity_themes()` at [content_resolver.py:1308](src/toybox/activities/content_resolver.py#L1308) and passed as a separate `activity_themes: list[str]` arg to the picker functions — **not** carried on `RewardActivityContext`. `element_id` is **not currently carried** anywhere in the resolver.
- **`RewardActivityContext` actual fields.** [`content_resolver.py:1097`](src/toybox/activities/content_resolver.py#L1097) — `id`, `session_id`, `persona_id`, `slot_fills_json`, `current_step_count`. No `activity_themes`, no `template_id` (template_id is decoded from `slot_fills_json`'s reserved `"__template_id"` key inside `_compute_activity_themes`).
- **Corpus shapes.** [`Song`](src/toybox/activities/song_corpus.py#L136) and [`Joke`](src/toybox/activities/joke_corpus.py#L104) are Pydantic `frozen=True`, `extra="forbid"` models. Ids are kebab-case slugs (`^[a-z0-9-]+$`, ≤64 chars).
  - **`Song` full field list** (10 fields): `id: str (min 1, max 64)`, `title: str (min 1, max 120)`, `audio_path: str (min 1, max 200, must start with "audio/", no ".." traversal)`, `duration_seconds: int (1-30)`, `theme: Theme`, `age_band: AgeBand`, `persona_compat: tuple[str, ...] (min length 1)`, `license: str (min 1, max 64)`, `credit: str (min 1, max 200)`, `lyrics: str (min 1, max 500)`. Phase Q adds `element_id: str | None = None` and `family: Family | None = None`.
  - **`Joke` full field list** (7 fields): `id: str (min 1, max 64)`, `setup: str (min 1, max 200)`, `punchline: str (min 1, max 200)`, `theme: Theme`, `optional_toy_slot: bool`, `age_band: AgeBand`, `persona_compat: tuple[str, ...] (min length 1)`. Phase Q adds `element_id` and `family` (same shape as Song).
  - **`Theme` StrEnum** ([themes.py:20](src/toybox/activities/themes.py#L20)) — 13 values: `adventure`, `magic`, `space`, `animals`, `vehicles`, `food`, `friendship`, `pirates`, `knights`, `weather`, `music`, `silly`, `feelings`. Phase Q uses `silly` or `music` for element content (matches M7a heuristic).
  - **`AgeBand`** = `Literal["3-5", "6-8", "9-12"]`. Phase Q uses `3-5` (Child B is pre-reader, Child A is early-reader; both fit the 3-5 lyrical register).
  - **Injection guard** — both loaders reject entries whose text fields contain the case-insensitive substrings `<system-reminder>` or `ignore prior instructions` (defense-in-depth per security.md).
- **Existing element-themed content.** M7a ([`scripts/generate_element_song_manifest.py`](scripts/generate_element_song_manifest.py)) shipped 25 element songs in [`data/songs/manifest.json`](data/songs/manifest.json): 10 family songs (`noble_gases_drift_quiet`, `halogens_make_friends`, `alkali_metals_go_zoom`, `alkaline_earths_keep_strong`, `transition_metals_shiny_song`, `post_transition_metals_bendy`, `metalloids_in_between`, `nonmetals_everywhere`, `lanthanides_glow_soft`, `actinides_radiate_far`) + 15 popular-element songs (gold, silver, iron, helium, oxygen, hydrogen, neon, mercury, copper, uranium, sodium, calcium, carbon, nitrogen, chlorine). All themed `silly`/`music`, `persona_compat: ["periodic_table", "all"]`. Audio is already rendered.
- **Only 2 element-themed jokes** exist in [`data/jokes/jokes.json`](data/jokes/jokes.json) (53 jokes total).
- **Element catalog + Family enum.** [`data/elements/elements.json`](data/elements/elements.json) holds 118 entries with stable ids `<symbol-lower>-<atomic-number>` (e.g. `h-1`, `au-79`) plus `family`, `name`, `symbol`, `fun_fact`, `story_seed_hooks`. [`element_corpus.py:106 Family`](src/toybox/activities/element_corpus.py#L106) is the StrEnum source of truth — singular slugs: `noble_gas`, `halogen`, `alkali_metal`, `alkaline_earth`, `transition_metal`, `post_transition_metal`, `metalloid`, `nonmetal`, `lanthanide`, `actinide`. M7a song ids use **plural** prefixes (e.g. `noble_gases_*`); the Family enum uses **singular** slugs. Phase Q resolves this drift via a new `family: Family | None` field on Song/Joke instead of relying on id-prefix matching.
- **Audio pipeline.** [`scripts/generate_song_corpus.py`](scripts/generate_song_corpus.py) renders MP3s via Coqui TTS XTTS-v2 in a side venv (`pip install TTS`, ffmpeg on PATH). ~5-15 min for 50 songs on modern laptop CPU. K11 + M7b shipped the precedent; operator already has the venv set up. The script reads `Song.audio_path` from manifest entries and writes MP3s to `data/songs/<audio_path>` — every new element-song manifest entry MUST set `audio_path: audio/element-song-<sym>-<n>.mp3` for the renderer to land it at the expected location.
- **Claude auth.** Toybox calls `api.anthropic.com` directly via `urllib` + OAuth bearer; no `anthropic` SDK, no API key. See [`src/toybox/ai/client.py:115 AnthropicClient`](src/toybox/ai/client.py#L115) — sync `urllib.request` POST to `/v1/messages` with `Authorization: Bearer <token>`; `OAuthToken` from [`src/toybox/ai/oauth.py`](src/toybox/ai/oauth.py). Project memory entry `project_oauth_only_claude_auth.md` documents the no-SDK invariant.
- **Activity → resolver wire.** The current activity's `element_id` lives on `activity.steps[i].element_id` (per-step, not per-activity); the API serializer denormalizes corpus fields into `step.metadata` for element steps. Phase Q's "primary" element_id for an activity = the first persisted step row whose `element_id IS NOT NULL` (matches the M3 element_id surface contract).

## 3. Scope

**In:**
- `Song` + `Joke` schema extension: add `element_id: str | None = None` AND `family: Family | None = None`
- Picker refactor: `element_id → family → theme → untheme` fallback chain
- `RewardActivityContext` extension to carry `element_id`
- New `family_for(element_id) -> Family | None` helper in `element_corpus.py`
- 103 LLM-authored element songs (Coqui-rendered MP3s); 15 M7a popular-element entries backfilled with `element_id`
- 118 LLM-authored element jokes
- Backfill `family` on M7a's 10 family songs so they serve as the family-tier fallback
- Coverage-gate lint test (every element has at least one song path + one joke path)
- End-to-end integration test (fixture-based smoke gate)
- Operator iPad UAT (real-world smoke gate)

**Out:**
- New persona, theme, or age_band taxonomy entries
- Re-authoring M7a's 25 songs (lyrics + audio kept as-is; only the `element_id`/`family` metadata fields are backfilled)
- Picture rewards (this phase touches songs + jokes only)
- Element microgame template changes (Phase N closed)
- Element_id awareness anywhere except the reward picker (e.g. NOT in joke insertion via [#194](https://github.com/aberson/toybox/issues/194) parent insert button — that's a future phase)

## 4. Impact Analysis

| File | Change |
|---|---|
| [`src/toybox/activities/song_corpus.py`](src/toybox/activities/song_corpus.py) | Add `element_id: str \| None = None` AND `family: Family \| None = None` to `Song`; extend `pick_song()` with `element_id` + `family_hint` kwargs; loader accepts both fields; `_validate_raw_entry` checks element_id format + family enum membership; `_BY_ELEMENT_ID` + `_BY_FAMILY` lookup caches built at load time |
| [`src/toybox/activities/joke_corpus.py`](src/toybox/activities/joke_corpus.py) | Same shape — `element_id` + `family` fields, picker kwargs, loader, validator, caches |
| [`src/toybox/activities/element_corpus.py`](src/toybox/activities/element_corpus.py) | Add `family_for(element_id) -> Family \| None` helper with cached `dict[str, Family]` lookup |
| [`src/toybox/activities/content_resolver.py`](src/toybox/activities/content_resolver.py) | `RewardActivityContext` gains `element_id: str \| None = None`; `_try_pick_song` + `_try_pick_joke` accept it; both implement `element_id → family → theme → untheme` chain (family-tier resolved via `family_for(ctx.element_id)`); `resolve_reward()` threads element_id through |
| [`src/toybox/api/activities.py`](src/toybox/api/activities.py) | Reward-step caller extracts the activity's "primary" element_id (first persisted step with non-null element_id) and passes it into `RewardActivityContext` |
| [`data/songs/manifest.json`](data/songs/manifest.json) | Append 103 element-song entries (each with `element_id`, `family`, `audio_path`); backfill `element_id` on M7a's 15 popular-element entries; backfill `family` on M7a's 10 family entries |
| [`data/jokes/jokes.json`](data/jokes/jokes.json) | Append 118 element-joke entries (each with `element_id` + `family`) |
| `data/songs/audio/element-song-<sym>-<n>.mp3` × 103 | New MP3s via Coqui render (not committed; local-only per M7a convention) |
| `scripts/generate_element_song_corpus.py` (new) | LLM-author 103 element songs via `AnthropicClient` (OAuth + urllib) |
| `scripts/generate_element_joke_corpus.py` (new) | Same shape — 118 element jokes |
| `tests/unit/activities/test_song_corpus.py` | element_id + family field load/reject tests; M7a backfill assertions |
| `tests/unit/activities/test_joke_corpus.py` | Same |
| `tests/unit/activities/test_element_corpus.py` | `family_for()` round-trips all 118 element ids; returns None for unknown |
| `tests/unit/activities/test_content_resolver.py` | element_id picker tests; family/theme/untheme fallback assertions |
| `tests/unit/activities/test_element_reward_coverage.py` (new) | Coverage gate — every element resolves to a song path + a joke path |
| `tests/integration/test_phase_q_smoke.py` (new) | End-to-end picker contract test with fixture corpus |
| [`frontend/src/shared/types.ts`](frontend/src/shared/types.ts) | Codegen regen if reward wire shape changes (likely no change — only context-side adds) |

## 5. New Components

- **`scripts/generate_element_song_corpus.py`** — one-shot operator script. Loads [`data/elements/elements.json`](data/elements/elements.json), iterates all 118 entries, calls Claude via the existing toybox OAuth + urllib path for each, writes lyric + metadata entries into [`data/songs/manifest.json`](data/songs/manifest.json). Idempotent (strips existing `element-song-*` ids before appending). Flags: `--dry-run`, `--validate`, `--force`. Mirrors M7a's structure exactly.
- **`scripts/generate_element_joke_corpus.py`** — same shape, target [`data/jokes/jokes.json`](data/jokes/jokes.json), ids `element-joke-<symbol-lower>-<atomic-number>`. Both setup + punchline LLM-authored. Same idempotency contract.
- **118 element-song MP3s** at `data/songs/audio/element-song-<id>.mp3` (Coqui-rendered, operator-produced, **not checked into git** — same convention as M7a's audio).
- **`tests/integration/test_phase_q_smoke.py`** — fixture-driven smoke gate. Loads a 2-3 entry fake corpus, calls `resolve_reward()` with a `RewardActivityContext` carrying a known `element_id`, asserts the element-keyed entry wins. Then mutates the context to an element with no entry, asserts family-tier fallback fires. Then unset both, asserts theme fallback.

## 6. Design Decisions

### D1 — Fallback chain: `element_id → family → theme → untheme`

When a reward fires on an activity with `element_id` set, the picker first tries entries with `Song.element_id == activity.element_id`. If none match (including persona_compat + audio-present filters), look up the element's family via `family_for(element_id) -> Family | None` (new helper in `element_corpus.py`, see D8) and try entries with `Song.family is family_hint` — identity, not equality, per code-quality.md §2. If still no match, fall through to the existing `theme → untheme` chain.

The family match keys on a new explicit `family: Family | None` field on `Song`/`Joke` (added in Q1, backfilled on M7a's 10 family songs in Q2) rather than an id-prefix heuristic. Rationale: M7a song ids use plural prefixes (`noble_gases_*`) while the `Family` enum uses singular slugs (`noble_gas`) — `id.startswith(f"{family}_")` would not match. An explicit field is the single source of truth (per [code-quality.md §"One source of truth for data-shape constants"](../../dev/.claude/rules/code-quality.md)) and makes the matching trivially `is`-assertable in tests. Alternative considered: hand-maintain a `_FAMILY_PREFIXES` constant mapping `Family.noble_gas → "noble_gases"` — rejected because it duplicates the family taxonomy in a second place. Alternative considered: skip family-tier entirely — rejected because it wastes M7a's 10 family songs.

### D8 — `family_for(element_id)` helper in `element_corpus.py`

Single function added to [`src/toybox/activities/element_corpus.py`](src/toybox/activities/element_corpus.py) (alongside existing `Element` model + corpus loader): `family_for(element_id: str) -> Family | None` builds (and caches) a `dict[str, Family]` keyed by element_id on first call, then returns `dict.get(element_id)`. Returns None for unknown ids (resolver falls through to theme tier). Importing from `element_corpus.py` keeps `content_resolver.py` free of JSON-loading code and matches the existing pattern where the resolver imports `pick_song` / `pick_joke` rather than touching corpora directly.

### D2 — LLM authoring via existing OAuth path

Generator scripts call `api.anthropic.com` through the project's existing `urllib` + OAuth bearer pattern at [`src/toybox/ai/client.py:115 AnthropicClient`](src/toybox/ai/client.py#L115). `OAuthToken` is loaded via the existing app-startup path (see [`src/toybox/ai/oauth.py`](src/toybox/ai/oauth.py)); scripts can reuse the same load mechanism the production runtime uses. No new dependency. No API key. Idempotent per-element with `--validate` re-loading through the production corpus loader to catch shape drift. Mirrors M7a's operator-run pattern. Alternative considered: out-of-band hand-prompting — rejected because it makes re-running impossible if the prompt or model improves. Alternative considered: in-build-step LLM invocation — rejected because long-running non-deterministic LLM calls inside `/build-step` violate the autonomous-build contract.

### D3 — Operator skim-review as the quality gate

Generator outputs 118+118 JSON entries; operator opens both files and skims for stinkers (unfunny jokes, awkward lyrics, science inaccuracies, accidental personification of the element-as-character per the recent template rewrite). Inline-edit fixes any bad entries before commit. Pattern matches Phase N N1 (operator skim-review of 118 distractors). Alternative considered: automated lint + spot-check — rejected because lint catches injection but not content quality; the latter is what matters here. Alternative considered: ship-and-fix-during-UAT — rejected because defects propagate to kids in real activities.

### D4 — `persona_compat: ["periodic_table", "all"]` for new entries

Element rewards fire under the Periodic Table Professor persona AND as universal fallbacks for any persona running an element activity. Matches M7a's pattern exactly. Alternative considered: `["periodic_table"]` only — rejected because cross-persona element_microgame templates would lose their reward fallback. Alternative considered: `["all"]` — rejected because the element-themed lyric reads jarring under, say, Princess persona.

### D5 — Reward id naming: `element-song-<symbol-lower>-<atomic-number>` / `element-joke-<symbol-lower>-<atomic-number>`

E.g. `element-song-h-1`, `element-joke-au-79`. Matches existing song/joke kebab-case slug convention and the element catalog's id format. Element_id field on the entry holds the same `<symbol-lower>-<atomic-number>` value, matching `activity.steps[i].element_id`. Direct dict-lookup key.

### D6 — Backfill M7a's 25 songs in-place, generate the remaining 103

The 15 popular-element entries (gold, silver, iron, helium, oxygen, hydrogen, neon, mercury, copper, uranium, sodium, calcium, carbon, nitrogen, chlorine) get `element_id` set to match their element — they become the canonical first-tier match for those elements; the Phase Q song generator skips those 15 elements so we don't duplicate work. The 10 family songs stay `element_id`-null but gain explicit `family: Family` values so the family-tier fallback resolves to them. Net new element-songs Phase Q ships: 103 (118 − 15).

### D7 — Producer→consumer drift mitigation

Per [code-quality.md "Audit wire shape when storage representation changes"](../../dev/.claude/rules/code-quality.md) and [code-quality.md "One source of truth for data-shape constants"](../../dev/.claude/rules/code-quality.md): the `element_id` field on `Song`/`Joke` AND the `element_id` field on `RewardActivityContext` AND the `element_id` field on `activity.steps[i]` are all the same shape (`<symbol-lower>-<atomic-number>`). Single source of truth: the element id format is defined once in `data/elements/elements.json` and the format regex `^[a-z]{1,3}-[0-9]{1,3}$` already lives in the M3 element-id validator. Step Q5 reuses that regex; doesn't redefine it. Step Q6 (smoke gate) asserts the producer→consumer round trip with both populated and empty corpora.

## 7. Build Steps

<!-- autofix-applied: 2026-05-19 -->
### Step Q1: Song + Joke schema — element_id + family fields
- **Problem:** Extend `Song` and `Joke` Pydantic models with two optional fields: `element_id: str | None = None` (validator regex `^[a-z]{1,3}-[0-9]{1,3}$` when present) and `family: Family | None = None` (the `Family` StrEnum from `element_corpus.py`; pydantic coerces string values via `Family(value)` and rejects unknowns). Loaders accept both fields; existing entries continue loading. Add unit tests covering: element_id accepts valid + rejects malformed; family accepts the 10 enum slugs + rejects unknowns; both fields default to None; co-presence permitted (an entry may set both — e.g. a transition-metal-specific element).
- **Status:** DONE (2026-05-19)
- **Issue:** #196
- **Flags:** --reviewers code
- **Produces:** modified `src/toybox/activities/song_corpus.py`, `src/toybox/activities/joke_corpus.py`, `tests/unit/activities/test_song_corpus.py`, `tests/unit/activities/test_joke_corpus.py`
- **Done when:** `uv run pytest tests/unit/activities/test_song_corpus.py tests/unit/activities/test_joke_corpus.py -k "element_id or family"` passes ≥10 new test cases; existing tests still pass.
- **Depends on:** none

<!-- autofix-applied: 2026-05-19 -->
### Step Q2: Backfill M7a — element_id on 15 popular-element songs + family on 10 family songs
- **Problem:** Two backfills in one step. (a) Add `element_id` to each of M7a's 15 popular-element entries (gold→au-79, silver→ag-47, iron→fe-26, helium→he-2, oxygen→o-8, hydrogen→h-1, neon→ne-10, mercury→hg-80, copper→cu-29, uranium→u-92, sodium→na-11, calcium→ca-20, carbon→c-6, nitrogen→n-7, chlorine→cl-17). (b) Add `family` (singular enum slug) to each of M7a's 10 family entries: `noble_gases_drift_quiet → noble_gas`, `halogens_make_friends → halogen`, `alkali_metals_go_zoom → alkali_metal`, `alkaline_earths_keep_strong → alkaline_earth`, `transition_metals_shiny_song → transition_metal`, `post_transition_metals_bendy → post_transition_metal`, `metalloids_in_between → metalloid`, `nonmetals_everywhere → nonmetal`, `lanthanides_glow_soft → lanthanide`, `actinides_radiate_far → actinide`. Add tests asserting all 15 popular ids + all 10 family slugs round-trip through `load_songs()`.
- **Issue:** #197
- **Flags:** --reviewers code
- **Produces:** modified `data/songs/manifest.json`, new assertions in `tests/unit/activities/test_song_corpus.py`
- **Done when:** `uv run pytest tests/unit/activities/test_song_corpus.py -k "m7a_backfill"` passes ≥2 new test cases (one per backfill); corpus loads cleanly through `load_songs()`.
- **Depends on:** Q1

<!-- autofix-applied: 2026-05-19 -->
### Step Q3: Element-song generator script
- **Problem:** Author `scripts/generate_element_song_corpus.py` — a one-shot operator CLI mirroring M7a's structure ([`scripts/generate_element_song_manifest.py`](scripts/generate_element_song_manifest.py)). Loads `data/elements/elements.json`, iterates the 103 elements NOT already in M7a's popular-element set, calls Claude via [`src/toybox/ai/client.py:115 AnthropicClient`](src/toybox/ai/client.py#L115) (`AnthropicClient(token).complete_text(...)` using `OAuthToken` from `oauth.py`) with a prompt that produces a 4-8 line kid-friendly rhyme per element, writes JSON entries into `data/songs/manifest.json` with: `id: element-song-<sym>-<n>`, `element_id: <element.id>`, `family: <element.family>` (also set, for defense-in-depth picker matching), `audio_path: audio/element-song-<sym>-<n>.mp3` (required for K11 renderer to land MP3s at the expected path; matches existing K11 audio-path convention), `persona_compat: ["periodic_table", "all"]`, `theme: silly` or `music` (per `fun_fact` keyword match, matching M7a's heuristic), `age_band: 3-5`. Idempotent (strip existing `element-song-*` ids before appending; M7a's 15 backfilled entries use different ids and are preserved). Flags: `--dry-run`, `--force`, `--validate`, `--output`. Authors the script only — does NOT invoke it (LLM calls live in Q7).
- **Issue:** #198
- **Flags:** --reviewers code
- **Produces:** new `scripts/generate_element_song_corpus.py`
- **Done when:** `uv run python scripts/generate_element_song_corpus.py --dry-run` succeeds without making network calls (script structure renders the planned JSON with `audio_path` set); unit test confirms the prompt builder produces valid JSON-shaped output for a mocked Claude response, including non-empty `audio_path`.
- **Depends on:** Q1

<!-- autofix-applied: 2026-05-19 -->
### Step Q4: Element-joke generator script
- **Problem:** Author `scripts/generate_element_joke_corpus.py` — same shape as Q3 but for jokes. Iterates all 118 elements (no M7a backfill exists for jokes), prompts Claude via `AnthropicClient` for setup+punchline per element, writes entries with: `id: element-joke-<sym>-<n>`, `element_id: <element.id>`, `family: <element.family>`, `persona_compat: ["periodic_table", "all"]`, `theme: silly`, `age_band: 3-5`, `optional_toy_slot: false`. Idempotent strip+append on the `element-joke-*` prefix.
- **Issue:** #199
- **Flags:** --reviewers code
- **Produces:** new `scripts/generate_element_joke_corpus.py`
- **Done when:** `uv run python scripts/generate_element_joke_corpus.py --dry-run` succeeds; unit test confirms prompt builder shape and that every generated entry sets element_id + family.
- **Depends on:** Q1

<!-- autofix-applied: 2026-05-19 -->
### Step Q5: Picker refactor + element_id threading + family_for helper
- **Problem:** Four coordinated changes.
  - **(a)** Extend `RewardActivityContext` in `src/toybox/activities/content_resolver.py` with `element_id: str | None = None`. Backwards-compatible (keyword default).
  - **(b)** Add `family_for(element_id: str) -> Family | None` helper to `src/toybox/activities/element_corpus.py`. Builds a cached `dict[str, Family]` on first call by iterating the loaded element corpus; returns `dict.get(element_id)`. Cache invalidates with the existing element-corpus cache hook.
  - **(c)** Extend `pick_song` and `pick_joke` with two new kwargs: `element_id: str | None = None` and `family_hint: Family | None = None`. When `element_id` is provided, only candidates with `Song.element_id == element_id` qualify. When `family_hint` is provided (and `element_id` is not), only candidates with `Song.family is family_hint` qualify. Both kwargs ANDed with existing `persona_compat` + `theme` + `age_band` + `require_audio` filters. Performance: build `_BY_ELEMENT_ID: dict[str, list[Song]]` and `_BY_FAMILY: dict[Family, list[Song]]` caches at corpus-load time so per-pick is O(1) lookup.
  - **(d)** Modify `_try_pick_song` and `_try_pick_joke` to implement the chain: element_id pick → if None and ctx.element_id is set, resolve `family_hint = family_for(ctx.element_id)` and pick by family → if still None, fall through to existing theme-then-untheme. Update `src/toybox/api/activities.py`'s reward-step caller to extract the activity's "primary" element_id (first persisted step with non-null element_id) and pass it into `RewardActivityContext`.
- **Issue:** #200
- **Flags:** --reviewers code
- **Produces:** modified `src/toybox/activities/content_resolver.py`, `src/toybox/activities/song_corpus.py`, `src/toybox/activities/joke_corpus.py`, `src/toybox/activities/element_corpus.py`, `src/toybox/api/activities.py`; new unit tests asserting picker contract (element match wins; family-tier fallback fires for an element whose family has a song but no element-specific entry; theme fallback fires when neither; untheme fallback fires when no theme matches); test that `family_for()` returns the right Family for all 118 elements and None for an unknown id.
- **Done when:** `uv run pytest tests/unit/activities/test_content_resolver.py tests/unit/activities/test_element_corpus.py -k "element_id or family"` passes ≥8 new test cases; existing reward-resolver tests still pass; identity check (`is`, not `==`) used wherever Family values are compared.
- **Depends on:** Q1

### Step Q6: Fixture-based end-to-end smoke gate
- **Problem:** Add `tests/integration/test_phase_q_smoke.py` — a real-end-to-end smoke gate that loads a small in-memory corpus (3 element songs + 2 family songs + 1 generic-theme song; 3 element jokes + 1 generic-theme joke), then calls `resolve_reward()` through the API layer (or directly if simpler) with `RewardActivityContext` carrying known element_ids. Assertions in this order: (a) when element_id matches a corpus entry, that entry wins; (b) when element_id has no element-keyed entry but the family does, the family-tier song wins; (c) when neither element nor family has an entry, theme fallback fires; (d) when nothing matches, untheme fallback fires. This is the producer→consumer drift test required by code-quality.md.
- **Issue:** #201
- **Flags:** --reviewers code
- **Produces:** new `tests/integration/test_phase_q_smoke.py`
- **Done when:** `uv run pytest tests/integration/test_phase_q_smoke.py -v` passes all four assertions on a single run. ≥4 tests.
- **Depends on:** Q5

<!-- autofix-applied: 2026-05-19 -->
### Step Q7: Operator runs generators + skim-reviews + commits corpus
- **Type:** operator
- **Problem:** Operator runs Q3 and Q4 generator scripts (live LLM calls): `uv run python scripts/generate_element_song_corpus.py --validate` then `uv run python scripts/generate_element_joke_corpus.py --validate`. Inspect both modified files. Skim all new entries (103 songs + 118 jokes). For any individual stinker (unfunny joke, awkward lyric, science inaccuracy, element-as-character personification), **inline-edit the entry's JSON content**. If a systemic quality issue surfaces (e.g. the generator's prompt produces consistently weak lyrics across an entire family), **do NOT edit the generator script as part of this step**; instead file a follow-up issue and the conditional Q7b step will revise the prompt + regenerate. This step is content-only: operator touches JSON in `data/` and writes the run doc. Estimated time: 30-90 min (LLM calls ~10-20 min + skim 30-60 min).
- **Issue:** #202
- **Flags:**
- **Produces:** content updates to `data/songs/manifest.json` (+103 entries) and `data/jokes/jokes.json` (+118 entries) — content data only, NOT code artifacts; commit with message `feat(phase-q): element-song + element-joke corpora authored + reviewed`. If systemic prompt-quality issues found, additionally write `documentation/findings/phase-q-prompt-issues.md` summarizing the pattern — that file's existence is Q7b's predicate.
- **Done when:** `uv run pytest tests/unit/activities/test_song_corpus.py tests/unit/activities/test_joke_corpus.py` passes; operator confirms skim PASS (or files prompt-issues.md) in run doc at `documentation/runs/2026-05-19-phase-q-corpus-review.md`.
- **Depends on:** Q3, Q4

<!-- autofix-applied: 2026-05-19 -->
### Step Q7b: Conditional — generator prompt revision + regenerate
- **Type:** conditional
- **Condition:** test -s documentation/findings/phase-q-prompt-issues.md
- **Problem:** Only runs if Q7 surfaced systemic prompt-quality issues (file `documentation/findings/phase-q-prompt-issues.md` exists and is non-empty). Read the operator's findings, revise `scripts/generate_element_song_corpus.py` and/or `scripts/generate_element_joke_corpus.py` prompt strings to address the systemic issue, then rerun the affected generator(s) with `--force` so the corpus is re-authored. After regenerate, surfaces back to Q7 (operator re-skim) — loop until skim PASS. The looping happens by the operator restarting the build-phase at Q7 after this step lands.
- **Issue:** #203
- **Flags:** --reviewers code
- **Produces:** modified `scripts/generate_element_song_corpus.py` and/or `scripts/generate_element_joke_corpus.py` (prompt-string changes only); regenerated corpus entries in `data/songs/manifest.json` / `data/jokes/jokes.json`
- **Done when:** the prompt-issues.md findings are addressed in the revised prompt; regenerated entries load cleanly through `load_songs()` / `load_jokes()`; operator deletes `documentation/findings/phase-q-prompt-issues.md` (so the predicate flips false for any future re-run). Re-skim is operator-discretionary — Q7.5 coverage gate is the automated final check.
- **Depends on:** Q7

<!-- autofix-applied: 2026-05-19 -->
### Step Q7.5: Coverage-gate test
- **Problem:** Add `tests/unit/activities/test_element_reward_coverage.py` — lint-style test that walks every element in `data/elements/elements.json` and asserts each has at least one corpus path for a song reward AND at least one path for a joke. Specifically: for each element, EITHER `load_songs()` contains an entry with `element_id == <element.id>` OR `load_songs()` contains an entry with `family is <element.family>`; same shape for jokes. Catches generator-script regressions where the LLM silently skipped an element or wrote a malformed entry that fails to load. Runs at every CI invocation; no LLM cost. Positioned AFTER Q7 (operator corpus generation) so the test runs against a populated corpus — running it earlier would fail trivially on the empty pre-Q7 state.
- **Issue:** #204
- **Flags:** --reviewers code
- **Produces:** new `tests/unit/activities/test_element_reward_coverage.py`
- **Done when:** `uv run pytest tests/unit/activities/test_element_reward_coverage.py -v` returns 2 tests passing (one for songs, one for jokes); on a deliberately-broken corpus that's missing element X, the test FAILS with a clear "element X has no song path" message.
- **Depends on:** Q7, Q7b (if Q7b ran, its regenerated corpus is what Q7.5 validates; if Q7b skipped, Q7.5 validates Q7's original corpus)

### Step Q8: Operator renders 118 new MP3s via Coqui TTS
- **Type:** operator
- **Problem:** Operator activates the K11 Coqui venv (existing from K11/M7b: `.coqui-venv\Scripts\activate`) and runs `python scripts/generate_song_corpus.py` to render audio for all new `element-song-*` entries. The K11 script already skips existing MP3s, so only the 103 new entries render. Estimated time: 30-90 min on CPU. Commits resulting MP3s into `data/songs/audio/` — but per project convention audio files are **not** checked into git (M7a precedent); operator confirms files are present locally + writes a run-doc entry saying "rendered, present locally, not committed per audio-convention".
- **Issue:** #205
- **Flags:**
- **Produces:** 103 new `data/songs/audio/element-song-*.mp3` files (local-only); run-doc note at `documentation/runs/2026-05-19-phase-q-audio-render.md` confirming presence + file count
- **Done when:** `Get-ChildItem data\songs\audio\element-song-*.mp3 | Measure-Object | Select-Object Count` returns 103 (matches the 103 new song entries from Q7).
- **Depends on:** Q7

### Step Q9: Operator iPad UAT — real-world smoke gate
- **Type:** operator
- **Problem:** With backend + frontend running, operator triggers an element activity (e.g. a meet_element template for Titanium, or an element_microgame for Iron) on the iPad kiosk, advances through to the reward step, confirms the kid sees a **Titanium-specific** song or joke (lyric/punchline references titanium-spaceship, titanium-strong, etc.), not a family song and not a random theme song. Repeat for ≥3 elements covering different families (one transition metal, one halogen, one noble gas). Then trigger an element with NO custom entry (use Bismuth or one of the rarer ones the LLM may have skipped) — confirm the family-tier fallback fires (e.g. a post-transition-metals family song). Then trigger a non-element activity — confirm theme-based picking still works.
- **Issue:** #206
- **Flags:**
- **Produces:** run doc at `documentation/runs/2026-05-19-phase-q-uat.md` with PASS/FAIL per element + free-form observations
- **Done when:** operator records PASS verdict in the run doc with verification screenshots; any FAIL surfaces as a follow-up issue.
- **Depends on:** Q5, Q7, Q7.5, Q8

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| **LLM-author quality** | 221 LLM-generated entries may include duds (unfunny, awkward, scientifically wrong, element-as-character personification) | Q7 operator inline-edit for individual stinkers; conditional Q7b revises generator prompt + regenerates if systemic quality issue surfaces |
| **LLM token cost + wall clock** | 226 LLM calls in Q7 — ~5s wall clock each ≈ 20 min; token cost roughly 10-30k tokens depending on prompt length and Claude model | Acceptable for one-time corpus authoring; operator accepts cost before invoking Q7 |
| **Audio render time** | 103 × ~10s = ~17 min on modern CPU, longer on older hardware | Q8 operator runs in background; not blocking |
| **Producer→consumer drift on element_id + family** | Both fields are shape constants shared across 5+ surfaces (`Element.id` / `Family` enum / `Song.element_id` / `Song.family` / `Joke.element_id` / `Joke.family` / `activity.steps[i].element_id` / `RewardActivityContext.element_id`) | Single source of truth per [code-quality.md §2](../../dev/.claude/rules/code-quality.md): `Family` StrEnum + element_id regex defined ONCE each (in `element_corpus.py` and the M3 step-id validator respectively); every consumer imports rather than redefines; Q6 fixture-based smoke gate + Q7.5 coverage gate + Q9 iPad UAT cover the round trip |
| **Picker performance** | 1000+ corpus entries × every reward fire | Q5 builds `_BY_ELEMENT_ID: dict[str, list[Song]]` + `_BY_FAMILY: dict[Family, list[Song]]` caches at load time; per-pick is O(1) lookup + filter |
| **Coqui voice consistency** | New element-songs render via XTTS-v2 but operator may have updated the model since M7a | Q8 operator notes the model id in run doc; if it differs from M7a, accept slight voice drift as known cost |
| **Wire shape regression** | `RewardActivityContext` extension is internal to the resolver; `ResolvedReward` wire shape unchanged | Pre-commit codegen hook catches any drift; expected no-op |
| **Activity → reward element_id source** | `element_id` lives on `activity.steps[i]` — picker has to choose which step's element_id to use | Q5 picks the activity's "primary" element_id = first persisted step row whose `element_id IS NOT NULL` (matches M3 element_id surface contract). Documented in §2 + Q5 PR description |
| **Q7 quality regress loop** | If Q7b is invoked, operator re-skims after regenerate — could loop if each prompt revision introduces a new pathology | Bound the loop: after 2 Q7b iterations, operator either accepts the corpus as-is + files defect issues for remaining stinkers, OR escalates the design (e.g. switch to operator-author for the remaining elements) |

## 9. Testing Strategy

**Unit tests** (Q1, Q2, Q5):
- `Song.element_id` and `Joke.element_id` accept valid ids, reject malformed (`H-1`, `helium`, empty).
- `Song.family` and `Joke.family` accept the 10 Family enum slugs, reject unknowns.
- `pick_song(element_id=...)` returns the element-keyed entry when one matches.
- `pick_joke(element_id=...)` same.
- `pick_song(family_hint=Family.noble_gas)` matches `noble_gases_drift_quiet` AND any other entry with `family is Family.noble_gas`.
- M7a's 15 popular-element entries load with the expected `element_id`; M7a's 10 family entries load with the expected `family` enum value.
- `family_for(element_id)` returns the right `Family` for all 118 elements; returns `None` for unknown ids.

**Integration tests** (Q6):
- `resolve_reward()` with `RewardActivityContext(element_id="ti-22")` and a fixture corpus containing a titanium song returns that song.
- Same call with `element_id="bi-83"` (no element entry) but a `post_transition_metals_bendy` family song present returns the family song.
- Same call with neither element nor family match but a `themes=["space"]` and a space-themed corpus song returns the space song.
- Same call with no element/family/theme match returns the untheme fallback.

**Producer→consumer drift smoke gate** (Q6 + Q9):
- Q6: fixture-based integration test exercising the round trip with both populated and sparse corpora.
- Q9: real-world iPad UAT exercises the full producer (activity gets element_id from M3 corpus) → consumer (reward picker reads the same field) chain with real Coqui-rendered audio.

**Existing tests that might break:**
- `test_content_resolver.py`'s reward-fallback tests — `RewardActivityContext` signature extension is backwards-compatible (`element_id` defaults to None) so existing call sites don't change. If any test constructs the context positionally, refactor to keyword args.
- `test_song_corpus.py` / `test_joke_corpus.py` validator tests — the new optional field shouldn't break existing entries, but the test that asserts `extra="forbid"` rejects unknown fields will need an explicit known-field allowlist.

**How to verify end-to-end** (Q9 operator flow):
1. `uv run python -m toybox.db.migrate`
2. `uv run python -m toybox.main --host 0.0.0.0 --port 8000` (in one shell)
3. `cd frontend; npm run dev` (in another shell)
4. iPad: `http://<lan-ip>:4000/child`; parent on laptop: `http://localhost:4000/parent`
5. Parent: trigger an `element_microgame_ti_22` activity (Titanium)
6. Approve on parent; advance on kiosk through to reward step
7. Verify reward is element-specific (lyrics or punchline references titanium)
8. Repeat for ≥2 other elements covering different families
9. Trigger one rare element (e.g. `bi-83` Bismuth) with no element-keyed corpus entry; verify family fallback (`post_transition_metals_bendy` or similar) fires
10. Trigger a non-element activity (e.g. an Adventures branching template); verify theme-based picking still works
