"""Pin: importing the ``toybox.tts`` package does NOT eagerly load
the ``tts`` extra's deps (``kokoro_onnx``, ``soundfile``) — nor
``onnxruntime`` (core dep, but the engine must not build a session at
import time).

The lazy-import contract is what lets the CI / no-extra hosts import
the package, probe capability, and run the stub path. Mirrors
``tests/unit/image_gen/test_lazy_imports.py`` — subprocess snapshot so
the check runs against clean interpreter state.
"""

from __future__ import annotations

import subprocess
import sys

_FORBIDDEN = ("kokoro_onnx", "soundfile", "onnxruntime")


def _check_lazy(import_target: str) -> None:
    forbidden_repr = repr(list(_FORBIDDEN))
    snippet = (
        f"import sys\n"
        f"import {import_target}\n"
        f"forbidden = set({forbidden_repr})\n"
        f"leaked = sorted(forbidden & set(sys.modules))\n"
        f"if leaked:\n"
        f"    raise SystemExit('LEAK:' + ','.join(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stdout + result.stderr
        if "LEAK:" in message:
            leaked = message.split("LEAK:", 1)[1].strip()
            raise AssertionError(f"{import_target} eagerly imported heavy deps: {leaked}")
        raise AssertionError(
            f"subprocess importing {import_target} failed unexpectedly:\n{message}"
        )


def test_tts_package_top_level_does_not_load_heavy_deps() -> None:
    _check_lazy("toybox.tts")


def test_engine_module_does_not_eagerly_import_heavy_deps() -> None:
    _check_lazy("toybox.tts.engine")


def test_download_cli_module_does_not_eagerly_import_heavy_deps() -> None:
    _check_lazy("toybox.tts.__main__")
