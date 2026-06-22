"""Phase Y Step Y7 — per-call IP-Adapter scale override (identity infra).

Covers the testable seams without a GPU: the ``GenerationContext.ipa_scale``
field (additive, defaults None), the ``_resolve_ipa_scale`` resolution helper
(override vs module default), and that an override neither breaks the stub path
nor perturbs seed determinism. The actual ``pipe.set_ip_adapter_scale`` call
lives in the real-pipeline path and is exercised on operator hardware.
"""

from __future__ import annotations

import pytest

from toybox.image_gen.models import GenerationContext
from toybox.image_gen.pipeline import (
    IP_ADAPTER_SCALE,
    STUB_ENV,
    _resolve_ipa_scale,
    generate_action,
)


def _ctx(ipa_scale: float | None = None) -> GenerationContext:
    return GenerationContext(
        toy_display_name="Penguin",
        persona_display_name=None,
        tags=(),
        ipa_scale=ipa_scale,
    )


def test_generation_context_ipa_scale_defaults_none() -> None:
    # Additive field: existing keyword constructions (3 args) still work and
    # default the new field to None (byte-identical pre-Y behaviour).
    ctx = GenerationContext(toy_display_name="Bear", persona_display_name=None, tags=())
    assert ctx.ipa_scale is None


def test_resolve_ipa_scale_defaults_to_module_constant() -> None:
    assert _resolve_ipa_scale(_ctx()) == IP_ADAPTER_SCALE
    assert IP_ADAPTER_SCALE == 0.6  # pin the documented default


def test_resolve_ipa_scale_honors_override() -> None:
    assert _resolve_ipa_scale(_ctx(0.8)) == 0.8


def test_resolve_ipa_scale_zero_is_an_explicit_override() -> None:
    # 0.0 is a real value (disables IPA), distinct from None ("use default").
    assert _resolve_ipa_scale(_ctx(0.0)) == 0.0


async def test_stub_generate_action_unaffected_by_ipa_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STUB_ENV, "1")
    # Same (slot, seed) must yield identical stub bytes regardless of the
    # ipa_scale override — the override must not leak into the determinism key,
    # and a set override must not break the call path.
    with_override = await generate_action(b"ref", "idle", seed=7, ctx=_ctx(0.9))
    without_override = await generate_action(b"ref", "idle", seed=7, ctx=_ctx(None))
    assert with_override == without_override
