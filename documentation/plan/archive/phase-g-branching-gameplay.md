# Phase G — Branching gameplay (multi-option steps + variable length)

> **ARCHIVED 2026-05-11: phase shipped.** See [plan.md status](../../plan.md#status) for the authoritative completion record. Internal cross-refs in this doc are frozen as of archival.

> **Scope:** Phase G build plan — extend the offline activity template model from a fixed-5 linear list of steps to a directed acyclic graph with optional choice points. Adds three optional fields to the step schema (`id`, `next`, `choices`), drops the `minItems=5, maxItems=5` constraint, switches `activity_steps` from pre-seeded to lazy insertion, and renders a multi-button choice UI on the kiosk when a step has choices. Carries the per-step `**Problem:**/**Type:**/**Issue:**/**Flags:**/**Status:**` shape that `/build-phase` parses. Sequenced after Phase F.5 (DONE 2026-05-09); precedes Phase E and ships the dynamic-step-count + lazy-insertion primitives that E5 had planned to ship. Top-level overview is in [../plan.md](../plan.md). Investigation that led to this phase is in this conversation; no separate run-doc.

## What this feature does

Replaces the linear 5-step template model with a graph model. Today every activity is exactly 5 steps and the kiosk shows one "Next" button; the rhythm is identical across all ~20 templates and a child burns through the variety in an afternoon.

Phase G adds three optional schema fields on `step`:

- **`id: str`** — stable identifier for branch targeting; required only on steps that are referenced by another step's `next` or a `choices[].next`. Steps with no `id` can still be reached via the implicit fall-through (rule 3 below) but cannot be jumped to.
- **`next: str`** — explicit successor step `id`, overriding the default fall-through to the next array position.
- **`choices: [{label, next}]`** — branching point; the kiosk renders one button per choice, the kid picks one, the activity follows `choices[i].next`.

The schema also relaxes `minItems=5, maxItems=5` to `minItems=3, maxItems=20`, so templates can be short (3-step micro-quests) or long (multi-branch missions). All ~20 existing templates have neither `next` nor `choices` and stay valid unchanged — they just rely on the implicit "fall through to next array position" rule that already matches today's behavior.

Backend changes are scoped to: schema validation, Pydantic loosening, two additive DB migrations (`activity_steps.chosen_label` + `activity_steps.choices_json`; `activities.slot_fills_json`), lazy step insertion at activity creation, and a `choice_index` body field on `POST /api/activities/{id}/advance` plus a new `choices` field on the activity-step response/WS shape. Frontend changes are scoped to: choice-button rendering when a step has `choices`, error-state handling on advance, and dropping the "of 5" suffix from any progress indicator.

The template authoring surface picks up immediately: a single 7-node template with two 2-way branches yields four distinct playthroughs from one template id; kids replay to see different endings. Mixing 3-step bursts with 8-step missions breaks the "5 cards every time" rhythm.

## Existing context

- **Template authoring** lives at [src/toybox/activities/templates/](../../src/toybox/activities/templates/) — one file per intent (`boredom.json`, `request_play.json`, `request_story.json`, `request_activity.json`), validated against [_schema.json](../../src/toybox/activities/templates/_schema.json) at startup. Today's schema enforces `minItems=5, maxItems=5` on `steps` ([_schema.json:51-52](../../src/toybox/activities/templates/_schema.json#L51-L52)).
- **Step model** at [src/toybox/activities/models.py:96](../../src/toybox/activities/models.py#L96) — `Field(min_length=5, max_length=5)` on the activity's `steps` list mirrors the JSON schema constraint.
- **Activity creation** in [src/toybox/activities/generator.py](../../src/toybox/activities/generator.py) pre-seeds all 5 steps into `activity_steps` at creation. Slot fills (`{toy}`, `{adjective}`, etc.) resolve once per activity via [src/toybox/activities/slots.py](../../src/toybox/activities/slots.py); generation is fully deterministic from seed. **Today the resolved fills are embedded directly into each pre-seeded step's `body` string with no separate persistence** — Phase G's lazy insertion needs them stored separately so the advance handler can re-render new step bodies + choice labels with the same fills as step 1.
- **Advance state machine** at [src/toybox/api/activities.py:1356-1435](../../src/toybox/api/activities.py#L1356-L1435) — `POST /api/activities/{id}/advance` increments the `current` flag to the next sequential step. `If-Match-Version` required (**invariant 3** from [plan.md](../plan.md#key-invariants-must-respect-on-every-edit) — every activity mutation requires the `If-Match-Version` header carrying the activity's current `version`; mismatch returns 409 with the current version in the body so the client can refetch and retry).
- **DB shape** at [src/toybox/db/migrations/0001_initial.sql](../../src/toybox/db/migrations/0001_initial.sql) — `activity_steps(id, activity_id, seq, body, sfx, expected_action, current)`; [migration 0006](../../src/toybox/db/migrations/0006_activity_step_action_slot.sql) added `action_slot`. Migration counter is at 0006; **G2 adds 0007 (`chosen_label` + `choices_json` on `activity_steps`) and 0008 (`slot_fills_json` on `activities`)**.
- **Anti-signal feedback** at [src/toybox/activities/feedback.py](../../src/toybox/activities/feedback.py) — `signature = {template_id}:{slot_fingerprint}` ([data-model.md:215](data-model.md#L215)), where `slot_fingerprint` is sha256 of the sorted `key=value` pairs of slot fills that contribute to the signature (per `slots.SlotRegistry.signature_set` — `{toy}` and `{room}` contribute today; parametric word-list slots like `{adjective}` and `{action_verb}` do NOT). Phase G keeps this scheme unchanged — feedback still keys on template + signature-contributing slot fills, not on which path the kid took. Path-aware feedback is deferred.
- **Frontend kiosk** at [frontend/src/child/components/](../../frontend/src/child/components/) — `StepCard.tsx` renders body text and `action_slot` sprite; `NextStepButton.tsx` posts to `/advance`.
- **Phase E coordination:** [phase-e.md](phase-e.md) plans non-linear gameplay with a local model authoring steps one at a time. That phase's E5 step had planned the migration + Pydantic loosening + child-UI dynamic-step rendering. **Phase G ships those primitives first** (offline-template-driven). Phase E inherits the loosened constraint and the lazy-insertion path; what stays in Phase E is local-model-driven authoring, the 30-second transcript-reaction window, parent pause/regenerate-from-here signals, and `is_complete: bool` per step.
- **Operating mode:** per [`feedback_autonomous_build_bundled_ui.md`](../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md) — `--reviewers code` for code steps; visual UI verification batches into G6 (operator-driven smoke gate).

## Vocabulary and conventions

**Graph terms:**
- **Node** = a single step (one entry in the template's `steps` array). Has stable `id` if used as a branch target.
- **Edge** = the successor relationship — implicit (next array position), explicit (`next: "step_id"`), or branching (`choices[i].next`).
- **Terminal** = a node with no outgoing edge (no `next`, no `choices`, AND it's the last step in the array). Reaching a terminal ends the activity.
- **Path** = the ordered list of nodes a single playthrough visits. Two playthroughs of the same template can have different paths.

**Existing step fields** (today's shape — Phase G leaves these untouched):
- `text: str` — required; rendered on the kiosk's StepCard. Supports slot placeholders (`{toy}`, `{room}`, `{adjective}`, `{action_verb}`, `{prop}`, `{body_part}`, `{count}`). Min length 1, max length 600.
- `sfx?: str | null` — optional sound-effect tag (e.g. `transition`, `success`). Max length 64.
- `expected_action?: str | null` — optional parent-coaching hint (NOT shown to the child). Max length 64.
- `action_slot?: str | null` — optional sprite-rendering vocabulary key. One of the 10 fixed members of `ACTION_SLOTS` (Phase F): `idle`, `pointing`, `looking`, `jumping`, `cheering`, `thinking`, `waving`, `running`, `sleeping`, `confused`. NULL means render no sprite.

**New step fields added by Phase G** (all optional; backward-compatible with existing templates):
- `id?: str` — stable identifier; required only on steps that are branch targets. Pattern `^[a-z0-9][a-z0-9_]*$`, max 32 chars (tighter than template ids since they appear in JSON as targets).
- `next?: str` — explicit successor. Forbidden together with `choices` (would be ambiguous).
- `choices?: [{label, next}]` — 2-4 choice branches. `label` is the button text (supports slot substitution like `text` does); `next` is the successor step id.

**Edge resolution rules** (deterministic; one outcome per step):
1. Step has `choices` → kid picks one; activity advances to `choices[i].next`.
2. Else step has `next` → advance to `next`.
3. Else if not the last step in the array → advance to next array position (preserves current linear behavior).
4. Else → terminal; activity completes.

**How choices reach the kiosk** (load-bearing — fixes the gap that the kiosk needs rendered button text, not template `{toy}` placeholders):
When the advance handler inserts a step that has `choices`, it renders each `choices[i].label` using the activity's persisted `slot_fills` and writes the resulting JSON-encoded list of strings (`["Sneak past Penguin", "Charge in bravely"]`) to the new `activity_steps.choices_json` column. The activity-state API response and WS step payload include `choices: [{label, choice_index}, ...] | null` — the serializer expands the stored array into objects (array index = `choice_index`). The kiosk renders one button per entry; on tap, posts `{choice_index}` to `/advance`. Storing rendered labels per-step (rather than re-rendering on every read) matches today's pattern of pre-rendering `body` at insert time, and keeps in-flight activities stable across template edits.

**Activity-step payload shape** (the response/WS object the kiosk consumes — given so G3/G4 don't have to grep the existing serializer):

```jsonc
// today (pre-Phase G):
{
  "id": "uuid-v4-string",
  "seq": 1,
  "body": "Penguin declares this is now a morning expedition!",  // rendered, no {slot} placeholders
  "sfx": "transition",                                            // or null
  "expected_action": null,                                        // or short coaching hint string
  "current": true,                                                // exactly one row true at a time
  "action_slot": "cheering"                                       // or null
}

// Phase G adds (additive, all nullable):
{
  // ...all fields above, unchanged...
  "chosen_label": null,                                           // string the kid picked at THIS step (G2 column)
  "choices": null                                                 // [{ "label": "...", "choice_index": 0 }, ...] when step has choices, else null (G3 serializer; backed by activity_steps.choices_json)
}
```

The serializer transforms `activity_steps.choices_json` (stored as JSON array of strings) into the response's `choices: [{label, choice_index}]` (objects) by enumerating array indices. `chosen_label` is read from the column directly.

**Slot-fill persistence** (load-bearing — required by lazy advance):
At activity creation time, the resolved slot map (`{room: "kitchen", toy: "Penguin", adjective: "sparkly", ...}`) is persisted as JSON in the new `activities.slot_fills_json` column. The advance handler reads it when inserting subsequent steps so the body and choice labels render with the same fills as step 1. Anti-signal signature computation continues to hash the fills directly (path-agnostic; unchanged from today).

**Idempotent advance under retry**:
`If-Match-Version` is mandatory on every `/advance` POST (existing invariant 3). On a successful advance the version increments; a retry with the stale version returns 409 with no INSERT side-effect. Lazy step insertion does NOT change idempotency semantics — the version-conflict path is the same as today, just with one INSERT inside the transaction instead of zero.

**Build-step flag conventions** (per the `/build-step` skill — same as Phase F.5):
- `--reviewers code` — four code-quality reviewers; default for autonomous-build operating mode.
- `--reviewers full` — code + runtime reviewers.
- `--isolation worktree` (default) — agent works in a temporary git worktree.
- `--ui` — Playwright UI evidence pass.

**Step `Type:` taxonomy:** `code` (autonomous `/build-step`), `operator` (manual procedure), `wait` (long-wall-clock observation).

## Scope

**In:**
- `_schema.json` extended with optional `id`, `next`, `choices` on `step`; `steps.minItems` relaxed `5 → 3`, `steps.maxItems` relaxed `5 → 20`
- Pydantic `Activity.steps` constraint relaxed from `min_length=5, max_length=5` to `min_length=3, max_length=20`; new `Step.id`, `Step.next`, `Step.choices` optional fields
- Template-load-time validator: unique `id`s within a template, every `next` / `choices[].next` resolves to an existing `id` in the same template, all steps reachable from `steps[0]` (no orphans), no cycles, at least one path reaches a terminal, `next` and `choices` are mutually exclusive, choice count is 2-4
- `activity_steps` migration 0007: add nullable `chosen_label TEXT` column AND nullable `choices_json TEXT` column; existing rows default NULL on both. `choices_json` stores the rendered choice labels (JSON array of strings) when the step has choices; NULL otherwise
- `activities` migration 0008: add `slot_fills_json TEXT NOT NULL DEFAULT '{}'` column for slot-fill persistence; existing rows backfill to `'{}'` (their pre-seeded step bodies already have rendered fills, so the empty default is correct for in-flight activities)
- Generator change: at activity creation, persist the resolved slot map to `activities.slot_fills_json`; insert ONLY `steps[0]` into `activity_steps` (lazy mode); if `steps[0]` has `choices`, render labels and write `choices_json`. Anti-signal signature computation unchanged (still hashes the slot fills directly)
- API extension: `POST /api/activities/{id}/advance` accepts optional body `{"choice_index": int}`; required (400 with `code=choice_required`) when current step has `choices`; forbidden (400 with `code=choice_not_allowed`) when current step has none. Response shape extends activity-step payloads to include `choices: [{label: str, choice_index: int}] | null`, parsed from `choices_json`. WS step payload mirrors this. `If-Match-Version` mandatory; mismatch returns 409 with no INSERT
- Frontend choice rendering: when `step.choices` is present, render N buttons (each posting `{choice_index}`); otherwise render the existing `NextStepButton`. On 4xx/5xx response the button re-enables; on 409 the activity state is refetched. Drop "of N" from the progress indicator
- Validator unit tests + JSON schema fixtures + Pydantic round-trip tests
- DB migration test asserting old activities still load + advance correctly (no breakage of in-flight activities at upgrade time)
- 4 new branching templates — 1 per intent — each with at least one choice point and at least 2 distinct endings
- Operator-driven smoke gate (G6): on iPad kiosk, run 5 activities — at least 1 linear (regression) + 4 branching (one per intent) — and verify the choice buttons render, advance correctly, and no console / WS errors

**Out:**
- Path-aware anti-signal feedback (signature scheme unchanged; deferred to a follow-on if data shows a need)
- Cycles in the graph (validator hard-rejects; can revisit if a use case appears)
- Local-model-authored branching (Phase E concern; offline templates only here)
- Multi-toy choice consequences (e.g., "if {toy} is a unicorn, branch to fairy-glade") — out of scope; choices are kid-driven, not slot-driven
- `is_complete: bool` per step (Phase E concern; offline templates use explicit terminals)
- Variants (`variants: [...]` at template level) — subsumed by branching; not adding as a separate primitive
- Re-authoring all existing templates as branching (existing 5-step templates stay; G5 only adds 4 new ones)
- A separate `/api/activities/{id}/choose` endpoint (folded into `/advance` per design decision below)
- Choice-history visibility in the parent dashboard (the `chosen_label` column ships, but no parent UI surface this phase)
- Mid-activity backtracking / "go back" (one-way traversal only)
- Animated choice buttons / haptics (default React Button styling on iPad — same as `NextStepButton`)

## Impact analysis

| File / module | Nature | Notes |
|---|---|---|
| `src/toybox/activities/templates/_schema.json` | MODIFY | Add optional `id`, `next`, `choices` on `step`; relax `steps.minItems` 5→3, `steps.maxItems` 5→20; add `oneOf` constraint mutual-excluding `next` and `choices` |
| `src/toybox/activities/models.py` | MODIFY | Add `Step.id: str \| None`, `Step.next: str \| None`, `Step.choices: list[Choice] \| None`; new `Choice(label: str, next: str)` model; relax `Activity.steps` `Field(min_length=5, max_length=5)` → `Field(min_length=3, max_length=20)` |
| `src/toybox/activities/generator.py` | MODIFY | New `validate_template_graph(template)` function called at load time — checks unique ids, all targets resolve, no orphans, no cycles, at least one terminal reachable, `next` ⊕ `choices`. At activity creation: persist resolved slot map to `activities.slot_fills_json`; switch from "insert all 5 steps" to "insert only `steps[0]`"; if `steps[0]` has `choices`, render labels using slot fills and write `choices_json`. Anti-signal signature computation unchanged (still `{template_id}:{slot_fingerprint}`) |
| `src/toybox/db/migrations/0007_activity_step_choices.sql` | NEW | Two ALTER statements: `ALTER TABLE activity_steps ADD COLUMN chosen_label TEXT;` + `ALTER TABLE activity_steps ADD COLUMN choices_json TEXT;`. Forward-only; old rows default NULL on both. `chosen_label` records the label the kid picked at this step (NULL = linear advance or terminal); `choices_json` is the rendered JSON list of choice labels for steps that have choices (e.g. `["Sneak past Penguin", "Charge in bravely"]`), NULL otherwise |
| `src/toybox/db/migrations/0008_activity_slot_fills.sql` | NEW | `ALTER TABLE activities ADD COLUMN slot_fills_json TEXT NOT NULL DEFAULT '{}';`. Forward-only; default `'{}'` covers in-flight activities (their pre-seeded step bodies already have rendered fills, so empty fills are correct for them; new activities populate from the slot resolver at creation time). Documented inline as "JSON-encoded resolved slot map; read by the lazy advance handler when rendering subsequent steps" |
| `src/toybox/api/activities.py` | MODIFY | Extend `POST /api/activities/{id}/advance` body to accept optional `choice_index: int`. Resolve next step server-side via the edge rules; on choice required + missing → 400 `code=choice_required`; on choice not allowed but provided → 400 `code=choice_not_allowed`; on out-of-range index → 400 `code=invalid_choice_index`. Insert the next step into `activity_steps` at the next `seq`, mark previous as not current, record `chosen_label` on the previous step's row when applicable, render new step's body + (if applicable) `choices_json` using `activities.slot_fills_json`. `If-Match-Version` requirement preserved (409 on mismatch, no INSERT side-effect on retry). Response shape extends step payloads with `choices: [{label, choice_index}] \| null` parsed from `choices_json`; WS broadcast mirrors this |
| `src/toybox/activities/feedback.py` | UNCHANGED | Signature scheme unchanged (`{template_id}:{slot_fingerprint}`); path-agnostic feedback continues working |
| `src/toybox/activities/templates/branching/*.json` | NEW (4 files) | One branching template per intent (`boredom`, `request_play`, `request_story`, `request_activity`). Subdirectory keeps the new content visible; the loader globs `**/*.json` under `templates/` (verify or extend in G1) |
| `frontend/src/child/components/StepCard.tsx` | MODIFY | When `step.choices` is present, render the choice buttons in place of `<NextStepButton>`. Layout: vertical stack on iPad portrait; minimum touch target 44pt per Apple HIG |
| `frontend/src/child/components/NextStepButton.tsx` | UNCHANGED | Retains current shape for non-branching steps. No refactor in this phase |
| `frontend/src/child/components/ChoiceButton.tsx` | NEW | One button per choice; posts `{choice_index}` to `/advance`. Disabled while in-flight (prevents double-tap during the lazy step insert). Handles 4xx (re-enable + inline error indicator), 409 (refetch activity state), 5xx (re-enable + global toast if available) |
| `frontend/src/child/components/ProgressIndicator.tsx` (or wherever "step N of 5" lives) | MAYBE MODIFY | If a "step N of 5" string exists in a component (G4 greps to verify), drop the denominator (show "step N" or nothing). If only in comments / fixtures / test snapshots, leave alone. Grep result recorded in G4 commit |
| `frontend/src/shared/types.ts` | REGEN | `pydantic-to-typescript` codegen runs on the new `Step.id/next/choices` fields and `Choice` model; pre-commit hook catches drift per **invariant 9** (Pydantic ↔ TypeScript codegen is a pre-commit hook; drift in `frontend/src/shared/types.ts` is a check failure) |
| `tests/unit/activities/test_template_loader.py` | MODIFY | Add fixture templates (valid + invalid: orphan, cycle, missing target, ambiguous next+choices, choice count out of range) and assert each is accepted/rejected correctly |
| `tests/unit/activities/test_generator.py` | MODIFY | Update for lazy step insertion: assert only `steps[0]` is in `activity_steps` after creation; assert `activities.slot_fills_json` populated; assert `choices_json` populated when `steps[0]` has choices; assert anti-signal signature stable; regression test for pre-G2 activities (pre-seeded 5 rows + empty `slot_fills_json`) still advancing correctly |
| `tests/unit/api/test_activities_advance.py` | MODIFY (or NEW) | Cover all four advance branches (linear, explicit-`next`, choices-resolves, terminal) + all three 400 error codes + idempotency under stale `If-Match-Version` retry (assert 409 + no row inserted) |
| `tests/integration/test_branching_e2e.py` | NEW | End-to-end: load a branching template fixture, create an activity, advance through path A in one test and path B in a second, assert: `activity_steps` rows match the chosen path in `seq` order; `chosen_label` matches the rendered button text; `choices_json` has no unresolved `{slot}` placeholders; WS payload includes `choices: [{label, choice_index}]`; anti-signal signature unchanged across paths |
| `tests/fixtures/activities/branching_*.json` | NEW (3 files) | Test fixtures: a minimum valid branching template, an invalid (orphan) template, an invalid (cycle) template |
| `documentation/plan/data-model.md` | MODIFY | Add `chosen_label` and `choices_json` to the `activity_steps` table; add `slot_fills_json` to the `activities` table |
| `documentation/plan/activity-loop.md` | MODIFY | New short section documenting the graph model + edge rules + lazy insertion + slot-fill persistence |
| `documentation/plan/phase-e.md` | MODIFY | Note that E5's "migration + Pydantic loosening + child UI dynamic-step rendering" sub-deliverable is now inherited from Phase G; what remains in E5 is local-model authoring, transcript-reaction window, pause/regenerate-from-here, and `is_complete: bool` |
| `documentation/runs/<date>-branching-gameplay-uat.md` | NEW (G6 deliverable) | UAT run-doc — 5 activities, choice-tap evidence, console/WS log snapshots |

## Design decisions

### One endpoint, optional `choice_index` (vs new `/choose` endpoint)

Folding the choice into `/advance` keeps the state machine in one place and avoids a parallel auth/validation/version surface. The kiosk already calls `/advance`; adding an optional body field is one new line on the frontend. Rejected: a separate `/api/activities/{id}/choose` endpoint — would duplicate `If-Match-Version` handling, version conflict semantics, and breaker logic for no clear gain.

### Lazy step insertion (vs pre-seed all reachable steps)

Pre-seeding all reachable nodes was tempting because the graph is finite and small (≤20 nodes per template). Rejected because: (1) the kid's actual playthrough should be the visible state in `activity_steps` — operator and parent UI inspections become unambiguous; (2) `chosen_label` records cleanly on previous-step rows without needing a separate choices table; (3) replay/audit reads the activity_steps table top to bottom and gets the kid's path as a list, no graph traversal required. Cost: each `/advance` does one extra INSERT. Negligible at SQLite speeds.

### Edge rule fall-through preserves current templates

Rule 3 (implicit advance to next array position) was chosen specifically so all ~20 existing templates load and play without modification. Verified: every existing template has neither `next` nor `choices` and ends at array index 4 → falls through 0→1→2→3→4 → terminal at 4 → activity completes. Identical to today.

### Mutual exclusion of `next` and `choices`

A step cannot have both. If both were allowed, the runtime would have to decide which wins, and authors would gravitate to leaving one as a "default" — confusing semantics. The validator rejects ambiguous shapes at load time.

### Cycles disallowed (for now)

A cycle could let a kid loop "until they want to stop" — interesting, but introduces unbounded activity length and complicates `chosen_label` (which previous row do you record on?). Defer to a future phase if a use case appears. The validator BFS-from-start rejects revisits.

### Anti-signal signature stays template-level, not path-level

The current signature scheme `{template_id}:{slot_fingerprint}` works fine — parent feedback is on the experience as a whole. Adding path-awareness would require adding the path hash to the signature and segmenting feedback by path, which doubles the data needed for the anti-signal heuristic to converge. Defer until kids have played enough branching templates to know if "I liked the snack ending but not the fight ending" is a real signal worth capturing.

### Choice count constrained to 2-4

Two is the minimum for a meaningful choice. Four fits on iPad portrait without scrolling at a 44pt touch target. Five+ becomes a list and degrades the "feel" of choosing. Hard-validated at the schema layer.

### Drop the 5-step constraint without lifting the upper bound to ∞

`maxItems=20` is generous (a 20-node graph supports ~10 unique playthroughs) but bounded — a runaway template with hundreds of steps is almost certainly an authoring bug. 20 chosen because: (1) the 4 templates planned for G5 fit comfortably in 7-12 nodes each; (2) double the headroom; (3) round number.

### Branching templates land in `templates/branching/` subdirectory

Keeps the new content visibly separate so operators can see at a glance "these are the branching ones." Loader globs `**/*.json` under `templates/`; G1 verifies (and extends if needed) the recursive load. Does NOT change template intent semantics — each branching template still declares its `intent` field and is grouped with linear siblings at runtime.

### Render choices to a column at insert time (vs. recompute on every read)

Rendered choice labels live in `activity_steps.choices_json`, written when the choice-bearing step is inserted. Alternative: compute on every API/WS read by joining template + `activities.slot_fills_json`. Rejected because: (1) consistent with today's pattern of pre-rendering `body` at insert time — same data lifecycle for both fields; (2) keeps in-flight activities stable across template edits (an operator tweaking a template doesn't surprise a kid mid-activity); (3) simpler API serializer (one column read, no template lookup needed). Cost: choices_json is denormalized data. Acceptable — the table already denormalizes `body` the same way.

### Migration is forward-only and additive

Migration 0007 adds two nullable columns (`chosen_label`, `choices_json`) on `activity_steps`. Migration 0008 adds `slot_fills_json` on `activities` with `NOT NULL DEFAULT '{}'`. No backfill, no data loss, no rollback path. Consistent with **invariant 10** (forward-only migrations; v1 has no rollback path and no DB backups; abort + preserve DB on failure, recover via `documentation/operator/recovery.md`). In-flight activities at upgrade time keep running on the linear advance path — their pre-seeded steps already have rendered fills in `body`, so their empty `slot_fills_json` default is correct (the lazy handler is never called on these because all their steps already exist), and rule 3 handles their advance flow unchanged.

## Build steps

| # | Step | Type | Reviewers (canonical) | Done-when summary |
|---|------|------|----------------------|-------------------|
| G1 | Schema + Pydantic + validator: extend `_schema.json` with `id`/`next`/`choices`, relax minItems/maxItems, add `validate_template_graph`, update `Step` + new `Choice` models, fixture-driven validator tests | code | `--reviewers code` | `_schema.json` extended; `validate_template_graph()` rejects orphan/cycle/missing-target/ambiguous-next-choices/choice-count-out-of-range fixtures; existing 4 template files still load and validate; ruff + mypy strict + pydantic-to-ts codegen clean |
| G2 | DB migrations 0007 + 0008; slot-fill persistence; lazy step insertion in generator | code | `--reviewers code` | Migrations 0007 (`chosen_label` + `choices_json` on `activity_steps`) and 0008 (`slot_fills_json` on `activities`) land; generator persists slot fills + inserts only `steps[0]` at activity creation, populating `choices_json` if applicable; old activities still advance correctly; seq-grep audit recorded; unit tests cover linear + branching first-step + regression for pre-G2 activities |
| G3 | API: extend `/advance` with `choice_index`; edge resolution; response shape; idempotency; tests | code | `--reviewers code` | `POST /api/activities/{id}/advance` accepts optional `choice_index`; resolves next step per the four edge rules and renders body + choices using `slot_fills_json`; returns 400 with `choice_required` / `choice_not_allowed` / `invalid_choice_index` on bad input; activity-step response/WS payload extended with `choices: [{label, choice_index}] \| null`; idempotency under retry verified (stale `If-Match-Version` → 409 + no row inserted); all four advance branches covered in tests |
| G4 | Frontend: ChoiceButton component, StepCard branch-rendering, drop "of N" progress denominator | code | `--reviewers code` (UI evidence batched to G6 per `feedback_autonomous_build_bundled_ui.md`) | `ChoiceButton.tsx` ships; `StepCard.tsx` renders choice buttons when `step.choices` is present and `NextStepButton` otherwise; progress indicator no longer shows "of 5"; pydantic-to-ts codegen reflects new `Step.choices` + `Choice` shape; component-level tests + Playwright smoke (in G6) cover both render paths |
| G5 | Operator: author 4 branching templates (one per intent) under `templates/branching/<intent>.json` | operator | n/a | 4 JSON files committed; each has 2-4 choice points and at least 2 distinct endings; validator passes on all 4 at app boot; one-line note in run-doc per template documenting the design intent + paths |
| G6 | UAT smoke gate: iPad kiosk, 5 activities (1 linear regression + 4 branching, one per intent), evidence captured | operator | n/a | UAT run-doc lands with: 5 activities played end-to-end, choice-tap screenshots, no console errors, no WS errors, activity_steps rows match the chosen path, `chosen_label` populated as expected; pass/fail recorded |

**Issues:** Phase G umbrella → #70 · G1 → #71 · G2 → #72 · G3 → #73 · G4 → #74 · G5 → #75 · G6 → #76.

**Sequencing:**

```
G1 ─┬─→ G2 ─→ G3 ─┐
    ├─→ G4 ───────┤─→ G6
    └─→ G5 ───────┘
```

G1 lands first — everyone reads the schema + Pydantic + pydantic-to-ts codegen output. After G1, three branches run in parallel: (a) backend `G2 → G3` (G3 depends on G2's lazy-insertion contract + new columns); (b) frontend `G4` alone (only needs G1's TypeScript types; mocks the API per the contract pinned in this plan-doc); (c) operator `G5` alone (only needs the schema + validator). G2 ⊥ G4 ⊥ G5. G3 ⊥ G4 ⊥ G5. G6 is the bundled UI gate at the end and depends on all three branches.

#### Step G1: Schema + Pydantic + validator

- **Problem:** Extend [`src/toybox/activities/templates/_schema.json`](../../src/toybox/activities/templates/_schema.json) with three optional step fields per the §"Vocabulary and conventions" spec: `id` (pattern `^[a-z0-9][a-z0-9_]*$`, max 32 chars), `next` (string), `choices` (array of `{label: str, next: str}`, length 2-4). Add a `oneOf` or equivalent mutual exclusion between `next` and `choices`. Relax `steps.minItems` 5→3 and `steps.maxItems` 5→20. Update [`src/toybox/activities/models.py`](../../src/toybox/activities/models.py): add `Step.id: str \| None`, `Step.next: str \| None`, `Step.choices: list[Choice] \| None`; new `Choice(label: str, next: str)` Pydantic model; relax `Activity.steps` `Field(min_length=5, max_length=5)` → `Field(min_length=3, max_length=20)`. Add a new function `validate_template_graph(template) -> None` (raises on invalid) in [`src/toybox/activities/generator.py`](../../src/toybox/activities/generator.py) (or a new `_validator.py` module if the generator file is getting long) that enforces, in order: (a) all `id`s in the template are unique; (b) every `next` and `choices[].next` resolves to a step `id` in the same template; (c) all steps reachable from `steps[0]` (BFS — no orphans); (d) no cycles (BFS revisit = error); (e) at least one path reaches a terminal node; (f) no step has both `next` and `choices`; (g) `len(choices) in [2,3,4]` when present. Wire `validate_template_graph` into `load_templates()` so any failing template crashes startup with a clear error message naming the template_id and the violation. Update fixtures + unit tests in [`tests/unit/activities/test_template_loader.py`](../../tests/unit/activities/test_template_loader.py) (or wherever the existing template tests live — verify in G1) to cover each rejection branch + a happy-path branching fixture. Verify the existing 4 production template files (`boredom.json`, `request_play.json`, `request_story.json`, `request_activity.json`) still load + validate after the schema relaxation. Verify the recursive load path: place a fixture under `tests/fixtures/activities/branching/` and assert it's loaded; if the loader doesn't currently recurse, extend it. `pydantic-to-typescript` codegen must regenerate `frontend/src/shared/types.ts` cleanly (the new fields appear as optional). ruff + mypy strict clean.
- **Type:** code
- **Issue:** #71
- **Flags:** `--reviewers code`
- **Status:** DONE (2026-05-09)
- **Depends on:** none (kicks off Phase G)
- **Parallel-safe with:** none — strictly first; G2/G3/G4/G5 all read the schema or the Pydantic shape
- **Done when:** Schema extended; Pydantic models updated; `validate_template_graph()` ships and is wired into `load_templates()`; orphan/cycle/missing-target/ambiguous/choice-count fixtures all rejected with clear errors naming the template; all existing production templates still load; pydantic-to-ts codegen clean; ruff + mypy strict clean.

#### Step G2: DB migrations + slot-fill persistence + lazy step insertion

- **Problem:** Add **two** migrations:
  - [`src/toybox/db/migrations/0007_activity_step_choices.sql`](../../src/toybox/db/migrations/0007_activity_step_choices.sql) — two ALTER statements: `ALTER TABLE activity_steps ADD COLUMN chosen_label TEXT;` AND `ALTER TABLE activity_steps ADD COLUMN choices_json TEXT;`. Inline comment per column: `chosen_label` = "label of the choice the kid picked at this step, if any; NULL means linear advance or terminal"; `choices_json` = "JSON array of rendered choice-button labels for this step (e.g. `[\"Sneak past Penguin\", \"Charge in\"]`); NULL when step has no choices".
  - [`src/toybox/db/migrations/0008_activity_slot_fills.sql`](../../src/toybox/db/migrations/0008_activity_slot_fills.sql) — single statement: `ALTER TABLE activities ADD COLUMN slot_fills_json TEXT NOT NULL DEFAULT '{}';`. Inline comment explains: "JSON-encoded resolved slot map (e.g. `{\"toy\": \"Penguin\", \"room\": \"kitchen\", \"adjective\": \"sparkly\"}`); set at activity creation; read by the lazy advance handler in G3 to render subsequent step bodies + choice labels with the same fills as step 1."
  
  Update [`src/toybox/activities/generator.py`](../../src/toybox/activities/generator.py) activity-creation path:
  1. Resolve all slot fills as today (deterministic from seed via `slots.SlotRegistry`).
  2. Persist the resolved slot map as JSON to `activities.slot_fills_json`.
  3. Insert ONLY `steps[0]` into `activity_steps` (down from 5): render the body text using slot fills (existing logic), persist the step's `id` if present (otherwise NULL), persist `action_slot`, set `current=1`, `seq=1`. **If `steps[0]` has `choices`** — render each `choices[i].label` using the same slot fills, write the JSON-encoded list to `choices_json`. Subsequent steps will be inserted lazily by G3's advance handler.
  
  The signature computation in [`src/toybox/activities/feedback.py`](../../src/toybox/activities/feedback.py) is unchanged — still hashes `{template_id}:{slot_fingerprint}` from the resolved slot fills, NOT from the path the kid takes. Verify it still computes deterministically using the persisted `slot_fills_json` shape.
  
  **Grep audit before changing the lazy-insertion path** — pre-listed targets to verify nothing assumes monotonic 5-row pre-seeding: search `src/toybox/api/`, `src/toybox/activities/`, `src/toybox/ws/`, `src/toybox/storage/`, `frontend/src/` for: `MAX(seq)`, `seq=`, `seq DESC`, `step_count`, `len(steps)`, `steps.length`, `5` (manual review of literal-5 hits in activity-step contexts). Audit findings recorded in the G2 commit message; any consumer that breaks under lazy insertion gets fixed in G2 or a tracked follow-up issue if out of scope.
  
  Update [`tests/unit/activities/test_generator.py`](../../tests/unit/activities/test_generator.py) to assert: (a) after creating an activity from a 5-step linear template, `activity_steps` has exactly 1 row (down from 5); (b) `activities.slot_fills_json` is populated with the resolved map; (c) anti-signal signature still computes deterministically and matches the pre-G2 value for the same template+seed; (d) the inserted step is `steps[0]` with `current=1`, `seq=1`; (e) for a branching template fixture where `steps[0]` has `choices`, `choices_json` is populated with rendered labels (no `{toy}` placeholders remaining). Add a regression test that an activity created BEFORE this migration (simulated via fixture INSERTs at the old shape — pre-seeded 5 rows + empty `slot_fills_json`) still loads and advances correctly — Phase G must not break in-flight activities at upgrade time. Document the lazy-insertion shift + slot-fill persistence one-paragraph in [activity-loop.md](activity-loop.md); add `chosen_label` and `choices_json` to the [data-model.md](data-model.md) `activity_steps` table; add `slot_fills_json` to the `activities` table.
- **Type:** code
- **Issue:** #72
- **Flags:** `--reviewers code`
- **Status:** DONE (2026-05-09)
- **Depends on:** Step G1 / #71 (Pydantic shape required for the new step fields when persisting)
- **Parallel-safe with:** Step G4 / #74 (frontend; disjoint files), Step G5 / #75 (templates; disjoint files). G3 sequences after G2.
- **Done when:** Migrations 0007 + 0008 land and run cleanly forward; generator persists `slot_fills_json` at activity creation; generator inserts only `steps[0]` at creation; existing 5-step linear templates produce a 1-row `activity_steps` insert + populated `slot_fills_json`; branching templates with `choices` on `steps[0]` produce `choices_json` with no unresolved `{slot}` placeholders; old activities (pre-G2 fixture rows with empty `slot_fills_json`) still advance correctly; anti-signal signature stable across the change; grep audit complete and recorded in commit message; data-model.md + activity-loop.md updated; unit tests + regression test green; ruff + mypy strict clean.

#### Step G3: API — `/advance` with `choice_index`

- **Problem:** Extend [`src/toybox/api/activities.py`](../../src/toybox/api/activities.py) `POST /api/activities/{id}/advance`:
  
  **Request shape:** body accepts optional `choice_index: int` (new Pydantic `AdvanceRequest` model). `If-Match-Version` header mandatory (existing invariant 3) — on mismatch, return 409 with the current version body and **do NOT INSERT** a new step row (idempotency under retry).
  
  **Edge resolution** — resolve the next step server-side per the four rules in §"Vocabulary and conventions":
  1. Current step has `choices` → `choice_index` required → look up the choice's `next` step id in the template → load `activities.slot_fills_json` → render the new step's `body` and (if it has `choices`) its `choices_json` using the persisted slot fills → INSERT the new step into `activity_steps` at `seq = current_seq + 1` with `current=1`, persisting rendered `body`, `action_slot`, `id` (if present in template), and `choices_json` (if applicable). Mark previous step `current=0` and write `chosen_label = <the rendered label of the chosen entry>` (read from the previous step's persisted `choices_json` so the label exactly matches what the kid saw).
  2. Current step has `next` → ignore `choice_index` (or 400 `choice_not_allowed` if provided); resolve target id → INSERT next step (same render-from-slot-fills flow as rule 1).
  3. No `choices`, no `next`, not last in template array → fall through to next array position → INSERT next step (same render flow).
  4. No `choices`, no `next`, last in template array → terminal; transition activity to completed (existing state-machine logic); no INSERT.
  
  **Label render timing:** choice labels are rendered with slot fills at the time the choice-bearing step is INSERTED into `activity_steps` (not at template-load time, not at kiosk-fetch time). Once written, `choices_json` is the source of truth for what the kid sees and what `chosen_label` records.
  
  **Error cases (all 400 with `code=...` in the response body):**
  - `choice_required` — current step has `choices` but request omitted `choice_index`
  - `choice_not_allowed` — request provided `choice_index` but current step has no `choices`
  - `invalid_choice_index` — `choice_index` out of range for `choices` length
  
  **Response shape change:** activity-step payloads (in `/api/activities/{id}` response and the WS `activity.state` topic) extend with `choices: [{label: str, choice_index: int}] | null`. The serializer parses `activity_steps.choices_json` (a JSON array of strings) into the object form by enumerating array indices. Frontend codegen propagates the new field via pydantic-to-typescript; pre-commit hook catches drift (invariant 9). Existing consumers of activity-step payloads that don't read `choices` are backwards-compatible — the new field is additive and nullable.
  
  **Tests** at [`tests/unit/api/test_activities_advance.py`](../../tests/unit/api/) (verify path; create if missing) — cover all four advance branches + all three error cases + version-conflict regression. **Idempotency test:** simulate a retried POST with stale `If-Match-Version` after a successful advance; assert the response is 409 AND `activity_steps` row count is unchanged (no double-insert). New integration test [`tests/integration/test_branching_e2e.py`](../../tests/integration/) — load a branching fixture, create the activity, advance through path A in one test, advance through path B in a second test (separate activities), assert: (a) `activity_steps` rows match the chosen path in `seq` order; (b) `chosen_label` set on rows where the kid had a choice and matches the rendered label they saw; (c) the same anti-signal signature is computed for both paths (path-agnostic by design); (d) the WS payload for the choice-bearing step includes `choices: [{label, choice_index}]` with rendered labels.
- **Type:** code
- **Issue:** #73
- **Flags:** `--reviewers code`
- **Status:** DONE (2026-05-10)
- **Depends on:** Step G2 / #72 (lazy-insertion contract + `chosen_label` + `choices_json` + `slot_fills_json` columns required)
- **Parallel-safe with:** Step G4 / #74 — frontend reads this plan-doc for the `AdvanceRequest` request shape AND the activity-step response shape (`choices: [{label, choice_index}] | null`); pydantic-to-ts codegen formalizes both. G5 / #75 (templates) is parallel-safe with G3 — disjoint file sets
- **Done when:** `/advance` accepts optional `choice_index`; all four edge rules implemented and unit-tested; all three 400 error codes returned correctly; `chosen_label` recorded on previous step's row when a choice was made and matches the rendered label the kid saw; activity-step response/WS payload extended with `choices: [{label, choice_index}] | null`; idempotency test passes (stale `If-Match-Version` retry → 409 + no row inserted); integration test covers two paths through one branching template; pydantic-to-ts codegen reflects both the new request body shape and the new response field; ruff + mypy strict clean.

#### Step G4: Frontend — ChoiceButton + StepCard branching + progress indicator

- **Problem:** Add new component [`frontend/src/child/components/ChoiceButton.tsx`](../../frontend/src/child/components/ChoiceButton.tsx) — receives `(label: string, choiceIndex: number, onAdvance: (choiceIndex: number) => void, disabled: boolean)`. Posts `{choice_index}` to `/api/activities/{id}/advance` via the existing client (look at how `NextStepButton.tsx` does it; reuse its request helper). Disabled state during in-flight POST prevents double-tap. Touch target ≥44pt per Apple HIG (existing kiosk on iPad). **Error handling:** on a 4xx response other than 409, the button re-enables and a small inline error indicator surfaces (sufficient for autonomous-build review — operator can confirm UX in G6). On a 409 (`If-Match-Version` mismatch), the kiosk refetches activity state via the existing fetch pattern and re-renders, since another tab/window may have advanced or dismissed the activity. On 5xx, button re-enables; existing global error toast (if any — verify) surfaces, otherwise inline indicator.
  
  Update [`frontend/src/child/components/StepCard.tsx`](../../frontend/src/child/components/StepCard.tsx) — when `step.choices` is non-null and non-empty, render a vertical stack of N `<ChoiceButton>`s in place of `<NextStepButton>`; otherwise render `<NextStepButton>` as today. The `choices` field arrives on the step payload from G3's response/WS shape (`choices: [{label, choice_index}] | null`); the kiosk does NOT need to know about templates or slot fills.
  
  Verify whether a "step N of 5" string exists anywhere in the frontend (grep for `of 5`, `of {`, `step.*\d.*of`, `\\\$.*length`, `steps\\.length`); if found in a component, drop the denominator (show "step N" or nothing); if only in a comment / fixture / test snapshot, leave alone. Record the grep result in the G4 commit message.
  
  Add component-level tests for `ChoiceButton` (disabled while in-flight; calls onAdvance with the correct index; renders the label; re-enables on 4xx; refetches on 409) and `StepCard` (renders choice path vs next path based on `step.choices` presence/null). UI evidence is intentionally bundled to G6 per `feedback_autonomous_build_bundled_ui.md` — DO NOT add `--ui` to this step. ruff/mypy do not run on TS — Vitest + `tsc --noEmit` do; both must be clean.
- **Type:** code
- **Issue:** #74
- **Flags:** `--reviewers code` (UI evidence intentionally bundled to G6 per [`feedback_autonomous_build_bundled_ui.md`](../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md))
- **Status:** DONE (2026-05-09)
- **Depends on:** Step G1 / #71 (TypeScript types for `Step.choices` + `Choice` come from pydantic-to-ts codegen on G1's models). Soft coupling with Step G3 / #73 — frontend reads this plan-doc for the `AdvanceRequest` request shape AND the activity-step response shape (`choices: [{label, choice_index}] | null`); both are codified by pydantic-to-ts.
- **Parallel-safe with:** Step G2 / #72 + Step G3 / #73 (disjoint file sets — backend owns `src/`, frontend owns `frontend/src/`; API contract is pinned in this plan-doc and codified by codegen). Step G5 / #75 (templates) is also parallel-safe — pure JSON, no code overlap.
- **Done when:** `ChoiceButton.tsx` ships with component-level tests covering all four states (idle, in-flight disabled, 4xx re-enable, 409 refetch); `StepCard.tsx` renders both branches per `step.choices` presence; "step N of 5" denominator dropped (or confirmed absent in components, with grep result recorded in commit); pydantic-to-ts codegen clean; tsc + Vitest green; pre-commit hook clean (no codegen drift).

#### Step G5: Operator — author 4 branching templates

- **Problem:** Author 4 branching templates, one per intent, under `src/toybox/activities/templates/branching/<intent>.json` (or wherever G1 finalized the recursive-load path). Each template:
  - Has 2-4 choice points
  - Has at least 2 distinct endings (different `next` chains converge to different terminal nodes)
  - Total node count between 5 and 12
  - Reuses existing slot vocabulary (`{toy}`, `{adjective}`, `{action_verb}`, `{prop}`, `{room}`, `{count}`, `{body_part}`) — no new slots needed for this step (a future phase can add slots)
  - Uses the same `action_slot` vocabulary (10 fixed values from `ACTION_SLOTS`) for sprite rendering — no new sprite work
  - Declares appropriate `buckets` (`morning`, `afternoon`, `evening`, `wind_down`, `always`) like existing templates
  - Validates clean against G1's `validate_template_graph` at app boot
  
  Suggested template ideas (operator picks final shapes):
  - **`boredom`** — "Mystery in {room}": kid hears a sound, picks investigate-quietly vs. announce-yourself, dragon vs. mouse encounter, fight vs. feed. ~8 nodes, 4 endings.
  - **`request_play`** — "Adventure with {toy}": kid picks fantasy-realm (forest vs. spaceship), then picks tool (sword vs. wand), then a small final twist. ~10 nodes, 4 endings.
  - **`request_story`** — "Story-swap night": kid hears the start of a story, picks a turning point twice, ends differently each time. ~7 nodes, 4 endings.
  - **`request_activity`** — "Mission of the day": kid picks short-mission vs. long-mission at the start (this template specifically demonstrates the variable-step-count primitive — short branch is 4 steps, long branch is 9). ~9 nodes, 2 main paths.
  
  For each template, write a one-line design note in the G5 run-doc capturing intent + paths. The templates are committable JSON — no special license posture required (they're plain text content authored by the operator). Operator-runnable smoke check: `uv run python -m toybox.activities.lint_templates` (verify the entry point exists; if not, `uv run python -c "from toybox.activities.generator import load_templates; load_templates(); print('all templates ok')"` works as a one-liner). Must print success.
- **Type:** operator
- **Issue:** #75
- **Flags:** n/a (operator step; manual JSON authoring)
- **Status:** DONE (2026-05-10) — exceeded scope: 200 templates shipped instead of 4 (50 per intent) via overnight 4-parallel-agent soak; 0% validation failures; full report at `documentation/runs/2026-05-10-template-soak.md`
- **Depends on:** Step G1 / #71 (schema + validator must be live so authoring iteration is fast feedback)
- **Parallel-safe with:** Step G2 / #72, G3 / #73, G4 / #74 — pure JSON content, zero code overlap; runs on the third parallel branch after G1 (alongside backend G2→G3 and frontend G4)
- **Done when:** 4 templates committed under `src/toybox/activities/templates/branching/`; each validates against `validate_template_graph` at boot; each has 2-4 choice points and 2+ endings; one-line design note per template in the G5 run-doc.

#### Step G6: UAT smoke gate — iPad kiosk, 5 activities

- **Problem:** Operator-driven smoke gate on the real iPad kiosk. Steps:
  1. Pull latest, run migrations forward (`uv run python -m toybox.db.migrate`), confirm migrations 0007 + 0008 applied: `activity_steps` has `chosen_label` + `choices_json` columns AND `activities` has `slot_fills_json`. Quick check: `sqlite3 data/toybox.db ".schema activity_steps"` and `.schema activities`.
  2. Boot the backend (existing run script) + Vite dev server. Confirm app loads on iPad PWA without errors.
  3. **Linear regression activity** — trigger an existing 5-step template (e.g. `boredom_anytime_silly`). Confirm it runs end-to-end with the existing `NextStepButton`, no choice buttons appear, activity completes at step 5. Screenshot 1 step.
  4. **Branching activity per intent** (4 total — one per `boredom`, `request_play`, `request_story`, `request_activity`). For each:
     - Trigger via the parent UI manual-trigger button.
     - Tap through one path; capture a screenshot at each choice point showing the buttons.
     - Confirm the activity advances correctly + the chosen path matches what the kid tapped.
     - Verify in DB: `activity_steps` rows are in `seq` order; `chosen_label` populated on rows where a choice was made AND matches the rendered button text the kid saw on the screenshot; `choices_json` populated on choice-bearing steps with no unresolved `{slot}` placeholders; `current` flag tracks correctly; activity completes at the expected terminal.
     - **Record the chosen path** in the run-doc as the ordered list of node `id`s visited (e.g. `[open, sneak, encounter, snack, victory]`) — useful cross-check against the template's declared graph and feeds the post-G6 metrics watch list.
  5. **Regression sanity:** trigger one MORE existing linear template; confirm it still works post-migration.
  6. Capture browser console logs (no errors) + WS logs from the backend (no malformed messages). The pause/regenerate-from-here flow is out of scope this phase — confirm only that the existing pause/end controls still work for both linear and branching activities.
  7. Write run-doc at `documentation/runs/<date>-branching-gameplay-uat.md` with: linear regression result, 4 branching template results (path taken, choices made, chosen_label values from DB, screenshots), regression-sanity result, console + WS log status. Pass/fail per template + overall.
  
  Pass criteria: 5/5 activities complete end-to-end without errors; `chosen_label` populated correctly on every choice tap; no console / WS errors. Soft-pass acceptable: if 4/5 activities complete cleanly and the failure is a content issue (template authoring nit) rather than a runtime issue, fix the template, re-run that template only, mark soft-pass.
  
  **Closes Phase G** when soft-pass or pass.
- **Type:** operator
- **Issue:** #76
- **Flags:** n/a (operator-driven UAT; manual iPad interaction)
- **Status:** DONE (2026-05-10) — operator-confirmed PASS on all 6 punch-list rows; run-doc at `documentation/runs/2026-05-10-phase-g-uat.md`; bonus fix landed during UAT (WS-origin TOYBOX_LAN_IP env-var visibility in README + ipad-setup doc + diagnostic log)
- **Depends on:** Steps G1-G5 / #71-#75 (everything must be live)
- **Parallel-safe with:** none — strictly last
- **Done when:** UAT run-doc lands at `documentation/runs/<date>-branching-gameplay-uat.md` with the 5-activity evidence; 5/5 pass or 4/5 + content-only soft-pass; Phase G closed with the run-doc commit message referencing this phase plan.

## Open risks

- **Existing `activity_steps.seq` indexing assumes monotonic insertion at activity creation** — if any code path queries `MAX(seq)` and expects the full step count up front (e.g. for progress UI), that assumption breaks under lazy insertion. G2 includes an explicit grep audit (targets pre-listed in the step body); any consumer that breaks under lazy insertion gets fixed in G2 or tracked as a follow-up. Most likely safe (the parent dashboard reads activity state, not unfilled-step state) but verified by the audit, not assumed.
- **`pydantic-to-typescript` codegen for optional `Choice[]` field** — confirm the generated TS shape is `Choice[] | null` not `Choice[] | undefined`; the kiosk's `step.choices` check needs to be either nullish-aware (`step.choices?.length`) or normalized. G1 verifies the generated shape and G4 uses the right idiom.
- **Phase E inheritance is non-trivial** — Phase E5 originally bundled migration + Pydantic loosening + dynamic-step UI. Phase G ships those primitives. When Phase E starts, that scope must be subtracted from E5 and the remaining (local-model authoring, transcript-reaction window, pause/regenerate-from-here, `is_complete: bool`) re-scoped. G2 includes a one-liner edit to phase-e.md; E author should verify before starting E.
- **Anti-signal blindness to path** — parent feedback "this didn't work" on a branching template applies to ALL paths. If the dragon-fight ending is great but the dragon-snack ending is flat, the parent's "didnt_work" on the snack ending vetoes BOTH paths next time. Acceptable for v1 of branching; flagged as the most likely "feel" complaint that would justify a path-aware-feedback follow-on phase.

## Metrics to watch (post-G6)

After G6 lands, watch over the next 1-2 weeks of normal use:

- **Replay rate per branching template** (how often is the same template_id triggered twice in a session) — proxy for "kid wants to see other endings"
- **Choice distribution per choice point** — are kids picking 50/50, or is one branch overwhelmingly preferred? Skewed branches indicate authoring imbalance and inform G5+ template iteration
- **Average activity length** in steps — should drift up from ~5 (linear-only) toward ~6-7 once branching templates mix in
- **Anti-signal hit rate on branching templates** vs. linear — if branching templates start getting `didnt_work`-flagged disproportionately, the path-blindness risk above is biting and we revisit

These can ship as a one-shot eval script in a follow-on phase; not blocking for G6.
