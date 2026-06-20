"""FastAPI application factory.

Kept separate from :mod:`toybox.main` so tests can build the app without
running uvicorn or going through CLI parsing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .activities.element_corpus import elements_root
from .activities.song_corpus import songs_audio_root
from .api.activities import router as activities_router
from .api.audio import router as audio_router
from .api.auth import router as auth_router
from .api.banned_themes_settings import router as banned_themes_settings_router
from .api.catalog import router as catalog_router
from .api.children import router as children_router
from .api.clickable_words_enabled_settings import (
    router as clickable_words_enabled_settings_router,
)
from .api.game_complexity_settings import router as game_complexity_settings_router
from .api.game_linearity_settings import router as game_linearity_settings_router
from .api.health import router as health_router
from .api.image_gen_settings import router as image_gen_settings_router
from .api.jokes_enabled_settings import router as jokes_enabled_settings_router
from .api.listening import router as listening_router
from .api.metrics import router as metrics_router
from .api.parent_involvement_settings import (
    router as parent_involvement_settings_router,
)
from .api.play_standalone_enabled_settings import (
    router as play_standalone_enabled_settings_router,
)
from .api.play_target_depth_settings import router as play_target_depth_settings_router
from .api.read_me_button_enabled_settings import (
    router as read_me_button_enabled_settings_router,
)
from .api.rewards import router as rewards_router
from .api.rooms import router as rooms_router
from .api.search import router as search_router
from .api.songs_enabled_settings import router as songs_enabled_settings_router
from .api.spoken_text_limit_settings import router as spoken_text_limit_settings_router
from .api.toys import admin_router as toys_admin_router
from .api.toys import router as toys_router
from .api.transcript_retention_settings import router as transcript_retention_settings_router
from .api.transcripts import router as transcripts_router
from .core.transcript_retention import run_transcript_sweep_loop
from .db import connect, resolve_db_path
from .image_gen.capability import is_image_gen_capable
from .image_gen.worker import (
    ImageGenWorker,
    start_image_gen_worker,
    stop_image_gen_worker,
)
from .storage.images import images_root
from .ws.envelope import build_envelope
from .ws.server import build_router as build_ws_router
from .ws.server import get_pubsub
from .ws.topics import Topic

_logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build and return the FastAPI app for the Phase A skeleton."""
    app = FastAPI(title="toybox", version="0.1.0")
    app.include_router(health_router)
    app.include_router(listening_router)
    app.include_router(audio_router)
    app.include_router(image_gen_settings_router)
    app.include_router(banned_themes_settings_router)
    app.include_router(transcript_retention_settings_router)
    app.include_router(play_target_depth_settings_router)
    app.include_router(spoken_text_limit_settings_router)
    # Phase W Step W1: two true-stub household dials (parent involvement +
    # game complexity). PERSIST ONLY — wired to no behavior yet; a later
    # phase consumes the values. GET is household-read, PUT is parent-scope.
    app.include_router(parent_involvement_settings_router)
    app.include_router(game_complexity_settings_router)
    # Phase W Step W2: household game-linearity dial. WIRED (not a stub) —
    # the propose path reads it and passes linear_only=(value=="linear")
    # into the offline generator to exclude branching templates. GET is
    # household-read, PUT is parent-scope.
    app.include_router(game_linearity_settings_router)
    # Phase K Step K2 + Phase L Step L5: five parent-controlled feature
    # flags (originally eight; L5 removed the three Phase K play-surface
    # flags as part of re-framing jokes/songs as per-activity reward
    # types). Order within the cohort is alphabetical so the router list
    # stays mechanically scannable.
    app.include_router(clickable_words_enabled_settings_router)
    app.include_router(jokes_enabled_settings_router)
    app.include_router(play_standalone_enabled_settings_router)
    app.include_router(read_me_button_enabled_settings_router)
    app.include_router(songs_enabled_settings_router)
    app.include_router(auth_router)
    app.include_router(activities_router)
    app.include_router(children_router)
    app.include_router(toys_router)
    # Phase P Step P6 — admin actions that span every toy (e.g. global
    # "regenerate every toy" sprite re-render). Mounted alongside the
    # per-toy router so the helpers + Pydantic models stay in one file
    # but the URL space (``/api/admin/...``) stays distinct from the
    # per-toy verbs (``/api/toys/{id}/...``).
    app.include_router(toys_admin_router)
    app.include_router(rewards_router)
    app.include_router(rooms_router)
    app.include_router(transcripts_router)
    app.include_router(metrics_router)
    # Phase R Step R4: activity + template search (no auth — read-only).
    # Prefix is embedded in search.py's router declaration (``/api/search``).
    app.include_router(search_router)
    # Phase T Step T2: full offline template catalog (no auth — read-only).
    # Prefix is embedded in catalog.py's router declaration (``/api/catalog``).
    app.include_router(catalog_router)
    app.include_router(build_ws_router())

    # Static read-only mount for committed toy + room images so the
    # parent UI can render thumbnails via plain ``<img src=...>`` tags
    # (img elements can't carry custom auth headers, and this is a
    # local-dev kiosk — no public exposure). Sits under ``/api`` so
    # the vite dev proxy forwards it to the backend without extra
    # config. ``check_dir=False`` keeps the app bootable on a fresh
    # checkout before the first upload has created the directory.
    app.mount(
        "/api/static/images",
        StaticFiles(directory=str(images_root()), check_dir=False),
        name="images",
    )

    # Phase K Step K13 — Static read-only mount for the bundled song
    # corpus audio (committed .mp3 files rendered one-shot by
    # ``scripts/generate_song_corpus.py``). Mirrors the image mount
    # idiom above. The kiosk's K12 ``SongPlayer`` falls back to
    # ``/api/static/songs/audio/<song_id>.mp3`` when the per-step
    # metadata blob doesn't carry an explicit ``audio_url`` — K13's
    # standalone propose path emits the URL directly using the same
    # prefix so both paths converge on this single mount.
    # ``check_dir=False`` keeps the app bootable before the operator
    # runs the Coqui render script.
    app.mount(
        "/api/static/songs/audio",
        StaticFiles(directory=str(songs_audio_root()), check_dir=False),
        name="songs_audio",
    )

    # Phase M Step M3 — Static read-only mount for the bundled element
    # sprites (one .png per corpus entry, e.g. ``au-79.png``). Mirrors
    # the images + songs mounts above. The kiosk's ElementCard loads
    # ``/api/static/elements/<element_id>.png`` directly via ``<img
    # src=...>``; an HTTP 404 falls back to a Vite-bundled
    # periodic-table avatar asset (``frontend/src/child/assets/
    # periodic_table_fallback.png``) handled inline in the React
    # component's onError. ``check_dir=False`` keeps the app bootable
    # before M2b ships the sprite generator output — the deferred-
    # sprite case is identical to the kiosk fallback path so the
    # surface degrades gracefully.
    app.mount(
        "/api/static/elements",
        StaticFiles(directory=str(elements_root()), check_dir=False),
        name="elements",
    )

    # Phase F Step F3 — image-gen capability boot probe. Logs the
    # resolved branch + reason at INFO so ops can spot a degraded boot
    # in the journal without calling a status endpoint. Runs in the
    # synchronous ``create_app`` body (cheap — checkpoint check is just
    # ``Path.is_file()`` + a lazy torch probe) so the line shows up
    # before the lifespan fires. The capability check itself is also
    # called per-request from the F4 worker + F5 endpoints; this boot
    # probe is purely informational.
    #
    # Broad-except wrap is deliberate: a corrupt torch / CUDA driver
    # can raise something other than ImportError (e.g. RuntimeError on
    # driver mismatch). The probe is informational — never let it
    # crash app boot. We log a WARNING with the exception class so ops
    # can still see what happened.
    try:
        capable, _reason_enum, reason = is_image_gen_capable()
    except Exception as exc:  # noqa: BLE001 -- boot resilience: never let image-gen probe crash app
        capable, reason = False, f"probe raised {type(exc).__name__}"
        _logger.warning(
            "image-gen capability probe raised %s: %s",
            type(exc).__name__,
            exc,
        )
    _logger.info("image-gen capability=%s reason=%s", capable, reason)

    return app


