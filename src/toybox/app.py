"""FastAPI application factory.

Kept separate from :mod:`toybox.main` so tests can build the app without
running uvicorn or going through CLI parsing.
"""

from __future__ import annotations

from fastapi import FastAPI

from .api.health import router as health_router
from .api.listening import router as listening_router


def create_app() -> FastAPI:
    """Build and return the FastAPI app for the Phase A skeleton."""
    app = FastAPI(title="toybox", version="0.1.0")
    app.include_router(health_router)
    app.include_router(listening_router)
    return app


__all__ = ["create_app"]
