"""Phase G template-graph validator + Phase K K3 template-shape validator.

Phase G (:func:`validate_template_graph`) runs once per template at
load time (called from :mod:`toybox.activities.generator._load_intent_templates`).
Enforces the directed-graph invariants the runtime depends on:

a. All ``id`` values within a template are unique.
b. Every ``next`` and ``choices[].next`` resolves to a step ``id``
   that exists in the same template.
c. All steps are reachable from ``steps[0]`` via BFS — no orphans.
d. No cycles — BFS revisits raise.
e. At least one path reaches a terminal node (a node with no
   outgoing edge).
f. No step has BOTH ``next`` and ``choices`` (also caught at the
   Pydantic + JSON-schema layers — defense-in-depth).
g. ``len(choices) in (2, 3, 4)`` when present (also caught at the
   Pydantic + JSON-schema layers — defense-in-depth).

**Edges include implicit fall-through.** A step that has neither
``next`` nor ``choices`` AND is not the last entry in the template's
``steps`` array advances to ``steps[i + 1]``. This is the rule that
keeps existing 5-step linear templates valid unchanged. Reachability
and cycle detection both account for it.

Phase K K3 (:func:`validate_template`) runs the same per-template
load-time gate over the new role / theme / ending_step / step-kind
shape. The gates enforce:

* All ``{role_name}`` placeholders in step ``text`` are members of
  ``required_roles ∪ optional_roles ∪ {known non-role slots}`` (the
  legacy ``{toy}`` / ``{slot}`` / SlotRegistry word-list slots are
  still permitted unchanged).
* ``len(required_roles) ≤ distinct_toy_ceiling(template)`` where
  the ceiling counts distinct role-bearing placeholders the template
  references in step text + choice labels + title.
* ``ending_step.kind`` ∈ ``{"song", "joke"}`` (gated by Pydantic
  ``EndingStep`` once typed — re-asserted here for defense-in-depth).
* ``song`` / ``joke`` step bodies have either ``corpus_id`` set or
  ``auto=True`` (gated by Pydantic ``Step`` ``model_validator`` —
  re-asserted here so the public validator entry point gives a
  single ``TemplateGraphError`` shape regardless of which layer
  caught the violation).

A graph violation raises :class:`TemplateGraphError`. Each error
message names the offending template id and the specific violation
so the operator can find the file and the offending step quickly.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Final

from .models import EndingStep, Step, Template
from .roles import Role
from .slots import KNOWN_SLOTS


class TemplateGraphError(ValueError):
    """Raised when a template's step graph violates Phase G invariants."""


def _step_outgoing(
    step: Step,
    array_index: int,
    array_length: int,
    id_to_index: dict[str, int],
    branch_destination_ids: frozenset[str],
) -> list[int]:
    """Return the array indices of every step ``step`` has an edge to.

    Implements the edge rules from the Phase G plan:
    1. ``choices`` → all ``choices[i].next`` targets.
    2. ``next`` → the single ``next`` target.
    2.5. branch-destination leaf (referenced by some ``choices[*].next``,
         no ``next``/``choices`` of its own) → terminal. Without this
         the implicit fall-through in rule 3 would bleed one branch's
         ending into the next array entry (the sibling branch's ending).
    3. neither + not last in array + not a branch destination → fall
       through to ``array_index + 1``.
    4. neither + last in array → terminal (no outgoing edges).

    Targets that fail to resolve return an empty list — the
    missing-target check (rule b) runs separately so the caller can
    emit a precise error message.
    """
    if step.choices is not None:
        out: list[int] = []
        for choice in step.choices:
            target_idx = id_to_index.get(choice.next)
            if target_idx is not None:
                out.append(target_idx)
        return out
    if step.next is not None:
        target_idx = id_to_index.get(step.next)
        if target_idx is None:
            return []
        return [target_idx]
    if step.id is not None and step.id in branch_destination_ids:
        return []
    if array_index + 1 < array_length:
        return [array_index + 1]
    return []


def _is_terminal(
    step: Step,
    array_index: int,
    array_length: int,
    branch_destination_ids: frozenset[str],
) -> bool:
    """A step is terminal iff it has no outgoing edges under the
    rules in :func:`_step_outgoing` — either it's the last array entry
    with no ``next``/``choices``, or it's a branch-destination leaf."""
    if step.choices is not None or step.next is not None:
        return False
    if array_index + 1 == array_length:
        return True
    return step.id is not None and step.id in branch_destination_ids


