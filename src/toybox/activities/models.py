"""Pydantic models for the offline activity generator.

The :class:`Activity` model is the public output of
:func:`toybox.activities.generator.generate`. Field shapes align with
the SQLite schema in ``src/toybox/db/migrations/0001_initial.sql``
(tables ``activities`` and ``activity_steps``) so a future persistence
step can serialize without translation gymnastics. Both models are
``frozen=True`` to match :class:`toybox.ws.envelope.Envelope`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from ..image_gen.models import ACTION_SLOTS
from .roles import Role
from .themes import Theme


class Animation(StrEnum):
    """Phase L Step L1: the six picture-reward animations.

    Member names match the lowercase CSS-friendly keyframe identifiers
    used in the frontend's ``rewardAnimations.css`` module (L11). Member
    values are the same strings — they appear verbatim as JSON keys in
    ``rewards.animation``, in the per-activity reward step metadata, and
    as ``RewardName`` string-literal union entries in
    ``frontend/src/shared/types.ts`` (auto-derived at codegen time).

    Order is the parent-UI presentation order (matches the dropdown in
    L6's RewardsList component) — NOT alphabetical. The codegen step
    (``tools/gen_types_ts.py``, L1 extension) preserves member-definition
    order when emitting the TS union so the frontend dropdown matches.

    Single source of truth per code-quality.md §2: no duplicate string
    literals in the frontend; the TS union is derived from this enum.
    """

    shine = "shine"
    jump = "jump"
    spin = "spin"
    pulse = "pulse"
    wobble = "wobble"
    float = "float"


# Phase L Step L1: per-activity reward type.
#
# Wire shape: one of the five literal strings; persisted to
# ``activities.reward_type`` (0020). NULL in that column means "legacy
# pre-L activity" — the resolver (L3) treats NULL as "no reward step".
# ``"random"`` is the documented default that the API layer (L2) writes
# when the parent's ApproveRequest omits the field. ``"none"`` is the
# explicit opt-out (L follow-up Change D): the parent picked "no reward
# this activity"; the resolver short-circuits and no reward step is
# appended. Distinct from NULL (legacy) so we can tell "parent opted
# out" apart from "row predates Phase L" in metrics.
#
# Declared as a ``typing.Literal`` alias rather than a StrEnum because
# the five values are wire-only — they never appear as Python attribute
# accesses; the API layer and resolver consume them as raw strings. Per
# documentation/phase-l-plan.md design decision.
RewardType = Literal["picture", "joke", "song", "random", "none"]

# Phase G: pattern + max length for step ids used as branch targets.
# Tighter than template ids (which allow up to 64 chars) because step ids
# appear inline as `next` / `choices[].next` values inside JSON and the
# author should be nudged toward short identifiers.
_STEP_ID_PATTERN: str = r"^[a-z0-9][a-z0-9_]*$"
_STEP_ID_MAX_LENGTH: int = 32


def _validate_action_slot(v: str | None) -> str | None:
    """Validate that ``v`` is ``None`` or a member of :data:`ACTION_SLOTS`.

    Phase F Step F6: the generator (offline + single-shot Claude path)
    emits one of the 10 fixed action slot keys per step; out-of-vocab
    values from the model fall through the existing malformed-output
    fallback path. ``None`` is the default both for missing-field input
    AND for legacy rows pre-F6, so the kiosk's "no sprite" branch is
    the natural pre-F6 behavior.
    """
    if v is None:
        return v
    if v not in ACTION_SLOTS:
        raise ValueError(f"action_slot must be one of {ACTION_SLOTS!r} or None, got {v!r}")
    return v


class Choice(BaseModel):
    """One branch in a step's :attr:`Step.choices` list (Phase G).

    Authored at template-definition time. The kiosk consumes a
    *runtime* shape (``{label, choice_index}``) that the API serializer
    derives from the persisted ``activity_steps.choices_json`` column;
    that runtime shape is intentionally distinct from this
    template-time shape so an in-flight activity is stable across
    template edits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1, max_length=200)
    next: str = Field(min_length=1)


def _validate_choice_count(v: list[Choice] | None) -> list[Choice] | None:
    """Pin ``len(choices) in (2, 3, 4)`` when ``choices`` is set.

    Phase G: a step that branches must offer at least 2 options (else
    it is not a choice) and at most 4 (per the choice-count design
    decision in the Phase G plan — fits iPad portrait at 44pt touch
    targets without scrolling). The JSON-schema layer enforces the
    same range; defense-in-depth here so callers building Activities
    in tests / fixtures cannot construct a malformed shape.
    """
    if v is None:
        return v
    if not 2 <= len(v) <= 4:
        raise ValueError(f"choices must have between 2 and 4 entries, got {len(v)}")
    return v


