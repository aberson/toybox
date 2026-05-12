"""TTL sweep for stale ``proposed`` activities.

Proposed activities have a time-to-live equal to ``3 × cadence``: an
activity that the parent never approves or dismisses within three full
autonomous-cadence intervals is silently transitioned to ``dismissed``
so the scrolling queue doesn't accumulate ghost cards. The sweep runs
as a lifespan task on a 10s tick — the next sweep observes settings
changes (cadence flip, including the 0=disabled case) without a
backend restart.

**Cadence semantics:** when ``play_cadence_seconds == 0`` (autonomous
proposes disabled by the operator) the sweep is also a no-op — there's
no natural TTL when nothing's being added, and dismissing proposes the
parent is actively reviewing would be hostile. The disabled-poll
behaviour matches :mod:`toybox.core.play_cadence`'s wake-and-skip
shape so a flip back to non-zero is honoured on the next tick.

**Timestamp format:** the cutoff is rendered identically to how the
propose flow writes ``activities.created_at`` (see
:func:`toybox.api.activities._now_iso`):
``ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")``.
The shape pinned by
:data:`toybox.core.transcript_retention.ENDED_AT_ISO_FORMAT_NOTE` is
load-bearing here too — the sweep compares ``created_at < cutoff``
lexicographically, which only matches numeric comparison when both
sides are the same UTC-second-precision-trailing-Z shape.

Mirrors :func:`toybox.core.transcript_retention.run_transcript_sweep_loop`
in structure: same async task pattern, same monkey-patchable
``asyncio.sleep`` seam, same cancel + await teardown shape — so the
lifespan can drive both with one helper.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import play_cadence_seconds
from .pubsub import PubSub

_logger = logging.getLogger(__name__)


# Tick interval for the sweep. Shorter than the autonomous cadence's
# 10/30/60s presets so a freshly-expired row is reaped within ~10s of
# crossing the TTL threshold — long enough that the loop doesn't burn
# the event loop, short enough that the parent UI's TTL fade animation
# (which expects the row to disappear shortly after the wedge empties)
# lines up with the actual eviction.
_TICK_SECONDS = 10.0

# TTL multiplier on the live cadence: a proposed row older than
# ``cadence * _TTL_MULTIPLIER`` is reaped. 3× is the parent UI's
# spec — far enough into the past that the autonomous cadence has
# already cycled in fresher suggestions, close enough that the queue
# doesn't grow boundlessly when the parent is away.
_TTL_MULTIPLIER = 3


def _format_created_at_cutoff(ts: datetime) -> str:
    """Render ``ts`` as a UTC ISO string byte-identical to ``_now_iso``.

    Mirrors :func:`toybox.api.activities._now_iso` so the sweep's
    ``created_at < cutoff`` comparison is a valid lex sort. Any drift
    here (e.g. microsecond precision, ``+00:00`` suffix) would silently
    skip rows that look "older" numerically but compare "newer"
    lexicographically.
    """
    return ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _emit_dismissed_envelope(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    activity_id: str,
) -> None:
    """Publish an ``activity.state`` envelope for the dismissed row.

    Re-fetches the row + serializes through ``_row_to_response`` +
    ``_emit_state`` so the envelope shape is byte-identical to what the
    REST dismiss handler emits — same field set, same PII stripping
    (``trigger_phrase`` / ``persona_reasoning`` are popped before the
    payload crosses the child-kiosk topic boundary). Late-imports
    :mod:`toybox.api.activities` to keep this module's import surface
    tight; activities.py drags the full generator stack.
    """
    from ..api.activities import _emit_state, _fetch_activity_row, _row_to_response  # noqa: PLC0415

    row = _fetch_activity_row(conn, activity_id)
    response = _row_to_response(conn, row)
    _emit_state(pubsub, response)


def sweep_expired_proposed(
    conn: sqlite3.Connection,
    pubsub: PubSub,
    now: datetime,
) -> int:
    """Dismiss every ``proposed`` row older than ``cadence × 3``.

    Reads the live cadence per call; when ``cadence == 0`` (disabled)
    the function short-circuits without writing. Emits one
    ``activity.state`` envelope per dismissed id so connected UIs see
    the disappearance. Returns the dismissed-row count for log driver.

    The UPDATE selects the victim ids first (so the post-update SELECT
    isn't racing the UPDATE itself), then issues a single statement
    with an ``IN`` clause + a state guard. The guard (``state =
    'proposed'``) makes the operation idempotent — a concurrent
    parent-side dismiss / approve that flipped the row between our
    SELECT and UPDATE is harmless (the WHERE filter drops it from the
    update set and we just don't emit for that id).
    """
    cadence = play_cadence_seconds.get(conn)
    if cadence == 0:
        return 0
    ttl = timedelta(seconds=cadence * _TTL_MULTIPLIER)
    cutoff = _format_created_at_cutoff(now - ttl)
    # Snapshot the victims FIRST so we know which envelopes to emit.
    # A LIMIT-less DELETE+RETURNING would be cleaner but sqlite's
    # RETURNING support landed in 3.35; the project pins a wider
    # version range, so two statements is the portable shape.
    rows = conn.execute(
        "SELECT id FROM activities WHERE state = 'proposed' AND created_at < ?",
        (cutoff,),
    ).fetchall()
    if not rows:
        return 0
    victim_ids = [str(r["id"]) for r in rows]
    placeholders = ",".join("?" for _ in victim_ids)
    with conn:
        cursor = conn.execute(
            f"UPDATE activities SET state = 'dismissed', version = version + 1 "
            f"WHERE state = 'proposed' AND id IN ({placeholders})",
            victim_ids,
        )
    dismissed_count = cursor.rowcount
    if dismissed_count <= 0:
        return 0
    for activity_id in victim_ids:
        try:
            _emit_dismissed_envelope(conn, pubsub, activity_id)
        except Exception:  # noqa: BLE001 -- envelope emit must not crash sweep
            _logger.warning(
                "proposed-ttl envelope emit failed for %s; row still dismissed",
                activity_id,
                exc_info=True,
            )
    return dismissed_count


def start_proposed_ttl_sweep(
    get_pubsub_fn: Callable[[], PubSub],
    db_path: Path,
    *,
    conn_factory: Callable[[Path], sqlite3.Connection] | None = None,
    sleep: Callable[[float], Any] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    interval_seconds: float = _TICK_SECONDS,
) -> asyncio.Task[None]:
    """Spawn the TTL sweep as an ``asyncio.Task``.

    Returns the task immediately; the caller (the production
    ``_metrics_lifespan``) is responsible for ``cancel() + await`` on
    shutdown. The task is named ``proposed-ttl-sweep`` so it shows up
    cleanly in ``asyncio.all_tasks()`` dumps.

    The optional ``conn_factory`` / ``sleep`` / ``clock`` /
    ``interval_seconds`` seams exist purely for unit testing —
    production callers pass none of them and get the default
    ``connect(db_path)`` + ``asyncio.sleep`` + wall-clock wiring.

    Mirrors :func:`toybox.core.play_cadence.start_cadence_loop` in
    teardown shape: per-tick exceptions are logged at ERROR and the
    loop continues so a single bad tick (locked DB, missing file,
    transient sqlite error) doesn't crash the lifespan task.
    ``asyncio.CancelledError`` is re-raised so the lifespan can shut
    the task down cleanly.
    """
    if conn_factory is None:
        from ..db import connect  # noqa: PLC0415

        def _default_factory(path: Path) -> sqlite3.Connection:
            return connect(path, check_same_thread=False)

        conn_factory = _default_factory

    sleep_fn = sleep if sleep is not None else asyncio.sleep

    async def _loop() -> None:
        while True:
            await sleep_fn(interval_seconds)
            conn: sqlite3.Connection | None = None
            try:
                conn = conn_factory(db_path)
                pubsub = get_pubsub_fn()
                count = sweep_expired_proposed(conn, pubsub, clock())
                if count:
                    _logger.info("swept %d expired proposed activities", count)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("proposed-ttl sweep tick failed")
            finally:
                if conn is not None:
                    conn.close()

    return asyncio.create_task(_loop(), name="proposed-ttl-sweep")


__all__ = [
    "start_proposed_ttl_sweep",
    "sweep_expired_proposed",
]
