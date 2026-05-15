"""Unit tests for the Phase E Step 25c benchmark + decision-doc CLI.

The benchmark CLI in :mod:`toybox.ai.local_benchmark` exercises a
locally-hosted OpenAI-compatible runtime over 10 fixture prompts. These
tests stand up a stdlib ``http.server`` on an ephemeral port and point
the benchmark at it via ``TOYBOX_LOCAL_RUNTIME_URL``, so the CLI logic
is exercised without a real GGUF / Ollama install.

Cases covered (matching the spec in the build-step prompt):

1. probe-stale: no marker → exit 1 ``code=probe_stale`` detail mentions
   "no marker found" or "absent"
2. probe-stale: marker too old (>1h) → exit 1 ``code=probe_stale``
3. happy path: 10 fixture prompts, valid Activity per response, stub
   nvidia-smi returns 6115 → exit 0, result file with
   ``schema_validity_percent==100`` and ``peak_vram_mib==6115``;
   asserts exactly 10 chat POSTs and that the unload POST landed first.
4. schema-validity tally: 7 valid + 3 invalid Activity payloads →
   ``schema_validity_percent==70.0``
5. nvidia-smi unavailable: sampler raises ``FileNotFoundError`` →
   result file written with ``peak_vram_mib=null`` and exit 0
6. decision gate 7B-pass: 7B clears thresholds, 3B fails one →
   verdict ``7B`` in the rendered markdown
7. decision gate 3B-pass: 7B fails one, 3B clears → verdict ``3B``
8. decision gate cloud-burst: both fail → verdict ``cloud-burst``
9. atomicity: no ``.tmp`` files survive a successful decision-doc write

Handler-thread assertions are recorded onto ``Handler.chat_calls`` /
``Handler.unload_calls`` and re-asserted on the main thread after
``_stop_server`` — bare ``assert`` inside ``do_POST`` would be swallowed
by ``BaseHTTPRequestHandler``'s exception handler and silently pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from toybox.ai import local_benchmark as bench_mod
from toybox.ai.local_benchmark import LOCAL_RUNTIME_URL_ENV

# ---------------------------------------------------------------------
# Canned activity JSON (matches the Phase G relaxed shape: 3-20 steps)
# ---------------------------------------------------------------------

_CANNED_ACTIVITY: dict[str, Any] = {
    "id": "bench-activity-001",
    "template_id": "bench_template",
    "title": "Benchmark activity",
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
    # Missing required `title`, `template_id`, etc. Pydantic rejects.
    "id": "x",
    "steps": [],
}

_MODEL_ID = "qwen2.5:3b-instruct-q5_K_M"


# ---------------------------------------------------------------------
# SSE helper — turn a final-content string into an OpenAI-compat stream.
# ---------------------------------------------------------------------


def _sse_chunks(content: str, *, completion_tokens: int | None = None) -> bytes:
    """Frame a single-shot ``content`` string as an SSE response body.

    Emits a delta.content chunk followed by ``data: [DONE]``. If
    ``completion_tokens`` is provided, the chunk carries a ``usage``
    field so the benchmark uses the precise token count.
    """
    chunk: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": content},
                "finish_reason": None,
            }
        ],
    }
    if completion_tokens is not None:
        chunk["usage"] = {"completion_tokens": completion_tokens}
    body = b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n"
    body += b"data: [DONE]\n\n"
    return body


# ---------------------------------------------------------------------
# Fake-server helpers
# ---------------------------------------------------------------------


def _make_handler(
    *,
    models_body: dict[str, Any],
    chat_responder: Any,
) -> type[BaseHTTPRequestHandler]:
    """Build a handler class with closed-over fake-runtime behaviour.

    * ``models_body`` — JSON returned by GET ``/v1/models``.
    * ``chat_responder(call_index)`` — returns the SSE bytes body for
      the Nth POST ``/v1/chat/completions`` (or a ``(status, bytes)``
      tuple for an HTTP-error response).

    The handler also accepts POST ``/api/generate`` (Ollama's eviction
    endpoint), recording each call onto ``Handler.unload_calls``.
    """

    class Handler(BaseHTTPRequestHandler):
        chat_calls: list[dict[str, Any]] = []
        unload_calls: list[dict[str, Any]] = []
        # Records ordering across endpoints: each entry is a
        # ("chat" | "unload" | "models", index) tuple. The benchmark
        # must hit "unload" before any "chat".
        event_log: list[tuple[str, int]] = []

        def log_message(self, format: str, *args: Any) -> None:  # silence
            return

        def do_GET(self) -> None:  # noqa: N802 — stdlib signature
            if self.path == "/v1/models":
                Handler.event_log.append(("models", len(Handler.event_log)))
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
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            try:
                request_body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                request_body = {}

            if self.path == "/api/generate":
                Handler.unload_calls.append({"body": request_body})
                Handler.event_log.append(("unload", len(Handler.event_log)))
                payload = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            if self.path != "/v1/chat/completions":
                self.send_response(404)
                self.end_headers()
                return

            call_index = len(Handler.chat_calls)
            Handler.chat_calls.append({"body": request_body})
            Handler.event_log.append(("chat", len(Handler.event_log)))
            result = chat_responder(call_index, request_body)
            if isinstance(result, tuple):
                status, body = result
            else:
                status = 200
                body = result
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


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


def _models_body(model_ids: list[str]) -> dict[str, Any]:
    return {"object": "list", "data": [{"id": mid, "object": "model"} for mid in model_ids]}


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def fixture_path(tmp_path: Path) -> Path:
    """Write a 10-line prompts.jsonl fixture into a temp tree."""
    fx_dir = tmp_path / "tests" / "fixtures" / "eval"
    fx_dir.mkdir(parents=True)
    fx_file = fx_dir / "prompts.jsonl"
    lines = []
    for i in range(1, 11):
        lines.append(
            json.dumps(
                {
                    "id": f"f{i:03d}",
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
        )
    fx_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return fx_file


@pytest.fixture
def marker_dir(tmp_path: Path) -> Path:
    """Empty marker dir; tests that need a probe marker write one explicitly."""
    out = tmp_path / "data" / "models"
    out.mkdir(parents=True)
    return out


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    out = tmp_path / "out" / "benchmark"
    out.mkdir(parents=True)
    return out


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(LOCAL_RUNTIME_URL_ENV, raising=False)
    yield


def _write_fresh_marker(marker_dir: Path, *, age_minutes: float = 0.0) -> Path:
    """Write a probe-pass marker with an ISO timestamp ``age_minutes`` ago."""
    ts = datetime.now(UTC) - timedelta(minutes=age_minutes)
    safe_iso = ts.strftime(bench_mod._MARKER_TS_FORMAT)
    marker = marker_dir / f".probe-pass-{safe_iso}.json"
    marker.write_text(
        json.dumps({"iso_ts": safe_iso, "model_id": _MODEL_ID, "fixture_id": "f001"}),
        encoding="utf-8",
    )
    return marker


def _result_files(output_dir: Path) -> list[Path]:
    return list(output_dir.glob(".benchmark-*.json"))


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_probe_stale_no_marker(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No probe-pass marker → exit 1, code=probe_stale, detail mentions absent."""
    monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, "http://127.0.0.1:1")
    rc = bench_mod.run_benchmark(
        model_id=_MODEL_ID,
        marker_dir=marker_dir,
        output_dir=output_dir,
        fixture_path=fixture_path,
        vram_sampler=lambda: 6115,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "code=probe_stale" in out
    # Detail must explain why — "absent" / "no marker" / "probe absent".
    assert "no marker found" in out or "absent" in out
    assert _result_files(output_dir) == []


def test_probe_stale_marker_too_old(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Marker exists but its ISO timestamp is 90 min ago → probe_stale."""
    monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, "http://127.0.0.1:1")
    _write_fresh_marker(marker_dir, age_minutes=90.0)
    rc = bench_mod.run_benchmark(
        model_id=_MODEL_ID,
        marker_dir=marker_dir,
        output_dir=output_dir,
        fixture_path=fixture_path,
        vram_sampler=lambda: 6115,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "code=probe_stale" in out
    # Detail must surface the >1h staleness.
    assert "stale" in out or ">1h" in out or "1:" in out  # "1:30:00" timedelta repr
    assert _result_files(output_dir) == []


def test_benchmark_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fresh marker + valid Activity per prompt → exit 0, all 10 schema-valid."""
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    def chat(call_index: int, body: dict[str, Any]) -> bytes:
        # Emit a valid Activity for every prompt; include
        # usage.completion_tokens so the precise-token branch is exercised.
        return _sse_chunks(json.dumps(_CANNED_ACTIVITY), completion_tokens=50)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=lambda: 6115,
        )
    finally:
        _stop_server(server, thread)

    out = capsys.readouterr().out
    assert rc == 0, out
    assert "benchmark=PASS" in out

    # 10 chat POSTs total (cold folded into first warm, per docstring).
    assert len(handler.chat_calls) == 10, (
        f"expected 10 chat POSTs (1 cold + 9 warm), got {len(handler.chat_calls)}"
    )
    # Unload was hit, and BEFORE any chat call.
    assert len(handler.unload_calls) >= 1, "unload POST must fire"
    first_unload_idx = next(i for i, (kind, _) in enumerate(handler.event_log) if kind == "unload")
    first_chat_idx = next(i for i, (kind, _) in enumerate(handler.event_log) if kind == "chat")
    assert first_unload_idx < first_chat_idx, "unload must precede first chat call"

    # The unload POST carried the documented Ollama evict shape.
    unload_body = handler.unload_calls[0]["body"]
    assert unload_body.get("model") == _MODEL_ID
    assert unload_body.get("keep_alive") == 0

    # Result file written with the expected fields.
    files = _result_files(output_dir)
    assert len(files) == 1, files
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["model_id"] == _MODEL_ID
    assert payload["fixture_ids"] == [f"f{i:03d}" for i in range(1, 11)]
    assert payload["schema_validity_percent"] == 100.0
    assert payload["peak_vram_mib"] == 6115
    assert isinstance(payload["cold_start_seconds"], (int, float))
    assert isinstance(payload["warm_first_token_latency_seconds_median"], (int, float))
    assert isinstance(payload["steady_state_tps"], (int, float))
    assert len(payload["per_prompt"]) == 10
    # T1 / H1 fix: cold_start_seconds must be the cold call's first-token
    # latency, NOT the total-generation time. The threshold gate (<30 s
    # for 7B, <15 s for 3B) is a load-delay gate; total-generation time
    # would render it meaningless. Both values come from the same
    # measurement → exact equality (after the round-to-3 in the writer).
    assert payload["cold_start_seconds"] == payload["per_prompt"][0]["first_token_seconds"]
    # Per-prompt records carry the documented fields.
    for entry in payload["per_prompt"]:
        assert {
            "fixture_id",
            "first_token_seconds",
            "total_seconds",
            "completion_tokens",
            "schema_valid",
        } <= set(entry.keys())
        assert entry["schema_valid"] is True
        assert entry["completion_tokens"] == 50
    # Filename is colon-free (Windows-safe).
    assert ":" not in files[0].name
    # No leftover .tmp file.
    assert not list(output_dir.glob(".benchmark-*.tmp")), "atomic write should clean up .tmp"


def test_benchmark_schema_validity_tally(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """7 valid + 3 invalid responses → schema_validity_percent == 70.0."""
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    def chat(call_index: int, body: dict[str, Any]) -> bytes:
        # First 7 valid, last 3 invalid (missing title).
        if call_index < 7:
            return _sse_chunks(json.dumps(_CANNED_ACTIVITY), completion_tokens=50)
        return _sse_chunks(json.dumps(_MALFORMED_ACTIVITY), completion_tokens=50)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=lambda: 6115,
        )
    finally:
        _stop_server(server, thread)

    capsys.readouterr()
    assert rc == 0
    files = _result_files(output_dir)
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["schema_validity_percent"] == 70.0
    # Per-prompt tally must mirror the responder behaviour.
    valids = [p["schema_valid"] for p in payload["per_prompt"]]
    assert valids == [True] * 7 + [False] * 3


def test_benchmark_nvidia_smi_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """nvidia-smi raises FileNotFoundError → peak_vram_mib=null, exit 0."""
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    def chat(call_index: int, body: dict[str, Any]) -> bytes:
        return _sse_chunks(json.dumps(_CANNED_ACTIVITY), completion_tokens=50)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)

    def vram_unavailable() -> int:
        raise FileNotFoundError("nvidia-smi not on PATH")

    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=vram_unavailable,
        )
    finally:
        _stop_server(server, thread)

    capsys.readouterr()
    # nvidia-smi missing is COSMETIC, not fatal.
    assert rc == 0
    files = _result_files(output_dir)
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["peak_vram_mib"] is None
    # Schema metrics still computed normally.
    assert payload["schema_validity_percent"] == 100.0