StepKind = Literal["text", "fork", "song", "joke"]


class Step(BaseModel):
    """Template-time step definition (Phase G).

    Mirrors ``$defs/step`` in
    ``src/toybox/activities/templates/_schema.json``. Authored by hand
    in the per-intent template JSON files. The *runtime* counterpart
    (one row in ``activity_steps``) is :class:`ActivityStep` — it
    carries the rendered (slot-filled) body and the runtime
    ``current`` flag that this model does not.

    Phase G additions: ``id``, ``next``, ``choices``. All optional and
    backward-compatible: existing templates that have none of these
    fields rely on the implicit fall-through edge rule (advance to
    the next array position).

    Phase K K3 additions: ``kind`` (one of ``"text" | "fork" | "song"
    | "joke"``, default ``"text"`` for backward-compat with the 200
    existing branching templates), ``corpus_id`` (corpus entry id for
    ``song`` / ``joke`` kinds — corpus loading lives in K10/K11; K3
    only gates the SHAPE), and ``auto`` (when true on a ``song`` /
    ``joke`` step, the engine picks a corpus entry at activity-creation
    time using the template's ``recommended_themes``). ``corpus_id``
    and ``auto`` are mutually exclusive and at least one must be set
    on ``song`` / ``joke`` steps.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=600)
    sfx: str | None = Field(default=None, max_length=64)
    expected_action: str | None = Field(default=None, max_length=64)
    action_slot: Annotated[str | None, AfterValidator(_validate_action_slot)] = None
    # Phase G: stable identifier for branch targeting. Required only
    # on steps referenced as a `next` / `choices[].next` target;
    # the load-time graph validator catches missing-target / orphan
    # cases. Pattern + length matched to ``_schema.json``.
    id: Annotated[
        str | None,
        Field(default=None, pattern=_STEP_ID_PATTERN, max_length=_STEP_ID_MAX_LENGTH),
    ] = None
    # Phase G: explicit successor step id. Mutually exclusive with
    # ``choices`` (enforced by ``_check_next_xor_choices`` below).
    next: str | None = Field(default=None, min_length=1)
    # Phase G: branching choice point. ``len(choices) in (2,3,4)``.
    choices: Annotated[list[Choice] | None, AfterValidator(_validate_choice_count)] = None
    # Phase K K3: step kind. ``"text"`` is the default for the 200
    # existing branching templates that omit the field. ``"song"`` and
    # ``"joke"`` deliver interjections — validator gates the shape
    # (corpus_id XOR auto=true). ``"fork"`` is the explicit spelling of
    # the existing choices-bearing shape (not auto-derived; templates
    # that set choices but leave kind at the default ``"text"`` still
    # validate, preserving backward-compat).
    kind: StepKind = "text"
    # Phase K K3: corpus entry id for ``song`` / ``joke`` step kinds.
    # Pattern + length echoed from corpus-entry-id format in
    # documentation/phase-k-plan.md §2 "Identifier formats".
    corpus_id: str | None = Field(default=None, min_length=1, max_length=64)
    # Phase K K3: when true on a ``song`` / ``joke`` step, the engine
    # picks a corpus entry from ``recommended_themes`` at creation
    # time. Mutually exclusive with ``corpus_id``.
    auto: bool | None = None

    @model_validator(mode="after")
    def _check_next_xor_choices(self) -> Step:
        """Reject steps that set BOTH ``next`` and ``choices``.

        The runtime would otherwise have to pick a winner — confusing
        for authors. The JSON-schema layer enforces the same constraint
        via a ``not`` clause; this validator is defense-in-depth for
        callers that bypass the schema (tests, in-memory fixtures).
        """
        if self.next is not None and self.choices is not None:
            raise ValueError(f"step id={self.id!r} sets both `next` and `choices`; pick one")
        return self

    @model_validator(mode="after")
    def _check_song_joke_shape(self) -> Step:
        """Phase K K3: ``song`` / ``joke`` steps must reference a
        corpus entry via ``corpus_id``.

        K10 (jokes) and K11 (songs) own corpus loading; K3 only gates
        the SHAPE so a hand-edited template with a typo'd ``kind``
        or missing source-of-content fails LOUDLY at load time.

        Phase L Step L5 — ``auto=True`` is now REJECTED on song / joke
        steps. The advance-time embedded picker that resolved a corpus
        entry from ``recommended_themes`` at activity-creation time
        (``_pick_embedded_corpus_step`` in ``api/activities.py``) was
        deleted in L5. Without that picker, an ``auto=True`` step falls
        through to the generic insertion path and the template's
        placeholder ``text`` ("(embedded song — body rendered from
        corpus at advance time)") renders verbatim on the kiosk. The
        validator now refuses the shape so a future template author
        cannot reintroduce the regression. See
        ``documentation/phase-l-plan.md`` § L5 for the surface deletion.
        """
        if self.kind in ("song", "joke"):
            # Phase L Step L5: ``auto=true`` no longer has a runtime
            # picker. Reject so the regression that bled placeholder
            # body text onto the kiosk cannot recur.
            if self.auto is True:
                raise ValueError(
                    f"step id={self.id!r} kind={self.kind!r} sets `auto=true`, "
                    f"but the embedded picker was removed in Phase L Step L5 "
                    f"(see documentation/phase-l-plan.md). Pin a specific "
                    f"corpus entry via `corpus_id` instead."
                )
            if self.corpus_id is None:
                raise ValueError(
                    f"step id={self.id!r} kind={self.kind!r} must set "
                    f"`corpus_id` (Phase L Step L5 removed the `auto=true` path)"
                )
        else:
            # ``corpus_id`` / ``auto`` are only meaningful on song / joke
            # steps. Reject them on other kinds so a stray field doesn't
            # silently mask a typo in ``kind``.
            if self.corpus_id is not None:
                raise ValueError(
                    f"step id={self.id!r} kind={self.kind!r} sets `corpus_id`; "
                    f"only valid on kind='song' or kind='joke'"
                )
            if self.auto is not None:
                raise ValueError(
                    f"step id={self.id!r} kind={self.kind!r} sets `auto`; "
                    f"only valid on kind='song' or kind='joke'"
                )
        return self


class ActivityStep(BaseModel):
    """Runtime step in an activity (one row in ``activity_steps``).

    Mirrors the ``activity_steps`` columns: ``seq`` (1-indexed in the
    DB, but we use ``step_index`` in-memory and 0-index it because
    Python lists are 0-indexed and that matches how callers iterate
    ``activity.steps``), ``body`` (the spoken/displayed text), ``sfx``,
    ``expected_action``, and (Phase F Step F6) ``action_slot`` —
    one of the 10 fixed action vocabulary keys (or ``None`` to render
    no sprite). ``current`` lives at runtime, not on generated output,
    and is therefore omitted here.

    Phase G additions (load-bearing for G2 lazy insertion + G3 advance):

    * ``step_id`` — the template-time :attr:`Step.id` for this step
      when it has one (NULL on legacy linear steps with no id).
      Persisted to ``activity_steps.step_template_id`` so the lazy
      advance handler in G3 can resolve ``next`` / ``choices[i].next``
      targets via template lookup without having to recover the
      array index from rendered body text.
    * ``choices_rendered`` — the per-choice button labels for this
      step, already rendered with the activity's slot fills (so the
      list is byte-identical to what the kiosk shows). NULL when the
      template step has no ``choices``. Persisted to
      ``activity_steps.choices_json`` as a JSON array of strings.
    """

    model_config = ConfigDict(frozen=True)

    step_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    sfx: str | None = None
    expected_action: str | None = None
    action_slot: Annotated[str | None, AfterValidator(_validate_action_slot)] = None
    # Phase G: template step id (when present in the template). NULL
    # on legacy linear steps that have no `id`. Pattern + length match
    # ``Step.id`` exactly.
    step_id: Annotated[
        str | None,
        Field(default=None, pattern=_STEP_ID_PATTERN, max_length=_STEP_ID_MAX_LENGTH),
    ] = None
    # Phase G: rendered choice-button labels. Each entry is a label
    # string with all ``{slot}`` placeholders already substituted via
    # the activity's slot fills, so the runtime label is the EXACT
    # string the kid sees. ``None`` for steps that have no choices.
    choices_rendered: tuple[str, ...] | None = None


class Activity(BaseModel):
    """A generated 5-step activity.

    Field shapes echo the ``activities`` table where possible. Fields
    that are runtime-only (e.g. ``state``, ``session_id``,
    ``created_at``) are NOT set by the generator — a persistence
    layer adds them when an Activity is enqueued for a session.

    The ``metadata`` dict carries the load-bearing inputs to the
    Phase D step 19 ``signature`` computation: the template id, the
    sorted slot values, and the hour bucket label.

    ``toy_ids`` carries the FK(s) of toys the generator picked for
    this activity; an empty tuple means none.

    .. warning::

       ``frozen=True`` does NOT deep-freeze nested mutable values.
       The ``metadata`` dict itself is rebindable-via-Pydantic only,
       but its contents (the ``dict`` and any nested mutable values
       like ``list``) can technically still be mutated in-place.
       Callers MUST treat ``metadata`` as read-only after
       construction — Phase D's ``signature`` reads
       ``metadata["slot_values"]``, and post-hoc mutation would
       silently change signatures. The generator emits
       ``slot_values`` as an immutable ``tuple[str, ...]`` to make
       the most load-bearing entry safe from accidental mutation.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    persona_id: str | None = None
    title: str = Field(min_length=1)
    # Phase G: relaxed from `min_length=5, max_length=5` to
    # `min_length=3, max_length=20` so templates can be short
    # (3-step micro-quests) or long (multi-branch missions). The
    # generator still seeds the runtime activity with all template
    # steps; G2 introduces lazy DB insertion that decouples the DB
    # row count from the template step count.
    steps: list[ActivityStep] = Field(min_length=3, max_length=20)
    version: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    toy_ids: tuple[str, ...] = Field(default_factory=tuple)


