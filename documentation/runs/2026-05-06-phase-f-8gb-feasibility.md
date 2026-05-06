# Phase F — 8 GB VRAM feasibility investigation (2026-05-06)

> Research-only investigation produced during Phase F kickoff. Triggered by host pre-flight finding 8 GB VRAM (RTX 4070 Laptop) on the build machine, below the 12 GB floor declared in the plan. This document records the feasibility study; F1/F9/F10 remain PAUSED until a probe confirms the recommended config actually lands under the budget on this hardware.

## Executive answer

**Likely yes, but marginal — confidence ~65%.** The SDXL + IP-Adapter ViT-H + LoRA stack at 1024×1024 fp16 with `enable_model_cpu_offload()` + VAE slicing + attention slicing should land in the **6.0–7.5 GB peak VRAM** band, which fits 8 GB with thin headroom. The bigger risk is the **120 s wall-clock budget**: model offload typically costs 1.3–2× baseline; sequential offload costs 3–5× and almost certainly blows the budget. rembg (~170 MB u2net) is small enough to coexist if loaded onto CPU or kept resident on GPU between calls.

**Recommendation:** run the probe (procedure below) before committing to the 8 GB floor. If the probe shows >7.0 GB peak or >90 s wall-clock, fall back to:
- **SDXL Lightning + IP-Adapter at 1024** (4-step inference cuts time ~6×, peak VRAM essentially identical), or
- **SD 1.5 + IP-Adapter at 768×768** (definitive fit, ~3 GB peak, lower fidelity but fine for 128 px sprite output post-quantize).

## Key citations

