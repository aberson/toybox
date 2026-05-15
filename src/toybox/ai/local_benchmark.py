"""Phase E Step 25c (E1c) — local-model benchmark + decision-doc CLI.

Sibling module to :mod:`toybox.ai.local` (Step 25b shipped the ``--probe``
CLI). This module ships two operator-facing entry points:

1. ``uv run python -m toybox.ai.local_benchmark --benchmark --model <tag>``

   Runs a 10-prompt benchmark against a locally-hosted OpenAI-compatible
   runtime (Ollama, LM Studio, llama.cpp) and writes the measured metrics
   to ``data/models/.benchmark-<model-slug>-<iso>.json``. Pre-flight gates
   on a recent ``.probe-pass-*.json`` marker from the Step 25b probe
   (>1h-old or missing → ``code=probe_stale`` exit 1, no output written).

2. ``uv run python -m toybox.ai.local_benchmark --write-decision-doc \\
       --7b-results <path> --3b-results <path>``

   Reads two benchmark result files, applies the E1c threshold gate, and
   writes ``documentation/local-model-decision.md`` with the verdict
   (``7B`` / ``3B`` / ``cloud-burst``) and per-model metrics table.

The two modes are mutually-exclusive flags on a single argparse parser
(matching ``__main__.py``'s shape); choosing one or the other is
``--benchmark`` vs ``--write-decision-doc``.

Module independence
-------------------

Per the Step 25c plan, this module does NOT import from
:mod:`toybox.ai.local` — the probe-CLI's helpers (``_get_json``,
``_resolve_repo_root``, the ISO format constant) are re-implemented
here so the two CLIs can evolve independently. Tests assert the marker
filename ISO regex separately for each.

Cold-start measurement
----------------------

"Cold start" is measured for the FIRST prompt of the run only, after
explicitly unloading the model via Ollama's documented evict mechanism
(``POST /api/generate`` with ``keep_alive: 0`` and an empty prompt).
The 10 chat POSTs are then prompt#1 (cold) + prompt#2-10 (warm). Total
POSTs per run: 1 unload + 10 chat (= 11 HTTP calls; tests count only
the 10 chat calls).

We report ``cold_start_seconds`` as the **first-token latency** on the
cold call (i.e. ``per_prompt[0].first_token_seconds``). That value is
the wall-clock from request-send to the first SSE content chunk and
captures model-load delay + first-token compute — exactly what the
E1c threshold gate (<30 s for 7B, <15 s for 3B) is designed to bound.
Reporting total-generation time would be dominated by token-emission
throughput and render those thresholds meaningless.

Steady-state TPS
----------------

``(total_response_tokens - 1) / (total_wall_clock - first_token_latency)``,
averaged across prompts 2-10 (the warm calls). Token count is
``usage.completion_tokens`` from the streaming response's final chunk
when present; otherwise it falls back to a whitespace-split count of
the assembled content. The chosen-path is recorded per-prompt for audit.

Peak VRAM
---------

Sampled in a background thread via ``nvidia-smi --query-gpu=memory.used
--format=csv,noheader,nounits`` every 0.5 s. The sampler thread starts
before the first POST and ends after the last. Max observed value (in
MiB) is reported. If ``nvidia-smi`` is unavailable (PATH miss or
non-zero exit), ``peak_vram_mib`` is set to ``null`` and the run
continues — this is COSMETIC, not a fatal failure (per spec). Operators
on non-CUDA boxes still get the latency/TPS/schema metrics.

SSE parsing
-----------

Ollama's ``/v1/chat/completions`` with ``stream: true`` emits the
OpenAI-compatible SSE format: chunks framed as ``data: {...}\\n\\n``
with a trailing ``data: [DONE]\\n\\n`` sentinel. We read line-by-line,
strip the ``data: `` prefix, and parse each non-``[DONE]`` line as JSON.
Time-to-first-content-chunk = wall-clock from the request send to the
first chunk whose ``choices[0].delta.content`` is non-empty.

Failure paths (no output file on any):

* ``probe_stale`` — marker missing or >1h old
* ``runtime_unreachable`` — URLError on any HTTP request
* ``model_not_loaded`` — model id not in ``/v1/models`` after re-load
* ``streaming_parse_error`` — SSE chunks malformed
* ``interrupt`` — ``KeyboardInterrupt`` → exit 130, no output
* (``nvidia_smi_unavailable`` is cosmetic: peak_vram_mib=null, exit 0)

Each failure exits 1 (except interrupt → 130) with
``benchmark=FAIL code=<code> detail=<detail>`` on stdout.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

from pydantic import ValidationError

from ..activities.models import Activity

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Env + constants (independent of toybox.ai.local — see module docstring)
# ---------------------------------------------------------------------

#: Env var the operator sets to point at a running local OpenAI-compatible
#: runtime. Default matches Ollama's stock listener (mirrors
#: :mod:`toybox.ai.local` constants but re-declared here per the
#: module-independence rule in the spec).
LOCAL_RUNTIME_URL_ENV: Final[str] = "TOYBOX_LOCAL_RUNTIME_URL"
DEFAULT_LOCAL_RUNTIME_URL: Final[str] = "http://localhost:11434"

#: Where probe-pass markers (written by ``toybox.ai.local --probe``)
#: live. We READ them here to gate the benchmark on a recent probe.
PROBE_MARKER_DIR: Final[Path] = Path("data/models")

#: Where the benchmark result JSON is written. Same parent as the probe
#: marker directory so an operator only has to inspect one location.
BENCHMARK_OUTPUT_DIR: Final[Path] = Path("data/models")

#: How fresh a probe marker must be for the benchmark to start.
PROBE_MAX_AGE: Final[timedelta] = timedelta(hours=1)

#: Where the decision-doc is written by ``--write-decision-doc``.
DECISION_DOC_PATH: Final[Path] = Path("documentation/local-model-decision.md")

#: ISO format used in marker / benchmark filenames. Colon-free for
#: Windows filesystem compatibility (Windows rejects ``:`` in filenames).
_MARKER_TS_FORMAT: Final[str] = "%Y-%m-%dT%H-%M-%SZ"

#: Pre-compiled regex matching the marker filename ISO substring.
#: Used to parse the timestamp out of the marker filename (and also
#: cross-asserted with the in-file ``iso_ts``).
_MARKER_ISO_RE: Final[re.Pattern[str]] = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z")

#: Per-HTTP-call socket timeout (seconds). Streaming reads can run
#: longer than a normal probe — keep generous so a slow 7B doesn't
#: trip on the first token.
_HTTP_CALL_TIMEOUT_SEC: Final[float] = 180.0

#: Default sampling cadence for the nvidia-smi background poller.
_VRAM_SAMPLE_INTERVAL_SEC: Final[float] = 0.5

#: Context length we send in the Ollama options. Spec calls for 4 K.
_NUM_CTX: Final[int] = 4096

#: Chat sampling defaults (matches toybox.ai.local for parity).
_CHAT_MAX_TOKENS: Final[int] = 1024
_CHAT_TEMPERATURE: Final[float] = 0.7

#: How many fixture prompts to run (first N lines of prompts.jsonl).
_BENCHMARK_PROMPT_COUNT: Final[int] = 10

#: How many characters of error detail to surface in the failure
#: envelope. Matches the cap in toybox.ai.local.
_ERROR_EXCERPT_CAP: Final[int] = 400

_PROBE_SYSTEM_PROMPT: Final[str] = (
    "You emit ONE JSON object matching the toybox Activity schema. "
    "Required fields: id (non-empty string), template_id (non-empty), "
    "title (non-empty), steps (array of 3-20 objects each with "
    "step_index (>=0) and text (1-600 chars)), version (>=1), "
    "metadata (object), toy_ids (array of strings). "
    "Emit ONLY the JSON object, no prose."
)

# ---------------------------------------------------------------------
# Threshold gates (E1c spec)
# ---------------------------------------------------------------------

#: 7B thresholds: cold<30s, warm-first-token<2s, TPS>=30, VRAM<11 GiB,
#: schema validity == 100%. ``null`` peak_vram_mib treated as failing.
_7B_THRESHOLDS: Final[dict[str, float]] = {
    "cold_start_seconds": 30.0,
    "warm_first_token_latency_seconds_median": 2.0,
    "steady_state_tps": 30.0,
    "peak_vram_mib": 11264.0,
    "schema_validity_percent": 100.0,
}

#: 3B thresholds: cold<15s, warm-first-token<1s, TPS>=60, VRAM<7 GiB,
#: schema validity == 100%.
_3B_THRESHOLDS: Final[dict[str, float]] = {
    "cold_start_seconds": 15.0,
    "warm_first_token_latency_seconds_median": 1.0,
    "steady_state_tps": 60.0,
    "peak_vram_mib": 7168.0,
    "schema_validity_percent": 100.0,
}


# ---------------------------------------------------------------------
# Repo-root resolution (re-implemented per module-independence rule)
# ---------------------------------------------------------------------


def _resolve_repo_root() -> Path:
    """Walk up from this file to find the repo root (``pyproject.toml``).

    Mirrors the walk-up in :mod:`toybox.ai.local` but re-implemented
    here so the two modules can evolve independently.
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[3] if len(here.parents) > 3 else here.parent


