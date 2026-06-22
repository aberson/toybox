# Human-body learning game (organs/systems)

**Tier:** 2
**Status:** Investigated
**Related:** [[10-tap-grid-qa-reward-pattern]], [[09-elements-game-redesign]], [[12-per-child-personalization]]

## What it is
A second concrete instance of the tap-grid Q&A reward activity ([[10-tap-grid-qa-reward-pattern]]), themed on the human body instead of the periodic table. A child sees a body diagram (a "body board"), taps an organ or system position, answers a short question, and a correct answer reveals a reward — a song plus the organ filling in / lighting up on the board. It is the direct sibling of the elements game ([[09-elements-game-redesign]]): same mechanic, different subject, different art.

Today's elements activity is a linear 3-step intro/fact/hook template carrying an `element_id` per step and an auto `song` ending reward (see `request_activity.json` `meet_element_*` templates, Phase M/N). The body game reuses that exact shape — a per-organ template with an `organ_id` analog and a song reward — and adds the tap-a-position-on-a-board interaction that topic 10 generalizes. Proving the pattern works for a second domain is the payoff: it shows the board/Q&A/reward engine is reusable, not a one-off for chemistry.

## Why it matters
Kid-UX payoff: the body is intrinsically interesting to young children, and an illustrated, tappable board beats a wall of text. Child B (4, pre-reader, likes the periodic table) already enjoys the structured "meet a thing, hear a fact" loop; a body board with picture-first tap targets and audio questions extends that loop to a non-reader. Child A (6, early reader) can take harder questions and read short labels.

Failure modes: (1) anatomy illustrations that look clinical, gory, or uncanny — a hard child-safety/age-appropriateness risk for under-6s; (2) re-implementing the board engine instead of reusing topic 10's; (3) drifting away from the proven short template back into the "longer = less effective" trap that the elements redesign (topic 9) is explicitly reverting.

## When to apply
- After [[10-tap-grid-qa-reward-pattern]] exists as a reusable engine — build this on top, do not fork it.
- When the operator wants a second subject to validate that engine.
- For a science/biology play theme aimed at the existing `request_activity` / element-microgame surface.

## How to apply
- Board: a labeled body silhouette with a small fixed set of tap positions (heart, lungs, brain, stomach, bones, muscles) — far fewer cells than the periodic table, which suits a 4-year-old. Reuse topic 10's grid/board component; positions are an `organ_id` list mirroring `element_id`.
- Q&A + reward: one question per organ; correct answer triggers the same reward path the element template uses — `ending_step: {kind: song, auto: true}` plus a fill-in/light-up of the tapped organ.
- Anatomy art via the local SD pipeline: prefer friendly, simplified, cartoon-style organ illustrations, NOT realistic anatomy. The pipeline already pushes "2D cartoon, simple shapes, clean lines" with a negative prompt (`pipeline.py` `_build_prompt`, `DEFAULT_NEGATIVE_PROMPT`). Extend the negative prompt with `realistic, gore, blood, medical, dissection` and keep a parent-preview/allowlist gate (see [[04-content-safety-guardrails-primer]]). Treat organ art as a small fixed asset set — pre-render the ~6 organs once (offline batch), like Phase M's element sprites, rather than generating on demand. This sidesteps single-worker-GPU latency entirely at play time.
- Feasibility/effort: low-to-moderate IF topic 10's engine lands first; the body game is then mostly content (templates + ~6 pre-rendered images + questions). Per-child tuning (Child B picture-only vs Child A text labels) ties into [[12-per-child-personalization]].
- Prototype: hand-author one body template (heart) following the `meet_element_*` shape with an `organ_id`, hard-code one pre-rendered cartoon heart PNG, wire it to topic 10's tap board, and watch one child tap-answer-reward.

## References
- `src/toybox/activities/templates/branching/request_activity.json` — `meet_element_*` templates (intro/fact/hook + `element_id` + `song` ending reward); the shape this game parallels.
- `src/toybox/image_gen/pipeline.py` — `_build_prompt`, `DEFAULT_NEGATIVE_PROMPT`, cartoon-style 4-step LCM single-worker pipeline.
- `src/toybox/image_gen/models.py` — `ACTION_PROMPTS`, `GenerationContext` (pre-render asset model).
- `documentation/master-plan.md` — element cards / element-microgame = Phase M/N; rewards = Phase L; element-specific rewards = Phase Q.
- `documentation/investigations/local-sd-kids-ux/plan.md`, `topics.md` — scope, kids (Child A 6, Child B 4).

## Open questions
- Which organ set and question difficulty is right per age — confirm against real kids, not assumed.
- Are 6 cartoon organ illustrations recognizable enough to a pre-reader without labels? Prototype before committing.
- Does the body diagram itself (silhouette) come from SD, a static asset, or a simple drawn SVG? Static is safer and cheaper.
- Does this share enough with topic 10 to be pure content, or does the body board need a board-shape distinct from a grid (positions, not cells)?
