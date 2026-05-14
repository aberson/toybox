"""Unit tests for the Phase E Step 28 partial local-adapter seams.

Covers three orthogonal surfaces:

1. :class:`toybox.ai.local.LocalActivityGenerator` -- both Protocol
   methods raise :class:`NotImplementedError` with a Step 26 / issue
   #38 pointer baked into the message. The constructor stores config
   without instantiating any HTTP client.
2. :func:`toybox.ai.capability.is_local_capable` -- across the three
   failure modes (cannot-connect, model-not-loaded, breaker-open)
   the returned reason strings are pinned to the module-level
   constants (so integration tests can match by symbol, not prose).
3. Per-adapter breaker independence -- tripping Claude does NOT
   affect ``is_local_capable``, and tripping the local breaker does
   NOT affect Claude's :func:`is_capable`. The two breakers are
   wired separately via :func:`toybox.ai.breaker.get_local_breaker`.
"""

from __future__ import annotations

import json
import time
import urllib.error
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from toybox.ai.breaker import (
    CircuitBreaker,
    get_local_breaker,
    reset_local_breaker_for_tests,
)
from toybox.ai.capability import (
    LOCAL_BREAKER_OPEN_REASON,
    LOCAL_MODEL_NOT_LOADED_REASON,
    LOCAL_NOT_INSTALLED_REASON,
    is_capable,
    is_local_capable,
)
from toybox.ai.local import (
    DEFAULT_LOCAL_RUNTIME_URL,
    LOCAL_MODEL_ID_ENV,
    LOCAL_RUNTIME_URL_ENV,
    STEP_26_HINT,
    LocalActivityGenerator,
)
from toybox.ai.oauth import OAuthToken, save_token
from toybox.core.capability import CapabilityReason
from toybox.core.listening import ListeningMode

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_local_breaker() -> Iterator[None]:
    """Each test gets a fresh local breaker singleton.

    The module-level cache in :mod:`toybox.ai.breaker` would otherwise
    leak failure state across tests (a previous test's tripped
    breaker would make the next test's capability check return
    ``breaker-open`` no matter what the HTTP mock said).
    """
    reset_local_breaker_for_tests()
    yield
    reset_local_breaker_for_tests()


@pytest.fixture
def clear_local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop both local env vars so the test starts from a clean slate."""
    monkeypatch.delenv(LOCAL_RUNTIME_URL_ENV, raising=False)
    monkeypatch.delenv(LOCAL_MODEL_ID_ENV, raising=False)


# ----------------------------------------------------------------------
# (1) LocalActivityGenerator -- both entry points raise with the hint
# ----------------------------------------------------------------------


def test_local_adapter_constructor_args_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructor args override both env vars.

    Subsumes the env-only-read tests: passing constructor args while
    env vars are ALSO set proves both that the env vars would be read
    (otherwise overriding them would be vacuous) and that explicit
    args win. Also exercises the default branch via :attr:`model_id is
    None` when no args/env are present (the
    ``test_is_local_capable_probes_default_url`` test covers the URL
    default).
    """
    monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, "http://10.0.0.5:9000")
    monkeypatch.setenv(LOCAL_MODEL_ID_ENV, "qwen2.5:7b")
    gen = LocalActivityGenerator(runtime_url="http://localhost:11434", model_id="phi3")
    assert gen.runtime_url == "http://localhost:11434"
    assert gen.model_id == "phi3"


async def test_generate_activity_raises_with_step_26_hint() -> None:
    gen = LocalActivityGenerator()
    with pytest.raises(NotImplementedError) as exc_info:
        await gen.generate_activity(object())
    msg = str(exc_info.value)
    assert "Step 26" in msg
    assert "#38" in msg
    assert msg == STEP_26_HINT


async def test_generate_activity_loop_raises_with_step_26_hint() -> None:
    gen = LocalActivityGenerator()
    # ``tools`` arg is not touched by the implementation pre-Step-26;
    # passing None here proves the raise happens before any tools
    # plumbing is consulted.
    with pytest.raises(NotImplementedError) as exc_info:
        await gen.generate_activity_loop(object(), None)  # type: ignore[arg-type]
    msg = str(exc_info.value)
    assert "Step 26" in msg
    assert "#38" in msg
    assert msg == STEP_26_HINT


