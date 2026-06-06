# Phase T — Bundled UAT Clearance + Offline Template Catalog Browse

## 1. What This Feature Does

Phase T has two distinct parts:

**T1 — Bundled iPad UAT (operator step).** Clears the deferred UAT debt accumulated across
Phases R, S, and O. R5 (Phase R UX refinements) and S3 (Phase S kiosk visual refresh) have been
code-shipped since 2026-06-05 but never iPad-validated. O1+O2 (Phase O 5-tab parent UX
categorization) are also code-shipped and merged, but issues #178, #179, and #180 were never
closed. A single iPad session closes all five pending issue threads plus their umbrella issues
(#211 Phase R, #217 Phase S, #177 Phase O).

**T2–T3 — Offline template catalog browse (code steps).** Adds a parent-facing browse surface
that lets the parent page through the full 1,243-template catalog without first triggering the
mic. Each Play sub-tab (All, Adventures, Elements, Feelings & Friends) gets a [Queue / Browse
catalog] toggle. In Browse mode the queue is replaced by template cards — title, themes, step
count, and a "Launch" button that proposes the pinned template into the queue. The backend
exposes a new `GET /api/catalog` endpoint; the frontend adds a `CatalogPanel` component and
wires it into the existing sub-tab layout from Phase O.

Phase R's plan explicitly reserved "Offline template mode for adventures (Phase T)" as out of
scope for R, making this the planned next step for the parent UX surface.

**T4 — Phase T iPad UAT (operator step).** Validates catalog browse on real hardware with both
children present (Child A age 6, Child B age 4).

## 2. Existing Context

- **Activity search (Phase R4):** `src/toybox/api/search.py` — `GET /api/search?q=...` does
  keyword LIKE scan over past activities + in-memory template registry. Exposes
  `_load_intent_templates(intent)` from `generator.py`. Template metadata available on each
  `_Template` dataclass: `id`, `title`, `recommended_themes` (tuple of Theme enums),
  `required_roles`, `optional_roles`, `steps` (for count). No pagination — "the target dataset
  is small enough that a LIKE scan is instantaneous on a local SQLite file" (per search.py
  module docstring).

- **Template pinning (Phase R4):** `ProposeRequest.template_id: str | None`
  (`activities.py:530`) — when set, the generator bypasses the slot-picker and uses the named
  template. The SearchPanel's "Try this" / "Play again" buttons already use this. The catalog
  "Launch" button follows the same pattern.

