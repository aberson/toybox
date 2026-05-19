"""Phase N Step N4 — generate 118 ``element_microgame`` templates.

Analog to :mod:`scripts.generate_meet_element_templates` (Phase M4),
but emits the new Phase N ``template_type: "element_microgame"`` shape:
4 steps (text / fork / fork / text), Iridia-bias via
``required_roles: ["guide_mentor"]``, ``element_id`` on every step,
and a Phase L song reward via
``ending_step: {"kind": "song", "auto": true, "element_id": <id>}``.

Generation source data
----------------------

* :mod:`toybox.activities.element_corpus` — 118 elements (``Element``
  dataclass, ``Family`` StrEnum, ``get_element``, ``peer_in_family``,
  ``peer_out_of_family``).
* :mod:`toybox.activities.distractor_corpus` — 118 distractor pairs
  (``Distractor`` dataclass: ``element_id``, ``fact_a_true``,
  ``fact_b_false``).

Per-element narration shape (phase-n-plan §1):

* Step 1 (kind=text, id=``intro``): "Iridia narrates symbol, name,
  family, color, room-temperature phase."
* Step 2 (kind=fork, id=``family_fork``): binary fork —
  ``peer_in_family`` (correct) vs ``peer_out_of_family`` (wrong;
  persona redirects but advance proceeds either way).
* Step 3 (kind=fork, id=``fact_fork``): binary fork —
  ``fact_a_true`` (correct) vs ``fact_b_false`` (wrong; redirect +
  advance).
* Step 4 (kind=text, id=``reward``): close on ``fun_fact``;
  ``ending_step`` auto-fires a song reward.

Both forks are **convergent** — both choices' ``next`` points at the
SAME next step. This matches the engine-side "advance anyway" semantic
the plan calls out: wrong-answer guidance is text-level (in the choice
label / next step text), not branch-structure-level. It also keeps the
template-graph validator happy (no orphan branches).

``na-11`` / ``ca-20`` edge case
-------------------------------

``peer_in_family`` raises ``ValueError`` on Sodium (``na-11``) and
Calcium (``ca-20``) because each is the SOLE entry in its family at
age_band 3-5 (caught by N3 build, documented in
``documentation/phase-n-plan.md`` §2). Per plan §5 N4, we fall back to
using one of the element's own ``story_seed_hooks`` entries as the
"correct" step-2 choice text — the cross-family peer is still the
distractor, so the template's STRUCTURE is unchanged (still a 2-choice
fork) and only the CHOICE TEXT differs from the regular path.

Determinism
-----------

* Iteration order = sorted by ``atomic_number`` ascending.
* Per-element peer picks: ``random.Random(hash(element_id) & 0xFFFFFFFF)``,
  reseeded for each element so a corpus tweak that adds element X
  doesn't perturb element Y's peer pick.
* Same corpus + same distractors → byte-identical output across runs.

Idempotence
-----------

The strip-by-prefix pattern uses the EXACT prefix
``element_microgame_`` so the script never touches M4's
``meet_element_*`` siblings (the two prefixes share no common stem
under string ``startswith``).

``--validate`` flag
-------------------

After writing, the validate path:

1. Calls :func:`toybox.activities.generator.clear_template_cache` +
   :func:`_load_intent_templates("request_activity")` so the entire
   load pipeline (jsonschema -> Pydantic -> ``_validator``) runs over
   the fresh file.
2. Asserts the count of loaded ``element_microgame_*`` templates is
   exactly 118 (a drop indicates the WHOLE file was dropped on a
   jsonschema failure, OR an individual template failed the Pydantic
   / graph layer).
3. Runs :func:`toybox.activities._validator.validate_template` against
   every loaded ``element_microgame_*`` template and asserts all 118
   pass (defense-in-depth — the loader already runs this gate, but a
   future loader refactor could decouple them; the explicit check
   prevents silent regressions).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from toybox.activities.distractor_corpus import (
    Distractor,
    load_distractors,
)
from toybox.activities.element_corpus import (
    Element,
    Family,
    get_element,
    load_elements,
    peer_in_family,
    peer_out_of_family,
)

_logger = logging.getLogger(__name__)

# Path constants — same convention as M4.
_DEFAULT_OUTPUT: Final[Path] = Path(
    "src/toybox/activities/templates/branching/request_activity.json"
)

# Strip-by-prefix identity. Note: M4 uses ``meet_element_`` so the two
# prefixes never collide under ``str.startswith`` — the strip pass is
# safe to run against the shared output file.
_TEMPLATE_ID_PREFIX: Final[str] = "element_microgame_"

# Family slug -> kid-friendly display string. Sibling to M5's
# family-slug table (``scripts/generate_family_pretend_templates.py``);
# uses SINGULAR forms (article-aware grammar — "It's a halogen") where
# M5 uses plural ("the halogens are..."). NEVER diverge from this
# mapping in spelling/phrasing (synonyms break the M5 reviewer's grep
# contract and read inconsistent to a kid hearing both N4 and M5
# narration). Lanthanide / actinide are absent from M5 (no 3-5
# templates) but appear in the 118-element corpus, so we include them
# here too.
_FAMILY_DISPLAY: Final[dict[Family, str]] = {
    Family.noble_gas: "noble gas",
    Family.halogen: "halogen",
    Family.alkali_metal: "alkali metal",
    Family.alkaline_earth: "alkaline earth",
    Family.transition_metal: "transition metal",
    Family.post_transition_metal: "post-transition metal",
    Family.metalloid: "metalloid",
    Family.nonmetal: "nonmetal",
    Family.lanthanide: "lanthanide",
    Family.actinide: "actinide",
}


# ---------------------------------------------------------------------
# Template id derivation
# ---------------------------------------------------------------------


def _template_id_for(element: Element) -> str:
    """Translate ``<symbol-lower>-<atomic_number>`` into the template id
    format ``element_microgame_<symbol>_<atomic_number>``.

    Template ids forbid hyphens (regex ``^[a-z0-9][a-z0-9_]*$``); the
    composite ``element_id`` requires them. Same id-shape pattern as
    M4's :func:`scripts.generate_meet_element_templates._template_id_for`.
    """
    return f"{_TEMPLATE_ID_PREFIX}{element.id.replace('-', '_')}"


def _rng_for_element(element_id: str) -> random.Random:
    """Per-element deterministic ``random.Random`` instance.

    Seeded by an MD5 digest of the element_id (truncated to 32 bits)
    rather than Python's built-in ``hash``. The plan's example wording
    is ``hash(element_id) & 0xFFFFFFFF``; Python randomizes
    ``hash(str)`` per-process unless ``PYTHONHASHSEED`` is fixed, which
    would make the script's "byte-identical re-runs" property hold
    only within a single process. MD5 is content-addressable and stable
    across processes / Python versions / platforms — exactly what the
    cross-process determinism contract needs. (We are NOT relying on
    MD5 for any cryptographic property here; it's a fast stable hash.)

    Reseeded per element so adding a new corpus entry never perturbs
    the peer picks for any other element — re-running the script after
    a corpus tweak produces a clean diff of only the touched element
    rows.
    """
    digest = hashlib.md5(element_id.encode("utf-8"), usedforsecurity=False).digest()
    seed = int.from_bytes(digest[:4], "big")
    return random.Random(seed)


# ---------------------------------------------------------------------
# Per-element template
# ---------------------------------------------------------------------


def _intro_text(element: Element) -> str:
    """Step 1 narration — the ``{guide_mentor}`` (Iridia, biased by
    ``required_roles``) introduces symbol, name, family, color, and
    room-temperature phase per phase-n-plan §1.

    All non-role interpolation happens here at generation time (not
    via the runtime slot-fill engine) because the corpus fields aren't
    in ``KNOWN_SLOTS`` and would echo back as literal
    ``{family_display}`` on the kiosk if left as braces. The
    ``{guide_mentor}`` placeholder, on the other hand, MUST stay as a
    brace literal — the runtime slot-fill engine consumes it and the
    K3 validator gate ``len(required_roles) ≤ distinct_toy_ceiling``
    requires at least one ``{guide_mentor}`` reference in the
    template's text to balance the ``required_roles: ["guide_mentor"]``
    declaration.
    """
    family_display = _FAMILY_DISPLAY[element.family]
    article = "an" if family_display[0] in ("a", "e", "i", "o", "u") else "a"
    return (
        f'{{guide_mentor}} smiles and holds up a card. "Look at this! '
        f"This is {element.symbol} — {element.name}. "
        f"It's {article} {family_display}. "
        f"It's {element.color_description} and at room temperature "
        f"it's a {element.phase_at_room_temp}.\""
    )


def _family_fork_text(element: Element) -> str:
    """Step 2 question prompt."""
    family_display = _FAMILY_DISPLAY[element.family]
    return f'{{guide_mentor}} leans in. "Can you find another {family_display}?"'


def _fact_fork_text(element: Element) -> str:
    """Step 3 question prompt."""
    return f'{{guide_mentor}} tilts her head. "What else is true about {element.name}?"'


def _reward_text(element: Element) -> str:
    """Step 4 close — reveal ``fun_fact`` + cue the song reward."""
    return f'{{guide_mentor}} beams. "Here\'s a secret about {element.name}: {element.fun_fact}"'


def _build_family_fork_choices(
    element: Element,
    rng: random.Random,
    next_id: str,
) -> list[dict[str, Any]]:
    """Step 2 — same-family peer (correct) vs cross-family peer (wrong).

    Both branches point at ``next_id`` (convergent fork — engine-side
    "advance anyway" semantic). Correct choice is index 0 to match the
    plan's "choice[0] = correct" convention.

    Edge case (na-11, ca-20): ``peer_in_family`` raises because each is
    the sole entry in its family at age_band 3-5. Fall back to a
    story_seed_hooks entry as the "correct" choice label; the
    cross-family peer remains the distractor so the template shape
    (2-choice fork) is unchanged.
    """
    out_of_family = peer_out_of_family(element.id, rng)
    try:
        in_family = peer_in_family(element.id, rng)
        correct_label = in_family.name
    except ValueError:
        # na-11 / ca-20 fallback. Use story_seed_hooks[0] with {name}
        # substituted; this is the safest neutral correct-answer text
        # we can ship without a same-family peer. The narrative shape
        # still reads as "another <family>" → option A is a property
        # of the element itself, option B is the cross-family
        # distractor (still in-family-incorrect by construction).
        hook = element.story_seed_hooks[0].replace("{name}", element.name)
        correct_label = hook
        _logger.debug(
            "%s: peer_in_family raised; falling back to story_seed_hooks[0] for "
            "Step 2 correct choice",
            element.id,
        )

    return [
        {"label": correct_label, "next": next_id},
        {"label": out_of_family.name, "next": next_id},
    ]


def _build_fact_fork_choices(
    distractor: Distractor,
    next_id: str,
) -> list[dict[str, Any]]:
    """Step 3 — fact_a_true (correct) vs fact_b_false (wrong).

    Both branches point at ``next_id`` (convergent). Correct choice is
    index 0 per the plan convention.
    """
    return [
        {"label": distractor.fact_a_true, "next": next_id},
        {"label": distractor.fact_b_false, "next": next_id},
    ]


def _build_template(
    element: Element,
    distractor: Distractor,
) -> dict[str, Any]:
    """Render one ``element_microgame_<id>`` template dict.

    Field order matches the M4 ``meet_element_*`` and Phase G + K
    templates already in ``request_activity.json``: id, title, buckets,
    template_type, steps, required_roles, optional_roles,
    recommended_themes, ending_step.

    The 4 steps share the same step-id convention used across the
    Phase G + M templates (lowercase snake; no hyphens; first char
    alphanumeric).
    """
    rng = _rng_for_element(element.id)

    fact_step_id = "fact_fork"
    reward_step_id = "reward"

    return {
        "id": _template_id_for(element),
        "title": f"Microgame: {element.name}",
        "buckets": ["always"],
        "template_type": "element_microgame",
        "steps": [
            {
                "id": "intro",
                "text": _intro_text(element),
                "kind": "text",
                "element_id": element.id,
            },
            {
                "id": "family_fork",
                "text": _family_fork_text(element),
                "kind": "fork",
                "element_id": element.id,
                "choices": _build_family_fork_choices(element, rng, next_id=fact_step_id),
            },
            {
                "id": fact_step_id,
                "text": _fact_fork_text(element),
                "kind": "fork",
                "element_id": element.id,
                "choices": _build_fact_fork_choices(distractor, next_id=reward_step_id),
            },
            {
                "id": reward_step_id,
                "text": _reward_text(element),
                "kind": "text",
                "element_id": element.id,
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": [],
        "recommended_themes": ["music"],
        "ending_step": {
            "kind": "song",
            "auto": True,
            "element_id": element.id,
        },
    }


# ---------------------------------------------------------------------
# File I/O — mirrors M4
# ---------------------------------------------------------------------


def _load_existing(path: Path) -> dict[str, Any]:
    """Read the existing intent file. Same defensive shape as M4's loader."""
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append element_microgame "
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


def _strip_microgame_entries(
    templates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return ``templates`` minus every ``element_microgame_*`` entry.

    Uses the EXACT prefix ``element_microgame_`` so M4's
    ``meet_element_*`` entries are never touched (the two prefixes
    share no common stem under ``str.startswith``).
    """
    return [t for t in templates if not str(t.get("id", "")).startswith(_TEMPLATE_ID_PREFIX)]


def _generate_all(
    elements: Iterable[Element],
    distractors: Iterable[Distractor],
) -> list[dict[str, Any]]:
    """Iterate the corpus (sorted by atomic_number) and emit one
    template per element. Pairs each ``Element`` with its matching
    ``Distractor`` by ``element_id``; raises if any element has no
    distractor entry (which would indicate a corpus mismatch between
    M1 and N1).
    """
    distractor_by_id = {d.element_id: d for d in distractors}
    sorted_elements = sorted(elements, key=lambda e: e.atomic_number)
    out: list[dict[str, Any]] = []
    for element in sorted_elements:
        distractor = distractor_by_id.get(element.id)
        if distractor is None:
            raise ValueError(
                f"element {element.id!r} has no entry in distractors.json; "
                f"every element must have a paired distractor for the "
                f"element_microgame Step-3 fork"
            )
        out.append(_build_template(element, distractor))
    return out


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """Persist the merged payload — same indent / encoding / trailing
    newline as M4 so a mixed regen produces clean diffs."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_post_write(path: Path, *, expected_count: int) -> None:
    """Round-trip the freshly-written file through the production
    template loader + structural validator.

    Steps:

    1. Clear the per-intent template cache so the loader re-reads from
       disk.
    2. Load via :func:`toybox.activities.generator._load_intent_templates`,
       which exercises the jsonschema layer (WHOLE-file drop on
       failure) + Pydantic ``Template`` + ``validate_template`` graph
       + structural gates.
    3. Filter to ``element_microgame_*`` and assert the count == 118.
    4. Re-run ``validate_template`` against each loaded entry —
       defense-in-depth so a future loader refactor that decouples the
       gates can't silently skip them.

    A drop indicates the WHOLE intent file was dropped on a jsonschema
    failure (e.g. a typo in a generated step) OR an individual
    template failed Pydantic or graph validation.
    """
    # Imported inside the function so the script's normal mode doesn't
    # pay the cost of pulling in the generator module. Same pattern as
    # the M4 validator.
    import os

    from toybox.activities import generator as _generator
    from toybox.activities._validator import (
        validate_template,
    )
    from toybox.activities.generator import (
        _load_intent_templates,
        clear_template_cache,
    )
    from toybox.activities.models import Template

    # Test indirection: when ``TOYBOX_TEMPLATES_DIR`` is set, point the
    # loader at the sandbox templates dir for the duration of validation.
    # Production mode leaves the env var unset → ``TEMPLATES_DIR`` keeps
    # its default ``src/toybox/activities/templates`` value and behavior
    # is unchanged. This is the minimum-viable indirection that lets
    # tests subprocess the script with ``--output`` + ``--validate``
    # against a tmp_path sandbox without polluting the production
    # templates dir.
    override = os.environ.get("TOYBOX_TEMPLATES_DIR")
    saved_templates_dir = _generator.TEMPLATES_DIR
    if override:
        # ``TEMPLATES_DIR`` is annotated ``Final`` on the generator
        # module; mypy correctly flags rebinding. This is a deliberate
        # test-only indirection seam — TOYBOX_TEMPLATES_DIR is unset in
        # production runs and the assignment never fires. The type:
        # ignore is scoped to this single Final-rebinding line.
        _generator.TEMPLATES_DIR = Path(override)  # type: ignore[misc]
    try:
        clear_template_cache()
        loaded_internal = _load_intent_templates("request_activity")
        loaded_micro_internal = [t for t in loaded_internal if t.id.startswith(_TEMPLATE_ID_PREFIX)]
        if len(loaded_micro_internal) != expected_count:
            raise SystemExit(
                f"--validate: expected {expected_count} element_microgame_* "
                f"templates to load, got {len(loaded_micro_internal)}. "
                f"The whole intent file may have been dropped on schema failure; "
                f"check {path} and re-run."
            )

        # Round-trip every microgame template through Pydantic + the
        # structural validator from the raw JSON, so the count check above
        # is paired with an explicit gate-by-gate verification. The
        # ``_load_intent_templates`` path runs these gates, but its skip-
        # on-failure semantics (warn + skip individual templates that fail
        # Pydantic / ValueError) mean a single bad row could pass through
        # silently dropped — re-asserting here makes the assertion airtight.
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
        raw_micro = [
            t
            for t in raw_payload["templates"]
            if isinstance(t, dict) and str(t.get("id", "")).startswith(_TEMPLATE_ID_PREFIX)
        ]
        if len(raw_micro) != expected_count:
            raise SystemExit(
                f"--validate: file contains {len(raw_micro)} element_microgame_* "
                f"entries on disk, expected {expected_count}"
            )
        for raw in raw_micro:
            template = Template.model_validate(raw)
            validate_template(template)

        _logger.info(
            "--validate: %d element_microgame_* templates loaded + validated cleanly",
            len(loaded_micro_internal),
        )
    finally:
        # Restore module-level templates dir regardless of validation
        # outcome so subsequent in-process callers see the default again.
        # Final-rebinding ignore matches the override site above.
        _generator.TEMPLATES_DIR = saved_templates_dir  # type: ignore[misc]
        clear_template_cache()


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate 118 'element_microgame' request_activity templates "
            "from data/elements/elements.json + data/elements/distractors.json "
            "and append them to request_activity.json (Phase N Step N4)."
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
            "element_microgame_* entries are stripped before appending); "
            "this flag merely tags the run in the summary log. Sibling-CLI "
            "parity with M4 + 9 other template generators."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load via the production template loader + "
            "structural validator and assert all 118 new templates pass."
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

    # ``load_elements`` warms the M1 cache; the distractor loader
    # itself cross-checks element_ids via get_element.
    elements = load_elements()
    distractors = load_distractors()

    # Resolve assertion (used in the no-flag path's logging only).
    if get_element("h-1") is None:
        raise SystemExit("element corpus did not load (h-1 missing)")

    payload = _load_existing(output)
    existing_templates: list[dict[str, Any]] = list(payload["templates"])
    pre_count = len(existing_templates)

    stripped = _strip_microgame_entries(existing_templates)
    stripped_count = pre_count - len(stripped)

    new_templates = _generate_all(elements, distractors)

    merged = stripped + new_templates
    payload["templates"] = merged

    post_count = len(merged)
    _logger.info(
        "summary: pre=%d, removed_existing_microgame=%d, generated=%d, post=%d, force=%s",
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
        _validate_post_write(output, expected_count=len(new_templates))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
