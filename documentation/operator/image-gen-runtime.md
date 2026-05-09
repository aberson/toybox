# Operator runbook: Phase F.5 image-gen runtime

This runbook is the **F.5-1 deliverable** for Phase F.5 (sprite pipeline cartoon redo). It supersedes the original Phase F runbook (which described the SDXL+IPA pipeline retired by F.5 after F9 hit `c10.dll` access violations on 2026-05-07; see [issue #61](https://github.com/aberson/toybox/issues/61)).

The new pipeline is **SD 1.5 + LCM-LoRA + cartoon style at 512², 4-step inference, no IP-Adapter, no `model_cpu_offload`** — fits in ~4-5 GB peak on 8 GB hardware with comfortable headroom. Tier C sticker-composite fallback is the offline-floor for hosts without a capable GPU. Read this end-to-end the first time you bring up image generation on a host; subsequent installs can lean on the per-component download scripts plus the smoke command.

## GPU floor (relaxed from F2)

| | VRAM | What works | Notes |
|---|---|---|---|
| Recommended | ≥6 GB | Native config, comfortable headroom for whisper-on-CUDA | Mid-range desktop / laptop cards |
| Validated | 4–6 GB | **Canonical config** (no offload, just `pipe.to("cuda")`) — peak ~4-5 GB / ~2 s/sprite | RTX 4070 Laptop measured during F.5-2 |
| Tier C only | <4 GB or no GPU | Capability gate routes to composite fallback (rembg + Pillow paste); zero VRAM, ~100 ms/sprite | Cartoon templates curated in F.5-3b |
| Hard-off | env-disabled | `TOYBOX_IMAGE_GEN_ENABLED=false` skips both Tier B and Tier C | Operator-explicit "no sprites" |

Confirm what your host has:

```powershell
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
```

## Driver install (no-op if whisper-GPU is already up)

Phase F.5 shares the GPU runtime with the existing whisper STT path. If `nvidia-smi` already returns a GPU and toybox's STT has been running on GPU, you do NOT need to reinstall.

If this is a fresh GPU host:
1. Install the NVIDIA driver matched to your GPU. CUDA 12.4 runtime is what `torch>=2.6` ships against.
2. Reboot.
3. Run the `nvidia-smi` query above to confirm the driver loaded.

cuDNN 8.x is bundled into modern PyTorch wheels — no separate cuDNN install required.

## Checkpoint install

F.5 uses three checkpoint families plus the rembg model from F1:

| Family | Repo | Size | Path |
|---|---|---|---|
| SD 1.5 base | `stable-diffusion-v1-5/stable-diffusion-v1-5` | ~3 GB | `data/models/image_gen/sd15/base/` |
| LCM-LoRA SD 1.5 | `latent-consistency/lcm-lora-sdv1-5` | ~70 MB | `data/models/image_gen/sd15/lcm_lora/` |
| Cartoon checkpoint candidate | `Lykon/dreamshaper-7` (HF, OpenRAIL-M) OR ToonYou Beta 6 from civitai | ~3-4 GB | `data/models/image_gen/cartoon_checkpoint/` |
| Cartoon LoRA candidate (optional, A/B alternative) | civitai search "pixar cartoon lora SD 1.5"; any LoRA <500 MB | ~150-300 MB | `data/models/image_gen/cartoon_lora/` |
| rembg `u2net.onnx` (UNCHANGED from F1) | already on disk if F1 ran | ~170 MB | `data/models/image_gen/bg_remove/` |

**Note on the runwayml delisting:** `runwayml/stable-diffusion-v1-5` was delisted from HF in late 2024 by Runway. The official mirror that took over is `stable-diffusion-v1-5/stable-diffusion-v1-5`. Use that repo ID; older docs and tutorials referencing the runwayml path will 404.

### Run the per-component download scripts

```powershell
# SD 1.5 base (~3 GB; fp16 variant only)
$env:PYTHONIOENCODING='utf-8'
uv run scripts/f5_download_sd15.py

# LCM-LoRA (~70 MB)
uv run scripts/f5_download_lcm.py

# Cartoon checkpoint (Lykon/dreamshaper-7 default; ~3-4 GB fp16)
uv run scripts/f5_download_cartoon_checkpoint.py
```

Each script uses `huggingface_hub.snapshot_download` with `allow_patterns` filtering so only fp16 variants land. Total disk: ~7 GB (plus ~9 GB of obsolete SDXL/IPA/pixel-art-lora checkpoints from F1 that are NOT auto-deleted — see "Cleaning up obsolete F1 checkpoints" below).

### Cartoon LoRA (optional, A/B alternative)

**F.5-4 outcome (2026-05-09):** the smoke gate confirmed `TOYBOX_IMAGE_GEN_CARTOON_MODE=checkpoint` as the winner — the LoRA-mode A/B path was skip-clean because no SD 1.5 cartoon LoRA was sourced during F.5-1 (no good HF-hosted candidate without browser/auth at the time). The tiebreak rule applied: `checkpoint` is a single-model load (fewer moving parts, simpler operator setup, cleaner default). See [`documentation/runs/2026-05-09-toy-action-sprites-cartoon-smoke.md`](../runs/2026-05-09-toy-action-sprites-cartoon-smoke.md). Stick with the default unless you have a reason to want the LoRA path.

If you DO want to wire LoRA mode for an A/B comparison on your own host: civitai is the dominant source for SD 1.5 cartoon LoRAs. Pick one whose license permits local-private use (most do; redistribution is what's typically forbidden), download to `data/models/image_gen/cartoon_lora/pytorch_lora_weights.safetensors`, document the source + license in the manifest, and set `TOYBOX_IMAGE_GEN_CARTOON_MODE=lora` before backend restart.

### Verifying the manifest

`data/models/image_gen/manifest.json` records sha256 + size of each top-level safetensors / onnx file. Regenerate after install:

```powershell
uv run scripts/f5_compute_manifest.py
```

(Script writes the manifest from the on-disk files; safe to re-run.)

### Cleaning up obsolete F1 checkpoints

F.5 keeps the old SDXL/IPA/pixel-art-lora checkpoints on disk through F.5-5 for the rollback path. After F.5-5 soak passes, reclaim ~9 GB:

```powershell
Remove-Item -Recurse -Force data/models/image_gen/sdxl
Remove-Item -Recurse -Force data/models/image_gen/ip_adapter
Remove-Item -Recurse -Force data/models/image_gen/pixel_art_lora
```

Do NOT run this until F.5-5 passes. If F.5-5 fails native-crash check, the rollback is `git checkout pre-f5-cutover` (the tag F.5-1 sets) AND the old checkpoints must still be on disk.

## Canonical pipeline config

This is the F.5-validated config. Differs from F2 in significant ways:

```python
import torch
from diffusers import StableDiffusionPipeline, LCMScheduler

# Cartoon-mode dispatch (env-selectable):
# TOYBOX_IMAGE_GEN_CARTOON_MODE=checkpoint -> full cartoon checkpoint replaces SD 1.5 base
# TOYBOX_IMAGE_GEN_CARTOON_MODE=lora       -> SD 1.5 base + cartoon LoRA add-on

# Mode A: cartoon checkpoint (default, recommended for simpler operator setup)
pipe = StableDiffusionPipeline.from_pretrained(
    "data/models/image_gen/cartoon_checkpoint",
    torch_dtype=torch.float16,
    use_safetensors=True,
    local_files_only=True,
)

# Mode B (alternative): SD 1.5 base + cartoon LoRA add-on
# pipe = StableDiffusionPipeline.from_pretrained(
#     "data/models/image_gen/sd15/base",
#     torch_dtype=torch.float16,
#     variant="fp16",
#     use_safetensors=True,
#     local_files_only=True,
# )
# pipe.load_lora_weights(
#     "data/models/image_gen/cartoon_lora",
#     adapter_name="cartoon",
# )

# ALWAYS: stack LCM-LoRA on top
pipe.load_lora_weights(
    "data/models/image_gen/sd15/lcm_lora",
    adapter_name="lcm",
)
# In Mode B (lora cartoon) compose both adapters:
# pipe.set_adapters(["lcm", "cartoon"], adapter_weights=[1.0, 1.0])
# In Mode A (cartoon checkpoint) just use lcm:
pipe.set_adapters(["lcm"], adapter_weights=[1.0])

pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)

# Send to CUDA ONCE and stay. NO model_cpu_offload — that path was the prime
# suspect for the F9 c10.dll crashes that this phase exists to fix.
pipe.to("cuda")

# Cheap memory knob for VAE peak.
pipe.vae.enable_slicing()

# DO NOT call pipe.enable_attention_slicing() — F1 gotcha; PyTorch 2.4+ SDPA
# already provides memory-efficient attention by default.

# Generation: 4-step LCM with low CFG.
result = pipe(
    prompt=...,
    negative_prompt="photorealistic, 3d, blurry, smooth shading, antialiased",
    num_inference_steps=4,
    guidance_scale=1.0,            # LCM convention; higher CFG hurts LCM output
    height=512,
    width=512,
)
```

Per-sprite wall-clock target: **~2 s warm** on RTX 4070 Laptop (8 GB). 10 sprites per toy → ~20 s total. The original F2 measurement on the same host was ~30 s/sprite alone (or ~60-70 s/sprite under whisper-on-CUDA contention) — F.5 is ~15× faster per sprite by switching from SDXL @ 1024² 25-step to SD 1.5 @ 512² 4-step.

## Running the backend with image-gen enabled

```powershell
uv run --extra image_gen python -m toybox.main
```

A bare `uv run python -m toybox.main` triggers an implicit `uv sync` that normalizes the venv to the default (non-extras) dependency set, **silently uninstalling torch + diffusers + rembg**. The capability gate then reports `CUDA not available` even on hosts with a working GPU. Always pass `--extra image_gen` when launching the backend to keep the optional deps resident.

## Smoke probe

The canonical smoke probe is the F.5-2 CLI:

```powershell
uv run --extra image_gen python -m toybox.image_gen --probe <toy_id> --slot idle
```

Pass criteria:
- Wall-clock <30 s (vs F2's <120 s budget)
- Output PNG at `data/images/toy_actions/<toy_id>/idle.png` is non-empty + valid + has alpha channel
- Marker file `data/models/image_gen/.probe-pass-<iso>.json` written

## Tier C composite fallback (capability-gate-False, non-env-disabled)

When `is_image_gen_capable()` returns False because of GPU/VRAM/missing-checkpoints (NOT env-disabled), the worker routes to `composite.py` instead of returning a 409. Tier C uses the existing rembg + Pillow stack to composite the bg-removed toy photo onto a hand-curated cartoon action template.

**Templates:** 10 256×256 RGBA PNGs under `data/sprites/templates/<slot>.png`, one per `ACTION_SLOTS` member. The F.5-3b deliverable populates this with operator-curated art (CC0 or operator-drawn, never civitai). The F.5-3 placeholder generator (`scripts/f5_generate_templates.py`) ships iconographic placeholders that work but don't look polished — replace any individual template by dropping a new PNG at the same path. Composite cache flushes per process restart.

**Manifest:** `data/sprites/templates/manifest.json` declares `toy_box: [x0,y0,x1,y1]` (range 0..255) + `behind: bool` per slot. `behind: true` composites the toy UNDER the template (e.g. behind a pointing-arrow); `behind: false` composites OVER (toy on top of a stage / floor).

**No code change needed to swap art.** Replacing a PNG is hot-reloadable on the next backend restart.

## Env-var reference

| Var | Default | Purpose |
|---|---|---|
| `TOYBOX_IMAGE_GEN_ENABLED` | `auto` | `auto` = capability-gated; `true` = force-on (will fail loudly without GPU); `false` = force-off (no Tier B AND no Tier C) |
| `TOYBOX_IMAGE_GEN_DEVICE` | `cuda` | `cuda` or `cpu`; CPU mode is for testing only |
| `TOYBOX_IMAGE_GEN_MODEL_DIR` | `data/models/image_gen` | Root directory for checkpoints |
| `TOYBOX_IMAGE_GEN_BASE_MODEL_PATH` | `data/models/image_gen/sd15/base` | SD 1.5 base path (used in `lora` cartoon mode) |
| `TOYBOX_IMAGE_GEN_CARTOON_MODE` | `checkpoint` | `checkpoint` or `lora` — picks Mode A or Mode B per the canonical config above |
| `TOYBOX_IMAGE_GEN_CARTOON_PATH` | `data/models/image_gen/cartoon_checkpoint` | Path to cartoon checkpoint OR LoRA depending on mode |
| `TOYBOX_IMAGE_GEN_LCM_LORA_PATH` | `data/models/image_gen/sd15/lcm_lora` | LCM-LoRA path (always loaded) |
| `TOYBOX_IMAGE_GEN_OUTPUT_DIM` | `128` | Final sprite output dimension (square; resized after rembg cleanup) |
| `TOYBOX_IMAGE_GEN_TIMEOUT_SEC` | `120` | Per-slot generation timeout (now generously over-provisioned given ~2 s/sprite) |
| `TOYBOX_IMAGE_GEN_MIN_VRAM_GB` | `12` | Capability gate floor; **set to 6 on 8 GB hosts** |
| `TOYBOX_IMAGE_GEN_BREAKER_THRESHOLD` | `3` | Failures-in-60s threshold for breaker open |
| `TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC` | `300` | Breaker open duration |

**Removed since F2:** `TOYBOX_IMAGE_GEN_PALETTE_COLORS` (quantize step retired).

For the 8 GB host:

```dotenv
# .env (loaded automatically at backend startup via python-dotenv)
TOYBOX_IMAGE_GEN_MIN_VRAM_GB=6
TOYBOX_IMAGE_GEN_TIMEOUT_SEC=300       # carry-over from F2 .env; harmless given new <5 s/sprite reality
TOYBOX_IMAGE_GEN_BREAKER_THRESHOLD=5   # carry-over; same harmless
```

## Troubleshooting

### "torch.cuda.OutOfMemoryError" during the smoke probe

Check that another GPU consumer isn't holding VRAM:

```powershell
nvidia-smi
```

Expected steady state: SD 1.5 + LCM at 512² ~4-5 GB, plus whisper-small if running ~250-500 MB. Total ~5.5 GB peak on the 8 GB card. If a different process (Phase E local LLM, browser GPU compositor, an unrelated Python REPL with cached tensors) is holding more than ~2 GB, free it.

### "RuntimeError: Expected all tensors to be on the same device"

This was a known F2 failure mode (cuda:0 vs cpu mismatch from `model_cpu_offload` interaction with LoRA). F.5 doesn't use offload; if you see this in F.5, check:
1. The pipeline was constructed with `pipe.to("cuda")` AFTER all `load_lora_weights` calls
2. No `enable_model_cpu_offload()` slipped back into the canonical config
3. The cartoon checkpoint's safetensors are valid (sha256 matches manifest)

### "AttributeError: 'tuple' object has no attribute 'shape'" inside attention_processor

You're calling `pipe.enable_attention_slicing()`. Remove it. F1 gotcha #2.

### `enable_vae_slicing` deprecation warning

Use `pipe.vae.enable_slicing()` instead. F1 gotcha #3.

### `data/models/image_gen/manifest.json` shows missing files

Re-run the per-component download scripts. If files keep going missing, check `Get-PSDrive C` (Windows) for free disk and verify the install dir isn't on a quota-limited mount.

### First generation is suspiciously slow (>10 s)

The first generation per backend boot includes lazy module import (torch, diffusers) + first CUDA kernel compile + LoRA fusion. Subsequent generations should be ~2 s on 8 GB hosts. If steady-state is also >10 s, check that LCM-LoRA is actually loaded and `pipe.scheduler` is `LCMScheduler` — without LCM, the default DPM scheduler runs 25+ steps and is ~6× slower.

### Sprite quality is poor / doesn't resemble the toy

This is content quality, not runtime. F.5 explicitly relaxed the subject-identity requirement (the user said "more important that the toy be doing the action than be detailed"). The DB `tags` field + Pillow palette extraction + the cartoon LoRA carry the recognition floor.

If a particular toy reliably produces unrecognizable output:
1. Check the `tags` are populated (`SELECT tags FROM toys WHERE id = ?`). With Claude vision available at ingest, tags should be a JSON array. Without, operator-typed tags or empty.
2. Verify the source toy photo isn't poorly lit / has a busy background; rembg may struggle and palette extraction picks up noise.
3. The "regenerate" button is the parent's recourse for individual bad outputs.
4. The Tier C composite path is the fallback — if Tier B output isn't recognizable enough, the operator can flip a slot's `error_msg` to force composite for that toy. (Mechanism for this is a F.5+ enhancement; not in scope for F.5-1 itself.)

### "running in composite-only mode" banner appears on the parent UI

Capability gate is False but not env-disabled — sprites are being generated by Tier C composite (rembg + Pillow paste) instead of the diffusion pipeline. Check the boot log for the specific reason:

```
INFO toybox.app: image-gen capability=False reason=<...>
```

Reasons:
- `CUDA not available` → no NVIDIA GPU detected, or driver broken
- `VRAM 5.0GB < floor 6.0GB` → another GPU consumer is holding VRAM
- `checkpoints missing: ...` → run F.5-1 download scripts again

If you intended to run Tier B but capability is False, fix the underlying issue and restart. If you intended Tier C (e.g. testing the fallback path), this is expected.

## What this does NOT cover

- Custom LoRA training per toy. Out of scope for v1.5; if subject identity drifts consistently, follow-up phase adds a per-toy DreamBooth-lite step.
- Animated sprites. Out of scope; v1.5 is static PNGs only.
- Online image-gen fallback when the local GPU is unavailable. Plan-level decision: hosts without a GPU degrade to Tier C composite; without Tier C templates, sprites are absent (kiosk shows persona-only).
- Sprite editing UI. Operator's only tools are the per-slot and "regenerate all" buttons.
- Claude-driven prompt rewrites at ingest time. Deferred as future opt-in enhancement gated by `is_capable()`; F.5 prompts are templated fully locally from `toys.tags` + `toys.display_name` + `personas.display_name` + Pillow palette extraction.

## Related

- Plan: [`documentation/plan/phase-f-5-sprite-cartoon-redo.md`](../plan/phase-f-5-sprite-cartoon-redo.md)
- Original Phase F plan (the now-superseded SDXL pipeline, archived 2026-05-09): [`documentation/plan/archive/phase-f-toy-action-sprites.md`](../plan/archive/phase-f-toy-action-sprites.md)
- F9 fail report (the motivation for F.5): [`documentation/runs/2026-05-07-toy-action-sprites-smoke.md`](../runs/2026-05-07-toy-action-sprites-smoke.md)
- Investigation that produced the F.5 design: [`documentation/runs/2026-05-08-sprite-pipeline-alternatives.md`](../runs/2026-05-08-sprite-pipeline-alternatives.md)
- Per-component download scripts: [`scripts/f5_download_sd15.py`](../../scripts/f5_download_sd15.py), [`scripts/f5_download_lcm.py`](../../scripts/f5_download_lcm.py), [`scripts/f5_download_cartoon_checkpoint.py`](../../scripts/f5_download_cartoon_checkpoint.py)
- Template generator for F.5-3b placeholders: [`scripts/f5_generate_templates.py`](../../scripts/f5_generate_templates.py)
- Manifest computer (regenerates `manifest.json` from on-disk files): [`scripts/f5_compute_manifest.py`](../../scripts/f5_compute_manifest.py)
- Generated manifest (after first run): `data/models/image_gen/manifest.json` (gitignored)
