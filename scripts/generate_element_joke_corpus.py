"""Phase Q Step Q4 — generate LLM-authored element-themed jokes.

One-shot CLI that iterates the 118 elements in
``data/elements/elements.json`` and prompts Claude (via the OAuth
:class:`AnthropicClient`) for one kid-friendly joke per element. The
result is appended to ``data/jokes/jokes.json`` after stripping the
previous ``element-joke-*`` cohort. Q4 ships the SCRIPT only; the live
LLM run is Q7 (operator) and ``--dry-run`` covers automated tests.

Coverage
--------

ALL 118 elements. Unlike songs (Phase M Step M7a backfilled 25
hand-authored entries, so Q3 only fills the 93 gap), jokes had no M-era
backfill — every element gets a fresh LLM-authored joke here.

Output shape
------------

Each entry matches the :class:`toybox.activities.joke_corpus.Joke`
model with the Phase Q optional ``element_id`` and ``family`` fields
populated::

    {
        "id": "element-joke-<symbol-lower>-<atomic-number>",
        "setup": "<≤200 chars>",
        "punchline": "<≤200 chars>",
        "theme": "silly",
        "optional_toy_slot": false,
        "age_band": "3-5",
        "persona_compat": ["periodic_table", "all"],
        "element_id": "<element.id>",
        "family": "<element.family.value>"
    }

Constants (per phase-q-plan §6 D4 and §6 D5):

* ``theme`` is always ``"silly"`` — element jokes are not science
  lectures; the joke universe is silly humor.
* ``optional_toy_slot`` is always ``False`` — element jokes do not
  toy-substitute.
* ``age_band`` is always ``"3-5"`` — Child B (4yo, pre-reader) is the
  primary audience; Child A (6) tolerates the same band.
* ``persona_compat`` is always ``["periodic_table", "all"]`` so the
  joke surfaces under Professor Iridia AND as a universal fallback.
* ``id`` prefix is the load-bearing :data:`ELEMENT_JOKE_ID_PREFIX`
  constant — strip + append idempotency keys on it.

Model
-----

Pinned via the same env var as the rest of toybox
(:func:`toybox.ai.client.text_model`, default ``claude-sonnet-4-6``).
No hard-coded model id at the call site — model swap is a one-env-var
change for the operator.

CLI flags
---------

``--dry-run``      mock Claude responses (no network); render shape
                   from each element's ``story_seed_hooks``.
``--force``        log tag only; idempotent strip+append is always on.
``--validate``     after write, re-load via
                   :func:`toybox.activities.joke_corpus.load_jokes` and
                   assert all 118 element-joke entries loaded cleanly.
``--output PATH``  override the destination (default
                   ``data/jokes/jokes.json``).
``--limit N``      only process the first N elements (smoke / CI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Final

_logger = logging.getLogger(__name__)

# Public idempotency contract. The strip pass removes every entry whose
# id starts with this prefix BEFORE the new cohort is appended, so a
# re-run never duplicates. Exposed as a module-level constant (not
# hard-coded inline) per code-quality.md §2 "one source of truth for
# data-shape constants" — the unit tests assert against this same name.
ELEMENT_JOKE_ID_PREFIX: Final[str] = "element-joke-"

_DEFAULT_OUTPUT: Final[Path] = Path("data/jokes/jokes.json")
_DEFAULT_ELEMENTS: Final[Path] = Path("data/elements/elements.json")

# Per-entry constants — single source of truth, asserted by tests.
_THEME: Final[str] = "silly"
_AGE_BAND: Final[str] = "3-5"
_PERSONA_COMPAT: Final[tuple[str, ...]] = ("periodic_table", "all")
_OPTIONAL_TOY_SLOT: Final[bool] = False

# LLM token budget — a setup + punchline pair plus the structured
# delimiters fits comfortably in 400 output tokens; we double it for
# margin so the model is never truncated mid-punchline.
_MAX_TOKENS: Final[int] = 800

# Length cap enforced both in the prompt instructions AND in
# :func:`parse_llm_response`. Matches the
# :class:`toybox.activities.joke_corpus.Joke` Pydantic ``max_length``
# on ``setup`` and ``punchline`` (200 chars). One source of truth.
_MAX_LINE_CHARS: Final[int] = 200

# Marker tokens the LLM is asked to emit. Kept here as constants so the
# parser and the prompt stay in sync — if the format changes, both
# update together. Plain ALL-CAPS markers chosen over JSON to keep the
# parser robust against the model wrapping its reply in prose.
_SETUP_MARKER: Final[str] = "SETUP:"
_PUNCHLINE_MARKER: Final[str] = "PUNCHLINE:"


# ---------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------


def build_prompt(element: dict[str, Any]) -> str:
    """Build the per-element LLM prompt asking for setup + punchline.

    Inputs are the raw dict shape from ``data/elements/elements.json``
    (not the pydantic ``Element`` model) so this helper stays callable
    without a model-load step in tests.

    Constraints baked into the prompt (matches the assertions in
    :func:`parse_llm_response`):

    * Each line ≤ :data:`_MAX_LINE_CHARS` characters.
    * Single joke (setup + punchline pair); no extras.
    * Audience: 3-5 year olds (Child B is 4 and pre-reader).
    * No element-as-character personification — the element is the
      TOPIC, not a speaking character. Per the M-era template rewrite
      (commit ``ce740fc``) which fixed the {guide_mentor}-as-actor
      pattern in element pretend-play templates: the joke is ABOUT the
      element, not narrated FROM it.
    * No literacy-dependent puns (Child B can't read).
    * Output format uses the literal markers :data:`_SETUP_MARKER` and
      :data:`_PUNCHLINE_MARKER` on their own lines, which the parser
      keys on.
    """
    name = element.get("name", "?")
    symbol = element.get("symbol", "?")
    family = element.get("family", "?")
    fun_fact = element.get("fun_fact", "")
    seeds = element.get("story_seed_hooks", []) or []
    seed_block = "\n".join(f"- {hook}" for hook in seeds[:3])

    return (
        f"You write one kid-friendly joke about the element {name} "
        f"(symbol {symbol}, family: {family}).\n"
        "\n"
        f"Audience: ages 3-5. The joke will be read aloud by a parent to "
        f"a pre-reader. The kid does NOT need to read text to get the joke "
        f"-- puns that only land in writing (e.g. spelling tricks) are "
        f"forbidden.\n"
        "\n"
        f"Topic context:\n"
        f"- Fun fact: {fun_fact}\n"
        f"- Story seeds you may draw imagery from:\n"
        f"{seed_block}\n"
        "\n"
        "Style rules:\n"
        f"- Treat the element as the TOPIC, not a speaking character. "
        f"Do not personify {name} as a character that talks, has feelings, "
        f"or takes actions in first person. The joke is ABOUT {name}, not "
        f"narrated BY {name}.\n"
        "- Keep it silly and warm. No fart jokes; no insults; no scary "
        "framings (no 'lick it' / 'eat it' / 'touch it' for hazardous "
        "elements).\n"
        f"- Setup must be <= {_MAX_LINE_CHARS} characters.\n"
        f"- Punchline must be <= {_MAX_LINE_CHARS} characters.\n"
        "- Setup and punchline on their own lines.\n"
        "\n"
        f"Output format (exactly two lines, each starting with the marker):\n"
        f"{_SETUP_MARKER} <setup line here>\n"
        f"{_PUNCHLINE_MARKER} <punchline line here>\n"
    )


# ---------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------


def parse_llm_response(raw: str, element: dict[str, Any]) -> dict[str, str]:
    """Extract ``{"setup": ..., "punchline": ...}`` from the raw text.

    Tolerant to: leading/trailing whitespace, model-prefixed pleasantries
    above the markers, blank lines between marker lines. STRICT on:
    missing marker, empty setup or punchline, >200 char setup or
    punchline.

    Raises ``ValueError`` on any defect so the caller can WARN + skip
    the offending element. ``element`` is passed in so error messages
    name the element id (debug ergonomics for the operator running Q7).
    """
    element_id = element.get("id", "<unknown>")

    setup: str | None = None
    punchline: str | None = None

    # Scan line-by-line so spurious prose around the markers doesn't
    # break parsing. The model occasionally prefixes "Here's a joke:"
    # despite the explicit format instruction — be lenient.
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(_SETUP_MARKER):
            setup = stripped[len(_SETUP_MARKER) :].strip()
        elif stripped.startswith(_PUNCHLINE_MARKER):
            punchline = stripped[len(_PUNCHLINE_MARKER) :].strip()

    if setup is None:
        raise ValueError(
            f"element {element_id!r}: response missing {_SETUP_MARKER!r} "
            f"marker (raw={raw!r})"
        )
    if punchline is None:
        raise ValueError(
            f"element {element_id!r}: response missing {_PUNCHLINE_MARKER!r} "
            f"marker (raw={raw!r})"
        )

    if not setup:
        raise ValueError(f"element {element_id!r}: setup is empty")
    if not punchline:
        raise ValueError(f"element {element_id!r}: punchline is empty")

    if len(setup) > _MAX_LINE_CHARS:
        raise ValueError(
            f"element {element_id!r}: setup is {len(setup)} chars "
            f"(>{_MAX_LINE_CHARS} cap)"
        )
    if len(punchline) > _MAX_LINE_CHARS:
        raise ValueError(
            f"element {element_id!r}: punchline is {len(punchline)} chars "
            f"(>{_MAX_LINE_CHARS} cap)"
        )

    return {"setup": setup, "punchline": punchline}


# ---------------------------------------------------------------------
# Entry assembly
# ---------------------------------------------------------------------


def build_entry(element: dict[str, Any], llm_response: str) -> dict[str, Any]:
    """Assemble one ``jokes.json`` entry from an element + raw LLM reply.

    Raises ``ValueError`` (via :func:`parse_llm_response`) if the reply
    doesn't conform; the caller decides whether to WARN + skip or abort.

    Field order matches existing ``data/jokes/jokes.json`` entries so a
    visual diff stays readable.
    """
    parsed = parse_llm_response(llm_response, element)
    symbol_lower = str(element["symbol"]).lower()
    atomic_number = int(element["atomic_number"])
    joke_id = f"{ELEMENT_JOKE_ID_PREFIX}{symbol_lower}-{atomic_number}"

    return {
        "id": joke_id,
        "setup": parsed["setup"],
        "punchline": parsed["punchline"],
        "theme": _THEME,
        "optional_toy_slot": _OPTIONAL_TOY_SLOT,
        "age_band": _AGE_BAND,
        "persona_compat": list(_PERSONA_COMPAT),
        "element_id": str(element["id"]),
        "family": str(element["family"]),
    }


# ---------------------------------------------------------------------
# Strip / append helpers
# ---------------------------------------------------------------------


def strip_existing(jokes: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    """Return ``jokes`` with every entry whose id starts with ``prefix`` removed.

    Prefix-based (not allow-list) because Q4's cohort spans all 118
    elements and a hand-maintained allow-list would drift every time
    the element catalog changed. The prefix ``element-joke-`` is
    namespaced so existing Phase K hand-authored entries
    (``why-chicken-crossed``, ``knock-knock-boo``, etc.) are NEVER
    matched.
    """
    return [j for j in jokes if not str(j.get("id", "")).startswith(prefix)]


def _load_existing_jokes(path: Path) -> list[dict[str, Any]]:
    """Read the existing jokes corpus as a JSON array.

    Mirrors :func:`scripts.generate_element_song_manifest._load_existing`
    structurally — the jokes file is a top-level array, same as the
    song manifest.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append element "
            f"joke entries. Run from the worktree root."
        )
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError(
            f"output file {path} is not a JSON array (got "
            f"{type(payload).__name__}); refusing to overwrite"
        )
    return payload


