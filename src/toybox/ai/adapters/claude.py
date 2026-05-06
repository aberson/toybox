"""Claude implementation of :class:`ActivityGeneratorAdapter`.

Single-shot path is a thin no-op wrapper that delegates to
:mod:`toybox.ai.client` and parses the JSON Activity envelope. The
loop-mode path dispatches tool calls via the injected
:class:`ToolDispatcher` (defined in :mod:`toybox.ai.tools`) and feeds
results back until the model emits the final Activity.

Tool-loop telemetry per call is captured into a list on the adapter
instance so the call site can persist it onto
``labeled_events.tool_calls`` after the activity is returned. We don't
write the row from in here — :mod:`toybox.ai.labeled_events` owns the
recorder and the dispatch site has the connection.

Tool-use protocol — current vs. native API
------------------------------------------

Loop-mode currently parses a ``{"tool_calls": [...]}`` JSON envelope
from the model's free-form text content (see :func:`_extract_tool_calls`
+ the catalog wedge in :func:`_augment_system_with_tools`). This is a
deliberately stub-friendly text protocol that drives :class:`StubClient`
cleanly for unit + integration tests — both sides of the wire speak
plain JSON, no special branch in the test stub.

The production-grade path is the Anthropic messages API's native
tool-use surface: pass ``tools=[...]`` to the messages API and read
structured ``content[i].type == "tool_use"`` blocks back. Real Claude
will not reliably emit the bespoke ``{"tool_calls": [...]}`` envelope
without coercion, so the current path is correct for the StubClient
but is best treated as a stop-gap. Migration to the native tool_use
API is deferred to Step 26 (E2) when the local-runtime carve-out
lands and we can mock both protocol shapes identically.

# TODO(step-26): migrate to native Anthropic tool_use API
#   - Pass ``tools=[...]`` to ``client.complete_text``/messages API
#   - Read structured ``content[i].type == "tool_use"`` blocks
#   - Drop the ``_extract_tool_calls`` text-envelope parser below
#   - Drop the catalog wedge in ``_augment_system_with_tools``
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Final

from ...activities.models import Activity
from ..client import AIClient, AIMessage
from ..tools import ToolDispatcher, registered_tools, telemetry_entry, tool_loop_timeout_sec

_logger = logging.getLogger(__name__)

DEFAULT_LOOP_MAX_TURNS: Final[int] = 8
DEFAULT_GENERATION_MAX_TOKENS: Final[int] = 2048

_TOOL_DESCRIPTIONS: Final[dict[str, str]] = {
    "get_persona": (
        "Look up a persona's display name, system prompt, behavior tags, and voice tone."
    ),
    "get_room": "Look up a room's name, features, and image_path by UUID.",
    "get_inventory": "List the recent toys for a child (UUID), ordered by recency.",
    "get_recent_transcript": ("Return recent child transcript snippets (last window_sec seconds)."),
    "get_prior_steps": "Return step bodies already emitted on the activity (UUID).",
    "get_anti_signal": (
        "Look up parent anti-signal feedback for a (template_id, slot_dict) signature."
    ),
}


@dataclass
class ClaudeAdapterContext:
    """Adapter-side context for a single generation.

    Carries the inputs needed to call Claude (messages, system prompt,
    max_tokens) plus references the dispatch site needs to bridge the
    tool-loop's results back to the labeled_events row.

    The shape is intentionally permissive — adapters in future steps
    can subclass / extend without breaking the Protocol contract.
    """

    system_prompt: str
    user_prompt: str
    max_tokens: int = DEFAULT_GENERATION_MAX_TOKENS
    max_loop_turns: int = DEFAULT_LOOP_MAX_TURNS


@dataclass
class ClaudeGenerationResult:
    """Wrapper for adapters that need both the activity and the tool log."""

    activity: Activity
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ClaudeActivityGenerator:
    """Thin Claude wrapper for the activity-generation surface.

    The single-shot path is a passthrough — :meth:`generate_activity`
    runs exactly one ``client.complete_text`` call, parses the JSON,
    and constructs an :class:`Activity`. The output for a fixed
    ``(system_prompt, user_prompt, max_tokens)`` is byte-identical to
    a direct call against the same client (the passthrough test pins
    this).

    The loop-mode path drives the messages API with ``tools=[...]``
    enabled, dispatches tool calls via the injected
    :class:`ToolDispatcher`, captures telemetry, and exits once the
    model emits a final assistant turn that parses as an Activity (or
    ``max_loop_turns`` is exhausted).
    """

    def __init__(self, client: AIClient) -> None:
        self._client = client
        self._tool_calls: list[dict[str, Any]] = []

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        """Tool-call telemetry recorded since the last loop run."""
        return list(self._tool_calls)

    # ------------------------------------------------------------------ single

    async def generate_activity(self, ctx: object) -> Activity:
        """Single-shot generation; passthrough to ``client.complete_text``."""
        adapter_ctx = self._coerce_ctx(ctx)
        response = await self._client.complete_text(
            [AIMessage(role="user", content=adapter_ctx.user_prompt)],
            system=adapter_ctx.system_prompt,
            max_tokens=adapter_ctx.max_tokens,
        )
        return _parse_activity(response.text)

    # ------------------------------------------------------------------ loop

    async def generate_activity_loop(self, ctx: object, tools: ToolDispatcher) -> Activity:
        """Tool-loop generation. Dispatches tool calls via ``tools``.

        The loop runs at most ``ctx.max_loop_turns`` times. On each
        turn it:

        1. Calls Claude with the conversation built so far.
        2. Tries to parse the response as a final Activity JSON. If it
           parses, the loop exits with that Activity.
        3. Otherwise, attempts to extract one or more tool-call
           directives from the response (see :func:`_extract_tool_calls`),
           dispatches them via ``tools.call_tool``, and appends the
           tool results to the conversation as a synthetic user turn.

        The whole loop is capped at ``TOYBOX_TOOL_LOOP_TIMEOUT_SEC`` —
        a model that ping-pongs forever returns a timeout error rather
        than hanging the kid-facing path.
        """
        self._tool_calls = []
        adapter_ctx = self._coerce_ctx(ctx)
        loop_timeout = tool_loop_timeout_sec()

        messages: list[AIMessage] = [
            AIMessage(role="user", content=adapter_ctx.user_prompt),
        ]
        system_with_tools = _augment_system_with_tools(adapter_ctx.system_prompt)

        async with asyncio.timeout(loop_timeout):
            for _turn in range(adapter_ctx.max_loop_turns):
                response = await self._client.complete_text(
                    messages,
                    system=system_with_tools,
                    max_tokens=adapter_ctx.max_tokens,
                )
                tool_calls = _extract_tool_calls(response.text)
                if not tool_calls:
                    return _parse_activity(response.text)

                messages.append(AIMessage(role="assistant", content=response.text))
                tool_results: list[dict[str, Any]] = []
                for tc in tool_calls:
                    name = str(tc.get("name", ""))
                    raw_args = tc.get("args")
                    args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, dict) else {}
                    result = await tools.call_tool(name, args)
                    self._tool_calls.append(telemetry_entry(result))
                    tool_results.append(
                        {
                            "tool": result["tool"],
                            "data": result["data"],
                            "error": result["error"],
                            "reason": result.get("reason"),
                        }
                    )
                messages.append(
                    AIMessage(
                        role="user",
                        content=json.dumps(
                            {"tool_results": tool_results},
                            sort_keys=True,
                            ensure_ascii=False,
                        ),
                    )
                )
        raise RuntimeError(f"loop did not converge within {adapter_ctx.max_loop_turns} turns")

    # ------------------------------------------------------------------ helpers

    def _coerce_ctx(self, ctx: object) -> ClaudeAdapterContext:
        if isinstance(ctx, ClaudeAdapterContext):
            return ctx
        raise TypeError(
            f"ClaudeActivityGenerator expects ClaudeAdapterContext; got {type(ctx).__name__}"
        )


def _augment_system_with_tools(base_system: str) -> str:
    """Append a one-line tool catalog to the system prompt.

    The Anthropic SDK's ``tools=[...]`` parameter is the production
    surface, but :class:`StubClient` (and tests) drive a text-only
    Protocol. We mirror the catalog inline so a stub can simulate
    tool-use turns without a special path.
    """
    catalog_lines = ["Tools available:"]
    for name in registered_tools():
        desc = _TOOL_DESCRIPTIONS.get(name, "")
        catalog_lines.append(f"- {name}: {desc}")
    catalog_lines.append(
        "To call a tool, reply with EXACTLY: "
        '{"tool_calls": [{"name": "<tool>", "args": {...}}]} '
        "and nothing else. To finish, reply with the final Activity JSON."
    )
    return base_system + "\n\n" + "\n".join(catalog_lines)


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


def _extract_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse a possible tool-use turn from the model's output.

    Returns an empty list when the model's output is the final
    Activity JSON (or any non-tool-use shape). Tolerates a leading
    ```json fence — the system prompt forbids them but Claude
    occasionally adds them anyway.

    H4: Each entry must carry both ``name`` (str) AND ``args`` (dict).
    Without this gate, an entry with no ``args`` key would dispatch
    with ``args={}`` — and any tool whose every arg has a default
    (e.g. ``get_recent_transcript.window_sec``) would then be
    invokable with zero model intent, a model-controllable surface to
    trigger transcript dumps with no validation. Malformed entries
    are dropped + logged rather than coerced.
    """
    # TODO(step-26): migrate to native Anthropic tool_use API.
    # Today this parses a free-text JSON envelope from the model's
    # text content; Step 26 (E2) replaces it with structured
    # ``content[i].type == "tool_use"`` blocks read from the messages
    # API directly (with ``tools=[...]`` passed in).
    candidate = _strip_code_fences(text)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    raw = payload.get("tool_calls")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            _logger.warning("dropping non-dict tool-call entry: %r", entry)
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            _logger.warning("dropping tool-call entry with missing/empty 'name': %r", entry)
            continue
        if "args" not in entry or not isinstance(entry["args"], dict):
            _logger.warning(
                "dropping tool-call entry missing dict 'args' (tool=%s): %r",
                name,
                entry,
            )
            continue
        out.append(entry)
    return out


def parse_activity_from_text(text: str) -> Activity:
    """Parse the model's final reply into an :class:`Activity`.

    Tolerant of a leading ```json fence (mirrors the judge parser's
    fence-stripping behavior). Pydantic's ``model_validate_json`` does
    the schema-shape enforcement; downstream code can rely on the 5-step
    invariant.

    This is the load-bearing seam for both the single-shot and
    loop-mode paths — both call into here for their final JSON →
    Activity parse. Tests that want to compare adapter output to "the
    production parser" should call this function directly rather than
    rebuilding the parse inline.
    """
    candidate = _strip_code_fences(text)
    return Activity.model_validate_json(candidate)


# Backwards-compatible private alias retained for in-module callers
# (kept private-looking with the underscore so external imports
# clearly target ``parse_activity_from_text``).
_parse_activity = parse_activity_from_text


__all__ = [
    "ClaudeActivityGenerator",
    "ClaudeAdapterContext",
    "ClaudeGenerationResult",
    "DEFAULT_GENERATION_MAX_TOKENS",
    "DEFAULT_LOOP_MAX_TURNS",
    "parse_activity_from_text",
]