# ----------------------------------------------------------------------
# (2) is_local_capable() across the three failure modes
# ----------------------------------------------------------------------


class _FakeResponse:
    """Minimal urllib-response-like object for tests.

    Provides ``read()`` + the context-manager protocol. Mirrors the
    relevant surface :func:`toybox.ai.capability._probe_local_models_sync`
    consumes so we don't have to monkeypatch the response shape too.
    """

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _ok_models_payload(ids: list[str]) -> bytes:
    return json.dumps({"object": "list", "data": [{"id": i} for i in ids]}).encode("utf-8")


def _patch_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    side_effect: Callable[[Any, Any], Any] | None = None,
    response_body: bytes | None = None,
) -> list[str]:
    """Stub :func:`urllib.request.urlopen` for the capability probe.

    Returns a list that records every URL passed to the stub so tests
    can assert the configured runtime URL was actually used.
    """
    calls: list[str] = []

    def _stub(req: Any, **kwargs: Any) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        if side_effect is not None:
            return side_effect(req, kwargs)
        assert response_body is not None
        return _FakeResponse(response_body)

    monkeypatch.setattr("toybox.ai.capability.urllib.request.urlopen", _stub)
    return calls


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_true_when_runtime_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_urlopen(monkeypatch, response_body=_ok_models_payload(["qwen2.5:7b"]))
    capable, reason = await is_local_capable()
    assert capable is True
    assert reason is None


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_probes_default_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env var set → probes :data:`DEFAULT_LOCAL_RUNTIME_URL` per the plan."""
    calls = _patch_urlopen(monkeypatch, response_body=_ok_models_payload(["any"]))
    await is_local_capable()
    assert len(calls) == 1
    assert calls[0] == DEFAULT_LOCAL_RUNTIME_URL + "/v1/models"


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_probes_configured_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOCAL_RUNTIME_URL_ENV, "http://10.0.0.5:9000/")
    calls = _patch_urlopen(monkeypatch, response_body=_ok_models_payload(["any"]))
    await is_local_capable()
    # The probe appends /v1/models and strips a trailing slash.
    assert calls[0] == "http://10.0.0.5:9000/v1/models"


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_cannot_connect_returns_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(req: Any, kwargs: Any) -> Any:
        raise urllib.error.URLError("connection refused")

    _patch_urlopen(monkeypatch, side_effect=_raise)
    capable, reason = await is_local_capable()
    assert capable is False
    assert reason == LOCAL_NOT_INSTALLED_REASON


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_timeout_returns_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout (TimeoutError) is the same failure shape as no connection."""

    def _raise(req: Any, kwargs: Any) -> Any:
        raise TimeoutError("probe exceeded 2s")

    _patch_urlopen(monkeypatch, side_effect=_raise)
    capable, reason = await is_local_capable()
    assert capable is False
    assert reason == LOCAL_NOT_INSTALLED_REASON


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_http_error_returns_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 4xx/5xx response (urllib.error.HTTPError) is treated as not-installed.

    ``HTTPError`` is a subclass of ``URLError``; the probe's exception
    handler relies on that inheritance to lump HTTP-level failures in
    with connect-level ones. Pin the inheritance so a regression that
    re-orders or narrows the catch clause fails fast. The breaker MUST
    accumulate a failure for this shape (same as URLError) so a stuck
    503 eventually short-circuits to breaker-open.
    """

    def _raise(req: Any, kwargs: Any) -> Any:
        raise urllib.error.HTTPError(
            url="http://localhost:11434/v1/models",
            code=503,
            msg="Service Unavailable",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    _patch_urlopen(monkeypatch, side_effect=_raise)
    breaker = get_local_breaker()
    assert breaker._consecutive_failures == 0

    capable, reason = await is_local_capable()
    assert capable is False
    assert reason == LOCAL_NOT_INSTALLED_REASON
    # The HTTPError path must record a breaker failure -- otherwise a
    # stuck 503 would never trip the breaker and probes would keep
    # hitting a dead runtime forever.
    assert breaker._consecutive_failures == 1


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_success_resets_failure_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy probe clears prior consecutive failures.

    Sequence: (connect-fail × 2) → (healthy) MUST leave the breaker's
    failure counter at zero so the next connect-fail starts fresh
    rather than tripping the breaker on a single transient blip.
    Asserted via observable behavior: after the reset, a subsequent
    connect-fail must NOT immediately open the breaker.
    """
    breaker = get_local_breaker()
    threshold = breaker.threshold
    # threshold must be > 2 for this test's "2 fails then recover" to
    # not have already tripped; default is 3.
    assert threshold > 2

    fail_count = {"n": 0}

    def _side_effect(req: Any, kwargs: Any) -> Any:
        fail_count["n"] += 1
        if fail_count["n"] <= 2:
            raise urllib.error.URLError("transient")
        # Third call onward: healthy response.
        return _FakeResponse(_ok_models_payload(["any"]))

    _patch_urlopen(monkeypatch, side_effect=_side_effect)

    # Two failures.
    for _ in range(2):
        capable, reason = await is_local_capable()
        assert capable is False
        assert reason == LOCAL_NOT_INSTALLED_REASON
    assert breaker._consecutive_failures == 2

    # One healthy probe -- counter must reset.
    capable, reason = await is_local_capable()
    assert capable is True
    assert reason is None
    assert breaker._consecutive_failures == 0
    assert breaker.is_open() is False


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_model_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOCAL_MODEL_ID_ENV, "qwen2.5:7b")
    # The runtime answers but the desired model isn't in the list.
    _patch_urlopen(monkeypatch, response_body=_ok_models_payload(["phi3", "llama3"]))
    capable, reason = await is_local_capable()
    assert capable is False
    assert reason == LOCAL_MODEL_NOT_LOADED_REASON


