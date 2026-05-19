# Phase N — operator iPad UAT run

- **Date:** 2026-05-18 (staged; UAT execution pending)
- **Phase:** N (element_microgame template shape + D1/D2 fold-ins)
- **Master at start of UAT:** `a4ae931` (N0 + N0b + N1-prep + N1 + N1.5 + N2 + N3 + N4 + N5 all shipped)
- **Operator:** abero
- **Device:** iPad on LAN, Safari → `http://<TOYBOX_LAN_IP>:4000/parent` + `/child`
- **Verdict:** ⏳ pending

## Pre-flight

| Check | Status |
|---|---|
| Backend started (`uv run python -m toybox.main --host 0.0.0.0 --port 8000`) | ☐ |
| Frontend started with LAN bind (`cd frontend; npm run dev`) | ☐ |
| `TOYBOX_LAN_IP` env var set in backend shell (so kiosk avatar binding works) | ☐ |
| iPad → PIN-unlocked parent app | ☐ |
| `data/songs/audio/*.mp3` rendered (75 entries from M7b) | ☐ |
| `data/images/elements/*.png` rendered (118 sprites from M2b) | ☐ |
| N5 smoke gate green: `uv run pytest tests/integration/test_phase_n_smoke.py` | ☐ |

## Part (a) — Cross-family spot-check (12 entries)

Per plan §5 N6 part (a): browse 12 element_microgame entries across the three kid-likely families. **Quality bar per row:** Step 1 intro flows; Step 2 family fork makes sense; Step 3 fact fork is binary + understandable; Step 4 reward fires the right song. **Pass if** ≥10 of 12 read coherently.

### Nonmetal (4)

| # | element_id | template_id | Renders | Step 1 flows | Step 2 fork sensible | Step 3 fork binary+clear | Step 4 reward fires | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | h-1 | `element_microgame_h_1` (Hydrogen) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 2 | c-6 | `element_microgame_c_6` (Carbon) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 3 | n-7 | `element_microgame_n_7` (Nitrogen) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 4 | o-8 | `element_microgame_o_8` (Oxygen) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |

### Transition metal (4)

| # | element_id | template_id | Renders | Step 1 flows | Step 2 fork sensible | Step 3 fork binary+clear | Step 4 reward fires | Verdict |
|---|---|---|---|---|---|---|---|---|
| 5 | fe-26 | `element_microgame_fe_26` (Iron) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 6 | cu-29 | `element_microgame_cu_29` (Copper) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 7 | ag-47 | `element_microgame_ag_47` (Silver) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 8 | au-79 | `element_microgame_au_79` (Gold) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |

### Noble gas (4)

| # | element_id | template_id | Renders | Step 1 flows | Step 2 fork sensible | Step 3 fork binary+clear | Step 4 reward fires | Verdict |
|---|---|---|---|---|---|---|---|---|
| 9 | he-2 | `element_microgame_he_2` (Helium) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 10 | ne-10 | `element_microgame_ne_10` (Neon) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 11 | ar-18 | `element_microgame_ar_18` (Argon) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| 12 | xe-54 | `element_microgame_xe_54` (Xenon) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |

**Result:** _of 12 pass the coherence bar_ → ☐ PASS (≥10) / ☐ FAIL (<10)

## Part (b) — Walkthrough with Child B (4 templates)

Per plan §5 N6 part (b): walkthrough with Child B on 4 element microgames: 2 familiar + 2 new. **Quality bar per row** (matches M14 + extra criterion e):

- (a) renders OK
- (b) Child B engages ≥50% of intended steps (parent estimate)
- (c) no rejection ("I don't want this")
- (d) no engine bug (404, validator error, blank step)
- (e) Child B picks the correct fork on at least 1 of 2 attempts per template (measures whether the binary forks are age-appropriate)

**Pass if** ≥3 of 4 pass criteria (a-d) AND sub-criterion (e) hits across ≥50% of attempts overall.

