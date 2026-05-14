"""Phase E Step 28 integration tests: env-var dispatch + tool_calls write.

Exercises ``_do_propose`` end-to-end through the FastAPI propose
endpoint:

* (a) ``TOYBOX_GENERATOR_ADAPTER`` and ``TOYBOX_GENERATOR_MODE`` UNSET
  → v1 byte-identity. The labeled_events row's ``tool_calls`` column is
  NULL and ``generator_path = 'offline'``.
* (b) ``TOYBOX_GENERATOR_ADAPTER=claude`` + ``TOYBOX_GENERATOR_MODE=loop``
  → ``ClaudeActivityGenerator.generate_activity_loop`` runs; the
  labeled_events row's ``tool_calls`` column is populated with a list
  containing at least one ``{tool, args, result_summary, latency_ms,
  error, ts}`` dict; ``generator_path = 'claude'``.
* (c) Loop-mode invalid args: model emits a tool call with a non-UUID
  ``room_id``; the structured recovery error is fed back to the next
  model turn and the model recovers by emitting a final activity (or a
  retry with a corrected room_id). This lives entirely on the test mock
  — no real Claude.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from toybox.db.connection import connect

PROPOSE_BODY: dict[str, Any] = {
    "intent": "request_play",
    "slot": "unicorns",
    "hour": 12,
    "seed": 42,
}


@pytest.fixture(autouse=True)
def _reset_local_breaker() -> Iterator[None]:
    """Each test gets a fresh local breaker singleton.

    The module-level cache in :mod:`toybox.ai.breaker` would otherwise
    leak failure state across tests in this module. Matches the
    equivalent autouse fixture in ``tests/unit/ai/test_local.py``.
    """
    from toybox.ai.breaker import reset_local_breaker_for_tests

    reset_local_breaker_for_tests()
    yield
    reset_local_breaker_for_tests()


def _read_labeled_event(db_path: Path, activity_id: str) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM labeled_events WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()
        if row is None:
            raise AssertionError(f"no labeled_events row for {activity_id}")
        return {key: row[key] for key in row.keys()}
    finally:
        conn.close()


def _propose(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json=PROPOSE_BODY,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


# -------------------------------------------------------------------- (a) v1


def test_v1_default_path_is_byte_identical(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both env vars unset → activity is byte-identical to the offline generator.

    The offline ``generate(...)`` is fully deterministic given
    ``(intent, slot, context, hour, seed)`` — same inputs produce the
    same Activity (including ``id``). We compute that baseline directly
    against the same fixed inputs and assert the propose-path output
    matches it field-for-field. This is the actual byte-identity claim:
    if a future refactor accidentally re-routes ``propose`` through a
    different code path, this test fails.
    """
    from toybox.activities.generator import generate as offline_generate

    monkeypatch.delenv("TOYBOX_GENERATOR_ADAPTER", raising=False)
    monkeypatch.delenv("TOYBOX_GENERATOR_MODE", raising=False)

    # Compute the baseline directly from the offline generator with the
    # same fixed inputs propose will use. We pass the same DB connection
    # so feedback-consultation rng-consumption order matches what the
    # propose path does — generator's id derivation depends on the
    # selected template_id which depends on rng consumption.
    baseline_conn = connect(db_path)
    try:
        baseline_activity = offline_generate(
            intent=PROPOSE_BODY["intent"],
            slot=PROPOSE_BODY["slot"],
            context=None,
            hour=PROPOSE_BODY["hour"],
            seed=PROPOSE_BODY["seed"],
            persona_id=None,
            conn=baseline_conn,
            available_toys=[],
            available_rooms=[],
            resolved_children=None,
        )
    finally:
        baseline_conn.close()

    # Drive propose with the same inputs and force persona_id=None so
    # the random library persona splice doesn't perturb metadata.
    propose_body = dict(PROPOSE_BODY)
    propose_body["persona_id"] = None
    response = client.post("/api/activities/propose", json=propose_body, headers=parent_headers)
    assert response.status_code == 201, response.text
    proposed = response.json()

    # Activity-id is contractually deterministic — propose must echo
    # the same uuid as the offline generator computed.
    assert proposed["id"] == baseline_activity.id
    assert proposed["title"] == baseline_activity.title

    # Phase G G2.5: propose response carries the full template plan
    # (5 steps for linear templates), rendered with the same slot fills
    # the offline generator used. All 5 step bodies should round-trip.
    baseline_step_texts = [s.text for s in baseline_activity.steps]
    propose_step_texts = [s["body"] for s in proposed["steps"]]
    assert propose_step_texts == baseline_step_texts

    # signature + hour_bucket + slot_values in metadata also pin the same.
    # signature is the load-bearing hash — if it diverges, downstream
    # feedback aggregation breaks silently.
    proposed_meta = proposed.get("metadata") or {}
    assert proposed_meta.get("signature") == baseline_activity.metadata.get("signature")
    assert proposed_meta.get("hour_bucket") == baseline_activity.metadata.get("hour_bucket")
    assert list(proposed_meta.get("slot_values") or ()) == list(
        baseline_activity.metadata.get("slot_values") or ()
    )

    # labeled_events row pins the offline path + no tool_calls; the
    # row's ``activity_json`` carries the full Activity envelope so we
    # can pin template_id byte-identity from there.
    baseline_row = _read_labeled_event(db_path, proposed["id"])
    assert baseline_row["generator_path"] == "offline"
    assert baseline_row["tool_calls"] is None
    persisted_activity = json.loads(baseline_row["activity_json"])
    assert persisted_activity["template_id"] == baseline_activity.template_id
    assert persisted_activity["id"] == baseline_activity.id
    # Step bodies inside the persisted activity_json are byte-identical
    # to the offline-generated ones — proves the propose path didn't
    # mutate step text on the way to persistence.
    persisted_step_texts = [s["text"] for s in persisted_activity["steps"]]
    assert persisted_step_texts == baseline_step_texts

    # Activity-shape sanity (cheap follow-on assertions).
    # Phase G G2.5: propose response carries the full template plan
    # (5 steps for linear templates) — restored after G2 lazy-insert
    # narrowed it to 1, so the parent dashboard can preview all steps
    # before approving. activity_steps DB rows remain lazy-inserted.
    assert len(proposed["steps"]) == 5
    assert proposed["state"] == "proposed"