class Template(BaseModel):
    """Phase K K3: template-time Pydantic shape.

    Mirrors ``$defs/template`` in
    ``src/toybox/activities/templates/_schema.json``. The
    ``_parse_template`` path in :mod:`toybox.activities.generator`
    keeps using its lightweight ``_Template`` dataclass for the
    runtime hot path; this Pydantic model exists so the new K3
    template-level invariants (``required_roles`` / ``optional_roles``
    / ``recommended_themes``) get the same defense-in-depth treatment
    the per-step shape gets via :class:`Step`. Callers building
    templates from in-memory dicts (tests, fixtures, generator
    scripts) can construct this and receive the full validation
    including the K3 placeholder / ceiling / kind-shape gates wrapped
    by :func:`toybox.activities._validator.validate_template`.

    Backward-compat: every K3 field is optional with a default of the
    empty list, so a JSON template authored under the pre-K3 schema
    parses unchanged.

    Phase L Step L5 — ``extra="ignore"`` was relaxed from
    ``extra="forbid"`` so existing template JSONs that still carry the
    deprecated ``ending_step`` key (Phase K K3) parse cleanly. The
    runtime no longer reads the field; the template authors will
    scrub it in a future cleanup pass.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_]*$")
    title: str = Field(min_length=1, max_length=200)
    buckets: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=3, max_length=20)
    # Phase K K3: top-level role declarations. Both lists default to
    # empty for backward-compat with the 200 existing branching
    # templates that omit the fields entirely. Pydantic gates that
    # every entry is a valid :class:`Role`; uniqueness is enforced by
    # the validator (see ``_check_role_uniqueness``).
    required_roles: list[Role] = Field(default_factory=list)
    optional_roles: list[Role] = Field(default_factory=list)
    # Phase K K3: theme tags. Pydantic gates that every entry is a
    # valid :class:`Theme`. Phase L Step L5 removed the K14 embedded
    # picker that consumed these tags; the field remains accepted so
    # existing templates parse unchanged and a future surface can
    # reintroduce theme-driven selection.
    recommended_themes: list[Theme] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_role_uniqueness(self) -> Template:
        """Reject duplicate role entries within ``required_roles`` or
        ``optional_roles``, and reject overlap between the two lists.

        Authoring intent for a role appearing in both lists is
        ambiguous (required AND optional?) — fail loudly rather than
        pick a winner.
        """
        if len(set(self.required_roles)) != len(self.required_roles):
            raise ValueError(f"template id={self.id!r}: `required_roles` contains duplicates")
        if len(set(self.optional_roles)) != len(self.optional_roles):
            raise ValueError(f"template id={self.id!r}: `optional_roles` contains duplicates")
        overlap = set(self.required_roles) & set(self.optional_roles)
        if overlap:
            raise ValueError(
                f"template id={self.id!r}: role(s) {sorted(r.value for r in overlap)!r} "
                f"appear in both `required_roles` and `optional_roles`; pick one"
            )
        return self


__all__ = [
    "Activity",
    "ActivityStep",
    "Animation",
    "Choice",
    "RewardType",
    "Step",
    "StepKind",
    "Template",
]
