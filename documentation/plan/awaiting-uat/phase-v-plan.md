# Phase V — Hybrid Toy Action Animation (SVD idle + CSS slot-entry)

## 1. What This Feature Does

Phase U shipped AnimateDiff-from-scratch animated WebPs but identity preservation was poor —
generated sprites bore little resemblance to the original toys. A three-way comparison of
approaches (A = CSS-only, B = AnimateDiff video-to-video, C = Stable Video Diffusion) showed:

- **A (CSS)**: perfect fidelity, mechanical 2D motion only
- **B (AnimateDiff v2v)**: correct identity, noticeably blurry
- **C (SVD)**: sharp identity preservation, organic natural motion, no slot-specific pose control

Phase V implements a hybrid:

1. **SVD idle loop** — Stable Video Diffusion generates a looping animated WebP for the `idle`
   slot only (~28 files). SVD animates from the existing static sprite, producing organic
   breathing/swaying motion while preserving the toy's exact appearance.

2. **CSS slot-entry animation** — when a step loads (or the slot changes), a one-shot CSS
   keyframe animation plays based on the slot name: `jumping` bounces in from below,
   `cheering` wiggles energetically, `waving` swings in from the side. After the intro
   completes (`onAnimationEnd`), the sprite settles to its static pose.

3. **WebP-first idle steady state** — after the CSS intro, if `slot === "idle"` and an SVD
   `.webp` is present, `ToyActionSprite` transitions to the animated WebP loop. All other
   slots remain as sharp static PNGs with no steady-state CSS loop (clean, no distraction
   during reading/listening steps).

The result: every step transition feels alive and responsive, the idle state shows genuine
organic character motion, and fine toy details (fur texture, distinctive features) are
preserved perfectly.

## 2. Existing Context

- **`scripts/compare_animate.py`**: compare harness with A/B/C implementations; `run_c()`
  contains working SVD generation code using `StableVideoDiffusionPipeline`. Reuse this
  for the batch script.
- **`scripts/batch_animate.py`**: existing AnimateDiff batch driver. Phase V adds a
  `--approach svd` flag that switches to SVD, restricts to `--slot idle`, and reads from
  the existing static PNGs (not reference photos).
- **`src/toybox/image_gen/animate.py`**: AnimateDiff wrapper. Phase V does NOT modify this —
  AnimateDiff remains available for future slot-specific work. SVD batch is a separate
  code path in `batch_animate.py`.
- **`frontend/src/child/components/ToyActionSprite.tsx`**: already has WebP-first fallback
  (Phase U). Phase V extends with: (a) `animating` state on mount/slot change, (b) CSS
  intro keyframes per slot, (c) post-intro WebP-for-idle logic.
- **SVD model**: `data/models/image_gen/svd/` — downloaded during Phase U comparison;
  `stabilityai/stable-video-diffusion-img2vid-xt`.
- **Static sprites**: all 10 slots for all ~28 toys in `data/images/toy_actions/<toy_id>/`.
  SVD reads from `<toy_id>/idle.png` (the existing cartoon sprite, not the raw photo).
- **Current WebP files**: `data/images/toy_actions/<toy_id>/<slot>.webp` — the poor-quality
  AnimateDiff outputs from Phase U. Phase V overwrites `idle.webp` only with SVD output.
  Non-idle WebPs remain on disk but are never loaded (CSS takes over for those slots).

## 3. Scope

**In scope:**
- `scripts/batch_animate.py` — add `--approach svd` flag; when used, reads static PNGs as
  input (not reference photos), runs SVD, writes output to same `<toy_id>/idle.webp` path
- `frontend/src/child/components/ToyActionSprite.tsx` — slot-entry CSS intro animation
  (one-shot on mount/slot change) + post-intro WebP-for-idle steady state
- `frontend/src/child/components/ToyActionSprite.test.tsx` — tests for intro animation state
  machine and idle WebP transition

