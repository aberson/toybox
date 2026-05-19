# Phase N — Element microgame template shape (feature plan)

## 1. What this feature does

Phase N mints a new template shape `element_microgame` purpose-built for the Periodic Table track. It replaces the M4 generic `request_activity` shape for element activities — which Phase M UAT surfaced as too flat (passes engine quality bar but reads as persona monologue with no kid agency on element content).

**Shape:** 4 steps, 2 sequential binary forks. One template per element (118 total), generator-minted from the existing element corpus.

```
Step 1 — Intro (no fork; persona Iridia narrates):
  "Hi {kid_name}! This is {symbol} — {name}. It's a {family_display}.
   It's {color_description} and at room temperature it's a {phase}."

Step 2 — Fork 1, family recognition (binary choice):
  "Can you find another {family_display}?
   Is it {peer_in_family} or {peer_out_of_family}?"
  → correct: peer_in_family
  → wrong:   peer_out_of_family (persona gently redirects, advance anyway)

Step 3 — Fork 2, "next in family" or property (binary choice):
  "What else is true about {name}?
   {fact_a_true} or {fact_b_false}?"
  → correct: fact_a_true (sourced from fun_fact or story_seed_hooks)
  → wrong:   fact_b_false (corpus-authored plausible distractor)

Step 4 — Reward (no fork):
  "Yes! And here's a secret about {name}: {fun_fact_full}."
  Auto-fires element-themed song reward (re-uses M7 corpus).
```

**Why now.** Phase M UAT defect D3 — operator: *"elements need their own templates. Doesn't need to be complex, just something that exposes the child to facts about an element."* Selected 4-steps-with-two-forks shape over 3-step-single-fork and 2-step-no-fork after UAT pause on 2026-05-18.

**Out of scope:**
- Listen-for-answer steps (Phase M scope decision; remains deferred).
- Retiring or migrating Phase M's M4 `meet_element_*` templates. New shape ships alongside; M4 templates can be retired in a follow-up phase or kept for narration-only variety.
- Parent UI tab nav (folded into Phase O — see `documentation/phase-o-plan.md`).

**In scope (additions from Phase M UAT pause 2026-05-18):**
- **Phase M UAT defect D2** — child-kiosk persona-letter blocks the Next button on element activities. Folded as **N0** (gates all other N work). UAT row #4 (`shrink_into_helium_balloon_voyage`) is deferred until N0 ships.
- **Phase M UAT defect D1** — `persona_reasoning` text names a wrong persona ("professor pip" instead of "Professor Iridia"). Folded as **N0b**. Element-adjacent UI fix; bundles cleanly with N0.

## 2. Existing context

### Fresh-reader pointers

