"""Tier C composite fallback for the image-gen subsystem.

Phase F.5 Step F.5-3a. The composite path produces sprites WITHOUT
diffusion: rembg cuts the toy out of the reference photo, then Pillow
pastes the cutout onto a hand-drawn cartoon action template. Pure
CPU, ~100 ms per sprite. The worker dispatches here when the F.5-3a
capability gate returns False with a non-env-disabled
:class:`toybox.image_gen.capability.CapabilityReason` (no CUDA, low
VRAM, or missing checkpoints) — the toy box still gets sprites, just
at lower fidelity than the Tier B diffusion pipeline.

Public entry: :func:`composite_action` — async, runs the heavy work
(rembg + Pillow) on a worker thread via :func:`asyncio.to_thread`.
The signature mirrors :func:`pipeline.generate_action` so the worker
can dispatch to either based on the capability gate state without
ceremony.

ALL heavy imports (``rembg``, ``PIL``) live INSIDE the worker thread
so module import stays cheap when the feature isn't running. The
``test_lazy_imports`` test pins this contract — a top-level
``import rembg`` here would fail it.

Templates layout (operator-curated in F.5-3b, placeholders shipped
in F.5-1):

* ``data/sprites/templates/<slot>.png`` — 256×256 RGBA template per
  :data:`toybox.image_gen.models.ACTION_SLOTS` member.
* ``data/sprites/templates/manifest.json`` — per-slot metadata::

      {
        "idle":     {"toy_box": [40, 60, 90, 110], "behind": false},
        "pointing": {"toy_box": [50, 70, 100, 120], "behind": true},
        ...
      }

  ``toy_box`` is ``[x0, y0, x1, y1]`` in template-pixel coordinates
  (256×256 → values 0..255). ``behind=true`` composites the toy
  UNDER the template (e.g. behind a pointing-arrow shape so the
  arrow is on top); ``behind=false`` composites OVER.

If the requested slot's template or manifest entry is missing,
:func:`composite_action` raises
:class:`toybox.image_gen.models.ImageGenCapacityError` with a
``"composite template missing for slot=..."`` detail; the worker
catches this and writes ``error_msg="image_gen_composite_only"`` on
the row.

Templates and manifest are cached at module level after first read so
subsequent calls don't re-read the disk. The cache is keyed on the
templates-dir path so a test using a tmp_path templates dir doesn't
collide with the production cache.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Final

from .models import (
    ACTION_SLOTS,
    GenerationContext,
    ImageGenCapacityError,
)

_logger = logging.getLogger(__name__)

# Env knob mirroring the operator runbook §"Sprite templates".
TEMPLATES_DIR_ENV: Final[str] = "TOYBOX_SPRITE_TEMPLATES_DIR"
DEFAULT_TEMPLATES_DIR: Final[str] = "data/sprites/templates"

# Output dimensions (kiosk renders at 96-128 px; mirror Tier B's
# default 128 px target).
OUTPUT_DIM: Final[int] = 128

# Per-process caches. Keyed on the resolved templates-dir path so
# tests using a tmp_path can coexist with a production cache from a
# prior call within the same process. Values are PNG bytes (template)
# / parsed dict (manifest). A simple lock guards the cache writes —
# concurrent worker jobs read in parallel, but the first-write race
# would otherwise produce duplicate disk reads.
_TEMPLATE_CACHE: dict[tuple[str, str], bytes] = {}
_MANIFEST_CACHE: dict[str, dict[str, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

# Cached rembg ONNX runtime session. ``new_session`` constructs an
# InferenceSession at ~10-100 ms+ per call; on a 10-sprite ingest
# that's wasted overhead. Caching at module level avoids the
# rebuild cost. The session is thread-safe per onnxruntime's docs.
_REMBG_SESSION: Any | None = None
_REMBG_SESSION_LOCK = threading.Lock()


def _templates_dir() -> Path:
    """Return the configured templates dir, honouring the env override."""
    raw = os.environ.get(TEMPLATES_DIR_ENV)
    return Path(raw) if raw else Path(DEFAULT_TEMPLATES_DIR)


def _validate_slot(slot: str) -> None:
    """Reject any ``slot`` outside :data:`ACTION_SLOTS`.

    Mirrors :func:`toybox.storage.toy_actions._validate_slot` —
    defense against slot-injection before we construct a filesystem
    path. The worker already validates upstream, but this is a public
    entry point so we re-validate here.
    """
    if slot not in ACTION_SLOTS:
        raise ValueError(f"slot {slot!r} is not in ACTION_SLOTS (got {sorted(ACTION_SLOTS)!r})")


def _load_template_bytes(templates_dir: Path, slot: str) -> bytes:
    """Read ``<templates_dir>/<slot>.png``, caching after first hit.

    Raises :class:`ImageGenCapacityError` if the file is missing —
    the worker maps this to ``error_msg="image_gen_composite_only"``.
    """
    key = (str(templates_dir), slot)
    cached = _TEMPLATE_CACHE.get(key)
    if cached is not None:
        return cached
    path = templates_dir / f"{slot}.png"
    if not path.is_file():
        raise ImageGenCapacityError(f"composite template missing for slot={slot}")
    data = path.read_bytes()
    with _CACHE_LOCK:
        _TEMPLATE_CACHE[key] = data
    return data


def _load_manifest(templates_dir: Path) -> dict[str, dict[str, Any]]:
    """Read ``<templates_dir>/manifest.json``, caching after first hit.

    Raises :class:`ImageGenCapacityError` if the file is missing or
    malformed — same recovery path as a missing template.
    """
    key = str(templates_dir)
    cached = _MANIFEST_CACHE.get(key)
    if cached is not None:
        return cached
    path = templates_dir / "manifest.json"
    if not path.is_file():
        raise ImageGenCapacityError(f"composite template missing for slot=<manifest>: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImageGenCapacityError(f"composite manifest unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ImageGenCapacityError(
            f"composite manifest malformed: expected object, got {type(raw).__name__}"
        )
    # Validate shape lazily — only the entry we read at composite
    # time needs strict validation; keep the cache as-loaded so a
    # malformed sibling slot doesn't poison the whole manifest.
    typed: dict[str, dict[str, Any]] = {
        slot: dict(entry) for slot, entry in raw.items() if isinstance(entry, dict)
    }
    with _CACHE_LOCK:
        _MANIFEST_CACHE[key] = typed
    return typed


def reset_caches_for_tests() -> None:
    """Drop the cached templates + manifest + rembg session. Used by test fixtures."""
    with _CACHE_LOCK:
        _TEMPLATE_CACHE.clear()
        _MANIFEST_CACHE.clear()
    reset_rembg_session_for_tests()


def reset_rembg_session_for_tests() -> None:
    """Drop the cached rembg session so a test fixture can rebuild it."""
    global _REMBG_SESSION
    with _REMBG_SESSION_LOCK:
        _REMBG_SESSION = None


def _get_rembg_session() -> Any:
    global _REMBG_SESSION
    if _REMBG_SESSION is not None:
        return _REMBG_SESSION
    with _REMBG_SESSION_LOCK:
        if _REMBG_SESSION is None:
            from rembg import new_session

            _REMBG_SESSION = new_session(
                model_name="u2net",
                providers=["CPUExecutionProvider"],
            )
    return _REMBG_SESSION


def _composite_sync(
    reference_bytes: bytes,
    slot: str,
    templates_dir: Path,
) -> bytes:
    """Synchronous composite body — runs on a worker thread.

    Steps (mirroring the spec):

    1. rembg u2net cutout of ``reference_bytes`` (CPU).
    2. Load (cached) ``<slot>.png`` template.
    3. Load (cached) manifest.json; pull ``toy_box`` + ``behind``.
    4. Resize the cutout to fit ``toy_box``.
    5. Composite onto the template (alpha-aware paste); ``behind=True``
       → cutout under template; ``behind=False`` → cutout over template.
    6. Resize final to :data:`OUTPUT_DIM` × :data:`OUTPUT_DIM` RGBA.
    7. Encode as PNG, return bytes.
    """
    # Lazy imports keep the module import cheap (no rembg / Pillow at
    # collection time on hosts without the image_gen extras).
    from PIL import Image
    from rembg import remove

    manifest = _load_manifest(templates_dir)
    entry = manifest.get(slot)
    if entry is None:
        raise ImageGenCapacityError(
            f"composite template missing for slot={slot}: no manifest entry"
        )
    raw_box = entry.get("toy_box")
    if (
        not isinstance(raw_box, list)
        or len(raw_box) != 4
        or not all(isinstance(v, (int, float)) for v in raw_box)
    ):
        raise ImageGenCapacityError(f"composite manifest entry for slot={slot} has invalid toy_box")
    x0, y0, x1, y1 = (int(v) for v in raw_box)
    if x1 <= x0 or y1 <= y0:
        raise ImageGenCapacityError(f"composite manifest entry for slot={slot} has empty toy_box")
    behind = bool(entry.get("behind", False))

    template_bytes = _load_template_bytes(templates_dir, slot)
    template = Image.open(io.BytesIO(template_bytes)).convert("RGBA")

    # rembg cutout — u2net on CPU. The InferenceSession is cached at
    # module level (~10-100 ms saved per call after the first); the
    # underlying model file is mmap'd by onnxruntime. CPU is forwarded
    # explicitly so a host with a CUDA-onnxruntime build doesn't
    # accidentally allocate VRAM here.
    session = _get_rembg_session()
    cutout_bytes = remove(reference_bytes, session=session)
    if not isinstance(cutout_bytes, (bytes, bytearray)):
        # rembg can also return a PIL image / numpy array depending
        # on input; force bytes for the predictable Pillow path.
        raise ImageGenCapacityError("rembg returned non-bytes; expected PNG-encoded bytes")
    cutout = Image.open(io.BytesIO(bytes(cutout_bytes))).convert("RGBA")

    # Resize cutout into the toy_box. Use LANCZOS for the downsample
    # — kiosk-quality output, not real-time.
    target_w = x1 - x0
    target_h = y1 - y0
    cutout_resized = cutout.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # Composite: build a new RGBA canvas the size of the template,
    # then paste in the canonical order. Pillow's ``alpha_composite``
    # honours the cutout's alpha for clean edges. ``Image.paste`` with
    # the cutout itself as the mask achieves the same outcome and is
    # simpler.
    canvas_size = template.size
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    if behind:
        # Cutout first (under), template on top.
        canvas.paste(cutout_resized, (x0, y0), cutout_resized)
        canvas.alpha_composite(template)
    else:
        # Template first (under), cutout on top.
        canvas.alpha_composite(template)
        canvas.paste(cutout_resized, (x0, y0), cutout_resized)

    # Final resize to the kiosk-target dimension. RGBA preserved.
    out = canvas.resize((OUTPUT_DIM, OUTPUT_DIM), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    out.save(buffer, format="PNG")
    return buffer.getvalue()


async def composite_action(
    reference_bytes: bytes,
    slot: str,
    seed: int,
    ctx: GenerationContext,
) -> bytes:
    """Composite the toy photo onto a hand-drawn cartoon action template.

    Same signature as :func:`pipeline.generate_action` so the worker
    can dispatch to either based on the capability gate state. ``seed``
    is accepted but unused (composites are fully deterministic from
    inputs). ``ctx`` is similarly unused; the composite path doesn't
    need toy display name / persona / tags because there's no prompt
    to construct.

    Returns 128×128 RGBA PNG bytes.

    Raises:
        ValueError: When ``slot`` is not in :data:`ACTION_SLOTS`.
        ImageGenCapacityError: When the template or manifest entry for
            ``slot`` is missing or malformed. The worker maps this to
            ``error_msg="image_gen_composite_only"`` on the failed row.
    """
    del seed, ctx  # accepted for signature symmetry; not used
    _validate_slot(slot)
    templates_dir = _templates_dir()
    return await asyncio.to_thread(_composite_sync, reference_bytes, slot, templates_dir)


__all__ = [
    "DEFAULT_TEMPLATES_DIR",
    "OUTPUT_DIM",
    "TEMPLATES_DIR_ENV",
    "composite_action",
    "reset_caches_for_tests",
    "reset_rembg_session_for_tests",
]
