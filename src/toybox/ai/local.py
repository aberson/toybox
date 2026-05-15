"""Local-runtime adapter for the Phase E loop-mode dispatch.

This module ships the code-only seams of the locally-hosted inference
path (Ollama / LM Studio / llama.cpp) without any production generation
logic -- the actual ``/v1/chat/completions`` plumbing for activity
generation lands in Step 26 (issue #38). For now the adapter conforms to
:class:`toybox.ai.adapters.ActivityGeneratorAdapter` and raises
:class:`NotImplementedError` from both entry points so an operator who
opts in via ``TOYBOX_GENERATOR_ADAPTER=local`` sees a clear pointer to
the next-step issue.

The constructor stores config (runtime URL, model id) but does NOT
instantiate an HTTP client -- keeping the import cheap means the v1
``claude+single`` path's import surface is unchanged for operators
who never flip the env var.

Phase E Step 25b (E1b) — ``--probe`` smoke entry point
------------------------------------------------------

``uv run python -m toybox.ai.local --probe`` smoke-tests a running
OpenAI-compatible local runtime end-to-end against ONE fixture prompt
(the first line of ``tests/fixtures/eval/prompts.jsonl``, id ``f001``).
On success it writes a marker file at
``data/models/.probe-pass-<iso>.json`` that the Step 25c benchmark CLI
reads as its "passed probe within the last hour" prerequisite.

Done-when contract (from ``documentation/plan/phase-e.md`` §"Step 25b"):

* CLI parses one fixture activity end-to-end with no exceptions.
* Marker file written to ``data/models/`` on success.
* Wall-clock budget defaults to 60 seconds (overrideable via
  ``TOYBOX_PROBE_BUDGET_SEC`` env or ``--budget-sec`` flag).

Marker filename uses a colon-free ISO format
(``YYYY-MM-DDTHH-MM-SSZ``) because Windows filesystems reject ``:`` in
filenames. We strip colons by formatting with
``strftime("%Y-%m-%dT%H-%M-%SZ")`` rather than ``isoformat()``.

The marker file is written atomically (``write to <name>.tmp`` then
``os.replace``) so a kill mid-write can't leave a partial JSON file for
the Step 25c benchmark CLI to choke on.

Ollama JSON-mode strategy
-------------------------

Ollama 0.5+ supports passing a JSON schema as the ``format`` parameter
for structured-output generation (the model is constrained to emit JSON
conforming to that schema). Older Ollama versions only support
``format: "json"`` (basic JSON-mode — model emits *some* JSON, schema
not enforced server-side). The probe tries the schema-passing form
first; if Ollama returns HTTP 4xx (a client-side rejection of the
schema-passing shape) the probe falls back to basic ``format: "json"``
and records which path was used in the marker file's
``ollama_format_path`` field. 5xx and other non-2xx non-4xx responses
are NOT a schema rejection — they bubble up as ``runtime_unreachable``
without a redundant fallback attempt that would mask the real server
error.

HTTP convention
---------------

Per ``.claude/rules/claude-auth.md``, all internal HTTP uses stdlib
``urllib.request`` only -- no ``requests``, no ``httpx``, no
``aiohttp``. The probe is synchronous; one-shot CLIs don't benefit from
async here.

Wall-clock budget enforcement scope
-----------------------------------

:func:`_check_wall_clock` is called between HTTP calls. The per-call
socket timeout is capped at ``min(_HTTP_CALL_TIMEOUT_SEC, budget)`` so a
single hung socket can't burn the full budget, but a hang on a later
call can still overshoot the wall-clock budget by up to
``_HTTP_CALL_TIMEOUT_SEC`` seconds before the next inter-call check
runs. Accepted as documented slack — the budget is a soft ceiling on
the inter-call axis, not a hard real-time deadline.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from pydantic import ValidationError

from ..activities.models import Activity
from .tools import ToolDispatcher

_logger = logging.getLogger(__name__)

#: Env var the operator sets to point at a running local OpenAI-compatible
#: runtime. Default matches Ollama's stock listener per
#: ``documentation/plan/phase-e.md``.
LOCAL_RUNTIME_URL_ENV: Final[str] = "TOYBOX_LOCAL_RUNTIME_URL"
DEFAULT_LOCAL_RUNTIME_URL: Final[str] = "http://localhost:11434"

#: Env var the operator sets to pin a specific model id (e.g.
#: ``qwen2.5:7b``). When set, :func:`toybox.ai.capability.is_local_capable`
#: asserts that id is present in ``/v1/models``; when unset, the probe
#: only confirms the response is well-formed.
LOCAL_MODEL_ID_ENV: Final[str] = "TOYBOX_LOCAL_MODEL_ID"

#: Env var the operator sets to override the probe's wall-clock budget
#: (in seconds). Default is :data:`DEFAULT_PROBE_BUDGET_SEC`.
PROBE_BUDGET_ENV: Final[str] = "TOYBOX_PROBE_BUDGET_SEC"
DEFAULT_PROBE_BUDGET_SEC: Final[float] = 60.0

#: Where the success marker is written. Path is relative to the
#: project root; the probe creates the directory if missing.
PROBE_MARKER_DIR: Final[Path] = Path("data/models")

#: Step-26 / issue-#38 pointer baked into the NotImplementedError so a
#: future operator (or LLM reading the traceback) sees the right next
#: step. Centralised so tests can pin the substring without
#: copy-pasting prose.
STEP_26_HINT: Final[str] = (
    "local adapter generation logic ships in Step 26 (issue #38); "
    "the current build only wires the capability probe + breaker seams"
)


class LocalActivityGenerator:
    """Local-runtime implementation of :class:`ActivityGeneratorAdapter`.

    Both ``generate_activity`` and ``generate_activity_loop`` raise
    :class:`NotImplementedError` with a message that cites Step 26 /
    issue #38 explicitly. The constructor accepts the runtime URL and
    model id strings so a Step-26 follow-up can drop the real HTTP
    client in without changing the call-site contract -- the
    capability probe and the breaker are already wired against this
    shape.

    No HTTP client is instantiated at construction time: this keeps the
    module cheap to import even when the local path isn't active.
    """

    def __init__(
        self,
        *,
        runtime_url: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self._runtime_url = (
            runtime_url
            if runtime_url is not None
            else os.environ.get(LOCAL_RUNTIME_URL_ENV, DEFAULT_LOCAL_RUNTIME_URL)
        )
        self._model_id = model_id if model_id is not None else os.environ.get(LOCAL_MODEL_ID_ENV)

    @property
    def runtime_url(self) -> str:
        """Configured runtime URL (env or constructor override)."""
        return self._runtime_url

    @property
    def model_id(self) -> str | None:
        """Configured model id, or ``None`` if the operator left it unset."""
        return self._model_id

    async def generate_activity(self, ctx: object) -> Activity:
        """Single-shot generation -- not yet implemented."""
        raise NotImplementedError(STEP_26_HINT)

    async def generate_activity_loop(self, ctx: object, tools: ToolDispatcher) -> Activity:
        """Tool-loop generation -- not yet implemented."""
        raise NotImplementedError(STEP_26_HINT)


# ---------------------------------------------------------------------
# Step 25b (E1b) — --probe CLI
# ---------------------------------------------------------------------


def _resolve_repo_root() -> Path:
    """Walk up from this file to find the repo root (sentinel ``pyproject.toml``).

    Used so the probe CLI can resolve the bundled fixture path regardless
    of the operator's current working directory. Falls back to the parent
    of the package dir if no sentinel is found (best-effort — the explicit
    ``FileNotFoundError`` wrapping in :func:`_read_first_fixture` catches
    the bad-path case with a clean ``invalid_json`` exit code).
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    # Fallback: src/toybox/ai/local.py -> src/toybox/ai -> src/toybox -> src
    # The caller's FileNotFoundError handling covers the missing-fixture
    # case if this fallback is wrong.
    return here.parents[3] if len(here.parents) > 3 else here.parent