# ---------------------------------------------------------------------
# Decision-doc gate tests
# ---------------------------------------------------------------------

_PASSING_7B_RESULT: dict[str, Any] = {
    "iso_ts": "2026-05-14T10-00-00Z",
    "model_id": "qwen2.5:7b-instruct-q4_K_M",
    "fixture_ids": [f"f{i:03d}" for i in range(1, 11)],
    "cold_start_seconds": 4.5,
    "warm_first_token_latency_seconds_median": 0.32,
    "steady_state_tps": 35.0,
    "peak_vram_mib": 6115,
    "schema_validity_percent": 100.0,
    "per_prompt": [],
}

_PASSING_3B_RESULT: dict[str, Any] = {
    "iso_ts": "2026-05-14T10-00-00Z",
    "model_id": "qwen2.5:3b-instruct-q5_K_M",
    "fixture_ids": [f"f{i:03d}" for i in range(1, 11)],
    "cold_start_seconds": 2.5,
    "warm_first_token_latency_seconds_median": 0.5,
    "steady_state_tps": 80.0,
    "peak_vram_mib": 4500,
    "schema_validity_percent": 100.0,
    "per_prompt": [],
}


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_decision_gate_7b_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """7B clears every threshold, 3B fails VRAM → verdict 7B."""
    p7 = tmp_path / "7b.json"
    p3 = tmp_path / "3b.json"
    failing_3b = dict(_PASSING_3B_RESULT)
    failing_3b["peak_vram_mib"] = 8000  # 3B threshold is <7168
    _write_result(p7, _PASSING_7B_RESULT)
    _write_result(p3, failing_3b)
    out_path = tmp_path / "documentation" / "local-model-decision.md"

    rc = bench_mod.write_decision_doc(
        path_7b=p7,
        path_3b=p3,
        output_path=out_path,
    )
    captured = capsys.readouterr().out
    assert rc == 0
    assert "decision_doc=WRITTEN" in captured
    assert "verdict=7B" in captured

    body = out_path.read_text(encoding="utf-8")
    assert "**Verdict:** 7B" in body
    # Metrics table includes both rows.
    assert "| 7B " in body
    assert "| 3B " in body
    # Contention-answer placeholder is verbatim.
    assert "Operator: fill in (a) sequentialize" in body
    assert "Note on 8 GB hosts" in body


