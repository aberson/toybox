# Task State

**Task:** /build-phase --plan documentation/plan/phase-z-persona-voices-plan.md (Phase Z persona voices, goal-driven span Z1..Z7-prep; STOP before operator Z7/Z8/Z9)
**Status:** IN_PROGRESS — Z1 DONE (PASS iter 1/3, all 4 reviewers approved, merged + gates green on master); dispatching Z2 next
**Last written:** 2026-07-03T20:10:00Z
**Session SHA:** d2bca48 (Z1 checkpoint commit pending)

## Next Action
Checkpoint-commit Z1 (scoped add: 9 merged files + plan + current.md), close #3, remove worktree_z1, then /build-step Z2 (#4, sentence-boundary truncation, --reviewers code).
Resume if session lost: `/build-phase --plan documentation/plan/phase-z-persona-voices-plan.md --resume Z2`

## Completed
- [d2bca48] Pre-flight disposition (plan §8 prerequisite): ChoiceReadButton(+test) + StepCard read-aloud split + ReadMeButton export + launcher banner committed. Gates: tsc/eslint clean, vitest 817. NOTE: launch-toybox.ps1 banner belonged to the parallel uat-ui session — swept into this commit (benign, coherent, gates green); flag in final report.
- Baseline gates on master @ d2bca48: pytest 2671 passed / mypy clean / ruff check clean. Pre-existing `ruff format --check` drift on 100 files (ruff 0.15.12 floats via >=0.4) — OUT OF SCOPE, noted for housekeeping.
- Step Z1 (#3) PASS iter 1/3: voice_profile decoded-object wire-through on all 3 persona-envelope paths (random SELECT, pinned _hydrate_persona_meta_by_id ×3 propose flows, dispatcher full envelope); RewardStep DEFAULT_VOICE_PROFILE dedup + threading. Post-merge master gates: pytest 2680 (+9) / vitest 820 (+3) / mypy / ruff / tsc / eslint all green. 4 reviewers approved, 0 high/medium findings.

## WIP
**Current:** Step Z2 (#4): sentence-boundary-aware fallback truncation
**Approach:** Replace truncateAtWordBoundary (ReadMeButton.tsx) with sentence-aware truncation (last ./!/? at or below limit; word-boundary fallback for over-limit first sentence; keep … and limit=0/short passthroughs); update both callers (ReadMeButton, ChoiceReadButton) + tests incl. the 157-char "What does Miss Maple think?" regression.

## Dead Ends / Decisions (carried from Phase Z prep)
- iPad Web Speech voice identity is a DEAD END — server-rendered Kokoro clips are the path.
- Never enqueue TTS in the propose path; enqueue at approve/beat-insert/joke-insert/reward-resolve only.
- Kokoro CPU-only in-process; GPU flip = provider-seam config later, NOT scoped.

## Critical Gotchas
- **PARALLEL uat-ui session artifacts:** untracked `.claude/skills/uat-ui/evals/` — NEVER commit; never `git add -A` on master; scope every add.
- **CRLF flap on `frontend/src/shared/{errors,types}.ts`:** show M with ZERO content diff — never stage unless a step actually regenerates codegen (Z3 will).
- Worktree venv lacks image_gen extra → mypy shows 4 unused-type-ignore errors in image_gen/animate.py + 4 GPU tests skip there; both CLEAN on master full venv. Judge worktree gates accordingly for every Z step.
- WS timing tests (test_ws_toy_actions_topic, test_ws_heartbeat) flake under full-suite load; pass in isolation. Pre-existing.
- Z4 shares activities.py with Z1 — Z4 builds only AFTER Z1 merges (sequential order handles this).
- Wire-shape trap: voice_profile must be a DECODED object on the wire; kiosk typeof-number guard rejects raw JSON strings (persona-voice.ts:95-97).
- Baseline counts: pytest 2671 (master full venv), vitest 817 (pre-Z1).

## Key Files
- Phase Z plan: `documentation/plan/phase-z-persona-voices-plan.md` (umbrella #2; steps #3-#12)
- Z1 seams: `src/toybox/api/activities.py` (:1729 picker; pinned callers ~:2047/:2234/:2405), `src/toybox/main.py:863`, `frontend/src/child/components/RewardStep.tsx`
- Z4 hooks: activities.py post_approve :2932 (S2 pattern :3047), `_insert_adventure_beat` :4726, `_parent_insert_finish` :3695, `_insert_reward_step_as_current` :5346
- Kiosk speech seams: `frontend/src/child/{tts.ts,persona-voice.ts}`, `components/{ReadMeButton,ChoiceReadButton,JokeStep,StepCard,RewardStep}.tsx`, `sfx.ts` + `KioskPinPrompt.tsx:69`
