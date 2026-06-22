# Task State

**Task:** Local-SD → kids' UX investigation — PLAN WRITTEN, dispatch pending in a clean window
**Status:** Plan complete at `documentation/investigations/local-sd-kids-ux/` (topics.md + plan.md, 17 tiered topics). The heavy sub-agent fan-out (one investigation file per topic) is intentionally deferred to a FRESH context window. Prior work this session (Claude Images / sprite-gen overhaul) shipped + committed at `4d592ae`; not pushed.
**Last written:** 2026-06-22T07:10:00Z
**Session SHA:** (set at commit)

## Next Action
In a CLEAN context window: read `documentation/investigations/local-sd-kids-ux/plan.md` + `topics.md`, then execute the plan's "Execution model" — dispatch one background sub-agent per topic (17), waves of ~10-12, each grounded per the plan; retry failures; write `README.md`; report. (A ready transition prompt was emitted in chat.)

## WIP
Nothing executing. Plan files written + (about to be) committed. Investigation dispatch not started.

## Completed (this session)
- Shipped + committed the sprite-gen overhaul `4d592ae` (image_gen_mode: cartoon[default]/composite/claude_svg; broken SVD .webp removed; CSS idle-bob; 429 hardening). Checkpoint `6fbe79b`.
- Diagnosed: subscription OAuth (Claude AND Google) = UI entitlement, not programmatic API; direct API image-gen is rate-limited/paid. Decision: local SD (cartoon) is the correct default; claude_svg optional best-effort.
- `/user-brainstorm` ideation → 17 tiered topics for local-SD kids-UX; operator scoped to Play(non-transcription)+Kiosk, dropped reward-art, reframed guardrails as a general primer, added style-cohesion + per-child personalization, bumped animation→T2 and coloring-page→T1.
- Wrote the investigation plan (topics.md + plan.md).

## Dead Ends / Decisions
- Animation via AnimateDiff (Phase U) abandoned; SVD .webp garbled (removed). True local-SD animation is an OPEN investigation (topic 13), not a solved thing.
- Investigation scope: Play(non-transcription)+Kiosk only; leave Settings/Transcription/Kids-mgmt (work well). Children tab underused → personalization (topic 12).

## Critical Gotchas
- **PARALLEL SESSION on master** (a `uat-ui` skill iteration committed `9cedbb1` + left `.claude/skills/uat-ui/evals/` untracked). Coordinate before any push; never `git add -A`.
- CRLF churn on frontend/src/shared/{errors,types}.ts shows "M" with empty diff — never stage.
- Untracked NOT this work: documentation/runs/2026-06-21-room-import-uat.md, frontend/playwright/room-import.spec.ts.
- PowerShell mangles multi-line `git commit -m` → use `git commit -F <file>`.

## Key Files
- Investigation plan: `documentation/investigations/local-sd-kids-ux/{plan.md,topics.md}` (plan.md is self-contained for a fresh window)
- SD pipeline: `src/toybox/image_gen/{pipeline,worker,models,composite}.py`; modes in `src/toybox/core/image_gen_mode.py`
- Kiosk sprite + CSS animation: `frontend/src/child/components/ToyActionSprite.{tsx,module.css}`