_REPO_ROOT: Final[Path] = _resolve_repo_root()

#: Fixture path the probe reads from. Anchored to the repo root so the
#: CLI works from any cwd. Tests monkeypatch this attribute to inject a
#: temporary fixture file.
_PROBE_FIXTURE_REL: Final[Path] = _REPO_ROOT / "tests" / "fixtures" / "eval" / "prompts.jsonl"

#: Per-HTTP-call timeout cap. The wall-clock budget enforces the larger
#: budget around the whole probe; this caps any single call so a hung
#: socket can't burn the whole budget on one call.
_HTTP_CALL_TIMEOUT_SEC: Final[float] = 30.0

#: Chat-request token / sampling defaults. Kept as module-level
#: ``Final`` constants so the probe payload shape is grep-discoverable
#: and consistent with the env-driven constants above.
_CHAT_MAX_TOKENS: Final[int] = 1024
_CHAT_TEMPERATURE: Final[float] = 0.7

#: How many characters of a pydantic validation error / model content
#: dump to surface in error envelopes and stricter-retry prompts.
#: Keeps log lines and the retry prompt bounded.
_ERROR_EXCERPT_CAP: Final[int] = 400

#: ``strftime`` pattern for the marker filename's ISO timestamp.
#: Colon-free (``YYYY-MM-DDTHH-MM-SSZ``) so the filename is valid on
#: Windows. Step 25c's "passed probe within the last hour" check parses
#: this format from the filename.
_MARKER_TS_FORMAT: Final[str] = "%Y-%m-%dT%H-%M-%SZ"

