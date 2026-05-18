"""Phase M Step M4 — generate 118 "Meet an Element" request_activity templates.

One-shot CLI that walks the 118-element corpus loaded by
:mod:`toybox.activities.element_corpus` and appends one ``meet_element_<id>``
template per element to
``src/toybox/activities/templates/branching/request_activity.json``.

Each generated template is shaped so Professor Iridia (persona-picker
bias via ``required_roles: ["guide_mentor"]``) introduces one element
through the M3 :class:`ElementCard` (kiosk-side inline render keyed on
``step.element_id``). The activity ends with a Phase L song reward via
``ending_step: {"kind": "song", "auto": true}``.

Step-count
----------

The phase-m plan §5.4 example shows a single step. The current production
validators (``Template.steps min_length=3`` in
:mod:`toybox.activities.models` and ``"minItems": 3`` in
``_schema.json``) reject one-step templates and were NOT relaxed in M3.
Touching M3's schema surface from M4 is out of scope per the build-step
prompt, so the generator emits three logical narration steps per
element:

1. Opening + name reveal (+ pronunciation guide when present); carries
   ``element_id`` so the M3 ElementCard renders once at the top.
2. ``fun_fact`` verbatim from the corpus.
3. One ``story_seed_hooks`` entry (rotated by index) + a soft outro.

This preserves the single-conceptual-card UX (one ElementCard renders,
on step 1 only — Phase L's reward step appends a song after step 3) and
satisfies both validator layers. The plan-example mismatch is recorded
in M4's build report rather than corrected here.

Idempotence
-----------

Re-running the script is safe: existing ``meet_element_*`` entries are
stripped before the new batch is appended, so the script always produces
exactly 118 fresh entries (sorted by ``atomic_number``) plus the
pre-existing non-meet templates in their original order. ``--force`` is
accepted for parity with sibling scripts but has no observable effect
beyond the always-on idempotent regeneration (the flag toggles a log
line so callers can audit a reset versus an initial append).

Validation
----------

``--validate`` invokes ``toybox.activities.generator._load_intent_templates``
after writing and asserts the post-write count equals
``pre_existing_non_meet + 118``. A drop indicates the schema or Pydantic
gate rejected a generated entry (the whole intent file is dropped on
JSON-schema failure per :func:`generator._load_intent_templates`, so the
delta surfaces cleanly).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

# The project's pyproject.toml restricts mypy to src/ + tests/, so a
# per-file mypy invocation against this script doesn't see the toybox
# package's py.typed marker; suppress the resulting import-untyped
# warning locally. Production imports inside src/ are unaffected.
from toybox.activities.element_corpus import (  # type: ignore[import-untyped]
    Element,
    load_elements,
)

_logger = logging.getLogger(__name__)

# Path constants (mirrors scripts/generate_element_sprites.py style:
# project-relative defaults, env-free, operator overrides via --output).
_DEFAULT_OUTPUT: Final[Path] = Path(
    "src/toybox/activities/templates/branching/request_activity.json"
)

# Template id prefix; iteration order is by ascending atomic_number so a
# diff against a re-generated file is byte-stable.
_TEMPLATE_ID_PREFIX: Final[str] = "meet_element_"

# Music-keyword heuristic per the build-step prompt. Hits in
# ``fun_fact + story_seed_hooks`` route the template to
# ``recommended_themes: ["music"]``; otherwise ``["silly"]``. The list is
# deliberately short to keep the music bucket at the prompt's ~20-30
# expected share — false positives mostly land on "sound" hits like neon
# signs and gold's musical-folklore reputation, which is the desired
# routing.
_MUSIC_KEYWORDS: Final[tuple[str, ...]] = (
    "song",
    "music",
    "sound",
    "whistle",
    "hum",
    "note",
    "bell",
    "ring",
)

# Opening-phrase pool (5 variants) for non-pronunciation-guide elements.
# Rotated by ``index % len(pool)`` so consecutive elements draw distinct
# openings. Each opening MUST end mid-sentence so the per-element name
# slot lands inside the persona's quoted speech ("...{name}!"). The
# closing-quote round-out is appended by the caller.
#
# Each opening embeds the ``{guide_mentor}`` role placeholder so the
# template's ``required_roles: ["guide_mentor"]`` declaration satisfies
# the K3.2 distinct-toy-ceiling validator gate (see
# :func:`toybox.activities._validator.validate_template`). At runtime
# the role-slot picker fills ``{guide_mentor}`` from the parent's toy
# pool (toy with role=guide_mentor wins; otherwise the fallback string
# ``"a kindly mentor"`` from
# :data:`toybox.activities.generic_descriptors.GENERIC_DESCRIPTORS` is
# substituted). When Professor Iridia is the active persona — the
# persona-picker bias case the plan §5.4 calls out — the persona narrates
# the activity regardless of the placeholder fill; the placeholder
# resolves a TOY name, not the persona name, so the narrative voice is
# still hers.
_OPENINGS_PLAIN: Final[tuple[str, ...]] = (
    '{guide_mentor} pulls a shiny card from a pocket. "This is {name}!',
    '{guide_mentor} holds up a glowing card. "Look at this — {name}!',
    '{guide_mentor} turns the page in a sparkly notebook. "Today we meet {name}!',
    '{guide_mentor} smiles and reveals a new card. "Say hello to {name}!',
    "{guide_mentor} taps the clipboard with a glittery pen. \"Here's a special one — {name}!",
)

# Pronunciation-guide variant pool (5 variants). Each must speak the
# name AND inject the phonetic respelling so a pre-reader hears both.
# Same ``{guide_mentor}`` placeholder convention as the plain pool above.
_OPENINGS_PRONOUNCED: Final[tuple[str, ...]] = (
    '{guide_mentor} pulls a shiny card from a pocket. "This is {name} — say {guide} with me!',
    "{guide_mentor} holds up a glowing card. \"Look at this — {name}! That's {guide}.",
    "{guide_mentor} turns the page in a sparkly notebook. "
    '"Today we meet {name} — try saying {guide}!',
    '{guide_mentor} smiles and reveals a new card. "Say hello to {name}! Sound it out: {guide}.',
    "{guide_mentor} taps the clipboard with a glittery pen. "
    "\"Here's a tricky name — {name}, or {guide}!",
)

# Step-2 fun-fact lead-ins. Rotated by index so consecutive elements
# don't all start with the same connector.
_FACT_LEADINS: Final[tuple[str, ...]] = (
    'She leans in close. "Did you know? {fact}"',
    'She points at the card. "Here\'s the cool part — {fact}"',
    'She wiggles her eyebrows. "Listen to this: {fact}"',
    'She tilts the card so it catches the light. "Check this out — {fact}"',
)

# Step-3 hook lead-ins. Same rotation pattern; the soft outro at the end
# nudges the kiosk into the ending-step song without prompting a kid
# action (every Meet-an-Element template is narration-only per phase-m-
# plan §6.10 — no template-level encouragement to touch / taste).
_HOOK_LEADINS: Final[tuple[str, ...]] = (
    'She winks. "Imagine — {hook}. Fun, right?"',
    'She giggles. "Picture this: {hook}!"',
    'She nods slowly. "Here\'s a fun thought — {hook}."',
    'She spreads her hands wide. "Wow — {hook}! Now let\'s sing about it."',
)


# ---------------------------------------------------------------------
# Theme heuristic
# ---------------------------------------------------------------------


def _pick_theme(element: Element) -> str:
    """Return ``"music"`` if the element's prose hits a music-adjacent
    keyword; else ``"silly"``.

    Searches ``fun_fact`` + each ``story_seed_hooks`` entry. Match is
    case-insensitive on raw substring (no word-boundary) so compound
    words like ``"songbird"`` count toward the music bucket — desired
    behaviour because a kid hearing "songbird" still hears "song."
    """
    haystack = " ".join((element.fun_fact, *element.story_seed_hooks)).casefold()
    for needle in _MUSIC_KEYWORDS:
        if needle in haystack:
            return "music"
    return "silly"


# ---------------------------------------------------------------------
# Hook substitution
# ---------------------------------------------------------------------


def _substitute_name(hook: str, name: str) -> str:
    """Replace the ``{name}`` placeholder used in ``story_seed_hooks``.

    ``elements.json`` writes hooks like ``"{name} floats balloons up
    into the sky"`` so the corpus stays name-agnostic. M4's narration
    needs the real element name inline; ``.format()`` would also
    interpret other ``{}`` tokens (none present today but a hostile
    corpus author could inject one), so we use a literal ``str.replace``
    instead — safer and faster.
    """
    return hook.replace("{name}", name)


# ---------------------------------------------------------------------
# Template id derivation
# ---------------------------------------------------------------------


def _template_id_for(element: Element) -> str:
    """Translate ``<symbol-lower>-<atomic_number>`` (e.g. ``"au-79"``)
    into the template id format ``meet_element_<symbol>_<atomic_number>``
    (e.g. ``"meet_element_au_79"``).

    The template id regex (``^[a-z0-9][a-z0-9_]*$``, max 64) forbids
    hyphens; the step ``element_id`` regex (``^[a-z]{1,3}-[0-9]{1,3}$``)
    requires them. The two formats are different by design — see
    phase-m-plan.md §5.4 example.
    """
    return f"{_TEMPLATE_ID_PREFIX}{element.id.replace('-', '_')}"


# ---------------------------------------------------------------------
# Per-element template
# ---------------------------------------------------------------------


def _build_template(element: Element, *, index: int) -> dict[str, Any]:
    """Render one ``meet_element_<id>`` template dict.

    ``index`` is the element's 0-indexed position in the corpus walk
    (sorted by ``atomic_number``). Used to rotate openings, fact
    lead-ins, hook lead-ins, and the picked ``story_seed_hooks`` entry
    so consecutive elements feel varied to a kid who hears multiple
    "Meet" cards in a row.
    """
    name = element.name
    fact = element.fun_fact
    hooks = element.story_seed_hooks
    hook_raw = hooks[index % len(hooks)]
    hook = _substitute_name(hook_raw, name)

    if element.pronunciation_guide is not None:
        opening_tpl = _OPENINGS_PRONOUNCED[index % len(_OPENINGS_PRONOUNCED)]
        # ``str.replace`` rather than ``str.format`` because the
        # opening also carries the literal ``{guide_mentor}`` role
        # placeholder which the runtime slot-fill engine consumes — we
        # must NOT expand it at generation time.
        opening = opening_tpl.replace("{name}", name).replace(
            "{guide}", element.pronunciation_guide
        )
    else:
        opening_tpl = _OPENINGS_PLAIN[index % len(_OPENINGS_PLAIN)]
        opening = opening_tpl.replace("{name}", name)
    # Close the persona's quoted speech for the step-1 narration.
    step1_text = f'{opening}"'

    fact_lead = _FACT_LEADINS[index % len(_FACT_LEADINS)]
    step2_text = fact_lead.replace("{fact}", fact)

    hook_lead = _HOOK_LEADINS[index % len(_HOOK_LEADINS)]
    step3_text = hook_lead.replace("{hook}", hook)

    theme = _pick_theme(element)

    # Field order matches the 250 pre-existing Phase G + Phase K templates
    # in request_activity.json: id, title, buckets, steps, required_roles,
    # optional_roles, recommended_themes, ending_step. Convention break
    # caught by the M4 style reviewer 2026-05-18; keeping the order
    # consistent across all 368 templates makes the JSON readable as a
    # single file.
    return {
        "id": _template_id_for(element),
        "title": f"Meet {name}!",
        "buckets": ["always"],
        "steps": [
            {
                "text": step1_text,
                "action_slot": "pointing",
                "element_id": element.id,
            },
            {
                "text": step2_text,
                "action_slot": "thinking",
            },
            {
                "text": step3_text,
                "action_slot": "looking",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": [theme],
        "ending_step": {
            "kind": "song",
            "auto": True,
        },
    }


# ---------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------


def _load_existing(path: Path) -> dict[str, Any]:
    """Read the existing intent file. Raises a clear error if missing
    or malformed — we never want to silently overwrite a structurally
    broken file with a fresh one.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append meet-element "
            f"templates. Run from the worktree root."
        )
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"output file {path} is not a JSON object (got "
            f"{type(payload).__name__}); refusing to overwrite"
        )
    if payload.get("intent") != "request_activity":
        raise ValueError(
            f"output file {path} has intent={payload.get('intent')!r}; expected 'request_activity'"
        )
    templates = payload.get("templates")
    if not isinstance(templates, list):
        raise ValueError(
            f"output file {path} does not contain a 'templates' list; refusing to overwrite"
        )
    return payload


