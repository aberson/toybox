"""Phase N Step N4 — deterministic element_microgame generator (unit tests).

Coverage for ``scripts/generate_element_microgames.py``. The generator
reads two corpora:

* ``data/elements/elements.json`` — the 118-element corpus (M1).
* ``data/elements/distractors.json`` — the 118-distractor corpus (N1).

…and writes 118 ``element_microgame_<id>`` templates into the shared
``request_activity.json`` intent file, idempotently stripping the
previous ``element_microgame_*`` cohort before appending and leaving
``meet_element_*`` (M4) siblings untouched.

Plan §N4 done-when:

    generator unit-tested with deterministic seed (same input →
    byte-identical JSON)

Style mirrors :mod:`tests.unit.test_generate_distractor_corpus` (the
N1.5 precedent that shipped 18 tests two commits earlier):
``importlib.util.spec_from_file_location`` to load ``scripts/`` (which
is not a Python package); ``tmp_path`` sandboxes; ``monkeypatch.setenv``
for ``TOYBOX_DATA_DIR`` + ``TOYBOX_ALLOW_LLM_DISTRACTORS``. ``--validate``
subprocess tests additionally set ``TOYBOX_TEMPLATES_DIR`` so the loader
walks the sandbox templates dir instead of the in-package one.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from toybox.activities.distractor_corpus import clear_distractor_cache
from toybox.activities.element_corpus import clear_element_cache

# ---------------------------------------------------------------------
# Module loading (scripts/ is not a Python package)
# ---------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "generate_element_microgames.py"
_REAL_ELEMENTS_JSON = _REPO_ROOT / "data" / "elements" / "elements.json"
_REAL_DISTRACTORS_JSON = _REPO_ROOT / "data" / "elements" / "distractors.json"
_REAL_DISTRACTORS_CREDITS = _REPO_ROOT / "data" / "elements" / "_distractors_credits.md"
_REAL_TEMPLATES_DIR = _REPO_ROOT / "src" / "toybox" / "activities" / "templates"
_REAL_REQUEST_ACTIVITY = _REAL_TEMPLATES_DIR / "branching" / "request_activity.json"
_REAL_SCHEMA = _REAL_TEMPLATES_DIR / "_schema.json"


def _load_generator_module() -> types.ModuleType:
    """Load scripts/generate_element_microgames.py via importlib.

    Mirrors :func:`tests.unit.scripts.test_f5_compute_manifest._load_module`.
    """
    spec = importlib.util.spec_from_file_location("_generate_element_microgames", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generator_module() -> Iterator[types.ModuleType]:
    """Per-test fresh load of the generator script as a module."""
    module = _load_generator_module()
    yield module
    sys.modules.pop("_generate_element_microgames", None)


# ---------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    """Both element + distractor + template caches must be path-keyed-clean per test."""
    clear_element_cache()
    clear_distractor_cache()
    # Best-effort template cache clear — generator module imports it lazily.
    try:
        from toybox.activities.generator import clear_template_cache

        clear_template_cache()
    except Exception:  # pragma: no cover — defensive
        pass
    yield
    clear_element_cache()
    clear_distractor_cache()
    try:
        from toybox.activities.generator import clear_template_cache

        clear_template_cache()
    except Exception:  # pragma: no cover — defensive
        pass


def _seed_data_dir(tmp_path: Path) -> Path:
    """Lay down ``elements/`` under ``tmp_path`` with real elements + distractors JSON.

    Returns the ``tmp_path`` so callers can monkey-patch ``TOYBOX_DATA_DIR``.
    """
    elements_dir = tmp_path / "elements"
    elements_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REAL_ELEMENTS_JSON, elements_dir / "elements.json")
    shutil.copy(_REAL_DISTRACTORS_JSON, elements_dir / "distractors.json")
    shutil.copy(_REAL_DISTRACTORS_CREDITS, elements_dir / "_distractors_credits.md")
    return tmp_path


def _seed_templates_dir(tmp_path: Path) -> Path:
    """Lay down a ``templates/branching/request_activity.json`` skeleton under tmp_path.

    Copies the real schema + the real request_activity file so the loader
    walks the sandbox without polluting the production templates dir.
    Returns the templates dir (``tmp_path/templates``).
    """
    templates_dir = tmp_path / "templates"
    branching_dir = templates_dir / "branching"
    branching_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REAL_SCHEMA, templates_dir / "_schema.json")
    shutil.copy(_REAL_REQUEST_ACTIVITY, branching_dir / "request_activity.json")
    return templates_dir


def _enable_llm_distractors(monkeypatch: pytest.MonkeyPatch) -> None:
    """The N1.5 LLM-flag gate must be open for load_distractors() to succeed."""
    monkeypatch.setenv("TOYBOX_ALLOW_LLM_DISTRACTORS", "1")


def _run_generator(
    generator_module: types.ModuleType,
    *,
    output: Path,
    validate: bool = False,
    force: bool = False,
) -> None:
    """Invoke the generator in-process via ``main(...)``.

    Default behavior: write to ``output`` (no --validate). The validate
    flag exercises the loader round-trip; callers must set
    ``TOYBOX_TEMPLATES_DIR`` to point at the sandbox first.
    """
    argv = ["--output", str(output)]
    if validate:
        argv.append("--validate")
    if force:
        argv.append("--force")
    rc = generator_module.main(argv)
    assert rc == 0, f"main({argv!r}) returned {rc!r}"


def _read_output_bytes(output: Path) -> bytes:
    return output.read_bytes()


def _load_microgames(output: Path) -> list[dict[str, Any]]:
    payload = json.loads(output.read_text(encoding="utf-8"))
    return [
        t
        for t in payload["templates"]
        if isinstance(t, dict) and str(t.get("id", "")).startswith("element_microgame_")
    ]


# ---------------------------------------------------------------------
# Determinism — primary done-when from plan §N4
# ---------------------------------------------------------------------


def test_running_generator_twice_produces_byte_identical_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generator_module: types.ModuleType,
) -> None:
    """Two separate sandboxes, same corpus → byte-identical output.

    This is the core plan §N4 done-when: ``generator unit-tested with
    deterministic seed (same input → byte-identical JSON)``.
    """
    sandbox_a = _seed_data_dir(tmp_path / "a")
    sandbox_b = _seed_data_dir(tmp_path / "b")
    templates_a = _seed_templates_dir(tmp_path / "a")
    templates_b = _seed_templates_dir(tmp_path / "b")
    output_a = templates_a / "branching" / "request_activity.json"
    output_b = templates_b / "branching" / "request_activity.json"

    _enable_llm_distractors(monkeypatch)

    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox_a))
    _run_generator(generator_module, output=output_a)
    bytes_a = _read_output_bytes(output_a)

    clear_element_cache()
    clear_distractor_cache()
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox_b))
    _run_generator(generator_module, output=output_b)
    bytes_b = _read_output_bytes(output_b)

    assert bytes_a == bytes_b, (
        "request_activity.json must be byte-identical across runs "
        "(plan §N4 done-when: deterministic seed)"
    )


def test_running_generator_twice_in_same_sandbox_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generator_module: types.ModuleType,
) -> None:
    """Second run over the same sandbox produces byte-identical bytes.

    Locks the strip-then-append idempotence property: a re-run with no
    corpus changes must not perturb the output file.
    """
    sandbox = _seed_data_dir(tmp_path)
    templates = _seed_templates_dir(tmp_path)
    output = templates / "branching" / "request_activity.json"

    _enable_llm_distractors(monkeypatch)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))

    _run_generator(generator_module, output=output)
    first = _read_output_bytes(output)

    _run_generator(generator_module, output=output)
    second = _read_output_bytes(output)

    assert first == second, "re-running generator over existing file must be idempotent"


# ---------------------------------------------------------------------
# Idempotence — strip-by-prefix must leave M4 siblings alone
# ---------------------------------------------------------------------


def test_strip_by_prefix_preserves_meet_element_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generator_module: types.ModuleType,
) -> None:
    """The strip pass must not touch ``meet_element_*`` (M4) entries.

    Pre-condition (smoke): the real seed file already has 118 meet_element_*
    entries. Post-condition: same 118 still present after a fresh microgame
    regenerate.
    """
    sandbox = _seed_data_dir(tmp_path)
    templates = _seed_templates_dir(tmp_path)
    output = templates / "branching" / "request_activity.json"

    _enable_llm_distractors(monkeypatch)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))

    # Smoke pre-check — fixture honestly contains the M4 cohort.
    pre_payload = json.loads(output.read_text(encoding="utf-8"))
    pre_meet = {
        str(t.get("id"))
        for t in pre_payload["templates"]
        if isinstance(t, dict) and str(t.get("id", "")).startswith("meet_element_")
    }
    assert pre_meet, "fixture must seed at least one meet_element_* entry"

    _run_generator(generator_module, output=output)

    post_payload = json.loads(output.read_text(encoding="utf-8"))
    post_meet = {
        str(t.get("id"))
        for t in post_payload["templates"]
        if isinstance(t, dict) and str(t.get("id", "")).startswith("meet_element_")
    }
    assert post_meet == pre_meet, (
        "regenerate must leave meet_element_* entries untouched; "
        f"only-pre={pre_meet - post_meet}, only-post={post_meet - pre_meet}"
    )


# ---------------------------------------------------------------------
# na-11 / ca-20 fallback — uses story_seed_hooks[0], not a cross-family peer
# ---------------------------------------------------------------------


def _load_corpus_dict() -> dict[str, dict[str, Any]]:
    """Return shipped elements indexed by element_id."""
    data = json.loads(_REAL_ELEMENTS_JSON.read_text(encoding="utf-8"))
    return {e["id"]: e for e in data}


@pytest.mark.parametrize("element_id", ["na-11", "ca-20"])
def test_sole_family_member_fallback_uses_story_seed_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generator_module: types.ModuleType,
    element_id: str,
) -> None:
    """``na-11`` and ``ca-20`` are the SOLE entries in their family at age_band 3-5
    so ``peer_in_family`` raises. The plan §5 N4 fallback uses
    ``story_seed_hooks[0]`` (with ``{name}`` substituted) as the Step 2 correct
    choice label. Lock the edge-case path.
    """
    sandbox = _seed_data_dir(tmp_path)
    templates = _seed_templates_dir(tmp_path)
    output = templates / "branching" / "request_activity.json"

    _enable_llm_distractors(monkeypatch)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, output=output)

    corpus = _load_corpus_dict()
    expected_hook = corpus[element_id]["story_seed_hooks"][0].replace(
        "{name}", corpus[element_id]["name"]
    )

    microgames = {t["id"]: t for t in _load_microgames(output)}
    template_id = f"element_microgame_{element_id.replace('-', '_')}"
    assert template_id in microgames, f"missing template for {element_id}"

    template = microgames[template_id]
    family_fork_step = template["steps"][1]
    assert family_fork_step["id"] == "family_fork"
    correct_label = family_fork_step["choices"][0]["label"]
    assert correct_label, f"{element_id}: Step-2 correct choice label must be non-empty"
    assert correct_label == expected_hook, (
        f"{element_id}: Step-2 correct choice must use story_seed_hooks[0] "
        f"(expected {expected_hook!r}, got {correct_label!r})"
    )


# ---------------------------------------------------------------------
# All 118 covered + shape contracts
# ---------------------------------------------------------------------


def test_all_118_elements_get_a_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generator_module: types.ModuleType,
) -> None:
    """For every element in elements.json, the output contains an
    ``element_microgame_<id>`` entry — no element skipped, no extras."""
    sandbox = _seed_data_dir(tmp_path)
    templates = _seed_templates_dir(tmp_path)
    output = templates / "branching" / "request_activity.json"

    _enable_llm_distractors(monkeypatch)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, output=output)

    corpus = _load_corpus_dict()
    expected_ids = {f"element_microgame_{eid.replace('-', '_')}" for eid in corpus.keys()}
    actual_ids = {t["id"] for t in _load_microgames(output)}

    assert actual_ids == expected_ids, (
        f"missing={expected_ids - actual_ids}; extra={actual_ids - expected_ids}"
    )
    assert len(actual_ids) == 118


def test_every_microgame_has_canonical_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generator_module: types.ModuleType,
) -> None:
    """Every emitted template has the Phase N microgame shape:

    * ``template_type == "element_microgame"``
    * ``required_roles == ["guide_mentor"]`` (Iridia-bias)
    * 4 steps in order: intro / family_fork / fact_fork / reward
    * ``ending_step`` is the auto-firing song reward with ``element_id`` set
    """
    sandbox = _seed_data_dir(tmp_path)
    templates = _seed_templates_dir(tmp_path)
    output = templates / "branching" / "request_activity.json"

    _enable_llm_distractors(monkeypatch)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, output=output)

    microgames = _load_microgames(output)
    assert len(microgames) == 118

    for t in microgames:
        tid = t["id"]
        assert t["template_type"] == "element_microgame", (
            f"{tid}: template_type must be 'element_microgame', got {t['template_type']!r}"
        )
        assert t["required_roles"] == ["guide_mentor"], (
            f"{tid}: required_roles must be ['guide_mentor'], got {t['required_roles']!r}"
        )
        step_ids = [s["id"] for s in t["steps"]]
        assert step_ids == ["intro", "family_fork", "fact_fork", "reward"], (
            f"{tid}: step ids must be intro/family_fork/fact_fork/reward, got {step_ids}"
        )
        assert t["ending_step"]["kind"] == "song", f"{tid}: ending_step.kind must be 'song'"
        assert t["ending_step"]["auto"] is True, f"{tid}: ending_step.auto must be True"
        assert t["ending_step"]["element_id"], (
            f"{tid}: ending_step.element_id must be set (Phase L song reward)"
        )


# ---------------------------------------------------------------------
# --validate CLI — exit 0 on clean, non-zero on corruption
# ---------------------------------------------------------------------


def _run_cli(
    *,
    sandbox: Path,
    templates_dir: Path,
    args: list[str],
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the generator script as a subprocess against a sandbox.

    Sets:
    * ``TOYBOX_DATA_DIR`` → sandbox (where elements.json + distractors.json live)
    * ``TOYBOX_TEMPLATES_DIR`` → sandbox templates dir (where the loader walks)
    * ``TOYBOX_ALLOW_LLM_DISTRACTORS=1`` → unblock the distractor loader's LLM gate

    We pass ``--output`` to the templates-dir sandbox so the generator
    writes there, not into the in-package production dir.
    """
    env = {
        **dict(os.environ),
        "TOYBOX_DATA_DIR": str(sandbox),
        "TOYBOX_TEMPLATES_DIR": str(templates_dir),
        "TOYBOX_ALLOW_LLM_DISTRACTORS": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=str(_REPO_ROOT),
    )


