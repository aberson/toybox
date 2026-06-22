# Task State

**Task:** Claude Images / sprite-gen overhaul — SHIPPED + committed
**Status:** DONE + COMMITTED to master at `4d592ae` (NOT pushed — no push without ask). Image-gen now has three mutually-exclusive modes (cartoon=local SD [default] / composite / claude_svg=Claude SVG, opt-in best-effort). Broken SVD .webp removed; kiosk shows good static .png with a model-free CSS idle-bob. Gates green: backend 2620 pytest (1 pre-existing ws_origin flake, green solo) / mypy / ruff; frontend 800 vitest / typecheck / lint.
**Last written:** 2026-06-22T05:35:00Z
**Session SHA:** 4d592ae

## Next Action

Nothing required — feature is committed. Open/optional:
1. **Operator UAT (reliable path):** mode is back to `cartoon` (local SD) in the DB. Regenerate a toy's sprites → kiosk shows the static PNG cast with a gentle idle bob. This is the dependable, free, on-device path.
2. **claude_svg is best-effort:** it 429s on the subscription OAuth token (direct /v1/messages is rate-limited for subscription tokens). Failed slots fall back to PNG. If we want it reliable, switch its transport to the **`claude` CLI** (PROVEN to work — generated a clean cartoon SVG of the toy from the photo, no 429). Not done (the user chose local-default + keep claude_svg as optional).
3. **Top-fidelity option (paid):** Gemini Flash Image does true raster image-to-image but needs a PAID Google AI Studio key (~$0.04/img); the Google-Pro *subscription* does NOT grant API access. Not built.
4. Push when ready (`git push`), after resolving the parallel-session situation below.

## WIP
Nothing in flight. Session at a clean, committed stopping point.

## Completed (this session)
- Unbreak: static-mount MIME (image/webp + image/svg+xml); removed broken SVD .webp idle swap.
- CSS idle-bob (reduced-motion-safe) on the kiosk idle sprite — model-free animation, replaces the webp.
- `claude_svg` as a third image_gen_mode (worker mode-first dispatch + _run_one_svg + svg_gen with sanitize + 429/529 retry/backoff → `claude_images_rate_limited`); kiosk/grid prefer .svg (svg→png→hidden). Cartoon stays default.
- Removed the first-cut standalone boolean flag entirely (folded into image_gen_mode per operator request).
- Live findings: direct-API OAuth works auth-wise but is rate-limited (429) for subscription tokens; the `claude` CLI path works; Gemini image-gen is paid-tier only.
- Committed scoped: `4d592ae` (23 files). Reset DB image_gen_mode → cartoon.

## Dead Ends / Decisions
- Subscription OAuth tokens (Claude AND Google) are UI entitlements, NOT programmatic API access — both throttle/deny direct API image-gen. Lesson: a consumer subscription ≠ an API key.
- Claude has no raster image-gen (vision is input-only) → "Claude image" = SVG. Gemini CAN do raster i2i but paid-only.
- Decision: local SD (cartoon) is the correct DEFAULT for this local-first device; claude_svg optional best-effort; CSS bob for animation (no generative model needed).

## Critical Gotchas
- **PARALLEL SESSION active on master:** a `uat-ui` skill iteration committed `9cedbb1` (.claude/skills/uat-ui/SKILL.md) mid-work, and `.claude/skills/uat-ui/evals/` is untracked. It races the git index (my first commit attempt failed on lock contention). Coordinate before pushing; don't `git add -A`.
- CRLF churn on frontend/src/shared/{errors,types}.ts shows "M" with empty diff — known noise, never stage.
- Parallel-session/earlier untracked NOT this work: documentation/runs/2026-06-21-room-import-uat.md, frontend/playwright/room-import.spec.ts.
- PowerShell mangles multi-line `git commit -m` (splits into pathspecs) — use `git commit -F <file>`.
- claude_svg via direct API = rate-limited; cartoon/composite are the reliable backends.

## Key Files
- Mode core: src/toybox/core/image_gen_mode.py (cartoon|composite|claude_svg)
- SVG gen: src/toybox/image_gen/svg_gen.py (+ retry/backoff); worker dispatch: src/toybox/image_gen/worker.py (_run_one_svg)
- Kiosk: frontend/src/child/components/ToyActionSprite.{tsx,module.css} (preferSvg chain + idle-bob), StepCard.tsx
- Parent toggle: frontend/src/parent/components/SettingsPanel.tsx (ImageGenModeToggle, 3 options)
- Tunables: TOYBOX_CLAUDE_SVG_MODEL (claude-opus-4-8), TOYBOX_CLAUDE_SVG_TIMEOUT_SEC (90)
