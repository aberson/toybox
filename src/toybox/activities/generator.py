"""Offline activity generator.

The generator is fully deterministic given ``(intent, slot, context,
hour, seed)``: same inputs produce the byte-for-byte identical
:class:`Activity` (including the ``id`` field). It does not call
Claude, does not touch the database, and does not depend on the wall
clock. It is the fallback used by listening modes 1, 3 (when Claude
is not capable), and 4-5 when the breaker is open.

Template selection algorithm
----------------------------

1. Load the template library for the given intent (one JSON file per
   intent under :mod:`toybox.activities.templates`). The library is
   schema-validated and cached after first load.
2. Filter templates whose ``buckets`` declaration is eligible at the
   given ``hour`` (see :mod:`toybox.activities.time_of_day`).
3. If no templates remain, fall back to the intent's ``"always"``
   templates regardless of bucket. If still empty, fall back to the
   ``boredom`` intent's ``"always"`` templates. If THAT is empty (it
   should never be — the shipped library has at least one ``always``
   template per intent), raise.
4. Sort the eligible list by ``template_id`` for determinism, then
   pick one with a seeded ``random.Random(seed)``.

Slot substitution
-----------------

Templates may include ``{slot}`` and ``{toy}`` placeholders.

* ``{slot}`` is replaced by the caller's ``slot`` argument when
  provided. When ``slot is None``, ``{slot}`` is replaced by the
  literal string ``"adventure"`` so step text remains coherent —
  templates that absolutely require a slot should include their
  ``{slot}`` placeholder anyway, since substitution defaults are
  Phase A scaffolding (Phase C step 18 wires real toys / rooms /
  banned themes).
* ``{toy}`` is replaced by the literal Phase A placeholder
  ``"Mr. Unicorn"`` per ``documentation/plan.md`` issue #7 notes.

The set of substituted slot values is collected in sorted order into
``Activity.metadata["slot_values"]`` as a ``tuple[str, ...]`` — this
is the load-bearing input to Phase D step 19's ``signature``
computation, and both the sort and the tuple immutability make the
signature stable against slot ordering and post-construction
mutation.

Determinism of ``Activity.id``
------------------------------

``uuid.uuid4()`` is non-deterministic, so the generator derives a UUID
from the SHA-256 of a canonical input string and forces the version
nibble to ``4`` plus the variant bits to RFC-4122. Same inputs +
seed + selected template_id → same UUID.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import ValidationError as PydanticValidationError

from ..image_gen.models import ACTION_SLOTS
from ._validator import TemplateGraphError, validate_template, validate_template_graph
from .content_resolver import (
    SAFE_DEFAULT_TEMPLATE,
    ResolvedChildren,
    ResolvedRoom,
    ResolvedToy,
    SafeDefaultTemplate,
    apply_banned_themes_filter,
)
from .feedback import Candidate, compute_signature, consult_and_select
from .models import Activity, ActivityStep, Step, Template
from .roles import Role
from .slots import SIGNATURE_CONTRIBUTING_SLOTS, SlotRegistry
from .themes import Theme
from .time_of_day import ALWAYS_BUCKET, hour_bucket, is_eligible

_logger = logging.getLogger(__name__)

_PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent
TEMPLATES_DIR: Final[Path] = _PACKAGE_DIR / "templates"
SCHEMA_FILENAME: Final[str] = "_schema.json"

# Phase A placeholder per plan.md issue #7: "toys = ['Mr. Unicorn']".
DEFAULT_TOY_NAME: Final[str] = "Mr. Unicorn"

# Used when caller passes slot=None and a template still has {slot}.
DEFAULT_SLOT_FILLER: Final[str] = "adventure"

SUPPORTED_INTENTS: Final[tuple[str, ...]] = (
    "request_play",
    "request_story",
    "request_activity",
    "boredom",
)

# Final fallback intent if everything else fails to match.
FALLBACK_INTENT: Final[str] = "boredom"


@dataclass(frozen=True, slots=True)
class _StepTemplate:
    text: str
    sfx: str | None
    expected_action: str | None
    # Phase F Step F6: per-step action vocabulary key (one of
    # ``ACTION_SLOTS`` or ``None``). The template loader rejects
    # out-of-vocab values at boot so a typo'd template fails LOUDLY
    # rather than silently degrading the kiosk render path.
    action_slot: str | None
    # Phase G additions (all optional, backward-compatible). Carried
    # through to G2/G3 so the runtime can: persist ``id`` per row,
    # resolve ``next`` / ``choices`` server-side at advance time, and
    # render choice labels with the activity's slot fills.
    id: str | None = None
    next: str | None = None
    choices: tuple[tuple[str, str], ...] | None = None
    # Phase K K14: per-step ``kind`` discriminator. ``kind`` defaults
    # to ``"text"`` so the 200 existing branching templates parse
    # unchanged. ``auto`` defaults to ``None``; Phase L Step L5 deleted
    # the advance-time embedded picker that consumed ``auto=True`` and
    # the Pydantic validator (``Step._check_song_joke_shape``) now
    # rejects ``auto=True`` on song/joke steps. ``auto`` is retained on
    # the dataclass for future re-introduction of a corpus picker but
    # always parses as ``None`` from production templates today.
    kind: str = "text"
    auto: bool | None = None
    # Phase M Step M3: optional reference to an element corpus entry
    # carried through verbatim from the template JSON. Cross-corpus
    # resolution is gated by :func:`validate_template`. ``None`` for
    # the overwhelming majority of templates that don't reference an
    # element (pre-M3 templates parse unchanged).
    element_id: str | None = None


@dataclass(frozen=True, slots=True)
class _Template:
    id: str
    title: str
    buckets: frozenset[str]
    steps: tuple[_StepTemplate, ...]
    # Phase K K5: surface the K3 role declarations on the cached
    # dataclass so the propose path (api/activities.py) can call
    # :func:`toybox.activities.content_resolver.resolve_role_slots`
    # after :func:`generate` picks a template. Default to empty tuples
    # so the 200 existing branching templates (which omit the fields)
    # parse unchanged. Source of truth for the role taxonomy is
    # :class:`toybox.activities.roles.Role`; we store the enum members
    # directly (not strings) so the picker doesn't have to re-coerce.
    required_roles: tuple[Role, ...] = ()
    optional_roles: tuple[Role, ...] = ()
    # Phase K K14: surface the K3 theme tags on the cached dataclass.
    # Phase L Step L5 removed the K14 propose-time endings appender and
    # the K14 advance-time embedded picker; the field stays so a future
    # surface that filters by template themes can read it without
    # re-parsing JSON. ``ending_step`` was removed entirely in L5.
    recommended_themes: tuple[Theme, ...] = ()


# Cache: (templates_dir, intent) -> list of loaded templates.
# Populated lazily on first call and re-used for the lifetime of the
# process. Keying on the directory path means a test that monkeypatches
# ``TEMPLATES_DIR`` to a fresh fixture path automatically gets fresh
# loads — previously the cache leaked stale templates across runs if
# the caller forgot to call ``clear_template_cache``.
_TEMPLATE_CACHE: dict[tuple[Path, str], list[_Template]] = {}
_SCHEMA_VALIDATOR_CACHE: dict[Path, Draft202012Validator] = {}


def _load_schema() -> Draft202012Validator:
    cached = _SCHEMA_VALIDATOR_CACHE.get(TEMPLATES_DIR)
    if cached is not None:
        return cached
    schema_path = TEMPLATES_DIR / SCHEMA_FILENAME
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    _SCHEMA_VALIDATOR_CACHE[TEMPLATES_DIR] = validator
    return validator


def _parse_template(raw: dict[str, Any], *, source: str = "<inline>") -> _Template:
    """Convert a JSON-shaped template dict into the internal dataclass.

    Phase F Step F6: ``action_slot`` (when present) is cross-checked
    against :data:`ACTION_SLOTS`. Bad values raise :class:`ValueError`
    so a hand-edited template with a typo fails LOUDLY at load time
    rather than silently degrading on the kiosk path. ``source`` is
    a label (filename or in-memory marker) used in the error message
    so operators can find the offending file fast.

    Phase G: each step is also Pydantic-validated as a :class:`Step`
    (catches malformed ``id`` patterns, ``next``/``choices`` mutual
    exclusion, choice-count range), and the resulting list is then
    passed to :func:`validate_template_graph` which enforces the
    template-wide graph invariants (unique ids, resolved targets,
    reachability, no cycles, terminal-reachable). Either layer
    raising bubbles up to the caller so the template is dropped
    with a logged WARNING — except graph violations, which the
    Phase G plan specifies as a hard load-time failure that surfaces
    via the :class:`TemplateGraphError` class to the caller.
    """
    template_id = str(raw["id"])
    steps_raw = raw["steps"]
    parsed_steps: list[_StepTemplate] = []
    # Phase K K3: parse the whole template through Pydantic so the
    # K3 cross-field shape (role / theme / ending_step) is gated
    # alongside per-step shape. The legacy per-step ``Step.model_validate``
    # loop below stays so the ``action_slot`` ValueError branch (which
    # isn't a Pydantic gate) still fires with its tailored error
    # message before the more general Template validation. Both layers
    # raise pydantic.ValidationError / ValueError that the loader
    # catches as a logged warn + skip.
    template_model = Template.model_validate(raw)
    for s in steps_raw:
        slot = s.get("action_slot")
        if slot is not None and slot not in ACTION_SLOTS:
            raise ValueError(
                f"template {template_id!r} in {source}: step action_slot={slot!r} "
                f"is not in ACTION_SLOTS={ACTION_SLOTS!r}"
            )
        # Pydantic validation per step: ``Template.model_validate``
        # above already ran this for every step, but we keep the
        # per-step loop so the ``_StepTemplate`` dataclass can be
        # built incrementally with the source-level ``s`` dict in
        # scope (action_slot ValueError above relies on that).
        step_model = Step.model_validate(s)
        choices_tuple: tuple[tuple[str, str], ...] | None = None
        if step_model.choices is not None:
            choices_tuple = tuple((c.label, c.next) for c in step_model.choices)
        parsed_steps.append(
            _StepTemplate(
                text=str(s["text"]),
                sfx=s.get("sfx"),
                expected_action=s.get("expected_action"),
                action_slot=slot,
                id=step_model.id,
                next=step_model.next,
                choices=choices_tuple,
                # Phase K K14: thread kind + auto through to the cached
                # dataclass. Phase L Step L5 removed the advance-time
                # embedded picker that consumed ``auto=True``; the field
                # is retained on the dataclass for future re-introduction
                # but always parses as ``None`` from production templates
                # today (Pydantic rejects ``auto=True`` on song/joke).
                kind=step_model.kind,
                auto=step_model.auto,
                # Phase M Step M3: pass the optional element_id through
                # so the runtime ActivityStep can echo it on the WS
                # envelope. Pydantic + the M3 cross-corpus validator
                # below have already gated the regex shape and resolved
                # the id to a real entry.
                element_id=step_model.element_id,
            )
        )
    # Phase G graph validation. Raises TemplateGraphError on any
    # violation. We let it propagate so a graph-invalid template
    # is a hard load-time error per the Phase G plan; the caller
    # in ``_load_intent_templates`` is responsible for whether to
    # surface that as a fatal startup error or a logged skip.
    validate_template_graph(template_id, template_model.steps)
    # Phase K K3 template-shape validation (placeholder gate, distinct-
    # toy ceiling, ending_step / song-joke shape defense-in-depth).
    # Same hard-fail semantics as ``validate_template_graph``.
    validate_template(template_model)
    return _Template(
        id=template_id,
        title=str(raw["title"]),
        buckets=frozenset(raw.get("buckets", []) or []),
        steps=tuple(parsed_steps),
        # Phase K K5: copy the K3 role declarations off the Pydantic
        # template model (already validated above). ``Template.model_validate``
        # coerces JSON strings to :class:`Role` members so the dataclass
        # stores enum values directly.
        required_roles=tuple(template_model.required_roles),
        optional_roles=tuple(template_model.optional_roles),
        # Phase K K14: surface theme tags on the cached dataclass.
        # Phase L Step L5 removed the embedded/ending consumers; the
        # field remains for the bias-by-theme picker (Phase G).
        recommended_themes=tuple(template_model.recommended_themes),
    )


def _discover_intent_template_files(intent: str) -> list[Path]:
    """Walk ``TEMPLATES_DIR`` recursively for files named ``{intent}.json``.

    Phase G: branching templates live under
    ``src/toybox/activities/templates/branching/<intent>.json``
    alongside the existing top-level per-intent files. Multiple files
    per intent are loaded and merged so authors can group templates
    by shape (linear vs. branching) without changing per-file shape.

    Files are returned in deterministic order (top-level first, then
    subdirectories sorted by relative path) so downstream sorting by
    template id stays stable across runs.
    """
    out: list[Path] = []
    top_level = TEMPLATES_DIR / f"{intent}.json"
    if top_level.is_file():
        out.append(top_level)
    # Recurse one level deep into immediate subdirectories. Use rglob so
    # nested groupings (e.g. ``templates/branching/<intent>.json``)
    # match without hard-coding the subdirectory name. Sorted by
    # relative path so file-order is deterministic.
    nested = sorted(
        (p for p in TEMPLATES_DIR.rglob(f"{intent}.json") if p != top_level and p.is_file()),
        key=lambda p: p.relative_to(TEMPLATES_DIR).as_posix(),
    )
    out.extend(nested)
    return out


def _load_intent_templates(intent: str) -> list[_Template]:
    """Load and validate templates for ``intent``. Caches per-(dir, intent).

    Phase G: discovers ``{intent}.json`` recursively under
    ``TEMPLATES_DIR`` (top-level + any subdirectory like ``branching/``)
    and merges all matching files into a single intent pool. Per-file
    schema / JSON parse errors are logged + skipped (existing behavior);
    graph-validation errors raise unchanged.
    """
    cache_key = (TEMPLATES_DIR, intent)
    cached = _TEMPLATE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if intent not in SUPPORTED_INTENTS:
        # Caller passed an unknown intent string — treat as empty so the
        # caller's fallback path takes over.
        _TEMPLATE_CACHE[cache_key] = []
        return []

    paths = _discover_intent_template_files(intent)
    if not paths:
        _logger.warning(
            "no intent template files found for intent=%r under %s",
            intent,
            TEMPLATES_DIR,
        )
        _TEMPLATE_CACHE[cache_key] = []
        return []

    validator = _load_schema()
    templates: list[_Template] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _logger.warning("intent template JSON malformed, skipping: %s (%s)", path, exc)
            continue

        try:
            validator.validate(payload)
        except ValidationError as exc:
            _logger.warning(
                "intent template file failed schema validation, skipping: %s (%s)",
                path,
                exc.message,
            )
            continue

        if payload["intent"] != intent:
            _logger.warning(
                "intent template file %s has mismatched intent %r (expected %r), skipping",
                path,
                payload["intent"],
                intent,
            )
            continue

        # We iterate the per-template list explicitly (rather than as a
        # genexpr fed to extend) so a single bad template can be
        # warned-and-skipped without losing its valid siblings in the
        # same intent file. Three failure modes are distinguished:
        #
        # 1. ``TemplateGraphError`` — Phase G plan: hard load-time
        #    failure. Surface unchanged so startup crashes with a
        #    clear template_id + violation. (Operator must fix the file.)
        # 2. ``pydantic.ValidationError`` — per-step shape error
        #    (bad ``id`` regex, bad ``next``/``choices`` XOR via the
        #    Pydantic model validator, choice-count out of [2, 4],
        #    etc). The JSON-schema layer above already catches most
        #    of these; this catches what slips through (e.g. fields
        #    Pydantic enforces but jsonschema can't easily express).
        #    Treat as the malformed-file case: warn + skip the file.
        # 3. ``ValueError`` — raised by ``_parse_template`` when
        #    ``action_slot`` is set but not in ``ACTION_SLOTS``.
        #    Same handling as #2 — warn + skip.
        for t in payload["templates"]:
            try:
                templates.append(_parse_template(t, source=path.name))
            except TemplateGraphError:
                raise
            except (PydanticValidationError, ValueError) as exc:
                tid = t.get("id", "<unknown>") if isinstance(t, dict) else "<unknown>"
                _logger.warning(
                    "intent template %r in %s failed step-shape validation, skipping: %s",
                    tid,
                    path,
                    exc,
                )
                continue

    # Sort by id so that downstream selection is deterministic
    # regardless of file order.
    templates.sort(key=lambda t: t.id)
    _TEMPLATE_CACHE[cache_key] = templates
    return templates


def find_template_by_id(template_id: str) -> _Template | None:
    """Phase G G3: look up a loaded template by its id.

    Searches every supported intent's template pool and returns the
    first match. Returns ``None`` if no template with that id exists
    in any pool — caller decides whether that's a 500 (corrupt
    activity row) or a 404. Cached implicitly via
    :func:`_load_intent_templates`'s per-intent cache.

    The lazy advance handler in :mod:`toybox.api.activities` calls
    this with the ``template_id`` recovered from the activity's
    persisted summary envelope so it can resolve ``next`` /
    ``choices[i].next`` targets at advance time.
    """
    for intent in SUPPORTED_INTENTS:
        for template in _load_intent_templates(intent):
            if template.id == template_id:
                return template
    return None


def _template_has_branching_step(template: _Template) -> bool:
    """True iff ``template`` contains any step with ``choices``.

    Phase W Step W2: a "branching" template is one where at least one
    step exposes ``choices`` (the runtime renders choice buttons and the
    kid steers the path). A "linear" template has no such step — every
    step is a straight body that advances via ``next`` or sequence.
    """
    return any(step.choices is not None for step in template.steps)


def _filter_choice_free(templates: list[_Template]) -> list[_Template]:
    """Phase W Step W2: drop every template that has a branching step.

    Used when the household ``game_linearity`` dial is ``"linear"`` —
    the propose path threads ``linear_only=True`` and the picker must
    never surface a choice-bearing activity.
    """
    return [t for t in templates if not _template_has_branching_step(t)]


def _filter_eligible(templates: list[_Template], hour: int) -> list[_Template]:
    return [t for t in templates if is_eligible(set(t.buckets), hour)]


def _filter_always(templates: list[_Template]) -> list[_Template]:
    return [t for t in templates if ALWAYS_BUCKET in t.buckets]


def _preview_slot_values(
    template: _Template,
    *,
    slot: str | None,
    toy: str,
) -> tuple[str, ...]:
    """Compute the slot values a template would substitute, without
    actually substituting.

    Returns the same tuple shape as ``Activity.metadata["slot_values"]``
    so the signature is stable: includes ``slot`` if any of the
    template's title or step texts contains ``{slot}`` and ``slot`` is
    non-empty, AND includes ``toy`` if the template uses ``{toy}`` and
    ``toy`` is non-default (i.e. the resolver provided a real catalog
    name, not the legacy :data:`DEFAULT_TOY_NAME` placeholder). This
    MUST track ``_substitute`` exactly — if substitution semantics
    drift, the generator's emitted signature will diverge from what
    consultation predicted, and feedback rows will silently stop
    matching. The covering test
    ``test_preview_matches_post_substitution`` pins the equivalence.
    """
    haystacks: list[str] = [template.title]
    haystacks.extend(s.text for s in template.steps)
    out: list[str] = []
    if slot is not None and slot.strip() and any("{slot}" in h for h in haystacks):
        out.append(slot)
    if toy != DEFAULT_TOY_NAME and any("{toy}" in h for h in haystacks):
        out.append(toy)
    return tuple(out)


def _build_candidates(
    templates: list[_Template],
    *,
    slot: str | None,
    toy: str,
) -> list[Candidate]:
    """Build :class:`Candidate` rows for feedback consultation."""
    out: list[Candidate] = []
    for t in templates:
        sv = _preview_slot_values(t, slot=slot, toy=toy)
        out.append(
            Candidate(
                template_id=t.id,
                signature=compute_signature(t.id, sv),
                slot_values=sv,
            )
        )
    return out


def _pick_with_consultation(
    templates: list[_Template],
    *,
    slot: str | None,
    toy: str,
    rng: random.Random,
    conn: sqlite3.Connection | None,
) -> _Template:
    """Apply feedback consultation if ``conn`` is set, else uniform pick.

    Centralising the conn-vs-rng branch here keeps ``_select_template``
    readable and makes the fallback paths share the same behaviour.
    """
    templates_by_id = {t.id: t for t in templates}
    candidates = _build_candidates(templates, slot=slot, toy=toy)
    chosen = consult_and_select(candidates, conn, rng)
    return templates_by_id[chosen.template_id]


def _safe_default_to_template() -> _Template:
    """Convert :data:`SAFE_DEFAULT_TEMPLATE` to the generator's internal shape.

    The content_resolver module ships a tag-free safe-default. The
    offline picker works with :class:`_Template` instances, so we
    materialise the dataclass once (pure function, no I/O).
    """
    steps_tuple: tuple[_StepTemplate, ...] = tuple(
        _StepTemplate(
            text=str(step["text"]),
            sfx=step.get("sfx"),
            expected_action=step.get("expected_action"),
            # The safe-default template is hand-curated; if a future
            # entry adds an action_slot we honor it. Otherwise NULL,
            # which means kiosk renders no sprite (current behavior).
            action_slot=step.get("action_slot"),
        )
        for step in SAFE_DEFAULT_TEMPLATE.steps
    )
    return _Template(
        id=SAFE_DEFAULT_TEMPLATE.id,
        title=SAFE_DEFAULT_TEMPLATE.title,
        buckets=SAFE_DEFAULT_TEMPLATE.buckets,
        steps=steps_tuple,
    )


def _apply_category_filter(
    templates: list[_Template],
    category: str | None,
) -> list[_Template]:
    """Filter templates to the parent's selected Play sub-tab category.

    Categories mirror the frontend ``categorize()`` helper's precedence
    rules (Elements > Feelings & Friends > Adventures) so a "Trigger now"
    invoked from a category sub-tab generates an activity that lands in
    that same sub-tab post-categorization.

    * ``None`` → no filter (current behavior; "All" sub-tab semantics).
    * ``"elements"`` → templates where ANY step has ``element_id`` set.
    * ``"feelings-friends"`` → templates whose ``recommended_themes``
      contains the ``feelings`` Theme.
    * ``"adventures"`` → templates that are NEITHER (no step.element_id
      AND no ``feelings`` in recommended_themes).
    * Unknown category strings → no filter (degrade gracefully — a
      typo'd category shouldn't starve the picker).

    Soft-fallback semantics match :func:`_apply_preferred_themes`: when
    NO template matches the category, return ``templates`` unchanged
    rather than starve the picker. This preserves the existing trigger
    behavior when a category has no eligible offline templates.
    """
    if category is None:
        return templates
    if category == "elements":
        matchers = [
            t for t in templates if any(step.element_id is not None for step in t.steps)
        ]
    elif category == "feelings-friends":
        matchers = [
            t for t in templates if any(str(theme) == "feelings" for theme in t.recommended_themes)
        ]
    elif category == "adventures":
        matchers = [
            t
            for t in templates
            if not any(step.element_id is not None for step in t.steps)
            and not any(str(theme) == "feelings" for theme in t.recommended_themes)
        ]
    else:
        return templates
    return matchers if matchers else templates


def _apply_preferred_themes(
    templates: list[_Template],
    preferred_themes: Sequence[str],
) -> list[_Template]:
    """Bias toward templates whose ``recommended_themes`` overlap ``preferred_themes``.

    When ``preferred_themes`` is empty, return ``templates`` unchanged
    (no bias — current behavior). When non-empty:

    * If ANY template overlaps the preference set, return only the
      overlapping subset. The downstream picker then chooses from a
      tighter pool, raising the chance the kid sees a topical activity.
    * If NO template overlaps, return ``templates`` unchanged. Preference
      is a hint, not a hard filter — we never starve the picker just
      because none of the eligible templates happen to be tagged with
      what the kid was just talking about.

    Theme equality is on the string value so callers can pass either
    :class:`Theme` enum members or plain ``str`` (matches the existing
    ``banned_themes`` convention).
    """
    if not preferred_themes:
        return templates
    pref_set = {str(t) for t in preferred_themes}
    matchers = [
        t for t in templates if any(str(theme) in pref_set for theme in t.recommended_themes)
    ]
    return matchers if matchers else templates


def _apply_banned_themes(
    templates: list[_Template],
    banned_themes: Sequence[str],
) -> list[_Template]:
    """Filter ``templates`` through the content_resolver banned-themes gate.

    When ALL templates filter out, the gate substitutes the safe-default
    template. The substitution comes back from
    :func:`apply_banned_themes_filter` as a single-element list
    containing :class:`SafeDefaultTemplate`; we convert it to the
    internal :class:`_Template` so the rest of the picker doesn't need
    to special-case the type.
    """
    if not banned_themes:
        return templates
    filtered = apply_banned_themes_filter(templates, banned_themes)
    converted: list[_Template] = []
    for tpl in filtered:
        if isinstance(tpl, SafeDefaultTemplate):
            converted.append(_safe_default_to_template())
        elif isinstance(tpl, _Template):
            converted.append(tpl)
        # Other types: ignore — defensive against future safe-default
        # impls that don't match our dataclass.
    return converted


def _select_template(
    intent: str,
    hour: int,
    rng: random.Random,
    *,
    slot: str | None,
    toy: str,
    conn: sqlite3.Connection | None,
    banned_themes: Sequence[str] = (),
    preferred_themes: Sequence[str] = (),
    category: str | None = None,
    linear_only: bool = False,
) -> tuple[_Template, str]:
    """Pick one template. Returns ``(template, source_intent)``.

    ``source_intent`` is the intent whose template pool was actually
    used — usually equal to ``intent``, but distinct after a fallback.

    When ``linear_only`` is True (household ``game_linearity == "linear"``)
    every candidate pool — the primary intent pool AND each fallback
    pool (intent ``always``, boredom ``always``) — is pre-filtered to
    drop templates that contain a branching step (a step with
    ``choices``). The choice-free filter is applied to the loaded pools
    up-front so the entire existing fallback chain stays intact, just
    operating over choice-free templates. When ``linear_only`` is False
    (default) the pools are untouched and behavior is byte-identical to
    pre-W2.

    When ``conn`` is provided, the picker consults the ``feedback``
    table per :mod:`toybox.activities.feedback` — ``didnt_work``
    vetoes a candidate (re-pick), ``loved_it`` boosts it,
    ``dismissed_pre_approval`` softly penalises it. Each fallback
    pool also gets the consultation, so a parent who has
    ``didnt_work``-flagged the only eligible primary template still
    benefits from anti-signal boosting in the always-fallback pool.

    ``toy`` is the toy name that will be substituted into ``{toy}``
    placeholders. It's threaded through to candidate-signature
    computation so the consultation veto matches the eventual
    activity signature byte-for-byte.
    """
    primary = _load_intent_templates(intent)
    if linear_only:
        primary = _filter_choice_free(primary)
    eligible = _apply_preferred_themes(
        _apply_category_filter(
            _apply_banned_themes(_filter_eligible(primary, hour), banned_themes),
            category,
        ),
        preferred_themes,
    )
    if eligible:
        eligible.sort(key=lambda t: t.id)
        return (
            _pick_with_consultation(eligible, slot=slot, toy=toy, rng=rng, conn=conn),
            intent,
        )

    # Fallback 1: any "always" template within the requested intent.
    always_primary = _apply_preferred_themes(
        _apply_category_filter(
            _apply_banned_themes(_filter_always(primary), banned_themes),
            category,
        ),
        preferred_themes,
    )
    if always_primary:
        always_primary.sort(key=lambda t: t.id)
        _logger.info(
            "no eligible templates for intent=%s hour=%d; falling back to intent's always pool",
            intent,
            hour,
        )
        return (
            _pick_with_consultation(always_primary, slot=slot, toy=toy, rng=rng, conn=conn),
            intent,
        )

    # Fallback 2: boredom intent's always pool (final safety net).
    if intent != FALLBACK_INTENT:
        boredom = _load_intent_templates(FALLBACK_INTENT)
        if linear_only:
            boredom = _filter_choice_free(boredom)
        always_boredom = _apply_preferred_themes(
            _apply_category_filter(
                _apply_banned_themes(_filter_always(boredom), banned_themes),
                category,
            ),
            preferred_themes,
        )
        if always_boredom:
            always_boredom.sort(key=lambda t: t.id)
            _logger.info(
                "no templates for intent=%s; falling back to boredom always pool",
                intent,
            )
            return (
                _pick_with_consultation(always_boredom, slot=slot, toy=toy, rng=rng, conn=conn),
                FALLBACK_INTENT,
            )

    # Final escape hatch: every pool was either empty or wiped by the
    # banned-themes filter. The content_resolver's safe-default IS
    # tag-free, so this path is reached only when there are also zero
    # shipped templates — a packaging error. We still surface a usable
    # activity rather than crashing on a child waiting for one.
    if banned_themes:
        _logger.warning(
            "all template pools wiped by banned_themes filter for intent=%s; "
            "returning safe-default",
            intent,
        )
        return _safe_default_to_template(), FALLBACK_INTENT

    raise RuntimeError(
        f"no offline templates available for intent={intent!r} hour={hour} "
        f"and no fallback templates either"
    )


# Match ``{name}`` placeholders. Slot names are lower-snake-case;
# anything outside that alphabet (numbers, hyphens, etc.) is left
# unmatched so JSON-y braces in step text wouldn't accidentally trip
# the substitutor.
_SLOT_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{([a-z_][a-z_]*)\}")


def _resolve_template_slots(
    template: _Template,
    *,
    slot: str | None,
    toy: str,
    registry: SlotRegistry,
    rng: random.Random,
) -> dict[str, str]:
    """Resolve every placeholder in a template ONCE, in deterministic order.

    Walks ``template.title`` and every step's text, collects unique
    placeholder names in order of first occurrence, and resolves each
    one. Resolving once per template (vs once per step) is what makes
    ``{action_verb}`` consistent across the title and all steps —
    otherwise step 1 would say "stomp" and step 3 would say "spin"
    and the activity would feel incoherent.

    The first-occurrence ordering is what determines RNG consumption
    order. It's stable per template-id, so the same seed always
    produces the same fills.

    ``{toy}`` and ``{slot}`` are resolved without consuming RNG
    (they're already-decided inputs); registry-backed slots consume
    RNG via ``registry.fill``.
    """
    haystack_parts: list[str] = [template.title]
    for s in template.steps:
        haystack_parts.append(s.text)
        if s.choices is not None:
            for label, _next_id in s.choices:
                haystack_parts.append(label)
    haystack = " ".join(haystack_parts)
    seen: set[str] = set()
    order: list[str] = []
    for match in _SLOT_PATTERN.finditer(haystack):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            order.append(name)

    resolved: dict[str, str] = {}
    for name in order:
        if name == "slot":
            if slot is not None and slot.strip():
                resolved[name] = slot
            else:
                resolved[name] = DEFAULT_SLOT_FILLER
        elif name == "toy":
            resolved[name] = toy
        else:
            resolved[name] = registry.fill(name, rng)
    return resolved


def _substitute(
    text: str,
    slot_values: dict[str, str],
) -> tuple[str, list[str]]:
    """Apply pre-resolved slot values to ``text``.

    Returns ``(substituted_text, signature_slot_values)``. The second
    element is the subset of slot values that should contribute to
    the activity's feedback signature — see
    :data:`toybox.activities.slots.SIGNATURE_CONTRIBUTING_SLOTS` for
    the rationale (catalog-backed and caller-supplied slots are
    semantic; word-list fills are surface variety and share signature
    so feedback aggregates).

    Default toy / slot values are excluded from the signature
    contribution so a Phase A empty-catalog test doesn't pollute the
    signature space with placeholder strings.
    """
    used: list[str] = []
    out = text
    for name, value in slot_values.items():
        placeholder = f"{{{name}}}"
        if placeholder not in out:
            continue
        out = out.replace(placeholder, value)
        if name not in SIGNATURE_CONTRIBUTING_SLOTS:
            continue
        if name == "toy" and value == DEFAULT_TOY_NAME:
            continue
        if name == "slot" and value == DEFAULT_SLOT_FILLER:
            continue
        used.append(value)
    return out, used


def render_with_slot_fills(text: str, slot_fills: dict[str, str]) -> str:
    """Phase G G3: render ``{slot}`` placeholders in ``text``.

    Public, signature-only wrapper over :func:`_substitute` so the lazy
    advance handler in :mod:`toybox.api.activities` can render later
    step bodies + choice labels with the SAME slot fills as step 1
    (persisted in ``activities.slot_fills_json``). Returns just the
    substituted text — the signature-contribution side-channel is
    irrelevant outside the generator's selection path.

    Behavior matches :func:`_substitute` exactly: replaces every
    ``{name}`` for which ``slot_fills`` has a key. Missing fills are
    left as literal placeholders rather than raising — keeps a fixture
    activity (created without going through the generator) advance-
    able even if its persisted slot map is empty.
    """
    out, _used = _substitute(text, slot_fills)
    return out


def _derive_uuid(
    *,
    intent: str,
    slot: str | None,
    context: dict[str, Any] | None,
    hour: int,
    seed: int,
    template_id: str,
) -> str:
    """Deterministically derive a UUID4-shaped string from the inputs.

    Uses SHA-256 of a canonical JSON encoding (sorted keys), then
    forces the standard UUID-v4 variant + version bits. ``uuid.UUID``
    constructed from 16 bytes after these adjustments produces a
    well-formed v4 UUID string.

    ``context`` MUST be JSON-serialisable (its values must be only
    ``None``, ``bool``, ``int``, ``float``, ``str``, ``list``, or
    ``dict`` — i.e. things ``json.dumps`` accepts in default mode).
    Non-serialisable values raise :class:`TypeError` with the offending
    key in the message.
    """
    payload = {
        "intent": intent,
        "slot": slot,
        "context": context if context is not None else {},
        "hour": hour,
        "seed": seed,
        "template_id": template_id,
    }
    try:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except TypeError as exc:
        # json.dumps raises plain TypeError without naming the offending
        # key; walk context to find the first non-serialisable entry so
        # the caller's error message is actionable.
        offending_key: str | None = None
        if context is not None:
            for k, v in context.items():
                try:
                    json.dumps(v)
                except TypeError:
                    offending_key = k
                    break
        raise TypeError(
            f"generate() context must be JSON-serialisable; offending key={offending_key!r} ({exc})"
        ) from exc
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    raw = bytearray(digest[:16])
    # Set version to 4 (random) — high nibble of byte 6.
    raw[6] = (raw[6] & 0x0F) | 0x40
    # Set variant to RFC 4122 — top two bits of byte 8 are 10.
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _pick_toy_entry(
    available_toys: Sequence[ResolvedToy],
    rng: random.Random,
) -> ResolvedToy | None:
    """Deterministically pick a :class:`ResolvedToy` from ``available_toys``.

    Returns ``None`` when the input is empty so the caller can degrade
    to :data:`DEFAULT_TOY_NAME` and an empty ``toy_ids`` tuple. Sorts
    entries by ``display_name`` to match the legacy name-only picker's
    rng draw byte-for-byte (same N items, same single ``rng.choice``).
    """
    if not available_toys:
        return None
    entries_sorted = sorted(available_toys, key=lambda entry: entry.display_name)
    return rng.choice(entries_sorted)


def _pick_toy_name(
    available_toys: Sequence[ResolvedToy],
    rng: random.Random,
) -> str:
    """Deterministically pick a toy display name from ``available_toys``.

    Falls back to :data:`DEFAULT_TOY_NAME` when the input is empty so
    the Phase A placeholder behaviour holds for empty-catalog tests.
    Thin shim over :func:`_pick_toy_entry` so name-only callers stay
    valid while the generator captures the picked toy's id alongside.
    """
    picked = _pick_toy_entry(available_toys, rng)
    if picked is None:
        return DEFAULT_TOY_NAME
    return picked.display_name


def generate(
    intent: str,
    slot: str | None,
    context: dict[str, Any] | None,
    hour: int,
    seed: int,
    *,
    persona_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    available_toys: Sequence[ResolvedToy] = (),
    available_rooms: Sequence[ResolvedRoom] = (),
    resolved_children: ResolvedChildren | None = None,
    preferred_themes: Sequence[str] = (),
    category: str | None = None,
    pinned_template_id: str | None = None,
    linear_only: bool = False,
) -> Activity:
    """Generate a deterministic :class:`Activity`.

    Phase G: ``Activity.steps`` length matches the picked template's
    step count (3-20 nodes per the relaxed Pydantic constraint),
    NOT a fixed 5. The runtime row count in ``activity_steps``
    decouples from the template step count under G2's lazy
    insertion path (only ``steps[0]`` is INSERTed at activity
    creation; subsequent rows land via G3's advance handler).

    Args:
        intent: One of :data:`SUPPORTED_INTENTS`. Unknown intents are
            tolerated and fall through to the boredom always pool.
        slot: Optional slot value (e.g. ``"unicorns"``). When ``None``
            the placeholder ``"adventure"`` is substituted into
            ``{slot}`` occurrences and ``metadata.slot_values`` is
            empty.
        context: Optional caller-supplied context dict. Currently only
            used as part of the determinism key (so two calls with
            different contexts produce different UUIDs even if all
            other inputs match). Values MUST be JSON-serialisable
            (``None``, ``bool``, ``int``, ``float``, ``str``, ``list``,
            ``dict``) — otherwise :class:`TypeError` is raised with the
            offending key.
        hour: Current local hour (0..23). Drives template eligibility
            via :func:`toybox.activities.time_of_day.is_eligible`.
        seed: Integer seed for the internal :class:`random.Random`.
            Same ``(intent, slot, context, hour, seed)`` MUST produce
            byte-identical output ASSUMING ``conn`` is ``None`` or the
            ``feedback`` table holds the same rows. Past parent
            feedback is part of the selection input by design — same
            seed + different feedback = potentially different pick.
        persona_id: Optional persona id to record on the activity.
        conn: Optional SQLite connection. When provided, the picker
            consults the ``feedback`` table to apply parent
            anti-signal feedback (``didnt_work`` re-pick,
            ``loved_it`` boost, ``dismissed_pre_approval`` soft
            penalty) per :mod:`toybox.activities.feedback`. When
            ``None`` (test path / non-DB callers), behaves exactly
            like the Phase A picker.
        available_toys: Resolved toys from
            :func:`toybox.activities.content_resolver.resolve_toys`.
            When non-empty, the ``{toy}`` placeholder is substituted
            with one toy's ``display_name``, picked deterministically
            from the seeded RNG. When empty, falls back to
            :data:`DEFAULT_TOY_NAME` (the Phase A placeholder).
        available_rooms: Resolved rooms — currently informational. The
            shipped templates don't reference ``{room}``; rooms are
            carried into the labeled_events ChatML so Phase E SFT
            sees them, and the Claude path picks them up via the
            system prompt.
        resolved_children: Aggregated child constraints from
            :func:`toybox.activities.content_resolver.resolve_child_profiles`.
            Drives the offline banned-themes filter — Claude-only
            reading-level guidance is appended at the call site.
        linear_only: Phase W Step W2. When True (household
            ``game_linearity == "linear"``) the picker excludes any
            template that contains a branching step (a step with
            ``choices``). The existing fallback chain is preserved but
            filtered to choice-free templates: primary intent pool →
            intent ``always`` pool → boredom ``always`` pool. When False
            (default) behavior is byte-identical to pre-W2.

    Returns:
        An immutable :class:`Activity` whose ``steps`` length matches
        the picked template's step count (3-20 entries per the
        Phase G Pydantic constraint).

    Raises:
        ValueError: if ``hour`` is outside ``0..23``.
        TypeError: if ``context`` contains a non-JSON-serialisable
            value.
        RuntimeError: if no offline templates can be loaded at all
            (indicates a packaging error — the shipped library always
            has at least one boredom-always template).
    """
    rng = random.Random(seed)

    banned_themes: tuple[str, ...] = ()
    if resolved_children is not None:
        banned_themes = tuple(resolved_children.banned_themes)

    # Pick the toy name BEFORE template selection so candidate
    # signatures (used by the feedback consultation) match the
    # post-substitution signature byte-for-byte. ``rng`` consumption
    # order matters here — pre-step-19 picks consumed rng for template
    # selection first, then the toy. Step 19 reverses this so the
    # toy is in scope when computing each candidate's signature.
    # Same seed + same available_toys → same toy → same template
    # (since the template pick is deterministic given the consumed
    # rng state); the change is invisible to a fixed-seed test as
    # long as the test seeds the toy list consistently.
    picked_toy = _pick_toy_entry(available_toys, rng)
    toy_name = picked_toy.display_name if picked_toy is not None else DEFAULT_TOY_NAME
    toy_ids: tuple[str, ...] = (picked_toy.id,) if picked_toy is not None else ()

    # Phase R Step R4: when the caller pinned a specific template id
    # (e.g. "Play again" in the search UI), short-circuit the slot-picker
    # and use that template directly.  If the id is no longer in the
    # registry (template renamed/deleted), fall back to the normal picker
    # and log a warning so the operator can diagnose stale ids.
    if pinned_template_id is not None:
        pinned = find_template_by_id(pinned_template_id)
        if pinned is not None:
            template = pinned
            _source_intent = intent  # keep intent consistent for callers
        else:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "generate: pinned_template_id %r not found; falling back to picker",
                pinned_template_id,
            )
            template, _source_intent = _select_template(
                intent,
                hour,
                rng,
                slot=slot,
                toy=toy_name,
                conn=conn,
                banned_themes=banned_themes,
                preferred_themes=preferred_themes,
                category=category,
                linear_only=linear_only,
            )
    else:
        template, _source_intent = _select_template(
            intent,
            hour,
            rng,
            slot=slot,
            toy=toy_name,
            conn=conn,
            banned_themes=banned_themes,
            preferred_themes=preferred_themes,
            category=category,
            linear_only=linear_only,
        )

    # Resolve every parametric slot (``{room}``, ``{action_verb}``,
    # ``{adjective}``, etc.) once per template so the title and all
    # steps share the same fills — without this, step 1 might say
    # "stomp" and step 3 "spin" and the activity reads incoherently.
    registry = SlotRegistry.from_resolved(available_rooms)
    slot_values = _resolve_template_slots(
        template,
        slot=slot,
        toy=toy_name,
        registry=registry,
        rng=rng,
    )

    # Substitute placeholders in title and steps.
    title_text, title_slots = _substitute(template.title, slot_values)

    steps: list[ActivityStep] = []
    used_slots: list[str] = list(title_slots)
    for idx, step_tpl in enumerate(template.steps):
        body, step_slots = _substitute(step_tpl.text, slot_values)
        used_slots.extend(step_slots)
        # Phase G: render choice labels with the SAME slot fills used
        # for the body. The rendered tuple is what the kiosk shows AND
        # what ``chosen_label`` records on advance — so a label like
        # ``"Sneak past {toy}"`` becomes ``"Sneak past Penguin"``
        # before it ever leaves the generator. Slot values used inside
        # choice labels also contribute to ``used_slots`` so the
        # signature reflects branching templates that mention
        # signature-contributing slots only inside choices.
        choices_rendered: tuple[str, ...] | None = None
        if step_tpl.choices is not None:
            rendered: list[str] = []
            for label, _next_id in step_tpl.choices:
                rendered_label, label_slots = _substitute(label, slot_values)
                used_slots.extend(label_slots)
                rendered.append(rendered_label)
            choices_rendered = tuple(rendered)
        steps.append(
            ActivityStep(
                step_index=idx,
                text=body,
                sfx=step_tpl.sfx,
                expected_action=step_tpl.expected_action,
                # Phase F Step F6: pass through the static slot from
                # the offline template. Validated against ACTION_SLOTS
                # at template-load time, so this is always valid here.
                action_slot=step_tpl.action_slot,
                # Phase G: thread the optional template step id and
                # the rendered choice labels through to the runtime
                # row so the persistence layer can write
                # ``step_template_id`` and ``choices_json`` without
                # needing the template back.
                step_id=step_tpl.id,
                choices_rendered=choices_rendered,
                # Phase M Step M3: thread element_id through to the
                # runtime row so the kiosk's ElementCard receives the
                # id on the WS envelope. ``None`` for the overwhelming
                # majority of steps (non-Periodic-Table content).
                element_id=step_tpl.element_id,
            )
        )

    activity_id = _derive_uuid(
        intent=intent,
        slot=slot,
        context=context,
        hour=hour,
        seed=seed,
        template_id=template.id,
    )

    slot_values_tuple = tuple(sorted(set(used_slots)))
    metadata: dict[str, Any] = {
        # Sorted + deduped to keep determinism: callers depend on this
        # ordering for the Phase D signature. We use a tuple (not a
        # list) so the value is immutable — Pydantic respects tuples
        # as-is, and callers who try to ``.append`` will get an error
        # rather than silently corrupting a future signature.
        "slot_values": slot_values_tuple,
        "hour_bucket": hour_bucket(hour).value,
        # Phase D step 20: anti-signal signature for feedback matching.
        # ``compute_signature`` takes the actual (post-substitution)
        # slot values, so the emitted signature is the canonical one
        # for THIS activity instance — feedback rows written for it
        # by the parent later (``didnt_work``/``loved_it``/
        # ``dismissed_pre_approval``) will key off the same hash, and
        # future ``generate()`` calls that produce the same template +
        # slot fills will match it.
        "signature": compute_signature(template.id, slot_values_tuple),
        # Phase G G2: full resolved slot map (slot-name → value) so the
        # persistence layer can write ``activities.slot_fills_json``
        # and the lazy advance handler in G3 can render later step
        # bodies + choice labels with the SAME fills as step 1.
        # Distinct from ``slot_values`` (the dedup-and-sort tuple used
        # by anti-signal): ``slot_fills`` is a dict keyed by slot
        # name, includes EVERY resolved fill (both signature-
        # contributing slots like ``{toy}`` and word-list slots like
        # ``{adjective}``), and preserves the slot-name → value
        # mapping that the renderer needs.
        "slot_fills": dict(slot_values),
    }

    return Activity(
        id=activity_id,
        template_id=template.id,
        persona_id=persona_id,
        title=title_text,
        steps=steps,
        version=1,
        metadata=metadata,
        toy_ids=toy_ids,
    )


def clear_template_cache() -> None:
    """Clear the in-process template + schema caches (test hook).

    Called automatically between tests via the ``_reset_cache``
    autouse fixture in ``test_offline_generator.py``. Production
    callers do not need to call this — the cache is keyed on
    ``TEMPLATES_DIR`` so a same-process change of the templates
    directory is already handled correctly.
    """
    _TEMPLATE_CACHE.clear()
    _SCHEMA_VALIDATOR_CACHE.clear()


def build_generator_context(
    *,
    intent: str,
    slot: str | None = None,
    persona_id: str | None = None,
    persona_card: str | None = None,
    available_toys: tuple[str, ...] = (),
    available_rooms: tuple[str, ...] = (),
    child_profile: dict[str, Any] | None = None,
    transcript_window: str | None = None,
    listening_mode: int | None = None,
    time_of_day: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    """Build a ``GeneratorContext`` for the labeled_events recorder.

    Centralised here so call sites (api/activities, core/escalation)
    don't need to import :mod:`toybox.ai.labeled_events` directly — they
    just call ``generator.build_generator_context(...)``. The return
    type is intentionally ``Any`` to avoid a circular import at the
    module-load layer (``ai.labeled_events`` imports
    ``activities.models`` which imports lightly enough to be safe in
    practice; the wrapper keeps the dependency direction clean even so).
    """
    # Late import to keep ``activities.generator`` importable in
    # contexts that haven't loaded the ``ai`` package yet.
    from ..ai.labeled_events import GeneratorContext

    return GeneratorContext(
        intent=intent,
        slot=slot,
        persona_id=persona_id,
        persona_card=persona_card,
        available_toys=available_toys,
        available_rooms=available_rooms,
        child_profile=child_profile,
        transcript_window=transcript_window,
        listening_mode=listening_mode,
        time_of_day=time_of_day,
        extra=extra if extra is not None else {},
    )


GENERATOR_ADAPTER_ENV: Final[str] = "TOYBOX_GENERATOR_ADAPTER"
GENERATOR_MODE_ENV: Final[str] = "TOYBOX_GENERATOR_MODE"

ADAPTER_CLAUDE: Final[str] = "claude"
ADAPTER_LOCAL: Final[str] = "local"
MODE_SINGLE: Final[str] = "single"
MODE_LOOP: Final[str] = "loop"

VALID_ADAPTERS: Final[frozenset[str]] = frozenset({ADAPTER_CLAUDE, ADAPTER_LOCAL})
VALID_MODES: Final[frozenset[str]] = frozenset({MODE_SINGLE, MODE_LOOP})


@dataclass(frozen=True, slots=True)
class GeneratorDispatch:
    """Effective adapter + mode after reading env vars.

    Built by :func:`resolve_dispatch` so call sites can branch on a
    typed value rather than re-reading env strings inline.
    """

    adapter: str
    mode: str

    @property
    def is_v1_default(self) -> bool:
        """True iff this is the v1 ``claude+single`` configuration."""
        return self.adapter == ADAPTER_CLAUDE and self.mode == MODE_SINGLE


def resolve_dispatch() -> GeneratorDispatch:
    """Read ``TOYBOX_GENERATOR_ADAPTER`` + ``TOYBOX_GENERATOR_MODE``.

    Defaults: adapter=claude, mode=single (= v1 behavior). Unknown
    values fall back to the default and emit a WARNING — better to
    serve a v1-shape activity than to crash propose on a typo.
    """
    raw_adapter = os.environ.get(GENERATOR_ADAPTER_ENV, ADAPTER_CLAUDE).strip().lower()
    if raw_adapter not in VALID_ADAPTERS:
        _logger.warning(
            "%s=%r is not in %s; using default %r",
            GENERATOR_ADAPTER_ENV,
            raw_adapter,
            sorted(VALID_ADAPTERS),
            ADAPTER_CLAUDE,
        )
        raw_adapter = ADAPTER_CLAUDE
    raw_mode = os.environ.get(GENERATOR_MODE_ENV, MODE_SINGLE).strip().lower()
    if raw_mode not in VALID_MODES:
        _logger.warning(
            "%s=%r is not in %s; using default %r",
            GENERATOR_MODE_ENV,
            raw_mode,
            sorted(VALID_MODES),
            MODE_SINGLE,
        )
        raw_mode = MODE_SINGLE
    return GeneratorDispatch(adapter=raw_adapter, mode=raw_mode)


def _local_not_implemented(mode: str) -> NotImplementedError:
    return NotImplementedError(f"local adapter ships in Step 26 (E2); requested mode={mode}")


__all__ = [
    "ADAPTER_CLAUDE",
    "ADAPTER_LOCAL",
    "DEFAULT_SLOT_FILLER",
    "DEFAULT_TOY_NAME",
    "FALLBACK_INTENT",
    "GENERATOR_ADAPTER_ENV",
    "GENERATOR_MODE_ENV",
    "GeneratorDispatch",
    "MODE_LOOP",
    "MODE_SINGLE",
    "SUPPORTED_INTENTS",
    "TEMPLATES_DIR",
    "VALID_ADAPTERS",
    "VALID_MODES",
    "build_generator_context",
    "clear_template_cache",
    "find_template_by_id",
    "generate",
    "render_with_slot_fills",
    "resolve_dispatch",
]
