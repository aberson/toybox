"""WebSocket topic registry.

The dotted string values are the on-the-wire identifiers used in every
:class:`toybox.ws.envelope.Envelope`. Member names use underscores so
they remain valid Python identifiers; the wire shape always goes through
``.value``.

Step 8 will add the broadcast machinery; Step 4 only needs the registry
so :func:`toybox.core.listening.set_mode` can build typed envelopes.
"""

from __future__ import annotations

from enum import StrEnum


class Topic(StrEnum):
    """Canonical ws topic identifiers."""

    listening_mode = "listening.mode"
    system = "system"
    triggers_invalidate = "triggers.invalidate"
    activity = "activity"
    activity_state = "activity.state"
    transcript = "transcript"
    # Step 24: operator metrics dashboard. The publisher snapshots every
    # 30 s; the parent UI's Operator tab subscribes for ws-pushed
    # updates and falls back to polling /api/metrics if the socket is
    # unavailable.
    metrics = "metrics"
    # Phase F Step F4: per-toy action-sprite generation status.
    # Parent-scope only — child tokens never receive these envelopes.
    # Envelope payload shape: ``{toy_id, slot, status, image_path?,
    # error?}`` where ``image_path`` is non-null only on
    # ``status == "done"`` and ``error`` is non-null only on
    # ``status == "failed"``.
    toy_actions = "toy_actions"


__all__ = ["Topic"]
