"""Unit tests for the Phase Y scene catalog (single source of truth)."""

from __future__ import annotations

from toybox.activities.scene_catalog import (
    DEFAULT_SCENE_ID,
    INTEREST_SCENE_TAGS,
    SCENE_IDS,
    SCENE_PROMPTS,
    scene_for_interest,
)

# Pin the id set explicitly so a future re-order / drop is a deliberate test
# edit, not a silent change (the set is a cross-module contract — Y2/Y3/Y4/Y5
# all key off it).
_EXPECTED_SCENE_IDS = (
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


def test_scene_ids_are_pinned() -> None:
    assert SCENE_IDS == _EXPECTED_SCENE_IDS


def test_scene_ids_are_unique_lowercase_nonempty() -> None:
    assert len(SCENE_IDS) > 0
    assert len(set(SCENE_IDS)) == len(SCENE_IDS), "duplicate scene id"
    for scene in SCENE_IDS:
        assert scene == scene.lower(), f"{scene!r} is not lowercase"
        assert scene.strip() == scene and scene != "", f"{scene!r} has bad whitespace"
        assert " " not in scene, f"{scene!r} is not filename-safe (contains space)"


def test_every_scene_id_has_a_prompt_and_vice_versa() -> None:
    assert set(SCENE_PROMPTS) == set(SCENE_IDS)
    for scene, prompt in SCENE_PROMPTS.items():
        assert isinstance(prompt, str) and prompt.strip(), f"empty prompt for {scene!r}"


def test_scene_prompts_carry_the_cartoon_style_suffix() -> None:
    # Cohesion guard: every scene prompt must end with the shared cartoon style
    # tail so backdrops match the sprite art style. Scenes are opaque, so the
    # tail must NOT request a transparent background.
    for scene, prompt in SCENE_PROMPTS.items():
        assert "2D cartoon, simple shapes, clean lines" in prompt, scene
        assert "transparent background" not in prompt, scene
        assert "no characters" in prompt, scene


def test_default_scene_is_a_member() -> None:
    assert DEFAULT_SCENE_ID in SCENE_IDS


def test_interest_tags_map_only_to_valid_scenes() -> None:
    assert INTEREST_SCENE_TAGS, "interest map should not be empty"
    for token, scene in INTEREST_SCENE_TAGS.items():
        assert token == token.lower(), f"interest key {token!r} is not lowercase"
        assert scene in SCENE_IDS, f"{token!r} -> {scene!r} is not a valid scene id"


def test_scene_for_interest_is_case_insensitive() -> None:
    assert scene_for_interest("dancing") == "stage"
    assert scene_for_interest("DANCING") == "stage"
    assert scene_for_interest("  Periodic Table  ") == "lab"


def test_scene_for_interest_unknown_returns_none() -> None:
    assert scene_for_interest("quantum chromodynamics") is None
    assert scene_for_interest("") is None


def test_module_is_stdlib_only() -> None:
    # The catalog must stay import-cheap (no torch/diffusers/PIL) — mirrors the
    # lazy-import contract on the image-gen modules. Check in a fresh
    # subprocess so deps imported by other tests in this session don't mask a
    # real heavy import inside scene_catalog.
    import subprocess
    import sys

    code = (
        "import toybox.activities.scene_catalog;"
        "import sys;"
        "heavy=[m for m in ('torch','diffusers','rembg') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", f"scene_catalog pulled in heavy deps: {result.stdout!r}"
