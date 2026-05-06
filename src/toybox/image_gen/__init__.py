"""Phase F image-gen subsystem.

Public surface — re-exports from :mod:`.models`, :mod:`.capability`,
and :mod:`.pipeline`. Importing this package is intentionally cheap;
heavy deps (torch / diffusers / transformers / rembg) live behind a
lazy import inside :func:`pipeline._run_pipeline_sync`.

The package is structured so:

* F2 ships the static data (slots, prompts) + capability gate +
  pipeline + CLI entry — no production wiring.
* F3 lands the migration + storage layer (consuming
  :class:`ToyActionStatus` + :data:`ACTION_SLOTS`).
* F4 lands the worker (importing :func:`generate_action`).
* F5 wires the REST endpoints.
* F6/F7/F8 layer the generator + kiosk + parent UI on top.
"""

from __future__ import annotations

from .capability import is_image_gen_capable
from .models import (
    ACTION_PROMPTS,
    ACTION_SLOTS,
    GenerationContext,
    ImageGenCapacityError,
    ImageGenTimeoutError,
    ToyActionRow,
    ToyActionStatus,
)
from .pipeline import generate_action

__all__ = [
    "ACTION_PROMPTS",
    "ACTION_SLOTS",
    "GenerationContext",
    "ImageGenCapacityError",
    "ImageGenTimeoutError",
    "ToyActionRow",
    "ToyActionStatus",
    "generate_action",
    "is_image_gen_capable",
]
