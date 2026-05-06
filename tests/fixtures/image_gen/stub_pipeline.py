"""Deterministic stub pipeline for CI / non-GPU dev runs.

Honors the same envelope env vars as
:mod:`toybox.image_gen.pipeline`'s real path:

* ``TOYBOX_IMAGE_GEN_STUB_MODE=oom`` — raise the synthetic CUDA-OOM
  sentinel that ``pipeline._run_pipeline_sync`` catches + re-raises
  as :class:`ImageGenCapacityError`.
* ``TOYBOX_IMAGE_GEN_STUB_DELAY_SEC`` — block this many seconds
  before returning, so a low ``TOYBOX_IMAGE_GEN_TIMEOUT_SEC`` trips
  :class:`ImageGenTimeoutError`.

The stub returns a 16×16 RGBA PNG. Pixel colors are derived from a
hash of ``(slot, seed)`` so the output varies but is reproducible:
calling twice with the same arguments yields byte-identical output.
At least one pixel is fully transparent so downstream alpha-channel
assertions pass.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
from typing import Any


def _stub_oom_active() -> bool:
    raw = os.environ.get("TOYBOX_IMAGE_GEN_STUB_MODE")
    if raw is None:
        return False
    return raw.strip().lower() == "oom"


def _stub_delay_sec() -> float:
    raw = os.environ.get("TOYBOX_IMAGE_GEN_STUB_DELAY_SEC")
    if raw is None:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _key_seed(slot: str, seed: int) -> bytes:
    """Derive a stable byte sequence from ``(slot, seed)``."""
    raw = f"{slot}:{seed}".encode()
    return hashlib.sha256(raw).digest()


def generate_action_stub(
    reference_bytes: bytes,
    slot: str,
    seed: int,
    ctx: Any,
) -> bytes:
    """Return a deterministic 16×16 RGBA PNG keyed off ``(slot, seed)``.

    ``reference_bytes`` and ``ctx`` are accepted to match the real
    pipeline's signature but are not part of the determinism key —
    that's intentional: downstream tests want "same slot+seed →
    same bytes" regardless of the reference photo.

    OOM mode is signalled by raising
    :class:`toybox.image_gen.pipeline._StubCudaOOM` so the real
    pipeline catches it. We import the sentinel locally to avoid
    a circular import at module top.
    """
    del reference_bytes, ctx  # not used by the stub

    if _stub_oom_active():
        # Local import so the stub module remains importable even
        # if pipeline.py grew a hard dep we don't want at fixture
        # collection time.
        from toybox.image_gen.pipeline import _StubCudaOOM

        raise _StubCudaOOM(f"stub OOM for slot={slot!r}")

    delay = _stub_delay_sec()
    if delay > 0:
        # Block — the calling thread is the asyncio.to_thread worker
        # so this does NOT freeze the event loop. ``asyncio.wait_for``
        # in the caller fires once timeout elapses; the worker
        # thread keeps running but its result is dropped.
        time.sleep(delay)

    # Lazy import Pillow so the stub doesn't slow down fixture
    # collection on hosts without it (Pillow IS a hard dep, but
    # the lazy pattern matches the real pipeline's posture).
    from PIL import Image

    digest = _key_seed(slot, seed)
    # Build a 16×16 RGBA image. Each pixel's RGBA is taken from a
    # rolling slice of the digest, repeating to fill 16*16=256
    # pixels × 4 channels = 1024 bytes. SHA-256 gives us 32 bytes;
    # we tile it.
    needed = 16 * 16 * 4
    tiled = (digest * ((needed // len(digest)) + 1))[:needed]
    pixels = bytearray(tiled)
    # Force at least one fully transparent pixel so alpha-channel
    # tests pass even if the digest happened to land on all 0xFF.
    pixels[3] = 0  # first pixel's alpha = 0
    img = Image.frombytes("RGBA", (16, 16), bytes(pixels))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


__all__ = ["generate_action_stub"]
