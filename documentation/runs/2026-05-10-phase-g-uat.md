# Phase G UAT — iPad kiosk smoke gate

**Date:** 2026-05-10
**Operator:** Abraham
**Device:** iPad PWA at `http://<lan-ip>:4000/child` (Add-to-Home-Screen)
**Backend:** master @ `1ea0a89` (Phase G G1+G2+G3+G4+G2.5 + 200-template soak + WS-origin diagnostic log)
**Result:** **PASS**

## Pass-criteria contract (from the G6 punch list)

| # | Activity | Verified |
|---|---|---|
| 1 | Linear regression — pre-Phase-G template runs end-to-end | ✅ NextStepButton renders, no choice buttons, advances to terminal |
| 2 | Branching: `boredom` — pick a path, observe ending; replay alternate path | ✅ Choice buttons render, advances on tap, reaches terminal; replay produces a different path |
| 3 | Branching: `request_play` | ✅ |
| 4 | Branching: `request_story` | ✅ |
| 5 | Branching: `request_activity` | ✅ |
| 6 | Multi-choice playthrough (template with 2+ choice points) — `chosen_label` recorded per choice point | ✅ |

**Eyeball checks during playthroughs:**
- ✅ Choice button labels fully rendered — no leftover `{toy}` / `{room}` placeholders
- ✅ Approve from parent → kiosk picks it up immediately (WS sync)
- ✅ No infinite "advancing..." spinner on choice tap
- ✅ Activity completes / "All done" screen renders at terminal

**DB spot-check** (operator-verified): `chosen_label` populated on choice-point rows matching the rendered label the kid saw; `choices_json` non-null on choice-bearing steps; row count matches playthrough length.

## Issues encountered + resolved during UAT

### iK-style issue: WS handshake 403 on iPad until `TOYBOX_LAN_IP` env var was set

Backend's WS Origin allow-list is loopback-only by default. Without `TOYBOX_LAN_IP` exported in the same PowerShell session that launches the backend, the iPad's `http://<lan-ip>:4000` Origin is rejected with HTTP 403 on every WS handshake.

**Symptom:** kiosk loads via HTTP, parent UI works, but parent-Approve doesn't sync to the iPad. Backend log shows:
```
INFO: 127.0.0.1:xxxxx - "WebSocket /ws" 403
INFO: connection rejected (403 Forbidden)
```

**Fix:**
```powershell
$env:TOYBOX_LAN_IP = "192.168.x.x"   # LAN IPv4 from ipconfig
uv run --extra image_gen python -m toybox.main --host 0.0.0.0 --port 8000
```

**Doc updates landed during UAT** (commits `869bb0d` + `1ea0a89`):
- README "Run on iPad" section now sets `TOYBOX_LAN_IP` in the same command that launches the backend
- `documentation/operator/ipad-setup.md` prereqs explain WHY the env var matters (allow-list computed from process env at request time)
- `documentation/operator/ipad-setup.md` troubleshooting matrix row title now names the symptom ("parent approve doesn't sync to kiosk") and the log signature, so a future operator greps the matrix and finds it
- WS server now logs `WARN: ws origin rejected: origin=... allow_list=... (set TOYBOX_LAN_IP env var to add http://<lan-ip>:4000)` on every 403 so the next mismatch (e.g. wrong IP, wrong port) is diagnosable from the log alone

## Phase G overall

Phase G ships with the full 6-step deliverable plus G2.5 (propose-UX fix) and the operator-driven content soak that took G5 from 4 templates to 200.

| Step | Issue | Commit | Outcome |
|---|---|---|---|
| G1 | #71 | `220582b` | Schema + Pydantic + graph validator (orphan/cycle/missing-target/etc) |
| G2 | #72 | `20b6375` (merge `1cf513d`) | Migrations 0007 + 0008, slot-fill persistence, lazy step insertion |
| G2.5 | (extends #72) | `a192d17` | Propose response renders full template plan for proposed/approved (parent dashboard preview UX restored) |
| G3 | #73 | `fa5e9db` | `/advance` with `choice_index`, edge resolution (4 rules), idempotency, response shape extension |
| G4 | #74 | `867e0b9` | ChoiceButton + StepCard branching, `Step N of 5` denominator dropped, sibling lock-out gating |
| G5 | #75 | `(4 merge commits)` | 200 branching templates (50× original scope) via 4-agent overnight soak; 0/200 validation failures; live in production via templates/branching/<intent>.json |
| G6 | #76 | _this run-doc_ | iPad UAT smoke gate PASS |

**Catalog total:** 225 templates (25 original linear + 200 branching).
**Test suite:** 1275 passing post-soak, all gates clean (ruff + mypy + tsc + eslint + vitest).
**Variety boost:** confirmed via seeded propose smoke — 40 seeds @ `intent=boredom` picked 26 distinct templates, 25 of them soak content.

Phase G is closed.
