# Phase O — Parent UX refresh (feature plan, seed)

## 1. What this feature does

Phase O is a parent-side UX refresh focused on **proposal-list categorization**. Surfaced by operator during Phase M UAT pause on 2026-05-18:

> *"Can we also make 4 tabs 'All', 'Adventures', 'Elements', and 'Transcriptions'. To allow for games to show in a combined list or show separately. We may refine this UI later but for now separate tabs work."*

The parent app currently has a single proposal list under the **Play → play-ideas** sub-tab (Phase H), plus a **Play → transcription** sub-tab for the passive-listening log. As template variety grew through Phases K (1000 templates) and M (1243 templates, including 118 element activities), the single proposal list became hard to scan for a specific category.

Phase O introduces a **5-way categorical sub-tab** structure under **Play**:

```
Play
├── All                — combined chronological proposal list (current behavior)
├── Adventures         — non-element, non-SEL activities (Phase G/K/L branching templates)
├── Elements           — element activities (Phase M M4 + Phase N element_microgames)
├── Feelings & Friends — SEL activities (Phase M M9-M12: feelings-naming, perspective-taking,
│                                         conflict-resolution, friendship-repair). All carry
│                                         `theme: feelings` (Phase M8 enum value).
└── Transcriptions     — passive listening transcript log (current behavior, renamed)
```

**Track 2 / SEL gets its own tab** by the same logic that drove the Elements split: shared categorical attribute (`theme: feelings`), parent-discoverable as a coherent set, and Child A-targeted in the same way Elements is Child B-targeted. Operator-confirmed naming via Phase M UAT pause 2026-05-18 after row #5 PASS: "**Feelings & Friends**" beats "Emotions" / "Big Feelings" / "Friends" by covering both feelings-naming (M9) and the three friend-coded sub-domains (M10 perspective / M11 conflict / M12 repair).

**Out of scope explicitly:**
- Deeper redesign per operator: *"We may refine this UI later but for now separate tabs work."* This is the first-pass split, not a UX overhaul.
- New activity categories beyond the four content categories (Adventures / Elements / Feelings & Friends / Transcriptions) listed above. Adding more (e.g. "Songs", "Jokes") is a Phase P candidate if needed.
- Localization / non-English tab labels. Toybox is en-only (per persona schemas + operator preferences); revisit when l10n becomes a project goal.
- Major backend logic changes. Phase O does add **two typed fields** to the pydantic `Activity` (per plan-review Blocker 1): `template_id: str | None` and `recommended_themes: list[str]` — a minimal wire-shape widening to surface attribution that's already persisted (template_id) or derivable (theme) backend-side. Both are typed, both regenerate `types.ts` via the Phase L pydantic→TS hook.

## 2. Existing context

### Fresh-reader pointers

