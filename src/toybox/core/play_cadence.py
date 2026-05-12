"""Autonomous play-queue cadence loop.

Wakes on a settings-driven cadence and fires a default-seed propose call
when the proposed queue is below the household-scoped target depth.
Settings and the listening mode are re-read every tick so an operator
flipping the parent-UI controls is honoured on the next wake-up without
a backend restart.

The loop calls :func:`toybox.api.activities._do_propose` directly. The
helper already enforces the contracts the loop needs — eviction, content
resolution, judge scheduling, ``activity.state`` envelope emission — and
there is no UUID-collision concern in production because every tick
uses a fresh random seed.

A fresh ``sqlite3.Connection`` is opened per tick via the injected
``conn_factory`` and closed in a ``finally``: a single bad tick (locked
DB, missing file, malformed setting) is logged and the loop continues.
``asyncio.CancelledError`` is re-raised so the lifespan helper can shut
the task down cleanly on app teardown — mirrors
:func:`toybox.core.transcript_retention.run_transcript_sweep_loop`.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import play_cadence_seconds, play_target_depth
from .listening import ListeningMode, current_mode
from .pubsub import PubSub
from .queue import proposed_count

_logger = logging.getLogger(__name__)


# Guard floor on the sleep value: a corrupt cadence integer that
# escaped the {0, 10, 30, 60} validator must still never spin the
# event loop. Matches the plan's `max(5, cadence)` pseudo-code.
_MIN_TICK_SECONDS = 5
# Wait length when cadence is ``0`` (disabled). The loop keeps
# polling so a settings flip from ``0`` → non-zero is honoured on the
# next cycle without a backend restart.
_DISABLED_POLL_SECONDS = 30


# Default-seed propose body fields. Kept module-level so the field
# choice is reviewable in one place: each autonomous tick asks the
# offline / Claude generator for a generic "request_play / freeplay"
# suggestion — the same shape a manual Trigger button issues.
_DEFAULT_INTENT = "request_play"
_DEFAULT_SLOT = "freeplay"


def _build_propose_body() -> Any:
    """Construct a ``ProposeRequest`` for one autonomous tick.

    Late-imports :class:`toybox.api.activities.ProposeRequest` to keep
    this module's import surface tight — activities.py drags the full
    generator stack (and recursively the AI client / labeled-events
    machinery) which would otherwise be loaded at import time on every
    process even if the cadence task never fires.
    """
    from ..api.activities import ProposeRequest  # noqa: PLC0415

    now = datetime.now(UTC)
    # ``secrets.randbelow`` (not ``random.randint``) matches prior
    # art in :mod:`toybox.api.activities` for non-deterministic
    # propose seeds — cryptographic randomness is not required, but
    # the project convention is to avoid the ``random`` module so
    # tests that pin ``secrets`` see consistent behaviour.
    seed = secrets.randbelow(2**31)
    return ProposeRequest(
        intent=_DEFAULT_INTENT,
        slot=_DEFAULT_SLOT,
        hour=now.hour,
        seed=seed,
    )


def _read_tick_settings(
    conn_factory: Callable[[Path], sqlite3.Connection],
    db_path: Path,
) -> tuple[int, int, ListeningMode]:
    """Read cadence / target / mode against a short-lived connection.

    Pulled out as a helper so the loop body keeps a single visual
    layer of try/except — the settings read has its own failure mode
    (locked DB on open) that the loop treats as a transient bad tick.
    """
    conn = conn_factory(db_path)
    try:
        cadence = play_cadence_seconds.get(conn)
        target = play_target_depth.get(conn)
        mode = current_mode(conn)
        return cadence, target, mode
    finally:
        conn.close()


def _do_propose_blocking(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    judge_call: Any,
) -> None:
    """Run one ``_do_propose`` call against the supplied connection.

    Wrapped so :func:`asyncio.to_thread` can dispatch the synchronous
    generator + DB writes onto a worker thread without parking the
    event loop on Whisper / Claude / sqlite I/O. The return value is
    discarded — the envelope emitted inside ``_do_propose`` is what
    the parent UI consumes.

    Unit tests assert on the constructed body by monkeypatching the
    late-imported :func:`toybox.api.activities._do_propose` symbol —
    see ``tests/unit/core/test_play_cadence.py::
    test_propose_body_fields_per_tick``. The late import (`from
    ..api.activities import _do_propose` inside the function body)
    resolves the patched attribute each call, so tests run on the
    real :func:`_build_propose_body` path.
    """
    from ..api.activities import _do_propose  # noqa: PLC0415

    body = _build_propose_body()
    _do_propose(body, conn, pubsub, judge_call=judge_call)


def start_cadence_loop(
    get_pubsub: Callable[[], PubSub],
    db_path: Path,
    *,
    judge_call_factory: Callable[[], Any] | None = None,
    conn_factory: Callable[[Path], sqlite3.Connection] | None = None,
    sleep: Callable[[float], Any] | None = None,
) -> asyncio.Task[None]:
    """Spawn the autonomous cadence loop as an ``asyncio.Task``.

    Returns the task immediately; the caller (the production
    ``_metrics_lifespan``) is responsible for cancelling + awaiting it
    on shutdown. The task is named ``play-cadence-loop`` so it shows up
    cleanly in ``asyncio.all_tasks()`` dumps.

    ``judge_call_factory`` is invoked once per propose tick — passing
    the factory (rather than a captured ``judge_call``) means an
    OAuth-token add/remove at runtime is picked up on the very next
    tick without a backend restart; the factory itself
    (:func:`toybox.api.activities.get_judge_call`) does a fresh
    ``load_token`` per call so this is cheap. ``None`` is the
    un-authed shape — the recorder still writes the labeled_events row,
    just without judge scores.

    The optional ``conn_factory`` / ``sleep`` seams exist purely for
    unit-testing — production callers pass none of them and get the
    default ``connect(db_path)`` + ``asyncio.sleep`` wiring.
    """
    if conn_factory is None:
        from ..db import connect  # noqa: PLC0415

        def _default_factory(path: Path) -> sqlite3.Connection:
            return connect(path, check_same_thread=False)

        conn_factory = _default_factory

    sleep_fn = sleep if sleep is not None else asyncio.sleep

    async def _loop() -> None:
        while True:
            # Snapshot phase: open a short-lived connection just long
            # enough to read the three live settings, then close it
            # before sleeping. Holding the connection across the
            # (potentially 60s) sleep would pin a sqlite handle and
            # contend with concurrent API writes for no benefit.
            try:
                cadence, target, mode = _read_tick_settings(
                    conn_factory, db_path
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("play cadence settings read failed")
                # Settings read failed (locked DB, missing file,
                # corrupt row). Park for the disabled-poll interval
                # and retry; a persistent fault should not tight-loop.
                await sleep_fn(_DISABLED_POLL_SECONDS)
                continue

            sleep_s: float
            if cadence == 0:
                sleep_s = _DISABLED_POLL_SECONDS
            else:
                sleep_s = max(_MIN_TICK_SECONDS, cadence)
            await sleep_fn(sleep_s)

            # ``cadence == 0`` means "cadence disabled" — the post-
            # sleep value is what gates emission, not the pre-sleep
            # value, but we already woke; just skip propose this tick.
            if cadence == 0:
                continue
            if mode in (ListeningMode.OFFLINE, ListeningMode.LOW):
                continue

            # Propose phase: open a fresh connection for the work.
            # Separate from the settings-snapshot connection above so
            # neither call holds a handle across the sleep.
            conn: sqlite3.Connection | None = None
            try:
                conn = conn_factory(db_path)
                if proposed_count(conn) >= target:
                    continue
                pubsub = get_pubsub()
                # Resolve judge_call per-tick so a token change
                # (login/logout mid-process) is honoured on the next
                # propose without a backend restart. Defensive: a
                # factory exception logs and the loop continues.
                judge_call: Any = None
                if judge_call_factory is not None:
                    try:
                        judge_call = judge_call_factory()
                    except Exception:
                        _logger.exception(
                            "play cadence judge_call_factory failed"
                        )
                        judge_call = None
                await asyncio.to_thread(
                    _do_propose_blocking,
                    conn,
                    pubsub,
                    judge_call,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("play cadence propose tick failed")
            finally:
                if conn is not None:
                    conn.close()

    return asyncio.create_task(_loop(), name="play-cadence-loop")


__all__ = ["start_cadence_loop"]
