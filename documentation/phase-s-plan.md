# Phase S — Kiosk Visual Refresh + Character Animation

## 1. What This Feature Does

Phase S gives the child kiosk a visual identity layer. Two changes ship in sequence:

**S1 — Persona-keyed background gradients + step card prominence.** The kiosk background shifts from a static near-white gradient to a richer per-persona gradient (detective → navy/charcoal, periodic_table → teal/dark-green, princess → rose/lavender, wizard → purple/indigo, null → warm amber/sand). The step card gets a more prominent visual treatment (larger body font, stronger shadow, tighter layout) so the current step reads clearly at arm's length on an iPad.

**S2 — Claude-annotated per-step avatar animation.** At approve-time, the backend makes a single synchronous Claude call that reads all step bodies and persona context and assigns one animation from the existing vocabulary (`float`, `pulse`, `wobble`, `jump`, `shine`, `spin`) to each step. The hints are persisted into each step's `metadata_json` before the WebSocket broadcast fires, so the kiosk always receives a fully-annotated activity. The avatar plays its step's animation (infinite, slow, non-flashing) for the duration of that step; on advance, the new step's animation begins.

**S3 — iPad UAT.** Validates both S1 and S2 visually on-device; also closes R5 #216 (bundled with this session per the Phase R plan).

## 2. Existing Context

- **Background style** (`frontend/src/child/App.tsx:96-103`): `FULL_BLEED_BACKGROUND_STYLE` is a static inline `CSSProperties` object — `linear-gradient(180deg, #fefefe 0%, #f4f4f7 100%)`. Both `persona_id` and `intent_source` are on the `Activity` wire shape (`frontend/src/child/api.ts:142-143`) and already read in `avatarLetter`/`avatarImage`.
- **PersonaAvatar** (`frontend/src/child/components/PersonaAvatar.tsx`): 240px, renders `<img>` or 8-color deterministic-letter circle. No animation code. `data-testid="persona-avatar"` is the test seam.
- **Animation keyframes** (`frontend/src/child/animations/rewardAnimations.css`): six keyframes — `shine`, `jump`, `spin`, `pulse`, `wobble`, `float` — plus `all-done-shine`. Currently applied only to reward-step images and the "All done!" heading. The keyframes are defined but have no corresponding CSS utility classes yet (reward steps apply them via inline `CSSProperties` from `rewardAnimations.ts`; `all-done-shine` is the only class-based usage).
- **Step-advance seam** (`App.tsx:444-450`): `prevStepSeqRef` + `currentStepSeq(activity)` already tracks seq changes for SFX; animation class changes on step advance piggyback this pattern.
- **Step metadata** (`src/toybox/api/activities.py:787-803`): `activity_steps.metadata_json` is a freeform JSON blob (migration 0016). Already carries `audio_url` (songs), `punchline` (jokes), element corpus fields, `reward_kind`. Adding `avatar_animation: string` to a step's metadata blob requires no migration — it slots in alongside the existing keys.
- **Claude client** (`src/toybox/ai/client.py`): urllib + OAuth bearer, no SDK. `_post_messages` is the sync urllib call, wrapped in `asyncio.to_thread` for async callers. Sync callers can call a new sync wrapper directly. Text model is `claude-sonnet-4-6` by default (env `TOYBOX_CLAUDE_TEXT_MODEL`).
- **Approve handler** (`src/toybox/api/activities.py:2604-2718`): synchronous FastAPI route. The structure is: fetch row → validate transition → `_attempt_transition` (DB write, line 2707) → `_row_to_response` (line 2716) → `_emit_state` (WS broadcast, line 2717) → return response. The annotation call inserts between `_attempt_transition` and `_emit_state` so the kiosk's first "approved" WS message carries full animations.
- **Production persona_ids** (from DB): `null`, `detective`, `periodic_table`, `princess`, `wizard`. All theming handles `null` gracefully with a warm neutral fallback.

## 3. Scope

**In scope:**
- Persona-keyed background gradients (CSS only, no new assets)
- Step card visual prominence (font size, shadow — CSS only)
- Claude call at approve-time to annotate each step's `avatar_animation`
- New `src/toybox/ai/animator.py` sync annotator module
- PersonaAvatar reads `avatar_animation` from current step metadata and applies CSS class
- CSS utility classes for avatar animations (`.avatar-animate-float`, etc.) added to `rewardAnimations.css`
- Graceful fallback: if annotation is unavailable (Claude error/timeout, old activity), avatar uses `float`
- S3 iPad UAT closes R5 #216 and S3

