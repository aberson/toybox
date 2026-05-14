"""Live capability gate.

Gathers the six runtime signals the pure
:func:`toybox.core.capability.compose_capability` function consumes:

* listening mode (parent slider)        — ``settings.listening_mode``
* config presence                       — ``TOYBOX_CLAUDE_TEXT_MODEL`` env
* token presence + token expiry         — ``~/.toybox/secrets.json``
* breaker state                         — :class:`CircuitBreaker`
* recent 429 / rate-limited             — derived from breaker open-cause
* network reachability                  — TCP probe to api.anthropic.com

The pure composition function is reused as-is — DO NOT redefine
``CapabilityReason`` here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.capability import (
    CapabilityReason,
    CapabilityState,
    compose_capability,
)
from ..core.listening import ListeningMode, current_mode
from ..db import connect, resolve_db_path
from .breaker import CircuitBreaker, get_local_breaker
from .client import TEXT_MODEL_ENV
from .local import (
    DEFAULT_LOCAL_RUNTIME_URL,
    LOCAL_MODEL_ID_ENV,
    LOCAL_RUNTIME_URL_ENV,
)
from .oauth import OAuthToken, load_token

_logger = logging.getLogger(__name__)

NetworkProbe = Callable[[], Awaitable[bool]]

_DEFAULT_PROBE_HOST = "api.anthropic.com"
_DEFAULT_PROBE_PORT = 443
_DEFAULT_PROBE_TIMEOUT_SEC = 2.0


def _connect_probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def default_network_probe() -> bool:
    """Default reachability probe — TCP to api.anthropic.com:443.

    Tests substitute a stub returning False (or True) so they don't have
    to hit real DNS. The blocking ``socket.create_connection`` runs in
    ``asyncio.to_thread`` so we don't stall the event loop.
    """
    return await asyncio.to_thread(
        _connect_probe, _DEFAULT_PROBE_HOST, _DEFAULT_PROBE_PORT, _DEFAULT_PROBE_TIMEOUT_SEC
    )


def _config_present() -> bool:
    """True iff the minimal pinned-model env var is non-empty.

    The default fallback in :mod:`toybox.ai.client` means we can run
    without explicitly setting it, BUT the capability matrix demands a
    way to surface ``config_missing``. We treat an empty string as
    explicitly-cleared to give operators a simple kill-switch.
    """
    raw = os.environ.get(TEXT_MODEL_ENV)
    if raw is None:
        # Unset → fall back to default model → config IS present.
        return True
    return bool(raw.strip())


def _read_listening_mode() -> int:
    """Read the persisted listening mode, defaulting to OFFLINE on error.

    A DB error here is benign at the capability layer — it just blocks
    AI calls until the DB is reachable, which is the same protective
    behavior we want for ``listening_mode == 1``.
    """
    try:
        conn = connect(resolve_db_path())
    except sqlite3.Error as exc:
        _logger.warning("capability: cannot open DB to read mode: %s", exc)
        return int(ListeningMode.OFFLINE)
    try:
        return int(current_mode(conn))
    except (sqlite3.Error, ValueError) as exc:
        _logger.warning("capability: cannot read listening mode: %s", exc)
        return int(ListeningMode.OFFLINE)
    finally:
        conn.close()


def _gather_token() -> tuple[bool, bool]:
    """Return ``(token_present, token_expired)``."""
    token: OAuthToken | None = load_token()
    if token is None:
        return False, False
    return True, token.is_expired(int(time.time()))


async def is_capable(
    breaker: CircuitBreaker,
    *,
    network_probe: NetworkProbe | None = None,
    listening_mode: int | None = None,
) -> tuple[bool, CapabilityReason | None]:
    """Gather live signals and call :func:`compose_capability`.

    Args:
        breaker: The shared in-process :class:`CircuitBreaker`.
        network_probe: Optional override; defaults to a TCP probe of
            ``api.anthropic.com:443``. Tests pass a stub returning the
            desired state without hitting DNS.
        listening_mode: Optional override; if ``None`` the mode is read
            from the SQLite settings row. Tests pass an int directly to
            avoid touching the DB.

    Returns:
        ``(True, None)`` if every gate is clear; otherwise
        ``(False, reason)`` where ``reason`` is the highest-priority
        :class:`CapabilityReason`.
    """
    probe = network_probe if network_probe is not None else default_network_probe

    mode = listening_mode if listening_mode is not None else _read_listening_mode()
    config_present = _config_present()
    token_present, token_expired = _gather_token()
    # A 429-induced OPEN surfaces as ``rate_limited`` (priority above
    # ``breaker_open``); a failure-induced OPEN surfaces as
    # ``breaker_open``. Both come from the same breaker — the
    # ``is_rate_limited()`` predicate distinguishes them.
    rate_limited = breaker.is_rate_limited()
    breaker_open = breaker.is_open() and not rate_limited
    network_online = await probe()

    state = CapabilityState(
        listening_mode=mode,
        config_present=config_present,
        token_present=token_present,
        token_expired=token_expired,
        breaker_open=breaker_open,
        rate_limited=rate_limited,
        network_online=network_online,
    )
    return compose_capability(state)


async def is_capable_from_state(state: CapabilityState) -> tuple[bool, CapabilityReason | None]:
    """Direct passthrough useful for tests that build state by hand."""
    return compose_capability(state)


# Module-level stable reason constants. Pinned as constants (not inline
# strings) so integration tests can ``from toybox.ai.capability import
# LOCAL_NOT_INSTALLED_REASON`` and assert against the canonical value
# rather than copy-pasting the prose -- this is the same rationale the
# CapabilityReason enum has for Claude, just without the enum overhead
# since the local probe's failure causes don't overlap that enum.

#: Cannot connect to ``<TOYBOX_LOCAL_RUNTIME_URL>/v1/models`` -- the
#: runtime is not running, the URL points at the wrong host/port, or a
#: firewall is in the way. Mirrors the pre-probe stub's wording so
#: anything that grep'd the old string still matches.
LOCAL_NOT_INSTALLED_REASON: str = "local runtime not yet installed"

#: ``TOYBOX_LOCAL_MODEL_ID`` was set but the configured id isn't present
#: in the ``/v1/models`` response, OR the response is malformed (missing
#: ``data`` array, non-JSON body, etc).
LOCAL_MODEL_NOT_LOADED_REASON: str = "local model not loaded"

#: The per-adapter local circuit breaker is OPEN. Independent of the
#: Claude breaker -- tripping Claude's does NOT disable the local path
#: and vice versa.
LOCAL_BREAKER_OPEN_REASON: str = "local breaker open"

#: Timeout for the ``/v1/models`` probe. Matches the Claude probe's 2s
#: budget (see :data:`_DEFAULT_PROBE_TIMEOUT_SEC`) -- a slow local
#: runtime should fail the gate fast rather than stall the propose path.
_LOCAL_PROBE_TIMEOUT_SEC: float = 2.0


def _probe_local_models_sync(url: str) -> dict[str, Any]:
    """Synchronously GET ``<url>/v1/models`` and return the parsed JSON.

    Raises :class:`urllib.error.URLError` /
    :class:`urllib.error.HTTPError` / :class:`OSError` /
    :class:`TimeoutError` on connection failure, and
    :class:`json.JSONDecodeError` on a malformed body. The caller
    converts these to a stable reason string -- internal exception
    types are not part of the public contract.

    Uses ``urllib.request`` to mirror the rest of the codebase's HTTP
    idiom (see :mod:`toybox.ai.client`); no third-party HTTP client
    is introduced here.
    """
    probe_url = url.rstrip("/") + "/v1/models"
    req = urllib.request.Request(probe_url, method="GET")
    with urllib.request.urlopen(req, timeout=_LOCAL_PROBE_TIMEOUT_SEC) as resp:
        raw = resp.read()
    parsed: Any = json.loads(raw)
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("expected object", "<response>", 0)
    return parsed


def _model_id_in_payload(payload: dict[str, Any], model_id: str) -> bool:
    """Check if ``model_id`` is present in an OpenAI-shape ``/v1/models`` body.

    OpenAI-compatible runtimes (Ollama, LM Studio, llama.cpp's server)
    return ``{"object": "list", "data": [{"id": "...", ...}, ...]}``.
    Tolerates extra keys; ``data`` must be a list of dicts with at
    least an ``id`` field for any entry to match.
    """
    data = payload.get("data")
    if not isinstance(data, list):
        return False
    for entry in data:
        if isinstance(entry, dict) and entry.get("id") == model_id:
            return True
    return False


async def is_local_capable() -> tuple[bool, str | None]:
    """Probe the locally-hosted runtime and return ``(capable, reason)``.

    Walks four gates in priority order:

    1. **Breaker open** -- if the per-adapter local breaker is OPEN we
       short-circuit without an HTTP call. Returns
       :data:`LOCAL_BREAKER_OPEN_REASON`.
    2. **Cannot connect** -- GET ``<TOYBOX_LOCAL_RUNTIME_URL>/v1/models``
       fails with a network error or non-2xx. Returns
       :data:`LOCAL_NOT_INSTALLED_REASON` and records a failure on the
       breaker (so repeated probes can trip it).
    3. **Malformed response** -- the body isn't a JSON object, or
       parses but is shape-wrong. Returns
       :data:`LOCAL_MODEL_NOT_LOADED_REASON`. Counts as a breaker
       failure (a runtime that answers but malformed-ly is still not
       usable).
    4. **Model not loaded** -- ``TOYBOX_LOCAL_MODEL_ID`` is set but the
       id isn't in the ``/v1/models`` response. Returns
       :data:`LOCAL_MODEL_NOT_LOADED_REASON`. NOT a breaker failure --
       the runtime is healthy, just the desired model isn't loaded.

    Returns ``(True, None)`` on success and resets the breaker's
    consecutive-failure counter.

    The Claude :func:`is_capable` is unchanged -- the two probes are
    intentionally orthogonal so a Claude breaker trip can't disable
    the local path and vice versa (see
    :func:`toybox.ai.breaker.get_local_breaker`).
    """
    breaker = get_local_breaker()
    if breaker.is_open():
        return False, LOCAL_BREAKER_OPEN_REASON

    url = os.environ.get(LOCAL_RUNTIME_URL_ENV, DEFAULT_LOCAL_RUNTIME_URL)
    model_id = os.environ.get(LOCAL_MODEL_ID_ENV)

    try:
        payload = await asyncio.to_thread(_probe_local_models_sync, url)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        _logger.debug("local runtime probe failed (%s): %s", type(exc).__name__, exc)
        breaker.record_failure()
        return False, LOCAL_NOT_INSTALLED_REASON
    except (json.JSONDecodeError, ValueError) as exc:
        _logger.debug("local runtime probe returned malformed body: %s", exc)
        breaker.record_failure()
        return False, LOCAL_MODEL_NOT_LOADED_REASON

    if model_id is not None and model_id.strip():
        if not _model_id_in_payload(payload, model_id):
            # The runtime is healthy enough to answer; the model just
            # isn't loaded. Do NOT trip the breaker for this -- it's an
            # operator-config issue, not a transient runtime fault.
            # Also clear any prior consecutive failures: the runtime
            # answered cleanly, so a (connect-fail × 2) → (200 but
            # wrong-model) sequence must not leave the breaker one
            # connect-fail away from tripping.
            breaker.record_success()
            return False, LOCAL_MODEL_NOT_LOADED_REASON

    # Successful probe -- clear any prior failures so a transient outage
    # that auto-recovered doesn't sit on the breaker indefinitely.
    breaker.record_success()
    return True, None


__all__ = [
    "LOCAL_BREAKER_OPEN_REASON",
    "LOCAL_MODEL_NOT_LOADED_REASON",
    "LOCAL_NOT_INSTALLED_REASON",
    "NetworkProbe",
    "default_network_probe",
    "is_capable",
    "is_capable_from_state",
    "is_local_capable",
]
