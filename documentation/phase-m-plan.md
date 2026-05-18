# Phase M — Content depth: Periodic Table Professor + SEL (feature plan)

## 1. What this feature does

Phase M is a **content-depth phase** with two parallel tracks, both shipped through the existing branching-template + corpus + persona substrate (Phase G/K/L). Zero new step kinds; one minor schema addition (`element_id` optional field on steps); one new theme enum value.

1. **Track 1 — Periodic Table Professor expansion (all 118 elements).** Direct serve for Child B (4yo, pre-reader, Periodic-Table-fascinated). Ships an `element_corpus` data + loader, sprite-per-element via the existing F.5 image-gen pipeline, a kiosk `ElementCard` component (renders inline when a step opts in via `element_id`), 118 "Meet an Element" single-step templates auto-firing element-themed song rewards, ~30 element-family pretend-play branching templates (noble gases as "quiet kids," halogens as "friend-makers"), ~15 "shrink down" guided-journey templates, and ~25 element-themed Coqui TTS songs into the corpus.

2. **Track 2 — SEL branching templates.** Direct serve for Child A (6yo, early-reader, LOL-doll social play). Mints a new `Theme.feelings` enum value and ships ~80 SEL branching templates across four CASEL-aligned competencies (feelings-naming, perspective-taking, conflict resolution, friendship/repair). Pure content + one schema/enum change; no new engine code.

**Why now.** The May-2026 game-quality investigation (synthesis report in conversation, no doc artifact) surfaced two highest-leverage Tier-S directions: (a) PT expansion as the deepest serve for one of two real kids on the device, (b) SEL templates as the highest-evidence-base content vertical with zero engine work. Both ride on chassis fully shipped through Phase L (branching engine, role/theme taxonomies, song corpus, rewards). No infrastructure phase needed.