_PROBE_SYSTEM_PROMPT: Final[str] = (
    "You emit ONE JSON object matching the toybox Activity schema. "
    "Required fields: id (non-empty string), template_id (non-empty), "
    "title (non-empty), steps (array of 3-20 objects each with "
    "step_index (>=0) and text (1-600 chars)), version (>=1), "
    "metadata (object), toy_ids (array of strings). "
    "Emit ONLY the JSON object, no prose."
)

#: Substring injected into the stricter retry prompt that proves the
#: validation-error excerpt was forwarded back to the model. Tests assert
#: on this token to guard the retry-prompt shape against silent regression.
_RETRY_PROMPT_MARKER: Final[str] = "STRICT: prior attempt failed schema validation with:"


class _ProbeFailure(Exception):
    """Internal control-flow exception with a stable error code.

    ``code`` matches one of the documented exit-1 codes
    (``runtime_unreachable``, ``model_not_loaded``, ``invalid_json``,
    ``schema_mismatch``, ``timeout``). ``detail`` is rendered verbatim
    on the failure line.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _resolve_runtime_url() -> str:
    """Return the configured runtime base URL with any trailing slash stripped."""
    return os.environ.get(LOCAL_RUNTIME_URL_ENV, DEFAULT_LOCAL_RUNTIME_URL).rstrip("/")


def _resolve_budget_sec(cli_override: float | None) -> float:
    """Resolve the wall-clock budget, preferring CLI flag over env var.

    Raises :class:`_ProbeFailure` with ``code='invalid_budget'`` on a
    malformed ``TOYBOX_PROBE_BUDGET_SEC`` value (non-numeric, zero, or
    negative), and on a non-positive ``--budget-sec`` CLI override.
    Surfacing this as a probe-failure envelope keeps a typo'd env var
    from crashing the CLI with an uncaught ``ValueError`` traceback.
    """
    if cli_override is not None:
        if cli_override <= 0:
            raise _ProbeFailure(
                "invalid_budget", f"--budget-sec must be positive, got {cli_override!r}"
            )
        return cli_override
    env_val = os.environ.get(PROBE_BUDGET_ENV)
    if env_val is not None:
        try:
            parsed = float(env_val)
        except ValueError as exc:
            raise _ProbeFailure(
                "invalid_budget", f"{PROBE_BUDGET_ENV}={env_val!r} not a float: {exc}"
            ) from exc
        if parsed <= 0:
            raise _ProbeFailure(
                "invalid_budget", f"{PROBE_BUDGET_ENV}={env_val!r} must be positive"
            )
        return parsed
    return DEFAULT_PROBE_BUDGET_SEC


def _get_json(url: str, timeout: float) -> tuple[int, Any]:
    """Synchronous GET returning ``(status, parsed_json_body)``.

    Returns ``(status, None)`` for non-2xx responses without raising
    so callers can decide whether to treat each failure shape as
    ``runtime_unreachable`` or as a more specific error.
    """
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = int(resp.status)
    except urllib.error.URLError as exc:
        # ``HTTPError`` subclasses ``URLError``; both shapes lump
        # together as "the runtime is unreachable or angry" at this
        # layer. The chat-completions POST has its own HTTP-status
        # awareness so it can branch on 400 for the format-fallback.
        raise _ProbeFailure("runtime_unreachable", str(exc)) from exc
    except TimeoutError as exc:
        raise _ProbeFailure("runtime_unreachable", "timeout") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise _ProbeFailure("runtime_unreachable", f"malformed JSON: {exc}") from exc
    return status, parsed


def _post_json(
    url: str, payload: dict[str, Any], timeout: float
) -> tuple[int, dict[str, Any] | None]:
    """Synchronous POST returning ``(status, parsed_json_body_or_None)``.

    Returns ``(status, None)`` for any non-2xx response (caller decides
    whether to retry / fall back / fail). URLError / TimeoutError bubble
    up as :class:`_ProbeFailure` with ``runtime_unreachable``.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        # HTTP-status errors (4xx/5xx) — caller can branch on status
        # (the format-fallback path needs to see 400 explicitly so we
        # don't lump it into runtime_unreachable here).
        return int(exc.code), None
    except urllib.error.URLError as exc:
        raise _ProbeFailure("runtime_unreachable", str(exc)) from exc
    except TimeoutError as exc:
        raise _ProbeFailure("runtime_unreachable", "timeout") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _ProbeFailure("invalid_json", f"response body not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _ProbeFailure("invalid_json", f"response body not an object: {type(parsed)!r}")
    return status, cast(dict[str, Any], parsed)


def _read_first_fixture(fixture_path: Path) -> dict[str, Any]:
    """Read and parse the first line of ``prompts.jsonl``.

    Wraps both the open() and the json.loads() in the ``_ProbeFailure``
    envelope so an operator running the CLI from a directory where the
    fixture isn't reachable, or a fixture with a malformed first line,
    sees a ``probe=FAIL code=invalid_json`` exit instead of an uncaught
    ``FileNotFoundError`` / ``JSONDecodeError`` traceback.
    """
    try:
        with fixture_path.open("r", encoding="utf-8") as fh:
            first = fh.readline()
    except (FileNotFoundError, OSError) as exc:
        raise _ProbeFailure("invalid_json", f"fixture {fixture_path} not readable: {exc}") from exc
    if not first.strip():
        raise _ProbeFailure("invalid_json", f"fixture {fixture_path} empty")
    try:
        parsed = json.loads(first)
    except json.JSONDecodeError as exc:
        raise _ProbeFailure(
            "invalid_json", f"fixture {fixture_path} first line malformed: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise _ProbeFailure("invalid_json", f"fixture line not an object: {type(parsed)!r}")
    return cast(dict[str, Any], parsed)


def _format_user_prompt(fixture: dict[str, Any]) -> str:
    """Build a compact human-readable summary of the fixture for the model."""
    child = fixture.get("child_profile", {}) or {}
    return (
        f"persona={fixture.get('persona', '?')} "
        f"child_age={child.get('age', '?')} "
        f"child_name={child.get('name', '?')} "
        f"available_toys={fixture.get('available_toys', [])} "
        f"transcript_window={fixture.get('transcript_window', '')!r} "
        f"trigger={fixture.get('trigger', '?')}"
    )


def _choose_model_id(models_response: Any) -> str | None:
    """Return the operator-pinned model id, or first available, or None."""
    pinned = os.environ.get(LOCAL_MODEL_ID_ENV)
    if pinned:
        return pinned
    if not isinstance(models_response, dict):
        return None
    data = models_response.get("data")
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if isinstance(first, dict) and isinstance(first.get("id"), str):
        return cast(str, first["id"])
    return None


def _verify_model_loaded(models_response: Any, model_id: str) -> None:
    """Raise :class:`_ProbeFailure` if ``model_id`` isn't in the response."""
    if not isinstance(models_response, dict):
        raise _ProbeFailure("model_not_loaded", f"models response not an object: {model_id}")
    data = models_response.get("data")
    if not isinstance(data, list):
        raise _ProbeFailure("model_not_loaded", f"models response missing 'data' array: {model_id}")
    ids = [d["id"] for d in data if isinstance(d, dict) and isinstance(d.get("id"), str)]
    if model_id not in ids:
        raise _ProbeFailure(
            "model_not_loaded",
            f"model {model_id!r} not in /v1/models (have: {ids})",
        )


def _chat_request_payload(
    model_id: str,
    system_msg: str,
    user_msg: str,
    *,
    format_value: Any,
) -> dict[str, Any]:
    """Build the JSON body for a ``/v1/chat/completions`` POST."""
    return {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": _CHAT_MAX_TOKENS,
        "temperature": _CHAT_TEMPERATURE,
        "format": format_value,
    }


def _extract_content(chat_response: dict[str, Any]) -> str:
    """Extract ``choices[0].message.content`` or raise ``invalid_json``."""
    choices = chat_response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _ProbeFailure(
            "invalid_json",
            f"chat response missing choices: {chat_response!r}"[:_ERROR_EXCERPT_CAP],
        )
    first = choices[0]
    if not isinstance(first, dict):
        raise _ProbeFailure("invalid_json", f"choices[0] not an object: {type(first)!r}")
    message = first.get("message")
    if not isinstance(message, dict):
        raise _ProbeFailure(
            "invalid_json", f"choices[0].message missing: {first!r}"[:_ERROR_EXCERPT_CAP]
        )
    content = message.get("content")
    if not isinstance(content, str):
        raise _ProbeFailure(
            "invalid_json", f"choices[0].message.content not a string: {type(content)!r}"
        )
    return content


def _check_wall_clock(start: float, budget_sec: float) -> None:
    """Raise ``_ProbeFailure(code='timeout', ...)`` if budget has elapsed."""
    elapsed = time.monotonic() - start
    if elapsed > budget_sec:
        raise _ProbeFailure("timeout", f"elapsed={elapsed:.2f}s budget={budget_sec:.2f}s")


def _write_marker(
    *,
    runtime_url: str,
    model_id: str,
    fixture_id: str,
    elapsed: float,
    format_path: str,
    retries: int,
    marker_dir: Path,
) -> Path:
    """Write the probe-pass marker JSON atomically; return its path.

    Filename uses ``YYYY-MM-DDTHH-MM-SSZ`` (no colons) to be safe on
    Windows filesystems. Writes to a ``<name>.tmp`` sibling first then
    ``os.replace``s into the final path so a kill mid-write can't leave a
    partial JSON file for Step 25c's reader to choke on (``os.replace`` is
    atomic on both POSIX and Windows).
    """
    marker_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    safe_iso = now.strftime(_MARKER_TS_FORMAT)
    marker_path = marker_dir / f".probe-pass-{safe_iso}.json"
    tmp_path = marker_path.with_suffix(marker_path.suffix + ".tmp")
    payload = {
        "iso_ts": safe_iso,
        "runtime_url": runtime_url,
        "model_id": model_id,
        "fixture_id": fixture_id,
        "wall_clock_seconds": round(elapsed, 3),
        "validation_mode": "strict",
        "ollama_format_path": format_path,
        "retries": retries,
    }
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, marker_path)
    return marker_path


def _post_chat_with_format_fallback(
    chat_url: str,
    model_id: str,
    system_msg: str,
    user_msg: str,
    *,
    per_call_timeout: float,
) -> tuple[dict[str, Any], str]:
    """POST to /v1/chat/completions, trying schema-format first.

    Returns ``(parsed_response_body, format_path)`` where
    ``format_path`` is ``"schema"`` if the schema-passing form
    succeeded, ``"json"`` if we fell back to basic JSON-mode.

    Fallback policy: only fall back from ``format=<schema>`` to
    ``format='json'`` on HTTP ``4xx`` responses (a client-side rejection
    of the schema shape, as older Ollama versions do). ``5xx`` and other
    non-2xx non-4xx responses are NOT a schema-shape rejection — they
    surface immediately as ``runtime_unreachable`` so a real server bug
    isn't masked by a second futile attempt.

    Raises :class:`_ProbeFailure` on URLError, TimeoutError, non-4xx
    non-2xx response, malformed JSON response body, or non-dict body.
    """
    schema = Activity.model_json_schema()
    payload = _chat_request_payload(model_id, system_msg, user_msg, format_value=schema)
    status, body = _post_json(chat_url, payload, timeout=per_call_timeout)
    if 200 <= status < 300 and body is not None:
        return body, "schema"
    if not (400 <= status < 500):
        # 5xx or other unexpected status — don't mask the real error
        # with a second attempt against the same broken server.
        raise _ProbeFailure(
            "runtime_unreachable",
            f"chat completions returned HTTP {status} on schema-format attempt",
        )
    # 4xx: schema-passing rejected by the runtime (older Ollama, etc.).
    # Retry with the broader format='json' shape.
    payload = _chat_request_payload(model_id, system_msg, user_msg, format_value="json")
    status, body = _post_json(chat_url, payload, timeout=per_call_timeout)
    if not (200 <= status < 300) or body is None:
        raise _ProbeFailure(
            "runtime_unreachable",
            f"chat completions returned HTTP {status} for both schema and json format",
        )
    return body, "json"


def run_probe(
    *,
    budget_sec: float | None = None,
    marker_dir: Path | None = None,
    fixture_path: Path | None = None,
) -> int:
    """Run the probe; return exit code (0 on success, 1 on any failure).

    Prints a single line on success::

        probe=PASS model=<id> elapsed=<float>s marker=<path>

    Prints a single line on failure::

        probe=FAIL code=<code> ...

    and returns 1. No marker is written on failure.
    """
    runtime_url = _resolve_runtime_url()
    start = time.monotonic()
    try:
        # _resolve_budget_sec can itself raise _ProbeFailure on bad env
        # input — keep it INSIDE the try so the failure surfaces via the
        # normal envelope, not as an uncaught ValueError.
        budget = _resolve_budget_sec(budget_sec)
        fixture = fixture_path if fixture_path is not None else _PROBE_FIXTURE_REL
        out_dir = marker_dir if marker_dir is not None else PROBE_MARKER_DIR
        per_call_timeout = min(_HTTP_CALL_TIMEOUT_SEC, budget)
        # 1) GET /v1/models — verify the chosen model id is loaded.
        _, models_body = _get_json(f"{runtime_url}/v1/models", timeout=per_call_timeout)
        _check_wall_clock(start, budget)
        model_id = _choose_model_id(models_body)
        if not model_id:
            raise _ProbeFailure(
                "model_not_loaded",
                "no model id pinned and /v1/models data[].id list is empty",
            )
        _verify_model_loaded(models_body, model_id)
        _check_wall_clock(start, budget)

        # 2) Read fixture line one (f001).
        fixture_data = _read_first_fixture(fixture)
        fixture_id = str(fixture_data.get("id") or "unknown")
        user_msg = _format_user_prompt(fixture_data)
        chat_url = f"{runtime_url}/v1/chat/completions"

        # 3) First chat attempt — strict system prompt, try schema-format.
        body, format_path = _post_chat_with_format_fallback(
            chat_url,
            model_id,
            _PROBE_SYSTEM_PROMPT,
            user_msg,
            per_call_timeout=per_call_timeout,
        )
        _check_wall_clock(start, budget)
        content = _extract_content(body)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise _ProbeFailure(
                "invalid_json", f"model content not JSON: {str(exc)[:_ERROR_EXCERPT_CAP]}"
            ) from exc

        retries = 0
        validation_error: str | None = None
        try:
            Activity.model_validate(parsed)
        except ValidationError as exc:
            validation_error = str(exc)[:_ERROR_EXCERPT_CAP]

        if validation_error is not None:
            # Retry once with a stricter system prompt quoting the error.
            # The injected ``_RETRY_PROMPT_MARKER`` substring is asserted
            # in unit tests as proof that the validation excerpt was
            # actually forwarded to the model on the retry.
            retries = 1
            _check_wall_clock(start, budget)
            stricter_system = (
                _PROBE_SYSTEM_PROMPT
                + " "
                + _RETRY_PROMPT_MARKER
                + " "
                + validation_error
                + " Emit a corrected Activity JSON now with ALL required fields populated."
            )
            body2, format_path2 = _post_chat_with_format_fallback(
                chat_url,
                model_id,
                stricter_system,
                user_msg,
                per_call_timeout=per_call_timeout,
            )
            _check_wall_clock(start, budget)
            content2 = _extract_content(body2)
            try:
                parsed = json.loads(content2)
            except json.JSONDecodeError as exc:
                raise _ProbeFailure(
                    "invalid_json", f"retry content not JSON: {str(exc)[:_ERROR_EXCERPT_CAP]}"
                ) from exc
            try:
                Activity.model_validate(parsed)
            except ValidationError as exc:
                excerpt = str(exc)[:_ERROR_EXCERPT_CAP]
                raise _ProbeFailure("schema_mismatch", excerpt) from exc
            # Use the retry's format path -- it's the one that actually
            # produced the validated activity.
            format_path = format_path2

        elapsed = time.monotonic() - start
        marker_path = _write_marker(
            runtime_url=runtime_url,
            model_id=model_id,
            fixture_id=fixture_id,
            elapsed=elapsed,
            format_path=format_path,
            retries=retries,
            marker_dir=out_dir,
        )
        print(
            f"probe=PASS model={model_id} elapsed={elapsed:.2f}s marker={marker_path}",
            flush=True,
        )
        return 0
    except _ProbeFailure as fail:
        elapsed = time.monotonic() - start
        if fail.code == "timeout":
            print(f"probe=FAIL code=timeout elapsed={elapsed:.2f}s", flush=True)
        elif fail.code == "runtime_unreachable":
            print(
                f"probe=FAIL code=runtime_unreachable runtime_url={runtime_url} "
                f"detail={fail.detail!r}",
                flush=True,
            )
        elif fail.code == "schema_mismatch":
            print(f"probe=FAIL code=schema_mismatch error={fail.detail!r}", flush=True)
        elif fail.code == "model_not_loaded":
            print(f"probe=FAIL code=model_not_loaded detail={fail.detail!r}", flush=True)
        elif fail.code == "invalid_json":
            print(f"probe=FAIL code=invalid_json detail={fail.detail!r}", flush=True)
        elif fail.code == "invalid_budget":
            print(f"probe=FAIL code=invalid_budget detail={fail.detail!r}", flush=True)
        else:
            print(f"probe=FAIL code={fail.code} detail={fail.detail!r}", flush=True)
        return 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="toybox.ai.local",
        description=(
            "Phase E Step 25b — smoke-probe a running local OpenAI-compatible "
            "runtime end-to-end against ONE fixture prompt."
        ),
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help=(
            "Run the end-to-end smoke probe; write a "
            "data/models/.probe-pass-<iso>.json marker on success."
        ),
    )
    parser.add_argument(
        "--budget-sec",
        type=float,
        default=None,
        help=(f"Wall-clock budget in seconds (default 60; env override: {PROBE_BUDGET_ENV})."),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns 0 on probe success, 1 on failure."""
    args = _parse_args(argv)
    if not args.probe:
        print("toybox.ai.local: nothing to do; pass --probe to run the smoke probe.")
        return 0
    return run_probe(budget_sec=args.budget_sec)


__all__ = [
    "DEFAULT_LOCAL_RUNTIME_URL",
    "DEFAULT_PROBE_BUDGET_SEC",
    "LOCAL_MODEL_ID_ENV",
    "LOCAL_RUNTIME_URL_ENV",
    "LocalActivityGenerator",
    "PROBE_BUDGET_ENV",
    "PROBE_MARKER_DIR",
    "STEP_26_HINT",
    "main",
    "run_probe",
]


if __name__ == "__main__":
    sys.exit(main())
