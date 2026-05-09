# Phase F.5 — Sprite pipeline cartoon redo (post-F9-fail v1.5 retry)

> **Scope:** Phase F.5 build plan — replace the SDXL+IPA+pixel-art-LoRA pipeline that crashed F9 ([smoke run 2026-05-07](../runs/2026-05-07-toy-action-sprites-smoke.md), [issue #61](https://github.com/aberson/toybox/issues/61)) with a smaller cartoon-style pipeline (SD 1.5 + LCM-LoRA + cartoon LoRA at 512²) plus a Tier C sticker-composite fallback for hosts without a capable GPU. Carries the per-step `**Problem:**/**Type:**/**Issue:**/**Flags:**/**Status:**` shape that `/build-phase` parses. Sequenced after Phase F's F1-F8 (which DONE shipped 2026-05-06); supersedes the SDXL pipeline shipped in F2. Top-level overview is in [../plan.md](../plan.md). Detailed comparison of approaches considered is in [../runs/2026-05-08-sprite-pipeline-alternatives.md](../runs/2026-05-08-sprite-pipeline-alternatives.md).

## What this feature does

Replaces the `SDXL @ 1024² + IP-Adapter ViT-H + pixel-art LoRA + double rembg + palette quantize` pipeline with **SD 1.5 + LCM-LoRA (4 step) + cartoon style at 512²**, no IP-Adapter, no model_cpu_offload, no palette quantize. The cartoon style is selectable at runtime via env var: either a **full cartoon checkpoint** (e.g. ToonYou Beta 6, replacing SD 1.5 base) or **SD 1.5 base + cartoon LoRA add-on** (e.g. designPixar). Both are downloaded as part of F.5-1 so the F.5-4 smoke gate can A/B-test them and pick the winner.

Sprite generation becomes **fully offline at gen time** — prompts are templated locally from data already in the DB (`toys.display_name`, `toys.tags`, `personas.display_name`) plus Pillow color extraction from the rembg cutout. No new Claude calls. No new ingest latency. No new DB columns.

A **Tier C sticker-composite fallback** ships in the same phase: when `is_image_gen_capable()` returns False because of GPU/VRAM/missing-checkpoints (NOT env-disabled), the worker routes to `composite.py` instead of `pipeline.py`. The composite path uses 10 hand-curated cartoon action templates and the existing rembg + Pillow stack to produce sprites in <100 ms with zero VRAM. This unblocks households without a capable GPU, which today get nothing.

This phase is also the fix for [issue #61](https://github.com/aberson/toybox/issues/61) — the SDXL pipeline's `c10.dll` access violation prime suspect was the `enable_model_cpu_offload()` path interacting badly with IP-Adapter + LoRA + an 8 GB-marginal VRAM budget. SD 1.5 + LCM at 512² fits in ~4-5 GB peak and runs **without offload** (`pipe.to("cuda")` and stay), eliminating that whole class of bug by construction. F.5-5's soak gate is the verification.

## Existing context

- **F1-F8 shipped 2026-05-06** ([phase-f-toy-action-sprites.md](phase-f-toy-action-sprites.md)). The full pipeline is in production: `image_gen` module, `toy_actions` table, single-worker queue, REST endpoints, parent UI grid, child kiosk render, action_slot per step. **F9 smoke gate FAILED 2026-05-07** with a native PyTorch crash (3-of-10 sprites generated, then `c10.dll` access violation killed the backend). F10 soak is BLOCKED.
- **Public contract preserved:** `generate_action(reference_bytes, slot, seed, ctx) -> bytes`. F.5 changes the implementation, not the worker dispatch shape, the WS topic, the REST endpoints, or the DB schema.
- **`src/toybox/image_gen/`** has 4 modules. F.5 rewrites [`pipeline.py`](../../src/toybox/image_gen/pipeline.py); updates [`capability.py`](../../src/toybox/image_gen/capability.py) (`REQUIRED_CHECKPOINTS` to new paths); adds new `composite.py` for Tier C; leaves `worker.py` (single asyncio queue, dispatch logic gets a small branch) and `models.py` (`ACTION_SLOTS`, `ACTION_PROMPTS`, status enums — keeps shape).
- **DB schema unchanged.** The `toy_actions` table from migration 0005 still works. Migration counter is at 0006; **F.5 does NOT add a migration**.
- **Old sprites kept on cutover.** Existing toys' sprites (pixel-art) stay where they are on disk. `toy_actions` rows stay `done`. The parent UI still shows them. Operator clicks "regenerate all" if they want cartoon versions; otherwise the library is mixed pixel-art + cartoon for a transition period. No data loss, no migration code, no breaking parent-UI changes.
- **Capability gate keeps its 4-branch shape**, just with new checkpoint paths. The `env-disabled` branch hard-offs everything (no Tier B, no Tier C). The other branches (no CUDA / low VRAM / missing checkpoints) route to Tier C composite fallback. This is a behavioral change to the worker dispatch but NOT to the public API or the boot probe.
- **Operating mode** for autonomous build: per [`feedback_autonomous_build_bundled_ui.md`](../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md) `--reviewers code` for code steps; visual UI verification batches into F.5-4 (operator-driven smoke gate). See "Vocabulary and conventions" below for the full operating-mode rule.

## Vocabulary and conventions

Inline reference for terms used throughout this doc. Each item is one line.

**Diffusion stack:**
- **SDXL** = Stable Diffusion XL (Stability AI, 2023; native 1024×1024 image generator). The pipeline F.5 supersedes.
- **SD 1.5** = Stable Diffusion 1.5 (Runway/StabilityAI, 2022; native 512×512). F.5's new base model.
- **LoRA** = Low-Rank Adaptation. Small fine-tuning weight set (~50-200 MB) that composes onto a base model at runtime via `pipe.load_lora_weights(...)`.
- **LCM-LoRA** = Latent Consistency Model LoRA. A distillation that collapses 25-step DPM scheduling into 4-step inference; one of the LoRAs F.5 stacks on top of SD 1.5.
- **IP-Adapter / IPA** = Image-Prompt Adapter. Subject-identity conditioning from a reference photo, via a CLIP image encoder; F2 used the ViT-H variant. F.5 drops this entirely.
- **VAE** = Variational Autoencoder. The encoder/decoder pair that lifts pixels into the diffusion latent space; appears as `pipe.vae` in diffusers.
- **rembg** = background-removal library (https://github.com/danielgatis/rembg). F.5 uses its `u2net` ONNX model on CPU to produce alpha masks. Already in the F2 stack.

**Source / license:**
- **civitai** = civitai.com, the community model-sharing site for SD checkpoints and LoRAs. Default licenses permit local-private use; redistribution typically forbidden. Used as a candidate source for cartoon checkpoints/LoRAs in F.5-1.
- **CC0** = Creative Commons Zero (public domain dedication; no attribution required). Required license posture for templates shipped under `data/sprites/templates/` so they can be committed to the repo without legal risk.

**Identifiers:**
- `toy_id` = UUIDv4 string **without dashes** (32 hex chars; e.g. `3413ff7609024ebc900aee0a31205223` from the F9 smoke fixture). Stored as TEXT in the `toys` table; same shape across all F-phase tables.
- `slot` = `str`, member of `ACTION_SLOTS`.
- `seed` = `int` (64-bit; worker draws via `secrets.randbits(64)`); deterministic input to `pipeline.generate_action(...)` and `composite.composite_action(...)`.
- Run-doc filenames use `YYYY-MM-DD-<topic>.md` format (UTC date prefix) per the existing convention in `documentation/runs/`.

**`ACTION_SLOTS` vocabulary** (defined in [`src/toybox/image_gen/models.py`](../../src/toybox/image_gen/models.py)):
`idle, pointing, looking, jumping, cheering, thinking, waving, running, sleeping, confused` — 10 fixed members. The same 10 strings are used as filenames under `data/images/toy_actions/<toy_id>/<slot>.png` AND under `data/sprites/templates/<slot>.png`.

**Existing data shapes inherited from Phase F (load-bearing for F.5):**
- `toys.tags` is stored as `TEXT` containing a JSON array literal (e.g. `'["plush","unicorn","pink"]'`); the worker decodes via `json.loads()` before constructing `GenerationContext.tags: tuple[str, ...]`. F.5-2's `_build_prompt` rewrite consumes the decoded tuple.
- `toy_actions(toy_id, slot, status, image_path, seed, error_msg, updated_at)` — `PRIMARY KEY (toy_id, slot)`, `FOREIGN KEY (toy_id) REFERENCES toys(id) ON DELETE CASCADE`. Status enum: `queued | running | done | failed | superseded`. **F.5 changes none of this.**
- `GenerationContext` (in `src/toybox/image_gen/models.py`): `toy_display_name: str`, `persona_display_name: str | None`, `tags: tuple[str, ...]`. F.5 keeps the existing shape; the `tags` field is already there from F2 (just unused by the F2 prompt template, used by F.5).
- REST endpoint shapes (`GET /api/toys/{id}/actions`, `POST /api/toys/{id}/actions/regenerate`, `POST /api/toys/{id}/actions/{slot}/regenerate`) inherited unchanged from [`phase-f-toy-action-sprites.md` § "REST shapes"](phase-f-toy-action-sprites.md#rest-shapes). F.5-3a adds an optional `mode: "composite_only"` field on the 200 response (and changes the previous 409 to 200 when capability is False due to GPU/VRAM/checkpoints — but `ENV_DISABLED` still returns 409).
- WS `Topic.toy_actions` envelope shape inherited unchanged from Phase F appendix: `{topic: "toy_actions", data: {toy_id, slot, status, image_path?, error?}}` where `status ∈ {queued, running, done, failed, superseded}`.
- `/api/health` response includes `ai.claude_capable: bool` (Phase D step 24); F.5-4 step 1 reads it to record `tags_populated_by` per ingest.

**Build-step flag conventions** (per the `/build-step` skill — these appear in step `Flags:` lines):
- `--reviewers code` — runs four code-quality agent reviewers (correctness, bugs, tests, style). No runtime UI reviewer. **Default for the autonomous-build operating mode in this phase.**
- `--reviewers runtime` — runtime evidence reviewers (Playwright, smoke runs).
- `--reviewers full` — both code + runtime reviewers.
- `--reviewers auto` — tests-only.
- `--isolation worktree` (default) — agent works in a temporary git worktree.
- `--ui` — adds an explicit Playwright UI evidence pass.

**Step `Type:` taxonomy** (per the `/build-phase` skill):
- `code` — autonomous build agent (`/build-step`); produces a code diff.
- `operator` — manual procedure the operator performs (install software, curate art, run a UI smoke test). No code diff.
- `wait` — long-wall-clock observation; operator monitors an unattended system run (e.g. F.5-5 soak).

**Operator preference — bundled UI verification** (per `~/.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md`):
> UI-touching code steps run with `--reviewers code` (no runtime UI reviewer); visual UI verification batches into one operator-driven smoke gate at phase end. F.5-4 is that gate for this phase. F.5-3a's `--reviewers code` flag (despite touching `frontend/`) is a deliberate exception to the standard "frontend-touching steps need UI verification" rule, NOT an oversight. A `/build-step` agent dispatching F.5-3a should NOT add `--ui` thinking it's required.

**Prior Phase F step status** (so a fresh model can locate what's already shipped):

| Step | Goal | Status |
|---|---|---|
| F1 | Image-gen runtime install + smoke probe | DONE 2026-05-06 |
| F2 | Pipeline (SDXL+IPA+pixel-art-LoRA) | DONE 2026-05-06 — **superseded by F.5-2** |
| F3 | `toy_actions` table + storage + capability gate | DONE 2026-05-06 |
| F4 | Single-worker asyncio queue + WS topic | DONE 2026-05-06 |
| F5 | REST endpoints + ingest hook | DONE 2026-05-06 |
| F6 | `activity_steps.action_slot` migration + generator + offline templates | DONE 2026-05-06 |
| F7 | Child kiosk renders sprite next to StepCard | DONE 2026-05-06 |
| F8 | Parent UI 2×5 sprite grid + WS progress | DONE 2026-05-06 |
| F9 | Smoke gate (1 toy → 10 actions → activity) | **FAILED 2026-05-07** ([run-doc](../runs/2026-05-07-toy-action-sprites-smoke.md)); cause is [issue #61](https://github.com/aberson/toybox/issues/61) |
| F10 | 30-toy soak | BLOCKED on F9 / #61 — F.5 unblocks |

## Scope

**In:**
- `pipeline.py` rewrite to SD 1.5 + LCM-LoRA + cartoon style at 512², env-selectable between full-checkpoint and base+LoRA modes
- `capability.py` `REQUIRED_CHECKPOINTS` updated for new layout; new env knobs `TOYBOX_IMAGE_GEN_BASE_MODEL_PATH`, `TOYBOX_IMAGE_GEN_CARTOON_LORA_PATH` (optional), `TOYBOX_IMAGE_GEN_LCM_LORA_PATH`
- New `composite.py` Tier C fallback module — rembg cutout + paste onto template + save PNG
- 10 hand-curated cartoon action templates under `data/sprites/templates/<slot>.png` — gitignored if non-redistributable, with `.gitkeep` placeholders
- Worker dispatch logic: when capability is False due to GPU/VRAM/checkpoints, route enqueued jobs to `composite.py` instead of returning 409
- Stub fixture updated to match new public surface (signature unchanged; assertions on output may need adjusting)
- Tests updated: `test_pipeline_stub.py`, `test_capability.py`, `test_lazy_imports.py`, `test_models.py`, `test_cli.py`, `test_worker.py`, `test_image_gen_real_gpu.py`, `test_image_gen_worker_e2e.py`, plus new `test_composite.py`
- Operator runbook at `documentation/operator/image-gen-runtime.md` updated for new install procedure (SD 1.5 + LCM + cartoon options) + sha256s
- F.5-4 smoke gate: A/B comparison between cartoon-checkpoint and base+LoRA modes; capability-False composite path verification
- F.5-5 soak gate: 30 toys × 10 sprites unattended

**Out:**
- DB schema changes (none needed — `toy_actions` shape preserved)
- API surface changes (none — REST endpoints + WS topic unchanged)
- Per-toy DreamBooth / textual inversion (rejected in investigation: training-time VRAM > 8 GB)
- IP-Adapter retention with smaller models (rejected: ViT-H encoder is the biggest single cost; dropping it is the point)
- Online image-gen fallback (rejected per Phase F's existing scope)
- Animated sprites or multi-toy compositions (out of Phase F's existing scope)
- Claude-driven prompt rewrites at ingest time (deferred as future opt-in enhancement gated by `is_capable()`; mentioned for completeness only)
- Backfilling cartoon sprites for existing toys (operator-driven via "regenerate all"; no auto-trigger)
- Cleaning up old SDXL checkpoints (`data/models/image_gen/sdxl/`, `ip_adapter/`, `pixel_art_lora/` can stay on disk — they're ~9 GB but not actively loaded; operator can delete manually)
- Capability-gate env-disabled hard-off behavior change (preserved as-is — `TOYBOX_IMAGE_GEN_ENABLED=false` still skips composite too)

## Impact analysis

| File / module | Nature | Notes |
|---|---|---|
| `src/toybox/image_gen/pipeline.py` | REWRITE | Drop IPA, pixel-art-LoRA, model_cpu_offload, palette quantize. Load SD 1.5 base + LCM-LoRA + (cartoon LoRA OR cartoon checkpoint via env). New `_build_prompt` template using DB fields + Pillow palette. Keep rembg pre/post passes. Public `generate_action()` signature unchanged. |
| `src/toybox/image_gen/capability.py` | MODIFY | Replace `REQUIRED_CHECKPOINTS` constant with new SD 1.5 + LCM + cartoon paths. Add `TOYBOX_IMAGE_GEN_CARTOON_MODE=checkpoint\|lora` env handling so the gate validates the right files for the configured mode. |
| `src/toybox/image_gen/composite.py` | NEW | Tier C fallback. `composite_action(reference_bytes, slot, ctx) -> bytes`. rembg → Pillow paste onto `data/sprites/templates/<slot>.png` → return PNG bytes. Pure CPU, ~100 ms. |
| `src/toybox/image_gen/worker.py` | MODIFY | Small dispatch branch: when `is_image_gen_capable()=False` due to GPU/VRAM/checkpoints (NOT env-disabled), route the job to `composite_action` instead of returning 409 / failing. Tier B + Tier C share the same `(toy_id, slot, seed) → PNG bytes` interface and the same DB write path. |
| `src/toybox/image_gen/models.py` | UNCHANGED | `ACTION_SLOTS`, `ACTION_PROMPTS`, status enums all keep shape. New env knobs live in `pipeline.py` / `capability.py`, not here. |
| `src/toybox/image_gen/__main__.py` | MODIFY | `--probe` CLI updates to load the new pipeline; the marker file pattern stays. |
| `src/toybox/api/toys.py` | MODIFY | When capability gate flips reason from "image_gen_disabled" to "image_gen_composite_only", surface that to the parent UI as a banner ("running in composite-only mode — sprites will be lower fidelity"). The endpoint behavior changes: 409 only on env-disabled; 200 + queued in composite-only mode. |
| `frontend/src/parent/components/ToyActionGrid.tsx` | MODIFY | Render the new "composite-only mode" banner reason text when the capability response indicates Tier C is active. The disabled-banner shape from F8 already supports a free-text reason; this is one new reason string, not a layout change. |
| `frontend/src/parent/api.ts` | UNCHANGED | The actions endpoints' response shape doesn't change; only the textual `reason` value differs. |
| `tests/fixtures/image_gen/stub_pipeline.py` | MODIFY | Stub returns deterministic PNGs for both `generate_action` AND `composite_action` paths (both use the same signature). |
| `tests/unit/image_gen/test_pipeline_stub.py` | MODIFY | Update assertions for the new pipeline shape (no quantize, no IPA, no offload). The "lazy import" assertions are unchanged. |
| `tests/unit/image_gen/test_capability.py` | MODIFY | Update for new `REQUIRED_CHECKPOINTS`; add tests for `TOYBOX_IMAGE_GEN_CARTOON_MODE` env handling (checkpoint vs lora branch). |
| `tests/unit/image_gen/test_lazy_imports.py` | UNCHANGED-ish | Still asserts `torch` and `diffusers` aren't loaded by `import toybox.image_gen.pipeline`. The list of imports inside `_run_pipeline_sync` changes but the lazy-import contract doesn't. |
| `tests/unit/image_gen/test_models.py` | UNCHANGED | Action slots + status enums unchanged. |
| `tests/unit/image_gen/test_cli.py` | MODIFY | New `--probe` path for the new pipeline. |
| `tests/unit/image_gen/test_worker.py` | MODIFY | Add tests for capability-False-routes-to-composite branch. |
| `tests/unit/image_gen/test_composite.py` | NEW | Unit tests for Tier C: composite produces a valid PNG with alpha; missing template handled gracefully (404-style fallback to no sprite); deterministic output for same input. |
| `tests/integration/test_image_gen_real_gpu.py` | MODIFY | `@pytest.mark.requires_gpu` integration test now exercises the SD 1.5 + LCM cartoon pipeline. Asserts: peak VRAM < 6 GB, wall-clock < 5 s/sprite, output is valid RGBA PNG. |
| `tests/integration/test_image_gen_worker_e2e.py` | MODIFY | Add a test branch for capability-False worker dispatch (uses stub composite path). |
| `tests/integration/test_app_startup_image_gen_probe.py` | MODIFY | Update for new boot-probe expected reason strings. |
| `tests/integration/test_toys_api_actions.py` | MODIFY | Add a test for the new "composite-only mode" 200-response branch (vs the old 409 hard-off). |
| `data/models/image_gen/sd15/` | NEW DIR | SD 1.5 base checkpoint + LCM-LoRA (~3 GB + ~70 MB). Gitignored. |
| `data/models/image_gen/cartoon_lora/` | NEW DIR | Cartoon LoRA add-on candidate (e.g. designPixar, ~150 MB). Gitignored. |
| `data/models/image_gen/cartoon_checkpoint/` | NEW DIR | Full cartoon checkpoint candidate (e.g. ToonYou Beta 6, ~4 GB). Gitignored. |
| `data/models/image_gen/{sdxl,ip_adapter,pixel_art_lora}/` | OBSOLETE | ~9 GB of unused checkpoints. Operator can delete; F.5 doesn't auto-clean. |
| `data/models/image_gen/manifest.json` | UPDATE | New SHA256s for SD 1.5 + LCM + cartoon; old SDXL/IPA hashes can stay or be removed (not load-bearing once `REQUIRED_CHECKPOINTS` is updated). |
| `data/sprites/templates/<slot>.png` | NEW (10 files) | Hand-curated cartoon action templates for Tier C composite. ~256×256 PNG with alpha. License-explicit (CC0 or operator-drawn) to avoid redistribution issues. |
| `documentation/operator/image-gen-runtime.md` | REWRITE | New install procedure: SD 1.5 base + LCM-LoRA + (cartoon checkpoint AND cartoon LoRA), sha256s, smoke probe, env-var reference, troubleshooting (incl. "old SDXL checkpoints can be deleted to reclaim ~9 GB"). |
| `.env.example` | MODIFY | New env vars: `TOYBOX_IMAGE_GEN_BASE_MODEL_PATH`, `TOYBOX_IMAGE_GEN_CARTOON_MODE` (`checkpoint\|lora`), `TOYBOX_IMAGE_GEN_CARTOON_PATH`, `TOYBOX_IMAGE_GEN_LCM_LORA_PATH`. Update `TOYBOX_IMAGE_GEN_OUTPUT_DIM` default from 128→128 (unchanged but documented), drop `TOYBOX_IMAGE_GEN_PALETTE_COLORS` (no longer used). |
| `pyproject.toml` | UNCHANGED-ish | Optional-deps `image_gen` extra still needs `diffusers`, `transformers`, `torch`, `rembg`, `Pillow`; `transformers` is no longer strictly required (no IP-Adapter image encoder) but is a thin import; leave it in to avoid cycle-time on the next thing that wants it. |

## New components

### `src/toybox/image_gen/pipeline.py` (rewritten)
Same public entry: `async def generate_action(reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext) -> bytes`. Same lazy-import + `asyncio.to_thread` + `wait_for` timeout pattern. Same module-level pipeline cache for warm reuse across slots. Differences from F2's pipeline:

1. **Subject isolate** — rembg u2net (unchanged; CPU).
2. **Pipeline construction (cached, mode-selectable):**
   - **`TOYBOX_IMAGE_GEN_CARTOON_MODE=checkpoint`** (default): `StableDiffusionPipeline.from_pretrained(TOYBOX_IMAGE_GEN_CARTOON_PATH, ...)` — full cartoon checkpoint replaces SD 1.5 base.
   - **`TOYBOX_IMAGE_GEN_CARTOON_MODE=lora`**: `StableDiffusionPipeline.from_pretrained(TOYBOX_IMAGE_GEN_BASE_MODEL_PATH, ...)` then `pipe.load_lora_weights(TOYBOX_IMAGE_GEN_CARTOON_PATH, adapter_name="cartoon")`.
   - In both cases: `pipe.load_lora_weights(TOYBOX_IMAGE_GEN_LCM_LORA_PATH, adapter_name="lcm")`, `pipe.set_adapters(["lcm"], adapter_weights=[1.0])` (or `["lcm", "cartoon"]` in lora mode), `pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)`.
   - **No IP-Adapter, no model_cpu_offload, no attention_slicing.** `pipe.to("cuda")` and stay.
   - `pipe.vae.enable_slicing()` retained (cheap, helps VAE decode peak at 512²).
3. **Prompt construction (fully local):**
   ```python
   def _build_prompt(slot, ctx, palette_hex):
       intro = (
           f"{ctx.persona_display_name} the {ctx.toy_display_name}"
           if ctx.persona_display_name
           else f"a {ctx.toy_display_name}"
       )
       tags = ", ".join(ctx.tags) if ctx.tags else ""
       palette = ", ".join(f"primary color {h}" for h in palette_hex[:3])
       return (
           f"{intro}, {tags}, {palette}, "
           f"{ACTION_PROMPTS[slot]}, "
           f"2D cartoon, simple shapes, clean lines, transparent background"
       )
   ```
   `ctx` gets a new `tags: tuple[str, ...]` field (populated by the worker from `toys.tags`); existing fields (`toy_display_name`, `persona_display_name`) unchanged.
4. **Palette extraction:** Pillow `cutout.quantize(colors=8, method=MEDIANCUT).getpalette()` → top-3 hex codes. Sub-100 ms.
5. **Generate:** `pipe(prompt, num_inference_steps=4, guidance_scale=1.0, height=512, width=512, generator=torch.Generator("cuda").manual_seed(seed))`. (`guidance_scale=1.0` is the LCM convention; higher CFG hurts LCM output.)
6. **Output bg-remove:** rembg second pass on the 512² result for clean alpha.
7. **No palette quantize step.** Cartoon LoRAs already produce flat-color output; quantizing on top adds banding without aesthetic benefit.
8. **Output resize:** the kiosk renders at 96-128 px; current pipeline downsamples to 128. Keep that — Pillow `resize((128, 128), BILINEAR)` after the bg-remove pass.

CLI: `uv run python -m toybox.image_gen --probe <toy_id> --slot idle` — same shape as today, exercises the full new pipeline. Marker file at `data/models/image_gen/.probe-pass-<iso>.json` (unchanged).

### `src/toybox/image_gen/composite.py` (new)
Tier C fallback. Pure-CPU composite path that produces sprites without diffusion. Public entry mirrors `generate_action`:

```python
async def composite_action(
    reference_bytes: bytes,
    slot: str,
    seed: int,
    ctx: GenerationContext,
) -> bytes:
    """Composite the toy photo onto a hand-drawn cartoon action template.

    Same signature as ``pipeline.generate_action`` so the worker can
    dispatch to either based on capability gate state. ``seed`` is
    accepted but unused (composites are fully deterministic from
    inputs). Returns 128x128 RGBA PNG bytes.
    """
```

Steps:
1. **rembg cutout** of the reference bytes (u2net, CPU, ~100 ms).
2. **Load template** from `data/sprites/templates/<slot>.png` (cached after first read).
3. **Resize cutout** to fit the template's "toy area" (a fixed bounding box per template, defined in `data/sprites/templates/manifest.json`).
4. **Pillow paste** with alpha mask: composite the cutout into the template's toy area.
5. **Output:** 128×128 RGBA PNG bytes.

If the template file is missing (e.g. operator hasn't curated all 10 yet): log WARNING and raise `ImageGenCapacityError("composite template missing for slot=...")` — the worker marks the row failed with that message. Parent UI shows the failure reason.

Templates are PNG with alpha + a small `manifest.json` declaring the toy bounding box per slot:

```json
{
  "idle": {"toy_box": [40, 60, 90, 110], "behind": false},
  "pointing": {"toy_box": [50, 70, 100, 120], "behind": true},
  ...
}
```

`behind: true` means the toy is composited UNDER the template (e.g. behind a pointing-arrow shape so the arrow is on top); `behind: false` means the toy is composited OVER (e.g. on top of a small "stage" shape underneath).

### `src/toybox/image_gen/worker.py` (modified)
Existing dispatch logic:
```python
if not is_capable:
    record_failure_with_reason("image_gen_disabled")
    return
result = await pipeline.generate_action(...)
```

Becomes:
```python
capable, reason = is_image_gen_capable()
if not capable:
    if reason.startswith("image-gen disabled via TOYBOX_IMAGE_GEN_ENABLED"):
        # Hard-off: env-disable wins, no Tier C either.
        record_failure_with_reason("image_gen_disabled")
        return
    # GPU/VRAM/checkpoints unavailable but env not disabled → Tier C.
    result = await composite.composite_action(...)
else:
    result = await pipeline.generate_action(...)
# Same DB write + WS emit path for both branches.
```

The `(queued, running, done, failed, superseded)` status flow is unchanged. The `error_msg` for capability-gated rows changes from `"image_gen_disabled"` to `"image_gen_composite_only: <reason>"` (when env-disabled stays the existing string). The supersede-on-rapid-double-enqueue logic is unchanged.

### `src/toybox/image_gen/capability.py` (modified)
`REQUIRED_CHECKPOINTS` becomes mode-aware:

```python
def _required_checkpoints() -> tuple[str, ...]:
    mode = os.environ.get("TOYBOX_IMAGE_GEN_CARTOON_MODE", "checkpoint").lower()
    base = (
        "sd15/lcm_lora/pytorch_lora_weights.safetensors",
        "bg_remove/u2net.onnx",
    )
    if mode == "checkpoint":
        return base + ("cartoon_checkpoint/model_index.json",
                       "cartoon_checkpoint/unet/diffusion_pytorch_model.fp16.safetensors")
    elif mode == "lora":
        return base + ("sd15/base/model_index.json",
                       "sd15/base/unet/diffusion_pytorch_model.fp16.safetensors",
                       "cartoon_lora/pytorch_lora_weights.safetensors")
    else:
        # Unknown mode: validate base only; pipeline.py will reject at load time.
        return base
```

Boot probe still runs `is_image_gen_capable()`; reason strings keep the four-branch shape. The new `image-gen disabled via TOYBOX_IMAGE_GEN_ENABLED` reason is unchanged.

### `data/sprites/templates/` (new directory, 10 PNGs + 1 manifest)
Hand-curated 256×256 cartoon templates, one per `ACTION_SLOTS` member. Each is a transparent-background PNG depicting an action archetype (a pointing arrow + speech bubble for `pointing`; a magnifying glass for `looking`; a bouncing motion-blur for `jumping`; etc.). The toy photo gets composited into a designated bounding box per template — declared in `manifest.json` per the shape above.

**License posture:** templates ship as either CC0 art (sourced from openpeeps.com or similar permissive libraries, recolored/adjusted) OR operator-drawn original art. NOT civitai-sourced (license risk). Document the source per template in `data/sprites/templates/CREDITS.md`. The directory is gitignored if any source is non-redistributable; `.gitkeep` placeholders ship for missing slots.

### `documentation/operator/image-gen-runtime.md` (rewritten)
F.5-1 deliverable. Sections:

- GPU floor (now 6 GB recommended, 4 GB minimum — relaxed from F1's 12 GB recommendation)
- Driver install (unchanged from F1; CUDA 11.8 or 12.x + cuDNN 8.x)
- Model checkpoint download — explicit URLs + sha256s for: SD 1.5 base, LCM-LoRA SD 1.5, cartoon checkpoint candidate (ToonYou or equivalent), cartoon LoRA candidate (designPixar or equivalent), rembg u2net (unchanged from F1)
- File layout under `data/models/image_gen/sd15/`, `cartoon_checkpoint/`, `cartoon_lora/`
- Env-var reference table (new: `TOYBOX_IMAGE_GEN_CARTOON_MODE`, `TOYBOX_IMAGE_GEN_BASE_MODEL_PATH`, `TOYBOX_IMAGE_GEN_CARTOON_PATH`, `TOYBOX_IMAGE_GEN_LCM_LORA_PATH`)
- Smoke probe — `uv run python -m toybox.image_gen --probe <toy_id> --slot idle`; expected wall-clock <10 s; expected output: valid RGBA PNG with no quantize banding
- Cleaning up old SDXL checkpoints — instructions for the ~9 GB of obsolete `data/models/image_gen/{sdxl,ip_adapter,pixel_art_lora}/`
- Troubleshooting — CUDA OOM (now much rarer with 4-5 GB peak), driver mismatch, missing checkpoints, "composite-only mode" banner explanation

## Design decisions

### Drop IP-Adapter entirely (vs keep at lower scale)
IP-Adapter's sole purpose was preserving subject identity from the reference photo. Its costs: ~2.5 GB ViT-H image encoder + ~700 MB IPA weights + ~1 GB activations + a long `pipe.load_ip_adapter()` initialization that's the most fragile part of the F2 pipeline (the c10.dll crashes happened during repeated forwards through the IPA-aware UNet). The operator explicitly relaxed the identity requirement: "more important that the toy be doing the action 'pointing' or showing an emotion 'happy' than be detailed." With identity off the critical path, IPA's ROI evaporates. `display_name` + `tags` (already in DB) + Pillow palette tokens carry the recognition floor a 5-year-old needs (dominant color + dominant shape + one signature element). Sibling toys that differ only in a small detail will collide — accepted, documented, and Tier C composite is the recovery path when that matters.

### SD 1.5 over SDXL Lightning / SDXL Turbo at 4-step
Same step count, same speed-class, but SDXL Lightning/Turbo carry the SDXL UNet (~6 GB fp16 alone, before any LoRA / IPA / activations). Same UNet is what's been crashing the box. The win from "fewer steps" is dwarfed by the loss from "still loaded the same too-big UNet that's crashing." SD 1.5 + LCM-LoRA is the only credible option that fits in <5 GB peak with headroom for whisper-on-CUDA contention AND avoids the offload path that's the prime crash suspect.

### LCM-LoRA over SDXL Lightning baked-into-UNet
LCM-LoRA is a strict add-on (one `load_lora_weights` call); base model is unchanged; can be enabled/disabled per-call. Lightning swaps the UNet weights and is harder to back out of. LCM-LoRA is also the most-tested 4-step path on SD 1.5 specifically; Lightning is SDXL-native. Use LCM-LoRA, base stays clean.

### Two cartoon modes (full checkpoint vs base+LoRA), env-selectable
Operator's explicit ask was to "allow both options, see which works better." The pipeline supports either via `TOYBOX_IMAGE_GEN_CARTOON_MODE`. F.5-4's smoke gate A/B-tests them on the same fixture toy and the operator picks the winner; the runbook lists the chosen one as default. No code commitment to one over the other in F.5-2; the env handling is the entire delta. Tradeoff: slightly more env config surface; gain: real measurement-driven decision instead of a guess. The "winner" is either documented in the F.5-4 run-doc with the choice, OR left as a runtime knob if both produce acceptable output.

### Drop `enable_model_cpu_offload()` AND `enable_attention_slicing()`
At ~4-5 GB peak, the pipeline fits in 8 GB without offload. `pipe.to("cuda")` everything once at construction, never move. This eliminates the entire class of "tensor on cuda:0 vs cpu" device-mismatch bugs (the corroborating evidence for the c10.dll crash root cause) by construction, not by debugging. `enable_attention_slicing()` is forbidden per the F1 "three diffusers gotchas" (clobbers IPA-aware attention processors); we no longer have IPA, but PyTorch 2.4+ SDPA already provides memory-efficient attention by default — slicing adds nothing and may interact badly with LCM scheduling. Just don't call it.

### Drop palette quantize post-process
The pixel-art LoRA + 32-color quantize stack was load-bearing for the pixel-art aesthetic. The cartoon LoRA / checkpoint produces flat-color output natively. Quantizing on top of cartoon output adds banding artifacts without aesthetic benefit. Drop the step; sprite saves are direct PNGs from the diffusion output (after rembg cleanup).

### Tier C composite as inline Pillow code, not a separate module gate
The composite path is ~50 lines of Pillow + rembg (already in stack). It lives in `composite.py` for cleanliness, but doesn't need its own capability gate, breaker, or worker queue — it shares all of those with Tier B. The worker dispatch is one branch (`if capable: pipeline; else: composite`). The breaker treats composite failures the same as Tier B failures (3 in 60 s opens). This keeps the operational model simple: one queue, one breaker, one capability surface.

### Env-disabled stays hard-off (does NOT route to composite)
`TOYBOX_IMAGE_GEN_ENABLED=false` is operator-explicit "I don't want sprite generation at all." Routing that to Tier C composite would surprise the operator. Other capability-False reasons (no CUDA, low VRAM, missing checkpoints) DO route to Tier C — those are "the system can't do Tier B but the operator hasn't said stop." The four capability branches keep their existing reason strings; the dispatch in `worker.py` distinguishes by reason prefix.

### Old sprites preserved on cutover (not auto-regenerated)
Existing toys have `toy_actions` rows in `done` state pointing at pixel-art PNG files on disk. F.5 does NOT auto-trigger a regenerate-all sweep at boot (that would be 10-300 jobs queued unattended on first start, with no operator visibility — surprising and risky). Files stay where they are; the parent UI displays them; the operator can click "regenerate all" per-toy if they want cartoon versions. Library will be mixed pixel-art + cartoon during the transition. Documented in F.5-1's runbook.

### Public `generate_action()` signature unchanged (`ctx` grows internally)
The four-arg signature `(reference_bytes, slot, seed, ctx)` is preserved. `GenerationContext` gains a `tags: tuple[str, ...]` field (today populated from `toys.tags`) — additive, backwards-compatible if existing callers ignore it. The worker construction site changes to populate the new field; no other caller needs updating.

## Build steps

| # | Step | Type | Reviewers (canonical) | Done-when summary |
|---|------|------|----------------------|-------------------|
| F.5-1 | Operator: download SD 1.5 + LCM + cartoon candidates; update runbook + manifest sha256s; tag rollback point | operator | n/a | All four checkpoint families on disk + sha256-verified; load smoke (one-line `from_pretrained` test) succeeds for both cartoon modes; runbook landed; `git tag pre-f5-cutover` set so F.5-5 has a rollback path |
| F.5-2 | Pipeline rewrite: SD 1.5 + LCM + cartoon (env-selectable), drop IPA / pixel-art / quantize / offload / `transformers`, replace `_build_prompt`, update `REQUIRED_CHECKPOINTS`, update stub fixture, update tests | code | `--reviewers code` | New pipeline.py + capability.py changes ship; grep for `ip_adapter`, `pixel_art_lora`, `enable_model_cpu_offload`, `CLIPVisionModelWithProjection` in `src/toybox/image_gen/` returns zero matches in non-test code; all unit tests green; `@pytest.mark.requires_gpu` integration test produces valid RGBA PNG end-to-end on operator host; lazy-import contract preserved |
| F.5-3a | Tier C composite fallback (code): `composite.py` + worker dispatch via new `CapabilityReason` enum + frontend banner + tests; templates ship as `.gitkeep` placeholders | code | `--reviewers code` (UI evidence intentionally bundled to F.5-4 per `feedback_autonomous_build_bundled_ui.md`) | `composite.py` + `CapabilityReason(StrEnum)` ship; `is_image_gen_capable()` returns enum + detail string; worker dispatches on enum (no prefix-string matching); `data/sprites/templates/.gitkeep` shipped with empty manifest; new `test_composite.py` + worker dispatch test green; parent UI banner reflects composite-only mode |
| F.5-3b | Operator: curate 10 cartoon action templates + manifest.json + CREDITS.md | operator | n/a | All 10 PNGs committed under `data/sprites/templates/<slot>.png`; `manifest.json` declares `toy_box` + `behind` per slot; `CREDITS.md` records source + license per template (CC0 / operator-drawn — NOT civitai); each template loads via Pillow without error |
| F.5-4 | Smoke gate: 1 toy → 10 cartoon sprites, A/B between cartoon modes, capability-False composite verification, pick + document winner | operator | n/a | 10/10 sprites complete on a fresh toy without c10.dll crashes in BOTH cartoon modes; per-slot wall-clocks + nvidia-smi snapshots recorded; tags-source recorded per ingest (Claude vs operator-typed); visual comparison documented; capability-False composite path produces 10/10 valid PNGs; winner picked (tiebreak: `checkpoint` mode) + runbook updated; pass/fail in `documentation/runs/<date>-toy-action-sprites-cartoon-smoke.md` |
| F.5-5 | Soak: 30 toys × 10 sprites = 300 jobs unattended (clean DB), expected ~10 min wall-clock; soft-pass 280+ done, **0 native crashes** | wait | n/a | Soak report at `documentation/runs/<date>-toy-action-sprites-cartoon-soak.md` with: total wall-clock, success rate, peak VRAM, breaker transitions, native crash count (HARD threshold: 0), any anomalies. Soft-pass criterion: 280+/300 done + 0 native crashes + no leaked VRAM. Closes #61 if soft-pass met |

**Issues:** Phase F.5 umbrella → #62 · F.5-1 → #63 · F.5-2 → #64 · F.5-3a → #65 · F.5-3b → #66 · F.5-4 → #67 · F.5-5 → #68. Issue #61 closes via reference from F.5-5's soak report.

**Sequencing:** F.5-1 → {F.5-2, F.5-3a, F.5-3b} (all three parallel after F.5-1) → F.5-4 → F.5-5. F.5-2 ⊥ F.5-3a (disjoint files: F.5-2 owns `pipeline.py`+`capability.py`+`__main__.py`+image_gen tests; F.5-3a owns NEW `composite.py`+`worker.py`+`api/toys.py`+frontend grid+new tests). F.5-3b is pure art work, independent of code changes. The only soft coupling is F.5-3a's `test_image_gen_worker_e2e.py` test that asserts capability-False routes to composite — uses the stub composite (deterministic 16×16 PNG) so doesn't need real templates from F.5-3b.

#### Step F.5-1: Operator — download checkpoints + update runbook

- **Problem:** Operator-driven download of SD 1.5 + LCM-LoRA + cartoon-mode candidates. **First action: `git tag pre-f5-cutover`** to capture the F2 pipeline state on disk for the F.5-5 rollback path. Then the install work. Exact files:
  - **SD 1.5 base** from HF: `stable-diffusion-v1-5/stable-diffusion-v1-5` (the official mirror that took over after Runway delisted `runwayml/stable-diffusion-v1-5` in late 2024). ~3 GB safetensors. Save under `data/models/image_gen/sd15/base/`.
  - **LCM-LoRA SD 1.5** from HF: `latent-consistency/lcm-lora-sdv1-5`. ~70 MB. Save under `data/models/image_gen/sd15/lcm_lora/`.
  - **Cartoon-mode-checkpoint candidate** — operator picks. Canonical recommendation: ToonYou Beta 6 from civitai (~4 GB) for the strong cartoon style; alternatives include any SD 1.5 cartoon fine-tune (Lykon/dreamshaper-7 is HF-hosted and permissively licensed if civitai is undesirable). Save under `data/models/image_gen/cartoon_checkpoint/`.
  - **Cartoon-mode-LoRA candidate** — operator picks. Canonical recommendations are civitai-hosted cartoon SD-1.5 LoRAs (search "pixar cartoon lora SD 1.5"); any LoRA <500 MB works. Save under `data/models/image_gen/cartoon_lora/`.
  
  All sha256-verified against upstream-published checksums; sha256s recorded in `data/models/image_gen/manifest.json`. **Runbook rewrite** at `documentation/operator/image-gen-runtime.md` covers: install procedure, GPU floor (relaxed to 4 GB minimum / 6 GB recommended), env-var reference (new vars), smoke probe, cleanup of old SDXL checkpoints (~9 GB reclaimable via `Remove-Item -Recurse -Force data/models/image_gen/{sdxl,ip_adapter,pixel_art_lora}` — NOT auto-deleted by F.5; operator runs after F.5-5 passes so the rollback path stays available). **Load-only smoke** (operator pastes one snippet to confirm checkpoints load): `python -c "from diffusers import StableDiffusionPipeline; p = StableDiffusionPipeline.from_pretrained('data/models/image_gen/sd15/base', torch_dtype='float16'); p.load_lora_weights('data/models/image_gen/sd15/lcm_lora'); print('loaded ok')"` AND `python -c "from diffusers import StableDiffusionPipeline; p = StableDiffusionPipeline.from_pretrained('data/models/image_gen/cartoon_checkpoint', torch_dtype='float16'); print('loaded ok')"`. Both must complete without OOM on the operator's host.
- **Type:** operator
- **Issue:** #63
- **Flags:** n/a (operator step; not invoked through `/build-step`)
- **Status:** PENDING
- **Depends on:** none (kicks off F.5)
- **Parallel-safe with:** none — strictly first; F.5-2's real-GPU test depends on these checkpoints being on disk; F.5-3a's tests use the stub so don't need real checkpoints, but F.5-3a sequenced after F.5-1 for cleanliness
- **Done when:** All four checkpoint families on disk under `data/models/image_gen/{sd15/{base,lcm_lora},cartoon_checkpoint,cartoon_lora,bg_remove}/`; manifest.json updated with sha256s for each; both load-only smoke snippets succeed without OOM; runbook landed at `documentation/operator/image-gen-runtime.md` covering install + sha256s + env-var reference + GPU floor + troubleshooting + cleanup recipe; `git tag pre-f5-cutover` set.

#### Step F.5-2: Pipeline rewrite

- **Problem:** Rewrite [`src/toybox/image_gen/pipeline.py`](../../src/toybox/image_gen/pipeline.py) per §"New components". Key deltas from F2: drop IP-Adapter loading + reference-image conditioning; drop pixel-art LoRA; drop palette quantize step; drop `enable_model_cpu_offload()` (`pipe.to("cuda")` and stay); drop `enable_attention_slicing()` (already forbidden); **drop the `from transformers import CLIPVisionModelWithProjection` import entirely** (no IPA = no encoder needed; `transformers` stays in `pyproject.toml` extras for future re-use but the pipeline.py import line goes away). Add `from diffusers import LCMScheduler` import (lazy, inside `_run_pipeline_sync`); add LCM-LoRA loading + `pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)`. Add cartoon mode dispatch via `TOYBOX_IMAGE_GEN_CARTOON_MODE=checkpoint|lora` (default `checkpoint`). Replace `_build_prompt` with the DB-fields + Pillow-palette template (signature change: takes a new `palette_hex: tuple[str, ...]` arg derived from the cutout). Pipeline cache reuse pattern preserved (one warm pipeline per process). Generation params: `num_inference_steps=4`, `guidance_scale=1.0`, `height=512`, `width=512`. Output: 128×128 RGBA PNG (resize after rembg cleanup; no quantize). Update [`src/toybox/image_gen/capability.py`](../../src/toybox/image_gen/capability.py) `REQUIRED_CHECKPOINTS` to mode-aware shape. Update [`src/toybox/image_gen/__main__.py`](../../src/toybox/image_gen/__main__.py) `--probe` to load the new pipeline (signature unchanged). Update `tests/fixtures/image_gen/stub_pipeline.py` to match the new public surface (signature unchanged; assertions on output may need adjusting if the stub was checking for quantize markers). Update `.env.example` per the migration table in the appendix. **Update tests:** `test_pipeline_stub.py` (drop quantize/IPA assertions; add LCM/cartoon-mode assertions), `test_capability.py` (new `REQUIRED_CHECKPOINTS` shape; new mode env handling), `test_lazy_imports.py` (still asserts torch + diffusers not loaded by `import toybox.image_gen.pipeline` — contract preserved; ALSO assert `transformers` not loaded), `test_models.py` (no change), `test_cli.py` (probe path updates), `test_image_gen_real_gpu.py` (`@pytest.mark.requires_gpu`-gated — runs against real checkpoints; assert peak VRAM <6 GB via `torch.cuda.max_memory_allocated()`, wall-clock <5 s, RGBA PNG with valid alpha channel; parametrized over both cartoon modes). Pipeline cache survives env changes ONLY via process restart (same as today; document inline).
- **Type:** code
- **Issue:** #64
- **Flags:** --reviewers code
- **Status:** PENDING
- **Depends on:** Step F.5-1 (#63 — checkpoints on disk so `@pytest.mark.requires_gpu` test can run)
- **Parallel-safe with:** F.5-3a (#65) — disjoint files: F.5-2 owns `pipeline.py` + `capability.py` + `__main__.py` + `image_gen` tests; F.5-3a owns `composite.py` + `worker.py` + `api/toys.py` + frontend grid + new tests. The only soft coupling: F.5-3a adds a `CapabilityReason` enum to `capability.py`. To avoid a merge conflict, F.5-3a's changes to `capability.py` are additive (add the enum; change `is_image_gen_capable()` return type); F.5-2's changes to `capability.py` are localized to `REQUIRED_CHECKPOINTS`. The two PRs touch different parts of the same file — easy three-way merge. Parallel-safe also with F.5-3b (#66, pure art work).
- **Done when:** New `pipeline.py` + `capability.py` + `__main__.py` ship; `pipe.to("cuda")` once at construction; 4-step LCM generation runs end-to-end against the real checkpoints on operator host; `@pytest.mark.requires_gpu` integration test asserts peak VRAM <6 GB, wall-clock <5 s, output is 128×128 RGBA PNG with non-trivial alpha channel; lazy-import test still green AND extended to assert `transformers` not loaded; all updated unit tests green; **`grep -r "ip_adapter\|pixel_art_lora\|enable_model_cpu_offload\|CLIPVisionModelWithProjection" src/toybox/image_gen/` returns zero matches** (the grep is the deterministic verification — `enable_attention_slicing` MAY appear in `tests/unit/image_gen/` for the AST regression test pinning the F1 gotcha, but nowhere in `src/`); `.env.example` updated; `pydantic-to-typescript` codegen unchanged (no API surface delta); `ruff` + `mypy strict` clean.

#### Step F.5-3a: Tier C composite fallback (code)

- **Problem:** New module [`src/toybox/image_gen/composite.py`](../../src/toybox/image_gen/composite.py): `async def composite_action(reference_bytes, slot, seed, ctx) -> bytes`. Same public signature as `pipeline.generate_action` so the worker dispatches without ceremony. Steps: rembg u2net cutout → load `data/sprites/templates/<slot>.png` (cached after first read; per-slot template) → load `data/sprites/templates/manifest.json` (cached) → resize cutout to fit `manifest[slot].toy_box` → composite with alpha mask onto the template (above or below the template depending on `manifest[slot].behind`) → 128×128 RGBA PNG bytes. Pure CPU, ~100 ms. **Templates ship as `.gitkeep` placeholders in this step** — the operator-curated PNGs land in F.5-3b. If any required template is missing at runtime, `composite_action` raises `ImageGenCapacityError("composite template missing for slot=<slot>")` and the worker marks the row failed with that message; this is the legitimate state between F.5-3a landing and F.5-3b completing. **`CapabilityReason(StrEnum)` introduced in [`src/toybox/image_gen/capability.py`](../../src/toybox/image_gen/capability.py)** with members `CAPABLE`, `ENV_DISABLED`, `NO_CUDA`, `LOW_VRAM`, `MISSING_CHECKPOINTS`. `is_image_gen_capable()` return type changes from `tuple[bool, str]` to `tuple[bool, CapabilityReason, str]` — the bool stays for backwards-compat at call sites that only need yes/no, the enum is the dispatch key, the string is human-readable detail (e.g. `"VRAM 6.9GB < floor 12.0GB"`) for UI display. **Worker dispatch:** modify [`src/toybox/image_gen/worker.py`](../../src/toybox/image_gen/worker.py) so when capability returns False, the worker dispatches on the enum: `ENV_DISABLED` → existing hard-off behavior (record `error_msg="image_gen_disabled"`); `NO_CUDA` / `LOW_VRAM` / `MISSING_CHECKPOINTS` → route to `composite_action` (record `error_msg="image_gen_composite_only"` ONLY when composite itself fails; success path uses no error_msg). **No prefix-string matching anywhere in dispatch.** **API + UI surface:** [`src/toybox/api/toys.py`](../../src/toybox/api/toys.py) endpoints update to return 200 + queued (instead of 409 `image_gen_disabled`) when capability is False but composite is available; the response body indicates `mode: "composite_only"` so the parent UI can render a banner ("running in composite-only mode — sprites will be lower fidelity"). The 409 hard-off response is still returned for `ENV_DISABLED`. [`frontend/src/parent/components/ToyActionGrid.tsx`](../../frontend/src/parent/components/ToyActionGrid.tsx) updates to render that banner reason text. **`error_msg` consumer audit:** existing consumers of `toy_actions.error_msg` (parent UI per-cell tooltip; metrics dashboard via Step 24's `/api/metrics`) — verify both still display sensibly given the new `"image_gen_composite_only"` value AND the no-error-msg success path; if either parses by string match, update accordingly. The plan does NOT introduce a separate `error_code` enum column (avoids a migration); accept the slight blur of putting structured-ish data in a free-text column, document inline. **Tests:** new `tests/unit/image_gen/test_composite.py` covering: composite produces valid RGBA PNG with non-trivial alpha (use a small fixture template + manifest from `tests/fixtures/`); missing template raises `ImageGenCapacityError`; deterministic output for same inputs (composite has no randomness); per-slot toy_box honored. Update `tests/integration/test_image_gen_worker_e2e.py` to add a capability-False-routes-to-composite branch (uses a stub composite that returns deterministic PNGs). Update `tests/integration/test_toys_api_actions.py` for the new "composite_only" 200-response branch + regression check that `ENV_DISABLED` still returns 409. **Lazy import:** composite.py only needs Pillow + rembg (already in stack); no new imports outside the existing extras. Composite path uses the same image-gen breaker as Tier B (same queue, one breaker, simpler operational model — accept the slight semantic blur and document inline).
- **Type:** code
- **Issue:** #65
- **Flags:** --reviewers code (UI evidence intentionally bundled to F.5-4 per [`feedback_autonomous_build_bundled_ui.md`](../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md))
- **Status:** PENDING
- **Depends on:** Step F.5-1 (#63) — capability.py changes are independent of pipeline.py; tests use the existing stub fixture. Soft coupling with F.5-2 (#64): both touch `capability.py` (F.5-2 modifies `REQUIRED_CHECKPOINTS`; F.5-3a adds `CapabilityReason` + return-type change). Edits are localized to different functions/symbols; clean three-way merge.
- **Parallel-safe with:** F.5-2 (#64, disjoint primary file sets; small `capability.py` overlap as noted above) and F.5-3b (#66, pure art work, fully independent)
- **Done when:** `composite.py` ships; `CapabilityReason(StrEnum)` ships in `capability.py`; `is_image_gen_capable()` returns the new 3-tuple shape; worker dispatch branches on the enum (no prefix-string matching anywhere — `grep -r "startswith.*image-gen disabled\|startswith.*TOYBOX_IMAGE_GEN_ENABLED" src/toybox/` returns zero matches in dispatch code); `data/sprites/templates/.gitkeep` shipped (NO real PNGs in this step — those land in F.5-3b); empty `data/sprites/templates/manifest.json` shipped (`{}`); new `test_composite.py` green using a tests/fixtures/ template (separate from `data/sprites/templates/` so the test doesn't depend on F.5-3b); updated `test_image_gen_worker_e2e.py` covers both Tier B and Tier C dispatch branches; updated `test_toys_api_actions.py` covers the 200 + `mode: "composite_only"` branch AND the still-409 `ENV_DISABLED` branch; parent UI banner renders the new reason string; pydantic-to-ts codegen reflects any response shape additions; ruff + mypy strict clean.

#### Step F.5-3b: Operator — curate Tier C templates

- **Problem:** Operator produces 10 cartoon action templates + `manifest.json` + `CREDITS.md` under `data/sprites/templates/`. Each template is a 256×256 transparent-background PNG depicting an action archetype: a pointing arrow + speech bubble for `pointing`; a magnifying glass for `looking`; motion-blur lines for `jumping`; a stage / floor / spotlight for `idle`; etc. The toy photo gets composited into a designated bounding box per template — declared in `manifest.json` per the shape pinned in §"New components" / `composite.py`. **Source posture:** CC0 art OR operator-drawn — NOT civitai (license risk for templates that ship in the repo, even on private hosts). Recommended sources: openpeeps.com (CC0 SVG kit), unDraw.co (CC0 illustrations), or operator-original work in any 2D editor. Recolor / adapt CC0 sources as needed. **`CREDITS.md`** at `data/sprites/templates/CREDITS.md` records source + license per template (one line per slot: `idle: openpeeps.com base, recolored — CC0`). **Manifest values:** `toy_box` is `[x0, y0, x1, y1]` in template-pixel coordinates (256×256 templates → values 0..255); `behind: true` composites the toy UNDER the template (e.g. behind a pointing-arrow shape so the arrow is on top), `behind: false` composites OVER. Operator can iterate template quality without code changes — replacing PNGs is hot-reloadable on the next composite call (composite.py caches manifest + templates per process; restart to pick up new versions). **Smoke check (operator-runnable):** `python -c "from PIL import Image; import json, sys; m=json.load(open('data/sprites/templates/manifest.json')); [Image.open(f'data/sprites/templates/{slot}.png').verify() for slot in ['idle','pointing','looking','jumping','cheering','thinking','waving','running','sleeping','confused']]; print('all 10 templates valid')"` — must print successfully.
- **Type:** operator
- **Issue:** #66
- **Flags:** n/a (operator step; manual art curation)
- **Status:** PENDING
- **Depends on:** Step F.5-1 (#63) — so operator knows the slot vocabulary is final. NOT dependent on F.5-2 or F.5-3a; F.5-3b can run in parallel with both.
- **Parallel-safe with:** F.5-2 (#64), F.5-3a (#65) — different file sets entirely (`data/sprites/templates/*.png` + `manifest.json` + `CREDITS.md`); zero overlap with code.
- **Done when:** All 10 PNGs committed under `data/sprites/templates/<slot>.png` (slot ∈ ACTION_SLOTS); `manifest.json` declares all 10 slots with `toy_box` + `behind` + `source` per the appendix shape; `CREDITS.md` records source + license per template; operator smoke check command above prints `"all 10 templates valid"`.

#### Step F.5-4: Smoke gate — 1 toy, 10 cartoon sprites, A/B + composite verification

- **Problem:** Operator smoke test against the real GPU. Steps:
  1. Verify capability gate is `(True, CapabilityReason.capable, "capable")` (`is_image_gen_capable()` startup log shows green). Also confirm Claude vision capability via `(Invoke-RestMethod http://127.0.0.1:8000/api/health).ai.claude_capable` — record `True` or `False` in the run-doc; this determines whether toy ingest populates `tags` via Claude or via operator-typed entry, which affects how rich the prompt template gets.
  2. Confirm `TOYBOX_IMAGE_GEN_CARTOON_MODE=checkpoint` (the default per `.env.example`). Upload one fresh toy via parent UI. Record `tags_populated_by: claude|operator` in the run-doc per the toy.
  3. Run an `nvidia-smi` snapshot logger in a separate PowerShell window: `while ($true) { nvidia-smi --query-gpu=memory.used,temperature.gpu --format=csv,noheader >> logs/f54-vram.csv; Start-Sleep -Seconds 1 }` so per-slot VRAM + temp data is captured alongside the visual judgment.
  4. Watch the WS-driven grid update; record per-slot wall-clock times. Confirm 10/10 sprites land within ~30 s total. Visually rate: recognizable toy, clear action verb.
  5. Trigger one activity (manual trigger button). Watch the kiosk render the sprite next to step body for each step transition.
  6. Hit "regenerate" on one slot. Confirm queued → running → done with a different image (seed advanced).
  7. Switch to `TOYBOX_IMAGE_GEN_CARTOON_MODE=lora`. Restart backend. Hit "regenerate all" on the same toy. Compare per-slot output vs step 4.
  8. **Capability-False verification:** stop the backend; rename `data/models/image_gen/sd15/base/model_index.json` → `model_index.json.bak` to force `REQUIRED_CHECKPOINTS` to fail; restart backend; confirm capability log shows `(False, CapabilityReason.missing_checkpoints, "checkpoints missing: ...")`; upload a SECOND fresh toy; confirm 10/10 composite sprites generate from the templates (each ~100 ms, ~1 s total); confirm parent UI shows "running in composite-only mode" banner. Restore the renamed file when done. **Then verify env-disabled hard-off:** stop backend; set `$env:TOYBOX_IMAGE_GEN_ENABLED='false'`; restart; upload a THIRD toy; confirm 409 `image_gen_disabled` response (no composite fallback); unset env when done.
  9. **Pick winner.** Visual eval + wall-clock data. **Tiebreak rule (apply when both modes look equally good):** pick `checkpoint` mode — single-model load = fewer moving parts, simpler operator setup, cleaner default. Update `.env.example` + runbook with the winner as default. Document the visual rating + the chosen mode (and tiebreak rationale, if used) in the run-doc.

  **Pass criteria:** 10/10 sprites in BOTH cartoon modes without c10.dll crash, ≤30 s total per run, recognizable toys + clear action verbs in operator's visual judgment, capability-False composite path produces 10/10 valid PNGs, env-disabled still hard-offs at 409, regenerate works, no native crashes in any of the runs. Operator records observations in `documentation/runs/<date>-toy-action-sprites-cartoon-smoke.md` per the existing run-doc convention; attach `logs/f54-vram.csv`.

  **If any criterion fails:** file follow-up issue, do NOT proceed to F.5-5. Tier B not working blocks F.5-5; Tier C not working blocks the household-without-GPU story but is not a hard F.5-5 blocker (doc the gap, file follow-up, optionally proceed).
- **Type:** operator
- **Issue:** #67
- **Flags:** n/a (operator step; manual verification of the full pipeline)
- **Status:** PENDING
- **Depends on:** Step F.5-2 (#64, cartoon pipeline) AND Step F.5-3a (#65, composite + worker dispatch) AND Step F.5-3b (#66, real templates for Tier C verification — without these, step 8 fails on `composite template missing`)
- **Parallel-safe with:** none — verification gate after the full implementation chain
- **Done when:** Smoke run report at `documentation/runs/<date>-toy-action-sprites-cartoon-smoke.md` with all criteria passing; cartoon-mode winner picked + documented (tiebreak rule applied if needed); runbook + `.env.example` updated with winner as default; capability-False composite verified; env-disabled hard-off regression verified; `logs/f54-vram.csv` attached.

#### Step F.5-5: Soak — 30 toys × 10 sprites unattended

- **Problem:** Soak gate. Same shape as the original Phase F F10, with the new pipeline. Required by the build-phase autonomous-system observation pattern (background async worker + producer-consumer chain → unit tests insufficient; system must run end-to-end with realistic inputs and be watched long enough to expose time-dependent failures: cumulative VRAM creep, queue starvation, breaker false-trips, race conditions, native PyTorch crashes that wouldn't surface in a 60 s smoke). Steps:
  1. **Pre-flight: install Windows Error Reporting LocalDumps registry key for `python.exe`** so any native crash during the soak produces a minidump for post-mortem (insurance against re-running the soak just to capture a dump):
     ```powershell
     $key = "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\python.exe"
     New-Item -Path $key -Force | Out-Null
     New-ItemProperty -Path $key -Name "DumpFolder" -Value "$pwd\logs\crashdumps" -PropertyType ExpandString -Force | Out-Null
     New-ItemProperty -Path $key -Name "DumpType" -Value 2 -PropertyType DWORD -Force | Out-Null
     New-ItemProperty -Path $key -Name "DumpCount" -Value 10 -PropertyType DWORD -Force | Out-Null
     New-Item -ItemType Directory -Path logs/crashdumps -Force | Out-Null
     ```
     (Requires administrator PowerShell. Skip if elevation isn't available — soak still runs, just no minidumps if crash happens.)
  2. **Reset DB to a known clean state** (explicit, not vague):
     ```powershell
     # Stop backend first (see global teardown pattern)
     uv run python -c @"
     import sqlite3
     c = sqlite3.connect('data/toybox.db')
     c.execute('UPDATE toys SET archived=1')          # archive everything from prior runs (F9 fixtures + F.5-4 toys)
     c.execute('DELETE FROM toy_actions')             # truncate; FK cascade not needed since archived toys' rows can't accept new sprites anyway
     c.commit()
     print('toys archived:', c.execute('SELECT COUNT(*) FROM toys WHERE archived=1').fetchone())
     print('toy_actions remaining:', c.execute('SELECT COUNT(*) FROM toy_actions').fetchone())
     # Expected: archived count >= 4 (F9 fixtures + F.5-4 smoke toys); toy_actions: 0
     "@
     # Confirm data/images/toy_actions/ has no orphan PNG dirs from prior runs
     Get-ChildItem data/images/toy_actions -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
     ```
  3. Restart backend. Confirm boot capability gate logs `(True, CAPABLE, "capable")`.
  4. Ingest 30 toys via parent UI in one sitting (~3 minutes upload work; the worker runs ~30 × 10 = 300 generation jobs).
  5. Leave running unattended. **Expected wall-clock: ~10 minutes** (300 jobs × ~2 s/sprite). This is dramatically faster than the original F10's overnight projection because the new pipeline is ~15× faster per sprite — the soak completes inside a coffee break.
  6. After completion, audit:
      - All 300 jobs reached `done` OR `failed` with a recorded reason (none stuck `queued` or `running`).
      - **No native crashes** (Windows Application event log clean — no `python.exe` / `c10.dll` faulting application records during the soak window). Check `logs/crashdumps/` is empty (or absent if pre-flight skipped).
      - Peak VRAM stayed under 6 GB (`nvidia-smi --query-gpu=memory.used --format=csv` logged every 30 s during the soak; same one-line PowerShell loop as F.5-4 step 3, redirected to `logs/f55-vram.csv`).
      - WS envelopes match DB state (no envelopes dropped).
      - Breaker did not false-trip (recorded breaker state transitions in logs).
      - No backend restarts during the soak.
  7. Hit "regenerate all" on the 5 toys with the most failures (if any) and confirm recovery.
  Report at `documentation/runs/<date>-toy-action-sprites-cartoon-soak.md` with: total wall-clock, success rate, peak VRAM, breaker transitions, native crash count (MUST be 0), any anomalies + their resolution. Attach `logs/f55-vram.csv` and `logs/crashdumps/*` (if any).
  **Soft-pass criterion:** 280+/300 jobs successful AND **0 native crashes** AND no autonomous-system invariant violated (no leaked VRAM, no stuck rows, no false breaker trips). Native crash count is a hard threshold — even 1 c10.dll-class crash means F.5 has not actually fixed #61 and the phase fails. **Rollback path on hard fail:** `git checkout pre-f5-cutover` (the tag from F.5-1) + ensure `data/models/image_gen/{sdxl,ip_adapter,pixel_art_lora}/` are still on disk (operator should NOT have run the cleanup recipe yet).
- **Type:** wait
- **Issue:** #68
- **Flags:** n/a (operator step; long-wall-clock observation — though "long" is now ~10 min not overnight)
- **Status:** PENDING
- **Depends on:** Step F.5-4 (#67, smoke gate passed)
- **Parallel-safe with:** none — final observation gate
- **Done when:** Soak report landed at `documentation/runs/<date>-toy-action-sprites-cartoon-soak.md`; soft-pass met (280+/300 + 0 native crashes + no invariant violation); if not met, file follow-up issues for each invariant violation and re-run F.5-5 after fixes. Issue #61 closes via reference from this report (soak passing IS the fix verification). Operator may now run the obsolete-checkpoint cleanup recipe (`Remove-Item -Recurse -Force data/models/image_gen/{sdxl,ip_adapter,pixel_art_lora}`) to reclaim ~9 GB; the rollback option is gone after this point but soak-pass means rollback isn't needed.

## Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| LCM-LoRA + cartoon-LoRA interaction | Two LoRAs stacked on SD 1.5 may conflict on the UNet attention layers, producing degraded output | F.5-2 uses `pipe.set_adapters(["lcm", "cartoon"], adapter_weights=[1.0, 1.0])` (the diffusers-recommended multi-adapter API). F.5-4 smoke gate is the empirical check; if quality is bad, the cartoon-checkpoint mode is the fallback (same phase, no follow-up needed) |
| Cartoon checkpoint license / redistributability | ToonYou-class checkpoints are civitai-sourced with creator-set licenses; bundling for distribution could violate terms | F.5-1 documents license per checkpoint in the runbook; for household-private use, local download is fine; if distribution becomes a real path, switch to HF-Hub-hosted equivalents with explicit licenses (see [investigation doc](../runs/2026-05-08-sprite-pipeline-alternatives.md#q2--cartoon-style-loras--fine-tunes)) |
| Tier C template curation cost | 10 hand-drawn cartoon templates is a one-time art investment; if templates look bad, Tier C output is a dealbreaker | Templates can iterate without code changes (just replace PNGs); F.5-4 smoke includes a Tier C visual check; first-pass templates can be CC0-sourced + Pillow-recolored, refined later |
| Native crash reappears at scale | F.5-5 soak might still hit a c10.dll crash from a different code path even though we removed offload + IPA | Run as a hard gate: 0 native crashes is the soft-pass criterion. If it fails, the dispatch ratio of 1 crash per 300 jobs is much better than F2's 1 per 4 sprites — but the ROOT cause hasn't been fixed; file follow-up. F.5-5 step 1 pre-installs the Windows Error Reporting `LocalDumps\python.exe` registry key so a crash produces an immediate minidump under `logs/crashdumps/` — no second soak run needed to capture the data. Rollback path: `git checkout pre-f5-cutover`. The Tier C path is unaffected by torch crashes since it doesn't load torch |
| Subject identity collisions | Two near-identical toys (twin teddy bears) may produce visually-indistinguishable cartoon sprites | Accepted per investigation doc + operator's explicit relaxation of the identity requirement; Tier C composite path is the recovery option for cases where it matters (the photo IS the toy, no possibility of collision) |
| Whisper-on-CUDA still co-resident | Whisper-small on CUDA + SD 1.5 + LCM cartoon at peak ~5 GB = ~5.5 GB total of 8 GB, vs old SDXL stack's ~6.5 GB. Better but not zero contention | New peak provides ~2.5 GB headroom for transient overshoots, vs old stack's ~1.5 GB. F.5-5 soak measures actual contention behavior. If still problematic, a follow-up phase moves whisper to CPU (whisper-small CPU is ~2× slower but VRAM contention disappears) |
| Old SDXL checkpoints on disk | ~9 GB of unused weights under `data/models/image_gen/{sdxl,ip_adapter,pixel_art_lora}/` after F.5 — wastes disk, confuses operators reading the dir | F.5-1 runbook documents the cleanup procedure; F.5 does NOT auto-delete (operator might want them for rollback); after F.5-5 passes, operator can safely `Remove-Item -Recurse -Force` the obsolete subdirs |
| Cartoon-mode A/B inconclusive | F.5-4 might find both modes produce comparable output; "winner" choice becomes arbitrary | Acceptable outcome — keep both modes supported via the env knob; pick checkpoint as the default (single load = simpler operator setup) and document that operators preferring LoRA composability can flip the env var |
| Migration of existing toys' `toy_actions` rows | Old rows reference pixel-art PNG files; the parent UI grid renders them mixed with new cartoon sprites until the operator regenerates | Documented in F.5-1 runbook + UI banner ("some sprites are from the previous pixel-art pipeline; click 'regenerate all' to refresh"). No data loss; visual inconsistency is acceptable for a transition period |
| Capability-gate reason-string parsing in worker | A naive worker dispatch would branch on `reason.startswith("image-gen disabled via TOYBOX_IMAGE_GEN_ENABLED")` — fragile string-matching | **In F.5-3a's scope (NOT deferred):** introduce `CapabilityReason(StrEnum)` (`CAPABLE`, `ENV_DISABLED`, `NO_CUDA`, `LOW_VRAM`, `MISSING_CHECKPOINTS`); `is_image_gen_capable()` returns `(bool, CapabilityReason, str)`; worker dispatches on the enum. `grep -r "startswith.*TOYBOX_IMAGE_GEN_ENABLED" src/toybox/` returns zero matches in dispatch code is part of F.5-3a's done-when |
| Tier C runs even when env-disabled was the operator's intent | If an operator misunderstands the env knob, they might disable expecting Tier C to take over | F.5-3 documents inline + in runbook: `TOYBOX_IMAGE_GEN_ENABLED=false` is hard-off, period. To run composite-only, operator can rename a checkpoint dir aside (the F.5-4 smoke procedure does this) — explicit, intentional |
| Pillow palette extraction quality | `quantize(colors=8).getpalette()` may over-emphasize background colors if rembg cutout is imperfect | The cutout is alpha-masked before quantize; only opaque pixels contribute to the palette. Edge cases (toys with mostly-transparent details) may underweight signature colors; accepted, documented |

## Testing strategy

**Unit tests (every step):**
- F.5-2: `test_pipeline_stub.py` covers `_build_prompt` template output (DB-fields + palette tokens), cartoon-mode env dispatch (checkpoint vs lora branches), `_palette_extract` produces sensible top-3 hex codes; `test_capability.py` covers new `REQUIRED_CHECKPOINTS` mode-aware shape, all four return branches with new reason strings, missing-checkpoint case for both modes; `test_lazy_imports.py` asserts `torch` and `diffusers` not loaded by `import toybox.image_gen.pipeline` (contract preserved through the rewrite); `test_cli.py` covers new `--probe` path; `test_models.py` unchanged.
- F.5-3: `test_composite.py` covers composite produces valid RGBA PNG, missing template raises `ImageGenCapacityError`, deterministic output for same inputs, per-slot toy_box honored, manifest parsing; `test_worker.py` adds capability-False-routes-to-composite branch (uses stub composite for determinism).

**Integration tests:**
- F.5-2: `test_image_gen_real_gpu.py` (`@pytest.mark.requires_gpu`) — full pipeline against real checkpoints on operator host; asserts peak VRAM <6 GB via `torch.cuda.max_memory_allocated()`, wall-clock <5 s, output is valid 128×128 RGBA PNG with non-trivial alpha. Update for both cartoon modes (test parametrization).
- F.5-3: `test_image_gen_worker_e2e.py` adds capability-False branch — sets up a fixture with capability returning `(False, "checkpoints missing: ...")`, asserts the worker dispatches to composite stub and writes the PNG to the expected path. `test_toys_api_actions.py` adds the 200 + `mode: "composite_only"` response branch; verifies the env-disabled case still returns 409.
- F.5-4: full single-toy run against real GPU (manual operator step); A/B both cartoon modes; capability-False composite path verified by renaming a checkpoint dir aside.

**End-to-end smoke (F.5-4):** operator manual run; pass criteria documented at the step.

**Soak observation (F.5-5):** 30-toy unattended run; pass criteria documented at the step.

**Existing tests that may break (audit before F.5-2 lands):**
- Tests that assert SDXL-specific behavior (palette quantize markers, IPA-specific output shape, 1024² output dim): grep `1024\|quantize\|ip_adapter\|pixel_art` in `tests/{unit,integration}/image_gen/`. Each will need updating or removing.
- `tests/integration/test_app_startup_image_gen_probe.py` references the boot-probe reason strings — update for new mode-aware shape.
- The `pydantic-to-typescript` codegen pre-commit hook may detect `mode: "composite_only"` as a new field on the action endpoints' response — re-baseline `frontend/src/shared/types.ts` in F.5-3.
- Old SDXL/IPA checkpoint references in capability tests — update to new file paths.
- F2's "three diffusers gotchas" tests (`enable_attention_slicing` AST regression test) stay valid — F.5 still doesn't call attention_slicing.

**Eval / regression:**
- Phase F doesn't have a numerical eval like Phase E. Quality assessment is operator-visual at F.5-4. If quality regresses noticeably between F2 (pixel-art) and F.5 (cartoon), that's a feature, not a bug — the user explicitly asked for cartoon over pixel-art. Document operator's visual rating in the F.5-4 run-doc for posterity.

## Operator pre-flight before kicking off F.5-1

1. Confirm CUDA toolkit + cuDNN installed + GPU visible to PyTorch (unchanged from F1): `uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`.
2. Confirm at least 12 GB free under `data/models/image_gen/` for SD 1.5 base (~3 GB) + LCM-LoRA (~70 MB) + cartoon checkpoint (~4 GB) + cartoon LoRA (~150 MB) + working space + the existing ~9 GB of obsolete SDXL/IPA/pixel-art-lora checkpoints (which can be cleaned up after F.5-5 passes — keep them through F.5-5 for the rollback path).
3. Confirm Phase F F1-F8 are DONE on this host (the F.5 worker + table + REST surface depends on them).
4. Recommended: archive `data/toybox.db` to a backup before F.5-1 starts (`Copy-Item data/toybox.db data/toybox.db.pre-f5-bak`). F.5 doesn't migrate the schema, but rolling back is easier with a backup.
5. **Rollback insurance:** F.5-1's first action is `git tag pre-f5-cutover` (its Done when verifies this). If F.5-5 fails native-crash check, recovery is `git checkout pre-f5-cutover` + restore old SDXL checkpoints from `data/models/image_gen/{sdxl,ip_adapter,pixel_art_lora}/` (which F.5 leaves on disk through F.5-5 for exactly this reason).
6. **(F.5-3b prerequisite)** Have CC0 art sources or willingness to draw 10 cartoon action templates handy. Simplest path: use openpeeps.com (CC0) base figures, recolor + add per-action props (magnifying glass, motion-blur lines, speech bubbles) in any 2D editor. F.5-3b can run in parallel with F.5-2 + F.5-3a, so starting on the art early shortens the critical path.
7. **(F.5-5 prerequisite, optional but recommended)** Install the Windows Error Reporting `LocalDumps\python.exe` registry key per the F.5-5 step 1 PowerShell snippet. Requires administrator PowerShell. Without this, a native crash during F.5-5 leaves no minidump and the only follow-up data is the Application event log. With the key set, you get a full minidump in `logs/crashdumps/` per crash, which makes root-causing the c10.dll signature dramatically easier if F.5 doesn't actually fix #61.
8. **(F.5-1 license note)** The cartoon checkpoint and cartoon LoRA candidates are typically civitai-sourced. Civitai default licenses permit local-private use; bundling for distribution may not be permitted. For a household-private kiosk this is fine. Document whichever you pick + its license in `documentation/operator/image-gen-runtime.md` as part of F.5-1.

## Appendix

### Cartoon mode env-var matrix

| `TOYBOX_IMAGE_GEN_CARTOON_MODE` | Loads | Notes |
|---|---|---|
| `checkpoint` (default) | `from_pretrained(TOYBOX_IMAGE_GEN_CARTOON_PATH)` | Full cartoon checkpoint replaces SD 1.5 base. Single model load. |
| `lora` | `from_pretrained(TOYBOX_IMAGE_GEN_BASE_MODEL_PATH)` then `load_lora_weights(TOYBOX_IMAGE_GEN_CARTOON_PATH)` | SD 1.5 base + cartoon LoRA composed via `set_adapters(["lcm","cartoon"])`. Easier to swap cartoon styles later. |

`TOYBOX_IMAGE_GEN_LCM_LORA_PATH` is ALWAYS loaded (default `data/models/image_gen/sd15/lcm_lora`).

### Tier C template manifest shape (`data/sprites/templates/manifest.json`)

```json
{
  "idle": {"toy_box": [40, 60, 90, 110], "behind": false, "source": "openpeeps-base + recolor"},
  "pointing": {"toy_box": [50, 70, 100, 120], "behind": true, "source": "operator-drawn"},
  "looking": {"toy_box": [45, 65, 95, 115], "behind": false, "source": "openpeeps-magnifier"},
  "jumping": {"toy_box": [40, 50, 90, 100], "behind": false, "source": "openpeeps-jump"},
  "cheering": {"toy_box": [40, 70, 90, 120], "behind": true, "source": "operator-drawn"},
  "thinking": {"toy_box": [50, 60, 100, 110], "behind": false, "source": "operator-drawn"},
  "waving": {"toy_box": [45, 65, 95, 115], "behind": false, "source": "openpeeps-wave"},
  "running": {"toy_box": [40, 70, 90, 120], "behind": false, "source": "openpeeps-run"},
  "sleeping": {"toy_box": [40, 80, 90, 130], "behind": false, "source": "operator-drawn"},
  "confused": {"toy_box": [50, 60, 100, 110], "behind": true, "source": "operator-drawn"}
}
```

`toy_box: [x0, y0, x1, y1]` in template-pixel coordinates (range 0..255 for 256×256 templates; the example values keep the toy in the upper-left to mid quadrants for visual contrast against template props that occupy the lower-right). `behind: true` composites the toy UNDER the template (e.g. behind a pointing-arrow shape so the arrow is on top); `behind: false` composites OVER (toy on top of a stage / floor). `source` is informational, useful when iterating template quality.

### Old → new env var migration

| Old (F2) | New (F.5) | Note |
|---|---|---|
| `TOYBOX_IMAGE_GEN_PALETTE_COLORS=32` | (deprecated) | Quantize step removed |
| `TOYBOX_IMAGE_GEN_OUTPUT_DIM=128` | unchanged | Output still 128×128 |
| `TOYBOX_IMAGE_GEN_TIMEOUT_SEC=300` | unchanged | Per-call timeout (now generously over-provisioned given ~2 s/sprite) |
| `TOYBOX_IMAGE_GEN_MIN_VRAM_GB=6` | unchanged | New pipeline fits comfortably; floor stays at 6 |
| `TOYBOX_IMAGE_GEN_BREAKER_THRESHOLD=5` | unchanged | Breaker config unchanged |
| `TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC=300` | unchanged | Cooldown unchanged |
| (none) | `TOYBOX_IMAGE_GEN_BASE_MODEL_PATH=data/models/image_gen/sd15/base` | New: SD 1.5 base path |
| (none) | `TOYBOX_IMAGE_GEN_CARTOON_MODE=checkpoint` | New: `checkpoint` or `lora` |
| (none) | `TOYBOX_IMAGE_GEN_CARTOON_PATH=data/models/image_gen/cartoon_checkpoint` | New: path to cartoon checkpoint OR LoRA depending on mode |
| (none) | `TOYBOX_IMAGE_GEN_LCM_LORA_PATH=data/models/image_gen/sd15/lcm_lora` | New: LCM-LoRA path (always loaded) |

### File layout (post-F.5)

```
data/
├── images/
│   └── toy_actions/
│       └── <toy_id>/
│           ├── idle.png         # generated by Tier B OR Tier C (same path)
│           ├── pointing.png
│           ├── ... (10 slots)
├── sprites/
│   └── templates/
│       ├── manifest.json        # toy_box + behind + source per slot (F.5-3b)
│       ├── CREDITS.md           # source + license per template (F.5-3b)
│       ├── idle.png             # 256×256 RGBA (F.5-3b)
│       ├── pointing.png
│       ├── ... (10 slots)
└── models/
    └── image_gen/
        ├── sd15/
        │   ├── base/                                # SD 1.5 base (F.5-1)
        │   │   ├── model_index.json
        │   │   └── unet/diffusion_pytorch_model.fp16.safetensors
        │   └── lcm_lora/                            # LCM-LoRA SD 1.5 (F.5-1)
        │       └── pytorch_lora_weights.safetensors
        ├── cartoon_checkpoint/                      # full cartoon SD 1.5 fine-tune (F.5-1)
        │   ├── model_index.json
        │   └── unet/diffusion_pytorch_model.fp16.safetensors
        ├── cartoon_lora/                            # cartoon LoRA add-on (F.5-1)
        │   └── pytorch_lora_weights.safetensors
        ├── bg_remove/
        │   └── u2net.onnx                           # unchanged from F1
        ├── manifest.json                            # SHA256s for all of the above
        ├── .probe-pass-<iso>.json                   # written by --probe
        ├── sdxl/                                    # OBSOLETE — kept on disk through F.5-5 for rollback;
        ├── ip_adapter/                              # operator deletes after F.5-5 passes
        └── pixel_art_lora/
```

`data/sprites/` is a new peer of `data/images/` and `data/models/`. The Tier C `composite.py` reads from `data/sprites/templates/`; the Tier B `pipeline.py` writes to `data/images/toy_actions/<toy_id>/<slot>.png`. Both write the same output path so the parent UI's static-mount URL pattern is unchanged.

### `toy_actions.error_msg` value shapes (post-F.5)

The `error_msg` column is free-text but its values follow a documented vocabulary so consumers (parent UI tooltip, metrics dashboard) can group sensibly without parsing:

| Value | Set by | Meaning |
|---|---|---|
| `NULL` | worker | Success path (status=`done`); no error |
| `"image_gen_disabled"` | worker | `CapabilityReason.env_disabled` — operator explicitly turned off via `TOYBOX_IMAGE_GEN_ENABLED=false`; no Tier C either |
| `"image_gen_composite_only"` | worker | Tier C composite was attempted (capability False due to GPU/VRAM/checkpoints) AND failed |
| `"composite template missing for slot=<slot>"` | composite.py | Tier C tried but template PNG or manifest entry missing for that slot |
| `"timeout"` | worker | `asyncio.wait_for` fired during pipeline.generate_action |
| `"image-gen breaker open"` | worker | breaker tripped on a prior failure; this enqueue rejected immediately |
| `"interrupted by restart"` | worker startup sweep | Row was `running` at boot; sweep marked it `failed` (recoverable via regenerate) |
| `"<200-char excerpt of exception>"` | worker | Catchall for unexpected exceptions in pipeline or composite |

If any consumer adds new branches to its `error_msg` switch (e.g. metrics dashboard groups by reason), update this table at the same time.
