# Phase F — Sprite pipeline alternatives investigation (2026-05-08)

> Research-only investigation triggered by [F9 smoke gate failing on 2026-05-07](2026-05-07-toy-action-sprites-smoke.md) (c10.dll crash on 8 GB VRAM) AND a constraint relaxation from the operator: **fidelity / subject identity is no longer required; action verb + emotion legibility is the load-bearing requirement; cartoon style is preferred over pixel-art**. This document scopes the design space and recommends a v1.5 target. No code changes here. The full Phase F build plan ([phase-f-toy-action-sprites.md](../plan/phase-f-toy-action-sprites.md)) remains the canonical reference for what's currently shipped.

## Why this investigation exists

The current pipeline ([src/toybox/image_gen/pipeline.py](../../src/toybox/image_gen/pipeline.py)) is **SDXL @ 1024² + IP-Adapter SDXL ViT-H + pixel-art LoRA + double rembg pass**, 25 inference steps, ~6.1 GB peak VRAM measured during the [2026-05-06 8 GB feasibility probe](2026-05-06-phase-f-8gb-feasibility.md). The same pipeline crashed Python natively (`c10.dll` access violation, 0xc0000005) three times during F9 smoke on 2026-05-07. Same fault offset across all 3 crashes — deterministic, not memory corruption.

The operator's new framing changes the design space dramatically:

> "The picture generation is very detailed, we don't need that. It might be better and faster to have 2D cartoon style art. More important that the toy be doing the action 'pointing' or showing an emotion 'happy' than be detailed."

Concretely this unlocks three things:
1. **Drop IP-Adapter entirely** — its sole purpose is preserving subject identity from a reference photo (a ~2.5 GB ViT-H image encoder + ~700 MB IPA weights + activations on top). If "the unicorn pointing" being recognizable as the *user's specific* unicorn is no longer a hard requirement, IPA goes away.
2. **Replace SDXL with a smaller text-to-image model** — SD 1.5 base is ~3 GB fp16; SDXL UNet alone is ~6 GB. The choice is unblocked once IPA is gone.
3. **Lower the resolution target** — current pipeline generates 1024² then post-processes to 128 px. Generating directly at 512² is 4× cheaper on memory + compute and the kiosk renders at 96-128 px anyway.

Together these eliminate the 8 GB-marginal posture *and* the `enable_model_cpu_offload()` path that's the prime suspect for the c10.dll crashes (offload moves parameters between GPU/CPU per-call; offload-state-vs-LoRA-vs-IPA synchronization is the most plausible source of the cuda:0/cpu device-mismatch we observed).

## Constraint vector (current vs target)

| Dimension | Current | v1.5 target |
|---|---|---|
| Base model | SDXL 1.0 (~6 GB UNet alone) | SD 1.5 (~3 GB UNet) |
| Resolution | 1024² fp16 | 512² fp16 |
| Steps | 25 (DPM) | 4 (LCM-LoRA) |
| Subject conditioning | IP-Adapter ViT-H reference photo | None — caption-driven |
| Style | Pixel-art LoRA + palette-quantize | Cartoon LoRA, no quantize |
| Memory knob | `enable_model_cpu_offload()` + `vae.enable_slicing()` | Fits in VRAM; no offload needed |
| Peak VRAM | ~6.1 GB measured, ~7+ GB worst case | **~4-5 GB estimated** |
| Wall-clock per sprite | ~30 s alone, ~60-70 s under whisper-on-CUDA contention | **~2 s estimated** |
| Failure mode | 8 GB-marginal; native crashes | Comfortable headroom on 8 GB |
| Identity preservation | Strong (IPA conditions on photo) | Weak (relies on caption + palette) |
| Action verb legibility | Strong (SDXL prompt adherence) | Strong (SD 1.5 + cartoon LoRA + 4-step LCM) |

## Three tiers (ranked)

### Tier A — drop-in replacement, smallest code delta

**Stack:** Keep SDXL UNet; swap pixel-art LoRA → cartoon SDXL LoRA; add SDXL Lightning 4-step LoRA; drop IP-Adapter branch; drop quantize; reduce to 768² or 512².

