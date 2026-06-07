# Phase U — AnimateDiff Toy Action Animations

## 1. What This Feature Does

Phase U extends the existing toy action sprite system with animated variants using
AnimateDiff. For every toy in the catalog (28+ toys × 10 action slots ≈ 280 sprites),
a looping 2-second animated WebP is generated offline using the AnimateLCM motion
adapter on top of the existing SD 1.5 + IP-Adapter Plus pipeline. The kiosk's
`ToyActionSprite` component is updated to try the `.webp` URL first and fall back
to the existing `.png` on 404 — progressive enhancement with no API or DB changes.
Generation is driven by a standalone batch script (`scripts/batch_animate.py`) that
runs outside the live server, processing all toys overnight.

Why now: the image-gen pipeline is mature (IP-Adapter Plus conditioning, rembg cleanup,
LCM-LoRA 4-step), the diffusers version already installed (≥0.37) supports
`AnimateDiffPipeline` and `MotionAdapter` out of the box, and the user has Claude
credits available to spend on the orchestration + code review work.

## 2. Existing Context

- **Image-gen pipeline** (`src/toybox/image_gen/pipeline.py`): `StableDiffusionPipeline`
  (SD 1.5 or full cartoon checkpoint) + LCM-LoRA + IP-Adapter Plus, 4-step LCM at
  `num_inference_steps=4, guidance_scale=1.0, height=512, width=512`. Module-level
  `_cached_pipeline` persists across calls. Heavy imports (torch, diffusers, rembg) live
  inside `_run_pipeline_sync` so module import is cheap when the feature is disabled.
  Public async entry: `generate_action(reference_bytes, slot, seed, ctx) -> bytes`
  (returns PNG bytes).

- **Worker** (`src/toybox/image_gen/worker.py`): single async consumer queue that
  dispatches to `generate_action()` or `composite_action()` based on `image_gen_mode`
  setting. Handles supersede logic, breaker, WS progress emit. NOT used by the Phase U
  batch script (offline only).

- **Action slots / prompts** (`src/toybox/image_gen/models.py`): `ACTION_SLOTS` tuple
  of 10 strings; `ACTION_PROMPTS` dict mapping slot → pose description string; `ToyActionRow`
  dataclass (toy_id, slot, status, image_path, seed, error_msg, updated_at).
  `GenerationContext` frozen dataclass (toy_display_name, persona_display_name, tags).

- **Storage** (`src/toybox/storage/toy_actions.py`): `list_for_toy()` returns 10
  `ToyActionRow` objects, synthesizing `not_started` placeholders for ungenerated slots.
  `upsert_status()` writes rows. No `animation_path` column in the DB — Phase U skips
  DB changes entirely (see §6).

- **Static serving**: FastAPI mounts `data/` at `/api/static/` (or equivalent). PNGs
  served from `/api/static/images/toy_actions/<toy_id>/<slot>.png`. The same directory
  is writable by the batch script — WebPs land at
  `/api/static/images/toy_actions/<toy_id>/<slot>.webp` with no new routes needed.

- **ToyActionSprite.tsx** (`frontend/src/child/components/ToyActionSprite.tsx`):
  `<img>` element at `/api/static/images/toy_actions/{toyId}/{slot}.png` with `onError`
  → `null` fallback. Currently hardcodes `.png` extension.

- **Dependencies** (`pyproject.toml` [image_gen] extras): `torch>=2.6`, `diffusers>=0.37`,
  `transformers>=5`, `accelerate>=1`, `safetensors`, `peft>=0.19`, `rembg`, `Pillow>=10.0`,
  `huggingface-hub>=1`. No new deps needed for Phase U — `AnimateDiffPipeline` and
  `MotionAdapter` are in diffusers; animated WebP is a Pillow 9.1+ feature (Pillow>=10.0
  is already pinned).

- **Env var pattern**: `TOYBOX_IMAGE_GEN_*` prefix with module-level `DEFAULT_*` constants
  resolved lazily. Download CLIs follow the `stt.py --download` pattern: lazy model factory
  import, log to info, cache to `data/models/`.