For first-run setup, backend/frontend commands, and project conventions: see [`toybox/CLAUDE.md`](../CLAUDE.md).
Other phase plans referenced: [`phase-m-plan.md`](phase-m-plan.md) (immediate predecessor, defines M1-M14 and the element corpus + ElementCard), [`phase-l-plan.md`](phase-l-plan.md) (reward matcher + song corpus shape), [`phase-o-plan.md`](phase-o-plan.md) (parent UX 5-tab refresh, consumes Phase N's `template_type`).

### Acronyms and terms used in this plan

| Term | Meaning |
|---|---|
| **Phase G** | Branching template engine (forks, choices, role slots). Predecessor to Phase K/L/M. |
| **Phase K** | 1000-template content expansion (10-role taxonomy, 12-theme taxonomy, songs/jokes corpora). |
| **Phase L** | Rewards system + per-activity reward TYPES (jokes/songs as reward step kinds). |
| **Phase M** | Periodic Table Professor + SEL content depth. Direct predecessor; ships the element corpus (M1), ElementCard kiosk component (M3), 118 "Meet an Element" templates (M4), song corpus expansion (M7), feelings theme (M8), and SEL templates (M9-M12). |
| **Phase O** | Parent UX 5-tab refresh — consumes Phase N's `template_type` field as the "Elements" categorization signal. Plan at [`phase-o-plan.md`](phase-o-plan.md). |
| **M1, M3, M4, M7, M8, M13** | Specific Phase M build-steps referenced by id. Full step list in [`phase-m-plan.md`](phase-m-plan.md) §5. |
| **UAT** | User Acceptance Test. Operator + kid walk the live system on the iPad kiosk to validate end-to-end content quality. |
| **TDD** | Test-Driven Development. The `--tdd` flag on a build-step invokes `/build-step-tdd` instead of `/build-step` — tests-first, red-green-refactor workflow. Right fit for data loaders + validators where the contract is the spec. |
| `--reviewers code` | Flag to `/build-step` selecting the 4-parallel-reviewer mode (correctness / bugs / test quality / style). The other options are `auto` (just tests), `runtime` (3 evidence-based reviewers requiring `--start-cmd` + `--url`), and `full` (all 7). Phase N uses `code` everywhere because toybox's PIN-gated parent UI blocks `runtime`/`full` reviewers from doing UI evidence capture (see memory `feedback_buildstep_pin_gate_blocks_ui_evidence`). |
| **`/build-step` / `/build-phase`** | Workspace skills. `/build-phase` walks the steps in this plan and dispatches each to `/build-step` (or `/build-step-tdd` when `--tdd` is set). Operator-type steps halt orchestration so the operator can do the manual work. SKILL.md files at `dev/.claude/skills/`. |

### `Element` type (returned by corpus loader)

`element_corpus.py` exposes an `Element` dataclass with the same fields as the JSON entry plus a `Family` StrEnum for `family`. Used by `get_element(element_id)`, `peer_in_family(...)`, `peer_out_of_family(...)`. Fields: `id: str`, `symbol: str`, `name: str`, `atomic_number: int`, `atomic_mass: float`, `family: Family`, `phase_at_room_temp: Literal["solid","liquid","gas"]`, `color_description: str`, `discovered_era: str`, `fun_fact: str`, `story_seed_hooks: list[str]`, `pronunciation_guide: str | None`, `age_band: Literal["3-5"]`.

### `element_id` format

`{symbol-lowercased}-{atomic_number}`. Examples: `h-1` (Hydrogen), `he-2` (Helium), `au-79` (Gold), `uue-119` (hypothetical Ununennium if added). Matches Phase M3's wire shape.

### M7 song corpus entry shape (relevant to N5 reward assertion)

A song corpus entry (in `data/songs/songs.json` or the Phase K convention path — confirm at build time) carries `id`, `title`, `theme`, optional `element_id`, optional `audio_path`. The reward matcher (Phase L) prefers entries whose `element_id` matches the current activity's `element_id`. N5 asserts `reward.template_id` corresponds to an entry whose `element_id == activity.element_id` when one exists for that element; else any song-corpus entry passes.

### Pydantic→TypeScript codegen command

`uv run python -m toybox.codegen.types_emit` (or the project's actual command — confirm via `package.json` / `pyproject.toml` at build time). Regenerates `frontend/src/shared/types.ts`. Phase L pattern: codegen runs as a pre-commit hook in CI; locally call it after wire-shape changes.

### Corpus schema (already shipped in M1)

`data/elements/elements.json` — 118 entries, each with:
- `id`, `symbol`, `name`, `atomic_number`, `atomic_mass`
- `family` (StrEnum: alkali_metal, alkaline_earth_metal, transition_metal, post_transition_metal, metalloid, nonmetal, halogen, noble_gas, lanthanide, actinide)
- `phase_at_room_temp` (solid/liquid/gas)
- `color_description` (e.g. "shiny yellow", "colorless gas")
- `fun_fact` (one-sentence factoid)
- `story_seed_hooks` (3+ narration scaffolds with `{name}` interpolation)
- `pronunciation_guide` (optional phonetic respelling)
- `age_band` ("3-5" for all 118)

Loader: [`src/toybox/activities/element_corpus.py`](../src/toybox/activities/element_corpus.py) (Phase M1). Adds family-grouped lookup + age-band filter.

### Template schema constraints (already shipped, relevant to Phase N)

Verified via [`_schema.json`](../src/toybox/activities/templates/_schema.json) at plan-review:

- **Step-count floor:** `steps.minItems: 3` ([`_schema.json:51`](../src/toybox/activities/templates/_schema.json#L51)). Phase N's 4 steps is safely above the floor. M4 hit this floor when its plan example showed a single step ([generate_meet_element_templates.py:14-33](../scripts/generate_meet_element_templates.py)); Phase N avoids the issue by design.
- **Fork shape:** `kind: "fork"` enum value already exists ([`_schema.json:134`](../src/toybox/activities/templates/_schema.json#L134)) with `choices: minItems 2, maxItems 4` ([`_schema.json:177-178`](../src/toybox/activities/templates/_schema.json#L177)). Phase N's binary fork = `choices` of length 2. **No schema changes required for the fork primitive.**
- **`additionalProperties: true` on templates** ([`_schema.json:23`](../src/toybox/activities/templates/_schema.json#L23)). Adding a `template_type` field is documentation-only at the schema layer; structural enforcement happens in the Python validator (N2).

### Persona binding (existing)

`periodic_table` persona ("Professor Iridia", [`periodic_table.json:3`](../src/toybox/personas/library/periodic_table.json#L3)) is selected for any template tagged with `element_id` per Phase M § 6.9 weighting via `required_roles: ["guide_mentor"]` ([`periodic_table.json:13-17`](../src/toybox/personas/library/periodic_table.json#L13) — `role_weights.guide_mentor: 1.5`). Smoke gate sub-test (h) asserts >50% selection. Phase N templates inherit this binding via the same `required_roles: ["guide_mentor"]` (matches M4's [`generate_meet_element_templates.py:9`](../scripts/generate_meet_element_templates.py#L9)); no persona work needed.

### Template emission convention (one file per intent)

Project convention is **one file per intent**: [`branching/request_activity.json`](../src/toybox/activities/templates/branching/request_activity.json), `request_play.json`, `request_story.json`, `boredom.json`. M4 follows this by appending 118 `meet_element_*` entries to `request_activity.json` with strip-by-prefix idempotence ([generate_meet_element_templates.py:36-44](../scripts/generate_meet_element_templates.py#L36)). **Phase N follows the same convention** — `element_microgame_*` templates append to the same `request_activity.json`, sharing the strip-by-prefix idempotence pattern. No new file paths.

### Persona-reasoning wire shape (relevant to N0b)

Verified via grep at plan-review: `persona_reasoning` is a caller-supplied passthrough on `ProposeRequest` ([`activities.py:414`](../src/toybox/api/activities.py#L414)), persisted as-is in activity metadata. The propose handler itself **does NOT synthesize** the text — it reads `metadata.get("persona_reasoning")` ([`activities.py:982`](../src/toybox/api/activities.py#L982)). The string "professor pip" does NOT appear anywhere in `src/toybox/` (zero grep matches). The producer of the wrong text is **upstream of the propose handler** — likely (a) the frontend supplying a literal at propose time, (b) an LLM-tool call generating reasoning copy, or (c) a template field carrying frozen text. N0b's first action is locating the producer; the fix follows.

## 3. Scope summary

- **One new optional template field** `template_type: "element_microgame"`. **Zero JSON-schema change** (existing `additionalProperties: true` on templates accepts the field). Structural rules (4 steps; fork on 2+3; no fork on 1+4) live in the Python validator [`toybox/activities/_validator.py`](../src/toybox/activities/_validator.py).
- **`template_type` on existing M4 `meet_element_*` templates:** stays absent (no backfill). Phase O `categorize()` already prefers `element_id` as the "Elements" signal — backfilling 118 M4 templates adds churn without changing categorization behavior.
- **One new corpus helper** in `element_corpus.py`: `peer_in_family(element_id, rng) -> Element` + `peer_out_of_family(element_id, rng) -> Element` (deterministic by element_id, returns one in-family neighbor + one cross-family distractor).
- **Per-element distractor data** in `data/elements/distractors.json` (NEW): for each element, one `{fact_a_true, fact_b_false}` pair for Step 3. ~118 entries, ~80 KB. Authoring flow (resolved 2026-05-18 mid-Phase-N): **N1.5 deterministically generates** all 118 entries from corpus rotations (no LLM — algorithmic), then **N1 operator skim-review** flips per-entry `source: llm` → `source: operator` after a ~10-min editor pass. **Provenance:** sibling `data/elements/_distractors_credits.md` documents authorship per entry (matches existing `_credits.md` pattern). Loader rejects entries whose credits-file row claims `source: llm` unless explicitly opted-in via `TOYBOX_ALLOW_LLM_DISTRACTORS=1` (guards against silent unreviewed-machine-gen drift).
- **One generator script** at `scripts/generate_element_microgames.py` (analogous to M4): reads corpus + distractors, **appends** 118 templates to `src/toybox/activities/templates/branching/request_activity.json` (sharing intent + idempotence pattern with M4). One file, not 118.
- **Intent:** `request_activity` (matches M4 — element microgames are directed kid-engagement, not stories or play).
- **Each generated template carries:** `required_roles: ["guide_mentor"]` (Iridia bias, matches M4), `template_type: "element_microgame"`, `element_id` on every step so ElementCard renders consistently, `ending_step: {"kind": "song", "auto": true, "element_id": <id>}` so Phase L's reward matcher picks the element-themed song.
- **Frontend types codegen:** Phase L's pydantic→TypeScript hook regenerates `frontend/src/shared/types.ts` so Phase O's `categorize()` can read `template_type` with a real type. Add to N2 done-when.
- **Smoke gate** in `tests/integration/test_phase_n_smoke.py`: end-to-end propose → 4-step walk → both fork branches → reward fires + asserts the **element-themed** song from M7 corpus is selected (not a generic).

No new engine code. No frontend component changes (ElementCard already renders on `element_id` steps).

## 4. Out of scope (explicit, with reason)

- **Phase M template retirement.** Keep both shapes; let UAT decide. Phase O could retire M4 element templates if Phase N supersedes them.
- **N-ary forks (3+ options per step).** 4yo Child B can hold one binary choice per fork; ternary forks tested poorly in Phase G observation.
- **Cross-element comparison forks** (e.g. "Which is heavier, Gold or Iron?"). Requires multi-element corpus joins; deferred.
- **Audio narration of the fork choices.** Phase M's TTS layer reads narration on tap; same applies. No Phase N work.

## 5. Build steps

### Step N0: Child-kiosk persona-letter hides on element cards (D2 fix — BLOCKER GATE)
- **Problem:** `StepCard.tsx` renders a persona letter/initial badge that overlaps the Next button on element-activity cards (those that render `ElementCard`). UAT defect D2: operator could not tap Next during Phase M row #4. The element sprite already encodes persona identity, so the letter is redundant on top of being a blocker. Conditionally hide the persona-letter surface when `ElementCard` is rendered. Add vitest coverage that asserts (a) persona letter renders on non-element StepCard; (b) persona letter hidden when `element_id` is present on the step; (c) Next button is reachable (testing-library `getByRole('button', { name: /next/i })` not obscured by sibling positioning).
- **Type:** code
- **Issue:** #168 (umbrella #167)
- **Flags:** `--reviewers code`
- **Status:** DONE (2026-05-18) — fix landed in App.tsx (preserves "avatar OUTSIDE StepCard" invariant); `currentStepHasElement` exported helper; 3 vitest tests via KioskComposition wrapper. Iter 2/3 PASS at edc3cc9.
- **Produces:** patch to `frontend/src/child/components/StepCard.tsx` + vitest coverage in `StepCard.test.tsx` + manual visual spot-check via dev server.
- **Done when:** vitest passes; on iPad, element activity cards show Next button unobstructed; non-element cards (SEL, branching, etc.) still show persona letter as before.
- **Depends on:** none (UI-only fix).
- **Gates:** all subsequent Phase N steps + Phase M UAT row #4 retest.

### Step N0b: `persona_reasoning` text matches resolved persona (D1 fix)
- **Problem:** On the parent propose card, `persona_reasoning` text says "professor pip" while the resolved persona (post-approve) is "Professor Iridia." **Plan-review verified the producer is NOT in `src/toybox/api/activities.py`** — that file's propose handler is a metadata-passthrough ([`activities.py:982`](../src/toybox/api/activities.py#L982): `persona_reasoning = raw_reasoning`). The string "professor pip" does NOT appear in `src/toybox/` (zero grep matches). **First action: locate the upstream producer.** Likely candidates in priority order: (i) frontend propose call — grep `frontend/src/parent/**` for `persona_reasoning` and string literals; (ii) LLM tool call site — grep `src/toybox/ai/**` for reasoning-generator code that runs before propose; (iii) a template field carrying frozen text — grep `src/toybox/activities/templates/**` for `"persona_reasoning"`. Once producer is located, two fix paths: (a) move reasoning generation downstream of persona binding so it can interpolate `{persona.display_name}` deterministically; (b) template-time injection of persona name and skip LLM-authored persona references. Prefer (b) for templates with deterministic persona binding (element_microgame in particular); (a) for runtime-picked personas. Build-step proceeds autonomously after producer-site grep; halts and surfaces only if grep returns zero matches across all three candidate surfaces.
- **Type:** code
- **Issue:** #169 (umbrella #167)
- **Flags:** `--reviewers code`
- **Produces:** patch to whichever module authors `persona_reasoning`; regression test that asserts `persona_reasoning` references the actually-bound persona's `display_name` for a sample of element + non-element templates.
- **Done when:** smoke gate sub-test asserts persona-name consistency between `activity.metadata.persona.display_name` and `activity.persona_reasoning` substring match; manual spot-check on iPad shows correct persona names pre-approve + post-approve.
- **Depends on:** none (orthogonal to N0, but typically batched in one session).
- **Cross-link to M4:** if the producer is upstream (not template-frozen), the fix also corrects D1 for existing M4 `meet_element_*` templates as a side effect. If the producer IS template-frozen (option iii), then the M4 generator also needs an idempotent regenerate to write correct reasoning into the 118 existing entries — call out at build time.
- **Status:** DONE (2026-05-18) — root cause was regenerate inheriting source's reasoning text while NOT inheriting source's persona_id (text named OLD persona, row carried NEW persona_id). Fixed at `cfc9e5f` by dropping implicit-from-source inheritance in `post_regenerate` + unifying dispatcher path with `_build_persona_reasoning` (display_name lookup). 5 new integration tests pin both surfaces. NOT template-frozen, so M4 regenerate not needed. Iter 1/3 PASS.

### Step N1-prep: Distractor corpus scaffold + loader (code-only)
- **Problem:** Build the infrastructure that N1 (operator) will fill. Author `src/toybox/activities/distractor_corpus.py` — loader for `data/elements/distractors.json` with the entry shape `{ "element_id": "au-79", "fact_a_true": "...", "fact_b_false": "..." }`. Loader gates: rejects entries whose corresponding `_distractors_credits.md` row claims `source: llm` unless `TOYBOX_ALLOW_LLM_DISTRACTORS=1` env var is set (default-off; opt-in for future LLM-pass-with-review experiments). Ships an empty `distractors.json` (just `[]`) and a `_distractors_credits.md` with the header row + format documentation but NO entries — N1 (operator) fills both. Format for `_distractors_credits.md` is a markdown table:
  ```markdown
  | element_id | source | reasoning |
  |---|---|---|
  | au-79 | operator | False fact "Gold floats in water" picked because Child B sees coins sink. |
  ```
  Ships a CLI `uv run python -m toybox.activities.distractor_corpus --validate` that asserts (a) `distractors.json` has N entries; (b) each has a corresponding credits row; (c) no entry has `source: llm` unless env flag set; (d) round-trip JSON-load passes. CLI is what N1 (operator) runs to confirm their authoring is correct.
- **Type:** code
- **Issue:** #170 (umbrella #167)
- **Flags:** `--tdd`
- **Produces:** `src/toybox/activities/distractor_corpus.py` + empty `data/elements/distractors.json` (just `[]`) + `data/elements/_distractors_credits.md` with header row + format docs + tests + `--validate` CLI.
- **Done when:** unit tests cover injection guard + missing-element rejection + round-trip + LLM-source-rejection-without-env-flag + empty-corpus-is-valid; CLI works on the empty scaffold (prints "0 entries, 0 credits rows, OK").
- **Depends on:** Phase M1 (element corpus).
- **Status:** DONE (2026-05-18) — shipped at `b128b0a` with 37 unit tests. Iter 2 fixed a HIGH-severity duplicate-credits-row gate bypass surfaced by code review (parser-level `seen_credit_ids` detection; both orderings tested). Also wrapped cross-corpus `get_element` errors in `DistractorCorpusError` and hoisted argparse parser. CLI validates the shipped empty scaffold cleanly.

### Step N1.5: Generator script — 118 distractor entries (deterministic, from corpus)
- **Problem:** Author `scripts/generate_distractor_corpus.py` (analog to M4's `generate_meet_element_templates.py`). For each of 118 elements, deterministically generate a `{fact_a_true, fact_b_false}` pair using corpus-only logic (no LLM). `fact_a_true` paraphrases the element's own `fun_fact` or first `story_seed_hook`. `fact_b_false` is a plausible-but-wrong claim derived from a deterministic transformation of corpus data — strategy options (dev agent picks the cleanest): (a) rotate-and-attribute — take a true fact about ANOTHER element (target's `atomic_number` seeds the other-element pick) and re-attribute as if true of the target; (b) property inversion — flip a known corpus property (e.g. claim a metal is a gas, claim a noble gas reacts violently); (c) cross-family swap — claim a property typical of a different family. Re-runs produce byte-identical output (modulo formatting normalization). Writes 118 entries to `data/elements/distractors.json` (sorted by `atomic_number`) AND 118 rows to `_distractors_credits.md` with `source: llm` + per-row reasoning of the form "fact_b_false strategy: <a|b|c>, derived from <source>". Source-tag `llm` is the agreed shorthand for "machine-generated, awaiting operator review" — loader-gated until N1 flips tags.
- **Type:** code
- **Issue:** #192 (umbrella #167)
- **Flags:** `--reviewers code --tdd`
- **Produces:** `scripts/generate_distractor_corpus.py` + `data/elements/distractors.json` (118 entries) + `data/elements/_distractors_credits.md` (118 rows, all `source: llm`) + generator unit tests.
- **Done when:** generator is deterministic (same input → byte-identical JSON + markdown across runs); snapshot test pins output for 5 known elements (Gold, Hydrogen, Helium, Iron, Oxygen); `uv run python -m toybox.activities.distractor_corpus --validate` reports `118 entries, 118 credits rows, all source: llm`; loader rejects load without `TOYBOX_ALLOW_LLM_DISTRACTORS=1` (validates N1-prep gate from real data).
- **Depends on:** N1-prep (loader contract + empty scaffold).

### Step N1: Operator skim-review — flip source tags after ~10-min editor pass
- **Problem:** Open `data/elements/distractors.json` + `_distractors_credits.md` in the editor (or paged via CLI). Per element entry: read the generated `fact_a_true` (corpus paraphrase) and `fact_b_false` (algorithmic distractor). For acceptable entries (read naturally to a 4yo, false-fact is plausible-but-clearly-wrong), flip the credits row's `source: llm` → `source: operator`. For unacceptable entries (awkward phrasing from cross-element rotation, factually ambiguous, too-subtly-wrong), either edit in-place OR delete that element's entry (engine gracefully degrades when an element has no distractor — Step 3 fork falls back to `story_seed_hooks` per N4's generator). Quality bar: would Child B (4yo) hear this `fact_b_false` and could it become a mislearned belief? If yes, reject. **Why operator-driven:** N1.5 is algorithmic so failure modes are awkward phrasing and rotations that don't translate (e.g. Helium's "makes voices squeaky" doesn't read naturally as a Gold fact). Operator scan catches these without the 40-60min full-auth tax. Estimated effort: ~10 min skim + ~5-10 min for edits to the ~10% of entries needing them.
- **Type:** operator
- **Issue:** #171 (umbrella #167)
- **Produces:** edited `_distractors_credits.md` with `source: operator` on accepted rows + in-place edits or deletions for rejects. `distractors.json` updated to match any deletions.
- **Done when:** `uv run python -m toybox.activities.distractor_corpus --validate` reports `N entries, N credits rows, OK` with all rows tagged `source: operator`; no row still tagged `source: llm` in final commit.
- **Depends on:** N1.5.

### Step N2: Structural validator for `template_type: "element_microgame"`
- **Problem:** Extend the **Python** validator at `src/toybox/activities/_validator.py` (not the JSON schema — `_schema.json` already accepts arbitrary additional fields per `additionalProperties: true`). When a template carries `template_type === "element_microgame"`, enforce: exactly 4 steps; `steps[1].kind === "fork"` with `choices.length === 2`; `steps[2].kind === "fork"` with `choices.length === 2`; `steps[0]` and `steps[3]` are `kind === "text"`; `element_id` non-null on every step; `required_roles` includes `"guide_mentor"`; `ending_step.kind === "song"`. Then refresh `frontend/src/shared/types.ts` via the pydantic→TS hook so `template_type` is a typed field downstream.
- **Type:** code
- **Issue:** #172 (umbrella #167)
- **Flags:** `--tdd`
- **Produces:** validator code + 5 fixture templates (1 valid, 4 invalid — each one violating a different rule above) + regenerated `types.ts`.
- **Done when:** existing 1243 templates pass (smoke run against the production catalog); valid fixture passes; each invalid fixture fails with a specific error message naming the violated rule; `types.ts` shows `template_type?: "element_microgame"` (or string union if M4 ever gets typed).
- **Depends on:** N1 (so distractor loader exists for tests that synthesize valid templates).

### Step N3: Corpus peer-picker helpers
- **Problem:** Add `peer_in_family(element_id, rng) -> Element` and `peer_out_of_family(element_id, rng) -> Element` to `element_corpus.py`. Deterministic when `rng` seed is fixed; returns elements appropriate for the requesting element's `age_band` (i.e. don't suggest Plutonium as a peer for Gold to a 4yo).
- **Type:** code
- **Issue:** #173 (umbrella #167)
- **Flags:** `--tdd`
- **Produces:** two new public functions + tests.
- **Done when:** family-match returns same-family element ≠ self; cross-family returns different-family element; both filter by age_band; both raise on unknown element_id.
- **Depends on:** N1.

### Step N4: Generator script + 118 templates appended to `request_activity.json`
- **Problem:** `scripts/generate_element_microgames.py` (analog to `scripts/generate_meet_element_templates.py`) reads corpus + distractors, applies the 4-step / 2-fork narration scaffold using `story_seed_hooks` for Step 1 visual descriptor, picks one peer-in-family + one peer-out-of-family per element (seeded by `element_id` — deterministic), and **appends 118 entries** to [`src/toybox/activities/templates/branching/request_activity.json`](../src/toybox/activities/templates/branching/request_activity.json) under intent `request_activity`. Each entry carries `id: "element_microgame_<element_id>"`, `template_type: "element_microgame"`, `required_roles: ["guide_mentor"]`, `element_id` on every step, and `ending_step: {"kind": "song", "auto": true, "element_id": <id>}`. **Idempotence pattern matches M4** ([generate_meet_element_templates.py:36-44](../scripts/generate_meet_element_templates.py#L36)): strip-by-prefix (`element_microgame_*`) then re-append sorted by `atomic_number`. Re-runs produce byte-identical output (modulo formatting normalization). Accepts `--validate` flag like M4: re-loads `_load_intent_templates` after writing + asserts the count delta is exactly 118.
- **Type:** code
- **Issue:** #174 (umbrella #167)
- **Produces:** generator script + 118 entries appended to `request_activity.json` (NOT a new directory).
- **Done when:** all 118 entries validate (via N2's validator + the existing JSON-schema pass); generator unit-tested with deterministic seed (same input → byte-identical JSON); `--validate` reports `+118` delta. **Operator visual spot-check is folded into N6 UAT** (per plan-wrap §11.b) — N4 ships when automated assertions pass; UI quality signal comes from N6.
- **Depends on:** N2 + N3.

### Step N5: Smoke gate
- **Problem:** `tests/integration/test_phase_n_smoke.py` covers end-to-end: propose with `template_type=element_microgame`, walk Steps 1-4 selecting the correct fork on Step 2 and Step 3, assert reward fires AND the reward's `template_id` resolves to an **element-themed** song from M7 corpus when one exists for that element (e.g. `meet_element_h-1` → Hydrogen-themed song, not a generic), with explicit fallback assertion for elements where no themed song exists. Assert persona is `periodic_table` (Iridia). Assert `ElementCard` renders on every step (verify `element_id` on the wire envelope). Real DB, real corpora, real validators, no mocks.
- **Type:** code
- **Issue:** #175 (umbrella #167)
- **Flags:** `--tdd`
- **Produces:** new integration test file.
- **Done when:** sub-tests (a) propose succeeds, (b) all 4 steps walk + 2 fork picks resolve correctly, (c) persona is Iridia, (d) ElementCard `element_id` present on every step's envelope, (e) reward asserts element-themed song selection (+ fallback case). Smoke gate adds ~3-5s to suite.
- **Depends on:** N4.

### Step N6: iPad UAT (operator)
- **Problem:** Two parts. **(a) Cross-family spot-check** (folded in from former N4 done-when per plan-wrap §11.b): browse 12 entries across the three kid-likely families — 4 nonmetal + 4 transition_metal + 4 noble_gas — and confirm each reads as a cohesive 4-step game on the kiosk (Step 1 intro flows; Step 2 family fork makes sense; Step 3 fact fork is binary + understandable; Step 4 reward fires the right song). **(b) Walkthrough with Child B** on 4 element microgames: 2 familiar (Gold, Hydrogen) + 2 new (Helium, Copper). Quality bar mirrors M14: (a) renders OK, (b) Child B engages ≥50%, (c) no rejection, (d) no engine bug. Bonus criterion: (e) Child B picks the correct fork on at least 1 of 2 attempts per template — measures whether the binary forks are age-appropriate. Walk Phase M's deferred row #4 (`shrink_into_helium_balloon_voyage`) in the same session — N0 should have unblocked it.
- **Type:** operator
- **Issue:** #176 (umbrella #167)
- **Produces:** `documentation/runs/<YYYY-MM-DD>-phase-n-uat.md` (matches Phase M / K format).
- **Done when:** part (a) cross-family spot-check: ≥10 of 12 entries read coherently. Part (b) walkthrough: ≥3 of 4 pass quality bar; sub-criterion (e) hits across ≥50% of attempts. Phase M row #4 retest: PASS or new defect filed. All defects filed as follow-up issues, non-blocking per Phase K/M precedent.
- **Depends on:** N5 + N0 (the row #4 retest depends on N0).

## 6. Acceptance

Phase N closes when N0 + N0b + N1-prep + N1.5 + N1 + N2 + N3 + N4 + N5 ship + N6 UAT passes the quality bar. N0 also unblocks the deferred Phase M UAT row #4 (`shrink_into_helium_balloon_voyage`) — retested in the N6 session. Total scope estimate: **8 code build-steps + 2 operator steps** (was 7+2 before the 2026-05-18 N1.5 insertion that moved distractor authoring from operator-full-auth to generator-then-skim).

Step shape table for `/build-phase` dispatch:

| Step | Type | Flags | Depends on |
|---|---|---|---|
| N0 | code | `--reviewers code` | — |
| N0b | code | `--reviewers code` | — |
| N1-prep | code | `--reviewers code --tdd` | M1 corpus |
| N1.5 | code | `--reviewers code --tdd` | N1-prep |
| N1 | operator | — | N1.5 |
| N2 | code | `--reviewers code --tdd` | N1-prep (loader) |
| N3 | code | `--reviewers code --tdd` | M1 corpus |
| N4 | code | `--reviewers code` | N1 + N2 + N3 |
| N5 | code | `--reviewers code --tdd` | N4 |
| N6 | operator | — | N5 + N0 |

## 7. Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| Distractor accuracy (N1.5 + N1) | A wrong-but-plausible distractor confuses 4yo or mis-teaches. | Two-stage authoring: N1.5 deterministically generates entries from corpus rotations (no LLM hallucination surface); N1 operator skim-review flips source-tags per entry, edits or deletes rejects. Provenance gated: `_distractors_credits.md` per-entry row + loader rejection of `source: llm` without explicit env opt-in. Spot-check pass during N6 UAT. |
| Producer-of-`persona_reasoning` may be template-frozen (N0b) | If the wrong-name text is baked into 118 M4 template entries (not LLM-generated at runtime), N0b fix needs an idempotent regenerate of M4 — additional scope. | N0b's grep-first protocol surfaces this at build-step start (option iii). If template-frozen, N0b widens to include an M4 regenerate; cross-link noted in step problem statement so reviewer doesn't reject scope creep. |
| Frontend `types.ts` regeneration drift (N2) | Skipping the pydantic→TS codegen leaves `template_type` as untyped in `categorize()` (Phase O), forcing a string-literal compare instead of a union match. | N2's done-when explicitly requires `types.ts` regeneration + commit. CI already gates against stale codegen (per Phase L pattern). |
| Peer-in-family for sparse families (N3) | Halogens have 5 entries; lanthanides have 15; both small enough that the picker collides on small RNG seeds. | Picker explicitly excludes self; raises if family has <2 members at corpus-load time (caught by N3 test). Lanthanide/actinide families' age_band may exclude them entirely for 3-5 — fall back to nearest-family. |
| Fork-2 difficulty calibration | Step 3 fact-distractor binary too hard for 4yo if the "false" fact is too plausible. | UAT criterion (e) measures kid pick rate. If <30% correct on Step 3 forks during UAT, Phase O hardens distractors (more obviously wrong choices). |
| Template proliferation | 118 microgames + 118 M4 "Meet" templates = 236 element activities. Catalog bloat. | Acceptable trade for content depth. Phase O can retire M4 element templates if Phase N supersedes them in UAT. |
| Element coverage skew | Familiar elements (Gold, Helium, Hydrogen) dominate Child B's exposure; obscure elements (Curium, Tennessine) never surface. | Picker seed is deterministic per element_id but proposal weighting is uniform across templates. Acceptable for Phase N; Phase O could weight by `kid_familiarity_score` if it surfaces as a need. |
| Phase M defect D1 (persona_reasoning text) bleed | If `persona_reasoning` for Phase N templates also names a wrong persona, UAT noise from D1 obscures Phase N's own quality signal. | Phase N templates ship with `persona_reasoning` pre-filled at generator time using the corpus (`"Iridia loves teaching about {family} elements like {name}"`) — no LLM generation, no drift. D1 fix happens in parallel but Phase N doesn't depend on it. |

## 8. Testing strategy

### Unit tests
- **N1** — distractor corpus loader: 118 entries, schema validation, unknown-element rejection, injection guard.
- **N2** — schema validator: 4-step structure enforced, fork-on-step-2+3 enforced, element_id on every step.
- **N3** — peer pickers: deterministic per seed, family-membership invariant, age-band filter, self-exclusion, sparse-family raise.
- **N4** — generator: deterministic output, byte-identical re-runs, all 118 outputs validate.

### Integration test (smoke gate)
- **N5** — `test_phase_n_smoke.py` covers end-to-end propose → walk → reward. Real DB. No mocks. Catches producer-consumer drift between element_corpus → microgame template → ElementCard wire shape.

### Manual / UAT
- **N6** — Operator UAT with Child B on 4 element microgames. Quality bar matches M14 + extra fork-correctness criterion.

### Regression risk surface
- **Existing M4 element templates** — Phase N doesn't touch them; proposal weighting is uniform per-template, so M4 templates still surface. Both shapes coexist.
- **Reward matcher (Phase L)** — element-themed song corpus already handles `element_id` reward matching; Phase N reuses the same surface. Smoke gate N5 sub-test (e) covers.
- **Frontend ElementCard** — already renders on `element_id` steps. No new code; Phase N templates use the same wire shape.

### Performance
- 118 new templates → catalog goes 1243 → 1361. Phase K validated 1000 at load time; 1361 stays well under any concerning latency.
- Distractor corpus is ~80 KB; loader cache fine.

## 9. Resolved decisions (formerly open questions)

Resolved during `/plan-review` on 2026-05-18 (operator delegated defaults to assistant):

1. **Template emission path** — single file (`request_activity.json`), append + strip-by-prefix idempotence (matches M4). Not a per-template directory.
2. **`template_type` backfill on M4 templates** — none. M4 stays untyped. Phase O `categorize()` uses `element_id` as the Elements signal, so backfill adds churn without behavior change.
3. **N0b investigation protocol** — grep first then patch autonomously; build-step halts only if producer site cannot be located across the three candidate surfaces (frontend / ai-tools / template fields).
4. **Distractor provenance** — `_distractors_credits.md` sibling file (matches existing `_credits.md` pattern); loader gates `source: llm` rows behind `TOYBOX_ALLOW_LLM_DISTRACTORS=1` env var (default off).
5. **Distractor authoring flow** (resolved 2026-05-18 mid-Phase-N, after Phase M UAT close-out): split into N1.5 (deterministic generator, code) + N1 (operator skim-review, ~10-15 min). Replaces original "operator authors 118 by hand" (~40-60 min). Kid-safety property preserved via the review-pass; the env-var loader gate from §9.4 enforces that final-shipped data is operator-reviewed (`source: operator`) unless the env opt-in is set.

Still-open question (deferred to N6 UAT signal):

- **Fork-2 axis (fixed vs varied)** — this plan locks Fork 2 to fact-distractor pairs from `distractors.json`. N6 UAT criterion (e) measures kid pick rate; <30% suggests broadening to varied axes (color / phase / use / origin) in Phase O follow-up.

## 10. Status

**2026-05-18** — drafted during Phase M UAT pause after defect D3. `/plan-review` + `/plan-wrap` passes complete:
- `/plan-review` edits — §2 + §3 + N0b + N1 + N2 + N4 + N5 + §7 + §9 (resolved decisions).
- `/plan-wrap` edits — split N1 into N1-prep (code) + N1 (operator) per §11 blocker; moved N4's kiosk spot-check into N6 per §11 symmetric blocker; added Fresh-Reader Pointers + Acronyms + Element shape + element_id format + M7 corpus shape + codegen command to §2; added step-shape dispatch table to §6; cleaned up duplicate §2 subsections.

Ready for `/repo-sync` → mint umbrella + 9 step issues (N0, N0b, N1-prep, N1, N2, N3, N4, N5, N6). Phase M UAT close-out (11/12 PASS, 1 DEFERRED) runs in parallel; row #4 (`shrink_into_helium_balloon_voyage`) retest folded into N6.

**2026-05-18 — mid-Phase-N redirect** (operator paused at /build-phase Step 0 to ask "why am I authoring N1?"). Resolved by inserting **N1.5 (code, deterministic generator)** between N1-prep and N1; **reshaped N1** from full 118-entry authoring (~40-60 min) to skim-review pass (~10-15 min). §3, §6 table, §6 acceptance count (7→8 code steps), §7 risk row, §9 resolved decisions (#5 added) all updated. New GH issue to be minted for N1.5; #171 N1 body to be revised. Phase N orchestration resumes from N0 dispatch after these issue updates land.