def test_validate_cli_exit_zero_on_clean_corpus(
    tmp_path: Path,
    generator_module: types.ModuleType,
) -> None:
    """``--validate`` against a freshly-generated clean sandbox exits 0
    and surfaces a useful confirmation string in stdout/stderr."""
    sandbox = _seed_data_dir(tmp_path)
    templates_dir = _seed_templates_dir(tmp_path)
    output = templates_dir / "branching" / "request_activity.json"

    # First pass — generate (no validate yet, just write the sandbox file).
    result_gen = _run_cli(
        sandbox=sandbox,
        templates_dir=templates_dir,
        args=["--output", str(output)],
    )
    assert result_gen.returncode == 0, (
        f"generate returncode={result_gen.returncode}; "
        f"stdout={result_gen.stdout!r}; stderr={result_gen.stderr!r}"
    )

    # Second pass — validate the file written above.
    result_val = _run_cli(
        sandbox=sandbox,
        templates_dir=templates_dir,
        args=["--output", str(output), "--validate"],
    )
    assert result_val.returncode == 0, (
        f"--validate returncode={result_val.returncode}; "
        f"stdout={result_val.stdout!r}; stderr={result_val.stderr!r}"
    )
    combined = (result_val.stdout + result_val.stderr).lower()
    assert "118" in combined, f"--validate confirmation should surface count 118; got: {combined!r}"
    assert "validated" in combined or "loaded" in combined or "cleanly" in combined, (
        f"--validate stdout/stderr should announce success; got: {combined!r}"
    )


