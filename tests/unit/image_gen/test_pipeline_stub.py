"""Stub-runtime coverage for :func:`toybox.image_gen.pipeline.generate_action`.

Exercises orchestration (timeout, OOM envelope, determinism)
without touching torch. ``TOYBOX_IMAGE_GEN_STUB=1`` short-circuits
the heavy path; the stub fixture honors the same env knobs the
real pipeline would.

Also covers the plain helpers exposed by ``pipeline.py`` whose
behaviour is independent of the heavy path:

* :func:`_build_prompt` — DB-fields + palette-token template
* :func:`_extract_palette_hex` — Pillow MEDIANCUT colour extraction
* :func:`_cartoon_mode` — env dispatch validation
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from toybox.image_gen.models import (
    GenerationContext,
    ImageGenCapacityError,
    ImageGenTimeoutError,
)
from toybox.image_gen.pipeline import (
    CARTOON_MODE_CHECKPOINT,
    CARTOON_MODE_ENV,
    CARTOON_MODE_LORA,
    STUB_DELAY_ENV,
    STUB_ENV,
    STUB_MODE_ENV,
    TIMEOUT_ENV,
    _build_prompt,
    _cartoon_mode,
    _extract_palette_hex,
    generate_action,
)


@pytest.fixture(autouse=True)
def _enable_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the stub path for every async test in this file."""
    monkeypatch.setenv(STUB_ENV, "1")
    # Default: no delay, no oom mode unless the test sets one.
    monkeypatch.delenv(STUB_DELAY_ENV, raising=False)
    monkeypatch.delenv(STUB_MODE_ENV, raising=False)


def _ctx(
    *,
    persona: str | None = "Hopper",
    name: str = "Bunny",
    tags: tuple[str, ...] = ("plush",),
) -> GenerationContext:
    return GenerationContext(
        toy_display_name=name,
        persona_display_name=persona,
        tags=tags,
    )


async def test_returns_valid_png_with_alpha() -> None:
    out = await generate_action(b"reference-bytes", "idle", seed=12345, ctx=_ctx())

    assert isinstance(out, bytes)
    assert len(out) > 0
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGBA"
    # The stub guarantees at least one fully transparent pixel.
    alpha_band = img.getchannel("A")
    assert min(alpha_band.getextrema()) == 0


async def test_deterministic_for_same_slot_and_seed() -> None:
    a = await generate_action(b"ref", "pointing", seed=42, ctx=_ctx())
    b = await generate_action(b"different-ref-bytes", "pointing", seed=42, ctx=_ctx())
    # Same (slot, seed) → same bytes regardless of reference photo
    # (stub determinism contract).
    assert a == b


async def test_different_seeds_produce_different_outputs() -> None:
    a = await generate_action(b"ref", "idle", seed=1, ctx=_ctx())
    b = await generate_action(b"ref", "idle", seed=2, ctx=_ctx())
    assert a != b


async def test_different_slots_produce_different_outputs() -> None:
    a = await generate_action(b"ref", "idle", seed=42, ctx=_ctx())
    b = await generate_action(b"ref", "jumping", seed=42, ctx=_ctx())
    assert a != b


async def test_oom_envelope_raises_capacity_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STUB_MODE_ENV, "oom")

    with pytest.raises(ImageGenCapacityError):
        await generate_action(b"ref", "idle", seed=99, ctx=_ctx())


async def test_timeout_raises_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub sleeps 5s; pipeline cap at 0.2s → wait_for fires.
    monkeypatch.setenv(STUB_DELAY_ENV, "5")
    monkeypatch.setenv(TIMEOUT_ENV, "0.2")

    with pytest.raises(ImageGenTimeoutError):
        await generate_action(b"ref", "idle", seed=7, ctx=_ctx())


