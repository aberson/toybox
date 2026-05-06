"""FastAPI application factory.

Kept separate from :mod:`toybox.main` so tests can build the app without
running uvicorn or going through CLI parsing.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.activities import router as activities_router
from .api.auth import router as auth_router
from .api.children import router as children_router
from .api.health import router as health_router
from .api.listening import router as listening_router
from .api.metrics import router as metrics_router
from .api.rooms import router as rooms_router
from .api.toys import router as toys_router
from .api.transcripts import router as transcripts_router
from .image_gen.capability import is_image_gen_capable
from .storage.images import images_root
from .ws.server import build_router as build_ws_router

_logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build and return the FastAPI app for the Phase A skeleton."""
    app = FastAPI(title="toybox", version="0.1.0")
    app.include_router(health_router)
    app.include_router(listening_router)
    app.include_router(auth_router)
    app.include_router(activities_router)
    app.include_router(children_router)
    app.include_router(toys_router)
    app.include_router(rooms_router)
    app.include_router(transcripts_router)
    app.include_router(metrics_router)
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
        capable, reason = is_image_gen_capable()
    except Exception as exc:  # noqa: BLE001 -- boot resilience: never let image-gen probe crash app
        capable, reason = False, f"probe raised {type(exc).__name__}"
        _logger.warning(
            "image-gen capability probe raised %s: %s",
            type(exc).__name__,
            exc,
        )
    _logger.info("image-gen capability=%s reason=%s", capable, reason)

    return app


__all__ = ["create_app"]