For first-run setup + backend/frontend run commands: see [`toybox/CLAUDE.md`](../CLAUDE.md) and [`frontend-ui.md`](../.claude/rules/frontend-ui.md) (frontend on `:4000`, NOT `:3000`).
Phase context: [`phase-h-plan.md`](phase-h-plan.md) established the tab substrate ([App.tsx:65-66](frontend/src/parent/App.tsx#L65)); [`phase-m-plan.md`](phase-m-plan.md) shipped the Element track (M1-M7) + SEL track (M8-M12); [`phase-n-plan.md`](phase-n-plan.md) ships the element_microgame template shape whose `template_type` Phase O consumes (sequencing detail below).

### Acronyms and terms used in this plan

| Term | Meaning |
|---|---|
| **Phase G / K / L** | Branching engine (G), 1000-template expansion (K), rewards system (L). |
| **Phase H** | Top-tab + sub-tab system in `App.tsx`; introduced the `useTabState` + `localStorage` persistence pattern Phase O extends. |
| **Phase M** | Periodic Table Professor (M1-M7) + SEL (M8-M12) content tracks. Source of the `element_id` field (M3) and `theme: feelings` enum value (M8). |
| **Phase N** | Element microgame template shape; ships `template_type: "element_microgame"` and `categorize()`-relevant wire fields. Phase O O2 depends on N2's codegen baseline. |
| **SEL** | Social-Emotional Learning. The Phase M Track 2 content category (feelings + perspective + conflict + repair) that surfaces under the "Feelings & Friends" tab. |
| **UAT** | User Acceptance Test (operator + kid on iPad). |
| **TDD** | Test-Driven Development. The `--tdd` flag selects `/build-step-tdd` (tests-first, red-green-refactor). Fit for pure helpers + filter logic where the contract is the spec. |
| `--reviewers code` | `/build-step` flag for the 4-parallel-reviewer mode (no runtime/UI-evidence reviewers). All Phase O code steps use this because toybox's PIN-gated parent UI blocks runtime reviewers (see memory `feedback_buildstep_pin_gate_blocks_ui_evidence`). |
| **`/build-step` / `/build-phase`** | Workspace skills. `/build-phase` walks the steps in this plan and dispatches each to `/build-step` (or `/build-step-tdd` when `--tdd` is set). Operator-type steps halt orchestration. SKILL.md files at `dev/.claude/skills/`. |
| **`useTabState`** | Custom hook in [`components/Tabs.tsx`](../frontend/src/parent/components/Tabs.tsx). Signature: `useTabState<K extends string>(key: string, defaultValue: K, validValues: readonly K[]): { value: K, setValue: (k: K) => void }`. Persists selected key in `localStorage` under `key`, reads lazily at mount, rewrites only on explicit `setValue` (no eager migration — invalid stored values fall back to defaultValue without overwriting). Phase H groundwork. |
| **`<Tabs>` / `<SubTabs>`** | Controlled tab-renderer components in `Tabs.tsx`. `TabsProps<K>` shape: `{ items: readonly { key: K, label: string }[], value: K, onChange: (key: K) => void }`. Consumer owns the panel render outside the component. |
| **`PlayQueueList`** | Phase J step J8 component at [`components/PlayQueueList.tsx`](../frontend/src/parent/components/PlayQueueList.tsx). Renders the pinned active card (as `ActivityPanel`) + each proposed activity as a `SuggestionCard`. Owns TTL-fade machinery + per-action busy flags. Phase O extends with an optional `filterCategory` prop. |
| **`TranscriptsManager`** | Existing component at [`components/TranscriptsManager.tsx`](../frontend/src/parent/components/TranscriptsManager.tsx). Renders the passive-listening transcript log. The Transcriptions tab continues to render this unchanged. |
| **pydantic→TS codegen** | `uv run python tools/gen_types_ts.py` — deterministic; pre-commit hook gates drift via `git diff --exit-code`. Walks pydantic models + StrEnums in `src/toybox/` and emits `frontend/src/shared/types.ts`. Per the script's own docs, the project does NOT use `pydantic2ts` (requires Node `json2ts` not on the dev machine); instead a hand-rolled deterministic emitter ships in `tools/gen_types_ts.py`. |
| **`template_id` format** | Slug pattern `^[a-z0-9][a-z0-9_]*$` per [`_schema.json:28`](../src/toybox/activities/templates/_schema.json#L28). Examples: `meet_element_au-79`, `feelings_lost_blanket`, `noble_gas_party_floaters`. |

### Phase H tab substrate (already shipped)

Phase H established the top-tab + sub-tab system in `frontend/src/parent/App.tsx`:

- Top tabs: `play` | `kids-toyboxes` | `settings` ([App.tsx:65](frontend/src/parent/App.tsx#L65))
- Play sub-tabs: `play-ideas` | `transcription` ([App.tsx:66](frontend/src/parent/App.tsx#L66))
- Tab selection persists in `localStorage` under `toybox.parent.tabs.*` keys

Phase O widens `PlaySubTab` from 2 values to **5**: splits `play-ideas` into the 3 categorical filters (All / Adventures / Elements / Feelings & Friends) + renames `transcription` to `transcriptions` (label "Transcriptions") for plural-consistency. The All tab preserves current `play-ideas` rendering behavior.

### Activity wire shape (current state + Phase O additions)

Plan-review verified that the typed `Activity` interface ([api.ts:40-53](frontend/src/parent/api.ts#L40)) currently exposes:

```ts
interface Activity {
  id: string;
  state: ActivityState;
  // ... other typed fields ...
  metadata: Record<string, unknown>;   // untyped catch-all
  steps: ActivityStep[];                // each step: { seq, body, sfx, expected_action, current } — NO element_id
}
```

Backend persists `template_id` inside `metadata` ([activities.py:2306](src/toybox/api/activities.py#L2306)) but it's not surfaced as a typed field. `element_id` is per-step on the template definition; resolved server-side by `_resolve_element_id_for_persisted_step` ([activities.py:822-828](src/toybox/api/activities.py#L815)) but not currently emitted on `ActivityStep`. `theme` is a template attribute, not on the activity envelope at all.

**Phase O adds two typed wire-shape fields** to Activity (chose option (b) from plan-review):

```ts
interface Activity {
  // ... existing fields ...
  template_id: string | null;          // NEW: surface for categorize() — server already persists in metadata
  recommended_themes: string[];        // NEW: from template's recommended_themes field; empty array if none
}
```

Plus one typed field on `ActivityStep`:

```ts
interface ActivityStep {
  // ... existing fields ...
  element_id: string | null;           // NEW: from step's element_id (M3 schema add); null on non-element steps
}
```

Three typed-field additions; one pydantic regen via `uv run python tools/gen_types_ts.py` (deterministic emitter — see acronyms table above for the pydantic2ts rationale). **No new HTTP/WS routes.** No new persistence schema.

### Touched API surfaces (existing — Phase O widens serialization, doesn't add routes)

- **`GET /api/activities/{id}`** → returns the full `Activity` envelope. Phase O adds `template_id`, `recommended_themes` to the response body and `element_id` to each step. Existing consumers ignore unknown fields, so this is additive.
- **`GET /api/activities` (list)** → same envelope, applied per-activity.
- **WS topic `activity.proposed`** → carries `{ type: "proposed", activity: Activity }`. The Activity payload widens by the same three fields.
- **WS topic `activity.state`** → state-change envelope carrying Activity; same fields ride along.

(Route + topic details live in [`documentation/plan/api.md`](plan/api.md); Phase O reuses them as-is.)

### Categorize() logic (where it lives + what it reads)

The categorize helper lives in a **new file** `frontend/src/parent/components/categorize.ts` (sibling to `PlayQueueList.tsx`, not folded in to keep PlayQueueList focused on TTL/render machinery). `PlayQueueList` accepts an optional `filterCategory` prop ("adventures" | "elements" | "feelings-friends" | undefined) and consults categorize() per-activity.

Categorize rules — read the new typed fields:

- **Elements:** any step with non-null `step.element_id` → category = Elements (Phase M M4 + Phase N templates both set this).
- **Feelings & Friends:** `activity.recommended_themes.includes("feelings")` → category = Feelings & Friends (Phase M8-M12 templates carry `theme: "feelings"`).
- **Adventures:** everything else (no element_id step, no feelings theme).

**Precedence:** Elements > Feelings & Friends > Adventures. Hypothetically-mixed activities (element + feelings, which Phase M's track design disjoints) resolve to Elements. Pin in categorize() with a regression test.

## 3. Scope summary

- **Wire-shape widening** (small): add `template_id: str | None` + `recommended_themes: list[str]` to pydantic `Activity`; add `element_id: str | None` to pydantic `ActivityStep`. Backend already persists template_id; recommended_themes + step element_id are surfaced from template + step definitions. Regenerate `frontend/src/shared/types.ts` via `uv run python tools/gen_types_ts.py`.
- **`PlaySubTab` enum widening** in [`App.tsx:66`](frontend/src/parent/App.tsx#L66): from 2 values to **5** — `"play-ideas"` splits into `"all" | "adventures" | "elements" | "feelings-friends"`; `"transcription"` renames to `"transcriptions"`. Update `PLAY_SUBTAB_VALUES` ([App.tsx:71](frontend/src/parent/App.tsx#L71)) to match. Migration: existing `localStorage` values mapping `"play-ideas"` → `"all"`, `"transcription"` → `"transcriptions"`; unknowns → `"all"`.
- **One new sibling helper file** `frontend/src/parent/components/categorize.ts` exporting `categorize(activity: Activity) -> "adventures" | "elements" | "feelings-friends"` (see §2 for rules + precedence). Pure function; no React.
- **`PlayQueueList` prop addition**: optional `filterCategory?: "adventures" | "elements" | "feelings-friends"`. When set, PlayQueueList filters `proposedList` via `categorize()` before rendering. When undefined, current behavior.
- **App.tsx tab routing**: each of the 4 content sub-tabs renders `<PlayQueueList ... filterCategory={tabKey} />` (or undefined for "all"); "transcriptions" renders the existing TranscriptsManager unchanged.
- **Tab labels** updated in `<Tabs>` props: "All", "Adventures", "Elements", "Feelings & Friends", "Transcriptions" (in that order — see §7 rationale).
- **Empty-state copy** per tab — pinned strings:
  - All: *"No play ideas yet. Approve one when a suggestion appears."*
  - Adventures: *"No adventures suggested yet."*
  - Elements: *"No element activities suggested yet."*
  - Feelings & Friends: *"No feelings & friends activities suggested yet."*
  - Transcriptions: existing copy (unchanged).
- **Test impact** (likely-affected, must update): `frontend/src/parent/App.test.tsx`, `App.bootstrap.test.tsx`, `components/Tabs.test.tsx`, `components/PlayQueueList.test.tsx`. Existing parent vitest suite must still pass after migrations.
- **Vitest coverage** (new): `categorize.test.ts` (4+ sample activities incl. precedence + edge cases); `App.test.tsx` migration coverage (old localStorage values → new keys); empty-state render coverage per tab.

No new React components beyond `categorize.ts` (a pure helper, not a component). No new ws topics. No new HTTP routes. **Two pydantic field additions** that flow through existing codegen.

## 4. Out of scope

- **Activity types beyond elements/adventures/transcriptions.** Songs + jokes are reward types, not standalone activities (Phase L design). They surface as part of an Adventure's reward step, not as their own tab. If kids' usage shows songs/jokes need their own tab, Phase P.
- **Category badges on the All-tab proposal cards** (e.g. green dot for Adventure, atom icon for Element). Nice-to-have polish; defer.
- **Per-tab kid-targeted filtering** (e.g. "Elements" tab pre-filters to Child B's proposals). Defer; operator can already filter by `child_id` via existing controls.
- **Mobile / iPad-specific tab layout.** Phase H's tab system is already iPad-compatible per Phase G/K UAT. No new mobile work.

## 5. Build steps

### Step O1: `PlaySubTab` widening + localStorage migration
- **Problem:** Extend `PlaySubTab` in [`App.tsx:66`](frontend/src/parent/App.tsx#L66) from 2 values to **5**: `"all" | "adventures" | "elements" | "feelings-friends" | "transcriptions"`. Update `PLAY_SUBTAB_VALUES` array ([App.tsx:71](frontend/src/parent/App.tsx#L71)) to match. Add localStorage-key migration: on app boot, if `toybox.parent.tabs.play` is `"play-ideas"`, rewrite to `"all"`; if `"transcription"`, rewrite to `"transcriptions"`; unknown values default to `"all"`. Update the `<Tabs>` props with the 5 labels in order: "All", "Adventures", "Elements", "Feelings & Friends", "Transcriptions". Wire empty-state copy per §3. Wire the 4 content sub-tabs to render `<PlayQueueList filterCategory={tabKey} />` (or undefined for "all"); transcriptions tab continues to render TranscriptsManager unchanged. The `filterCategory` prop is no-op until O2 ships — O1 plumbs the wire but doesn't filter.
- **Type:** code
- **Issue:** #178 (umbrella #177)
- **Flags:** `--reviewers code` + `--tdd`
- **Produces:** `App.tsx` patch + `PlayQueueList.tsx` prop addition (filterCategory, no-op until O2) + vitest coverage in `App.test.tsx` + `App.bootstrap.test.tsx` + `PlayQueueList.test.tsx`.
- **Done when:** App boots cleanly with old localStorage values (verified via test fixture); new 5-tab structure renders; all 5 tab clicks change state + persist; `filterCategory` prop accepted but ignored at runtime; existing parent vitest suite passes after test migrations.
- **Depends on:** none (parallel to Phase N N0+N0b; categorize logic doesn't ship until O2).
- **Status:** DONE (2026-05-19) — 20 TDD tests green (15 `App.tab-migration.test.tsx` + 5 `PlayQueueList.test.tsx` filterCategory describe). Migration runs synchronously before `useTabState` to beat its lazy initializer; idempotent. `filterCategory` prop plumbed type-side; runtime filter is no-op (O2 activates). 626/626 vitest, tsc + lint clean.

### Step O2: Wire-shape additions + `categorize.ts` helper + filter activation
- **Problem:** Add `template_id: str | None` + `recommended_themes: list[str]` to pydantic `Activity`; add `element_id: str | None` to pydantic `ActivityStep`. Backend populates from existing template/metadata sources (template_id is already in metadata per [activities.py:2306](src/toybox/api/activities.py#L2306); element_id and recommended_themes are derivable from the template definition). Regenerate `frontend/src/shared/types.ts` via `uv run python tools/gen_types_ts.py`. Create `frontend/src/parent/components/categorize.ts` exporting `categorize(activity: Activity) -> "adventures" | "elements" | "feelings-friends"` with precedence Elements > Feelings & Friends > Adventures (rules + reasoning per §2). Activate `PlayQueueList`'s `filterCategory` prop: filter `proposedList` via `categorize()` before rendering when prop is set; pass-through when undefined.
- **Type:** code
- **Issue:** #179 (umbrella #177)
- **Flags:** `--reviewers code` + `--tdd`
- **Produces:** pydantic field additions (Python) + regenerated `types.ts` + `categorize.ts` helper + `categorize.test.ts` + `PlayQueueList.tsx` filter wiring + tests covering the filter path.
- **Done when:** filter returns correct category for 4+ sample activities (one Phase L Adventure, one Phase M element, one Phase M SEL, one hypothetical-mixed); precedence test pins Elements > Feelings & Friends > Adventures; empty-state shows for empty filter result; pydantic→TS codegen runs clean (no diff after regen); existing pytest + vitest suites pass; backend serializes the new fields on `/api/activities/{id}` and on the propose ws envelope.
- **Depends on:** O1 (plumbing) + **Phase N N2** (Phase N N2 also runs the codegen hook; Phase O O2 lands after to avoid stomping or racing the regen). If Phase N hasn't started, O2 can ship first — sequencing is mutual ("whichever ships first owns the codegen baseline"), but if both are in-flight simultaneously, coordinate.
- **Status:** DONE (2026-05-19) — 22 new tests green (13 pytest + 9 vitest categorize/filter). Wire fields land on `ActivityResponse` via `_row_to_response` chokepoint (single seam for GET + propose + WS envelope). `gen_types_ts.py` extended to hand-emit `interface Activity` and `interface ActivityStep` into `shared/types.ts`; idempotence test still green. `categorize.ts` imports `Activity` from `../api` (hand-rolled parent surface) to match what `PlayQueueList`'s `proposedList` actually carries. Filter activated upstream of the row map; rows missing both signals pass through to preserve O1 backward compat. Pre-existing flake observed in `tests/integration/test_ws_origin.py` (different parameterization each run, passes in isolation sometimes) — NOT caused by O2 (no backend WS edits); to be filed as a follow-up issue.

### Step O3: iPad UAT (operator)
- **Problem:** Operator on iPad: visit Play, switch through all 5 sub-tabs (All / Adventures / Elements / Feelings & Friends / Transcriptions), verify proposals show in correct buckets, verify empty-state text shows when bucket is empty, verify `localStorage` persists across reload (close tab, reopen, last selection sticks), verify Transcriptions tab still shows raw transcript log unchanged. Test in both kid-empty (no proposals) and kid-busy (proposals across all 3 categories) moments. Spot-check at least one of each: an Adventure proposal under Adventures, an element activity under Elements, a SEL activity under Feelings & Friends.
- **Type:** operator
- **Issue:** #180 (umbrella #177)
- **Produces:** `documentation/runs/<YYYY-MM-DD>-phase-o-uat.md` (matches Phase M / K format).
- **Done when:** all 5 tabs work; no engagement regression vs Phase M baseline; operator confirms the categorical split feels useful (not redundant — if the All tab is the only one used in practice, file as Phase P scope-down feedback but Phase O still ships).
- **Depends on:** O2.
- **Status:** DEFERRED-TO-UAT as M1 (2026-05-19). Pure-observation operator step; bundled into the phase-end Manual UAT section below per `/build-phase` skill default.

## 6. Acceptance

Phase O closes when O1 + O2 ship + O3 UAT confirms the tabs are useful. Total scope estimate: **2 code build-steps + 1 operator step**. **Estimated effort:** small — three typed-field additions via existing codegen, plus the tab-routing + categorize helper. No new HTTP/WS routes, no new persistence schema.

Step shape table for `/build-phase` dispatch:

| Step | Type | Flags | Depends on |
|---|---|---|---|
| O1 | code | `--reviewers code --tdd` | — |
| O2 | code | `--reviewers code --tdd` | O1 + Phase N N2 (codegen coordination) |
| O3 | operator | — | O2 |

## 7. Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| localStorage migration corrupts existing tab state | Operator hits app after Phase O ships, lands on Settings tab unexpectedly. | O1 migration covers `"play-ideas"` → `"all"` + `"transcription"` → `"transcriptions"` explicitly; defaults to `"all"` if unknown. Vitest coverage. |
| Element categorization edge cases | Activity with `element_id` on one step but not another — which category? | O2 categorize helper documents: any step with `element_id` → Elements bucket. Single-source-of-truth in helper, tested. Surface for ambiguity reduces to zero if Phase M M4 + Phase N templates all set `element_id` on every step (M4 + Phase N N4 generators do this by construction). |
| Categorical split feels redundant | Operator's UAT (O3) may show the All-tab is mostly used and Adventures/Elements rarely. | If true, O3 verdict can scope the tabs down — keep All + Transcriptions only, retire Adventures/Elements. Phase O ships the structure; usage informs next phase. |
| Tab label "Transcriptions" is the corrected spelling | Operator typed "Trasncriptions" in the request. Likely typo. | Use "Transcriptions" (standard English plural). Confirm with operator at O1 build-step if pushback. |
| Codegen race with Phase N N2 | Both phases regenerate `frontend/src/shared/types.ts` via the pydantic→TS hook. Concurrent in-flight builds can stomp each other's regen. | O2 explicitly depends on N2 — sequencing pins Phase N's `template_type` codegen first, Phase O's `template_id` + `recommended_themes` + `element_id` codegen second. If Phase O ships first (Phase N delayed), O2 owns the baseline + N2 rebases when it lands. CI gates against stale codegen per Phase L pattern, so divergence surfaces fast. |
| Tab order (All → Adventures → Elements → Feelings & Friends → Transcriptions) | Reordering casually after ship breaks operator muscle memory + breaks test fixtures asserting position. | Default chosen: All-first (preserves current behavior), then categorical filters in template-rollout order (Adventures = oldest, Elements = Phase M, Feelings & Friends = Phase M Track 2), Transcriptions last (distinct data source). Pin in §3 + §5 O1 — Phase P would re-order only with explicit operator UAT signal. |

## 8. Resolved decisions (formerly open questions)

Resolved during `/plan-review` on 2026-05-18 (operator delegated defaults to assistant):

1. **Default tab on boot** — `"all"` (preserves combined-list behavior under the new structure; no behavior change for inattentive operators).
2. **Sub-tab refactor, not top-tab promotion** — kept under Play sub-tabs per operator phrasing ("for now separate tabs work"). Top-tab promotion is a Phase P candidate if behavior signals it.
3. **Songs/jokes tabs deferred** — reward TYPES, not activities; not in scope for Phase O. Phase P+ if needed.
4. **Wire-shape widening strategy** — option (b): typed pydantic field additions (`template_id`, `recommended_themes`, step `element_id`) flowed through existing Phase L codegen. NOT reading from untyped `metadata`, NOT computing category server-side.
5. **Filter-helper location** — sibling `categorize.ts` file + `filterCategory` prop on PlayQueueList. NOT folded into PlayQueueList's body (keeps that component focused on TTL/render machinery).
6. **Localization** — out of scope; toybox is en-only.

## 9. Status

**2026-05-18** — seeded during Phase M UAT pause after operator requested 4-tab proposal-list refactor (became 5-tab once SEL was added). `/plan-review` + `/plan-wrap` passes complete:
- `/plan-review` edits — §1 out-of-scope + §2 fresh-reader pointers + acronyms + wire-shape audit + categorize logic location + §3 scope (wire-shape widening + sibling helper + pinned empty-state strings + test impact) + §5 O1/O2/O3 + §6 step-shape dispatch table + §7 codegen-race + tab-order risks + §8 resolved decisions.
- `/plan-wrap` edits — expanded acronyms table with `/build-step`, `/build-phase`, `useTabState`, `<Tabs>`, `PlayQueueList`, `TranscriptsManager`, `template_id` format pattern, and the actual pydantic→TS codegen command (`uv run python tools/gen_types_ts.py`); added "Touched API surfaces" subsection documenting the existing routes Phase O widens (no new routes added).

Ready for `/repo-sync` → mint umbrella + 3 step issues (O1, O2, O3). Phase O is independent of Phase N for kickoff but O2 depends on N2 for codegen baseline (sequencing pinned at §5 O2).

## Manual UAT

*Generated by /build-phase on 2026-05-19. Append-only; re-running the phase adds new items below, never modifies existing ones.*

### M1: iPad UAT — 5-tab split, empty states, localStorage persistence

- **Source step:** Step O3 (from this plan's §5)
- **Issue:** #180
- **Commands:**
  ```powershell
  # In two terminals:
  uv run python -m toybox.main --host 127.0.0.1 --port 8000
  cd frontend; npm run dev
  # Then open the parent app on iPad at http://<TOYBOX_LAN_IP>:4000/parent
  ```
- **What to look for:**

  | Check | Expected outcome |
  |---|---|
  | Boot with empty localStorage | App lands on the All sub-tab; no crash. |
  | Migration from legacy state | If iPad had `toybox.parent.tabs.play = "play-ideas"` from before O1, it now reads `"all"`; if `"transcription"`, it now reads `"transcriptions"`. |
  | 5 sub-tabs render | Labels in order: All / Adventures / Elements / Feelings & Friends / Transcriptions. |
  | Click-through | Each sub-tab click changes selection AND persists across reload (close tab, reopen → last selection sticks). |
  | Empty-state copy per tab | All: "No play ideas yet. Approve one when a suggestion appears." / Adventures: "No adventures suggested yet." / Elements: "No element activities suggested yet." / Feelings & Friends: "No feelings & friends activities suggested yet." |
  | Adventure proposal under Adventures | Approve or wait for a non-element non-feelings proposal (Phase K/L branching template) — it appears under Adventures + All, not under Elements / Feelings & Friends. |
  | Element activity under Elements | Approve or wait for a Phase M `meet_element_*` or Phase N `element_microgame_*` proposal — it appears under Elements + All, not under Adventures. |
  | SEL activity under Feelings & Friends | Approve or wait for a Phase M `feelings_*` / `perspective_*` / `conflict_*` / `repair_*` proposal — it appears under Feelings & Friends + All. |
  | Transcriptions tab unchanged | Raw transcript log renders as before; no element/adventure cards leak in. |
  | Engagement regression check | No regression vs Phase M baseline — proposals continue to render and approve cleanly across all 4 content sub-tabs. |
  | Categorical utility | After 10 min of mixed use: does the operator naturally use the categorical filters, or is All the only one used? Useful signal vs ceremony signal for Phase P scope-down. |

- **If it fails:** file follow-up issue at `aberson/toybox` referencing #180 + this run-doc; do NOT roll back O1/O2 unless the parent UI is unusable. The first iteration of O3 is acceptable with cosmetic defects per the Phase K/M precedent.
