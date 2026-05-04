"""Phase D step 20: anti-signal feedback consultation in the generator.

Coverage matrix (per GitHub issue #25 done-when):

* :func:`compute_signature` is deterministic, sort-stable, and matches
  the documented ``sha256("{template_id}:{sorted slot k=v}")`` formula
  byte-for-byte.
* The generator emits the signature on ``Activity.metadata["signature"]``.
* Predicted signature for a template (via the consultation pre-pick)
  matches the post-pick emitted signature — the load-bearing
  invariant for end-to-end matching.
* ``didnt_work`` row vetoes a candidate (re-pick happens).
* ``loved_it`` row boosts a candidate (selected over an equivalent
  unflagged one when both are eligible at the same hour).
* ``dismissed_pre_approval`` row reduces ranking by less than
  ``didnt_work`` would (still pickable; loses to ``loved_it``).
* Empty feedback table: behaves like the Phase A picker.
* Multiple feedback rows for the same signature stack their weight.
* Malformed / unknown ``kind`` rows are inert (don't crash, don't
  match).
* All-blocked degradation: if every candidate is ``didnt_work``,
  the picker still returns one (with a logged warning) — refusing
  to suggest anything is worse than re-suggesting a vetoed
  activity to a kid who is right now bored and waiting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from toybox.activities import generate
from toybox.activities.feedback import (
    KIND_DIDNT_WORK,
    KIND_DISMISSED_PRE_APPROVAL,
    KIND_LOVED_IT,
    Candidate,
    FeedbackCounts,
    compute_signature,
    consult_and_select,
    fetch_counts,
    slot_fingerprint,
)
from toybox.activities.generator import (
    _load_intent_templates,
    _preview_slot_values,
    clear_template_cache,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    clear_template_cache()
    yield
    clear_template_cache()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Per-test SQLite connection with the migrated schema."""
    path = tmp_path / "fb.db"
    conn = connect(path)
    run_migrations(conn)
    # Seed a session + activity so the FK-bearing feedback inserts
    # below don't trip the schema. The activity row is a stub; the
    # generator under test never reads it back.
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            ("s1", "2026-05-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            ("a1", "s1", "proposed", "2026-05-01T00:00:00Z"),
        )
    try:
        yield conn
    finally:
        conn.close()


def _insert_feedback(
    conn: sqlite3.Connection,
    *,
    signature: str,
    kind: str,
    activity_id: str = "a1",
    reason: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO feedback "
            "(id, activity_id, step_seq, kind, signature, reason, created_at) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?)",
            (str(uuid.uuid4()), activity_id, kind, signature, reason, "2026-05-01T00:00:00Z"),
        )


# ---------------------------------------------------------------------------
# Signature computation
# ---------------------------------------------------------------------------


def test_signature_matches_documented_formula() -> None:
    """The signature MUST be ``sha256("{template_id}:{sorted slot k=v}")``."""
    sig = compute_signature("tpl_x", ["unicorns"])
    expected = hashlib.sha256(b"tpl_x:slot=unicorns").hexdigest()
    assert sig == expected


def test_signature_is_sort_stable() -> None:
    """Permuted slot values produce the same signature."""
    a = compute_signature("tpl_x", ["unicorns", "dragons"])
    b = compute_signature("tpl_x", ["dragons", "unicorns"])
    c = compute_signature("tpl_x", ["dragons", "dragons", "unicorns"])  # dedup
    assert a == b == c


def test_signature_empty_slot_values() -> None:
    sig = compute_signature("tpl_x", [])
    expected = hashlib.sha256(b"tpl_x:").hexdigest()
    assert sig == expected


def test_slot_fingerprint_sorted_and_deduped() -> None:
    assert slot_fingerprint(["b", "a", "a"]) == "slot=a,slot=b"
    assert slot_fingerprint([]) == ""
    assert slot_fingerprint(["x"]) == "slot=x"


# ---------------------------------------------------------------------------
# Generator emits signature on metadata
# ---------------------------------------------------------------------------


def test_generator_emits_signature_on_metadata() -> None:
    a = generate("request_play", "unicorns", None, 10, 42)
    assert "signature" in a.metadata
    expected = compute_signature(a.template_id, a.metadata["slot_values"])
    assert a.metadata["signature"] == expected


def test_generator_signature_stable_across_runs() -> None:
    a = generate("request_play", "unicorns", None, 10, 42)
    b = generate("request_play", "unicorns", None, 10, 42)
    assert a.metadata["signature"] == b.metadata["signature"]


# ---------------------------------------------------------------------------
# Pre-pick preview matches post-pick emitted signature
# ---------------------------------------------------------------------------


