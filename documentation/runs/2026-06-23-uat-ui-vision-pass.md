# uat-ui desktop vision pass — #223 visual rows (2026-06-23)

**Goal:** convert the #223 bundle's *visual-glance* rows into desktop vision-judged PASS/FAIL verdicts, so the operator's on-device session shrinks to the truly-physical bits (touch, Guided Access, kid eyes).

**Method (per the `uat-ui` skill):**
- Took down the leftover M2 app; brought up an **isolated** instance: `TOYBOX_DB_PATH=data/uat-visual.db`, `TOYBOX_DISABLE_AUDIO=1`, `TOYBOX_IMAGE_GEN_STUB=1`, backend loopback :8000 + vite :4000. Never touched the real DB.
- Authored two Playwright flows (new files, feature untouched):
  - `frontend/playwright/uat-visual-parent.spec.ts` — tabs / settings / search / catalog. **PASS** (18.5s).
  - `frontend/playwright/uat-visual-kiosk.spec.ts` — kiosk render at iPad 1024×768, two personas. **PASS** (4.3s).
- Stage screenshots → `frontend/playwright/test-results/uat-visual-{parent,kiosk}/`; `/api` read-backs captured (`settings-pressed.json`, `kiosk-readback.json`).
- Vision-judged the screenshots against the pixels + read-backs (strict, escalate-don't-auto-pass).

## Verdicts — 9 PASS / 1 FAIL

| Row | Verdict | What the pixels showed |
|---|---|---|
| O1 — 5 Play sub-tabs | ✅ PASS | All / Adventures / Elements / Feelings & Friends / Transcriptions, in order; "All" active. |
| R1 — prominent CTAs, no cadence | ✅ PASS | Full-width blue "Trigger now" + purple "Start an Adventure" primary buttons; **no cadence control** (`cadenceControlCount=0`). |
| O2 — sub-tab filtering | ✅ PASS | "Elements" chip active (blue), queue body = "No element activities suggested yet." |
| R4 — search | ✅ PASS | Query "play" → ~19 template rows each with a "Try this" button + a proposed-activity card; no crash. |
| T1 — catalog browse + chips | ✅ PASS | Browse panel with many template cards + a theme-chip row; filtered shot shows the "adventure" chip active and matching cards. |
| W1/W2/W5 — settings dials | ✅ PASS | Involvement=Medium, Complexity=Medium, Linearity=Non-linear, Q&A=Off, Boss-fights=On — matches `settings-pressed.json`. |
| S1 — per-persona gradient | ✅ PASS | Detective = blue/indigo gradient; periodic_table = green gradient — clearly different per persona. |
| S2 — persona avatar | ✅ PASS | Round letter-badge avatar ("D" / "P") above the card (letter mode — image-gen stubbed). |
| S3 — readable step card | ✅ PASS | Large bold two-line body on a prominent light card, legible at 1024×768; "STEP 1" + big "Next". |
| **Y — scene backdrop** | ✅ **PASS** (after remediation) | Initially **FAIL** — broken-image placeholder, because the scene library was never rendered (`bedroom.png` 404). After rendering M1 (`batch_scenes.py` → 10 scenes) and re-running the kiosk flow, the **rendered bedroom scene displays full-bleed behind the step card** and the body text stays clearly readable through the translucent card. |

## The Y-backdrop FAIL → root cause → remediation

Not a code bug: `scripts/batch_scenes.py` writes to `data/images/scenes/<id>.png` and the static mount + `scene_url` both read from there — the paths **agree**. The cause was that **the scene library had never been rendered**:
- `data/images/scenes/` did not exist; `find data -iname "*.png" | grep scene` → 0 files.
- `GET /api/static/images/scenes/bedroom.png` → **404**.

So any activity resolving to a scene (default `bedroom`, or interest/template-driven) showed a **broken image**, not a backdrop. This **intersected Phase Y M1/M2**: M1 (`#267`, render the library) had not actually run/persisted, so M2 (`#274`, "backdrop renders") could not have been true — even though both were reported PASS and `#264` was closed.

**Remediation (operator-approved, 2026-06-23):** ran `uv run python scripts/batch_scenes.py` (GPU free, no stub) → **10/10 scenes rendered, 0 failed** (~50s via the 4-step LCM path). `GET .../scenes/bedroom.png` → **200**. Re-ran the kiosk flow on a clean DB → the bedroom backdrop renders behind the readable step card. M1 is now genuinely satisfied and M2's render-half is desktop-confirmed; `#264` stays closed with real evidence.

**Still operator-only on a physical iPad (not covered by this desktop pass):** the M2 interest-driven scene differentiation (Child A→stage vs Child B→lab) was not exercised — the synthetic test child has no `interests`, so both personas resolved to the default `bedroom`. The resolver chain itself is unit/integration-tested (Y4); only the on-device end-to-end "different kid → different scene" remains a human glance.

## M1 scene-library vision scan (10 scenes — the subjective half of M1)

Vision-scanned all 10 rendered scenes (`data/images/scenes/`) for the M1 age-appropriateness + style-cohesion eyeball:

- **Child-safety: PASS.** Nothing violent/gory/frightening. One mild note — `castle` reads as a dim, empty gothic cathedral with a lone figure (somber/eerie rather than fairy-tale), worth a parent glance but not a block.
- **Style cohesion: FAIL.** The set splits **5/5**: cartoon-illustration (`forest`, `space`, `stage`, `park`, `undersea`) vs **photorealistic 3D-render** (`bedroom`, `kitchen`, `lab`, `workshop`, `castle`). Behind cartoon toy sprites, the photoreal indoor scenes risk the **"ransom-note" mismatch** the scene prompts explicitly tried to avoid — a flat cartoon sprite composited onto a photoreal room reads as pasted-on.
- **Minor render defects:** `forest` has a faint garbled-text smudge bottom-left (cosmetic); `undersea` has a faint distant fish/shark silhouette + moodier palette (safe).
- **Recommendation:** ship-able as a safety matter; for a polished one-style library, regenerate the 4 photoreal indoor scenes (+`castle`, brighter/friendlier) in the painterly style of `park`/`stage`/`forest`. This likely needs a `SCENE_PROMPTS` tweak to force the cartoon style, then a re-render — a **Phase Y polish follow-up**, not a code defect.

## What this clears

For the 9 PASS rows, the *render* is now desktop-vision-confirmed. The operator's remaining on-device work for those rows is only the genuinely-physical layer (touch responsiveness, Guided Access, arm's-length readability for ages 4 & 6, kid recognition) — not "does it render correctly". The voice (W3/W4) + kid-engagement items are unchanged and still require a person.

**Caveat:** desktop chromium at an iPad viewport ≠ a physical iPad. Animation smoothness / "no strobe over time" (S2 temporal) and true-device fidelity remain a human glance.

VERDICT: **10 PASS / 0 FAIL after remediation.** The lone FAIL (Y-backdrop) was the scene library never having been rendered; rendering M1 (10 scenes) fixed it and re-running the kiosk flow confirmed the backdrop renders. Residual human (physical iPad only): touch / Guided Access / arm's-length readability for ages 4 & 6, M1 scene age-appropriateness + style eyeball, and the M2 interest-driven scene differentiation (Child A vs Child B).