def _collect_branch_destination_ids(steps: list[Step]) -> frozenset[str]:
    """Step ids referenced by some step's ``choices[*].next``."""
    out: set[str] = set()
    for s in steps:
        if s.choices is None:
            continue
        for choice in s.choices:
            out.add(choice.next)
    return frozenset(out)


def validate_template_graph(template_id: str, steps: list[Step]) -> None:
    """Enforce the Phase G graph invariants on ``steps``.

    ``template_id`` is included in every error message so the operator
    can pinpoint the offending file. ``steps`` MUST be the parsed
    Pydantic-validated step list — this validator does not re-check
    fields the Pydantic / JSON-schema layers already enforce
    (length range, choice-count range, mutual exclusion of
    ``next`` and ``choices``); it only checks the graph-level shape.
    But it does emit clearer errors for the few overlapping cases
    (defense-in-depth for non-Pydantic callers).

    Raises:
        TemplateGraphError: on any graph violation. Message names
            ``template_id`` and the offending step / target.
    """
    # ----- (a) unique ids ----------------------------------------------------
    id_to_index: dict[str, int] = {}
    for idx, step in enumerate(steps):
        if step.id is None:
            continue
        if step.id in id_to_index:
            raise TemplateGraphError(
                f"template {template_id!r}: duplicate step id "
                f"{step.id!r} at indices {id_to_index[step.id]} and {idx}"
            )
        id_to_index[step.id] = idx

    # ----- (f) + (g): defense-in-depth on per-step shape --------------------
    # Pydantic catches both already, but a caller building Steps
    # by directly populating model_config-bypassing code paths
    # could still slip past. Cheap to verify here.
    for idx, step in enumerate(steps):
        if step.next is not None and step.choices is not None:
            label = step.id if step.id is not None else f"index {idx}"
            raise TemplateGraphError(
                f"template {template_id!r}: step {label!r} has both `next` and `choices`; pick one"
            )
        if step.choices is not None and not 2 <= len(step.choices) <= 4:
            label = step.id if step.id is not None else f"index {idx}"
            raise TemplateGraphError(
                f"template {template_id!r}: step {label!r} has "
                f"{len(step.choices)} choices; must be between 2 and 4"
            )

    # ----- (b) every next / choices[].next resolves -------------------------
    for idx, step in enumerate(steps):
        label = step.id if step.id is not None else f"index {idx}"
        if step.next is not None and step.next not in id_to_index:
            raise TemplateGraphError(
                f"template {template_id!r}: step {label!r} references unknown next id {step.next!r}"
            )
        if step.choices is not None:
            for choice in step.choices:
                if choice.next not in id_to_index:
                    raise TemplateGraphError(
                        f"template {template_id!r}: step {label!r} "
                        f"choice {choice.label!r} references unknown "
                        f"next id {choice.next!r}"
                    )

    # ----- (c) + (d): BFS from steps[0]; reachability + cycle ---------------
    # We track per-node visited state on the array index (not the id)
    # so steps without an id participate too. A revisit during BFS
    # signals a cycle — the implicit "advance to next array position"
    # cannot create a cycle on its own, but combining it with `next`
    # back-edges can.
    array_length = len(steps)
    branch_destination_ids = _collect_branch_destination_ids(steps)
    visited: set[int] = set()
    parents: dict[int, int | None] = {0: None}
    queue: deque[int] = deque([0])
    while queue:
        idx = queue.popleft()
        if idx in visited:
            # Cycle — reconstruct the path back to itself for the
            # error message so the operator can read it as
            # ``a → b → a`` instead of just "cycle through a".
            cycle_path = _trace_cycle(parents, idx)
            raise TemplateGraphError(
                f"template {template_id!r}: cycle detected through {cycle_path}"
            )
        visited.add(idx)
        for next_idx in _step_outgoing(
            steps[idx], idx, array_length, id_to_index, branch_destination_ids
        ):
            if next_idx not in visited and next_idx not in queue:
                parents[next_idx] = idx
                queue.append(next_idx)
            elif next_idx in visited:
                # Back-edge to an already-finalized node = cycle.
                cycle_parents = {**parents, idx: parents.get(idx), next_idx: idx}
                cycle_path = _trace_cycle(cycle_parents, next_idx)
                raise TemplateGraphError(
                    f"template {template_id!r}: cycle detected through {cycle_path}"
                )

    # ----- (c) orphan check (any unvisited step) ----------------------------
    for idx in range(array_length):
        if idx not in visited:
            step_id = steps[idx].id
            label = step_id if step_id is not None else f"index {idx}"
            raise TemplateGraphError(
                f"template {template_id!r}: step {label!r} is unreachable from steps[0]"
            )

    # ----- (e) at least one terminal reachable ------------------------------
    if not any(
        _is_terminal(steps[idx], idx, array_length, branch_destination_ids) for idx in visited
    ):
        # With the implicit-fall-through rule the last array entry
        # always satisfies (e), so this branch only fires if every
        # reached node has an outgoing edge — e.g. all steps use
        # explicit ``next`` and form a closed loop, which the cycle
        # check catches first. Keep the check anyway: a future edit
        # that loosens the cycle detector mustn't accidentally allow
        # terminal-free graphs.
        raise TemplateGraphError(
            f"template {template_id!r}: no terminal step reachable from steps[0]"
        )


