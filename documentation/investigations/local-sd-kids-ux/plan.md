# Investigation plan — Local-SD → kids' UX

## Goal
Produce a tiered reference set on **ambitious ways to use toybox's on-device
local Stable-Diffusion pipeline (generated imagery + animation) to make the
child experience more interesting** — focused on the areas the operator finds
"a little boring." One 350-600-word investigation file per topic (17 total),
cross-referenced, grepped later. This is exploration/reference, NOT a build
plan — each file ends with open questions, not a commitment.

## Scope (operator-set)
- **In scope:** Play (non-transcription) activities + the **Child Kiosk**
  ("functionally good, just needs more"). Operator is open to anything here.
- **Out of scope (working well — don't propose reworking):** Kids & toyboxes
  management, Settings, Play-Transcription (passive listening → suggestions).
- **Opportunity:** the **Children tab is underused** → per-child personalization.
- Dropped seed: reward/sticker art.

## Tech grounding (the local SD pipeline)
- SD 1.5 + cartoon LoRA + IP-Adapter Plus + rembg cutout; **single uvicorn
  worker**, single-worker GPU; 4-step LCM; ~512px transparent PNG from a
  reference photo. Code: `src/toybox/image_gen/{pipeline,worker,composite,models}.py`.
- Three `image_gen_mode` values: `cartoon` (local SD, default) / `composite`
  (Tier C fallback) / `claude_svg` (Claude SVG, opt-in, rate-limited best-effort).
- Kiosk applies CSS animation to sprites (`frontend/src/child/components/
  ToyActionSprite.module.css` — intro animations + a looping idle-bob).
- Prior animation attempts: AnimateDiff abandoned (Phase U); SVD animated
  `.webp` produced garbled output and was removed (2026-06-21). True animation
  is an open question (topic 13).
- Every investigation must weigh: **kid-UX payoff, feasibility on the current
  SD setup, effort, latency/throughput on a single-worker GPU, child-safety/
  age-appropriateness, and how to prototype.**

## Per-agent grounding (each sub-agent reads, for real — no invented refs)
- `toybox/CLAUDE.md` (project overview, stack, gotchas).
- `toybox/.claude/rules/*.md` (esp. `frontend-ui.md`, `code-quality.md`).
- Workspace memory: `C:\Users\abero\.claude\projects\c--Users-abero-dev-toybox\memory\MEMORY.md`
  and topic-relevant `project_*.md` (kids profiles, phase statuses) /
  `feedback_*.md`.
- SD capability: `src/toybox/image_gen/{pipeline,worker,models}.py`.
- `documentation/master-plan.md` + `documentation/plan/` sub-docs for feature
  context (adventure = Phase W; elements/element cards = Phase M; rewards =
  Phase L; personas = Phase K/S).
- `documentation/investigations/local-sd-kids-ux/topics.md` (canonical
  cross-reference slug list).

## Kids (for personalization grounding)
Child A, 6 — early reader; likes dancing, LOL dolls. Child B, 4 — pre-reader; likes
the periodic table; quiet. (DB birthdates are placeholders; trust memory.)

## Per-topic grounding bullets (feed these to the matching agent)
- **01 toy-character-identity-consistency:** keeping a toy looking like itself
  across many generated scenes. IP-Adapter conditioning, seed reuse, reference-
  image pinning. Load-bearing for storybook/adventure/scenes — incoherent
  characters break all of them.
- **02 kiosk-style-cohesion:** one coherent art style across all generated
  imagery + the existing persona gradients (Phase S). Pair tightly with 03.
  Negative space: a "ransom-note" kiosk if each image looks different.
- **03 activity-scene-backgrounds:** generate a backdrop per activity/step
  behind the step card. Biggest single "not boring" visual lever. Weigh
  pre-render vs on-demand on one GPU.
- **04 content-safety-guardrails-primer:** GENERAL primer (operator wants to
  know "how it's done," not a toybox audit): negative prompts, checkpoint/model
  choice, NSFW/output classifiers, prompt allowlists, human-in-the-loop parent
  preview. Keep concise; reference tone.
- **05 coloring-page-line-art-mode:** line-art output (ControlNet canny / prompt
  for outlines). Bumped to Tier 1 — could UNLOCK new interactive play (color
  on-screen, print-and-color). Note interaction surface, not just generation.
- **06 illustrated-adventure-mode:** Phase W dynamic adventure engine + boss
  fights. Operator: "good direction, needs development, more lively; text
  blocks could be more visually interesting." Generate scene/character art per
  beat.
- **07 visual-interest-text-step-cards:** break up text-heavy step cards
  (StepCard.tsx) with imagery/layout; complements 06 beyond adventure.
- **08 personalized-storybook-illustrations:** illustrated story beats starring
  the household toys (depends on 01); ties to request_story intent.
- **09 elements-game-redesign:** operator: making the elements template LONGER
  made it LESS effective → revert to ~3 steps. New mechanic: blank periodic
  table → tap an element position → answer question(s) → correct plays a song
  + fills in the element. Existing element sprites/cards exist (Phase M;
  `element_id`, periodic-table fallback asset). Instance of pattern 10.
- **10 tap-grid-qa-reward-pattern:** META — generalize 09 + 11 into a reusable
  "tap-a-position-on-a-board → Q&A → correct → reward-reveal (song + fill-in)"
  activity template. The high-leverage reuse play.
- **11 human-body-learning-game:** parallel to elements — organs/systems, with
  SD-generated anatomy illustrations; same pattern as 10.
- **12 per-child-personalization:** activate the underused Children tab — tune
  generated visuals + difficulty + themes to each child's age/reading-level/
  interests (Child A vs Child B). Kids love seeing their stuff.
- **13 local-sd-animation-from-stills:** operator: "nice still generation —
  could it do full-on animation given a long run + thought-out generation
  steps?" Explore frame-sequence / sprite-sheet / img2img-chain / ControlNet
  pose-sequence / interpolation (RIFE/FILM) approaches; overnight batch;
  single-worker GPU realism. Acknowledge AnimateDiff/SVD prior failures.
- **14 image-based-choice-buttons:** generated thumbnails on branching choice
  buttons (helps pre-readers); branching already works (Phase G).
- **15 vocabulary-concept-illustration:** illustrate nouns/verbs/concepts for
  pre-readers (Child B); ties to clickable-words (Phase K).
- **16 persona-avatar-variety:** more avatar variety + emotional expressions
  per persona (Phase K/S gradients + avatars).
- **17 visual-schedules-step-thumbnails:** pre-reader "what's next" visual
  schedule / step thumbnails (accessibility/orientation aid).

## Execution model (do this in the clean window)
Meta files (`topics.md`, this `plan.md`) are already written. Remaining:
1. Dispatch **one background sub-agent per topic** (17), in **waves of ~10-12**
   (`run_in_background: true`) to avoid 529 cascades. Each agent prompt = the
   template below + the topic's grounding bullets + grounding files + likely
   cross-refs.
2. Retry any failed agent (529/timeout) in a fresh single dispatch.
3. After all 17 `NN-<slug>.md` exist (glob to confirm), write `README.md`:
   intro, tier-organized links with a one-line hook each, provenance, "see also"
   (workspace rules/memories the agents referenced).
4. Report: files written, retries, topics-per-tier, agent notes, next action.

## Investigation file template (verbatim)
```markdown
# <Topic Title>

**Tier:** <1 | 2 | 3 | 4>
**Status:** Investigated
**Related:** [[other-slug]], [[other-slug]]

## What it is
<1-2 paragraphs. State the concept directly.>

## Why it matters
<Kid-UX payoff. Failure modes. What goes wrong without it.>

## When to apply
<Trigger conditions — bullet list when natural.>

## How to apply
<Concrete patterns on the current SD setup; feasibility, effort, latency on a
single-worker GPU, safety, how to prototype. Snippets/checklists welcome.>

## References
<Only files the agent actually read. Rule: `<file>.md § <section>`. Memory:
`<memory-file>.md`. Code: `src/toybox/...`. Incident: <phase/step>.>

## Open questions
<What's unsettled; what to investigate or prototype next.>
```

## Constraints
350-600 words/file; no emojis; reference tone (terse, factual, grep-friendly);
cross-refs via `[[slug]]`; relative-path markdown links; no invented references;
an honest one-liner beats filler.

## Done criteria
17 `NN-<slug>.md` + `README.md` exist under
`documentation/investigations/local-sd-kids-ux/`, each following the template,
each citing only verified references, cross-linked by tier.