# ---------------------------------------------------------------------
# Phase F Step F4 — image-gen worker lifespan helpers
# ---------------------------------------------------------------------


def default_worker_conn_factory() -> Callable[[], sqlite3.Connection]:
    """Return a ``conn_factory`` matching :class:`ImageGenWorker`'s contract.

    Mirrors :func:`toybox.metrics.default_conn_factory` — a per-call
    ``sqlite3.Connection`` opened against ``TOYBOX_DB_PATH`` with
    ``check_same_thread=False`` (the worker runs DB work via
    :func:`asyncio.to_thread`, so the connection may bounce between
    threads). Each helper opens + closes its own connection so the
    long-lived consumer task doesn't pin a single sqlite handle.
    """

    def _factory() -> sqlite3.Connection:
        return connect(resolve_db_path(), check_same_thread=False)

    return _factory


def default_worker_emit() -> Callable[[Topic, dict[str, Any]], Awaitable[None]]:
    """Return an ``emit`` callable that publishes via the singleton pubsub.

    The callable awaits-but-returns-immediately: pubsub.publish is sync
    and never blocks (drop-oldest backpressure). We wrap it in an async
    function so the worker's ``EmitCallable`` typing matches both the
    real publisher and async test stubs.
    """
    pubsub = get_pubsub()

    async def _emit(topic: Topic, payload: dict[str, Any]) -> None:
        envelope = build_envelope(topic=topic, payload=payload)
        pubsub.publish(envelope)

    return _emit


