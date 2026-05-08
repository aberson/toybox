"""CRUD helpers for the ``toy_actions`` table.

Mirrors :mod:`toybox.storage.images` in posture: typed dataclass return
values, sqlite3 connection injected by the caller (no FastAPI imports),
path-traversal hardening on ``toy_id``. Migration that creates the
table is ``0005_toy_actions.sql``; status values are pinned in
:class:`toybox.image_gen.models.ToyActionStatus`; the canonical 10-slot
vocabulary is :data:`toybox.image_gen.models.ACTION_SLOTS`.

Public surface:

* :func:`upsert_status` — idempotent ``INSERT ... ON CONFLICT DO UPDATE``.
* :func:`list_for_toy` — returns rows for ALL 10 slots in canonical
  order; missing slots are synthesized as ``not_started`` placeholders
  so the parent UI grid renders consistently before any jobs enqueue.
* :func:`get_image_path` — quick path lookup, ``None`` when missing.
* :func:`delete_for_toy_archived` — wipe rows for a toy on soft-archive
  (the toys.archived = 1 path doesn't trigger the FK cascade); the PNG
  files on disk are intentionally LEFT in place per plan §Out.

Path-traversal posture: every public helper validates ``toy_id`` against
the UUIDv4 regex before touching the DB or the filesystem. The toys
table only ever stores UUIDv4 values today, but the validator means a
future schema change can't accidentally turn ``toy_id`` into a path
segment that escapes ``data/images/toy_actions/``. Same defensive
posture as :func:`toybox.storage.images.committed_dir`'s subdir
whitelist.

NOT_STARTED placeholders: :func:`list_for_toy` returns
``ToyActionRow`` instances with ``status = ToyActionStatus.not_started``
for slots that have no DB row. The synthesized rows carry
``image_path = None``, ``seed = None``, ``error_msg = None``, and
``updated_at = ""``. The storage layer NEVER persists ``not_started``
(every persisted status passes through :func:`upsert_status` with a
worker-supplied real status); the placeholder exists purely to give
the UI grid a stable 10-row shape.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from typing import Final

from ..image_gen.models import ACTION_SLOTS, ToyActionRow, ToyActionStatus

_logger = logging.getLogger(__name__)

# UUIDv4 regex (case-insensitive). Accepts both the hyphenated form
# (``str(uuid.uuid4())``) and the 32-char hex form (``uuid.uuid4().hex``)
# because ``api.toys.post_confirm`` and ``storage.images`` mint toy ids
# via ``.hex`` while activities + the original toy_actions tests use
# the hyphenated form. Both encodings are equally safe as path
# segments — what matters is that the pattern rejects path-traversal
# payloads like ``../foo``. Mirrored in ``toybox.image_gen.__main__``.
_UUID4_RE: Final[re.Pattern[str]] = re.compile(
    r"^("
    r"[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    r")$",
    re.IGNORECASE,
)


def _validate_toy_id(toy_id: str) -> None:
    """Reject any ``toy_id`` that isn't a canonical UUIDv4.

    Raises:
        ValueError: When ``toy_id`` doesn't match the UUIDv4 pattern
            (including the empty string and any path-traversal payload
            like ``"../foo"``).
    """
    if not isinstance(toy_id, str) or not _UUID4_RE.match(toy_id):
        raise ValueError(f"toy_id {toy_id!r} is not a valid UUIDv4")


def _validate_slot(slot: str) -> None:
    """Reject any ``slot`` outside the canonical
    :data:`ACTION_SLOTS` vocabulary.

    Raises:
        ValueError: When ``slot`` is not in ``ACTION_SLOTS``.
    """
    if slot not in ACTION_SLOTS:
        raise ValueError(
            f"slot {slot!r} is not in ACTION_SLOTS (got {sorted(ACTION_SLOTS)!r})"
        )


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 ``Z``-suffixed string.

    Matches the format the migration runner uses for ``applied_at``
    (see :mod:`toybox.db.migrations`); keeping the wire format
    consistent across columns simplifies log diff/grep across rows.
    """
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _row_to_dataclass(row: sqlite3.Row) -> ToyActionRow:
    """Convert a sqlite3 row into a :class:`ToyActionRow`."""
    return ToyActionRow(
        toy_id=row["toy_id"],
        slot=row["slot"],
        status=ToyActionStatus(row["status"]),
        image_path=row["image_path"],
        seed=row["seed"],
        error_msg=row["error_msg"],
        updated_at=row["updated_at"],
    )


def upsert_status(
    conn: sqlite3.Connection,
    toy_id: str,
    slot: str,
    status: ToyActionStatus,
    *,
    image_path: str | None = None,
    error_msg: str | None = None,
    seed: int | None = None,
) -> ToyActionRow:
    """Idempotent upsert of one ``(toy_id, slot)`` row.

    Sets ``updated_at`` to the current UTC ISO-8601 string. Uses
    ``INSERT ... ON CONFLICT(toy_id, slot) DO UPDATE`` so the worker
    can call this freely on every status transition without an
    existence check.

    Returns the row as a :class:`ToyActionRow` after the upsert.

    Raises:
        ValueError: When ``toy_id`` isn't a UUIDv4 or ``slot`` is
            outside :data:`ACTION_SLOTS`. We also reject
            ``ToyActionStatus.not_started`` here because that's a
            UI-only placeholder synthesized by :func:`list_for_toy`,
            never persisted.
    """
    _validate_toy_id(toy_id)
    _validate_slot(slot)
    if status is ToyActionStatus.not_started:
        raise ValueError(
            "ToyActionStatus.not_started is a UI-only placeholder; "
            "do not persist it",
        )

    updated_at = _now_iso()
    status_str = status.value
    with conn:
        conn.execute(
            """
            INSERT INTO toy_actions
                (toy_id, slot, status, image_path, seed, error_msg, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(toy_id, slot) DO UPDATE SET
                status     = excluded.status,
                image_path = excluded.image_path,
                seed       = excluded.seed,
                error_msg  = excluded.error_msg,
                updated_at = excluded.updated_at
            """,
            (toy_id, slot, status_str, image_path, seed, error_msg, updated_at),
        )

    row = conn.execute(
        "SELECT * FROM toy_actions WHERE toy_id = ? AND slot = ?",
        (toy_id, slot),
    ).fetchone()
    # Defensive: the upsert above guarantees a row exists, but a row
    # factory misconfiguration would surface here as a clear error
    # rather than a silent ``None`` propagating into the dataclass.
    if row is None:  # pragma: no cover — should be unreachable
        raise RuntimeError(
            f"upsert_status returned no row for ({toy_id!r}, {slot!r})"
        )
    return _row_to_dataclass(row)


