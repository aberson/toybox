"""Pydantic models for the offline activity generator.

The :class:`Activity` model is the public output of
:func:`toybox.activities.generator.generate`. Field shapes align with
the SQLite schema in ``src/toybox/db/migrations/0001_initial.sql``
(tables ``activities`` and ``activity_steps``) so a future persistence
step can serialize without translation gymnastics. Both models are
``frozen=True`` to match :class:`toybox.ws.envelope.Envelope`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActivityStep(BaseModel):
    """One step in a 5-step linear activity.

    Mirrors the ``activity_steps`` columns: ``seq`` (1-indexed in the
    DB, but we use ``step_index`` in-memory and 0-index it because
    Python lists are 0-indexed and that matches how callers iterate
    ``activity.steps``), ``body`` (the spoken/displayed text), ``sfx``,
    and ``expected_action``. ``current`` lives at runtime, not on
    generated output, and is therefore omitted here.
    """

    model_config = ConfigDict(frozen=True)

    step_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    sfx: str | None = None
    expected_action: str | None = None


class Activity(BaseModel):
    """A generated 5-step activity.

    Field shapes echo the ``activities`` table where possible. Fields
    that are runtime-only (e.g. ``state``, ``session_id``,
    ``created_at``) are NOT set by the generator — a persistence
    layer adds them when an Activity is enqueued for a session.

    The ``metadata`` dict carries the load-bearing inputs to the
    Phase D step 19 ``signature`` computation: the template id, the
    sorted slot values, and the hour bucket label.

    .. warning::

       ``frozen=True`` does NOT deep-freeze nested mutable values.
       The ``metadata`` dict itself is rebindable-via-Pydantic only,
       but its contents (the ``dict`` and any nested mutable values
       like ``list``) can technically still be mutated in-place.
       Callers MUST treat ``metadata`` as read-only after
       construction — Phase D's ``signature`` reads
       ``metadata["slot_values"]``, and post-hoc mutation would
       silently change signatures. The generator emits
       ``slot_values`` as an immutable ``tuple[str, ...]`` to make
       the most load-bearing entry safe from accidental mutation.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    persona_id: str | None = None
    title: str = Field(min_length=1)
    steps: list[ActivityStep] = Field(min_length=5, max_length=5)
    version: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Activity", "ActivityStep"]
