# Operator runbook: Phase F image-gen runtime

This runbook is the F1 deliverable for Phase F (toy action sprites). It covers driver / checkpoint install, the empirically-validated canonical pipeline config, the smoke probe, troubleshooting, and the env-var reference. Read this end-to-end the first time you bring up image generation on a host; subsequent installs can lean on `scripts/image_gen_setup.py` plus the smoke command.

## GPU floor

| | VRAM | What works | Notes |
|---|---|---|---|
| Recommended | ≥16 GB | Native config, no offload, ~10–15 s/sprite | Phase F was originally scoped here |
| Supported | 12–16 GB | Native or single-knob memory opt | Mid-range desktop / workstation cards |
| Validated | 8 GB | **Canonical config** (model_cpu_offload + vae.enable_slicing()) — peak 6.11 GB / 30 s/sprite | RTX 4070 Laptop measured 2026-05-06 |
| Below floor | <8 GB | Capability gate disables image-gen; kiosk degrades to persona-only | No graceful runtime fallback below 8 GB |

Confirm what your host has:

```powershell
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
```

The 8 GB validation report (probe procedure, full diffusers config, three IPA gotchas, output sample) lives at [`documentation/runs/2026-05-06-phase-f-8gb-feasibility.md`](../runs/2026-05-06-phase-f-8gb-feasibility.md). If you have <8 GB, stop here — you cannot run Phase F on this host.

## Driver install (no-op if whisper-GPU is already up)

Phase F shares the GPU runtime with the existing whisper STT path. If `nvidia-smi` already returns a GPU and toybox's STT has been running on GPU, you do NOT need to reinstall.

If this is a fresh GPU host:
1. Install the NVIDIA driver matched to your GPU. CUDA 12.4 runtime is what `torch>=2.6` ships against.
2. Reboot.
3. Run the `nvidia-smi` query above to confirm the driver loaded.

cuDNN 8.x is bundled into modern PyTorch wheels — no separate cuDNN install required.

## Checkpoint install

Run the setup script from the repo root:

```powershell
uv run scripts/image_gen_setup.py
```

The script:
1. Downloads SDXL base 1.0 fp16 weights to `data/models/image_gen/sdxl/stable-diffusion-xl-base-1.0/` (filtered to fp16 variants only — saves ~7 GB vs the full repo).
2. Downloads IP-Adapter SDXL ViT-H weights + the matching CLIP image encoder to `data/models/image_gen/ip_adapter/`.
3. Downloads the pixel-art-XL LoRA to `data/models/image_gen/pixel_art_lora/`.
4. Downloads `rembg`'s `u2net.onnx` to `data/models/image_gen/bg_remove/`.
5. Computes sha256 of each top-level safetensors / onnx file and writes `data/models/image_gen/manifest.json`.

Total disk: ~13 GB. Re-running the script is safe; existing files are skipped.

If `~/.cache/huggingface/hub/` already has the HF-hosted models from a prior `from_pretrained` call (e.g. you ran the 8 GB probe), `snapshot_download` reuses them — only `u2net.onnx` will actually fetch fresh.

### Verifying the manifest

After the script finishes, `manifest.json` contains the sha256 + size of each file. Diff against this file in future installs to detect bit-rot or partial downloads. To regenerate it without re-fetching:

```powershell
uv run scripts/image_gen_setup.py
```

(The script always rewrites the manifest from the on-disk files even when downloads are skipped.)

## Canonical pipeline config

This is the empirically-validated config. **All three rules below are mandatory** — each one was discovered by an actual probe crash on 8 GB hardware. Even on hosts with plenty of VRAM, follow them; they're correctness, not just memory.

