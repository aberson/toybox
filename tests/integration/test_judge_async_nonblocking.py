"""Integration test: the judge sample MUST NOT block the kid-facing path.

Calls schedule_judge_sample with a slow stub judge and verifies the
caller returns immediately (well before the judge resolves).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from toybox.activities.generator import generate
from toybox.ai.labeled_events import (
    GeneratorContext,
    schedule_judge_sample,
)


@pytest.mark.asyncio
async def test_schedule_judge_sample_returns_before_judge_resolves() -> None:
    """The schedule call returns a Task immediately; the judge sleeps."""
    activity = generate(
        intent="boredom", slot=None, context={"id": "j1"}, hour=10, seed=1
    )
    ctx = GeneratorContext(intent="boredom")
    judge_started = asyncio.Event()
    judge_finished = asyncio.Event()

    async def slow_judge(*, activity: Any, ctx: Any, row_id: int) -> None:
        judge_started.set()
        await asyncio.sleep(0.5)
        judge_finished.set()

    t0 = time.perf_counter()
    task = schedule_judge_sample(
        row_id=5,  # in-sample at default rate=5
        activity=activity,
        ctx=ctx,
        judge_call=slow_judge,
        rate=5,
    )
    elapsed = time.perf_counter() - t0
    assert task is not None
    # The schedule call returned virtually instantly — well under the
    # 0.5s judge cost. Latency budget: schedule_judge_sample must be
    # microseconds, not milliseconds.
    assert elapsed < 0.05, f"schedule call took {elapsed:.3f}s — should be near-zero"
    assert not judge_finished.is_set()
    # Drive the loop and confirm the judge actually runs
    await asyncio.wait_for(task, timeout=2.0)
    assert judge_finished.is_set()


@pytest.mark.asyncio
async def test_schedule_judge_sample_skipped_when_out_of_sample() -> None:
    activity = generate(
        intent="boredom", slot=None, context={"id": "j2"}, hour=10, seed=2
    )
    ctx = GeneratorContext(intent="boredom")

    async def never_called(*, activity: Any, ctx: Any, row_id: int) -> None:
        raise AssertionError("judge should not have been called")

    task = schedule_judge_sample(
        row_id=7,  # not in-sample at rate=5
        activity=activity,
        ctx=ctx,
        judge_call=never_called,
        rate=5,
    )
    assert task is None


def test_schedule_judge_sample_no_loop_uses_thread() -> None:
    """Outside an asyncio loop, schedule_judge_sample falls back to a thread.

    The sync HTTP propose handler runs the judge sample with no running
    event loop. Rather than skip the sample (which would defeat the
    Phase E SFT pipeline), the scheduler spawns a daemon thread that
    owns its own loop. The thread's coroutine fires the judge_call;
    we wait briefly for it to complete and assert it actually ran.
    """
    import threading

    activity = generate(
        intent="boredom", slot=None, context={"id": "j3"}, hour=10, seed=3
    )
    ctx = GeneratorContext(intent="boredom")
    fired = threading.Event()

    async def fire_then_signal(*, activity: Any, ctx: Any, row_id: int) -> None:
        fired.set()

    # No running loop here — sync test function
    result = schedule_judge_sample(
        row_id=5,
        activity=activity,
        ctx=ctx,
        judge_call=fire_then_signal,
        rate=5,
    )
    # Fallback returns a Thread, not a Task.
    assert isinstance(result, threading.Thread)
    # The thread runs asyncio.run(coroutine); wait for it to fire.
    assert fired.wait(timeout=2.0), "judge thread did not fire within 2s"


def test_schedule_judge_sample_no_loop_no_judge_call_returns_none() -> None:
    """``judge_call=None`` short-circuits before any thread is spun up."""
    activity = generate(
        intent="boredom", slot=None, context={"id": "j-none"}, hour=10, seed=99
    )
    ctx = GeneratorContext(intent="boredom")

    result = schedule_judge_sample(
        row_id=5,
        activity=activity,
        ctx=ctx,
        judge_call=None,
        rate=5,
    )
    assert result is None