@contextlib.asynccontextmanager
async def image_gen_worker_lifespan(app: FastAPI) -> AsyncIterator[ImageGenWorker]:
    """Start the image-gen worker; stop on shutdown. Composable.

    Usage::

        async with image_gen_worker_lifespan(app) as worker:
            yield

    The worker is the singleton retrieved via
    :func:`toybox.image_gen.worker.get_image_gen_worker`. Restart
    recovery runs BEFORE the consumer task starts, with the recovered
    count logged at INFO. Both the smoke and the metrics-only lifespan
    in :mod:`toybox.main` compose this helper so the worker is wired
    identically across runtime modes.
    """
    del app  # not used; kept for the lifespan signature contract.
    worker = await start_image_gen_worker(
        default_worker_conn_factory(),
        default_worker_emit(),
    )
    try:
        yield worker
    finally:
        await stop_image_gen_worker()


# ---------------------------------------------------------------------
# Phase I Step I2 — transcript retention sweep lifespan
# ---------------------------------------------------------------------


@contextlib.asynccontextmanager
async def transcript_sweep_lifespan(app: FastAPI) -> AsyncIterator[asyncio.Task[None]]:
    """Spawn the periodic transcript-retention sweep task; cancel on shutdown.

    Mirrors :func:`image_gen_worker_lifespan` in shape: a background
    ``asyncio.Task`` is created on enter and cancelled + awaited on
    exit. The loop driver wakes every 10s by default, reads the
    current retention preset via :func:`current_retention_seconds`,
    computes a pipeline-format cutoff, and runs one bulk
    ``DELETE FROM transcripts`` statement. Errors per tick are logged
    and the loop continues — only ``CancelledError`` (raised by
    ``task.cancel()`` on shutdown) escapes.
    """
    del app  # not used; kept for the lifespan signature contract.
    task = asyncio.create_task(
        run_transcript_sweep_loop(default_worker_conn_factory()),
        name="transcript-sweep-loop",
    )
    try:
        yield task
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


__all__ = [
    "create_app",
    "default_worker_conn_factory",
    "default_worker_emit",
    "image_gen_worker_lifespan",
    "transcript_sweep_lifespan",
]
