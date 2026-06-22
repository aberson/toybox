# Visual schedules & step-card thumbnails

**Tier:** 4
**Status:** Investigated
**Related:** [[07-visual-interest-text-step-cards]], [[15-vocabulary-concept-illustration]]

## What it is
A small per-step thumbnail strip ("filmstrip") rendered on the child kiosk so a
non-reader can see the activity as a sequence of pictures: what was done, what is
happening now, and roughly what comes next. This is the classic accessibility
"visual schedule" — a picture-based first/then/next board — applied to toybox's
running activity. Each thumbnail is a tiny cached image (the step's cast sprite,
a generated scene from [[07-visual-interest-text-step-cards]], or a concept icon
from [[15-vocabulary-concept-illustration]]) plus a clear "you are here" marker.

Today the kiosk shows only a bare text label `Step N` (see `StepCard.tsx`, the
`currentIndex` block). Phase G G4 deliberately dropped the "of N" denominator
because branching templates visit variable node counts — so a literal "3 of 7"
would lie about the path. A visual schedule sidesteps that: it shows the *path
walked so far* plus the *current* node, which is honest even when the total is
unknown.

## Why it matters
Child B (4, pre-reader) is the primary beneficiary: he cannot read `Step 3`, so the
only orientation cue he gets today is the body text being read aloud. A picture
filmstrip gives him a non-verbal "where am I / are we almost done" answer —
reducing the mid-activity disorientation that makes long scripts feel boring or
overwhelming. Visual schedules are a well-established aid for pre-readers and for
kids who need predictability.

Failure modes to avoid: (1) a *forward-looking* schedule that shows unvisited
branch steps would re-introduce the G4 "lying total" problem and spoil choices;
(2) thumbnails too small or too similar to distinguish (4-year-old can't tell two
near-identical sprites apart); (3) per-step on-demand generation adding latency on
the single-worker GPU; (4) a strip that competes with the body text and cast
sprites for the limited iPad-portrait vertical space the StepCard already fights
over (note the many `clamp(...)` font/gap rules).

## When to apply
- Kid is a pre-reader / early-reader (per-child via the Children tab — see
  [[12-per-child-personalization]] in topics.md). Off by default for fluent readers.
- Linear or mostly-linear activities where a *visited* sequence is meaningful.
- Longer scripts (the boredom/orientation payoff scales with step count).

## How to apply
Render a horizontal strip of N small (~48-64px) thumbnails above or below the
StepCard body, one per *visited + current* step, current marked (ring/scale/dot).
Prefer **reuse over generation**: the cast sprite for each step already exists
(`ToyActionSprite`, cached PNG/SVG per toy+slot), and scene/concept art from sibling
topics is cacheable per `step_template_id`. Generation should be a fallback only,
done at activity-propose time (batch, off the kid's critical path) and keyed/cached
so a re-entered activity never re-generates. On the single-worker GPU at 4-step LCM
(`pipeline.py`), never generate a thumbnail synchronously while the kid waits.

Prototype cheaply with **zero new generation**: add a `data-current-index`-driven
strip that reuses existing sprites and a generic "done" checkmark for past steps —
pure frontend, no pipeline work. Validate orientation payoff with Child B before
investing in per-step bespoke art. Gate behind a per-child flag.

## References
- `frontend/src/child/components/StepCard.tsx` (`Step N` label; `currentIndex`/`totalSteps`; cast sprite rendering)
- `frontend/src/child/components/NextStepButton.tsx`
- `documentation/plan/activity-loop.md` § State machine / Step shape / Lazy step insertion (G2)
- `src/toybox/image_gen/pipeline.py` (SD 1.5 + LCM, single-worker, caching)
- `.claude/rules/frontend-ui.md` (single uvicorn worker; iPad-portrait constraints)
- `documentation/investigations/local-sd-kids-ux/topics.md`
- Incident: Phase G G4 (dropped "of N" denominator for branching)

## Open questions
- Past-only strip vs. a first/then/next 3-slot board — which orients Child B better?
- For branching templates, can the strip show the chosen branch icon without
  spoiling the unchosen one?
- Does the strip fit iPad portrait alongside cast sprites + body, or must it
  replace the `Step N` text rather than add to it?
- Generic "done" marker vs. per-step thumbnail — is the bespoke art worth the
  batch-generation + caching complexity for a Tier-4 polish item?