| # | element_id | template_id | Familiarity | Renders (a) | Engaged (b) | Not rejected (c) | No engine bug (d) | Picks correct fork (e) | Verdict | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| W1 | au-79 | `element_microgame_au_79` (Gold) | Familiar | ☐ | ☐ | ☐ | ☐ | __/2 forks | ☐ |  |
| W2 | h-1 | `element_microgame_h_1` (Hydrogen) | Familiar | ☐ | ☐ | ☐ | ☐ | __/2 forks | ☐ |  |
| W3 | he-2 | `element_microgame_he_2` (Helium) | New | ☐ | ☐ | ☐ | ☐ | __/2 forks | ☐ |  |
| W4 | cu-29 | `element_microgame_cu_29` (Copper) | New | ☐ | ☐ | ☐ | ☐ | __/2 forks | ☐ |  |

**Result (a-d):** _of 4 pass_ → ☐ PASS (≥3) / ☐ FAIL (<3)
**Result (e):** total correct forks: __ of 8 → ☐ PASS (≥4) / ☐ FAIL (<4)

## Phase M row #4 retest

Per plan §5 N6 + Phase M UAT deferred row #4. N0 (#168) was the BLOCKER GATE for this; verify it's actually unblocked on the kiosk.

| # | Template id | Intent | Kid | Renders | Engaged | Not rejected | No engine bug | Verdict | Notes |
|---|---|---|---|---|---|---|---|---|---|
| M4-retest | `shrink_into_helium_balloon_voyage` | request_story | Child B | ☐ | ☐ | ☐ | ☐ | ☐ |  |

**Expected:** PASS (N0 fix hides persona-letter when ElementCard renders, so Next button is reachable). If FAIL with a different defect, file as a new issue.

## Spec-clarity flags seeded by N5 — eye these during UAT

From N5 smoke gate (#175) primary-source review — UAT may surface real-world signal on these:

1. **`fact_a_true ≡ fun_fact` verbatim in 117/118 entries.** Step 4 reward repeats Step 3 correct choice. Intentional per plan §1 (reinforcement) but may read flat. Easy fix at N7-or-O if Child B finds it boring: rotate Step 4 to use `story_seed_hooks` instead.

2. **All element_microgames pick from the SAME 19 music-themed songs.** Plan §3 + §5 N5 promised element-themed song selection but it's not wired (issue #194 filed as Phase O followup). N5's xfail-strict tests lock the gap. UAT note: if Child B asks "why does it keep playing the same song?" — that's the wire gap, not a per-template defect.

3. **na-11 + ca-20 family-fork awkwardness.** These are the sole 3-5 family members for alkali_metal + alkaline_earth. The Step 2 fork is "find another alkali metal" but the correct answer is a story_seed_hook about sodium itself (falls back because no in-family peer at 3-5). Verified working but reads inconsistent with the question. Not a UAT row but worth checking if you propose either of these.

## Defects + verdict

| ID | Surface | Symptom | Severity | Fix path | Folded into |
|---|---|---|---|---|---|
| _(pending)_ |  |  |  |  |  |

**Phase N verdict:** ⏳ pending

## How to run

```powershell
# Backend (in one shell)
cd C:\Users\abero\dev\toybox
$env:TOYBOX_LAN_IP = "<your-lan-ip>"  # check via ipconfig
uv run python -m toybox.main --host 0.0.0.0 --port 8000

# Frontend (in another shell)
cd C:\Users\abero\dev\toybox\frontend
npm run dev

# Then on iPad: Safari -> http://<TOYBOX_LAN_IP>:4000/parent
# PIN-unlock, then for each row above: propose with the listed template_id (via
# the parent UI's template-pin field or by re-rolling until you get the target),
# approve, then have Child B play through on /child kiosk.
```

If you can't pin a specific template_id from the parent UI (UI doesn't expose it), the easiest fallback is to keep re-rolling Child B's "request_activity" intent until each target element_microgame surfaces. The pool currently has both M4 `meet_element_*` (118 entries) AND N4 `element_microgame_*` (118 entries) under the same intent, so expect to roll through some M4 templates between hits.

Mark each ☐ as the kiosk session unfolds, then update the verdict at the bottom.
