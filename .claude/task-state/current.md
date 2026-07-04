# Task State

**Task:** /build-phase --plan documentation/plan/awaiting-uat/phase-z-persona-voices-plan.md (Phase Z persona voices, goal-driven span Z1..Z7-prep; STOP before operator Z7/Z8/Z9)
**Status:** COMPLETE — all 7 automated steps Z1..Z7-prep DONE, issues #3-#9 closed; final master gates green; operator handoff = plan `## Manual UAT` M1(Z7 #10) → M2(Z8 #11) → M3(Z9 #12)
**Last written:** 2026-07-03T23:50:00Z
**Session SHA:** Z7-prep checkpoint (after 330fc9a)

## Next Action
Operator: run Manual UAT M1 (voice audition, #10) per the plan's `## Manual UAT` section — `uv sync --extra tts`, `python -m toybox.tts --download`, `scripts/batch_tts_audition.py`, listen + sign off casting. Then M2 (real-engine smoke, #11), then M3 (iPad UAT, #12).
After UAT: `/repo-update` to update docs/README, commit posterity, and push (7 local checkpoint commits are unpushed).

## Completed
- [d2bca48] Pre-flight disposition (plan §8 prerequisite): ChoiceReadButton(+test) + StepCard read-aloud split + ReadMeButton export + launcher banner committed. Gates: tsc/eslint clean, vitest 817. NOTE: launch-toybox.ps1 banner belonged to the parallel uat-ui session — swept into this commit (benign, coherent, gates green); flag in final report.
- Baseline gates on master @ d2bca48: pytest 2671 passed / mypy clean / ruff check clean. Pre-existing `ruff format --check` drift on 100 files (ruff 0.15.12 floats via >=0.4) — OUT OF SCOPE, noted for housekeeping.
- Step Z1 (#3) PASS iter 1/3: voice_profile decoded-object wire-through on all 3 persona-envelope paths (random SELECT, pinned _hydrate_persona_meta_by_id ×3 propose flows, dispatcher full envelope); RewardStep DEFAULT_VOICE_PROFILE dedup + threading. Post-merge master gates: pytest 2680 (+9) / vitest 820 (+3) / mypy / ruff / tsc / eslint all green. 4 reviewers approved, 0 high/medium findings. Checkpoint cfe2cba, #3 closed.
- Step Z2 (#4) PASS iter 2/3: truncateSpokenText sentence-boundary truncation; 157-char operator regression pinned; vitest 836 (+16). Checkpoint 98d3629, #4 closed.
- Step Z3 (#5) PASS iter 2/3: toybox.tts substrate (engine/stub/probe/--download), neural_voice schema + castings + codegen, wire-suite extension. pytest 2726 (+46). Checkpoint 1810c3e, #5 closed.
- Step Z4 (#6) PASS iter 2/3: tts/cache.py + tts/worker.py + /api/static/tts mount + 6 producer enqueue/stamp sites (approve S2-slot, lazy next-step, adventure beat, interjection joke, reward joke, preview derivation). Wire keys: spoken_audio_url, spoken_audio_setup_url/punchline_url, spoken_choice_audio_urls (index-aligned), spoken_question_audio_url; songs/non-joke rewards none; propose enqueues nothing. pytest 2766 on master (+40); grep-gate pins ONE URL-prefix constant. Checkpoint pending; #6 closing.

- Step Z5 (#7) PASS iter 2/3: clip-audio.ts substrate + all speech surfaces clip-first with Web Speech fallback; generation token + 12s watchdog + real-samples prime WAV + ClickableText focus fix from review. vitest 895 (+59). Checkpoint ef88057, #7 closed.
- Step Z6 (#8) PASS iter 1/3: neural_voice_enabled flag end-to-end via recipe; migration 0031; OFF-override integration test. pytest 2773 (+7), vitest 897 (+2). Checkpoint 330fc9a, #8 closed.
- Step Z7-prep (#9) PASS iter 2/3: scripts/batch_tts_audition.py (28-voice Kokoro sweep + runtime persona castings, dry-run without deps, personas-first manifest, all-fail=1 exit) + 15 hermetic tests incl. unsafe-id/unsafe-voice defense branches. #9 closed.
- Phase-end: `## Manual UAT` section appended to the plan (M1=Z7 audition #10, M2=Z8 smoke #11, M3=Z9 iPad #12).

## WIP
(none — automated span complete; operator handoff)
**Approach:** Build frontend/src/child/clip-audio.ts (one shared HTMLAudioElement primed at PIN-gate gesture next to sfx.ts unlockAudio(); playClip(url) rejects on 404/decode/interrupt). Update ReadMeButton, JokeStep (autoplay sequencing + replay, preserve #207 cancel semantics), ChoiceReadButton, StepCard threading: step carries spoken_audio_url AND neural_voice_enabled ON → play clip with FULL text contract (no truncation); any failure/missing URL → Web Speech path (Z2 truncation there only). Clip playback interrupts Web Speech and vice versa (single audio focus). NOTE: neural_voice_enabled flag ships in Z6 — Z5 should thread a boolean prop defaulting ON (or read a store field defaulting true) so Z6 can wire the real flag.
**Z5 contract:** kiosk consumes spoken_audio_url / spoken_audio_setup_url / spoken_audio_punchline_url / spoken_choice_audio_urls (aligned with choices) / spoken_question_audio_url; URLs may 404 until worker renders (fallback designed); approved template activities render from the PREVIEW path (store.ts RENDERABLE_STATES) which carries derived URLs.
**Z4 handoff notes (from bug reviewer):** engine lazy-init has no lock — fine for single-asyncio-worker Z4 design, add threading.Lock ONLY if synthesize ever runs in a threadpool. synthesize() non-ValueError exceptions propagate raw — Z4 worker must catch-and-degrade. room_classifier downloader shares the truncated-download latent flaw (pre-existing; phase follow-up candidate). test_ws_origin flakes under full-suite load (passes isolated) — pre-existing, same class as ws_heartbeat.

## Dead Ends (Z2, for the record)
- ChoiceReadButton fixture "Go left. Stop. Then run..." limit 12: word-boundary revert passes ALL its tests (last space adjacent to terminator) — fixtures must make old/new outputs DIFFER.

## Dead Ends / Decisions (carried from Phase Z prep)
- iPad Web Speech voice identity is a DEAD END — server-rendered Kokoro clips are the path.
- Never enqueue TTS in the propose path; enqueue at approve/beat-insert/joke-insert/reward-resolve only.
- Kokoro CPU-only in-process; GPU flip = provider-seam config later, NOT scoped.

## Critical Gotchas
- **Bare `uv sync` STRIPS extras from master's venv** (removed torch/diffusers mid-Z3 → mypy showed the 4 unused-ignore artifacts + GPU tests would skip). After any pyproject/uv.lock merge: `uv sync --extra image_gen`. Keep the `tts` extra UNINSTALLED (operator installs at Z7; stub tests + lazy-import tests assume base env).
- **PowerShell native-arg quoting mangles embedded double quotes in `git commit -m @'...'@`** (Z2 checkpoint failed with pathspec errors) — always write the message to a file and use `git commit --file`.
- **PARALLEL uat-ui session artifacts:** untracked `.claude/skills/uat-ui/evals/` — NEVER commit; never `git add -A` on master; scope every add.
- **CRLF flap on `frontend/src/shared/{errors,types}.ts`:** show M with ZERO content diff — never stage unless a step actually regenerates codegen (Z3 will).
- Worktree venv lacks image_gen extra → mypy shows 4 unused-type-ignore errors in image_gen/animate.py + 4 GPU tests skip there; both CLEAN on master full venv. Judge worktree gates accordingly for every Z step.
- WS timing tests (test_ws_toy_actions_topic, test_ws_heartbeat) flake under full-suite load; pass in isolation. Pre-existing.
- Z4 shares activities.py with Z1 — Z4 builds only AFTER Z1 merges (sequential order handles this).
- Wire-shape trap: voice_profile must be a DECODED object on the wire; kiosk typeof-number guard rejects raw JSON strings (persona-voice.ts:95-97).
- Baseline counts: pytest 2671 (master full venv), vitest 817 (pre-Z1).

## Key Files
- Phase Z plan: `documentation/plan/awaiting-uat/phase-z-persona-voices-plan.md` (umbrella #2; steps #3-#12)
- Z1 seams: `src/toybox/api/activities.py` (:1729 picker; pinned callers ~:2047/:2234/:2405), `src/toybox/main.py:863`, `frontend/src/child/components/RewardStep.tsx`
- Z4 hooks: activities.py post_approve :2932 (S2 pattern :3047), `_insert_adventure_beat` :4726, `_parent_insert_finish` :3695, `_insert_reward_step_as_current` :5346
- Kiosk speech seams: `frontend/src/child/{tts.ts,persona-voice.ts}`, `components/{ReadMeButton,ChoiceReadButton,JokeStep,StepCard,RewardStep}.tsx`, `sfx.ts` + `KioskPinPrompt.tsx:69`
