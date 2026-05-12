# Phase iPad-Kiosk UAT — 2026-05-09

Verification half of [Phase iPad-Kiosk Step iK5](../plan/archive/phase-ipad-kiosk.md#step-ik5-operator-ipad-setup-doc--visual-verification). Tests the kiosk-on-iPad install + run procedure documented in [`../operator/ipad-setup.md`](../operator/ipad-setup.md), using the 14-row M_iK manual table from the same plan section.

## Environment

| Field | Value |
|---|---|
| Date | 2026-05-09 |
| iPadOS version | 17.7.10 (≥16.4 — iK3 Wake Lock active, no Auto-Lock=Never fallback needed) |
| Home Wi-Fi SSID | <home-wifi-ssid> |
| Home machine LAN IP | <lan-ip> |
| Kiosk URL | `http://<lan-ip>:4000/child` |
| Backend command | `uv run --extra image_gen python -m toybox.main --host 0.0.0.0 --port 8000` |
| Frontend command | `cd frontend; npm run dev -- --host 0.0.0.0` |
| Operator | <operator> |
| Master HEAD at start | `4ab383d docs(ipad-kiosk): operator iPad setup procedure (iK5 doc half)` |

## Results — M_iK 14-row table

| # | Check | Expected | Result | Notes |
|---|---|---|---|---|
| 1 | Settings → General → About → Software Version | Record iPadOS version | PASS | iPadOS 17.7.10 — Wake Lock supported |
| 2 | Confirm iPad Wi-Fi SSID matches home machine SSID | Same SSID required | PASS | Both on "<home-wifi-ssid>" |
| 3 | Open `http://<lan-ip>:4000/child` in Safari | PIN prompt renders full-screen, landscape, gradient extends to all corners (iK2) | PENDING | |
| 4 | Enter PIN | Activity panel appears, audio unlocks silently (iK4) | PENDING | |
| 5 | Trigger activity from parent UI on home machine | Kiosk shows persona avatar + step 1 within ~1 sec | PASS (after backend restart with `TOYBOX_LAN_IP`) | First attempt FAILED — kiosk stuck at "Waiting for play to start..." because backend was started without `TOYBOX_LAN_IP=<lan-ip>`, so the WS upgrade from `http://<lan-ip>:4000` was 403'd by the Origin allow-list at `src/toybox/ws/server.py:380`. Restarting backend with the env var set + Safari refresh + re-PIN + re-dispatch produced expected behavior on the second attempt. See A2. |
| 6 | Tap Next | Transition SFX plays (iK4 unlock confirmed), step 2 renders | SOFT-PASS (visible advance OK; audio N/A) | Step 2 rendered immediately on Next-tap (visible kiosk advance works). NO transition SFX played, but this is a pre-existing kiosk-content gap, NOT an iK4 regression: `frontend/public/sfx/` contains only `.gitkeep` — `transition.wav` and `success.wav` were never shipped. Per `frontend/src/child/sfx.ts:102` `preloadSfx` silently marks the asset dead on 404, so the kiosk has never made sound at all. iK4 unlock cannot be verified against missing assets, but the iK4 code is forward-compatible — unlockAudio() will prime the audio context correctly once the SFX files are shipped. See A3. |
| 7 | Pause activity from parent for ≥3 minutes | Kiosk screen stays awake (iK3 Wake Lock); auto-lock does NOT fire | PENDING | |
| 8 | Resume + advance to completion | "All done!" screen renders, success SFX plays | PENDING | |
| 9 | Pull down Control Center → close → reopen kiosk tab | WS reconnects (existing behavior); Wake Lock re-acquires (iK3) | PENDING | |
| 10 | Toggle iPad Wi-Fi off → on | WS reconnects within ~5 sec; activity state resyncs (existing Phase A behavior) | PENDING | |
| 11 | Tap Share → Add to Home Screen → "toybox" → Add | Icon appears on iPad home screen with toybox glyph (iK1) | PENDING | |
| 12 | Tap home-screen icon | Kiosk launches full-screen, NO Safari URL bar / chrome (iK1 standalone mode) | PENDING | |
| 13 | Rotate iPad to portrait | Kiosk stays landscape (iK1 orientation lock — only in standalone-launched mode) | PENDING | |
| 14 | Settings → Accessibility → Guided Access → enable + lock | Triple-click locks iPad to kiosk; child cannot swipe to other apps | PENDING | |

## Anomalies / follow-ups

(filled in as walk-through progresses)

- **A1 [doc bug, found pre-UAT]:** `documentation/operator/ipad-setup.md` prereq #2 PowerShell block omits `--extra image_gen`. The bare `uv run python -m toybox.main` triggers an implicit uv sync that silently uninstalls torch/diffusers/rembg, breaking the image_gen capability gate for subsequent sessions. Fix in close-out commit: change the prereq #2 fence to use `uv run --extra image_gen python -m toybox.main --host 0.0.0.0 --port 8000`. The bug-reviewer in build-step missed this because the bare command works for the kiosk-only path; the silent side-effect on image_gen wasn't in scope of the four reviewers.

- **A2 [doc gap, found at row 5]:** `documentation/operator/ipad-setup.md` does not name the `TOYBOX_LAN_IP` env var that adds the iPad's `http://<lan-ip>:4000` origin to the WS upgrade allow-list at `src/toybox/ws/server.py:93-99`. Without it, kiosk WS upgrade returns 403, kiosk sits at `activity === null` forever, and the operator-facing symptom is "Waiting for play to start..." with no audible failure. The doc *links* to `documentation/plan/how-to-run.md` which does cover `TOYBOX_LAN_IP` (line 70 + env-var table line 107), but the indirection is fragile — an operator following just the iPad doc end-to-end without clicking the prereq link will miss the env var. Fix in close-out commit: inline `TOYBOX_LAN_IP` into prereq #2 (or add a new prereq step between current #2 and #3). The bug-reviewer caught the WS-Origin troubleshooting row but didn't notice the doc never tells the operator how to configure the allow-list. Found because I gave a condensed startup recipe at the top of this UAT session that ALSO omitted `TOYBOX_LAN_IP` — the symptom surfaced at row 5.

- **A4 [product gap, discovered during real-iPad play between rows 6 and 7]:** Kids on the kiosk tap the big Next button prematurely; parent has no remediation other than `End` (terminal). Need a "step back" control in the parent UI that decrements the kiosk's current step. Scoped + filed as #69 with full backend/frontend/test surface and rejection-path matrix. Not in iK5 scope (this is a new product feature, not a kiosk-on-iPad install/run gap), but discovered because UAT involved real kid play, which exposed the gap. Decision pending: build #69 immediately after iK5 close-out, or queue.

- **A3 [pre-existing kiosk-content gap, surfaced at row 6]:** SFX assets (`transition.wav`, `success.wav`) have never been shipped. `frontend/public/sfx/` contains only `.gitkeep`. The kiosk's `preloadSfx` (`frontend/src/child/sfx.ts:102`) silently marks the asset dead on 404, so the kiosk renders correctly but never makes sound. Phase D plan (`documentation/plan/phase-d.md:58-59`) names these as royalty-free assets to source (e.g. freesound.org CC0) but the work was never executed. NOT an iK4 regression — iK4's `unlockAudio()` plumbing is forward-compatible, it just has nothing to unlock today. Disposition: rows 6 and 8 (success SFX) marked SOFT-PASS — visible-kiosk gates pass, audio gates noted as N/A. Recommended follow-up: file a separate issue for "ship transition.wav + success.wav under frontend/public/sfx/" (NOT in iK5 scope). Phase iPad-Kiosk umbrella claim that "the frontend already ships everything a kiosk needs ... audio SFX with silent fallback" (`phase-ipad-kiosk.md:9`) is misleading — silent fallback works, but the SFX themselves have never made sound; consider rewording when this audit closes.

## Verdict

PENDING — verdict written after all 14 rows have a result.
