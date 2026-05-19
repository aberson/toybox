"""Phase N Step N1.5 — deterministic distractor-corpus generator (TDD).

TDD coverage for ``scripts/generate_distractor_corpus.py``. The generator
reads the 118-element corpus at ``data/elements/elements.json`` and emits
two artifacts:

1. ``data/elements/distractors.json`` — list of 118 entries shaped
   ``{element_id, fact_a_true, fact_b_false}``, sorted by
   ``atomic_number`` ascending.
2. Appends 118 rows to ``data/elements/_distractors_credits.md`` (one per
   entry), all tagged ``source: llm`` (the N1 skim-review window flips
   accepted rows to ``operator``).

Hard contract:

* Determinism — same input → byte-identical outputs across runs.
* ``--validate`` subcommand re-loads the written files via the N1-prep
  loader (``toybox.activities.distractor_corpus.load_distractors``) with
  ``TOYBOX_ALLOW_LLM_DISTRACTORS=1`` and asserts the 118/118/all-llm
  invariants. Exit 0 on success, non-zero on failure.

The script is loaded via ``importlib.util.spec_from_file_location`` to
match the convention in :mod:`tests.unit.scripts.test_f5_compute_manifest`
since ``scripts/`` is not a Python package.

Style mirrors :mod:`tests.unit.test_distractor_corpus`: ``tmp_path`` +
``monkeypatch.setenv("TOYBOX_DATA_DIR", ...)`` so the generator writes
into a sandboxed elements dir, not the shipped one.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from toybox.activities.distractor_corpus import (
    DistractorCorpusError,
    clear_distractor_cache,
    load_distractors,
)
from toybox.activities.element_corpus import clear_element_cache

# ---------------------------------------------------------------------
# Module loading (scripts/ is not a Python package)
# ---------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "generate_distractor_corpus.py"
_REAL_ELEMENTS_JSON = _REPO_ROOT / "data" / "elements" / "elements.json"


def _load_generator_module() -> types.ModuleType:
    """Load scripts/generate_distractor_corpus.py via importlib.

    Mirrors :func:`tests.unit.scripts.test_f5_compute_manifest._load_module`.
    """
    spec = importlib.util.spec_from_file_location("_generate_distractor_corpus", _SCRIPT_PATH)
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
    sys.modules.pop("_generate_distractor_corpus", None)


# ---------------------------------------------------------------------
# Sandbox fixture
# ---------------------------------------------------------------------

_CREDITS_HEADER = (
    "# Distractor corpus — per-entry source attribution\n"
    "\n"
    "Format: one row per element_id with `source` in {`operator`, `llm`}\n"
    "and a `reasoning` column explaining the choice. Loader rejects\n"
    "`source: llm` rows unless `TOYBOX_ALLOW_LLM_DISTRACTORS=1` is set.\n"
    "\n"
    "| element_id | source | reasoning |\n"
    "|---|---|---|\n"
)


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    """Both distractor + element caches must be path-keyed-clean per test."""
    clear_distractor_cache()
    clear_element_cache()
    yield
    clear_distractor_cache()
    clear_element_cache()


def _seed_sandbox(tmp_path: Path) -> Path:
    """Lay down an empty-scaffold ``elements/`` dir under ``tmp_path``.

    Copies the shipped ``elements.json`` (the corpus the generator reads)
    + a blank ``distractors.json`` + the header-only credits file. Returns
    the ``tmp_path`` so callers can ``monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))``.
    """
    elements_dir = tmp_path / "elements"
    elements_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REAL_ELEMENTS_JSON, elements_dir / "elements.json")
    (elements_dir / "distractors.json").write_text("[]\n", encoding="utf-8")
    (elements_dir / "_distractors_credits.md").write_text(_CREDITS_HEADER, encoding="utf-8")
    return tmp_path


def _run_generator(generator_module: types.ModuleType, sandbox: Path) -> None:
    """Invoke the generator's write entry point against the sandbox.

    The exact callable name is the developer's choice; this helper tries
    a few canonical candidates so the tests don't pin a specific public
    name. Calling convention: zero-arg run that reads/writes via
    ``TOYBOX_DATA_DIR`` (which the test sets to ``sandbox``).
    """
    for candidate in ("generate", "run", "write_corpus", "write_distractors"):
        fn = getattr(generator_module, candidate, None)
        if callable(fn):
            fn()
            return
    # Fall back to ``main([])`` with no args — should default to "write".
    if hasattr(generator_module, "main"):
        rc = generator_module.main([])
        assert rc in (None, 0), f"main([]) returned {rc!r}"
        return
    raise AssertionError(
        "generator module exposes no write entry point "
        "(expected one of: generate/run/write_corpus/write_distractors/main)"
    )


def _read_outputs(sandbox: Path) -> tuple[bytes, bytes]:
    """Return (distractors.json bytes, _distractors_credits.md bytes)."""
    j = (sandbox / "elements" / "distractors.json").read_bytes()
    m = (sandbox / "elements" / "_distractors_credits.md").read_bytes()
    return j, m


def _load_corpus_dict() -> dict[str, dict[str, Any]]:
    """Return the shipped elements indexed by element_id for fact-source checks."""
    data = json.loads(_REAL_ELEMENTS_JSON.read_text(encoding="utf-8"))
    return {e["id"]: e for e in data}


def _content_words(text: str) -> set[str]:
    """Tokenize to lowercase content words >=4 chars (filters stop-words by length)."""
    return {
        "".join(c for c in tok.lower() if c.isalnum())
        for tok in text.split()
        if len("".join(c for c in tok.lower() if c.isalnum())) >= 4
    }


# ---------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------


def test_running_generator_twice_produces_byte_identical_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Two separate sandboxes → byte-identical distractors.json."""
    sandbox_a = _seed_sandbox(tmp_path / "a")
    sandbox_b = _seed_sandbox(tmp_path / "b")

    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox_a))
    _run_generator(generator_module, sandbox_a)
    json_a, _ = _read_outputs(sandbox_a)

    clear_distractor_cache()
    clear_element_cache()
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox_b))
    _run_generator(generator_module, sandbox_b)
    json_b, _ = _read_outputs(sandbox_b)

    assert json_a == json_b, "distractors.json must be byte-identical across runs"


