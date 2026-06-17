# Sonnet-Window Revisit Plan — toybox

**Status:** Open — Opus diff re-review pending. **CORRECTED 2026-06-17:** window widened Jun 5–8 → Jun 4–16; added the missed `02f8c76` LAN-bind launcher (security) and `668c1b7` compare_animate.py (a 341-line ML script hidden behind a "docs" subject).
**Created:** 2026-06-16
**Owner:** operator + Opus session

## Why this exists

A `latest`-channel CLI auto-update (~2026-06-05) silently reset the model from Opus to
Sonnet. The regression ran from ~Jun 5 until **Opus was re-pinned 2026-06-16** — true window
**Jun 4–16**. Root cause + fix: `dev/CLAUDE.md` Environment section, memory
`feedback_model_pin_opus_autoupdate_reset`.

toybox landed **four `/build-phase` phases (R, S, T, U/V)** under Sonnet. Revisit depth:
**diff re-review with Opus**.

> **Window correction (2026-06-17).** The original plan claimed "Jun 5–8" and missed two
> commits: `02f8c76` (Jun 5, the one-click launcher that binds `0.0.0.0` for the child kiosk —
> security-relevant) and `668c1b7` (Jun 7, which despite its `docs(U→V)` subject adds a
> 341-line `scripts/compare_animate.py` ML script). toybox's last feature commit is `241eaf0`
> (Jun 7); there is **no feature work Jun 9–16**, so the forward tail is clean — but the
> truncated label is what hid the two Jun 5–7 commits.

## Scope caveat

These were `/build-phase` runs (sub-agent developers/reviewers). Sub-agent pinning may
differ, but **plan authoring, phase orchestration, UAT-bundle decisions, and pipeline
fixes were Sonnet**. toybox is also a child-facing device — safety judgment (Phase R Q&A
gating) and the LAN-bind guard deserve careful Opus passes.

## Commit inventory (Jun 4–16)

### Phase R — UX refinements (HIGH — child-facing + safety gating)