def _trace_cycle(parents: dict[int, int | None], start_idx: int) -> str:
    """Walk ``parents`` back from ``start_idx`` to surface the cycle.

    Returns a human-readable arrow chain like ``'a' -> 'b' -> 'a'``.
    Falls back to bare array indices when a step has no id.
    """
    path: list[int] = [start_idx]
    seen = {start_idx}
    current = parents.get(start_idx)
    while current is not None:
        path.append(current)
        if current in seen:
            break
        seen.add(current)
        current = parents.get(current)
    path.reverse()
    return " -> ".join(repr(str(idx)) for idx in path)


# ---------------------------------------------------------------------------
# Phase K K3: template-shape validator
# ---------------------------------------------------------------------------

# Match ``{name}`` placeholders. Mirrors the pattern used by
# :func:`toybox.activities.generator._resolve_template_slots` so the
# validator counts the SAME placeholder set the substitutor sees.
# Slot names are lower-snake-case; anything outside that alphabet is
# ignored so JSON-y braces in step text wouldn't trip the gate.
_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{([a-z_][a-z_]*)\}")

# Non-role placeholder names the template author may legally use.
# ``toy`` and ``slot`` are the legacy single-toy / caller-supplied
# substitution slots; the SlotRegistry-backed names round out the
# canonical list. Anything outside this set AND outside the declared
# role list is flagged.
_NON_ROLE_KNOWN_SLOTS: Final[frozenset[str]] = frozenset({"toy", "slot"}) | KNOWN_SLOTS


def _collect_placeholder_names(template: Template) -> set[str]:
    """Return every ``{name}`` placeholder name referenced anywhere
    in the template's title, step text, or fork-choice labels.

    Mirrors the haystack assembled by
    :func:`toybox.activities.generator._resolve_template_slots` so the
    K3 placeholder gate sees the SAME set of names the runtime
    substitutor will resolve. Keeping the two in lock-step is
    code-quality.md §1 — the placeholder-name shape is the producer
    side of a producer-consumer pair, and the runtime substitutor is
    the consumer.
    """
    parts: list[str] = [template.title]
    for s in template.steps:
        parts.append(s.text)
        if s.choices is not None:
            for choice in s.choices:
                parts.append(choice.label)
    haystack = " ".join(parts)
    return {match.group(1) for match in _PLACEHOLDER_PATTERN.finditer(haystack)}


def _collect_role_placeholders(template: Template) -> set[str]:
    """Return the subset of placeholder names that are valid ``Role`` values.

    Used both by the placeholder gate (membership in
    ``required_roles ∪ optional_roles``) and by the distinct-toy-ceiling
    computation (how many distinct toy-bearing slots the template
    actually references).
    """
    role_values = {r.value for r in Role}
    return _collect_placeholder_names(template) & role_values


def _distinct_toy_ceiling(template: Template) -> int:
    """Return the max number of distinct toys this template can use.

    Counts distinct role-bearing placeholders ``{role_name}`` referenced
    anywhere in the template's title, step text, or choice labels.
    The legacy single-toy ``{toy}`` placeholder is NOT counted here —
    it pre-dates the role taxonomy and is filled by the persona's
    primary toy regardless of role declarations.

    Used by the K3 gate ``len(required_roles) ≤ distinct_toy_ceiling``:
    a template declaring 3 required roles but only referencing 2 of
    them in step text is misconfigured — the third role would never
    appear in the kid's experience.
    """
    return len(_collect_role_placeholders(template))