def test_running_generator_twice_produces_byte_identical_credits_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Two separate sandboxes → byte-identical _distractors_credits.md."""
    sandbox_a = _seed_sandbox(tmp_path / "a")
    sandbox_b = _seed_sandbox(tmp_path / "b")

    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox_a))
    _run_generator(generator_module, sandbox_a)
    _, md_a = _read_outputs(sandbox_a)

    clear_distractor_cache()
    clear_element_cache()
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox_b))
    _run_generator(generator_module, sandbox_b)
    _, md_b = _read_outputs(sandbox_b)

    assert md_a == md_b, "_distractors_credits.md must be byte-identical across runs"


# ---------------------------------------------------------------------
# Output shape — distractors.json
# ---------------------------------------------------------------------


def test_distractors_json_has_118_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """One distractor entry per element (corpus has 118)."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    payload = json.loads((sandbox / "elements" / "distractors.json").read_text("utf-8"))
    assert isinstance(payload, list)
    assert len(payload) == 118, f"expected 118 entries, got {len(payload)}"


def test_distractors_json_entries_sorted_by_atomic_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Entries are ordered by ascending atomic_number (deterministic diff-friendly)."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    payload = json.loads((sandbox / "elements" / "distractors.json").read_text("utf-8"))
    corpus = _load_corpus_dict()
    atomic_numbers = [corpus[entry["element_id"]]["atomic_number"] for entry in payload]
    assert atomic_numbers == sorted(atomic_numbers), (
        f"entries must be sorted by atomic_number; got order={atomic_numbers}"
    )


def test_each_distractor_entry_has_required_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Every entry has exactly element_id, fact_a_true, fact_b_false (loader contract)."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    payload = json.loads((sandbox / "elements" / "distractors.json").read_text("utf-8"))
    required = {"element_id", "fact_a_true", "fact_b_false"}
    for i, entry in enumerate(payload):
        assert isinstance(entry, dict), f"entry {i} is not an object: {entry!r}"
        missing = required - set(entry)
        assert not missing, f"entry {i} ({entry.get('element_id')!r}) missing keys: {missing}"


