# Illustrated, livelier adventure mode

**Tier:** 2
**Status:** Investigated
**Related:** [[01-toy-character-identity-consistency]], [[03-activity-scene-backgrounds]], [[07-visual-interest-text-step-cards]], [[08-personalized-storybook-illustrations]]

## What it is
Phase W shipped a dynamic adventure engine (`activities/adventure.py`): an
adventure is an activity whose steps are generated beat-by-beat (max 6) as the
child advances, hybrid Claude-online / offline-deterministic, climaxing in a
boss-fight beat. Today every beat is pure text — 1-3 sentences plus 2-3 choice
buttons, rendered through the kiosk StepCard's default text/fork path. The
operator's verdict: "good direction, needs development, more lively; text
blocks could be more visually interesting."

This topic is the imagery layer over that engine: generate a scene illustration
(and/or a character vignette) per beat so each step shows a picture of what is
happening, not just a paragraph. The boss-fight beat especially wants a hero-vs-
boss image. It sits at the intersection of scene backgrounds ([[03-activity-scene-backgrounds]])
and character consistency ([[01-toy-character-identity-consistency]]), applied
to a *non-deterministic, generated* step sequence.

## Why it matters
Child A (6, early reader) can parse the text but an illustration makes a beat feel
like a storybook rather than a quiz. Child B (4, pre-reader) cannot read the beat
at all — a picture is the only thing carrying the story for him, and image-based
choices ([[14-image-based-choice-buttons]]) become the mechanic that lets him
play unassisted. Failure modes: latency stalls (a child waits on a blank card
while the GPU grinds); incoherent characters across beats (the hero looks
different every screen, breaking the through-line — see [[01-toy-character-identity-consistency]]);
and style drift that makes the adventure look like a ransom note next to the
rest of the kiosk ([[02-kiosk-style-cohesion]]).

## When to apply
- Child starts an adventure (`activities.adventure`) and `boss_fights_enabled`.
- A beat advances and the next beat body is available.
- Boss climax beat (`kind == "boss_fight"`) — the highest-payoff single image.

## How to apply
The hard constraint is the **single uvicorn worker + single-worker GPU**
(`pipeline.py`: 4-step LCM, ~512px, default 120s timeout). The worker queue is
FIFO and serial; one SD call already runs seconds-to-minutes. Generating art
*synchronously per beat* would stall the child every advance — unacceptable.

How art keeps up with a dynamic engine: the engine produces one beat at a time
and beats build on prior choices, so the *next* beat's content is unknown until
the child clicks. Two viable patterns:
- **Latency-hiding prefetch.** When a beat with choices renders, enqueue art for
  the most-likely next beat(s) in the background while the child reads. Display
  text-first, swap the image in on WS arrival (the toy_actions worker already
  emits per-slot `done` envelopes — mirror that pattern for beat art). Accept
  that fast clickers see text-only beats; that is the graceful-degrade default.
- **Bounded pre-render.** For the offline-deterministic path, beats are
  byte-identical per `(seed, beat_index, history)`. A small fixed pool of
  reusable scene backdrops ([[03-activity-scene-backgrounds]]) keyed by theme +
  beat-index can be pre-rendered overnight and selected at runtime — no live GPU
  cost. This covers the common case; live gen is the enhancement.

Identity: reuse the IP-Adapter Plus cutout + seed-reuse approach from the toy
pipeline so the hero looks like the household toy across beats. Boss = the
cast's boss-role toy. Safety: the engine's prompts are already "warm, never
scary"; any image prompt must inherit the pipeline's negative prompt and the
general guardrails ([[04-content-safety-guardrails-primer]]). Prototype: add a
beat-art slot to the worker, hard-code one theme, render the boss beat only,
measure end-to-end latency on the operator's GPU before widening.

## References
- `src/toybox/activities/adventure.py` (beat engine, kinds, MAX_ADVENTURE_BEATS=6)
- `src/toybox/image_gen/pipeline.py` (SD 1.5 + LCM + IPA Plus, 4-step, 512px, 120s)
- `src/toybox/image_gen/worker.py` (single FIFO worker, WS `toy_actions` envelopes, supersede)
- `documentation/investigations/local-sd-kids-ux/plan.md` § Tech grounding
- `.claude/rules/frontend-ui.md` (kiosk dev-server / single-worker note)
- Memory: `project_phase_w_complete_2026-06-20.md` (W4 engine, W5 boss fights)
- Phase: Phase W (W4 dynamic adventure, W5 boss-fight climax)

## Open questions
- Prefetch hit-rate: how many of 2-3 choice branches must be pre-rendered to
  usually have art ready, given a serial GPU? Is one-branch speculative enough?
- Per-beat live gen vs a reusable theme-keyed backdrop pool — does the child
  notice repeated backdrops within a 6-beat run, or is novelty in the text+toy
  vignette enough?
- Does the online Claude beat path expose enough scene detail to prompt a
  faithful illustration, or does the image need its own scene-summary call?
- Boss-only illustration as a cheaper MVP — does one climax image deliver most
  of the "livelier" payoff for a fraction of the GPU budget?
