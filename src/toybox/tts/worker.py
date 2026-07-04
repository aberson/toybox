"""Background TTS synth worker (Phase Z Z4).

A SINGLE asyncio consumer task draining a bounded in-memory queue of
``(text, voice)`` jobs — the :class:`toybox.image_gen.worker.ImageGenWorker`
lifecycle shape (``start`` spawns the consumer, ``stop`` drains within
a grace window then cancels), minus everything this worker doesn't
need: no DB rows, no WS envelopes, no circuit breaker. A failed clip
just never lands on disk and the kiosk's 404 → Web Speech fallback
covers it (plan §5 "no breaker needed").

Single-task-by-design is load-bearing: the Z3 engine's lazy init has
NO lock, so exactly one consumer means the first ``synthesize`` call
builds the engine and every later call reuses it — no double-load
race, no lock needed (Z3 handoff note).

Enqueue contract (:meth:`TtsWorker.enqueue` / :func:`enqueue_clip`):

* **fire-and-forget, never blocks** — a thread-safe
  ``call_soon_threadsafe`` hand-off onto the worker's loop; the
  request-path caller (a sync FastAPI handler on a threadpool worker)
  returns immediately.
* **capability-gated no-op** — when :func:`is_tts_capable` is False
  (no ``tts`` extra / no model files and not in stub mode) nothing is
  queued, so the consumer task sits parked on ``queue.get()`` forever
  (blocked, NOT spinning).
* **skip-if-exists** — checked here AND again at drain time, so
  repeat text (templates repeat heavily) is rendered once.
* **queue-full no-op** — drop + WARNING rather than backpressure onto
  the request path.

Per-item isolation: every exception from a job (including raw
``ImportError`` / onnxruntime errors propagating out of
:func:`synthesize` — its documented contract) is caught, logged at
WARNING with context, and the loop continues.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Final

from .cache import clip_exists, clip_path
from .engine import is_tts_capable, synthesize

_logger = logging.getLogger(__name__)

# Bounded queue: at ~50 KB/s of WAV and a handful of texts per
# activity, 256 pending clips is far beyond any legitimate burst; a
# full queue means the host can't keep up and dropping (fallback
# already in place) beats unbounded memory growth.
DEFAULT_QUEUE_MAXSIZE: Final[int] = 256

# Grace window for ``stop()`` to drain remaining jobs before
# cancelling the consumer. Clips render in ~5-8 s each on CPU; one
# in-flight clip should finish, a long backlog should not hold up
# shutdown. Anything dropped is re-derivable — the URL is already
# persisted and a later identical enqueue (skip-if-exists misses)
# re-renders it.
_DEFAULT_SHUTDOWN_GRACE_SEC: Final[float] = 10.0


class TtsWorker:
    """Single-consumer synth queue. Construct cheap; :meth:`start` spawns."""

    def __init__(
        self,
        *,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        shutdown_grace_sec: float = _DEFAULT_SHUTDOWN_GRACE_SEC,
    ) -> None:
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=queue_maxsize)
        self._consumer: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False
        self._rendered_count = 0
        self._failed_count = 0
        self._shutdown_grace_sec = shutdown_grace_sec

    # ------------------------------------------------------------------
    # Lifecycle (ImageGenWorker shape)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the consumer task on the running loop. Idempotent."""
        if self._consumer is not None:
            return
        self._stopping = False
        self._loop = asyncio.get_running_loop()
        self._consumer = asyncio.create_task(
            self._consumer_loop(),
            name="toybox-tts-worker",
        )

    async def stop(self) -> None:
        """Drain the queue (grace window), then cancel the consumer.

        Mirrors ``ImageGenWorker.stop``: ``queue.join()`` returns once
        every popped job has also called ``task_done`` — covering both
        queued-but-unstarted jobs and the in-flight one. On timeout we
        log a WARNING and cancel; dropped clips simply stay unrendered
        (the kiosk 404-falls-back, and a future enqueue of the same
        text re-renders).
        """
        self._stopping = True
        if self._consumer is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=self._shutdown_grace_sec)
        except TimeoutError:
            _logger.warning(
                "tts worker: shutdown grace (%.1fs) elapsed with %d job(s) "
                "still queued; cancelling consumer",
                self._shutdown_grace_sec,
                self._queue.qsize(),
            )
        self._consumer.cancel()
        try:
            await self._consumer
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 -- cleanup
            pass
        self._consumer = None
        self._loop = None
        _logger.info(
            "tts worker stopped (%d clip(s) rendered, %d failed this run)",
            self._rendered_count,
            self._failed_count,
        )

    # ------------------------------------------------------------------
    # Enqueue (sync, thread-safe, fire-and-forget)
    # ------------------------------------------------------------------

    def enqueue(self, text: str, voice: str) -> bool:
        """Queue one clip render. Never blocks; returns True iff queued.

        Safe to call from any thread (the sync FastAPI handlers run on
        a threadpool): the actual ``put_nowait`` is marshalled onto the
        worker's loop via ``call_soon_threadsafe``.

        No-op (returns False) when: the worker is stopped/stopping,
        text or voice is blank, TTS is not capable on this host, the
        clip already exists on disk, the voice id is unsafe, or the
        queue is full.
        """
        # Snapshot the loop reference ONCE: ``stop()`` (on the loop
        # thread) nulls ``self._loop`` concurrently with foreign-thread
        # enqueues, so re-reading the attribute later could raise
        # AttributeError mid-request (shutdown TOCTOU).
        loop = self._loop
        if self._stopping or loop is None:
            return False
        if not text.strip() or not voice.strip():
            return False
        if not is_tts_capable():
            return False
        try:
            if clip_exists(voice, text):
                return False
        except ValueError:
            # Unsafe voice id (corrupt persona data). The resolver in
            # api.activities falls back to the default voice before we
            # get here; this guard keeps a bad id from ever reaching
            # the filesystem/URL layer.
            _logger.warning("tts worker: dropping clip job with unsafe voice id %r", voice)
            return False
        if self._queue.full():
            # Approximate fast-path check (authoritative re-check in
            # _put_nowait below); fire-and-forget means drop, not block.
            _logger.warning(
                "tts worker: queue full (%d); dropping clip job voice=%s text_len=%d",
                self._queue.maxsize,
                voice,
                len(text),
            )
            return False
        try:
            running_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            # Already on the worker's loop (async caller / same-loop
            # test): put directly so the item is queued synchronously.
            return self._put_nowait((text, voice))
        try:
            # Foreign thread (the sync FastAPI handlers run on the
            # threadpool): marshal the put onto the worker's loop.
            loop.call_soon_threadsafe(self._put_nowait, (text, voice))
        except (RuntimeError, AttributeError):
            # Loop already closed / torn down (shutdown race) — drop
            # silently; the persisted URL keeps the fallback contract
            # intact. AttributeError is defense-in-depth should a
            # partially torn-down loop object surface mid-call.
            return False
        return True

    def _put_nowait(self, item: tuple[str, str]) -> bool:
        """Loop-thread half of :meth:`enqueue`.

        Deliberately does NOT re-check ``_stopping``: an item accepted
        by ``enqueue`` before ``stop()`` began is still drained inside
        the shutdown grace window (``stop`` awaits ``queue.join()``).
        """
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            _logger.warning(
                "tts worker: queue full (%d); dropping clip job voice=%s text_len=%d",
                self._queue.maxsize,
                item[1],
                len(item[0]),
            )
            return False
        return True

    @property
    def queue_size(self) -> int:
        """Jobs waiting to be picked up. Test-only."""
        return self._queue.qsize()

    @property
    def rendered_count(self) -> int:
        """Clips rendered this run. Test-only."""
        return self._rendered_count

    # ------------------------------------------------------------------
    # Consumer
    # ------------------------------------------------------------------

    async def _consumer_loop(self) -> None:
        """Run forever, popping jobs; one bad job never kills the loop."""
        while True:
            text, voice = await self._queue.get()
            try:
                await self._run_one(text, voice)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- per-item isolation by contract
                self._failed_count += 1
                _logger.warning(
                    "tts worker: clip render failed (voice=%s, text_len=%d, "
                    "%d failed this run) — Web Speech fallback stays in place",
                    voice,
                    len(text),
                    self._failed_count,
                    exc_info=True,
                )
            finally:
                self._queue.task_done()

    async def _run_one(self, text: str, voice: str) -> None:
        """Render one clip to the cache path (skip-if-exists re-check)."""
        path = clip_path(voice, text)
        if path.is_file():
            # Drain-side skip: a duplicate enqueue raced the first
            # render, or an operator batch pre-rendered the clip.
            return

        def _synth_and_write() -> int:
            wav = synthesize(text, voice)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so the static mount can never serve a
            # half-written WAV (os.replace is atomic on one volume).
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(wav)
            os.replace(tmp, path)
            return len(wav)

        # CPU-bound synth (RTF ≈ 1.1) runs on a thread so the event
        # loop keeps serving requests while a clip renders.
        n_bytes = await asyncio.to_thread(_synth_and_write)
        self._rendered_count += 1
        _logger.info(
            "tts worker: rendered clip %s/%s (%d bytes; %d rendered this run)",
            voice,
            path.name,
            n_bytes,
            self._rendered_count,
        )