def test_decision_gate_3b_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """7B fails VRAM, 3B clears every threshold → verdict 3B."""
    p7 = tmp_path / "7b.json"
    p3 = tmp_path / "3b.json"
    failing_7b = dict(_PASSING_7B_RESULT)
    failing_7b["peak_vram_mib"] = 12000  # 7B threshold is <11264
    _write_result(p7, failing_7b)
    _write_result(p3, _PASSING_3B_RESULT)
    out_path = tmp_path / "documentation" / "local-model-decision.md"

    rc = bench_mod.write_decision_doc(
        path_7b=p7,
        path_3b=p3,
        output_path=out_path,
    )
    captured = capsys.readouterr().out
    assert rc == 0
    assert "verdict=3B" in captured
    body = out_path.read_text(encoding="utf-8")
    assert "**Verdict:** 3B" in body


def test_decision_gate_cloud_burst(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both 7B and 3B fail at least one threshold → verdict cloud-burst."""
    p7 = tmp_path / "7b.json"
    p3 = tmp_path / "3b.json"
    failing_7b = dict(_PASSING_7B_RESULT)
    failing_7b["peak_vram_mib"] = 12000  # 7B threshold is <11264
    failing_3b = dict(_PASSING_3B_RESULT)
    failing_3b["steady_state_tps"] = 50.0  # 3B threshold is >=60
    _write_result(p7, failing_7b)
    _write_result(p3, failing_3b)
    out_path = tmp_path / "documentation" / "local-model-decision.md"

    rc = bench_mod.write_decision_doc(
        path_7b=p7,
        path_3b=p3,
        output_path=out_path,
    )
    captured = capsys.readouterr().out
    assert rc == 0
    assert "verdict=cloud-burst" in captured
    body = out_path.read_text(encoding="utf-8")
    assert "**Verdict:** cloud-burst" in body


def test_decision_doc_no_leftover_tmp(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After a successful decision-doc write, no .tmp sibling exists.

    Atomicity guard — if ``os.replace`` is dropped, the ``.tmp`` survives
    and downstream readers race-condition on a half-written file.
    """
    p7 = tmp_path / "7b.json"
    p3 = tmp_path / "3b.json"
    _write_result(p7, _PASSING_7B_RESULT)
    _write_result(p3, _PASSING_3B_RESULT)
    doc_dir = tmp_path / "documentation"
    out_path = doc_dir / "local-model-decision.md"

    rc = bench_mod.write_decision_doc(
        path_7b=p7,
        path_3b=p3,
        output_path=out_path,
    )
    capsys.readouterr()
    assert rc == 0
    # The .md file exists.
    assert out_path.is_file()
    # No `.tmp` leftover anywhere under documentation/.
    leftovers = list(doc_dir.rglob("*.tmp"))
    assert leftovers == [], f"unexpected .tmp leftover: {leftovers}"


# ---------------------------------------------------------------------
# T2 — model-loaded check ordering (covers H2 fix)
# ---------------------------------------------------------------------


def test_model_not_loaded_when_chat_succeeds_but_id_absent(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Chat succeeds but /v1/models omits the model id → code=model_not_loaded."""
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    def chat(call_index: int, body: dict[str, Any]) -> bytes:
        return _sse_chunks(json.dumps(_CANNED_ACTIVITY), completion_tokens=50)

    # Note: models_body advertises a DIFFERENT id than what we benchmark.
    handler = _make_handler(
        models_body=_models_body(["some-other-model:7b"]),
        chat_responder=chat,
    )
    server, thread, url = _start_server(handler)
    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=lambda: 6115,
        )
    finally:
        _stop_server(server, thread)

    out = capsys.readouterr().out
    assert rc == 1
    assert "code=model_not_loaded" in out
    # No result file on failure.
    assert _result_files(output_dir) == []


def test_model_not_loaded_when_chat_fails_and_id_absent(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Chat returns HTTP 500 AND id absent → model_not_loaded wins over runtime_unreachable."""
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    def chat(call_index: int, body: dict[str, Any]) -> tuple[int, bytes]:
        # Always fail (simulates Ollama erroring when asked to generate
        # with an unknown model id).
        return (500, b'{"error": "model not found"}')

    handler = _make_handler(
        models_body=_models_body(["some-other-model:7b"]),
        chat_responder=chat,
    )
    server, thread, url = _start_server(handler)
    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=lambda: 6115,
        )
    finally:
        _stop_server(server, thread)

    out = capsys.readouterr().out
    assert rc == 1
    # The diagnostic code must be model_not_loaded — the actionable one.
    assert "code=model_not_loaded" in out
    assert "code=runtime_unreachable" not in out
    assert _result_files(output_dir) == []


# ---------------------------------------------------------------------
# T3 — runtime_unreachable (URLError on unbound port)
# ---------------------------------------------------------------------


def test_runtime_unreachable_on_unbound_port(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No server on the configured port → code=runtime_unreachable, exit 1."""
    _write_fresh_marker(marker_dir, age_minutes=10.0)
    # Bind+release a socket to surface a port that's definitely free.
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Race window between unbind and benchmark: small for a unit test;
    # the URLError is what we want to assert, and any port that's truly
    # unbound will yield ECONNREFUSED on Windows.
    monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, f"http://127.0.0.1:{port}")
    rc = bench_mod.run_benchmark(
        model_id=_MODEL_ID,
        marker_dir=marker_dir,
        output_dir=output_dir,
        fixture_path=fixture_path,
        vram_sampler=lambda: 6115,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "code=runtime_unreachable" in out
    assert _result_files(output_dir) == []


# ---------------------------------------------------------------------
# T4 — streaming_parse_error (malformed SSE)
# ---------------------------------------------------------------------


def test_streaming_parse_error_on_malformed_sse(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Server emits malformed SSE → code=streaming_parse_error, exit 1."""
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    def chat(call_index: int, body: dict[str, Any]) -> bytes:
        return b"data: not-valid-json\n\n"

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=lambda: 6115,
        )
    finally:
        _stop_server(server, thread)

    out = capsys.readouterr().out
    assert rc == 1
    assert "code=streaming_parse_error" in out
    assert _result_files(output_dir) == []


# ---------------------------------------------------------------------
# T5 — results_unreadable for --write-decision-doc
# ---------------------------------------------------------------------


def test_decision_doc_results_unreadable_missing_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pointing --7b-results at a nonexistent path → code=results_unreadable."""
    p3 = tmp_path / "3b.json"
    _write_result(p3, _PASSING_3B_RESULT)
    out_path = tmp_path / "documentation" / "local-model-decision.md"

    rc = bench_mod.write_decision_doc(
        path_7b=tmp_path / "does-not-exist.json",
        path_3b=p3,
        output_path=out_path,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "code=results_unreadable" in out
    assert not out_path.exists()


def test_decision_doc_results_unreadable_malformed_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed result JSON (not an object) → code=results_unreadable."""
    p7 = tmp_path / "7b.json"
    p3 = tmp_path / "3b.json"
    # Valid JSON but not an object — our loader rejects.
    p7.write_text("[1, 2, 3]", encoding="utf-8")
    _write_result(p3, _PASSING_3B_RESULT)
    out_path = tmp_path / "documentation" / "local-model-decision.md"

    rc = bench_mod.write_decision_doc(
        path_7b=p7,
        path_3b=p3,
        output_path=out_path,
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "code=results_unreadable" in out
    assert not out_path.exists()


# ---------------------------------------------------------------------
# T6 — module-independence invariant (no transitive import of local.py)
# ---------------------------------------------------------------------


def test_local_benchmark_does_not_import_local_module() -> None:
    """``local_benchmark.py`` source must not import from ``toybox.ai.local``.

    The two CLIs are deliberately independent (see module docstring) —
    helpers were re-implemented in ``local_benchmark.py`` rather than
    shared, so the modules can evolve separately. ``toybox.ai.local`` is
    pulled into ``sys.modules`` indirectly by ``toybox.ai.__init__``
    (which imports ``capability`` which imports ``local``), so a
    runtime ``'toybox.ai.local' not in sys.modules`` check is
    unenforceable. Source-level grep is the durable signal.

    Run as a subprocess so the assertion is independent of whatever
    pytest's parent process has already imported.
    """
    code = (
        "import ast, sys\n"
        "from importlib.util import find_spec\n"
        "spec = find_spec('toybox.ai.local_benchmark')\n"
        "assert spec is not None and spec.origin is not None\n"
        "with open(spec.origin, 'r', encoding='utf-8') as fh:\n"
        "    source = fh.read()\n"
        "tree = ast.parse(source)\n"
        "for node in ast.walk(tree):\n"
        "    if isinstance(node, ast.ImportFrom):\n"
        "        # ImportFrom: `from <module> import X` — module can be\n"
        "        # absolute ('toybox.ai.local') or relative ('.local', level=1).\n"
        "        if node.module == 'toybox.ai.local':\n"
        "            raise AssertionError(\n"
        "                f'absolute import from toybox.ai.local on line {node.lineno}'\n"
        "            )\n"
        "        if node.level == 1 and node.module == 'local':\n"
        "            raise AssertionError(\n"
        "                f'relative import from .local on line {node.lineno}'\n"
        "            )\n"
        "    elif isinstance(node, ast.Import):\n"
        "        for alias in node.names:\n"
        "            if alias.name == 'toybox.ai.local':\n"
        "                raise AssertionError(\n"
        "                    f'import toybox.ai.local on line {node.lineno}'\n"
        "                )\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert result.returncode == 0, (
        f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------
# T7 — SSE multi-line JSON buffering (covers M3 fix)
# ---------------------------------------------------------------------


def _sse_chunks_multi_line(content_pieces: list[str]) -> bytes:
    """Emit each content piece as a SEPARATE SSE chunk (not multi-line within a chunk).

    Used to verify the parser correctly assembles content across
    successive chunks. (True multi-line ``data:`` framing within one
    SSE message is exercised by ``_sse_chunks_multi_data_lines``.)
    """
    body = b""
    for piece in content_pieces:
        chunk = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": piece},
                    "finish_reason": None,
                }
            ],
        }
        body += b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n"
    body += b"data: [DONE]\n\n"
    return body