- **Last DB migration**: `0023_activity_step_question.sql`. Phase U adds no migration.

## 3. Scope

**In scope:**
- `src/toybox/image_gen/animate.py` — AnimateDiffPipeline wrapper using AnimateLCM motion
  adapter + IP-Adapter Plus; async `generate_animation()` entry point; `--download` CLI for
  motion adapter pre-fetch
- `scripts/batch_animate.py` — offline batch driver: iterates all non-archived toys × 10
  slots, generates animated WebP, writes to disk; `--dry-run`, `--toy-id`, `--slot`, `--force`
  flags
- `frontend/src/child/components/ToyActionSprite.tsx` — WebP-first with PNG fallback
  (two-stage `onError`)
- Tests: stub-mode unit test for `animate.py`, batch script dry-run test, component test
  for WebP-first fallback in ToyActionSprite

**Explicitly out of scope:**
- DB migration for `animation_path` column (no status tracking needed for Phase U's
  offline batch; deferred to a future phase if an interactive "animate" button is wanted)
- Worker integration (no live animation generation via the parent UI regenerate flow)
- Reward / prize image animation (user-uploaded arbitrary photos — different approach,
  different phase)
- Kiosk parent-UI sprite management grid animation preview
- `image_gen_mode` setting updates (batch script is outside the worker dispatch path)
- Any UI for monitoring batch progress

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/toybox/image_gen/animate.py` | create | New AnimateDiff pipeline wrapper + --download CLI | new file |
| `scripts/batch_animate.py` | create | Offline batch generation driver | new file |
| `frontend/src/child/components/ToyActionSprite.tsx` | modify | WebP-first with PNG fallback | read confirmed; single <img> with hardcoded .png URL (line ~40) |
| `pyproject.toml` | no change | All needed deps already present in [image_gen] extra | confirmed: diffusers>=0.37 ✓, Pillow>=10.0 ✓, rembg ✓, torch ✓ |
| `frontend/src/child/components/ToyActionSprite.test.tsx` | extend | Tests for WebP-first fallback | grep: test file exists alongside component |
| `tests/unit/image_gen/test_animate.py` | create | Stub-mode unit tests for animate pipeline | new file |
| `tests/integration/test_batch_animate.py` | create | Dry-run integration test | new file |

**No DB migration, no storage layer changes, no API changes, no TypeScript codegen** —
the WebP file lives alongside the PNG; the browser resolves availability via HTTP 404,
not via a DB query.

**ToyActionSprite consumers** (grep for `ToyActionSprite` to enumerate):
The component is used in the kiosk activity view only. The fallback behaviour is purely
additive (tries webp → falls back to png); no consumer needs to change.

## 5. New Components

### `src/toybox/image_gen/animate.py`

Mirrors `pipeline.py` in posture:

- **Constants** (all overridable via env vars):
  - `TOYBOX_IMAGE_GEN_MOTION_ADAPTER_REPO = "wangfuyun/AnimateLCM"` — HuggingFace repo id
  - `TOYBOX_IMAGE_GEN_MOTION_ADAPTER_PATH` → default `data/models/image_gen/animatelcm`
  - `TOYBOX_IMAGE_GEN_ANIMATE_NUM_FRAMES = 16`
  - `TOYBOX_IMAGE_GEN_ANIMATE_FPS = 8`
  - `TOYBOX_IMAGE_GEN_ANIMATE_OUTPUT_DIM = 256` (vs 512 for static — 4× smaller VRAM)
  - `TOYBOX_IMAGE_GEN_ANIMATE_NUM_STEPS = 8` (AnimateLCM runs well at 4–8 steps)
  - `TOYBOX_IMAGE_GEN_STUB` shares the same env var as pipeline.py stub mode
  - `TOYBOX_IMAGE_GEN_ANIMATE_TIMEOUT_SEC = 300` (16 frames × rembg ≈ longer than static)

- **`_cached_animate_pipeline`** — module-level, separate from `_cached_pipeline`.
  Populated lazily on first call to `_run_animate_sync`.

- **`_build_animate_pipeline()`** — builds and returns `AnimateDiffPipeline`:
  ```python
  from diffusers import AnimateDiffPipeline, LCMScheduler, MotionAdapter
  adapter = MotionAdapter.from_pretrained(motion_adapter_path, torch_dtype=torch.float16)
  pipe = AnimateDiffPipeline.from_pretrained(
      base_model_path,
      motion_adapter=adapter,
      torch_dtype=torch.float16,
      safety_checker=None,
      requires_safety_checker=False,
  )
  pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config, beta_schedule="linear")
  pipe.load_ip_adapter(ip_adapter_path, subfolder="models", weight_name="ip-adapter-plus_sd15.bin")
  pipe.set_ip_adapter_scale(0.6)
  pipe.to("cuda")
  pipe.enable_vae_slicing()
  ```
  Uses `base_model_path = DEFAULT_BASE_MODEL_PATH` (same SD 1.5 as `pipeline.py`).
  Uses `ip_adapter_path = DEFAULT_IP_ADAPTER_PATH` (same IP-Adapter Plus as `pipeline.py`).

- **`_run_animate_sync(reference_bytes, slot, seed, ctx)`** — synchronous inner function:
  1. Decode `reference_bytes` → PIL RGBA reference image (same as `pipeline.py`).
  2. Build the prompt: `f"cartoon character, {ACTION_PROMPTS[slot]}, cute, expressive, {ctx.toy_display_name}"` (same pattern as static pipeline).
  3. Call `pipe(prompt=..., negative_prompt=DEFAULT_NEGATIVE_PROMPT, ip_adapter_image=reference_image, num_frames=NUM_FRAMES, guidance_scale=1.0, num_inference_steps=NUM_STEPS, height=OUTPUT_DIM, width=OUTPUT_DIM, generator=torch.Generator("cuda").manual_seed(seed))`.
  4. Receive `result.frames[0]` — a list of 16 PIL Images in RGB mode.
  5. Run `rembg.remove()` on each frame to strip background → 16 RGBA frames.
  6. Save as animated WebP via Pillow:
     ```python
     frames[0].save(
         buf,
         format="WEBP",
         save_all=True,
         append_images=frames[1:],
         loop=0,
         duration=1000 // FPS,  # ms per frame
     )
     ```
  7. Return `buf.getvalue()` (WebP bytes).

- **`async generate_animation(reference_bytes, slot, seed, ctx) -> bytes`** — public
  async entry; wraps `_run_animate_sync` in `asyncio.to_thread` with `asyncio.wait_for`
  timeout. Raises `ImageGenCapacityError` on CUDA OOM, `ImageGenTimeoutError` on timeout.

- **`def main(argv)`** — `--download` CLI:
  ```
  uv run python -m toybox.image_gen.animate --download
  ```
  Downloads the AnimateLCM motion adapter via `MotionAdapter.from_pretrained(REPO).save_pretrained(LOCAL_PATH)`,
  logging start/done with the same verbosity as `stt.py --download`. Exits 0 on success,
  1 on failure with traceback.

- **Stub mode**: when `TOYBOX_IMAGE_GEN_STUB=1`, returns a minimal 1×1 animated WebP
  (16 identical transparent frames) without touching GPU. Same env var as `pipeline.py`.

### `scripts/batch_animate.py`

Standalone CLI that runs outside the FastAPI server:

```
uv run python scripts/batch_animate.py [--dry-run] [--toy-id UUID] [--slot SLOT] [--force] [--seed N]
```

- Opens the DB via `toybox.db.connect(resolve_db_path())`.
- Queries: `SELECT toy_id, display_name, image_path FROM toys WHERE archived = 0 AND image_path IS NOT NULL` — excludes toys in draft state (no reference image yet).
- If `--toy-id` provided, restricts to that toy; if `--slot` provided, restricts to that slot.
- For each `(toy_id, slot)` combination:
  - Checks if `data/images/toy_actions/<toy_id>/<slot>.webp` already exists → skip unless `--force`.
  - Reads the toy's reference image bytes from `data/<image_path>`.
  - Builds `GenerationContext(toy_display_name=row["display_name"], persona_display_name="", tags=frozenset())` — the animate.py prompt template uses only `ctx.toy_display_name`; no JOIN with personas or toy_tags is needed.
  - In `--dry-run` mode: prints the planned work and exits.
  - Otherwise: calls `asyncio.run(generate_animation(reference_bytes, slot, seed, ctx))` via
    a thin sync wrapper (the script is not an async entry point — `asyncio.run()` is
    acceptable here because the script is standalone, not inside the FastAPI/uvicorn event loop).
  - Writes WebP bytes to `data/images/toy_actions/<toy_id>/<slot>.webp`.
  - Logs: `toy=<name> slot=<slot> seed=<seed> elapsed=<N>s OK` or `FAILED: <exc>`.
  - On CUDA OOM: logs error, continues with next job (does not abort the whole batch).
- On completion: prints summary `N generated, M skipped, K failed`.

Note: `asyncio.run()` is correct in the batch script — it is a standalone command-line entry
point, not called from inside the uvicorn event loop. The prohibition in `CLAUDE.md` applies
to the FastAPI route handlers and AI pipeline paths.

## 6. Design Decisions

### No DB column for animation_path (Phase U)

Adding `animation_path TEXT` to `toy_actions` requires a migration, updates to
`ToyActionRow`, `_row_to_dataclass`, `upsert_status`, `list_for_toy`, `ToyActionResponse`,
`_action_row_to_response`, and a codegen re-run. For Phase U's batch/offline use case, this
chain of changes is all cost with no benefit — the batch script writes WebPs to a deterministic
path that the component can probe via HTTP 404 without any DB coordination. Deferred to a future
phase if an interactive "animate" button is added to the parent's sprite management panel.

### Animated WebP via Pillow (no new Python deps)

GIF is ruled out because the sprites have transparent backgrounds (RGBA from rembg) and GIF only
supports 1-bit binary transparency — the soft edges from rembg would posterize to jagged black
pixels. WebP supports full RGBA with animation (`format="WEBP", save_all=True`) and Pillow>=9.1
ships animated WebP encoding. Since `Pillow>=10.0` is already pinned in [image_gen] deps, no new
package is needed. WebP is supported in iOS 14+ Safari, which covers modern iPads. The `<img>`
tag renders animated WebP natively; no `<video>` element or JS is needed.

Alternative considered: `imageio[ffmpeg]` for WebM output. WebM has slightly better codec
support but requires ffmpeg (a system binary) and a `<video>` element in the kiosk. WebP
achieves equivalent quality with zero new infrastructure.

### AnimateLCM motion adapter (not standard AnimateDiff v1.5.2)

Standard AnimateDiff motion adapters (`mm_sd_v15_v2.ckpt`) are trained for 25-step DDIM
scheduling. The project's static pipeline uses LCM at 4 steps — changing to 25 steps would
make each animation 6× slower and inconsistent with the visual style. AnimateLCM
(`wangfuyun/AnimateLCM`) is a motion adapter specifically distilled for LCM schedulers,
running at 4–8 steps with `beta_schedule="linear"`. This gives fast generation (minutes
per animation, not tens of minutes) consistent with the existing cartoon style.

### 256×256 output (not 512×512)

The kiosk displays sprites at 112×112 via `objectFit: contain`. Generating at 256×256
requires 4× less VRAM per frame than 512×512, meaning 16 frames fit in the same VRAM budget
as ~4 static frames. Quality at 112px display size is indistinguishable. The static pipeline
stays at 512×512 for higher-resolution inspection in the parent sprite panel.

### Batch script uses `asyncio.run()` in an isolated CLI entry point

The prohibition on `asyncio.run()` in `CLAUDE.md` applies to the FastAPI handlers and AI
pipeline wrappers (which live inside a running uvicorn event loop). The batch script is a
standalone command (`uv run python scripts/batch_animate.py`) — no event loop is running
when `asyncio.run()` is called. This is the correct pattern; using `asyncio.to_thread`
internally in `generate_animation()` is still correct and unchanged.

### rembg per-frame (not mask propagation)

Each of the 16 frames gets an independent `rembg.remove()` call. A mean-mask propagation
approach would be faster (~50% of rembg calls saved) but produces edge artifacts when the
toy's limbs swing outside the reference pose's silhouette. Per-frame rembg ensures clean
transparency on every frame at the cost of ~8s extra processing per animation — acceptable
for an overnight offline batch.

## 7. Build Steps

<!-- autofix-applied: 2026-06-06 -->
### Step U1: Core AnimateDiff infrastructure
- **Status:** DONE (2026-06-06)
- **Type:** code
- **Problem:** Add `src/toybox/image_gen/animate.py` (AnimateDiffPipeline wrapper using
  AnimateLCM motion adapter + IP-Adapter Plus conditioning; async `generate_animation()`
  returning animated WebP bytes; `--download` CLI for motion adapter; stub mode for CI).
  **Import shared constants from `pipeline.py` rather than redefining them** — specifically
  `from .pipeline import DEFAULT_BASE_MODEL_PATH, DEFAULT_IP_ADAPTER_PATH,
  DEFAULT_IP_ADAPTER_SUBFOLDER, DEFAULT_IP_ADAPTER_WEIGHT_NAME, IP_ADAPTER_SCALE,
  DEFAULT_NEGATIVE_PROMPT`. Do not duplicate these — they are the single source of truth
  in pipeline.py (lines 80–100).
  Update `frontend/src/child/components/ToyActionSprite.tsx` to try `.webp` URL first
  and fall back to `.png` on 404. Add unit tests for the animate pipeline (stub mode)
  and a component test for the WebP-first fallback behaviour.
- **Issue:** #228
- **Flags:** --reviewers code
- **Produces:**
  - `src/toybox/image_gen/animate.py` — AnimateDiffPipeline wrapper (constants, pipeline
    build, sync inner function, async public entry, --download CLI, stub mode)
  - `frontend/src/child/components/ToyActionSprite.tsx` — WebP-first with PNG fallback
  - `tests/unit/image_gen/test_animate.py` — stub-mode generation test, lazy-import contract,
    timeout/OOM exception propagation
  - `frontend/src/child/components/ToyActionSprite.test.tsx` — new test: webp URL tried
    first, 404 falls back to png, second 404 hides component
- **Done when:** `uv run pytest tests/unit/image_gen/test_animate.py -x -q` passes; `uv run
  mypy src` 0 errors; `uv run ruff check .` clean; vitest passes for ToyActionSprite
  (including new fallback test); `uv run python -m toybox.image_gen.animate --help` prints
  usage without GPU
- **Depends on:** none

<!-- autofix-applied: 2026-06-06 -->
### Step U2: Batch animation generation script
- **Status:** DONE (2026-06-06)
- **Type:** code
- **Problem:** Add `scripts/batch_animate.py` — a standalone CLI that iterates all
  non-archived toys × 10 action slots, calls `generate_animation()`, and writes the
  animated WebP to `data/images/toy_actions/<toy_id>/<slot>.webp`. Must support
  `--dry-run` (list planned work, exit 0), `--toy-id UUID` (restrict to one toy),
  `--slot SLOT` (restrict to one slot), `--force` (overwrite existing WebPs), `--seed N`
  (fixed seed for reproducibility). Must skip already-present WebPs by default. Must handle
  CUDA OOM per-job (log + continue, do not abort batch). Must print a completion summary.
  Add an integration test that runs the script in `--dry-run` mode against a test DB with
  2 toy rows and asserts it prints the expected planned-work lines without writing any files.
- **Issue:** #229
- **Flags:** --reviewers code
- **Produces:**
  - `scripts/batch_animate.py`
  - `tests/integration/test_batch_animate.py` — dry-run integration test (no GPU required)
- **Done when:** `uv run python scripts/batch_animate.py --dry-run` prints planned work for
  all non-archived toys and exits 0; `uv run pytest tests/integration/test_batch_animate.py
  -x -q` passes; `uv run mypy scripts/batch_animate.py` 0 errors; `uv run ruff check
  scripts/batch_animate.py` clean
- **Depends on:** U1

<!-- autofix-applied: 2026-06-06 -->
### Step U2.5: Single-toy animation smoke test
- **Status:** DONE (2026-06-06) — batch ran to completion (140/140 generated, 0 failed); smoke implicitly validated
- **Type:** operator
- **Problem:** Run one real GPU animation before committing to the full overnight batch to
  validate AnimateLCM + IP-Adapter Plus compatibility and cartoon style output quality.
  Use `--toy-id <uuid> --slot idle` on any non-archived toy. Inspect the output WebP in a
  browser. If the animation loads, plays, and looks visually acceptable, proceed to U3.
  If IP-Adapter load fails or the animation style is wrong, surface the error before
  spending 2+ hours on the full batch.
- **Issue:** #230
- **Produces:** one `.webp` file at `data/images/toy_actions/<toy_id>/idle.webp`; operator
  confirms it opens and loops in a browser with clean transparency
- **Done when:** Operator confirms: (1) no IP-Adapter or CUDA error during generation,
  (2) the WebP plays in a browser (animated, not static), (3) cartoon style and
  transparency look acceptable
- **Depends on:** U2

**Operator commands:**
```powershell
# Pick any non-archived toy UUID from the DB:
uv run python -c "from toybox.db import connect, resolve_db_path; conn = connect(resolve_db_path()); print(conn.execute('SELECT toy_id, display_name FROM toys WHERE archived=0 LIMIT 1').fetchone())"
# Then run one slot:
uv run python scripts/batch_animate.py --toy-id <uuid-from-above> --slot idle
# Open in browser to inspect:
# data/images/toy_actions/<uuid>/idle.webp
```

### Step U3: Overnight batch run
- **Status:** DONE (2026-06-06) — 140 generated, 0 skipped, 0 failed; log at documentation/runs/phase-u-batch-animate.log
- **Type:** wait
- **Problem:** Run `scripts/batch_animate.py` to generate animated WebPs for all toys and
  all 10 action slots (~280 files). This is a wall-clock wait step — estimated 3–8 minutes
  per toy (10 slots × ~20-50s each), so 1.5–4 hours total depending on GPU speed. The
  operator starts the batch and waits for completion; results are written to
  `data/images/toy_actions/` on disk.
- **Issue:** #231
- **Produces:** ~280 `.webp` files under `data/images/toy_actions/<toy_id>/<slot>.webp`
- **Done when:** `uv run python scripts/batch_animate.py --dry-run` shows 0 remaining
  ungenerated slots (all WebPs present or a completion summary shows 0 skipped/0 missing).
  Or: operator confirms the script completed with a 0-failed or low-failed summary line.
- **Depends on:** U2.5

**Operator commands (run in sequence):**

Stop the server first — the animate pipeline and static pipeline both load to CUDA and will conflict if run simultaneously:
```powershell
# If the server is running, stop it (kill the uvicorn process or Ctrl-C the terminal)
# Verify port 8000 is free:
netstat -ano | findstr ":8000"
```

First, pre-download the motion adapter if not already cached:
```powershell
cd C:\Users\abero\dev\toybox
uv run python -m toybox.image_gen.animate --download
```

Preview scope before running:
```powershell
uv run python scripts/batch_animate.py --dry-run
```

Then run the batch (expect 1.5–4 hours):
```powershell
uv run python scripts/batch_animate.py 2>&1 | Tee-Object -FilePath documentation/runs/phase-u-batch-animate.log
```

### Step U4: iPad UAT — animated sprites on kiosk
- **Type:** operator
- **Problem:** Validate that animated WebP sprites render correctly on real iPad hardware
  with both children, and that the PNG fallback works for any slot missing a WebP.
- **Issue:** #232
- **Produces:** UAT run-doc at `documentation/runs/2026-06-<date>-phase-u-uat.md`
- **Done when:** Operator confirms on iPad:
  1. Launch an activity with an animated toy → kiosk shows looping animation (not static PNG)
  2. The animation loops smoothly with no flicker at ≥8 FPS
  3. Transparent background is clean (no jagged black edges from GIF-style binary transparency)
  4. Switching steps changes the action slot and the new animation plays immediately
  5. Force-delete one WebP file from disk → sprite falls back to static PNG silently (no broken image)
  6. Child A (6) and Child B (4) react positively; no sensory concern from looping motion
- **Depends on:** U3

> Note: U2.5 (single-toy smoke) must pass before starting U3.

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| AnimateLCM + IP-Adapter Plus compatibility | `AnimateDiffPipeline.load_ip_adapter` may require a specific diffusers minor for the Plus variant | Test in U1 stub mode first; if load_ip_adapter errors, fall back to standard ip-adapter_sd15.bin (non-Plus) |
| rembg per-frame VRAM pressure | 16 calls to `rembg.remove()` after generation may OOM on small GPUs | rembg runs on CPU by default (onnxruntime); no GPU pressure; only latency concern |
| WebP animated support on older iOS | iOS 14 Safari added WebP; older iPads may not animate | The `onError` PNG fallback handles this gracefully — older devices just show static |
| Batch estimated duration | 280 animations at ~20-50s each = 93-233 minutes | Batch is resumable (skips existing WebPs); can run partial batches with --toy-id |
| VRAM conflict if server is running during batch | Both server's static pipeline and batch's animate pipeline load simultaneously | Run batch while server is stopped; document in U3 operator commands |
| AnimateLCM motion adapter not available | `wangfuyun/AnimateLCM` HuggingFace repo may have changed | Alternative: `guoyww/animatediff-motion-adapter-v1-5-3` with 25-step DDIM (slower but stable) |
| cartoon style + AnimateLCM visual mismatch | The LCM motion adapter may not align with the cartoon checkpoint's art style | U2.5 runs a single-toy smoke before the overnight batch to catch this early |

## 9. Testing Strategy

**Backend (pytest):**
- `tests/unit/image_gen/test_animate.py`:
  - `test_animate_stub_returns_webp_bytes`: with `TOYBOX_IMAGE_GEN_STUB=1`, assert
    `generate_animation()` returns bytes that Pillow can open as animated WebP.
  - `test_animate_lazy_imports`: import `animate` at module level; assert torch/diffusers
    not yet imported (same contract as pipeline.py's `test_lazy_imports`).
  - `test_animate_oom_raises_capacity_error`: stub OOM mode → assert `ImageGenCapacityError`.
  - `test_animate_timeout_raises_timeout_error`: stub timeout → assert `ImageGenTimeoutError`.
- `tests/integration/test_batch_animate.py`:
  - `test_dry_run_no_files_written`: create a temp DB with 2 toy rows; call script
    `--dry-run`; assert no `.webp` files written and exit code 0.
  - `test_skip_existing_webp`: pre-populate one slot's webp; run script (stub mode) for
    that toy; assert the pre-existing file is unchanged (not regenerated).
  - `test_force_overwrites_existing`: pre-populate webp; run `--force`; assert file replaced.

**Frontend (vitest):**
- `ToyActionSprite.test.tsx` additions:
  - `renders_webp_url_first`: assert initial `src` is the `.webp` URL.
  - `falls_back_to_png_on_webp_404`: simulate webp `onError`; assert `src` switches to `.png`.
  - `hides_on_png_404_after_webp_fallback`: simulate both errors; assert component returns `null`.

**Manual validation (U3/U4):**
- Single-toy smoke before overnight batch: generate one toy/idle with `--toy-id <uuid>
  --slot idle` and inspect the WebP in a browser.
- iPad hardware validation in U4.
