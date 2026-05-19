# Phase P — Toy image generation quality redo (feature plan)

## 1. What this feature does

Phase P re-introduces **reference-image conditioning** to the toy action sprite pipeline via IP-Adapter (Plus) for SD 1.5, so that generated sprites visually resemble the parent-uploaded toy photo while still preserving action-pose intent from `ACTION_PROMPTS`. It also raises sprite output resolution from 128×128 to 512×512 (matching element sprites), extends the negative prompt with text-glyph suppressors, and ships a parent-facing **"Regenerate all"** button so operators can upgrade the existing toy sprite catalog in one explicit action.

**Why now.** Phase M2b shipped element sprites (Professor Iridia + Pillow text overlay) that visibly outperform toy sprites despite using the same SD 1.5 + LCM-LoRA backbone. The motivating gap analysis ([§ "Why elements look better" below](#why-elements-look-better-this-phases-motivating-gap)) confirmed the difference is conditioning-quality, not model choice: element sprites anchor on a stable named cartoon archetype, toy sprites anchor on `display_name` + 3 hex-color palette tokens. The hex-token kludge ([pipeline.py:175-208](../src/toybox/image_gen/pipeline.py#L175-L208)) is the weakest link; IP-Adapter replaces it with proper image-pathway conditioning.

**Why this and not custom per-toy training.** A DreamBooth-lite or textual-inversion per toy was considered and rejected during Phase F.5 (training-time VRAM exceeds 8 GB; ingest-time wait would be minutes per toy). IP-Adapter is zero-training, runs at the same 4-step LCM inference budget, and works on top of the existing cartoon checkpoint + LCM-LoRA stack.

**Operator constraint relaxed.** Phase F.5 explicitly relaxed the subject-identity requirement based on operator framing at the time: *"more important that the toy be doing the action than be detailed."* That framing has been reversed by the operator after seeing Phase M2b element sprite quality: *"the image generation we use for the elements has much better results than the way we do toys"* and *"however we can preserve [action] intent and still generate decent images that look like the original character from the toy picture, let's do it."* Phase P implements that reversal.

## 2. Existing context

### Fresh-reader pointers

For first-run setup, backend/frontend commands, and project conventions: see [`toybox/CLAUDE.md`](../CLAUDE.md).

Image-gen runbook (per-component download scripts, env vars, troubleshooting): [`documentation/operator/image-gen-runtime.md`](operator/image-gen-runtime.md).

Phase F.5 archive — the predecessor migration that retired the original SDXL + IP-Adapter pipeline because of `c10.dll` access violation crashes: [`documentation/plan/archive/phase-f-5-sprite-cartoon-redo.md`](plan/archive/phase-f-5-sprite-cartoon-redo.md). The crash investigation that produced the F.5 design: [`documentation/runs/2026-05-08-sprite-pipeline-alternatives.md`](runs/2026-05-08-sprite-pipeline-alternatives.md). The F9 crash report itself: [`documentation/runs/2026-05-07-toy-action-sprites-smoke.md`](runs/2026-05-07-toy-action-sprites-smoke.md).

### Acronyms used in this plan

| Term | Meaning |
|---|---|
| **SD 1.5** | Stable Diffusion v1.5. ~3 GB fp16 UNet. The image-gen backbone shipped in Phase F.5. |
| **SDXL** | Stable Diffusion XL. ~6 GB fp16 UNet alone. The PREVIOUS backbone, retired by F.5 because it didn't fit in 8 GB without `enable_model_cpu_offload()`, and that path was the prime suspect for the `c10.dll` crash. |
| **LCM** | Latent Consistency Model. A scheduler that runs SD inference in ~4 steps instead of ~25. Loaded as a LoRA adapter. |
| **LoRA** | Low-Rank Adaptation. Small adapter weights that fine-tune a model without re-shipping the whole checkpoint. |
| **IP-Adapter** | Image-conditioning adapter. Augments SD's text-only prompt with a parallel image-pathway: a CLIP image encoder produces embeddings that condition the UNet's cross-attention. This is the load-bearing addition of Phase P. |
| **IP-Adapter Plus** | A specific variant (`ip-adapter-plus_sd15.bin`) with stronger identity transfer than the standard IPA. Plus is what Phase P ships. |
| **rembg** | Background-removal library; uses the u2net ONNX model on CPU. Already in the stack — used for the existing pipeline's subject-isolation step and Tier C composite. |
| **Tier B** | The diffusion-based pipeline ([pipeline.py](../src/toybox/image_gen/pipeline.py)). What Phase P modifies. |
| **Tier C** | The composite fallback ([composite.py](../src/toybox/image_gen/composite.py)) — rembg + Pillow paste onto hand-drawn templates. Pure CPU. **Phase P does NOT touch Tier C.** |
| **ACTION_SLOTS** | 10 canonical action keys (`idle`, `pointing`, `looking`, `jumping`, `cheering`, `thinking`, `waving`, `running`, `sleeping`, `confused`) defined in [models.py:37-48](../src/toybox/image_gen/models.py#L37-L48). |
| **ACTION_PROMPTS** | Per-slot pose-detail prompt fragments in [models.py:53-68](../src/toybox/image_gen/models.py#L53-L68) (e.g. `"mid-jump in the air with both feet off the ground, arms raised"`). **Phase P preserves these unchanged** — they are the action-intent signal. |
| **Capability gate** | The 4-branch check in [`capability.py`](../src/toybox/image_gen/capability.py) that gates Tier B based on env-disabled / CUDA / VRAM / checkpoint-present. Returns `(capable: bool, reason: CapabilityReason, detail: str)`. |
| **`CapabilityReason`** | StrEnum with five values: `capable` (gate open, Tier B runs), `env_disabled` (hard-off, no Tier C either), `no_cuda` (Tier C fallback), `low_vram` (Tier C fallback), `missing_checkpoints` (Tier C fallback). |
| **Per-pipeline breaker** | The `ImageGenBreaker` ([`capability.py`](../src/toybox/image_gen/capability.py)) that opens after 3 failures in 60s (env-tunable). Independent from Claude/LLM breakers. |
| **`GenerationContext`** | Frozen dataclass passed to `generate_action`. Fields: `toy_display_name: str`, `persona_display_name: str \| None`, `tags: tuple[str, ...]`. Worker constructs it from the `toys` row + optional joined `personas` row. P4's new `_build_prompt` reads `toy_display_name`, `persona_display_name`, `tags` — `palette_hex` parameter is removed. |
| **`toy_id`** | UUIDv4 string. Validated via `_validate_toy_id` ([toys.py](../src/toybox/api/toys.py)) at every API and worker entry point. |
| **`Topic.toy_actions`** | WebSocket topic emitted by the image-gen worker after every status transition. Payload shape: `{toy_id: str, slot: str, status: str, image_path: str \| None, error: str \| None}`. `image_path` non-null only when `status == "done"`; `error` non-null only when `status == "failed"`. |
| **Dreamshaper-7** | The default cartoon checkpoint shipped in F.5 — a fine-tuned SD 1.5 (`Lykon/dreamshaper-7`, ~3-4 GB fp16). Lives at `data/models/image_gen/cartoon_checkpoint/`. Phase P loads IP-Adapter on top of this. |
| **PIN-gated UI** | Parent app surfaces require parent PIN. Means `/build-step --reviewers full` runtime evidence capture can't reach UI for verification; use `--reviewers code` and an iPad UAT step instead (workspace memory: `feedback_buildstep_pin_gate_blocks_ui_evidence`). |
| **`/build-step` / `/build-phase`** | Workspace orchestration skills. `/build-phase` walks the steps in this plan and dispatches each to `/build-step` (or `/build-step-tdd` when `--tdd` is set). `Type: operator` steps halt orchestration so the operator does the manual work; `Type: code` steps spawn `/build-step` which produces a code diff. SKILL.md files live at `dev/.claude/skills/`. |
| **`--reviewers code`** | Flag to `/build-step` selecting 4-parallel-reviewer mode (correctness / bugs / test quality / style). Alternatives: `auto` (just tests), `runtime` (3 evidence-based reviewers requiring `--start-cmd` + `--url`), `full` (all 7). Phase P uses `code` everywhere because toybox's PIN-gated parent UI blocks `runtime`/`full` reviewers from doing UI evidence capture. |

### Current toy image-gen pipeline ([pipeline.py](../src/toybox/image_gen/pipeline.py))

Signature: `generate_action(reference_bytes: bytes, slot: str, seed: int, ctx: GenerationContext) -> bytes`. Async wrapper around `_run_pipeline_sync`. Heavy imports (`torch`, `diffusers`, `rembg`, `PIL`) live inside the sync body so module import stays cheap when the feature is disabled.

Current pipeline steps in `_run_pipeline_sync` ([pipeline.py:317-400](../src/toybox/image_gen/pipeline.py#L317-L400)):

1. rembg u2net cutout of `reference_bytes` (CPU).
2. Pillow `Image.quantize(colors=8).getpalette()` → top-3 dominant hex colors of the cutout.
3. Construct (or reuse) the cached SD 1.5 + LCM-LoRA + cartoon-checkpoint pipeline via `_build_pipeline()`.
4. `_build_prompt(slot, ctx, palette_hex)` assembles a string of the shape:
   `"<persona> the <toy_display_name>, <tags>, <palette tokens>, <ACTION_PROMPTS[slot]>, 2D cartoon, simple shapes, clean lines, transparent background"`
   where `<palette tokens>` is `", ".join(f"primary color {h}" for h in palette_hex[:3])`.
5. `pipe(prompt=..., negative_prompt=DEFAULT_NEGATIVE_PROMPT, num_inference_steps=4, guidance_scale=1.0, height=512, width=512)` — generation runs at 512² internally.
6. Second rembg pass on the generated image to clean up the residual non-transparent background.
7. Resize to `TOYBOX_IMAGE_GEN_OUTPUT_DIM` (default 128) using `Image.Resampling.BILINEAR`.
8. PNG-encode and return bytes.

**Key fact:** the reference photo never reaches the diffuser. It is read only to (a) derive the top-3 hex colors injected as prompt tokens and (b) feed the second-pass rembg cleanup. Identity must be reconstructed from `toy_display_name` + `tags` + 3 hex colors.

### Current element sprite pipeline ([generate_element_sprites.py](../scripts/generate_element_sprites.py))

Operator-run CLI; not part of the service. Same SD 1.5 + LCM-LoRA + cartoon-checkpoint backbone, same 4-step LCM, same 512² generation. Differences:

1. **No reference photo.** Pure text-to-image.
2. **Stable anchored archetype.** Every prompt anchors on `"Professor Iridia, a friendly cartoon scientist with curly hair and round glasses, smiling warmly, holding up a glowing orb of {name} in {color_description}"`. Only the orb-contents string varies across all 118 elements.
3. **Extended negative prompt** ([generate_element_sprites.py:105-108](../scripts/generate_element_sprites.py#L105-L108)) adds `"text, letters, numbers, writing, symbols, watermark"` on top of the toy pipeline's `DEFAULT_NEGATIVE_PROMPT`. Suppresses SD's tendency to scatter pseudo-glyphs.
4. **Pillow post-render text overlay** ([generate_element_sprites.py:206-290](../scripts/generate_element_sprites.py#L206-L290)) composites the periodic-table-cell text on a rounded white panel. Sidesteps SD's known weakness at legible glyphs at 4 steps. **Phase P does NOT introduce a post-overlay** — toy sprites don't carry on-image text.
5. **Output stays 512×512** — no downsampling.
6. **Deterministic seed.** `seed = sha256(element.id) % 2**31` so re-rolls are byte-comparable.

### Why elements look better (this phase's motivating gap)

Same backbone, dramatically different conditioning. Summary of the asymmetry:

| | Elements (M2b) | Toys (current) |
|---|---|---|
| Subject identity source | rich text prompt anchored to Iridia | `display_name` + 3 hex colors |
| Reference photo → diffuser | n/a (no photo) | none (rembg → palette tokens only) |
| Negative prompt | extended with text suppressors | bare default |
| Output dim on disk | 512 | 128 (downsampled with BILINEAR) |
| Glyphs on output | Pillow overlay, not SD | n/a (no on-image text) |
| Per-id seed determinism | `sha256(id) % 2**31` | random per generation, stored in DB |

The two highest-leverage gaps for closing this delta:

1. **Identity conditioning.** Elements get a stable, well-described archetype (Iridia). Toys can't have a "stable archetype" because each toy is unique; the analogue is to condition on the toy's reference image directly. That's what IP-Adapter does.
2. **Output resolution.** Detail that survives 512×512 collapses at 128×128. Element sprites stay 512; toys lose most of what IP-Adapter would bring if we keep downsampling.

### What IP-Adapter does (and the SD 1.5 vs SDXL distinction)

**Mechanism.** IP-Adapter adds a parallel image-conditioning pathway to SD. The reference image flows through a CLIP image encoder (ViT-L for SD 1.5; ViT-H for SDXL), producing embeddings that condition the UNet's cross-attention alongside the text prompt embeddings. Generation runs at the same step count (4 LCM steps). One scale knob — `pipe.set_ip_adapter_scale(s)` — controls how strongly the image conditioning competes with the text prompt. Higher values pull harder toward the reference photo; lower values let the text prompt (including pose detail) dominate.

**Why not just use img2img.** img2img uses the reference image as the latent INIT and lets the diffuser denoise from there. That's fine when the target image has the same pose/composition as the input — but Phase P's whole point is changing the pose (the toy "jumping" vs "sleeping"). At high img2img strength, the original pose stays; at low strength, identity collapses. There's no sweet spot for "preserve identity, change pose." IP-Adapter sidesteps this: it conditions on the image's APPEARANCE without anchoring to its pose, because the image encoder produces semantic embeddings, not pixel structure.

**SD 1.5 vs SDXL IP-Adapter — the critical distinction.** The original Phase F pipeline that crashed used **SDXL** + **IP-Adapter SDXL** + **`enable_model_cpu_offload()`** at **1024²** + 25 steps. The c10.dll access-violation crashes were attributed by the F.5 investigation primarily to `enable_model_cpu_offload()` — the offload path moves parameters between GPU and CPU per-forward, and the synchronization with LoRA + IPA layers is fragile (the corroborating evidence was a `Expected all tensors to be on cuda:0 and cpu` error on a sister toy). The F.5 cutover eliminated `model_cpu_offload` along with SDXL.

**Phase P's IP-Adapter configuration is structurally different:**
- **SD 1.5 base** (not SDXL) — UNet ~3 GB instead of ~6 GB.
- **CLIP ViT-L image encoder** (not ViT-H) — ~700 MB instead of ~2.5 GB.
- **NO `enable_model_cpu_offload()`** — the SD 1.5 stack fits comfortably in 8 GB without offload. F.5 deliberately uses just `pipe.to("cuda")`.
- **4 steps, 512²** — not 25 steps, 1024².

Estimated peak VRAM: ~5-6 GB (vs F.5's current ~4-5 GB). Headroom on 8 GB hardware remains comfortable. **The crash class that drove F.5 is structurally not in the Phase P configuration.** This is documented in the Risks section so reviewers don't have to re-derive it.

### Hardware envelope

RTX 4070 Laptop, 8 GB VRAM, Windows 11, torch 2.6.0+cu124. F.5's measured steady-state: ~4-5 GB peak, ~2 s/sprite warm, no offload. Phase P adds ~700 MB for the image encoder + ~100 MB for the IPA weights themselves + activations. Expected steady-state: ~5-6 GB peak, ~2.5-3 s/sprite warm. 10 sprites per toy → ~25-30 s wall-clock total. Per-call timeout cap stays at 120s (env-overridable via `TOYBOX_IMAGE_GEN_TIMEOUT_SEC`).

### Current frontend rendering ([ToyActionSprite.tsx](../frontend/src/child/components/ToyActionSprite.tsx))

`<img src="/api/static/images/toy_actions/<toy_id>/<slot>.png" width={size} height={size}>`. The component is dimension-agnostic — `width`/`height` are the display size, the source PNG can be any dimension. **One critical CSS rule that Phase P changes:** `imageRendering: "pixelated"` ([ToyActionSprite.tsx:67](../frontend/src/child/components/ToyActionSprite.tsx#L67)). This was correct for upscaling 128 → 112 because the source was already low-fi; for a 512 source resampled down to 112, smooth resampling (the browser default) is correct.

## 3. Scope summary

**In scope:**

- Add IP-Adapter Plus SD 1.5 to the existing diffusion pipeline.
- Replace the rembg → 3-hex-color palette-token prompt construction with IP-Adapter image conditioning. The cutout still feeds IPA; the hex-token string goes away.
- Extend `DEFAULT_NEGATIVE_PROMPT` with text/glyph suppressors (port the verbatim string from `generate_element_sprites.py`).
- Change `DEFAULT_OUTPUT_DIM` from 128 to 512; drop the final downsampling step in the pipeline.
- Update the capability gate's `_required_checkpoints()` to validate IP-Adapter weights are present (mode-aware: required when Tier B is reachable).
- Add a `scripts/f5_download_ip_adapter.py` script following the F.5 download-script pattern.
- Update the operator runbook ([image-gen-runtime.md](operator/image-gen-runtime.md)) — new checkpoint family row, new troubleshooting entries, removal of the now-incorrect "Sprite quality is poor / doesn't resemble the toy" relaxation note.
- Frontend: drop `imageRendering: "pixelated"` on [ToyActionSprite.tsx](../frontend/src/child/components/ToyActionSprite.tsx). Verify [ToyActionGrid.tsx](../frontend/src/parent/components/ToyActionGrid.tsx) (parent) and any other consumers render cleanly at 512-source.
- New backend endpoint `POST /api/admin/regenerate-every-toy-action` + new parent UI button labeled **"Regenerate sprites for every toy"** that triggers re-enqueue of every `(toy_id, slot)` across every non-archived toy. **Distinct from the existing per-toy `POST /api/toys/{toy_id}/actions/regenerate`** ([toys.py:1287](../src/toybox/api/toys.py#L1287)) which only enqueues 10 slots for one toy. Confirmation dialog. Progress visible via the existing per-slot WS-driven status badges on each toy's grid.

**Out of scope (kept as deferred / escape hatches):**

- **ControlNet-OpenPose** for explicit pose enforcement. If IPA scale-tuning at P7 can't find a value that preserves both identity and pose, ControlNet-OpenPose is the documented escape hatch but lives in a follow-up phase (Phase Q candidate), not Phase P.
- **Per-toy custom training** (DreamBooth-lite / textual inversion). Rejected during F.5 for the same reasons; not reopened.
- **Online image-gen fallback** when the local GPU is unavailable. Hosts without a GPU continue to degrade to Tier C composite. Phase P does not change the dispatch logic.
- **Animated sprites** (multi-frame action sequences). Out of scope; sprites remain static PNGs.
- **Per-slot scale-knob tuning** (different scale per action). Phase P uses one scale constant for all 10 slots. If UAT shows certain slots need different values, follow-up phase.
- **Tier C template upgrades.** Composite fallback art is unchanged.
- **Element sprite changes.** Element pipeline already meets the visual bar Phase P is targeting; do not touch.

## 4. Impact analysis

| File / module | Nature of change | Notes |
|---|---|---|
| [`src/toybox/image_gen/pipeline.py`](../src/toybox/image_gen/pipeline.py) | **Modify** — heaviest change in the phase | Add IP-Adapter load to `_build_pipeline`. Replace `_build_prompt` with a version that drops palette-hex tokens. Pass `ip_adapter_image=cutout` and `set_ip_adapter_scale` in the generate call. Extend `DEFAULT_NEGATIVE_PROMPT`. Change `DEFAULT_OUTPUT_DIM` 128 → 512. Drop the post-rembg-cleanup resize. Preserve lazy-import contract (`test_lazy_imports` pins it). |
| [`src/toybox/image_gen/capability.py`](../src/toybox/image_gen/capability.py) | **Modify** — `_required_checkpoints()` extension | Add IP-Adapter Plus checkpoint paths to the mode-aware required-set. Both `checkpoint` and `lora` cartoon modes require IPA + image encoder. |
| [`scripts/f5_compute_manifest.py`](../scripts/f5_compute_manifest.py) | **Modify** | Pick up new IP-Adapter file(s) for sha256+size recording. |
| [`documentation/operator/image-gen-runtime.md`](operator/image-gen-runtime.md) | **Modify** | New checkpoint family table row; new download-script call; new env-var entries; new troubleshooting entries. Remove the "subject identity drift is acceptable" relaxation paragraph (P5's prompt assumption reversed). Update "Custom LoRA training per toy" deferred note. |
| [`frontend/src/child/components/ToyActionSprite.tsx`](../frontend/src/child/components/ToyActionSprite.tsx) | **Modify** | Drop `imageRendering: "pixelated"`. Default `size` unchanged. |
| [`frontend/src/child/components/ToyActionSprite.test.tsx`](../frontend/src/child/components/ToyActionSprite.test.tsx) | **Modify** | Update snapshot / style-assertion if it checks for `pixelated`. |
| [`frontend/src/parent/components/ToyActionGrid.tsx`](../frontend/src/parent/components/ToyActionGrid.tsx) | **Audit only** | Re-verify it renders cleanly with 512-source PNGs. **Do NOT add the global regenerate button here** — that stays at the toys-list level. The existing `onRegenerateAll` prop is per-toy and remains unchanged. |
| [`frontend/src/parent/components/ToyIngest.tsx`](../frontend/src/parent/components/ToyIngest.tsx) | **Modify** | Add new GLOBAL "Regenerate sprites for every toy" button + confirmation dialog + composite-only banner. Pinned host: this is the same component that already wires `handleRegenerateAll(toyId)` per-toy at lines 905 and 1352. |
| [`frontend/src/parent/components/ToyActionGrid.test.tsx`](../frontend/src/parent/components/ToyActionGrid.test.tsx) | **No change** | Existing per-toy `onRegenerateAll` tests stay valid. |
| Vitest test for the new global button | **Create** | Coverage for the new global button, dialog flow, and composite-only banner. Test file likely sits next to the parent toys-list component identified above. |
| [`frontend/src/parent/api.ts`](../frontend/src/parent/api.ts) | **Modify** | New API call for `POST /api/admin/regenerate-every-toy-action`. |
| [`src/toybox/api/toys.py`](../src/toybox/api/toys.py) | **Modify** | Add `POST /api/admin/regenerate-every-toy-action` endpoint (distinct from existing per-toy `POST /api/toys/{toy_id}/actions/regenerate` at [toys.py:1287](../src/toybox/api/toys.py#L1287)). Add new `BulkRegenerateResponse` Pydantic model alongside existing `RegenerateResponse`. Iterates non-archived toys + 10 slots, enqueues via existing `get_image_gen_worker()`. Auth: `RequireScope({TokenScope.parent})` — same pattern as `regenerate_all_actions`. |
| [`tests/integration/test_toys_api_actions.py`](../tests/integration/test_toys_api_actions.py) | **Modify** | Coverage for the new global endpoint, mirroring the existing `test_regenerate_all_*` pattern at lines 351-432: add `test_regenerate_every_toy_enqueues_all`, `test_regenerate_every_toy_409_when_disabled`, `test_regenerate_every_toy_200_with_composite_only_mode`, `test_regenerate_every_toy_503_when_worker_not_running`. |
| [`tests/unit/image_gen/test_models.py`](../tests/unit/image_gen/test_models.py) | **Audit** | If it asserts a specific `ACTION_PROMPTS` string, no change. If it asserts pipeline output dim 128, update to 512. |
| [`tests/unit/image_gen/test_pipeline_stub.py`](../tests/unit/image_gen/test_pipeline_stub.py) | **Modify** | Delete `_extract_palette_hex` import at line 38 and the two dedicated tests at lines 207-231 (`test_extract_palette_hex_returns_hex_strings`, `test_extract_palette_hex_caps_at_n_entries`). Audit for any output-dim assertion that needs 128 → 512 update. |
| [`tests/fixtures/image_gen/stub_pipeline.py`](../tests/fixtures/image_gen/stub_pipeline.py) | **Audit + possibly modify** | If stub emits 16×16 PNG, leave as-is (orchestration test, not pixel-comparison). If anything asserts the stub's output dim matches `DEFAULT_OUTPUT_DIM`, update. |
| [`tests/integration/test_image_gen_worker_e2e.py`](../tests/integration/test_image_gen_worker_e2e.py) | **Modify (P4 done-when)** | The "new components require an integration test through the production caller" rule applies — extend to exercise IP-Adapter loading. |
| [`tests/integration/test_image_gen_real_gpu.py`](../tests/integration/test_image_gen_real_gpu.py) | **Modify** | Operator-only test that exercises real GPU; extend to assert IPA is loaded. |
| [`tests/unit/image_gen/test_capability.py`](../tests/unit/image_gen/test_capability.py) | **Modify** | New required-checkpoints assertions for IP-Adapter files. |
| `scripts/f5_download_ip_adapter.py` | **Create (P1)** | Mirrors existing `f5_download_sd15.py` / `f5_download_lcm.py` shape. |
| [`.gitignore`](../.gitignore) | **Audit** | Verify `data/models/image_gen/ip_adapter/` is covered by the existing `data/` gitignore (it should be — `data/` is gitignored entirely). |
| [`src/toybox/image_gen/composite.py`](../src/toybox/image_gen/composite.py) | **No change** | Tier C fallback continues to use the bg-removed photo composited onto hand-drawn templates. Doesn't load diffusion or IPA. |
| [`src/toybox/image_gen/worker.py`](../src/toybox/image_gen/worker.py) | **No change** | Public seam (`generate_action`) signature unchanged. Worker enqueue / dispatch / breaker logic untouched. |

### Downstream-consumer grep checklist for output-dim change (per workspace `code-quality.md` "grep all downstream consumers when changing a key/id/shape")

When P4 changes `DEFAULT_OUTPUT_DIM` 128 → 512 and drops the resize:

1. `grep -rn "OUTPUT_DIM\|128.*128\|width.*128.*height.*128" src/ tests/ frontend/src/` — every consumer of the old dimension.
2. Verify [ToyActionSprite.tsx](../frontend/src/child/components/ToyActionSprite.tsx) `width={size}/height={size}` — display-side agnostic to source dim (confirmed by reading the file).
3. Verify [ToyActionGrid.tsx](../frontend/src/parent/components/ToyActionGrid.tsx) — likely passes a smaller `size` (~64-80) for grid cells; same display-agnostic pattern.
4. Verify `OUTPUT_DIM` constant in `composite.py:79` — that's Tier C's separate output dim (128), unrelated to Tier B; do NOT change.
5. Check `tests/fixtures/image_gen/stub_pipeline.py` — if the stub emits 16×16 PNG, no consumer asserts a specific size, leave alone.
6. Any test that PNG-decodes an actual sprite and checks dimensions → update.

Report results inline in P4's done-when as a table (one row per call site, verdict: OK / needs fix / already handled).

## 5. New components

- **`scripts/f5_download_ip_adapter.py`** — operator-run download script. Uses `huggingface_hub.snapshot_download` to fetch `h94/IP-Adapter` filtered to `ip-adapter-plus_sd15.bin` plus the CLIP ViT-L image encoder. Writes to `data/models/image_gen/ip_adapter/ip-adapter-plus_sd15.bin` and `data/models/image_gen/ip_adapter/image_encoder/`. Mirrors the existing F.5 download scripts in error handling, path layout, and `--help` behavior.
- **`POST /api/admin/regenerate-every-toy-action` endpoint** — NEW global endpoint, distinct from the existing per-toy `POST /api/toys/{toy_id}/actions/regenerate` ([toys.py:1287](../src/toybox/api/toys.py#L1287)). Parent-PIN-gated. Iterates `SELECT id FROM toys WHERE archived = 0` and for each toy × each `ACTION_SLOTS` member, calls the existing `get_image_gen_worker().enqueue(toy_id, slot)`. Reuses the existing worker's enqueue-time supersede semantics. Returns a new `BulkRegenerateResponse(toy_count: int, total_enqueued: int, mode: str | None)`. The `mode` field is `None` when capability is `capable`, `"composite_only"` when capability is gated for a non-env-disabled reason (no-CUDA / low-VRAM / missing-checkpoints) so the parent UI can render a banner. `env_disabled` capability returns 409 instead. No new schema or storage layer.
- **"Regenerate sprites for every toy" button** — NEW global button at the top of [`frontend/src/parent/components/ToyIngest.tsx`](../frontend/src/parent/components/ToyIngest.tsx) (the same component that already wires up `handleRegenerateAll(toyId)` per-toy at [ToyIngest.tsx:905](../frontend/src/parent/components/ToyIngest.tsx#L905) and [:1352](../frontend/src/parent/components/ToyIngest.tsx#L1352)). The new button lives OUTSIDE `ToyActionGrid.tsx`, which stays scoped to per-toy regenerate via the existing `onRegenerateAll` prop at [ToyActionGrid.tsx:47](../frontend/src/parent/components/ToyActionGrid.tsx#L47). Confirmation dialog. On confirm, POSTs to the new endpoint, shows `total_enqueued`, surfaces composite-only banner when `mode == "composite_only"`. Per-toy progress visible via the existing per-slot WS `Topic.toy_actions` envelope stream.

No new entities, no schema changes, no migrations.

## 6. Design decisions

**Decision: IP-Adapter Plus, not the standard variant.**
The standard `ip-adapter_sd15.bin` produces lighter identity transfer and follows the text prompt more aggressively. The Plus variant (`ip-adapter-plus_sd15.bin`) is specifically the identity-stronger variant. Phase P's gap is identity, not prompt-following, so Plus is the correct default. Plus also requires the same CLIP ViT-L image encoder as standard, so the encoder-weight cost is paid either way; we are not paying anything extra for choosing Plus. Standard-variant escape hatch is rejected — adds env-knob complexity for a path nobody is asking for.

**Decision: scale knob is a constant in pipeline.py, tuned during UAT.**
The scale parameter (`pipe.set_ip_adapter_scale(s)` where `s ∈ [0.0, 1.0]`) is the only parameter likely to need adjustment during the rollout. Public benchmarks suggest 0.5-0.7 is the typical sweet spot for SD 1.5 + IPA Plus. Rather than expose this as an env var or settings-table value (both of which require operator-facing documentation and surface area), Phase P bakes a single constant in [pipeline.py](../src/toybox/image_gen/pipeline.py) (initial value 0.6) and treats P7 as the UAT-tune step. Future re-tuning is a one-line code edit + re-deploy. If real-world experience shows different operators want different values, a follow-up phase can promote it to a settings entry; do not pre-build.

**Decision: output 512×512 (match element sprites), drop `imageRendering: pixelated`.**
Detail that survives 512² collapses at 128². Element sprites at 512 are visibly higher-fidelity than toy sprites at 128 even when both use the same underlying generation. Disk + bandwidth differences on local LAN are negligible (≈80-150 KB vs ≈20 KB per PNG; 10 toys × 10 slots = ~5-10 MB total). The frontend's `imageRendering: pixelated` exists to crisp-up pixel-art-style upscaling of low-res sources; for a 512-source resampled down to 112 display, smooth resampling (browser default) is correct.

**Decision: GLOBAL re-render is operator-explicit, not lazy-on-click or forced-on-boot.**
Lazy regen (only regenerate the next time a parent clicks the per-slot regenerate button) is the lowest-blast-radius option, but it leaves the operator unaware that the new pipeline is live; existing sprites stay forever until clicked. Forced-on-boot regen would surprise the operator with a 30+ minute compute spike during a backend restart and risks looking broken during the window. An explicit "Regenerate sprites for every toy" button is the right middle: operator-aware, paced (uses the existing single-worker FIFO so it doesn't spike VRAM), and reversible (operator can stop the backend if needed; restart-recovery sweep cleans up queued/running rows). Builds on the existing per-slot + per-toy regenerate UX patterns.

**Decision: no pre-implementation probe step.**
Phase F.5 ran a 30-minute local feasibility probe before committing to the cutover. Phase P skips that step on the user's call: the SD 1.5 + IPA Plus configuration is well-documented in public benchmarks, the VRAM math is comfortable (~5-6 GB peak vs 8 GB available), and the load-bearing risk is not capacity (P2's regression smoke catches that) but visual quality (which only an operator UAT can judge anyway). P7 IS the empirical gate; P2's regression smoke catches any crash regression on the existing pipeline before P3 touches anything.

**Decision: extend negative prompt verbatim from element sprites.**
The element-sprite negative prompt adds `"text, letters, numbers, writing, symbols, watermark"` to suppress SD's tendency to render pseudo-glyphs on clothing, backgrounds, and props. Toy sprites don't carry on-image text, but the suppression terms are universally applicable — pseudo-glyphs in toy backgrounds are visual noise. Port the string verbatim (no per-toy customization) to keep the negative-prompt source-of-truth visible across both pipelines.

**Decision: bundle frontend changes + global regenerate into Phase P, not split into Phase Q.**
The pipeline change alone produces no user-visible difference until parents trigger a regenerate (the existing 128 PNGs continue to render until then). Shipping the new global regenerate button in the same phase means one iPad UAT covers the whole rollout, the operator can validate end-to-end on their actual toys, and the user-facing value lands the same release. The phase is 9 steps (P1–P8 with P7 split into P7+P7b) — comparable to Phase H (6 steps) and Phase L (12 steps).

## 7. Build steps

### Step P1: IP-Adapter Plus download script + manifest extension + runbook update
- **Problem:** Create `scripts/f5_download_ip_adapter.py` **mirroring [`scripts/f5_download_sd15.py`](../scripts/f5_download_sd15.py)'s shape verbatim** — single `huggingface_hub.snapshot_download` call with `allow_patterns` filtering plus two `print` statements; NO argparse, NO `--help`, NO try/except. The existing F.5 download scripts deliberately ship as 30-line one-shots; do not introduce a divergent pattern. It downloads `h94/IP-Adapter` filtered to `ip-adapter-plus_sd15.bin` plus the `models/image_encoder/` subdirectory (CLIP ViT-L). Writes to `data/models/image_gen/ip_adapter/ip-adapter-plus_sd15.bin` and `data/models/image_gen/ip_adapter/image_encoder/`. Also: extend `scripts/f5_compute_manifest.py` to pick up the new IPA file for sha256+size recording. Also: update [`documentation/operator/image-gen-runtime.md`](operator/image-gen-runtime.md) — add a new "IP-Adapter Plus" row to the Checkpoint install table, add the script invocation under "Run the per-component download scripts", and add a troubleshooting entry "IP-Adapter weights missing → run `f5_download_ip_adapter.py`". Remove the existing "Sprite quality is poor / doesn't resemble the toy" relaxation paragraph (Phase P reverses its premise). Update the deferred-future-work note at [image-gen-runtime.md:306](operator/image-gen-runtime.md) about "per-toy DreamBooth-lite" to reference that IP-Adapter is now the in-pipeline answer to the identity problem.
- **Type:** code
- **Issue:** #183
- **Flags:** `--reviewers code`
- **Produces:** `scripts/f5_download_ip_adapter.py` (new, ~30 lines matching the existing pattern), `scripts/f5_compute_manifest.py` (modified), `documentation/operator/image-gen-runtime.md` (modified).
- **Done when:** Script matches the structure of `f5_download_sd15.py` (no argparse, no try/except, no logging). Runbook diff reads cleanly. Manifest computer picks up the new file path even when it doesn't exist yet (returns "missing" rather than crashing). ruff + mypy strict clean on touched Python.
- **Depends on:** none.
- **Status:** DONE (2026-05-18)

### Step P2: Operator — download IP-Adapter weights + regression smoke on EXISTING pipeline
- **Problem:** On the F.5-capable host, run `uv run python scripts/f5_download_ip_adapter.py` and verify `data/models/image_gen/ip_adapter/ip-adapter-plus_sd15.bin` exists (~100 MB) and `data/models/image_gen/ip_adapter/image_encoder/` contains the CLIP ViT-L files (~700 MB). Then run the existing F.5 smoke probe — `uv run --extra image_gen python -m toybox.image_gen --probe <existing-toy-id> --slot idle` — and verify it still produces a non-empty 128×128 PNG at `data/images/toy_actions/<toy_id>/idle.png` with no `c10.dll` crashes. **This is the regression smoke gate before any pipeline code change touches the live system.** If the existing pipeline is broken on the host BEFORE Phase P starts, do not proceed.
- **Type:** operator
- **Issue:** #184
- **Flags:** n/a (operator step)
- **Produces:** Confirmed IPA weights on disk + green run of the existing pipeline as a baseline. Capture wall-clock + peak VRAM via `nvidia-smi` for reference.
- **Done when:** Both checkpoints present on disk, regression smoke produces a sprite without a crash, baseline numbers captured (informally — no formal artifact required).
- **Depends on:** P1.

### Step P3: Extend capability.py `_required_checkpoints()` for IP-Adapter
- **Problem:** Modify [`src/toybox/image_gen/capability.py`](../src/toybox/image_gen/capability.py) `_required_checkpoints()` to add IP-Adapter Plus to the required-checkpoints set. Both `checkpoint` and `lora` cartoon modes require IPA + image encoder (the IPA is loaded regardless of which cartoon-base is used). Files: `ip_adapter/ip-adapter-plus_sd15.bin` + the image-encoder's `model.safetensors` or `pytorch_model.bin` (verify the actual filename `huggingface_hub` writes during the P2 download and pin that exact relative path). Capability gate must return `False` with `CapabilityReason.missing_checkpoints` when either file is absent. Unit test: pin `TOYBOX_IMAGE_GEN_MODEL_DIR` to a tmp_path, assert capable=False before the IPA files exist, True after.
- **Type:** code
- **Issue:** #185
- **Flags:** `--reviewers code`
- **Produces:** `capability.py` modified, `tests/unit/image_gen/test_capability.py` extended.
- **Done when:** Tests pass. Boot-time capability log line (in [`src/toybox/app.py`](../src/toybox/app.py)) shows the new files in the missing-checkpoints detail string when they're absent. mypy + ruff clean.
- **Depends on:** P1 (path layout pinned by the download script).

### Step P4: Rewrite pipeline.py — IP-Adapter integration, drop hex-tokens, 512² output, extended negative prompt
- **Problem:** Modify [`src/toybox/image_gen/pipeline.py`](../src/toybox/image_gen/pipeline.py) end-to-end per the Design Decisions above:
  1. In `_build_pipeline`: after the existing `pipe.set_adapters([...], adapter_weights=[...])` call (both `checkpoint` and `lora` cartoon-mode branches), add `pipe.load_ip_adapter(...)` pointing at the new IPA weights + image encoder (verify the exact diffusers API call signature against the diffusers version pinned in `pyproject.toml`). Call `pipe.set_ip_adapter_scale(IP_ADAPTER_SCALE)` where `IP_ADAPTER_SCALE: Final[float] = 0.6` is a new module-level constant. Wrap in the same partial-construction try/except + `torch.cuda.empty_cache()` cleanup so a partial IPA load doesn't leak.
  2. Replace `_build_prompt` with a version that drops `palette_hex` entirely. New shape: `f"{intro}, {tags}, {ACTION_PROMPTS[slot]}, 2D cartoon, simple shapes, clean lines, transparent background"`. The `intro` and `tags` logic stays. The `palette_hex` parameter is removed from the signature. Delete `_extract_palette_hex` ([pipeline.py:211-226](../src/toybox/image_gen/pipeline.py#L211-L226)) and the call site in `_run_pipeline_sync` step 2 ([pipeline.py:364](../src/toybox/image_gen/pipeline.py#L364)). Also delete the two dedicated unit tests at [tests/unit/image_gen/test_pipeline_stub.py:207-231](../tests/unit/image_gen/test_pipeline_stub.py#L207-L231) (`test_extract_palette_hex_returns_hex_strings`, `test_extract_palette_hex_caps_at_n_entries`) and the import at [test_pipeline_stub.py:38](../tests/unit/image_gen/test_pipeline_stub.py#L38).
  3. Extend `DEFAULT_NEGATIVE_PROMPT` to append the element sprite suppressors verbatim: `", text, letters, numbers, writing, symbols, watermark"`.
  4. In `_run_pipeline_sync` step 5, pass `ip_adapter_image=cutout_image` to the `pipe(...)` call (where `cutout_image` is the rembg-cut Pillow Image from step 1, already RGBA-converted).
  5. Change `DEFAULT_OUTPUT_DIM: Final[int] = 128` → `512`.
  6. In `_run_pipeline_sync` step 7, the final resize is now `cleaned_image.resize((output_dim, output_dim), Image.Resampling.LANCZOS)` — but when `output_dim == 512` and `cleaned_image` is already 512², the resize is a no-op; keep the call for the env-override case where an operator overrides to a non-512 dim.
  7. **Preserve the lazy-import contract** — IPA / CLIP / new diffusers symbols stay inside `_run_pipeline_sync` and `_build_pipeline`. The `test_lazy_imports` test must remain green. Specifically: do not import any `IPAdapter`-named symbol at module scope.
  8. **Integration test through the production worker caller** (per workspace `code-quality.md` rule): extend [`tests/integration/test_image_gen_worker_e2e.py`](../tests/integration/test_image_gen_worker_e2e.py) so the worker's `_run_one_body` calls the real `generate_action` (via the stub-pipeline path with a stub that exercises the `ip_adapter_image` argument shape) and asserts the resulting PNG bytes round-trip through the storage layer + WS envelope emission. The unit-test-only path of `pipeline.py` doesn't catch silent-wiring failures; only the worker E2E does.
  9. **Downstream-consumer grep checklist** (per workspace `code-quality.md` rule for shape changes): produce a one-row-per-callsite table for every consumer of the old 128-dim output in the PR description, with verdicts. The known-at-plan-time consumers are:
     - `src/toybox/image_gen/pipeline.py:78` — `DEFAULT_OUTPUT_DIM = 128` → change to 512.
     - `src/toybox/image_gen/composite.py:79` — `OUTPUT_DIM = 128` → **do NOT change**, Tier C is a separate output path.
     - `frontend/src/child/components/ToyActionSprite.tsx` — display-size agnostic (`width={size} height={size}` on `<img>`) ✓.
     - `frontend/src/parent/components/ToyActionGrid.tsx:76` — passes `size={88}` to ToyActionSprite, display-side agnostic ✓.
     - `tests/fixtures/image_gen/stub_pipeline.py` — stub emits 16×16; audit for any explicit-dim assertions and update only if found.
     - `tests/unit/image_gen/test_pipeline_stub.py` — palette tests deleted per step 2 above; also audit for any output-dim assertion.
     P4 still runs the full grep at implementation time to catch anything missed; this list is a prepop, not the final answer.
  10. Update [`tests/fixtures/image_gen/stub_pipeline.py`](../tests/fixtures/image_gen/stub_pipeline.py) only if it asserts a specific output dim; otherwise leave its 16×16 placeholder alone (orchestration test).
- **Type:** code
- **Issue:** #186
- **Flags:** `--reviewers code`
- **Produces:** `pipeline.py` modified, worker E2E test extended, stub-pipeline test audited.
- **Done when:** All existing image-gen tests still green. New IPA-shape assertion in worker E2E green. mypy strict + ruff clean. Lazy-import test still green. Downstream-consumer grep checklist included in PR description with verdicts.
- **Depends on:** P3.

### Step P5: Frontend — drop `imageRendering: pixelated`; verify 512-source rendering
- **Problem:** Modify [`frontend/src/child/components/ToyActionSprite.tsx`](../frontend/src/child/components/ToyActionSprite.tsx) line 67: remove `imageRendering: "pixelated"` from `baseStyle`. The element-style behavior collapses to the browser default (smooth resampling), which is correct for 512-source downscaled to 112-display. Update [`frontend/src/child/components/ToyActionSprite.test.tsx`](../frontend/src/child/components/ToyActionSprite.test.tsx) if it asserts the `pixelated` rule. Audit [`frontend/src/parent/components/ToyActionGrid.tsx`](../frontend/src/parent/components/ToyActionGrid.tsx) — if it uses `<ToyActionSprite>` it automatically inherits the change; if it renders sprites another way, audit that path too. Vitest snapshot updates as needed.
- **Type:** code
- **Issue:** #187
- **Flags:** `--reviewers code`
- **Produces:** `ToyActionSprite.tsx` modified, related vitest snapshots updated.
- **Done when:** `npm run test` green. `npm run typecheck` + `npm run lint` clean.
- **Depends on:** P4 (so the new 512 sprites are what the modified frontend will render against; reverse order means existing 128 sprites render smoothly-blurry rather than pixelated, which is correct but transitional).

### Step P6: Global "Regenerate every toy" backend endpoint + parent UI button
- **Problem:** **Disambiguation up front:** a per-toy regenerate-all already ships at `POST /api/toys/{toy_id}/actions/regenerate` ([toys.py:1287](../src/toybox/api/toys.py#L1287)) returning `RegenerateResponse(queued=list[str], mode=...)`, and `ToyActionGrid` already exposes an `onRegenerateAll` prop ([ToyActionGrid.tsx:47](../frontend/src/parent/components/ToyActionGrid.tsx#L47)) wired up per-toy in `ToyIngest.tsx:905,1352`. **Phase P adds a separate GLOBAL endpoint + button that enqueues every (toy × slot) across the whole library**, distinct from the existing per-toy flow. Do not collide names.
  
  Backend ([`src/toybox/api/toys.py`](../src/toybox/api/toys.py)): add `POST /api/admin/regenerate-every-toy-action` with parent-PIN token scope (`RequireScope({TokenScope.parent})` — same pattern as `regenerate_all_actions` at toys.py:1291). Handler iterates `SELECT id FROM toys WHERE archived = 0`, then for each toy × each `ACTION_SLOTS` member calls `await worker.enqueue(toy_id, slot)` where `worker = get_image_gen_worker()`. Reuses the existing worker's enqueue-time supersede semantics. Define a new response model `BulkRegenerateResponse(toy_count: int, total_enqueued: int, mode: str | None)` — `mode` mirrors the existing `RegenerateResponse.mode` semantics (capability-gate composite-only signal). If `get_image_gen_worker()` returns None, return 503 with `{"code": "image_gen_worker_unavailable"}` mirroring the existing `_require_worker_or_503()` shape. Capability-disabled path returns 409 mirroring `_check_capability_or_409()`.
  
  Frontend: add a button labeled **"Regenerate sprites for every toy"** at the top of [`frontend/src/parent/components/ToyIngest.tsx`](../frontend/src/parent/components/ToyIngest.tsx) (NOT inside `ToyActionGrid` — that component stays scoped to one toy). `ToyIngest.tsx` is the pinned host: it's the same component that already wires `handleRegenerateAll(toyId)` per-toy at lines 905 and 1352, so the global button sits naturally alongside. Click → confirmation dialog ("Regenerate sprites for every toy? This will enqueue ~N toys × 10 slots and run in the background."). On confirm, POST to the new endpoint via [`frontend/src/parent/api.ts`](../frontend/src/parent/api.ts), show `total_enqueued` + composite-only banner if `mode == "composite_only"`, then the existing per-slot WS-driven status badges on each toy's grid show progress.
  
  Integration test in [`tests/integration/test_toys_api_actions.py`](../tests/integration/test_toys_api_actions.py): mirror the existing `test_regenerate_all_*` test pattern at lines 351-432 — cover `test_regenerate_every_toy_enqueues_all`, `test_regenerate_every_toy_409_when_disabled`, `test_regenerate_every_toy_200_with_composite_only_mode`, `test_regenerate_every_toy_503_when_worker_not_running`. No 404 variant (no `{toy_id}` in the URL). Vitest coverage for the new button + confirm dialog + composite-mode banner.
- **Type:** code
- **Issue:** #188
- **Flags:** `--reviewers code`
- **Produces:** `toys.py` modified (new endpoint + new `BulkRegenerateResponse` model), `api.ts` modified, parent toys-list component modified, integration tests added, vitest coverage added.
- **Done when:** Backend + frontend tests green following the existing `test_regenerate_all_*` pattern at [test_toys_api_actions.py:351-432](../tests/integration/test_toys_api_actions.py#L351-L432). mypy + ruff clean. Endpoint returns 401 without parent PIN; 200 with parent PIN + capability=True; 409 when capability is hard-off; 503 when worker is absent. Composite-only signal flows through `mode` field correctly.
- **Depends on:** P4 (new pipeline live), P5 (kiosk renders new sprites correctly).

### Step P7: Operator smoke gate — 1 toy × 10 sprites + UAT-tune the IPA scale value
- **Problem:** On the F.5-capable host with P1-P6 deployed (initial `IP_ADAPTER_SCALE = 0.6` from P4): pick one existing ingested toy with a clear reference photo (Plush Unicorn `2e6931e0…` from the existing fixtures is a good candidate per the F.5 smoke history). Generate all 10 action sprites via the existing per-toy endpoint `POST /api/toys/{toy_id}/actions/regenerate`. Operator inspects each sprite at `data/images/toy_actions/<toy_id>/<slot>.png`:
  - Does the sprite visually resemble the toy in the reference photo? (Identity check.)
  - Is the action recognizable as the slot it represents? (Pose-intent check.)
  - Are any sprites obviously broken (smeared, pseudo-text, wrong subject)? (Quality floor.)
  
  Capture per-sprite wall-clock + peak VRAM via `nvidia-smi --query-gpu=memory.used --format=csv` during a single 10-slot run. **If identity is weak or pose collapses across multiple slots, scale-knob tuning is needed.** The tune itself is per-operator-judgment: try 0.4 (loosen identity) and 0.8 (tighten identity), regenerate at each value, eyeball the side-by-side. **Operator does NOT edit code here** — the code change lives in P7b. P7's deliverable is a run-doc that names the chosen value in plain text.
  
  Mechanism for trying alternate scale values without a code change: operator temporarily edits `IP_ADAPTER_SCALE` in their working tree, restarts the backend (`uv run --extra image_gen python -m toybox.main`), regenerates, compares; then **reverts the local edit** so P7's git state is clean. The chosen value is then encoded in the run-doc for P7b to apply formally.
  
  Document chosen value + eyeballed per-slot quality + per-sprite wall-clock + peak VRAM in `documentation/runs/2026-MM-DD-phase-p-smoke.md`. State the chosen value verbatim and visibly (e.g. a line reading `"P7b should set IP_ADAPTER_SCALE = 0.7"`) so P7b can ingest it unambiguously.
- **Type:** operator
- **Issue:** #189
- **Flags:** n/a (operator step)
- **Produces:** Run-doc at `documentation/runs/2026-MM-DD-phase-p-smoke.md` naming the chosen `IP_ADAPTER_SCALE` value. No code changes — operator's working tree is clean at step end.
- **Done when:** 10/10 sprites generate without crash. Operator judgment: identity AND pose are visibly improved over the pre-P state on at least 7 of 10 sprites. Scale value pinned in the run-doc. Per-sprite wall-clock < 5s warm. Peak VRAM < 7 GB on the 8 GB host. Run-doc committed. **Working tree is clean (no IP_ADAPTER_SCALE edit lingering).**
- **Depends on:** P6.

### Step P7b: Apply UAT-tuned IP_ADAPTER_SCALE value to pipeline.py
- **Problem:** Read the chosen `IP_ADAPTER_SCALE` value from P7's run-doc (`documentation/runs/2026-MM-DD-phase-p-smoke.md`). Edit [`src/toybox/image_gen/pipeline.py`](../src/toybox/image_gen/pipeline.py) to set the `IP_ADAPTER_SCALE: Final[float]` constant to that value. Add or update a unit test that pins the constant value (so a silent future change goes through review): assert `pipeline.IP_ADAPTER_SCALE == <value>` in [`tests/unit/image_gen/test_pipeline_stub.py`](../tests/unit/image_gen/test_pipeline_stub.py) or a sibling module-level test. If P7 picks 0.6 (matches P4's initial value), still ship P7b — the test is the load-bearing artifact and confirms a deliberate choice rather than a no-op default.
- **Type:** code
- **Issue:** #190
- **Flags:** `--reviewers code`
- **Produces:** `pipeline.py` patched, unit test pinning the chosen scale value.
- **Done when:** Pinned test green. ruff + mypy strict clean. PR description references the P7 run-doc by path.
- **Depends on:** P7.

### Step P8: Operator iPad UAT — 2-3 toys, GLOBAL "regenerate every toy" flow, kiosk render in real activities
- **Problem:** On the iPad kiosk + parent app: with both kids' active toys (Child A: LOL dolls, varied plush; Child B: Periodic Table accessories per memory `project_kids_profiles.md`), ingest 2-3 fresh toys or use existing ones. Click the **new GLOBAL "Regenerate sprites for every toy" button** (from P6) at the top of the parent toys-list page. Verify:
  1. Button works → `total_enqueued` count is reported in the response toast.
  2. Per-slot WS-driven status badges in each toy's grid show queued → running → done.
  3. After completion, each toy's sprites visually represent that toy.
  4. Run a real activity from the kiosk that uses one of the toys' action sprites — confirm the kiosk renders the new 512-source sprite at the expected display size without `pixelated` chunkiness.
  5. Take 2-3 representative iPad screenshots for the run-doc.
  6. Both kids weigh in: does Child A recognize her LOL doll in the cheering sprite? Does Child B recognize the toy in the looking sprite?
  Document at `documentation/runs/2026-MM-DD-phase-p-uat.md`. Defects: file as follow-up GitHub issues (don't block Phase P close on cosmetic per-toy quality misses; the recourse is per-slot regenerate or operator scale-tune).
- **Type:** operator
- **Issue:** #191
- **Flags:** n/a (operator step)
- **Produces:** Run-doc at `documentation/runs/2026-MM-DD-phase-p-uat.md` with operator judgment + kid-feedback + screenshots. Possibly follow-up issues for cosmetic defects.
- **Done when:** Identity + pose pass for ≥ 70% of the slots generated. New global regenerate flow works end-to-end on iPad. No crashes. Run-doc committed.
- **Depends on:** P7b.

## 8. Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| `c10.dll` crash regression | The 2026-05-07 crashes that drove Phase F.5 happened with **SDXL + IPA + `enable_model_cpu_offload()` + 1024² + 25 steps**. Phase P uses SD 1.5 + IPA Plus + NO offload + 512² + 4 steps. Crash class is structurally different. But IP-Adapter is in the family of code paths that produced the original crash, and a reviewer or future-Claude reading this plan may not have the F.5 history available. | P2 includes regression smoke on the EXISTING pipeline before P3 touches anything. P7 is the new-pipeline smoke gate. If `c10.dll` recurs at P7, immediate rollback is `git revert` of the P3+P4 commits — the capability gate fails closed when IPA weights are present but pipeline raises, and the worker breaker trips after 3 failures. Existing toy PNGs continue to render via the static-files mount during rollback. |
| IPA scale collapses pose | Too-high scale → identity preserved but `jumping` looks like the photo (stiff limbs / wrong pose). Too-low scale → identity weak, regress to current state. No single right value; depends on cartoon checkpoint + toy reference quality. | P7 explicitly UAT-tunes scale across 0.4 / 0.6 / 0.8 on the same toy + same slots and picks the best. Documented in run-doc. If no scale works, escape hatch is ControlNet-OpenPose (Phase Q candidate; NOT in P scope). |
| Output 512 changes downstream assumptions | Any code that PNG-decodes a sprite and asserts 128×128 dimension breaks. Tests in `tests/unit/image_gen/` are the most likely surface. | P4's done-when requires a downstream-consumer grep checklist with one row per call site + verdict (per workspace `code-quality.md` rule). |
| Existing 128 sprites render smoothly-blurry between P5 ship and P6 trigger | After P5 drops `imageRendering: pixelated`, existing 128-source PNGs upscaled to 112 display use smooth resampling — slightly blurry, not pixel-art-crisp. This is NOT a bug, but it's a transitional UX state. | Documented in §6. Phase P bundles P5+P6 so the transitional state is short (single deploy). Operator can trigger the new global "Regenerate sprites for every toy" button immediately after P6 ships to convert all sprites to 512-source where smooth resampling is the correct choice. |
| Global regenerate VRAM contention with whisper-on-CUDA | N toys × 10 slots through the single-worker FIFO queue. Each job ~3 s warm. Whisper-small on CUDA holds ~250-500 MB. Combined peak ~5.5-6 GB. Comfortably under 8 GB. | Existing single-worker FIFO already serializes; no spike. Existing breaker handles real OOM. No new mitigation needed. Operator can flip `TOYBOX_AUDIO_DEVICE=cpu` if whisper-on-CUDA contention is observed (existing env knob). |
| Cartoon checkpoint + IPA compatibility | IPA SD 1.5 attaches to UNet cross-attention. Dreamshaper-7 (the current cartoon checkpoint) is a fine-tuned SD 1.5 — same architecture, same attachment points. Should work. | P7 is the empirical test. If incompatible, fall back to SD 1.5 base for IPA's UNet attachment and apply cartoon LoRA on top (the existing `TOYBOX_IMAGE_GEN_CARTOON_MODE=lora` path already supports this configuration). |
| Operator forgets to run P2 | If P3 ships before P2 runs on the host, the capability gate will return False with `missing_checkpoints` until the operator downloads. Pipeline fails closed. No crash. | Documented in P3's done-when (boot log shows the missing-checkpoint detail). Runbook updated in P1 with the download script call. Detection is loud (boot log + parent UI banner). |
| Image encoder filename pinning | `huggingface_hub.snapshot_download` writes the image-encoder weights to a specific filename (`model.safetensors` vs `pytorch_model.bin` depending on the upstream repo). P3's capability check must reference whatever name the actual download produces, not a guessed one. | P3 done-when explicitly says to pin the actual filename after P2's download is complete. Don't guess. |

**Open questions:**

- **`IP_ADAPTER_SCALE` initial value.** Pinned at 0.6 in P4 based on public benchmarks. UAT-tuned at P7. If experience shows different cartoon checkpoints need different defaults, follow-up phase considers promoting to env var.
- **What if P7 fails ≥70% bar but is "better than current"?** Subjective. Operator decides at P7. If the gap is small but positive, ship and file follow-ups. If the gap is large and IPA-Plus is the bottleneck, file Phase Q for ControlNet-OpenPose.

## 9. Testing strategy

**Backend tests (pytest):**

- Unit: `tests/unit/image_gen/test_capability.py` — extended with IPA-required-checkpoint assertions (P3).
- Unit: `tests/unit/image_gen/test_pipeline_stub.py` — verify stub still works, lazy-import contract still pinned (P4).
- Unit: `tests/unit/image_gen/test_lazy_imports.py` — verify `import toybox.image_gen.pipeline` does NOT pull in diffusers / torch / IPA symbols (P4).
- Integration: `tests/integration/test_image_gen_worker_e2e.py` — extended to assert worker → pipeline path exercises the new `ip_adapter_image` arg shape end-to-end (P4 done-when's required integration test).
- Integration: `tests/integration/test_image_gen_real_gpu.py` — operator-only test that runs the real pipeline; extended to assert IPA is loaded (P4, but skipped in CI).
- Integration: `tests/integration/test_toys_api_actions.py` — extended to cover the new global `POST /api/admin/regenerate-every-toy-action` endpoint (P6), mirroring the existing `test_regenerate_all_*` test pattern at lines 351-432.

**Frontend tests (vitest):**

- `ToyActionSprite.test.tsx` — update if it asserts the `pixelated` style (P5).
- Vitest coverage for the new GLOBAL "Regenerate sprites for every toy" button (P6) — written next to the parent toys-list component that hosts the button. `ToyActionGrid.test.tsx` is unchanged.

**End-to-end (operator):**

- P2: existing pipeline regression smoke (sanity baseline).
- P7: new pipeline smoke gate (1 toy × 10 sprites; UAT-tune scale).
- P8: iPad UAT (2-3 toys; full global "Regenerate sprites for every toy" flow; kiosk render in real activities).

**What might break:**

- Any test that PNG-decodes a sprite and asserts dimensions. P4's grep checklist surfaces these.
- The frontend's `ToyActionSprite` snapshot test if it captures the inline style verbatim. P5 updates the snapshot.
- The capability gate test if it pins the exact set of required files. P3 updates.

**Per-call timeout headroom.** Existing `TOYBOX_IMAGE_GEN_TIMEOUT_SEC` default is 120s. New steady-state per-sprite is ~3-4s warm. Comfortable. The operator's `.env` already sets 300 (carry-over from F2 — harmless surplus). No change.

**Lazy-import test (`test_lazy_imports.py`) is load-bearing.** A regression where `import toybox.image_gen.pipeline` accidentally pulls in heavy IPA / CLIP / diffusers symbols at module scope would silently re-introduce a ~2s import-time stall on every backend boot, even when image-gen is disabled. P4 must preserve this contract.

## 10. Status

| Step | Status |
|------|--------|
| P1 — IP-Adapter Plus download script + manifest + runbook | DONE (2026-05-18) |
| P2 — Operator: download + regression smoke | not started |
| P3 — capability.py checkpoint extension | not started |
| P4 — pipeline.py rewrite (IPA + 512² + extended negative + drop hex-tokens) | not started |
| P5 — Frontend: drop `imageRendering: pixelated` | not started |
| P6 — GLOBAL "Regenerate every toy" endpoint + parent UI button | not started |
| P7 — Operator: smoke gate + UAT-tune IPA scale (run-doc only, no code edit) | not started |
| P7b — Code: apply UAT-chosen IP_ADAPTER_SCALE to pipeline.py + pin test | not started |
| P8 — Operator: iPad UAT (global regenerate + real activity) | not started |
