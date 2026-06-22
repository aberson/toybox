# Per-child personalization (activate the Children tab)

**Tier:** 2
**Status:** Investigated
**Related:** [[08-personalized-storybook-illustrations]], [[15-vocabulary-concept-illustration]], [[16-persona-avatar-variety]]

## What it is
The `children` table already stores rich per-child signals — `birthdate`,
`reading_level` (pre-reader / early-reader / fluent), `interests` (free text),
`comfort`, `pronouns`, `notes` — editable from the Children tab
(`ChildProfileEditor.tsx` → `api/children.py`). Today only one of these
signals actually steers content: `reading_level` flows through
`content_resolver.build_claude_directive` into the activity-generation system
prompt (and aggregates MINIMUM across a multi-child activity). `interests`,
`birthdate`/age, and `comfort` are captured but never reach generation; the
SD pipeline's `GenerationContext` (`toy_display_name`, `persona_display_name`,
`tags`) carries no child channel at all.

This topic is about activating the underused tab: routing the per-child signals
already on disk into generated visuals, activity difficulty, and theme/subject
selection, so what Child A sees differs from what Child B sees.

## Why it matters
Kids respond hard to seeing their own things. A 6-year-old who loves LOL dolls
and a 4-year-old fixated on the periodic table want visibly different play. The
failure mode is the opposite of personalization: a one-size kiosk that feels
generic, pitched at the wrong reading level (text Child B can't read, or
babyish for Child A), themed for neither child. Because the signals exist but sit
idle, the cheapest win is wiring, not data collection.

## When to apply
- An activity is bound to a known `child_id` (single-child runs — the strongest
  signal; multi-child runs must aggregate, as reading_level already does).
- Generation has a free parameter the child profile can bias: a `tags` slot on
  `GenerationContext`, a theme/subject pick, a difficulty dial, a step count.
- Pre-reader present → prefer imagery over text (ties to pre-reader topics).

## How to apply
Three channels, in ascending effort:

1. **Difficulty / text (cheapest, partly built).** `reading_level` already
   reaches the Claude directive. Extend the same pattern: derive age from
   `birthdate` (placeholder dates — trust profiles), map to step count / question
   difficulty for the tap-grid pattern. Pre-reader → fewer words, larger art.
2. **Theme/subject from `interests`.** Parse `interests` free text into a short
   token list and feed it as subject bias when picking templates or composing
   scene prompts. Child A → "dancing, LOL dolls"; Child B → "periodic table,
   chemistry". This wants light normalization (free text → safe tokens) and a
   safety pass so a typo can't inject an off-theme prompt — see
   [[04-content-safety-guardrails-primer]].
3. **Generated visuals (highest payoff, most effort).** Add an optional
   per-child `tags` contribution to `GenerationContext._build_prompt`. The IPA
   reference still pins the toy's identity; child tags only tint the scene
   ("ballet stage" vs "laboratory bench"). Latency unchanged — same 4-step LCM,
   single-worker GPU; cost is prompt-assembly plumbing, not extra inference.

**Prototype:** add an `interests`-derived tag list to one activity's scene
prompt for a single child, hardcode Child A-vs-Child B, and eyeball two kiosk runs
side by side before generalizing the resolver path.

## References
- Code: `src/toybox/api/children.py` (full child schema + CRUD).
- Code: `frontend/src/parent/components/ChildProfileEditor.tsx` (the tab UI).
- Code: `src/toybox/activities/content_resolver.py` (`build_claude_directive`,
  `_READING_LEVEL_DIRECTIVES`, MINIMUM reading-level aggregation).
- Code: `src/toybox/image_gen/pipeline.py` (`_build_prompt`), `models.py`
  (`GenerationContext`: `toy_display_name`, `persona_display_name`, `tags`).
- Memory: `project_kids_profiles.md` (Child A 6 / Child B 4), `project_phase_k_*`.
- Plan: `documentation/investigations/local-sd-kids-ux/plan.md` (scope: Children
  tab underused).

## Open questions
- Free-text `interests` → prompt tokens: NL parse, fixed tag picker, or a small
  curated interest taxonomy on the Children tab?
- Multi-child runs: how to blend two children's interests without muddying the
  scene (alternate per step? intersect? pick the activity owner)?
- Does `comfort` (loud_ok / prefers_quiet) belong here, gating SFX/animation
  intensity rather than visuals?
- Is age-from-birthdate trustworthy enough given placeholder DB dates, or should
  difficulty key off `reading_level` only?
