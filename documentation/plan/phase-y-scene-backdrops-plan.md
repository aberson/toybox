# Phase Y — Scene backdrops + per-child scene selection

## 1. What This Feature Does

Phase Y gives every kiosk activity a **full-bleed illustrated backdrop** behind
the step card, drawn from a small **pre-rendered scene library** (forest,
kitchen, space, lab, stage, castle, undersea, bedroom…). Today the kiosk
composes transparent-PNG toy sprites over a flat per-persona gradient — a
treasure hunt, a wind-down story, and an elements quiz all share the same plain
background. A scene backdrop is a pure additive layer (sprites already sit on
transparency) and is the single biggest "not boring" visual lever available.

Which scene an activity uses is driven primarily by an explicit, template-authored
`scene_id`, falling back to the running **child's interests** (Child A → dance/stage
scenes, Child B → lab/space) and then a default. Because the image pipeline is
single-worker FIFO with a 120 s/call cap, **all GPU work is offline batch** — the
runtime only ever serves static PNGs. The phase also lands the topic-01 identity
infrastructure (caller-supplied seed + per-call IP-Adapter-scale override) so a
future per-scene *composited* render and coherent multi-sprite story sets can pin
a toy's appearance.

Built from the `local-sd-kids-ux` investigation set
([README](../investigations/local-sd-kids-ux/README.md)) — topics
[03](../investigations/local-sd-kids-ux/03-activity-scene-backgrounds.md) (spine),
[12](../investigations/local-sd-kids-ux/12-per-child-personalization.md), and
[01](../investigations/local-sd-kids-ux/01-toy-character-identity-consistency.md).

## 2. Existing Context

- **Image pipeline** (`src/toybox/image_gen/pipeline.py`): SD 1.5 + cartoon
  LoRA + LCM (4-step) + IP-Adapter Plus (`IP_ADAPTER_SCALE = 0.6`) + double
  rembg cutout → 512px transparent PNG. `generate_action(reference, slot, seed,
  ctx)` always passes `ip_adapter_image` and a 120 s `asyncio.wait_for` cap.
  `_build_prompt` appends a fixed `"2D cartoon, simple shapes, clean lines,
  transparent background"` suffix.
- **Worker** (`worker.py`): single FIFO `asyncio.Queue`; writes
  `data/images/toy_actions/<toy_id>/<slot>.png`; `enqueue(...)` already accepts
  an optional `seed`. Served via the `/api/static/images` mount.
- **Static mount** (`app.py:168`): `/api/static/images` → `images_root()` —
  covers the whole `data/images/` tree, so `data/images/scenes/<id>.png` serves
  at `/api/static/images/scenes/<id>.png` with **no new mount** (same as the
  existing `rewards/` and the `/api/static/elements` precedents).
- **Templates**: Pydantic `Template` (`activities/models.py:435`) + a
  lightweight generator-side `_Template` (`activities/generator.py`,
  `find_template_by_id`) + JSON validator (`activities/_validator.py`). ~1360
  branching templates across 4 intents.
- **Child personalization** (`activities/content_resolver.py`): the `children`
  table already stores `reading_level`, `interests`, `birthdate`, `comfort`,
  `pronouns`, `notes` (`api/children.py`). Only `reading_level` reaches
  generation today, via `resolve_child_profiles` → `build_claude_directive`.
  `resolve_child_profiles` currently `SELECT`s only `id, reading_level`.
- **Activity persistence + wire** (`api/activities.py`): activities persist
  `slot_fills_json` (with reserved keys like `__reward_id`, `__template_id`); the
  Activity wire model carries a `metadata` dict; per-step `metadata` denormalizes
  fields (song `audio_url`, element fields) so the kiosk needs no extra fetch.
- **Kiosk** (`frontend/src/child/components/StepCard.tsx`): a translucent
  `rgba(255,255,255,0.82)` card over the flat persona gradient; sprites render
  as transparent PNGs. No backdrop layer exists.
