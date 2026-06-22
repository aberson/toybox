# Local-SD → kids' UX — investigation set

Tiered reference set on ambitious ways to use toybox's on-device local
Stable-Diffusion pipeline (generated imagery + animation) to make the child
experience less "a little boring." 17 investigation files, one per topic, each
350-600 words in a fixed template (What it is / Why it matters / When to apply /
How to apply / References / Open questions). This is exploration/reference, NOT a
build plan — every file ends with open questions, not a commitment.

**Scope (operator-set).** In: Play (non-transcription) activities + the Child
Kiosk. Out (working well — not for rework): Kids & toyboxes management, Settings,
Play-Transcription. Standing opportunity: the underused **Children tab** →
per-child personalization. Dropped seed: reward/sticker art.

**Tech grounding.** SD 1.5 + cartoon LoRA + IP-Adapter Plus + rembg cutout;
single uvicorn worker, single-worker GPU; 4-step LCM; ~512px transparent PNG from
a reference photo. Three `image_gen_mode` values: `cartoon` (local SD, default) /
`composite` (Tier C fallback) / `claude_svg` (opt-in, rate-limited). The
single-worker FIFO GPU queue is the recurring constraint — it pushes nearly every
topic toward **pre-render / overnight-batch** over synchronous on-demand
generation. True animation remains unsolved (AnimateDiff abandoned in Phase U;
SVD `.webp` removed 2026-06-21 — see [topic 13](13-local-sd-animation-from-stills.md)).

## Tier 1 — Load-bearing

- [01 — Consistent toy-character identity across scenes](01-toy-character-identity-consistency.md)
  — per-call IP-Adapter Plus + per-row seed; the prerequisite that storybook,
  adventure, and scene art all break without.
- [02 — Style cohesion across the kiosk](02-kiosk-style-cohesion.md) — one
  hardcoded prompt suffix pins style today and the three render modes are
  stylistically unrelated: the "ransom-note kiosk" risk.
- [03 — Per-activity scene & background generation](03-activity-scene-backgrounds.md)
  — the biggest single "not boring" visual lever; an additive backdrop layer,
  best served from a small pre-rendered `scene_id` library.
- [04 — Content-safety guardrails (general primer)](04-content-safety-guardrails-primer.md)
  — how kid-safe image gen is done industry-wide: negative prompts, model choice,
  output classifiers, prompt allowlists, parent preview.
- [05 — Coloring-page (line-art) mode](05-coloring-page-line-art-mode.md) —
  prompt-only outlines now, ControlNet canny later; could unlock new interactive
  play (on-screen color vs print-and-color).

## Tier 2 — High-leverage

- [06 — Illustrated, livelier adventure mode](06-illustrated-adventure-mode.md) —
  Phase W's dynamic engine can't gen art per beat on a serial GPU; background
  prefetch or overnight theme-keyed backdrops, boss-only image as the MVP.
- [07 — Visual interest for text-heavy step cards](07-visual-interest-text-step-cards.md)
  — StepCard is the one universal renderer; a three-tier path (pure CSS → reuse
  slot sprites → batch-only new gen).
- [08 — Personalized storybook illustrations](08-personalized-storybook-illustrations.md)
  — re-pose existing slot sprites per story beat at a fixed seed, pre-rendered at
  approval time; depends on topic 01.
- [09 — Interactive "elements" game redesign](09-elements-game-redesign.md) —
  revert to ~3 steps; blank periodic table → tap → Q&A → song + fill-in, reusing
  Phase M element sprites off the tap critical path.
- [10 — Reusable tap-grid + Q&A + reward-reveal pattern](10-tap-grid-qa-reward-pattern.md)
  — the META reuse play: grid-nav + Q&A gate + cell card + song reward already
  exist unfused; each new subject board becomes a data file, not a code phase.
- [11 — Human-body learning game (organs/systems)](11-human-body-learning-game.md)
  — second concrete instance of the pattern; friendly non-clinical anatomy,
  pre-render ~6 organs offline.
- [12 — Per-child personalization (activate the Children tab)](12-per-child-personalization.md)
  — only `reading_level` reaches generation today; interests, age, and comfort
  are captured but unwired — cheap wiring, not new data collection.
- [13 — Local-SD animation from stills](13-local-sd-animation-from-stills.md) —
  honest post-mortem on the abandoned AnimateDiff/SVD path; sprite-sheet + CSS
  `steps()` and overnight img2img-chains are what actually fit one GPU.

## Tier 3 — Advanced / enhancements

- [14 — Image-based choice buttons (branching)](14-image-based-choice-buttons.md)
  — helps pre-readers pick; no per-choice image field exists, so a concept-keyed
  thumbnail library deduped by label-hash beats per-template gen across ~1360 templates.
- [15 — Vocabulary & concept illustration (pre-readers)](15-vocabulary-concept-illustration.md)
  — clickable-words is audio-only today; tap-to-see is additive, caches well over
  a finite vocab, and generic concept icons can skip the IP-Adapter path.
- [16 — Persona avatar variety & emotional expressions](16-persona-avatar-variety.md)
  — the avatar is a single emoji glyph with zero expression states; pre-render
  4-5 IP-Adapter faces per persona and swap via the existing `avatar_animation` path.

## Tier 4 — Polish

- [17 — Visual schedules & step-card thumbnails](17-visual-schedules-step-thumbnails.md)
  — the kiosk shows a bare "Step N"; a visited-path filmstrip reusing cached slot
  sprites is a zero-generation MVP for pre-reader orientation.

## Provenance

Topic set assembled via `/user-brainstorm` over three rounds (10-topic seed + two
gap-fill/scope rounds with the operator); see [topics.md](topics.md). Execution
plan and the verbatim file template: [plan.md](plan.md). Each file was written by
an independent background sub-agent that read real code, docs, and memory and was
instructed to cite only verified references (no invented sources). Tiering = impact
on the "make play/kiosk less boring" goal.

## See also (sources the agents referenced)

- Project: [`toybox/CLAUDE.md`](../../../CLAUDE.md), [`.claude/rules/frontend-ui.md`](../../../.claude/rules/frontend-ui.md)
- SD pipeline: `src/toybox/image_gen/{pipeline,worker,models,composite,animate,capability}.py`
- Plan context: [`documentation/master-plan.md`](../../master-plan.md) and
  `documentation/plan/` sub-docs; Phase plans for W (adventure), P (IP-Adapter
  image-gen redo), U/V (animation).
- Feature lineage by phase: M (elements / element cards), L + Q (rewards), K + S
  (personas, avatars, gradients), G (branching gameplay).
- Workspace memory: `project_kids_profiles.md` (Child A 6 / Child B 4) and the phase
  status memories under the toybox memory index.