- **Phase O sub-tab layout (merged at `993dd90`):** Five sub-tabs under Play — All /
  Adventures / Elements / Feelings & Friends / Transcriptions. O1 added
  `frontend/src/parent/components/PlaySubTab` widened to 5 tabs + localStorage migration. O2
  added `frontend/src/parent/components/categorize.ts` helper + `filterCategory` prop on
  `PlayQueueList`. O3 = iPad UAT (deferred; issues #178, #179, #180 open). The Phase O
  category labels (Adventures / Elements / Feelings & Friends) map to a `categorize()` rule
  using `recommended_themes` + `template_id` prefix — the same data the catalog endpoint will
  expose.

- **ProposeRequest shape** (`activities.py:480–530`): `intent` (required), `slot`, `hour`,
  `seed`, `persona_id`, `category`, `template_id`, `use_recent_transcripts`. No `child_id`
  field — propose is household-scoped. When `template_id` is pinned, `category` filter is a
  no-op (template IS the selection).

- **Router registration pattern** (`app.py:43, 103–104`): routers are imported as
  `from .api.<module> import router as <name>_router` and registered with
  `app.include_router(<name>_router)`. The search router is the nearest example —
  `catalog.py`'s router follows the same pattern.

- **TypeScript codegen** (`tools/gen_types_ts.py`): deterministic emitter; pre-commit hook
  gates drift. New Pydantic models on the backend auto-regenerate `frontend/src/shared/types.ts`
  when the pre-commit hook runs (or manually via `uv run python tools/gen_types_ts.py`).

- **Template title format:** Titles contain `{slot}` placeholders (e.g., `"Pirate ship of
  {room}"`). Phase T displays them verbatim in the catalog — parents understand the system.
  Placeholder substitution is a future refinement.

- **No auth on catalog endpoint:** `GET /api/catalog` is read-only and exposes no PII. Same
  reasoning as search.py — no auth required. The parent UI itself is PIN-gated at the React
  level.

## 3. Scope

**In scope:**
- Bundled iPad UAT closing R5 (#216), S3 (#220), O3 (#180), and formally closing O1 (#178) +
  O2 (#179) since their code is merged
- `GET /api/catalog` — new endpoint returning all 1,243 template entries (id, title, intent,
  themes, step_count); no query params; no auth; reuses `_load_intent_templates`
- New Pydantic models `CatalogEntry` + `CatalogResponse` in `catalog.py`; codegen update
- `CatalogPanel` React component — [Queue / Browse catalog] toggle per Play sub-tab; template
  cards; theme filter chips; "Launch" button
- `getCatalog()` function in `frontend/src/parent/api.ts`
- Phase T iPad UAT

**Explicitly out of scope:**
- Slot placeholder substitution in catalog titles (`{room}` → displayed verbatim)
- Persona picker per catalog card (Launch uses persona_id=null, same as "Try this" today)
- Pagination (1,243 templates at ~200 bytes each = ~250 KB — fine for local LAN)
- Theme filters on the backend (frontend does all filtering via categorize logic)
- Q7–Q8 Phase Q operator steps (#202–#205) — these require running Coqui TTS locally;
  excluded from T's UAT bundle to keep the session focused
- P7–P8 Phase P operator steps (#189, #191) — also excluded from T's UAT bundle for the
  same reason (require hardware-bound rendering work)
- Phase E (local fine-tune) — still stalled; Step 27 can resume whenever ≥50 SFT rows exist

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/toybox/api/catalog.py` | create | New router + `CatalogEntry` + `CatalogResponse` Pydantic models; `GET /api/catalog`; reuses `_load_intent_templates` | `_load_intent_templates` glob confirmed in `src/toybox/activities/generator.py`; currently imported only by `src/toybox/api/search.py` (grep confirmed 1 caller) |
| `src/toybox/app.py` | modify | Register `catalog_router` via `include_router` (same pattern as search router at line 104) | read confirmed; `search_router` at line 43 + 104 is the nearest analog |
| `frontend/src/shared/types.ts` | modify (codegen) | `CatalogEntry` + `CatalogResponse` types generated by `tools/gen_types_ts.py` after `catalog.py` adds new Pydantic models | pre-commit hook auto-gates drift; manual run: `uv run python tools/gen_types_ts.py` |
| `frontend/src/parent/api.ts` | extend | Add `getCatalog(): Promise<CatalogResponse>` — `fetch("/api/catalog")` + parse | glob confirmed (file exists, pattern matches existing `getSearch` etc.) |
| New: `frontend/src/parent/components/CatalogPanel.tsx` | create | Browse panel: fetches catalog on mount; applies `categorizeTemplate()` filter for current sub-tab; theme filter chips; template card grid; "Launch" button calling `api.propose({intent, template_id, hour, seed})` | |
| Play sub-tab render location | modify | Add [Queue / Browse catalog] segment control per sub-tab; toggling shows `PlayQueueList` or `CatalogPanel` | Phase O confirmed `PlayQueueList` + `filterCategory` pattern in `frontend/src/parent/`; exact component confirmed by build-step agent at implementation time |
| `tests/unit/api/test_catalog.py` | create | Unit tests: all-templates response, total count matches sum of all intent template counts, entry shape (id/title/intent/themes/step_count all present), no auth required | |
| `tests/integration/test_catalog_api.py` | create | Integration test: `GET /api/catalog` returns 200 + JSON with `total > 0`; all entries have non-empty `id` and `intent` in `SUPPORTED_INTENTS` | |
| `frontend/src/parent/components/CatalogPanel.test.tsx` | create | Vitest: renders template cards; Launch calls `api.propose` with correct template_id; theme chip filter hides non-matching cards; toggle switches between Queue/Browse views | |

## 5. New Components

### `src/toybox/api/catalog.py`

Exports `router` (FastAPI `APIRouter(prefix="/api/catalog", tags=["catalog"])`).

**Pydantic models:**

```python
class CatalogEntry(BaseModel):
    id: str
    title: str
    intent: str
    themes: list[str]      # from _Template.recommended_themes (Theme enum values)
    step_count: int        # len(_Template.steps) — includes fork branches

class CatalogResponse(BaseModel):
    entries: list[CatalogEntry]
    total: int             # len(entries) — convenience for the frontend
```

**Endpoint:**

```
GET /api/catalog
No required params. No auth.
Returns CatalogResponse with all 1,243 templates across all 4 SUPPORTED_INTENTS.
Uses _load_intent_templates(intent) for each intent in SUPPORTED_INTENTS.
Deduplicates by template id (same _Template id can appear in multiple intent files — use a seen_ids set, same pattern as search.py:_search_templates).
```

The response is ~250 KB JSON — acceptable for a local LAN device. No pagination needed.

### `frontend/src/parent/components/CatalogPanel.tsx`

Props: `{ filterCategory: "adventures" | "elements" | "feelings-friends" | undefined }` —
mirrors the same `filterCategory` type accepted by `PlayQueueList`.

Behavior:
- Fetches `GET /api/catalog` once on mount (no refetch needed — templates don't change at
  runtime). Stores in local state.
- Applies `categorizeTemplate(entry, filterCategory)` to filter entries for the current sub-tab.
  `categorizeTemplate` is a new helper in `CatalogPanel.tsx` that replicates Phase O's
  `categorize()` logic applied to `CatalogEntry` (using `entry.themes` instead of
  `activity.recommended_themes`).
- Optional theme filter: a row of chip buttons populated from the union of all themes across
  the visible entries. Tapping a chip further filters the list. One active chip at a time;
  tapping again deselects.
- Template card: `entry.title`, intent badge, theme chips, `step_count` label ("N steps"),
  "Launch" button.
- Launch button: calls `api.propose({ intent: entry.intent, template_id: entry.id, hour:
  new Date().getHours(), seed: Math.floor(Math.random() * 1e6), use_recent_transcripts: false
  })` (`api.propose()` is the class method in `frontend/src/parent/api.ts:971`; NOT a
  standalone `proposeActivity` function). On success, shows a brief "Proposed!" toast. On
  error, shows "Failed — retry?".

The [Queue / Browse catalog] toggle that surfaces `CatalogPanel` vs. `PlayQueueList` is added
to the per-sub-tab render in the Play tab component (App.tsx or its extracted sub-component),
using `useTabState` from `frontend/src/parent/components/Tabs.tsx` for toggle persistence (NOT bare `useState` or raw `localStorage`; same pattern as Phase H). The toggle renders only for the four
content sub-tabs (All, Adventures, Elements, Feelings & Friends) — the Transcriptions sub-tab
has no catalog view.

## 6. Design Decisions

### GET /api/catalog returns all templates — no server-side filtering

1,243 templates × ~200 bytes ≈ 250 KB. On a local LAN this is a single fast fetch. Phase O's
`categorize.ts` already handles category filtering on the frontend; duplicating the logic in
Python for a server-side `category` param adds a second source of truth with no benefit at this
scale. All filtering (category, theme chips) is done client-side.

Alternative considered: server-side `theme` filter param. Rejected — frontend already needs to
filter by category anyway, so the extra server param buys nothing except partial filtering of a
250 KB response into a 200 KB response.

### Toggle (not a new sub-tab) for browse vs. queue

Phase O's 5-tab layout is already established. Adding a 6th sub-tab ("Catalog") would widen the
tab bar past a comfortable fit on iPad (6 items). A per-tab toggle ([Queue | Browse catalog])
reuses the existing sub-tab's intent (Adventures → shows adventure queue OR adventure templates)
and keeps tab count at 5. The toggle's active value can persist in `localStorage` if desired,
but defaults to Queue to preserve current UX for parents who never want browse.

### Catalog title displayed verbatim (with `{slot}` placeholders)

Slot substitution requires knowing which child, toy, and room are active at browse time — the
same resolver that runs at propose time. Running the full slot resolver for 1,243 templates on
every catalog fetch is heavy and would change the title on every proposal (different random
picks). Verbatim display is honest ("Pirate ship of {room}" clearly shows this is a template)
and fast. Future refinement: a lightweight "preview" substitution that replaces `{room}` →
"the room", `{quest_giver}` → "your child", etc. without full resolver machinery.

### No auth on GET /api/catalog

Same reasoning as `search.py`: read-only, no PII, local device. The parent UI itself is
PIN-gated at the React session level.

### Launch button follows the same propose path as "Try this" in SearchPanel

`ProposeRequest.template_id` already handles template pinning. `POST /api/activities/propose`
with `{ intent, template_id, hour, seed }` is identical to what SearchPanel's "Try this"
button posts. No new backend endpoint needed for "launch from catalog".

## 7. Build Steps

### Step T1: Bundled iPad UAT — close R5 + S3 + O3
- **Problem:** R5 (#216), S3 (#220), and O3 (#180) are deferred UAT issues from Phases R, S,
  and O respectively. O1 (#178) and O2 (#179) code is merged but issues are open. All five
  close in a single iPad session before new T code ships.
- **Type:** operator
- **Issue:** #223
- **Produces:** UAT run-doc at `documentation/runs/2026-06-<date>-phase-t-uat-bundle.md`;
  close #216, #220, #180, #178, #179, and umbrella issues #211 (Phase R), #217 (Phase S),
  #177 (Phase O)
- **Done when:** Operator confirms on iPad:
  1. **R5 checklist** — cadence controls gone from Settings; TriggerButton is the prominent
     primary CTA; Read Me spoken text limit is respected (long step body truncates at word
     boundary); Q&A gating works (Next hidden until parent approves/skips); activity search
     returns relevant results and "Try this" proposes the pinned template
  2. **S3 checklist** — kiosk background shifts to persona-appropriate gradient when activity
     is approved (test at least 2 different personas); avatar animates continuously per step;
     animation changes on step advance; no flashing or strobe; step card body is clearly
     readable at arm's length; both Child A (6) and Child B (4) confirm no animation triggers
     sensory concern
  3. **O3 checklist** — Play tab shows 5 sub-tabs (All / Adventures / Elements / Feelings &
     Friends / Transcriptions); each sub-tab filters the suggestion queue correctly; Adventures
     shows non-element / non-SEL activities; Elements shows element activities; Feelings &
     Friends shows SEL activities; Transcriptions tab shows the listening log unchanged
  4. Write run-doc with verdict per checklist row
- **Depends on:** none

### Step T2: Backend `GET /api/catalog` endpoint
- **Problem:** No API endpoint exists for browsing the full template catalog without a search
  query. The frontend cannot render a catalog browse panel without template metadata from the
  backend.
- **Issue:** #224
- **Flags:** --reviewers code
- **Produces:**
  - `src/toybox/api/catalog.py` — `CatalogEntry` + `CatalogResponse` Pydantic models;
    `GET /api/catalog` endpoint (no auth, no params, returns all templates from all 4 intents)
  - `src/toybox/app.py` — `catalog_router` import + `include_router` call
  - `frontend/src/shared/types.ts` — updated via `uv run python tools/gen_types_ts.py` (adds
    `CatalogEntry`, `CatalogResponse` TypeScript interfaces)
  - `tests/unit/api/test_catalog.py` — unit tests (total count, entry shape, no auth)
  - `tests/integration/test_catalog_api.py` — integration test via TestClient (200 + valid
    JSON, all entries have non-empty id + valid intent)
- **Done when:** `uv run pytest tests/ -x -q` passes (including new catalog tests); `uv run
  mypy src` 0 errors; `uv run ruff check .` clean; `git diff --exit-code
  frontend/src/shared/types.ts` confirms codegen ran and types.ts includes `CatalogEntry`
- **Depends on:** T1 (UAT cleared)
- **Status:** DONE (2026-06-06)

<!-- autofix-applied: 2026-06-06 -->
### Step T3: Frontend `CatalogPanel` + [Queue / Browse catalog] toggle
- **Problem:** The parent has no way to browse the template catalog without a search query.
  The backend catalog endpoint built in T2 has no frontend consumer yet.
- **Issue:** #225
- **Flags:** --reviewers code
- **Produces:**
  - `frontend/src/parent/components/categorize.ts` — extend with `categorizeTemplate(entry:
    CatalogEntry, filterCategory: "adventures" | "elements" | "feelings-friends" | undefined):
    boolean` helper; MUST reuse / extract the shared logic from the existing `categorize()`
    function in the same file so both consumers import from one source of truth (no standalone
    copy in CatalogPanel.tsx). Read `categorize.ts` before implementing.
  - `frontend/src/parent/components/CatalogPanel.tsx` — fetches `GET /api/catalog`; applies
    `categorizeTemplate()` filter + theme chip filter; renders template cards with title, intent
    badge, themes, step count, "Launch" button calling `api.propose()` (class method in
    `frontend/src/parent/api.ts:971`; NOT a standalone `proposeActivity` function)
  - `frontend/src/parent/api.ts` — add `getCatalog(): Promise<CatalogResponse>` method on
    the API class (same pattern as `api.propose()`, `api.search()`, etc.)
  - Play sub-tab render (grep `PlayQueueList` in App.tsx to find the render location before
    writing code) — [Queue / Browse catalog] toggle per content sub-tab (All, Adventures,
    Elements, Feelings & Friends; not Transcriptions); persist toggle with `useTabState` from
    `frontend/src/parent/components/Tabs.tsx` (NOT bare `useState` or raw `localStorage`) so
    each sub-tab's browse/queue choice persists across tab switches
  - `frontend/src/parent/components/CatalogPanel.test.tsx` — vitest: renders template cards;
    Launch calls `api.propose` with correct template_id + intent; theme chip filters list;
    toggle switches views; no crash on empty catalog response
- **Done when:** `npm run typecheck && npm run lint && npm run test -- --run` all pass; new
  vitest tests cover: Launch button calls `api.propose` with correct template_id + intent,
  `categorizeTemplate` filter correctness for all 3 category values, theme chip deselect, empty
  state; no regressions in existing parent UI tests
- **Depends on:** T2

### Step T4: iPad UAT — catalog browse validation
- **Problem:** CatalogPanel is new UI that runs on real hardware with real children; vitest
  covers unit paths but not the actual "parent browses, taps Launch, kiosk runs activity" flow.
- **Type:** operator
- **Issue:** #226
- **Produces:** UAT run-doc at `documentation/runs/2026-06-<date>-phase-t-catalog-uat.md`
- **Done when:** Operator confirms on iPad:
  1. Parent Play tab shows [Queue | Browse catalog] toggle on Adventures sub-tab
  2. Switching to Browse catalog shows template cards with titles, theme badges, step count
  3. Tapping "Launch" on a template proposes it into the queue (visible in All sub-tab)
  4. Approving a proposed catalog-launched template runs on the kiosk normally
  5. Theme chip filters the visible template list (selecting "pirates" hides non-pirate cards)
  6. Switching back to Queue restores the normal suggestion queue
  7. Elements sub-tab browse shows element templates; Feelings & Friends shows SEL templates
  8. No regressions in the existing R+S features (search, Q&A gating, avatar animations, gradients)
- **Depends on:** T3

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| 250 KB catalog response slow on older iPad | Safari may stall briefly on JSON parse | Profile in UAT; if >1s on iPad Pro, add server-side `intent` filter as a follow-up |
| `categorizeTemplate()` diverges from `categorize()` | Two copies of categorize logic drift | Extract shared categorize rules into a single helper in `categorize.ts` that both `PlayQueueList` and `CatalogPanel` import. The build-step agent must read `categorize.ts` before implementing `categorizeTemplate()` |
| Template title `{slot}` placeholders confuse parents | "Pirate ship of {room}" looks broken | Documented as expected in Phase T; future refinement for slot preview |
| Phase O issues #178/#179 never closed | Issues look open even though code shipped | T1 explicitly closes them as part of the O3 UAT verdict |
| O/P/Q UAT still pending after T1 | P7/P8 (#189/#191) and Q7–Q8 (#202–#205) remain open | Scoped out of T1 intentionally (hardware-bound rendering); add to Phase U planning backlog |
| Toggle persistence across tab switches | If toggle is in local state only, switching sub-tabs resets it to Queue | Persist toggle value in localStorage per sub-tab using `useTabState` pattern (Phase H). Build-step agent: read `useTabState` signature in `frontend/src/parent/components/Tabs.tsx` before implementing |

## 9. Testing Strategy

**Backend (pytest):**
- `tests/unit/api/test_catalog.py`: verify `GET /api/catalog` returns `CatalogResponse` with
  `total > 0`; each entry has `id`, `title`, `intent` in `SUPPORTED_INTENTS`, `themes: list[str]`,
  `step_count: int >= 1`; total matches len(entries); no auth header required.
- `tests/integration/test_catalog_api.py`: mount the app via `TestClient`; hit the real
  endpoint; assert 200 + JSON parseable to `CatalogResponse`; spot-check that a known template
  id appears (e.g., `request_play_soak_pirate_01`).
- Existing pytest suite: must continue to pass (catalog endpoint is additive — no existing
  route changes).

**Frontend (vitest):**
- `CatalogPanel.test.tsx`: mock `getCatalog()` to return a small fixture (3 templates of
  different categories/themes); assert cards render; assert Launch calls `api.propose` with
  correct template_id; assert theme chip hides non-matching cards; assert toggle switches
  between queue placeholder and catalog view.
- Existing parent UI tests: must pass unchanged (Play tab layout additions are additive).

**iPad UAT (T4):** the only validation for the full "parent browses → launches → kiosk runs"
round-trip. See T4 done-when checklist.

**Data pipeline smoke:** no new producer-consumer chains introduced. The catalog endpoint reads
from the same in-memory template registry as the search endpoint — the same loading/validation
path that's been exercised since Phase G.