def _load_elements(path: Path) -> list[dict[str, Any]]:
    """Read the element catalog from ``path`` as a list of raw dicts.

    Reading the raw JSON (not via ``load_elements()``) so a corpus
    schema change does not block the joke generator from running for
    the schema-stable fields it needs (id, symbol, atomic_number,
    name, family, fun_fact, story_seed_hooks).
    """
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError(
            f"element catalog {path} is not a JSON array (got "
            f"{type(payload).__name__})"
        )
    out: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError(
                f"element catalog {path}: entry is not an object: {entry!r}"
            )
        out.append(entry)
    return out


def _write_payload(path: Path, payload: list[dict[str, Any]]) -> None:
    """Persist with the same indent + trailing newline shape as jokes.json."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Mock-mode response (for --dry-run + tests)
# ---------------------------------------------------------------------


def _mock_response(element: dict[str, Any]) -> str:
    """Render a deterministic fake LLM reply for ``--dry-run`` + tests.

    Mirrors the format the prompt asks the real model to produce, so
    :func:`parse_llm_response` exercises the same path on both
    live + dry-run inputs. Uses the element's first story_seed_hook as
    the setup material so the output is at least element-flavored
    (helps the operator eyeball a dry-run for shape).
    """
    name = element.get("name", "this element")
    seeds = element.get("story_seed_hooks", []) or []
    seed = seeds[0] if seeds else f"{name} is interesting"
    # Strip the {name} placeholder so dry-run shape looks realistic.
    seed_rendered = str(seed).replace("{name}", name)
    return (
        f"{_SETUP_MARKER} Why is {name} a great topic for a joke?\n"
        f"{_PUNCHLINE_MARKER} Because {seed_rendered}!\n"
    )


# ---------------------------------------------------------------------
# LLM client construction
# ---------------------------------------------------------------------


def _build_real_client() -> Any:
    """Construct an :class:`AnthropicClient` from the persisted OAuth token.

    Imports are deferred to keep ``--dry-run`` and ``--help`` paths
    network-free and import-error free even in environments where the
    OAuth token does not exist yet (the test suite never hits this
    branch; Q7 operator invocation does).
    """
    from toybox.ai.client import AnthropicClient
    from toybox.ai.oauth import load_token

    token = load_token()
    if token is None:
        raise SystemExit(
            "OAuth token missing — run `uv run python -m toybox.ai --check` "
            "to verify auth before invoking the joke generator without --dry-run."
        )
    return AnthropicClient(token)


def _call_client(client: Any, prompt: str) -> str:
    """Invoke ``client.complete_text`` synchronously and return the text payload.

    The :class:`AIClient` Protocol is async (so production call sites
    can yield to the mic loop); a one-shot CLI doesn't need that, so
    we wrap each call in :func:`asyncio.run`. Each element gets its own
    event-loop spin — keeps state isolated so a failure on element N
    doesn't poison element N+1.
    """
    from toybox.ai.client import AIMessage

    async def _do() -> str:
        resp = await client.complete_text(
            [AIMessage(role="user", content=prompt)],
            max_tokens=_MAX_TOKENS,
        )
        return str(resp.text)

    return asyncio.run(_do())


# ---------------------------------------------------------------------
# Validation (post-write)
# ---------------------------------------------------------------------


def _validate_post_write(path: Path, *, expected_min: int) -> None:
    """Re-load via the production joke_corpus loader and assert the cohort loaded.

    Mirrors M7a's validator. Uses ``>= expected_min`` rather than
    equality because Q3's song run + Q4's joke run are independent;
    operator may run Q4 multiple times during Q7 skim-review with
    inline JSON edits in between, and ``expected_min`` is the lower
    bound for "the cohort was written".
    """
    # Local import: keep joke_corpus deps off the module-import path so
    # `--dry-run` works without ever touching the loader.
    from toybox.activities.joke_corpus import clear_joke_cache, load_jokes

    clear_joke_cache()
    jokes = load_jokes()
    loaded = [j for j in jokes if j.id.startswith(ELEMENT_JOKE_ID_PREFIX)]
    if len(loaded) < expected_min:
        raise SystemExit(
            f"--validate: expected >={expected_min} element-joke entries "
            f"to load, got {len(loaded)}. Check {path} for shape errors "
            f"and re-run."
        )
    _logger.info(
        "--validate: %d element-joke entries loaded cleanly through "
        "toybox.activities.joke_corpus.load_jokes",
        len(loaded),
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate one LLM-authored kid-friendly joke per element "
            "(all 118) and append into data/jokes/jokes.json. "
            "Phase Q Step Q4."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Skip live Claude calls; render deterministic mock entries "
            "from each element's story_seed_hooks. Print the merged "
            "JSON to stdout and exit; do not write the file."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=("File to append to. Default: data/jokes/jokes.json."),
    )
    parser.add_argument(
        "--elements",
        type=Path,
        default=_DEFAULT_ELEMENTS,
        help=("Element catalog to iterate. Default: data/elements/elements.json."),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Idempotent regeneration is always-on (existing "
            f"{ELEMENT_JOKE_ID_PREFIX!r}-prefixed entries are stripped "
            "before appending); this flag just tags the run in the log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load via the production joke_corpus loader "
            "and assert the element-joke cohort loaded cleanly."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Process only the first N elements (smoke / CI). Default: "
            "no limit (all 118)."
        ),
    )
    return parser.parse_args(argv)


def _generate_entries(
    elements: list[dict[str, Any]],
    *,
    dry_run: bool,
    limit: int | None,
) -> list[dict[str, Any]]:
    """Drive the per-element prompt → parse → entry pipeline.

    Logs INFO on each successful element and WARN on per-element parse
    failures (skipping that element). The caller decides whether to
    treat a partial cohort as success — for Q4 we accept partial; an
    operator running with ``--validate`` will catch a too-small cohort.
    """
    client: Any = None
    if not dry_run:
        client = _build_real_client()

    target = elements if limit is None else elements[:limit]
    entries: list[dict[str, Any]] = []
    for idx, element in enumerate(target, start=1):
        element_id = element.get("id", "<unknown>")
        prompt = build_prompt(element)
        try:
            if dry_run:
                raw = _mock_response(element)
            else:
                raw = _call_client(client, prompt)
            entry = build_entry(element, raw)
        except ValueError as exc:
            _logger.warning(
                "element %s (%d/%d): parse failure, skipping: %s",
                element_id,
                idx,
                len(target),
                exc,
            )
            continue
        entries.append(entry)
        _logger.info(
            "element %s (%d/%d): generated id=%s",
            element_id,
            idx,
            len(target),
            entry["id"],
        )
    return entries


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    output: Path = args.output
    elements_path: Path = args.elements

    elements = _load_elements(elements_path)
    _logger.info(
        "loaded %d elements from %s (limit=%s, dry_run=%s, force=%s)",
        len(elements),
        elements_path,
        args.limit,
        args.dry_run,
        args.force,
    )

    new_entries = _generate_entries(
        elements,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    if not new_entries:
        _logger.error("no element-joke entries generated; aborting")
        return 1

    existing = _load_existing_jokes(output)
    pre_count = len(existing)
    stripped = strip_existing(existing, ELEMENT_JOKE_ID_PREFIX)
    stripped_count = pre_count - len(stripped)
    merged = stripped + new_entries
    post_count = len(merged)

    _logger.info(
        "summary: pre=%d, removed_existing_element_jokes=%d, generated=%d, post=%d",
        pre_count,
        stripped_count,
        len(new_entries),
        post_count,
    )

    if args.dry_run:
        sys.stdout.write(json.dumps(merged, indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0

    _write_payload(output, merged)
    _logger.info("wrote %d jokes to %s", post_count, output)

    if args.validate:
        _validate_post_write(output, expected_min=len(new_entries))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
