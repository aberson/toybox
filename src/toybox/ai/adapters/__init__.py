"""Adapter package for the Phase E loop-mode generator dispatch.

Two concrete adapters live (or will live) under this package:

* :class:`ClaudeActivityGenerator` (this carve-out) — thin wrapper
  around the existing single-shot path in :mod:`toybox.ai.client` plus
  a tool-loop entry point that talks to the Anthropic messages API
  with ``tools=[...]`` enabled.
* :class:`LocalActivityGenerator` (Step 28 partial) — ships the
  Protocol-conformant scaffolding; both entry points raise
  ``NotImplementedError`` pointing at Step 26 / issue #38 where the
  real ``/v1/chat/completions`` generation logic lands.

Both implement :class:`ActivityGeneratorAdapter`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...activities.models import Activity
from ..local import LocalActivityGenerator
from ..tools import ToolDispatcher
from .claude import ClaudeActivityGenerator


@runtime_checkable
class ActivityGeneratorAdapter(Protocol):
    """Two-method contract every adapter implements.

    Single-shot ``generate_activity`` retains the v1 behavior for
    ``TOYBOX_GENERATOR_MODE=single`` (the default). Tool-loop
    ``generate_activity_loop`` powers ``TOYBOX_GENERATOR_MODE=loop``.
    """

    async def generate_activity(self, ctx: object) -> Activity:
        """Single-shot generation; v1 behavior."""
        ...

    async def generate_activity_loop(self, ctx: object, tools: ToolDispatcher) -> Activity:
        """Tool-loop generation; loop dispatches via ``tools.call_tool``."""
        ...


__all__ = [
    "ActivityGeneratorAdapter",
    "ClaudeActivityGenerator",
    "LocalActivityGenerator",
]
