"""Phase W Step W4 — dynamic adventure engine integration tests.

These exercise the FULL producer→consumer round trip (code-quality.md §4)
through the REAL propose → advance handlers (NO direct-DB step injection):

* propose ``{adventure: true}`` (nonlinear) seeds beat 1 with
  kind="adventure_beat" + choices; advancing ≥3 times generates fresh
  ``adventure_beat`` steps and eventually reaches the terminal/reward path.
* linear adventures emit beats with no choices.
* with the capability gate stubbed GREEN, the Claude path is invoked (the
  injected sync client is called); stubbed not-green → offline fallback (no
  client call).
* a normal (non-adventure) propose/advance is unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.activities.adventure import MAX_ADVENTURE_BEATS, _theme_for
from toybox.api.activities import _adventure_seed_from_id, get_sync_ai_client
from toybox.core.game_linearity import set_game_linearity
from toybox.db.connection import connect

MAX_ADVANCE_LOOPS = 12


# ---------------------------------------------------------------------------
# Helpers — propose / approve / advance through the production handlers.
# ---------------------------------------------------------------------------


def _set_linearity(db_path: Path, value: str) -> None:
    conn = connect(db_path, check_same_thread=False)
    try:
        set_game_linearity(conn, value)
    finally:
        conn.close()


def _propose_adventure(
    client: TestClient, parent_headers: dict[str, str], *, seed: int = 99
) -> dict[str, Any]:
    resp = client.post(
        "/api/activities/propose",
        json={
            "intent": "request_play",
            "slot": "freeplay",
            "hour": 12,
            "seed": seed,
            "adventure": True,
        },
        headers=parent_headers,
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _propose_normal(
    client: TestClient, parent_headers: dict[str, str], *, seed: int = 42
) -> dict[str, Any]:
    resp = client.post(
        "/api/activities/propose",
        json={"intent": "request_play", "slot": "unicorns", "hour": 12, "seed": seed},
        headers=parent_headers,
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _approve(
    client: TestClient, parent_headers: dict[str, str], activity_id: str, version: int
) -> dict[str, Any]:
    resp = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert resp.status_code == 200, resp.text
    return cast("dict[str, Any]", resp.json())


def _advance(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
    *,
    choice_index: int | None = None,
    expected_status: int = 200,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if choice_index is not None:
        body["choice_index"] = choice_index
    resp = client.post(
        f"/api/activities/{activity_id}/advance",
        json=body,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert resp.status_code == expected_status, resp.text
    return cast("dict[str, Any]", resp.json())


def _current_step(state: dict[str, Any]) -> dict[str, Any] | None:
    for step in state.get("steps", []):
        if step.get("current"):
            return cast("dict[str, Any]", step)
    return None


def _count_beat_rows(db_path: Path, activity_id: str) -> int:
    """Count persisted ``adventure_beat`` step rows for an activity."""
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM activity_steps "
            "WHERE activity_id = ? AND kind = 'adventure_beat'",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


# ---------------------------------------------------------------------------
# Fixtures — offline client + green/not-green stubbed clients.
# ---------------------------------------------------------------------------


@pytest.fixture
def offline_client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient with the sync AI client pinned to None (offline path)."""
    app.dependency_overrides[get_sync_ai_client] = lambda: None
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_sync_ai_client, None)