def _sse_chunks_multi_data_lines(json_obj: dict[str, Any]) -> bytes:
    """Frame ONE JSON message as MULTIPLE ``data:`` lines (per SSE spec).

    The body is split at a `,` boundary in the JSON, then each piece is
    sent as its own ``data:`` line. SSE clients per spec should
    concatenate consecutive ``data:`` lines (separated by ``\\n``)
    until they hit a blank line.
    """
    serialised = json.dumps(json_obj)
    # Split into two pieces at the first comma.
    split_at = serialised.index(",", 1)
    part_a = serialised[: split_at + 1]
    part_b = serialised[split_at + 1 :]
    body = (
        b"data: " + part_a.encode("utf-8") + b"\n"
        b"data: " + part_b.encode("utf-8") + b"\n\n"
        b"data: [DONE]\n\n"
    )
    return body


def test_sse_parser_buffers_multi_data_lines(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Single JSON message split across multiple data: lines assembles correctly."""
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    # Construct a chunk-message that will be split.
    chunk = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": json.dumps(_CANNED_ACTIVITY)},
                "finish_reason": None,
            }
        ],
        "usage": {"completion_tokens": 50},
    }

    def chat(call_index: int, body: dict[str, Any]) -> bytes:
        return _sse_chunks_multi_data_lines(chunk)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=lambda: 6115,
        )
    finally:
        _stop_server(server, thread)

    capsys.readouterr()
    assert rc == 0
    files = _result_files(output_dir)
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    # All 10 responses parsed and validated as Activity.
    assert payload["schema_validity_percent"] == 100.0


# ---------------------------------------------------------------------
# T8 — SSE empty keepalive (covers M4 fix)
# ---------------------------------------------------------------------


def _sse_chunks_with_keepalive(content: str, *, completion_tokens: int) -> bytes:
    """Emit an SSE stream with a ``data: \\n\\n`` keepalive interleaved.

    The parser must skip the empty data line (treat as keepalive) and
    continue to the real content chunk.
    """
    chunk = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": content},
                "finish_reason": None,
            }
        ],
        "usage": {"completion_tokens": completion_tokens},
    }
    body = b"data: \n\n"  # empty keepalive — must be skipped
    body += b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n"
    body += b"data: \n\n"  # another keepalive
    body += b"data: [DONE]\n\n"
    return body


def test_sse_parser_skips_empty_keepalive(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``data:\\n\\n`` keepalive between content chunks is skipped silently."""
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    def chat(call_index: int, body: dict[str, Any]) -> bytes:
        return _sse_chunks_with_keepalive(json.dumps(_CANNED_ACTIVITY), completion_tokens=50)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=lambda: 6115,
        )
    finally:
        _stop_server(server, thread)

    capsys.readouterr()
    assert rc == 0
    files = _result_files(output_dir)
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["schema_validity_percent"] == 100.0