@pytest.mark.usefixtures("clear_local_env")
async def test_model_not_loaded_does_not_trip_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator-config errors (wrong model id) do NOT trip the breaker.

    Design invariant: an operator who sets
    ``TOYBOX_LOCAL_MODEL_ID=qwen2.5:7b`` but forgot to ``ollama pull``
    will hit the probe N>>threshold times before realising the
    misconfig. Tripping the breaker on those probes would surface
    ``breaker_open`` instead of the actionable
    ``LOCAL_MODEL_NOT_LOADED_REASON`` -- masking the real cause.

    The runtime answered cleanly (200 + valid JSON shape); only the
    desired model is missing. Every call must report
    ``LOCAL_MODEL_NOT_LOADED_REASON`` and the breaker must stay closed
    no matter how many times we probe.
    """
    monkeypatch.setenv(LOCAL_MODEL_ID_ENV, "qwen2.5:7b")
    _patch_urlopen(monkeypatch, response_body=_ok_models_payload(["phi3", "llama3"]))

    breaker = get_local_breaker()
    # Probe well past threshold so a buggy implementation would trip.
    probe_count = breaker.threshold * 3
    for _ in range(probe_count):
        capable, reason = await is_local_capable()
        assert capable is False
        assert reason == LOCAL_MODEL_NOT_LOADED_REASON
    # Breaker must remain closed -- this is operator config, not a
    # transient runtime fault.
    assert breaker.is_open() is False
    assert breaker._consecutive_failures == 0


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 with a non-JSON body is treated as model-not-loaded."""
    _patch_urlopen(monkeypatch, response_body=b"not json at all")
    capable, reason = await is_local_capable()
    assert capable is False
    assert reason == LOCAL_MODEL_NOT_LOADED_REASON


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_model_id_matches_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOCAL_MODEL_ID_ENV, "qwen2.5:7b")
    _patch_urlopen(
        monkeypatch,
        response_body=_ok_models_payload(["phi3", "qwen2.5:7b"]),
    )
    capable, reason = await is_local_capable()
    assert capable is True
    assert reason is None


