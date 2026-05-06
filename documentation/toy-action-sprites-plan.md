# Phase F — Toy action sprites (child-side enrichment, post-v1)

> Sibling expansion. Master plan ([plan.md](plan.md)) stays canonical for v1 scope; this doc carries the per-step `**Problem:**/**Type:**/**Issue:**/**Flags:**/**Status:**/**Done when:**` shape that `/build-phase` parses. Sequenced independently of [phase-e-plan.md](phase-e-plan.md) — Phase E and Phase F share migrations 0004/0005 numerically; whichever ships first claims those numbers and the other renumbers at build time (see §"Risks and open questions").

## Build status (2026-05-06): 8 GB feasibility CONFIRMED — Phase F fully unblocked

Host GPU pre-flight found NVIDIA RTX 4070 Laptop, 8 GB total VRAM (driver 581.95) — below the 12 GB floor originally declared. Started in **Option B** mode (F2–F8 against the stub-runtime fixture), then ran an empirical probe to test 8 GB feasibility per [`runs/2026-05-06-phase-f-8gb-feasibility.md`](runs/2026-05-06-phase-f-8gb-feasibility.md). Probe **passed**: full SDXL + IP-Adapter ViT-H + pixel-art LoRA stack lands at **6.11 GB peak / 30.2 s wall-clock** at 1024×1024 fp16 with `enable_model_cpu_offload()` + `pipe.vae.enable_slicing()`. Output ([visual evidence](runs/2026-05-06-vram-probe-output.png)) is a clean pixel-art sprite with subject identity preserved from the reference photo.

**F1 + F9 + F10 are unblocked on this host.** The plan's `TOYBOX_IMAGE_GEN_MIN_VRAM_GB` default stays at 12 (matches headroom-host posture); 8 GB hosts override via `.env` after running the probe. F10's wall-clock projection drops from overnight (10-15h) to ~2.5 hours given the measured 30 s/sprite rate.

Three diffusers gotchas discovered during the probe — these are **mandatory** for F2's `pipeline.py` implementation:
1. The CLIP image encoder must be loaded explicitly via `CLIPVisionModelWithProjection.from_pretrained("h94/IP-Adapter", subfolder="models/image_encoder")` and passed to `StableDiffusionXLPipeline.from_pretrained(image_encoder=...)`. Otherwise IPA crashes with shape mismatch (`mat1 and mat2 shapes cannot be multiplied (2x1280 and 1024x8192)`).
2. **Do NOT call `pipe.enable_attention_slicing()`** — it overwrites IPA-aware attention processors with sliced ones, crashing generation at the first `attn2` call (`AttributeError: 'tuple' object has no attribute 'shape'`). PyTorch 2.4+ SDPA already provides memory-efficient attention.
3. Use `pipe.vae.enable_slicing()` (canonical) not the deprecated pipeline-level `enable_vae_slicing()`.