def test_preview_matches_post_substitution() -> None:
    """End-to-end parity: the signature predicted from
    ``_preview_slot_values`` (what consultation keys off) MUST equal
    the signature actually emitted on ``Activity.metadata`` (what
    feedback rows persist against). Drift between the predicate and
    the substitution path silently breaks feedback matching, so this
    test exercises the REAL ``generate(...)`` call rather than
    re-implementing the predicate.
    """
    cases = [
        ("request_play", "unicorns", 10, 0),
        ("request_play", "unicorns", 10, 7),
        ("request_play", None, 14, 1),
        ("request_play", "", 9, 2),
        ("request_story", "dragons", 19, 3),
        ("request_story", None, 20, 4),
        ("request_activity", "rockets", 13, 5),
        ("request_activity", "", 11, 6),
        ("boredom", "puzzles", 15, 8),
        ("boredom", None, 8, 9),
        ("boredom", "robots", 16, 11),
    ]
    for intent, slot, hour, seed in cases:
        a = generate(intent, slot, None, hour, seed)
        # Look up the chosen template object via metadata.template_id +
        # the registry. The chosen intent may have fallen back to
        # boredom; try the requested intent first, then boredom.
        chosen = None
        for candidate_intent in (intent, "boredom"):
            for t in _load_intent_templates(candidate_intent):
                if t.id == a.template_id:
                    chosen = t
                    break
            if chosen is not None:
                break
        assert chosen is not None, (
            f"could not locate template {a.template_id!r} for intent={intent!r}"
        )
        # No available_toys passed to generate(...) above → toy =
        # DEFAULT_TOY_NAME, which means {toy} is NOT included in the
        # signature (preview matches the empty-catalog substitution).
        from toybox.activities.generator import DEFAULT_TOY_NAME  # noqa: PLC0415

        predicted_sv = _preview_slot_values(chosen, slot=slot, toy=DEFAULT_TOY_NAME)
        predicted_sig = compute_signature(a.template_id, predicted_sv)
        assert predicted_sig == a.metadata["signature"], (
            f"signature drift for intent={intent!r} slot={slot!r} seed={seed}: "
            f"predicted={predicted_sig} emitted={a.metadata['signature']}"
        )


# ---------------------------------------------------------------------------
# fetch_counts: empty / single / mixed
# ---------------------------------------------------------------------------


def test_fetch_counts_empty_table(db: sqlite3.Connection) -> None:
    assert fetch_counts(db, ["sig-a"]) == {}


def test_fetch_counts_no_signatures_short_circuits(db: sqlite3.Connection) -> None:
    # Even if rows exist, an empty signature list returns empty
    # without a query.
    _insert_feedback(db, signature="sig-a", kind=KIND_LOVED_IT)
    assert fetch_counts(db, []) == {}


def test_fetch_counts_aggregates_by_kind(db: sqlite3.Connection) -> None:
    _insert_feedback(db, signature="sig-a", kind=KIND_LOVED_IT)
    _insert_feedback(db, signature="sig-a", kind=KIND_LOVED_IT)
    _insert_feedback(db, signature="sig-a", kind=KIND_DIDNT_WORK)
    _insert_feedback(db, signature="sig-b", kind=KIND_DISMISSED_PRE_APPROVAL)
    counts = fetch_counts(db, ["sig-a", "sig-b", "sig-missing"])
    assert counts["sig-a"] == FeedbackCounts(didnt_work=1, loved_it=2, dismissed=0)
    assert counts["sig-b"] == FeedbackCounts(didnt_work=0, loved_it=0, dismissed=1)
    assert "sig-missing" not in counts


def test_fetch_counts_ignores_unknown_kind(db: sqlite3.Connection) -> None:
    """Unknown kind values (e.g. legacy rows or hand-inserted bad data)
    are silently dropped — they don't match a candidate's hash and
    are inert by construction."""
    _insert_feedback(db, signature="sig-a", kind="something_else")
    counts = fetch_counts(db, ["sig-a"])
    assert "sig-a" not in counts


# ---------------------------------------------------------------------------
# consult_and_select: the load-bearing decision
# ---------------------------------------------------------------------------


def _make_candidates(n: int) -> list[Candidate]:
    return [Candidate(template_id=f"t{i}", signature=f"sig-{i}") for i in range(n)]


def test_consult_select_no_conn_uniform_pick() -> None:
    """Phase A behaviour preserved when conn is None."""
    rng = random.Random(0)
    cands = _make_candidates(3)
    chosen = consult_and_select(cands, None, rng)
    assert chosen.template_id in {c.template_id for c in cands}


