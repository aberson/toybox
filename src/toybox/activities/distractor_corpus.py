"""Phase N Step N1-prep — distractor corpus loader + validator + CLI.

The distractor corpus is the data source for Phase N
``element_microgame`` Step-3 forks ("which of these is true about
{element_name}?"). Each entry holds one true fact and one
plausible-but-false fact about a specific element. The N6 microgame
template renders the two facts as fork choices to the kid; the engine
records which side they pick.

Entries live in ``data/elements/distractors.json`` and are paired
with a per-row provenance entry in ``data/elements/_distractors_credits.md``
(operator-authored). The loader cross-checks: every entry MUST have a
matching credits row; rows tagged ``source: llm`` are rejected by
default and gated behind the env flag ``TOYBOX_ALLOW_LLM_DISTRACTORS=1``
(opt-in for the N1.5 generator + N1 skim-review window).

Entry shape (``Distractor``)::

    {
        "element_id": "au-79",
        "fact_a_true": "...",
        "fact_b_false": "..."
    }

Public surface mirrors :mod:`toybox.activities.element_corpus`:

* :class:`Distractor` — frozen Pydantic model.
* :class:`DistractorCorpusError` — typed exception that names the
  failing ``element_id`` (or row) for operator-facing error messages.
* :func:`load_distractors` — returns the validated tuple; cached.
* :func:`validate_corpus` — runs the full load + summary; returns a
  :class:`ValidationSummary` for the CLI.
* :func:`clear_distractor_cache` — test hook.

CLI::

    uv run python -m toybox.activities.distractor_corpus --validate

Prints ``N entries, N credits rows, OK`` on success and exits 0. On
failure, prints ``N entries, N credits rows, FAIL: <reason>`` to stderr
and exits 1.

Security defense-in-depth per ``security.md``: entries containing
``<system-reminder>`` or ``ignore prior instructions`` (case-insensitive)
in ``fact_a_true`` / ``fact_b_false`` are rejected at load time.

Single source of truth (``code-quality.md`` §2): the element-id
universe is :func:`toybox.activities.element_corpus.get_element` — the
loader cross-references via that lookup so the legal set of element_ids
NEVER drifts from the M1 corpus.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from toybox.activities.element_corpus import get_element

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

Source = Literal["operator", "llm"]
_VALID_SOURCES: Final[frozenset[str]] = frozenset({"operator", "llm"})

# Mirrors element_corpus._data_root path-resolution: env override for
# tests, default to ``Path("data")`` relative to the process cwd.
_DATA_ROOT_ENV: Final[str] = "TOYBOX_DATA_DIR"
_DEFAULT_DATA_ROOT: Final[Path] = Path("data")
_ELEMENTS_SUBDIR: Final[str] = "elements"
_DISTRACTORS_FILENAME: Final[str] = "distractors.json"
_CREDITS_FILENAME: Final[str] = "_distractors_credits.md"

# Env flag for opting in to LLM-source rows. Default OFF for safety;
# the literal string "1" is the ONLY truthy value (matches the plan
# spec and the existing TOYBOX_DEBUG_* style elsewhere in the codebase).
_LLM_OPT_IN_ENV: Final[str] = "TOYBOX_ALLOW_LLM_DISTRACTORS"
_LLM_OPT_IN_VALUE: Final[str] = "1"

# Injection-payload needles — case-insensitive substring match against
# fact_a_true and fact_b_false. Mirrors element_corpus._INJECTION_NEEDLES.
_INJECTION_NEEDLES: Final[tuple[str, ...]] = (
    "<system-reminder>",
    "ignore prior instructions",
)


# ---------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------


class DistractorCorpusError(ValueError):
    """Typed exception for distractor-corpus validation failures.

    Subclasses :class:`ValueError` so existing ``except ValueError``
    blocks (e.g. the FastAPI startup gate) still catch it, but the
    typed exception lets the CLI distinguish corpus failures from
    incidental JSON parse errors.

    Every message names the failing element_id (or row identifier)
    so the N1 operator running ``--validate`` can locate the broken
    entry without grepping.
    """


# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------


class Distractor(BaseModel):
    """A single distractor entry. Frozen so the cached tuple is shared safely.

    The two facts are kept loose-text (no specific length cap beyond a
    generous max) — operator phrasing varies and we don't want to
    over-prescribe. Pydantic + ``extra=forbid`` keeps the JSON schema
    tight against accidental typos.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    element_id: str = Field(min_length=1, max_length=16)
    fact_a_true: str = Field(min_length=1, max_length=240)
    fact_b_false: str = Field(min_length=1, max_length=240)


