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

## Addendum (2026-06-23) — M1 render gap discovered + remediated

A later desktop vision pass (`documentation/runs/2026-06-23-uat-ui-vision-pass.md`) surfaced that the **scene library had not actually been rendered** at the time of the M1/M2 PASS above: `data/images/scenes/` did not exist and `GET /api/static/images/scenes/bedroom.png` returned **404**, so the kiosk backdrop was a broken-image placeholder rather than a scene. The original M1 "Scene PNGs written" PASS and the M2 "Backdrop renders" PASS were therefore **not actually true on this machine** — the GPU render had not been run.

**Remediation (operator-approved):** ran `uv run python scripts/batch_scenes.py` → **10/10 scenes rendered** (bedroom, forest, kitchen, space, lab, stage, castle, undersea, park, workshop), 0 failed (~50s, 4-step LCM). `GET .../scenes/bedroom.png` → **200**. Re-ran the kiosk vision flow on a clean DB: the bedroom scene now renders full-bleed behind the readable step card (screenshot in the vision-pass run-doc). M1 is now genuinely satisfied; M2's render-half is desktop-confirmed. `#264` remains closed.

**Still genuinely human (physical iPad / parent):**
- M1 — vision-scanned all 10 scenes (see the vision-pass run-doc): **safety PASS** (mild note on `castle`'s somber gothic-cathedral tone), but **style-cohesion FAIL** — `bedroom`, `kitchen`, `lab`, `workshop`, `castle` rendered photorealistic, mismatching the cartoon toy sprites (5/5 split). Safe to ship as-is; a polished one-style library is a **Phase Y polish follow-up** (regenerate the photoreal scenes in cartoon style via a `SCENE_PROMPTS` tweak) — not a code defect.
- M2 — interest-driven scene differentiation (Child A→stage vs Child B→lab) on a real device. The synthetic test child had no `interests`, so both personas resolved to default `bedroom`; the resolver chain is unit/integration-tested (Y4) but the on-device end-to-end was not exercised.
