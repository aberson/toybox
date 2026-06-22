# Task State

**Task:** "Claude Images" = a third `image_gen_mode` value (`claude_svg`) — Claude-authored animated SVG action sprites; + idle-sprite unbreak
**Status:** CODE COMPLETE, UNCOMMITTED. Refactored from a separate boolean flag into a mutually-exclusive mode alongside cartoon/composite (operator's request). All gates green: backend 2614 pytest (1 ws_origin flake passes in isolation) / mypy 146 / ruff clean; frontend 800 vitest / typecheck / lint. HEAD still `aa7791f` (only the prior checkpoint committed; feature is uncommitted).
**Last written:** 2026-06-21T20:25:00Z
**Session SHA:** aa7791f

## Next Action

Decide one:
1. **Commit the feature** (scoped `git add` to the change set below; do NOT `git add -A` — skip CRLF-flapping `shared/*.ts` + parallel-session `room-import.*`).
2. **(a) 429 hardening** — in worker `_run_one_svg`, map 429 → a clear `rate_limited` error + retry-with-backoff honoring Retry-After, and/or space the 10 per-toy calls. The live run proved the subscription token rate-limits direct /v1/messages (see below).
3. **Live artifact** — still pending; only works when NOT racing this Claude Code session for the subscription budget. Operator path: Settings → Image-gen mode → "Claude Images" → regenerate a toy's sprites.

## WIP

Nothing half-applied. Refactor complete + green. Representative SVG (Claude-authored stand-in) rendered earlier looks great — clean cartoon cat, animates, scales; big win over the SVD blobs. Live pipeline output still unproven (rate-limited).

## Completed (this session)
- **Unbreak #3/#4:** app.py registers image/webp + image/svg+xml MIME; ToyActionSprite dropped the broken SVD .png→.webp swap (idle stays on good .png). +test_static_mime.py.
- **Claude Images as a MODE (current design):** `image_gen_mode` core extended to `cartoon | composite | claude_svg` (core + api/image_gen_settings Literals + ImageGenModeToggle option + ImageGenMode type both apis). Worker `_run_one_body` now probes mode FIRST; `mode == "claude_svg"` → `_run_one_svg` (skips SD capability/breaker — needs a token not a GPU); cartoon/composite reuse the same probed mode. svg_gen.py unchanged (OAuth vision→sanitized cartoon SVG, idle self-animating). client.py describe_image has model/system overrides + svg_model() (Opus 4.8). One format per slot on disk (sibling cleanup both paths).
- **Kiosk:** child api getImageGenMode (unauth GET) → App preferSvg = (mode==="claude_svg") → StepCard `preferSvg` → ToyActionSprite svg→png→hidden chain. Parent ToyActionGrid derives preferSvg from row.image_path extension.
- **REMOVED (was the first cut):** standalone boolean flag claude_images_enabled — core module, api router, migration 0030, ClaudeImagesControl.tsx, all App/SettingsPanel/api wiring, and their tests. Local dev DB residue cleaned (schema_migrations v30 row + settings key deleted → back to v29).
- **Tests:** test_svg_gen, test_worker_svg (mode_probe="claude_svg"), test_static_mime, image_gen_mode core+api claude_svg coverage, ToyActionSprite preferSvg, StepCard preferSvg integration, ToyActionGrid svg-row, ImageGenModeToggle claude_svg.
- **Live E2E finding:** OAuth auth path WORKS (429 rate_limit_error, never 401; `oauth-2025-04-20` header makes no difference). Subscription token is rate-limited for direct /v1/messages while this Claude Code session runs → couldn't capture a genuine artifact. Error-handling verified live (429 → row failed, no crash, no breaker trip).

## Dead Ends / Decisions
- "Claude Images" is a MODE, not a boolean — mutually exclusive with cartoon/composite (operator's call; the worker already treated it that way by short-circuiting).
- Claude has NO image-gen API → "Claude image" = Claude-authored SVG (vision input only). Confirmed via claude-api skill.
- Did NOT add the `oauth-2025-04-20` header to client.py — live test showed it doesn't change the 429; toybox's bare-header pattern is fine (matches toy_vision).
- Did NOT reformat worker.py — pre-existing repo `ruff format` drift (104 files); new files are format-canonical.

## Critical Gotchas
- CRLF churn on frontend/src/shared/{errors,types}.ts shows "M" with EMPTY diff — known noise, not this work; don't stage.
- Parallel-session artifacts NOT mine: documentation/runs/2026-06-21-room-import-uat.md + frontend/playwright/room-import.spec.ts. Scope `git add`.
- Live SVG path UNVERIFIED end-to-end (StubClient tests only). Real call needs an idle subscription (not racing a Claude Code session) + a fresh token (`scripts/uat/bridge_claude_creds.py`, rotates ~daily).
- Cost: regenerating one toy in claude_svg mode = 10 Claude vision calls.

## Key Files (change set — for scoped commit)
- Backend new: src/toybox/image_gen/svg_gen.py
- Backend mod: src/toybox/app.py (MIME + router list), src/toybox/ai/client.py (svg_model + describe_image overrides), src/toybox/image_gen/worker.py (mode-first dispatch + _run_one_svg), src/toybox/core/image_gen_mode.py (+claude_svg), src/toybox/api/image_gen_settings.py (Literals)
- Backend deleted: core/claude_images_enabled.py, api/claude_images_enabled_settings.py, db/migrations/0030_claude_images_enabled.sql
- Frontend mod: child/{App.tsx,api.ts,components/StepCard.tsx,ToyActionSprite.tsx,ToyActionSprite.module.css}; parent/{App.tsx,api.ts,components/SettingsPanel.tsx,ToyActionGrid.tsx}
- Frontend deleted: parent/components/ClaudeImagesControl.tsx (+.test)
- Tests new: tests/unit/test_static_mime.py, tests/unit/image_gen/test_svg_gen.py, tests/unit/image_gen/test_worker_svg.py + frontend ToyActionSprite/StepCard/ToyActionGrid/ImageGenModeToggle test updates
- Tests deleted: tests/unit/core/test_claude_images_enabled.py, tests/integration/test_claude_images_enabled_api.py
- Tunables: TOYBOX_CLAUDE_SVG_MODEL (default claude-opus-4-8), TOYBOX_CLAUDE_SVG_TIMEOUT_SEC (90)
