"""Phase S Step S2: sync per-step avatar animation annotator.

Called from ``post_approve`` (a synchronous FastAPI route) to assign
one animation name from the ``Animation`` vocabulary to each activity
step. Persisted into ``activity_steps.metadata_json`` before the WS
broadcast fires, so the kiosk always receives a fully-annotated activity.

Uses ``SyncAIClient.complete_text_sync`` (not ``AIClient.complete_text``)
because ``post_approve`` is synchronous and ``asyncio.run()`` is unsafe
inside a running event loop. The capability gate is intentionally bypassed
— any failure (offline, 401, timeout) logs a WARNING and returns ``{}``
so the kiosk falls back to ``float`` for all steps.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from ..activities.models import Animation
from .client import AIMessage, AIResponse, SyncAIClient

_logger = logging.getLogger(__name__)

# Hard timeout for one annotator call — shorter than judge (30s) because
# animation hints are lower stakes than rubric scores.
ANIMATOR_TIMEOUT_SEC: float = 15.0

# Max output tokens — one JSON object with N small annotation records.
ANIMATOR_MAX_TOKENS: int = 512

_VALID_ANIMATIONS: frozenset[str] = frozenset(a.value for a in Animation)

# Vocabulary guidance baked into the system prompt so Claude doesn't need
# to know animation CSS properties — it just picks a name.
_SYSTEM_PROMPT = (
    "You are annotating child activity steps with avatar animation hints.\n"
    "For each step, pick ONE animation from this vocabulary:\n"
    "  float  — gentle idle bob (calm/narrative steps)\n"
    "  pulse  — soft scale pulse (friendly/informational steps)\n"
    "  wobble — playful rock (fun/humorous steps)\n"
    "  jump   — energetic bounce (exciting/action steps)\n"
    "  shine  — golden glow (celebration/reward steps)\n"
    "  spin   — slow rotation (transformative/magical steps)\n"
    "Respond ONLY with valid JSON in this shape:\n"
    '{"annotations": [{"seq": <int>, "animation": "<name>"}, ...]}\n'
    "No markdown, no explanation, just the JSON object."
)


def annotate_step_animations(
    steps: Sequence[Any],  # ActivityStepResponse — uses .seq, .body, .kind
    persona_id: str | None,
    client: SyncAIClient,
) -> dict[int, str]:
    """Assign one animation name to each step.

    Returns a ``{seq: animation_name}`` mapping.  Reward steps (``kind
    == "reward"``) always receive ``"shine"`` regardless of Claude output
    — overridden after parsing so the caller doesn't need to filter.

    On any exception (network, timeout, parse error, invalid JSON):
    logs ``WARNING`` and returns ``{}`` — callers treat empty as "no
    annotation, use fallback".
    """
    if not steps:
        return {}

    # Build the user message listing each step body + seq.
    persona_note = f"Persona: {persona_id}" if persona_id else "Persona: none (neutral)"
    step_lines = "\n".join(f"Step {s.seq}: {s.body}" for s in steps)
    user_content = f"{persona_note}\n\nSteps:\n{step_lines}"

    try:
        response: AIResponse = client.complete_text_sync(
            [AIMessage(role="user", content=user_content)],
            max_tokens=ANIMATOR_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
        )
        raw = response.text.strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"expected dict, got {type(parsed).__name__}")
        annotations_list = parsed.get("annotations")
        if not isinstance(annotations_list, list):
            raise ValueError("missing or non-list 'annotations' key")

        result: dict[int, str] = {}
        for item in annotations_list:
            if not isinstance(item, dict):
                continue
            seq = item.get("seq")
            anim = item.get("animation")
            if not isinstance(seq, int) or not isinstance(anim, str):
                continue
            if anim not in _VALID_ANIMATIONS:
                _logger.debug(
                    "animator: unknown animation %r for seq %d — dropped", anim, seq
                )
                continue
            result[seq] = anim

        # Override reward steps — always shine.
        for s in steps:
            if getattr(s, "kind", None) == "reward":
                result[s.seq] = Animation.shine.value

        return result

    except Exception as exc:
        _logger.warning(
            "animator: annotation failed (%s: %s) — returning empty dict",
            type(exc).__name__,
            exc,
        )
        return {}
