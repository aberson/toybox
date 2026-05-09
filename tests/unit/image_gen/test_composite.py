"""Unit coverage for :mod:`toybox.image_gen.composite`.

Phase F.5 Step F.5-3a. The composite path is pure CPU (Pillow +
rembg); we stub rembg so the test doesn't need ONNX runtime / the
~170 MB u2net model file. The stub returns the input bytes wrapped
in a Pillow RGBA image so the rest of the pipeline (resize, paste)
exercises real Pillow code.

Templates live under ``tests/fixtures/image_gen/composite_templates/``
in this test — generated procedurally inside a fixture so we don't
ship binary PNGs in tree. The fixture redirects the composite
module's ``TOYBOX_SPRITE_TEMPLATES_DIR`` env var so production
templates aren't read.
"""

from __future__ import annotations

import io
import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from toybox.image_gen import composite
from toybox.image_gen.composite import (
    TEMPLATES_DIR_ENV,
    composite_action,
    reset_caches_for_tests,
)
from toybox.image_gen.models import (
    GenerationContext,
    ImageGenCapacityError,
)

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_template(path: Path, *, color: tuple[int, int, int, int], size: int = 256) -> None:
    """Write one solid-color RGBA PNG template at ``path``."""
    img = Image.new("RGBA", (size, size), color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")


def _make_reference_jpeg(
    color: tuple[int, int, int] = (200, 100, 50),
    size: tuple[int, int] = (64, 64),
) -> bytes:
    """Build a small JPEG to use as the toy reference image."""
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def _ctx() -> GenerationContext:
    return GenerationContext(
        toy_display_name="Sparkle Unicorn",
        persona_display_name=None,
        tags=("plush", "unicorn"),
    )


@pytest.fixture(autouse=True)
def _reset_composite_caches() -> Iterator[None]:
    """Drop the per-process template + manifest caches between tests."""
    reset_caches_for_tests()
    yield
    reset_caches_for_tests()


@pytest.fixture(autouse=True)
def stub_rembg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake ``rembg`` module so the composite path runs without
    ONNX runtime / the u2net model file.

    The stub mimics the production interface:

    * ``new_session(model_name=..., providers=...)`` returns a sentinel.
    * ``remove(input_bytes, session=...)`` returns the input image as
      bytes after a Pillow round-trip into RGBA, with one corner pixel
      forced fully transparent so alpha-channel assertions work.
    """

    def _new_session(*, model_name: str, providers: list[str]) -> object:
        # Sanity-pin: production path must request u2net + CPUExecutionProvider.
        assert model_name == "u2net"
        assert providers == ["CPUExecutionProvider"]
        return object()

    def _remove(input_bytes: bytes, *, session: object) -> bytes:
        # Decode the JPEG / PNG to RGBA, force one transparent pixel,
        # re-encode as PNG bytes (matches the real rembg's PNG output).
        img = Image.open(io.BytesIO(input_bytes)).convert("RGBA")
        pixels = img.load()
        if pixels is not None:
            # Top-left fully transparent so alpha tests fire.
            pixels[0, 0] = (0, 0, 0, 0)
            # Mid-image fully opaque so non-trivial alpha assertions
            # see a clear OPAQUE pixel.
            pixels[img.size[0] // 2, img.size[1] // 2] = (255, 0, 0, 255)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    fake = types.ModuleType("rembg")
    fake.new_session = _new_session  # type: ignore[attr-defined]
    fake.remove = _remove  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rembg", fake)


@pytest.fixture
def templates_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a tests/fixtures-style templates dir at ``tmp_path``.

    Two slots seeded with distinct ``toy_box`` regions and one with
    ``behind=True`` so the per-slot variant tests can dispatch.
    """
    root = tmp_path / "composite_templates"
    root.mkdir()

    # Two solid-color templates so we can detect template pixels in
    # the composite output.
    _make_template(root / "idle.png", color=(0, 255, 0, 200))
    _make_template(root / "pointing.png", color=(0, 0, 255, 200))
    _make_template(root / "looking.png", color=(255, 255, 0, 200))

    manifest: dict[str, dict[str, Any]] = {
        "idle": {"toy_box": [40, 40, 200, 200], "behind": False},
        # Pointing uses a tighter box + ``behind=True`` so output
        # differs from the idle / looking variants.
        "pointing": {"toy_box": [60, 80, 180, 220], "behind": True},
        # Looking uses a different box (smaller) so the per-slot box
        # test sees distinct output sizes.
        "looking": {"toy_box": [10, 10, 100, 100], "behind": False},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setenv(TEMPLATES_DIR_ENV, str(root))
    # Drop the cache again now that the env is in place — the autouse
    # fixture ran earlier in collection.
    reset_caches_for_tests()
    return root


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


async def test_composite_produces_valid_rgba_png_with_alpha(
    templates_root: Path,
) -> None:
    """Output is a valid PNG, RGBA mode, with at least one non-trivial alpha."""
    raw = await composite_action(
        _make_reference_jpeg(),
        slot="idle",
        seed=12345,
        ctx=_ctx(),
    )
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    img = Image.open(io.BytesIO(raw))
    assert img.format == "PNG"
    assert img.mode == "RGBA"
    # The output is sized to the kiosk target (128×128 per spec).
    assert img.size == (composite.OUTPUT_DIM, composite.OUTPUT_DIM)

    # Alpha channel has variance — at least one transparent and one
    # opaque pixel. Grab the alpha band and check min/max.
    alpha = img.getchannel("A")
    extrema = alpha.getextrema()
    assert extrema is not None
    a_min, a_max = extrema
    assert a_min < 255, "expected at least one non-fully-opaque pixel"
    assert a_max > 0, "expected at least one non-fully-transparent pixel"


async def test_missing_template_raises_capacity_error(
    templates_root: Path,
) -> None:
    """A slot with no template PNG raises :class:`ImageGenCapacityError`.

    The manifest fixture only declares ``idle``/``pointing``/``looking``
    — every other slot is unmapped, so requesting one triggers the
    F.5-3a missing-template branch (the worker maps this to
    ``error_msg="image_gen_composite_only"``).
    """
    with pytest.raises(ImageGenCapacityError) as exc_info:
        await composite_action(
            _make_reference_jpeg(),
            slot="cheering",  # not in the test manifest
            seed=1,
            ctx=_ctx(),
        )
    assert "composite template missing" in str(exc_info.value)
    assert "cheering" in str(exc_info.value)


async def test_missing_manifest_raises_capacity_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No manifest.json at all → :class:`ImageGenCapacityError`."""
    empty = tmp_path / "empty_templates"
    empty.mkdir()
    # Drop just one template PNG — no manifest.json.
    _make_template(empty / "idle.png", color=(255, 0, 0, 255))
    monkeypatch.setenv(TEMPLATES_DIR_ENV, str(empty))
    reset_caches_for_tests()

    with pytest.raises(ImageGenCapacityError):
        await composite_action(
            _make_reference_jpeg(),
            slot="idle",
            seed=1,
            ctx=_ctx(),
        )


async def test_invalid_slot_raises_value_error(
    templates_root: Path,
) -> None:
    """Slot outside :data:`ACTION_SLOTS` is rejected before disk access."""
    with pytest.raises(ValueError, match="ACTION_SLOTS"):
        await composite_action(
            _make_reference_jpeg(),
            slot="not-a-slot",
            seed=1,
            ctx=_ctx(),
        )


async def test_deterministic_output_for_same_inputs(
    templates_root: Path,
) -> None:
    """Two calls with the same reference + slot produce byte-identical PNGs.

    Pinned because seed is intentionally unused: composite output is
    determined by the cutout + template pixels, nothing random.
    """
    ref = _make_reference_jpeg()
    first = await composite_action(ref, slot="idle", seed=11, ctx=_ctx())
    second = await composite_action(ref, slot="idle", seed=22, ctx=_ctx())
    assert first == second, "composite output must be deterministic from inputs"


async def test_per_slot_toy_box_honored(
    templates_root: Path,
) -> None:
    """Different slots produce different output (per-slot toy_box + template).

    Both ``idle`` and ``looking`` have ``behind=False`` but different
    boxes + different template colors. The output bytes must differ —
    proves the manifest's toy_box AND template-per-slot are both
    being read.
    """
    ref = _make_reference_jpeg()
    idle_out = await composite_action(ref, slot="idle", seed=1, ctx=_ctx())
    looking_out = await composite_action(ref, slot="looking", seed=1, ctx=_ctx())
    assert idle_out != looking_out


async def test_behind_flag_changes_output(
    templates_root: Path,
) -> None:
    """``behind=True`` (pointing) vs ``behind=False`` (idle) → different bytes.

    The idle template is green at full alpha 200; the pointing template
    is blue. ``behind=True`` puts the cutout under the template so the
    template pixels (where they overlap the cutout) show on top;
    ``behind=False`` puts the template under the cutout so cutout
    pixels show on top. Even for the same cutout these produce
    structurally different output.
    """
    ref = _make_reference_jpeg()
    over_out = await composite_action(ref, slot="idle", seed=1, ctx=_ctx())
    under_out = await composite_action(ref, slot="pointing", seed=1, ctx=_ctx())
    assert over_out != under_out


