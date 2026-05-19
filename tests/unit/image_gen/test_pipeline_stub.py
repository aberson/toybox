"""Stub-runtime coverage for :func:`toybox.image_gen.pipeline.generate_action`.

Exercises orchestration (timeout, OOM envelope, determinism)
without touching torch. ``TOYBOX_IMAGE_GEN_STUB=1`` short-circuits
the heavy path; the stub fixture honors the same env knobs the
real pipeline would.

Also covers the plain helpers exposed by ``pipeline.py`` whose
behaviour is independent of the heavy path:

* :func:`_build_prompt` — DB-fields prompt template (Phase P drops
  the palette-hex tokens; identity/colour now ride IP-Adapter Plus).
* :func:`_cartoon_mode` — env dispatch validation
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock

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
    DEFAULT_IP_ADAPTER_PATH,
    DEFAULT_IP_ADAPTER_SUBFOLDER,
    DEFAULT_IP_ADAPTER_WEIGHT_NAME,
    IP_ADAPTER_SCALE,
    STUB_DELAY_ENV,
    STUB_ENV,
    STUB_MODE_ENV,
    TIMEOUT_ENV,
    _build_pipeline,
    _build_prompt,
    _cartoon_mode,
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


def test_build_prompt_with_persona_and_tags() -> None:
    prompt = _build_prompt(
        "pointing",
        _ctx(persona="Hopper", name="Bunny", tags=("plush", "soft", "pink")),
    )
    assert "Hopper the Bunny" in prompt
    assert "plush, soft, pink" in prompt
    assert "pointing at something off to the side" in prompt
    assert "2D cartoon, simple shapes, clean lines, transparent background" in prompt


def test_build_prompt_without_persona_uses_a_intro() -> None:
    prompt = _build_prompt(
        "idle",
        _ctx(persona=None, name="Bunny", tags=("plush",)),
    )
    assert prompt.startswith("a Bunny,")
    assert "Hopper" not in prompt


def test_build_prompt_with_empty_tags_degrades_gracefully() -> None:
    """Empty ``ctx.tags`` produces an empty tag fragment without crashing."""
    prompt = _build_prompt(
        "idle",
        _ctx(persona=None, name="Bunny", tags=()),
    )
    assert "a Bunny" in prompt
    # The tag fragment is the empty string; no NoneType / KeyError.
    assert "None" not in prompt


def test_build_prompt_drops_palette_hex_tokens() -> None:
    """Phase P regression: ``primary color #...`` tokens must NOT appear.

    The Phase P pipeline rewrite dropped the palette-hex tokens — IPA
    Plus now carries identity / colour conditioning. The hex tokens
    biased SD 1.5 toward rendering literal text glyphs of the codes,
    which the extended negative prompt explicitly suppresses.
    """
    prompt = _build_prompt(
        "idle",
        _ctx(persona="Hopper", name="Bunny", tags=("plush",)),
    )
    assert "primary color" not in prompt
    # Tightened: target the exact joined phrase the old palette-token
    # format used (``"primary color #..."``) so legitimate future prompt
    # tokens like ``"#1 priority"`` don't false-positive.
    assert "primary color #" not in prompt


def test_build_prompt_unknown_slot_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown action slot"):
        _build_prompt("not-a-slot", _ctx())


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


# ---------------------------------------------------------------------
# _build_pipeline IPA wiring
# ---------------------------------------------------------------------
#
# These tests lock in the silent-wiring rule (workspace
# code-quality.md § "New components require an integration test"):
# the integration test in tests/integration/test_image_gen_worker_e2e.py
# proves the ``ip_adapter_image=`` kwarg reaches the production
# ``pipe(...)`` call, but a future refactor could drop the
# ``load_ip_adapter`` + ``set_ip_adapter_scale`` calls inside
# ``_build_pipeline`` while keeping the kwarg, and only a real-GPU
# test (operator-only, not in CI) would catch it. These unit tests
# pin the load shape independently.


@pytest.mark.parametrize(
    "mode",
    [CARTOON_MODE_CHECKPOINT, CARTOON_MODE_LORA],
)
def test_build_pipeline_loads_ip_adapter_with_pinned_args(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """``_build_pipeline`` must call ``load_ip_adapter`` + ``set_ip_adapter_scale``
    with the constants pinned in ``pipeline.py``, for BOTH cartoon modes.

    Approach: monkeypatch ``diffusers.StableDiffusionPipeline.from_pretrained``
    and ``diffusers.LCMScheduler.from_config`` so ``_build_pipeline``
    reaches the IPA section without touching the filesystem or GPU.
    The returned MagicMock pipe records every method call.
    """
    monkeypatch.setenv(CARTOON_MODE_ENV, mode)

    import diffusers

    fake_pipe = MagicMock(name="fake_sd_pipeline")
    # ``pipe.scheduler.config`` is read by the LCMScheduler.from_config
    # call; MagicMock auto-creates the chain, no extra setup needed.

    def _fake_from_pretrained(*_args: Any, **_kwargs: Any) -> MagicMock:
        return fake_pipe

    def _fake_lcm_from_config(*_args: Any, **_kwargs: Any) -> MagicMock:
        return MagicMock(name="fake_scheduler")

    monkeypatch.setattr(
        diffusers.StableDiffusionPipeline,
        "from_pretrained",
        _fake_from_pretrained,
    )
    monkeypatch.setattr(
        diffusers.LCMScheduler,
        "from_config",
        _fake_lcm_from_config,
    )

    # Minimal fake torch satisfying the ``torch_mod`` param. Only
    # ``float16`` (attr lookup) and ``cuda.is_available`` (exception
    # path, not exercised here) are referenced by ``_build_pipeline``.
    fake_torch = MagicMock(name="fake_torch")
    fake_torch.float16 = "fp16-sentinel"

    result = _build_pipeline(fake_torch)

    assert result is fake_pipe

    # IPA load: pinned constants, single call.
    assert fake_pipe.load_ip_adapter.call_count == 1, (
        f"load_ip_adapter must be called exactly once; got "
        f"{fake_pipe.load_ip_adapter.call_args_list!r}"
    )
    fake_pipe.load_ip_adapter.assert_called_once_with(
        DEFAULT_IP_ADAPTER_PATH,
        subfolder=DEFAULT_IP_ADAPTER_SUBFOLDER,
        weight_name=DEFAULT_IP_ADAPTER_WEIGHT_NAME,
    )

    # IPA scale: pinned value, single call.
    assert fake_pipe.set_ip_adapter_scale.call_count == 1, (
        f"set_ip_adapter_scale must be called exactly once; got "
        f"{fake_pipe.set_ip_adapter_scale.call_args_list!r}"
    )
    fake_pipe.set_ip_adapter_scale.assert_called_once_with(IP_ADAPTER_SCALE)
