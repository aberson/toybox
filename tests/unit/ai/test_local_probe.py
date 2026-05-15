"""Unit tests for the Phase E Step 25b ``--probe`` CLI in ``toybox.ai.local``.

The probe smoke-tests a running OpenAI-compatible local runtime
end-to-end. These tests stand up a stdlib ``http.server`` on an
ephemeral port and point the probe at it via the
``TOYBOX_LOCAL_RUNTIME_URL`` env var, so the probe-CLI logic is
exercised without any real GGUF / Ollama install.

Cases covered:

1. happy path (strict, format=schema) → exit 0, marker written
2. model not in /v1/models → exit 1, code=model_not_loaded, no marker
3. chat returns non-JSON content → exit 1, code=invalid_json, no marker
4. schema-fails-twice → exit 1, code=schema_mismatch, no marker, TWO POSTs
5. retry succeeds on second attempt → exit 0, marker.retries == 1, with
   stricter-prompt assertions
6. connection refused (no server) → exit 1, code=runtime_unreachable
7. wall-clock timeout (exhausted via clock-stub) → exit 1, code=timeout, no marker
8. ollama format-schema fallback → success with marker.ollama_format_path='json'
9. --budget-sec CLI flag drives ``main`` argparse branch
10. malformed TOYBOX_PROBE_BUDGET_SEC env value → invalid_budget envelope
11. 5xx on schema-format does NOT fall back (runtime_unreachable)
12. happy-path marker iso_ts matches the documented filename pattern

Handler-thread assertions are recorded into ``Handler.chat_calls`` and
re-asserted on the main thread after ``_stop_server`` — bare
``assert`` inside ``do_POST`` would be swallowed by
``BaseHTTPRequestHandler``'s exception handler and silently pass.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from toybox.ai import local as local_mod
from toybox.ai.local import (
    LOCAL_MODEL_ID_ENV,
    LOCAL_RUNTIME_URL_ENV,
    PROBE_BUDGET_ENV,
)

probe_main = local_mod.main

# Marker filename ISO pattern documented in module docstring and asserted
# by Step 25c's "passed probe within the last hour" check.
_MARKER_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z")

# ---------------------------------------------------------------------
# Canned Activity JSON
# ---------------------------------------------------------------------

# A deterministic 3-step Activity that satisfies the actual
# Activity.model_validate constraints (id non-empty, template_id
# non-empty, title non-empty, steps min_length=3, each step.text 1-600
# chars, etc.). Three steps is the minimum allowed by the relaxed
# Phase G Activity.steps shape — keeps the canned payload small.
_CANNED_ACTIVITY: dict[str, Any] = {
    "id": "probe-activity-001",
    "template_id": "probe_template",
    "title": "Smoke-probe activity",
    "version": 1,
    "metadata": {"slot_values": [], "hour_bucket": "afternoon"},
    "steps": [
        {"step_index": 0, "text": "Start by stretching."},
        {"step_index": 1, "text": "Hop three times."},
        {"step_index": 2, "text": "Take a slow breath."},
    ],
    "toy_ids": [],
}

_MALFORMED_ACTIVITY: dict[str, Any] = {
    # Missing required `title`, `template_id`, etc. — Pydantic rejects.
    "id": "x",
    "steps": [],
}

_MODEL_ID = "qwen2.5:3b-instruct-q5_K_M"


# ---------------------------------------------------------------------
# Fake-server helpers
# ---------------------------------------------------------------------


def _make_handler(
    *,
    models_body: dict[str, Any],
    chat_responder: Any,
) -> type[BaseHTTPRequestHandler]:
    """Build a ``BaseHTTPRequestHandler`` class with closed-over state.

    * ``models_body`` -- the JSON body returned by ``GET /v1/models``.
    * ``chat_responder(call_index, request_body)`` -- callable that
      returns ``(status_code, response_body_dict_or_bytes)`` for the
      Nth ``POST /v1/chat/completions``. ``response_body`` of ``None``
      means "send an empty body" (used for HTTP-error responses).

    Each request's parsed JSON body and inferred ``format`` value are
    recorded on the Handler class attribute ``chat_calls`` (a list of
    dicts) so tests can assert on them from the main thread.
    ``BaseHTTPRequestHandler`` catches exceptions raised inside
    ``do_POST`` and turns them into a 500, so an ``assert`` inside the
    handler is silently swallowed — never assert from the handler
    thread.
    """

    class Handler(BaseHTTPRequestHandler):
        # Per-class counter — each test spawns its own Handler class so
        # the counter doesn't leak across tests.
        chat_calls: list[dict[str, Any]] = []

        def log_message(self, format: str, *args: Any) -> None:  # silence
            return

        def do_GET(self) -> None:  # noqa: N802 — stdlib signature
            if self.path == "/v1/models":
                body = json.dumps(models_body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802 — stdlib signature
            if self.path != "/v1/chat/completions":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            try:
                request_body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                request_body = {}
            call_index = len(Handler.chat_calls)
            fmt_value = request_body.get("format") if isinstance(request_body, dict) else None
            Handler.chat_calls.append(
                {
                    "body": request_body,
                    "format": fmt_value,
                    "format_is_dict": isinstance(fmt_value, dict),
                }
            )
            status, body = chat_responder(call_index, request_body)
            if isinstance(body, dict):
                payload = json.dumps(body).encode("utf-8")
            elif isinstance(body, bytes):
                payload = body
            else:
                payload = b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

    return Handler


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return int(port)


def _start_server(
    handler_cls: type[BaseHTTPRequestHandler],
) -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host_raw, port = server.server_address[:2]
    host = host_raw.decode("ascii") if isinstance(host_raw, bytes) else str(host_raw)
    url = f"http://{host}:{port}"
    return server, thread, url


def _stop_server(server: HTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)


def _chat_success_body(activity_json: dict[str, Any]) -> dict[str, Any]:
    """Wrap a parsed activity JSON object in an OpenAI chat-completions response."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(activity_json),
                },
                "finish_reason": "stop",
            }
        ],
    }


