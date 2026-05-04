"""FastAPI application factory.

Kept separate from :mod:`toybox.main` so tests can build the app without
running uvicorn or going through CLI parsing.
"""

from __future__ import annotations

from fastapi import FastAPI

from .api.activities import router as activities_router
from .api.auth import router as auth_router
from .api.children import router as children_router
from .api.health import router as health_router
from .api.listening import router as listening_router
from .api.toys import router as toys_router
from .api.transcripts import router as transcripts_router
from .ws.server import build_router as build_ws_router


def create_app() -> FastAPI:
    """Build and return the FastAPI app for the Phase A skeleton."""
    app = FastAPI(title="toybox", version="0.1.0")
    app.include_router(health_router)
    app.include_router(listening_router)
    app.include_router(auth_router)
    app.include_router(activities_router)
    app.include_router(children_router)
    app.include_router(toys_router)
    app.include_router(transcripts_router)
    app.include_router(build_ws_router())
    return app


__all__ = ["create_app"]
