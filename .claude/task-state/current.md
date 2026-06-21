# Task State

**Task:** Toybox — Phase W + X shipped; loose-end #207 fixed; docs refreshed (SESSION END)
**Status:** No active build. Phase W + X CODE COMPLETE + pushed; #207 (iOS TTS race) fixed; README/docs refreshed via /repo-update. All gates green: 2586 pytest / 791 vitest, 0 type/lint. master `3fd238f`.
**Last written:** 2026-06-20T19:45:00Z
**Session SHA:** 3fd238f

## Next Action

Pick one (nothing is mid-flight):
1. **Operator: bundled iPad UAT #223** — validates R/S/O/T/V/W/X together. Prereq for the X rows: `uv run python -m toybox.ai.room_classifier --download` (first real-CLIP run). When a phase's rows pass, close its umbrella (#211 R, #217 S, #177 O, #222 T, #234 V, #246 W, #254 X) and move its plan from `plan/awaiting-uat/` to `plan/archive/`.
2. **Build #134 — Personas tab** (the meatiest cleanly-buildable feature): `/plan-feature` it first (backend `/api/personas` CRUD + `PersonasPanel.tsx`, mirror ToyIngest; persona columns already exist from Phase K, no migration).
3. Smaller buildable code: #245 (OFFLINE Claude-call bypass — needs a 1-decision: "no outbound Claude at all" vs "no primary-loop Claude"), #136 (transcript-topic injection — needs /plan-feature), #244 (= build Phase D Step 20 child-token pairing).

NOT buildable (operator-gated): Phase E local-model chain (hardware install #35 + benchmark/decision #37), Phase Q tail (operator corpus run #202).

## WIP

Nothing in flight. Session is at a clean stopping point.

## Completed (this session)
- [413e19c] #207 fixed — iOS Safari cancel-then-speak race: made `tts.ts cancel()` conditional on `speaking || pending` (single source of truth; fixes all 3 call sites incl. JokeStep the issue missed). +4 vitest. Adversarial review: never worse than old behavior.
- [3fd238f] /repo-update — README badges 2586/791 + milestone line + T/V/W/X added; master-plan drift-checked clean (25/25 refs); CLAUDE.md+memory verified; issue #263 created+closed.
- (earlier) Phase X X1-X7 shipped (`87af523`), #255-261 closed, X8 folded into #223; W+X plans moved to plan/awaiting-uat/.
- (earlier) Phase W X1-X7... W1-W6 shipped (`3b5df17`), #247-252 closed.
- Buildable-not-UAT survey workflow (wf_7dbd366a-767): 119 units verified, only 2 cleanly buildable (#207 done, #134 next).

## Dead Ends / Decisions
- #207: did NOT use the issue's literal option-3 (conditional cancel inside speak()) — would break JokeStep's two-back-to-back queued speaks (punchline would cancel setup). Used conditional cancel() instead.
- Room-import matching: LOCAL ONNX CLIP (operator changed from Claude-vision); paste-HTML over live scrape.

## Critical Gotchas
- CRLF churn on frontend/src/shared/{errors,types}.ts in nearly every commit — `git checkout --` them before staging.
- Parent UI is PIN-gated → all code steps `--reviewers code`; UI validated only in iPad UAT (#223).
- X tests run model-free (injected fake classifier); real CLIP first exercised at #223 via `--download`.

## Key Files
- Master index: `documentation/master-plan.md`
- Awaiting-UAT plans: `documentation/plan/awaiting-uat/` (O,P,Q,R,S,T,V,W,X)
- Bundled UAT: GH #223
- #207 fix: `frontend/src/child/tts.ts` (`cancel()`) + `tts.test.ts`