def test_pipeline_does_not_call_enable_attention_slicing() -> None:
    """Regression test for diffusers Gotcha 2 (see feedback_diffusers_ipa_gotchas.md).

    enable_attention_slicing() overwrites the IPA attention processors. Future
    contributors might 're-enable' it to 'help with memory'; this test fails
    loudly if they do.

    AST-based check against the source file: walks the parsed module looking
    for any call whose attribute name is ``enable_attention_slicing``. This
    is robust against docstring / comment mentions of the rule (the module
    docstring itself describes the prohibition, so a naive substring check
    would false-positive).
    """
    import ast
    from pathlib import Path

    from toybox.image_gen import pipeline as pipeline_mod

    source = Path(pipeline_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "enable_attention_slicing":
                offenders.append(node.lineno)
            elif isinstance(func, ast.Name) and func.id == "enable_attention_slicing":
                offenders.append(node.lineno)
    assert not offenders, (
        f"pipeline.py must NOT call enable_attention_slicing() (lines {offenders}) — "
        "see documentation/operator/image-gen-runtime.md §'Canonical pipeline config' "
        "Rule 2."
    )


# ---------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------


def test_build_prompt_with_persona_tags_and_palette() -> None:
    prompt = _build_prompt(
        "pointing",
        _ctx(persona="Hopper", name="Bunny", tags=("plush", "soft", "pink")),
        ("#ffaabb", "#001122", "#deadbe"),
    )
    assert "Hopper the Bunny" in prompt
    assert "plush, soft, pink" in prompt
    assert "primary color #ffaabb" in prompt
    assert "primary color #001122" in prompt
    assert "primary color #deadbe" in prompt
    assert "pointing at something off to the side" in prompt
    assert "2D cartoon, simple shapes, clean lines, transparent background" in prompt


def test_build_prompt_without_persona_uses_a_intro() -> None:
    prompt = _build_prompt(
        "idle",
        _ctx(persona=None, name="Bunny", tags=("plush",)),
        ("#aabbcc",),
    )
    assert prompt.startswith("a Bunny,")
    assert "Hopper" not in prompt


def test_build_prompt_with_empty_tags_degrades_gracefully() -> None:
    """Empty ``ctx.tags`` produces an empty tag fragment without crashing."""
    prompt = _build_prompt(
        "idle",
        _ctx(persona=None, name="Bunny", tags=()),
        ("#112233",),
    )
    assert "a Bunny" in prompt
    assert "primary color #112233" in prompt
    # The tag fragment is the empty string; no NoneType / KeyError.
    assert "None" not in prompt


def test_build_prompt_palette_caps_at_three_colors() -> None:
    """Only the first three palette hex codes are emitted."""
    palette = ("#aaaaaa", "#bbbbbb", "#cccccc", "#dddddd", "#eeeeee")
    prompt = _build_prompt("idle", _ctx(), palette)
    for hex_code in palette[:3]:
        assert f"primary color {hex_code}" in prompt
    for hex_code in palette[3:]:
        assert f"primary color {hex_code}" not in prompt


def test_build_prompt_unknown_slot_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown action slot"):
        _build_prompt("not-a-slot", _ctx(), ("#ffffff",))


# ---------------------------------------------------------------------
# _extract_palette_hex
# ---------------------------------------------------------------------


def test_extract_palette_hex_returns_hex_strings() -> None:
    """Synthetic two-colour image yields hex codes shaped #RRGGBB."""
    img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
    # Splash a green square so MEDIANCUT picks up at least two colours.
    for x in range(8):
        for y in range(8):
            img.putpixel((x, y), (0, 255, 0, 255))

    palette = _extract_palette_hex(img, n=3)
    assert 1 <= len(palette) <= 3
    for entry in palette:
        assert entry.startswith("#")
        assert len(entry) == 7
        # All lowercase hex digits per the f-string format.
        assert entry[1:] == entry[1:].lower()
        int(entry[1:], 16)  # hex-decode roundtrip


def test_extract_palette_hex_caps_at_n_entries() -> None:
    img = Image.new("RGBA", (16, 16), (10, 20, 30, 255))
    palette = _extract_palette_hex(img, n=2)
    assert len(palette) <= 2


# ---------------------------------------------------------------------
# _cartoon_mode env dispatch
# ---------------------------------------------------------------------


def test_cartoon_mode_default_is_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CARTOON_MODE_ENV, raising=False)
    assert _cartoon_mode() == CARTOON_MODE_CHECKPOINT


def test_cartoon_mode_explicit_lora(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CARTOON_MODE_ENV, "lora")
    assert _cartoon_mode() == CARTOON_MODE_LORA


def test_cartoon_mode_normalizes_case_and_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CARTOON_MODE_ENV, "  Checkpoint  ")
    assert _cartoon_mode() == CARTOON_MODE_CHECKPOINT


def test_cartoon_mode_unknown_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CARTOON_MODE_ENV, "garbage")
    with pytest.raises(ValueError, match="not in"):
        _cartoon_mode()