| Commit | Date | What | Re-review focus |
|--------|------|------|-----------------|
| 5ac5860 | 06-05 | plan Phase R UX refinements | Plan soundness |
| 222a804 | 06-05 | R1: remove cadence loop + restyle TriggerButton | Behavior change correct? |
| c6d4816 | 06-05 | R2: spoken text character limit | Limit value sane for kids |
| a7ebf84 | 06-05 | **R3: Q&A gating for activity steps (#214)** | **Safety-critical — gating logic for child content** |
| fafb9c5 | 06-05 | R4: activity search GET /api/search + SearchPanel (#215) | Endpoint + search correctness |

### Launcher — LAN bind for child kiosk (HIGH — security) — ADDED 2026-06-17

| Commit | Date | What | Re-review focus |
|--------|------|------|-----------------|
| 02f8c76 | 06-05 | one-click launcher (parent local + child on LAN) — scripts/launch-toybox.ps1 (+88), .cmd (+3) | **LAN bind `0.0.0.0`** — confirm the backend's parent-PIN startup guard is a real invariant (CLAUDE.md: "Startup guard refuses non-loopback host without one") and the launcher cannot bypass it; `$bindHost` defaults to loopback when `-LoopbackOnly`/no LAN IP. Per `dev/.claude/rules/security.md` "documentation is not a control". |

### Phase S — kiosk visual refresh + animation (MEDIUM)

| Commit | Date | What |
|--------|------|------|
| 2e78fa4 | 06-05 | plan Phase S |
| e50a88e | 06-05 | S1: persona-keyed kiosk gradients + step card prominence |
| c7b19e0 / 2040322 | 06-05 | S2: Claude approve-time avatar animations |

### Phase T — bundled UAT + offline catalog browse (MEDIUM)

| Commit | Date | What | Re-review focus |
|--------|------|------|-----------------|
| a20b7dd | 06-06 | plan Phase T | — |
| 601e329 | 06-06 | T2: GET /api/catalog + CatalogEntry/Response + tests | **Wire-shape: response read directly by frontend** (code-quality rule) |
| 45ab1f8 | 06-06 | T3: CatalogPanel + browse toggle + categorizeTemplate | Categorization correctness |

### Phase U/V — AnimateDiff/SVD animations (MEDIUM — heavy ML pipeline)

| Commit | Date | What | Re-review focus |
|--------|------|------|-----------------|
| 1d7a05a | 06-06 | plan Phase U | — |
| a25f4a1 | 06-06 | U1: AnimateDiff wrapper + ToyActionSprite WebP fallback | Pipeline correctness |
| 7d12c20 | 06-06 | U2/U3: pipeline fixes; 140 WebPs generated | Output quality (visual) |
| 44fba96 | 06-07 | plan Phase V | — |
| 4908131 | 06-07 | V1: ToyActionSprite CSS intro + idle WebP state machine | State machine correctness |
| 241eaf0 | 06-07 | V2: --approach svd to batch_animate.py | — |
| 668c1b7 | 06-07 | **compare_animate.py (NEW, 341 lines)** behind a docs(U→V) subject | **Unlisted ML script** — SVD/AnimateDiff img2vid pipeline: subprocess handling, model-path/output-path construction, identity-preservation defaults |
| ce7e0a0 | 06-07 | remove unused io import in compare_animate.py (F401) | Trivial; fold into the 668c1b7 review |

> Phase V left **M1 (iPad UAT) deferred** (07f7013). Confirm that UAT was completed correctly
> or is still pending — a Sonnet-authored deferral decision.

## Shared deliverable

Each step appends per-commit verdicts to **`documentation/sonnet-window-revisit-findings.md`**.
Row: `commit | verdict (OK | needs-fix | reverted) | note`.

## Procedure (per commit, inside each step)

1. `git -C c:/Users/abero/dev/toybox show <sha>` — read the full diff.
2. Judge: intent preserved? load-bearing dropped? safety/security regression?
3. Record verdict in the findings doc; apply fixes in-step for needs-fix/revert.

## Build steps

### Step 1: Re-review Phase R — UX + Q&A safety gating

- **Problem:** Phase R (Jun 5) changed child-facing UX under Sonnet, including **R3 Q&A gating for activity steps (#214)** — the logic deciding what a child is shown. A Sonnet error in gating is a child-safety risk; R2's spoken-text character limit and R4's search endpoint also need a pass.
- **Type:** code
- **Issue:** #239
- **Files:** src/toybox/ (activities, api, ws), frontend/src/parent/, frontend/src/child/, documentation/sonnet-window-revisit-findings.md
- **Done when:** a7ebf84 (R3 gating) reviewed FIRST for safety-correctness; c6d4816 (R2 limit) confirmed sane for kids; 222a804 (R1) + fafb9c5 (R4 search) reviewed; test suite green; verdicts appended; fixes applied where needed.
- **Flags:** --reviewers code
- **Status:** DONE (2026-06-17) — all 5 commits OK (no Sonnet error). a7ebf84 R3 gate verified sound (server-side, parent-scope, fails-closed, atomic) by orchestrator + adversarial reviewer. Cross-cutting pre-existing finding (kiosk holds parent token; gate UI-enforced not credential-enforced until Phase D Step 20) filed as #244.

<!-- autofix-applied: 2026-06-17 -->
### Step 2: Re-review launcher LAN-bind security (02f8c76)

- **Problem:** `02f8c76` (Jun 5) added a one-click launcher that binds the backend to `0.0.0.0` for the iPad child kiosk. The launcher only *comments* "LAN bind requires the parent PIN"; per `security.md`, documentation is not a control. The backend enforces the PIN as a **startup invariant** in `src/toybox/core/bind_guard.py` — verify that guard actually refuses a non-loopback host without a PIN, and that the launcher cannot bypass it.
- **Type:** code
- **Issue:** #240
- **Files:** scripts/launch-toybox.ps1, scripts/launch-toybox.cmd, src/toybox/core/bind_guard.py (non-loopback PIN startup guard), src/toybox/main.py (guard invocation), documentation/sonnet-window-revisit-findings.md
- **Done when:** the backend startup guard is confirmed to refuse a non-loopback host when no parent PIN is set (stable error, not just a log line); the launcher's `$bindHost` logic defaults to `127.0.0.1` under `-LoopbackOnly` or no-LAN-IP and cannot inject a PIN bypass; verdict appended; any guard gap fixed in-step.
- **Flags:** --reviewers code
- **Status:** DONE (2026-06-17) — all 4 items OK, NO bypass. Guard aborts startup (`return 1`) on non-loopback+no-PIN before uvicorn.run; `args.host` single-source (`TOYBOX_HOST`→guard AND uvicorn, no divergence); launcher defaults to loopback under -LoopbackOnly/no-LAN-IP, seeds no PIN, injects no bypass. `_pin_is_set` fails closed (missing row→False; unopenable DB→exit non-zero). Verified by orchestrator independent read + dev agent adversarial bypass hunt.

### Step 3: Re-review Phase S — kiosk visual + avatar animation

- **Problem:** Phase S (Jun 5) added persona-keyed kiosk gradients, step-card prominence, and Claude approve-time avatar animations under Sonnet.
- **Type:** code
- **Issue:** #241
- **Files:** frontend/src/child/, frontend/src/parent/, src/toybox/, documentation/sonnet-window-revisit-findings.md
- **Done when:** 2e78fa4/e50a88e/c7b19e0/2040322 diffs reviewed for correctness; verdicts appended; fixes applied where needed.
- **Flags:** --reviewers code

### Step 4: Re-review Phase T — catalog endpoint wire-shape

- **Problem:** Phase T (Jun 6) added GET /api/catalog + CatalogEntry/Response (601e329) and the CatalogPanel browse UI (45ab1f8). The catalog response is read directly by the frontend — a Sonnet wire-shape drift is exactly the class `code-quality.md` warns about.
- **Type:** code
- **Issue:** #242
- **Files:** src/toybox/api/, frontend/src/shared/types.ts, frontend/src/parent/, documentation/sonnet-window-revisit-findings.md
- **Done when:** the /api/catalog response shape asserted to match what CatalogPanel renders (not just "tests pass" — the dev who shipped it may have updated tests to match); categorizeTemplate correctness checked; verdicts appended; fixes applied where needed.
- **Flags:** --reviewers code

### Step 5: Re-review Phase U/V animations + compare_animate.py

- **Problem:** Phase U/V (Jun 6–7) built the AnimateDiff→SVD hybrid animation pipeline under Sonnet, including the **previously-unlisted** `scripts/compare_animate.py` (668c1b7, 341 lines, hidden behind a docs subject) and its F401 follow-up (ce7e0a0). Sonnet bugs could hide in SVD model loading, subprocess handling, or output-path construction.
- **Type:** code
- **Issue:** #243
- **Files:** scripts/compare_animate.py, scripts/batch_animate.py, src/toybox/ (ToyActionSprite / sprite state machine), frontend/src/child/, documentation/sonnet-window-revisit-findings.md
- **Done when:** a25f4a1/7d12c20 (AnimateDiff), 4908131 (CSS intro + idle state machine), 241eaf0 (SVD), and 668c1b7/ce7e0a0 (compare_animate.py) diffs reviewed for pipeline + path/subprocess correctness; Phase V M1 iPad-UAT status confirmed (not silently skipped); verdicts appended; fixes applied where needed.
- **Flags:** --reviewers code

## Notes

- dev / applied_learning have their own revisit plans; void_furnace is handled by the operator separately.
- Steps are ordered by risk: Step 1 (child-safety Q&A gating) and Step 2 (LAN-bind security) are highest; the rest are correctness/quality.
