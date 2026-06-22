"""Claude-authored SVG action sprites (the "Claude Images" flag).

When ``image_gen_mode == "claude_svg"`` (the parent "Claude Images"
mode), the image-gen worker generates each action sprite as a cartoon
**SVG** drawn by Claude from the toy's reference photo — instead of the
local Stable-Diffusion PNG pipeline (the three modes are mutually
exclusive). Claude's API emits text/code only (no
raster output), so an SVG vector cartoon is what a "Claude image" can
be; the ``idle`` slot additionally gets a self-contained looping CSS
animation, which is the "Claude animation" half of the feature.

The call reuses the existing OAuth-direct client
(:class:`toybox.ai.client.AnthropicClient`) — no API key, no SDK — via
:meth:`describe_image` with a capable model override (see
:func:`toybox.ai.client.svg_model`). The returned SVG is **sanitised**
(scripts / event handlers / foreignObject / ``javascript:`` URIs
stripped) as defence in depth even though the kiosk renders it through a
passive ``<img>`` (where script execution is already disabled).

This module is the SVG analogue of :mod:`toybox.image_gen.pipeline`
(local SD) and :mod:`toybox.image_gen.composite` (Tier C). The worker
dispatches to it before the SD branch when the flag is on; see
:meth:`toybox.image_gen.worker.ImageGenWorker._run_one_svg`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import urllib.error
from typing import Final

from ..ai.client import AIClient, AIResponse, AnthropicClient, svg_model
from ..ai.oauth import load_token
from .models import ACTION_PROMPTS, GenerationContext

_logger = logging.getLogger(__name__)

_SVG_TIMEOUT_ENV: Final[str] = "TOYBOX_CLAUDE_SVG_TIMEOUT_SEC"
_DEFAULT_SVG_TIMEOUT_SEC: Final[float] = 90.0
# Generous-but-bounded output ceiling. A compact animated SVG is a few KB
# (~1-3k tokens); 8k leaves headroom without inviting a sprawling,
# slow-to-stream document.
_SVG_MAX_TOKENS: Final[int] = 8000

# Rate-limit retry. The subscription OAuth token is rate-limited for
# direct /v1/messages use, and a full regenerate fires 10 calls (one per
# slot), so a transient 429 is common. We retry a few times with backoff
# — honouring the server's ``Retry-After`` when present, capped so the
# single-consumer worker doesn't stall the whole queue on one slot. 529
# (overloaded) is retried the same way. After the cap, the slot fails
# cleanly as ``rate_limited`` and the operator can regenerate later.
_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({429, 529})
_SVG_MAX_ATTEMPTS: Final[int] = 3
_SVG_RETRY_BASE_SEC: Final[float] = 2.0
_SVG_RETRY_CAP_SEC: Final[float] = 20.0

# The on-disk reference photo is whatever the parent uploaded — jpeg, png,
# or webp (see storage/images.py upload validation). Default to jpeg when
# the magic bytes are unrecognised; the vision API tolerates a slightly
# wrong media_type for the common formats but we sniff to be correct.
_DEFAULT_MEDIA_TYPE: Final[str] = "image/jpeg"


class ClaudeImagesUnavailable(RuntimeError):
    """Raised when SVG generation can't run because no OAuth token is on disk.

    This is the graceful-degradation signal: the worker marks the slot
    ``failed`` with a clear reason and the kiosk falls back to any
    existing PNG (or hides the sprite). It is NOT a pipeline-health
    failure, so it never trips the SD circuit breaker.
    """


class SvgGenerationError(RuntimeError):
    """Raised when Claude's response can't be parsed into a usable SVG.

    Covers an empty/refused response, a reply with no ``<svg>`` element,
    or a truncated document (no closing tag). The worker marks the slot
    ``failed``; the kiosk falls back as above.
    """


class SvgRateLimitedError(RuntimeError):
    """Raised when Claude keeps returning 429/529 after the retry budget.

    Distinct from a generic failure so the worker can mark the slot with
    a clear ``rate_limited`` reason (operator-readable in the parent grid)
    rather than a raw ``HTTP Error 429`` string. Like the others it never
    trips the SD breaker — it's a Claude-availability signal, not a
    GPU/pipeline-health one.
    """


def svg_timeout_sec() -> float:
    """Return the configured SVG-generation timeout (seconds)."""
    raw = os.environ.get(_SVG_TIMEOUT_ENV)
    if not raw:
        return _DEFAULT_SVG_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_SVG_TIMEOUT_SEC
    return value if value > 0 else _DEFAULT_SVG_TIMEOUT_SEC


_SYSTEM_PROMPT: Final[str] = (
    "You are an expert SVG illustrator for a young children's app. "
    "You output ONE valid, self-contained SVG document and nothing else: "
    "no prose, no explanation, no markdown code fences. The very first "
    "characters of your reply must be '<svg' and the very last must be "
    "'</svg>'. Use only vector shapes (path, circle, ellipse, rect, "
    "polygon, g) and inline <style>. NEVER include <script>, "
    "<foreignObject>, external references, raster <image> elements, or "
    "event-handler attributes."
)


def _pose_text(slot: str) -> str:
    """Return the pose description for a slot (falls back to the slot key)."""
    return ACTION_PROMPTS.get(slot, slot.replace("_", " "))


def build_user_prompt(slot: str, ctx: GenerationContext) -> str:
    """Compose the per-(toy, slot) SVG instruction sent alongside the photo."""
    name = ctx.toy_display_name or "the toy"
    descriptor_bits: list[str] = []
    if ctx.persona_display_name:
        descriptor_bits.append(f'persona "{ctx.persona_display_name}"')
    if ctx.tags:
        descriptor_bits.append("features: " + ", ".join(ctx.tags))
    descriptor = ("; " + "; ".join(descriptor_bits)) if descriptor_bits else ""

    lines = [
        f'Draw a friendly flat cartoon of "{name}"{descriptor} based on the '
        "attached photo — match its dominant colors and the few defining "
        "features (shape, ears, eyes, any accessories) but stylise it as a "
        "simple, bold, kid-friendly cartoon, not a photo-trace.",
        f"Pose: {_pose_text(slot)}.",
        'Canvas: viewBox="0 0 128 128", transparent background (no '
        "background rect), the character centered and filling most of the "
        "frame.",
    ]
    if slot == "idle":
        lines.append(
            "Add a GENTLE looping idle animation using an inline <style> "
            "block with CSS @keyframes (e.g. a slow breathing scale or a "
            "small vertical bob, ~2-3s, animation-iteration-count: "
            "infinite). Keep the motion subtle and non-flashing — no color "
            "strobing, opacity flicker, or fast movement (motion-sensitive "
            "children). Wrap the moving parts in a <g> the animation "
            "targets."
        )
    else:
        lines.append("This is a static pose — no animation, no <style> keyframes.")
    lines.append("Keep the document compact. Output only the SVG.")
    return "\n".join(lines)


def _sniff_media_type(data: bytes) -> str:
    """Best-effort image media-type from magic bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return _DEFAULT_MEDIA_TYPE