**Out of scope:**
- Modifying `animate.py` (AnimateDiff path unchanged)
- Generating SVD animations for non-idle slots
- Replacing AnimateDiff infrastructure (kept for potential future use)
- Downloading SVD model (already present from Phase U comparison run)

## 4. Design Decisions

### SVD reads from static PNG, not reference photo
The static cartoon sprites (existing `idle.png` files) already went through IP-Adapter Plus
conditioning and look like clean cartoon characters. SVD on these is "animate this cartoon
character" — clear, consistent style. SVD on the raw toy photos would be "animate this
physical toy plushie" — the SVD output renders as a photorealistic 3D object, not a cartoon.

### Only idle slot gets SVD WebP
Idle is the steady state between step actions — the character sits and breathes while the
child reads the step card. Other action slots (`jumping`, `cheering`, etc.) need to clearly
communicate the action; CSS keyframe animations do this reliably with zero quality loss.
Generating SVD for action slots would produce natural motion that doesn't look like the named
action (SVD has no text conditioning), and the CSS intro already gives each action slot a
distinct visual identity.

### CSS intro is one-shot, not a loop
The action-slot animations play once on step entry, then freeze on the static PNG. A looping
CSS animation during a `reading` or `listening` step would be visually distracting. The
brief intro draws the child's attention to the new step, then settles.

### `onAnimationEnd` triggers WebP-for-idle (not a timer)
Using `onAnimationEnd` is more robust than a setTimeout because it respects the actual CSS
animation duration (which may change if the keyframe is tweaked) and avoids a race condition
where the WebP src loads before the intro completes.

## 5. CSS Animation Design

Each slot gets a named intro animation. All animations use `animation-fill-mode: forwards`
and `animation-iteration-count: 1` so they run once and hold the final frame.

| Slot | Intro animation | Duration | Character |
|---|---|---|---|
| `idle` | `fadeIn` | 0.4s | gentle appearance; SVD loop follows |
| `pointing` | `slideInLeft` | 0.5s | character slides in from left, arm already extended |
| `looking` | `tiltIn` | 0.5s | slight head-tilt as character appears |
| `jumping` | `bounceUp` | 0.6s | springs up from below the frame |
| `cheering` | `bounceWiggle` | 0.6s | bounces in with a wiggle |
| `thinking` | `floatIn` | 0.7s | floats in slowly, as if thoughtful |
| `waving` | `swingIn` | 0.5s | swings in from the side |
| `running` | `slideInFast` | 0.3s | fast slide from right, as if skidding to a stop |
| `sleeping` | `slowFadeIn` | 1.0s | very slow fade, sleepy entrance |
| `confused` | `wobbleIn` | 0.6s | wobbles side to side as it appears |

## 6. ToyActionSprite State Machine

```
mount / slot change
       │
       ▼
  [animating=true]
  intro CSS animation plays (one-shot, ~0.4–1.0s)
       │
       ▼ onAnimationEnd
  [animating=false]
  slot === "idle"?
    ├─ yes: format = "webp" → tries <toy_id>/idle.webp
    │       onError → format = "png" (WebP missing/failed)
    └─ no: format = "png" static (no loop)
```

Key: `animating` is a ref (not state) — toggling it adds/removes a CSS class without
triggering a re-render that would restart the animation.

## 7. Build Steps