def test_each_distractor_entry_element_id_matches_a_real_element(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Every element_id in the output is a real id from elements.json."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    payload = json.loads((sandbox / "elements" / "distractors.json").read_text("utf-8"))
    real_ids = set(_load_corpus_dict().keys())
    output_ids = [entry["element_id"] for entry in payload]
    # Every output id is real.
    bogus = [eid for eid in output_ids if eid not in real_ids]
    assert not bogus, f"unknown element_ids in output: {bogus}"
    # All 118 real ids covered (no element skipped).
    assert set(output_ids) == real_ids, (
        f"missing element_ids: {real_ids - set(output_ids)}; extra: {set(output_ids) - real_ids}"
    )


def test_fact_a_true_and_fact_b_false_differ_per_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """For every entry, the two facts are not equal."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    payload = json.loads((sandbox / "elements" / "distractors.json").read_text("utf-8"))
    same = [
        entry["element_id"]
        for entry in payload
        if entry["fact_a_true"].strip() == entry["fact_b_false"].strip()
    ]
    assert not same, f"entries with identical fact_a_true/fact_b_false: {same}"


def test_fact_a_and_fact_b_are_non_empty_strings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Both fact fields are non-empty strings (matches Distractor min_length=1)."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    payload = json.loads((sandbox / "elements" / "distractors.json").read_text("utf-8"))
    for entry in payload:
        a = entry["fact_a_true"]
        b = entry["fact_b_false"]
        assert isinstance(a, str) and a.strip(), (
            f"entry {entry['element_id']!r}: fact_a_true must be a non-empty string, got {a!r}"
        )
        assert isinstance(b, str) and b.strip(), (
            f"entry {entry['element_id']!r}: fact_b_false must be a non-empty string, got {b!r}"
        )


# ---------------------------------------------------------------------
# Output shape — _distractors_credits.md
# ---------------------------------------------------------------------


def _parse_md_rows(md_text: str) -> list[tuple[str, str, str]]:
    """Pull `| eid | source | reasoning |` rows out of the markdown.

    Skips fenced-block content + the header/divider rows (matches the
    loader's _parse_credits behavior).
    """
    rows: list[tuple[str, str, str]] = []
    in_fence = False
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells:
            continue
        if cells[0].lower() == "element_id":
            continue
        if all(c and set(c) <= set("-:") for c in cells):
            continue
        if len(cells) == 3:
            rows.append((cells[0], cells[1], cells[2]))
    return rows


def test_credits_md_has_header_plus_118_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Exactly 118 data rows appended (no pre-existing example rows leak in)."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    md_text = (sandbox / "elements" / "_distractors_credits.md").read_text("utf-8")
    rows = _parse_md_rows(md_text)
    assert len(rows) == 118, f"expected 118 data rows in credits md, got {len(rows)}"


def test_every_credits_row_has_source_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """N1.5 generates LLM-sourced rows; operator-skim-review in N1 flips them."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    md_text = (sandbox / "elements" / "_distractors_credits.md").read_text("utf-8")
    rows = _parse_md_rows(md_text)
    non_llm = [(eid, src) for (eid, src, _) in rows if src != "llm"]
    assert not non_llm, f"non-llm source values in N1.5 output: {non_llm}"


def test_every_credits_row_has_reasoning_in_expected_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Reasoning column starts with `fact_b_false strategy:` (a/b/c naming the source)."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    md_text = (sandbox / "elements" / "_distractors_credits.md").read_text("utf-8")
    rows = _parse_md_rows(md_text)
    bad = [
        (eid, reasoning)
        for (eid, _, reasoning) in rows
        if not reasoning.startswith("fact_b_false strategy:")
    ]
    assert not bad, (
        f"reasoning column must start with 'fact_b_false strategy:'; offenders: {bad[:5]}"
    )


def test_credits_md_element_ids_match_distractors_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Every distractors entry has a credits row and vice versa (1:1 alignment)."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    payload = json.loads((sandbox / "elements" / "distractors.json").read_text("utf-8"))
    md_text = (sandbox / "elements" / "_distractors_credits.md").read_text("utf-8")
    json_ids = {entry["element_id"] for entry in payload}
    md_ids = {eid for (eid, _, _) in _parse_md_rows(md_text)}
    assert json_ids == md_ids, (
        f"distractors.json vs credits.md element_id sets differ; "
        f"only-in-json={json_ids - md_ids}, only-in-md={md_ids - json_ids}"
    )


# ---------------------------------------------------------------------
# Snapshot pinning — 5 known elements (loose: fact_a derived from corpus)
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "element_id",
    ["h-1", "he-2", "o-8", "fe-26", "au-79"],
)
def test_known_element_fact_a_paraphrases_fun_fact_or_seed_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generator_module: types.ModuleType,
    element_id: str,
) -> None:
    """For each pinned element, fact_a_true shares >=1 content word (>=4 chars)
    with either the corpus ``fun_fact`` or the first ``story_seed_hooks`` entry.

    Loose check by design — don't pin to specific bytes; pin to "fact_a
    is plausibly derived from the corpus". A generator that emitted
    canned filler (e.g. "this element is interesting") would fail this.
    """
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    payload = json.loads((sandbox / "elements" / "distractors.json").read_text("utf-8"))
    by_id = {e["element_id"]: e for e in payload}
    assert element_id in by_id, f"missing entry for {element_id}"
    entry = by_id[element_id]
    fact_a = entry["fact_a_true"]

    corpus = _load_corpus_dict()[element_id]
    name = corpus["name"]
    fun_fact = corpus.get("fun_fact") or ""
    seed_hook = ""
    hooks = corpus.get("story_seed_hooks") or []
    if hooks:
        seed_hook = hooks[0].replace("{name}", name)

    a_words = _content_words(fact_a)
    fun_words = _content_words(fun_fact) - _content_words(name)
    hook_words = _content_words(seed_hook) - _content_words(name)

    overlap_fun = a_words & fun_words
    overlap_hook = a_words & hook_words
    assert overlap_fun or overlap_hook, (
        f"{element_id} fact_a_true={fact_a!r} does not share any content word "
        f"with fun_fact={fun_fact!r} or first seed_hook={seed_hook!r}"
    )