def _extract_svg(text: str) -> str | None:
    """Pull the ``<svg>…</svg>`` document out of the model reply.

    Tolerates leading/trailing prose or markdown fences the model may add
    despite the system prompt. Returns ``None`` when no complete SVG
    element is present (empty/refused/truncated reply).
    """
    start = text.find("<svg")
    if start == -1:
        return None
    end = text.rfind("</svg>")
    if end == -1 or end < start:
        return None
    return text[start : end + len("</svg>")]


# Defence-in-depth sanitisation. The kiosk renders the SVG via a passive
# <img>, which already disables scripting, but a stripped document is
# safe even if a future code path inlines it. Regex (not a parser) is
# deliberate: we only need to neutralise known-dangerous constructs, and
# pulling in a full XML/SVG sanitiser dependency is unjustified here.
_SCRIPT_RE: Final[re.Pattern[str]] = re.compile(
    r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL
)
_FOREIGNOBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"<foreignObject\b.*?</foreignObject\s*>", re.IGNORECASE | re.DOTALL
)
# Event-handler attributes: on<name>="..." / '...' / =bareword.
_EVENT_HANDLER_RE: Final[re.Pattern[str]] = re.compile(
    r"\son[a-zA-Z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE
)
# javascript: URIs in href / xlink:href.
_JS_HREF_RE: Final[re.Pattern[str]] = re.compile(
    r"((?:xlink:)?href)\s*=\s*([\"'])\s*javascript:[^\"']*\2", re.IGNORECASE
)