def _models_body(model_ids: list[str]) -> dict[str, Any]:
    return {"object": "list", "data": [{"id": mid, "object": "model"} for mid in model_ids]}


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def fixture_path(tmp_path: Path) -> Path:
    """Project-root-shape fixture: write a single f001 line into a temp tree."""
    fx_dir = tmp_path / "tests" / "fixtures" / "eval"
    fx_dir.mkdir(parents=True)
    fx_file = fx_dir / "prompts.jsonl"
    fx_file.write_text(
        json.dumps(
            {
                "id": "f001",
                "child_profile": {
                    "age": 5,
                    "name": "Sam",
                    "interests": ["dinosaurs"],
                    "reading_level": "early_reader",
                },
                "persona": "mr_unicorn",
                "available_rooms": ["living_room"],
                "available_toys": ["stuffed_unicorn"],
                "transcript_window": "I'm bored.",
                "trigger": "boredom_explicit",
                "listening_mode": 3,
                "anti_signal": [],
                "time_of_day": "afternoon",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return fx_file


@pytest.fixture
def marker_dir(tmp_path: Path) -> Path:
    out = tmp_path / "data" / "models"
    out.mkdir(parents=True)
    return out


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(LOCAL_RUNTIME_URL_ENV, raising=False)
    monkeypatch.delenv(LOCAL_MODEL_ID_ENV, raising=False)
    monkeypatch.delenv(PROBE_BUDGET_ENV, raising=False)
    yield


def _run_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    runtime_url: str,
    model_id: str | None,
    fixture_path: Path,
    marker_dir: Path,
    budget_sec: float | None = None,
    budget_via_cli: bool = False,
    capsys: pytest.CaptureFixture[str] | None = None,
) -> tuple[int, str]:
    """Drive ``run_probe`` via ``main()`` using injected fixture + marker dir.

    We monkeypatch the module-level fixture/marker-dir constants because
    the public CLI doesn't accept them as flags (only ``--budget-sec``).
    If ``budget_via_cli`` is true, the budget is passed as
    ``--budget-sec`` on the argv instead of via the env var so the
    argparse branch gets coverage.
    """
    monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, runtime_url)
    if model_id is not None:
        monkeypatch.setenv(LOCAL_MODEL_ID_ENV, model_id)
    monkeypatch.setattr(local_mod, "_PROBE_FIXTURE_REL", fixture_path)
    monkeypatch.setattr(local_mod, "PROBE_MARKER_DIR", marker_dir)
    argv: list[str] = ["--probe"]
    if budget_sec is not None:
        if budget_via_cli:
            argv += ["--budget-sec", str(budget_sec)]
        else:
            monkeypatch.setenv(PROBE_BUDGET_ENV, str(budget_sec))
    rc = probe_main(argv)
    captured = capsys.readouterr().out if capsys is not None else ""
    return rc, captured


def _marker_files(marker_dir: Path) -> list[Path]:
    return list(marker_dir.glob(".probe-pass-*.json"))


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_probe_happy_path_strict_schema(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Happy path: GET /v1/models OK, single chat POST returns valid Activity."""

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, _chat_success_body(_CANNED_ACTIVITY)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        rc, out = _run_probe(
            monkeypatch,
            runtime_url=url,
            model_id=_MODEL_ID,
            fixture_path=fixture_path,
            marker_dir=marker_dir,
            capsys=capsys,
        )
    finally:
        _stop_server(server, thread)

    assert rc == 0, out
    assert "probe=PASS" in out
    # Schema-format path was taken on the first (and only) call. Asserts
    # OUTSIDE the handler thread so a false assertion actually fails.
    assert len(handler.chat_calls) == 1
    assert handler.chat_calls[0]["format_is_dict"], "happy path expected schema format"

    files = _marker_files(marker_dir)
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["model_id"] == _MODEL_ID
    assert payload["fixture_id"] == "f001"
    assert payload["validation_mode"] == "strict"
    assert payload["ollama_format_path"] == "schema"
    assert payload["retries"] == 0
    assert payload["runtime_url"] == url
    assert isinstance(payload["wall_clock_seconds"], (int, float))
    assert isinstance(payload["iso_ts"], str)
    # ISO pattern must match the documented filename shape — Step 25c's
    # "passed probe within the last hour" check parses this format.
    assert _MARKER_ISO_RE.fullmatch(payload["iso_ts"]), payload["iso_ts"]
    # Marker filename must be colon-free (Windows-safe).
    assert ":" not in files[0].name


def test_probe_model_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pinned model id missing from /v1/models → exit 1, no marker."""

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        pytest.fail("chat must NOT be called when model is missing")

    handler = _make_handler(
        models_body=_models_body(["phi3", "llama3"]),
        chat_responder=chat,
    )
    server, thread, url = _start_server(handler)
    try:
        rc, out = _run_probe(
            monkeypatch,
            runtime_url=url,
            model_id=_MODEL_ID,
            fixture_path=fixture_path,
            marker_dir=marker_dir,
            capsys=capsys,
        )
    finally:
        _stop_server(server, thread)

    assert rc == 1
    assert "code=model_not_loaded" in out
    assert _marker_files(marker_dir) == []


def test_probe_invalid_json_content(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Chat returns content that isn't JSON → exit 1, no marker."""

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "not json at all"}}
            ]
        }

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        rc, out = _run_probe(
            monkeypatch,
            runtime_url=url,
            model_id=_MODEL_ID,
            fixture_path=fixture_path,
            marker_dir=marker_dir,
            capsys=capsys,
        )
    finally:
        _stop_server(server, thread)

    assert rc == 1
    assert "code=invalid_json" in out
    assert _marker_files(marker_dir) == []


