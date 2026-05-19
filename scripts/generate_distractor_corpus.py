"""Phase N Step N1.5 — deterministic distractor-corpus generator.

Reads ``data/elements/elements.json`` and writes:

1. ``data/elements/distractors.json`` — 118 entries shaped
   ``{element_id, fact_a_true, fact_b_false}``, sorted by
   ``atomic_number`` ascending.
2. Appends 118 rows to ``data/elements/_distractors_credits.md`` after
   the existing table header. Every row's ``source`` column is ``llm``;
   every row's ``reasoning`` column begins with the literal prefix
   ``fact_b_false strategy: <a|b|c>, derived from <source>``.

Generation is fully deterministic — same input corpus produces
byte-identical outputs across runs. No randomness, no clock-based
values, no external calls.

``fact_a_true`` strategy
------------------------

For every element, ``fact_a_true`` is the element's own ``fun_fact``
copied verbatim from ``elements.json``. The fun_fact is corpus-vetted
truth, already kid-appropriate, and guarantees content-word overlap
with the source field (the N1.5 snapshot tests require this).

``fact_b_false`` strategy
-------------------------

Strategy (a) "rotate-and-attribute": pick a partner element via
``(atomic_number * 37) % 118`` (37 is coprime to 118 → bijection, so
every target gets a stable distinct partner) and re-attribute the
partner's ``fun_fact`` to the target by replacing the partner's name
(case-insensitive) with the target's name. The replacement is
case-insensitive on the first character so phrasing like "Helium..."
stays grammatically intact when retargeted to "Gold".

When the partner happens to be the target itself, or when the
replacement produces text identical to ``fact_a_true``, the rotation
advances by one (``+= 1`` mod 118) until a distinct partner produces a
distinct text. This is rare (rotation step 37 is coprime to 118) but
the fallback keeps the generator deterministic AND keeps
``fact_a_true != fact_b_false`` invariant.

Output format
-------------

``distractors.json`` is written with ``indent=2``, ``ensure_ascii=False``,
sort-keys per entry preserved (insertion order: element_id, fact_a_true,
fact_b_false), and a single trailing newline. Mirrors the convention in
``scripts/generate_meet_element_templates.py``.

Credits rows are appended to the existing header file as a single
contiguous block, one per element in ``atomic_number`` order. Each row
is ``| <element_id> | llm | fact_b_false strategy: a, derived from
<partner_id> fun_fact |``. The append is idempotent — every run rebuilds
the rows-after-header section from scratch, preserving any non-table
trailing content (e.g. the ``## File history`` section in the shipped
header).

CLI
---

::

    uv run python scripts/generate_distractor_corpus.py            # write
    uv run python scripts/generate_distractor_corpus.py --validate # validate

``--validate`` re-loads the written corpus via
``toybox.activities.distractor_corpus.validate_corpus`` with
``TOYBOX_ALLOW_LLM_DISTRACTORS=1`` and prints
``<N> entries, <N> credits rows, all source: llm (OK)`` on success,
exit 0. On mismatch (count != 118, missing element_id, etc.) prints a
``FAIL: ...`` line to stderr and exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Final

# NOTE on import typing: ``mypy src scripts`` (the project gate) sees the
# toybox ``py.typed`` marker via the ``src`` arg, so plain imports work.
# Per-file mypy invocations against this script alone do NOT — sibling
# generators (e.g. scripts/generate_meet_element_templates.py) carry a
# local ``# type: ignore[import-untyped]`` for that case and accept the
# resulting "unused-ignore" noise under the project gate as a baseline.
# We omit those comments here so the project gate stays clean for this
# new file; if a future per-file invocation needs them, restore the
# ignore-comment pattern.
from toybox.activities.distractor_corpus import (
    DistractorCorpusError,
    clear_distractor_cache,
    validate_corpus,
)
from toybox.activities.element_corpus import (
    Element,
    clear_element_cache,
    load_elements,
)

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

_DATA_ROOT_ENV: Final[str] = "TOYBOX_DATA_DIR"
_DEFAULT_DATA_ROOT: Final[Path] = Path("data")
_ELEMENTS_SUBDIR: Final[str] = "elements"
_DISTRACTORS_FILENAME: Final[str] = "distractors.json"
_CREDITS_FILENAME: Final[str] = "_distractors_credits.md"

_LLM_OPT_IN_ENV: Final[str] = "TOYBOX_ALLOW_LLM_DISTRACTORS"
_LLM_OPT_IN_VALUE: Final[str] = "1"

# Rotation step for strategy (a). 37 is coprime to 118 (118 = 2 * 59;
# gcd(37, 118) = 1) so multiplication mod 118 is a bijection — every
# target is paired with a stable distinct partner.
_ROTATION_STEP: Final[int] = 37
_CORPUS_SIZE: Final[int] = 118

# Credits-table marker — every line at or below this header is rebuilt
# on each run; anything above is preserved verbatim.
_DATA_DIVIDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\s*\|\s*-+.*\|\s*$")


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------


def _data_root() -> Path:
    raw = os.environ.get(_DATA_ROOT_ENV)
    return Path(raw) if raw else _DEFAULT_DATA_ROOT


def _distractors_path() -> Path:
    return _data_root() / _ELEMENTS_SUBDIR / _DISTRACTORS_FILENAME


def _credits_path() -> Path:
    return _data_root() / _ELEMENTS_SUBDIR / _CREDITS_FILENAME


# ---------------------------------------------------------------------
# Fact generation
# ---------------------------------------------------------------------


def _retarget(text: str, *, old_name: str, new_name: str) -> str:
    """Replace ``old_name`` with ``new_name`` in ``text``, case-insensitively.

    Used to re-attribute a partner element's ``fun_fact`` to the target
    element under strategy (a). We do a literal-substring replace (not a
    word-boundary regex) so phrasings like "Hydrogen's" survive into
    "Gold's" with the apostrophe-s intact. ``re.sub`` with ``re.IGNORECASE``
    is the simplest case-insensitive form available in stdlib.
    """
    pattern = re.compile(re.escape(old_name), re.IGNORECASE)
    return pattern.sub(new_name, text)


def _pick_partner_index(target_index: int) -> int:
    """Return the rotation partner's 0-indexed position for strategy (a)."""
    return (target_index * _ROTATION_STEP) % _CORPUS_SIZE


