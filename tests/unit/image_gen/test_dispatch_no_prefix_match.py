"""F.5-3a structural regression: dispatch must not prefix-match capability strings.

The contract is "no prefix-string matching on capability reason in
dispatch code". Worker dispatch + REST routes branch on the
:class:`CapabilityReason` enum; the human-readable detail string is
for UI display only. A future refactor that re-introduces a
``startswith("image-gen disabled ...")`` or
``startswith("TOYBOX_IMAGE_GEN_ENABLED ...")`` check on the detail
string would silently couple dispatch to the UI copy. This test
fails on the regression.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repository root resolved relative to this test file:
# ``<repo>/tests/unit/image_gen/test_*.py``  →  ``<repo>``.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Roots whose .py files must not contain the banned prefix-match
# patterns. ``app.py`` is a single file so we list its directory and
# filter.
_PYTHON_TARGETS: tuple[Path, ...] = (
    _REPO_ROOT / "src" / "toybox" / "image_gen",
    _REPO_ROOT / "src" / "toybox" / "api",
    _REPO_ROOT / "src" / "toybox" / "app.py",
)

_BANNED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"startswith.*image-gen disabled"),
    re.compile(r"startswith.*TOYBOX_IMAGE_GEN_ENABLED"),
)


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for target in _PYTHON_TARGETS:
        if target.is_file() and target.suffix == ".py":
            files.append(target)
        elif target.is_dir():
            files.extend(target.rglob("*.py"))
    return files


def test_no_prefix_match_on_capability_reason_in_dispatch_code() -> None:
    py_files = _iter_py_files()
    assert py_files, f"no .py files under {_PYTHON_TARGETS!r}"

    offenders: list[tuple[Path, int, str]] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern in _BANNED_PATTERNS:
                if pattern.search(line):
                    offenders.append((path, line_no, line.strip()))

    if offenders:
        formatted = "\n".join(
            f"  {path.relative_to(_REPO_ROOT)}:{line_no}: {snippet}"
            for path, line_no, snippet in offenders
        )
        pytest.fail(
            "dispatch code must not prefix-match capability reason strings; "
            "branch on CapabilityReason enum instead.\n" + formatted
        )