def test_probe_schema_mismatch_both_attempts(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both attempts return JSON that fails Activity validation → exit 1.

    Asserts TWO POSTs to /v1/chat/completions (initial + one retry).
    """

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, _chat_success_body(_MALFORMED_ACTIVITY)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        rc, out = _run_probe(
            monkeypatch,
            runtime_url=url,
            model_id=_MODEL_ID,
            fixture_path=fixture_path,
            marker_dir=marker_dir,
            capsys=capsys,
        )
    finally:
        _stop_server(server, thread)

    assert rc == 1
    assert "code=schema_mismatch" in out
    assert len(handler.chat_calls) == 2, "must retry exactly once before giving up"
    assert _marker_files(marker_dir) == []


def test_probe_retry_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """First chat call fails schema, second succeeds → exit 0, retries=1.

    Also asserts (a) the second-attempt system prompt differs from the
    first and (b) carries the stricter-retry marker token — proving the
    validation-error excerpt was forwarded back to the model on retry.
    """

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if call_index == 0:
            return 200, _chat_success_body(_MALFORMED_ACTIVITY)
        return 200, _chat_success_body(_CANNED_ACTIVITY)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        rc, out = _run_probe(
            monkeypatch,
            runtime_url=url,
            model_id=_MODEL_ID,
            fixture_path=fixture_path,
            marker_dir=marker_dir,
            capsys=capsys,
        )
    finally:
        _stop_server(server, thread)

    assert rc == 0, out
    files = _marker_files(marker_dir)
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["retries"] == 1

    # Retry-prompt assertions — guard against silent regression where
    # the retry sends the same prompt and the model just rerolls.
    assert len(handler.chat_calls) == 2
    first_system = handler.chat_calls[0]["body"]["messages"][0]["content"]
    second_system = handler.chat_calls[1]["body"]["messages"][0]["content"]
    assert first_system != second_system, "stricter retry must differ from first prompt"
    assert local_mod._RETRY_PROMPT_MARKER in second_system, (
        "second-attempt system prompt must inject the validation-error excerpt; "
        f"got: {second_system!r}"
    )
    # Spot-check: the first prompt does NOT contain the marker (i.e. the
    # change is real, not vacuous).
    assert local_mod._RETRY_PROMPT_MARKER not in first_system


def test_probe_runtime_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No server bound → exit 1, code=runtime_unreachable, no marker."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    # Do not start any server.
    rc, out = _run_probe(
        monkeypatch,
        runtime_url=url,
        model_id=_MODEL_ID,
        fixture_path=fixture_path,
        marker_dir=marker_dir,
        capsys=capsys,
        # Tight budget — connect failure should be near-instant; keep
        # budget small so a stalled localhost connect can't burn 60s.
        budget_sec=5.0,
    )
    assert rc == 1
    assert "code=runtime_unreachable" in out
    assert _marker_files(marker_dir) == []


def test_probe_wall_clock_timeout(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Wall-clock budget exhausted between calls → code=timeout exactly.

    Stubs ``time.monotonic`` inside the probe module so the second call to
    ``_check_wall_clock`` sees an elapsed > budget. This provably exercises
    ``_check_wall_clock`` (not urllib's socket timeout), letting us assert
    EXACTLY ``code=timeout`` — no OR with ``runtime_unreachable``. If
    wall-clock enforcement is removed, the server replies in milliseconds
    and this test fails with ``probe=PASS`` instead of silently passing
    via socket timeout.
    """

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, _chat_success_body(_CANNED_ACTIVITY)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)

    # Stub clock: first call returns t0, then jumps so the very next
    # _check_wall_clock sees elapsed >> budget.
    real_monotonic = time.monotonic
    t0 = real_monotonic()
    calls = {"n": 0}

    def stub_monotonic() -> float:
        calls["n"] += 1
        # First call is the run_probe start anchor; subsequent calls
        # report 10s elapsed, which exceeds the 2s budget.
        if calls["n"] == 1:
            return t0
        return t0 + 10.0

    monkeypatch.setattr(local_mod.time, "monotonic", stub_monotonic)
    try:
        rc, out = _run_probe(
            monkeypatch,
            runtime_url=url,
            model_id=_MODEL_ID,
            fixture_path=fixture_path,
            marker_dir=marker_dir,
            capsys=capsys,
            budget_sec=2.0,
        )
    finally:
        _stop_server(server, thread)

    assert rc == 1
    # Exactly ``code=timeout`` — no OR. If this fails with
    # runtime_unreachable, _check_wall_clock isn't being reached.
    assert "code=timeout" in out, out
    assert "code=runtime_unreachable" not in out, out
    assert _marker_files(marker_dir) == []


