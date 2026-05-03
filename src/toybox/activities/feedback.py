"""Anti-signal feedback for the offline activity generator (Phase D step 20).

Two responsibilities:

1. **Signature computation.** Each activity candidate (template + the
   sorted, deduped slot values that template would substitute) has a
   stable :func:`compute_signature` derived from
   ``sha256("{template_id}:{slot_fingerprint}")`` per
   ``documentation/plan.md`` §"feedback table". The fingerprint is the
   sorted ``slot=value`` pairs joined by ``,`` — sorting is the
   load-bearing step that makes the same slot-set produce the same
   signature regardless of insertion order.

2. **Consultation.** Given a list of candidate templates (and their
   would-be slot values) plus a SQLite connection, return a single
   selected ``(template_id, signature)`` after applying past parent
   feedback as a candidate-ranking adjustment. See
   :func:`consult_and_select`.

Decay strategy: weight multipliers (not time windows). A
``didnt_work`` entry is a hard veto (re-pick) — the candidate is
removed from the eligible pool unless the pool would otherwise be
empty (in which case we degrade gracefully and pick from the full
list, since serving *something* is better than crashing on a bored
child). ``loved_it`` adds a positive offset to the candidate's
weight; ``dismissed_pre_approval`` subtracts a smaller penalty than
``didnt_work`` (the parent dismissed before the child even tried it,
which is a softer signal). The constants live as module attributes
for testability.

Why weight multipliers and not time windows: fewer moving parts. A
weight pipeline is one ``GROUP BY`` query plus a Python dict; a time
window means another column on ``feedback`` (or filtering by
``created_at``) and a clock dependency. The Phase D step 20 spec
allows either — see ``documentation/plan.md`` §"Phase D — Polish"
step 20 row.
"""

from __future__ import annotations

import hashlib
import logging
import random
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

_logger = logging.getLogger(__name__)

# Feedback ``kind`` values written to the ``feedback`` table. Pinned
# here so callers (api/activities.py) and the consultation reader
# agree on the literal strings.
KIND_DIDNT_WORK: Final[str] = "didnt_work"
KIND_LOVED_IT: Final[str] = "loved_it"
KIND_DISMISSED_PRE_APPROVAL: Final[str] = "dismissed_pre_approval"

ALL_FEEDBACK_KINDS: Final[frozenset[str]] = frozenset(
    {KIND_DIDNT_WORK, KIND_LOVED_IT, KIND_DISMISSED_PRE_APPROVAL}
)

# Weight adjustments applied during consultation. ``didnt_work`` is a
# hard veto (we drop the candidate from the eligible pool entirely
# unless that would empty the pool — see ``consult_and_select``).
# ``dismissed_pre_approval`` is a soft anti-signal; ``loved_it``
# boosts. Each ``feedback`` row contributes its weight cumulatively,
# so two ``loved_it`` rows for the same signature double-boost.
WEIGHT_LOVED_IT: Final[float] = 0.5
WEIGHT_DISMISSED: Final[float] = -0.3
WEIGHT_DIDNT_WORK: Final[float] = -1.0  # informational; veto handled separately


@dataclass(frozen=True, slots=True)
class FeedbackCounts:
    """Per-signature feedback tally pulled from the ``feedback`` table."""

    didnt_work: int = 0
    loved_it: int = 0
    dismissed: int = 0

    def is_blocked(self) -> bool:
        """A single ``didnt_work`` row is enough to block re-pick."""
        return self.didnt_work > 0

    def weight(self) -> float:
        """Soft-signal weight (boost minus dismissed penalty).

        ``didnt_work`` is intentionally NOT folded in here — it is a
        hard veto handled separately by
        :meth:`is_blocked`. Including it here would be double-counting
        when the veto kicks in and would silently flip behaviour when
        the veto is overridden by the all-blocked degradation.
        """
        return WEIGHT_LOVED_IT * self.loved_it + WEIGHT_DISMISSED * self.dismissed


