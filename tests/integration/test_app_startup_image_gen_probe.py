"""Phase F Step F3 — image-gen capability boot probe is logged at startup.

The probe runs in the synchronous ``create_app`` body so the line lands
in the journal before any request hits the server. Two tests:

1. The probe runs once per ``create_app()`` and logs both ``capable``
   and a non-empty ``reason``.
2. Monkeypatching the model dir to an empty path forces the
   missing-checkpoints branch — we assert the reason text reflects
   that branch (not the cuda-unavailable / vram branches).

The test does NOT depend on a real GPU. On a CI host without torch the
probe falls through to ``capable=False reason="CUDA not available"``,
which is fine — the test verifies the LOG was emitted, not the verdict.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from toybox import app as app_module
from toybox.app import create_app
from toybox.image_gen.capability import ENABLED_ENV, MODEL_DIR_ENV


def test_create_app_logs_image_gen_capability(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``create_app()`` must log a ``capability=...`` INFO line.

    The line is the seam ops use to tell at-a-glance whether image-gen
    is degraded on a fresh boot. We pin the format so the journal
    parser doesn't drift.
    """
    # Force a deterministic branch: empty model dir → missing-checkpoints
    # *or* CUDA not available, depending on the host. Either way the
    # log line shape is the same. We force ENABLED unset so the
    # env-disabled branch doesn't short-circuit.
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))

    # ``app_module.__name__`` is ``toybox.app`` — same string the boot
    # probe's _logger uses.
    with caplog.at_level(logging.INFO, logger=app_module.__name__):
        create_app()

    matches = [
        r
        for r in caplog.records
        if r.name == app_module.__name__
        and r.levelname == "INFO"
        and "image-gen capability=" in r.getMessage()
    ]
    assert matches, [r.getMessage() for r in caplog.records]
    msg = matches[-1].getMessage()
    # Format: "image-gen capability=<bool> reason=<text>"
    assert re.match(r"image-gen capability=(True|False) reason=.+", msg), msg


def test_boot_probe_missing_checkpoints_branch(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Force the missing-checkpoints branch via ``monkeypatch``.

    Stubs the cuda probe to return ``(True, 99 GB)`` so the gate
    progresses past the first three branches; the empty model dir
    then surfaces the missing-checkpoints reason. The boot-probe log
    line must reflect that branch.
    """
    # Pretend torch + plenty of VRAM so cuda + vram checks pass.
    from toybox.image_gen import capability as capability_mod

    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.setattr(
        capability_mod,
        "_probe_cuda_and_vram",
        lambda: (True, 99.0),
    )
    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path))  # empty → all missing

    with caplog.at_level(logging.INFO, logger=app_module.__name__):
        create_app()

    info_records = [
        r
        for r in caplog.records
        if r.name == app_module.__name__
        and r.levelname == "INFO"
        and "image-gen capability=" in r.getMessage()
    ]
    assert info_records, [r.getMessage() for r in caplog.records]
    msg = info_records[-1].getMessage()
    assert "capability=False" in msg
    # ``is_image_gen_capable``'s missing-checkpoints branch returns a
    # reason that starts with "checkpoints missing:".
    assert "checkpoints missing" in msg, msg


def test_boot_probe_swallows_unexpected_exception(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-ImportError from the probe must not crash ``create_app()``.

    Real-world failure mode: corrupt CUDA driver / mismatched torch
    build raises ``RuntimeError`` from the lazy probe. The boot path
    is informational; we wrap it in broad-except + log a WARNING and
    treat the result as ``(False, "<exception class name>")`` so the
    app still boots and the journal still has a clear signal.
    """

    def _explode() -> tuple[bool, str]:
        raise RuntimeError("synthetic CUDA driver mismatch")

    monkeypatch.setattr(app_module, "is_image_gen_capable", _explode)

    with caplog.at_level(logging.INFO, logger=app_module.__name__):
        app = create_app()

    # App boot succeeded.
    assert app is not None

    # WARNING with the exception class name.
    warnings = [
        r
        for r in caplog.records
        if r.name == app_module.__name__
        and r.levelname == "WARNING"
        and "RuntimeError" in r.getMessage()
        and "image-gen capability probe raised" in r.getMessage()
    ]
    assert warnings, [r.getMessage() for r in caplog.records]

    # INFO line still emitted with the synthesized fallback reason.
    info_records = [
        r
        for r in caplog.records
        if r.name == app_module.__name__
        and r.levelname == "INFO"
        and "image-gen capability=" in r.getMessage()
    ]
    assert info_records, [r.getMessage() for r in caplog.records]
    msg = info_records[-1].getMessage()
    assert "capability=False" in msg
    assert "probe raised RuntimeError" in msg, msg