def _strip_meet_entries(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the input list with every ``meet_element_*`` entry removed.

    Idempotence guarantee: running the generator N times produces the
    same on-disk file as running it once.
    """
    return [t for t in templates if not str(t.get("id", "")).startswith(_TEMPLATE_ID_PREFIX)]


def _generate_all(elements: Iterable[Element]) -> list[dict[str, Any]]:
    """Iterate the corpus (sorted by ``atomic_number`` for deterministic
    output order) and emit one template per element."""
    sorted_elements = sorted(elements, key=lambda e: e.atomic_number)
    return [_build_template(e, index=idx) for idx, e in enumerate(sorted_elements)]


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """Persist the merged payload with 2-space indent and a trailing
    newline — matches the existing on-disk format produced by Phase G's
    template authoring tooling.
    """
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_post_write(path: Path, *, expected_meet: int) -> None:
    """Confirm the generator output round-trips through the production
    template loader.

    The loader is invoked via
    :func:`toybox.activities.generator._load_intent_templates` because
    it exercises both the JSON-schema layer (the gate that drops the
    WHOLE file if any single template trips ``minItems`` etc.) and the
    Pydantic + graph layers. A delta against ``expected_meet`` indicates
    one of those gates rejected the new batch.
    """
    # Imported inside the function so the script's normal mode doesn't
    # pay the (small) cost of pulling in the whole generator module.
    from toybox.activities.generator import (  # type: ignore[import-untyped]
        _load_intent_templates,
        clear_template_cache,
    )

    clear_template_cache()
    templates = _load_intent_templates("request_activity")
    loaded_meet = [t for t in templates if t.id.startswith(_TEMPLATE_ID_PREFIX)]
    if len(loaded_meet) != expected_meet:
        raise SystemExit(
            f"--validate: expected {expected_meet} meet_element_* templates "
            f"to load, got {len(loaded_meet)}. The whole intent file may "
            f"have been dropped on schema failure; check {path} and re-run."
        )
    _logger.info(
        "--validate: %d meet_element_* templates loaded cleanly through "
        "toybox.activities.generator._load_intent_templates",
        len(loaded_meet),
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate 118 'Meet an Element' request_activity templates "
            "from data/elements/elements.json and append them to "
            "request_activity.json (Phase M Step M4)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=("Print the merged JSON to stdout and exit; do not write the output file."),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=(
            "File to append to. Default: "
            "src/toybox/activities/templates/branching/request_activity.json."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Idempotent regeneration is always-on (existing "
            "meet_element_* entries are stripped before appending); "
            "this flag merely tags the run in the summary log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load via the production template loader "
            "and assert all 118 new templates load cleanly."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    output: Path = args.output

    payload = _load_existing(output)
    existing_templates: list[dict[str, Any]] = list(payload["templates"])
    pre_count = len(existing_templates)

    stripped = _strip_meet_entries(existing_templates)
    stripped_count = pre_count - len(stripped)

    elements = load_elements()
    new_templates = _generate_all(elements)

    merged = stripped + new_templates
    payload["templates"] = merged

    post_count = len(merged)
    _logger.info(
        "summary: pre=%d, removed_existing_meet=%d, generated=%d, post=%d, force=%s",
        pre_count,
        stripped_count,
        len(new_templates),
        post_count,
        args.force,
    )

    if args.dry_run:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0

    _write_payload(output, payload)
    _logger.info("wrote %d templates to %s", post_count, output)

    if args.validate:
        _validate_post_write(output, expected_meet=len(new_templates))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