**Estimated VRAM peak:** ~5.5-6.5 GB (UNet still big). **Estimated wall-clock:** ~5-8 s/sprite. **Effort:** small (LoRA swap, scheduler change, remove IPA, drop quantize). **Risk:** medium — same SDXL UNet that's been crashing this box. You get speed but not the VRAM relief that would let you exit `enable_model_cpu_offload()`, which is the prime suspect for the c10.dll crash.

**Verdict: not recommended.** Doesn't address the actual crash mode.

### Tier B — recommended v1.5 target

**Stack:** **SD 1.5 + LCM-LoRA + cartoon-style LoRA at 512²**, 4 steps, NO IP-Adapter, NO reference photo at gen time. Prompts templated **fully locally** from data already in the DB:

```
"<intro>, <tags>, <palette tokens>, <ACTION_PROMPTS[slot]>, 2D cartoon, simple shapes"
```

Where:
- `<intro>` = `f"{persona.display_name} the {toy.display_name}"` if persona is set else `f"a {toy.display_name}"` (existing pattern from current pipeline.py:154-174)
- `<tags>` = `toys.tags` joined with commas (existing column, populated at ingest by [toy_vision.py](../../src/toybox/ai/toy_vision.py) when Claude is capable, by operator entry when not)
- `<palette tokens>` = top 3 dominant hex colors extracted from the rembg cutout via Pillow `Image.quantize(colors=8).getpalette()` — no model, runs CPU-side, sub-100 ms

**Zero new external dependencies.** No new Claude call at ingest. No new ingest latency. No new DB column. The existing toy_vision Claude call already happens (or is operator-replaced) at ingest and populates `tags` — Tier B reads what's already there. Sprite generation itself is fully offline, exactly like the rest of the kid-facing path.

**Optional enhancement** (NOT v1.5 scope, mentioned for completeness): the same `is_capable()` capability-gate pattern used elsewhere in the codebase could later add a Claude-driven per-slot prompt rewrite *when available*, for personalized prompts like "Mr. Unicorn pointing his rainbow horn at the magnifying glass". This would be a strict enhancement — falls back to the local template when Claude is gated off. Defer until/unless real-use shows the local template's quality is the limiting factor.

