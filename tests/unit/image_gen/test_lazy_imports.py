"""Pin: ``import toybox.image_gen.pipeline`` does NOT eagerly load
torch / diffusers / transformers / rembg.

These deps are installed only via the ``image_gen`` optional extra.
The lazy-import contract is what lets non-GPU hosts run the rest
of toybox without paying for these huge wheels. If somebody adds
a top-level ``import torch`` to ``pipeline.py`` (or to a module
``pipeline.py`` transitively imports), this test fails.

We use a subprocess for the snapshot so the assertion runs against a
clean Python state — popping modules from ``sys.modules`` in the
current process would corrupt the identity of pipeline-defined
classes (notably ``_StubCudaOOM``) used by sibling tests.
"""

from __future__ import annotations

import subprocess
import sys

_FORBIDDEN = ("torch", "diffusers", "transformers", "rembg")


def _check_lazy(import_target: str) -> None:
    """Spawn a subprocess that imports ``import_target`` and checks
    ``sys.modules`` for forbidden entries. Raises AssertionError on
    leak; the subprocess's exit code distinguishes leak vs. import
    failure."""
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
            raise AssertionError(
                f"{import_target} eagerly imported heavy deps: {leaked}"
            )
        raise AssertionError(
            f"subprocess importing {import_target} failed unexpectedly:\n{message}"
        )


def test_pipeline_module_does_not_eagerly_import_heavy_deps() -> None:
    _check_lazy("toybox.image_gen.pipeline")


def test_image_gen_package_top_level_does_not_load_heavy_deps() -> None:
    _check_lazy("toybox.image_gen")
