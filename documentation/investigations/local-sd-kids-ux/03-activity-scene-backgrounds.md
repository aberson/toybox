# Per-activity scene & background generation

**Tier:** 1
**Status:** Investigated
**Related:** [[01-toy-character-identity-consistency]], [[02-kiosk-style-cohesion]], [[06-illustrated-adventure-mode]], [[07-visual-interest-text-step-cards]]

## What it is
Generate a full-bleed illustrated backdrop that sits *behind* the kiosk step card,
scoped to the activity (or to individual steps). Today the kiosk renders the
`StepCard` — a translucent white card (`background: rgba(255,255,255,0.82)`) holding
the body text + cast sprites — floating over a flat per-persona gradient (Phase S).
The toy sprites themselves are 512px transparent PNG cutouts (`pipeline._run_pipeline_sync`
double-rembg's them to RGBA). Nothing in the play scene is *of a place*: a treasure
hunt, a wind-down story, and an elements quiz all share the same plain gradient. This
topic adds a generated scene image (a kitchen, a forest clearing, outer space) as the
layer the card and sprites compose onto.

## Why it matters
This is the single biggest "not boring" visual lever available. Sprite cutouts already
sit on transparency, so a backdrop is a pure additive layer — the cast literally stands
*in* the scene instead of on a gradient. For Child B (4, pre-reader) a recognizable place
carries narrative the text can't; for Child A (6) it raises production value toward the
storybook feel she gets from LOL-doll media. Failure modes: a busy/high-contrast backdrop
drowns the translucent card text (readability is already a tuned constraint — see the
`StepCard` font-size clamps); a backdrop that changes art style per step produces the
"ransom-note kiosk" that [[02-kiosk-style-cohesion]] warns about; and per-step on-demand
generation on one GPU stalls the kid mid-activity.

## When to apply
- Story/adventure intents (`request_story`, Phase W adventure beats) where place is narrative.
- Activities that already name a `{room}` slot (`activity-loop.md` § Parametric slot registry).
- NOT song/joke/reward steps that own their full surface, nor short quiz grids where a scene competes with the board.

## How to apply
**Pre-render is the default; on-demand is the exception.** The pipeline is single-worker,
single-GPU, ~4-step LCM, with a 120s per-call timeout (`pipeline.DEFAULT_TIMEOUT_SEC`) and
a FIFO `asyncio.Queue` worker that already serializes sprite jobs. A per-step on-demand
backdrop would queue behind sprite work and the kid would watch a blank scene resolve.

- **Pre-render a small scene library (recommended).** Generate a fixed set of backdrops
  (forest, kitchen, space, castle, undersea, bedroom) offline — the same overnight-batch
  posture used for toy sprites and the song corpus. Templates/activities reference a
  `scene_id`; the kiosk loads a static PNG via the existing `/api/static/images/...` mount.
  Zero runtime GPU cost, fully deterministic, easy to parent-preview. Backdrops are
  scenery (no characters) so [[01-toy-character-identity-consistency]] doesn't apply — but
  the LoRA/prompt suffix MUST match the sprite style ([[02-kiosk-style-cohesion]]) or the
  cast looks pasted on.
- **On-demand only for adventure** ([[06-illustrated-adventure-mode]]), where each beat is
  unique and can't be pre-baked. Gate it: generate at *approve* time (not per-step), cap to
  one backdrop per activity, and degrade to a library scene through the capability gate /
  breaker (`worker._run_one_body`) so a GPU-busy or offline host still renders something.
- **Prototype:** drop a fixed PNG behind `StepCard` (a `position: fixed` full-viewport layer
  with the card on top); dial card opacity until text passes the readability bar. Then wire
  a `scene_id` into a couple of templates pointing at hand-generated SD backdrops. Measure
  one LCM backdrop's wall-clock on the operator GPU before considering any runtime path.
- **Safety:** backdrops are scenery; reuse the existing `safety_checker=None` +
  negative-prompt posture, and because pre-rendered, every scene gets a one-time parent
  eyeball ([[04-content-safety-guardrails-primer]]).

## References
- `src/toybox/image_gen/pipeline.py` (LCM/4-step, 512px RGBA cutout, timeout)
- `src/toybox/image_gen/worker.py` (single FIFO worker, capability/breaker dispatch, static path)
- `src/toybox/image_gen/capability.py` (capability gate, breaker)
- `frontend/src/child/components/StepCard.tsx` (translucent card, gradient backdrop, cast sprites)
- `documentation/plan/activity-loop.md` § Step shape / Parametric slot registry
- `CLAUDE.md` § Gotchas (single uvicorn worker, image_gen_mode)
- `.claude/rules/frontend-ui.md`

## Open questions
- Per-activity scene vs per-step scene — does changing place mid-activity help or jar?
- Card readability over a generated backdrop: opacity tuning, or a blur/scrim layer?
- How big should the pre-rendered scene library be before it stops feeling repetitive?
- Could a scene be tinted/seeded to match the persona gradient so the two layers cohere?