def _build_fact_b_false(
    target: Element, *, target_index: int, sorted_elements: list[Element]
) -> tuple[str, Element]:
    """Render strategy (a) ``fact_b_false`` + return the partner element.

    Walks rotation candidates until we find one whose retargeted
    ``fun_fact`` differs from the target's ``fun_fact``. The first
    candidate is `target_index * _ROTATION_STEP % 118`; subsequent
    candidates step by ``+1`` mod 118 if needed. Because the corpus has
    118 distinct fun_facts the loop terminates inside the first few
    iterations.
    """
    fact_a = target.fun_fact
    candidate_index = _pick_partner_index(target_index)
    # `set` of indices we've tried so the worst-case loop can't infinite.
    tried: set[int] = set()
    while candidate_index in tried or candidate_index == target_index:
        candidate_index = (candidate_index + 1) % _CORPUS_SIZE
    while True:
        tried.add(candidate_index)
        partner = sorted_elements[candidate_index]
        if partner.id == target.id:
            # Self-rotation — advance.
            candidate_index = (candidate_index + 1) % _CORPUS_SIZE
            continue
        retargeted = _retarget(partner.fun_fact, old_name=partner.name, new_name=target.name)
        if retargeted.strip() and retargeted.strip() != fact_a.strip():
            return retargeted, partner
        # Identical or empty after retarget — try next.
        candidate_index = (candidate_index + 1) % _CORPUS_SIZE
        if candidate_index in tried:
            # Pathological corpus — fall back to a constant. Should never
            # happen with 118 distinct fun_facts, but the explicit fallback
            # keeps the generator deterministic.
            return f"{target.name} is actually a gas that escapes into space.", partner


# ---------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------


def _build_entries(
    elements: list[Element],
) -> tuple[list[dict[str, str]], list[tuple[str, str, str]]]:
    """Return (json entries, credits rows) sorted by ``atomic_number``.

    Each credits row is ``(element_id, source, reasoning)``.
    """
    sorted_elements = sorted(elements, key=lambda e: e.atomic_number)
    json_entries: list[dict[str, str]] = []
    credits_rows: list[tuple[str, str, str]] = []
    for index, element in enumerate(sorted_elements):
        fact_a = element.fun_fact
        fact_b, partner = _build_fact_b_false(
            element, target_index=index, sorted_elements=sorted_elements
        )
        json_entries.append(
            {
                "element_id": element.id,
                "fact_a_true": fact_a,
                "fact_b_false": fact_b,
            }
        )
        reasoning = f"fact_b_false strategy: a, derived from {partner.id} fun_fact"
        credits_rows.append((element.id, "llm", reasoning))
    return json_entries, credits_rows


# ---------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------


