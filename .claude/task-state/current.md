# Task State

**Task:** Phase Y — COMPLETE (code + Manual UAT M1/M2 PASS). Awaiting operator direction on the next work item.
**Status:** Phase Y fully done 2026-06-23. M1 (#267 scene render) + M2 (#274 iPad backdrop) both PASS — run doc `documentation/runs/2026-06-23-phase-y-uat.md`. Umbrella #264 closed; #267 + #274 closed; #271 (Y6 code-step whose close was missed during /build-phase) closed retroactively. Plan moved `awaiting-uat/` → `archive/`. master + origin in sync.
**Last written:** 2026-06-23T19:29:23Z
**Session SHA:** (set by close-out commit)

## Next Action
Operator to choose the next work item. Recommended options, grounded in open GitHub issues:
1. **Build Phase E** (local model + tool-loop) — the next BUILDABLE code phase. `documentation/plan/phase-e.md`; master-plan says "Step 27 free to resume" (≥50-SFT-row gate cut 2026-05-21). Autonomous via `/plan-expedite --plan documentation/plan/phase-e.md` → `/build-phase`.
2. **Fix open production follow-ups** (both code, autonomous): #245 (OFFLINE `listening_mode` privacy bypassed by background Claude calls — S2 animator + judge; privacy gap on a family-private product) and #244 (R3 Q&A gate UI-enforced, not credential-enforced, until Phase D Step 20 child-token pairing).
3. **Clear the awaiting-UAT backlog** — bundle #223 (R/S/O/T/V/W/X iPad UAT; needs operator on iPad + `room_classifier --download`). Separately: Phase P #189/#191 (hardware-bound render + UAT), Phase Q #202/#205 (operator generators + Coqui MP3 render).

## Completed (Phase Y — this arc)
- Investigation set → /plan-feature → /plan-expedite (#264–274) → /build-phase Y1–Y8 (8 scoped checkpoint commits, each gates-green + code-review PASS) → /repo-update → Manual UAT M1/M2 PASS → close-out.
- Code under test: master `f878eb7`. backend 2,670 pytest + frontend 802 vitest; mypy/ruff clean (modulo pre-existing generator.py/models.py ruff-format debt).

## Dead Ends / Decisions
- Pre-render scene library ONLY; runtime serves static PNGs via existing `/api/static/images` mount. No new mount.
- `scene_id` is a first-class `activities` column (migration 0030), not a `slot_fills_json` key.
- `ruff format` swept PRE-EXISTING debt into generator.py + models.py — do NOT `ruff format` those whole files (HEAD already fails `ruff format --check` there); scope edits.

## Critical Gotchas
- **PARALLEL uat-ui session on master.** NEVER `git add -A`; scope every add. CRLF flap on `frontend/src/shared/{errors,types}.ts` — never stage. Untracked `.claude/skills/uat-ui/evals/`, `documentation/runs/2026-06-21-room-import-uat.md`, `frontend/playwright/room-import.spec.ts`, `.plan-expedite-state.phase-x-done` belong to the parallel session — leave them.
- WS-topic timing tests (test_ws_toy_actions_topic, test_ws_heartbeat) flake under full-suite load; pass in isolation. Pre-existing, not Phase Y.

## Key Files
- Phase Y plan (archived): `documentation/plan/archive/phase-y-scene-backdrops-plan.md`
- UAT run doc: `documentation/runs/2026-06-23-phase-y-uat.md`
- Scene seams: `src/toybox/activities/{scene_catalog,content_resolver,_validator,generator,models}.py`, `src/toybox/image_gen/{pipeline,models}.py`, `src/toybox/api/activities.py`, `scripts/batch_scenes.py`, migration `0030_activities_scene_id.sql`, `frontend/src/child/App.tsx`
