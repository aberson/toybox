# Style cohesion across the kiosk

**Tier:** 1
**Status:** Investigated
**Related:** [[01-toy-character-identity-consistency]], [[03-activity-scene-backgrounds]], [[07-visual-interest-text-step-cards]], [[16-persona-avatar-variety]]

## What it is
A single coherent art style across every piece of imagery the kiosk shows a
child — toy sprites, activity backgrounds, choice thumbnails, reward art,
avatars — so the screen reads as one designed world rather than a collage of
unrelated pictures. It also means the generated imagery harmonizes with the
non-generated chrome already shipped: the persona-keyed background gradients
from Phase S (`theming.ts`) and the persona avatar circles.

Today only the toy action sprites are SD-generated, and the style is pinned by
one hardcoded suffix in `pipeline._build_prompt`: `"2D cartoon, simple shapes,
clean lines, transparent background"`, with a shared `DEFAULT_NEGATIVE_PROMPT`
banning photorealism/3d/gradients/text. As topics 03/06/08 add backgrounds,
scenes, and storybook beats, every new generation surface inherits — or breaks
— that one style contract.

## Why it matters
Kid-UX payoff: a consistent look makes the kiosk feel like a polished product,
not a tech demo, and lowers visual load for a 4yo (Child B) who navigates by
picture not text. Cohesion lets the persona color world (detective = navy/slate,
periodic_table = green, princess = pink, wizard = purple per `theming.ts`) carry
meaning instead of fighting the art.

Failure mode — the "ransom-note" kiosk: a flat cartoon sprite on a
painterly background under a Claude-SVG reward, each at a different line weight,
saturation, and lighting. Because the three `image_gen_mode` values (cartoon
local-SD / composite / claude_svg) are stylistically unrelated rendering paths,
mixing them on one screen is the single biggest cohesion risk.

## When to apply
- Before adding any new generated surface (background, thumbnail, storybook,
  reward art) — pin its style to the same contract.
- When a screen composites more than one image source at once.
- When `image_gen_mode` can vary per-toy within one household, so two sprites
  in one activity could come from different renderers.

## How to apply
- **One style token block, one source of truth.** Extract the cartoon suffix +
  `DEFAULT_NEGATIVE_PROMPT` into a shared style constant and have every new
  prompt builder import it (mirrors the workspace "one source of truth for
  data-shape constants" rule). Backgrounds/scenes should append the SAME suffix
  so line weight and shading match the sprites.
- **Pin the look.** Fixed `IP_ADAPTER_SCALE` (0.6) and seed reuse (see
  [[01-toy-character-identity-consistency]]) already stabilize per-toy identity;
  reuse a per-household base seed for backgrounds so palette/lighting stays put.
- **Harmonize with Phase S gradients.** Constrain generated backgrounds to a
  palette that sits under the persona gradient (or render the gradient as the
  backdrop and keep generated content as transparent-PNG foreground — cheapest
  and most cohesive on a single-worker GPU).
- **Avoid mode-mixing on a screen.** Don't composite a `claude_svg` element next
  to a local-SD sprite; pick one renderer per screen.
- Feasibility/latency: zero new model cost — this is prompt/constant discipline
  plus palette constraints, so no extra 4-step LCM passes. Safety unchanged
  (same negative prompt, `safety_checker=None` is pre-existing).
- Prototype: generate the 10 action sprites + one background for one toy, lay
  them on the persona gradient, eyeball for line-weight/saturation drift.

## References
- `src/toybox/image_gen/pipeline.py` (`_build_prompt`, `DEFAULT_NEGATIVE_PROMPT`,
  `IP_ADAPTER_SCALE`, `_run_pipeline_sync`)
- `src/toybox/image_gen/models.py` (`ACTION_SLOTS`, `GenerationContext`)
- `frontend/src/child/theming.ts` (Phase S persona gradients)
- `frontend/src/child/components/PersonaAvatar.tsx` (avatar palette)
- `documentation/investigations/local-sd-kids-ux/plan.md` § Tech grounding
- `.claude/rules/code-quality.md` § One source of truth for data-shape constants
- `CLAUDE.md` § Gotchas (single uvicorn worker; `image_gen_mode` values)

## Open questions
- Should backgrounds be generated at all, or should the Phase S gradient stay
  the universal backdrop with only transparent foreground art generated?
- Does the cartoon checkpoint hold a stable palette across very different
  prompts (sprite vs. scene), or does it need an explicit palette token despite
  Phase P having dropped hex tokens for biasing toward literal glyphs?
- Can `composite`/`claude_svg` fallbacks be style-matched to local-SD cartoon,
  or must a household be locked to one renderer for cohesion?
- Per-persona style accents (line color tuned to the gradient) — enhances
  cohesion or fragments it?
