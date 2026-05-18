# Phase M — operator iPad UAT run

- **Date:** 2026-05-18
- **Phase:** M (Periodic Table Professor + SEL content depth)
- **Master at start of UAT:** `ad5c5f7` (M1-M13 autonomous block + M2b 118 element sprites + Pillow text overlay redesign)
- **Operator:** abero
- **Device:** iPad on LAN, Safari → `http://<TOYBOX_LAN_IP>:4000/parent` + `/child`
- **Verdict:** _(filled at end of UAT)_

## Pre-flight

| Check | Status |
|---|---|
| Backend started (`uv run python -m toybox.main --host 0.0.0.0 --port 8000`) | ☐ |
| Frontend started with LAN bind (`cd frontend; npm run dev`) | ☐ |
| iPad → PIN-unlocked parent app | ☐ |
| `data/songs/audio/*.mp3` rendered via M7b (75 entries: 50 prior + 25 element-themed) | ☐ |
| `data/images/elements/*.png` rendered via M2b (118 sprites local; 14 in git) | ☑ — committed at `ad5c5f7` |
| M13 smoke gate green | ☑ — committed at `768ad1d` |

## Walkthrough results — 12-activity matrix

Per `phase-m-plan.md` § M14: 4 Track 1 (Periodic Table) for Child B (4yo) + 8 Track 2 (SEL) for Child A (6yo). **Quality bar per activity:** (a) sprite/card renders without error AND (b) kid engages for ≥50% of intended steps (parent estimate) AND (c) kid does not actively reject ("I don't want this") AND (d) no engine bug (404, validator error, blank step). Pass = a+b+c+d.

### Track 1 — Periodic Table (for Child B, 4yo)

| # | Template id | Intent | Kid | Renders | Engaged | Not rejected | No engine bug | Verdict | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `meet_element_au-79` | request_activity | Child B | ☐ | ☐ | ☐ | ☐ | ☐ | Familiar element (Gold); checks element sprite + name pronunciation + ElementCard render |
| 2 | `meet_element_h-1` | request_activity | Child B | ☐ | ☐ | ☐ | ☐ | ☐ | Second Meet-an-Element; verifies element-themed song reward wires (auto-song ending should pick a Hydrogen song from M7a corpus) |
| 3 | `noble_gas_party_floaters` | request_play | Child B | ☐ | ☐ | ☐ | ☐ | ☐ | Element-family pretend-play (M5); 4-8 steps, 2-4 fork choices; verifies family-name slug consistency in narration |
| 4 | `shrink_into_helium_balloon_voyage` | request_story | Child B | ☐ | ☐ | ☐ | ☐ | ☐ | Shrink-down journey (M6); verifies M6 in-line `{guide_mentor}` template substitution post-Iridia-leak fix (commit `c93f42c`) |

### Track 2 — SEL (for Child A, 6yo early-reader)

| # | Template id | Intent | Kid | Renders | Engaged | Not rejected | No engine bug | Verdict | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 5 | `feelings_lost_blanket` | request_story | Child A | ☐ | ☐ | ☐ | ☐ | ☐ | Feelings-naming (M9); verifies Child A recognizes the feeling cluster on the chosen fork |
| 6 | `feelings_block_tower_falls` | request_story | Child A | ☐ | ☐ | ☐ | ☐ | ☐ | Feelings-naming (M9); second sample — different fork structure |
| 7 | `perspective_toy_taken` | request_play | Child A | ☐ | ☐ | ☐ | ☐ | ☐ | Perspective-taking (M10); two-act structure; verifies frenemy role soft-fallback works on toy pool without a frenemy-tagged toy (per plan §8) |
| 8 | `perspective_last_cookie` | request_play | Child A | ☐ | ☐ | ☐ | ☐ | ☐ | Perspective-taking (M10); second sample |
| 9 | `conflict_pick_book` | request_activity | Child A | ☐ | ☐ | ☐ | ☐ | ☐ | Conflict-resolution (M11); verifies frenemy-as-slot-key (slot label, not adversarial framing) works as designed |
| 10 | `conflict_pick_snack` | request_activity | Child A | ☐ | ☐ | ☐ | ☐ | ☐ | Conflict-resolution (M11); second sample |
| 11 | `repair_forgot_invite` | request_play | Child A | ☐ | ☐ | ☐ | ☐ | ☐ | Friendship-repair (M12); rupture-and-repair shape; verifies 3 × 2-step recovery fork structure |
| 12 | `repair_block_tower` | request_play | Child A | ☐ | ☐ | ☐ | ☐ | ☐ | Friendship-repair (M12); second sample |

## Defects

_(filled at end of UAT; one section per defect, mirror Phase K format)_

## Observations

_(free-form operator notes captured during UAT; engagement patterns, what surprised, what to follow up on)_

## Operator workflow

1. Pre-flight: tick all six pre-flight rows. Backend + frontend bind on LAN; iPad on the same WiFi.
2. For each row in the 12-activity matrix:
   - Open the parent app on the iPad → propose a new activity → wait until the template id matches the row's `Template id` (re-roll as needed; templates are seed-picked).
   - Approve the activity → hand the iPad to the named kid → observe.
   - Tick the four pass-criteria cells; write a one-line note in the Notes column if anything is off.
   - Mark Verdict = ☑ if all four ticks; ☐ + defect note if any fail.
3. After all 12 rows: file follow-up GitHub issues for any failures (non-blocking per Phase K precedent; defects added under `## Defects` above).
4. Set the top-of-doc `Verdict:` line and commit this run doc.

## How to find a specific template by id (when it doesn't surface from auto-propose)

Templates are picked per intent + bucket + theme + role coverage. To force a specific template, use the propose endpoint's debug flag (or wait through 5-10 re-rolls — proposal seeds vary). Operator manual override path:

```powershell
# Force-propose a specific template against a kid:
$pin = Read-Host -AsSecureString "Parent PIN"
$body = @{ child_id = "<rocket_or_ama_uuid>"; template_id = "meet_element_au-79" } | ConvertTo-Json
# (route + auth shape per documentation/plan/api.md propose endpoint)
```

If re-rolls don't surface a target template within ~10 attempts, the bucket or role-cast may be filtering it out — note that in the row's Notes and proceed.