def list_for_toy(
    conn: sqlite3.Connection,
    toy_id: str,
) -> list[ToyActionRow]:
    """Return rows for ALL 10 slots in :data:`ACTION_SLOTS` order.

    Slots without a DB row are synthesized as
    ``ToyActionRow(toy_id, slot, status=ToyActionStatus.not_started,
    image_path=None, seed=None, error_msg=None, updated_at="")``
    so the parent UI's 2x5 grid always has 10 cells regardless of
    how many jobs have enqueued.

    The synthesized rows are NEVER persisted — :func:`upsert_status`
    rejects ``not_started`` explicitly. They exist only on the
    return path of this helper.

    Raises:
        ValueError: When ``toy_id`` isn't a UUIDv4.
    """
    _validate_toy_id(toy_id)
    rows = conn.execute(
        "SELECT * FROM toy_actions WHERE toy_id = ?",
        (toy_id,),
    ).fetchall()
    by_slot: dict[str, ToyActionRow] = {}
    for row in rows:
        try:
            dataclass_row = _row_to_dataclass(row)
        except ValueError as exc:
            # An out-of-vocab status value in the DB shouldn't crash the
            # whole grid — log + skip so the UI sees the slot as
            # "not_started" and the operator can re-trigger generation.
            _logger.warning(
                "list_for_toy: skipping row with invalid status (%s, %s): %s",
                toy_id,
                row["slot"],
                exc,
            )
            continue
        if dataclass_row.slot not in ACTION_SLOTS:
            # Out-of-vocab slot in the DB shouldn't crash the grid —
            # log + skip so the canonical slot still shows the
            # ``not_started`` placeholder. Operators can use the
            # warning to trace the bad write.
            _logger.warning(
                "list_for_toy: skipping row with unknown slot (toy_id=%s slot=%r)",
                toy_id,
                dataclass_row.slot,
            )
            continue
        by_slot[dataclass_row.slot] = dataclass_row

    out: list[ToyActionRow] = []
    for slot in ACTION_SLOTS:
        if slot in by_slot:
            out.append(by_slot[slot])
        else:
            out.append(
                ToyActionRow(
                    toy_id=toy_id,
                    slot=slot,
                    status=ToyActionStatus.not_started,
                    image_path=None,
                    seed=None,
                    error_msg=None,
                    updated_at="",
                )
            )
    return out


def get_image_path(
    conn: sqlite3.Connection,
    toy_id: str,
    slot: str,
) -> str | None:
    """Return the persisted ``image_path`` for one slot, or ``None``.

    Returns ``None`` when:

    * No row exists for ``(toy_id, slot)``.
    * The row exists but ``image_path`` is ``NULL`` (e.g. status is
      ``queued`` / ``running`` / ``failed`` — the worker only
      populates the path on ``done``).

    Raises:
        ValueError: When ``toy_id`` isn't a UUIDv4 or ``slot`` is
            outside :data:`ACTION_SLOTS`.
    """
    _validate_toy_id(toy_id)
    _validate_slot(slot)
    row = conn.execute(
        "SELECT image_path FROM toy_actions WHERE toy_id = ? AND slot = ?",
        (toy_id, slot),
    ).fetchone()
    if row is None:
        return None
    path = row["image_path"]
    return path if path else None


def delete_for_toy_archived(
    conn: sqlite3.Connection,
    toy_id: str,
) -> int:
    """Delete every ``toy_actions`` row for ``toy_id``.

    Called on the soft-archive code path
    (``UPDATE toys SET archived = 1``) which doesn't trigger the FK
    ``ON DELETE CASCADE`` on the parent toy. The hard-delete path
    (DELETE FROM toys) does trigger the cascade, so this helper is
    redundant there; calling it is still safe (returns 0).

    The PNG files under
    ``data/images/toy_actions/<toy_id>/<slot>.png`` are intentionally
    LEFT in place per plan §Out — cleanup is operator-driven, not
    automatic. The DB rows being gone is sufficient: the parent UI
    won't list the toy, and the kiosk's
    ``onError``-hides-element handler renders a 404 sprite as
    "no sprite for this slot" gracefully.

    Returns:
        Count of rows deleted.

    Raises:
        ValueError: When ``toy_id`` isn't a UUIDv4.
    """
    _validate_toy_id(toy_id)
    with conn:
        cur = conn.execute(
            "DELETE FROM toy_actions WHERE toy_id = ?",
            (toy_id,),
        )
        deleted = cur.rowcount
    return int(deleted) if deleted is not None else 0


__all__ = [
    "delete_for_toy_archived",
    "get_image_path",
    "list_for_toy",
    "upsert_status",
]