@pytest.mark.usefixtures("clear_local_env")
async def test_is_local_capable_breaker_open_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-tripped local breaker returns breaker-open WITHOUT an HTTP call."""
    breaker = get_local_breaker()
    # Trip by recording threshold failures (default 3).
    for _ in range(breaker.threshold):
        breaker.record_failure()
    assert breaker.is_open() is True

    calls = _patch_urlopen(monkeypatch, response_body=_ok_models_payload(["any"]))
    capable, reason = await is_local_capable()
    assert capable is False
    assert reason == LOCAL_BREAKER_OPEN_REASON
    # No HTTP call should have happened.
    assert calls == []


# ----------------------------------------------------------------------
# (3) Per-adapter breaker independence
# ----------------------------------------------------------------------


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    secrets = tmp_path / "secrets.json"
    monkeypatch.setenv("TOYBOX_SECRETS_PATH", str(secrets))
    yield secrets


def _valid_token(ttl_sec: int = 3600) -> OAuthToken:
    return OAuthToken(
        access_token="acc-tok",
        refresh_token="ref-tok",
        expires_at=int(time.time()) + ttl_sec,
    )


async def _online_probe() -> bool:
    return True


@pytest.mark.usefixtures("clear_local_env", "secrets_dir")
async def test_tripping_claude_breaker_leaves_local_capable(
    monkeypatch: pytest.MonkeyPatch,
    secrets_dir: Path,
) -> None:
    """A Claude breaker open does NOT affect is_local_capable.

    The two breakers are fully independent instances -- one is built
    at the API call sites, the other is the module-level singleton
    returned by :func:`get_local_breaker`. Tripping Claude's must
    leave the local probe entirely unaffected.
    """
    monkeypatch.setenv("TOYBOX_CLAUDE_TEXT_MODEL", "claude-sonnet-4-6")
    save_token(_valid_token(), secrets_dir)

    # Trip the Claude breaker by 3 consecutive failures.
    claude_breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0)
    claude_breaker.record_failure()
    claude_breaker.record_failure()
    claude_breaker.record_failure()
    assert claude_breaker.is_open() is True

    capable, reason = await is_capable(
        claude_breaker,
        network_probe=_online_probe,
        listening_mode=int(ListeningMode.DEFAULT),
    )
    # Claude side reports breaker_open.
    assert capable is False
    assert reason is CapabilityReason.breaker_open

    # Local side: the local breaker is a separate instance and was
    # never touched -- the probe must report whatever the runtime
    # says (we mock it healthy here).
    _patch_urlopen(monkeypatch, response_body=_ok_models_payload(["any"]))
    local_capable, local_reason = await is_local_capable()
    assert local_capable is True
    assert local_reason is None


@pytest.mark.usefixtures("clear_local_env", "secrets_dir")
async def test_tripping_local_breaker_leaves_claude_capable(
    monkeypatch: pytest.MonkeyPatch,
    secrets_dir: Path,
) -> None:
    """A local breaker open does NOT affect is_capable (Claude).

    Symmetric to the Claude→local case: the local breaker's open
    state must NOT degrade the Claude probe.
    """
    monkeypatch.setenv("TOYBOX_CLAUDE_TEXT_MODEL", "claude-sonnet-4-6")
    save_token(_valid_token(), secrets_dir)

    # Trip the local breaker.
    local_breaker = get_local_breaker()
    for _ in range(local_breaker.threshold):
        local_breaker.record_failure()
    assert local_breaker.is_open() is True

    # Local side reports breaker_open.
    local_capable, local_reason = await is_local_capable()
    assert local_capable is False
    assert local_reason == LOCAL_BREAKER_OPEN_REASON

    # Claude side: a fresh Claude breaker is fully closed -- the
    # local trip has no bearing on it.
    claude_breaker = CircuitBreaker(threshold=3, cooldown_sec=60.0)
    capable, reason = await is_capable(
        claude_breaker,
        network_probe=_online_probe,
        listening_mode=int(ListeningMode.DEFAULT),
    )
    assert capable is True
    assert reason is None


# ----------------------------------------------------------------------
# (4) Breaker accumulates probe failures (not strictly required by the
# brief but pins the wiring: a stuck-down runtime eventually trips the
# breaker so subsequent probes short-circuit fast).
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("clear_local_env")
async def test_repeated_probe_failures_trip_local_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    breaker = get_local_breaker()
    threshold = breaker.threshold

    def _raise(req: Any, kwargs: Any) -> Any:
        raise urllib.error.URLError("connection refused")

    _patch_urlopen(monkeypatch, side_effect=_raise)
    for _ in range(threshold):
        capable, reason = await is_local_capable()
        assert capable is False
        # First N probes report not-installed (and accumulate failures).
        assert reason == LOCAL_NOT_INSTALLED_REASON
    # After ``threshold`` failures, the next probe short-circuits
    # to breaker-open without an HTTP call.
    assert breaker.is_open() is True
    capable, reason = await is_local_capable()
    assert capable is False
    assert reason == LOCAL_BREAKER_OPEN_REASON
