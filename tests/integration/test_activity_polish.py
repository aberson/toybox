"""Step 23 activity polish: pause/resume idempotency + why-this telemetry.

Pins the new wire-shape additions (``trigger_phrase``,
``persona_reasoning``) on the activity response and the idempotent
behaviour of the pause/resume endpoints — a parent who clicks pause
twice gets two 200s back with the SAME version both times, so the
optimistic-concurrency cached version on the next mutation can't go
stale because of a quiet UX double-tap.

The trigger_phrase + persona_reasoning live in the existing
``activities.summary`` JSON envelope (no schema migration). The
``signature`` field that step 20 anti-signal feedback depends on stays
in scope alongside the new fields — see ``test_summary_envelope_keeps_step20_signature``.

Iter-2 additions:
  * Child WS payload PII strip (``test_child_ws_envelope_omits_pii``)
  * ``trigger_phrase`` length cap at 512 chars (``test_propose_*_too_long_is_422``)
  * Pause/resume from terminal states 409 (``test_pause_from_*_is_409``,
    ``test_resume_from_*_is_409``)
  * Idempotent pause race (``test_pause_idempotent_with_stale_version``)
  * labeled_events ChatML row coexists with new summary fields
    (``test_propose_with_why_telemetry_keeps_labeled_events_intact``)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.ws.topics import Topic

PROPOSE_BODY: dict[str, Any] = {
    "intent": "request_play",
    "slot": "unicorns",
    "hour": 12,
    "seed": 42,
}


def _propose(
    client: TestClient,
    headers: dict[str, str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = dict(PROPOSE_BODY)
    if extra is not None:
        body.update(extra)
    response = client.post(
        "/api/activities/propose",
        json=body,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


def _approve_and_advance_to_running(
    client: TestClient,
    headers: dict[str, str],
    activity_id: str,
) -> dict[str, Any]:
    """Walk an activity from proposed → approved → running and return the
    running response body."""
    approve = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**headers, "If-Match-Version": "1"},
    )
    assert approve.status_code == 200, approve.text
    advance = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**headers, "If-Match-Version": str(approve.json()["version"])},
    )
    assert advance.status_code == 200, advance.text
    assert advance.json()["state"] == "running"
    return cast("dict[str, Any]", advance.json())


# ---------------------------------------------------------------------------
# pause / resume idempotency
# ---------------------------------------------------------------------------


def test_pause_idempotent(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Calling pause when already paused is a 200 with no version bump."""
    activity = _propose(client, parent_headers)
    running = _approve_and_advance_to_running(client, parent_headers, activity["id"])
    first = client.post(
        f"/api/activities/{activity['id']}/pause",
        headers={**parent_headers, "If-Match-Version": str(running["version"])},
    )
    assert first.status_code == 200
    paused_version = first.json()["version"]

    second = client.post(
        f"/api/activities/{activity['id']}/pause",
        headers={**parent_headers, "If-Match-Version": str(paused_version)},
    )
    assert second.status_code == 200, second.text
    assert second.json()["state"] == "paused"
    # CRITICAL: no version bump on the no-op. Without this, a parent's
    # quick double-tap on pause would race the next mutation.
    assert second.json()["version"] == paused_version


