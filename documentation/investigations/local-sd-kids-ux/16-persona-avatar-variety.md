# Persona avatar variety & emotional expressions

**Tier:** 3
**Status:** Investigated
**Related:** [[02-kiosk-style-cohesion]], [[12-per-child-personalization]], [[13-local-sd-animation-from-stills]]

## What it is
Today each library persona ships exactly one static avatar PNG
(`princess`, `wizard`, `detective`, `periodic_table` under
`src/toybox/personas/library/avatars/`). The kiosk renders that single image
in `PersonaAvatar.tsx` and animates it only via CSS motion classes
(`avatar-animate-{float,pulse,wobble,jump,shine,spin}`) chosen per step from
`currentStep.metadata.avatar_animation`. The persona never changes *face* — it
moves but its expression is frozen.

This topic explores giving each persona a small finite set of pre-rendered
**emotional expression** images — e.g. neutral, happy, surprised, encouraging,
thinking — and having the kiosk swap which image is shown in response to
activity events (correct answer → happy, wrong answer → encouraging, boss
fight → surprised, idle → neutral). The motion stays CSS; the *face* becomes a
state variable.

## Why it matters
A persona that reacts emotionally reads as a companion rather than a sticker.
For Child B (4, pre-reader) facial affect is a primary comprehension channel —
an encouraging face after a miss communicates "try again" without words. For
Child A (6) the variety keeps a long adventure from feeling static. Expressions
also reinforce the existing persona-gradient mood cue (Phase S) so the whole
screen shifts together.

Failure modes: identity drift (the "happy wizard" looks like a different
character than the "neutral wizard") breaks the companion illusion; too many
expressions invites generation cost and curation burden; uncanny or
ambiguous faces confuse a 4-year-old. Mismatched expression-to-event (happy on
a wrong answer) is worse than no expression.

## When to apply
- After style cohesion ([[02-kiosk-style-cohesion]]) is settled, so the
  expression set matches the kiosk art direction.
- When an activity emits clear emotional events: Q&A correct/incorrect,
  reward reveal, boss-fight start, encouragement beats.
- Library personas first (4 finite, curatable); custom personas later.

## How to apply
Pre-render, do not generate live. For each persona, run the existing pipeline
(`generate_action` in `image_gen/pipeline.py`) with the **persona's own avatar
as the IP-Adapter reference image** and the same fixed seed, varying only an
expression token in the prompt ("happy smiling face", "surprised wide eyes",
"gentle encouraging smile"). Reusing one reference + one seed is the identity
anchor — IP-Adapter Plus already carries identity/colour conditioning, so the
expressions stay on-model. Output: ~4-5 PNGs per persona (≈20 images total for
the 4 library personas) — a one-time overnight batch, zero kiosk-time latency.

Kiosk swap mechanism: extend `PersonaAvatar.tsx` to accept an
`expression` prop and resolve `imagePath` to
`library/avatars/{persona}_{expression}.png` (fall back to the existing
single avatar, then the letter circle — the failure ladder already exists).
Drive `expression` from a new step-metadata key (mirror the proven
`avatar_animation` wiring tested in `App.avatar-animation.test.tsx`); the CSS
motion class is orthogonal and stays. Prototype: hand-pick 3 expressions for
`wizard`, render them, and wire the correct/incorrect Q&A events in one
activity to prove the swap reads on the iPad before scaling to all personas.

Effort: low-moderate (additive; no pipeline change). Safety: each rendered
face needs the same parent-skim review as other generated art.

## References
- `src/toybox/image_gen/pipeline.py` (IP-Adapter Plus + fixed-seed reuse)
- `frontend/src/child/components/PersonaAvatar.tsx` (image/letter fallback ladder)
- `frontend/src/child/components/ToyActionSprite.module.css` (CSS motion model)
- `frontend/src/child/animations/rewardAnimations.css` (`avatar-animate-*` classes)
- `frontend/src/child/App.avatar-animation.test.tsx` (step-metadata → avatar wiring)
- `src/toybox/personas/library/wizard.json`, `_schema.json` (persona shape)
- `src/toybox/personas/models.py` (persona attribute models)
- MEMORY: `project_phase_s_planning_2026-06-05.md` (gradients + avatar animation),
  `project_kids_profiles.md` (Child A 6, Child B 4)
- Incident: Phase K (persona library), Phase S (S1 gradients / S2 avatar animation)

## Open questions
- Which expression set is minimal-but-sufficient — is 3 (neutral / happy /
  encouraging) enough, or do boss fights need a distinct surprised/serious face?
- Does fixed-seed + reference reuse actually hold identity across expression
  prompts, or does a strong "surprised" token collapse the pose? Prototype
  needed.
- Should expression be persona-authored (in the JSON, like `behavior_tags`)
  or purely event-derived at the kiosk?
- Custom (parent-uploaded) personas have one photo and no expression set —
  generate expressions on import, or leave them static? Ties to
  [[12-per-child-personalization]].
- Could expression-swap become true micro-animation (morph between faces)
  rather than a hard cut? See [[13-local-sd-animation-from-stills]].