```python
import torch
from diffusers import StableDiffusionXLPipeline
from transformers import CLIPVisionModelWithProjection

# Rule 1: load the CLIP image encoder EXPLICITLY -- ip-adapter_sdxl_vit-h
# does not bundle one. Without this, generation crashes with
# "RuntimeError: mat1 and mat2 shapes cannot be multiplied (2x1280 and 1024x8192)".
image_encoder = CLIPVisionModelWithProjection.from_pretrained(
    "h94/IP-Adapter",
    subfolder="models/image_encoder",
    torch_dtype=torch.float16,
)

pipe = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    image_encoder=image_encoder,             # MANDATORY (Rule 1)
    torch_dtype=torch.float16,
    variant="fp16",
    use_safetensors=True,
)
pipe.load_ip_adapter(
    "h94/IP-Adapter",
    subfolder="sdxl_models",
    weight_name="ip-adapter_sdxl_vit-h.safetensors",
)
pipe.set_ip_adapter_scale(0.6)
pipe.load_lora_weights("nerijs/pixel-art-xl")

# Memory knobs.
pipe.enable_model_cpu_offload()              # ~50% VRAM cut, ~1.3-2x slowdown
pipe.vae.enable_slicing()                    # Rule 3: canonical API; pipe.enable_vae_slicing() is deprecated
# Rule 2: DO NOT call pipe.enable_attention_slicing(). It overwrites the
# IPA-aware attention processors that load_ip_adapter installs, and the next
# attn2 call crashes with "AttributeError: 'tuple' object has no attribute 'shape'".
# PyTorch 2.4+ SDPA already provides memory-efficient attention by default.
```

The negative prompt baked into Phase F's pipeline is `"photorealistic, 3d, blurry, smooth shading, antialiased, gradient"` — pushes toward the crisp pixel-art aesthetic. See plan §"Action vocabulary" for the per-slot prompt strings.

## Running the backend with image-gen enabled

**Important: always start the backend with `--extra image_gen`**:

```powershell
uv run --extra image_gen python -m toybox.main
```

A bare `uv run python -m toybox.main` triggers an implicit `uv sync` that normalizes the venv to the default (non-extras) dependency set, **silently uninstalling torch + diffusers + transformers + rembg**. The capability gate then reports `CUDA not available` even on hosts with a working GPU. Always pass `--extra image_gen` when launching the backend (and any related script) to keep the optional deps resident.

## Smoke probe

The canonical smoke probe is the F2-shipped CLI:

```powershell
uv run --extra image_gen python -m toybox.image_gen --probe <toy_id> --slot idle
```

Pass criteria:
- Wall-clock <120 s
- Output PNG at `data/images/toy_actions/<toy_id>/idle.png` is non-empty + valid + has alpha channel + ≤32 colors
- Marker file `data/models/image_gen/.probe-pass-<iso>.json` written

