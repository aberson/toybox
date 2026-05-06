"""Static data + simple dataclasses for the image-gen subsystem.

Stdlib-only — DO NOT import torch / diffusers / transformers / rembg
here (they belong inside ``pipeline._run_pipeline_sync`` so module
import stays cheap when the feature is disabled).

Public exports (canonical-order):

* :data:`ACTION_SLOTS` — the 10 fixed action slot keys.
* :data:`ACTION_PROMPTS` — slot → SDXL pose-detail string.
* :class:`ToyActionStatus` — DB row status enum.
* :class:`ToyActionRow` — typed row dataclass mirroring the
  ``toy_actions`` table that F3 will land.
* :class:`GenerationContext` — frozen prompt-template inputs the
  worker passes to :func:`pipeline.generate_action`.
* :class:`ImageGenCapacityError` — raised on (real or simulated)
  CUDA OOM so the worker breaker can trip.
* :class:`ImageGenTimeoutError` — raised when the pipeline's
  :func:`asyncio.wait_for` cap fires.

The slot ordering and prompt strings are pinned to the plan's
Appendix §"Action vocabulary" — the kiosk + parent UI grid render
in this order, and downstream code (F4 worker, F8 grid) iterates
over ``ACTION_SLOTS`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# The 10 action slots, in canonical order. The parent UI's 2×5 grid
# renders in this order; the kiosk's StepCard sprite resolver looks
# up by slot key. Slot key strings are also the validation set the
# generator's ``action_slot`` field is checked against (F6).
ACTION_SLOTS: Final[tuple[str, ...]] = (
    "idle",
    "pointing",
    "looking",
    "jumping",
    "cheering",
    "thinking",
    "waving",
    "running",
    "sleeping",
    "confused",
)

# Per-slot pose-detail string injected into the SDXL prompt template
# (see :func:`pipeline._run_pipeline_sync`). Strings copied verbatim
# from plan §"Action vocabulary".
ACTION_PROMPTS: Final[dict[str, str]] = {
    "idle": "standing in a neutral pose, facing forward",
    "pointing": "pointing at something off to the side, arm extended",
    "looking": (
        "holding a magnifying glass up to one eye, examining something carefully"
    ),
    "jumping": "mid-jump in the air with both feet off the ground, arms raised",
    "cheering": "both arms raised overhead in celebration, smiling broadly",
    "thinking": "one hand on chin in a thoughtful pose, looking slightly upward",
    "waving": "one hand raised in a friendly wave gesture, smiling",
    "running": "mid-stride running pose, leaning forward, both feet visible",
    "sleeping": "curled up with eyes closed in a peaceful sleeping pose",
    "confused": (
        "shrugging with both shoulders raised, palms facing up, puzzled expression"
    ),
}


class ToyActionStatus(StrEnum):
    """Per-slot job status as persisted on ``toy_actions.status``.

    * ``queued``     — enqueued by the worker, not yet started.
    * ``running``    — pipeline currently generating.
    * ``done``       — PNG written, ``image_path`` populated.
    * ``failed``     — generation raised; ``error_msg`` populated.
    * ``superseded`` — a regen request preempted this row mid-flight
                       (the new row replaces it).
    """

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    superseded = "superseded"


@dataclass(slots=True)
class ToyActionRow:
    """Typed mirror of one ``toy_actions`` row.

    F3 lands the migration + storage layer; F2 ships the dataclass
    so capability + worker code can refer to a stable shape.
    """

    toy_id: str
    slot: str
    status: ToyActionStatus
    image_path: str | None = None
    seed: int | None = None
    error_msg: str | None = None
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class GenerationContext:
    """Prompt-template inputs that vary per-toy.

    Constructed by the worker (F4) from a ``toys`` row + (optionally)
    the joined ``personas`` row. Frozen because we never mutate it
    after construction; ``slots=True`` saves a per-instance dict.
    """

    toy_display_name: str
    persona_display_name: str | None
    tags: tuple[str, ...]


class ImageGenCapacityError(Exception):
    """Raised by :func:`pipeline.generate_action` on CUDA OOM.

    The worker's breaker reads this and trips on 3-in-60s.
    """


class ImageGenTimeoutError(Exception):
    """Raised when the per-call :func:`asyncio.wait_for` fires."""


__all__ = [
    "ACTION_PROMPTS",
    "ACTION_SLOTS",
    "GenerationContext",
    "ImageGenCapacityError",
    "ImageGenTimeoutError",
    "ToyActionRow",
    "ToyActionStatus",
]