class _StubSyncClient:
    """Minimal SyncAIClient stub returning a canned adventure beat."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_text_sync(self, messages: Any, **kwargs: Any) -> Any:
        self.calls += 1
        from toybox.ai.client import AIResponse

        payload = json.dumps(
            {
                "body": f"Claude beat number {self.calls}!",
                "choices": ["Online choice A", "Online choice B"],
            }
        )
        return AIResponse(text=payload, model="stub")


@pytest.fixture
def client_with_stub_ai(app: FastAPI) -> Iterator[tuple[TestClient, _StubSyncClient]]:
    stub = _StubSyncClient()
    app.dependency_overrides[get_sync_ai_client] = lambda: stub
    try:
        with TestClient(app) as test_client:
            yield test_client, stub
    finally:
        app.dependency_overrides.pop(get_sync_ai_client, None)


async def _capable_true(*_args: Any, **_kwargs: Any) -> tuple[bool, None]:
    return True, None


async def _capable_false(*_args: Any, **_kwargs: Any) -> tuple[bool, Any]:
    from toybox.ai.capability import CapabilityReason

    return False, CapabilityReason.network_offline


# ---------------------------------------------------------------------------
# PRIMARY — offline nonlinear round trip through propose → advance.
# ---------------------------------------------------------------------------


def test_offline_adventure_generates_beats_through_advance_to_terminal(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """propose(adventure, nonlinear) → advance ≥3 beats (each kind=
    adventure_beat with a fresh body) → reaches terminal/reward.
    """
    _set_linearity(db_path, "nonlinear")

    body = _propose_adventure(offline_client, parent_headers, seed=99)
    activity_id = body["id"]

    # Beat 1 is seeded as kind=adventure_beat with choices.
    first = _current_step(body)
    assert first is not None
    assert first["kind"] == "adventure_beat"
    assert first["choices"], "nonlinear adventure beat 1 must carry choices"
    assert first["body"]

    # The activities row carries adventure=1 (the wiring).
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT adventure FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert int(row["adventure"]) == 1

    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])
    assert state["state"] == "running"

    seen_bodies: set[str] = set()
    first_step = _current_step(state)
    assert first_step is not None
    seen_bodies.add(first_step["body"])

    generated_beats = 0
    reached_terminal = False
    for _ in range(MAX_ADVANCE_LOOPS):
        current = _current_step(state)
        assert current is not None
        ci = 0 if current.get("choices") else None
        state = _advance(
            offline_client, parent_headers, activity_id, state["version"], choice_index=ci
        )
        new_current = _current_step(state)
        if state["state"] in {"completed", "ended"}:
            reached_terminal = True
            break
        assert new_current is not None
        if new_current["kind"] == "adventure_beat" and new_current["body"] not in seen_bodies:
            generated_beats += 1
            seen_bodies.add(new_current["body"])
        # Reward step also signals we've passed the adventure bound.
        if new_current["kind"] == "reward":
            reached_terminal = True
            break

    assert generated_beats >= 3, f"expected ≥3 generated beats, saw {generated_beats}"
    assert reached_terminal, "adventure never reached terminal/reward within the bound"

    # MEDIUM-3: termination must be pinned to the cap. Exactly
    # MAX_ADVENTURE_BEATS beat rows are persisted (import the constant — do
    # NOT hardcode), and the advance that crosses the cap routes to
    # terminal/reward WITHOUT generating an (N+1)th beat.
    beat_rows = _count_beat_rows(db_path, activity_id)
    assert beat_rows == MAX_ADVENTURE_BEATS, (
        f"expected exactly {MAX_ADVENTURE_BEATS} persisted beat rows, saw {beat_rows}"
    )
    # One more advance past terminal must not add another beat row.
    if state["state"] == "completed":
        pass  # already dismissed; no further advance to attempt
    else:
        current = _current_step(state)
        ci = 0 if current and current.get("choices") else None
        state = _advance(
            offline_client, parent_headers, activity_id, state["version"], choice_index=ci
        )
        assert _count_beat_rows(db_path, activity_id) == MAX_ADVENTURE_BEATS, (
            "crossing the cap must not generate another adventure_beat"
        )


def test_linear_adventure_beats_have_no_choices(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """With game_linearity=linear, generated beats carry no choices."""
    _set_linearity(db_path, "linear")

    body = _propose_adventure(offline_client, parent_headers, seed=33)
    activity_id = body["id"]
    first = _current_step(body)
    assert first is not None
    assert first["kind"] == "adventure_beat"
    assert not first["choices"], "linear adventure beat 1 must have no choices"

    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])
    # Advance one more beat (no choice_index since linear).
    state = _advance(offline_client, parent_headers, activity_id, state["version"])
    current = _current_step(state)
    if current is not None and current["kind"] == "adventure_beat":
        assert not current["choices"], "linear beat must not carry choices"


# ---------------------------------------------------------------------------
# Online path — gate green invokes Claude; not-green uses offline fallback.
# ---------------------------------------------------------------------------


def test_gate_green_invokes_claude_for_adventure(
    client_with_stub_ai: tuple[TestClient, _StubSyncClient],
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate green → the adventure beat call goes through the Claude client."""
    client, stub = client_with_stub_ai
    monkeypatch.setattr("toybox.ai.capability.is_capable", _capable_true)
    _set_linearity(db_path, "nonlinear")

    body = _propose_adventure(client, parent_headers, seed=77)
    activity_id = body["id"]
    # Propose seeded beat 1 online — the stub was called and its body shows.
    assert stub.calls >= 1, "Claude client should have been called for the seed beat"
    first = _current_step(body)
    assert first is not None
    assert first["body"].startswith("Claude beat number")
    labels = [c["label"] for c in first["choices"]]
    assert labels == ["Online choice A", "Online choice B"]

    # Reset call count; the approve step-animator also shares the stub.
    calls_before = stub.calls
    state = _approve(client, parent_headers, activity_id, body["version"])
    state = _advance(client, parent_headers, activity_id, state["version"])
    stub_after_approve = stub.calls
    # Advance once with a choice → another online beat call fires.
    current = _current_step(state)
    assert current is not None
    ci = 0 if current.get("choices") else None
    state = _advance(client, parent_headers, activity_id, state["version"], choice_index=ci)
    assert stub.calls > stub_after_approve, "advance should have called Claude for the next beat"
    new_current = _current_step(state)
    assert new_current is not None
    assert new_current["body"].startswith("Claude beat number")
    assert calls_before >= 1


