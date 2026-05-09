"""GPU-gated integration test for the SD 1.5 + LCM-LoRA + cartoon pipeline.

Skipped automatically when :func:`is_image_gen_capable` returns
False — i.e. on CI / dev hosts without a capable GPU + the required
checkpoints on disk. Run on operator hardware via::

    uv run pytest -m requires_gpu tests/integration/test_image_gen_real_gpu.py

Parametrized over ``TOYBOX_IMAGE_GEN_CARTOON_MODE`` so both the
single-cartoon-checkpoint mode (default) and the SD-1.5-base +
cartoon-LoRA mode are exercised end-to-end. Each mode skips
individually if its required checkpoint set isn't on disk.
"""

from __future__ import annotations

import io
import os
import time
from collections.abc import Iterator

import pytest
from PIL import Image

from toybox.image_gen.capability import (
    CARTOON_MODE_ENV,
    is_image_gen_capable,
)
from toybox.image_gen.models import GenerationContext
from toybox.image_gen.pipeline import (
    DEFAULT_OUTPUT_DIM,
    generate_action,
    reset_pipeline_cache_for_tests,
)


def _has_capable_gpu(mode: str) -> bool:
    """Return True iff the gate is open under the given cartoon mode."""
    prior = os.environ.get(CARTOON_MODE_ENV)
    os.environ[CARTOON_MODE_ENV] = mode
    try:
        capable, _reason_enum, _detail = is_image_gen_capable()
    finally:
        if prior is None:
            os.environ.pop(CARTOON_MODE_ENV, None)
        else:
            os.environ[CARTOON_MODE_ENV] = prior
    return capable


@pytest.fixture
def _isolate_pipeline_cache() -> Iterator[None]:
    """Drop the cached pipeline before AND after each parametrized run.

    Each cartoon mode constructs a different pipeline; reusing the
    cache across modes would invalidate the second run's load path.
    """
    reset_pipeline_cache_for_tests()
    yield
    reset_pipeline_cache_for_tests()


@pytest.mark.requires_gpu
@pytest.mark.parametrize("cartoon_mode", ["checkpoint", "lora"])
async def test_real_pipeline_one_generation(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_pipeline_cache: None,
    cartoon_mode: str,
) -> None:
    """One end-to-end generation per cartoon mode.

    Pass criteria mirrored from the build doc §F.5-2 Done when:

    * 128×128 RGBA PNG output with non-trivial alpha
    * peak VRAM < 6 GB via ``torch.cuda.max_memory_allocated()``
    * wall-clock < 5 s
    """
    monkeypatch.setenv(CARTOON_MODE_ENV, cartoon_mode)
    if not _has_capable_gpu(cartoon_mode):
        pytest.skip(
            f"cartoon_mode={cartoon_mode}: capability gate closed "
            "(no GPU or missing checkpoints for this mode)"
        )

    import torch

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

    # First call warms the module-level pipeline cache (cold load
    # takes 10–20 s for the SD 1.5 + LoRA stack on this card; not
    # what we're measuring). Wall-clock + peak-VRAM budgets apply to
    # the warm path the worker actually runs.
    await generate_action(ref_bytes, "idle", seed=12345, ctx=ctx)
    torch.cuda.reset_peak_memory_stats()

    started = time.monotonic()
    out = await generate_action(ref_bytes, "idle", seed=12346, ctx=ctx)
    elapsed = time.monotonic() - started

    assert isinstance(out, bytes)
    assert len(out) > 0
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"
    assert img.mode == "RGBA"
    assert img.size == (DEFAULT_OUTPUT_DIM, DEFAULT_OUTPUT_DIM)

    # Non-trivial alpha — at least one fully transparent pixel from
    # the rembg cleanup pass.
    alpha_band = img.getchannel("A")
    alpha_min, alpha_max = alpha_band.getextrema()
    assert alpha_min == 0, "no transparent pixels in output"
    assert alpha_max > 0, "output is fully transparent"

    assert elapsed < 5.0, f"warm wall-clock {elapsed:.2f}s exceeds 5s budget"
    peak_gb = float(torch.cuda.max_memory_allocated()) / float(1024**3)
    assert peak_gb < 6.0, f"peak VRAM {peak_gb:.2f} GB exceeds 6 GB budget"
