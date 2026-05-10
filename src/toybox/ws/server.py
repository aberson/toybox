"""WebSocket upgrade endpoint: Origin check + auth + per-subscriber fan-out.

Public surface:

* :func:`build_router` returns a FastAPI router exposing ``WS /ws``.
* :func:`get_pubsub` is the FastAPI dependency that returns the
  process-singleton :class:`toybox.core.pubsub.PubSub`. Tests override
  this dependency with a fresh hub per case.
* :data:`DEFAULT_ORIGIN_ALLOW_LIST` is the seed allow-list. Production
  startup may extend it via ``TOYBOX_LAN_IP``.

Wire protocol (after the upgrade succeeds):

1. Server sends ``{"type": "ready", "topics": [...]}`` listing every
   topic this socket is subscribed to.
2. Server pushes :class:`toybox.ws.envelope.Envelope` instances as
   JSON. Clients identify them by the ``topic`` field.
3. Server periodically sends ``{"type": "ping", "ts": "<iso>"}``.
   Clients reply with ``{"type": "pong"}``. Any frame from the client
   resets the silence timer; the explicit pong is the contract.
4. Client may send ``{"type": "subscribe", "topics": [...]}`` to
   replace its topic set (auth scope still applies).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, WebSocket, status
from fastapi.websockets import WebSocketDisconnect

from ..core.auth import TokenError, TokenScope, TokenSubject, validate_token
from ..core.pubsub import PubSub
from ..db import connect, resolve_db_path
from .envelope import Envelope
from .heartbeat import HeartbeatConfig, heartbeat_config
from .topics import Topic

_logger = logging.getLogger(__name__)

DEFAULT_ORIGIN_ALLOW_LIST: tuple[str, ...] = (
    "http://localhost:4000",
    "http://127.0.0.1:4000",
)

LAN_IP_ENV = "TOYBOX_LAN_IP"

# Topics each scope is allowed to subscribe to.
_PARENT_TOPICS = frozenset(
    {
        Topic.activity_state,
        Topic.activity,
        Topic.listening_mode,
        Topic.system,
        Topic.triggers_invalidate,
        Topic.transcript,
        # Step 24: operator dashboard pushes snapshots to parents only.
        Topic.metrics,
        # Phase F Step F4: action-sprite generation status. Parent-scope
        # only — the kiosk renders sprites via static <img src=...> and
        # never needs the per-job status stream.
        Topic.toy_actions,
    }
)
_CHILD_TOPICS = frozenset(
    {
        Topic.activity_state,
        Topic.activity,
        Topic.system,
        Topic.listening_mode,
    }
)
_ADMIN_TOPICS = _PARENT_TOPICS | _CHILD_TOPICS


def topics_for_scope(scope: TokenScope) -> frozenset[Topic]:
    """Return the topic set a given scope may subscribe to."""
    if scope is TokenScope.parent:
        return _PARENT_TOPICS
    if scope is TokenScope.child:
        return _CHILD_TOPICS
    if scope is TokenScope.admin:
        return _ADMIN_TOPICS
    return frozenset()


def origin_allow_list() -> tuple[str, ...]:
    """Resolve the runtime Origin allow-list (loopback + optional LAN IP)."""
    bases = list(DEFAULT_ORIGIN_ALLOW_LIST)
    lan_ip = os.environ.get(LAN_IP_ENV)
    if lan_ip:
        bases.append(f"http://{lan_ip}:4000")
    return tuple(bases)


def origin_allowed(origin: str | None, allow_list: tuple[str, ...]) -> bool:
    """Strict equality check against the allow-list."""
    if origin is None:
        return False
    return origin in allow_list


_PROCESS_PUBSUB: PubSub | None = None


def _process_pubsub() -> PubSub:
    """Lazy-init the process-singleton hub.

    Tests override the dependency directly; production callers reach
    the same instance through :func:`get_pubsub`.
    """
    global _PROCESS_PUBSUB
    if _PROCESS_PUBSUB is None:
        _PROCESS_PUBSUB = PubSub()
    return _PROCESS_PUBSUB


def get_pubsub() -> PubSub:
    """FastAPI dependency: return the process-singleton :class:`PubSub`."""
    return _process_pubsub()


def get_ws_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield a SQLite connection for the ws upgrade.

    ``check_same_thread=False`` is required because the upgrade handler
    runs in the asyncio event loop while the dependency-yielded
    connection may be touched from a worker thread; the connection is
    used by exactly one request, so SQLite's own locking is sufficient.
    """
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def get_heartbeat_config() -> HeartbeatConfig:
    """FastAPI dependency: env-tuned heartbeat cadence."""
    return heartbeat_config()


