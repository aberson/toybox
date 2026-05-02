"""Curated NLP trigger registry.

Public surface for Phase A Step 6. The registry merges shipped
defaults (``defaults.json``) into a user-editable copy at
``data/triggers.json`` (or ``$TOYBOX_TRIGGERS_USER_PATH``) and exposes
a deterministic, offline :func:`match` that scans an utterance against
both the curated patterns and the dynamic toy-name source.
"""

from __future__ import annotations

from .registry import (
    DEFAULT_USER_PATH,
    DEFAULTS_PATH,
    SCHEMA_VERSION,
    TRIGGERS_USER_PATH_ENV,
    Intent,
    load_registry,
    match,
    user_path,
)

__all__ = [
    "DEFAULTS_PATH",
    "DEFAULT_USER_PATH",
    "Intent",
    "SCHEMA_VERSION",
    "TRIGGERS_USER_PATH_ENV",
    "load_registry",
    "match",
    "user_path",
]
