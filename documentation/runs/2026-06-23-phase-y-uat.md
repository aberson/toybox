# Phase Y — operator Manual UAT run (M1 + M2)

- **Date:** 2026-06-23
- **Phase:** Y (scene backdrops + per-child scene selection)
- **Code under test:** master `f878eb7` (Y1–Y8 merged); docs/checkpoints through `10b33c4`
- **Operator:** abero
- **Device (M2):** iPad on LAN, Safari → child kiosk
- **Verdict:** ✅ **PASS** (M1 + M2 both pass; no defects filed)

Phase Y's Manual UAT is **separate** from the bundled iPad UAT [#223](https://github.com/aberson/toybox/issues/223) (R/S/O/T/V/W/X) — it has its own two-step bundle (M1 render + M2 iPad) in the plan's `## Manual UAT` section.

## M1 (#267) — Render the scene library

`uv run python scripts/batch_scenes.py` with the backend stopped (avoids the dual-CUDA OOM — Phase U U3 lesson) → scene PNGs to `data/images/scenes/`.

| Check | Expected outcome | Result |
|---|---|---|
| Scene PNGs written | one `data/images/scenes/<id>.png` per `SCENE_IDS` member (~8–10) | ✅ PASS |
| Age-appropriateness | every scene is child-safe / age-appropriate (parent eyeball) | ✅ PASS |
| Style cohesion | scenes read as the SAME cartoon style as the toy sprites (no "ransom-note" mismatch) | ✅ PASS |

**M1 verdict:** ✅ PASS

## M2 (#274) — iPad backdrop UAT

Backend (loopback) + `npm run dev`; propose + approve for **Child A**, then **Child B**; child kiosk opened on the iPad.

| Check | Expected outcome | Result |
|---|---|---|
| Backdrop renders | a full-bleed scene image sits BEHIND the step card | ✅ PASS |
| Readability | body text stays readable over the scene (scrim + card opacity hold) | ✅ PASS |
| Cast in-style | the toy sprites look like they're IN the scene, not pasted on | ✅ PASS |
| Per-child differs | interest-selected scene differs between Child A (dance/stage) and Child B (lab/space) | ✅ PASS |

**M2 verdict:** ✅ PASS

## Defects + verdict

| ID | Surface | Symptom | Severity | Fix path |
|---|---|---|---|---|
| _(none)_ | — | — | — | — |

**Phase Y verdict:** ✅ **COMPLETE.**

## Close-out actions taken

- Umbrella [#264](https://github.com/aberson/toybox/issues/264) closed.
- Operator step issues [#267](https://github.com/aberson/toybox/issues/267) (M1) + [#274](https://github.com/aberson/toybox/issues/274) (M2) closed.
- [#271](https://github.com/aberson/toybox/issues/271) (Step Y6 — kiosk backdrop layer) closed **retroactively**: the code shipped at `f878eb7` and passed code review, but its completion comment + close were missed during the `/build-phase` run (last comment was "Step Y6 started"). The umbrella's CODE-COMPLETE comment had claimed #268–273 closed; #271 had slipped.
- Plan moved `documentation/plan/awaiting-uat/` → `documentation/plan/archive/` per the master-plan archive convention.
- master-plan Status table, README, and CLAUDE.md status surfaces updated to UAT-PASS.
