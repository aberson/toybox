# Coloring-page (line-art) mode

**Tier:** 1
**Status:** Investigated
**Related:** [[03-activity-scene-backgrounds]], [[14-image-based-choice-buttons]], [[12-per-child-personalization]]

## What it is
A generation mode that produces black-outline, white-fill line art — a "coloring page" — instead of the current flat-color cartoon sprite. The same SD 1.5 + LCM pipeline that draws a toy at `src/toybox/image_gen/pipeline.py` would emit clean uncolored outlines (a toy, a scene, an element) that a child then fills in. The novelty is the *interaction surface*, not just the render: a coloring page is only interesting if a kid can actually color it. Two surfaces are viable — an on-screen kiosk canvas (tap-a-region or freehand fill, fully on-device) and print-and-color (PNG to the parent's printer for offline play).

This is distinct from the three current `image_gen_mode` values (`cartoon` / `composite` / `claude_svg`); line-art is a candidate *fourth* output style, not a backend swap.

## Why it matters
Coloring is open-ended, low-pressure, and age-spanning: Child B (4, pre-reader) can scribble-fill without reading anything, and Child A (6) can color her own LOL-doll or a generated scene. It converts the pipeline from "watch a sprite appear" into "make something yourself" — the single biggest lever from passive to active play, and it pairs naturally with personalization ([[12-per-child-personalization]]): color *your* toy. Failure modes: outlines that aren't closed (on-screen flood-fill bleeds across the whole image); too much fine detail for a 4-year-old's tap accuracy; LCM at 4 steps producing gray smudges instead of crisp black lines; and a print path that depends on a printer the family may not have, so on-screen must be the primary surface.

## When to apply
- A child requests "draw / color / make" play, or a parent wants a calm offline activity.
- As an alternate render of an existing asset — color the toy, an activity scene ([[03-activity-scene-backgrounds]]), or an element.
- As a coloring variant of image-based choice thumbnails ([[14-image-based-choice-buttons]]).

## How to apply
Generation: the current pipeline has **no ControlNet wired** — only LCM-LoRA + IP-Adapter Plus (verified in `pipeline.py:_build_pipeline`). Two paths:
1. **Prompt-only (prototype first):** append "black and white line art, bold clean outlines, coloring book page, no shading, white background" and lean on the existing negative prompt (which already suppresses `smooth shading, gradient`). Cheapest — zero new model weights, same 4-step latency.
2. **ControlNet canny (higher fidelity):** generate or take a colored image, run canny edge-detection, regenerate as outlines. Adds a ControlNet model + an edge-detect preprocess; a real new dependency on a single-worker GPU, so defer until prompt-only proves the interaction.

Interaction: on-screen, render the PNG to a kiosk canvas and use a palette + flood-fill (HTML canvas `getImageData`/bucket-fill) — closed-outline quality decides whether tap-fill or freehand-brush is safer. Print path: expose the PNG for the parent to print. Latency: same as today (~4 steps); pre-render coloring pages into a small library rather than on-demand to avoid GPU contention. Safety: line art is *lower* risk than colored output. Prototype: flip the prompt suffix on one toy, eyeball outline closure, then build a throwaway canvas fill before committing to ControlNet.

## References
- `src/toybox/image_gen/pipeline.py` — pipeline, `_build_pipeline` (no ControlNet), `DEFAULT_NEGATIVE_PROMPT`, 4-step LCM at 512px.
- `src/toybox/image_gen/worker.py` — single-worker queue; `_remove_sibling_formats` format-per-slot rule.
- `src/toybox/image_gen/models.py` — `GenerationContext`, action slots.
- `.claude/rules/frontend-ui.md` — kiosk dev-server / port `:4000`.
- `documentation/investigations/local-sd-kids-ux/{plan,topics}.md` — scope, tech grounding.
- Kids profiles (memory): Child A 6, Child B 4.

## Open questions
- Does prompt-only line art yield *closed* outlines reliably at 4 LCM steps, or is ControlNet canny mandatory for flood-fill to work?
- On-screen flood-fill vs freehand brush — which suits a 4-year-old's tap accuracy?
- Persist a child's colored result (save/show-off), or ephemeral?
- Is print-and-color worth a UI surface, or on-screen only for v1?