def test_gate_not_green_uses_offline_no_client_call(
    client_with_stub_ai: tuple[TestClient, _StubSyncClient],
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate NOT green → the Claude client is never called; offline assembly."""
    client, stub = client_with_stub_ai
    monkeypatch.setattr("toybox.ai.capability.is_capable", _capable_false)
    _set_linearity(db_path, "nonlinear")

    body = _propose_adventure(client, parent_headers, seed=55)
    activity_id = body["id"]
    seed_calls = stub.calls
    assert seed_calls == 0, "gate not green must not call Claude for the seed beat"
    first = _current_step(body)
    assert first is not None
    assert not first["body"].startswith("Claude beat number")

    state = _approve(client, parent_headers, activity_id, body["version"])
    # Approve runs the S2 step-animator which shares the stub; ignore that.
    calls_after_approve = stub.calls
    state = _advance(client, parent_headers, activity_id, state["version"])
    current = _current_step(state)
    assert current is not None
    ci = 0 if current.get("choices") else None
    state = _advance(client, parent_headers, activity_id, state["version"], choice_index=ci)
    assert stub.calls == calls_after_approve, "gate not green must not call Claude on advance"
    new_current = _current_step(state)
    assert new_current is not None
    if new_current["kind"] == "adventure_beat":
        assert not new_current["body"].startswith("Claude beat number")


# ---------------------------------------------------------------------------
# MEDIUM-1 — multi-beat choice history reaches a LATER beat's generation.
# ---------------------------------------------------------------------------


class _RecordingSyncClient:
    """Sync client stub that records the ``story_so_far`` of each online call.

    The adventure engine's user payload is JSON carrying ``story_so_far`` —
    the ordered choice history. By capturing it per call we can prove an
    EARLIER beat's choice reaches a LATER beat's generation (the W4 "the
    adventure builds on prior choices" contract).
    """

    def __init__(self) -> None:
        self.calls = 0
        self.histories: list[list[str]] = []

    def complete_text_sync(self, messages: Any, **kwargs: Any) -> Any:
        self.calls += 1
        from toybox.ai.client import AIResponse

        # The engine sends the history inside the user message content.
        user_content = ""
        for m in messages:
            content = getattr(m, "content", None)
            if isinstance(content, str) and content:
                user_content = content
        try:
            parsed = json.loads(user_content)
            story = parsed.get("story_so_far", [])
            if isinstance(story, list):
                self.histories.append([str(s) for s in story])
            else:
                self.histories.append([])
        except (json.JSONDecodeError, TypeError):
            self.histories.append([])

        # Deterministic distinct choice labels per call so the recorded
        # history at the next beat reflects the choice picked at this one.
        payload = json.dumps(
            {
                "body": f"Claude beat number {self.calls}!",
                "choices": [f"Choice {self.calls}A", f"Choice {self.calls}B"],
            }
        )
        return AIResponse(text=payload, model="stub")


@pytest.fixture
def client_with_recording_ai(
    app: FastAPI,
) -> Iterator[tuple[TestClient, _RecordingSyncClient]]:
    stub = _RecordingSyncClient()
    app.dependency_overrides[get_sync_ai_client] = lambda: stub
    try:
        with TestClient(app) as test_client:
            yield test_client, stub
    finally:
        app.dependency_overrides.pop(get_sync_ai_client, None)


def test_online_history_chains_earlier_choices_into_later_beats(
    client_with_recording_ai: tuple[TestClient, _RecordingSyncClient],
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An EARLIER beat's choice must reach a LATER beat's online generation.

    Regression for MEDIUM-1: the advance steps SELECT had dropped
    ``chosen_label`` so ``_adventure_history_from_steps`` always returned
    empty — the online prompt only ever saw the single most-recent choice.
    Here we advance through several beats picking a KNOWN choice each time
    and assert the prompt for a later beat contains the choices made at the
    earlier beats (the full ordered chain, not just the latest).
    """
    client, stub = client_with_recording_ai
    monkeypatch.setattr("toybox.ai.capability.is_capable", _capable_true)
    _set_linearity(db_path, "nonlinear")

    body = _propose_adventure(client, parent_headers, seed=123)
    activity_id = body["id"]
    state = _approve(client, parent_headers, activity_id, body["version"])
    state = _advance(client, parent_headers, activity_id, state["version"])

    # Walk forward, always picking choice index 0, recording the label we
    # picked at each beat so we can assert it later surfaces in the history.
    picked_labels: list[str] = []
    for _ in range(4):
        current = _current_step(state)
        assert current is not None
        if not current.get("choices"):
            break
        picked_labels.append(current["choices"][0]["label"])
        state = _advance(client, parent_headers, activity_id, state["version"], choice_index=0)
        if state["state"] in {"completed", "ended"}:
            break

    # The recorded history at the LAST online generation must contain the
    # FULL ordered chain of choices picked at the earlier beats — not just
    # the most recent one.
    assert len(picked_labels) >= 2, "need at least two prior choices to prove chaining"
    last_history = stub.histories[-1]
    for label in picked_labels[:-1]:
        assert label in last_history, (
            f"earlier choice {label!r} missing from later beat's history {last_history!r}"
        )
    # Specifically: an EARLY choice (the first one) reaches a LATE beat.
    assert picked_labels[0] in last_history
    # And the history is the ordered prefix the engine accumulated.
    assert last_history[: len(picked_labels) - 1] == picked_labels[:-1]


# ---------------------------------------------------------------------------
# MEDIUM-2 — one consistent offline theme from beat 0 through the last beat.
# ---------------------------------------------------------------------------


def test_offline_adventure_keeps_one_theme_across_all_beats(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """The offline theme must be identical for beat 0 and every later beat.

    Regression for MEDIUM-2: propose seeded beat 0 from ``body.seed`` while
    advance derived a DIFFERENT seed from the activity id, so the opener's
    theme disagreed with the rest ~92% of the time. Now both share the
    id-derived seed; assert the theme word from the id-derived seed appears
    in beat 0 AND survives through the final beat.
    """
    _set_linearity(db_path, "nonlinear")

    body = _propose_adventure(offline_client, parent_headers, seed=4242)
    activity_id = body["id"]

    # The whole adventure is keyed on this seed (propose + advance share it).
    expected_seed = _adventure_seed_from_id(activity_id)
    expected_theme = _theme_for(expected_seed)

    first = _current_step(body)
    assert first is not None
    assert expected_theme in first["body"].lower(), (
        f"beat 0 opener should carry the id-derived theme {expected_theme!r}; got {first['body']!r}"
    )

    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])

    # Walk every later beat. The id-derived theme word must recur in the
    # rendered text (the offline transition/choice fragments splice
    # ``{theme}`` in), proving the opener and the rest share ONE theme. A
    # different theme word never appearing as a beat's theme is implied —
    # there is now a single seed, so ``_theme_for`` returns one value.
    saw_theme_in_later_beat = False
    for _ in range(MAX_ADVANCE_LOOPS):
        current = _current_step(state)
        assert current is not None
        if current["kind"] == "adventure_beat":
            rendered = current["body"].lower()
            for c in current.get("choices") or []:
                rendered += " " + str(c["label"]).lower()
            if expected_theme in rendered:
                saw_theme_in_later_beat = True
        ci = 0 if current.get("choices") else None
        state = _advance(
            offline_client, parent_headers, activity_id, state["version"], choice_index=ci
        )
        if state["state"] in {"completed", "ended"}:
            break

    assert saw_theme_in_later_beat, (
        f"expected the id-derived theme {expected_theme!r} to recur in later beats"
    )


# ---------------------------------------------------------------------------
# Non-adventure propose/advance is unchanged.
# ---------------------------------------------------------------------------


def test_normal_propose_advance_unchanged(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """A normal (adventure=false) propose persists adventure=0 and advances
    through the template path exactly as before.
    """
    body = _propose_normal(offline_client, parent_headers, seed=42)
    activity_id = body["id"]
    first = _current_step(body)
    assert first is not None
    assert first["kind"] != "adventure_beat"

    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT adventure FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert int(row["adventure"]) == 0

    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])
    assert state["state"] == "running"