# ---------------------------------------------------------------------
# T9 — .tmp cleanup on os.replace failure (covers M7 fix)
# ---------------------------------------------------------------------


def test_decision_doc_tmp_cleaned_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If os.replace raises, the .tmp orphan is swept before re-raise."""
    p7 = tmp_path / "7b.json"
    p3 = tmp_path / "3b.json"
    _write_result(p7, _PASSING_7B_RESULT)
    _write_result(p3, _PASSING_3B_RESULT)
    doc_dir = tmp_path / "documentation"
    out_path = doc_dir / "local-model-decision.md"

    original_replace = os.replace

    def boom(src: Any, dst: Any) -> None:
        # Mimic a Windows "dest is open in another process" or POSIX
        # cross-filesystem EXDEV error.
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="simulated replace failure"):
        bench_mod.write_decision_doc(
            path_7b=p7,
            path_3b=p3,
            output_path=out_path,
        )
    capsys.readouterr()

    # No .tmp leftover; the .md file was never created (replace failed).
    leftovers = list(doc_dir.rglob("*.tmp"))
    assert leftovers == [], f"unexpected .tmp leftover: {leftovers}"
    assert not out_path.exists()
    # Restore so other tests behave normally (monkeypatch.setattr handles it,
    # but we reference the original to silence unused-variable warning).
    _ = original_replace


# ---------------------------------------------------------------------
# T10 — subprocess.TimeoutExpired on nvidia-smi (covers H16 fix)
# ---------------------------------------------------------------------


def test_vram_sampler_handles_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sampler that raises subprocess.TimeoutExpired marks itself unavailable.

    Regression for H16: ``TimeoutExpired`` is NOT a subclass of
    ``OSError``; before the fix it escaped the except clause and killed
    the sampler thread silently (or worse, propagated out of the
    benchmark).
    """
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    def chat(call_index: int, body: dict[str, Any]) -> bytes:
        return _sse_chunks(json.dumps(_CANNED_ACTIVITY), completion_tokens=50)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)

    def hanging_sampler() -> int:
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=5.0)

    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=hanging_sampler,
        )
    finally:
        _stop_server(server, thread)

    capsys.readouterr()
    # Benchmark completes despite the TimeoutExpired sampler — cosmetic
    # failure mode is "peak_vram_mib=null", not "whole run dies".
    assert rc == 0
    files = _result_files(output_dir)
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["peak_vram_mib"] is None


