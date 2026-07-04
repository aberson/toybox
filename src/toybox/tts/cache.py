"""Clip cache for server-rendered TTS audio (Phase Z Z4).

One tiny module owns the ENTIRE (voice, text) → clip mapping:

* on disk:   ``<data_root>/tts/<voice>/<sha256(text)[:16]>.wav``
* on the wire: ``/api/static/tts/<voice>/<sha256(text)[:16]>.wav``

Both shapes are derived from the SAME key by :func:`clip_path` /
:func:`clip_url`, so a producer that persists a URL and the static
mount that serves the file can never disagree — the kiosk NEVER
computes hashes (plan §6 wire-shape rule: the producer publishes, the
consumer reads).

``TTS_AUDIO_URL_PREFIX`` is the ONE url-prefix constant. Every
producer (``toybox.api.activities`` enqueue hooks) and the static
mount in :mod:`toybox.app` import it from here. This deliberately does
NOT repeat the songs two-constants wart (``_SONG_AUDIO_URL_PREFIX`` is
duplicated across ``api/activities.py`` and
``activities/interjection.py``) — a grep-gate test
(``tests/unit/tts/test_cache.py``) asserts the literal appears in this
file only.

Content-hash keying doubles as dedupe: templates repeat step text
heavily, so the same sentence spoken by the same voice is rendered
once and reused (plan §8 clip-cache-growth mitigation).

Data-root resolution is the engine's (``TOYBOX_TTS_STUB``-independent)
``TOYBOX_DATA_DIR`` convention — reused via the same-package private
helper so there is exactly one resolver in the ``toybox.tts`` package.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Final

from .engine import _data_root

# THE url prefix for served clips. The static mount in toybox.app and
# every persisted ``spoken_*_url`` metadata value derive from this one
# constant (code-quality.md §2, one source of truth).
TTS_AUDIO_URL_PREFIX: Final[str] = "/api/static/tts"

# On-disk clip tree under the data root (``TOYBOX_DATA_DIR``, default
# ``data/``): ``data/tts/<voice>/<key>.wav``.
CLIPS_SUBDIR: Final[str] = "tts"

# Hex chars of sha256(text) used as the clip key. 16 hex chars = 64
# bits — collision-safe for a household-scale corpus while keeping
# filenames short.
KEY_HEX_LEN: Final[int] = 16

# Kokoro voice ids are lowercase ascii + digits + underscores
# (``af_heart``, ``am_michael``, ``bf_emma``, ...). The voice id
# becomes BOTH a path segment and a URL segment, so validate it here
# (defense-in-depth against traversal via a corrupt persona JSON —
# mirrors ``image_gen.worker._output_path``'s toy_id re-validation).
# NOTE: matched via ``fullmatch`` — ``re.match`` + ``$`` would accept a
# trailing newline (``$`` matches just before a terminal ``\n``).
_VOICE_ID_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9_]{1,64}")


def is_safe_voice_id(voice: str) -> bool:
    """True iff ``voice`` is usable as a clip path/URL segment."""
    return bool(_VOICE_ID_RE.fullmatch(voice))


def _validated_voice(voice: str) -> str:
    if not is_safe_voice_id(voice):
        raise ValueError(f"invalid tts voice id: {voice!r}")
    return voice


def clips_root() -> Path:
    """Root of the on-disk clip cache (``<data_root>/tts``)."""
    return _data_root() / CLIPS_SUBDIR


def clip_key(text: str) -> str:
    """Content-hash key for ``text`` (first 16 hex of sha256)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:KEY_HEX_LEN]


def _clip_filename(text: str) -> str:
    return f"{clip_key(text)}.wav"


def clip_path(voice: str, text: str) -> Path:
    """On-disk path for the clip of ``text`` in ``voice``.

    Raises :class:`ValueError` on an unsafe voice id (never derived
    from user text — callers resolve the voice via
    :func:`toybox.api.activities._neural_voice_from_summary`, which
    falls back to the default on anything unsafe).
    """
    return clips_root() / _validated_voice(voice) / _clip_filename(text)


def clip_url(voice: str, text: str) -> str:
    """Wire URL for the clip of ``text`` in ``voice``.

    Derived from the SAME key as :func:`clip_path`, so a persisted URL
    always resolves to the file the worker writes (or 404s until the
    render lands — the designed Web Speech fallback).
    """
    return f"{TTS_AUDIO_URL_PREFIX}/{_validated_voice(voice)}/{_clip_filename(text)}"


def clip_exists(voice: str, text: str) -> bool:
    """True iff the clip for ``(voice, text)`` is already rendered."""
    return clip_path(voice, text).is_file()


__all__ = [
    "CLIPS_SUBDIR",
    "KEY_HEX_LEN",
    "TTS_AUDIO_URL_PREFIX",
    "clip_exists",
    "clip_key",
    "clip_path",
    "clip_url",
    "clips_root",
    "is_safe_voice_id",
]
