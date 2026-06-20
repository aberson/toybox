# Task State

**Task:** Phase W (Play Depth) — build (in progress)
**Status:** BUILDING — W1+W2+W3 DONE (#247-249 closed); W4 next (adventure engine, depends W2✅). Goal active (W1–W6 → Status: DONE).
**Last written:** 2026-06-20T06:00:00Z
**Session SHA:** e7bbb50

## Next Action

Continue build-phase at W2 (`/build-phase --plan documentation/plan/phase-w-plan.md --resume 2` if resuming fresh). Goal is armed on "Phase W steps W1-W6 all Status: DONE". Per-step flow: worktree → dev agent → 4 code reviewers → post-merge gates in MAIN project → mark plan Status: DONE → checkpoint commit → close issue → remove worktree. All `--reviewers code` (PIN-gated UI; verified in W7 UAT). Baseline gates: backend 2353 pytest, frontend 753 vitest, mypy+ruff clean.

Remaining: W2 #248 (linear toggle, touches generator.py — grep all `generate(` callers), W4 #250 (adventure engine, depends W2), W5 #251 (boss fight, depends W4), W3 #249 (STT grading), W6 #252 (smoke gate, depends W3/4/5), W7 #253 (operator UAT — deferred bundle, the goal's boundary). Spine: W2→W4→W5→W6; W3→W6; W1✅ before W7.

NOTE: `.git/info/exclude` in toybox has a TEMP block excluding `/data/images/compare/` (sprite-compare artifacts, not Phase W) so build-phase's `git add -A` stays clean — remove after Phase W. Also: my plan-expedite SKILL.md edits are stashed in the coding-root repo (`c:/Users/abero/dev`, `git stash list`) — restore after.

## WIP

**Current:** Phase W plan-expedited and ready for `/build-phase`. Umbrella #246; steps #247(W1 stub dials) #248(W2 linear toggle) #249(W3 STT grading) #250(W4 adventure engine) #251(W5 boss fight) #252(W6 smoke gate) #253(W7 iPad UAT). Plan `**Issue:**` lines backfilled.

**Approach:** Six not-built items from operator notes, one combined plan. Decisions baked in: hybrid Claude/offline adventure gen via capability gate; boss fight = climax beat at 5th adventure beat; dials are true stubs; STT grading reads last-30s passive transcript, decoupled from transcript_retention.

## Completed (this session)

- Doc tree reorg: `plan.md` → `documentation/master-plan.md`; phase docs sorted into `plan/` (active: e/w/x), `plan/awaiting-uat/` (O,P,Q,R,S,T,V), `plan/archive/` (K,L,M,N,J,U,SWR,e3). Commit `f0ad1ee`. Pointers re-based in CLAUDE.md, README, phase-e.md, archive README; all links verified resolvable.
- Drafted `documentation/plan/phase-w-plan.md` (play depth) + `documentation/plan/phase-x-room-import-plan.md` (room import — NOT yet expedited/synced).
- plan-expedite Phase W: plan-review (W4 entry-point fix) + plan-wrap (climax/window decisions) + repo-sync (#246–253). Commit `16201a0`.
- [22326e9] Step W1 (stub dials): PASS — backend 2353 / frontend 753, #247 closed.
- [a05572d] Step W2 (linear/non-linear toggle): PASS — backend 2378 / frontend 759, #248 closed.
- [e7bbb50] Step W3 (Whisper/STT Q&A auto-grading): PASS after 2 iters — REVIEW CAUGHT latent R3 bug (question/expected_answer never wired template→activity_steps; _schema.json additionalProperties:false rejected them); fixed full 8-hop chain + judge 8s-timeout (finally shutdown wait=False) + shared breaker. backend 2409 (+31) / frontend 767 (+8), #249 closed.

## Dead Ends / Decisions

- Room-import matching: operator chose reusing existing Claude vision over a net-new local CLIP (deferred follow-up); paste-HTML/URLs over live Redfin scrape.
- Phase X migrations assume Phase W lands first (0029+); build X after W or renumber.

## Critical Gotchas

- Parent UI is PIN-gated → runtime/UI reviewers can't authenticate; every code step is `--reviewers code`, UI checked in W7 UAT.
- 5 forward-only migrations 0024–0028 (one per code step); abort+preserve on failure.
- W2 changes `generate()` signature — grep all callers (code-quality §1).

## Key Files

- Plan: `documentation/plan/phase-w-plan.md`
- Next plan (drafted, not synced): `documentation/plan/phase-x-room-import-plan.md`
- Master index: `documentation/master-plan.md`
- Resume state: `.plan-expedite-state` (Phase W complete)