- **Migrations**: forward-only `.sql` files in `src/toybox/db/migrations/`,
  numerically ordered; latest is `0029` (Phase X). Next is `0030`.

## 3. Scope

**In:**
- A pre-rendered scene-image library + an offline batch CLI to generate it.
- An optional template `scene_id` field + JSON validator + household default.
- A scene resolver chain: template `scene_id` → child-interests selection →
  default; `interests` activated through `content_resolver`.
- Persist resolved `scene_id` per activity; expose `scene_url` on the wire.
- A kiosk full-viewport backdrop layer behind the step card, readability-tuned.
- Identity infrastructure: caller-supplied `seed` + per-call IPA-scale override
  threaded through `generate_action` / `GenerationContext` / the worker.

**Out (explicitly):**
- Runtime/on-demand scene generation (single-worker GPU → pre-render only).
- Per-step (mid-activity) scene changes — scene is **per-activity** in v1.
- Re-tinting baked scenes per child (a baked PNG can't be live-tinted; the child
  signal **selects** among existing scenes).
- Personalization levers other than interests→scene (age→difficulty,
  comfort→SFX deferred to a later phase; not wired here).
- Adventure-beat unique on-demand backdrops (topic 06 — separate phase).
- Reworking Settings, Kids-management, Transcription (out per investigation scope).
- Backfilling `scene_id` across all ~1360 templates (default + overrides only).

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/toybox/activities/scene_catalog.py` | create | Single source of truth: `SCENE_IDS` tuple + per-scene text2img prompt + interest→scene-tag map | new file |
| `scripts/batch_scenes.py` | create | Offline CLI: render each `SCENE_IDS` entry → `data/images/scenes/<id>.png` | new file (mirrors `scripts/batch_animate.py` posture) |
| `src/toybox/db/migrations/0030_activities_scene_id.sql` | create | `ALTER TABLE activities ADD COLUMN scene_id TEXT` | migrations are forward-only `.sql`, latest `0029` (migrations `__init__` docstring; master-plan Phase X) |
| `src/toybox/image_gen/pipeline.py` | extend | Add `generate_scene(prompt, seed)` text2img path (no IPA, opaque full-bleed); add optional `ipa_scale` override param to `generate_action` | read in full — `generate_action`:434, `IP_ADAPTER_SCALE`:96, `_build_prompt`:189 |
| `src/toybox/image_gen/models.py` | extend | `GenerationContext` gains optional identity fields (seed already a `generate_action` arg; add `ipa_scale: float \| None`) | read in full — `GenerationContext`:114 (3 fields today) |
| `src/toybox/image_gen/worker.py` | modify | Thread the IPA override through dispatch; `seed` already an `enqueue` param | read in full — `enqueue(seed=...)`:202, `_load_toy_context_sync` builds ctx:1184 |
| `src/toybox/activities/models.py` | extend | `Template` gains optional `scene_id: str \| None` | grep `class Template` → models.py:435 (1 def) |
| `src/toybox/activities/generator.py` | extend | `_Template` + `find_template_by_id` carry `scene_id` through to propose | grep hit (Template/scene set); `find_template_by_id` is the resolver seam |
| `src/toybox/activities/_validator.py` | extend | Validate `scene_id ∈ SCENE_IDS` (or null) at template-load | grep hit `_validator.py` |
| `src/toybox/activities/content_resolver.py` | modify | `ChildProfileRow` + `ResolvedChildren` gain `interests` (additive, keyword/default); `resolve_child_profiles` SELECT adds `interests`; new `normalize_interests()` + `resolve_scene_id()` chain | read in full — `resolve_child_profiles` SELECT only `id, reading_level`:507; consumers: `aggregate_child_constraints`, propose path |
| `src/toybox/api/activities.py` | modify | Propose persists resolved `scene_id`; Activity wire exposes `scene_url`; serializer denormalizes via the `data/images`→`/api/static/images` helper | grep — wire `metadata`:428, persist `slot_fills_json`:1403, existing URL helper content_resolver:1443 |
| `frontend/src/child/` kiosk shell (StepCard.tsx + its container) | modify | Render a `position: fixed` full-viewport backdrop `<img src={scene_url}>` behind the card; tune card opacity/scrim for readability | read StepCard.tsx — card `rgba(255,255,255,0.82)`:536, no backdrop layer |
| `frontend/src/shared/types.ts` | regenerate | Activity wire gains `scene_url`; codegen is a pre-commit hook | `tools/gen_types_ts.py` exists (glob); master-plan invariant #9 |
| `src/toybox/app.py` | none | Static mount already serves `data/images/scenes/` | app.py:168 mount `/api/static/images` → `images_root()` |
| `src/toybox/api/children.py` | none | `interests` column already in schema + wire | read in full — `interests`:88 |

## 5. New Components

- **`scene_catalog.py`** — canonical `SCENE_IDS` tuple, a per-scene text2img
  prompt string (with the sprite-matching cartoon style suffix for cohesion),
  and an `INTEREST_SCENE_TAGS` map (interest token → scene_id). Imported by the
  batch CLI, the generator, the resolver, and the validator — one source of
  truth so the id set can't drift (code-quality.md §"One source of truth").
- **`scripts/batch_scenes.py`** — offline operator CLI; for each scene calls
  `generate_scene(prompt, seed)` and writes `data/images/scenes/<id>.png`.
  `--force` re-renders; idempotent otherwise.
- **`pipeline.generate_scene()`** — text2img SD 1.5 + cartoon LoRA + LCM path
  with **no IP-Adapter** and **no rembg/transparency** (scenes are opaque
  full-bleed scenery, no toy). Used only by the batch CLI.
- **`content_resolver.resolve_scene_id()`** — the selection chain (template
  `scene_id` → first interest-matched scene → default) + `normalize_interests()`
  (free text → safe token list against a fixed allowlist; never a free prompt).
- **Migration `0030`** — `activities.scene_id TEXT` (nullable).

## 6. Design Decisions

- **Pre-render library, never runtime gen.** The pipeline is single-worker FIFO
  with a 120 s cap; a per-step on-demand backdrop would queue behind sprite work
  and the kid would watch a blank scene resolve. All GPU cost is paid offline in
  `batch_scenes.py`; runtime serves static PNGs through the existing mount. (topic 03)
- **Explicit template `scene_id` is the primary driver** (operator decision),
  with child-interest selection and a default as fallbacks. No mandatory
  ~1360-template backfill: templates default to the resolver chain, and authors
  set `scene_id` where place is load-bearing (story/adventure). Alternative
  considered — derive scene from the existing `{room}` slot — kept as a possible
  later fallback layer, not the v1 primary.
- **Personalization SELECTS, it does not generate.** A baked PNG can't be
  re-tinted live, so `interests` bias picks among existing scenes. `interests`
  free text is normalized to a fixed token allowlist that maps only to known
  `SCENE_IDS` — a typo can never inject a free prompt (topic 04 safety posture).
  Multi-child runs pick the activity owner's interests (single strongest signal);
  blending is an open question.
- **Scene `scene_id` is a first-class activity column (migration 0030)**, not a
  `slot_fills_json` reserved key. Reward used the reserved-key route because a
  reward is a transient per-advance pick; a scene is a stable per-activity visual
  attribute, more like the Phase X room columns — cleaner to serialize/query.
- **Identity infra is additive and dormant for backdrops.** Backdrops layer the
  *cached* sprite over the scene with no regeneration, so topic-01 work isn't on
  the backdrop critical path. Per the operator's "Full" choice we still thread a
  caller-supplied `seed` (already an `enqueue` param) + a per-call `ipa_scale`
  override now, so a future per-scene composited render and multi-frame story
  sets can pin a toy's identity without re-plumbing the pipeline.
- **Style cohesion guard.** `scene_catalog` prompts carry the same cartoon
  style suffix the sprite pipeline uses, so the cast doesn't look pasted onto a
  mismatched backdrop ("ransom-note kiosk", topic 02).
- **No autonomous/background behavior.** The scene library is produced by a
  one-shot operator-run CLI; the runtime change is static-file serving + a render
  layer. The autonomous-behavior trigger does not fire — no soak/observation step
  is required beyond the smoke gate + UAT.

## 7. Build Steps

### Step Y1: Scene catalog (single source of truth)
- **Type:** code
- **Problem:** Add `src/toybox/activities/scene_catalog.py` defining `SCENE_IDS`
  (≈8–10 ids), a per-scene text2img prompt (with the sprite-matching cartoon
  suffix), a `DEFAULT_SCENE_ID`, and an `INTEREST_SCENE_TAGS` map. Pure data +
  small helpers, no heavy imports.
- **Issue:** #265
- **Flags:** --reviewers code
- **Produces:** `scene_catalog.py`; unit tests asserting id-set stability, every
  id has a prompt, default ∈ `SCENE_IDS`, interest tags map only to valid ids.
- **Done when:** `uv run pytest tests/unit/activities/test_scene_catalog.py`
  passes; `uv run mypy src` clean.
- **Depends on:** none
- **Status:** DONE (2026-06-22)

### Step Y2: Scene generation path + batch CLI
- **Type:** code
- **Problem:** Add `pipeline.generate_scene(prompt, seed)` (text2img, no IPA, no
  transparency, opaque 512px) and `scripts/batch_scenes.py` that renders each
  `SCENE_IDS` entry to `data/images/scenes/<id>.png` (`--force` re-renders).
  Wire the CLI against the image-gen stub for CI; real GPU render is Y2-run.
- **Issue:** #266
- **Flags:** --reviewers code
- **Produces:** `generate_scene` in `pipeline.py`; `scripts/batch_scenes.py`;
  unit tests driving the CLI via `TOYBOX_IMAGE_GEN_STUB=1` (writes placeholder
  PNGs, asserts one file per scene id).
- **Done when:** stub-mode CLI run writes `len(SCENE_IDS)` PNGs to a tmp scenes
  dir; `test_lazy_imports` still green (no torch import at module load).
- **Depends on:** Y1
- **Status:** DONE (2026-06-22)

### Step Y3: Template `scene_id` field + validator
- **Type:** code
- **Problem:** Add optional `scene_id: str | None` to the Pydantic `Template`
  and the generator-side `_Template`/`find_template_by_id`; validate
  `scene_id ∈ SCENE_IDS` (or null) in `_validator.py`. No template backfill.
- **Issue:** #268
- **Flags:** --reviewers code
- **Produces:** schema + validator changes; unit tests (valid id parses,
  unknown id rejected, null/absent tolerated for the existing ~1360 templates).
- **Done when:** existing template-load tests pass with the new optional field;
  a template fixture carrying a bad `scene_id` fails validation.
- **Depends on:** Y1

### Step Y4: Scene resolver + interests activation
- **Type:** code
- **Problem:** In `content_resolver.py`: add `interests` to `ChildProfileRow` +
  `ResolvedChildren` (additive, keyword-defaulted); extend the
  `resolve_child_profiles` SELECT to read `interests`; add `normalize_interests()`
  (free text → allowlisted tokens) and `resolve_scene_id(template_scene_id,
  resolved_children, default)` implementing the chain.
- **Issue:** #269
- **Flags:** --reviewers code
- **Produces:** resolver changes; unit tests (explicit template id wins;
  interest match when template id absent; default when neither; multi-child uses
  owner; malformed/empty interests → default; injection token can't escape the
  allowlist).
- **Done when:** `uv run pytest tests/unit/test_content_resolver.py` passes
  (incl. existing `ResolvedChildren` construction sites — additive field must not
  break positional callers).
- **Depends on:** Y1

### Step Y5: Persist + serialize scene
- **Type:** code
- **Problem:** Migration `0030` adds `activities.scene_id`; the propose path
  calls `resolve_scene_id` and persists it; the Activity wire model exposes
  `scene_url` (denormalized from `scene_id` via the `data/images` →
  `/api/static/images` helper, `None` when unset); run `gen_types_ts.py`.
- **Issue:** #270
- **Flags:** --reviewers code
- **Produces:** `0030_activities_scene_id.sql`; propose + serializer changes;
  regenerated `frontend/src/shared/types.ts`; integration test asserting
  propose→persist→wire round trip carries the resolved `scene_url`.
- **Done when:** propose a template with a `scene_id` → `activities.scene_id`
  persisted AND the Activity wire response carries the matching `scene_url`;
  codegen drift check clean.
- **Depends on:** Y3, Y4

### Step Y6: Kiosk backdrop layer
- **Type:** code
- **Problem:** In the child kiosk shell that renders `StepCard`, add a
  `position: fixed` full-viewport backdrop `<img src={activity.scene_url}>`
  behind the card (render nothing when `scene_url` is absent). Tune the card
  opacity / add a scrim so body text stays readable over a busy scene; leave
  `prefers-reduced-motion` and existing a11y untouched (backdrop is static).
- **Issue:** #271
- **Flags:** --reviewers code
- **Produces:** kiosk render change; vitest (backdrop mounts when `scene_url`
  set, absent otherwise; card readability layer present; no new motion).
- **Done when:** `cd frontend; npm run test` + `npm run typecheck` pass.
  (UI is PIN/kiosk-gated — runtime reviewers can't reach it, so review is `code`
  + the Y9 iPad UAT, per `feedback_buildstep_pin_gate_blocks_ui_evidence`.)
- **Depends on:** Y5

### Step Y7: Identity infrastructure (seed + IPA-scale override)
- **Type:** code
- **Problem:** Thread an optional per-call `ipa_scale` override through
  `GenerationContext` → `generate_action` → the worker dispatch (`seed` is
  already an `enqueue` param). Default behavior byte-identical when unset
  (`IP_ADAPTER_SCALE = 0.6`). Additive infra for future per-scene composited art
  + coherent multi-sprite sets.
- **Issue:** #272
- **Flags:** --reviewers code
- **Produces:** signature + plumbing changes; unit tests (override reaches
  `set_ip_adapter_scale`; unset → 0.6; seed passes through unchanged) via the stub.
- **Done when:** `uv run pytest tests/unit -k image_gen` passes; `mypy src` clean.
- **Depends on:** none (parallel-safe with Y3–Y6)

### Step Y8: End-to-end smoke gate
- **Type:** code
- **Problem:** One real-component integration test (no boundary mocks): migrate a
  tmp DB, register a template with a `scene_id`, propose → assert
  `activities.scene_id` persisted, Activity wire carries `scene_url`,
  `GET /api/static/images/scenes/<id>.png` serves a fixture PNG (200 + image/png),
  AND a child with matching `interests` selects the interest scene when the
  template has none.
- **Issue:** #273
- **Flags:** --reviewers code
- **Produces:** `tests/integration/test_phase_y_smoke.py`.
- **Done when:** the smoke test passes end-to-end against `create_app()` + a real
  migrated DB; full `uv run pytest` green, no count regression.
- **Depends on:** Y5, Y6

### Step Y2-run: Render the scene library (operator)
- **Problem:** Run `batch_scenes.py` on the operator GPU to produce the real
  scene PNGs, and parent-eyeball each for age-appropriateness + style cohesion
  with the sprites. (Deferred to phase-end: only Y9 depends on these assets —
  the code steps Y3–Y8 use a fixture PNG — so this operator render clusters with
  the other operator step instead of halting the autonomous code span at step 3.)
- **Type:** operator
- **Issue:** #267
- **Produces:** `data/images/scenes/*.png` (gitignored runtime assets).
- **Done when:** operator confirms `len(SCENE_IDS)` scene PNGs exist under
  `data/images/scenes/` and each passes a one-time parent visual check. Server
  must be stopped during the batch (batch + live both load CUDA — Phase U U3 lesson).
- **Depends on:** Y2

### Step Y9: iPad UAT (operator)
- **Problem:** On the iPad kiosk, confirm the backdrop renders behind the step
  card, body text stays readable over the scene, the cast looks in-style (not
  pasted on), and an interest-selected scene differs between Child A and Child B.
- **Type:** operator
- **Issue:** #274
- **Produces:** a `documentation/runs/<date>-phase-y-uat.md` pass doc.
- **Done when:** operator confirms all four visual checks PASS (or files defects).
  May fold into the standing bundle #223.
- **Depends on:** Y8, Y2-run

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Card readability over a busy backdrop | Text drowns on high-contrast scenes | Scrim/opacity tuning in Y6; readability is an explicit Y9 UAT check |
| Style mismatch (ransom-note kiosk) | Scenes look unlike the sprites | `scene_catalog` prompts carry the sprite cartoon suffix; Y2-run parent eyeball |
| `ResolvedChildren`/`ChildProfileRow` shape change | Positional construction sites break | Field is additive + keyword-defaulted; Y4 done-when runs the full resolver suite |
| `interests` free-text injection | A typo/hostile string biases an off-theme scene | `normalize_interests` allowlist → only known `SCENE_IDS`; never a free prompt |
| Library too small / repetitive | Same few scenes feel stale | Start ~8–10; scene-count-vs-variety is an open question for a follow-up batch |
| GPU batch vs live server | Both load CUDA and conflict | Y2-run requires the server stopped (Phase U U3 lesson) |
| Per-activity vs per-step scene | Changing place mid-activity might help or jar | v1 is per-activity only; per-step deferred (open question) |
| Multi-child interest blend | Two children's interests muddy selection | v1 picks the activity owner; blending deferred (open question) |

## 9. Testing Strategy

- **Unit:** `scene_catalog` (id-set stability via `is`/`==`, prompt coverage,
  interest-map validity); `resolve_scene_id` chain + `normalize_interests`
  (incl. injection + multi-child); `_validator` scene_id acceptance/rejection;
  `generate_scene` + `batch_scenes` via the image-gen stub; `generate_action`
  `ipa_scale` override + seed pass-through via the stub.
- **Integration / smoke (Y8):** real `create_app()` + migrated DB —
  propose→persist→wire→static-serve round trip + interest-driven selection. This
  is the producer→consumer gate that mocked unit tests can't cover
  (code-quality.md §"Audit wire shape when storage representation changes").
- **Codegen:** `types.ts` drift check (pre-commit hook / existing codegen test)
  after the Activity wire gains `scene_url`.
- **Frontend (vitest):** backdrop mounts iff `scene_url` present; readability
  layer present; no new motion (a11y preserved).
- **Tests likely to break (update deliberately, treat as suspect per
  code-quality.md):** `ResolvedChildren` construction sites; Activity wire-shape
  assertions; the codegen snapshot. Any test diff that *narrows* a response shape
  gets scrutiny rather than a rubber stamp.
- **End-to-end visual:** Y9 iPad UAT — the only check that exercises the
  PIN-gated kiosk render path.

## Next steps

This plan must pass `/plan-review` and `/plan-wrap` BEFORE `/repo-sync` mints
issues (a gap caught after sync is an N+1-edit problem). The autonomous chain:

```
/plan-expedite --plan documentation/plan/phase-y-scene-backdrops-plan.md
```

then, once it returns READY:

```
/build-phase --plan documentation/plan/phase-y-scene-backdrops-plan.md
```