@dataclass(frozen=True)
class CreditsRow:
    """One parsed row from ``_distractors_credits.md``."""

    element_id: str
    source: Source
    reasoning: str


@dataclass(frozen=True)
class ValidationSummary:
    """Result of :func:`validate_corpus`, surfaced by the CLI."""

    entry_count: int
    credits_count: int


# ---------------------------------------------------------------------
# Path resolution + cache
# ---------------------------------------------------------------------


def _data_root() -> Path:
    raw = os.environ.get(_DATA_ROOT_ENV)
    return Path(raw) if raw else _DEFAULT_DATA_ROOT


def _distractors_path() -> Path:
    return _data_root() / _ELEMENTS_SUBDIR / _DISTRACTORS_FILENAME


def _credits_path() -> Path:
    return _data_root() / _ELEMENTS_SUBDIR / _CREDITS_FILENAME


# Cache: keyed on the resolved distractors-file path so a test that
# monkeypatches TOYBOX_DATA_DIR forces a fresh load. Same shape as
# :data:`toybox.activities.element_corpus._ELEMENT_CACHE`.
_DISTRACTOR_CACHE: dict[Path, tuple[Distractor, ...]] = {}


def clear_distractor_cache() -> None:
    """Drop the in-process cache. Test hook."""
    _DISTRACTOR_CACHE.clear()


# ---------------------------------------------------------------------
# Credits-file parser
# ---------------------------------------------------------------------


def _is_data_row(stripped: str) -> bool:
    """Return True for a markdown table row that carries data (not the header or divider)."""
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return False
    # Header divider looks like ``|---|---|---|`` — every cell, stripped of
    # surrounding whitespace, is a run of dashes / colons. Skip those.
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    if cells and all(c and set(c) <= set("-:") for c in cells):
        return False
    return True