_REPO_ROOT: Final[Path] = _resolve_repo_root()

#: Fixture path the benchmark reads from. Anchored to the repo root so
#: the CLI works from any cwd. Tests monkeypatch this attribute.
_BENCHMARK_FIXTURE_REL: Final[Path] = _REPO_ROOT / "tests" / "fixtures" / "eval" / "prompts.jsonl"


# ---------------------------------------------------------------------
# Control-flow exception
# ---------------------------------------------------------------------


class _BenchmarkFailure(Exception):
    """Internal control-flow exception with a stable error code.

    ``code`` matches one of the documented exit-1 codes
    (``probe_stale``, ``runtime_unreachable``, ``model_not_loaded``,
    ``streaming_parse_error``, ``results_unreadable``). ``detail`` is
    rendered verbatim on the failure line.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------
# HTTP helpers (urllib only — independent of toybox.ai.local)
# ---------------------------------------------------------------------


def _resolve_runtime_url() -> str:
    """Return the configured runtime base URL with any trailing slash stripped."""
    return os.environ.get(LOCAL_RUNTIME_URL_ENV, DEFAULT_LOCAL_RUNTIME_URL).rstrip("/")


def _get_json(url: str, timeout: float) -> tuple[int, Any]:
    """Synchronous GET returning ``(status, parsed_json_body)``.

    URLError / TimeoutError bubble up as ``_BenchmarkFailure`` with
    ``runtime_unreachable``.
    """
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = int(resp.status)
    except urllib.error.URLError as exc:
        raise _BenchmarkFailure("runtime_unreachable", str(exc)) from exc
    except TimeoutError as exc:
        raise _BenchmarkFailure("runtime_unreachable", "timeout") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise _BenchmarkFailure("runtime_unreachable", f"malformed JSON: {exc}") from exc
    return status, parsed


def _format_user_prompt(fixture: dict[str, Any]) -> str:
    """Build a compact human-readable summary of the fixture for the model.

    Same shape as :mod:`toybox.ai.local` — re-implemented per the
    module-independence rule.
    """
    child = fixture.get("child_profile", {}) or {}
    return (
        f"persona={fixture.get('persona', '?')} "
        f"child_age={child.get('age', '?')} "
        f"child_name={child.get('name', '?')} "
        f"available_toys={fixture.get('available_toys', [])} "
        f"transcript_window={fixture.get('transcript_window', '')!r} "
        f"trigger={fixture.get('trigger', '?')}"
    )


# ---------------------------------------------------------------------
# Probe-staleness gate
# ---------------------------------------------------------------------


def _parse_marker_iso(marker_path: Path) -> datetime | None:
    """Parse the ISO timestamp out of a marker filename.

    Returns ``None`` if the filename doesn't carry the expected pattern.
    Falls back to the file's mtime ONLY at the caller's discretion —
    here we just return ``None`` so the caller knows the parse failed.
    """
    match = _MARKER_ISO_RE.search(marker_path.name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(0), _MARKER_TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _find_latest_probe_marker(marker_dir: Path) -> Path | None:
    """Return the most-recent probe-pass marker file, or ``None`` if absent.

    "Most-recent" is determined by the parsed timestamp in the filename
    (NOT mtime — the file may have been copied across hosts). Files with
    unparseable filenames are skipped.
    """
    if not marker_dir.is_dir():
        return None
    candidates: list[tuple[datetime, Path]] = []
    for path in marker_dir.glob(".probe-pass-*.json"):
        ts = _parse_marker_iso(path)
        if ts is not None:
            candidates.append((ts, path))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def _assert_probe_fresh(marker_dir: Path, *, now: datetime | None = None) -> Path:
    """Raise ``_BenchmarkFailure(probe_stale)`` if no fresh marker is present.

    A "fresh" marker is one whose filename ISO timestamp is within
    :data:`PROBE_MAX_AGE` of ``now``. Returns the marker path on
    success. ``now`` is parameterised so tests can pin a deterministic
    clock without monkeypatching ``datetime``.
    """
    latest = _find_latest_probe_marker(marker_dir)
    if latest is None:
        raise _BenchmarkFailure(
            "probe_stale",
            f"no marker found in {marker_dir} (probe absent — run --probe first)",
        )
    ts = _parse_marker_iso(latest)
    if ts is None:
        raise _BenchmarkFailure(
            "probe_stale",
            f"marker {latest.name} has unparseable ISO timestamp",
        )
    current = now if now is not None else datetime.now(UTC)
    age = current - ts
    if age > PROBE_MAX_AGE:
        raise _BenchmarkFailure(
            "probe_stale",
            f"marker {latest.name} is stale (age={age}, >1h limit)",
        )
    return latest


# ---------------------------------------------------------------------
# nvidia-smi VRAM sampler
# ---------------------------------------------------------------------


def _sample_vram_mib() -> int:
    """Run ``nvidia-smi`` once and return the reported used VRAM in MiB.

    Raises ``FileNotFoundError`` if ``nvidia-smi`` is not on PATH.
    Raises ``subprocess.CalledProcessError`` on non-zero exit. Raises
    ``ValueError`` on unparseable output. Callers convert these to a
    "VRAM unavailable" outcome (cosmetic — no fatal failure).
    """
    if shutil.which("nvidia-smi") is None:
        raise FileNotFoundError("nvidia-smi not on PATH")
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=5.0,
    )
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return int(line.strip())


class _VRAMSampler:
    """Background thread sampling ``nvidia-smi`` every ``interval`` seconds.

    Designed to start before the first benchmark POST and stop after the
    last. If the first ``_sample_vram_mib`` call raises (no nvidia-smi,
    or non-zero exit), the sampler records ``available=False`` and the
    thread exits cleanly — :func:`peak_mib` then returns ``None``.

    Tests monkeypatch the ``_sampler`` callable so the production
    ``subprocess.run`` is never invoked during unit tests.
    """

    def __init__(
        self,
        *,
        interval: float = _VRAM_SAMPLE_INTERVAL_SEC,
        sampler: Any = None,
    ) -> None:
        self._interval = interval
        self._sampler = sampler if sampler is not None else _sample_vram_mib
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak: int | None = None
        self._available = True

    def start(self) -> None:
        """Start the background sampler. Idempotent."""
        if self._thread is not None:
            return
        # Take an initial sample synchronously so a fast benchmark
        # (e.g. a test) still records at least one VRAM reading. If the
        # initial sample raises, mark the sampler unavailable up-front
        # and skip spawning the thread.
        #
        # ``subprocess.TimeoutExpired`` is NOT a subclass of ``OSError``;
        # it inherits from ``subprocess.SubprocessError(Exception)``. Add
        # it explicitly so a stuck ``nvidia-smi`` (e.g. on a wedged WDDM
        # driver) is treated as "unavailable" rather than escaping and
        # propagating out of the benchmark.
        try:
            self._peak = int(self._sampler())
        except (
            FileNotFoundError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            ValueError,
            OSError,
        ) as exc:
            _logger.warning(
                "VRAM sampler initial sample failed (%s: %s); peak_vram_mib will be null",
                type(exc).__name__,
                exc,
            )
            self._available = False
            return
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Stop the background sampler and join. Idempotent."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def peak_mib(self) -> int | None:
        """Return the max observed sample, or ``None`` if unavailable."""
        return self._peak if self._available else None

    @property
    def available(self) -> bool:
        """``True`` if at least one VRAM sample succeeded; ``False`` if unavailable."""
        return self._available

    def _run(self) -> None:
        # ``_peak`` is already seeded by ``start()``'s initial sample.
        # See ``start`` for why ``subprocess.TimeoutExpired`` is listed
        # explicitly (not an ``OSError`` subclass).
        while not self._stop_event.wait(self._interval):
            try:
                sample = int(self._sampler())
            except (
                FileNotFoundError,
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                ValueError,
                OSError,
            ) as exc:
                # One bad sample doesn't poison the run — continue.
                _logger.warning(
                    "VRAM sample failed mid-run (%s: %s); skipping interval",
                    type(exc).__name__,
                    exc,
                )
                continue
            if self._peak is None or sample > self._peak:
                self._peak = sample


# ---------------------------------------------------------------------
# SSE streaming chat
# ---------------------------------------------------------------------


def _chat_stream_payload(model_id: str, system_msg: str, user_msg: str) -> dict[str, Any]:
    """Build the JSON body for a streaming chat-completions POST."""
    return {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "stream": True,
        "max_tokens": _CHAT_MAX_TOKENS,
        "temperature": _CHAT_TEMPERATURE,
        "options": {"num_ctx": _NUM_CTX},
    }


def _stream_chat_completion(
    chat_url: str,
    model_id: str,
    system_msg: str,
    user_msg: str,
    *,
    timeout: float,
) -> tuple[str, float, float, int | None]:
    """POST a streaming chat-completions request and return measurements.

    Returns a 4-tuple::

        (assembled_content, first_token_seconds, total_seconds,
         completion_tokens_or_None)

    * ``assembled_content`` — the concatenated ``delta.content`` across
      all streamed chunks (this is the JSON-as-string the model emitted).
    * ``first_token_seconds`` — wall-clock from request send to the
      first SSE chunk whose ``delta.content`` is non-empty.
    * ``total_seconds`` — wall-clock from request send to the
      ``data: [DONE]`` sentinel (or EOF if Ollama doesn't send one).
    * ``completion_tokens_or_None`` — ``usage.completion_tokens`` from
      the final chunk's ``usage`` field if present; ``None`` otherwise.
      Caller falls back to a whitespace-split count when ``None``.

    Raises ``_BenchmarkFailure``:

    * ``runtime_unreachable`` — URLError / TimeoutError / HTTPError /
      non-2xx response.
    * ``streaming_parse_error`` — a ``data: `` line is malformed JSON
      (not the ``[DONE]`` sentinel), or the response stream contains no
      ``delta.content`` chunks at all.
    """
    body = json.dumps(_chat_stream_payload(model_id, system_msg, user_msg)).encode("utf-8")
    req = urllib.request.Request(
        chat_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    start = time.monotonic()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise _BenchmarkFailure(
            "runtime_unreachable",
            f"chat completions returned HTTP {int(exc.code)}",
        ) from exc
    except urllib.error.URLError as exc:
        raise _BenchmarkFailure("runtime_unreachable", str(exc)) from exc
    except TimeoutError as exc:
        raise _BenchmarkFailure("runtime_unreachable", "timeout") from exc

    assembled: list[str] = []
    first_token_seconds: float | None = None
    completion_tokens: int | None = None
    # SSE message-boundary buffer: accumulate consecutive ``data:`` lines
    # and parse on the blank-line delimiter (per the SSE spec — a single
    # message can be split across multiple ``data:`` lines, though it's
    # rare for OpenAI-compat servers it's documented behaviour).
    pending_data: list[str] = []

    def _flush_pending() -> bool:
        """Parse the buffered ``data:`` lines as one SSE message.

        Returns ``True`` if a ``[DONE]`` sentinel was seen (caller should
        stop). Empty-buffer or keepalive-only buffer is a no-op.
        """
        nonlocal first_token_seconds, completion_tokens
        if not pending_data:
            return False
        payload_str = "\n".join(pending_data).strip()
        pending_data.clear()
        if not payload_str:
            # Pure keepalive (``data: \n\n``) — skip silently.
            return False
        if payload_str == "[DONE]":
            return True
        try:
            chunk = json.loads(payload_str)
        except json.JSONDecodeError as exc:
            raise _BenchmarkFailure(
                "streaming_parse_error",
                f"chunk not JSON: {str(exc)[:_ERROR_EXCERPT_CAP]}",
            ) from exc
        if not isinstance(chunk, dict):
            raise _BenchmarkFailure(
                "streaming_parse_error",
                f"chunk not an object: {type(chunk)!r}",
            )
        # Accumulate delta.content across choices[0].
        content_piece = _extract_delta_content(chunk)
        if content_piece:
            if first_token_seconds is None:
                first_token_seconds = time.monotonic() - start
            assembled.append(content_piece)
        # Final chunk MAY carry usage.completion_tokens (Ollama emits
        # this; some OpenAI-compat servers do not). Some servers also
        # emit ``completion_tokens`` as a float (``142.0``) or string
        # (``"142"``) — coerce both.
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            ct = usage.get("completion_tokens")
            coerced = _coerce_completion_tokens(ct)
            if coerced is not None:
                completion_tokens = coerced
        return False

    try:
        # ``readline`` here is iterator-safe on urllib's HTTPResponse:
        # each call returns one ``\n``-terminated chunk. SSE framing is
        # ``data: <json>\n\n`` — a blank line terminates a message. We
        # buffer consecutive ``data:`` lines into ``pending_data`` and
        # parse on each blank line.
        with resp:
            done = False
            for raw_line in resp:
                if done:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    # Blank line = message boundary; flush the buffer.
                    if _flush_pending():
                        done = True
                    continue
                if line.startswith("data:"):
                    # Strip the leading ``data:`` plus the conventional
                    # single space; preserve internal whitespace so a
                    # multi-line JSON body re-assembles cleanly.
                    value = line[len("data:") :]
                    if value.startswith(" "):
                        value = value[1:]
                    pending_data.append(value)
                # Other SSE lines (``event:``, ``:``-comment, ``id:``,
                # etc.) are ignored.
            # End of stream — flush any final pending message that
            # didn't have a trailing blank line.
            if not done:
                _flush_pending()
    except _BenchmarkFailure:
        raise
    except (urllib.error.URLError, TimeoutError) as exc:
        raise _BenchmarkFailure("runtime_unreachable", f"stream read failed: {exc}") from exc

    total_seconds = time.monotonic() - start
    if first_token_seconds is None:
        raise _BenchmarkFailure(
            "streaming_parse_error",
            "no delta.content chunks observed in stream",
        )
    return "".join(assembled), first_token_seconds, total_seconds, completion_tokens


def _extract_delta_content(chunk: dict[str, Any]) -> str:
    """Pull ``choices[0].delta.content`` out of a streaming chunk, or ``""``."""
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    if isinstance(content, str):
        return content
    return ""


def _coerce_completion_tokens(value: Any) -> int | None:
    """Coerce ``usage.completion_tokens`` to ``int``, tolerating server variance.

    Some OpenAI-compat servers emit ``142`` (int), ``142.0`` (float), or
    ``"142"`` (string). Return the coerced ``int`` on success, ``None``
    on unrecognised shapes — caller falls back to a whitespace-split
    count of the assembled content.
    """
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int`` — explicitly reject so a
        # stray ``True`` doesn't become a token count of 1.
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value)
        except (OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------
# Model unload (Ollama-specific evict)
# ---------------------------------------------------------------------


def _unload_model(runtime_url: str, model_id: str, *, timeout: float) -> None:
    """POST to ``/api/generate`` with ``keep_alive: 0`` to evict the model.

    Documented Ollama mechanism for forcing a model unload — pass an
    empty prompt and ``keep_alive: 0`` (the default keep_alive is 5m).
    The next ``/v1/chat/completions`` then re-loads the model from disk,
    which is what we measure as "cold start".

    HTTP-level errors do NOT fail the run — if the runtime doesn't
    support the eviction endpoint we fall through to whatever warm /
    cold state it already had, and the measured "cold" number is the
    runtime's natural state. Log a warning so a curious operator can
    diagnose. URLError IS fatal (the runtime is unreachable).
    """
    payload = {"model": model_id, "prompt": "", "keep_alive": 0}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{runtime_url}/api/generate",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        _logger.warning(
            "model unload via /api/generate returned HTTP %s (continuing)",
            int(exc.code),
        )
    except urllib.error.URLError as exc:
        raise _BenchmarkFailure("runtime_unreachable", f"unload request failed: {exc}") from exc
    except TimeoutError as exc:
        raise _BenchmarkFailure("runtime_unreachable", "unload timeout") from exc


# ---------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------


def _read_first_n_fixtures(fixture_path: Path, n: int) -> list[dict[str, Any]]:
    """Read the first ``n`` lines of ``prompts.jsonl`` and parse each.

    Raises ``_BenchmarkFailure(invalid_json)`` if the file is missing,
    a line is malformed, or fewer than ``n`` lines are present.
    """
    try:
        with fixture_path.open("r", encoding="utf-8") as fh:
            lines = [fh.readline() for _ in range(n)]
    except (FileNotFoundError, OSError) as exc:
        raise _BenchmarkFailure(
            "invalid_json", f"fixture {fixture_path} not readable: {exc}"
        ) from exc
    parsed: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        if not line.strip():
            raise _BenchmarkFailure(
                "invalid_json",
                f"fixture {fixture_path} has fewer than {n} non-empty lines",
            )
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _BenchmarkFailure(
                "invalid_json",
                f"fixture {fixture_path} line {idx + 1} malformed: {exc}",
            ) from exc
        if not isinstance(obj, dict):
            raise _BenchmarkFailure(
                "invalid_json",
                f"fixture line {idx + 1} not an object: {type(obj)!r}",
            )
        parsed.append(cast(dict[str, Any], obj))
    return parsed


# ---------------------------------------------------------------------
# Model-availability check (Ollama re-loads on first generate, so this
# runs AFTER the first chat call, not before)
# ---------------------------------------------------------------------


def _verify_model_in_models_response(models_response: Any, model_id: str) -> None:
    """Raise ``_BenchmarkFailure(model_not_loaded)`` if ``model_id`` is absent."""
    if not isinstance(models_response, dict):
        raise _BenchmarkFailure("model_not_loaded", f"models response not an object: {model_id}")
    data = models_response.get("data")
    if not isinstance(data, list):
        raise _BenchmarkFailure(
            "model_not_loaded",
            f"models response missing 'data' array: {model_id}",
        )
    ids = [d["id"] for d in data if isinstance(d, dict) and isinstance(d.get("id"), str)]
    if model_id not in ids:
        raise _BenchmarkFailure(
            "model_not_loaded",
            f"model {model_id!r} not in /v1/models (have: {ids})",
        )


# ---------------------------------------------------------------------
# Result-file writer
# ---------------------------------------------------------------------


def _model_slug(model_id: str) -> str:
    """Make a filesystem-safe slug from an Ollama model tag.

    Replaces ``:`` and ``/`` with ``-`` so the slug is valid on Windows
    filesystems. Other characters pass through.
    """
    return model_id.replace(":", "-").replace("/", "-")


def _write_benchmark_result(
    *,
    model_id: str,
    iso_ts: str,
    fixture_ids: list[str],
    cold_start_seconds: float,
    warm_first_token_median: float,
    steady_state_tps: float,
    peak_vram_mib: int | None,
    schema_validity_percent: float,
    per_prompt: list[dict[str, Any]],
    output_dir: Path,
    tps_samples_dropped: int = 0,
) -> Path:
    """Write the benchmark JSON atomically; return its path.

    Filename: ``.benchmark-<model-slug>-<iso>.json`` (colon-free for
    Windows). Atomic write via ``.tmp`` + ``os.replace`` — matches the
    E1b marker write pattern.

    ``tps_samples_dropped`` counts warm prompts whose TPS sample was
    rejected (response was a single token, or wall-clock span was
    non-positive). The count is surfaced in the JSON so a downstream
    reader can sanity-check that ``steady_state_tps`` came from a
    representative sample.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = _model_slug(model_id)
    out_path = output_dir / f".benchmark-{slug}-{iso_ts}.json"
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    payload: dict[str, Any] = {
        "iso_ts": iso_ts,
        "model_id": model_id,
        "fixture_ids": fixture_ids,
        "cold_start_seconds": round(cold_start_seconds, 3),
        "warm_first_token_latency_seconds_median": round(warm_first_token_median, 3),
        "steady_state_tps": round(steady_state_tps, 3),
        "peak_vram_mib": peak_vram_mib,
        "schema_validity_percent": round(schema_validity_percent, 2),
        "tps_samples_dropped": tps_samples_dropped,
        "per_prompt": per_prompt,
    }
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.replace(tmp_path, out_path)
    except OSError:
        # Cross-filesystem ``replace`` (POSIX EXDEV) or Windows "dest is
        # held open" leaves the ``.tmp`` behind. Sweep it before letting
        # the caller see the original failure.
        Path(tmp_path).unlink(missing_ok=True)
        raise
    return out_path


# ---------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------


def run_benchmark(
    *,
    model_id: str,
    marker_dir: Path | None = None,
    output_dir: Path | None = None,
    fixture_path: Path | None = None,
    vram_sampler: Any = None,
    now: datetime | None = None,
) -> int:
    """Run the 10-prompt benchmark; return exit code (0 success, 1 on any FAIL).

    Prints one line on success::

        benchmark=PASS model=<id> output=<path>

    Prints one line on failure::

        benchmark=FAIL code=<code> detail=<detail>

    No output file is written on any failure path.

    ``vram_sampler`` is injectable so tests can replace ``nvidia-smi``
    with a stub returning a deterministic MiB count (or raising
    ``FileNotFoundError`` to exercise the unavailable path).
    """
    runtime_url = _resolve_runtime_url()
    marker_d = marker_dir if marker_dir is not None else PROBE_MARKER_DIR
    output_d = output_dir if output_dir is not None else BENCHMARK_OUTPUT_DIR
    fixture_p = fixture_path if fixture_path is not None else _BENCHMARK_FIXTURE_REL
    try:
        # 1) Probe-staleness gate.
        _assert_probe_fresh(marker_d, now=now)

        # 2) Read 10 fixtures.
        fixtures = _read_first_n_fixtures(fixture_p, _BENCHMARK_PROMPT_COUNT)
        fixture_ids = [str(fx.get("id") or f"unknown-{i}") for i, fx in enumerate(fixtures)]

        # 3) Start VRAM sampler BEFORE any HTTP work.
        sampler = _VRAMSampler(sampler=vram_sampler)
        sampler.start()
        try:
            # 4) Unload the model so prompt #1 measures cold start.
            _unload_model(runtime_url, model_id, timeout=_HTTP_CALL_TIMEOUT_SEC)

            # 5) Iterate the 10 prompts. The first is "cold"; 2-10 are warm.
            chat_url = f"{runtime_url}/v1/chat/completions"
            per_prompt: list[dict[str, Any]] = []
            warm_first_token_times: list[float] = []
            warm_tps_samples: list[float] = []
            schema_pass_count = 0
            tps_samples_dropped = 0

            cold_start_seconds: float | None = None

            for idx, fixture in enumerate(fixtures):
                user_msg = _format_user_prompt(fixture)
                if idx == 0:
                    # On the cold call, model-id mistypes surface as
                    # ``runtime_unreachable`` (HTTPError) or
                    # ``streaming_parse_error`` from the chat endpoint
                    # before we ever query ``/v1/models``. Catch those,
                    # check the models endpoint immediately, and if the
                    # id is missing, surface ``model_not_loaded`` (the
                    # actionable diagnostic). If the id IS present, the
                    # original chat error was the real failure — re-raise.
                    try:
                        content, first_tok, total, tokens = _stream_chat_completion(
                            chat_url,
                            model_id,
                            _PROBE_SYSTEM_PROMPT,
                            user_msg,
                            timeout=_HTTP_CALL_TIMEOUT_SEC,
                        )
                    except _BenchmarkFailure:
                        try:
                            _, models_body = _get_json(
                                f"{runtime_url}/v1/models",
                                timeout=_HTTP_CALL_TIMEOUT_SEC,
                            )
                        except _BenchmarkFailure:
                            # ``/v1/models`` itself unreachable — the
                            # original chat failure is the real story.
                            raise
                        # If the model id is absent, that's the better
                        # error code; ``_verify_...`` raises model_not_loaded.
                        _verify_model_in_models_response(models_body, model_id)
                        # Models endpoint reports the id is present, so
                        # the chat-call error was something else — let it
                        # propagate.
                        raise
                    # Cold call succeeded — now verify the model id is
                    # in /v1/models. Ollama re-loads on first generate,
                    # so checking BEFORE the first call would
                    # false-positive on an idle-but-installed model.
                    cold_start_seconds = first_tok
                    _, models_body = _get_json(
                        f"{runtime_url}/v1/models", timeout=_HTTP_CALL_TIMEOUT_SEC
                    )
                    _verify_model_in_models_response(models_body, model_id)
                else:
                    content, first_tok, total, tokens = _stream_chat_completion(
                        chat_url,
                        model_id,
                        _PROBE_SYSTEM_PROMPT,
                        user_msg,
                        timeout=_HTTP_CALL_TIMEOUT_SEC,
                    )
                    warm_first_token_times.append(first_tok)
                    # Token count: prefer usage.completion_tokens, else
                    # whitespace-split. (Document this choice in module
                    # docstring.)
                    if tokens is not None:
                        token_count = tokens
                    else:
                        token_count = len(content.split())
                    # Steady-state TPS for this prompt:
                    # (tokens - 1) / (total - first_token)
                    span = total - first_tok
                    if span > 0 and token_count > 1:
                        warm_tps_samples.append((token_count - 1) / span)
                    else:
                        # One-token responses or zero-span responses
                        # produce no meaningful TPS — count them so the
                        # final JSON surfaces how many warm samples we
                        # had to drop.
                        tps_samples_dropped += 1

                # Schema-validity check.
                schema_valid = False
                try:
                    parsed = json.loads(content)
                    Activity.model_validate(parsed)
                    schema_valid = True
                except (json.JSONDecodeError, ValidationError):
                    schema_valid = False
                if schema_valid:
                    schema_pass_count += 1

                per_prompt.append(
                    {
                        "fixture_id": fixture_ids[idx],
                        "first_token_seconds": round(first_tok, 3),
                        "total_seconds": round(total, 3),
                        "completion_tokens": tokens,
                        "schema_valid": schema_valid,
                    }
                )
        finally:
            sampler.stop()

        # Cold start must have been recorded (the loop ran at least
        # once because _BENCHMARK_PROMPT_COUNT > 0); assert for the
        # type-checker.
        assert cold_start_seconds is not None
        warm_first_token_median = (
            statistics.median(warm_first_token_times) if warm_first_token_times else 0.0
        )
        steady_state_tps = statistics.mean(warm_tps_samples) if warm_tps_samples else 0.0
        schema_validity_percent = 100.0 * schema_pass_count / _BENCHMARK_PROMPT_COUNT

        if tps_samples_dropped:
            _logger.warning(
                "%d/%d warm TPS samples dropped (single-token or zero-span responses); "
                "steady_state_tps reflects the remaining %d samples",
                tps_samples_dropped,
                _BENCHMARK_PROMPT_COUNT - 1,
                len(warm_tps_samples),
            )

        iso_ts = (now if now is not None else datetime.now(UTC)).strftime(_MARKER_TS_FORMAT)
        out_path = _write_benchmark_result(
            model_id=model_id,
            iso_ts=iso_ts,
            fixture_ids=fixture_ids,
            cold_start_seconds=cold_start_seconds,
            warm_first_token_median=warm_first_token_median,
            steady_state_tps=steady_state_tps,
            peak_vram_mib=sampler.peak_mib(),
            schema_validity_percent=schema_validity_percent,
            per_prompt=per_prompt,
            output_dir=output_d,
            tps_samples_dropped=tps_samples_dropped,
        )
        print(f"benchmark=PASS model={model_id} output={out_path}", flush=True)
        return 0
    except _BenchmarkFailure as fail:
        print(
            f"benchmark=FAIL code={fail.code} detail={fail.detail!r}",
            flush=True,
        )
        return 1
    except KeyboardInterrupt:
        print("benchmark=FAIL code=interrupt detail='KeyboardInterrupt'", flush=True)
        return 130


# ---------------------------------------------------------------------
# Decision doc
# ---------------------------------------------------------------------


def _load_result_file(path: Path) -> dict[str, Any]:
    """Read a benchmark result JSON file; raise ``results_unreadable`` on error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise _BenchmarkFailure("results_unreadable", f"{path} not readable: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _BenchmarkFailure("results_unreadable", f"{path} malformed JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _BenchmarkFailure("results_unreadable", f"{path} not a JSON object: {type(parsed)!r}")
    return cast(dict[str, Any], parsed)


def _meets_thresholds(result: dict[str, Any], thresholds: dict[str, float]) -> bool:
    """Apply the E1c threshold gate. ``null`` peak_vram_mib treated as failing.

    Returns ``True`` iff every threshold passes. Thresholds:

    * ``cold_start_seconds`` — must be **<** threshold (strict less-than)
    * ``warm_first_token_latency_seconds_median`` — must be **<** threshold
    * ``steady_state_tps`` — must be **>=** threshold
    * ``peak_vram_mib`` — must be **<** threshold; ``None`` is a fail
    * ``schema_validity_percent`` — must be **==** threshold (100.0)
    """
    cold = result.get("cold_start_seconds")
    if not isinstance(cold, (int, float)) or cold >= thresholds["cold_start_seconds"]:
        return False
    warm = result.get("warm_first_token_latency_seconds_median")
    if (
        not isinstance(warm, (int, float))
        or warm >= thresholds["warm_first_token_latency_seconds_median"]
    ):
        return False
    tps = result.get("steady_state_tps")
    if not isinstance(tps, (int, float)) or tps < thresholds["steady_state_tps"]:
        return False
    vram = result.get("peak_vram_mib")
    if vram is None or not isinstance(vram, (int, float)):
        return False
    if vram >= thresholds["peak_vram_mib"]:
        return False
    validity = result.get("schema_validity_percent")
    if not isinstance(validity, (int, float)):
        return False
    if validity != thresholds["schema_validity_percent"]:
        return False
    return True


def _decide_verdict(result_7b: dict[str, Any], result_3b: dict[str, Any]) -> str:
    """Return ``'7B'``, ``'3B'``, or ``'cloud-burst'`` per the E1c gate.

    7B is preferred (higher-quality model); 3B is the consolation prize;
    cloud-burst is the fallback when neither local model passes.
    """
    if _meets_thresholds(result_7b, _7B_THRESHOLDS):
        return "7B"
    if _meets_thresholds(result_3b, _3B_THRESHOLDS):
        return "3B"
    return "cloud-burst"


def _format_metric(value: Any, *, places: int = 2, none_repr: str = "null") -> str:
    """Format a numeric metric for the markdown table, handling ``None``."""
    if value is None:
        return none_repr
    if isinstance(value, (int, float)):
        return f"{value:.{places}f}"
    return str(value)


def _render_decision_doc(
    *,
    verdict: str,
    result_7b: dict[str, Any],
    result_3b: dict[str, Any],
    today: str,
) -> str:
    """Build the markdown body of ``documentation/local-model-decision.md``."""
    table_header = (
        "| model | cold (s) | warm 1st-token (s) | TPS | peak VRAM (MiB) "
        "| schema validity (%) |\n"
        "|-------|----------|--------------------|-----|----------------|"
        "---------------------|"
    )

    def _row(label: str, result: dict[str, Any]) -> str:
        return (
            f"| {label} "
            f"| {_format_metric(result.get('cold_start_seconds'))} "
            f"| {_format_metric(result.get('warm_first_token_latency_seconds_median'))} "
            f"| {_format_metric(result.get('steady_state_tps'))} "
            f"| {_format_metric(result.get('peak_vram_mib'), places=0)} "
            f"| {_format_metric(result.get('schema_validity_percent'))} |"
        )

    return (
        f"# Phase E E1c — Local-model benchmark + decision ({today})\n"
        f"\n"
        f"**Verdict:** {verdict}\n"
        f"\n"
        f"## Per-model metrics\n"
        f"\n"
        f"{table_header}\n"
        f"{_row('7B', result_7b)}\n"
        f"{_row('3B', result_3b)}\n"
        f"\n"
        f"## Threshold gate\n"
        f"\n"
        f"7B-pass requires: cold_start < {_7B_THRESHOLDS['cold_start_seconds']:.0f}s, "
        f"warm 1st-token median < "
        f"{_7B_THRESHOLDS['warm_first_token_latency_seconds_median']:.0f}s, "
        f"steady-state TPS >= {_7B_THRESHOLDS['steady_state_tps']:.0f}, "
        f"peak VRAM < {_7B_THRESHOLDS['peak_vram_mib']:.0f} MiB, "
        f"schema validity == {_7B_THRESHOLDS['schema_validity_percent']:.0f}%.\n"
        f"\n"
        f"3B-pass requires: cold_start < {_3B_THRESHOLDS['cold_start_seconds']:.0f}s, "
        f"warm 1st-token median < "
        f"{_3B_THRESHOLDS['warm_first_token_latency_seconds_median']:.0f}s, "
        f"steady-state TPS >= {_3B_THRESHOLDS['steady_state_tps']:.0f}, "
        f"peak VRAM < {_3B_THRESHOLDS['peak_vram_mib']:.0f} MiB, "
        f"schema validity == {_3B_THRESHOLDS['schema_validity_percent']:.0f}%.\n"
        f"\n"
        f"Falls through to **cloud-burst** when neither local model passes.\n"
        f"\n"
        f"## Contention answer for 8 GB hosts\n"
        f"\n"
        f"Operator: fill in (a) sequentialize / (b) 3B-only / (c) cloud-burst 7B. "
        f'See documentation/plan/phase-e.md § Step 25c "Note on 8 GB hosts".\n'
        f"\n"
        f"## Runtime choice rationale\n"
        f"\n"
        f"Operator: fill in.\n"
        f"\n"
        f"## Known gotchas\n"
        f"\n"
        f"Operator: fill in.\n"
    )


def write_decision_doc(
    *,
    path_7b: Path,
    path_3b: Path,
    output_path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Read both result files, apply E1c gate, write the decision doc.

    Returns 0 on success, 1 on any failure (missing/malformed inputs).
    Output written atomically via ``.tmp`` + ``os.replace`` so a crash
    mid-write can't leave a half-rendered markdown file. Overwrites if
    the target path already exists.
    """
    out_path = output_path if output_path is not None else DECISION_DOC_PATH
    try:
        result_7b = _load_result_file(path_7b)
        result_3b = _load_result_file(path_3b)
    except _BenchmarkFailure as fail:
        print(
            f"benchmark=FAIL code={fail.code} detail={fail.detail!r}",
            flush=True,
        )
        return 1

    verdict = _decide_verdict(result_7b, result_3b)
    today = (now if now is not None else datetime.now(UTC)).strftime("%Y-%m-%d")
    body = _render_decision_doc(
        verdict=verdict,
        result_7b=result_7b,
        result_3b=result_3b,
        today=today,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(body, encoding="utf-8")
    try:
        os.replace(tmp_path, out_path)
    except OSError:
        # Same `.tmp` cleanup as the result-file writer; see comment
        # in ``_write_benchmark_result``.
        Path(tmp_path).unlink(missing_ok=True)
        raise
    print(
        f"decision_doc=WRITTEN path={out_path} verdict={verdict}",
        flush=True,
    )
    return 0


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Argparse with two mutually-exclusive modes.

    ``--benchmark --model <tag>`` and ``--write-decision-doc
    --7b-results <path> --3b-results <path>`` are the two entry points.
    Flags that would otherwise yield invalid Python identifiers
    (``args.7b_results``) are mapped via ``dest=`` to
    ``args.results_7b`` / ``args.results_3b`` — see the parser body.
    """
    parser = argparse.ArgumentParser(
        prog="toybox.ai.local_benchmark",
        description=(
            "Phase E Step 25c — local-model benchmark + decision-doc CLI. "
            "Use --benchmark to run the 10-prompt benchmark, or "
            "--write-decision-doc to aggregate two result files into a "
            "verdict markdown."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--benchmark",
        action="store_true",
        help=(
            "Run the 10-prompt benchmark against the model named by --model. "
            "Requires a fresh probe-pass marker (Step 25b)."
        ),
    )
    mode.add_argument(
        "--write-decision-doc",
        action="store_true",
        help=(
            "Aggregate --7b-results and --3b-results into documentation/local-model-decision.md."
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Ollama model tag (required with --benchmark).",
    )
    parser.add_argument(
        "--7b-results",
        dest="results_7b",
        type=str,
        default=None,
        help="Path to a benchmark result JSON for the 7B model.",
    )
    parser.add_argument(
        "--3b-results",
        dest="results_3b",
        type=str,
        default=None,
        help="Path to a benchmark result JSON for the 3B model.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on failure, 130 on Ctrl-C."""
    args = _parse_args(argv)
    if args.benchmark:
        if not args.model:
            print(
                "benchmark=FAIL code=invalid_args detail='--benchmark requires --model'",
                flush=True,
            )
            return 1
        return run_benchmark(model_id=args.model)
    # args.write_decision_doc is True (mutually-exclusive group).
    if not args.results_7b or not args.results_3b:
        print(
            "benchmark=FAIL code=invalid_args "
            "detail='--write-decision-doc requires --7b-results and --3b-results'",
            flush=True,
        )
        return 1
    return write_decision_doc(
        path_7b=Path(args.results_7b),
        path_3b=Path(args.results_3b),
    )


__all__ = [
    "BENCHMARK_OUTPUT_DIR",
    "DECISION_DOC_PATH",
    "DEFAULT_LOCAL_RUNTIME_URL",
    "LOCAL_RUNTIME_URL_ENV",
    "PROBE_MARKER_DIR",
    "PROBE_MAX_AGE",
    "main",
    "run_benchmark",
    "write_decision_doc",
]


if __name__ == "__main__":
    sys.exit(main())
