"""Claude vision call: identify a toy from a photo.

Step 16 calls this from the toy-upload router after the bytes have
been validated, hashed, and staged. The flow is:

1. Caller passes downscaled bytes (≤1600 long edge — see
   :func:`toybox.storage.images.downscale_for_vision`).
2. We send them as a base64 image to Claude Haiku
   (``TOYBOX_CLAUDE_VISION_MODEL``) with a user prompt asking for
   strict JSON: ``{display_name, tags, persona_match_id}``. The
   :class:`~toybox.ai.client.AIClient` Protocol's ``describe_image``
   has no separate ``system`` param so we inline the instruction
   into the user prompt.
3. Parse the response with Pydantic. Strip a Markdown ```json fence
   if Claude wraps the body. On parse failure log WARNING and return
   ``(None, "malformed")``.
4. Timeouts → log WARNING and return ``(None, "timeout")``.
5. SDK 429 / rate-limit responses → log INFO and return
   ``(None, "rate_limited")``.

The function is async to match the rest of the AI surface, but the
underlying SDK call runs in ``asyncio.to_thread`` so we don't block
the event loop. The per-request timeout (defaults to
``TOYBOX_VISION_TIMEOUT_SEC`` or 30s) is enforced via
``asyncio.wait_for``.

PRIVACY NOTE: Toy photos may include children's faces. The plan
§"Privacy guarantees" allows this exactly once at ingest under the
parent-acknowledged consent on the upload form. After commit the
photo lives only on the device.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .client import AIClient

_logger = logging.getLogger(__name__)

VISION_TIMEOUT_ENV: Final[str] = "TOYBOX_VISION_TIMEOUT_SEC"
DEFAULT_VISION_TIMEOUT_SEC: Final[float] = 30.0

# We send the downscaled JPEG to Claude — the storage helper always
# re-encodes to JPEG before passing bytes here, so this is the only
# media type we declare on the wire.
_VISION_MEDIA_TYPE: Final[str] = "image/jpeg"

# Inlined system prompt — see module docstring for why we don't pass
# a separate ``system`` arg.
_PROMPT: Final[str] = (
    "You are an assistant for a children's toy library. Identify the "
    "toy in the photo and return STRICT JSON with these exact keys:\n"
    '  {"display_name": "<short friendly name, 1-40 chars>",\n'
    '   "tags": ["<tag>", ...],\n'
    '   "persona_match_id": null}\n'
    "Tags are lowercase short keywords (1-3 words each), e.g. 'plush', "
    "'unicorn', 'pink'. Aim for 3-6 tags. If you can't tell what the toy "
    "is, return display_name 'unknown toy' with empty tags. NEVER "
    "include any commentary outside the JSON object."
)

# Strip a ```json ...``` (or plain ```...```) fence if Claude wraps the
# body. Anchored at the start of the trimmed string so we don't match
# stray fences elsewhere.
_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"\A```(?:json)?\s*(.*?)\s*```\Z",
    re.DOTALL | re.IGNORECASE,
)


class ToyVisionSuggestion(BaseModel):
    """Parsed vision response — what the parent UI uses to pre-fill fields."""

    model_config = ConfigDict(frozen=True)

    display_name: str = Field(min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=20)
    persona_match_id: str | None = None


def vision_timeout_sec() -> float:
    """Return the configured per-call timeout (env-overrideable)."""
    raw = os.environ.get(VISION_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_VISION_TIMEOUT_SEC
    try:
        parsed = float(raw)
    except ValueError:
        _logger.warning("%s=%r is not a float; using default", VISION_TIMEOUT_ENV, raw)
        return DEFAULT_VISION_TIMEOUT_SEC
    if parsed <= 0:
        _logger.warning("%s=%f <= 0; using default", VISION_TIMEOUT_ENV, parsed)
        return DEFAULT_VISION_TIMEOUT_SEC
    return parsed


def _classify_exception(exc: BaseException) -> str:
    """Classify an SDK exception by class first, then message.

    The Anthropic SDK's exception module is gated by the SDK install,
    which the test suite doesn't carry — we therefore can't import
    ``RateLimitError``/``APITimeoutError`` directly. We match on the
    class name (covers the real SDK exceptions when present) and only
    fall back to substring search on the message for plain
    ``Exception("...rate limit...")`` raised by stubs.
    """
    cls_name = type(exc).__name__
    cls_name_l = cls_name.lower()

    # Class-name match — the cheap, robust path. Real SDK errors land
    # here ('RateLimitError', 'APITimeoutError', etc).
    if "ratelimit" in cls_name_l:
        return "rate_limited"
    if isinstance(exc, TimeoutError) or "timeout" in cls_name_l:
        return "timeout"

    # Message fallback — only for plain ``Exception("rate limit")`` etc.
    msg = str(exc).lower()
    if "429" in msg or "rate limit" in msg or "rate-limit" in msg:
        return "rate_limited"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    return "error"


def _strip_fence(text: str) -> str:
    """Remove a leading/trailing ```json fence if present."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    if match is None:
        return stripped
    return match.group(1).strip()


def _parse_response(text: str) -> ToyVisionSuggestion | None:
    """Try to parse Claude's reply into a :class:`ToyVisionSuggestion`."""
    body = _strip_fence(text)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        _logger.warning("toy_vision: response is not valid JSON (%s): %r", exc, text[:200])
        return None
    if not isinstance(payload, dict):
        _logger.warning("toy_vision: response JSON is not an object: %r", text[:200])
        return None
    # Normalise tags: lowercase, strip, dedupe (case-insensitive),
    # drop empties. The model occasionally returns a stringified list
    # or a single string — fold both into a list of strings.
    raw_tags = payload.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    if not isinstance(raw_tags, list):
        raw_tags = []
    seen: set[str] = set()
    clean_tags: list[str] = []
    for tag in raw_tags:
        if not isinstance(tag, str):
            continue
        norm = tag.strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        clean_tags.append(norm)
    payload["tags"] = clean_tags
    try:
        return ToyVisionSuggestion(**payload)
    except ValidationError as exc:
        _logger.warning("toy_vision: response failed schema validation: %s", exc)
        return None


async def suggest_fields(
    client: AIClient,
    image_bytes: bytes,
    *,
    timeout_sec: float | None = None,
) -> ToyVisionSuggestion | tuple[None, str]:
    """Ask Claude to identify the toy in ``image_bytes``.

    Returns:
        On success: the parsed :class:`ToyVisionSuggestion`.
        On any failure: ``(None, reason)`` where ``reason`` is a short
        string the API surfaces as ``vision_error`` to the UI. Reasons:

        * ``"timeout"``      — the per-call timeout fired
        * ``"rate_limited"`` — Claude returned 429 / rate-limit
        * ``"malformed"``    — response wasn't parseable JSON / schema
        * ``"error"``        — anything else (logged with traceback)

    The caller (the toy router) inspects the type of the return: a
    :class:`ToyVisionSuggestion` populates ``suggested``, otherwise
    ``suggested=null`` and ``vision_error=reason`` are surfaced.
    """
    timeout = timeout_sec if timeout_sec is not None else vision_timeout_sec()
    try:
        response = await asyncio.wait_for(
            client.describe_image(
                image_bytes,
                prompt=_PROMPT,
                media_type=_VISION_MEDIA_TYPE,
                max_tokens=512,
            ),
            timeout=timeout,
        )
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        _logger.warning("toy_vision: call timed out after %.1fs", timeout)
        return (None, "timeout")
    except Exception as exc:  # noqa: BLE001 -- we deliberately blanket-catch
        kind = _classify_exception(exc)
        if kind == "rate_limited":
            _logger.info("toy_vision: rate-limited by Claude (%s)", exc)
        elif kind == "timeout":
            _logger.warning("toy_vision: call timed out (%s)", exc)
        else:
            _logger.warning("toy_vision: call failed: %s", exc, exc_info=True)
        return (None, kind)

    parsed = _parse_response(response.text)
    if parsed is None:
        return (None, "malformed")
    return parsed


__all__ = [
    "DEFAULT_VISION_TIMEOUT_SEC",
    "ToyVisionSuggestion",
    "VISION_TIMEOUT_ENV",
    "suggest_fields",
    "vision_timeout_sec",
]