def _parse_credits(text: str) -> tuple[CreditsRow, ...]:
    """Parse ``_distractors_credits.md`` into a tuple of :class:`CreditsRow`.

    The credits file is documented as a 3-column markdown table:
    ``| element_id | source | reasoning |``. We do NOT pull in a
    markdown library — the file is small (<150 rows max), the format is
    fixed by plan §5 / N1, and a hand-rolled split is faster + simpler.

    We skip the column-header row (``element_id``) and the divider row
    (``---``). Any other ``|``-delimited row is treated as data. Rows
    whose first cell is the literal token ``element_id`` (column
    header) are tolerated and skipped — operator may edit the file
    without restoring the divider line.

    Lines inside fenced markdown code blocks (``` ``` ``` or ``` ```lang ```)
    are skipped — the credits file's docstring shows an example table
    inside a fence, and we don't want that example to count as a real
    row. Both the opening and closing fence tokens are skipped.

    Raises :class:`DistractorCorpusError` on a malformed row (wrong
    cell count, unknown source value).
    """
    rows: list[CreditsRow] = []
    # Track every element_id we've already seen so a second occurrence is
    # flagged as a duplicate-credits-row defect (rather than silently
    # shadowing the earlier row in `_check_credits_alignment`'s dict
    # build). Mirrors the duplicate-entry rejection in `_validate_raw_entry`.
    # Real bypass vector: N1.5 appends `| au-79 | llm | auto-gen |`, then
    # an operator (or buggy tool) inserts a second `| au-79 | operator | ok |`
    # row at the bottom of the table instead of editing the first; the
    # loader would have treated `au-79` as operator-sourced, defeating the
    # `TOYBOX_ALLOW_LLM_DISTRACTORS` gate the N1 skim-review window depends on.
    seen_credit_ids: set[str] = set()
    in_fence = False
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not _is_data_row(stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells:
            continue
        # Tolerate the column-header row (operator may add/remove it).
        if cells[0].lower() == "element_id":
            continue
        if len(cells) != 3:
            raise DistractorCorpusError(
                f"_distractors_credits.md line {line_no}: expected 3 cells "
                f"(element_id | source | reasoning), got {len(cells)}: {cells!r}"
            )
        element_id, source, reasoning = cells
        if source not in _VALID_SOURCES:
            raise DistractorCorpusError(
                f"_distractors_credits.md line {line_no} (element_id={element_id!r}): "
                f"source={source!r} is not in {sorted(_VALID_SOURCES)!r}"
            )
        if element_id in seen_credit_ids:
            raise DistractorCorpusError(
                f"_distractors_credits.md line {line_no}: duplicate element_id "
                f"{element_id!r} (each element_id may appear at most once; edit "
                f"the existing row instead of appending a second one)"
            )
        seen_credit_ids.add(element_id)
        rows.append(CreditsRow(element_id=element_id, source=source, reasoning=reasoning))  # type: ignore[arg-type]
    return tuple(rows)


# ---------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------


def _check_injection(element_id: str, text: str, *, field: str) -> None:
    """Reject prompt-injection payloads in fact text (security.md defense-in-depth)."""
    haystack = text.casefold()
    for needle in _INJECTION_NEEDLES:
        if needle in haystack:
            raise DistractorCorpusError(
                f"distractor {element_id!r} {field}: injection payload {needle!r} detected; "
                f"reject per security.md (defense-in-depth)"
            )


def _llm_opt_in_enabled() -> bool:
    """Return True iff TOYBOX_ALLOW_LLM_DISTRACTORS is exactly the literal string '1'."""
    return os.environ.get(_LLM_OPT_IN_ENV) == _LLM_OPT_IN_VALUE


def _validate_raw_entry(raw: object, *, seen_ids: set[str]) -> Distractor:
    """Coerce one raw JSON entry into a :class:`Distractor`. Raises on any defect."""
    if not isinstance(raw, dict):
        raise DistractorCorpusError(
            f"distractor entry must be an object, got {type(raw).__name__}"
        )
    # element_id required + must be a string (cheap pre-Pydantic check so
    # the error names the field rather than dumping a generic Pydantic
    # error tree).
    raw_id = raw.get("element_id")
    if not isinstance(raw_id, str) or not raw_id:
        raise DistractorCorpusError(
            f"distractor entry missing or empty element_id: {raw_id!r}"
        )
    if raw_id in seen_ids:
        raise DistractorCorpusError(f"duplicate element_id {raw_id!r} in distractors.json")

    # Both facts required. Pre-Pydantic check so the error message names
    # the element_id (Pydantic strips that context when fields error
    # before the model is constructed).
    if "fact_a_true" not in raw or not isinstance(raw.get("fact_a_true"), str):
        raise DistractorCorpusError(
            f"distractor {raw_id!r}: fact_a_true missing or not a string"
        )
    if "fact_b_false" not in raw or not isinstance(raw.get("fact_b_false"), str):
        raise DistractorCorpusError(
            f"distractor {raw_id!r}: fact_b_false missing or not a string"
        )

    # Injection scan BEFORE Pydantic so the message names the element_id.
    _check_injection(raw_id, str(raw["fact_a_true"]), field="fact_a_true")
    _check_injection(raw_id, str(raw["fact_b_false"]), field="fact_b_false")

    # Cross-corpus check: element_id MUST resolve to an entry in the M1
    # element corpus. Doing this here keeps the "unknown element_id"
    # error close to the offending row.
    #
    # `get_element` returns None on miss, but its internal call to
    # `load_elements()` raises bare `ValueError` (NOT our subclass) when
    # the element corpus itself is malformed. The CLI catches
    # `DistractorCorpusError` only, so an upstream-corpus failure would
    # surface as an unhandled Python traceback rather than the documented
    # `FAIL: ...` message. Wrap it.
    try:
        resolved = get_element(raw_id)
    except DistractorCorpusError:
        # Already our type — just re-raise.
        raise
    except ValueError as exc:
        raise DistractorCorpusError(
            f"distractor {raw_id!r}: element corpus lookup failed "
            f"(elements.json is broken upstream): {exc}"
        ) from exc
    if resolved is None:
        raise DistractorCorpusError(
            f"distractor {raw_id!r}: element_id not found in element corpus "
            f"(expected a real id from data/elements/elements.json)"
        )

    # Hand to Pydantic for the per-field shape invariants.
    try:
        distractor = Distractor.model_validate(raw)
    except ValidationError as exc:
        raise DistractorCorpusError(
            f"distractor {raw_id!r}: pydantic validation failed: {exc}"
        ) from exc

    seen_ids.add(raw_id)
    return distractor


def _check_credits_alignment(
    entries: tuple[Distractor, ...],
    credits_rows: tuple[CreditsRow, ...],
    *,
    llm_opt_in: bool,
) -> None:
    """Cross-check entries against credits rows. Raises on any mismatch.

    Rules enforced:
    1. Every entry's element_id has a matching credits row.
    2. ``source: llm`` rows are rejected unless ``llm_opt_in`` is True.
    3. Credits rows whose element_id has no entry are tolerated (operator
       may pre-author credits while filling distractors.json gradually);
       callers can spot-check via the CLI's count summary.
    """
    credits_by_id: dict[str, CreditsRow] = {r.element_id: r for r in credits_rows}
    for entry in entries:
        row = credits_by_id.get(entry.element_id)
        if row is None:
            raise DistractorCorpusError(
                f"distractor {entry.element_id!r}: no matching row in "
                f"_distractors_credits.md (every entry needs a credits row)"
            )
        if row.source == "llm" and not llm_opt_in:
            raise DistractorCorpusError(
                f"distractor {entry.element_id!r}: credits row says "
                f"source: llm, but {_LLM_OPT_IN_ENV} is not set to "
                f"{_LLM_OPT_IN_VALUE!r} — set {_LLM_OPT_IN_ENV}=1 to opt in "
                f"(see Phase N N1.5 plan)"
            )


# ---------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------


def load_distractors() -> tuple[Distractor, ...]:
    """Return the validated distractor corpus. Cached on first call.

    Re-reads when ``TOYBOX_DATA_DIR`` changes in-process (cache is
    keyed on the resolved path). Raises :class:`DistractorCorpusError`
    on any validator defect — the corpus is bundled, so a failure is a
    packaging or operator-authoring error and should crash loudly
    rather than silently degrade.

    The empty scaffold (``[]`` + header-only credits file) is valid
    and returns an empty tuple.
    """
    path = _distractors_path()
    cached = _DISTRACTOR_CACHE.get(path)
    if cached is not None:
        return cached

    raw_text = path.read_text(encoding="utf-8")
    try:
        raw_payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise DistractorCorpusError(
            f"distractors.json at {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw_payload, list):
        raise DistractorCorpusError(
            f"distractors.json at {path} must be a JSON array, "
            f"got {type(raw_payload).__name__}"
        )

    credits_text = _credits_path().read_text(encoding="utf-8") if _credits_path().exists() else ""
    credits_rows = _parse_credits(credits_text)

    seen_ids: set[str] = set()
    entries: list[Distractor] = []
    for raw_entry in raw_payload:
        entries.append(_validate_raw_entry(raw_entry, seen_ids=seen_ids))

    result = tuple(entries)
    _check_credits_alignment(result, credits_rows, llm_opt_in=_llm_opt_in_enabled())

    _DISTRACTOR_CACHE[path] = result
    return result


def validate_corpus() -> ValidationSummary:
    """Load the corpus + return a :class:`ValidationSummary` for the CLI.

    Distinct from :func:`load_distractors` only in that it also reports
    the credits-row count (so the CLI can print
    ``N entries, M credits rows, OK``).
    """
    entries = load_distractors()
    credits_text = _credits_path().read_text(encoding="utf-8") if _credits_path().exists() else ""
    credits_rows = _parse_credits(credits_text)
    return ValidationSummary(entry_count=len(entries), credits_count=len(credits_rows))


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toybox.activities.distractor_corpus",
        description=(
            "Validate data/elements/distractors.json and _distractors_credits.md. "
            "Run this after every edit to confirm authoring is correct."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run the full validator and print a one-line summary. Exit 0 on success.",
    )
    return parser


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    return _build_arg_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if not args.validate:
        # Default behavior with no flags: show usage. Mirrors
        # toybox.audio.stt.main where a single-purpose CLI requires the
        # explicit verb flag and prints help otherwise.
        parser.print_help()
        return 0

    clear_distractor_cache()
    try:
        summary = validate_corpus()
    except DistractorCorpusError as exc:
        # Print a partial summary if we can compute the credits count
        # without re-triggering the load failure — helpful for the
        # operator to see "I have 117 entries, 118 credits rows; row
        # X is broken".
        credits_count = _safe_credits_count()
        print(
            f"? entries, {credits_count} credits rows, FAIL: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"{summary.entry_count} entries, {summary.credits_count} credits rows, OK")
    return 0


def _safe_credits_count() -> int | str:
    """Return the credits-row count for the error-path summary, or '?' on parse failure."""
    try:
        if not _credits_path().exists():
            return 0
        return len(_parse_credits(_credits_path().read_text(encoding="utf-8")))
    except DistractorCorpusError:
        return "?"


__all__ = [
    "CreditsRow",
    "Distractor",
    "DistractorCorpusError",
    "ValidationSummary",
    "clear_distractor_cache",
    "load_distractors",
    "validate_corpus",
]


if __name__ == "__main__":
    sys.exit(main())
