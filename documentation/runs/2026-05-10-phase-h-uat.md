# Phase H — iPad parent-app UAT (2026-05-10)

**Status:** PASS (with one unrelated Phase G defect filed as follow-up [#84](https://github.com/aberson/toybox/issues/84))

## Environment

- **Hardware:** iPad (operator-driven)
- **Backend:** master @ `1ea9559` (H5 checkpoint) running `uv run python -m toybox.main --host 0.0.0.0 --port 8000`
- **Frontend:** Vite dev server at `:4000` serving the master HEAD parent app
- **Pre-flight:** `data/toybox.db` backed up to `data/toybox.db.pre-h4.bak` before the first backend start that triggered migration 0009

## Walkthrough results

| # | Check | Result |
|---|-------|--------|
| 1 | Top tab + sub-tab navigation | PASS — all panels render, defaults match Vocabulary |
| 2 | Hard-refresh persistence from Settings → Stats | PASS — localStorage round-trips |
| 3 | Play Ideas trigger + activity surface hide on tab switch | PASS — surface hidden when off Play |
| 4 | Switch back to Play → activity surface restored | PASS — state preserved |
| 5 | Activity completion | PASS — ActivityPanel updates |
| 6 | Settings → Settings panel render | PASS — all toggles + BannedThemesSettings present |
| 7 | Banned themes Save round-trip | **PASS (after backend restart — see "Issues found" §1)** |
| 8 | Banned themes propagate to Claude prompt | PASS — verified via `GET /api/settings/banned-themes` |
| 9 | ChildProfileEditor has no banned-themes field | PASS — fully gone (form, list rows, preset picker) |
| 10 | Stats panel matches old Operator metrics | PASS |
| 11 | Clear + reload + fresh proposal | PASS — empty-global doesn't regress |

## Issues found

### 1. (Resolved) BannedThemesSettings 404 on Save

**Symptom:** Save in the new BannedThemesSettings panel surfaced two "api error 404" banners (one from initial GET, one from the PUT attempt).

**Root cause:** The backend process the operator was running predated the H4/H5 changes. The new `/api/settings/banned-themes` router was registered in `app.py` but the live uvicorn process was stale code.

**Resolution:** Restarted the backend. After restart, GET + PUT round-trip cleanly. Migration 0009 ran on first start.

**Not a Phase H regression** — purely a stale-process operational issue. Captured here for the next operator's H6-style walkthrough.

### 2. (Filed as follow-up) Slot-fill leaks `{adjective}` placeholder in choice labels

**Symptom:** On a fresh cursed-beast branching adventure, choice (2) of step "fight_fork" rendered as `"Tell it a {adjective} joke"` with the literal placeholder visible. Choice (1) `"Strike with the cardboard tube"` substituted correctly. Same choices array, partial fill.

**Root cause:** `_resolve_template_slots` at `src/toybox/activities/generator.py:625` builds the haystack from `template.title + step.text` but omits `step.choices[i].label`. Placeholders that live only in a choice label never get added to `slot_values`, so advance-time `render_with_slot_fills` falls back to the literal placeholder.

**Scope:** Phase G bug (haystack shipped with `fa5e9db` / `20b6375`). NOT a Phase H regression. Filed as [#84](https://github.com/aberson/toybox/issues/84) with root cause + fix sketch + audit follow-on for other label-only placeholders.

**Triage:** Medium severity — affects kid-visible UI on branching templates with this specific shape; not blocking H6 PASS for Phase H.

## Phase H closure

All H1–H5 deliverables verified working on real iPad hardware. Tab persistence, banned-themes round-trip, child editor cleanup, Stats panel, and activity-surface state preservation across tab switches all behave per the plan. Phase H is **DONE**.

Follow-up issue [#84](https://github.com/aberson/toybox/issues/84) tracks the unrelated Phase G slot-fill bug.