def test_resume_idempotent(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Calling resume when already running is a 200 with no version bump."""
    activity = _propose(client, parent_headers)
    running = _approve_and_advance_to_running(client, parent_headers, activity["id"])

    second = client.post(
        f"/api/activities/{activity['id']}/resume",
        headers={**parent_headers, "If-Match-Version": str(running["version"])},
    )
    assert second.status_code == 200, second.text
    assert second.json()["state"] == "running"
    assert second.json()["version"] == running["version"]


def test_pause_from_proposed_is_409(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Pause is only legal from running. From proposed, return 409."""
    activity = _propose(client, parent_headers)
    response = client.post(
        f"/api/activities/{activity['id']}/pause",
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_transition"


def test_pause_from_ended_is_409(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Pause from a terminal state (ended) returns 409. Same shape as
    pause-from-proposed, but exercises the post-end branch — terminal
    states must NOT silently round-trip an idempotent pause."""
    activity = _propose(client, parent_headers)
    running = _approve_and_advance_to_running(client, parent_headers, activity["id"])
    end = client.post(
        f"/api/activities/{activity['id']}/end",
        headers={**parent_headers, "If-Match-Version": str(running["version"])},
    )
    assert end.status_code == 200, end.text
    response = client.post(
        f"/api/activities/{activity['id']}/pause",
        headers={**parent_headers, "If-Match-Version": str(end.json()["version"])},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_transition"


def test_pause_from_completed_is_409(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Pause from completed (the natural-end terminal state) returns 409."""
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve_and_advance_to_running(client, parent_headers, activity_id)
    # advance until completed (5 steps, first advance arrived us at running).
    while state["state"] == "running":
        adv = client.post(
            f"/api/activities/{activity_id}/advance",
            headers={**parent_headers, "If-Match-Version": str(state["version"])},
        )
        assert adv.status_code == 200, adv.text
        state = adv.json()
    assert state["state"] == "completed"
    response = client.post(
        f"/api/activities/{activity_id}/pause",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_transition"


def test_resume_from_ended_is_409(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Symmetric coverage: resume from a terminal state returns 409."""
    activity = _propose(client, parent_headers)
    running = _approve_and_advance_to_running(client, parent_headers, activity["id"])
    end = client.post(
        f"/api/activities/{activity['id']}/end",
        headers={**parent_headers, "If-Match-Version": str(running["version"])},
    )
    assert end.status_code == 200, end.text
    response = client.post(
        f"/api/activities/{activity['id']}/resume",
        headers={**parent_headers, "If-Match-Version": str(end.json()["version"])},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_transition"


def test_pause_idempotent_with_stale_version(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Iter-2 L2: a concurrent same-version double-tap on pause must
    still 200, not 409, because the target state is already reached.

    Simulates the race: parent panel cached version V (running). Two
    pause clicks fly out. The first wins, transitions to paused at
    V+1. The second arrives with stale ``If-Match-Version: V`` —
    state is already paused, so the idempotency contract requires a
    200, not the version-conflict 409 we'd get if the version check
    fired before the state check.
    """
    activity = _propose(client, parent_headers)
    running = _approve_and_advance_to_running(client, parent_headers, activity["id"])
    running_version = running["version"]

    first = client.post(
        f"/api/activities/{activity['id']}/pause",
        headers={**parent_headers, "If-Match-Version": str(running_version)},
    )
    assert first.status_code == 200
    paused_version = first.json()["version"]
    assert paused_version == running_version + 1

    # Second click with the STALE running version. State is already
    # paused — must idempotently 200, not 409.
    second = client.post(
        f"/api/activities/{activity['id']}/pause",
        headers={**parent_headers, "If-Match-Version": str(running_version)},
    )
    assert second.status_code == 200, second.text
    assert second.json()["state"] == "paused"
    assert second.json()["version"] == paused_version


# ---------------------------------------------------------------------------
# Iter-2 length cap on trigger_phrase + persona_reasoning
# ---------------------------------------------------------------------------


def test_propose_trigger_phrase_too_long_is_422(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Iter-2 M2: trigger_phrase > 512 chars is rejected by Pydantic
    validation — keeps a 10K-char transcript substring out of the row
    summary + the WS envelope."""
    body = dict(PROPOSE_BODY)
    body["trigger_phrase"] = "x" * 513
    response = client.post(
        "/api/activities/propose",
        json=body,
        headers=parent_headers,
    )
    assert response.status_code == 422, response.text


def test_propose_persona_reasoning_too_long_is_422(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Same 512-char cap applies to persona_reasoning."""
    body = dict(PROPOSE_BODY)
    body["persona_reasoning"] = "x" * 513
    response = client.post(
        "/api/activities/propose",
        json=body,
        headers=parent_headers,
    )
    assert response.status_code == 422, response.text


def test_propose_trigger_phrase_at_cap_is_accepted(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """The boundary value (512 chars exactly) is accepted — confirms the
    cap is inclusive."""
    body = dict(PROPOSE_BODY)
    body["trigger_phrase"] = "x" * 512
    response = client.post(
        "/api/activities/propose",
        json=body,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# Iter-2 M1: child-kiosk WS payload must NOT contain trigger_phrase
# or persona_reasoning. The REST GET path (parent-only) DOES still
# carry them; the test_propose_captures_trigger_phrase covers that.
# ---------------------------------------------------------------------------


def _payload_has_key_anywhere(node: object, key: str) -> bool:
    """Recursive walk -- mirrors scripts/uat/ws_inspect.py so any caller
    that adds a new copy of a stripped key (e.g. inside ``metadata``)
    fails the assertion. Top-level-only checks miss the metadata leak
    that the M2.5.5 UAT surfaced.
    """
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_payload_has_key_anywhere(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_payload_has_key_anywhere(item, key) for item in node)
    return False


def test_child_ws_envelope_omits_pii(
    client: TestClient,
    parent_headers: dict[str, str],
    pubsub: PubSub,
) -> None:
    """The ``activity.state`` envelope published to the kid-kiosk WS
    topic MUST NOT include ``trigger_phrase`` (a literal substring of a
    child-spoken transcript) or ``persona_reasoning`` (a parent-facing
    rationale string). Both are stripped server-side in ``_emit_state``;
    the kid kiosk subscribes to the topic for state ticks but never
    needs to render either field.

    Both keys also live nested under ``metadata`` (Step 23 "why this?"
    telemetry persistence), so the assertion walks the whole tree --
    a top-level membership check would miss the metadata copy.
    """
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        body = _propose(
            client,
            parent_headers,
            extra={
                "trigger_phrase": "let's play unicorns",
                "persona_reasoning": "matched on child interest",
            },
        )
        envelope = sub.get_nowait()
        assert envelope.topic is Topic.activity_state
        assert envelope.payload["id"] == body["id"]
        # CRITICAL: NEITHER field appears anywhere in the envelope tree --
        # not at top-level, not nested in metadata.
        assert not _payload_has_key_anywhere(envelope.payload, "trigger_phrase")
        assert not _payload_has_key_anywhere(envelope.payload, "persona_reasoning")
    finally:
        sub.close()


def test_parent_rest_response_still_carries_pii(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Mirror test for the parent path: the GET response (parent-only
    scope) DOES still surface trigger_phrase + persona_reasoning. This
    is the load-bearing case for the parent UI's "why this?" panel."""
    body = _propose(
        client,
        parent_headers,
        extra={
            "trigger_phrase": "let's play unicorns",
            "persona_reasoning": "matched on child interest",
        },
    )
    fetched = client.get(
        f"/api/activities/{body['id']}",
        headers=parent_headers,
    ).json()
    assert fetched["trigger_phrase"] == "let's play unicorns"
    assert fetched["persona_reasoning"] == "matched on child interest"


# ---------------------------------------------------------------------------
# regenerate semantics: the source row keeps its activity_id (preserved as
# dismissed), and the new fresh proposal carries forward the why-telemetry.
# Step 23 spec note: the existing "skip & try another" semantics produce a
# new activity_id by design (Phase D step 20 anti-signal feedback flows
# through the dismiss transition on the source row). The source's
# activity_id is preserved across the regenerate flow — only its STATE
# changes.
# ---------------------------------------------------------------------------


def test_regenerate_preserves_source_activity_id(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """The source activity row keeps the SAME id across a regenerate
    cycle — only its state transitions to ``dismissed`` so step 20's
    anti-signal feedback writes against a stable signature."""
    activity = _propose(client, parent_headers)
    aid = activity["id"]
    response = client.post(
        f"/api/activities/{aid}/regenerate",
        json={"intent": "request_play", "hour": 12, "seed": 99},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert response.status_code == 200
    # Source row preserved its id — fetched directly to verify.
    source = client.get(f"/api/activities/{aid}", headers=parent_headers).json()
    assert source["id"] == aid
    assert source["state"] == "dismissed"


def test_regenerate_bumps_version(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """The source row's version bumps when the regenerate flow dismisses it."""
    activity = _propose(client, parent_headers)
    aid = activity["id"]
    initial_version = activity["version"]
    response = client.post(
        f"/api/activities/{aid}/regenerate",
        json={"intent": "request_play", "hour": 12, "seed": 99},
        headers={**parent_headers, "If-Match-Version": str(initial_version)},
    )
    assert response.status_code == 200
    source = client.get(f"/api/activities/{aid}", headers=parent_headers).json()
    assert source["version"] == initial_version + 1


# ---------------------------------------------------------------------------
# trigger_phrase + persona_reasoning capture
# ---------------------------------------------------------------------------


def test_propose_captures_trigger_phrase(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    body = _propose(
        client,
        parent_headers,
        extra={"trigger_phrase": "let's play unicorns"},
    )
    assert body["trigger_phrase"] == "let's play unicorns"

    # Read-back via GET surfaces the same value (so the WS poll path
    # sees it too — not just the propose response).
    fetched = client.get(
        f"/api/activities/{body['id']}",
        headers=parent_headers,
    ).json()
    assert fetched["trigger_phrase"] == "let's play unicorns"


def test_propose_emits_persona_reasoning_default(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Even without a caller-supplied reasoning, the field is populated —
    the synthesised default beats an empty panel."""
    body = _propose(client, parent_headers)
    assert isinstance(body["persona_reasoning"], str)
    assert body["persona_reasoning"]  # non-empty


def test_propose_persona_reasoning_caller_wins(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """A caller-supplied reasoning is persisted verbatim (after strip)."""
    body = _propose(
        client,
        parent_headers,
        extra={"persona_reasoning": "  matched on child interest in unicorns  "},
    )
    assert body["persona_reasoning"] == "matched on child interest in unicorns"


def test_summary_envelope_keeps_step20_signature(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Adding trigger_phrase + persona_reasoning to the summary metadata
    must NOT displace the step 20 ``signature`` field — Phase D
    anti-signal feedback writes hash on it."""
    body = _propose(
        client,
        parent_headers,
        extra={"trigger_phrase": "let's play unicorns"},
    )
    metadata = body["metadata"]
    # signature is computed by generate(); it MUST still be present.
    assert "signature" in metadata
    assert isinstance(metadata["signature"], str)
    assert metadata["signature"]


def test_regenerate_inherits_trigger_phrase(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """A "skip & try another" cycle keeps the same why-telemetry on the
    fresh proposal — the trigger phrase that started the original is
    still the why for the follow-up."""
    activity = _propose(
        client,
        parent_headers,
        extra={"trigger_phrase": "let's play unicorns"},
    )
    response = client.post(
        f"/api/activities/{activity['id']}/regenerate",
        json={"intent": "request_play", "hour": 12, "seed": 99},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert response.status_code == 200
    new = response.json()
    assert new["trigger_phrase"] == "let's play unicorns"
    # The regenerate inherits the source's reasoning by default too.
    assert new["persona_reasoning"]


# ---------------------------------------------------------------------------
# Iter-2 H2: a propose that supplies the new ``trigger_phrase`` +
# ``persona_reasoning`` fields must NOT regress the step-15
# labeled_events writer — its row + ChatML payload still land, AND the
# activity's summary JSON still carries step-20's ``signature`` field.
# ---------------------------------------------------------------------------


def test_propose_with_why_telemetry_keeps_labeled_events_intact(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Iter-2 H2: end-to-end pin that the new summary envelope additions
    coexist with step 15's labeled_events row + step 20's signature.

    1. Proposes with both ``trigger_phrase`` AND ``persona_reasoning``.
    2. Reads the labeled_events row directly (round-trip the writer).
    3. Confirms ``inputs_chatml_json`` is non-empty + parses as a list
       starting with a system role (the step-15 contract).
    4. Confirms the activity's summary JSON metadata still has
       ``signature`` (step-20) AND the new ``trigger_phrase`` +
       ``persona_reasoning`` (step-23). All three live side-by-side.
    """
    body = _propose(
        client,
        parent_headers,
        extra={
            "trigger_phrase": "let's play unicorns",
            "persona_reasoning": "matched on child interest in unicorns",
        },
    )
    activity_id = body["id"]

    # 2-3. labeled_events row write + ChatML intactness.
    conn: sqlite3.Connection = connect(db_path)
    try:
        row = conn.execute(
            "SELECT inputs_chatml_json, activity_json, generator_path "
            "FROM labeled_events WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "propose must still write a labeled_events row"
    assert row["generator_path"] == "offline"
    assert row["inputs_chatml_json"], "ChatML payload must not be empty"
    chatml = json.loads(row["inputs_chatml_json"])
    assert isinstance(chatml, list)
    assert len(chatml) >= 2
    assert chatml[0]["role"] == "system"
    assert chatml[1]["role"] == "user"

    # 4. Summary envelope metadata holds all three fields together.
    metadata = body["metadata"]
    assert "signature" in metadata
    assert isinstance(metadata["signature"], str) and metadata["signature"]
    assert metadata.get("trigger_phrase") == "let's play unicorns"
    assert metadata.get("persona_reasoning") == "matched on child interest in unicorns"
