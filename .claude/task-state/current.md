# Task State

**Task:** Phase Z — persona voices (server-rendered Kokoro TTS + voice wire-through). Plan staged + synced; next is the automated build span.
**Status:** IN_PROGRESS — /plan-feature + /plan-expedite chain DONE 2026-07-03 (plan-review READY auto-fixed 14; plan-wrap READY auto-fixed 4; repo-sync minted umbrella #2 + steps #3-#12; plan Issue: lines backfilled). Plan: `documentation/plan/phase-z-persona-voices-plan.md`. Build NOT started.
**Last written:** 2026-07-03T17:50:00Z
**Session SHA:** b00c4db

## Next Action
1. **Operator pre-flight (BLOCKING for Z2/Z5):** disposition the uncommitted working-tree edits overlapping Phase Z targets — `frontend/src/child/components/ReadMeButton.tsx` + `StepCard.tsx`(+test) modified, `ChoiceReadButton.tsx`(+test) UNTRACKED (likely the parallel uat-ui session's; see Gotchas). Commit or revert BEFORE /build-phase — worktree builds branch from committed state.
2. Then run (same window):
   `/goal "Phase Z automated steps Z1-Z7-prep are all marked Status: DONE in documentation/plan/phase-z-persona-voices-plan.md (issues #3-#9 closed), and backend gates (uv run pytest / uv run mypy src / uv run ruff check .) plus frontend gates (npm run test / typecheck / lint) exit 0 — STOP before operator steps Z7/Z8/Z9 (issues #10-#12); those are an operator handoff, not part of this goal"`
   `/build-phase --plan documentation/plan/phase-z-persona-voices-plan.md`

## Completed (Phase Z prep — this arc)
- 2026-07-03: Voice survey (6-agent workflow) → findings memory `project_voice_survey_2026-07-03.md`. Root causes: voice_profile never SELECTed by `_pick_random_library_persona`; iPad Web Speech cannot do voice identity; truncation cut = spoken-text-limit 150 at word boundary (operator's "What does Miss" example = exactly 157 chars).
- 2026-07-03: /plan-feature conversation (4 operator decisions: surfaces = step bodies + jokes; casting = defaults + audition; clips always full text; Kokoro CPU in-process) → plan authored.
- 2026-07-03: /plan-expedite chain: plan-review autofix (Type: fields ×7, main.py trigger-path correction — it writes NO metadata["persona"] at all, StepCard mount :914-921, concrete Z4 hook lines, 2 risk rows), plan-wrap autofix (VoiceProfile shape table, clip metadata key names pinned: spoken_audio_url / spoken_audio_setup_url / spoken_audio_punchline_url, RTF+G2P glosses), repo-sync (umbrella #2, steps #3-#12, all bodies rich, --ui deliberately omitted per PIN-gate precedent).

## Completed (Phase Y — prior arc)
- Phase Y fully done 2026-06-23 (M1 #267 + M2 #274 PASS; run doc `documentation/runs/2026-06-23-phase-y-uat.md`; umbrella #264 closed). Master `f878eb7` → `fc84e02` (persona art) → `b00c4db` (go-public docs).

## Dead Ends / Decisions
- iPad Web Speech voice identity is a DEAD END (premium voices walled off; voiceURI unreliable; iOS-version regressions) — do not revisit; server-rendered clips are the path.
- Piper rejected (robotic prosody + fork went GPL-3.0); XTTS-v2/F5-TTS rejected (non-commercial weights, repo is public); Zonos rejected (16 GB VRAM).
- Kokoro runs CPU-only in-process (kokoro-onnx on core onnxruntime) — GPU flip is a provider-seam config change later, NOT scoped.
- Never enqueue TTS in the propose path (proposals speculative); enqueue at approve/beat-insert/joke-insert/reward-resolve only.
- Pre-render scene library ONLY; runtime serves static PNGs via existing mounts (Phase Y decision, still holds).

## Critical Gotchas
- **PARALLEL uat-ui session on master.** NEVER `git add -A`; scope every add. CRLF flap on `frontend/src/shared/{errors,types}.ts` — never stage. Untracked `.claude/skills/uat-ui/evals/` + modified `scripts/launch-toybox.ps1` belong to it — leave them.
- **Uncommitted Z2/Z5-target files** (ReadMeButton/StepCard modified, ChoiceReadButton untracked) — MUST be dispositioned before /build-phase; recorded in plan §8 + umbrella #2 pre-flight note.
- Z4 shares `src/toybox/api/activities.py` with Z1 — build Z4 AFTER Z1 merges (declared in issues #6/#3).
- Wire-shape trap: voice_profile must be spliced as a DECODED object; kiosk typeof-number guard silently rejects raw JSON strings (persona-voice.ts:95-97).
- `ruff format` debt in generator.py/models.py is PRE-EXISTING — scope edits, never format whole files.
- WS-topic timing tests (test_ws_toy_actions_topic, test_ws_heartbeat) flake under full-suite load; pass in isolation. Pre-existing.

## Key Files
- Phase Z plan: `documentation/plan/phase-z-persona-voices-plan.md` (umbrella #2; steps #3-#12)
- Survey memory: `~/.claude/projects/c--Users-abero-dev-toybox/memory/project_voice_survey_2026-07-03.md`
- Z1 seams: `src/toybox/api/activities.py` (:1729 picker; pinned :2044/:2231/:2402), `src/toybox/main.py:863`, `frontend/src/child/components/RewardStep.tsx:52`
- Z4 hooks: activities.py post_approve :2932 (S2 pattern :3047), `_insert_adventure_beat` :4726, `_parent_insert_finish` :3695, `_insert_reward_step_as_current` :5346
- Kiosk speech seams: `frontend/src/child/{tts.ts,persona-voice.ts}`, `components/{ReadMeButton,ChoiceReadButton,JokeStep,StepCard,RewardStep}.tsx`, `sfx.ts` + `KioskPinPrompt.tsx:69`