def get_origin_allow_list() -> tuple[str, ...]:
    """FastAPI dependency: env-tuned Origin allow-list."""
    return origin_allow_list()


async def _send_envelope(ws: WebSocket, envelope: Envelope) -> None:
    """Wire serialization for a single envelope."""
    await ws.send_text(envelope.model_dump_json())


def _resolve_subscription(
    scope: TokenScope,
    requested: list[str] | None,
) -> set[Topic]:
    """Filter the requested topics down to what ``scope`` may see."""
    resolved, _rejected = _resolve_subscription_with_rejects(scope, requested)
    return resolved


def _resolve_subscription_with_rejects(
    scope: TokenScope,
    requested: list[str] | None,
) -> tuple[set[Topic], list[str]]:
    """Like :func:`_resolve_subscription`, also reporting unknown names.

    ``rejected`` collects raw names the client sent that didn't match
    any :class:`Topic` value or weren't allowed by ``scope``. Callers
    can surface them in the ``subscribed`` reply so the client knows
    why their subscription set didn't match what they asked for.
    """
    allowed = topics_for_scope(scope)
    if not requested:
        # Default subscription: every topic the scope can see.
        return set(allowed), []
    out: set[Topic] = set()
    rejected: list[str] = []
    for raw in requested:
        try:
            topic = Topic(raw)
        except ValueError:
            rejected.append(raw)
            continue
        if topic in allowed:
            out.add(topic)
        else:
            rejected.append(raw)
    if not out:
        # Fall back to allowed defaults rather than zero subscriptions.
        out = set(allowed)
    return out, rejected


async def _safe_close(ws: WebSocket, code: int) -> None:
    # The client may have already torn the socket down (common during the
    # auth handshake — they open, see they have no token, and disconnect
    # before our close fires). Uvicorn raises RuntimeError when we try to
    # send a second close in that race; swallow it.
    try:
        await ws.close(code=code)
    except RuntimeError:
        pass


async def _read_token_from_first_message(ws: WebSocket) -> str | None:
    """Wait for the first client frame and pull a token out of it."""
    try:
        message = await ws.receive_json()
    except (WebSocketDisconnect, ValueError):
        return None
    if not isinstance(message, dict):
        return None
    if message.get("type") != "auth":
        return None
    token = message.get("token")
    return token if isinstance(token, str) and token else None