Migration numbers settled at build time: F3 takes **0005** (next available after 0004 from Phase E #40); F6 takes **0006**.

## What this feature does

Generate a small library of pixel-art "action sprites" of each ingested toy — the same toy posed in 10 fixed actions (idle, pointing, looking through a magnifying glass, jumping, cheering, thinking, waving, running, sleeping, confused). One sprite is rendered next to the step text on the child kiosk every time a new step is shown, so "Mr. Unicorn says: get the magnifying glass and look around the room!" pairs with a 16-bit pixel-art Mr. Unicorn holding a magnifying glass.

The persona avatar stays at the top of the kiosk (unchanged); the new sprite slots in next to the step body. Parent toy detail page renders the 10 sprites as a 2×5 grid with per-slot status badges and "regenerate" buttons. All generation happens locally on the home GPU (no toy photos leave the device).

## Existing context

- **Toys** are stored in [toys](../src/toybox/db/migrations/0001_initial.sql#L19-L30) with one image at `data/images/toys/<uuid>.<ext>`, plus `display_name`, `tags`, `persona_id`. The source photo is kept on commit (`commit_staging` in [storage/images.py](../src/toybox/storage/images.py#L496-L534)) so regeneration can re-read it without re-uploading. Ingest pipeline + dedup + vision-suggest live in [storage/images.py](../src/toybox/storage/images.py) and [api/toys.py](../src/toybox/api/toys.py).
- **Vision** is currently Claude Haiku via OAuth ([ai/toy_vision.py](../src/toybox/ai/toy_vision.py)) — text out only. Subscription OAuth has no image-generation surface, so this feature requires a local diffusion runtime in-process (matches the [audio/stt.py](../src/toybox/audio/stt.py) "heavy work in `asyncio.to_thread`" pattern; does NOT add a separate ComfyUI/A1111 process).
- **Activities** stream steps to the child kiosk via WS `Topic.activity`. Each step row in [activity_steps](../src/toybox/db/migrations/0001_initial.sql#L84-L92) has `seq`, `body`, `sfx`, `expected_action`, `current` — no per-step image field today.
- **Child kiosk** ([child/App.tsx](../frontend/src/child/App.tsx#L401-L411)) renders `PersonaAvatar` (240px) + `StepCard` body text + `NextStepButton`. SFX fires on transitions (`shouldFireTransitionSfx`). The new sprite sits inside `StepCard` next to the body text; persona stays untouched at the top.
- **Capability + breaker pattern** is established for every external/heavy resource: [ai/capability.py](../src/toybox/ai/capability.py) (Claude OAuth + token), [ai/breaker.py](../src/toybox/ai/breaker.py) (per-call breaker). Phase E plans `is_local_capable()` for the local LLM; this feature adds `is_image_gen_capable()` parallel to it (per-pipeline breaker so a hung diffusion call doesn't disable Claude calls and vice versa).
- **Static file serving** mounts `data/images/` as `/api/images/` (or similar) for parent + child to fetch toy/room/persona images without API auth (the URL is enough — image paths are server-generated UUIDs). Action sprites land under `data/images/toy_actions/<toy_id>/<slot>.png` and reuse the same mount.
- **Single-worker architecture** ([README.md:36](../README.md#L36)): one uvicorn worker hosts mic capture, STT, AI calls, REST, WS. Image-gen joins this single process, runs jobs on a dedicated `asyncio.Queue` with a single worker task so SDXL doesn't get pre-empted by the (Phase E) local LLM or (current) STT.
- **Operating mode for autonomous build:** per [`feedback_autonomous_build_bundled_ui.md`](../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md) the user prefers code-only/code-with-UI runs back-to-back with `--reviewers code` (drop `--ui` runtime reviewer); visual UI verification batches into one human-driven test pass at the end (Step F9 is that pass for this feature).
- **Plan format conventions** mature: Phase E + Phases C/D in [plan.md](plan.md) follow `### Step N:` with required bullets. This doc mirrors that.

## Scope

**In:**
- New `src/toybox/image_gen/` module: `pipeline.py` (subject-isolate → SDXL + IP-Adapter + pixel-art LoRA → quantize → background-remove → PNG), `worker.py` (single-worker asyncio queue), `capability.py` (`is_image_gen_capable()`), `models.py` (status enum + row dataclasses)
- New `src/toybox/storage/toy_actions.py` (CRUD helpers for the new table)
- New schema: `toy_actions(toy_id, slot, status, image_path, seed, error_msg, updated_at)` + new column on `activity_steps.action_slot`
- Fixed 10-slot action vocabulary: `idle`, `pointing`, `looking`, `jumping`, `cheering`, `thinking`, `waving`, `running`, `sleeping`, `confused` (full prompt template per slot in Appendix §"Action vocabulary")
- Hook into `POST /api/toys` so a successful toy commit enqueues all 10 generation jobs
- REST: `GET /api/toys/{id}/actions`, `POST /api/toys/{id}/actions/regenerate`, `POST /api/toys/{id}/actions/{slot}/regenerate`
- WS `Topic.toy_actions` emits `{toy_id, slot, status, error?}` on every status transition (parent-scope only; child-scope tokens never receive this topic)
- Generator (single-shot Claude path + offline-template path) emits `action_slot` per step from the fixed vocabulary
- Child kiosk renders ~96-128 px sprite next to `StepCard` body; persona avatar stays at top
- Parent toy detail UI: 2×5 sprite grid with per-slot status badges + "regenerate" buttons (per-slot + all)
- Operator install procedure documented in `documentation/operator/image-gen-runtime.md` (drivers, model checkpoints, smoke probe, GPU floor)
- Capability gate disables the feature gracefully on insufficient hardware (no GPU / not enough VRAM / model files missing) — kiosk falls back to current persona-only display

**Out:**
- Animated sprites (each is a static PNG with alpha)
- Per-child personalization (everyone sees the same sprite for the same toy + slot)
- Action sprites for rooms or personas (toys only in this phase)
- Online image-gen fallback when local GPU unavailable (households without a GPU silently degrade to persona-only)
- Parent-defined action slots beyond the fixed 10 (vocabulary is hard-coded; if 10 isn't enough we'd revisit in a follow-up phase)
- Backfilling sprites for toys ingested before this feature ships (operator can manually hit "regenerate all" per toy)
- Backfilling `action_slot` for existing `activity_steps` rows (old completed activities just render with no sprite — same as kiosk's current behavior)
- Persistent queue table (in-flight jobs are dropped on backend restart; parent's "regenerate" recovers — single-worker single-process project posture)
- Sprite editing or human-in-the-loop refinement (the only tools the parent has are "regenerate this slot" or "regenerate all 10")
- PNG file cleanup on toy archive (DB rows cascade-delete via FK; PNG files remain on disk indefinitely — acceptable filesystem growth for the household device, ~1-2 MB per archived toy at 128×128/32-color; follow-up phase adds a janitor if the directory bloats)

## Impact analysis

| File / module | Nature | Notes |
|---|---|---|
| `src/toybox/image_gen/__init__.py` | NEW | Module init; exports public surface |
| `src/toybox/image_gen/pipeline.py` | NEW | `generate_action(reference_bytes, slot, seed, ctx) -> bytes`; lazy-loads `diffusers` + `torch` + IP-Adapter + LoRA; runs in `asyncio.to_thread`; covered in detail in §"New components" |
| `src/toybox/image_gen/worker.py` | NEW | Single-worker `asyncio.Queue`; started at app boot; consumes one job at a time; emits WS status; writes PNG; updates DB row |
| `src/toybox/image_gen/capability.py` | NEW | `is_image_gen_capable()` — `torch.cuda.is_available()` + free-VRAM probe + model-files-on-disk check; per-pipeline breaker via `ai/breaker.py` |
| `src/toybox/image_gen/models.py` | NEW | `ToyActionStatus` StrEnum (`queued`, `running`, `done`, `failed`, `superseded`); `ToyActionRow` dataclass; `ACTION_SLOTS` tuple of 10 keys; `ACTION_PROMPTS` dict from slot → pose-detail string for the prompt template |
| `src/toybox/storage/toy_actions.py` | NEW | CRUD helpers: `upsert_status`, `list_for_toy`, `get_image_path`, `delete_for_toy_archived`; schema-bound table accessors mirroring `storage/images.py` style |
| `src/toybox/db/migrations/00<N>_toy_actions.sql` | NEW | `toy_actions(toy_id, slot, status, image_path, seed, error_msg, updated_at)` with `PRIMARY KEY (toy_id, slot)` and `FOREIGN KEY (toy_id) REFERENCES toys(id) ON DELETE CASCADE`. `<N>` resolved at build time — 0004 if Phase E hasn't shipped 0004 yet, otherwise next available |
| `src/toybox/db/migrations/00<N+1>_activity_step_action_slot.sql` | NEW | Adds `action_slot TEXT` (nullable) to `activity_steps`; old rows default to NULL → kiosk renders no sprite (current behavior) |
| `src/toybox/api/toys.py` | MODIFY | After successful `POST /api/toys` commit, enqueue 10 generation jobs into the worker queue; new endpoints `GET /api/toys/{id}/actions`, `POST /api/toys/{id}/actions/regenerate`, `POST /api/toys/{id}/actions/{slot}/regenerate` |
| `src/toybox/activities/generator.py` | MODIFY | Single-shot prompt schema includes `action_slot` per step (one of the 10 vocabulary keys); response parser validates against `ACTION_SLOTS`; rejected output retries once then falls back to NULL slot (kiosk renders no sprite, current behavior) |
| `src/toybox/activities/templates/*.json` | MODIFY | Each template step gets a static `action_slot` field; offline path emits the static value verbatim |
| `src/toybox/activities/models.py` | MODIFY | `ActivityStep.action_slot: str \| None`; constraint validates against `ACTION_SLOTS` |
| `src/toybox/ws/server.py` | MODIFY | Register `Topic.toy_actions`; parent-scope-only; emit on every status transition from the worker |
| `src/toybox/app.py` | MODIFY | App boot starts the image-gen worker task and registers shutdown to drain the queue cleanly |
| `frontend/src/parent/components/ToyIngest.tsx` | MODIFY | After commit response, render `ToyActionGrid` (new) — 2×5 sprite grid with per-slot status badges; subscribe to `Topic.toy_actions` for live updates; "regenerate" buttons per slot + "regenerate all" |
| `frontend/src/parent/components/ToyActionGrid.tsx` | NEW | Reusable grid; renders for both fresh ingest and existing-toy edit flows |
| `frontend/src/parent/api.ts` | MODIFY | New methods: `listToyActions(id)`, `regenerateAllActions(id)`, `regenerateActionSlot(id, slot)` |
| `frontend/src/parent/ws.ts` | MODIFY | Subscribe to new `toy_actions` topic on parent connect |
| `frontend/src/parent/store.ts` | MODIFY | Reducer for `toy_actions` envelopes; per-toy slot status map |
| `frontend/src/child/components/StepCard.tsx` | MODIFY | When `activity_step.action_slot` is set AND the activity has at least one toy with a sprite for that slot, render a small `ToyActionSprite` (~96-128 px) next to the body text; otherwise render the body text as today |
| `frontend/src/child/components/ToyActionSprite.tsx` | NEW | `<img src="/api/images/toy_actions/<toy_id>/<slot>.png" />` with fallback-to-nothing on 404 (graceful when generation hasn't finished or capability gate is disabled) |
| `frontend/src/shared/types.ts` | REGEN | `ActivityStep.action_slot`, `ToyActionRow`, `ToyActionStatus` codegen'd from Pydantic |
| `documentation/operator/image-gen-runtime.md` | NEW | F1 deliverable — driver install, model checkpoint download paths + SHA-256s, GPU floor, smoke probe, troubleshooting, env-var reference |
| `pyproject.toml` | MODIFY | Add `[project.optional-dependencies] image_gen = ["diffusers>=0.27", "transformers", "accelerate", "torch", "rembg", "Pillow"]`; gated install so households without a GPU don't pull torch unnecessarily |
| `data/models/image_gen/` | EXTEND | `sdxl/`, `ip_adapter/`, `pixel_art_lora/`, `bg_remove/` subdirs (gitignored — `.gitkeep` placeholders ship); checkpoints downloaded by F1 |
| `.env` example | MODIFY | Add `TOYBOX_IMAGE_GEN_ENABLED` (auto/true/false; default auto = capability-gated), `TOYBOX_IMAGE_GEN_DEVICE` (cuda/cpu; default cuda), `TOYBOX_IMAGE_GEN_MODEL_DIR` (default `data/models/image_gen`), `TOYBOX_IMAGE_GEN_OUTPUT_DIM` (default 128), `TOYBOX_IMAGE_GEN_PALETTE_COLORS` (default 32), `TOYBOX_IMAGE_GEN_TIMEOUT_SEC` (default 120 per slot), `TOYBOX_IMAGE_GEN_MIN_VRAM_GB` (default 12) |

## New components

### `src/toybox/image_gen/pipeline.py`
Public entry point: `generate_action(reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext) -> bytes`. The `GenerationContext` dataclass carries prompt-template inputs that vary per toy and is constructed by the worker from a `toys` row + the persona row (if any):

```python
@dataclass(frozen=True, slots=True)
class GenerationContext:
    toy_display_name: str             # always populated
    persona_display_name: str | None  # None when toys.persona_id IS NULL
    tags: tuple[str, ...]             # may be empty
```

Per call:

1. **Subject isolation** — `rembg` removes the background from the source toy photo; produces an RGBA cutout in-memory.
2. **Reference embedding** — IP-Adapter embeds the cutout for SDXL conditioning.
3. **Generation** — SDXL base + pixel-art LoRA + IP-Adapter; prompt templated as `"<intro>, pixel art, 16-bit, sprite, retro game style, transparent background, <ACTION_PROMPTS[slot]>"` where `<intro>` is `f"{ctx.persona_display_name} the {ctx.toy_display_name}"` when persona is set, else `f"a {ctx.toy_display_name}"`. Tags are NOT injected — they're noisy and IP-Adapter already conditions on the reference photo. Deterministic via supplied seed; 1024×1024 output.
4. **Quantize** — Pillow downsamples to `TOYBOX_IMAGE_GEN_OUTPUT_DIM` (default 128 px) using bilinear, then quantizes to `TOYBOX_IMAGE_GEN_PALETTE_COLORS` (default 32) via `Image.quantize(method=MEDIAN_CUT)` for crisp pixel-art aesthetic.
5. **Output background-remove** — `rembg` second pass on the quantized result to clean up any non-transparent residue from SDXL's "transparent background" prompt (which it never honors perfectly).
6. **Save PNG with alpha** to `data/images/toy_actions/<toy_id>/<slot>.png`.

The pipeline lazy-imports `torch`/`diffusers`/`rembg` so `import toybox.image_gen.pipeline` is cheap when the feature is disabled. Heavy ops run in `asyncio.to_thread` to avoid blocking the event loop. Per-call timeout enforced via `asyncio.wait_for` (default 120s — SDXL on consumer GPU is 20-60s; we cap at 120s and surface failure rather than hang). Unhandled CUDA OOM caught explicitly and re-raised as `ImageGenCapacityError` so the worker can mark the row failed with a clear error message.

CLI: `uv run python -m toybox.image_gen --probe <toy_id> --slot idle` generates one sprite end-to-end and writes a `data/models/image_gen/.probe-pass-<iso>.json` marker on success. Used by F1's smoke probe and as a regression-detection seam after driver bumps.

### `src/toybox/image_gen/worker.py`
Single-worker asyncio queue. Started at app boot via `app.py`'s lifespan handler. Consumes `(toy_id, slot, seed)` tuples; for each:

1. Read `toys.image_path` for the source photo bytes.
2. Mark `toy_actions` row `running`; emit WS.
3. Call `pipeline.generate_action(...)`.
4. On success: write PNG, update row to `done`, set `image_path`, emit WS.
5. On `ImageGenCapacityError`: trip the per-pipeline breaker, mark row `failed` with reason "out of memory", drain remaining queue at 1 retry per slot before fully tripping.
6. On `asyncio.TimeoutError`: mark row `failed` with reason "timeout".
7. On any other exception: log + mark row `failed` with reason "error" + 200-char error excerpt.

Single-worker (NOT a worker pool) so SDXL never contends with itself for VRAM. Phase E's local-LLM worker runs on the same GPU but in its own queue; coordination is "first-come-first-served at the GPU level" — neither worker holds VRAM idle. If Phase E ships first and we observe contention, follow-up work is a shared GPU-job mutex.

Job identity is `(toy_id, slot)`; enqueueing a regen for the same `(toy_id, slot)` while one is in flight marks the in-flight row `superseded` and queues the new one (so a parent hammering "regenerate" doesn't pile up duplicates).

### `src/toybox/image_gen/capability.py`
`is_image_gen_capable()` — runs at app boot AND on every regeneration request:

- Check `TOYBOX_IMAGE_GEN_ENABLED` env (`auto`/`true`/`false`); if `false`, return `False` early.
- Check `torch.cuda.is_available()` (lazy import — only loads torch if needed).
- Check free VRAM ≥ `TOYBOX_IMAGE_GEN_MIN_VRAM_GB` (default 12 GB).
- Check the four model-files-on-disk under `TOYBOX_IMAGE_GEN_MODEL_DIR`.
- Return `(capable: bool, reason: str)` — `reason` populated on negative result so the parent UI can render an actionable banner ("image generation disabled: GPU not detected" / "model checkpoints missing — see operator runbook").

Per-pipeline breaker via the existing `ai/breaker.py` so repeated failures (3 in 60s) trip the breaker for `TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC` (default 300s) before allowing retries. Independent of the Claude breaker and (Phase E) local-LLM breaker.

### `src/toybox/storage/toy_actions.py`
CRUD helpers mirroring `storage/images.py`'s shape:

- `upsert_status(conn, toy_id, slot, status, *, image_path=None, error_msg=None, seed=None) -> ToyActionRow`
- `list_for_toy(conn, toy_id) -> list[ToyActionRow]` — returns rows for all 10 slots; missing slots synthesized as `(toy_id, slot, status="not_started")` so the parent UI grid renders consistently before any jobs enqueue.
- `get_image_path(conn, toy_id, slot) -> str | None`
- `delete_for_toy_archived(conn, toy_id) -> int` — soft-delete on toy archive; image files left on disk but rows removed (mirrors how `toys.archived = 1` keeps the source photo).

### `src/toybox/image_gen/models.py`
- `ACTION_SLOTS: Final[tuple[str, ...]]` — the 10 keys (canonical order matches Appendix §"Action vocabulary")
- `ACTION_PROMPTS: Final[dict[str, str]]` — slot → pose-detail string injected into the SDXL prompt template; concrete strings per Appendix
- `ToyActionStatus(StrEnum)`: `queued`, `running`, `done`, `failed`, `superseded`
- `ToyActionRow` dataclass: `(toy_id, slot, status, image_path, seed, error_msg, updated_at)`
- `ImageGenCapacityError(Exception)` — raised on CUDA OOM so the worker breaker can trip
- `ImageGenTimeoutError(Exception)` — raised by `pipeline.generate_action` after `asyncio.wait_for` fires

### `frontend/src/parent/components/ToyActionGrid.tsx`
Reusable grid. Props: `toyId`, `actions: ToyActionRow[]`, `onRegenerateSlot`, `onRegenerateAll`, `disabledReason?`. Renders 2×5 grid; each cell shows the sprite (if `done`), a status badge (if `queued`/`running`/`failed`), and a small "regenerate" button. Top of grid renders "regenerate all" + a count summary ("3/10 done"). When `disabledReason` is set (capability gate off), grid renders empty cells + a banner with the reason and disables all buttons.

### `frontend/src/child/components/ToyActionSprite.tsx`
Tiny component: `<img src="/api/images/toy_actions/<toy_id>/<slot>.png">` sized ~96-128 px, positioned in `StepCard` to the left of the step body text. `onError` handler hides the element so a missing sprite (404) gracefully degrades to body-text-only — covers (a) capability gate disabled, (b) generation hasn't finished yet, (c) generation failed for that slot. No spinner; the kiosk should never look "loading" — the sprite just appears once it's ready.

### `documentation/operator/image-gen-runtime.md`
F1 deliverable. Sections:
- GPU floor (12 GB VRAM minimum, 16 GB recommended) + measured TPS guidance
- Driver install (CUDA toolkit 11.8 or 12.x; cuDNN 8.x — same as the existing whisper-GPU path per [README.md:46](../README.md#L46))
- Model checkpoint download — explicit URLs + SHA-256s for SDXL base, IP-Adapter, pixel-art LoRA, rembg `u2net`/`isnet-general-use`
- File layout under `data/models/image_gen/`
- Smoke probe — `uv run python -m toybox.image_gen --probe <existing_toy_id> --slot idle`; expected wall-clock <120s; expected output `data/images/toy_actions/<toy_id>/idle.png` non-empty + valid PNG
- Troubleshooting — CUDA OOM ("drop output dim, drop palette colors, or upgrade VRAM"), driver mismatch, missing checkpoints
- Env-var reference (full table from `.env` example)

## Design decisions

### In-process `diffusers` library, not a separate ComfyUI/A1111 process
Matches the [audio/stt.py](../src/toybox/audio/stt.py) "heavy compute in `asyncio.to_thread`" pattern that already works for whisper. One process, one venv, one set of failure modes. ComfyUI/A1111 would add a second HTTP server, a second port, a second startup procedure, a second log location — none of which fits the "single-uvicorn-worker, local-first, household device" project posture. The trade-off is that we can't swap pipelines visually (no ComfyUI graph editor); the trade-off is acceptable because the pipeline is fixed (subject-isolate → SDXL+IPA+LoRA → quantize → bg-remove) and operator tunability is via env vars (output dim, palette colors, timeout, VRAM floor), not by re-wiring the graph.

### Seed strategy — random 64-bit per generation, persisted with the row
Each generation gets a fresh seed via `secrets.randbits(64)`; the seed is stored on the `toy_actions` row alongside the output `image_path` so the operator can reproduce a specific sprite from `(toy_id, slot, seed)` later (e.g., to investigate a particularly good or bad output, or to verify a regression after a model update). On regenerate, the worker draws a NEW seed (so the parent gets a different output) and overwrites the previous row's seed on success. Initial-generation seed is NOT derived from `(toy_id, slot)` because we want regenerate-after-bad-output to actually produce something different — the seed must be the source of variation, not a deterministic function of the inputs. The pipeline itself is fully deterministic given `(reference_bytes, slot, seed, ctx)` — repeating the same call with the same seed yields the same pixels.

### Single-worker asyncio.Queue, not a persistent queue table
In-process `asyncio.Queue` with one worker matches the project's single-worker SQLite + WAL posture (multi-worker SQLite silently corrupts; same posture extends to "multiple GPU consumers contending for VRAM"). Backend restart drops in-flight jobs; rows in `running` state at restart are recovered by a startup sweep that marks them `failed` with reason "interrupted by restart" so the parent UI can hit "regenerate". This is acceptable because: (a) typical regen takes <30 minutes for 10 slots, (b) restarts during ingest are rare in a household device, (c) the fully recoverable workflow ("hit regenerate") is no worse than the persistent-queue equivalent ("worker picks up where it left off"), and (d) persistent queue would require a new schema + cleanup job + a worker-leadership protocol that all complicate the single-worker model.

### Fixed 10-slot vocabulary, not LLM-derived per toy
The slots are intentionally generic action verbs that map cleanly onto step text (`pointing` ↔ "point at the door"; `looking` ↔ "use your magnifying glass"; etc.). LLM-derived per-toy actions would be more varied but would force a per-step picker that maps free-text step bodies to per-toy slot lists — extra Claude call per step or a deterministic mapping that grows brittle. The fixed 10 + "generator emits action_slot per step" approach is deterministic, has zero per-step inference cost, and is trivially extensible (add a slot to `ACTION_SLOTS`, regenerate sprites, ship). If the 10 prove insufficient after real use, follow-up work expands the vocabulary; the schema change is one column, no migration.

### Generator emits `action_slot`, not a step-time picker
Three options were considered: (a) random pick per step, (b) Claude/local picker maps step body → slot per step, (c) generator emits the slot as part of the step output. Option (c) wins because: it folds cleanly into the existing single-shot Claude prompt (one extra field per step), requires no extra inference per step, gives the generator the best information for picking (it wrote the step text and knows the verb intent), and works identically for the offline-template path (templates statically declare a slot per step). Risk: model emits an out-of-vocab slot — mitigated by validating against `ACTION_SLOTS` and falling back to `None` (kiosk renders no sprite, body text only) on validation failure.

### Pixel-art aesthetic via SDXL + LoRA + post-process palette quantize
The "16-bit pixel art" look comes from three sources stacked: (1) a pixel-art SDXL LoRA biases the latent toward sprite-style outputs, (2) the prompt explicitly invokes "pixel art, 16-bit, sprite, retro game style, transparent background", (3) post-process palette quantize to 32 colors at 128×128 enforces the aesthetic even when the LoRA + prompt don't fully commit. Using SDXL base (1024×1024 generation) + post-process quantize, rather than a pixel-native model like `pixel-art-xl` directly at 128×128, gives much better subject identity preservation — IP-Adapter works best at SDXL's native resolution.

### IP-Adapter for subject identity, not LoRA-per-toy or img2img
IP-Adapter takes the (background-removed) reference image and conditions the generation on its visual embeddings. Alternatives: (a) train a per-toy LoRA — too slow (15+ min per toy), too much disk (~50 MB per toy × N toys), too much engineering for a household device; (b) img2img on the source photo — can't change pose without losing identity, and changing pose IS the whole point. IP-Adapter is the standard "subject + new prompt" tool and runs at inference-time only, no training step.

### `is_image_gen_capable()` as a graceful-degradation gate
Households without a GPU should NOT be blocked from using toybox; they just don't get sprites. The capability gate runs at boot AND per-request so a runtime VRAM exhaustion (e.g. operator started a Phase E local-LLM run that's holding 10 GB) flips to `False` mid-session and new ingests skip generation cleanly rather than queueing jobs that would fail. **Existing rows in `done` state remain visible + functional regardless of capability gate state** — the capability check only gates *new generation* (ingest-time enqueue + regenerate endpoints + worker pickup); the kiosk continues to render existing sprites and the parent UI still renders the existing grid populated from DB rows. Only the regenerate buttons + ingest-time enqueue are gated; the parent UI surfaces the gate's reason in a banner so the operator understands why new generation is paused. The kiosk's `ToyActionSprite` uses `onError`-hides-element so a 404 (capability off mid-session, generation hasn't finished, generation failed) renders identically to "no sprite for this slot" — child UX is unchanged, body text reads as today. Per-pipeline breaker is independent of Claude/local-LLM breakers so a single bad image-gen install doesn't cascade.

### Migration numbering coordination with Phase E
Both phase-e-plan.md and this doc reserve migrations 0004 and 0005. They serve different schemas (E adds `activity_steps.is_complete` + `labeled_events.redact_for_sft`; F adds `toy_actions` + `activity_steps.action_slot`). Whichever ships first claims those numbers; the second renumbers at build time. The migration filenames in §"Impact analysis" use `<N>` as a placeholder for this reason. Build-step prompts must include a "check current max migration number first" instruction so the dev agent doesn't blindly hardcode 0004.

### Static file serving for sprites, not authenticated API endpoint
Action sprites live under `data/images/toy_actions/<toy_id>/<slot>.png` and are served via the existing `data/images/` StaticFiles mount. Same posture as toy/room/persona images: the URL is enough — image paths are server-generated and not enumerable from public surfaces. The child kiosk fetches sprites with `<img src="...">` directly without API auth; the parent UI does the same. If we ever need per-image authorization (e.g. multi-tenant deployment), a follow-up phase wraps the mount in a token check; v1 does not.

### Per-pipeline breaker, not a global "all AI" breaker
Existing pattern is one breaker per call-site: Claude vision has its own breaker, Claude judge has its own, Phase E local-LLM has its own. Image-gen joins this pattern with its own breaker. The benefit is independence — a flaky GPU doesn't disable Claude calls and vice versa. The cost is more breaker state; acceptable because the breakers are cheap (in-memory state, tiny logic).

## Build steps

| # | Step | Type | Reviewers (canonical) | Done-when summary |
|---|------|------|----------------------|-------------------|
| F1 | Image-gen runtime install + smoke probe | operator | n/a | Drivers + four checkpoints on disk + sha256-verified; `uv run python -m toybox.image_gen --probe ...` produces one valid PNG end-to-end <120s; operator runbook landed |
| F2 | Pipeline module: bg-remove → SDXL+IPA+LoRA → quantize → PNG | code | `--reviewers code` | `pipeline.generate_action(...)` ships; capability-gate stub + lazy-import working; deterministic seed yields deterministic pixels; CPU stub-runtime fixture for CI; CLI `--probe` shipped + tested |
| F3 | `toy_actions` table + storage layer + capability gate | code | `--reviewers code` | Migration lands forward + idempotent; `storage/toy_actions.py` CRUD covered by tests; `is_image_gen_capable()` covers all four return branches (env-disabled, no-CUDA, low-VRAM, missing-checkpoints) |
| F4 | Single-worker asyncio queue + WS `Topic.toy_actions` | code | `--reviewers code` | Worker started at app boot via lifespan; jobs flow queue → pipeline → DB → WS; supersede semantics tested; restart-recovery sweep marks orphaned `running` rows `failed`; per-pipeline breaker tested |
| F5 | REST endpoints + hook into `POST /api/toys` commit | code | `--reviewers code` | Three endpoints shipped + tested; toy commit enqueues 10 jobs (covered by integration test through the production caller); regenerate-one + regenerate-all wired; capability-disabled response surfaces actionable banner |
| F6 | `activity_steps.action_slot` migration + Pydantic + generator + offline templates | code | `--reviewers code` | Migration + Pydantic field + generator prompt update; vocab validation rejects out-of-vocab gracefully; offline-template path emits static slot per step; integration test through `_do_propose` proves the field reaches the DB |
| F7 | Child kiosk renders sprite next to StepCard | code | `--reviewers code` | `ToyActionSprite` renders next to body text when `action_slot` set + sprite exists; 404 hides element gracefully; persona avatar at top unchanged; vitest covers all branches; SFX timing unaffected |
| F8 | Parent toy detail: 2×5 sprite grid + per-slot regenerate + ws progress | code | `--reviewers code` | `ToyActionGrid` renders for fresh ingest + existing-toy-edit; per-slot regenerate fires REST + WS round-trip; status badges live-update; capability-disabled banner renders; vitest + parent-Playwright covers golden + degraded paths |
| F9 | **Smoke gate** — 1 toy → 10 actions → 1 activity → sprite verified | operator | n/a | Operator uploads one toy, all 10 sprites generate within wall-clock budget, one activity runs end-to-end with the sprite rendering on each step; pass/fail recorded in `documentation/runs/<date>-toy-action-sprites-smoke.md` |
| F10 | **Observation soak** — 30 toys back-to-back, VRAM/queue/throughput | wait | n/a | Operator ingests 30 toys in a single session; queue processes all 300 jobs without hanging; peak VRAM stays under floor; no failed-without-recovery rows; report at `documentation/runs/<date>-toy-action-sprites-soak.md` |

**Issues:** umbrella → #44 · F1 → #45 · F2 → #46 · F3 → #47 · F4 → #48 · F5 → #49 · F6 → #50 · F7 → #51 · F8 → #52 · F9 → #53 · F10 → #54 (created by `/repo-sync` 2026-05-06).

#### Step F1: Image-gen runtime install + smoke probe

- **Problem:** Operator-driven install of CUDA toolkit (11.8 or 12.x) + cuDNN 8.x (no-op if the existing whisper-GPU path is already installed). Download four checkpoints to `data/models/image_gen/` (gitignored): SDXL base (`stabilityai/stable-diffusion-xl-base-1.0`), IP-Adapter SDXL (`h94/IP-Adapter`'s `ip-adapter_sdxl_vit-h.safetensors`), pixel-art LoRA (chosen at this step from `nerijs/pixel-art-xl` or equivalent — operator records final choice + rationale in the runbook), and `rembg` model (`u2net.onnx` or `isnet-general-use.onnx`). All four sha256-verified against upstream-published checksums; checksums recorded in the operator doc. New `documentation/operator/image-gen-runtime.md` covers install procedure, GPU floor (12 GB minimum, 16 GB recommended), env-var reference, smoke probe, and troubleshooting. The smoke probe itself ships in F2 — F1's "smoke" is just confirming the four checkpoints load without OOM via a one-shot `python -c "from diffusers import StableDiffusionXLPipeline; ..."` snippet pasted into the runbook.
- **Type:** operator
- **Issue:** #45
- **Flags:** n/a (operator step; not invoked through `/build-step`)
- **Status:** DONE (2026-05-06) — ran `scripts/image_gen_setup.py` to populate the full `data/models/image_gen/{sdxl,ip_adapter,pixel_art_lora,bg_remove}/` layout (~10.5 GB total). All four checkpoints sha256-verified via `data/models/image_gen/manifest.json` (8 hashes covering all critical weight files). Local-path load smoke completed in 3.6 s on host GPU with the canonical config (`enable_model_cpu_offload()` + `pipe.vae.enable_slicing()`, no `enable_attention_slicing`). Operator runbook landed at [`documentation/operator/image-gen-runtime.md`](operator/image-gen-runtime.md) covering install + canonical config + smoke + env vars + the three diffusers gotchas surfaced by the 8 GB feasibility probe + troubleshooting.
- **Depends on:** none (kicks off Phase F)
- **Parallel-safe with:** none — strictly first step; F2 cannot start until checkpoints land
- **Done when:** All four checkpoints sha256-verified on disk under `data/models/image_gen/` (or `HF_HOME` cache); the load snippet completes without OOM on the home GPU; `documentation/operator/image-gen-runtime.md` covers install + sha256s + env-var reference + GPU floor + troubleshooting + the three diffusers gotchas surfaced by the probe (image_encoder explicit load; do-not-call attention_slicing; vae.enable_slicing canonical).

#### Step F2: Pipeline module — bg-remove → SDXL+IPA+LoRA → quantize → PNG

- **Problem:** Ship `src/toybox/image_gen/{pipeline.py, models.py, capability.py}`. `pipeline.generate_action(reference_bytes, slot, seed, ctx) -> bytes` runs the six-stage pipeline (subject-isolate → IP-Adapter embed → SDXL+LoRA generate → palette quantize → output bg-remove → save PNG with alpha). Lazy-imports `torch`/`diffusers`/`rembg` so module import is cheap when the feature is off. Heavy ops in `asyncio.to_thread`. Per-call timeout (`asyncio.wait_for`, default 120s) raises `ImageGenTimeoutError`; CUDA OOM caught + re-raised as `ImageGenCapacityError`. CLI `uv run python -m toybox.image_gen --probe <toy_id> --slot <slot>` wires the same code path against on-disk inputs and writes a `data/models/image_gen/.probe-pass-<iso>.json` marker on success — used by F1's runbook smoke + as a regression-detection seam after driver bumps. **Models module** ships `ACTION_SLOTS` + `ACTION_PROMPTS` + `ToyActionStatus` + dataclasses + the two custom exceptions. **Capability module** ships `is_image_gen_capable() -> tuple[bool, str]` covering env-disabled / no-CUDA / low-VRAM / missing-checkpoints branches. **Determinism + CI:** the real pipeline is `@pytest.mark.requires_gpu`-gated; for CI a stub-runtime fixture (`tests/fixtures/image_gen/stub_pipeline.py`) returns deterministic 16×16 single-color PNGs keyed off `(slot, seed)` so the worker + REST + UI tests downstream get reproducible bytes. The CLI `--probe` is tested against the stub fixture in CI; an opt-in `@pytest.mark.requires_gpu` integration test exercises the real pipeline once.
- **Type:** code
- **Issue:** #46
- **Flags:** --reviewers code
- **Status:** DONE (2026-05-06) — merged in commit 846b082. 17 files / 2785 insertions. Quality gates green on master: ruff clean, mypy strict 82 source files, pytest 1038 passed + 2 skipped (1 pre-existing + the new `@pytest.mark.requires_gpu` test which auto-skips without checkpoints + CUDA on the test runner). 2 dev iterations; iter-2 addressed 4 medium-severity bugs (path-traversal validator on `toy_id`, VRAM cleanup on partial pipeline construction failure, stub-mode WARNING log, NULL `image_path` LookupError handling) + 3 test deletions for duplicates/tautologies + AST-based regression test pinning Gotcha 2 (no `enable_attention_slicing` call sites).
- **Depends on:** Step F1 (#45 — checkpoints on disk)
- **Parallel-safe with:** none — strictly sequential; F3 + downstream consume `ACTION_SLOTS` + `ToyActionStatus` defined here
- **Done when:** `pipeline.py` + `models.py` + `capability.py` shipped; `pipeline.generate_action(...)` produces a valid PNG end-to-end against the stub fixture in CI; `is_image_gen_capable()` covers four return branches with unit tests; CLI `--probe` writes the marker file on success; lazy-import test (sys.modules snapshot — see §"Testing strategy") asserts `torch` and `diffusers` are NOT in `sys.modules` immediately after `import toybox.image_gen.pipeline`; `@pytest.mark.requires_gpu`-gated integration test exercises the real pipeline on the operator machine and asserts the output PNG is non-empty + valid + ≤`TOYBOX_IMAGE_GEN_PALETTE_COLORS` (default 32) colors + has alpha channel.

#### Step F3: `toy_actions` table + storage layer + capability gate

- **Problem:** New migration creates `toy_actions(toy_id TEXT NOT NULL, slot TEXT NOT NULL, status TEXT NOT NULL, image_path TEXT, seed INTEGER, error_msg TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (toy_id, slot), FOREIGN KEY (toy_id) REFERENCES toys(id) ON DELETE CASCADE)`. Migration number resolved at build time — read the existing max migration number under `src/toybox/db/migrations/` and use the next available; do NOT hardcode 0004 (Phase E plan also targets 0004; whichever ships first wins). New `src/toybox/storage/toy_actions.py` ships CRUD helpers: `upsert_status` (idempotent), `list_for_toy` (synthesizes `not_started` rows for missing slots so the UI grid renders consistently before any jobs enqueue), `get_image_path`, `delete_for_toy_archived`. Mirrors `storage/images.py`'s style (typed dataclass return values, sqlite3 connection injected, no FastAPI imports). **Path-traversal hardening:** every helper that accepts `toy_id` validates against the UUIDv4 regex (`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`) before constructing on-disk paths; `ValueError` raised on mismatch. Same defensive posture as `storage/images.py:committed_dir`'s subdir whitelist — `toys.id` is UUIDv4 by construction today, but the validator means a future schema change can't accidentally turn this into a path-traversal vector. The capability gate from F2 wires into a startup probe that logs the result + reason at INFO so ops can spot a degraded boot.
- **Type:** code
- **Issue:** #47
- **Flags:** --reviewers code
- **Status:** PENDING
- **Depends on:** Step F2 (#46 — `ACTION_SLOTS` + `ToyActionStatus` defined)
- **Parallel-safe with:** none — strictly sequential; F4 + F6 both wait on F3's migration number resolution before they can pick the next available number for their own migrations (F4 doesn't actually migrate but F6 does; running both in parallel risks number collision)
- **Done when:** Migration lands forward + idempotent (verified via the existing `tests/integration/migrations/` pattern that re-applies + asserts no error); `storage/toy_actions.py` CRUD round-trip covered by pytest with tmp DB; foreign-key cascade on `toys` archive verified; capability-gate startup probe logs the resolved branch with reason; missing-checkpoints branch tested by `monkeypatch`-ing the model dir.

#### Step F4: Single-worker asyncio queue + WS `Topic.toy_actions`

- **Problem:** New `src/toybox/image_gen/worker.py` runs a single `asyncio.Queue` consumer started at app boot via `app.py`'s lifespan handler and stopped (with queue drain) at shutdown. The worker accepts `(toy_id, slot, seed)` jobs, calls `pipeline.generate_action`, persists the PNG + DB row, and emits `Topic.toy_actions` envelopes on every status transition. **Supersede semantics:** dedup at enqueue time AND at run time. On enqueue, if any existing `(toy_id, slot)` row is in `queued` or `running` state, mark it `superseded` and queue the new job normally. The worker also re-checks the row state immediately before persisting — if its in-flight row is `superseded` (because a newer job enqueued mid-generation), the worker discards its output and skips the DB write. Together these two checks prevent pile-up from rapid parent clicks regardless of timing (queue empty + worker idle, queue empty + worker running, queue has items + worker running). **Restart recovery sweep:** at app boot, scan `toy_actions` for rows in `running` state and mark them `failed` with reason "interrupted by restart" so the parent UI shows the dropped jobs as recoverable (one-click regenerate). **Per-pipeline breaker:** 3 failures in 60s trips the breaker for `TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC` (default 300s); enqueues during open state are accepted but immediately marked `failed` with reason "image-gen breaker open" — keeps the UI honest about why jobs aren't progressing. **WS topic:** `Topic.toy_actions` registered in `src/toybox/ws/server.py`; parent-scope-only (child tokens never receive it); envelope shape `{toy_id, slot, status, error?, image_path?}` (image_path included on `done` so the UI can refresh the grid without a separate REST round-trip).
- **Type:** code
- **Issue:** #48
- **Flags:** --reviewers code
- **Status:** PENDING
- **Depends on:** Step F3 (#47 — table + storage layer)
- **Parallel-safe with:** Step F6 (#50) once F3 lands — F4 modifies `image_gen/worker.py` + `ws/server.py` + `app.py`; F6 modifies `activities/{generator,models,templates}` + adds a migration; disjoint file sets, no shared imports beyond `ACTION_SLOTS` (read-only constant from F2). Operator can dispatch F4 + F6 in parallel worktrees once F3's migration number is known
- **Done when:** `worker.py` shipped + started at app boot; integration test wires queue → stub pipeline → DB → captured WS envelopes and asserts the full lifecycle (`queued` → `running` → `done`); supersede semantics tested with rapid double-enqueue; restart recovery sweep tested by inserting an artificial `running` row pre-boot and asserting it's marked `failed` post-boot; breaker open/half-open/closed tested with deliberate stub failures; WS topic visible to parent token + invisible to child token.

#### Step F5: REST endpoints + hook into `POST /api/toys` commit

- **Problem:** Three new endpoints on `src/toybox/api/toys.py`:
  - `GET /api/toys/{id}/actions` — returns the synthesized 10-row list from `list_for_toy` (existing rows + `not_started` placeholders for missing slots).
  - `POST /api/toys/{id}/actions/regenerate` — enqueues 10 jobs (one per slot), returns `{queued: ["idle", "pointing", ...]}`.
  - `POST /api/toys/{id}/actions/{slot}/regenerate` — enqueues one job, returns `{queued: ["<slot>"]}`.
  All three respond `409` with `{code: "image_gen_disabled", reason: "..."}` when `is_image_gen_capable()` returns False; the parent UI surfaces the reason in the disabled-banner. **Hook into `POST /api/toys` commit:** after the row insert + `commit_staging` succeed, enqueue all 10 generation jobs in a single best-effort call (logged but doesn't fail the toy create on enqueue error — toy is still usable, parent can hit "regenerate all" to retry). This is the integration test seam per [`feedback_buildstep_require_integration_test.md`](../../../.claude/projects/c--Users-abero-dev/memory/feedback_buildstep_require_integration_test.md): the dev agent must add an integration test that POSTs through the production toy-create handler and asserts 10 jobs landed in the queue, NOT just a unit test of the enqueue helper.
- **Type:** code
- **Issue:** #49
- **Flags:** --reviewers code
- **Status:** PENDING
- **Depends on:** Step F4 (#48 — worker queue + WS topic)
- **Parallel-safe with:** Step F6 (#50) once F4 lands — F5 modifies `api/toys.py` only; F6 modifies `activities/{generator,models,templates}` + a migration; disjoint file sets. F5 + F6 can dispatch concurrently after F4 ships
- **Done when:** Three endpoints shipped + tested with httpx (golden + capability-disabled + non-existent-toy + non-existent-slot); toy-commit hook covered by an integration test that drives `POST /api/toys` end-to-end with a real DB + a stubbed worker queue and asserts 10 jobs were enqueued; per-slot regenerate supersedes correctly; OpenAPI schema reflects the new shapes; pydantic2ts regenerates `frontend/src/shared/types.ts`.

#### Step F6: `activity_steps.action_slot` migration + Pydantic + generator + offline templates

- **Problem:** New migration adds `action_slot TEXT` (nullable) to `activity_steps`; old rows default NULL → kiosk renders no sprite (current behavior). Migration number resolved at build time same as F3. Pydantic shape: `ActivityStep.action_slot: Annotated[str | None, AfterValidator(_validate_in_action_slots)] = None` — validator accepts None or a vocab member; rejects out-of-vocab with a clear error so the generator's malformed-output path catches it. **Generator prompt update:** the single-shot Claude prompt schema changes the per-step JSON shape from `{seq, body, sfx, expected_action}` to `{seq, body, sfx, expected_action, action_slot}` with an inlined enumeration of the 10 vocab members and a brief instruction ("pick the slot that best matches the verb in the step body; if no clear match, pick `idle`"). Existing parser already handles malformed-output → offline-fallback; we extend the validator to also reject out-of-vocab `action_slot` and rely on the same fallback. **Offline templates:** each template's step gets a static `action_slot` field hand-authored to match the template's verb. Template loader validates against `ACTION_SLOTS` at boot so a bad template fails loudly. **Integration test through `_do_propose`** (kiosk REST path) covers the production caller end-to-end and asserts the field reaches the DB row + the streaming WS envelope.
- **Type:** code
- **Issue:** #50
- **Flags:** --reviewers code
- **Status:** PENDING
- **Depends on:** Step F2 (#46 — `ACTION_SLOTS` defined); Step F3 (#47 — migration numbering coordination)
- **Parallel-safe with:** Step F4 (#48) once F3 lands; Step F5 (#49) once F4 lands — F6's file set (`activities/{generator,models,templates}/*` + new migration) is disjoint from F4's (`image_gen/worker.py` + `ws/server.py` + `app.py`) and F5's (`api/toys.py`); the only coupling is the migration number, which F3 settles. Operator can run F4 ⇆ F6 in parallel worktrees, then F5 ⇆ F6 in parallel after F4 merges
- **Done when:** Migration lands forward + idempotent; `ActivityStep.action_slot` field validates against vocab + accepts None; single-shot generator prompt updated + tested with stubbed Claude responses (good slot, bad slot, missing field) — bad slot + missing field both fall back to None; offline-template loader rejects bad slots at boot; offline-path step emission carries the static slot through to the DB; integration test through `_do_propose` asserts the slot survives REST + DB + WS; existing test fixtures that snapshot ActivityStep shapes updated.

#### Step F7: Child kiosk renders sprite next to StepCard

- **Problem:** New `frontend/src/child/components/ToyActionSprite.tsx` — small (~96-128 px) `<img src="/api/images/toy_actions/<toy_id>/<slot>.png" alt="<toy_display_name> <slot>" />` with `onError`-hides-element so a 404 (capability disabled, generation not yet finished, generation failed) renders gracefully as "no sprite for this step" — body text reads as today. The `alt` attribute (e.g. `alt="Mr. Unicorn looking"`) is mandatory for screen-reader accessibility — toybox is family-facing and the parent UI's grid view (F8) reuses this component, so a11y is not optional. The toy `display_name` is read from the activity's hydrated toy summary (already on the wire in the activity envelope); if missing, fall back to `alt="<slot>"`. **Activity → toy resolution:** the activity has a `toy_ids` array; the kiosk renders the sprite for the FIRST `toy_id` in the array (deterministic; if multiple toys are involved, follow-up phase could rotate or compose, out of scope here). `StepCard` modified to position the sprite to the LEFT of the body text, with the body text resizing to fill remaining width — both elements vertically centered. Persona avatar at the top stays untouched. SFX timing on `transition`/`success` unaffected (sprite is a passive element, no event handlers). **Vitest covers:** sprite renders when `action_slot` set + `toy_ids[0]` set; sprite hides on 404; sprite hides when `action_slot` is null; sprite hides when `toy_ids` is empty; layout regression (sprite + body text both visible at typical viewport sizes) snapshotted; alt attribute present + correct.
- **Type:** code
- **Issue:** #51
- **Flags:** --reviewers code --ui (canonical) — autonomous-build operating mode runs as `--reviewers code`; visual UI verification batches at F9 per [`feedback_autonomous_build_bundled_ui.md`](../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md)
- **Status:** PENDING
- **Depends on:** Step F5 (#49 — sprites are reachable via the static mount); Step F6 (#50 — `action_slot` flows through the WS envelope)
- **Parallel-safe with:** none — F8 depends on F7; F7 must land first
- **Done when:** `ToyActionSprite` component shipped + 4 vitest cases green; `StepCard` layout updated; persona avatar position unchanged (snapshot test); SFX-timing tests unchanged; Playwright child smoke updated to assert the sprite element appears on a step with a known slot + toy.

#### Step F8: Parent toy detail — 2×5 sprite grid + per-slot regenerate + ws progress

- **Problem:** New `frontend/src/parent/components/ToyActionGrid.tsx` — reusable 2×5 grid; props `(toyId, actions, onRegenerateSlot, onRegenerateAll, disabledReason?)`. Per-cell rendering: sprite (if `done`, with `alt` text per F7's component), status badge (if `queued`/`running`/`failed` with the failure reason on hover), "regenerate" button (small icon, fires `POST /api/toys/{id}/actions/{slot}/regenerate`). Top of grid: "regenerate all" button + `N/10 done` count. Capability-disabled banner + button-disable when `disabledReason` set. **Wire into `ToyIngest.tsx`** so a freshly-committed toy renders the grid below the toy form (replacing the current "image saved" success state); also wire into the existing toy-edit flow so the grid is reachable from the toy library. **Archived toys:** the grid is hidden on archived toys (the existing toy library hides archived toys behind a toggle; archived rows are read-only and the grid's regenerate buttons would be confusing). **WS subscribe:** parent connect subscribes to `Topic.toy_actions`; reducer in `parent/store.ts` keeps a per-toy slot status map; envelope updates flow to the grid in real time. **REST methods on `parent/api.ts`:** `listToyActions(id)`, `regenerateAllActions(id)`, `regenerateActionSlot(id, slot)`. Vitest + parent-Playwright cover the golden path (toy commit → grid renders → WS emits queued → running → done → sprite appears) and the degraded path (capability disabled → banner renders → buttons disabled).
- **Type:** code
- **Issue:** #52
- **Flags:** --reviewers code --ui (canonical) — autonomous-build operating mode runs as `--reviewers code`; visual UI verification batches at F9 per [`feedback_autonomous_build_bundled_ui.md`](../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md)
- **Status:** PENDING
- **Depends on:** Step F5 (#49 — REST + WS topic shipped); Step F7 (#51 — sprite component reusable for the grid cells)
- **Parallel-safe with:** none — strictly sequential; F8 reuses `ToyActionSprite` from F7 and consumes the REST + WS surface from F5
- **Done when:** `ToyActionGrid` shipped + reused from both ingest + edit flows; 9 vitest cases (4 cells × `done`/`running`/`queued`/`failed` + capability-disabled banner) green; parent-Playwright smoke covers the full ingest → WS → grid → done flow; reducer tested for envelope merge semantics (latest envelope wins per slot); regenerate-all button fires 10 enqueues in one click, regenerate-slot fires 1.

#### Step F9: Smoke gate — 1 toy → 10 actions → 1 activity → sprite verified

- **Problem:** Operator smoke test against a real GPU end-to-end. Steps:
  1. Verify capability gate is `True` (`is_image_gen_capable()` startup log shows green).
  2. Upload one fresh toy via the parent UI.
  3. Watch the WS-driven grid update; record per-slot wall-clock times.
  4. Confirm all 10 sprites land within the wall-clock budget (~20-30 minutes for 10 slots on a 16 GB GPU at ~2-3 minutes per generation, consistent with F10's 300-job/10-15h soak rate; cap: <60 minutes total; if any single slot exceeds 60 min, mark it failed and continue).
  5. Trigger one activity (manual trigger button) and watch the kiosk render the sprite next to the step body for each step transition.
  6. Visually confirm: sprite resembles the toy + matches the step's action verb; persona avatar at top is unchanged; layout is sane.
  7. Hit the "regenerate" button on one slot; confirm the slot transitions queued → running → done with a different image (seed advanced).
  8. Stop the backend mid-generation; restart; confirm the in-flight `running` row was marked `failed` with reason "interrupted by restart" and is recoverable via the regenerate button.
  Operator records observations in `documentation/runs/<date>-toy-action-sprites-smoke.md`. Pass criteria: all 10 sprites generate without errors; activity renders sprite per step; regenerate works; restart recovery works.
- **Type:** operator
- **Issue:** #53
- **Flags:** n/a (operator step; manual verification of the full pipeline)
- **Status:** PENDING (2026-05-06) — unblocked after 8 GB probe; budget per slot validated at ~30 s wall-clock (was projected at 2-3 min).
- **Depends on:** Steps F1-F8 all DONE
- **Parallel-safe with:** none — verification gate after the full implementation chain
- **Done when:** Smoke run report at `documentation/runs/<date>-toy-action-sprites-smoke.md` with all 8 criteria passing; if any fails, file as a follow-up issue and fix before F10.

#### Step F10: Observation soak — 30 toys back-to-back

- **Problem:** Operator stress test for the autonomous worker behavior. Required by the plan-feature skill quality bar: this feature has a background async worker (autonomous behavior) AND a producer-consumer chain (generator emits action_slot → DB → kiosk renders sprite), so component unit tests aren't sufficient — the system must run end-to-end with realistic inputs and be watched long enough to expose time-dependent failures (cumulative VRAM creep, queue starvation, breaker false-trips, race conditions between toy commit + worker pickup + WS emit). Steps:
  1. Reset queue + DB to a known state.
  2. Ingest 30 toys via the parent UI in one sitting (target: ~3 minutes of upload work; the worker runs ~30 × 10 = 300 generation jobs, expected wall-clock ~10-15 hours on a 16 GB GPU).
  3. Leave running unattended overnight.
  4. Next morning, audit:
     - All 300 jobs reached `done` OR `failed` with a recorded reason (none stuck `queued` or `running`).
     - Peak VRAM stayed under the floor (`nvidia-smi --query-gpu=memory.used --format=csv` logged every 60s).
     - WS envelopes match DB state (no envelopes dropped).
     - Breaker did not false-trip (recorded breaker state transitions in logs).
     - No backend restarts during the soak (logs show clean uptime).
  5. Hit "regenerate all" on the 5 toys with the most failures (if any) and confirm recovery.
  Report at `documentation/runs/<date>-toy-action-sprites-soak.md` with: total wall-clock, success rate, peak VRAM, breaker transitions, any anomalies + their resolution. **Soft-pass criterion** (per `feedback_buildstep_require_integration_test.md` and the autonomous-system observation pattern from Phase E §"Smoke gate carved out"): if 280+ of 300 jobs succeed AND no autonomous-system invariant violated (no leaked VRAM, no stuck rows, no false breaker trips), the soak passes; isolated failures get triaged but don't block the soak.
- **Type:** wait
- **Issue:** #54
- **Flags:** n/a (operator step; long-wall-clock observation)
- **Status:** PENDING (2026-05-06) — unblocked after 8 GB probe; expected wall-clock revised from overnight (10-15h) to ~2.5h given measured 30 s/sprite × 300 jobs.
- **Depends on:** Step F9 (#53 — smoke gate passed)
- **Parallel-safe with:** none — final observation gate; runs after smoke gate passes
- **Done when:** Soak report landed at `documentation/runs/<date>-toy-action-sprites-soak.md`; soft-pass criterion met (280+ of 300 jobs successful + no invariant violation); if not met, file follow-up issues for each invariant violation and re-run F10 after fixes.

## Risks and open questions

| Item | Risk | Mitigation | Source |
|---|---|---|---|
| VRAM contention with Phase E local LLM | If both Phase E (7B Q4 ~5-8 GB) and image-gen (SDXL+IPA+LoRA ~10 GB) load on the same GPU, total exceeds 16 GB and one OOMs | Workers run independent queues; per-pipeline breaker isolates failures; if measured contention is real, follow-up phase adds a shared GPU-job mutex | this doc §"Design decisions" |
| Generation latency per image | SDXL on consumer GPU is 20-60s/image × 10 = 3-10 minutes per toy ingest; parent may think the UI is stuck | WS-driven progress updates per slot; "N/10 done" count visible at all times; per-call timeout caps at 120s with clear failure message | this doc §"New components" §pipeline.py |
| Pixel-art LoRA + IP-Adapter quality | The combination may produce sprites that don't resemble the toy or don't look pixel-art; first-pass may need prompt + LoRA-strength tuning | F1 operator records LoRA choice + rationale; F9 smoke gate is the first quality check; "regenerate" button is the ongoing recovery path; if quality is consistently poor, follow-up phase swaps the LoRA or adds a custom DreamBooth step | this doc §"Design decisions" §"Pixel-art aesthetic" |
| Subject identity preservation | IP-Adapter is good but not perfect — unique features of "Mr. Unicorn" may not survive 10 different poses; the child may not recognize their toy | F9 smoke gate is the first identity check; per-slot regenerate gives the parent recourse; if identity drifts consistently, follow-up phase adds a per-toy LoRA-fine-tune step (training cost ~15min/toy) | this doc §"Design decisions" §"IP-Adapter for subject identity" |
| Migration 0004/0005 collision with Phase E | Both phases reserve those numbers; whichever ships first wins | Build-step prompts for F3 + F6 must include "read max migration number first; do NOT hardcode 0004"; the second phase to ship renumbers at build time | this doc §"Design decisions" §"Migration numbering coordination" |
| Backend restart drops in-flight jobs | A restart mid-generation orphans `running` rows, requiring parent-driven recovery | F4's restart-recovery sweep marks orphaned rows `failed` with clear reason; one-click regenerate recovers; alternative (persistent queue) deemed not worth the complexity for the household-device use case | this doc §"Design decisions" §"Single-worker asyncio.Queue" |
| Out-of-vocab `action_slot` from the model | Fine-tuned model emits a slot not in `ACTION_SLOTS`; downstream resolver gets confused | Pydantic validator rejects out-of-vocab; same offline-fallback path as malformed-output handles it; never reaches the kiosk | this doc §"Design decisions" §"Generator emits action_slot" |
| Sprite mismatch with step body | Generator picks a slot, but the step body's action doesn't actually match (e.g. body says "pretend to fly" but generator picks `jumping`) | Acceptable noise floor for v1 — `idle` is always a safe fallback; v1 ships with the 10-slot vocab as-is; if real-use shows a recurring mismatch class, follow-up phase expands the vocab | this doc §"Scope" §Out |
| Static file mount exposes sprite paths | Anyone on the LAN with a parent token + a guessed `(toy_id, slot)` URL can fetch the sprite without re-auth | Same posture as toy/room/persona images today; sprite paths are server-generated UUIDs (toy_id is UUIDv4); not a regression vs current state | this doc §"Design decisions" §"Static file serving" |
| Capability gate flips mid-session | Operator starts a Phase E LLM run that holds 10 GB; capability gate flips False; new ingests skip image-gen silently | UI surfaces the disabled-reason banner; operator can stop the LLM run + re-trigger ingest; no data loss; existing rows in `done` remain valid | this doc §"Design decisions" §"is_image_gen_capable() as a graceful-degradation gate" |
| `rembg` model download fragility | `rembg` lazy-downloads the model on first use; first-run from a fresh install may stall waiting on a model fetch | F1 operator step pre-downloads the model + records the path + sha256 + verifies offline-load; first generation should never hit a network fetch | this doc §"Build steps" §F1 |
| Pixel-art LoRA license + redistribution | Some LoRAs on civitai are not redistributable; we can't ship the file in `data/models/image_gen/` directly | F1 operator runbook documents the download URL + license; sha256 verifies integrity; any non-redistributable LoRAs are downloaded directly by the operator from upstream | this doc §"Build steps" §F1 |
| Soak overnight on a household GPU | 10-15 hours of sustained SDXL generation may overheat or stress the consumer GPU; thermal throttling could double the wall-clock | F10 logs `nvidia-smi --query-gpu=temperature.gpu` every 60s; abort criterion if temp >85C sustained for 10 min; if soak is consistently thermal-bound, follow-up phase adds a thermal-aware queue throttle | this doc §"Build steps" §F10 |
| Single source-photo per toy | Only ONE reference image is available for IP-Adapter conditioning; if that image is poorly lit or has a busy background, all 10 sprites are degraded | `rembg` subject-isolate strips the background before IP-Adapter sees it; if quality is poor, parent uploads a better photo via the existing toy-edit flow + hits regenerate; multi-photo support is out of scope for this phase | this doc §"Design decisions" §"IP-Adapter for subject identity" |

## Testing strategy

**Unit tests (every step):**
- F2: `pipeline.generate_action` against the stub-runtime fixture (deterministic 16×16 PNGs keyed off (slot, seed)) covering the orchestration; `ImageGenTimeoutError` raised when stub exceeds timeout; `ImageGenCapacityError` raised on simulated CUDA OOM; **lazy-import test** asserts `"torch" not in sys.modules` AND `"diffusers" not in sys.modules` immediately after `import toybox.image_gen.pipeline` (sys.modules-snapshot pattern works whether torch is installed in CI or not — if installed, asserts the lazy guard is real; if not installed, the test is trivially true and the gated `@pytest.mark.requires_gpu` integration test covers the real-import path); CLI `--probe` writes marker file
- F2 (gated): `@pytest.mark.requires_gpu` integration test runs the real pipeline once on the operator machine; asserts the output PNG is non-empty + valid + ≤`TOYBOX_IMAGE_GEN_PALETTE_COLORS` (default 32) colors + has alpha channel
- F3: migration forward + idempotence; `storage/toy_actions.py` round-trip CRUD with tmp DB; `list_for_toy` synthesizes `not_started` rows for missing slots; `delete_for_toy_archived` cascades on toy archive; `is_image_gen_capable()` covers all four return branches
- F4: worker queue lifecycle (start → enqueue → consume → stop); supersede semantics with rapid double-enqueue; restart-recovery sweep marks orphaned `running` rows `failed` with clear reason; per-pipeline breaker open/half-open/closed transitions; WS topic visible to parent token + invisible to child token
- F5: three endpoints with httpx covering golden + capability-disabled + non-existent-toy + non-existent-slot; toy-commit hook **integration test through the production toy-create handler** (per `feedback_buildstep_require_integration_test.md`) asserting 10 jobs land in the queue end-to-end (NOT just a unit test of the enqueue helper)
- F6: migration forward + idempotence; Pydantic validator accepts None + vocab member + rejects out-of-vocab with clear error; generator prompt update tested with stubbed Claude responses (good slot, bad slot, missing field) — bad + missing both fall back to None; offline-template loader rejects bad slots at boot; **integration test through `_do_propose`** asserts the slot survives REST + DB + WS
- F7: 4 vitest cases for `ToyActionSprite` (sprite renders, sprite hides on 404, sprite hides when `action_slot` null, sprite hides when `toy_ids` empty); `StepCard` layout snapshot regression; SFX-timing tests unchanged
- F8: 9 vitest cases (4 cells × 4 statuses + capability-disabled banner); reducer envelope-merge semantics (latest envelope per slot wins); regenerate-all fires 10 enqueues, regenerate-slot fires 1; parent-Playwright smoke covers full ingest → WS → grid → done flow

**Integration tests:**
- F4: full worker lifecycle with stub pipeline + real DB + captured WS envelopes; assert `queued` → `running` → `done` envelopes match DB transitions
- F5: full toy-create → 10 enqueues end-to-end; full per-slot regenerate → 1 enqueue + WS round-trip
- F6: full activity-propose → action_slot in DB → action_slot in WS envelope → `_do_propose` is the production caller
- F8: parent-Playwright smoke: upload toy → grid renders → WS emits 10 status transitions → done state shows all sprites

**End-to-end smoke (F9):** operator manual run of the full pipeline against a real GPU; pass criteria documented at the step

**Soak observation (F10):** 30-toy overnight unattended run; pass criteria documented at the step

**Existing tests that may break:**
- `ActivityStep` shape changes from F6 — grep for `ActivityStep(` constructions in `tests/unit/activities/` and `tests/integration/test_activities*.py`; fixtures may need an `action_slot=None` keyword
- `tests/integration/migrations/test_0001_initial.py` and any per-migration test pattern — F3 + F6 add new migration tests in the same pattern
- Pydantic-to-typescript codegen output changes from F5 + F6 — `frontend/src/shared/types.ts` regenerates; if it's snapshotted in tests (it isn't currently, but worth checking), re-baseline
- `Topic` enum union in WS server tests gains a new member — existing tests asserting "all topics in the enum" need extension

## Operator pre-flight before kicking off F1

1. Confirm CUDA toolkit + cuDNN installed + GPU visible to PyTorch: `uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0), torch.cuda.get_device_properties(0).total_memory / 1024**3)"`. Total memory should be ≥12 GB; <12 GB means the feature will be disabled by the capability gate (operator decision: skip Phase F or upgrade hardware).
2. Confirm at least 30 GB free under `data/models/image_gen/` for SDXL base (~7 GB) + IP-Adapter (~700 MB) + pixel-art LoRA (~250 MB) + rembg model (~170 MB) + working space.
3. Confirm Phase D PIN gate is configured (operator may want to test Phase F via the parent UI which is PIN-gated).
4. Decision: do you want to ship Phase E and Phase F sequentially or in parallel? If parallel, coordinate the migration numbering at build time (the second-to-ship phase renumbers).
5. (F10 only — observation soak prerequisite) Verify `nvidia-smi` is on PATH so the soak script can log VRAM + temperature: PowerShell `Get-Command nvidia-smi` (or bash `which nvidia-smi`) returns the binary path. On Windows it typically lives at `C:\Windows\System32\nvidia-smi.exe` after a recent driver install, but is sometimes missing from PATH on systems where the driver was installed by a non-default installer.

The capability gate (F2) means a household without a GPU can still SKIP all of Phase F at runtime — but the build steps F2-F8 still need to land, including their CI tests against the stub-runtime fixture, so the codebase is ready for any future household with a capable GPU.

## Appendix

### Action vocabulary

The 10 fixed slots, their ordinal position in `ACTION_SLOTS`, and the pose-detail string injected into the SDXL prompt template:

| # | slot | pose detail (`ACTION_PROMPTS[slot]`) | typical step verb |
|---|---|---|---|
| 0 | `idle` | "standing in a neutral pose, facing forward" | (default; ready / waiting) |
| 1 | `pointing` | "pointing at something off to the side, arm extended" | "point to", "look over there" |
| 2 | `looking` | "holding a magnifying glass up to one eye, examining something carefully" | "use your magnifying glass", "look closely" |
| 3 | `jumping` | "mid-jump in the air with both feet off the ground, arms raised" | "jump", "hop", "leap" |
| 4 | `cheering` | "both arms raised overhead in celebration, smiling broadly" | "celebrate", "good job!", "you did it!" |
| 5 | `thinking` | "one hand on chin in a thoughtful pose, looking slightly upward" | "think about", "what do you remember", "imagine" |
| 6 | `waving` | "one hand raised in a friendly wave gesture, smiling" | "say hi to", "wave hello", "greet" |
| 7 | `running` | "mid-stride running pose, leaning forward, both feet visible" | "run to", "race", "go quickly" |
| 8 | `sleeping` | "curled up with eyes closed in a peaceful sleeping pose" | "rest", "lie down", "take a nap" |
| 9 | `confused` | "shrugging with both shoulders raised, palms facing up, puzzled expression" | "I wonder", "what could it be", "I'm not sure" |

The full SDXL prompt template is `"<reference embedding>, pixel art, 16-bit, sprite, retro game style, transparent background, " + ACTION_PROMPTS[slot]`. Negative prompt: `"photorealistic, 3d, blurry, smooth shading, antialiased, gradient"` (pushes the model toward crisp pixel-art aesthetic).

### File layout

```
data/
├── images/
│   └── toy_actions/
│       └── <toy_id>/
│           ├── idle.png
│           ├── pointing.png
│           ├── looking.png
│           ├── jumping.png
│           ├── cheering.png
│           ├── thinking.png
│           ├── waving.png
│           ├── running.png
│           ├── sleeping.png
│           └── confused.png
└── models/
    └── image_gen/
        ├── sdxl/
        │   └── stable-diffusion-xl-base-1.0/...
        ├── ip_adapter/
        │   └── ip-adapter_sdxl_vit-h.safetensors
        ├── pixel_art_lora/
        │   └── pixel-art-xl.safetensors    # exact file chosen in F1
        ├── bg_remove/
        │   └── u2net.onnx                  # or isnet-general-use.onnx — F1 records choice
        └── .probe-pass-<iso>.json          # written by `python -m toybox.image_gen --probe`
```

### Env-var reference

| Var | Default | Purpose |
|---|---|---|
| `TOYBOX_IMAGE_GEN_ENABLED` | `auto` | `auto` = capability-gated; `true` = force-on (will fail loudly without GPU); `false` = force-off (skips ingest hook + capability probe; UI shows disabled banner) |
| `TOYBOX_IMAGE_GEN_DEVICE` | `cuda` | `cuda` or `cpu`; CPU mode is for testing only — generation takes >10 minutes per slot |
| `TOYBOX_IMAGE_GEN_MODEL_DIR` | `data/models/image_gen` | Root directory for the four checkpoints |
| `TOYBOX_IMAGE_GEN_OUTPUT_DIM` | `128` | Pixel-art sprite output dimension (square); reduce to 64 for tighter pixel-art look |
| `TOYBOX_IMAGE_GEN_PALETTE_COLORS` | `32` | Palette quantize color count; 16 = stricter 16-bit aesthetic; 64 = looser, more painterly |
| `TOYBOX_IMAGE_GEN_TIMEOUT_SEC` | `120` | Per-slot generation timeout; below this is normal SDXL latency on consumer GPU |
| `TOYBOX_IMAGE_GEN_MIN_VRAM_GB` | `12` | Capability gate floor; <floor disables the feature |
| `TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC` | `300` | Breaker open duration after 3 failures in 60s |

### WS payload — `Topic.toy_actions`

```json
{
  "topic": "toy_actions",
  "data": {
    "toy_id": "550e8400-e29b-41d4-a716-446655440000",
    "slot": "looking",
    "status": "done",
    "image_path": "data/images/toy_actions/550e8400-e29b-41d4-a716-446655440000/looking.png",
    "error": null
  }
}
```

`status` ∈ {`queued`, `running`, `done`, `failed`, `superseded`}. `image_path` is non-null only on `status="done"`. `error` is non-null only on `status="failed"` (200-char excerpt of the underlying exception).

### REST shapes

```
GET /api/toys/{id}/actions
  → 200 [{toy_id, slot, status, image_path?, seed?, error_msg?, updated_at}, ...]   # 10 rows always (synthesized for missing)
  → 404 {code: "toy_not_found"}
  → 409 {code: "image_gen_disabled", reason: "..."}                                 # capability gate off

POST /api/toys/{id}/actions/regenerate
  → 200 {queued: ["idle", "pointing", ..., "confused"]}                            # 10 slots
  → 404 {code: "toy_not_found"}
  → 409 {code: "image_gen_disabled", reason: "..."}

POST /api/toys/{id}/actions/{slot}/regenerate
  → 200 {queued: ["<slot>"]}
  → 404 {code: "toy_not_found"} | {code: "slot_not_in_vocab", slot: "<bad>"}
  → 409 {code: "image_gen_disabled", reason: "..."}
```