# -------------------------------------------------------------------- (b) loop


def _scripted_activity_payload(activity_id: str) -> str:
    return json.dumps(
        {
            "id": activity_id,
            "template_id": "loop_mode_test",
            "persona_id": None,
            "title": "Loop generated",
            "version": 1,
            "metadata": {"slot_values": []},
            "steps": [
                {"step_index": 0, "text": "Step one."},
                {"step_index": 1, "text": "Step two."},
                {"step_index": 2, "text": "Step three."},
                {"step_index": 3, "text": "Step four."},
                {"step_index": 4, "text": "Step five."},
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def test_claude_loop_populates_tool_calls(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loop mode → labeled_events.tool_calls is populated."""
    # Mock the AnthropicClient + StubClient seam by monkey-patching
    # the loop-generation builder to inject a scripted StubClient.
    monkeypatch.setenv("TOYBOX_GENERATOR_ADAPTER", "claude")
    monkeypatch.setenv("TOYBOX_GENERATOR_MODE", "loop")

    activity_id = "11111111-2222-4333-8444-555555555555"
    # Tool-use turn → final activity. The StubClient pops responses
    # in order on each call.
    scripted = [
        json.dumps(
            {
                "tool_calls": [
                    {
                        "name": "get_anti_signal",
                        "args": {
                            "template_id": "loop_mode_test",
                            "slot_dict": {"slot": "unicorns"},
                        },
                    }
                ]
            }
        ),
        _scripted_activity_payload(activity_id),
    ]

    import toybox.api.activities as activities_module
    from toybox.ai.client import StubClient

    real_run_loop = activities_module._run_loop_generation

    def _patched_run_loop(
        body: Any,
        conn: sqlite3.Connection,
        *,
        effective_persona_id: str | None,
        resolved_toys: list[Any],
        resolved_rooms: list[Any],
    ) -> tuple[Any, list[dict[str, Any]]]:
        # Patch StubClient + AnthropicClient inside the late-imports of
        # the helper. Easiest: monkeypatch the imports for the duration
        # of this call by stubbing load_token to return None (so the
        # helper picks StubClient) and pre-loading the StubClient with
        # the scripted responses via a sneak-in.
        del effective_persona_id, resolved_toys, resolved_rooms
        import asyncio

        from toybox.ai.adapters import ClaudeActivityGenerator
        from toybox.ai.adapters.claude import ClaudeAdapterContext
        from toybox.ai.tools import ToolContext, ToolDispatcher
        from toybox.db.connection import connect as _connect

        client_obj = StubClient(responses=scripted)

        def _factory() -> sqlite3.Connection:
            return _connect(db_path, check_same_thread=False)

        tool_ctx = ToolContext(
            connection_factory=_factory,
            session_id=body.session_id,
        )
        tools = ToolDispatcher(tool_ctx)
        adapter_ctx = ClaudeAdapterContext(
            system_prompt="loop test",
            user_prompt=json.dumps({"intent": body.intent}),
        )
        adapter = ClaudeActivityGenerator(client_obj)
        activity = asyncio.run(adapter.generate_activity_loop(adapter_ctx, tools))
        return activity, adapter.tool_calls

    monkeypatch.setattr(activities_module, "_run_loop_generation", _patched_run_loop)

    # Run propose. The activity_id in the mocked Activity is fixed.
    response = client.post("/api/activities/propose", json=PROPOSE_BODY, headers=parent_headers)
    # Restore for cleanliness (monkeypatch will undo it anyway).
    activities_module._run_loop_generation = real_run_loop

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] == activity_id

    row = _read_labeled_event(db_path, activity_id)
    assert row["generator_path"] == "claude"
    assert row["tool_calls"] is not None

    tool_calls = json.loads(row["tool_calls"])
    assert isinstance(tool_calls, list)
    assert len(tool_calls) >= 1
    first = tool_calls[0]
    for required_field in ("tool", "args", "result_summary", "latency_ms", "error", "ts"):
        assert required_field in first, f"missing {required_field} on tool_call entry"
    assert first["tool"] == "get_anti_signal"
    # No anti-signal hits on a fresh DB → error is None and the summary
    # mentions the no-hits text.
    assert first["error"] is None


# -------------------------------------------------------------------- (c) recovery


def test_loop_invalid_args_recovery(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad room_id → recovery error fed back, model retries → success."""
    monkeypatch.setenv("TOYBOX_GENERATOR_ADAPTER", "claude")
    monkeypatch.setenv("TOYBOX_GENERATOR_MODE", "loop")

    activity_id = "22222222-3333-4444-8555-666666666666"
    # Turn 1: bad room_id (path-traversal style). Adapter dispatcher
    # produces a structured recovery error; the model on turn 2
    # corrects to a final Activity payload.
    scripted = [
        json.dumps(
            {
                "tool_calls": [
                    {
                        "name": "get_room",
                        "args": {"room_id": "../../../etc/passwd"},
                    }
                ]
            }
        ),
        _scripted_activity_payload(activity_id),
    ]

    import toybox.api.activities as activities_module
    from toybox.ai.client import StubClient

    # M5: capture the StubClient instance the loop drives so the test
    # can inspect the second turn's message thread (proving the
    # error envelope was actually fed back to the model in a usable
    # shape).
    captured: dict[str, Any] = {}

    def _patched_run_loop(
        body: Any,
        conn: sqlite3.Connection,
        *,
        effective_persona_id: str | None,
        resolved_toys: list[Any],
        resolved_rooms: list[Any],
    ) -> tuple[Any, list[dict[str, Any]]]:
        del effective_persona_id, resolved_toys, resolved_rooms
        import asyncio

        from toybox.ai.adapters import ClaudeActivityGenerator
        from toybox.ai.adapters.claude import ClaudeAdapterContext
        from toybox.ai.tools import ToolContext, ToolDispatcher
        from toybox.db.connection import connect as _connect

        client_obj = StubClient(responses=scripted)
        captured["client"] = client_obj

        def _factory() -> sqlite3.Connection:
            return _connect(db_path, check_same_thread=False)

        tool_ctx = ToolContext(connection_factory=_factory, session_id=body.session_id)
        tools = ToolDispatcher(tool_ctx)
        adapter_ctx = ClaudeAdapterContext(
            system_prompt="loop recovery test",
            user_prompt=json.dumps({"intent": body.intent}),
        )
        adapter = ClaudeActivityGenerator(client_obj)
        activity = asyncio.run(adapter.generate_activity_loop(adapter_ctx, tools))
        return activity, adapter.tool_calls

    monkeypatch.setattr(activities_module, "_run_loop_generation", _patched_run_loop)

    response = client.post("/api/activities/propose", json=PROPOSE_BODY, headers=parent_headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] == activity_id

    row = _read_labeled_event(db_path, activity_id)
    tool_calls = json.loads(row["tool_calls"])
    # First call captured the invalid_args recovery (structured error
    # shape) — proving the recovery dict was fed back rather than
    # raised.
    assert len(tool_calls) >= 1
    invalid_call = tool_calls[0]
    assert invalid_call["tool"] == "get_room"
    assert invalid_call["error"] is not None
    assert invalid_call["error"].startswith("invalid_args:")

    # M5: prove the structured error was fed BACK to the model in a
    # usable shape on the next turn. Inspect the StubClient's recorded
    # ``calls`` log: the SECOND ``complete_text`` call's messages must
    # include a user-role message whose JSON body has a ``tool_results``
    # array containing an entry whose ``error`` starts with
    # ``invalid_args:`` and whose ``reason`` is non-empty.
    client_obj: Any = captured["client"]
    assert len(client_obj.calls) == 2, (
        f"expected 2 turns, got {len(client_obj.calls)}: {client_obj.calls}"
    )
    _name, second_msgs, _kwargs = client_obj.calls[1]
    user_msgs = [m for m in second_msgs if m.role == "user"]
    # Find the synthetic tool-results user turn (assistant turn carries
    # the model's previous tool_calls JSON; the user turn carries the
    # tool_results).
    tool_results_msg = None
    for m in user_msgs:
        try:
            decoded = json.loads(m.content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(decoded, dict) and "tool_results" in decoded:
            tool_results_msg = decoded
            break
    assert tool_results_msg is not None, (
        "second turn missing user-role message carrying tool_results JSON"
    )
    results = tool_results_msg["tool_results"]
    assert isinstance(results, list) and len(results) >= 1
    err_entries = [
        e
        for e in results
        if isinstance(e, dict)
        and isinstance(e.get("error"), str)
        and e["error"].startswith("invalid_args:")
    ]
    assert err_entries, f"no invalid_args entry in tool_results: {results}"
    err = err_entries[0]
    assert isinstance(err.get("reason"), str) and err["reason"], (
        f"reason must be a non-empty string: {err}"
    )


# -------------------------------------------------------------------- (d) H3 narrow-except


def test_loop_transient_failure_falls_back_to_offline_with_metadata_flag(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H3: Transient ``RuntimeError`` from loop generator → offline fallback.

    Asserts:
    1. The narrow catch fires (loop failure → offline activity is
       still produced).
    2. An ERROR-level log line is emitted with the structured envelope
       (``error_class`` + ``fallback_reason="transient"``).
    3. The persisted Activity's ``metadata`` envelope carries
       ``fallback_reason="transient_loop_failure"`` so an operator can
       grep ``labeled_events.activity_json`` to compute the rate.
    4. ``generator_path`` ends up ``"offline"`` (not ``"claude"``) and
       ``tool_calls`` is NULL because the loop never produced
       telemetry.
    """
    import logging as _logging

    monkeypatch.setenv("TOYBOX_GENERATOR_ADAPTER", "claude")
    monkeypatch.setenv("TOYBOX_GENERATOR_MODE", "loop")

    import toybox.api.activities as activities_module

    def _failing_run_loop(*args: object, **kwargs: object) -> tuple[Any, list[dict[str, Any]]]:
        raise RuntimeError("simulated transient claude outage")

    monkeypatch.setattr(activities_module, "_run_loop_generation", _failing_run_loop)

    caplog.set_level(_logging.ERROR, logger="toybox.api.activities")
    response = client.post("/api/activities/propose", json=PROPOSE_BODY, headers=parent_headers)
    assert response.status_code == 201, response.text
    body = response.json()

    # Structured ERROR log fired with the documented envelope.
    error_records = [
        r
        for r in caplog.records
        if r.levelno == _logging.ERROR and "claude+loop dispatch failed" in r.getMessage()
    ]
    assert len(error_records) == 1, [r.getMessage() for r in caplog.records]
    rec = error_records[0]
    assert getattr(rec, "error_class", None) == "RuntimeError"
    assert getattr(rec, "fallback_reason", None) == "transient"

    # Metadata envelope carries the fallback flag.
    meta = body.get("metadata") or {}
    assert meta.get("fallback_reason") == "transient_loop_failure"

    # Row pins the offline path + NULL tool_calls.
    row = _read_labeled_event(db_path, body["id"])
    assert row["generator_path"] == "offline"
    assert row["tool_calls"] is None


def test_loop_programming_bug_propagates_not_swallowed(
    client: TestClient,
    parent_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H3: ``TypeError``/``AttributeError`` etc. must NOT be swallowed.

    Programming bugs in the loop scaffolding (typos, wrong types, dict
    misses) are operator-actionable and must surface as 500s, not be
    masked by the offline fallback.
    """
    monkeypatch.setenv("TOYBOX_GENERATOR_ADAPTER", "claude")
    monkeypatch.setenv("TOYBOX_GENERATOR_MODE", "loop")

    import toybox.api.activities as activities_module

    def _buggy_run_loop(*args: object, **kwargs: object) -> tuple[Any, list[dict[str, Any]]]:
        # Attribute access on None — classic programming bug shape.
        raise AttributeError("'NoneType' object has no attribute 'foo'")

    monkeypatch.setattr(activities_module, "_run_loop_generation", _buggy_run_loop)

    # TestClient re-raises uncaught server exceptions; this must surface
    # the AttributeError rather than a bland 500-with-offline-activity.
    with pytest.raises(AttributeError, match="'NoneType'"):
        client.post("/api/activities/propose", json=PROPOSE_BODY, headers=parent_headers)


# ----------------------------- (e) local dispatch via mocked /v1/models probe


class _FakeProbeResponse:
    """Stub of the urllib response object for ``/v1/models`` probes."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeProbeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _patch_local_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_ids: list[str] | None = None,
    raises: Exception | None = None,
) -> list[str]:
    """Drive :func:`toybox.ai.capability.is_local_capable` via a mocked urlopen.

    When ``model_ids`` is set, the probe returns a healthy
    ``/v1/models`` envelope containing those ids. When ``raises`` is
    set, every probe raises that exception (cannot-connect path).
    Returns a call log so a test can assert the probe URL.
    """
    import urllib.request as _urllib_request  # noqa: PLC0415

    calls: list[str] = []

    def _stub(req: Any, **kwargs: Any) -> Any:  # noqa: ARG001
        calls.append(req.full_url if hasattr(req, "full_url") else str(req))
        if raises is not None:
            raise raises
        body = json.dumps(
            {"object": "list", "data": [{"id": mid} for mid in (model_ids or [])]}
        ).encode("utf-8")
        return _FakeProbeResponse(body)

    monkeypatch.setattr(_urllib_request, "urlopen", _stub)
    return calls


def test_local_dispatch_with_healthy_probe_reaches_local_adapter(
    client: TestClient,
    parent_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E: TOYBOX_GENERATOR_ADAPTER=local + healthy mock → reaches LocalActivityGenerator.

    Drives the FULL production caller end-to-end:

    1. Env vars set the adapter to ``local`` and the runtime URL to a
       mocked endpoint.
    2. The :func:`is_local_capable` probe is wired to a stubbed
       ``urlopen`` that returns ``/v1/models`` with the expected id.
    3. ``_do_propose`` resolves dispatch → reaches ``_dispatch_local``
       → instantiates :class:`LocalActivityGenerator` → calls
       :meth:`generate_activity_loop` which raises
       :class:`NotImplementedError` with the Step 26 / #38 hint.
    4. Starlette surfaces the exception to the TestClient.

    Code-quality rule "New components require an integration test
    through the production caller" -- the assertions pin the
    end-to-end dispatch chain, not just the adapter in isolation.
    """
    monkeypatch.setenv("TOYBOX_GENERATOR_ADAPTER", "local")
    monkeypatch.setenv("TOYBOX_GENERATOR_MODE", "loop")
    monkeypatch.setenv("TOYBOX_LOCAL_RUNTIME_URL", "http://10.0.0.42:11434")
    monkeypatch.setenv("TOYBOX_LOCAL_MODEL_ID", "qwen2.5:7b")

    calls = _patch_local_probe(monkeypatch, model_ids=["qwen2.5:7b"])

    with pytest.raises(NotImplementedError) as exc_info:
        client.post("/api/activities/propose", json=PROPOSE_BODY, headers=parent_headers)
    # Pin both the Step-26 wording AND the #38 issue pointer on the same
    # raised exception -- a future operator's traceback must link
    # directly to the follow-up work item.
    msg = str(exc_info.value)
    assert "Step 26" in msg
    assert "#38" in msg

    # The probe MUST have been called with the configured URL.
    assert len(calls) >= 1
    assert calls[0] == "http://10.0.0.42:11434/v1/models"


def test_local_dispatch_falls_back_when_runtime_unreachable(
    client: TestClient,
    parent_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    db_path: Path,
) -> None:
    """E2E: mocked /v1/models unreachable → fall back to Claude path + WARNING.

    Asserts:
    1. The dispatch degrades to the Claude (offline) path -- propose
       returns 201 with a valid activity rather than surfacing
       NotImplementedError.
    2. A WARNING-level log line carries the capability reason
       (``local runtime not yet installed``) so an operator can grep
       the cause.
    3. ``generator_path`` ends up ``"offline"`` on the persisted
       labeled_events row -- proves the degradation actually routed
       through the v1 generator and not the Claude loop.
    """
    import logging as _logging  # noqa: PLC0415

    monkeypatch.setenv("TOYBOX_GENERATOR_ADAPTER", "local")
    monkeypatch.setenv("TOYBOX_GENERATOR_MODE", "single")
    monkeypatch.setenv("TOYBOX_LOCAL_RUNTIME_URL", "http://10.0.0.42:11434")

    import urllib.error  # noqa: PLC0415

    _patch_local_probe(monkeypatch, raises=urllib.error.URLError("connection refused"))

    caplog.set_level(_logging.WARNING, logger="toybox.api.activities")
    response = client.post("/api/activities/propose", json=PROPOSE_BODY, headers=parent_headers)
    # Fallback succeeded -- propose returned a valid activity.
    assert response.status_code == 201, response.text
    body = response.json()

    # WARNING log captures the capability reason.
    warning_records = [
        r
        for r in caplog.records
        if r.levelno == _logging.WARNING and "local adapter not capable" in r.getMessage()
    ]
    assert len(warning_records) >= 1, [r.getMessage() for r in caplog.records]
    rec = warning_records[0]
    assert getattr(rec, "capability_reason", None) == "local runtime not yet installed"

    # Row pins the offline path -- degraded fallback routed through the
    # default offline generator, not the Claude loop.
    row = _read_labeled_event(db_path, body["id"])
    assert row["generator_path"] == "offline"


def test_local_dispatch_falls_back_when_model_not_loaded(
    client: TestClient,
    parent_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """E2E: probe returns 200 but the configured model id is missing.

    Same fallback shape as the unreachable case, but with the
    ``local model not loaded`` reason. Pins that the dispatch reads
    the right reason from :func:`is_local_capable` and surfaces it
    on the WARNING log envelope.
    """
    import logging as _logging  # noqa: PLC0415

    monkeypatch.setenv("TOYBOX_GENERATOR_ADAPTER", "local")
    monkeypatch.setenv("TOYBOX_GENERATOR_MODE", "single")
    monkeypatch.setenv("TOYBOX_LOCAL_RUNTIME_URL", "http://10.0.0.42:11434")
    monkeypatch.setenv("TOYBOX_LOCAL_MODEL_ID", "qwen2.5:7b")

    # Probe returns 200 with a DIFFERENT model id -- model-not-loaded path.
    _patch_local_probe(monkeypatch, model_ids=["llama3"])

    caplog.set_level(_logging.WARNING, logger="toybox.api.activities")
    response = client.post("/api/activities/propose", json=PROPOSE_BODY, headers=parent_headers)
    assert response.status_code == 201, response.text

    warning_records = [
        r
        for r in caplog.records
        if r.levelno == _logging.WARNING and "local adapter not capable" in r.getMessage()
    ]
    assert len(warning_records) >= 1
    assert getattr(warning_records[0], "capability_reason", None) == "local model not loaded"
