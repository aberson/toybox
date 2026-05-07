"""Regression test for the F9 silent-skip bug surfaced in 2026-05-07.

Operator wrote ``TOYBOX_IMAGE_GEN_MIN_VRAM_GB=6`` to ``.env`` per the F1
runbook, expecting the toybox backend to honor it. The backend never
loaded ``.env``, so the capability gate stayed at the default 12 GB
floor, the F5 toy-commit hook saw ``capable=False``, silently skipped
``worker.enqueue``, and the parent UI's :class:`ToyActionGrid` mounted
with "0/10 done" forever.

The fix added :func:`dotenv.load_dotenv` at the top of
``src/toybox/main.py`` BEFORE any toybox imports.

This test pins the contract:

* ``import toybox.main`` in a fresh Python process WITHIN a directory
  containing ``.env`` produces a populated ``os.environ`` for the keys
  declared in ``.env``.
* Existing shell env wins over ``.env`` (default ``load_dotenv``
  semantics — shell precedence preserved so users who set vars
  manually still see their values).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_probe_process(cwd: Path, env_overrides: dict[str, str]) -> str:
    """Run a subprocess that imports toybox.main and prints the probe var.

    The subprocess inherits the parent's PATH/PYTHONPATH but only the
    explicit env_overrides on top of that — so the test controls
    precedence cleanly.
    """
    import os

    # Start from a copy of the parent env so subprocess can find python +
    # uv + the toybox source — but strip any pre-existing probe value
    # so the test's .env-vs-shell precedence assertions are deterministic.
    env = {k: v for k, v in os.environ.items() if k != "TOYBOX_FIXBUG_PROBE"}
    env.update(env_overrides)
    cmd = [
        sys.executable,
        "-c",
        (
            "import os; "
            "import toybox.main as _;  "
            "print(os.environ.get('TOYBOX_FIXBUG_PROBE', '<unset>'))"
        ),
    ]
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        msg = (
            f"probe subprocess exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        raise AssertionError(msg)
    return result.stdout.strip()


def test_dotenv_in_cwd_is_loaded_when_main_imports(tmp_path: Path) -> None:
    """Writing ``.env`` in CWD before importing ``toybox.main`` populates env."""
    (tmp_path / ".env").write_text(
        "TOYBOX_FIXBUG_PROBE=loaded_from_dotenv\n",
        encoding="utf-8",
    )
    out = _run_probe_process(tmp_path, env_overrides={})
    assert out == "loaded_from_dotenv", (
        f"expected '.env' value to land in os.environ, got {out!r}"
    )


def test_shell_env_wins_over_dotenv(tmp_path: Path) -> None:
    """Shell-level env beats ``.env`` value (load_dotenv default semantics)."""
    (tmp_path / ".env").write_text(
        "TOYBOX_FIXBUG_PROBE=loaded_from_dotenv\n",
        encoding="utf-8",
    )
    out = _run_probe_process(
        tmp_path,
        env_overrides={"TOYBOX_FIXBUG_PROBE": "from_shell"},
    )
    assert out == "from_shell", (
        f"expected shell env to win over .env, got {out!r}"
    )


def test_no_dotenv_file_no_probe_value(tmp_path: Path) -> None:
    """Absent ``.env`` leaves the probe variable unset — sanity check."""
    out = _run_probe_process(tmp_path, env_overrides={})
    assert out == "<unset>", (
        f"expected no .env to mean probe stays unset, got {out!r}"
    )