**LoRA candidates** (research agent's findings, all SD 1.5 family):
- [ToonYou Beta 6](https://civitai.com/models/30240/toonyou) — 5-star checkpoint, strong cartoon vibe; civitai license — verify before redistribution
- [designPixar LoRA](https://civitai.com/models/734883/designpixar) — usable as a true add-on LoRA (not a full checkpoint), Pixar-3D look
- [Disney Pixar Cartoon Type B](https://civitai.com/models/75650/disney-pixar-cartoon-type-b) — checkpoint, 3D-Pixar
- LCM-LoRA: [latent-consistency/lcm-lora-sdv1-5](https://huggingface.co/latent-consistency/lcm-lora-sdv1-5) — official, drop-in via `pipe.load_lora_weights(...)` + `LCMScheduler.from_config(pipe.scheduler.config)` ([HF blog, lcm_lora](https://huggingface.co/blog/lcm_lora))

**Estimated VRAM peak:** **~4-5 GB.** Comfortable headroom for whisper-on-CUDA contention. Eliminates `enable_model_cpu_offload()` (just `.to("cuda")` everything), which eliminates the offload-induced device-mismatch class of bug. **Estimated wall-clock:** **~2 s/sprite warm** (extrapolating from "under 2 s" SD 1.5 full-step on RTX 4060 + LCM-LoRA's ~6× speedup); **~20 s for 10 sprites** end-to-end. **Effort:** medium (model swap; replace existing `_build_prompt` to template from DB fields + Pillow palette; drop IPA / pixel-art / quantize / offload branches). **Risk:** low — SD 1.5 is the most-tested 8 GB workload in the world; LCM-LoRA is one line; no IP-Adapter to fight; no offload to crash; no new external dependency.

**Subject identity tradeoff:** A 5-year-old's recognition threshold is dominant color + dominant shape + one signature element (rainbow mane, gold horn). The DB `tags` field captures the signature elements; Pillow palette extraction captures the dominant colors. Together they carry recognition for most cases. **Failure case:** two near-identical sibling toys that differ only by a small detail will collide — accept and document. The Tier C composite path is the recovery option when this matters.

**Verdict: ship this.**

### Tier C — escape hatch / offline mode

**Stack:** Sticker-composite. 10 hand-curated cartoon "action templates" (one per slot — a generic happy-pose silhouette, a pointing-arrow shape, etc.) shipped in `frontend/public/sprite-templates/` or similar. At sprite-generation time, composite the bg-removed toy photo onto/into the template via Pillow. **Zero VRAM. ~100 ms per sprite. Fully deterministic.**

**Effort:** medium — one-time art curation for 10 templates is the bulk of the work. The compositing logic is ~50 lines of Pillow.

**Subject identity tradeoff:** the toy is unambiguously itself (it's the actual photo). Action is legible from the template. Reads as a clip-art collage, not unified character art — adults will see the seams; kids likely won't care.

**Verdict: scaffold alongside Tier B as the breaker-open / capability-disabled / offline-mode floor.** If a Tier B sprite fails action-legibility QA (or never generates), Tier C produces something usable instantly. Today's pipeline already has the ingredients — `rembg` is in the stack, Pillow is in the stack, all that's missing is the templates and the composite logic. Honest fallback for the 0%-VRAM households the capability gate currently hard-disables.

## Approaches considered and rejected

| Approach | Why rejected |
|---|---|
| SDXL Turbo / SDXL Lightning 1-step | Same SDXL UNet → same ~6 GB UNet baseline → same VRAM-marginal box, same crash mode |
| Flux Schnell NF4 | 8-10 GB envelope per [diffusers#9149](https://github.com/huggingface/diffusers/issues/9149); overkill for 128 px output; weaker cartoon-LoRA ecosystem |
| PixArt-Σ | Diffusers docs demonstrate <8 GB feasibility but slower than SD 1.5 + LCM and thinner cartoon LoRA support |
| Stable Cascade / Würstchen | Three-stage cascade complexity not justified at this scale |
| Per-toy textual inversion / DreamBooth-lite | Training-time VRAM exceeds 8 GB; ingest-time wait would be minutes/toy; loses the "fast" win |
| SVG generation via Claude | Claude SVG output skews stick-figure-grade; latency dominated by Claude API; quality ceiling likely below user's bar |
| Open Peeps / Avataaars / DiceBear | Wrong domain (humanoid avatars, not stuffed toys); wrong identity story |
| AdaIN style transfer | Can re-style a photo but cannot change pose; action-verb legibility = zero |

## How this interacts with the existing failure mode

Tier B specifically addresses both observed F9 crash signatures:

1. **`c10.dll` access violation** — the prime suspect was the `enable_model_cpu_offload()` path (offload moves parameters between GPU/CPU per-forward; offload-state-vs-LoRA-vs-IPA synchronization is fragile). Tier B's ~4-5 GB peak fits in 8 GB **without offload** — `pipe.to("cuda")` and stay there. Crash class eliminated by construction.
2. **`Expected all tensors on cuda:0 and cpu` device mismatch** — same root cause: a tensor lingered on CPU when the model_cpu_offload hook moved the rest to CUDA. Same fix: no offload, no mismatch.

This makes the migration also the fix for issue #61 (the c10.dll crash blocker for F10). If we ship Tier B, F10 unblocks.

## Recommended next steps

1. **Run a single-prompt local probe** for Tier B before committing — ~30 minutes of work. Pick one of the cartoon LoRA candidates, load SD 1.5 + LCM-LoRA + cartoon LoRA on the actual 8 GB host, generate 1 image at 512², measure peak VRAM via `torch.cuda.max_memory_allocated()` and wall-clock. If peak <5 GB and wall-clock <3 s, the v1.5 budget holds. (Mirrors the [2026-05-06 8 GB feasibility probe](2026-05-06-phase-f-8gb-feasibility.md) pattern that unblocked Phase F originally.)
2. **`/plan-feature`** for "Phase F.5 — sprite pipeline cartoon redo" — a proper sub-plan that defines the migration steps. Likely shape:
    - F.5-1: cartoon-LoRA + LCM-LoRA selection + license/sha256 audit, runbook update
    - F.5-2: pipeline.py rewrite — SD 1.5 + LCM + cartoon LoRA at 512²; drop IPA + pixel-art LoRA + palette quantize + model_cpu_offload; replace `_build_prompt` with the DB-fields + Pillow-palette template
    - F.5-3: Tier C sticker-composite path as the capability-disabled / breaker-open / "Tier B output failed action-legibility QA" fallback (uses the existing rembg + Pillow stack — already in the codebase, just needs templates and ~50 lines of composite logic)
    - F.5-4: re-run F9 smoke gate against the new pipeline; expectation is 10/10 sprites at <30 s total + zero c10.dll crashes
    - F.5-5: re-run F10 soak (now with a realistic ~10-minute wall-clock budget for 30 toys × 10 sprites)
3. **Close issue #61** as obsolete-by-pivot once F.5-3 lands. The crash isn't being chased to root cause; the architecture that caused it is being retired.
4. **Keep the existing F9 fixture data** ([data/toybox.db](../../data/toybox.db) preserves the smoke-test toy with stranded `running` / `queued` rows) until F.5-5 — it's a free regression test that the new pipeline can recover from a half-finished prior toy.

## Caveats

- All wall-clock numbers cited here are extrapolated from public benchmarks on similar GPUs (RTX 4060/4070, RTX 3070), not measured on this exact host. Step 1 above (the local probe) is the load-bearing measurement.
- The "5-year-old recognizes Mr. Unicorn" claim in Tier B is a working hypothesis, not measured. The smoke gate UAT (F.5-5) is the real test. If recognition fails in practice, the recovery is to layer Tier C's composite *under* the diffusion output (toy photo half-transparent behind the cartoon body) — still cheap, still in the same code path.
- Civitai LoRAs default to creator-set licenses; for a household-private kiosk, local use is fine. Prefer HF-Hub-hosted equivalents where the license is explicit if any redistribution scenario emerges.
- The Tier B prompt template uses ONLY locally-available data (`toys.display_name`, `toys.tags`, `personas.display_name`, plus Pillow palette extraction from the rembg cutout). Sprite generation has no Claude dependency. The existing toy-ingest Claude vision call (which populates `tags`) is unchanged — already has its own capability-gate fallback to operator entry per [toy_vision.py](../../src/toybox/ai/toy_vision.py). A toy ingested fully offline (Claude unavailable, operator types `display_name` and leaves `tags` empty) will still generate sprites — the prompt template degrades to `"a {display_name}, <palette tokens>, <ACTION_PROMPTS[slot]>, 2D cartoon, simple shapes"` and the cartoon LoRA carries the rest.

## Sources

Cited inline above. Concentrated list:

- HF blog "SDXL in 4 steps with Latent Consistency LoRAs": https://huggingface.co/blog/lcm_lora
- LCM-LoRA SD 1.5 weights: https://huggingface.co/latent-consistency/lcm-lora-sdv1-5
- Diffusers PixArt-Σ docs (sub-8 GB pattern): https://huggingface.co/docs/diffusers/main/en/api/pipelines/pixart_sigma
- Felix Sanz, "PixArt-α with less than 8GB VRAM": https://www.felixsanz.dev/articles/pixart-a-with-less-than-8gb-vram
- ByteDance/SDXL-Lightning model card: https://huggingface.co/ByteDance/SDXL-Lightning
- Replicate sdxl-lightning-4step (latency reference): https://replicate.com/bytedance/sdxl-lightning-4step
- HF SDXL ONNX inference benchmarks: https://huggingface.co/blog/sdxl_ort_inference
- diffusers#9149 (Flux NF4 on 8 GB): https://github.com/huggingface/diffusers/issues/9149
- Stable Cascade announcement: https://stability.ai/news/introducing-stable-cascade
- Pablo Stanley Open Peeps (CC0 SVG kit): https://www.openpeeps.com/
- DiceBear Avataaars-style API: https://www.dicebear.com/
- ToonYou checkpoint: https://civitai.com/models/30240/toonyou
- designPixar LoRA: https://civitai.com/models/734883/designpixar
- Disney Pixar Cartoon Type B: https://civitai.com/models/75650/disney-pixar-cartoon-type-b
- HF textual-inversion training docs (per-toy embedding cost reference): https://huggingface.co/docs/diffusers/training/text_inversion
- h94/IP-Adapter HF (ViT-H weight sizes, current pipeline component): https://huggingface.co/h94/IP-Adapter

Non-authoritative blog references used for ballpark numbers: synpixcloud.com, promptingpixels.com, ucstrategies.com, maginative.com, antalpha medium summary. Treat the numbers as "right order of magnitude" — the local probe in step 1 above is the only measurement that matters for the go/no-go decision.
