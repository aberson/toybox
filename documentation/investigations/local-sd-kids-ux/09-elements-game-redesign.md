# Interactive "elements" game redesign

**Tier:** 2
**Status:** Investigated
**Related:** [[10-tap-grid-qa-reward-pattern]], [[11-human-body-learning-game]], [[12-per-child-personalization]]

## What it is
Child B (4, pre-reader) loves the periodic table. Phase M shipped an `element_microgame` template shape plus an element corpus (`element_corpus.py` / `data/elements/elements.json`), per-element sprites/cards keyed by `element_id` (e.g. `he-2`, `ne-10`), and a periodic-table fallback asset. The current play templates (in `request_play.json` / `request_activity.json`) thread one element per branching story: intro → a couple of forks → an ending. The operator's finding is blunt: making the elements template *longer* made it *less* effective. The redesign reverts to a short loop and swaps the long narrative for a spatial mechanic.

The proposed mechanic, in three steps: (1) show a **blank periodic table**; (2) the child **taps an element position**; (3) **answer a question (or two)** about that element — correct answer **plays a song and fills the tile in** with the existing element sprite/card. This is a concrete instance of the reusable board pattern generalized in [[10-tap-grid-qa-reward-pattern]].

## Why it matters
A 4-year-old pre-reader's attention is spatial and rhythmic, not linear-textual. A long branching script asks a non-reader to sit through narration that a parent must voice; each added beat is more reading and more waiting before the payoff. "Longer = worse" because the reward (the song, the reveal) gets pushed further from the tap. The blank-table mechanic front-loads agency (tap whatever you want) and shortens the loop to one decision and one reward, which is exactly the cadence the existing Phase L per-activity reward types and the Phase Q 1:1 `element_id`→reward mapping were built to feed. The filling-in of the board also gives a visible, accumulating sense of progress — a collection to complete — which longer prose cannot.

## When to apply
- Activity targets element/periodic-table content and the child is a pre- or early reader.
- A template's "effectiveness" complaint is really a length complaint (narration outweighs interaction).
- You have a finite, enumerable set with stable ids and matching sprites (elements qualify; see [[11-human-body-learning-game]] for the organ-system parallel).

## How to apply
Keep generation cheap by **reusing what Phase M already rendered**. The element sprites/cards exist and are keyed by `element_id`; the reveal just composites the existing sprite onto the tapped tile — no per-play SD call. The blank-table grid is static UI over the periodic-table fallback layout. SD's role is *additive and optional*: pre-render (overnight, single-worker GPU) any missing element art or a celebratory burst behind a correct reveal, never on the tap's critical path. This sidesteps the single-worker GPU latency problem entirely.

Question content can come from the corpus (`fun_fact`, `color_description`, `family`, `age_band`) so difficulty bands to the child — Child B gets "what color glows?" picture-choice questions, Child A gets a name/symbol prompt (ties to [[12-per-child-personalization]]). Reward = song via the Phase L reward type, selected by the Phase Q mapping.

Prototype: build the three-step `element_microgame` against ~6 noble-gas/halogen entries already authored in `request_play.json`, render the grid + tap + one picture-choice question + sprite-fill + song, and put it in front of Child B. Measure completed taps per session, not script depth.

## References
- `src/toybox/activities/element_corpus.py` (Element model, `element_id` shape, `age_band`/`family`/`fun_fact`)
- `src/toybox/activities/templates/branching/request_play.json` (existing per-element story steps; `element_id`, `action_slot`)
- `src/toybox/image_gen/pipeline.py` (single-worker GPU, 4-step LCM — why reuse over per-tap gen)
- `documentation/investigations/local-sd-kids-ux/topics.md`, `plan.md`
- Memory: `project_phase_m_autonomous_block_2026-05-18.md` (element cards/sprites), `project_phase_q_planning_2026-05-19.md` (1:1 mapping), `project_phase_l_shipped_2026-05-17.md` (reward types), `project_kids_profiles.md`

## Open questions
- Whole 118-tile table vs a per-session subset (a row/family) — does a full blank board overwhelm a 4-year-old?
- Tap-anywhere (child-led) vs prompt-a-target (guided) — which sustains more taps?
- Does the song reward saturate after a few reveals, and should it rotate via the Phase Q mapping?
- Are element sprites complete enough to fill any tapped tile, or does the fallback asset need to cover gaps gracefully?
