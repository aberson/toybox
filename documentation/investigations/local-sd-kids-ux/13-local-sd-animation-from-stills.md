# Local-SD animation from stills

**Tier:** 2
**Status:** Investigated
**Related:** [[16-persona-avatar-variety]], [[06-illustrated-adventure-mode]], [[01-toy-character-identity-consistency]]

## What it is
The operator's question: still generation is good — could a long overnight run plus careful step-by-step generation produce *full-on animation*? This file surveys ways to make a moving sprite out of toybox's static `.png` cartoon outputs **without** a video-diffusion model: frame-sequence batches, sprite sheets, img2img chains, ControlNet pose sequences, and frame interpolation (RIFE/FILM). It is exploration, not a build plan.

These differ from the two prior failures. Phase U's `AnimateDiff` (`animate.py`) generated motion *from scratch* and lost toy identity — sprites looked nothing like the toy. Phase V's `StableVideoDiffusion` (SVD) idle loop preserved identity in the compare harness but in production every generated `idle.webp` came out **garbled** and was removed (`4d592ae`, 2026-06-21). The current shipped animation is a model-free CSS `idleBob` keyframe in `ToyActionSprite.module.css`. So "true" diffusion animation is still open — but every approach below treats the *existing good still* as the anchor, not a fresh video generation.

## Why it matters
Movement is the single biggest "alive vs. cardboard" lever for Child A (6) and Child B (4). The CSS idle-bob is a stopgap: it's one shared motion, no per-pose character. Real per-action motion (a wave that waves, a jump that jumps) would lift the kiosk noticeably. Failure modes are well-attested here: identity drift (AnimateDiff), garbled frames (SVD), flicker/jitter, and motion-sensitivity (the idle-bob is deliberately tiny and `prefers-reduced-motion`-gated for exactly this reason — any new motion must inherit that discipline).

## When to apply
- The kiosk feels static and the operator wants more life than CSS bob gives.
- An overnight GPU window exists (single-worker; server must be stopped — the batch and live pipeline both load CUDA and conflict, per Phase U U3).
- A new still asset already exists to animate (these are post-processors on a good `.png`, never a replacement for it).

## How to apply
Candidate approaches, cheapest first:

- **Sprite sheet from a few keyframes + CSS `steps()`.** Generate 4–8 stills per action (img2img off the base sprite at low denoise so identity holds), tile into one `.png`, drive with `animation-timing-function: steps(N)`. Pure CSS playback, zero new runtime model, transparent-PNG-native. Best feasibility / best prototype-first.
- **img2img chain (Deforum-style).** Feed frame N back as the img2img init for frame N+1 at low strength. Coherent *because* each frame is anchored to the last still — the opposite of AnimateDiff's from-scratch motion. Risk: slow drift over many frames; keep chains short (≤8).
- **ControlNet pose sequence.** Drive a fixed sprite through OpenPose/canny skeletons per frame for deliberate, named motion (the thing SVD *can't* do — it has no pose control). Heaviest: needs a ControlNet model + pose source, not currently in the pipeline.
- **Frame interpolation (RIFE / FILM).** Generate 2–3 sharp keyframes, interpolate the in-betweens on CPU/GPU. Cheap smoothing layer; pairs with any generator above. New dependency.

Overnight-batch realism: per Phase U, ~140 stills ran in ~75 min on this single-worker GPU. A frame-sequence at 4–8× that volume is plausible overnight but only if each frame stays at the 256px animate dimension and the chain is short. Prototype path: extend `scripts/batch_animate.py` with a `--approach spritesheet` job that does low-denoise img2img off existing `idle.png`, tiles, and writes one sheet — measurable, model-free, no kiosk change until it looks right.

## References
- `src/toybox/image_gen/animate.py` — AnimateDiff/AnimateLCM wrapper (Phase U; identity-loss failure).
- `src/toybox/image_gen/pipeline.py` — SD 1.5 + LCM 4-step + IP-Adapter Plus still pipeline (anchor stills).
- `frontend/src/child/components/ToyActionSprite.module.css` — shipped CSS intro keyframes + `idleBob` (model-free current state).
- `documentation/plan/archive/phase-u-plan.md` — AnimateDiff batch; A/B/C compare; overnight-batch timing.
- `documentation/plan/awaiting-uat/phase-v-plan.md` — hybrid SVD idle + CSS slot-entry design.
- Incident: SVD `idle.webp` garbled, removed in commit `4d592ae` (2026-06-21).
- `MEMORY.md` → `project_phase_u_complete_2026-06-07`, `project_phase_v_status_2026-06-07`.

## Open questions
- Does low-denoise img2img off `idle.png` hold identity across 8 frames, or does it drift like SVD garbled? Prototype the sprite-sheet path first.
- Is a 4–8-frame `steps()` sprite sheet "alive enough" to beat the CSS idle-bob, or is the perceived gain too small to justify the GPU/storage cost?
- Could per-action sheets reuse the seed-pinning from [[01-toy-character-identity-consistency]] to keep all frames on-model?
