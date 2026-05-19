"""Top-level codegen entrypoint for backend → frontend type sync.

Phase A Step 1 spike outcome: **case 2** — ``pydantic2ts`` requires the
``json2ts`` Node CLI (``json-schema-to-typescript``) which is not present
on the developer machine and not declared as a project dependency. Until
that dependency story is settled, this script delegates to per-module
fallbacks that produce deterministic TS by walking the Pydantic /
StrEnum source directly.

Phase G G1: a second fallback writes
``frontend/src/shared/types.ts`` from the new
``toybox.activities.models.Step`` + ``Choice`` Pydantic models.
The fallback is deterministic — running it twice with no source
edits produces the same byte sequence — so the pre-commit hook
catches drift via ``git diff --exit-code``.

Per-toy role restrictions (post-K): the ``RoleName`` string-literal
union + ``ROLE_DISPLAY_NAMES`` constant emitted to ``types.ts`` are
derived from :class:`toybox.activities.roles.Role` and
:data:`toybox.activities.roles.ROLE_DISPLAY_NAMES` at generation time
— NOT hand-mirrored — so a future role taxonomy change auto-propagates
to the frontend. The single source of truth lives in ``roles.py`` per
code-quality.md §2.

When Pydantic2ts/json2ts is wired up, the fallback can be replaced
with a real codegen call without changing the pre-commit hook
contract.

    python tools/gen_types_ts.py
    git diff --exit-code frontend/src/shared/errors.ts frontend/src/shared/types.ts
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import get_args

# Run-time import of the role taxonomy so changes to roles.py
# automatically flow into the generated types.ts — there is no
# hand-mirrored list in this file (code-quality.md §2 "one source of
# truth"). The repo root is prepended to ``sys.path`` so the script
# works both from the repo root and from an editor invocation.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from toybox.activities.models import (  # noqa: E402
    Animation,
    RewardType,
    TemplateType,
)
from toybox.activities.roles import (  # noqa: E402
    ROLE_DISPLAY_NAMES,
    Role,
)

TYPES_TS_PATH = REPO_ROOT / "frontend" / "src" / "shared" / "types.ts"


def _emit_role_name_union() -> str:
    """Emit the ``RoleName`` string-literal union from the Role enum.

    Ordering: by :class:`Role` member ``value`` ASC, stable across
    Python releases so the codegen output is byte-deterministic.
    """
    values = sorted(role.value for role in Role)
    parts = " | ".join(f'"{value}"' for value in values)
    return f"export type RoleName = {parts};"


def _emit_role_display_names_const() -> str:
    """Emit the ``ROLE_DISPLAY_NAMES`` map keyed by ``RoleName``.

    Walks :data:`ROLE_DISPLAY_NAMES` in the same alphabetical order as
    the union above for deterministic diff output.
    """
    lines: list[str] = [
        "export const ROLE_DISPLAY_NAMES: Record<RoleName, string> = {",
    ]
    for value in sorted(role.value for role in Role):
        role = Role(value)
        display = ROLE_DISPLAY_NAMES[role]
        # ``json.dumps`` produces double-quoted JS-compatible string
        # literals and escapes any embedded special characters.
        lines.append(f"  {value}: {json.dumps(display)},")
    lines.append("};")
    return "\n".join(lines)


def _emit_animation_union() -> str:
    """Emit the ``Animation`` string-literal union from the Python enum.

    Phase L L1: order is preserved as member-definition order (NOT
    alphabetical) so the parent UI's reward-animation dropdown in
    ``RewardsList`` (L6) renders the six options in the spec order
    documented in ``documentation/phase-l-plan.md``.
    """
    parts = " | ".join(f'"{member.value}"' for member in Animation)
    return f"export type Animation = {parts};"


def _emit_reward_type_union() -> str:
    """Emit the ``RewardType`` string-literal union from the Literal alias.

    Phase L L1: derives the four wire strings from the source-of-truth
    ``typing.Literal`` alias in ``activities/models.py`` so the
    frontend never hand-mirrors the wire vocabulary.
    """
    parts = " | ".join(f'"{value}"' for value in get_args(RewardType))
    return f"export type RewardType = {parts};"


def _emit_template_type_union() -> str:
    """Emit the ``TemplateType`` string-literal union from the Literal alias.

    Phase N N2: derives the wire vocabulary for ``Template.template_type``
    from the source-of-truth :data:`TemplateType` alias in
    ``activities/models.py``. Phase O reads this typed value in the
    frontend to drive ``categorize()`` — a freeform string would have
    forced a hand-mirrored magic-string list per code-quality.md §2.
    """
    parts = " | ".join(f'"{value}"' for value in get_args(TemplateType))
    return f"export type TemplateType = {parts};"


def _build_types_ts_content() -> str:
    """Build the full ``types.ts`` payload as a deterministic string.

    The first two interfaces (``Choice`` + ``Step``) mirror the Phase G
    Pydantic models in :mod:`toybox.activities.models`. ``RoleName`` and
    ``ROLE_DISPLAY_NAMES`` are derived from :mod:`toybox.activities.roles`
    at generation time so the frontend never needs to hand-mirror the
    Phase K role taxonomy.
    """
    header = (
        "// AUTOGENERATED from Pydantic models + Role enum — "
        "do not edit by hand.\n"
        "// Regenerate with `python tools/gen_types_ts.py`.\n"
        "//\n"
        "// Phase G G1: until the pydantic2ts pipeline (gated on the json2ts\n"
        "// Node CLI) is wired up, the per-module fallback in\n"
        "// ``tools/gen_types_ts.py`` writes this file deterministically from\n"
        "// the Pydantic models in ``src/toybox/activities/models.py`` and\n"
        "// the Role taxonomy in ``src/toybox/activities/roles.py``. The\n"
        "// shapes below MUST stay in sync with those modules — the kiosk's\n"
        "// ChoiceButton (Phase G G4) imports ``Choice`` from here, and the\n"
        "// parent UI imports ``RoleName`` / ``ROLE_DISPLAY_NAMES`` for the\n"
        "// per-toy role restriction control.\n"
    )

    choice_step = (
        "\n/**\n"
        " * Phase G template-time choice — ONE branch in a step's\n"
        " * ``choices`` list. ``next`` is the successor step id authored in\n"
        " * the template JSON. The KIOSK does not consume this shape directly\n"
        " * — the API serializer transforms ``activity_steps.choices_json``\n"
        " * (a JSON array of pre-rendered label strings) into the runtime\n"
        " * ``{label, choice_index}`` shape kiosks render.\n"
        " */\n"
        "export interface Choice {\n"
        "  label: string;\n"
        "  next: string;\n"
        "}\n"
        "\n"
        "/**\n"
        " * Phase G template-time step definition. Optional fields ``id`` /\n"
        " * ``next`` / ``choices`` are additive — pre-Phase-G linear\n"
        " * templates leave all three null and rely on the implicit edge\n"
        " * rule (advance to next array position).\n"
        " *\n"
        " * The runtime activity-step shape (one row in ``activity_steps``,\n"
        " * what the kiosk receives over WS / REST) is intentionally NOT\n"
        " * this interface — the runtime form has ``seq``, ``body``,\n"
        " * ``current``, and a different ``choices`` shape carrying\n"
        " * ``{label, choice_index}``. See ``frontend/src/child/api.ts``\n"
        " * for the runtime ``ActivityStep``; this interface is for\n"
        " * tooling that authors / inspects template JSON.\n"
        " */\n"
        "export interface Step {\n"
        "  text: string;\n"
        "  sfx?: string | null;\n"
        "  expected_action?: string | null;\n"
        "  action_slot?: string | null;\n"
        "  id?: string | null;\n"
        "  next?: string | null;\n"
        "  choices?: Choice[] | null;\n"
        "  element_id?: string | null;\n"
        "}\n"
    )

    role_block = (
        "\n/**\n"
        " * Per-toy role restriction taxonomy — derived at codegen time\n"
        " * from ``src/toybox/activities/roles.py``. The single source of\n"
        " * truth is the Python ``Role`` enum; do not hand-edit either\n"
        " * the union below or ``ROLE_DISPLAY_NAMES``.\n"
        " */\n"
        f"{_emit_role_name_union()}\n"
        "\n"
        "/**\n"
        " * Title-cased display strings keyed by ``RoleName`` for the\n"
        " * parent UI's role-restriction control.\n"
        " */\n"
        f"{_emit_role_display_names_const()}\n"
    )

    template_type_block = (
        "\n/**\n"
        " * Phase N N2 template-type taxonomy — derived at codegen\n"
        " * time from the ``TemplateType`` Literal alias in\n"
        " * ``src/toybox/activities/models.py``. Templates that omit\n"
        " * the field round-trip as ``null``; templates that set it\n"
        " * activate the matching structural validator gate in\n"
        " * ``src/toybox/activities/_validator.py``. The frontend\n"
        " * (Phase O) reads this typed field rather than a freeform\n"
        " * string so ``categorize()`` cannot drift from the Python\n"
        " * source.\n"
        " */\n"
        f"{_emit_template_type_union()}\n"
    )

    reward_block = (
        "\n/**\n"
        " * Phase L L1 picture-reward animation taxonomy — derived at\n"
        " * codegen time from the ``Animation`` StrEnum in\n"
        " * ``src/toybox/activities/models.py``. Order is member-\n"
        " * definition order (NOT alphabetical) so the parent UI's\n"
        " * RewardsList animation dropdown renders the six options in\n"
        " * the spec order.\n"
        " */\n"
        f"{_emit_animation_union()}\n"
        "\n"
        "/**\n"
        " * Phase L L1 per-activity reward type — derived at codegen\n"
        " * time from the ``RewardType`` Literal alias in\n"
        " * ``src/toybox/activities/models.py``. The parent's approve\n"
        " * dropdown and the kiosk's reward-step renderer both branch\n"
        " * on these four wire strings.\n"
        " */\n"
        f"{_emit_reward_type_union()}\n"
    )

    activity_block = (
        "\n/**\n"
        " * Phase O Step O2 — runtime activity wire shape. Mirrors\n"
        " * :class:`toybox.api.activities.ActivityStepResponse` (one\n"
        " * row in ``activity_steps``, the shape the kiosk + parent UI\n"
        " * consume over REST / WS). Distinct from the template-time\n"
        " * ``Step`` interface above — that one models template JSON\n"
        " * authored by the generator; this one models the runtime row\n"
        " * the parent dashboard renders.\n"
        " *\n"
        " * ``element_id`` was added in Phase M Step M3; the field is\n"
        " * re-emitted here on the runtime shape so the parent UI's\n"
        " * categorize() helper can branch on it without an extra\n"
        " * template fetch.\n"
        " */\n"
        "export interface ActivityStep {\n"
        "  seq: number;\n"
        "  body: string;\n"
        "  sfx: string | null;\n"
        "  expected_action: string | null;\n"
        "  current: boolean;\n"
        "  element_id: string | null;\n"
        "}\n"
        "\n"
        "/**\n"
        " * Phase O Step O2 — runtime activity wire shape. Mirrors\n"
        " * :class:`toybox.api.activities.ActivityResponse`. The two\n"
        " * Phase O additions — ``template_id`` and\n"
        " * ``recommended_themes`` — are surfaced at the top of the\n"
        " * envelope so the parent UI's categorize() helper can bucket\n"
        " * activities into Adventures / Elements / Feelings & Friends\n"
        " * without a separate template fetch.\n"
        " *\n"
        " * The hand-rolled ``Activity`` interface in\n"
        " * ``frontend/src/parent/api.ts`` is the historical surface the\n"
        " * parent UI consumes; this codegen-emitted shape sits\n"
        " * alongside it as the typed contract categorize.ts imports.\n"
        " * Carries only the load-bearing fields categorize() reads\n"
        " * plus the original Activity identity fields — a fuller mirror\n"
        " * lands once the api.ts hand-roll is collapsed onto this one.\n"
        " */\n"
        "export interface Activity {\n"
        "  id: string;\n"
        "  state: string;\n"
        "  version: number;\n"
        "  title: string | null;\n"
        "  steps: ActivityStep[];\n"
        "  template_id: string | null;\n"
        "  recommended_themes: string[];\n"
        "}\n"
    )

    return header + choice_step + role_block + template_type_block + reward_block + activity_block


def run_error_codes_fallback() -> int:
    """Run the deterministic ErrorCode → TS generator."""
    script = REPO_ROOT / "tools" / "gen_error_codes_ts.py"
    result = subprocess.run([sys.executable, str(script)], cwd=str(REPO_ROOT))
    return result.returncode


def write_types_ts() -> int:
    """Write ``frontend/src/shared/types.ts`` deterministically.

    Returns 0 on success. Idempotent: re-running with no source
    changes leaves the file byte-identical.
    """
    TYPES_TS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TYPES_TS_PATH.write_text(_build_types_ts_content(), encoding="utf-8", newline="\n")
    return 0


def main() -> int:
    rc = run_error_codes_fallback()
    if rc != 0:
        return rc
    rc = write_types_ts()
    if rc != 0:
        return rc
    # When pydantic2ts/json2ts is wired up, additional codegen calls land here
    # for the Pydantic schema modules (see tools/README.md).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
