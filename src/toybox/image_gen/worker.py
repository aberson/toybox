"""Single-worker asyncio queue for the image-gen pipeline.

Phase F Step F4. The worker is the orchestration seam between the F2
pipeline (``pipeline.generate_action``) and the F3 storage layer
(``storage.toy_actions``). It owns:

* A bounded :class:`asyncio.Queue` of ``(toy_id, slot, seed)`` jobs.
* A single consumer :class:`asyncio.Task` that processes jobs FIFO so
  SDXL never contends with itself for VRAM (plan §"Single-worker
  asyncio.Queue").
* Per-job status persistence + WS envelope emission on
  :data:`toybox.ws.topics.Topic.toy_actions`.
* Per-pipeline breaker integration via
  :func:`toybox.image_gen.capability.get_image_gen_breaker`.
* Enqueue-time AND run-time supersede checks (the "dual dedup"
  pattern from the plan): a rapid double-enqueue marks the older
  ``queued``/``running`` row ``superseded`` so it doesn't pile up; a
  newer enqueue arriving while the worker is mid-generation flips the
  in-flight row to ``superseded`` and the worker discards its output
  on commit rather than overwriting the newer state.
* A boot-time restart-recovery sweep that marks any row left in the
  ``running`` state by a previous process as ``failed`` with reason
  ``"interrupted by restart"``.

The worker NEVER writes ``ToyActionStatus.not_started`` — that's a
UI-only placeholder synthesized by
:func:`toybox.storage.toy_actions.list_for_toy`; the storage seam
rejects it explicitly.

Public surface (mirrors the seam contracts pinned in the build-step
brief — :class:`ImageGenWorker` plus the module-level singleton
helpers used by the FastAPI lifespan):

* :class:`ImageGenWorker` — the worker class.
* :func:`get_image_gen_worker` — singleton accessor (None until
  :func:`start_image_gen_worker` runs).
* :func:`start_image_gen_worker` — construct + start the worker;
  also runs the restart-recovery sweep.
* :func:`stop_image_gen_worker` — drain queue + cancel consumer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

from ..ws.topics import Topic
from .capability import (
    CapabilityReason,
    get_image_gen_breaker,
    is_image_gen_capable,
)
from .models import (
    ACTION_SLOTS,
    GenerationContext,
    ImageGenCapacityError,
    ImageGenTimeoutError,
    ToyActionStatus,
)

_logger = logging.getLogger(__name__)

# Match the path layout the F2 CLI ``__main__`` uses so the kiosk's
# static-files mount (``/api/static/images/toy_actions/<toy_id>/...``)
# resolves the same files the worker writes. ``TOYBOX_DATA_DIR`` is the
# env override used across the storage subsystem.
_DATA_DIR_ENV: Final[str] = "TOYBOX_DATA_DIR"
_DEFAULT_DATA_ROOT: Final[Path] = Path("data")
_TOY_ACTIONS_SUBDIR: Final[str] = "toy_actions"

# Wait briefly when the breaker is open before re-checking. The breaker
# cooldown is configured upstream (``TOYBOX_IMAGE_GEN_BREAKER_OPEN_SEC``);
# this controls how often a worker that already popped a job re-checks
# rather than the cooldown duration itself.
_BREAKER_RECHECK_SEC: Final[float] = 0.05

# Default grace window for :meth:`ImageGenWorker.stop` to drain the queue
# before cancelling the consumer. The drain calls
# :meth:`asyncio.Queue.join`, which only returns once every popped job has
# also called ``task_done`` — so it covers both queued-but-unstarted jobs
# AND the in-flight job. On timeout we log a WARNING and fall through to
# cancel; restart-recovery picks up any orphaned ``running``/``queued``
# row on next boot (see :meth:`run_restart_recovery`).
_DEFAULT_SHUTDOWN_GRACE_SEC: Final[float] = 30.0


def _data_root() -> Path:
    """Return the configured data root, honouring ``TOYBOX_DATA_DIR``."""
    raw = os.environ.get(_DATA_DIR_ENV)
    return Path(raw) if raw else _DEFAULT_DATA_ROOT


def _output_path(toy_id: str) -> Path:
    """Directory under which a toy's action sprites live on disk.

    Defense-in-depth: re-validates ``toy_id`` against the UUIDv4 pattern
    here even though :meth:`ImageGenWorker.enqueue` already validates.
    A future code path that bypasses ``enqueue`` (e.g. a sync-only
    helper, an admin script) would otherwise be free to write to
    arbitrary path segments. Mirrors the ``slot`` re-validation in
    :meth:`ImageGenWorker._run_one`.
    """
    from ..storage.toy_actions import _validate_toy_id

    _validate_toy_id(toy_id)
    return _data_root() / "images" / _TOY_ACTIONS_SUBDIR / toy_id


def _stored_image_path(toy_id: str, slot: str) -> str:
    """DB-portable ``image_path`` value for a (toy, slot) PNG.

    Mirrors :func:`toybox.storage.images.relative_committed_path` —
    forward-slash paths so Windows + Linux dev DBs round-trip cleanly.
    """
    return f"data/images/{_TOY_ACTIONS_SUBDIR}/{toy_id}/{slot}.png"


# Type aliases for the worker constructor.
ConnFactory = Callable[[], sqlite3.Connection]
EmitCallable = Callable[[Topic, dict[str, object]], Awaitable[None]]


class ImageGenWorker:
    """Single-worker async queue + consumer for image-gen jobs.

    The worker is intentionally cheap to construct; the consumer task
    is only spawned by :meth:`start`. Tests typically construct +
    start manually so they can stub the pipeline before the worker
    pops its first job.
    """

    def __init__(
        self,
        conn_factory: ConnFactory,
        emit: EmitCallable,
        *,
        pipeline: Callable[
            [bytes, str, int, GenerationContext],
            Awaitable[bytes],
        ]
        | None = None,
        composite: Callable[
            [bytes, str, int, GenerationContext],
            Awaitable[bytes],
        ]
        | None = None,
        capability_probe: Callable[[], tuple[bool, CapabilityReason, str]] | None = None,
        shutdown_grace_sec: float = _DEFAULT_SHUTDOWN_GRACE_SEC,
    ) -> None:
        self._conn_factory = conn_factory
        self._emit = emit
        self._queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue()
        self._consumer: asyncio.Task[None] | None = None
        self._stopping = False
        self._shutdown_grace_sec = shutdown_grace_sec
        # Pipeline override is purely a test affordance — production
        # callers pass ``None`` and we resolve the real
        # :func:`generate_action` lazily so a worker constructed in a
        # capability-disabled deployment doesn't pay the import cost.
        self._pipeline_override = pipeline
        # F.5-3a: parallel override for the Tier C composite path.
        # ``None`` → resolve the real :func:`composite.composite_action`
        # lazily on first dispatch.
        self._composite_override = composite
        # F.5-3a: capability probe override so tests can pin the
        # dispatch branch without poking torch / env vars. Production
        # callers pass ``None`` and we use the real
        # :func:`is_image_gen_capable`.
        self._capability_probe = capability_probe

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        toy_id: str,
        slot: str,
        *,
        seed: int | None = None,
    ) -> None:
        """Queue one job, applying the enqueue-time supersede check.

        If the existing row for ``(toy_id, slot)`` is in
        :attr:`ToyActionStatus.queued` or :attr:`ToyActionStatus.running`,
        we mark it ``superseded`` first so the in-flight worker run
        (if any) discards its output on commit — see
        :meth:`_run_one`'s recheck. Then we ``upsert_status`` the new
        row to ``queued`` and put the tuple on the asyncio queue.

        Raises:
            ValueError: When ``toy_id`` is not a UUIDv4 or ``slot`` is
                outside :data:`ACTION_SLOTS`. The storage seam
                validates again, but raising here surfaces the bad
                input to the caller (REST handler in F5) with the
                actual offending value rather than after a queue
                round-trip.
        """
        # Validate up front — the storage seam re-validates but we want
        # the REST caller (F5) to see the error before anything lands
        # in the queue.
        from ..storage.toy_actions import (
            _validate_slot,
            _validate_toy_id,
        )

        _validate_toy_id(toy_id)
        _validate_slot(slot)
        resolved_seed = seed if seed is not None else self._fresh_seed()

        def _supersede_and_enqueue(conn: sqlite3.Connection) -> None:
            # Single ``BEGIN IMMEDIATE`` transaction so the SELECT +
            # supersede-write + queued-write commit atomically; without
            # this, two concurrent enqueues for the same (toy_id, slot)
            # could each see ``status='queued'``, both flip to
            # ``superseded``, and both write a fresh ``queued`` row,
            # losing the supersede invariant. The storage layer's
            # ``upsert_status`` opens its own ``with conn:`` per call,
            # so we bypass it here and inline the SQL — this is the
            # same set of writes ``upsert_status`` would emit, but
            # bracketed in one explicit transaction. ``BEGIN IMMEDIATE``
            # rather than the default deferred transaction so concurrent
            # enqueues serialize at the writer-lock acquisition rather
            # than racing the SELECT.
            from ..storage.toy_actions import _now_iso

            # We're under the stdlib's implicit-transaction mode
            # (``isolation_level=""``); the migration runner uses the
            # same explicit-BEGIN pattern. Commit at the end so the
            # serialization holds across all three statements.
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT status FROM toy_actions WHERE toy_id = ? AND slot = ?",
                    (toy_id, slot),
                ).fetchone()
                now = _now_iso()
                if row is not None and row["status"] in {
                    ToyActionStatus.queued.value,
                    ToyActionStatus.running.value,
                }:
                    conn.execute(
                        """
                        UPDATE toy_actions
                        SET status = ?, updated_at = ?
                        WHERE toy_id = ? AND slot = ?
                        """,
                        (
                            ToyActionStatus.superseded.value,
                            now,
                            toy_id,
                            slot,
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO toy_actions
                        (toy_id, slot, status, image_path, seed,
                         error_msg, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(toy_id, slot) DO UPDATE SET
                        status     = excluded.status,
                        image_path = excluded.image_path,
                        seed       = excluded.seed,
                        error_msg  = excluded.error_msg,
                        updated_at = excluded.updated_at
                    """,
                    (
                        toy_id,
                        slot,
                        ToyActionStatus.queued.value,
                        None,
                        resolved_seed,
                        None,
                        now,
                    ),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        await asyncio.to_thread(self._with_conn, _supersede_and_enqueue)
        await self._emit_status(
            toy_id,
            slot,
            ToyActionStatus.queued,
        )
        await self._queue.put((toy_id, slot, resolved_seed))

    async def start(self) -> None:
        """Spawn the consumer task. Idempotent."""
        if self._consumer is not None:
            return
        self._stopping = False
        self._consumer = asyncio.create_task(
            self._consumer_loop(),
            name="toybox-image-gen-worker",
        )

    async def stop(self) -> None:
        """Drain the queue (grace window) then cancel the consumer.

        Phase F Step F4 reviewer fix: the previous implementation
        cancelled immediately and silently dropped queued-but-unpopped
        jobs (those rows leaked across process restarts because the
        original restart-recovery sweep only looked at ``running``).
        Now we wait up to ``self._shutdown_grace_sec`` for
        :meth:`asyncio.Queue.join` to clear — that returns once every
        popped job has also called ``task_done``, so it covers both
        queued-but-unstarted jobs and the in-flight job. On timeout we
        log a WARNING and fall through to cancel; the next boot's
        restart-recovery sweep (which now also handles ``queued``)
        catches anything left behind. Cancellation of the in-flight
        job is also handled explicitly by :meth:`_run_one`'s
        ``CancelledError`` branch — it writes ``failed("interrupted by
        shutdown")`` before re-raising, so the row never leaks in the
        ``running`` state during the same shutdown.
        """
        self._stopping = True
        if self._consumer is None:
            return
        try:
            await asyncio.wait_for(
                self._queue.join(),
                timeout=self._shutdown_grace_sec,
            )
        except TimeoutError:
            _logger.warning(
                "image_gen worker: shutdown grace (%.1fs) elapsed with "
                "%d job(s) still in queue; cancelling consumer",
                self._shutdown_grace_sec,
                self._queue.qsize(),
            )
        self._consumer.cancel()
        try:
            await self._consumer
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 -- cleanup
            # Cancellation is the expected path; any other exception
            # has already been logged inside the consumer loop.
            pass
        self._consumer = None

    async def run_restart_recovery(self) -> int:
        """Mark any orphaned ``running``/``queued`` rows as ``failed``.

        Called once at app boot, BEFORE the consumer task starts. We
        don't emit WS envelopes for the recovered rows — there are no
        clients connected at this point, and the count is logged at
        INFO so ops see the recovery in the journal.

        Both ``running`` AND ``queued`` rows are swept: a process that
        died after :meth:`enqueue` wrote the queued row but before the
        consumer popped it would otherwise leak that row across
        restarts forever (the in-memory queue is gone, so no one will
        ever pop it). The :meth:`stop` drain covers the in-process
        case; this sweep is the belt-and-suspenders catch for crashes
        and OS-level kills.

        Idempotent: a second call returns 0 because the previous call
        flipped every row to ``failed``.

        Returns:
            Count of rows recovered.
        """
        return await asyncio.to_thread(self._restart_recovery_sync)

    @property
    def queue_size(self) -> int:
        """Current number of jobs waiting to be picked up. Test-only."""
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def _consumer_loop(self) -> None:
        """Run forever, popping jobs and dispatching them."""
        while True:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._run_one(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- defensive
                _logger.exception(
                    "image_gen worker: unhandled exception running job %s",
                    job,
                )
            finally:
                self._queue.task_done()

    async def _run_one(self, job: tuple[str, str, int]) -> None:
        """Process a single job per the lifecycle in the build-step brief.

        See module docstring for the full lifecycle. This method is the
        canonical spot where supersede-on-commit is enforced; tests
        wrap a stub pipeline + monkey-patch the row to ``superseded``
        mid-flight to assert the discard path.

        Cancellation: if the consumer task is cancelled mid-flight
        (e.g. shutdown grace fires while a stub is gated), the row
        could otherwise leak in ``running`` state until the next boot.
        We catch :class:`asyncio.CancelledError`, write
        ``failed("interrupted by shutdown")`` + emit a best-effort WS
        envelope, then re-raise. The PNG file (if already written) is
        intentionally left in place — orphan PNGs don't surface to
        users since the row is ``failed`` and restart recovery /
        next click will overwrite cleanly.
        """
        toy_id, slot, seed = job

        # Defensive validation — F5's REST handler validates and
        # ``enqueue`` validates, but a corrupted in-memory queue
        # entry should not crash the consumer.
        if slot not in ACTION_SLOTS:
            _logger.error(
                "image_gen worker: dropping job with invalid slot=%r toy_id=%s",
                slot,
                toy_id,
            )
            return

        try:
            await self._run_one_body(toy_id, slot, seed)
        except asyncio.CancelledError:
            await self._mark_cancelled_best_effort(toy_id, slot, seed)
            raise

    async def _run_one_body(
        self,
        toy_id: str,
        slot: str,
        seed: int,
    ) -> None:
        """Body of :meth:`_run_one`, factored out so the cancellation
        handler can be a single ``except`` at the call site.
        """
        # Run-time supersede check #1 — if the row was preempted while
        # this job sat in the queue, skip the entire pipeline call.
        if await self._is_superseded(toy_id, slot, seed=seed):
            _logger.info(
                "image_gen worker: skipping superseded job toy_id=%s slot=%s",
                toy_id,
                slot,
            )
            return

        # Per-pipeline breaker check. If open, mark the row failed and
        # skip — the plan's "enqueues during open state are accepted
        # but immediately marked failed" semantics. We re-check after
        # a short sleep so the breaker can transition to half-open
        # without us spinning.
        breaker = get_image_gen_breaker()
        if breaker.is_open():
            await asyncio.sleep(_BREAKER_RECHECK_SEC)
        if breaker.is_open():
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error="image-gen breaker open",
            )
            return

        # Mark running + emit WS.
        await self._upsert(
            toy_id,
            slot,
            ToyActionStatus.running,
            seed=seed,
        )
        await self._emit_status(toy_id, slot, ToyActionStatus.running)

        # Resolve the toy + persona context (DB read on a thread so the
        # asyncio loop stays free). Data-error branches (LookupError /
        # FileNotFoundError) do NOT trip the breaker — those are toy-
        # row issues, not GPU/pipeline issues. Three of these in a row
        # would otherwise falsely open the breaker for everyone.
        try:
            ctx, reference_bytes = await asyncio.to_thread(
                self._load_toy_context_sync,
                toy_id,
            )
        except (LookupError, FileNotFoundError, PermissionError) as exc:
            _logger.warning(
                "image_gen worker: toy lookup failed toy_id=%s slot=%s: %s",
                toy_id,
                slot,
                exc,
            )
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error=str(exc)[:200],
            )
            return
        except ValueError as exc:
            # Validation error (e.g., bad tags JSON) — data issue.
            _logger.warning(
                "image_gen worker: validation error loading context for toy_id=%s slot=%s: %s",
                toy_id,
                slot,
                exc,
            )
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error=str(exc)[:200],
            )
            return
        except Exception as exc:  # noqa: BLE001 -- defensive
            _logger.exception(
                "image_gen worker: unexpected error loading context for %s/%s",
                toy_id,
                slot,
            )
            breaker.check_and_record(success=False)
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error=str(exc)[:200],
            )
            return

        # F.5-3a: dispatch on the capability gate.
        #
        # * CAPABLE → Tier B diffusion pipeline (existing path).
        # * ENV_DISABLED → operator hard-off; mark failed with
        #   ``"image_gen_disabled"`` and skip both pipelines.
        # * NO_CUDA / LOW_VRAM / MISSING_CHECKPOINTS → Tier C
        #   composite fallback. Same DB-write + WS-emit path; on
        #   composite failure the row is marked failed with
        #   ``"image_gen_composite_only"`` (so parent UI can
        #   distinguish "GPU pipeline + composite both unavailable"
        #   from a Tier B OOM).
        #
        # No prefix-string matching anywhere — the dispatch is a
        # straight enum compare.
        try:
            capable, reason_enum, _detail = self._probe_capability()
        except Exception as exc:  # noqa: BLE001 -- defensive
            _logger.exception(
                "image_gen worker: capability probe raised for toy_id=%s slot=%s",
                toy_id,
                slot,
            )
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error=str(exc)[:200],
            )
            return

        if not capable and reason_enum is CapabilityReason.ENV_DISABLED:
            # Hard-off. Operator explicitly turned image-gen off; do
            # NOT route to composite either. Match the historical
            # error_msg so existing parent-UI consumers see the same
            # value they did pre-F.5.
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error="image_gen_disabled",
            )
            return

        composite_path = not capable

        # Run the chosen pipeline. Errors map to specific ``error_msg``
        # strings per the build-step brief.
        pipeline = self._resolve_composite() if composite_path else self._resolve_pipeline()
        try:
            png_bytes = await pipeline(reference_bytes, slot, seed, ctx)
        except ImageGenCapacityError as exc:
            breaker.check_and_record(success=False)
            # On the composite path, ``ImageGenCapacityError`` means a
            # template / manifest is missing — a structural Tier C
            # problem, not a GPU OOM. Use a distinct ``error_msg`` so
            # consumers can distinguish.
            if composite_path:
                _logger.warning(
                    "image_gen worker: composite raised for toy_id=%s slot=%s: %s",
                    toy_id,
                    slot,
                    exc,
                )
                await self._mark_failed(
                    toy_id,
                    slot,
                    seed,
                    error="image_gen_composite_only",
                )
                return
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error="out of memory",
            )
            return
        except ImageGenTimeoutError:
            breaker.check_and_record(success=False)
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error="timeout",
            )
            return
        except Exception as exc:  # noqa: BLE001 -- one bad pipeline shouldn't kill the worker
            _logger.exception(
                "image_gen worker: %s raised for toy_id=%s slot=%s",
                "composite" if composite_path else "pipeline",
                toy_id,
                slot,
            )
            breaker.check_and_record(success=False)
            # Composite-path failures get the canonical
            # ``"image_gen_composite_only"`` error_msg so the parent
            # UI's per-cell tooltip surfaces a coherent reason.
            if composite_path:
                await self._mark_failed(
                    toy_id,
                    slot,
                    seed,
                    error="image_gen_composite_only",
                )
                return
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error=str(exc)[:200],
            )
            return

        # Write PNG to disk first so a crash between filesystem write
        # and DB commit leaves a recoverable state (the DB row is
        # still ``running``; restart recovery flips it to ``failed``).
        out_dir = _output_path(toy_id)
        out_path = out_dir / f"{slot}.png"
        try:
            await asyncio.to_thread(self._write_png, out_path, png_bytes)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            # Filesystem-layer failures are environment / data issues
            # (full disk, ACL, missing parent), not GPU/pipeline
            # issues — don't trip the breaker.
            _logger.exception(
                "image_gen worker: failed to write PNG for %s/%s",
                toy_id,
                slot,
            )
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error=str(exc)[:200],
            )
            return
        except Exception as exc:  # noqa: BLE001 -- defensive
            _logger.exception(
                "image_gen worker: unexpected PNG-write failure for %s/%s",
                toy_id,
                slot,
            )
            breaker.check_and_record(success=False)
            await self._mark_failed(
                toy_id,
                slot,
                seed,
                error=str(exc)[:200],
            )
            return

        # Run-time supersede check #2 — if a newer enqueue arrived
        # while we were mid-generation, the row will already be
        # ``superseded`` OR have a different seed (because enqueue
        # writes the new seed). Delete the PNG we just wrote and skip
        # the done-write so the newer job's eventual output wins.
        if await self._is_superseded(toy_id, slot, seed=seed):
            try:
                await asyncio.to_thread(self._delete_png, out_path)
            except Exception:  # noqa: BLE001 -- best-effort
                _logger.warning(
                    "image_gen worker: failed to delete superseded PNG %s",
                    out_path,
                )
            _logger.info(
                "image_gen worker: discarded superseded output toy_id=%s slot=%s",
                toy_id,
                slot,
            )
            return

        # Commit success.
        stored_path = _stored_image_path(toy_id, slot)
        await self._upsert(
            toy_id,
            slot,
            ToyActionStatus.done,
            image_path=stored_path,
            seed=seed,
        )
        await self._emit_status(
            toy_id,
            slot,
            ToyActionStatus.done,
            image_path=stored_path,
        )
        breaker.check_and_record(success=True)

    async def _mark_cancelled_best_effort(
        self,
        toy_id: str,
        slot: str,
        seed: int,
    ) -> None:
        """Write ``failed("interrupted by shutdown")`` on cancellation.

        Best-effort: every step is wrapped so a failure here doesn't
        mask the original :class:`asyncio.CancelledError`. We use
        :func:`asyncio.shield` around the DB write because the
        surrounding cancel-propagation would otherwise abort the upsert
        before it commits, leaving the row stuck in ``running``.
        """
        from ..storage.toy_actions import upsert_status

        def _runner() -> None:
            conn = self._conn_factory()
            try:
                upsert_status(
                    conn,
                    toy_id,
                    slot,
                    ToyActionStatus.failed,
                    error_msg="interrupted by shutdown",
                    seed=seed,
                )
            finally:
                conn.close()

        try:
            await asyncio.shield(asyncio.to_thread(_runner))
        except Exception:  # noqa: BLE001 -- shutdown cleanup must not raise
            _logger.warning(
                "image_gen worker: failed to mark cancelled job toy_id=%s slot=%s",
                toy_id,
                slot,
                exc_info=True,
            )
            return
        try:
            await asyncio.shield(
                self._emit_status(
                    toy_id,
                    slot,
                    ToyActionStatus.failed,
                    error="interrupted by shutdown",
                )
            )
        except Exception:  # noqa: BLE001 -- shutdown cleanup must not raise
            _logger.warning(
                "image_gen worker: failed to emit cancellation envelope toy_id=%s slot=%s",
                toy_id,
                slot,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Storage + filesystem helpers
    # ------------------------------------------------------------------

    def _resolve_pipeline(
        self,
    ) -> Callable[[bytes, str, int, GenerationContext], Awaitable[bytes]]:
        """Return the pipeline callable, honouring the test override."""
        if self._pipeline_override is not None:
            return self._pipeline_override
        # Lazy import so a worker constructed in a degraded boot doesn't
        # immediately pay the pipeline-module import cost (which is
        # cheap, but the lazy posture matches the rest of the
        # subsystem).
        from .pipeline import generate_action

        return generate_action

    def _resolve_composite(
        self,
    ) -> Callable[[bytes, str, int, GenerationContext], Awaitable[bytes]]:
        """Return the composite callable, honouring the test override.

        F.5-3a: the worker dispatches to this when the capability
        gate returns False with a non-env-disabled reason (no CUDA,
        low VRAM, missing checkpoints).
        """
        if self._composite_override is not None:
            return self._composite_override
        # Lazy import so the composite module's rembg / Pillow deps
        # aren't loaded at worker construction time.
        from .composite import composite_action

        return composite_action

    def _probe_capability(self) -> tuple[bool, CapabilityReason, str]:
        """Return the resolved capability, honouring the test override.

        Uses ``check_free_vram=False`` to mirror the request-time
        callers (see :func:`is_image_gen_capable`'s docstring): once
        the boot probe established the hardware fits, mid-flight VRAM
        dips during another sprite's run shouldn't flip the dispatch
        branch.
        """
        if self._capability_probe is not None:
            return self._capability_probe()
        return is_image_gen_capable(check_free_vram=False)

    def _with_conn(
        self,
        action: Callable[[sqlite3.Connection], None],
    ) -> None:
        """Run ``action`` against a fresh DB connection. Closes on return."""
        conn = self._conn_factory()
        try:
            action(conn)
        finally:
            conn.close()

    def _restart_recovery_sync(self) -> int:
        """Synchronous body of :meth:`run_restart_recovery`.

        Sweeps both ``running`` AND ``queued`` rows. ``queued`` covers
        the case where a previous process crashed after enqueueing but
        before the consumer popped — without this catch, the row stays
        ``queued`` across restarts forever because the in-memory queue
        was lost with the process. ``running`` covers the older case:
        a row mid-pipeline at crash time.

        Reads + UPDATEs in a single transaction so a concurrent reader
        (there shouldn't be one — boot is single-threaded — but
        defensive) sees a consistent state.
        """
        from ..storage.toy_actions import upsert_status

        conn = self._conn_factory()
        try:
            rows = conn.execute(
                "SELECT toy_id, slot, seed FROM toy_actions WHERE status IN (?, ?)",
                (
                    ToyActionStatus.running.value,
                    ToyActionStatus.queued.value,
                ),
            ).fetchall()
            for row in rows:
                # ``upsert_status`` is idempotent and validates; a row
                # written before F3's validators landed could have an
                # out-of-vocab slot, so we catch + skip + log rather
                # than crash the whole sweep.
                try:
                    upsert_status(
                        conn,
                        row["toy_id"],
                        row["slot"],
                        ToyActionStatus.failed,
                        error_msg="interrupted by restart",
                        seed=row["seed"],
                    )
                except ValueError as exc:
                    _logger.warning(
                        "restart recovery: skipping invalid row (toy_id=%s slot=%s): %s",
                        row["toy_id"],
                        row["slot"],
                        exc,
                    )
            return len(rows)
        finally:
            conn.close()

    def _load_toy_context_sync(
        self,
        toy_id: str,
    ) -> tuple[GenerationContext, bytes]:
        """Read the toy + persona row and the source-photo bytes.

        Mirrors :func:`toybox.image_gen.__main__._load_toy_context` but
        also reads the file bytes here (the caller is the worker, not
        an interactive CLI, so the read happens inside the same
        thread-pool slot to keep async overhead down).
        """
        import json

        from ..storage.images import on_disk_image_path

        conn = self._conn_factory()
        try:
            row = conn.execute(
                """
                SELECT
                    t.display_name AS toy_display_name,
                    t.image_path   AS image_path,
                    t.tags         AS tags,
                    p.display_name AS persona_display_name
                FROM toys AS t
                LEFT JOIN personas AS p ON p.id = t.persona_id
                WHERE t.id = ? AND t.archived = 0
                LIMIT 1
                """,
                (toy_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise LookupError(f"no live toy with id={toy_id!r}")
        if row["image_path"] is None:
            raise LookupError(f"toy {toy_id!r} has no committed image")
        raw_tags = row["tags"]
        tags: tuple[str, ...]
        if raw_tags:
            try:
                parsed = json.loads(raw_tags)
                tags = tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()
            except (ValueError, TypeError):
                tags = ()
        else:
            tags = ()
        ctx = GenerationContext(
            toy_display_name=str(row["toy_display_name"]),
            persona_display_name=(
                str(row["persona_display_name"]) if row["persona_display_name"] else None
            ),
            tags=tags,
        )
        on_disk = on_disk_image_path(str(row["image_path"]))
        reference_bytes = on_disk.read_bytes()
        return ctx, reference_bytes

    async def _is_superseded(
        self,
        toy_id: str,
        slot: str,
        *,
        seed: int,
    ) -> bool:
        """Return True iff the in-flight job has been superseded.

        Two superseded conditions are checked, both of which can fire
        on the same row:

        1. ``status == "superseded"`` — explicit; either the
           enqueue-time helper flipped the row before writing the new
           ``queued`` row (rare race window), or an external actor
           (test, future API) wrote ``superseded`` directly.
        2. ``status in ("queued", "running")`` AND the row's ``seed``
           differs from this job's ``seed`` — a newer enqueue arrived
           while we were mid-pipeline and overwrote the row with the
           new job's seed (the common path: enqueue's
           ``upsert_status(superseded)`` is immediately followed by
           ``upsert_status(queued, seed=new_seed)``, so by the time
           the in-flight job commits, the persisted seed is the new
           job's, not its own).

        Reading both ``status`` and ``seed`` in a single SELECT keeps
        the recheck cheap.
        """

        def _read(conn: sqlite3.Connection) -> tuple[str, int | None] | None:
            row = conn.execute(
                "SELECT status, seed FROM toy_actions WHERE toy_id = ? AND slot = ?",
                (toy_id, slot),
            ).fetchone()
            if row is None:
                return None
            return str(row["status"]), row["seed"]

        def _runner() -> tuple[str, int | None] | None:
            conn = self._conn_factory()
            try:
                return _read(conn)
            finally:
                conn.close()

        result = await asyncio.to_thread(_runner)
        if result is None:
            # Row was deleted out from under us — treat as superseded
            # so we don't recreate it.
            return True
        status, persisted_seed = result
        if status == ToyActionStatus.superseded.value:
            return True
        # If the row's seed differs from this job's seed, a newer
        # enqueue overwrote the row.
        if persisted_seed is not None and persisted_seed != seed:
            return True
        return False

    async def _upsert(
        self,
        toy_id: str,
        slot: str,
        status: ToyActionStatus,
        *,
        image_path: str | None = None,
        seed: int | None = None,
        error_msg: str | None = None,
    ) -> None:
        """Run :func:`upsert_status` on a worker thread."""
        from ..storage.toy_actions import upsert_status

        def _runner() -> None:
            conn = self._conn_factory()
            try:
                upsert_status(
                    conn,
                    toy_id,
                    slot,
                    status,
                    image_path=image_path,
                    seed=seed,
                    error_msg=error_msg,
                )
            finally:
                conn.close()

        await asyncio.to_thread(_runner)

    async def _mark_failed(
        self,
        toy_id: str,
        slot: str,
        seed: int,
        *,
        error: str,
    ) -> None:
        """Convenience: write ``failed`` + emit the matching WS envelope."""
        await self._upsert(
            toy_id,
            slot,
            ToyActionStatus.failed,
            seed=seed,
            error_msg=error,
        )
        await self._emit_status(
            toy_id,
            slot,
            ToyActionStatus.failed,
            error=error,
        )

    async def _emit_status(
        self,
        toy_id: str,
        slot: str,
        status: ToyActionStatus,
        *,
        image_path: str | None = None,
        error: str | None = None,
    ) -> None:
        """Emit one ``Topic.toy_actions`` envelope with the canonical shape.

        Per the plan §"WS payload — Topic.toy_actions":

        * ``image_path`` is non-null only when ``status == "done"``.
        * ``error`` is non-null only when ``status == "failed"``.
        """
        payload: dict[str, object] = {
            "toy_id": toy_id,
            "slot": slot,
            "status": status.value,
            "image_path": image_path if status is ToyActionStatus.done else None,
            "error": error if status is ToyActionStatus.failed else None,
        }
        try:
            await self._emit(Topic.toy_actions, payload)
        except Exception:  # noqa: BLE001 -- defensive
            _logger.warning(
                "image_gen worker: emit failed (status=%s toy_id=%s slot=%s)",
                status.value,
                toy_id,
                slot,
                exc_info=True,
            )

    @staticmethod
    def _write_png(out_path: Path, png_bytes: bytes) -> None:
        """Write PNG bytes, creating the parent directory if missing."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(png_bytes)

    @staticmethod
    def _delete_png(out_path: Path) -> None:
        """Delete a PNG. Idempotent — missing file is not an error."""
        try:
            out_path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _fresh_seed() -> int:
        """Draw a fresh seed within SQLite's signed-INTEGER range.

        Mirrors :mod:`toybox.image_gen.__main__`'s default seed
        strategy (plan §"Seed strategy" — random per generation,
        persisted with the row), but caps at 63 bits because SQLite's
        INTEGER column is a signed 64-bit value and a full 64-bit
        unsigned draw can overflow with ``OverflowError`` on insert.
        63 bits is still ~9.2e18 distinct seeds — plenty for the
        plan's "different output per regenerate" requirement.
        """
        import secrets

        return secrets.randbits(63)


# ---------------------------------------------------------------------
# Module-level singleton accessors used by the FastAPI lifespan
# ---------------------------------------------------------------------


_worker: ImageGenWorker | None = None


def get_image_gen_worker() -> ImageGenWorker | None:
    """Return the process-wide worker, or ``None`` if not started.

    REST handlers (F5) use this to enqueue jobs without holding a
    direct reference to the worker; the lifespan handler is the
    canonical owner of the start/stop lifecycle.
    """
    return _worker


async def start_image_gen_worker(
    conn_factory: ConnFactory,
    emit: EmitCallable,
    *,
    pipeline: Callable[
        [bytes, str, int, GenerationContext],
        Awaitable[bytes],
    ]
    | None = None,
    composite: Callable[
        [bytes, str, int, GenerationContext],
        Awaitable[bytes],
    ]
    | None = None,
    capability_probe: Callable[[], tuple[bool, CapabilityReason, str]] | None = None,
) -> ImageGenWorker:
    """Construct + start the singleton worker.

    Runs the restart-recovery sweep BEFORE the consumer task starts so
    the boot log shows the recovery line before any new ``running``
    rows are produced. Idempotent — a second call is a no-op and
    returns the existing instance.
    """
    global _worker
    if _worker is not None:
        return _worker
    worker = ImageGenWorker(
        conn_factory,
        emit,
        pipeline=pipeline,
        composite=composite,
        capability_probe=capability_probe,
    )
    recovered = await worker.run_restart_recovery()
    if recovered:
        _logger.info(
            "image_gen worker: restart recovery flipped %d row(s) running→failed",
            recovered,
        )
    await worker.start()
    _worker = worker
    return worker


async def stop_image_gen_worker() -> None:
    """Stop + drop the singleton worker. Idempotent."""
    global _worker
    if _worker is None:
        return
    try:
        await _worker.stop()
    finally:
        _worker = None


def reset_image_gen_worker_for_tests() -> None:
    """Drop the cached worker without awaiting cleanup. Tests only."""
    global _worker
    _worker = None


__all__ = [
    "ConnFactory",
    "EmitCallable",
    "ImageGenWorker",
    "get_image_gen_worker",
    "reset_image_gen_worker_for_tests",
    "start_image_gen_worker",
    "stop_image_gen_worker",
]