**Out of scope explicitly:**
- **Listen-for-answer step type** (option #1 from the investigation). Any sub-feature requiring mic-into-running-activity is deferred to a future phase. Notably: the "What's it made of?" object hunt is dropped.
- **Persona memory across sessions** (option #4). Professor Iridia remains session-scoped per current architecture.
- **Per-element safety framing.** The existing `periodic_table.json` `system_prompt` already says *"you stay safe, never suggest tasting or touching anything unfamiliar"* — that floor stands. No additional per-element safety language is authored into the 118 "Meet" templates.

## 2. Existing context

### Acronyms used in this plan

| Term | Expansion |
|---|---|
| **SEL** | Social-Emotional Learning. Cluster of skill domains (feelings vocabulary, perspective-taking, conflict resolution, friendship repair) targeted by Track 2 of Phase M. |
| **CASEL** | Collaborative for Academic, Social, and Emotional Learning. The US non-profit whose 5-competency framework (self-awareness / self-management / social awareness / relationship skills / responsible decision-making) Phase M's SEL templates loosely align to. |
| **UAT** | User Acceptance Test. The hands-on operator validation phase. In toybox, UAT = parent + kids tap through real activities on the iPad kiosk. M14 is Phase M's UAT step. |
| **PWA** | Progressive Web App. A web app installable to an iPad home screen that runs full-screen. The toybox `/child` route is installed as a PWA on the kid's iPad. |
| **TTS** | Text-To-Speech. Two TTS layers in toybox: (a) browser `speechSynthesis` (Web Speech API) for kiosk-side click-to-read narration; (b) Coqui XTTS-v2 (offline, operator-rendered) for pre-rendered song .mp3s. M4 narration is read via (a); M7 song lyrics are rendered via (b). |
| **STT** | Speech-To-Text. Whisper (faster-whisper) is the local STT model. Not used for any Phase M step (listen-for-answer is explicitly out of scope, §1). |
| **WS** | WebSocket. The kiosk receives activity updates from the backend via WS topics (e.g. `activity.state`). The "ws envelope" referenced in M13 sub-test (a) is the JSON payload wrapping a topic + data. |
| **SD 1.5** | Stable Diffusion 1.5. The open-weights diffusion model used by toybox's image-gen pipeline (Phase F.5). |
| **LCM-LoRA** | Latent Consistency Model + Low-Rank Adaptation. A LoRA adapter on top of SD 1.5 that enables 4-step inference (vs SD 1.5's native 30-50 steps), making CPU-feasible rendering on home hardware. |
| **VRAM** | Video RAM (GPU memory). The F.5 pipeline targets 8 GB VRAM hardware; SD 1.5 + LCM-LoRA fits in 4-5 GB peak. |
| **OOM** | Out-Of-Memory. A GPU OOM crash kills the image-gen process; M2 done-when asserts no OOM during the 118-element render. |
| **TDD** | Test-Driven Development. The `--tdd` flag on a build-step invokes `/build-step-tdd` instead of `/build-step` — tests-first, red-green-refactor workflow. M1 uses TDD because it's a pure data-loader (schema validation + injection guard); good fit. |

### Toybox skill chain

Phase M ships via the standard toybox skill pipeline. Each `/<name>` is a Claude Code skill at `dev/.claude/skills/<name>/SKILL.md`:

- `/plan-feature` → this plan.
- `/plan-review` → gap-check before issues get minted.
- `/plan-wrap` → clean-context check (this section is the artifact of one).
- `/repo-sync` → mint GitHub umbrella + per-step issues from the plan.
- `/build-phase --plan documentation/phase-m-plan.md` → walks the M1-M14 steps, dispatching each to `/build-step` (or `/build-step-tdd` when `Flags: --tdd`).
- `/repo-update` → after M14, commit + update README + plan.md status row + push.

Each `/build-step` runs the step's `Problem` against a fresh dev agent in a git worktree, with reviewers (configurable via `--reviewers`) gating the merge. **`--reviewers` flag values:** `auto` (just tests), `code` (4 parallel reviewers: correctness, bugs, test quality, style), `runtime` (3 evidence-based reviewers — requires `--start-cmd` + `--url`), `full` (all 7 — same requirements as runtime). Toybox's PIN-gated parent UI blocks runtime/full reviewers from doing UI evidence capture (see memory `feedback_buildstep_pin_gate_blocks_ui_evidence`), so Phase M uses `--reviewers code` everywhere.

### Step `action_slot` vocabulary

Every `text` step may carry an optional `action_slot` field. Valid values come from `ACTION_SLOTS` at [`src/toybox/image_gen/models.py:37-48`](../src/toybox/image_gen/models.py#L37) — exactly 10 values:

```
idle, pointing, looking, jumping, cheering, thinking, waving, running, sleeping, confused
```

The kiosk renders the persona's per-action sprite (from Phase F's toy-action-sprite pipeline) when an `action_slot` is set. M4 templates use `"pointing"`; M5-M6 templates use the full vocabulary as appropriate.

### `Family` StrEnum (Phase M, new in M1)

The 10 element family slugs that M1 ships as a `Family(StrEnum)` in `element_corpus.py`. These are the canonical strings stored in `data/elements/elements.json` `family` field, AND the strings M5 narration prose must match (M5 done-when):

| Slug | Display |
|---|---|
| `alkali_metal` | Alkali metals (group 1) |
| `alkaline_earth` | Alkaline earth metals (group 2) |
| `transition_metal` | Transition metals (groups 3-12) |
| `post_transition_metal` | Post-transition metals (Al, Ga, Sn, Pb, etc.) |
| `metalloid` | Metalloids (B, Si, Ge, As, etc.) |
| `nonmetal` | Reactive nonmetals (C, N, O, P, S, Se) |
| `halogen` | Halogens (group 17) |
| `noble_gas` | Noble gases (group 18) |
| `lanthanide` | Lanthanides (atomic numbers 57-71) |
| `actinide` | Actinides (atomic numbers 89-103) |

### Glossary

| Term | Definition |
|---|---|
| **Kiosk** | The `/child` route, run as an installed PWA on iPad. Renders one step at a time with a persona avatar. |
| **Persona** | The kiosk's animated presenter. 4 personas ship today: Wizard ("Marvelous"), Princess ("Lyra"), Detective ("Inspector Pip"), Periodic Table Professor ("Iridia"). Each is a JSON file at [`src/toybox/personas/library/<id>.json`](../src/toybox/personas/library/) carrying `display_name, archetype, system_prompt, avatar_image_path, role_weights, voice_profile, spontaneity_rates`. Full JSONs for all 4 are inlined under Reference below. |
| **Branching template** | A JSON activity script at [`src/toybox/activities/templates/branching/<intent>.json`](../src/toybox/activities/templates/branching/) declaring `id` (regex `^[a-z0-9][a-z0-9_]*$`, max 64 chars), `title`, `buckets`, `required_roles`, `optional_roles`, `recommended_themes`, `steps`, `ending_step` (optional). Steps can be `text | fork | song | joke`. |
| **Intent** | One of four fixed values: `boredom | request_play | request_story | request_activity`. Locked in [`_schema.json`](../src/toybox/activities/templates/_schema.json). No new intents in Phase M. |
| **Role** | One of the 10 canonical roles at [`src/toybox/activities/roles.py`](../src/toybox/activities/roles.py) (friend, quest_giver, guide_mentor, needs_saving, boss_mini_boss, big_bad_boss, frenemy, sidekick, trickster, helper_townsperson). |
| **Theme** | One of the 12 canonical themes at [`src/toybox/activities/themes.py`](../src/toybox/activities/themes.py) (adventure, magic, space, animals, vehicles, food, friendship, pirates, knights, weather, music, silly). **Phase M adds a 13th: `feelings`.** |
| **Reward step** | Phase L's terminal step kind. Server resolves a concrete picture/joke/song reward by set-intersection of `activity_themes` and `reward.tags`. Element-themed songs from M7 become reward-eligible automatically once tagged. Wire shape inlined under Reference below. |
| **Element corpus** | New in M1: bundled data at `data/elements/elements.json` (118 entries) + loader at `src/toybox/activities/element_corpus.py`. Mirrors the song_corpus / joke_corpus pattern (loader + Pydantic model + seeded picker + validator + injection guard). |
| **Element card** | New in M3: kiosk-side component that renders an element's symbol + atomic number + name + sprite inline when a step carries `element_id`. |
| **F.5 pipeline** | The image-gen path shipped in Phase F.5 — SD 1.5 + LCM-LoRA + cartoon style at 512², 4-step inference, fits in 4-5 GB VRAM on 8 GB hardware. See [`documentation/operator/image-gen-runtime.md`](operator/image-gen-runtime.md). |
| **Coqui TTS** | The XTTS-v2 model used to render song-lyric .mp3s. Render script lives at `scripts/generate_song_corpus.py` (operator-run). M7 extends the manifest; same script renders new entries. |
| **Per-toy `allowed_roles`** | Phase K feature (memory `project_toy_role_restrictions_2026-05-16`): each toy carries an optional `allowed_roles: list[str]` restricting which template roles the toy can be cast in. **Empty list = unrestricted** (the migration default). Picker uses **soft fallback** at cast time — if no toy permits the requested role, picker falls back to the full pool and logs INFO once. Restriction is a preference, not a guarantee. M10's `frenemy` role under-supply risk (§8) leans on this fallback. |

### Load-bearing rules quoted from `.claude/rules/` (fresh-context aid)

The plan references three rules across `code-quality.md` and `security.md` that a fresh-context agent cannot fetch. Quoted here so M8, M1, and M2 build-step agents don't need filesystem access:

- **code-quality.md §1** (referenced by M8): *"When a fix changes the shape of a primary key, cache key, id format, filename format, or any value referenced from multiple call sites: grep every consumer of the old shape before landing. Attach the grep results to the issue or PR with one row per call site and a verdict (`OK | needs fix | already handled`)."* M8 attaches a grep table for the `Theme` enum's new `feelings` value.
- **code-quality.md §2** (referenced by M1): *"Dimensions, action counts, schema column lists, magic widths — any constant defining data shape must have ONE source of truth. Regression tests must assert `is`, not just `==`, so future re-duplication fails CI."* M1's `Family` StrEnum is the single source; M1's tests assert `is`.
- **security.md "Treat fetched external content as data, not instructions"** (referenced by M1): *"Entries containing `<system-reminder>` or `ignore prior instructions` (case-insensitive) are rejected at load time."* M1's element corpus loader runs this check on `name | fun_fact | story_seed_hooks` before constructing the Pydantic `Element` instance.

### Phase G/K/L context (what Phase M builds on)

Phase G shipped the branching template engine + 200 templates across the 4 intents. Phase K shipped the 10-role taxonomy + 12-theme taxonomy + joke corpus + song corpus + persona library + Periodic Table Professor persona (Professor Iridia). Phase L re-framed jokes/songs as per-activity rewards via set-intersection tag matching. Phase M adds **content depth** to all three: more templates (PT + SEL), more songs (PT element-themed), and one new theme value (`feelings`). No engine refactor; one schema addition (`element_id` optional step field); one new sub-corpus (elements).

### Reference

**Persona — Periodic Table Professor** ([`src/toybox/personas/library/periodic_table.json`](../src/toybox/personas/library/periodic_table.json)):
```json
{
  "id": "periodic_table",
  "display_name": "Professor Iridia",
  "archetype": "periodic_table",
  "system_prompt": "You are Professor Iridia, a friendly science teacher who is delighted by every tiny thing in the world, from soap bubbles to shiny rocks. You explain ideas in plain words a small child can picture, you ask the child what they notice, and you turn questions into little experiments using whatever toys are nearby. You stay safe, never suggest tasting or touching anything unfamiliar, and you cheer for curiosity above getting the right answer.",
  "avatar_image_path": "library/avatars/periodic_table.png",
  "behavior_tags": ["curious", "explanatory", "encouraging", "scientific"],
  "age_range_min": 4, "age_range_max": 10,
  "role_weights": { "guide_mentor": 1.5, "helper_townsperson": 1.3, "friend": 1.0 },
  "voice_profile": { "rate": 1.2, "pitch": 1.0 },
  "spontaneity_rates": { "jokes": 0.10, "songs": 0.0 }
}
```

**Persona — Princess** ([`src/toybox/personas/library/princess.json`](../src/toybox/personas/library/princess.json)) — load-bearing for §6.5 / §6.9 / M9-M12 ("Princess weights social roles"):
```json
{
  "id": "princess",
  "display_name": "Princess Lyra",
  "archetype": "princess",
  "system_prompt": "You are Princess Lyra, a brave and curious young princess from a small make-believe kingdom called Sundappled Hollow. You are friendly and polite, you love organizing tea parties for the toys, and you encourage the child to be the hero of every story. Your tone is warm and welcoming, and you always celebrate kindness, sharing, and trying again when something is hard.",
  "role_weights": { "friend": 1.5, "sidekick": 1.5, "helper_townsperson": 1.2, "big_bad_boss": 0.3 },
  "voice_profile": { "rate": 1.0, "pitch": 1.4 },
  "spontaneity_rates": { "jokes": 0.05, "songs": 0.15 }
}
```

**Persona — Detective** ([`src/toybox/personas/library/detective.json`](../src/toybox/personas/library/detective.json)) — load-bearing for §6.5 / §6.9 / M10 ("Detective weights perspective/observation roles + frenemy"):
```json
{
  "id": "detective",
  "display_name": "Inspector Pip",
  "archetype": "detective",
  "system_prompt": "You are Inspector Pip, a cheerful little detective who carries a magnifying glass and a notebook full of doodles. You turn ordinary moments into gentle mysteries, like the case of the missing sock or the puzzle of the wiggling pillow. You ask the child to look closely, gather clues together, and you celebrate every guess as a clever idea, never wrong, only one step closer to the answer.",
  "role_weights": { "quest_giver": 1.3, "helper_townsperson": 1.2, "frenemy": 1.3, "sidekick": 1.0 },
  "voice_profile": { "rate": 1.1, "pitch": 0.9 },
  "spontaneity_rates": { "jokes": 0.0, "songs": 0.0 }
}
```

**Persona — Wizard** ([`src/toybox/personas/library/wizard.json`](../src/toybox/personas/library/wizard.json)) — informational; not biased toward by any Phase M template, but referenced as the "off-character fallback" in §6.9:
```json
{
  "id": "wizard",
  "display_name": "Marvelous the Wizard",
  "archetype": "wizard",
  "system_prompt": "You are Marvelous the Wizard, a kindly and slightly forgetful old magician with a tall pointy hat and a crinkly smile. You speak gently, sprinkle your sentences with playful made-up spell words like ozzlebop and shimmershine, and you love turning everyday toys into tiny adventures. You never scare children, never use real-world danger, and you always invite the child to suggest the next bit of magic.",
  "role_weights": { "quest_giver": 1.5, "guide_mentor": 1.5, "big_bad_boss": 1.2, "frenemy": 1.1 },
  "voice_profile": { "rate": 0.9, "pitch": 0.7 },
  "spontaneity_rates": { "jokes": 0.10, "songs": 0.05 }
}
```

**Implication for Phase M's persona-bias mechanism** (§6.9):
- M4-M6 PT templates declare `required_roles: ["guide_mentor"]`. Personas weighting `guide_mentor` ≥ 1.0: Professor Iridia (1.5), Wizard (1.5). Persona picker chooses between these two; tie-break is by other role weights or seed. **Wizard is a meaningful fallback risk for PT templates** — both have guide_mentor=1.5. To bias more strongly toward Professor Iridia, M5-M6 templates may add `optional_roles: ["helper_townsperson"]` (PT weights 1.3, Wizard does not weight) to tip the picker.
- M9 / M11 / M12 SEL templates declare `required_roles: ["friend"]`. Personas weighting `friend` ≥ 1.0: Princess (1.5), Professor Iridia (1.0). **Princess is heavily favored.** Detective and Wizard do not weight `friend`.
- M10 declares `required_roles: ["friend", "frenemy"]`. Only **Detective** weights both (friend implicit at 0, frenemy 1.3). Princess + Iridia do not weight frenemy; Wizard weights frenemy 1.1. Detective will be the dominant pick; Wizard is the fallback.
- M13 sub-test (h) asserts the bias empirically on M4 templates: Professor Iridia >50% across 20 seeded runs.

**Phase L reward step shape** ([`src/toybox/api/activities.py`](../src/toybox/api/activities.py) — runtime `activity_steps` row, kind `"reward"`):
```jsonc
// Inserted after the last regular step + ending_step, at most once per activity.
// reward_type is chosen by parent at approve time, persisted on activities.reward_type.
{
  "kind": "reward",
  "reward_type": "picture" | "joke" | "song" | "random" | "none",  // matches activities.reward_type
  "text": "<resolved content: image_url for picture, joke setup for joke, song title for song>",
  "step_template_id": null,  // reward steps have no source template
  "seq": <last_seq + 1>,
  "metadata": {
    // For picture: { "reward_id": <slug>, "image_url": "/api/static/images/rewards/<id>.<ext>", "animation": "shine|jump|spin|pulse|wobble|float" }
    // For joke:    { "joke_id": "why-chicken-crossed", "punchline": "..." }
    // For song:    { "song_id": "rocket-launch-countdown", "audio_url": "/api/static/songs/audio/rocket-launch-countdown.mp3", "duration_seconds": 12 }
  }
}
```
Fall-back chain: if chosen reward type is empty (no active picture rewards uploaded, or `jokes_enabled=false`, etc.), server falls through `picture → joke → song → no reward`. The `none` reward_type explicitly disables the reward step. Phase L code path: `_terminal_advance` at `api/activities.py:~3700` calls `resolve_reward()`, appends the reward step + emits `activity.state` ws envelope.

M4 templates' inline `ending_step: {kind: "song", auto: true}` fires the song picker (an ending-step song, NOT a reward step). The Phase L reward step then appends AFTER the ending_step — so a Phase M Meet-an-Element activity typically ends with: text-step → ending_step song → reward-step song (or picture/joke if parent overrode the reward_type).

**Template schema** ([`src/toybox/activities/templates/_schema.json`](../src/toybox/activities/templates/_schema.json)) — Phase M extends `step.properties` with one optional field:
```jsonc
"element_id": {
  "type": ["string", "null"],
  "pattern": "^[a-z]{1,3}-[0-9]{1,3}$",
  "description": "Phase M: opt-in element card. References an entry in data/elements/elements.json by composite id <symbol-lower>-<atomic_number>, e.g. au-79, h-1, u-92. Kiosk renders ElementCard inline above the step text. Validator (toybox.activities._validator) checks the referenced element exists at template load time."
}
```

**Theme enum** ([`src/toybox/activities/themes.py`](../src/toybox/activities/themes.py)) — Phase M adds one value:
```python
class Theme(StrEnum):
    # ... existing 12 ...
    feelings = "feelings"  # Phase M
```
And `THEME_DISPLAY_NAMES[Theme.feelings] = "Feelings"`. The single-source-of-truth pattern from [`code-quality.md`](../../.claude/rules/code-quality.md) §2 means [`_schema.json`](../src/toybox/activities/templates/_schema.json) `theme` enum array gains `"feelings"` in the same commit. Persona `role_weights` are unchanged — no persona is theme-tagged.

**Song corpus shape** ([`src/toybox/activities/song_corpus.py`](../src/toybox/activities/song_corpus.py)) — Manifest at [`data/songs/manifest.json`](../data/songs/manifest.json), entries follow `{id, title, audio_path, duration_seconds, theme, age_band, persona_compat, license, credit, lyrics}`. M7 adds ~25 entries with `theme="silly"` or `theme="music"` (no science theme exists; we don't mint one in Phase M to limit theme drift) and `persona_compat=["periodic_table", "all"]` so reward matching surfaces them for activities with Professor Iridia in the persona slot.

**Reward matching** ([`src/toybox/api/activities.py` Phase L code](../src/toybox/api/activities.py)) — Set-intersection between `activity_themes` and `reward.tags`. Element-themed templates declare `recommended_themes: ["music", "silly"]` (or any 1-2 themes appropriate to the element's vibe); element-themed songs carry matching tags. The L-pattern means no special wiring — songs become rewards automatically once tagged.

**Validator** ([`src/toybox/activities/_validator.py`](../src/toybox/activities/_validator.py)) — Phase M extends:
- `element_id` references must resolve to a loaded element (cross-corpus validation).
- *(No template-side persona gating.* Persona is chosen at propose time by the existing picker; binding to Professor Iridia happens via `role_weights` bias — see §6.9. Templates do not declare `persona_compat`; that field exists on corpus entries only — [`song_corpus.py:156`](../src/toybox/activities/song_corpus.py#L156), [`joke_corpus.py:15`](../src/toybox/activities/joke_corpus.py#L15) — not on templates.)

**Persona-template binding mechanism** ([`src/toybox/activities/content_resolver.py:854-932`](../src/toybox/activities/content_resolver.py#L854) `assign_role_slots`) — Personas declare `role_weights: {role_name: float}` in their library JSON. Templates declare `required_roles: [...]` and `optional_roles: [...]`. The proposer selects a persona partly by how well its role_weights align with a template's required_roles. Periodic Table Professor weights `guide_mentor: 1.5, helper_townsperson: 1.3, friend: 1.0`. A template declaring `required_roles: ["guide_mentor"]` will tend to receive her. This is the only Phase M persona-binding mechanism — no new template field.

**Ending-step mechanism** ([`_validator.py:26,36-37`](../src/toybox/activities/_validator.py#L26)) — Templates carry a top-level `ending_step` field (NOT a step in the `steps` array): `{kind: "song" | "joke", auto: true | corpus_id: str}`. The engine renders ending_step after the last regular step. **Phase L's reward step appends AFTER `ending_step`** — they coexist (reward is a third terminal, not a replacement). Among existing templates, 227/250 boredom templates carry `ending_step: {kind: "song", auto: true}`; 66 use `{kind: "joke"}`; 23 omit. Phase M template authoring guidance below follows this precedent.

**Image-gen pipeline** ([`documentation/operator/image-gen-runtime.md`](operator/image-gen-runtime.md)) — Tier B (SD 1.5 cartoon at 512², 4-step LCM-LoRA). The active image-gen entry point is [`src/toybox/image_gen/worker.py`](../src/toybox/image_gen/worker.py) (job worker that renders per-toy action sprites). The F.5 setup scripts at [`scripts/f5_*.py`](../scripts/) (`f5_download_sd15.py`, `f5_download_lcm.py`, `f5_download_cartoon_checkpoint.py`, `f5_load_smoke.py`, `f5_generate_templates.py`) cover loader + smoke + the existing rendering path. M2's `scripts/generate_element_sprites.py` reuses `worker.py`'s pipeline plumbing + the F.5 SD1.5+LCM-LoRA loader pattern from `f5_load_smoke.py`, with a per-element prompt template (see §5.2).

### Open work and dependencies

- **Working tree clean** at `master 60f202c`. No open Phase M precursor work.
- **Phase E (local model + tool-loop) is in-flight** but does NOT touch any Phase M surface (templates, corpus, kiosk component). No coordination needed.
- **Issue #137 + #138** (Phase K follow-ups, both cosmetic) remain non-blocking.

## 3. Scope

### In scope

- 118 element data entries + loader + injection guard + tests.
- 118 element sprites rendered via existing F.5 pipeline.
- `ElementCard` kiosk component + `element_id` optional step field + schema/validator update.
- 118 "Meet an Element" single-step templates (request_activity).
- ~30 element-family pretend-play branching templates (request_play).
- ~15 "shrink down" guided-journey branching templates (request_story).
- ~25 element-themed song corpus entries + Coqui TTS audio renders.
- New `Theme.feelings` enum value + schema/display-name updates + downstream audit.
- ~20 feelings-naming branching templates (request_story).
- ~20 perspective-taking branching templates (request_play).
- ~25 conflict-resolution branching templates (request_play + request_activity).
- ~15 friendship/repair branching templates (request_play).
- Smoke gate exercising each new content category end-to-end.
- iPad UAT with both children on a curated subset.

### Out of scope

- **Listen-for-answer step type** — would be a new `kind` enum value + new endpoint + new kiosk UI; deferred. Drops the "What's it made of?" object hunt sub-feature.
- **Persona memory** — no cross-session state for Professor Iridia or any persona.
- **New intents** — schema's 4-intent enum stays locked; all Phase M templates fit one of the existing four.
- **New personas** — Professor Iridia is the only PT persona; no element-family sub-personas authored.
- **Per-element safety framing** — relies on the existing `system_prompt`. No per-element "don't touch mercury" templates.
- **New trigger phrases** — no Phase M edits to [`data/triggers.json`](../data/triggers.json) or [`src/toybox/triggers/defaults.json`](../src/toybox/triggers/defaults.json). Existing intent triggers cover the new content.
- **Parent-facing element browser** — no parent UI to enumerate elements; templates surface via existing propose flow only.

## 4. Impact analysis

| File / module | Nature of change | Driver |
|---|---|---|
| `data/elements/elements.json` | **NEW** — 118 entries | M1 |
| `data/elements/_credits.md` | **NEW** — factoid sources | M1 |
| `src/toybox/activities/element_corpus.py` | **NEW** — loader, model, picker, injection guard | M1 |
| `tests/unit/test_element_corpus.py` | **NEW** — schema + injection + picker tests | M1 |
| `scripts/generate_element_sprites.py` | **NEW** — per-element sprite render script | M2 |
| `data/images/elements/<id>.png` × 118 | **NEW** — rendered sprites | M2 |
| `frontend/src/child/components/ElementCard.tsx` | **NEW** — kiosk inline element card | M3 |
| `frontend/src/child/components/ElementCard.css` | **NEW** — styling | M3 |
| `frontend/src/child/components/StepCard.tsx` | **MODIFY** — render ElementCard when step.element_id present | M3 |
| `frontend/src/shared/types.ts` | **MODIFY** — codegen picks up `element_id` field on `ActivityStepResponse` | M3 |
| `src/toybox/activities/models.py` | **MODIFY** — `ActivityStep.element_id: str \| None` field | M3 |
| `src/toybox/activities/templates/_schema.json` | **MODIFY** — `step.properties.element_id` + add `"feelings"` to theme enum | M3 + M8 |
| `src/toybox/activities/_validator.py` | **MODIFY** — element_id resolves to loaded element (no persona-side gating; see §6.9) | M3 |
| `CLAUDE.md` (toybox project) | **MODIFY** — Gotchas + Directory layout reflect `element_id` step field + `data/elements/` corpus + new `feelings` theme | post-M14 (in `/repo-update`) |
| `src/toybox/activities/templates/branching/request_activity.json` | **MODIFY** — append 118 "Meet" templates + ~12 conflict-resolution templates | M4 + M11 |
| `src/toybox/activities/templates/branching/request_play.json` | **MODIFY** — append ~30 element-family + ~20 perspective + ~13 conflict + ~15 friendship templates | M5 + M10 + M11 + M12 |
| `src/toybox/activities/templates/branching/request_story.json` | **MODIFY** — append ~15 shrink-down + ~20 feelings-naming templates | M6 + M9 |
| `data/songs/manifest.json` | **MODIFY** — append ~25 element-themed entries | M7 |
| `data/songs/audio/<id>.mp3` × ~25 | **NEW** — Coqui TTS-rendered audio | M7 |
| `src/toybox/activities/themes.py` | **MODIFY** — `Theme.feelings` enum + display name | M8 |
| `tests/unit/test_themes.py` (if exists; else new) | **MODIFY/NEW** — assert feelings round-trips through enum + display + schema | M8 |
| `tests/integration/test_phase_m_smoke.py` | **NEW** — end-to-end propose→approve→play through one of each new content category | M13 |
| `documentation/plan.md` | **MODIFY** — Status row for Phase M | post-M14 (in `/repo-update`) |
| `documentation/runs/2026-MM-DD-phase-m-uat.md` | **NEW** — UAT run doc | M14 |

**Downstream-consumer grep checklist** (per [`code-quality.md`](../../.claude/rules/code-quality.md) §1, mandatory before M8 ships):

- Every consumer of `Theme` enum members: `grep -rn "Theme\\." src/toybox/`. Likely hits: `song_corpus.py`, `joke_corpus.py`, `content_resolver.py`, `_validator.py`, frontend `types.ts` (codegen), reward matcher. Each must accept `feelings` without raising.
- Every consumer of `THEME_DISPLAY_NAMES`: parent UI theme-picker (if any), suggestion-card render path.
- `data/jokes/jokes.json` + `data/songs/manifest.json` — existing entries do not need to add `feelings` tags, but schema validators must accept the new value without rejecting old entries.

## 5. New components

### 5.1 Element data corpus (M1)

**`data/elements/elements.json`** — JSON array of 118 entries. Per-entry shape:

```jsonc
{
  "id": "au-79",               // composite id <symbol-lower>-<atomic_number>
  "symbol": "Au",              // 1-3 char element symbol (display case)
  "name": "Gold",              // common name
  "atomic_number": 79,
  "atomic_mass": 197.0,        // rounded to 1dp for kid-friendliness
  "family": "transition_metal",// one of: alkali_metal, alkaline_earth, transition_metal, post_transition_metal, metalloid, nonmetal, halogen, noble_gas, lanthanide, actinide
  "phase_at_room_temp": "solid",  // solid | liquid | gas
  "color_description": "shiny yellow",  // for sprite prompt
  "discovered_era": "ancient", // ancient | <year>
  "fun_fact": "Gold is so soft you can hammer it into sheets thin enough to see through.",
  "story_seed_hooks": [
    "treasure chests are full of {name}",
    "{name} doesn't rust, which is why old crowns still shine",
    "tiny flecks of {name} are sometimes hidden in rocks by rivers"
  ],
  "pronunciation_guide": "gold",  // optional; phonetic respelling for Web Speech TTS when the common name (M4 narration default) mangles. e.g. "praseodymium" → "pray-zee-oh-DIH-mee-um". Defaults to `name` when null/missing.
  "age_band": "3-5"            // 3-5 | 6-8 | 9-12 — narrative complexity target
}
```

**`src/toybox/activities/element_corpus.py`** — Loader mirroring `song_corpus.py`:
- `Element` frozen Pydantic model.
- `load_elements() -> tuple[Element, ...]` — cached, injection-guarded.
- `pick_element(seed, *, family=None, age_band=None) -> Element | None` — deterministic seeded picker.
- `get_element(element_id) -> Element | None` — direct lookup for validator.
- `clear_element_cache()` — test hook.
- Injection guard rejects `<system-reminder>` / `ignore prior instructions` in `name | fun_fact | story_seed_hooks` (defense-in-depth per [`security.md`](../../.claude/rules/security.md)).
- `Family` `StrEnum` with the 10 family values — single source of truth, asserted `is` not `==` per [`code-quality.md`](../../.claude/rules/code-quality.md) §2.

### 5.2 Element sprite render script (M2)

**`scripts/generate_element_sprites.py`** — Reuses the F.5 SD1.5+LCM-LoRA loader pattern from [`scripts/f5_load_smoke.py`](../scripts/f5_load_smoke.py) and the per-job rendering plumbing from [`src/toybox/image_gen/worker.py`](../src/toybox/image_gen/worker.py). Inputs: every element from `load_elements()`. For each, runs the F.5 Tier-B pipeline with prompt:
```
Professor Iridia, a friendly cartoon scientist with curly hair and round glasses, holding up a glowing card showing the element symbol "{symbol}" and the number {atomic_number}. The card glows in {color_description}. Soft watercolor background, friendly atmosphere, children's book illustration style.
```
Output: `data/images/elements/<id>.png` (512×512, ~80-120KB each). Re-renders only missing entries by default; `--force` re-renders all; `--sample N` renders only the first N (for pre-validation). Wall-clock estimate: ~10s/sprite × 118 = ~20 min on the F.5 hardware floor.

**Pre-render validation** (before the full 118-element soak): render 3 representative elements (`h-1`, `au-79`, `u-92` — covers gas / solid / radioactive) with `--sample 3` and visually confirm cartoon-style consistency. Only proceed to the full run after the 3-sample passes. This catches SD1.5+LCM prompt-length sensitivity issues in 30 seconds instead of 20 minutes.

### 5.3 Element card kiosk component (M3)

**`frontend/src/child/components/ElementCard.tsx`** — Renders inline above the step's narration text when `step.element_id` is non-null. Card layout:

```
+----------------------------------+
|  [sprite 256×256]                |
|                                  |
|         Au                       |  ← large symbol, ~120pt
|        Gold                      |  ← name, ~36pt
|         79                       |  ← atomic number, ~24pt
+----------------------------------+
```

Visual: rounded card, soft drop-shadow, pulses gently for ~1s on mount. Sprite loaded from `/api/static/elements/<id>.png` (new FastAPI static mount on `data/images/elements/`). Falls back to `/api/static/personas/library/avatars/periodic_table.png` if the element sprite 404s (graceful degradation; M2 may not have rendered every sprite at first ship).

Pre-reader accessibility: the persona's narration must speak the element name aloud as part of the step `text` (e.g. *"This is gold! Gold is so soft you can hammer it into sheets thin enough to see through."*). M4 template generation enforces this.

### 5.4 "Meet an Element" template generator (M4)

M4 is **content authoring**, not new code. The build-step agent generates 118 single-step `request_activity` templates programmatically from `elements.json`. Each template uses `ending_step` (not an inline song step) per the established 1000-template pattern:

```jsonc
// One entry per element, appended to request_activity.json
{
  "id": "meet_element_au_79",
  "title": "Meet Gold!",
  "buckets": ["always"],
  "required_roles": ["guide_mentor"],   // biases persona picker toward Professor Iridia (role_weights[guide_mentor]=1.5)
  "optional_roles": ["friend"],
  "recommended_themes": ["silly"],       // or "music" — chosen per element vibe
  "steps": [
    {
      "text": "Professor Iridia pulls a shiny card from her pocket. \"This is gold! Gold is so soft you can hammer it into sheets thin enough to see through. Treasure chests are full of gold.\"",
      "action_slot": "pointing",
      "element_id": "au-79"
    }
  ],
  "ending_step": {
    "kind": "song",
    "auto": true
  }
}
```

The `ending_step` triggers the existing song picker after the last regular step. Phase L's reward step appends after `ending_step` automatically. Element-themed songs from M7 surface as both the ending song (theme-matched via `recommended_themes`) and as the Phase L reward — by design, the corpus is large enough that the two pickers rarely collide.

Persona binding is *not* declared on the template. `required_roles: ["guide_mentor"]` biases the persona picker toward Professor Iridia via her `role_weights[guide_mentor]=1.5` (see §6.9). If Princess or another persona happens to be picked instead, the template still plays — "{Princess} pulls a shiny card from her pocket. 'This is gold!'" — slightly off-character but functional.

### 5.5 Element-family pretend-play templates (M5)

~30 multi-step branching templates that personify families. Examples:
- **Noble gases as "quiet kids at the party"** — pretend the kid is throwing a party; helium floats up to the ceiling, neon glows on the sign, argon stays in the corner being chill. Branching: invite a noble gas to play, or visit them where they are. `request_play`. theme=`friendship` + `silly`.
- **Halogens as "friend-makers"** — pretend the kid is a sodium ion who needs a chlorine ion to make table salt; branching on which halogen friend to find. theme=`friendship` + `silly`.
- **Alkali metals as "go-getters"** — pretend the kid is a lithium battery powering different toys; branches on what toy to bring to life. theme=`silly` + `magic`.
- **Transition metals as "shiny crafters"** — pretend the kid is a blacksmith picking which metal (iron / copper / silver / gold) to forge into different items. theme=`adventure` + `silly`.

Per-template structure: 4-8 steps, 2-4 forks, `required_roles: ["guide_mentor"]` (persona bias toward Professor Iridia), element_id field used at most once per template to spotlight one family member, `ending_step: {kind: "song", auto: true}`. Family names in narration prose MUST match the `Family` StrEnum values from M1 (e.g. `noble_gas` → "noble gases"; `alkali_metal` → "alkali metals"); M5 done-when asserts this.

### 5.6 "Shrink down" guided journeys (M6)

~15 branching templates patterned on Magic School Bus. The kid shrinks down inside an element or compound; persona narrates the inside view; branches on where to go next (nucleus vs electron shell, surface vs interior, etc.). `request_story`. theme=`adventure` + `magic`. element_id field used on the entry step. `required_roles: ["guide_mentor"]`. `ending_step: {kind: "song", auto: true}`.

### 5.7 Element-themed song corpus (M7)

~25 Coqui TTS songs. Lyrics authored by the build-step agent, rendered via existing `scripts/generate_song_corpus.py`. Coverage: one song per element family (10) + popular individual elements (gold, silver, iron, helium, oxygen, hydrogen, neon, mercury, copper, uranium, sodium, calcium, carbon, nitrogen, chlorine). Each ~10-25 seconds. Lyric style: short rhyme, 4-8 lines, suitable for Twinkle-Twinkle or Row-Your-Boat melodies. `persona_compat: ["periodic_table", "all"]` so they surface as rewards under PT activities.

Example lyrics for "gold-shiny":
```
Gold is shiny, gold is bright,
Gold makes crowns that catch the light!
Gold won't rust, it won't turn green,
Prettiest metal you've ever seen!
```

### 5.8 'feelings' theme (M8)

Schema additions:
- `Theme.feelings = "feelings"` in [`themes.py`](../src/toybox/activities/themes.py)
- `THEME_DISPLAY_NAMES[Theme.feelings] = "Feelings"`
- `"feelings"` added to the `theme` enum in [`_schema.json`](../src/toybox/activities/templates/_schema.json)

Downstream-consumer audit (per [`code-quality.md`](../../.claude/rules/code-quality.md) §1) is part of M8's definition of done. Specifically: the build-step agent must `grep -rn "Theme\\." src/toybox/ frontend/` and confirm every site accepts the new value without modification, or document the modification.

### 5.9 SEL templates (M9-M12)

Pure content authoring; no new components. All four content sets share:
- `required_roles: ["friend"]` (and `["friend", "frenemy"]` for M10's two-sided POV). No persona-side gating — Princess and Detective both naturally surface for friend-heavy templates via their role_weights (Princess weights social roles; Detective weights perspective/observation roles).
- recommended_themes: `["feelings"]` for M9, `["feelings", "friendship"]` for M10, `["friendship"]` for M11-M12
- `ending_step: {kind: "joke", auto: true}` (jokes feel lighter than songs after an emotional scene; matches existing pattern from Phase G's "after a heavy moment, deflate with humor" templates)
- 4-8 steps per template, 2-4 fork choices

**M9 feelings-naming examples:**
- "{friend} can't find their favorite blanket. How do they feel?" → branches: sad / worried / angry / silly. Each branch deepens the feeling with a body cue ("Their tummy feels heavy") and a coping move.

**M10 perspective-taking examples:**
- A two-act template: act 1 plays the conflict from {friend1}'s view; act 2 replays the SAME scene from {friend2}'s view, revealing the other side's reasoning.

**M11 conflict-resolution examples:**
- "{princess} and {detective} both want the last cookie." → branches: split it / find a different snack / take turns choosing tomorrow / one offers, one accepts. Each branch resolves with a feelings-name check-in.

**M12 friendship/repair examples:**
- "{friend} accidentally knocked over your block tower. They look scared." → branches: tell them you feel sad / help rebuild together / pretend it didn't happen (with a follow-up that surfaces the buried feeling).

## 6. Design decisions

### 6.1 Element id format: `<symbol-lower>-<atomic_number>`

E.g. `au-79`, `h-1`, `u-92`. Chosen over plain atomic number (`79`) or plain symbol (`au`) because:
- Symbol alone collides (no, it doesn't — symbols are unique — but the composite is more readable in template JSON).
- Number alone hides the element identity from a human reading the template source.
- Composite stays under the 64-char regex limit and matches the `^[a-z]{1,3}-[0-9]{1,3}$` validator.

Trade-off: changing the format later is a corpus-wide rewrite. Accepted.

### 6.2 No per-element safety framing

Per the answered design question. Floor stays at the persona's existing `system_prompt` ("you stay safe, never suggest tasting or touching anything unfamiliar"). Risk: a 4yo asks about mercury, the template says nothing extra, the persona reads from the floor. Mitigated by: every "Meet an Element" template is single-step + narration-only (no kid action prompted), so there's no template-level encouragement to interact with the element physically.

### 6.3 Sprite-per-element via F.5 pipeline (vs shared sprite + symbol card)

Per the answered design question. Renders 118 sprites once via M2. Storage: ~118 × 100KB = ~12MB on disk. Trade-offs:
- (+) Visual richness — each element gets a distinct card; supports Child B's collection/recognition instinct.
- (+) Reuses fully-shipped F.5 chassis; no new image-gen code.
- (-) Render time + manual review burden (118 images to spot-check).
- (-) Re-render needed if Professor Iridia's avatar style ever changes.

Mitigation: M2 supports `--force` re-render but defaults to skipping existing files, so partial re-renders are cheap. Sprite review is bundled into M2's reviewers (`--reviewers code` with a spot-check sampling pattern instead of every-image review).

### 6.4 New `feelings` theme (vs reusing `friendship`)

Per the answered design question. Real impact: enum value + schema enum + display map + downstream-consumer audit. Pattern follows Phase K's theme taxonomy work. The grep audit in M8 is load-bearing — if any consumer hard-codes the 12-theme set (e.g. a frontend Select component with literal options), it must be extended.

### 6.5 SEL templates use existing personas, not a new SEL persona

Princess and Detective are the natural anchors (Princess for social/relational, Detective for perspective-taking). Minting a "Counselor" or "Feelings Coach" persona would (a) require a new persona JSON + sprite + system prompt, (b) compete with existing personas in the proposal picker. Phase M defers that question; if SEL templates land well, a dedicated persona is a Phase N candidate.

### 6.6 Element-themed songs use existing themes (no science theme)

Songs added in M7 carry `theme=silly` or `theme=music`, not a new `theme=science`. Reason: minting a science theme would push theme-count to 14 and re-trigger the downstream-consumer audit from M8 a second time. The existing themes are loose enough to fit ("Gold is shiny" → silly fits fine).

### 6.7 No "daily card" surface — content rides existing intents

Investigation framed PT expansion partly as "Meet an Element daily cards." Phase M ships these as **single-step `request_activity` templates**, not a new surface. Activation paths:
- Existing trigger phrases for `request_activity` propose them.
- Future Phase N could add a parent-side "Show me an element today" button without retro-fitting any M-shipped content.

### 6.8 Authoring style: sequential `/build-step` per content area

Per the answered design question. Each content area is one build-step with `--reviewers code` for content quality. Trade-off vs the Phase K K16 overnight 4-agent soak: slower wall-clock (~14 sequential steps vs 4 parallel) but simpler review and cleaner per-category iteration. Plan accepts the longer wall-clock for the content-review quality benefit.

### 6.9 Persona-template binding via `role_weights`, not a new template field

Plan-review (2026-05-17) discovered that `persona_compat` does NOT exist on templates ([verified](../src/toybox/activities/models.py#L354) — zero of 1000 existing templates carry it; Template Pydantic model has no such field; no consumer reads it). It exists only on corpus entries (songs / jokes).

Rather than introduce a new template field + validator + propose-time filter (which would break Phase M's "pure content; zero engine work" promise and require backfill across the existing 1000 templates), Phase M binds personas to templates via the **existing role_weights mechanism** ([content_resolver.py:854-932](../src/toybox/activities/content_resolver.py#L854) `assign_role_slots`):

- Templates declare `required_roles`.
- Personas weight specific roles in `role_weights` (Professor Iridia: `guide_mentor: 1.5`).
- The proposer's persona picker biases toward personas whose role_weights align with the template's required_roles.

For Phase M:
- All M4/M5/M6 PT templates declare `required_roles: ["guide_mentor"]` → Professor Iridia heavily favored.
- All M9-M12 SEL templates declare `required_roles: ["friend"]` (or `["friend", "frenemy"]` for M10) → Princess + Detective naturally favored over Wizard/Professor Iridia.

Trade-off: persona binding is *probabilistic*, not strict. Wizard might occasionally narrate a "Meet an Element" template. Acceptable per the answered design question — slight off-character framing is preferable to engine scope creep.

A hard-gating field can be added in a future phase if probabilistic binding turns out to break a specific scenario in UAT.

### 6.10 Element scope: all 118 (not a starter set)

Per the answered design question. Trade-offs:
- (+) Encyclopedic completeness serves Child B's depth-of-interest in the periodic table; missing elements would be visible to a kid who already knows the table.
- (+) Programmatic template generation (M4) absorbs the long tail at zero per-element authoring cost — 90 obscure elements get the same "Meet" treatment as the 25 famous ones, drawing from M1's per-element `fun_fact` + `story_seed_hooks`.
- (+) Sprite render is cheap: ~20 min wall-clock for all 118 on F.5 hardware floor; no operator-attention burden during the soak.
- (-) Long-tail elements (Berkelium, Roentgenium, Tennessine) have weaker story hooks; M1 authoring may rely on family-based generic prose ("This is an actinide that scientists made in a special lab") for ~20 of them.
- (-) 30 multisyllabic names need `pronunciation_guide` entries (M1 authoring lift).

Alternative ruled out: a 25-element starter set with hand-crafted templates per element. Cleaner per-element quality but visibly truncates Child B's possibility space; "where's tungsten?" is a real risk for a kid who recognizes obscure elements. Hand-crafting for 25 + programmatic for 93 is not enough cleaner to justify the asymmetry.

## 7. Build steps

Each step is `/build-phase`-compatible. `**Issue:** #` lines stay blank until `/repo-init` or `/repo-sync` mints the GitHub issues.

### Step M1: Element data corpus + loader
- **Problem:** Add the element corpus: `data/elements/elements.json` with all 118 entries (id, symbol, name, atomic_number, atomic_mass, family, phase_at_room_temp, color_description, discovered_era, fun_fact, story_seed_hooks, pronunciation_guide, age_band) + `src/toybox/activities/element_corpus.py` loader (model, picker, validator, injection guard) mirroring `song_corpus.py`. Source factoids from license-clean public-domain references first (NIST atomic-weights, US-government science sites, RSC periodic table API where MIT-licensed); fall back to Wikipedia only with explicit CC-BY-SA attribution in `data/elements/_credits.md`. `pronunciation_guide` is optional per-entry; populate for any element whose `name` defeats Web Speech TTS (multisyllabic transition metals, lanthanides, actinides — ~30 entries).
- **Type:** code
- **Issue:** #153
- **Flags:** --tdd
- **Produces:** `data/elements/elements.json` (118 entries), `data/elements/_credits.md`, `src/toybox/activities/element_corpus.py`, `tests/unit/test_element_corpus.py`.
- **Done when:** `uv run pytest tests/unit/test_element_corpus.py -v` passes; `load_elements()` returns exactly 118 entries; `pick_element(seed=0, family=Family.noble_gas)` is deterministic; injection-guard rejection covered; `uv run mypy src` + `uv run ruff check .` clean.
- **Depends on:** none.
- **Status:** DONE (2026-05-18) — 30/30 element tests green, full suite 1962/2 (was 1932/2; +30 as expected), mypy clean (125 src files), ruff check clean. Ships 118-element corpus + loader + injection guard + Family StrEnum; 104 entries carry pronunciation_guide (more aggressive than ~30 target — over-providing is safer for TTS). Age-band distribution skews 9-12 (15/21/82) because most of the periodic table is genuinely 9-12 territory; 3-5/6-8 buckets correctly hold elements a child actually encounters. data/elements/ un-ignored in .gitignore (matches songs/jokes pattern).

### Step M2: Element sprite render script
- **Problem:** Build `scripts/generate_element_sprites.py` reusing the F.5 SD1.5+LCM-LoRA loader pattern from [`scripts/f5_load_smoke.py`](../scripts/f5_load_smoke.py) and the rendering plumbing from [`src/toybox/image_gen/worker.py`](../src/toybox/image_gen/worker.py). Renders one sprite per element via the F.5 Tier-B pipeline (SD 1.5 + LCM-LoRA + cartoon style at 512², 4-step). Prompt: "Professor Iridia, a friendly cartoon scientist with curly hair and round glasses, holding up a glowing card showing the element symbol '{symbol}' and the number {atomic_number}. The card glows in {color_description}. Soft watercolor background, friendly atmosphere, children's book illustration style." Output: `data/images/elements/<id>.png`. Skip existing files by default; `--force` re-renders; `--sample N` renders only the first N (for pre-validation). **Pre-render gate:** run `--sample 3` on `h-1, au-79, u-92` first; visually confirm cartoon-style consistency before launching the full 118-element soak. Then run the full script. Commit the script + the 14 "canonical" sprites (one per `Family` enum value + 4 popular individuals: gold, helium, oxygen, iron) to git as style references; gitignore the remaining 104.
- **Type:** operator (split — see Status)
- **Issue:** #154
- **Flags:** (none — operator step)
- **Produces:** `scripts/generate_element_sprites.py`, 14 canonical `data/images/elements/<id>.png` committed (one per element family + gold/helium/oxygen/iron), 104 additional sprites generated locally + gitignored, updated `.gitignore`.
- **Done when:** Pre-render 3-sample gate passes operator visual check; full script runs to completion on all 118 elements; 14 canonical sprites committed; no GPU OOM (Tier B SD 1.5 fits in 4-5 GB peak per F.5 spec); wall-clock under 45 min for the full 118-element run.
- **Depends on:** M1.
- **Status:** SPLIT 2026-05-18 per `.claude/rules/plan-and-issue-flow.md` § "Operator-type steps must not produce code artifacts". Two subtasks:
  - **M2a (code, DONE 2026-05-18, commit `71e8eed`):** ships `scripts/generate_element_sprites.py` + `.gitignore` un-ignore block for 14 canonical sprite ids. Auto-authored as orchestrator prep so the operator brief is runtime-only. No mid-build halt.
  - **M2b (operator, DEFERRED to before M14):** the 118-sprite render soak + spot-check + canonical-sprite commit. **M3-M13 do NOT block on M2b** — [`ElementCard.tsx`](../frontend/src/child/components/ElementCard.tsx) renders a 404-fallback to the Professor Iridia persona avatar when a sprite is absent, and M3 vitest covers both render paths. The operator runs M2b alongside M14 (iPad UAT) in one session so manual gates bundle. Per-element fixed seed in the script (`sha256(element.id) % 2**31`) means M2b can re-render selectively without drift.

### Step M3: ElementCard kiosk component + schema/validator wiring
- **Problem:** Add `step.element_id` optional field to `_schema.json` (regex `^[a-z]{1,3}-[0-9]{1,3}$`); update `Step` + `ActivityStep` models in [`models.py`](../src/toybox/activities/models.py); extend `_validator.py` to confirm element_id resolves via `element_corpus.get_element()` (no persona-side gating per §6.9); add a FastAPI `StaticFiles` mount for `data/images/elements/` at `/api/static/elements/` following the [`app.py:103-104`](../src/toybox/app.py#L103) `images_root()` pattern and the [`app.py:119-120`](../src/toybox/app.py#L119) `songs_audio_root()` pattern; build `frontend/src/child/components/ElementCard.tsx` (sprite + symbol + name + atomic number, pulses on mount, falls back to `/api/static/personas/library/avatars/periodic_table.png` if the element sprite 404s); wire into [`StepCard.tsx`](../frontend/src/child/components/StepCard.tsx) to render above step text when `element_id` is present. Regenerate [`frontend/src/shared/types.ts`](../frontend/src/shared/types.ts) via the [`tools/gen_types_ts.py`](../tools/gen_types_ts.py) pre-commit hook.
- **Type:** code
- **Issue:** #155
- **Flags:** --reviewers code
- **Produces:** schema diff, model diff, validator diff, `app.py` static mount diff, `ElementCard.tsx` (with inlined `<style>` block matching kiosk-components convention), Vite-bundled `assets/periodic_table_fallback.png`, `StepCard.tsx` diff, regenerated `types.ts`.
- **Done when:** Backend tests cover validator rejection of unresolved element_id (e.g. `element_id: "xx-999"`); frontend vitest covers ElementCard render-with-sprite + render-with-fallback paths; static mount serves a sprite over HTTP in a test fixture (ship a tiny synthetic PNG under `tests/fixtures/` — do NOT depend on M2b runtime output); `uv run pytest`, `uv run mypy src`, `uv run ruff check .`, `npm run typecheck`, `npm run test` all clean.
- **Depends on:** M1, M2a (✅ commit `71e8eed`). **NOT blocked on M2b sprite soak** — kiosk falls back to a Vite-bundled persona avatar on sprite 404, both render paths covered by vitest.
- **Status:** DONE (2026-05-18) iter 2/3. iter-1 review caught 2 HIGH (kiosk-state element_id leak + fallback URL with no backing mount) + 3 MEDIUM (step-kind gate, CSS convention break, missing wire-shape test) + 2 LOW. iter-2 closed all HIGH+MED in same worktree. Verifiers confirmed CLOSED. Backend 1972 pass / 3 skipped (was 1962 + 2; +10 net pass); frontend 598 pass (was 592; +6). mypy + ruff + tsc + eslint clean (bundled cleanup: removed 3 unused `type: ignore` comments in `image_gen/pipeline.py` that surfaced after `uv sync` updated transitive dep typings — unrelated to M3 surface but blocked the gate). Wire-shape integration test `test_element_id_wire_shape.py` is the canonical regression guard — drives propose→approve→advance→running with real corpus + real DB, asserts element_id + denormalized symbol/name/atomic_number reach the WS envelope at every state.

### Step M4: "Meet an Element" templates (118)
- **Problem:** Author 118 single-step `request_activity` templates, one per element from `elements.json`. Each template: id `meet_element_<id>`, title "Meet {Name}!", required_roles `["guide_mentor"]` (persona bias toward Professor Iridia via her `role_weights[guide_mentor]=1.5`), optional_roles `["friend"]`, recommended_themes `["silly"]` or `["music"]` (chosen per element vibe), one `text` step with narration weaving in the fun_fact + one story_seed_hook + `action_slot: "pointing"` + `element_id: "<id>"`, top-level `ending_step: {kind: "song", auto: true}` (NOT an inline song step in the steps array — see §5.4 example). Each narration must speak the element name aloud (pre-reader accessibility); for elements with `pronunciation_guide` set, narration uses the guide phonetic respelling alongside the formal name ("This is praseodymium — say pray-zee-oh-DIH-mee-um with me!").
- **Type:** code
- **Issue:** #156
- **Flags:** --reviewers code
- **Produces:** 118 new entries appended to `src/toybox/activities/templates/branching/request_activity.json`.
- **Done when:** `uv run pytest tests/unit/activities/test_template_loader.py -v` passes for all 118 new templates (the existing `test_existing_production_templates_still_load` test exercises every template under `templates/branching/` through the loader + validator); spot-check 12 random templates (10% sample, min 5 floor) for narration quality (element name spoken, fun_fact woven naturally, no formulaic "{element name} is a {family}" boilerplate, pronunciation_guide used where present); template count in `request_activity.json` grows by 118 (from 250 → 368).
- **Depends on:** M1, M3.
- **Status:** DONE (2026-05-18) iter 1/3 + 1 in-line style fix. 4-way code review found 0 HIGH + 1 MEDIUM (field-order break — `steps` at position 7 vs convention position 4) + 1 LOW (nice-to-have `test_every_element_has_a_meet_template` regression test for future corpus drift). Field order corrected in `_build_template` and regenerated. Final: 118 `meet_element_*` templates, request_activity.json 250 → 368, all 118 atomic numbers covered, 104/104 pronunciation guides used where corpus carries them, 9 music + 109 silly themes (corpus-driven keyword filter), 3-step shape (Pydantic `Template.steps` enforces `min_length=3` per Phase G — plan §5.4's single-step example is documented as needing a follow-up correction; the 3-step split is name+ElementCard → fun_fact → story_seed_hook). Generator script `scripts/generate_meet_element_templates.py` is idempotent (re-runs produce byte-identical output via `--force`). Backend pytest 1972 pass / 3 skipped (was 1972; +0 new tests — leans on `test_existing_production_templates_still_load`). mypy + ruff clean.

### Step M5: Element-family pretend-play templates (~30)
- **Problem:** Author ~30 multi-step branching templates personifying element families. Coverage: noble gases as "quiet kids at the party" (4 templates), halogens as "friend-makers" (4), alkali metals as "go-getters" (4), alkaline earths as "shy helpers" (3), transition metals as "shiny crafters" (5), post-transition metals as "the soft ones" (3), metalloids as "in-betweeners" (3), nonmetals as "everywhere essentials" (4). Each template: 4-8 steps, 2-4 forks, `required_roles: ["guide_mentor"]` (persona bias), optional element_id on entry step spotlighting one family member, recommended_themes drawn from the 12-theme taxonomy, `ending_step: {kind: "song", auto: true}`. Family-name strings in narration MUST match the `Family` StrEnum slugs from M1 (e.g. narration says "noble gases" matching the `noble_gas` slug, not "rare gases" or "inert gases").
- **Type:** code
- **Issue:** #157
- **Flags:** --reviewers code
- **Produces:** ~30 new entries appended to `src/toybox/activities/templates/branching/request_play.json`.
- **Done when:** Template validation passes; spot-check 5 random templates (10% of 30 with min-5 floor) for narrative coherence + age-appropriate vocabulary + family-name slug consistency; per-family coverage matches the targets above (±1).
- **Depends on:** M1, M3.

### Step M6: "Shrink down" guided-journey templates (~15)
- **Problem:** Author ~15 branching templates patterned on Magic School Bus. The kid shrinks down inside an element or compound; persona narrates the inside view; branches on where to go next (nucleus vs electron shell, surface vs interior, gas state vs liquid state). Each template: 5-9 steps, 2-3 forks, `required_roles: ["guide_mentor"]` (persona bias), element_id on entry step, recommended_themes `["adventure", "magic"]`, `ending_step: {kind: "song", auto: true}`.
- **Type:** code
- **Issue:** #158
- **Flags:** --reviewers code
- **Produces:** ~15 new entries appended to `src/toybox/activities/templates/branching/request_story.json`.
- **Done when:** Template validation passes; spot-check 5 random templates for coherent inside-the-element imagery + age-appropriate scale framing (no scary "you're a tiny speck in a vast emptiness" framing for 4yo); element coverage spans solid + liquid + gas phases.
- **Depends on:** M1, M3.

### Step M7: Element-themed song corpus (~25)
- **Problem:** Author ~25 song entries for the song corpus. Coverage: one song per element family (10 entries) + 15 popular individual elements (gold, silver, iron, helium, oxygen, hydrogen, neon, mercury, copper, uranium, sodium, calcium, carbon, nitrogen, chlorine). Each entry: short rhyme (4-8 lines), 10-25s duration, theme `"silly"` or `"music"`, age_band `"3-5"`, persona_compat `["periodic_table", "all"]`, license `"CC-BY-4.0"`, credit `"Coqui TTS XTTS-v2 (operator-rendered)"`. Append to `data/songs/manifest.json`; run `scripts/generate_song_corpus.py` to render audio.
- **Type:** code
- **Issue:** #159
- **Flags:** --reviewers code
- **Produces:** ~25 entries appended to `data/songs/manifest.json`; ~25 `.mp3` files in `data/songs/audio/` (locally; `.gitignore` handles distribution).
- **Done when:** `uv run python -c "from toybox.activities.song_corpus import load_songs; print(len(load_songs()))"` returns prior_count + 25; audio render script completes for all 25; spot-check 5 audio files for clean playback + correct lyrics; spot-check 5 lyrics for kid-friendly rhyme + no chemistry inaccuracies.
- **Depends on:** none (independent of M1-M6).

### Step M8: Mint `Theme.feelings`
- **Problem:** Add `Theme.feelings = "feelings"` to [`themes.py`](../src/toybox/activities/themes.py) + `THEME_DISPLAY_NAMES[Theme.feelings] = "Feelings"`; add `"feelings"` to the `theme` enum in [`_schema.json`](../src/toybox/activities/templates/_schema.json); regenerate frontend types. **Mandatory downstream-consumer audit per [`code-quality.md`](../../.claude/rules/code-quality.md) §1:** `grep -rn "Theme\\." src/toybox/ frontend/src/` and confirm every site accepts the new value without modification, or document the modification. Attach the grep result table to the PR description (one row per call site + verdict OK / needs-fix / handled). Add a regression test asserting `Theme("feelings") is Theme.feelings` (per [`code-quality.md`](../../.claude/rules/code-quality.md) §2 `is` not `==`).
- **Type:** code
- **Issue:** #160
- **Flags:** --reviewers code
- **Produces:** `themes.py` diff, `_schema.json` diff, `types.ts` regen, `tests/unit/test_themes.py` (new or modified), PR-description grep table.
- **Done when:** `uv run pytest` + `uv run mypy src` + `uv run ruff check .` + `npm run typecheck` + `npm run test` all clean; grep result table shows zero unhandled consumers; `Theme("feelings") is Theme.feelings` test passes.
- **Depends on:** none (independent of M1-M7 but blocks M9-M12).

### Step M9: Feelings-naming templates (~20)
- **Problem:** Author ~20 `request_story` branching templates that model "I feel X because Y." Each template: 4-7 steps, 2-4 forks where each fork represents naming a different feeling (sad / worried / angry / silly / proud / left-out / excited). Branches deepen the feeling with a body cue + a coping move. `required_roles: ["friend"]` (persona bias toward Princess + Detective via their `friend`-weighted role_weights — no persona-side gating, see §6.9), recommended_themes `["feelings"]`, `ending_step: {kind: "joke", auto: true}`. Slot uses: `{friend}` placeholder for the character experiencing the feeling.
- **Type:** code
- **Issue:** #161
- **Flags:** --reviewers code
- **Produces:** ~20 entries appended to `src/toybox/activities/templates/branching/request_story.json`.
- **Done when:** Template validation passes; spot-check 5 random templates (10% of 20 with min-5 floor) for distinct feeling vocabulary (no two templates resolve to the same single feeling) + body cue + coping move + no judgmental framing ("you SHOULD feel X").
- **Depends on:** M8.

### Step M10: Perspective-taking templates (~20)
- **Problem:** Author ~20 `request_play` branching templates with a two-act structure: act 1 plays a conflict from {friend1}'s view; act 2 replays the SAME scene from {friend2}'s view, revealing the other side's reasoning. Each template: 6-10 steps, 1-3 forks per act, `required_roles: ["friend", "frenemy"]` (frenemy carries the contrasting POV — see [`roles.py`](../src/toybox/activities/roles.py); persona bias via role_weights, no persona-side gating), recommended_themes `["feelings", "friendship"]`, `ending_step: {kind: "joke", auto: true}`.
- **Type:** code
- **Issue:** #162
- **Flags:** --reviewers code
- **Produces:** ~20 entries appended to `src/toybox/activities/templates/branching/request_play.json`.
- **Done when:** Template validation passes; spot-check 5 random templates (10% of 20 with min-5 floor) for genuine perspective shift (act 2 reveals information act 1 didn't) + no "frenemy was secretly the villain" framing; confirm install's toy pool has at least 1 frenemy-eligible toy (per Phase K `allowed_roles` — see memory `project_toy_role_restrictions_2026-05-16`) or flag as a UAT-config gap.
- **Depends on:** M8.

### Step M11: Conflict-resolution templates (~25)
- **Problem:** Author ~25 branching templates modeling 4 resolution strategies (split it / find a substitute / take turns / one offers, one accepts). Split across `request_play` (~13) and `request_activity` (~12). Each template: 4-7 steps, 2-4 forks each representing one resolution strategy; each fork resolves with a feelings check-in. `required_roles: ["friend"]` (persona bias), recommended_themes `["friendship"]`, `ending_step: {kind: "joke", auto: true}`.
- **Type:** code
- **Issue:** #163
- **Flags:** --reviewers code
- **Produces:** ~13 entries appended to `request_play.json`, ~12 entries appended to `request_activity.json`.
- **Done when:** Template validation passes; spot-check 5 random templates (10% of 25 with min-5 floor) for non-formulaic resolutions (each fork meaningfully different, no "and they all hugged" boilerplate) + check-in step present in every terminal branch.
- **Depends on:** M8.

### Step M12: Friendship/repair templates (~15)
- **Problem:** Author ~15 `request_play` branching templates modeling rupture-and-repair scenarios (knocked over a tower, forgot to invite, said something mean). Each template: 5-8 steps, 2-3 forks where each branch represents a repair strategy (apologize / help fix it / acknowledge feelings / offer something). `required_roles: ["friend"]` (persona bias), recommended_themes `["friendship"]`, `ending_step: {kind: "joke", auto: true}`. At least one fork in every template must depict a "first try fails, second try works" recovery to model that repair takes effort.
- **Type:** code
- **Issue:** #164
- **Flags:** --reviewers code
- **Produces:** ~15 entries appended to `src/toybox/activities/templates/branching/request_play.json`.
- **Done when:** Template validation passes; spot-check 5 random templates for the "first try fails" beat in at least one fork + no "repair = instant forgiveness" shortcuts.
- **Depends on:** M8.

### Step M13: Smoke gate
- **Problem:** Write `tests/integration/test_phase_m_smoke.py` that exercises end-to-end propose→approve→advance→ending_step→reward through one sample of each new content category: (a) "Meet an Element" template — verify `element_id` reaches the ws envelope unredacted + `ending_step` song fires + Phase L reward step appends after; (b) element-family pretend-play template — verify role-fill assigns `guide_mentor` slot + `element_id` (if present) resolves via `element_corpus.get_element()`; (c) shrink-down journey — verify branching graph + `element_id` resolution; (d) feelings-naming template — verify `theme=feelings` reaches the reward matcher (a Phase L reward tagged "feelings" gets picked); (e) perspective-taking with frenemy role — verify cast assembly handles the `frenemy` slot (skip if test fixture lacks a frenemy-eligible toy + emit skipped-with-reason); (f) conflict-resolution — verify the same template-set works across both `request_play` and `request_activity` intents; (g) element-themed song picks as reward — verify a song with `persona_compat: ["periodic_table"]` and `theme=silly` matches when activity persona is `periodic_table` AND activity_themes ∋ silly; (h) persona-role-weight bias check — assert that with default seed + an M4 template declaring `required_roles: ["guide_mentor"]`, the persona picker selects `periodic_table` >50% of the time across 20 seeded runs (per §6.9). **No mocks** — real DB, real corpora, real validators. The smoke gate's deliverable is "the pipeline can complete one real cycle without crashing AND the persona-binding mechanism actually biases as expected" — pass/fail of any narrative quality is out of scope (UAT handles that).
- **Type:** code
- **Issue:** #165
- **Flags:** --reviewers code
- **Produces:** `tests/integration/test_phase_m_smoke.py` with 8 sub-tests (one per category above).
- **Done when:** All 8 sub-tests green on a clean checkout after running `uv run python -m toybox.db.migrate`; suite runs in under 60s; producer-consumer drift would visibly fail at least one sub-test (manually verify by temporarily setting `element_id` to an unresolved id and confirming sub-test (a) fails informatively).
- **Depends on:** M3, M4, M5, M6, M7, M9, M10, M11, M12.

### Step M14: iPad UAT
- **Problem:** Run a curated UAT with both children. Curate ~12 activities: 4 from Track 1 (one "Meet an Element" for a familiar element like gold or helium, one element-family pretend-play, one shrink-down journey, one element-themed song reward — verify Child B recognizes elements + engages with cards) + 8 from Track 2 (2 feelings-naming, 2 perspective-taking, 2 conflict-resolution, 2 friendship-repair — verify Child A engages + understands the feelings vocabulary). Operator walks through each, captures kid reactions, files defects against any template that confuses / bores / mis-renders. Write a UAT run doc at `documentation/runs/<YYYY-MM-DD>-phase-m-uat.md` matching the [Phase K UAT run doc](runs/2026-05-16-phase-k-uat.md) format (per-activity row with: persona / template id / kid / engagement / observation / verdict).
- **Type:** operator
- **Issue:** #166
- **Flags:** (none — operator step)
- **Produces:** `documentation/runs/<YYYY-MM-DD>-phase-m-uat.md` with per-activity pass/fail + defect tickets filed for any failures.
- **Done when:** All 12 activities tried; at least 10/12 pass operator quality bar. **Quality bar per activity:** (a) sprite/card renders without error AND (b) kid engages for ≥50% of intended steps (parent estimates) AND (c) kid does not actively reject ("I don't want this") AND (d) no engine bug (404, validator error, blank step). Pass = a+b+c+d. Any failures have follow-up issues filed (non-blocking for phase closeout per Phase K precedent).
- **Depends on:** M13 + **M2b sprite soak** (deferred from the M2 split — run alongside M14 in one operator session). Operator workflow: run M2b first (~30-45 min render + spot-check + commit 14 canonical sprites), then immediately into M14 iPad UAT so Child B sees actual element sprites instead of fallback persona avatars.

## 8. Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| Element factoid accuracy | An LLM-authored fun_fact is wrong ("uranium is safe to touch in small amounts"). | M1 build-step prompt mandates citing `data/elements/_credits.md` source per fact; spot-check sample. M2 sprite review is visual, not factual — M1 owns the truth check. |
| Element name pronunciation by Web Speech TTS | "Praseodymium" / "Roentgenium" sound bad through default TTS. | M1 ships a `pronunciation_guide` field on each element (phonetic respelling); M4 narration uses it when present alongside the formal name. Long names without a guide fall back to "an element called {symbol}." |
| 118 sprites style drift | Tier-B SD1.5 outputs vary; sprite #1 and sprite #118 don't look like the same persona holding cards. | M2 script uses a fixed seed per element_id and a frozen prompt template. Spot-check 12 sprites at M2 done-when; if drift, add a `--seed` mode that reseeds from element_id deterministically. |
| `feelings` theme downstream drift | A consumer of `Theme` enum hardcodes the 12 values (e.g. a frontend Select component literal) and silently drops `feelings`. | M8's mandatory grep audit + PR-description table catches this. Failure mode would be a silent "feelings-tagged templates never get reward-matched" bug — smoke gate sub-test (d) catches it as a regression. |
| Programmatic 118-template generation feels formulaic | All 118 "Meet" templates read the same; kid bored after 5. | M4 done-when includes a spot-check for "no formulaic boilerplate." M1 ships 3+ story_seed_hooks per element so M4 can vary narration per template. Accept that quality varies; UAT will surface the worst offenders. |
| SEL templates feel moralistic | "Lessons" reading like a Sesame Street script. | M9-M12 each have done-when bullets excluding "preachy" framing. Lean on Daniel Tiger / Bluey's lighter touch: model the behavior, don't narrate the lesson. |
| Frenemy role under-supplied | M10 perspective-taking requires `frenemy` role but most kid toys aren't tagged frenemy. | Phase K's per-toy `allowed_roles` (see memory `project_toy_role_restrictions_2026-05-16`) means toys default to all roles; frenemy is opt-out, not opt-in. Validate at M10 spot-check that toy pool has frenemy-eligible toys. |
| iPad UAT may surface real engine bugs, not template defects | Hard to triage during UAT if e.g. element sprite 404s. | Smoke gate (M13) shipped before UAT proves the engine is sound; UAT defects can be confidently scoped to content. |
| 14 canonical sprites committed to git is a partial sample | 14 covers families + popular elements but not the long tail; fresh-checkout dev sees gaps. | Acceptable trade. Selection criterion specified in M2 (one per Family enum value + gold/helium/oxygen/iron). Fresh-checkout dev runs `scripts/generate_element_sprites.py` to fill in the rest; the kiosk's persona-avatar fallback covers any missing sprite gracefully. |
| Persona-role-weight binding is probabilistic, not strict | Wizard occasionally narrates "Meet Gold!" instead of Professor Iridia. | Per §6.9 acceptable trade — slightly off-character is preferable to engine scope creep. Smoke gate sub-test (h) asserts >50% selection of `periodic_table` persona on M4 templates as a guardrail. If UAT surfaces an unacceptable miss rate, a `persona_required` template field is a future-phase add. |
| `frenemy` role availability gap (M10) | M10 perspective-taking templates require `frenemy` role; install's toy pool may have zero frenemy-eligible toys. | Smoke gate sub-test (e) skips with reason rather than failing if no frenemy-eligible toy exists. UAT operator confirms toy pool has at least one before running M10 templates. If gap, parent-side fix: tag one toy as frenemy-eligible via the Kids & Toyboxes tab (per Phase K memory `project_toy_role_restrictions_2026-05-16`). |
| Coqui TTS unavailable on the dev box | M7 can't render audio. | Manifest entries land in M7 without audio; the song_corpus loader handles missing audio gracefully (logs WARN, doesn't fail). Operator renders audio out-of-band. Existing pattern from Phase K. |
| Phase E touches `api/activities.py` in flight | Merge conflicts on `request_activity.json` / `request_play.json` / `request_story.json` if Phase E is mid-stream. | Phase E doesn't touch templates; Phase M doesn't touch the API surface except for the static mount in M3. Coordination: M3 lands before any Phase E push that touches static mounts. Otherwise independent. |

## 9. Testing strategy

### Unit tests
- **M1** — `test_element_corpus.py` covers: load returns 118 entries; injection guard rejects payload-bearing entries; picker is deterministic + filters by family + filters by age_band; `get_element` resolves valid + returns None on unknown; `Family` enum `is` identity preserved; `pronunciation_guide` field is optional and absent-tolerant.
- **M3** — Validator unit tests cover element_id unresolved rejection (e.g. `element_id: "xx-999"` raises); ElementCard vitest covers render with sprite + render with fallback to persona avatar. (No persona-side gating to test — see §6.9.)
- **M8** — `test_themes.py` covers `Theme("feelings") is Theme.feelings` + display map round-trip + schema enum round-trip.
- **M4-M6, M9-M12** — Existing template-validation test infrastructure (per Phase G/K) automatically covers every new template's schema compliance. No new unit-test files needed.

### Integration test (smoke gate)
- **M13** — `test_phase_m_smoke.py` covers end-to-end propose→approve→advance→reward for one sample of each new content category. Real DB, real corpora, real validators, no mocks. Catches producer-consumer drift across element corpus → template → kiosk wire shape AND across `feelings` theme → reward matcher.

### Manual / UAT
- **M2** — Operator spot-checks ~12 sprites visually after render script completes.
- **M14** — Operator-driven iPad UAT with both kids on 12 curated activities. Quality bar: 10/12 pass; defects filed as follow-up issues (non-blocking per Phase K precedent).

### Regression risk surface
- **Reward matcher (Phase L code)** — element-themed song corpus entries become reward-eligible. Smoke gate sub-test (g) covers the new path. Existing Phase L reward tests should not regress.
- **Schema migration** — `_schema.json` gains one optional field + one enum value. Backward-compat: existing 1000 templates do not carry `element_id` (treated as absent) and do not declare `feelings` theme (untouched).
- **Frontend types codegen** — pydantic→TS hook regenerates `types.ts`; any stale codegen surfaces as a CI pre-commit failure (already wired).

### Performance
- 1000 templates + 30 new family + 15 shrink-down + 118 Meet + 80 SEL = 1243 templates at phase end. Catalog load is O(n); Phase K validated 1000 at load time, 1243 stays well under any concerning latency.
- Element corpus is 118 entries × ~500 bytes = ~60KB; loader cache is fine.
- Sprite static serving uses FastAPI `StaticFiles` mount — same pattern as song audio; no new perf surface.