**Explicitly out of scope:**
- TTS-triggered talk shimmy (no TTS state wired to avatar)
- Step-advance one-shot reaction override (S2 uses a single per-step animation, not a two-layer idle+react system)
- New animation library (no Framer Motion / GSAP — pure CSS keyframes)
- Intent-source theming (persona_id only)
- Illustrated SVG backgrounds or any new asset pipeline
- Changes to the branching template JSON files (the annotation is runtime, not authoring-time)

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `frontend/src/child/App.tsx` | modify | `FULL_BLEED_BACKGROUND_STYLE` becomes a function call `gradientForPersona(activity?.persona_id)`; extract current step's `avatar_animation` from metadata and pass to PersonaAvatar | read confirmed (846 lines); `persona_id` at line 146, `FULL_BLEED_BACKGROUND_STYLE` at line 96, step-advance seam at line 444 |
| `frontend/src/child/components/PersonaAvatar.tsx` | modify | add `animationName?: string` prop; apply `.avatar-animate-{name}` CSS class on the root element (both img and letter-circle branches) | read confirmed (91 lines); both branches rendered at lines 48-90 |
| `frontend/src/child/animations/rewardAnimations.css` | extend | add six `.avatar-animate-{name}` utility classes that reference the existing keyframes with `animation-iteration-count: infinite` and per-keyframe safe durations | read confirmed (131 lines); keyframes defined, no utility classes exist yet |
| `src/toybox/api/activities.py` | modify | `post_approve` — inject `_annotate_and_persist_step_animations` call between `_attempt_transition` (line 2707) and `_emit_state` (line 2717); writes annotation results into `activity_steps.metadata_json` rows | read confirmed; approve handler at lines 2604-2718; `metadata_json` update pattern grep'd at lines 787, 895, 1355 |
| `frontend/src/child/components/StepCard.tsx` | modify | visual prominence: larger body font, stronger card shadow/border | file exists (not read in detail — implementation left to build-step agent within "more prominent" constraint) |
| New: `frontend/src/child/theming.ts` | create | `gradientForPersona(persona_id: string \| null): string` — maps the 5 known persona_ids + null to CSS gradient strings | |
| New: `src/toybox/ai/animator.py` | create | `annotate_step_animations(steps, persona_id, client) -> dict[int, str]` — synchronous Claude call, returns `{seq: animation_name}` map; timeout + error → empty dict | |
| `tests/unit/ai/test_animator.py` | create | unit tests for `annotate_step_animations` with StubClient | |
| `tests/integration/test_approve_animations.py` | create | integration test: approve → verify `metadata_json` rows carry `avatar_animation` key | |

## 5. New Components

### `frontend/src/child/theming.ts`

Exports `gradientForPersona(persona_id: string | null): string`. Maps:

| persona_id | gradient |
|---|---|
| `detective` | `linear-gradient(160deg, #1a237e 0%, #37474f 100%)` |
| `periodic_table` | `linear-gradient(160deg, #004d40 0%, #1b5e20 100%)` |
| `princess` | `linear-gradient(160deg, #fce4ec 0%, #e8d5f5 100%)` |
| `wizard` | `linear-gradient(160deg, #311b92 0%, #0d1b4b 100%)` |
| `null` / unknown | `linear-gradient(160deg, #fff8e1 0%, #ffe0b2 100%)` |

Returns a CSS gradient string suitable for the `background` property. No external deps.

### `src/toybox/ai/animator.py`

Exports `annotate_step_animations(steps: Sequence[ActivityStepResponse], persona_id: str | None, client: AIClient) -> dict[int, str]`.

`ActivityStepResponse` fields used by `animator.py` (from `src/toybox/api/activities.py:221`):
| field | type | note |
|---|---|---|
| `seq` | `int` | step sequence number (ge=1); used as the dict key in the return value |
| `body` | `str` | step text content (min_length=1); primary input to Claude prompt |
| `kind` | `str \| None` | step kind (`"text"`, `"fork"`, `"song"`, `"joke"`, `"reward"`, or `None`); reward steps should receive `"shine"` regardless of Claude output |
| `metadata` | `dict[str, Any] \| None` | existing blob; `avatar_animation` is added as a new key by `post_approve` after annotation |