### Step V1: ToyActionSprite CSS intro + idle WebP state machine
- **Umbrella:** #234
- **Type:** code
- **Status:** DONE (2026-06-07)
- **Problem:** Extend `ToyActionSprite.tsx` with:
  (a) A `SLOT_INTRO_ANIMATIONS` map (slot → CSS animation name) covering all 10 slots.
  (b) CSS keyframes for each intro animation in `ToyActionSprite.module.css`. Each
      slot's animation is targeted via attribute selector (e.g.
      `img[data-animating="jumping"] { animation: bounceUp 0.6s forwards; }`).
  (c) An `animating` boolean ref; set `true` on mount and on slot-prop change (via
      `useEffect`). While `animating=true`, the `<img>` carries a `data-animating="<slot>"`
      attribute (e.g. `data-animating="jumping"`). The CSS module targets this attribute
      for the slot-appropriate keyframe. Using a data attribute avoids CSS module
      class-name mangling in test assertions.
  (d) `onAnimationEnd` handler: sets `animating=false` (removes `data-animating`), then
      if `slot === "idle"` sets `format` state to `"webp"` (triggering the SVD loop
      attempt); otherwise leaves `format` as `"png"`.
  (e) The existing WebP-first `onError` fallback chain (webp → png → hidden) from Phase U
      is preserved — it only fires during the idle steady state.
  (f) Initial render uses `format="png"` during the animating phase; format only switches
      to `"webp"` after `onAnimationEnd` fires for the idle slot. The CSS intro animation
      plays on the sharp static PNG; the animated WebP activates after idle intro
      completes. **This is a breaking change from Phase U** (which started with
      `format="webp"`); existing tests must be updated (see below).
  Add tests:
  - `plays_intro_animation_on_mount`: assert `img.dataset.animating === slot` immediately
    after render (data-animating attribute present; avoids CSS module name mangling).
  - `clears_animating_on_animation_end`: fire `animationend` event, assert `data-animating`
    attribute is absent.
  - `transitions_to_webp_after_idle_intro`: fire `animationend` with slot=idle, assert src
    switches to `.webp`.
  - `stays_png_after_non_idle_intro`: fire `animationend` with slot=jumping, assert src
    stays `.png`.
  - `replays_intro_on_slot_change`: change `slot` prop, assert `data-animating` reappears
    with new slot value.
  Update existing Phase U tests (`ToyActionSprite.test.tsx`) — initial src is now `.png`
  (format starts as `"png"` during animation), not `.webp`:
  - `renders an <img> with the correct src + alt` → assert `.png` initial src
  - `falls back to png on webp 404` → WebP fallback fires only during idle steady state,
    not initial render; restructure to test idle-steady-state webp→png path
  - `appends ?v=<cacheKey> to the initial webp src` → assert initial `.png` src with key
  - `emits the bare webp URL with no query string when cacheKey is omitted` → assert
    initial `.png` src
- **Issue:** #235
- **Flags:** --reviewers code
- **Produces:**
  - `frontend/src/child/components/ToyActionSprite.tsx` — state machine + data-animating attribute
  - `frontend/src/child/components/ToyActionSprite.module.css` — all 10 keyframe animations (first CSS module in project; vitest handles via Vite transform natively)
  - `frontend/src/child/components/ToyActionSprite.test.tsx` — 5 new tests + 4 Phase U test updates
- **Done when:** `npm run test` passes including new tests; `npm run typecheck` clean;
  `npm run lint` clean
- **Depends on:** none

### Step V2: SVD idle batch
- **Type:** wait
- **Status:** DONE (2026-06-07) — code shipped at 241eaf0; batch pending operator run
- **Problem:** Add `--approach svd` flag to `scripts/batch_animate.py`. When `--approach svd`
  is used: reads the existing static `idle.png` for each toy as the input image (not the raw
  reference photo), runs `StableVideoDiffusionPipeline`, writes the animated WebP to
  `data/images/toy_actions/<toy_id>/idle.webp` (overwriting the Phase U AnimateDiff output).
  After adding the flag, run the full idle batch for all toys.
  The `run_c()` implementation in `scripts/compare_animate.py` is the reference; adapt it
  into `batch_animate.py`'s existing job-loop structure. Note: `run_c()` does not pass
  `local_files_only=True` to `from_pretrained()`; the batch path must pass it explicitly
  to prevent network access when running offline with the model already on disk.
- **Issue:** #236
- **Flags:** --reviewers code
- **Produces:**
  - `scripts/batch_animate.py` — `--approach svd` flag + SVD generation path
  - ~28 `.webp` files overwritten at `data/images/toy_actions/<toy_id>/idle.webp`
