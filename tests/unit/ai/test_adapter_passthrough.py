"""Passthrough test for :class:`ClaudeActivityGenerator`.

Proves the wrapper's single-shot path is byte-equivalent to a direct
``client.complete_text`` call followed by JSON parsing — no extra
post-processing, no token munging, no breaker side effects.

The contract: for the same ``(system_prompt, user_prompt, max_tokens)``,
``ClaudeActivityGenerator.generate_activity(ctx)`` and a direct
``client.py``-driven completion must produce structurally equal
:class:`Activity` objects.
"""

from __future__ import annotations

import json

import pytest

from toybox.activities.models import Activity
from toybox.ai.adapters import ClaudeActivityGenerator
from toybox.ai.adapters.claude import ClaudeAdapterContext
from toybox.ai.client import AIMessage, StubClient


def _sample_activity_json() -> str:
    """A canonical Activity JSON shape the model would emit."""
    payload = {
        "id": "11111111-2222-4333-8444-555555555555",
        "template_id": "play_anytime_invent",
        "persona_id": "wizard",
        "title": "A bright story",
        "version": 1,
        "metadata": {"slot_values": [], "hour_bucket": "midday"},
        "steps": [
            {"step_index": 0, "text": "Start by stretching."},
            {"step_index": 1, "text": "Say one wish out loud."},
            {"step_index": 2, "text": "Hop three times."},
            {"step_index": 3, "text": "Hum a quiet tune."},
            {"step_index": 4, "text": "Take a slow breath."},
        ],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


@pytest.fixture
def adapter_ctx() -> ClaudeAdapterContext:
    return ClaudeAdapterContext(
        system_prompt="System: be precise.",
        user_prompt='{"intent":"boredom"}',
        max_tokens=1024,
    )


async def test_generate_activity_matches_direct_client_call(
    adapter_ctx: ClaudeAdapterContext,
) -> None:
    """Wrapper and the production parser produce structurally equal Activities.

    M4: the "direct path" uses the actual production v1 single-shot
    parser exposed as :func:`toybox.ai.adapters.claude.parse_activity_from_text`,
    rather than a hand-written ``Activity.model_validate_json(text)``
    rewrite. Both the wrapper and a direct call must parse through
    the same seam — if a future refactor changes how the parser
    handles fences (for example) the wrapper's output should still
    equal the production parser's output for the same text.
    """
    from toybox.ai.adapters.claude import parse_activity_from_text

    text = _sample_activity_json()

    # Adapter path — drives ``client.complete_text`` + parses internally.
    wrapper_client = StubClient(responses=[text])
    adapter = ClaudeActivityGenerator(wrapper_client)
    via_wrapper: Activity = await adapter.generate_activity(adapter_ctx)

    # Direct path: same client surface, parse via the SAME production
    # function the adapter uses. This is the load-bearing assertion —
    # if the adapter and the production parser ever diverge, this fails.
    direct_client = StubClient(responses=[text])
    direct_resp = await direct_client.complete_text(
        [AIMessage(role="user", content=adapter_ctx.user_prompt)],
        system=adapter_ctx.system_prompt,
        max_tokens=adapter_ctx.max_tokens,
    )
    direct_activity: Activity = parse_activity_from_text(direct_resp.text)

    assert via_wrapper == direct_activity


async def test_passthrough_preserves_calling_kwargs(
    adapter_ctx: ClaudeAdapterContext,
) -> None:
    """Adapter forwards system, max_tokens, and user content unchanged."""
    text = _sample_activity_json()
    client = StubClient(responses=[text])
    adapter = ClaudeActivityGenerator(client)
    await adapter.generate_activity(adapter_ctx)

    assert len(client.calls) == 1
    name, msgs, kwargs = client.calls[0]
    assert name == "complete_text"
    assert kwargs["system"] == adapter_ctx.system_prompt
    assert kwargs["max_tokens"] == adapter_ctx.max_tokens
    # The single message is the user prompt verbatim.
    assert msgs[0].role == "user"
    assert msgs[0].content == adapter_ctx.user_prompt


async def test_passthrough_tolerates_code_fences(
    adapter_ctx: ClaudeAdapterContext,
) -> None:
    """The adapter strips ```json fences just like the judge parser."""
    text = "```json\n" + _sample_activity_json() + "\n```"
    client = StubClient(responses=[text])
    adapter = ClaudeActivityGenerator(client)
    activity = await adapter.generate_activity(adapter_ctx)
    assert activity.template_id == "play_anytime_invent"


def test_extract_tool_calls_drops_entry_missing_args(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H4: ``_extract_tool_calls`` requires a dict ``args`` key per entry.

    Without this gate, an entry like ``{"name": "get_recent_transcript"}``
    would dispatch with ``args={}`` and — because the resolver's
    args have defaults — succeed at returning the recent transcript.
    That's a model-controllable surface to trigger a transcript dump
    with zero validation. The fix drops the malformed entry + logs.
    """
    import logging as _logging

    from toybox.ai.adapters.claude import _extract_tool_calls

    payload = json.dumps(
        {
            "tool_calls": [
                {"name": "get_recent_transcript"},  # MISSING args
                {"name": "get_room", "args": {"room_id": "abc"}},  # well-formed
            ]
        }
    )
    caplog.set_level(_logging.WARNING, logger="toybox.ai.adapters.claude")
    result = _extract_tool_calls(payload)
    # The malformed entry is dropped; only the well-formed one survives.
    assert len(result) == 1
    assert result[0]["name"] == "get_room"
    # A WARNING log line was emitted for the dropped entry.
    drop_records = [
        r
        for r in caplog.records
        if r.levelno == _logging.WARNING and "missing dict 'args'" in r.getMessage()
    ]
    assert len(drop_records) == 1


def test_extract_tool_calls_drops_entry_with_non_dict_args() -> None:
    """``args`` present but not a dict (e.g. a string) is also rejected."""
    from toybox.ai.adapters.claude import _extract_tool_calls

    payload = json.dumps(
        {
            "tool_calls": [
                {"name": "get_room", "args": "not a dict"},
                {"name": "get_room", "args": ["also", "not"]},
            ]
        }
    )
    result = _extract_tool_calls(payload)
    assert result == []


def test_extract_tool_calls_drops_entry_with_missing_name() -> None:
    """``name`` missing, empty, or non-str → entry dropped.

    Iter-1 only guarded ``"name" in entry``; iter-2 H4 strengthened this
    to require ``isinstance(name, str) and name`` so empty-string and
    non-str names are also rejected. This test pins both branches.
    """
    from toybox.ai.adapters.claude import _extract_tool_calls

    payload = json.dumps(
        {
            "tool_calls": [
                {"args": {"x": 1}},  # no name
                {"name": "", "args": {}},  # empty name
                {"name": 42, "args": {}},  # non-str name
            ]
        }
    )
    result = _extract_tool_calls(payload)
    assert result == []