@dataclass(frozen=True, slots=True)
class Candidate:
    """One template candidate the consultation considers.

    ``signature`` is precomputed by the caller (the generator knows
    the template id and the slot values that would be substituted, so
    it can compute the signature without doing the substitution
    twice).
    """

    template_id: str
    signature: str
    slot_values: tuple[str, ...] = field(default=())


def slot_fingerprint(slot_values: Sequence[str]) -> str:
    """Build the slot-fingerprint half of a signature.

    The plan documents the format as "sha256 of sorted slot key=value
    pairs". The Phase A generator only emits one slot key (``slot``),
    so we model each value as ``slot=<v>`` and join sorted+deduped
    values with ``,``. Empty input → empty string (so a signature for
    a template with no slot fills is just
    ``sha256("{template_id}:")``).

    Sorting is load-bearing: the generator already emits
    ``slot_values`` sorted+deduped, but we re-sort here defensively so
    a caller that hand-builds ``slot_values`` can't accidentally
    perturb the signature.
    """
    deduped = sorted(set(slot_values))
    return ",".join(f"slot={v}" for v in deduped)


def compute_signature(template_id: str, slot_values: Sequence[str]) -> str:
    """Compute the anti-signal signature for a candidate.

    Formula (per GitHub issue #25 and ``documentation/plan.md``
    §"feedback table"):

    ::

        signature = sha256("{template_id}:{sorted slot k=v}")

    Returns a lowercase hex digest. Pure function — same inputs always
    return the same digest.
    """
    fingerprint = slot_fingerprint(slot_values)
    payload = f"{template_id}:{fingerprint}".encode()
    return hashlib.sha256(payload).hexdigest()


def fetch_counts(
    conn: sqlite3.Connection, signatures: Sequence[str]
) -> dict[str, FeedbackCounts]:
    """Aggregate ``feedback`` rows by signature for the given keys.

    Returns a dict keyed by signature. Signatures with no rows are
    ABSENT from the result (callers default to
    ``FeedbackCounts()``). Signatures whose ``kind`` is not one of the
    three documented values are silently ignored — old rows from
    before this module landed (e.g. the empty-string signature the
    pre-step-20 ``didnt-work`` endpoint wrote) won't match a
    candidate's hash and are inert by construction.

    Empty ``signatures`` short-circuits without a query.
    """
    if not signatures:
        return {}
    # Dedup so a caller passing the same signature multiple times
    # doesn't pay for a wider IN clause.
    unique = sorted(set(signatures))
    placeholders = ",".join("?" * len(unique))
    sql = (
        "SELECT signature, kind, COUNT(*) AS n "
        f"FROM feedback WHERE signature IN ({placeholders}) "
        "GROUP BY signature, kind"
    )
    out: dict[str, FeedbackCounts] = {}
    for row in conn.execute(sql, unique):
        sig = str(row["signature"]) if hasattr(row, "keys") else str(row[0])
        kind = str(row["kind"]) if hasattr(row, "keys") else str(row[1])
        n = int(row["n"]) if hasattr(row, "keys") else int(row[2])
        existing = out.get(sig, FeedbackCounts())
        if kind == KIND_DIDNT_WORK:
            out[sig] = FeedbackCounts(
                didnt_work=existing.didnt_work + n,
                loved_it=existing.loved_it,
                dismissed=existing.dismissed,
            )
        elif kind == KIND_LOVED_IT:
            out[sig] = FeedbackCounts(
                didnt_work=existing.didnt_work,
                loved_it=existing.loved_it + n,
                dismissed=existing.dismissed,
            )
        elif kind == KIND_DISMISSED_PRE_APPROVAL:
            out[sig] = FeedbackCounts(
                didnt_work=existing.didnt_work,
                loved_it=existing.loved_it,
                dismissed=existing.dismissed + n,
            )
        # Unknown kinds: ignore — see docstring.
    return out


