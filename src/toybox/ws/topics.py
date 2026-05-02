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


__all__ = ["Topic"]
