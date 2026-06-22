# Consistent toy-character identity across scenes

**Tier:** 1
**Status:** Investigated
**Related:** [[02-kiosk-style-cohesion]], [[03-activity-scene-backgrounds]], [[06-illustrated-adventure-mode]], [[08-personalized-storybook-illustrations]]

## What it is
Keeping a household toy looking like *itself* across every image the local SD pipeline generates — the same plush across 10 action sprites, across a storybook's beats, across adventure scenes. Toybox already does single-image identity via IP-Adapter Plus: `_run_pipeline_sync` runs a rembg cutout of the parent-uploaded photo, then passes that cutout as `ip_adapter_image` with `set_ip_adapter_scale(0.6)`, so the reference image (not just `display_name` + tags) carries identity/colour conditioning. The open problem for richer kids' UX is *multi-image* coherence: when one toy must appear in many generated frames, each frame is an independent job with an independent random seed, and nothing pins the toy's appearance from frame to frame.

## Why it matters
Identity consistency is load-bearing for storybook ([[08-personalized-storybook-illustrations]]) and illustrated adventure ([[06-illustrated-adventure-mode]]) — a story whose hero changes colour, shape, or face between pages reads as broken to a kid. Child A (6) and Child B (4) recognise *their* toy; an off-model render undoes the "that's my toy!" payoff that justifies on-device generation at all. Failure modes: drift in palette/proportions between frames, the IPA cutout failing on a busy photo so identity collapses to the text prompt, and pose prompts (`ACTION_PROMPTS`) overpowering identity when IPA scale is too low.

## When to apply
- Any feature that renders the SAME toy in MORE THAN ONE generated image (storybook, adventure beats, scene backgrounds with the toy composited in).
- When a kid will see two images side-by-side or in sequence and is expected to read them as one character.
- NOT needed for one-off single-sprite generation — the existing per-call IPA already handles that.

## How to apply
Patterns on the current SD 1.5 + LCM + IPA setup, cheapest first:

- **Reuse the same reference cutout for every frame.** The toy's committed photo is already the IPA conditioning image; feed the identical `ip_adapter_image` to all frames in a set. This is free — it's how the 10 action slots already work.
- **Pin the seed across a set.** Today each job draws a fresh random seed (`_fresh_seed`, 63-bit, stored per row). For a coherent multi-frame set, derive one base seed for the set and reuse it (or `base + frame_index`) so latent structure stays stable. Low effort: thread a caller-supplied seed through `generate_action` (the param already exists).
- **Raise IPA scale for story/scene work.** 0.6 is tuned for action sprites where pose must win. A higher per-call scale (e.g. 0.7-0.8) tightens identity at the cost of pose flexibility; the Phase P plan flags exactly this trade and names ControlNet-OpenPose as the escape hatch if scale alone can't hold both.

Latency/throughput: single-worker GPU, FIFO queue, ~4-step LCM at 512px — a storybook of N beats is N sequential ~few-second jobs. Pre-render sets in a batch job, never on the kiosk's critical path. Safety: same `safety_checker=None` posture as today, so identity work inherits the guardrail story in [[04-content-safety-guardrails-primer]] — no new exposure. Prototype: pick one ingested toy, generate the 10 action slots twice with (a) random seeds and (b) one pinned seed + fixed cutout; eyeball whether the pinned set reads as one character.

## References
- Code: `src/toybox/image_gen/pipeline.py` (IPA load, `set_ip_adapter_scale`, `ip_adapter_image`, `_build_prompt`)
- Code: `src/toybox/image_gen/worker.py` (`_fresh_seed`, per-slot job dispatch, per-job seed persistence)
- Code: `src/toybox/image_gen/models.py` (`ACTION_PROMPTS`, `GenerationContext`)
- `documentation/plan/awaiting-uat/phase-p-plan.md` § Design Decisions (IPA Plus, `IP_ADAPTER_SCALE`, ControlNet-OpenPose escape hatch)
- `project_phase_p_planning_2026-05-18.md` (Phase P scope; SD 1.5 vs SDXL distinction)
- `project_kids_profiles.md` (Child A / Child B recognition payoff)
- Incident: Phase P (IPA replaces hex-token identity kludge); Phase P P7/P7b (scale tuned, pinned at 0.6)

## Open questions
- Does seed-pinning meaningfully improve cross-frame coherence under IPA, or does the cutout dominate enough that seed barely matters? Needs an A/B prototype.
- Best IPA scale for story/scene (identity-first) vs sprite (pose-first) work — one global constant, or per-feature override?
- For backgrounds where the toy is composited rather than diffused in, is consistency better solved by reusing one rendered sprite than by re-generating? Overlaps [[03-activity-scene-backgrounds]].
- Would per-toy LoRA training (the deferred "Custom LoRA per toy" note) give stronger multi-scene identity than IPA, and is it feasible overnight on the single-worker GPU?
