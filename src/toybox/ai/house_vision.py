"""Claude vision call: identify a room (and its features) from a photo.

Step 17 calls this from the room-bulk-upload router after the bytes
have been validated, hashed, and staged. The flow mirrors
:mod:`toybox.ai.toy_vision`:

1. Caller passes downscaled bytes (≤1600 long edge — see
   :func:`toybox.storage.images.downscale_for_vision`).
2. We send them as a base64 image to Claude Haiku
   (``TOYBOX_CLAUDE_VISION_MODEL``) with a user prompt asking for
   strict JSON: ``{suggested_room_label, features: [{name}, ...]}``.
3. Parse with Pydantic. Strip a Markdown ```json fence if Claude
   wraps the body. On parse failure log WARNING and return
   ``(None, "malformed")``.
4. Timeouts → ``(None, "timeout")``; SDK 429 → ``(None, "rate_limited")``;
   anything else → ``(None, "error")``.

The room-bulk router invokes ``suggest_room`` once per photo, bounded
by ``asyncio.Semaphore(TOYBOX_VISION_CONCURRENCY)`` so 50 concurrent
vision calls don't auto-trigger 429s. Each per-photo failure is
recorded against the photo and the parent assigns manually in the
``Unassigned`` tab.

PRIVACY NOTE: Room photos are part of the household-context capture
the plan §"Privacy guarantees" allows at ingest under the parent-
acknowledged consent on the upload form. After commit the photo lives
only on the device.
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

# Inlined system prompt — see ``toy_vision._PROMPT`` for the rationale
# (the AIClient Protocol's ``describe_image`` has no separate ``system``
# arg).
_PROMPT: Final[str] = (
    "You are an assistant for a household catalog used to inspire "
    "child play. Identify the room in the photo and list 3-8 notable "
    "physical features a child might play with or near. Return STRICT "
    "JSON with these exact keys:\n"
    '  {"suggested_room_label": "<short room name, 1-40 chars>",\n'
    '   "features": [{"name": "<feature, 1-40 chars>"}, ...]}\n'
    "Examples of feature names: 'reading nook', 'toy bin', 'rug', "
    "'bookshelf', 'window seat'. Keep names short (1-3 words) and "
    "lowercase. If you can't identify the room confidently, return "
    "suggested_room_label 'unknown room' with an empty features list. "
    "NEVER include any commentary outside the JSON object."
)

# Strip a ```json ...``` (or plain ```...```) fence if Claude wraps the
# body. Anchored at the start of the trimmed string so we don't match
# stray fences elsewhere.
_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"\A```(?:json)?\s*(.*?)\s*```\Z",
    re.DOTALL | re.IGNORECASE,
)


class FeatureSuggestion(BaseModel):
    """A single feature name suggested by the vision model.

    A wrapping object (rather than bare ``str``) is used because the
    parent UI extends each feature with chip state, and the eventual
    ``room_features`` row has ``name`` plus optional ``tags`` we may
    populate in v1.5. Keeping the wire shape an object up front avoids
    a breaking schema change.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=40)


class HouseVisionSuggestion(BaseModel):
    """Parsed vision response for a room photo."""

    model_config = ConfigDict(frozen=True)

    suggested_room_label: str = Field(min_length=1, max_length=40)
    features: list[FeatureSuggestion] = Field(default_factory=list, max_length=20)


def vision_timeout_sec() -> float:
    """Return the configured per-call timeout (env-overrideable).

    Identical contract to :func:`toybox.ai.toy_vision.vision_timeout_sec`.
    Both call sites read the same env var so an operator tuning the
    timeout once affects every Claude vision call uniformly.
    """
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
    ``Exception("...rate limit...")`` raised by stubs. Mirrors
    :func:`toybox.ai.toy_vision._classify_exception`.
    """
    cls_name = type(exc).__name__
    cls_name_l = cls_name.lower()

    if "ratelimit" in cls_name_l:
        return "rate_limited"
    if isinstance(exc, TimeoutError) or "timeout" in cls_name_l:
        return "timeout"

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


def _normalise_features(raw: object) -> list[dict[str, str]]:
    """Coerce assorted feature shapes into a list of ``{"name": str}``.

    Real Claude responses occasionally return a list of bare strings
    or include an empty / null entry. Normalising here lets the
    downstream Pydantic validation focus on length bounds.
    """
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, str):
            name = entry.strip().lower()
        elif isinstance(entry, dict):
            candidate = entry.get("name")
            if not isinstance(candidate, str):
                continue
            name = candidate.strip().lower()
        else:
            continue
        if not name or name in seen:
            continue
        # Bound the length here too so the Pydantic max_length=40 doesn't
        # blow the whole response away — we drop overlong names instead.
        if len(name) > 40:
            continue
        seen.add(name)
        out.append({"name": name})
    return out


def _parse_response(text: str) -> HouseVisionSuggestion | None:
    """Try to parse Claude's reply into a :class:`HouseVisionSuggestion`."""
    body = _strip_fence(text)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        _logger.warning("house_vision: response is not valid JSON (%s): %r", exc, text[:200])
        return None
    if not isinstance(payload, dict):
        _logger.warning("house_vision: response JSON is not an object: %r", text[:200])
        return None

    # ``suggested_room_label`` is mandatory — without it we can't tab the
    # photo, so we treat the whole response as malformed.
    raw_label = payload.get("suggested_room_label")
    if not isinstance(raw_label, str) or not raw_label.strip():
        _logger.warning("house_vision: missing or empty suggested_room_label: %r", text[:200])
        return None
    payload["suggested_room_label"] = raw_label.strip()
    payload["features"] = _normalise_features(payload.get("features", []))
    try:
        return HouseVisionSuggestion(**payload)
    except ValidationError as exc:
        _logger.warning("house_vision: response failed schema validation: %s", exc)
        return None


async def suggest_room(
    client: AIClient,
    image_bytes: bytes,
    *,
    timeout_sec: float | None = None,
) -> HouseVisionSuggestion | tuple[None, str]:
    """Ask Claude to identify the room + features in ``image_bytes``.

    Returns:
        On success: the parsed :class:`HouseVisionSuggestion`.
        On any failure: ``(None, reason)`` with the same reason set as
        :func:`toybox.ai.toy_vision.suggest_fields`:

        * ``"timeout"``      — the per-call timeout fired
        * ``"rate_limited"`` — Claude returned 429 / rate-limit
        * ``"malformed"``    — response wasn't parseable JSON / schema
        * ``"error"``        — anything else (logged with traceback)

    The room-bulk router renders the failure reason as
    ``vision_error`` on the per-photo response envelope so the parent
    can manually assign the photo from the ``Unassigned`` tab.
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
        _logger.warning("house_vision: call timed out after %.1fs", timeout)
        return (None, "timeout")
    except Exception as exc:  # noqa: BLE001 -- we deliberately blanket-catch
        kind = _classify_exception(exc)
        if kind == "rate_limited":
            _logger.info("house_vision: rate-limited by Claude (%s)", exc)
        elif kind == "timeout":
            _logger.warning("house_vision: call timed out (%s)", exc)
        else:
            _logger.warning("house_vision: call failed: %s", exc, exc_info=True)
        return (None, kind)

    parsed = _parse_response(response.text)
    if parsed is None:
        return (None, "malformed")
    return parsed


__all__ = [
    "DEFAULT_VISION_TIMEOUT_SEC",
    "FeatureSuggestion",
    "HouseVisionSuggestion",
    "VISION_TIMEOUT_ENV",
    "suggest_room",
    "vision_timeout_sec",
]