- [diffusers — Reduce memory usage](https://huggingface.co/docs/diffusers/main/en/optimization/memory) — official memory-optimization API reference; documents `enable_model_cpu_offload()`, `enable_sequential_cpu_offload()` ("**extremely slow**"), VAE slicing/tiling, channels_last, layerwise casting. Current as of diffusers 0.31+.
- [Félix Sanz — Ultimate guide to optimizing SDXL](https://www.felixsanz.dev/articles/ultimate-guide-to-optimizing-stable-diffusion-xl) — measured peak VRAM at 1024×1024 fp16 on RTX 3090: **baseline 11.24 GB, model_cpu_offload 5.59 GB (-50%), sequential_cpu_offload 4.04 GB (-64%)**. Baseline 14.1 s/image; offload not separately timed but characterized as much slower.
- [HuggingFace blog — Exploring simple SDXL optimizations](https://huggingface.co/blog/simple_sdxl_optimizations) — sequential CPU offload + sliced VAE: **peak 11.45 GB allowing 4 images per prompt at 1024**; sliced VAE alone at 15.4 GB. Confirms model offload is the right knob for single-image 8 GB.
- [h94/IP-Adapter SDXL models tree](https://huggingface.co/h94/IP-Adapter/tree/main/sdxl_models) — file sizes: `ip-adapter_sdxl.safetensors` 703 MB (uses OpenCLIP-ViT-bigG), `ip-adapter_sdxl_vit-h.safetensors` 698 MB, `ip-adapter-plus_sdxl_vit-h.safetensors` 848 MB, `ip-adapter-plus-face_sdxl_vit-h.safetensors` 848 MB. The **ViT-H image encoder itself is ~2.5 GB / 632 M params** (loaded once, can sit on CPU between calls).
- [tencent-ailab/IP-Adapter README](https://github.com/tencent-ailab/IP-Adapter) — explicitly states ViT-H variant was chosen over ViT-bigG to "**reduce the memory usage in the inference phase**" with no measurable quality loss. Use the ViT-H variant.
- [diffusers issue #9149 — Flux NF4/FP8 on 6/8 GB](https://github.com/huggingface/diffusers/issues/9149) — Flux Schnell NF4 fits 8 GB at ~8–10 GB; **3.86× speedup on RTX 3070 Ti**; IP-Adapter for Flux not addressed here (and as of late 2025 IP-Adapter SDXL ecosystem is far more mature than Flux IPA).
- [ByteDance/SDXL-Lightning](https://huggingface.co/ByteDance/SDXL-Lightning) — 4-step inference, fully diffusers-compatible, same UNet as SDXL base so IP-Adapter loading via `pipe.load_ip_adapter()` works unchanged. Memory footprint identical to base SDXL; **wall-clock ~6× faster** because 4 steps vs 25–30.

## Recommended config for the 8 GB attempt

```python
import torch
from diffusers import StableDiffusionXLPipeline

pipe = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    torch_dtype=torch.float16,
    variant="fp16",
    use_safetensors=True,
)

# IP-Adapter: pick the ViT-H variant (smaller image encoder than bigG)
pipe.load_ip_adapter(
    "h94/IP-Adapter",
    subfolder="sdxl_models",
    weight_name="ip-adapter_sdxl_vit-h.safetensors",
)
pipe.set_ip_adapter_scale(0.6)

pipe.load_lora_weights("nerijs/pixel-art-xl")

# Memory knobs — order matters: do NOT call .to("cuda") before offload
pipe.enable_model_cpu_offload()       # first choice; ~50% VRAM cut, ~1.3–2× slowdown
pipe.enable_vae_slicing()             # cheap; covers VAE decode peak
pipe.enable_attention_slicing("max")  # extra safety; small extra slowdown
pipe.unet.to(memory_format=torch.channels_last)
# xformers is now default in PyTorch 2.4+ via SDPA — no explicit call needed.
# If still on torch <2.2: pipe.enable_xformers_memory_efficient_attention()
```

If `enable_model_cpu_offload()` OOMs, escalate to `enable_sequential_cpu_offload()` (drops to ~4 GB peak per Sanz, but expect 3–5× slowdown — likely blows 120 s budget on a 4070 Laptop).

## Probe procedure

Save as `vram_probe.py`. Total disk impact ~10 GB (SDXL base ~6.5 GB + IPA ~700 MB + image_encoder ~2.5 GB + LoRA ~200 MB). This is far smaller than F1's full install but you do still need the SDXL checkpoint — there is no honest sub-GB way to measure SDXL peak VRAM.

```python
# vram_probe.py
import time, torch
from diffusers import StableDiffusionXLPipeline
from diffusers.utils import load_image

torch.cuda.reset_peak_memory_stats()
t_load = time.time()

pipe = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    torch_dtype=torch.float16, variant="fp16", use_safetensors=True,
)
pipe.load_ip_adapter("h94/IP-Adapter", subfolder="sdxl_models",
                     weight_name="ip-adapter_sdxl_vit-h.safetensors")
pipe.set_ip_adapter_scale(0.6)
pipe.load_lora_weights("nerijs/pixel-art-xl")
pipe.enable_model_cpu_offload()
pipe.enable_vae_slicing()
pipe.enable_attention_slicing("max")
pipe.unet.to(memory_format=torch.channels_last)

ref = load_image("https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/diffusers/cat.png")
print(f"load+config: {time.time()-t_load:.1f}s")

torch.cuda.reset_peak_memory_stats()
t_gen = time.time()
img = pipe(
    prompt="pixel art, cute toy character running, 8-bit",
    ip_adapter_image=ref,
    num_inference_steps=25, guidance_scale=5.0,
    height=1024, width=1024,
    generator=torch.Generator("cuda").manual_seed(42),
).images[0]
torch.cuda.synchronize()
peak_gb = torch.cuda.max_memory_allocated() / 1024**3
print(f"generate: {time.time()-t_gen:.1f}s  peak VRAM: {peak_gb:.2f} GB")
img.save("probe.png")
# Pass: peak < 7.5 GB AND wall-clock < 90s. Marginal: 7.5–8.0 GB or 90–120s. Fail: OOM or >120s.
```

```powershell
uv venv --python 3.11
uv pip install "torch>=2.4" "torchvision" --index-url https://download.pytorch.org/whl/cu124
uv pip install "diffusers>=0.31" "transformers>=4.44" "accelerate>=0.33" "safetensors" "peft" "pillow"
uv run python vram_probe.py
```

## Fallback alternatives if probe fails (ranked)

1. **SDXL Lightning 4-step + IP-Adapter ViT-H** — same UNet memory profile (~6–7 GB peak with `enable_model_cpu_offload()`), but 4 inference steps instead of 25 cuts wall-clock to ~15–25 s on a 4070 Laptop even with offload. Quality is the same SDXL pixel-art character at 1024 then downscale. **Best fit-and-speed compromise.** [SDXL-Lightning](https://huggingface.co/ByteDance/SDXL-Lightning).
2. **SD 1.5 + IP-Adapter (`ip-adapter_sd15.safetensors`, 44 MB) + a pixel-art SD 1.5 LoRA at 768×768** — peak VRAM ~3.0–3.5 GB without any offload; 8–15 s/image on a 4070 Laptop. For a 128 px sprite output, the 1024 vs 768 native resolution difference is mostly invisible after palette-quantize and downscale. Lower IPA fidelity vs SDXL but the lossy pixel-art post-process forgives a lot. Definitive fit.
3. **SSD-1B + IP-Adapter (if a community-trained IPA exists for SSD-1B; check before committing)** — distilled SDXL, ~50% smaller UNet, 1024 native, ~5 GB peak. Faster than base SDXL on 8 GB; quality close to SDXL.
4. **Flux Schnell NF4 + (no IPA today)** — fits 8 GB at ~8–10 GB peak per [issue #9149](https://github.com/huggingface/diffusers/issues/9149), 4-step. **Drop reason:** IP-Adapter for Flux is community-experimental and not first-class in diffusers as of Q1 2026; this would mean re-architecting subject conditioning (e.g., Redux or a Flux-specific image-prompt fork). Only consider if SDXL stack definitively fails and reference-photo conditioning can be relaxed.
5. **Sequential CPU offload as a last resort** — guaranteed fit (~4 GB peak per Sanz) but 3–5× slowdown likely pushes per-sprite past 120 s on a 4070 Laptop. Acceptable only if you raise the per-call budget.

## How this affects Phase F builds

- **F2–F8 are unaffected.** The build steps land regardless; capability gate stays the gate.
- **F1 unblock criteria (revised):** if `vram_probe.py` reports peak VRAM < 7.5 GB AND wall-clock < 90 s on this host, F1's procedure can be attempted with the recommended config above. The four-checkpoint download is unchanged; only the runtime call sites change (add the offload + slicing knobs).
- **F2's `is_image_gen_capable()` `MIN_VRAM_GB` default:** keep at 12 (matches plan default for capable households). Override via env (`TOYBOX_IMAGE_GEN_MIN_VRAM_GB=8`) on this host if/when the probe passes — that's the documented escape valve, no code change needed.
- **F2's pipeline implementation should already include `enable_model_cpu_offload()` + VAE slicing + attention slicing as default knobs** so a host that overrides `MIN_VRAM_GB` to 8 just works without code changes. Build-step prompt for F2 will include this requirement.

## Status

- Probe procedure: NOT YET RUN. User to schedule.
- F1/F9/F10 status: PAUSED until probe lands a workable config or 12+ GB hardware is available.
- Sources cited above are all post-IPA-SDXL-release (Q4 2023+) and reflect diffusers 0.27+ / PyTorch 2.4+ behavior.

— produced by build-phase orchestrator's investigation agent, 2026-05-06

---

## Empirical findings (2026-05-06, after probe ran)

The probe was actually executed on the host machine after the research phase. **Verdict: PASS — definitive evidence the full Phase F stack fits in 8 GB.**

### Final probe result (rerun 5 of 5)

| Metric | Result | Threshold | Pass? |
|---|---|---|---|
| Peak VRAM | **6.11 GB** | <7.5 GB | ✅ |
| Wall-clock (25 steps, 1024×1024 fp16) | **30.2 sec** | <90 sec | ✅ |
| Stack | SDXL base + IP-Adapter ViT-H + pixel-art-XL LoRA + model_cpu_offload + vae.enable_slicing() | full | ✅ |
| Output validity | Valid PNG, IPA conditioning visible (subject identity preserved), pixel-art aesthetic intact | required | ✅ |

Visual evidence: [`2026-05-06-vram-probe-output.png`](2026-05-06-vram-probe-output.png) — pixel-art cat sprite generated from a generic cat reference photo with prompt "pixel art, cute toy character running, 8-bit". Full run log at [`2026-05-06-vram-probe.log`](2026-05-06-vram-probe.log).

Hardware: NVIDIA GeForce RTX 4070 Laptop GPU, 8 GB total VRAM, driver 581.95. Software: torch 2.6.0+cu124, diffusers 0.37.1, transformers 5.8.0, accelerate 1.13.0, peft 0.19.1, Python 3.12.13.

### Three diffusers gotchas discovered during the probe

These are mandatory implementation details for toybox's `src/toybox/image_gen/pipeline.py`. Without them the stack OOMs or crashes regardless of VRAM headroom.

**1. The image encoder MUST be loaded explicitly and passed at pipeline construction.**

`ip-adapter_sdxl_vit-h.safetensors` does NOT bundle its image encoder. If you call `pipe.load_ip_adapter(...)` without first constructing a `CLIPVisionModelWithProjection` and passing it as `image_encoder=...` to `from_pretrained`, generation crashes at the first `image_projection_layer` call with a shape mismatch:

```
RuntimeError: mat1 and mat2 shapes cannot be multiplied (2x1280 and 1024x8192)
```

The diffusers default falls back to OpenCLIP-ViT-bigG (1280-dim), but the ViT-H IPA weights expect ViT-H (1024-dim).

**Fix (mandatory):**
```python
from transformers import CLIPVisionModelWithProjection

image_encoder = CLIPVisionModelWithProjection.from_pretrained(
    "h94/IP-Adapter",
    subfolder="models/image_encoder",
    torch_dtype=torch.float16,
)

pipe = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    image_encoder=image_encoder,        # <-- MANDATORY
    torch_dtype=torch.float16,
    variant="fp16",
    use_safetensors=True,
)
pipe.load_ip_adapter(
    "h94/IP-Adapter",
    subfolder="sdxl_models",
    weight_name="ip-adapter_sdxl_vit-h.safetensors",
)
```

**2. `enable_attention_slicing()` is INCOMPATIBLE with IP-Adapter.**

The `enable_attention_slicing()` call swaps the unet's attention processors with sliced versions, which **overwrites** the IPA-aware processors that `load_ip_adapter()` installs. Subsequent generation crashes inside the swapped processor at the first `attn2` call with:

```
AttributeError: 'tuple' object has no attribute 'shape'
```

The trace points at `attention_processor.py` line 4029, which is reading `encoder_hidden_states.shape` before unpacking the IPA-tuple format `(text_states, image_embeds)`.

**Fix (mandatory): do NOT call `enable_attention_slicing()`.** PyTorch 2.4+ SDPA already provides memory-efficient attention by default, so slicing is redundant. The probe demonstrates that `enable_model_cpu_offload()` + `pipe.vae.enable_slicing()` is sufficient to land 6.11 GB peak.

This is true even WITHOUT the LoRA — the slicing+IPA incompatibility is independent of LoRA. The probe confirmed this in two separate runs.

**3. `enable_vae_slicing()` is deprecated; use `pipe.vae.enable_slicing()`.**

The pipeline-level `enable_vae_slicing()` will be removed in diffusers 0.40. Calling the VAE method directly avoids the deprecation warning:

```python
# Old (deprecated):
pipe.enable_vae_slicing()

# New (canonical):
pipe.vae.enable_slicing()
```

### Canonical config for toybox `src/toybox/image_gen/pipeline.py`

This is the empirically-validated config that lands 6.11 GB peak / 30s wall-clock on 8 GB hardware. Bake it as defaults; no env knobs needed for memory tuning unless future hardware needs different.

```python
import torch
from diffusers import StableDiffusionXLPipeline
from transformers import CLIPVisionModelWithProjection

image_encoder = CLIPVisionModelWithProjection.from_pretrained(
    "h94/IP-Adapter",
    subfolder="models/image_encoder",
    torch_dtype=torch.float16,
)

pipe = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    image_encoder=image_encoder,
    torch_dtype=torch.float16,
    variant="fp16",
    use_safetensors=True,
)
pipe.load_ip_adapter(
    "h94/IP-Adapter",
    subfolder="sdxl_models",
    weight_name="ip-adapter_sdxl_vit-h.safetensors",
)
pipe.set_ip_adapter_scale(0.6)            # toy ingest tuning may change this default
pipe.load_lora_weights("nerijs/pixel-art-xl")

pipe.enable_model_cpu_offload()           # ~50% VRAM cut, ~1.3-2x slowdown — required for 8 GB
pipe.vae.enable_slicing()                 # cheap; covers VAE decode peak
# DO NOT call enable_attention_slicing() — incompatible with IPA, see Gotcha #2
```

### Soak projection (revised)

Original F10 projection was 10-15h for 30 toys × 10 sprites = 300 generations. With measured 30.2 s/generation:

| Metric | Original projection | Empirical (with measured rate) |
|---|---|---|
| Per-sprite wall-clock | ~3 min | **30 sec** |
| 10 sprites per toy | ~30 min | **5 min** |
| 30 toys total | 10-15 hours | **2.5 hours** |

This is a 4-6× faster soak than the plan assumed. F10's overnight schedule is now overkill — a soak could complete during a single workday session. Plan doc's F10 wall-clock estimate should be revised in a future update.

### Implications for F1, F9, F10

- **F1 unblocked:** the discovered config works on 8 GB. F1 still ships per the plan (download checkpoints to `data/models/image_gen/`, document, smoke probe) but the smoke procedure can use the canonical config above.
- **F2 must bake in the canonical config as defaults.** This includes the explicit image_encoder load and the prohibition on attention_slicing. Detailed in F2's revised build-step prompt.
- **`TOYBOX_IMAGE_GEN_MIN_VRAM_GB` default:** keep at 12 (matches plan default for headroom hosts), but the discovered config means 8 GB hosts can override with confidence. The capability gate's `(False, "VRAM 8 GB < 12 GB floor")` reason becomes user-actionable: "set `TOYBOX_IMAGE_GEN_MIN_VRAM_GB=8` if your host can use the canonical config".
- **F9 unblocked:** the operator smoke gate now has empirical wall-clock budgets to validate against (30s/sprite ± 50%; 5 min/toy ± 50%).
- **F10 unblocked, schedule shortened:** 2.5 hours instead of overnight.

### Remaining unknowns

- **rembg memory footprint when stacked with the diffusion pipeline.** Probe didn't load rembg. The plan says ~170 MB for `u2net.onnx`; should fit easily in the 1.9 GB headroom. F2 build should validate this.
- **Phase E concurrent local-LLM contention.** If Phase E ships and the local LLM holds 5-8 GB simultaneously, the 8 GB host runs out. Per-pipeline breaker isolates failures, but coordinated GPU scheduling is a follow-up.
- **Generation quality for actual toy photos.** The probe used a generic cat reference. Real toys (varied lighting, multiple objects in frame, plush vs hard surface) may need different IPA scale or LoRA strength. F9's smoke gate is the first real test.

— probe executed and findings recorded by build-phase orchestrator, 2026-05-06