# ---------------------------------------------------------------------
# M6 sanity — tps_samples_dropped counter surfaces in the result file
# ---------------------------------------------------------------------


def test_tps_samples_dropped_field_surfaces_in_output(
    monkeypatch: pytest.MonkeyPatch,
    fixture_path: Path,
    marker_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``tps_samples_dropped`` is present and non-negative in every result file.

    M6 honesty: dropped samples must be visible so a downstream reader
    can sanity-check ``steady_state_tps``. The fake server is fast enough
    that warm spans collapse to ~0, so most samples WILL be dropped — we
    assert the field is surfaced (not that the count is zero).
    """
    _write_fresh_marker(marker_dir, age_minutes=10.0)

    def chat(call_index: int, body: dict[str, Any]) -> bytes:
        return _sse_chunks(json.dumps(_CANNED_ACTIVITY), completion_tokens=50)

    handler = _make_handler(models_body=_models_body([_MODEL_ID]), chat_responder=chat)
    server, thread, url = _start_server(handler)
    try:
        monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, url)
        rc = bench_mod.run_benchmark(
            model_id=_MODEL_ID,
            marker_dir=marker_dir,
            output_dir=output_dir,
            fixture_path=fixture_path,
            vram_sampler=lambda: 6115,
        )
    finally:
        _stop_server(server, thread)

    capsys.readouterr()
    assert rc == 0
    payload = json.loads(_result_files(output_dir)[0].read_text(encoding="utf-8"))
    # Field must exist and be a non-negative integer.
    assert "tps_samples_dropped" in payload
    assert isinstance(payload["tps_samples_dropped"], int)
    assert payload["tps_samples_dropped"] >= 0
    # The 9 warm calls (idx 1..9) are the candidate population.
    assert payload["tps_samples_dropped"] <= 9