# ---------------------------------------------------------------------
# Module-level singleton (FastAPI lifespan owner; ImageGenWorker shape)
# ---------------------------------------------------------------------


_worker: TtsWorker | None = None


def get_tts_worker() -> TtsWorker | None:
    """Return the process-wide worker, or ``None`` if not started."""
    return _worker


async def start_tts_worker(
    *,
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
    shutdown_grace_sec: float = _DEFAULT_SHUTDOWN_GRACE_SEC,
) -> TtsWorker:
    """Construct + start the singleton worker. Idempotent."""
    global _worker
    if _worker is not None:
        return _worker
    worker = TtsWorker(
        queue_maxsize=queue_maxsize,
        shutdown_grace_sec=shutdown_grace_sec,
    )
    await worker.start()
    _worker = worker
    return worker


async def stop_tts_worker() -> None:
    """Stop + drop the singleton worker. Idempotent."""
    global _worker
    if _worker is None:
        return
    try:
        await _worker.stop()
    finally:
        _worker = None


def reset_tts_worker_for_tests() -> None:
    """Drop the cached worker without awaiting cleanup. Tests only."""
    global _worker
    _worker = None


def enqueue_clip(text: str, voice: str) -> bool:
    """Fire-and-forget module-level enqueue for the API-layer hooks.

    No-ops (False) when the worker isn't running — e.g. a deployment
    that never composed :func:`toybox.app.tts_worker_lifespan`, or a
    unit test exercising the enqueue sites without a worker. The
    persisted ``spoken_*_url`` metadata is written regardless by the
    callers; an unrendered clip is the designed 404 → Web Speech
    fallback.
    """
    worker = _worker
    if worker is None:
        return False
    return worker.enqueue(text, voice)


__all__ = [
    "DEFAULT_QUEUE_MAXSIZE",
    "TtsWorker",
    "enqueue_clip",
    "get_tts_worker",
    "reset_tts_worker_for_tests",
    "start_tts_worker",
    "stop_tts_worker",
]
