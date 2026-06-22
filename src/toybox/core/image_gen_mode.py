"""Operator-controlled image-gen mode toggle.

Companion to :mod:`toybox.image_gen.worker`. Stores
``settings.image_gen_mode`` (TEXT, ``"cartoon"``/``"composite"``) and
defaults to ``"cartoon"`` when the row is missing — legacy databases
that predate the seed row in 0001_initial.sql still resolve cleanly
without an explicit migration step.

The mode is read fresh per-job by the worker, so the operator can flip
the toggle and have the next regenerate request honour it without a
backend restart. ``"cartoon"`` keeps the current capability-driven
dispatch (Tier B SD 1.5 when capable, Tier C composite when not);
``"composite"`` forces Tier C even on a capable GPU host;
``"claude_svg"`` ("Claude Images") bypasses the local SD pipeline
entirely and has Claude draw each sprite as a cartoon SVG via the OAuth
API (idle slot self-animating) — it needs a Claude token, not a GPU, so
the worker skips the capability/breaker gates for it. The three modes
are mutually exclusive — one generation backend at a time. The
``env_disabled`` hard-off branch in the worker still wins regardless of
mode.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Literal

from ..ws.envelope import Envelope, build_envelope
from ..ws.topics import Topic

_logger = logging.getLogger(__name__)

ImageGenMode = Literal["cartoon", "composite", "claude_svg"]

IMAGE_GEN_MODE_CARTOON: ImageGenMode = "cartoon"
IMAGE_GEN_MODE_COMPOSITE: ImageGenMode = "composite"
IMAGE_GEN_MODE_CLAUDE_SVG: ImageGenMode = "claude_svg"
IMAGE_GEN_MODE_DEFAULT: ImageGenMode = IMAGE_GEN_MODE_CARTOON

_SETTINGS_KEY = "image_gen_mode"
_VALID_MODES: frozenset[str] = frozenset(
    {IMAGE_GEN_MODE_CARTOON, IMAGE_GEN_MODE_COMPOSITE, IMAGE_GEN_MODE_CLAUDE_SVG}
)

Publisher = Callable[[Envelope], None]


def current_image_gen_mode(conn: sqlite3.Connection) -> ImageGenMode:
    """Return the persisted image-gen mode, defaulting to ``"cartoon"``.

    Legacy databases that predate the 0001_initial.sql seed row return
    the default rather than raising — keeps existing prod DBs working
    without an explicit migration.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        return IMAGE_GEN_MODE_DEFAULT
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    if raw == IMAGE_GEN_MODE_CARTOON:
        return IMAGE_GEN_MODE_CARTOON
    if raw == IMAGE_GEN_MODE_COMPOSITE:
        return IMAGE_GEN_MODE_COMPOSITE
    if raw == IMAGE_GEN_MODE_CLAUDE_SVG:
        return IMAGE_GEN_MODE_CLAUDE_SVG
    # Truncate the unrecognized value so a corrupt blob doesn't flood
    # the logs. Mirrors the format used in :mod:`toybox.core.mic_state`.
    truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
    _logger.warning(
        "settings.%s=%r unparseable; falling back to %s",
        _SETTINGS_KEY,
        truncated,
        IMAGE_GEN_MODE_DEFAULT,
    )
    return IMAGE_GEN_MODE_DEFAULT


def set_image_gen_mode(
    conn: sqlite3.Connection,
    mode: str,
    publisher: Publisher | None = None,
) -> ImageGenMode:
    """Persist ``mode`` and emit an ``image_gen.mode`` envelope.

    Raises :class:`ValueError` when ``mode`` is not one of the literal
    values ``"cartoon"`` / ``"composite"`` / ``"claude_svg"``.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"invalid image_gen_mode: {mode!r}")
    canonical: ImageGenMode
    if mode == IMAGE_GEN_MODE_CARTOON:
        canonical = IMAGE_GEN_MODE_CARTOON
    elif mode == IMAGE_GEN_MODE_COMPOSITE:
        canonical = IMAGE_GEN_MODE_COMPOSITE
    else:
        canonical = IMAGE_GEN_MODE_CLAUDE_SVG
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, canonical),
        )
    if publisher is not None:
        envelope = build_envelope(
            topic=Topic.image_gen_mode,
            payload={"mode": canonical},
        )
        publisher(envelope)
    return canonical


__all__ = [
    "IMAGE_GEN_MODE_CARTOON",
    "IMAGE_GEN_MODE_CLAUDE_SVG",
    "IMAGE_GEN_MODE_COMPOSITE",
    "IMAGE_GEN_MODE_DEFAULT",
    "ImageGenMode",
    "Publisher",
    "current_image_gen_mode",
    "set_image_gen_mode",
]