def validate_template(template: Template) -> None:
    """Phase K K3: enforce the role + theme + step-kind + ending-step
    shape invariants on a parsed :class:`Template`.

    Pre-conditions: the Pydantic layer (:class:`Template`,
    :class:`Step`, :class:`EndingStep`) has already gated per-field
    shape (roles are :class:`Role` members, themes are :class:`Theme`
    members, ``ending_step.kind`` is ``"song"`` or ``"joke"``,
    ``song`` / ``joke`` steps carry ``corpus_id`` XOR ``auto=True``).
    This validator adds the cross-field invariants that need the whole
    template in hand:

    1. Every ``{role_name}`` placeholder used in step text / choice
       labels / title is a member of
       ``required_roles ∪ optional_roles``. A bare role name in text
       with no matching declaration is a typo or a stale rename.
    2. ``len(required_roles)`` ≤ ``_distinct_toy_ceiling(template)``.
       Declaring more required roles than the template references in
       any of its text means the extra roles silently never appear.

    Raises:
        TemplateGraphError: on any K3 violation. Message names
            ``template.id`` and the offending field / placeholder.

    The Phase G graph invariants are NOT re-checked here — call
    :func:`validate_template_graph` separately, or use the
    ``_parse_template`` path in :mod:`toybox.activities.generator`
    which orchestrates both.
    """
    template_id = template.id

    # ----- (K3.1) placeholder set ⊆ declared roles ∪ known non-role slots
    placeholder_names = _collect_placeholder_names(template)
    role_values = {r.value for r in Role}
    declared_role_values = {r.value for r in template.required_roles} | {
        r.value for r in template.optional_roles
    }
    for name in sorted(placeholder_names):
        if name in _NON_ROLE_KNOWN_SLOTS:
            continue
        if name in role_values:
            # A role-name placeholder must be declared in either
            # required_roles or optional_roles. Otherwise the
            # slot-fill engine has no signal that the role applies
            # and the placeholder would echo back as literal text.
            if name not in declared_role_values:
                raise TemplateGraphError(
                    f"template {template_id!r}: step text references "
                    f"role placeholder {{{name}}} but {name!r} is not "
                    f"in `required_roles` or `optional_roles`"
                )
            continue
        # Names outside both the role taxonomy AND the known slot list
        # are not flagged by this validator — the existing generator
        # behavior echoes unknown placeholders back as literal text so
        # the typo surfaces visibly. Tightening this is a v2 lint job.

    # ----- (K3.2) required_roles count ≤ distinct-toy ceiling --------------
    ceiling = _distinct_toy_ceiling(template)
    if len(template.required_roles) > ceiling:
        raise TemplateGraphError(
            f"template {template_id!r}: required_roles has "
            f"{len(template.required_roles)} entries but template "
            f"only references {ceiling} distinct role placeholder(s) "
            f"in its text — declared roles must each appear in at "
            f"least one step / choice / title"
        )

    # ----- (K3.3) ending_step.kind ∈ {"song", "joke"} ----------------------
    # The Pydantic ``EndingStep`` model already gates this via
    # ``Literal["song", "joke"]``; the defense-in-depth re-check here
    # catches in-memory construction paths that bypass Pydantic (e.g.,
    # a future caller building ``Template`` with ``model_construct`` to
    # skip validation). Cheap to verify; consistent error shape.
    if template.ending_step is not None:
        if not isinstance(template.ending_step, EndingStep):
            raise TemplateGraphError(
                f"template {template_id!r}: ending_step is not an EndingStep instance"
            )
        if template.ending_step.kind not in ("song", "joke"):
            raise TemplateGraphError(
                f"template {template_id!r}: ending_step.kind="
                f"{template.ending_step.kind!r} is not 'song' or 'joke'"
            )

    # ----- (K3.4) song / joke step shape: corpus_id XOR auto=True ----------
    # Same defense-in-depth rationale as (K3.3). The Pydantic
    # ``Step._check_song_joke_shape`` validator already catches these
    # at model construction; re-checked here so callers using
    # ``validate_template`` directly get a uniform
    # :class:`TemplateGraphError` shape.
    for idx, step in enumerate(template.steps):
        label = step.id if step.id is not None else f"index {idx}"
        if step.kind in ("song", "joke"):
            if step.corpus_id is None and step.auto is not True:
                raise TemplateGraphError(
                    f"template {template_id!r}: step {label!r} "
                    f"kind={step.kind!r} must set `corpus_id` or `auto=true`"
                )
            if step.corpus_id is not None and step.auto is True:
                raise TemplateGraphError(
                    f"template {template_id!r}: step {label!r} "
                    f"kind={step.kind!r} sets both `corpus_id` and "
                    f"`auto=true`; pick one"
                )


__all__ = [
    "TemplateGraphError",
    "validate_template",
    "validate_template_graph",
]