If you have not yet built F2, the standalone probe at [`documentation/runs/2026-05-06-phase-f-8gb-feasibility.md`](../runs/2026-05-06-phase-f-8gb-feasibility.md#probe-procedure) exercises the same pipeline against a generic cat reference (no toy required). Use that as the pre-F2 substitute.

## Env-var reference

| Var | Default | Purpose |
|---|---|---|
| `TOYBOX_IMAGE_GEN_ENABLED` | `auto` | `auto` = capability-gated; `true` = force-on (will fail loudly without GPU); `false` = force-off |
| `TOYBOX_IMAGE_GEN_DEVICE` | `cuda` | `cuda` or `cpu`; CPU mode is for testing only |
| `TOYBOX_IMAGE_GEN_MODEL_DIR` | `data/models/image_gen` | Root directory for the four checkpoints |
| `TOYBOX_IMAGE_GEN_OUTPUT_DIM` | `128` | Pixel-art sprite output dimension (square) |
| `TOYBOX_IMAGE_GEN_PALETTE_COLORS` | `32` | Palette quantize color count |
| `TOYBOX_IMAGE_GEN_TIMEOUT_SEC` | `120` | Per-slot generation timeout |
| `TOYBOX_IMAGE_GEN_MIN_VRAM_GB` | `12` | Capability gate floor; **set to 8 on validated 8 GB hosts** |
| `TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC` | `300` | Breaker open duration after 3 failures in 60s |

For the 8 GB host:

```dotenv
# .env (loaded automatically at backend startup via python-dotenv)
TOYBOX_IMAGE_GEN_MIN_VRAM_GB=6
```

The backend calls `dotenv.load_dotenv()` at the top of `src/toybox/main.py` before any other imports, so values in `.env` are honored — no need to export them in the shell first. Shell-level env vars still win over `.env` (default `load_dotenv` semantics: existing values are not overridden).

Use **6** on this RTX 4070 Laptop (8 GB total, ~6.9 GB free with browser open) — the empirical probe peaked at 6.11 GB so 6 is the right floor with thin headroom. Use **8** on hosts with ≥10-12 GB total but stricter "no-headroom-for-other-apps" posture. Keep the default **12** on dedicated 16+ GB hosts.

This override has no effect on hosts with ≥12 GB free; the capability gate's floor check just becomes trivially true earlier.

## Troubleshooting

### "torch.cuda.OutOfMemoryError" during the smoke probe

Check that you actually have the canonical config applied — specifically `enable_model_cpu_offload()`. On 8 GB hosts the native config (no offload) ALWAYS OOMs because SDXL alone is ~11 GB peak.

If you're already on the canonical config and still OOM, the most likely culprit is another GPU consumer:

```powershell
nvidia-smi
```

If a different process (Phase E local LLM, browser GPU compositor, an unrelated Python REPL with cached tensors) is holding VRAM, free it. The plan's "VRAM contention with Phase E local LLM" risk lives here.

### "RuntimeError: mat1 and mat2 shapes cannot be multiplied"

You're missing the explicit `image_encoder` load. Check that your pipeline construction includes `image_encoder=image_encoder` in the `from_pretrained` call AND that `image_encoder` came from `CLIPVisionModelWithProjection.from_pretrained("h94/IP-Adapter", subfolder="models/image_encoder")`. See Rule 1 above.

### "AttributeError: 'tuple' object has no attribute 'shape'" inside attention_processor

You're calling `pipe.enable_attention_slicing()`. Remove it. See Rule 2 above.

### `enable_vae_slicing` deprecation warning

Use `pipe.vae.enable_slicing()` instead. See Rule 3.

### `data/models/image_gen/manifest.json` shows missing files

Re-run `uv run scripts/image_gen_setup.py`. If files keep going missing, check `df -h` (Linux) or PowerShell `Get-PSDrive C` for free disk and verify the install dir isn't on a quota-limited mount.

### First generation is suspiciously slow (>2 minutes)

The first generation per backend boot includes lazy module import (torch, diffusers, transformers) + first CUDA kernel compile. Subsequent generations should be ~30 s on 8 GB hosts, ~10–15 s on ≥16 GB hosts. If steady-state is also >2 minutes, check `enable_model_cpu_offload()` is configured (it adds PCIe traffic per layer; expected on 8 GB but optional on ≥16 GB).

### Sprite quality is poor / doesn't resemble the toy

This is content quality, not runtime — the IPA scale (`pipe.set_ip_adapter_scale(0.6)`) and prompt template are the levers. Open a follow-up issue with the toy_id + slot + a sample sprite; do not modify the canonical config in `pipeline.py` ad-hoc. Per plan, "regenerate" buttons are the parent's recourse for individual bad outputs.

## What this does NOT cover

- Custom LoRA training per toy. Out of scope for v1; if subject identity drifts consistently, follow-up phase adds a per-toy DreamBooth step.
- Animated sprites. Out of scope; v1 is static PNGs only.
- Online image-gen fallback when the local GPU is unavailable. Plan-level decision: hosts without a GPU silently degrade.
- Sprite editing UI. Operator's only tools are the per-slot and "regenerate all" buttons.

## Related

- Plan: [`documentation/plan/phase-f-toy-action-sprites.md`](../plan/phase-f-toy-action-sprites.md)
- 8 GB feasibility report (with full probe procedure, output sample, three diffusers gotchas long-form): [`documentation/runs/2026-05-06-phase-f-8gb-feasibility.md`](../runs/2026-05-06-phase-f-8gb-feasibility.md)
- Setup script: [`scripts/image_gen_setup.py`](../../scripts/image_gen_setup.py)
- Generated manifest (after first run): `data/models/image_gen/manifest.json` (gitignored; regenerate via setup script)