# ---------------------------------------------------------------------
# Loader round-trip — N1-prep loader against real generated bytes
# ---------------------------------------------------------------------


def test_loader_rejects_real_distractors_without_env_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """118 source:llm rows must be rejected when TOYBOX_ALLOW_LLM_DISTRACTORS is unset."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    clear_distractor_cache()
    clear_element_cache()
    monkeypatch.delenv("TOYBOX_ALLOW_LLM_DISTRACTORS", raising=False)
    with pytest.raises(DistractorCorpusError, match=r"(?i)llm|TOYBOX_ALLOW_LLM_DISTRACTORS"):
        load_distractors()


def test_loader_accepts_real_distractors_with_env_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """With the env flag set, the loader returns all 118 entries cleanly."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    clear_distractor_cache()
    clear_element_cache()
    monkeypatch.setenv("TOYBOX_ALLOW_LLM_DISTRACTORS", "1")
    loaded = load_distractors()
    assert len(loaded) == 118


# ---------------------------------------------------------------------
# --validate CLI
# ---------------------------------------------------------------------


def _run_cli(
    sandbox: Path, args: list[str], *, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the generator script as a subprocess with TOYBOX_DATA_DIR set to sandbox."""
    import os

    env = {**dict(os.environ), "TOYBOX_DATA_DIR": str(sandbox)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_validate_cli_succeeds_on_freshly_generated_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """After in-process generation, ``--validate`` subprocess exits 0 with the canonical summary."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    result = _run_cli(sandbox, ["--validate"])
    assert result.returncode == 0, (
        f"--validate exit={result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    # Canonical phrasing per the issue body: "118 entries, 118 credits rows, all source: llm".
    # Be lenient on exact format but require the three load-bearing numbers/tokens.
    assert "118 entries" in combined
    assert "118 credits" in combined
    assert "llm" in combined or "ok" in combined


def test_validate_cli_fails_on_corrupted_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """After generation, dropping one entry from distractors.json → --validate exits non-zero."""
    sandbox = _seed_sandbox(tmp_path)
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(sandbox))
    _run_generator(generator_module, sandbox)

    # Mutate distractors.json: drop the last entry.
    dpath = sandbox / "elements" / "distractors.json"
    payload = json.loads(dpath.read_text("utf-8"))
    assert len(payload) == 118
    dropped_id = payload[-1]["element_id"]
    dpath.write_text(json.dumps(payload[:-1]), encoding="utf-8")

    result = _run_cli(sandbox, ["--validate"])
    assert result.returncode != 0, (
        f"--validate should fail on 117-entry corpus; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    # Error message should name the count mismatch OR the orphaned credits row.
    assert (
        "117" in combined
        or "118" in combined
        or "mismatch" in combined
        or "credits" in combined
        or dropped_id.lower() in combined
    ), f"error message should surface the mismatch; got: {combined!r}"