def rank_candidates(
    candidates: Sequence[Candidate],
    counts: dict[str, FeedbackCounts],
) -> list[tuple[Candidate, float, bool]]:
    """Annotate each candidate with (weight, blocked) for selection.

    The caller (``consult_and_select``) interprets ``blocked=True`` as
    "drop unless all are blocked". The weight is the soft-signal
    score: higher = more likely to be picked. A candidate with no
    feedback rows has weight ``0.0`` and is unblocked, so unseen
    activities are competitive against weakly-loved ones (the
    ``+0.5`` for one ``loved_it``) and beat weakly-dismissed ones
    (the ``-0.3`` for one ``dismissed_pre_approval``).
    """
    ranked: list[tuple[Candidate, float, bool]] = []
    for c in candidates:
        fc = counts.get(c.signature, FeedbackCounts())
        ranked.append((c, fc.weight(), fc.is_blocked()))
    return ranked


def consult_and_select(
    candidates: Sequence[Candidate],
    conn: sqlite3.Connection | None,
    rng: random.Random,
) -> Candidate:
    """Pick one candidate after applying anti-signal feedback.

    Algorithm:

    1. If ``conn`` is ``None`` (test path / non-DB callers), pick
       uniformly with ``rng`` — exactly matching the Phase A
       behaviour.
    2. Fetch feedback counts for every candidate's signature.
    3. Rank candidates: unblocked ones first (sorted by weight DESC
       then ``template_id`` for determinism), blocked ones as a
       fallback pool.
    4. If at least one unblocked candidate exists, pick from the
       top-weight subset (all candidates tied at the maximum weight)
       with ``rng``. Otherwise log a WARNING and pick from the full
       list — refusing to suggest anything is worse than re-suggesting
       a vetoed activity to a child waiting for one.

    Returns the chosen :class:`Candidate`. Empty input raises
    :class:`ValueError` — the caller must not invoke this with no
    candidates (the generator's fallback chain already guarantees a
    non-empty pool before consultation runs).
    """
    if not candidates:
        raise ValueError("consult_and_select called with empty candidate list")
    # Sort defensively so determinism doesn't depend on caller order.
    sorted_candidates = sorted(candidates, key=lambda c: c.template_id)
    if conn is None:
        return rng.choice(sorted_candidates)

    try:
        counts = fetch_counts(conn, [c.signature for c in sorted_candidates])
    except sqlite3.Error as exc:
        # Feedback consultation is best-effort observability for the
        # generator's selection path. A DB blip must not break a
        # propose call — fall through to uniform pick. Log WARNING so
        # the failure is surfaced.
        _logger.warning("feedback consultation failed (%s); falling back to uniform pick", exc)
        return rng.choice(sorted_candidates)

    ranked = rank_candidates(sorted_candidates, counts)
    unblocked = [(c, w) for c, w, blocked in ranked if not blocked]

    if not unblocked:
        # All candidates have a ``didnt_work`` row. Degrade
        # gracefully: pick uniformly from the full pool. The
        # alternative (raise) would surface as a 500 to the parent
        # for a kid who is right now bored and waiting.
        _logger.warning(
            "all %d candidate templates are blocked by didnt_work feedback; "
            "degrading to uniform pick",
            len(sorted_candidates),
        )
        return rng.choice(sorted_candidates)

    # Pick from the top-weight tier. Equal-weight ties are broken by
    # ``template_id`` already (the ranked list inherits the sort), so
    # ``rng.choice`` over the tier is deterministic given the seed.
    max_weight = max(w for _, w in unblocked)
    top_tier = [c for c, w in unblocked if w == max_weight]
    return rng.choice(top_tier)


__all__ = [
    "ALL_FEEDBACK_KINDS",
    "KIND_DIDNT_WORK",
    "KIND_DISMISSED_PRE_APPROVAL",
    "KIND_LOVED_IT",
    "WEIGHT_DIDNT_WORK",
    "WEIGHT_DISMISSED",
    "WEIGHT_LOVED_IT",
    "Candidate",
    "FeedbackCounts",
    "compute_signature",
    "consult_and_select",
    "fetch_counts",
    "rank_candidates",
    "slot_fingerprint",
]