- Builds a single prompt listing all step bodies with their seq numbers and the persona context.
- Posts one Claude call (sync via urllib — mirrors `judge.py`'s `_post_messages` pattern but synchronous for use inside the sync `post_approve` handler).
- Requests JSON: `{"annotations": [{"seq": N, "animation": "..."}]}`.
- Validates each animation against `Animation` enum members; drops unknowns.
- On any exception (network, timeout, parse error): logs `WARNING`, returns `{}` (caller treats empty as "no annotation").
- Timeout: 15s hard cap (shorter than judge's 30s — animation hints are lower stakes).

Prompt vocabulary guidance: prefer `float`, `pulse`, `wobble` for calm/narrative steps; use `jump` for exciting/action steps; use `shine` only for celebratory steps; `spin` gets a 5s animation duration in CSS to avoid dizziness. Claude is not expected to know the CSS durations — the CSS utility classes encode safe durations per keyframe.

## 6. Design Decisions

### Persona_id as the sole theming key

`intent_source` (boredom / request_activity / request_play) describes why the activity was triggered, not the character's personality. The child interacts with the persona, not the trigger. The three intent values produce no meaningful color differentiation. `persona_id` gives five visually distinct palettes with clear character associations.

### CSS gradients only, no SVG assets

No new asset pipeline is warranted for a cosmetic pass. The existing `FULL_BLEED_BACKGROUND_STYLE` pattern (inline `CSSProperties`, `background: gradient`) extends cleanly to per-persona values. The `theming.ts` module keeps the map in TypeScript where it's type-checked and easy to update.

### Approve-time synchronous annotation (Approach B)

Three seams were evaluated:
- **Propose-time sync** (inside propose handler): adds latency to the propose call; propose is the parent's "discovery" moment and should stay fast.
- **Background task** (like `judge_and_persist`): the kiosk may render the first step before annotations arrive, requiring a fallback AND a late-update WS path — two new code paths.
- **Approve-time sync** (chosen): the parent is actively committing to "run this activity." A 1–2s pause while Claude annotates 5 steps is imperceptible in this context. The WS broadcast fires after annotation, so the kiosk always receives a fully-annotated activity — no fallback path needed beyond error handling.

The frontend still implements `float` as the fallback for activities approved before S2 shipped (legacy rows) or when annotation fails.

### Reuse existing keyframes, add CSS utility classes

The six keyframes are already defined in `rewardAnimations.css`. Rather than duplicating them or adding a new stylesheet, Phase S adds `.avatar-animate-{name}` utility classes in the same file. Each class encodes a safe `animation-duration` for its keyframe:

| class | keyframe | duration | rationale |
|---|---|---|---|
| `.avatar-animate-float` | `float` | 3s | gentle baseline idle |
| `.avatar-animate-pulse` | `pulse` | 2s | soft, child-safe scale |
| `.avatar-animate-wobble` | `wobble` | 1.8s | playful rock |
| `.avatar-animate-jump` | `jump` | 1.2s | energetic but bounded |
| `.avatar-animate-shine` | `shine` | 2.5s | glow — slow enough to be non-strobe |
| `.avatar-animate-spin` | `spin` | 5s | slow rotation avoids dizziness |

All classes use `animation-iteration-count: infinite` and `animation-timing-function: ease-in-out`. The NO FLASHING constraint is satisfied: no brightness spikes faster than 2.5s, no rapid alternating colors.

### No TS codegen change for `avatar_animation`

`ActivityStep.metadata` is already typed as `Record<string, unknown>` on the frontend. The annotation value is read as `String(step.metadata?.avatar_animation ?? "float")` — no Pydantic model change, no `gen_types_ts.py` run required.

### `animator.py` uses a `SyncAIClient` protocol, not `AIClient`

`AIClient.complete_text()` is async (`client.py:93`). `post_approve` is a synchronous FastAPI route — `await` is unavailable. `AnthropicClient` already has `_complete_text_sync()` (private, `client.py:179-193`) that calls urllib directly. The build-step agent must:

1. Expose it as a public method: `def complete_text_sync(self, messages, *, max_tokens, system) -> AIResponse`.
2. Add the same signature to `StubClient` (returns a deterministic animation JSON for tests).
3. Define a `SyncAIClient(Protocol)` with `complete_text_sync()` in `client.py`.
4. Change `annotate_step_animations`'s signature to accept `SyncAIClient`, not `AIClient`.

This keeps the protocol abstraction intact and avoids `asyncio.run()` (fragile inside a running event loop).

### Capability gate bypass is intentional for `animator.py`

CLAUDE.md states "Every Claude call goes through the capability gate for offline degradation." `is_capable()` (`src/toybox/ai/capability.py:120`) is async — calling it from sync `post_approve` without `asyncio.run()` is not possible safely. The animation annotation therefore skips the explicit capability check and relies entirely on its own `try/except` fallback: any failure (network error, 401, timeout, offline) logs a WARNING and returns `{}` — the kiosk falls back to `float` for all steps. This achieves the same offline-degradation outcome without the async gate. The build-step agent must NOT add `asyncio.run(is_capable(...))` here.

## 7. Build Steps

<!-- autofix-applied: 2026-06-05 -->
### Step S1: Persona-keyed background gradients + step card prominence
- **Problem:** The kiosk background is a static near-white gradient with no persona identity. The step card body font is small enough that it's hard to read at arm's length on an iPad.
- **Type:** code
- **Issue:** #218
- **Flags:** --reviewers code
- **Produces:**
  - `frontend/src/child/theming.ts` — `gradientForPersona()` function
  - `frontend/src/child/App.tsx` — `FULL_BLEED_BACKGROUND_STYLE` replaced by dynamic `gradientForPersona(activity?.persona_id)` call; static gradient retained as the no-activity idle state
  - `frontend/src/child/components/StepCard.tsx` — more prominent step card (larger body font, stronger shadow — exact values left to implementer within "clearly readable at arm's length" constraint)
  - Vitest tests: `gradientForPersona` returns distinct values for all 5 persona_ids and null; App renders persona-specific background when activity has persona_id
- **Done when:** `npm run typecheck && npm run lint && npm test -- --run` all pass; `gradientForPersona` test covers all 6 inputs (5 personas + null); no regressions in existing child kiosk tests
- **Depends on:** none
- **Status:** DONE (2026-06-05)

<!-- autofix-applied: 2026-06-05 -->
### Step S2: Claude approve-time animation annotation + avatar CSS animations
- **Problem:** The avatar is static — no animation at all. The kiosk feels flat and lifeless compared to the activity content.
- **Type:** code
- **Issue:** #219
- **Flags:** --reviewers code
- **Produces:**
  - `src/toybox/ai/animator.py` — `annotate_step_animations(steps, persona_id, client) -> dict[int, str]`; sync urllib call; graceful fallback (empty dict) on error
  - `src/toybox/api/activities.py` — `post_approve` injects annotation call + per-step `metadata_json` UPDATE between `_attempt_transition` and `_emit_state`
  - `frontend/src/child/animations/rewardAnimations.css` — six `.avatar-animate-{name}` utility classes with safe durations (see §6 Design Decisions table)
  - `frontend/src/child/components/PersonaAvatar.tsx` — `animationName?: string` prop; applies `.avatar-animate-{name}` or `.avatar-animate-float` (fallback) as className
  - `frontend/src/child/App.tsx` — extracts `avatar_animation` from current step's `metadata` and passes to PersonaAvatar; idle state (no activity) gets `float`
  - `tests/unit/ai/test_animator.py` — StubClient tests: happy path, error fallback, unknown animation filtered
  - `tests/integration/test_approve_animations.py` — integration test: POST approve → GET activity → assert each step's `metadata.avatar_animation` is a valid Animation member
- **Done when:** `uv run pytest tests/ -x -q` passes; `uv run mypy src` 0 errors; `npm run typecheck && npm run test -- --run` pass; integration test confirms annotation round-trip; legacy activities (no `avatar_animation` in metadata) render with `float` fallback (existing activity fixture test)
- **Depends on:** S1

<!-- autofix-applied: 2026-06-05 -->
### Step S3: iPad UAT — visual validation (closes R5 #216 + S3)
- **Problem:** Phase S visual changes (persona gradients, avatar animation) must be validated on a real iPad with both children before shipping; R5 #216 is also bundled in this session.
- **Type:** operator
- **Issue:** #220
- **Produces:** UAT pass/fail verdict; `documentation/runs/2026-06-<date>-phase-s-uat.md`
- **Done when:** Operator confirms on iPad:
  1. Kiosk background changes to persona-appropriate gradient when activity is approved (test with at least 2 different persona activities)
  2. Avatar animates continuously per step (not static)
  3. Animation changes on step advance (new step, new animation)
  4. No flashing, no strobe, no rapid brightness changes observed
  5. Step card body is clearly readable at arm's length
  6. R5 check: all Phase R features (cadence gone, Read Me limit, Q&A gating, search) still functional — no regressions from S1/S2
  7. Both Child A (6) and Child B (4) sensory constraint satisfied — no animation triggers concern
- **Depends on:** S2

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Claude annotation latency at approve-time | >3s Claude response makes approve feel sluggish | 15s timeout with graceful fallback; `float` default means a slow call still ships a usable activity; monitor in UAT |
| OAuth token stale at annotation time | Claude call gets 401 | Annotation module treats 401 like any other error — logs warning, returns empty dict; same pattern as judge fallback |
| `shine` animation brightness constraint | `filter: brightness(1.4)` at 2.5s cycle — borderline for photosensitivity | UAT explicitly checks; if problematic, drop `shine` from the Claude vocabulary and replace with `pulse` |
| Persona_id is null for many activities | null activities get warm amber gradient and `float` fallback — acceptable but less differentiated | Documented as expected; future phases can add intent-based theming as a refinement |
| `spin` causing dizziness on 5s cycle | Even slow spin may feel wrong for 4-year-old Child B | UAT explicitly validates Child B's response; fallback to `wobble` if spin causes concern |
| Step card prominence breaking existing vitest snapshots | Font/shadow changes may cause snapshot mismatches | Implementer should update snapshots explicitly, not silently; check vitest output |
| `rewardAnimations.css` bundled via `RewardStep.tsx` only | Tree-shaking could exclude `RewardStep.tsx` from a render path, dropping the CSS classes; `PersonaAvatar.tsx` has no direct import | If in doubt, add `import "../animations/rewardAnimations.css"` to `PersonaAvatar.tsx` explicitly; confirmed import chain: `RewardStep.tsx → rewardAnimations.ts → rewardAnimations.css` (grep verified) |
| `metadata_json` UPDATE pattern in `post_approve` | No existing UPDATE in `post_approve` — only `_attempt_transition` sets columns; build-step agent may write ad-hoc SQL | Grep `UPDATE activity_steps SET metadata_json` for the existing K13 advance-path pattern before writing new SQL; cite source file:line in the diff |

## 9. Testing Strategy

**Backend (pytest):**
- `tests/unit/ai/test_animator.py`: unit tests with `StubClient` — verify prompt construction includes step bodies + persona, happy path returns valid `{seq: animation}` dict, unknown animation strings are filtered, exception path returns empty dict.
- `tests/integration/test_approve_animations.py`: integration test against a live SQLite test DB. POST to `/api/activities/{id}/approve` (using `StubClient` so no real Claude call), assert each `activity_steps` row has `avatar_animation` key in `metadata_json`. Also test that an approval failure (version conflict) does NOT persist partial annotation.
- Existing approve tests: verify they still pass — the annotation call must be skipped when `StubClient` returns empty dict, not crash.

**Frontend (vitest):**
- `theming.test.ts`: `gradientForPersona` returns distinct strings for all 6 inputs, returns the null fallback for unknown inputs, returns valid CSS gradient syntax.
- `PersonaAvatar.test.tsx`: renders `.avatar-animate-float` when `animationName` is omitted or unknown; renders `.avatar-animate-jump` when `animationName="jump"`; existing image/letter-circle branching unaffected.
- `App.test.tsx`: when activity has a step with `metadata.avatar_animation = "wobble"`, PersonaAvatar receives `animationName="wobble"`.

**iPad UAT (S3):** the only runtime validation for visual correctness. See Step S3 done-when checklist.