def test_probe_format_schema_fallback_to_json(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Server 400s on format=<schema>, succeeds on format='json' → success."""

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, Any]:
        fmt = body.get("format")
        if isinstance(fmt, dict):
            # Reject schema-passing form like older Ollama versions.
            return 400, b'{"error": "format schema not supported"}'
        # Second call uses format='json' — accept.
        return 200, _chat_success_body(_CANNED_ACTIVITY)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        rc, out = _run_probe(
            monkeypatch,
            runtime_url=url,
            model_id=_MODEL_ID,
            fixture_path=fixture_path,
            marker_dir=marker_dir,
            capsys=capsys,
        )
    finally:
        _stop_server(server, thread)

    assert rc == 0, out
    files = _marker_files(marker_dir)
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["ollama_format_path"] == "json"
    assert payload["retries"] == 0
    # Main-thread assertion on the format progression: call 0 was the
    # dict schema, call 1 was the 'json' string.
    assert len(handler.chat_calls) == 2
    assert handler.chat_calls[0]["format_is_dict"], "call 0 should be schema-format"
    assert handler.chat_calls[1]["format"] == "json", "call 1 should be format='json' fallback"


def test_probe_5xx_on_schema_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """5xx on the schema attempt surfaces as runtime_unreachable, no second POST.

    The format-fallback is for 4xx (older Ollama rejects the schema
    shape). A 5xx is a real server bug — falling back would mask it
    behind another 5xx and a confusing ``runtime_unreachable`` further
    down the chain. Assert no retry POST on 5xx.
    """

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, Any]:
        return 503, b'{"error": "ollama overloaded"}'

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        rc, out = _run_probe(
            monkeypatch,
            runtime_url=url,
            model_id=_MODEL_ID,
            fixture_path=fixture_path,
            marker_dir=marker_dir,
            capsys=capsys,
        )
    finally:
        _stop_server(server, thread)

    assert rc == 1
    assert "code=runtime_unreachable" in out
    # Exactly one POST — we did NOT fall back to format='json' on a 5xx.
    assert len(handler.chat_calls) == 1, (
        f"5xx must not trigger format-fallback; expected 1 POST, got {len(handler.chat_calls)}"
    )
    assert _marker_files(marker_dir) == []


def test_probe_budget_sec_cli_flag(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--budget-sec`` CLI flag drives the argparse branch end-to-end.

    Happy path with a 5-second CLI budget. Asserts ``probe=PASS`` so the
    flag is wired into ``run_probe(budget_sec=...)`` rather than being
    parsed but ignored.
    """

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, _chat_success_body(_CANNED_ACTIVITY)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        rc, out = _run_probe(
            monkeypatch,
            runtime_url=url,
            model_id=_MODEL_ID,
            fixture_path=fixture_path,
            marker_dir=marker_dir,
            capsys=capsys,
            budget_sec=5.0,
            budget_via_cli=True,
        )
    finally:
        _stop_server(server, thread)

    assert rc == 0, out
    assert "probe=PASS" in out
    assert len(_marker_files(marker_dir)) == 1


def test_probe_malformed_budget_env_surfaces_envelope(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``TOYBOX_PROBE_BUDGET_SEC=abc`` → invalid_budget probe-FAIL envelope.

    Without explicit ValueError handling in ``_resolve_budget_sec``,
    ``float('abc')`` would propagate up as an uncaught traceback —
    operator sees a traceback instead of ``probe=FAIL code=invalid_budget``.
    """
    # No server needed — failure happens before any HTTP call.
    monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, "http://127.0.0.1:1")
    monkeypatch.setenv(LOCAL_MODEL_ID_ENV, _MODEL_ID)
    monkeypatch.setenv(PROBE_BUDGET_ENV, "abc")
    monkeypatch.setattr(local_mod, "_PROBE_FIXTURE_REL", fixture_path)
    monkeypatch.setattr(local_mod, "PROBE_MARKER_DIR", marker_dir)

    rc = probe_main(["--probe"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "code=invalid_budget" in out, out
    assert _marker_files(marker_dir) == []