def _sanitize_svg(svg: str) -> str:
    """Strip scripts, foreignObject, event handlers, and js: URIs."""
    svg = _SCRIPT_RE.sub("", svg)
    svg = _FOREIGNOBJECT_RE.sub("", svg)
    svg = _EVENT_HANDLER_RE.sub("", svg)
    svg = _JS_HREF_RE.sub(r'\1="#"', svg)
    return svg


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait before the next attempt after a retryable status.

    Honours the server's ``Retry-After`` (seconds) when present and
    parseable, else exponential backoff. Either way capped at
    :data:`_SVG_RETRY_CAP_SEC` so the single-consumer worker can't be
    stalled for minutes on one slot by a large ``Retry-After``.
    """
    if exc.headers is not None:
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _SVG_RETRY_CAP_SEC)
            except ValueError:
                pass
    return min(_SVG_RETRY_BASE_SEC * (2.0**attempt), _SVG_RETRY_CAP_SEC)


async def _request_svg_with_retry(
    client: AIClient,
    reference_bytes: bytes,
    *,
    prompt: str,
    media_type: str,
    timeout: float,
) -> AIResponse:
    """Call the vision API, retrying 429/529 with capped backoff.

    The per-attempt :func:`asyncio.wait_for` bounds each call by
    ``timeout``; the backoff sleeps are added between attempts. A
    non-retryable :class:`urllib.error.HTTPError` (auth, 4xx, 5xx other
    than 529) propagates immediately. After the attempt budget is spent
    on retryable statuses, raises :class:`SvgRateLimitedError`.
    """
    last_exc: urllib.error.HTTPError | None = None
    for attempt in range(_SVG_MAX_ATTEMPTS):
        try:
            return await asyncio.wait_for(
                client.describe_image(
                    reference_bytes,
                    prompt=prompt,
                    media_type=media_type,
                    max_tokens=_SVG_MAX_TOKENS,
                    model=svg_model(),
                    system=_SYSTEM_PROMPT,
                ),
                timeout=timeout,
            )
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRYABLE_STATUS:
                raise
            last_exc = exc
            if attempt + 1 >= _SVG_MAX_ATTEMPTS:
                break
            delay = _retry_delay(exc, attempt)
            _logger.warning(
                "svg_gen: Claude HTTP %s (attempt %d/%d); retrying in %.1fs",
                exc.code,
                attempt + 1,
                _SVG_MAX_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
    # Only reachable via the break after exhausting retries on a
    # retryable status, so ``last_exc`` is always set here.
    assert last_exc is not None
    raise SvgRateLimitedError(
        f"Claude rate limit (HTTP {last_exc.code}) after {_SVG_MAX_ATTEMPTS} attempts"
    ) from last_exc


async def generate_action_svg(
    reference_bytes: bytes,
    slot: str,
    ctx: GenerationContext,
    *,
    client: AIClient | None = None,
    timeout_sec: float | None = None,
) -> str:
    """Generate one sanitised SVG action sprite for ``(toy, slot)``.

    Positional signature ``(bytes, str, GenerationContext) -> str``
    matches the worker's ``svg_generator`` override slot, so tests inject
    a stub and production passes nothing (the worker resolves this
    function directly).

    ``client`` is the seam for unit tests (pass a ``StubClient``); when
    ``None`` an :class:`~toybox.ai.client.AnthropicClient` is built from
    the on-disk OAuth token, raising :class:`ClaudeImagesUnavailable`
    when no token is present.

    Raises:
        ClaudeImagesUnavailable: no OAuth token on disk.
        SvgRateLimitedError: Claude returned 429/529 past the retry budget.
        SvgGenerationError: the reply had no usable ``<svg>`` document.
        TimeoutError: a single attempt exceeded ``timeout_sec``.
    """
    if client is None:
        token = load_token()
        if token is None:
            raise ClaudeImagesUnavailable("no Claude OAuth token on disk")
        client = AnthropicClient(token)

    timeout = timeout_sec if timeout_sec is not None else svg_timeout_sec()
    response = await _request_svg_with_retry(
        client,
        reference_bytes,
        prompt=build_user_prompt(slot, ctx),
        media_type=_sniff_media_type(reference_bytes),
        timeout=timeout,
    )

    svg = _extract_svg(response.text)
    if svg is None:
        raise SvgGenerationError(
            f"no <svg> element in model reply for slot={slot!r} ({len(response.text)} chars)"
        )
    return _sanitize_svg(svg)


__all__ = [
    "ClaudeImagesUnavailable",
    "SvgGenerationError",
    "SvgRateLimitedError",
    "build_user_prompt",
    "generate_action_svg",
    "svg_timeout_sec",
]