def _write_json(path: Path, entries: list[dict[str, str]]) -> None:
    """Write ``distractors.json`` with the project's canonical formatting.

    ``indent=2`` + ``ensure_ascii=False`` + single trailing newline
    matches ``scripts/generate_meet_element_templates.py``.
    """
    text = json.dumps(entries, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def _rebuild_credits_text(existing_text: str, credits_rows: list[tuple[str, str, str]]) -> str:
    """Append (or rebuild) credits rows after the real table divider.

    Walks the file tracking fenced-code-block state (mirroring the
    loader's ``_parse_credits``) and locates the FIRST table-divider line
    (``|---|---|---|``) that is OUTSIDE any fenced block. Everything up
    to and including that divider is preserved verbatim. Subsequent
    out-of-fence data rows are dropped (so re-runs don't duplicate) and
    the freshly generated rows are inserted in their place. Any
    non-table trailing content (e.g. the ``## File history`` section in
    the shipped scaffold) is preserved verbatim AFTER the data rows.

    Fence-awareness matters because the shipped ``_distractors_credits.md``
    embeds a fenced markdown example table whose divider line MUST NOT
    be treated as the real anchor — doing so would inject all 118 rows
    inside the fence, where the loader's fence-skipping parser then
    ignores them and the corpus appears empty.

    Idempotent: running twice produces byte-identical output.
    """
    lines = existing_text.splitlines(keepends=False)

    # Pass 1: find the first divider line outside a fenced block.
    divider_idx: int | None = None
    in_fence_scan = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence_scan = not in_fence_scan
            continue
        if in_fence_scan:
            continue
        if _DATA_DIVIDER_PATTERN.match(line):
            divider_idx = idx
            break

    if divider_idx is None:
        # No real table — append a fresh one at the end. Robustness path
        # for an edited file; the shipped scaffold always carries the
        # header so we don't hit this in production.
        header = [
            "",
            "| element_id | source | reasoning |",
            "|---|---|---|",
        ]
        lines.extend(header)
        divider_idx = len(lines) - 1

    head = lines[: divider_idx + 1]
    tail = lines[divider_idx + 1 :]

    # Pass 2: strip pre-existing data rows from the tail, but ONLY
    # outside fenced blocks. Fenced-content example rows are preserved
    # verbatim — they're documentation, not data the loader sees.
    pruned_tail: list[str] = []
    in_fence_tail = False
    for line in tail:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence_tail = not in_fence_tail
            pruned_tail.append(line)
            continue
        if in_fence_tail:
            pruned_tail.append(line)
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells and all(c and set(c) <= set("-:") for c in cells):
                continue  # stray divider — drop, canonical one stays in head
            if cells and cells[0].lower() == "element_id":
                continue  # stray header row — drop
            continue  # data row — drop, will be replaced below
        pruned_tail.append(line)

    new_rows = [f"| {eid} | {src} | {reasoning} |" for (eid, src, reasoning) in credits_rows]

    out_lines = [*head, *new_rows, *pruned_tail]
    return "\n".join(out_lines) + "\n"


def _write_credits(path: Path, credits_rows: list[tuple[str, str, str]]) -> None:
    """Rewrite ``_distractors_credits.md`` with the 118 fresh rows."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    new_text = _rebuild_credits_text(existing, credits_rows)
    path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------


def generate() -> None:
    """Generate ``distractors.json`` + append 118 rows to the credits md.

    Reads paths via ``TOYBOX_DATA_DIR`` (test-overridable). Clears the
    element + distractor caches before running so a same-process call
    after a ``monkeypatch.setenv`` picks up the new path.
    """
    clear_element_cache()
    clear_distractor_cache()
    elements = list(load_elements())
    entries, credits_rows = _build_entries(elements)
    _write_json(_distractors_path(), entries)
    _write_credits(_credits_path(), credits_rows)


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _run_validate() -> int:
    """Validate the on-disk corpus.

    Re-loads via the N1-prep loader (with ``TOYBOX_ALLOW_LLM_DISTRACTORS=1``
    forced ON so source:llm rows are accepted) and asserts:

    * 118 distractor entries
    * 118 credits rows
    * All credits rows have source==llm
    * All element_ids match a real corpus element (loader enforces)

    Returns 0 on success, 1 on failure.
    """
    # Force LLM opt-in inside this process so the loader will accept
    # the source:llm rows we just wrote.
    os.environ[_LLM_OPT_IN_ENV] = _LLM_OPT_IN_VALUE
    clear_distractor_cache()
    clear_element_cache()
    try:
        summary = validate_corpus()
    except DistractorCorpusError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if summary.entry_count != _CORPUS_SIZE:
        print(
            f"FAIL: expected {_CORPUS_SIZE} entries, got {summary.entry_count}",
            file=sys.stderr,
        )
        return 1
    if summary.credits_count != _CORPUS_SIZE:
        print(
            f"FAIL: expected {_CORPUS_SIZE} credits rows, got {summary.credits_count}",
            file=sys.stderr,
        )
        return 1
    print(
        f"{summary.entry_count} entries, {summary.credits_count} credits rows, all source: llm (OK)"
    )
    return 0


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_distractor_corpus",
        description=(
            "Deterministically generate data/elements/distractors.json and "
            "append 118 rows to data/elements/_distractors_credits.md from "
            "the 118-element corpus (Phase N Step N1.5)."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After (or instead of) writing, validate the on-disk corpus via "
            "the N1-prep loader. Exit 0 on success, non-zero on mismatch."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.validate:
        return _run_validate()
    generate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