- **Done when:** `uv run python scripts/batch_animate.py --approach svd --slot idle --dry-run`
  lists all non-archived toys; full batch completes with 0 failures; a sample
  `idle.webp` opened in browser shows the character moving (not a static frame);
  `uv run ruff check scripts/batch_animate.py` clean; `uv run mypy src` clean
- **Depends on:** V1 (CSS animations can be tested independently; SVD WebPs only matter once
  the `onAnimationEnd` → webp transition is wired)

### Step V3: iPad UAT — hybrid animation
- **Type:** operator
- **Problem:** Validate the hybrid animation on real iPad hardware with both children.
- **Issue:** #237
- **Produces:** UAT run-doc at `documentation/runs/2026-06-<date>-phase-v-uat.md`
- **Done when:** Operator confirms on iPad:
  1. Step loads → intro animation plays once for the active slot (e.g. jumping bounces in)
  2. After intro: idle slot shows SVD loop (character sways/breathes organically)
  3. After intro: non-idle slots show static PNG (no ongoing animation during reading)
  4. Switching steps replays the intro for the new slot
  5. Force-delete one `idle.webp` → intro plays, then falls back gracefully to static PNG
  6. Both Child A (6) and Child B (4) react positively; no sensory concern from motion
  7. T1 bundled UAT (R5+S3+O1-O3) and T4 catalog UAT cleared in same session if pending
- **Depends on:** V2

## 8. Risks

| Item | Risk | Mitigation |
|---|---|---|
| SVD output still lacks slot-specific pose | SVD generates natural motion, not named actions | Accepted — CSS intro communicates the action clearly on entry; SVD only runs for idle |
| `onAnimationEnd` fires before CSS is applied in test env | JSDOM CSS animation timing is unreliable | Use `fireEvent.animationEnd` in tests; don't rely on actual timing |
| Overwriting Phase U idle WebPs | 28 files replaced; no rollback | Phase U WebPs were poor quality; SVD is strictly better. Batch is re-runnable with --force |
| SVD `enable_model_cpu_offload` + Windows | CPU offload is slower on Windows; no known error | Matches compare_animate.py which already ran successfully on this machine |
| CSS module class-name mangling in tests | CSS modules transform class names; `.classList.contains('animating')` would fail | Use `data-animating="<slot>"` attribute as the test hook; CSS targets attribute selectors. No existing `.module.css` files in project — first-use; vitest handles CSS modules via Vite transform natively without extra config |

## Manual UAT

*Generated by /build-phase on 2026-06-07. Append-only; re-running the phase adds new items below, never modifies existing ones.*

### M1: iPad UAT — hybrid animation
- **Source step:** Step V3 (from this plan's §7)
- **Issue:** #237
- **Commands to run:**
  ```powershell
  # Start backend and frontend, open on iPad browser at http://<LAN_IP>:4000/child
  # (requires TOYBOX_LAN_IP set + parent PIN configured)
  uv run python -m toybox.main --host 0.0.0.0 --port 8000
  cd frontend; npm run dev
  ```
- **What you're looking for:**

  | Check | Expected outcome |
  |---|---|
  | Step loads | Intro animation plays once for the active slot (e.g. jumping bounces in from below) |
  | After idle intro | Slot=idle shows SVD WebP loop (character sways/breathes organically) |
  | After non-idle intro | Non-idle slots show static PNG — no ongoing animation during reading/listening |
  | Step change | Switching steps replays the intro animation for the new slot |
  | WebP missing | Force-delete one `idle.webp` → intro plays, then falls back gracefully to static PNG |
  | Child A (6) | Reacts positively; no sensory concern from motion |
  | Child B (4) | Reacts positively; no sensory concern from motion |
  | T1 bundled UAT | R5+S3+O1-O3 cleared in same session if pending (#223) |
  | T4 catalog UAT | Catalog UAT cleared in same session if pending (#226) |
  | U4 UAT | U4 iPad UAT cleared in same session if pending (#232) |
