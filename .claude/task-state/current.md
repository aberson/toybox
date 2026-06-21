# Task State

**Task:** Toybox — "Claude Images" feature (Claude-authored SVG action sprites) + idle-sprite unbreak
**Status:** CODE COMPLETE, UNCOMMITTED. All gates green: 2624 pytest (+30) / 804 vitest (+~17), 0 type/lint. About to do a LIVE end-to-end run to render a real Claude SVG before deciding it's good. HEAD still `969404f` (nothing committed this session).
**Last written:** 2026-06-21T18:30:00Z
**Session SHA:** 969404f

## Next Action

Live end-to-end smoke of the Claude Images path (operator opted in):
1. Bridge a fresh OAuth token: `uv run python scripts/uat/bridge_claude_creds.py` (CLI tokens rotate ~daily), then `uv run python -m toybox.ai --check`.
2. `uv run python -m toybox.db.migrate` (applies 0030 if not already; DB is at v30 locally).
3. Enable the flag: `UPDATE settings SET value='true' WHERE key='claude_images_enabled';` (or PUT /api/settings/claude-images-enabled with a parent token).
4. Start backend (image-gen lifespan must run so the worker exists): `uv run python -m toybox.main --host 127.0.0.1 --port 8000`.
5. Trigger generation for toy `a7e7bd00cef14285aa98a310f1c58df5` (Sydney Bagheera Pillow): POST /api/toys/{id}/actions/regenerate (parent token) OR per-slot idle. Watch toy_actions rows → expect `image_path` ending `/idle.svg`.
6. Playwright (via subagent): screenshot `http://127.0.0.1:8000/api/static/images/toy_actions/a7e7bd00cef14285aa98a310f1c58df5/idle.svg` (and a couple non-idle slots) at 112px to see the rendered Claude SVG. Read the screenshot back.
7. If it looks good → report + offer to commit (scoped `git add` to the change set below). If bad → iterate the prompt in `svg_gen.py` (`build_user_prompt` / `_SYSTEM_PROMPT`).

## WIP

About to run the live E2E (step 1 above). Nothing half-applied. Feature fully built + tested with StubClient; the ONLY unverified leg is the real OAuth → Claude → SVG call.

## Completed (this session — Claude Images)
- **Unbreak #3/#4:** app.py registers image/webp + image/svg+xml MIME (fixes idle.webp→text/plain); ToyActionSprite dropped the broken SVD .png→.webp swap (idle stays on good .png). +test_static_mime.py.
- **Backend feature:** core/claude_images_enabled.py (default FALSE, opt-in) + api/claude_images_enabled_settings.py + migration 0030 + app registration; image_gen/svg_gen.py (OAuth vision→cartoon SVG, idle self-animating, sanitized: script/handler/foreignObject/js: stripped); worker.py `_run_one_svg` branch (flag-gated, skips SD GPU/breaker gates, one-format-per-slot on disk); ai/client.py describe_image gained model/system overrides + svg_model() (Opus 4.8).
- **Frontend feature:** ToyActionSprite `preferSvg` chain (svg→png→hidden); StepCard `claudeImagesEnabled`→preferSvg; child App.tsx + child api.ts standalone bootstrap fetch; ToyActionGrid derives preferSvg from row.image_path; parent api.ts get/setClaudeImagesEnabled; ClaudeImagesControl.tsx toggle in SettingsPanel; parent App.tsx state+bootstrap+handler.
- **Tests:** backend test_claude_images_enabled (core+api), test_svg_gen, test_worker_svg, test_static_mime; frontend ClaudeImagesControl.test, ToyActionGrid svg-row tests, ToyActionSprite preferSvg tests, StepCard preferSvg integration.
- (prior session, committed+pushed at master 3fd238f) Phase W + X shipped; #207 fixed; docs refreshed.

## Dead Ends / Decisions
- Claude has NO image-gen API (vision is input-only) — "Claude image" = Claude-authored SVG. Confirmed via claude-api skill, not memory.
- Flag is backend-read-only for GENERATION; kiosk fetches it standalone (NOT the Phase K cohort) only to set preferSvg. Parent grid uses row.image_path extension instead (no flag needed there).
- Default OFF: opt-in parallel path; local SD pipeline unchanged when off.
- Did NOT reformat worker.py — it was already part of the repo's pre-existing `ruff format` drift (104 files); reformatting = huge unrelated diff. New files ARE format-canonical.

## Critical Gotchas
- CRLF churn on frontend/src/shared/{errors,types}.ts shows as "M" with EMPTY git diff — known noise, NOT this session's work; `git checkout --` them (or just don't stage) before any commit.
- Parallel-session artifacts in tree NOT mine: documentation/runs/2026-06-21-room-import-uat.md + frontend/playwright/room-import.spec.ts (Phase X). Scope `git add` to the change set; do NOT `git add -A`.
- Live OAuth SVG path is UNVERIFIED by tests (StubClient only). It mirrors toy_vision's OAuth-direct urllib pattern (no SDK, no API key); efficacy depends on that same live path. OAuth tokens rotate ~daily — re-bridge before the live run.
- Cost: regenerating one toy = 10 Claude vision calls (one/slot) on the subscription.
- Backend worker needs the image-gen lifespan running (started by `toybox.main`), else get_image_gen_worker() is None and regenerate 503s.

## Key Files (this change set — for scoped commit)
- Backend new: src/toybox/core/claude_images_enabled.py, src/toybox/api/claude_images_enabled_settings.py, src/toybox/db/migrations/0030_claude_images_enabled.sql, src/toybox/image_gen/svg_gen.py
- Backend mod: src/toybox/app.py, src/toybox/ai/client.py, src/toybox/image_gen/worker.py
- Frontend new: frontend/src/parent/components/ClaudeImagesControl.tsx (+.test)
- Frontend mod: child/{App.tsx,api.ts,components/StepCard.tsx,ToyActionSprite.tsx,ToyActionSprite.module.css}; parent/{App.tsx,api.ts,components/SettingsPanel.tsx,ToyActionGrid.tsx}
- Tests: tests/unit/test_static_mime.py, tests/unit/core/test_claude_images_enabled.py, tests/unit/image_gen/test_svg_gen.py, tests/unit/image_gen/test_worker_svg.py, tests/integration/test_claude_images_enabled_api.py + the frontend .test.tsx siblings above
- Tunables: TOYBOX_CLAUDE_SVG_MODEL (default claude-opus-4-8), TOYBOX_CLAUDE_SVG_TIMEOUT_SEC (90)
