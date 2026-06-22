# Reusable tap-grid + Q&A + reward-reveal game pattern

**Tier:** 2
**Status:** Investigated
**Related:** [[09-elements-game-redesign]], [[11-human-body-learning-game]], [[12-per-child-personalization]]

## What it is
A single generalized activity shape that the elements game (topic 09) and the
human-body game (topic 11) are both instances of: a board of tappable
positions (a periodic table, a body diagram, a map) where each tap opens a
short Q&A; a correct answer triggers a reward-reveal — a song plays and the
tapped cell "fills in" with its art. The board is the navigation surface, the
Q&A is the learning gate, and the reveal is the payoff loop that pulls the kid
to the next cell.

The data primitives for this already exist in the offline template shape, just
not unified. `_schema.json` already carries: `id`/`next`/`choices` for grid
navigation, `question` + `expected_answer` for the Q&A gate (R3 parent-tap gate;
W3 auto-grade against transcript), `element_id` for a board-position card, and
`ending_step: {kind: "song", auto: true}` for the reward-reveal. The "pattern"
is recognizing these are one composable mechanic, not three features.

## Why it matters
This is the highest-leverage reuse play in the set. One template shape →
many subject boards with no new engine work: periodic table for Child B (4,
likes the periodic table), body systems, world maps, letter/number grids for
early-readers, shape boards. Each new subject is a content data file, not a
code phase. Without the META view, topics 09 and 11 each get bespoke wiring
that drifts; with it, a fix to grading or reveal art helps every board.

The reveal loop is also the antidote to the operator's "elements got LESS
effective when made LONGER" finding (topic 09): keep each Q&A to ~3 steps,
push variety into board breadth (many cells), not step depth.

## When to apply
- Subject decomposes into a fixed set of discrete, namable cells (elements,
  organs, countries, letters).
- A short factual question per cell has a checkable answer (`expected_answer`).
- The payoff is collection/completion ("fill the whole board").
- Pre-reader subjects (Child B): cell art carries meaning; answer can be spoken
  and W3-graded so no reading is required.

## How to apply
Data shape per cell: a board-position id, an SD-rendered fill-in image, 1-3
question steps (`question` + `expected_answer`), and a reward `ending_step`
(`kind: "song", auto: true`). The board itself is a kiosk grid component that
maps cell id → `element_id`-style corpus entry. Where SD plugs in (two seams):
(1) the **fill-in reveal art** per cell — pre-rendered overnight batch on the
single-worker GPU (a fixed, finite cell set, so no on-demand latency); the
cartoon LoRA + transparent-PNG path (`pipeline.py`) already produces exactly
this asset. (2) optionally the **blank board frame** as one backdrop image
(topic 03). Effort: medium — the grading + reveal logic is largely Phase R/W;
the new work is a generic board component and a board-definition file format.
Prototype: ship ONE subject (reuse the existing element corpus) as a tap-grid
end-to-end, then add body systems as a second data file to prove zero-code reuse.

## References
- `src/toybox/activities/templates/_schema.json` (`question`, `expected_answer`,
  `element_id`, `choices`/`next`, step `kind`)
- `src/toybox/activities/templates/branching/request_activity.json` (the
  `meet_element_*` templates: intro/fact/hook + `ending_step` song)
- `src/toybox/image_gen/pipeline.py` (cartoon LoRA + IPA + transparent PNG)
- `documentation/plan/activity-loop.md` § Step shape, § Lazy step insertion (G2)
- `documentation/investigations/local-sd-kids-ux/plan.md` (grounding bullets 09-11)

## Open questions
- Board state persistence: where does "which cells are filled" live per child?
  No `activity_steps` column tracks board completion across sessions.
- Reveal art coherence: pre-rendered cell art must share one style (topic 02)
  or the filled board looks like a ransom note.
- W3 auto-grading reliability for a 4-year-old's spoken answer — is parent-tap
  the safer default for pre-readers?
- Is a board a new top-level activity kind, or a thin kiosk view over a branching
  template's `choices` graph? The latter reuses more but caps grid size.