def test_validate_cli_exit_nonzero_on_corruption(
    tmp_path: Path,
    generator_module: types.ModuleType,
) -> None:
    """Corrupt a non-microgame sibling so the regenerate pass leaves the
    corruption in place → ``--validate`` exits non-zero and surfaces the
    count mismatch.

    Why corrupt a sibling instead of a microgame: the script is
    strip-then-append over the ``element_microgame_`` prefix on every
    run, so dropping a microgame entry and then invoking ``--validate``
    just resurrects it. Corrupting a ``meet_element_*`` (M4) entry
    preserves the corruption through regenerate — the strip pass only
    targets the microgame prefix, so the bad row stays on disk and
    fails the loader's whole-file schema check, which drops every
    template including the 118 fresh microgames. Count check then
    reports "expected 118, got 0".
    """
    sandbox = _seed_data_dir(tmp_path)
    templates_dir = _seed_templates_dir(tmp_path)
    output = templates_dir / "branching" / "request_activity.json"

    # Generate.
    result_gen = _run_cli(
        sandbox=sandbox,
        templates_dir=templates_dir,
        args=["--output", str(output)],
    )
    assert result_gen.returncode == 0, (
        f"generate returncode={result_gen.returncode}; stderr={result_gen.stderr!r}"
    )

    # Corrupt one meet_element_* sibling by stripping its required ``steps``
    # list. This survives the regenerate (which only touches microgame
    # entries) and triggers the loader's jsonschema whole-file drop.
    payload = json.loads(output.read_text(encoding="utf-8"))
    meet_entries = [
        t
        for t in payload["templates"]
        if isinstance(t, dict) and str(t.get("id", "")).startswith("meet_element_")
    ]
    assert meet_entries, "fixture must seed at least one meet_element_* sibling to corrupt"
    target_id = meet_entries[0]["id"]
    for entry in payload["templates"]:
        if isinstance(entry, dict) and entry.get("id") == target_id:
            entry.pop("steps", None)  # required by schema — drop triggers ValidationError
            break
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Validate — must fail (regenerate pass preserves the corrupted sibling,
    # then jsonschema rejects the whole file → 0 microgames loaded → count
    # check fails with "expected 118, got 0").
    result_val = _run_cli(
        sandbox=sandbox,
        templates_dir=templates_dir,
        args=["--output", str(output), "--validate"],
    )
    assert result_val.returncode != 0, (
        f"--validate must reject corrupted corpus; "
        f"stdout={result_val.stdout!r}; stderr={result_val.stderr!r}"
    )
    combined = (result_val.stdout + result_val.stderr).lower()
    assert "expected 118" in combined or "got 0" in combined or "validate" in combined, (
        f"--validate error should surface the count mismatch; got: {combined!r}"
    )
