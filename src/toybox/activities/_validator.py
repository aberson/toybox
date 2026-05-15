"""Phase G template-graph validator.

Runs once per template at load time (called from
:mod:`toybox.activities.generator._load_intent_templates`). Enforces
the directed-graph invariants the runtime depends on:

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

A graph violation raises :class:`TemplateGraphError`. Each error
message names the offending template id and the specific violation
so the operator can find the file and the offending step quickly.
"""

from __future__ import annotations

from collections import deque

from .models import Step


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
        _is_terminal(steps[idx], idx, array_length, branch_destination_ids)
        for idx in visited
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


__all__ = ["TemplateGraphError", "validate_template_graph"]
