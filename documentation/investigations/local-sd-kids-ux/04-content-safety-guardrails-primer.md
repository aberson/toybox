# Content-safety guardrails — general primer

**Tier:** 1
**Status:** Investigated
**Related:** [[03-activity-scene-backgrounds]], [[06-illustrated-adventure-mode]], [[08-personalized-storybook-illustrations]]

## What it is
A layered defense-in-depth pattern for keeping a local image-generation pipeline from ever showing a child unsafe, off-tone, or disturbing imagery. No single technique is reliable; the industry approach stacks several so a miss at one layer is caught at the next. The layers, roughly input → model → output → human:

1. **Prompt-side controls** — negative prompts, prompt allowlists/blocklists, and template-constrained inputs that never let raw free text reach the diffuser.
2. **Model choice** — picking a checkpoint/LoRA whose training distribution is already tame (cartoon/illustration models trained without explicit content), since the base distribution is the strongest single lever.
3. **Output classifiers** — a NSFW/safety classifier (e.g. an "image safety checker" CLIP-style model, or nudity/violence detectors) run on the generated pixels, gating display.
4. **Human-in-the-loop** — a parent preview/approval step before any new image reaches the kiosk.

## Why it matters
The kiosk audience is Child A (6) and Child B (4); a single disturbing frame is a real harm, not a cosmetic bug. Generative models fail in ways unit tests cannot see: an innocuous prompt plus an unlucky seed can yield body-horror, gore-adjacent shapes, or uncanny faces. Failure modes to design against: prompt injection via toy/persona names, distribution leakage from the base model, classifier false-negatives, and silent regressions when a model or prompt template changes. Toybox's current pipeline runs with `safety_checker=None, requires_safety_checker=False` (`src/toybox/image_gen/pipeline.py`) — deliberate, because the stock SD checker is lossy and the cartoon checkpoint plus a defensive negative prompt are the present guard. That leaves the output-classifier and parent-preview layers as the largest unbuilt safety headroom.

## When to apply
- Any path that renders a *newly generated* image to the child surface (scene backgrounds, adventure beats, storybook illustrations).
- Whenever free-form or semi-free text (child name, toy name, story prompt) can influence a prompt.
- Whenever the model, LoRA, or prompt template changes — re-validate, don't assume parity.
- Not needed for already-approved, cached sprites that a parent has seen.

## How to apply
**Negative prompts (in place today, cheap).** The pipeline already passes `DEFAULT_NEGATIVE_PROMPT` banning photorealism/text/etc. Extend with safety tokens (nsfw, gore, blood, scary, realistic-face). Zero latency cost; weakest layer alone.

**Model choice (free, structural).** A cartoon/illustration checkpoint is itself a safety control — it cannot easily render what it never learned. Already the default `cartoon` mode.

**Prompt allowlists (low effort).** Toybox already constrains prompts via `_build_prompt` + fixed `ACTION_PROMPTS` slots, so the diffuser never sees raw child text — this is a strong, underrated guard. Generalize: keep user text out of the prompt, or pass it through a denylist/normalizer first.

**Output classifier (highest payoff, moderate effort).** Add a post-generation gate: run a small CLIP-based safety classifier (or `nudenet`-class detector) on the PNG before persisting; block + regenerate or fall back to `composite` on a hit. On the single-worker GPU it adds one short inference per image (tens of ms to ~1s) — acceptable for pre-render, noticeable on-demand. Prototype: load the classifier in `_run_pipeline_sync` after step 4, score, and reject above a threshold.

**Parent preview (strongest, highest UX cost).** A human-in-the-loop review queue before any new image hits the kiosk — mirrors the existing parent-approval model for activities. Best combined with pre-rendering (see [[03-activity-scene-backgrounds]]).

Recommended stack for toybox: keep negative prompt + cartoon model + template allowlist, add an output classifier as the first new layer, reserve parent preview for on-demand/novel imagery.

## References
- `src/toybox/image_gen/pipeline.py` (`DEFAULT_NEGATIVE_PROMPT`, `safety_checker=None`, `_build_prompt`)
- `src/toybox/image_gen/models.py` (`ACTION_PROMPTS`, `ACTION_SLOTS`, `GenerationContext`)
- `documentation/investigations/local-sd-kids-ux/plan.md` § Tech grounding
- `documentation/master-plan.md` (Stack: SD 1.5 + LCM-LoRA; image-gen invariants)
- Memory: `project_kids_profiles.md` (Child A 6, Child B 4)

## Open questions
- Which output classifier balances recall on cartoon-style harm vs. single-GPU latency? CLIP-safety vs. nudenet vs. a small fine-tune?
- Threshold tuning: false-positives waste a generation slot; false-negatives reach a child. What's the acceptable operating point, and does it need per-age tuning?
- Is parent preview worth the UX friction for high-volume surfaces (per-step backgrounds), or only for novel/storybook imagery?
- Does prompt injection via toy/persona display names need explicit sanitizing, or does the template structure already neutralize it?
