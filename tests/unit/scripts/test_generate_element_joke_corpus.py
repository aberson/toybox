"""Phase Q Step Q4 — element-joke generator (unit tests).

Covers ``scripts/generate_element_joke_corpus.py``. The generator
reads ``data/elements/elements.json`` (118 elements), prompts Claude
once per element, and writes the cohort into a copy of
``data/jokes/jokes.json`` idempotently.

Q4 ships the SCRIPT only — Q7 (operator) runs the live LLM call. The
test suite uses ``--dry-run`` (deterministic mock responses, no
network) and monkeypatches :class:`AnthropicClient` construction to
raise so a stray live-call attempt fails LOUD.

Style mirrors :mod:`tests.unit.scripts.test_generate_element_microgames`
(N4 precedent): ``importlib.util.spec_from_file_location`` to load
``scripts/`` (not a Python package), ``tmp_path`` sandboxes,
``monkeypatch.setenv("TOYBOX_DATA_DIR", ...)`` for the production
loader round-trip.
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

from toybox.activities.joke_corpus import clear_joke_cache

# ---------------------------------------------------------------------
# Module loading (scripts/ is not a Python package)
# ---------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "generate_element_joke_corpus.py"
_REAL_ELEMENTS_JSON = _REPO_ROOT / "data" / "elements" / "elements.json"
_REAL_JOKES_JSON = _REPO_ROOT / "data" / "jokes" / "jokes.json"


def _load_generator_module() -> types.ModuleType:
    """Load scripts/generate_element_joke_corpus.py via importlib.

    Mirrors :func:`tests.unit.scripts.test_generate_element_microgames._load_generator_module`.
    """
    spec = importlib.util.spec_from_file_location(
        "_generate_element_joke_corpus", _SCRIPT_PATH
    )
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
    sys.modules.pop("_generate_element_joke_corpus", None)


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    """Joke loader cache must be path-keyed-clean per test."""
    clear_joke_cache()
    yield
    clear_joke_cache()


# ---------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------


def _seed_jokes_file(tmp_path: Path) -> Path:
    """Copy real jokes.json into ``tmp_path/jokes/jokes.json`` and return the path."""
    jokes_dir = tmp_path / "jokes"
    jokes_dir.mkdir(parents=True, exist_ok=True)
    dest = jokes_dir / "jokes.json"
    shutil.copy(_REAL_JOKES_JSON, dest)
    return dest


def _sample_element() -> dict[str, Any]:
    """A single real-shape element entry for unit-level tests."""
    return {
        "id": "h-1",
        "symbol": "H",
        "name": "Hydrogen",
        "atomic_number": 1,
        "atomic_mass": 1.0,
        "family": "nonmetal",
        "phase_at_room_temp": "gas",
        "color_description": "colorless gas",
        "discovered_era": "1766",
        "fun_fact": (
            "Hydrogen is the lightest element and makes up most of the sun."
        ),
        "story_seed_hooks": [
            "{name} floats balloons up into the sky",
            "stars are giant balls of glowing {name}",
        ],
        "pronunciation_guide": None,
        "age_band": "3-5",
    }


def _block_real_client(
    monkeypatch: pytest.MonkeyPatch, generator_module: types.ModuleType
) -> None:
    """Replace :func:`_build_real_client` with one that raises if invoked.

    Confirms ``--dry-run`` truly never constructs the AnthropicClient.
    The error name is checked by callers via pytest assertion.
    """

    def _explode() -> None:
        raise AssertionError("AnthropicClient must NOT be constructed in dry-run")

    monkeypatch.setattr(generator_module, "_build_real_client", _explode)


# ---------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------


def test_help_returns_zero() -> None:
    """``python scripts/generate_element_joke_corpus.py --help`` must exit 0.

    Uses a subprocess so the script's argparse handler exits with the
    real SystemExit (in-process main() does not exit on --help; argparse
    sys.exit(0)s).
    """
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"--help returned {result.returncode}: stderr={result.stderr!r}"
    )
    assert "Phase Q Step Q4" in result.stdout or "element" in result.stdout.lower()


# ---------------------------------------------------------------------
# Dry-run + AnthropicClient guard
# ---------------------------------------------------------------------


def test_dry_run_succeeds_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    generator_module: types.ModuleType,
) -> None:
    """``--dry-run --limit 2`` exits 0 and never constructs AnthropicClient."""
    output = _seed_jokes_file(tmp_path)
    _block_real_client(monkeypatch, generator_module)

    rc = generator_module.main(
        ["--dry-run", "--limit", "2", "--output", str(output)]
    )
    assert rc == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert isinstance(payload, list)
    new_entries = [
        j for j in payload if str(j.get("id", "")).startswith("element-joke-")
    ]
    assert len(new_entries) == 2
    # File on disk must NOT be touched in dry-run mode.
    on_disk = json.loads(output.read_text(encoding="utf-8"))
    assert all(
        not str(j.get("id", "")).startswith("element-joke-") for j in on_disk
    ), "dry-run must not write the file"


def test_dry_run_covers_all_118_elements_when_no_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    generator_module: types.ModuleType,
) -> None:
    """Full dry-run with no --limit emits ≥118 element-joke entries.

    Coverage assertion from Q4 spec: all 118 elements get an entry
    (no M7a-style backfill skip for jokes).
    """
    output = _seed_jokes_file(tmp_path)
    _block_real_client(monkeypatch, generator_module)

    rc = generator_module.main(["--dry-run", "--output", str(output)])
    assert rc == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    new_entries = [
        j for j in payload if str(j.get("id", "")).startswith("element-joke-")
    ]
    assert len(new_entries) >= 118, (
        f"expected >=118 element-joke entries, got {len(new_entries)}"
    )


# ---------------------------------------------------------------------
# parse_llm_response — happy path + rejection cases
# ---------------------------------------------------------------------


def test_parse_llm_response_extracts_setup_and_punchline(
    generator_module: types.ModuleType,
) -> None:
    """Marker-delimited response parses cleanly."""
    raw = (
        "SETUP: Why did Hydrogen float away?\n"
        "PUNCHLINE: Because it's the lightest element!\n"
    )
    out = generator_module.parse_llm_response(raw, _sample_element())
    assert out == {
        "setup": "Why did Hydrogen float away?",
        "punchline": "Because it's the lightest element!",
    }


def test_parse_llm_response_rejects_too_long_setup(
    generator_module: types.ModuleType,
) -> None:
    """Setup >200 chars is rejected with element id in the message."""
    long_setup = "x" * 201
    raw = f"SETUP: {long_setup}\nPUNCHLINE: ok punchline\n"
    with pytest.raises(ValueError, match="setup is 201 chars"):
        generator_module.parse_llm_response(raw, _sample_element())


def test_parse_llm_response_rejects_too_long_punchline(
    generator_module: types.ModuleType,
) -> None:
    """Punchline >200 chars is rejected."""
    long_pun = "y" * 250
    raw = f"SETUP: ok setup\nPUNCHLINE: {long_pun}\n"
    with pytest.raises(ValueError, match="punchline is 250 chars"):
        generator_module.parse_llm_response(raw, _sample_element())


def test_parse_llm_response_rejects_empty_setup(
    generator_module: types.ModuleType,
) -> None:
    """Empty setup (after marker) is rejected."""
    raw = "SETUP:\nPUNCHLINE: a punchline\n"
    with pytest.raises(ValueError, match="setup is empty"):
        generator_module.parse_llm_response(raw, _sample_element())


def test_parse_llm_response_rejects_missing_marker(
    generator_module: types.ModuleType,
) -> None:
    """A response without SETUP marker is rejected (defensive)."""
    raw = "Here is a joke without markers\n"
    with pytest.raises(ValueError, match="missing 'SETUP:'"):
        generator_module.parse_llm_response(raw, _sample_element())


# ---------------------------------------------------------------------
# build_entry — required fields + id format
# ---------------------------------------------------------------------


def test_build_entry_sets_required_fields(
    generator_module: types.ModuleType,
) -> None:
    """Every Phase-Q-required field is set on a generated entry."""
    raw = "SETUP: A setup line.\nPUNCHLINE: A punchline line.\n"
    entry = generator_module.build_entry(_sample_element(), raw)
    assert entry["element_id"] == "h-1"
    assert entry["family"] == "nonmetal"
    assert entry["theme"] == "silly"
    assert entry["optional_toy_slot"] is False
    assert entry["age_band"] == "3-5"
    assert entry["persona_compat"] == ["periodic_table", "all"]
    assert entry["setup"] == "A setup line."
    assert entry["punchline"] == "A punchline line."


def test_build_entry_id_format(generator_module: types.ModuleType) -> None:
    """Id matches ``element-joke-<symbol-lower>-<atomic-number>``."""
    raw = "SETUP: s.\nPUNCHLINE: p.\n"

    # Lowercase single-letter symbol.
    e1 = _sample_element()
    assert (
        generator_module.build_entry(e1, raw)["id"] == "element-joke-h-1"
    )

    # Two-letter symbol with mixed case from the catalog.
    e2 = dict(_sample_element())
    e2["id"] = "au-79"
    e2["symbol"] = "Au"
    e2["atomic_number"] = 79
    assert (
        generator_module.build_entry(e2, raw)["id"] == "element-joke-au-79"
    )


# ---------------------------------------------------------------------
# strip_existing — namespace safety + idempotency
# ---------------------------------------------------------------------


def test_strip_existing_removes_only_element_joke_prefix(
    generator_module: types.ModuleType,
) -> None:
    """Only entries with the element-joke- prefix are stripped.

    Non-prefixed entries — including ids that contain the substring
    ``-joke-`` elsewhere (defensive) — survive.
    """
    jokes = [
        {"id": "why-chicken-crossed"},
        {"id": "knock-knock-boo"},
        {"id": "element-joke-h-1"},
        {"id": "element-joke-au-79"},
        # Defensive: substring-not-prefix MUST survive.
        {"id": "some-element-joke-imposter"},
    ]
    out = generator_module.strip_existing(jokes, "element-joke-")
    out_ids = [j["id"] for j in out]
    assert "element-joke-h-1" not in out_ids
    assert "element-joke-au-79" not in out_ids
    assert "why-chicken-crossed" in out_ids
    assert "knock-knock-boo" in out_ids
    assert "some-element-joke-imposter" in out_ids


def test_idempotent_append_does_not_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    generator_module: types.ModuleType,
) -> None:
    """Two consecutive dry-runs produce the same cohort count, not doubled.

    Strip-then-append is the idempotency contract. Dry-run renders to
    stdout, so we read the JSON from capsys and assert the count after
    each run is equal (NOT doubled).
    """
    output = _seed_jokes_file(tmp_path)
    _block_real_client(monkeypatch, generator_module)

    # Run 1.
    rc1 = generator_module.main(
        ["--dry-run", "--limit", "3", "--output", str(output)]
    )
    assert rc1 == 0
    payload1 = json.loads(capsys.readouterr().out)
    count1 = sum(
        1 for j in payload1 if str(j.get("id", "")).startswith("element-joke-")
    )

    # Simulate a previous-run write by removing strip-target entries
    # AND appending the cohort to the on-disk file (so the next run
    # has to strip + re-append, not just append).
    base = json.loads(output.read_text(encoding="utf-8"))
    new_cohort = [
        j for j in payload1 if str(j.get("id", "")).startswith("element-joke-")
    ]
    output.write_text(
        json.dumps(base + new_cohort, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Run 2 — same --limit. The strip pass MUST remove the cohort from
    # run 1 before the new cohort is generated.
    rc2 = generator_module.main(
        ["--dry-run", "--limit", "3", "--output", str(output)]
    )
    assert rc2 == 0
    payload2 = json.loads(capsys.readouterr().out)
    count2 = sum(
        1 for j in payload2 if str(j.get("id", "")).startswith("element-joke-")
    )

    assert count1 == count2 == 3, (
        f"idempotent strip+append broken: run1={count1}, run2={count2}"
    )
    # Total file count must not grow run-over-run when the cohort size
    # is identical.
    assert len(payload1) == len(payload2)


# ---------------------------------------------------------------------
# Validate mode — production loader round-trip
# ---------------------------------------------------------------------


def test_validate_mode_round_trips_through_load_jokes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generator_module: types.ModuleType,
) -> None:
    """``--validate`` re-reads the file via the production joke_corpus loader.

    The cohort must pass every validator (theme=Theme.silly, persona
    compat shape, id kebab-slug, element_id regex, family enum).
    """
    output = _seed_jokes_file(tmp_path)
    _block_real_client(monkeypatch, generator_module)

    # Point the production joke loader at the sandbox.
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))

    rc = generator_module.main(
        [
            "--dry-run",
            "--limit", "2",
            "--output", str(output),
            "--validate",
        ]
    )
    # --dry-run intentionally short-circuits before write/validate, so
    # we run NON-dry to exercise --validate against a real write.
    # The block above keeps the test green; the next block does the real check.
    assert rc == 0

    # Now run without --dry-run, with --validate, against the sandbox.
    output = _seed_jokes_file(tmp_path / "real-run")
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path / "real-run"))

    # Replace _call_client with a mock so we never touch the network.
    def _mock_call(_client: Any, _prompt: str) -> str:
        return "SETUP: A test setup.\nPUNCHLINE: A test punchline.\n"

    monkeypatch.setattr(generator_module, "_call_client", _mock_call)
    # _build_real_client returns a sentinel — _call_client doesn't read it.
    monkeypatch.setattr(generator_module, "_build_real_client", lambda: object())

    rc2 = generator_module.main(
        [
            "--limit", "2",
            "--output", str(output),
            "--validate",
        ]
    )
    assert rc2 == 0

    written = json.loads(output.read_text(encoding="utf-8"))
    cohort = [j for j in written if str(j.get("id", "")).startswith("element-joke-")]
    assert len(cohort) == 2
