# Personalized storybook illustrations

**Tier:** 2
**Status:** Investigated
**Related:** [[01-toy-character-identity-consistency]], [[06-illustrated-adventure-mode]], [[12-per-child-personalization]], [[03-activity-scene-backgrounds]]

## What it is
Illustrated story beats for the `request_story` intent, where the characters are
the household toys. Each `request_story` template is a short branching script
(see `request_story.json`: a title, role/theme slots, 5-7 `steps`, an optional
`ending_step`). Every step already carries an `action_slot` (one of the 10
`ACTION_SLOTS` in `image_gen/models.py` — `thinking`, `looking`, `cheering`,
`waving`, `confused`, ...). The proposal here: render a picture-book illustration
per beat — the kid's own toy, in the pose the beat calls for, in front of a
scene that matches the story text — so a told story becomes a read-along
storybook.

The pieces exist. The SD 1.5 + cartoon-LoRA + IP-Adapter Plus pipeline already
turns a toy reference photo into a ~512px transparent-PNG sprite per action slot
(`pipeline._run_pipeline_sync`). A storybook illustration is that same sprite,
re-posed per beat, composited over a backdrop (topic 03) and shown beside the
step text instead of bare prose (topic 07).

## Why it matters
Seeing *your own toy* as the hero is the highest-affinity payoff in the whole set
— it converts a generic story into "a book about my LOL doll / my dragon." For
Child A (6, early reader) the picture supports the words; for Child B (4, pre-reader)
the picture *is* the story. Failure modes: the toy looks different in each beat
(breaks the "it's the same character" illusion — this is why topic 01 is a hard
dependency), the art style lurches between beats (topic 02), or generation
latency stalls the read-along so the kid loses the thread.

## When to apply
- A `request_story` activity is proposed/approved (intent-gated, not all play).
- The story has named character roles bound to real toys (`required_roles`,
  e.g. `friend`) — those are the toys to render.
- Pre-render is feasible because the full beat list is known at approval time.

## How to apply
Pre-render, do not generate on-demand mid-story. A story is a fixed beat list at
approval time, and the single-worker GPU queue (`ImageGenWorker`, FIFO, one job
at a time) cannot keep up with a kid tapping "next." On approve, enqueue one job
per beat: reuse the bound toy's reference photo as `ip_adapter_image`, set
`slot = step.action_slot`, and **reuse one fixed seed across all beats** so
identity holds (topic 01). At 4-step LCM, ~512px, a beat is on the order of a
few seconds; a 6-beat story pre-renders in well under a minute on the warm cached
pipeline — comfortably inside the parent's approve→hand-to-kid gap. Branch beats
(`choices`/`next` forks) must render *both* targets, since either may be taken.

Scenes: the per-beat backdrop is topic 03's job; the storybook layout (sprite +
text panel) is topic 07's. This topic owns the per-beat *character* render and
the seed/identity discipline that ties them together.

Safety: stories are template-authored (curated `request_story.json` text), so the
prompt surface is bounded — far safer than free-text. Keep the existing negative
prompt and `safety_checker=None` posture; the human-in-the-loop is the parent
approving the story before the kid sees it.

Prototype: pick `request_story_soak_dragon_01`, bind `friend` to a real toy, and
batch-generate one sprite per `action_slot` in the beat list at a fixed seed via
the F2 CLI / stub seam. Eyeball identity drift beat-to-beat before wiring any UI.

## References
- `src/toybox/image_gen/pipeline.py` (IP-Adapter `ip_adapter_image`, 4-step LCM, seed)
- `src/toybox/image_gen/models.py` (`ACTION_SLOTS`, `GenerationContext`)
- `src/toybox/image_gen/worker.py` (single-worker FIFO queue, per-job seed/supersede)
- `src/toybox/activities/templates/branching/request_story.json` (beat/`action_slot`/role shape)
- `documentation/plan/activity-loop.md` § Step shape, § State machine
- `documentation/investigations/local-sd-kids-ux/plan.md` (tech grounding, kids)

## Open questions
- Per-beat scene backdrop vs one consistent setting for the whole book — does a
  changing backdrop help immersion or just add latency and style drift?
- Should the bound-toy → role mapping be auto (persona age range) or a parent
  pick at approval, like `child_ids`?
- Branch forks double the render count; is pre-rendering both targets worth it,
  or render the taken branch lazily during the read?
- Does fixed-seed-across-beats over-constrain pose variety (topic 01's open
  tension — identity vs pose collapse at higher IP-Adapter scale)?
