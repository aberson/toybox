"""GPU-gated integration test for the real SDXL+IPA+LoRA pipeline.

Skipped automatically when :func:`is_image_gen_capable` returns
False — i.e. on CI / dev hosts without a capable GPU + the four
checkpoints on disk. Run on operator hardware via::

    uv run pytest -m requires_gpu tests/integration/test_image_gen_real_gpu.py
"""

from __future__ import annotations

import io
import time

import pytest
from PIL import Image

from toybox.image_gen.capability import is_image_gen_capable
from toybox.image_gen.models import GenerationContext
from toybox.image_gen.pipeline import (
    DEFAULT_PALETTE_COLORS,
    DEFAULT_TIMEOUT_SEC,
    generate_action,
)


def _has_capable_gpu() -> bool:
    capable, _reason_enum, _detail = is_image_gen_capable()
    return capable


@pytest.mark.requires_gpu
@pytest.mark.skipif(
    not _has_capable_gpu(),
    reason="needs >=8 GB CUDA GPU + image_gen checkpoints on disk",
)
async def test_real_pipeline_one_generation() -> None:
    """One end-to-end generation with the canonical config.

    Asserts the F1 smoke probe pass criteria mirrored from the
    operator runbook: non-empty PNG, RGBA mode, ≤ palette-colors
    unique colors, wall-clock under the timeout.
    """
    # Synthetic blue square as the reference photo so the test
    # doesn't require a specific toy fixture file.
    ref = Image.new("RGB", (256, 256), (30, 60, 200))
    ref_buffer = io.BytesIO()
    ref.save(ref_buffer, format="PNG")
    ref_bytes = ref_buffer.getvalue()

    ctx = GenerationContext(
        toy_display_name="Test Bunny",
        persona_display_name=None,
        tags=(),
    )

    started = time.monotonic()
    out = await generate_action(ref_bytes, "idle", seed=12345, ctx=ctx)
    elapsed = time.monotonic() - started

    assert isinstance(out, bytes)
    assert len(out) > 0
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"
    assert img.mode == "RGBA"
    # Palette quantize should cap unique colors at <= configured count.
    colors = img.getcolors(maxcolors=256 * 256)
    assert colors is not None, "image has more than 65536 unique colors"
    assert len(colors) <= DEFAULT_PALETTE_COLORS
    # Generated within timeout.
    assert elapsed <= DEFAULT_TIMEOUT_SEC