def test_consult_select_didnt_work_vetoes(db: sqlite3.Connection) -> None:
    """A ``didnt_work`` row drops that candidate entirely (re-pick)."""
    cands = _make_candidates(3)
    _insert_feedback(db, signature="sig-1", kind=KIND_DIDNT_WORK)
    # Across many seeds, candidate t1 must NEVER be picked.
    for seed in range(50):
        chosen = consult_and_select(cands, db, random.Random(seed))
        assert chosen.template_id != "t1"


def test_consult_select_loved_it_boosts(db: sqlite3.Connection) -> None:
    """A ``loved_it`` row biases the picker toward that candidate.

    The picker is weighted-random (post-bug-fix), not top-tier-only —
    a loved candidate gets a higher pick probability but does NOT
    lock out alternatives. Across many seeds: the loved candidate
    must be the most-picked, AND every other candidate must still be
    picked at least once (i.e., not locked out).
    """
    cands = _make_candidates(3)
    _insert_feedback(db, signature="sig-2", kind=KIND_LOVED_IT)
    counts = {"t0": 0, "t1": 0, "t2": 0}
    for seed in range(200):
        chosen = consult_and_select(cands, db, random.Random(seed))
        counts[chosen.template_id] += 1
    assert counts["t2"] > counts["t0"], counts
    assert counts["t2"] > counts["t1"], counts
    # Non-loved candidates still appear — bias, not lock-in.
    assert counts["t0"] > 0, counts
    assert counts["t1"] > 0, counts


def test_consult_select_dismissed_is_softer_than_didnt_work(db: sqlite3.Connection) -> None:
    """Dismissed-pre-approval reduces ranking but doesn't veto.

    Setup: t0 has nothing, t1 has dismissed_pre_approval, t2 has
    didnt_work. Across seeds:
      - t2 must NEVER be picked (didnt_work veto)
      - t1 CAN still be picked (soft signal, just lower weight) but
        must be picked less often than t0 across many seeds.
    """
    cands = _make_candidates(3)
    _insert_feedback(db, signature="sig-1", kind=KIND_DISMISSED_PRE_APPROVAL)
    _insert_feedback(db, signature="sig-2", kind=KIND_DIDNT_WORK)
    counts = {"t0": 0, "t1": 0, "t2": 0}
    for seed in range(200):
        chosen = consult_and_select(cands, db, random.Random(seed))
        counts[chosen.template_id] += 1
    assert counts["t2"] == 0, counts  # didnt_work veto is absolute
    assert counts["t1"] < counts["t0"], counts  # dismissed is softer
    assert counts["t1"] > 0, counts  # but not locked out


def test_consult_select_loved_it_beats_dismissed(db: sqlite3.Connection) -> None:
    """``loved_it`` (positive) outranks ``dismissed_pre_approval`` (negative)
    across many seeds, though weighted-random allows occasional
    crossover."""
    cands = _make_candidates(2)
    _insert_feedback(db, signature="sig-0", kind=KIND_DISMISSED_PRE_APPROVAL)
    _insert_feedback(db, signature="sig-1", kind=KIND_LOVED_IT)
    counts = {"t0": 0, "t1": 0}
    for seed in range(200):
        chosen = consult_and_select(cands, db, random.Random(seed))
        counts[chosen.template_id] += 1
    assert counts["t1"] > counts["t0"], counts


def test_consult_select_multiple_loved_it_stack(db: sqlite3.Connection) -> None:
    """Two loved_it rows for sig-0 raise its weighted-pick probability
    above one loved_it for sig-1 — both still appear, but sig-0 wins
    more often."""
    cands = _make_candidates(2)
    _insert_feedback(db, signature="sig-0", kind=KIND_LOVED_IT)
    _insert_feedback(db, signature="sig-0", kind=KIND_LOVED_IT)
    _insert_feedback(db, signature="sig-1", kind=KIND_LOVED_IT)
    counts = {"t0": 0, "t1": 0}
    for seed in range(200):
        chosen = consult_and_select(cands, db, random.Random(seed))
        counts[chosen.template_id] += 1
    assert counts["t0"] > counts["t1"], counts
    assert counts["t1"] > 0, counts


