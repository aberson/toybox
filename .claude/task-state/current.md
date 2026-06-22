# Task State

**Task:** Phase Y Manual UAT — operator runs M1 (render) then M2 (iPad backdrop)
**Status:** Phase Y code COMPLETE + PUSHED at master `1ed45ac` (origin/master up to date). /repo-update done: README + master-plan + CLAUDE.md + memory updated; plan moved to `plan/awaiting-uat/`; umbrella #264 has a code-complete summary comment and stays OPEN until M1/M2 pass. Only the two operator UAT steps remain. backend 2670 + frontend 802 green.
**Last written:** 2026-06-22T10:00:00Z
**Session SHA:** 1ed45ac

## Next Action
Operator runs the Manual UAT bundle in `documentation/plan/awaiting-uat/phase-y-scene-backdrops-plan.md` § Manual UAT, in order:
- **M1 (#267)** — stop the backend, then `uv run python scripts/batch_scenes.py` → renders ~8-10 scene PNGs to `data/images/scenes/`; parent-eyeball each (age-appropriate + style-cohesive with sprites). `--dry-run` previews.
- **M2 (#274)** — bring up backend (loopback) + `cd frontend; npm run dev`; propose+approve for Child A then Child B; open the child kiosk on iPad; confirm backdrop renders behind the step card, text readable, cast in-scene, and the interest-selected scene differs (Child A→stage, Child B→lab).
On PASS: write `documentation/runs/2026-06-22-phase-y-uat.md` and close umbrella #264.

## Completed (this session)
- Investigation set (17 files + README) committed 6dcd80f (local).
- /plan-feature → Phase Y plan; /plan-expedite (review+wrap READY, repo-sync #264-274).
- /build-phase Y1-Y8: scene_catalog (Y1) → generate_scene + batch_scenes CLI (Y2) → template scene_id + validator (Y3) → resolver + interests activation (Y4) → migration 0030 + wire scene_url + codegen (Y5) → kiosk backdrop layer (Y6) → IPA-scale override infra (Y7) → smoke gate (Y8). Each: gates green + code-review PASS + scoped checkpoint commit.

## Dead Ends / Decisions
- Pre-render scene library ONLY; runtime serves static PNGs via /api/static/images (covers data/images/scenes/). No new mount.
- scene_id is a first-class activities column (migration 0030), not a slot_fills_json reserved key.
- ruff format swept PRE-EXISTING debt into generator.py + models.py — reverted both to keep diffs scoped (HEAD already fails `ruff format --check` on those files; not mine to fix).
- Y6 kiosk uses --reviewers code (PIN-gated); Y9 iPad UAT is the visual gate.

## Critical Gotchas
- **PARALLEL SESSION on master** (uat-ui). Never `git add -A`; scope every add. CRLF flap on frontend/src/shared/{errors,types}.ts — never stage (Y5 staged types.ts ONLY for the real scene_url change; errors.ts left alone).
- WS-topic timing tests (test_ws_toy_actions_topic, test_ws_heartbeat) are flaky under full-suite load — pass in isolation. Pre-existing, not Phase Y.
- 10 LOCAL commits unpushed (6dcd80f + b53d794 + 8 checkpoints). Push only on operator OK.

## Key Files
- Phase Y plan + Manual UAT: `documentation/plan/phase-y-scene-backdrops-plan.md`
- Scene seams: `src/toybox/activities/{scene_catalog,content_resolver,_validator,generator,models}.py`, `src/toybox/image_gen/{pipeline,models}.py`, `src/toybox/api/activities.py`, `scripts/batch_scenes.py`, migration `0030_activities_scene_id.sql`, `frontend/src/child/App.tsx`
