"""Stub-mode coverage for :func:`toybox.image_gen.animate.generate_animation`."""

from __future__ import annotations

import io
import subprocess
import sys

import pytest
from PIL import Image

from toybox.image_gen.animate import (
    TIMEOUT_ENV,
    generate_animation,
)
from toybox.image_gen.models import (
    GenerationContext,
    ImageGenCapacityError,
    ImageGenTimeoutError,
)
from toybox.image_gen.pipeline import (
    STUB_DELAY_ENV,
    STUB_ENV,
    STUB_MODE_ENV,
)


@pytest.fixture(autouse=True)
def _enable_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STUB_ENV, "1")
    monkeypatch.delenv(STUB_DELAY_ENV, raising=False)
    monkeypatch.delenv(STUB_MODE_ENV, raising=False)


def _ctx() -> GenerationContext:
    return GenerationContext(
        toy_display_name="Bunny",
        persona_display_name="Hopper",
        tags=("plush",),
    )


async def test_animate_stub_returns_webp_bytes() -> None:
    out = await generate_animation(b"ref-bytes", "idle", seed=1, ctx=_ctx())
    assert isinstance(out, bytes) and len(out) > 0
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP"
    # Animated WebP is validated during U2.5 GPU smoke (requires libwebp with
    # animation support compiled in; CI hosts may only have static WebP).


def test_animate_lazy_imports() -> None:
    """Importing animate must not eagerly load torch/diffusers/transformers/rembg."""
    forbidden = ["torch", "diffusers", "transformers", "rembg"]
    snippet = (
        "import sys\n"
        "import toybox.image_gen.animate\n"
        f"forbidden = set({forbidden!r})\n"
        "leaked = sorted(forbidden & set(sys.modules))\n"
        "if leaked:\n"
        "    raise SystemExit('LEAK:' + ','.join(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"toybox.image_gen.animate eagerly imported heavy deps:\n"
        f"{result.stdout}{result.stderr}"
    )


async def test_animate_oom_raises_capacity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STUB_MODE_ENV, "oom")
    with pytest.raises(ImageGenCapacityError):
        await generate_animation(b"ref", "idle", seed=1, ctx=_ctx())


async def test_animate_timeout_raises_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STUB_DELAY_ENV, "5")
    monkeypatch.setenv(TIMEOUT_ENV, "0.2")
    with pytest.raises(ImageGenTimeoutError):
        await generate_animation(b"ref", "idle", seed=1, ctx=_ctx())