def test_consult_select_all_blocked_degrades_gracefully(
    db: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """If every candidate has ``didnt_work``, the picker still returns
    one (with a logged warning). Refusing to pick at all would
    surface as a 500 to a parent for a kid who is bored right now."""
    cands = _make_candidates(2)
    _insert_feedback(db, signature="sig-0", kind=KIND_DIDNT_WORK)
    _insert_feedback(db, signature="sig-1", kind=KIND_DIDNT_WORK)
    with caplog.at_level(logging.WARNING):
        chosen = consult_and_select(cands, db, random.Random(0))
    assert chosen.template_id in {"t0", "t1"}
    assert any("blocked" in r.message for r in caplog.records)


def test_consult_select_empty_raises() -> None:
    with pytest.raises(ValueError):
        consult_and_select([], None, random.Random(0))


def test_consult_select_db_error_falls_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A sqlite blip during consultation must not raise — degrade to
    uniform pick. We force the error by passing a closed connection."""
    path = tmp_path / "x.db"
    conn = connect(path)
    run_migrations(conn)
    conn.close()
    cands = _make_candidates(2)
    with caplog.at_level(logging.WARNING):
        chosen = consult_and_select(cands, conn, random.Random(0))
    assert chosen.template_id in {"t0", "t1"}
    assert any("feedback consultation failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# End-to-end: generate() with conn applies feedback
# ---------------------------------------------------------------------------


def test_generate_with_conn_respects_didnt_work(db: sqlite3.Connection) -> None:
    """Insert a ``didnt_work`` row matching one specific candidate's
    signature, then generate. The chosen activity's signature must
    NOT equal the vetoed one.

    We use a baseline no-conn run to discover an actually-eligible
    candidate's signature before vetoing it. Vetoing a non-eligible
    signature would make the test vacuously pass.
    """
    intent = "request_play"
    slot = "unicorns"
    hour = 10
    # Discover an eligible candidate via baseline no-conn pick, then
    # veto it. To avoid vacuous pass, we also collect a second
    # eligible signature (different seed picks a different template)
    # so we can confirm the picker re-routes to a real alternative.
    eligible_sigs: set[str] = set()
    for seed in range(50):
        a = generate(intent, slot, None, hour, seed)
        eligible_sigs.add(a.metadata["signature"])
        if len(eligible_sigs) >= 2:
            break
    assert len(eligible_sigs) >= 2, "test needs ≥2 distinct eligible signatures"
    vetoed = next(iter(eligible_sigs))
    _insert_feedback(db, signature=vetoed, kind=KIND_DIDNT_WORK)

    # Across many seeds, the emitted signature must never be the vetoed one.
    for seed in range(40):
        a = generate(intent, slot, None, hour, seed, conn=db)
        assert a.metadata["signature"] != vetoed, f"seed={seed}: generator picked vetoed signature"


def test_generate_without_conn_unaffected_by_feedback(db: sqlite3.Connection) -> None:
    """No conn → Phase A behaviour. Feedback rows must have zero effect."""
    a_seeded = generate("request_play", "unicorns", None, 10, 42)
    # Veto whatever the seed-42 pick was; without conn the result must
    # be byte-identical.
    _insert_feedback(db, signature=a_seeded.metadata["signature"], kind=KIND_DIDNT_WORK)
    a_again = generate("request_play", "unicorns", None, 10, 42)
    assert a_again == a_seeded


def test_generate_with_conn_loved_it_attracts_pick(db: sqlite3.Connection) -> None:
    """Insert ``loved_it`` for one specific candidate's signature.
    The generator should converge on that candidate across seeds.

    We need to pick a target that's actually eligible at the test
    hour — otherwise the picker never considers it and the loved-it
    boost has no effect to assert on. Run one no-conn baseline
    generate to discover an eligible signature, then loved_it that.
    """
    intent = "request_play"
    slot = "unicorns"
    hour = 10
    # Discover an eligible candidate by sampling one baseline pick.
    baseline = generate(intent, slot, None, hour, 0)
    target_sig = baseline.metadata["signature"]
    target_template_id = baseline.template_id
    _insert_feedback(db, signature=target_sig, kind=KIND_LOVED_IT)

    # The loved candidate must be the most-picked template across many
    # seeds, but the picker is weighted-random (post-bug-fix), so other
    # eligible templates still appear. With one loved_it the boost is
    # +0.5 on a 1.0 baseline, so the loved candidate gets 1.5/(1.5+N)
    # of the pool — modal but not majority for N>=2.
    template_counts: dict[str, int] = {}
    for seed in range(200):
        a = generate(intent, slot, None, hour, seed, conn=db)
        template_counts[a.template_id] = template_counts.get(a.template_id, 0) + 1
    # Loved template must be the modal pick.
    most_picked = max(template_counts, key=lambda k: template_counts[k])
    assert most_picked == target_template_id, template_counts
    # And it must be picked more often than the uniform-baseline rate
    # (1/N for N eligible templates) — i.e., the boost actually fired.
    target_n = template_counts[target_template_id]
    uniform_rate = 200 / len(template_counts)
    assert target_n > uniform_rate, template_counts


# ---------------------------------------------------------------------------
# API integration: dismiss / thumbs-up / didnt-work write feedback rows
# ---------------------------------------------------------------------------


def _propose(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    r = client.post(
        "/api/activities/propose",
        json={
            "intent": "request_play",
            "slot": "unicorns",
            "hour": 10,
            "seed": 7,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    payload = r.json()
    assert isinstance(payload, dict)
    return payload


def _read_feedback(db_path: Path) -> list[sqlite3.Row]:
    conn = connect(db_path)
    try:
        rows = list(conn.execute("SELECT * FROM feedback ORDER BY created_at"))
    finally:
        conn.close()
    return rows


def test_propose_persists_signature_in_summary(
    client: TestClient, parent_headers: dict[str, str], db_path: Path
) -> None:
    """The ``activities.summary`` JSON envelope must round-trip the
    signature so the feedback-write helpers can read it back."""
    activity = _propose(client, parent_headers)
    sig = activity["metadata"]["signature"]
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT summary FROM activities WHERE id = ?",
            (activity["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    payload = json.loads(row["summary"])
    assert payload["metadata"]["signature"] == sig


def test_dismiss_pre_approval_writes_feedback_row(
    client: TestClient, parent_headers: dict[str, str], db_path: Path
) -> None:
    activity = _propose(client, parent_headers)
    sig = activity["metadata"]["signature"]
    r = client.post(
        f"/api/activities/{activity['id']}/dismiss",
        headers={**parent_headers, "If-Match-Version": str(activity["version"])},
    )
    assert r.status_code == 200, r.text
    rows = _read_feedback(db_path)
    assert len(rows) == 1
    assert rows[0]["kind"] == KIND_DISMISSED_PRE_APPROVAL
    assert rows[0]["signature"] == sig


def test_thumbs_up_writes_feedback_row(
    client: TestClient, parent_headers: dict[str, str], db_path: Path
) -> None:
    activity = _propose(client, parent_headers)
    sig = activity["metadata"]["signature"]
    r = client.post(f"/api/activities/{activity['id']}/thumbs-up", headers=parent_headers)
    assert r.status_code == 200, r.text
    rows = _read_feedback(db_path)
    assert len(rows) == 1
    assert rows[0]["kind"] == KIND_LOVED_IT
    assert rows[0]["signature"] == sig


def test_didnt_work_writes_feedback_row_even_without_reason(
    client: TestClient, parent_headers: dict[str, str], db_path: Path
) -> None:
    """Pre-step-20 the row was only written when ``reason`` was set;
    we now always write so the anti-signal lands even on silent
    button presses."""
    activity = _propose(client, parent_headers)
    sig = activity["metadata"]["signature"]
    # Approve → advance (= running) → didnt_work — the state machine
    # disallows ``approved → didnt_work`` directly.
    r = client.post(
        f"/api/activities/{activity['id']}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": str(activity["version"])},
    )
    assert r.status_code == 200, r.text
    bumped = r.json()["version"]
    r = client.post(
        f"/api/activities/{activity['id']}/advance",
        headers={**parent_headers, "If-Match-Version": str(bumped)},
    )
    assert r.status_code == 200, r.text
    bumped = r.json()["version"]
    r = client.post(
        f"/api/activities/{activity['id']}/didnt-work",
        json={},
        headers={**parent_headers, "If-Match-Version": str(bumped)},
    )
    assert r.status_code == 200, r.text
    rows = _read_feedback(db_path)
    assert len(rows) == 1
    assert rows[0]["kind"] == KIND_DIDNT_WORK
    assert rows[0]["signature"] == sig
    assert rows[0]["reason"] is None


def test_dismiss_after_approval_does_not_write_feedback(
    client: TestClient, parent_headers: dict[str, str], db_path: Path
) -> None:
    """Soft anti-signal is for *pre*-approval dismiss only. Once the
    parent has approved, dismiss is no longer a "haven't even
    tried it" signal — the parent has the harder ``didnt-work`` path
    available for that."""
    activity = _propose(client, parent_headers)
    r = client.post(
        f"/api/activities/{activity['id']}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": str(activity["version"])},
    )
    assert r.status_code == 200
    bumped = r.json()["version"]
    r = client.post(
        f"/api/activities/{activity['id']}/dismiss",
        headers={**parent_headers, "If-Match-Version": str(bumped)},
    )
    assert r.status_code == 200
    assert _read_feedback(db_path) == []
