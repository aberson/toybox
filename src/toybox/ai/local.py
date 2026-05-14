"""Local-runtime adapter for the Phase E loop-mode dispatch.

This module ships the code-only seams of the locally-hosted inference
path (Ollama / LM Studio / llama.cpp) without any generation logic --
the actual ``/v1/chat/completions`` plumbing lands in Step 26 (issue
#38). For now the adapter conforms to
:class:`toybox.ai.adapters.ActivityGeneratorAdapter` and raises
:class:`NotImplementedError` from both entry points so an operator
who opts in via ``TOYBOX_GENERATOR_ADAPTER=local`` sees a clear
pointer to the next-step issue.

The constructor stores config (runtime URL, model id) but does NOT
instantiate an HTTP client -- keeping the import cheap means the v1
``claude+single`` path's import surface is unchanged for operators
who never flip the env var.
"""

from __future__ import annotations

import logging
import os
from typing import Final

from ..activities.models import Activity
from .tools import ToolDispatcher

_logger = logging.getLogger(__name__)

#: Env var the operator sets to point at a running local OpenAI-compatible
#: runtime. Default matches Ollama's stock listener per
#: ``documentation/plan/phase-e.md``.
LOCAL_RUNTIME_URL_ENV: Final[str] = "TOYBOX_LOCAL_RUNTIME_URL"
DEFAULT_LOCAL_RUNTIME_URL: Final[str] = "http://localhost:11434"

#: Env var the operator sets to pin a specific model id (e.g.
#: ``qwen2.5:7b``). When set, :func:`toybox.ai.capability.is_local_capable`
#: asserts that id is present in ``/v1/models``; when unset, the probe
#: only confirms the response is well-formed.
LOCAL_MODEL_ID_ENV: Final[str] = "TOYBOX_LOCAL_MODEL_ID"

#: Step-26 / issue-#38 pointer baked into the NotImplementedError so a
#: future operator (or LLM reading the traceback) sees the right next
#: step. Centralised so tests can pin the substring without
#: copy-pasting prose.
STEP_26_HINT: Final[str] = (
    "local adapter generation logic ships in Step 26 (issue #38); "
    "the current build only wires the capability probe + breaker seams"
)


class LocalActivityGenerator:
    """Local-runtime implementation of :class:`ActivityGeneratorAdapter`.

    Both ``generate_activity`` and ``generate_activity_loop`` raise
    :class:`NotImplementedError` with a message that cites Step 26 /
    issue #38 explicitly. The constructor accepts the runtime URL and
    model id strings so a Step-26 follow-up can drop the real HTTP
    client in without changing the call-site contract -- the
    capability probe and the breaker are already wired against this
    shape.

    No HTTP client is instantiated at construction time: this keeps the
    module cheap to import even when the local path isn't active.
    """

    def __init__(
        self,
        *,
        runtime_url: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self._runtime_url = (
            runtime_url
            if runtime_url is not None
            else os.environ.get(LOCAL_RUNTIME_URL_ENV, DEFAULT_LOCAL_RUNTIME_URL)
        )
        self._model_id = model_id if model_id is not None else os.environ.get(LOCAL_MODEL_ID_ENV)

    @property
    def runtime_url(self) -> str:
        """Configured runtime URL (env or constructor override)."""
        return self._runtime_url

    @property
    def model_id(self) -> str | None:
        """Configured model id, or ``None`` if the operator left it unset."""
        return self._model_id

    async def generate_activity(self, ctx: object) -> Activity:
        """Single-shot generation -- not yet implemented."""
        raise NotImplementedError(STEP_26_HINT)

    async def generate_activity_loop(self, ctx: object, tools: ToolDispatcher) -> Activity:
        """Tool-loop generation -- not yet implemented."""
        raise NotImplementedError(STEP_26_HINT)


__all__ = [
    "DEFAULT_LOCAL_RUNTIME_URL",
    "LOCAL_MODEL_ID_ENV",
    "LOCAL_RUNTIME_URL_ENV",
    "LocalActivityGenerator",
    "STEP_26_HINT",
]
