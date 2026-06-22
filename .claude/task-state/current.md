# Task State

**Task:** Phase Y (scene backdrops + per-child scene selection) — CODE COMPLETE
**Status:** /build-phase ran Y1-Y8 to completion. All 8 code steps Status: DONE; issues #265-266, #268-273 closed; umbrella #264. 8 checkpoint commits b53d794..e1025f7 (LOCAL, NOT pushed). Two operator steps remain in the Manual UAT bundle: M1 (Y2-run, #267 — GPU scene-library render) + M2 (Y9, #274 — iPad backdrop UAT). One Y7 regression caught + fixed in-phase (_FakePipe missing set_ip_adapter_scale). Full backend 2670 pass + 1 pre-existing WS-topic flake (passes isolated); frontend 802 pass.
**Last written:** 2026-06-22T09:30:00Z
**Session SHA:** e1025f7

## Next Action
Operator runs the Manual UAT bundle in `documentation/plan/phase-y-scene-backdrops-plan.md` § Manual UAT: **M1** (`uv run python scripts/batch_scenes.py` with the backend stopped → render + eyeball ~8-10 scene PNGs), then **M2** (iPad: backdrop renders behind step card, readable, cast in-style, Child A-vs-Child B scene differs). Then `/repo-update` to push + close umbrella #264. Push is pending operator approval (two phases' worth of local commits: investigation 6dcd80f, Phase Y plan b53d794 + 8 checkpoints).

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
