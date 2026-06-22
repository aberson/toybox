# Vocabulary & concept illustration (pre-readers)

**Tier:** 3
**Status:** Investigated
**Related:** [[14-image-based-choice-buttons]], [[17-visual-schedules-step-thumbnails]], [[12-per-child-personalization]]

## What it is
Show a small generated picture for a word so a pre-reader can "read" it by
recognising the image. The natural mount point already exists: Phase K's K9
`ClickableText` wraps every visible step body in word-level `<span>`s and, on
tap, speaks the single tapped word via the persona TTS profile
(`frontend/src/child/components/ClickableText.tsx`). This topic extends that
same tap from *hear the word* to *see the word* — tapping "rocket" pops a
cartoon rocket beside (or above) the text.

Each word maps to one cached ~512px illustration. The toybox SD pipeline
already produces exactly this shape of asset: a single transparent-background
PNG (`src/toybox/image_gen/pipeline.py`). The difference from action sprites is
that a vocabulary picture has no toy-identity requirement — it is a generic
concept icon, so the IP-Adapter reference-image path is unnecessary and could
be skipped for these calls.

## Why it matters
Child B (4, pre-reader) is the primary beneficiary: text on a step card is
inert to him today, and tap-to-hear only helps if he already knows the word
exists. A tap-to-see picture gives an independent, non-audio decoding path and
makes the body text a play surface instead of a wall. Child A (6, early reader)
gets a confirmation/word-association aid.

Failure modes: a wrong or ambiguous illustration mis-teaches (an SD "bat" that
renders an animal for "baseball bat"); abstract words illustrate poorly (see
below); latency on the single-worker GPU turns a tap into a stall; and unbounded
generation lets a kid paint arbitrary nouns into the model with no parent
preview.

## When to apply
- Word-tap surfaces where a pre-reader is present (kiosk step bodies, choice
  labels — overlaps [[14-image-based-choice-buttons]]).
- Concrete, picturable nouns drawn from a finite catalog vocabulary.
- A child profile flagged pre-reader (ties to [[12-per-child-personalization]]).
- NOT live free-text: only words that already appear in vetted templates.

## How to apply
- **Word -> cached illustration.** Build a vocabulary -> PNG map keyed by the
  normalised lemma. On first need, generate once; thereafter serve from disk.
  The template/catalog vocabulary is finite (the branching catalog is fixed
  text), so this is a bounded pre-render batch, not an on-demand load — run it
  overnight like the existing sprite/song batches.
- **Noun vs abstract.** Concrete nouns (rocket, dog, apple) render well at 4-step
  LCM. Abstract verbs/concepts (share, before, brave, because) do not — SD has no
  reliable visual for them. Restrict v1 to a curated concrete-noun allowlist;
  hand abstract words back to the existing tap-to-hear path rather than emitting a
  misleading picture.
- **Tie to clickable-words.** Reuse the K9 token model: add an optional
  illustration lookup to the word `<span>`'s `onClick` so tap can speak AND/OR
  reveal. Gate behind a new parent flag alongside the K9 flags already drilled
  through `App -> StepCard`.
- **Feasibility/effort.** Moderate. Generation is already solved; the work is
  the vocabulary map, an allowlist, a cache-serve route, and a kiosk reveal
  affordance. Drop IP-Adapter for these calls to simplify.
- **Prototype.** Hand-pick 20 concrete nouns from one intent's templates,
  batch-generate PNGs offline, and wire a static `{word: png}` map into
  `ClickableText` behind a flag. Watch Child B tap. No live GPU in the loop.

## References
- `src/toybox/image_gen/pipeline.py` (SD 1.5 + LCM + IP-Adapter; 512px PNG).
- `frontend/src/child/components/ClickableText.tsx` (K9 word-tap tokenizer).
- `frontend/src/child/components/StepCard.tsx` (ClickableText mount + flag threading).
- `documentation/investigations/local-sd-kids-ux/plan.md` (tech grounding, kids).
- `project_phase_k_complete_2026-05-16.md` (K9 click-to-read provenance).

## Open questions
- Where does the picture render — inline popover, side rail, or persona-area?
  Inline risks reflowing the step body mid-read.
- Does a parent need to approve each vocabulary image before a kid sees it, or
  is the concrete-noun allowlist sufficient safety (see general primer, topic 04)?
- How wide is the real catalog vocabulary after lemmatisation — does the
  finite-batch claim hold at 1000+ templates, or is it thousands of distinct nouns?
- Should the same picture double as a choice-button thumbnail to amortise the
  generation across [[14-image-based-choice-buttons]]?
