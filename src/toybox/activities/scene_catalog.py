"""Single source of truth for the kiosk scene-backdrop set (Phase Y).

The kiosk composes transparent-PNG toy sprites over a flat persona gradient;
Phase Y adds a full-bleed illustrated **scene backdrop** behind the step card,
drawn from a small PRE-RENDERED library. This module is the one place that
defines:

* :data:`SCENE_IDS` — the canonical scene id set. Imported by the batch CLI
  (``scripts/batch_scenes.py``, Y2), the template field + validator (Y3), the
  scene resolver (Y4), and the persist/serialize path (Y5). Defining the set
  ONCE here keeps producer and consumer from drifting (see
  ``.claude/rules/code-quality.md`` § "One source of truth for data-shape
  constants").
* :data:`SCENE_PROMPTS` — the offline text2img prompt per scene. Each ends with
  the SAME cartoon style descriptors the sprite pipeline uses
  (``image_gen.pipeline._build_prompt`` appends
  ``"2D cartoon, simple shapes, clean lines, transparent background"``) so the
  cast does not look pasted onto a mismatched backdrop ("ransom-note kiosk").
  Scenes are OPAQUE full-bleed scenery (no toy), so the suffix drops
  ``"transparent background"`` and instead pins ``"full background scene, no
  characters"``.
* :data:`DEFAULT_SCENE_ID` — the fallback scene when neither a template
  ``scene_id`` nor a child-interest match resolves one.
* :data:`INTEREST_SCENE_TAGS` — maps a normalized child-interest token to a
  scene id. Used by Y4's ``resolve_scene_id`` so a baked PNG is SELECTED for a
  child (a baked PNG cannot be re-tinted live). Every value is a member of
  :data:`SCENE_IDS`.

STDLIB-ONLY — no torch / diffusers / PIL imports here (mirror the lazy-import
discipline in :mod:`toybox.image_gen.models`); the heavy generation work lives
in the batch CLI's pipeline call, not in this data module.
"""

from __future__ import annotations

from typing import Final

# The canonical scene ids, in display order. Lowercase ascii, no spaces — these
# double as the on-disk filename stem (``data/images/scenes/<id>.png``) and the
# ``activities.scene_id`` column value, so the character set is deliberately
# filename-safe.
SCENE_IDS: Final[tuple[str, ...]] = (
    "forest",
    "kitchen",
    "space",
    "lab",
    "stage",
    "castle",
    "undersea",
    "bedroom",
    "park",
    "workshop",
)

# Shared cartoon style suffix. Mirrors the sprite pipeline's positive-prompt
# tail (``image_gen.pipeline._build_prompt``) MINUS "transparent background"
# (scenes are opaque), PLUS a full-bleed/no-characters pin so the diffuser
# renders scenery, not a subject.
_STYLE_SUFFIX: Final[str] = (
    "2D cartoon, simple shapes, clean lines, soft colors, child-friendly, "
    "full background scene, no characters"
)


def _scene_prompt(scene: str) -> str:
    """Compose a scene's full text2img prompt from its descriptor + style tail."""
    return f"{_SCENE_DESCRIPTORS[scene]}, {_STYLE_SUFFIX}"


# Per-scene descriptor (the subject half of the prompt). Kept separate from the
# composed prompt so the shared style tail has one definition.
_SCENE_DESCRIPTORS: Final[dict[str, str]] = {
    "forest": "a sunny forest clearing with tall trees and dappled light",
    "kitchen": "a cozy home kitchen with warm light and wooden counters",
    "space": "outer space with bright stars, planets, and a distant nebula",
    "lab": "a colorful science laboratory with beakers and a periodic table on the wall",
    "stage": "a bright dance stage with curtains and warm spotlights",
    "castle": "a friendly fairytale castle great hall with banners and tall windows",
    "undersea": "a calm underwater ocean scene with coral, seaweed, and gentle light",
    "bedroom": "a cozy child's bedroom with soft evening light and a window",
    "park": "a green park playground with a clear blue sky and a winding path",
    "workshop": "a tidy maker workshop with workbenches and friendly tools on the wall",
}

# Composed prompt per scene id. One entry per :data:`SCENE_IDS` member.
SCENE_PROMPTS: Final[dict[str, str]] = {scene: _scene_prompt(scene) for scene in SCENE_IDS}

# Neutral, calm fallback when nothing else resolves a scene.
DEFAULT_SCENE_ID: Final[str] = "bedroom"

# Normalized child-interest token -> scene id. Tokens are lowercase; the lookup
# in :func:`scene_for_interest` lowercases + strips its input first. Every value
# is a member of :data:`SCENE_IDS` (asserted in the unit test). Keep keys broad
# so a free-text ``children.interests`` string can match by substring upstream
# (Y4 normalizes the free text into tokens before calling here).
INTEREST_SCENE_TAGS: Final[dict[str, str]] = {
    # dance / performance -> stage
    "dance": "stage",
    "dancing": "stage",
    "ballet": "stage",
    "lol dolls": "stage",
    "music": "stage",
    "singing": "stage",
    # chemistry / elements -> lab
    "periodic table": "lab",
    "chemistry": "lab",
    "science": "lab",
    "elements": "lab",
    "experiments": "lab",
    # space -> space
    "space": "space",
    "astronomy": "space",
    "rockets": "space",
    "planets": "space",
    "stars": "space",
    # nature / animals -> forest
    "forest": "forest",
    "nature": "forest",
    "animals": "forest",
    "camping": "forest",
    # cooking -> kitchen
    "cooking": "kitchen",
    "baking": "kitchen",
    "food": "kitchen",
    # ocean -> undersea
    "ocean": "undersea",
    "under the sea": "undersea",
    "fish": "undersea",
    "mermaids": "undersea",
    # castles / royalty -> castle
    "castles": "castle",
    "princesses": "castle",
    "knights": "castle",
    "dragons": "castle",
    # building / making -> workshop
    "building": "workshop",
    "building blocks": "workshop",
    "lego": "workshop",
    "robots": "workshop",
    # playgrounds -> park
    "park": "park",
    "playground": "park",
    "sports": "park",
}


def scene_for_interest(token: str) -> str | None:
    """Return the scene id for a single interest ``token``, or ``None``.

    Case-insensitive and whitespace-trimmed. Returns ``None`` for an unknown
    token so the caller (Y4's resolver chain) can fall through to the next
    interest token or to :data:`DEFAULT_SCENE_ID`.
    """
    return INTEREST_SCENE_TAGS.get(token.strip().lower())


__all__ = [
    "DEFAULT_SCENE_ID",
    "INTEREST_SCENE_TAGS",
    "SCENE_IDS",
    "SCENE_PROMPTS",
    "scene_for_interest",
]