async def _serve(
    ws: WebSocket,
    *,
    subject: TokenSubject,
    initial_topics: list[str] | None,
    pubsub: PubSub,
    heartbeat: HeartbeatConfig,
) -> None:
    topics = _resolve_subscription(subject.scope, initial_topics)
    sub = pubsub.subscribe(topics)
    await ws.send_json({"type": "ready", "topics": sorted(t.value for t in topics)})

    last_seen = asyncio.get_event_loop().time()

    async def _send_loop() -> None:
        while True:
            envelope = await sub.get()
            await _send_envelope(ws, envelope)

    async def _recv_loop() -> None:
        nonlocal last_seen
        while True:
            try:
                message = await ws.receive_json()
            except WebSocketDisconnect:
                raise
            except ValueError:
                continue
            last_seen = asyncio.get_event_loop().time()
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "pong":
                continue
            if kind == "subscribe":
                topics_raw = message.get("topics")
                if isinstance(topics_raw, list):
                    string_topics = [t for t in topics_raw if isinstance(t, str)]
                    new_topics, rejected = _resolve_subscription_with_rejects(
                        subject.scope,
                        string_topics,
                    )
                    sub.state.topics = set(new_topics) | {Topic.system}
                    reply: dict[str, Any] = {
                        "type": "subscribed",
                        "topics": sorted(t.value for t in new_topics),
                    }
                    if rejected:
                        reply["rejected"] = list(rejected)
                    await ws.send_json(reply)

    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(heartbeat.ping_interval_sec)
            await ws.send_json({"type": "ping", "ts": datetime.now(UTC).isoformat()})

    async def _timeout_loop() -> None:
        nonlocal last_seen
        # The receive deadline fires on every interval; a tighter loop
        # keeps tests responsive without chewing CPU for production
        # defaults.
        check_interval = max(heartbeat.ping_timeout_sec / 4.0, 0.01)
        while True:
            await asyncio.sleep(check_interval)
            now = asyncio.get_event_loop().time()
            if now - last_seen > heartbeat.ping_timeout_sec:
                raise TimeoutError("ws heartbeat timeout")

    send_task = asyncio.create_task(_send_loop(), name="ws-send")
    recv_task = asyncio.create_task(_recv_loop(), name="ws-recv")
    ping_task = asyncio.create_task(_heartbeat_loop(), name="ws-ping")
    timeout_task = asyncio.create_task(_timeout_loop(), name="ws-timeout")
    tasks = (send_task, recv_task, ping_task, timeout_task)

    try:
        done, pending = await asyncio.wait(
            set(tasks),
            return_when=asyncio.FIRST_EXCEPTION,
        )
        # Cancel pending siblings and *await* their teardown so CPython
        # doesn't emit ``Task exception was never retrieved`` warnings.
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # Surface every fatal exception in the ``done`` set, not just
        # the first one — sibling failures (e.g. send_loop crashing at
        # the same instant as recv_loop) need to be visible.
        disconnect_seen = False
        timeout_seen = False
        fatal: BaseException | None = None
        for task in done:
            exc = task.exception()
            if exc is None:
                continue
            if isinstance(exc, WebSocketDisconnect):
                disconnect_seen = True
                continue
            if isinstance(exc, TimeoutError):
                timeout_seen = True
                continue
            # Log other sibling exceptions at WARNING so debugging
            # sees the full picture even if a higher-priority error
            # is raised below.
            _logger.warning("ws task %s raised %s", task.get_name(), exc, exc_info=exc)
            if fatal is None:
                fatal = exc
        if timeout_seen:
            try:
                await ws.close(code=status.WS_1011_INTERNAL_ERROR)
            except RuntimeError:
                pass
            return
        if disconnect_seen and fatal is None:
            return
        if fatal is not None:
            raise fatal
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        # Drain cancellations / residual exceptions so they don't get
        # logged by the event loop after we return.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task, result in zip(tasks, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(
                result,
                asyncio.CancelledError | WebSocketDisconnect,
            ):
                _logger.warning(
                    "ws task %s teardown error: %s",
                    task.get_name(),
                    result,
                    exc_info=result,
                )
        sub.close()


def build_router() -> APIRouter:
    """Build the ws router with the dependency-overridable helpers wired in."""
    router = APIRouter()

    @router.websocket("/ws")
    async def ws_endpoint(
        ws: WebSocket,
        pubsub: Annotated[PubSub, Depends(get_pubsub)],
        conn: Annotated[sqlite3.Connection, Depends(get_ws_db)],
        heartbeat: Annotated[HeartbeatConfig, Depends(get_heartbeat_config)],
        allow_list: Annotated[tuple[str, ...], Depends(get_origin_allow_list)],
        token: Annotated[str | None, Query()] = None,
        topics: Annotated[list[str] | None, Query()] = None,
    ) -> None:
        origin = ws.headers.get("origin")
        if not origin_allowed(origin, allow_list):
            # 403 close before accept so unauthorized origins never see
            # an open socket. Starlette's ``send_denial_response``
            # produces the HTTP-level 403 we want.
            _logger.warning(
                "ws origin rejected: origin=%r allow_list=%r "
                "(set TOYBOX_LAN_IP env var to add http://<lan-ip>:4000)",
                origin,
                allow_list,
            )
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await ws.accept()

        plaintext = token
        if not plaintext:
            plaintext = await _read_token_from_first_message(ws)

        if not plaintext:
            await _safe_close(ws, status.WS_1008_POLICY_VIOLATION)
            return

        try:
            subject = validate_token(conn, plaintext)
        except TokenError:
            await _safe_close(ws, status.WS_1008_POLICY_VIOLATION)
            return

        try:
            await _serve(
                ws,
                subject=subject,
                initial_topics=topics,
                pubsub=pubsub,
                heartbeat=heartbeat,
            )
        except WebSocketDisconnect:
            return
        except Exception:  # pragma: no cover - defensive
            _logger.exception("ws handler crashed")
            await _safe_close(ws, status.WS_1011_INTERNAL_ERROR)

    return router


__all__ = [
    "DEFAULT_ORIGIN_ALLOW_LIST",
    "LAN_IP_ENV",
    "build_router",
    "get_heartbeat_config",
    "get_origin_allow_list",
    "get_pubsub",
    "get_ws_db",
    "origin_allow_list",
    "origin_allowed",
    "topics_for_scope",
]
