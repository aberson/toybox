"""Stub-runtime coverage for :func:`toybox.image_gen.pipeline.generate_action`.

Exercises orchestration (timeout, OOM envelope, determinism)
without touching torch. ``TOYBOX_IMAGE_GEN_STUB=1`` short-circuits
the heavy path; the stub fixture honors the same env knobs the
real pipeline would.
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
    STUB_DELAY_ENV,
    STUB_ENV,
    STUB_MODE_ENV,
    TIMEOUT_ENV,
    generate_action,
)


@pytest.fixture(autouse=True)
def _enable_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the stub path for every test in this file."""
    monkeypatch.setenv(STUB_ENV, "1")
    # Default: no delay, no oom mode unless the test sets one.
    monkeypatch.delenv(STUB_DELAY_ENV, raising=False)
    monkeypatch.delenv(STUB_MODE_ENV, raising=False)


def _ctx() -> GenerationContext:
    return GenerationContext(
        toy_display_name="Bunny",
        persona_display_name="Hopper",
        tags=("plush",),
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
