"""Phase K Step K14 — shared interjection-step builder.

Single source of truth for the persisted shape of an interjection step
(song or joke). Called by every surface that inserts an interjection
into an activity:

* **K14 Surface B (embedded)** — at advance-time the engine picks a
  corpus entry matching ``template.recommended_themes`` and writes the
  step via this helper before persisting.
* **K14 Surface E (endings)** — at activity-creation time the engine
  appends an extra step after the template's last step when the
  template declared an ``ending_step``; that step is built here.
* **K15 Surface P (parent-insert)** — ``POST /api/activities/{id}/insert-{joke,song}``
  inserts at ``current_step + 1`` using this helper.
* **K15 Surface S (spontaneity)** — the advance-time roll-driven hook
  inserts an interjection via this helper before returning.

Per ``.claude/rules/code-quality.md`` §2 ("One source of truth for
data-shape constants"), all four surfaces MUST go through
:func:`build_interjection_step`. Re-implementing the dict shape in any
caller is a regression — tests assert ``is`` identity on the produced
step (same builder, same dict shape) so a future re-duplication fails
CI.

Persisted shape mirrors :func:`toybox.api.activities._persist_activity`
and :func:`toybox.api.activities._insert_next_step`'s INSERT contract —
the row writer expects:

* ``seq`` (int): 1-indexed position to insert.
* ``body`` (str): rendered display text (joke setup; song title).
* ``kind`` (``"song"`` | ``"joke"``): step-kind discriminator the K12
  StepCard dispatches on.
* ``metadata`` (dict): per-step blob written to
  ``activity_steps.metadata_json``. Always carries
  ``interjection`` (one of :class:`InterjectionKind`) + ``source_id``
  (the corpus entry id) per K13's migration 0016 comment. Per-kind
  fields:

  * song: ``audio_url`` (``/api/static/songs/audio/<id>.mp3``) +
    ``song_id`` (the corpus id, repeated for kiosk-side reading
    symmetry with the K13 standalone shape).
  * joke: ``punchline`` (post-slot-fill substituted) + ``joke_id``
    (the corpus id, repeated for kiosk-side reading symmetry with
    the K13 standalone shape).

The audio URL prefix is intentionally re-derived here from the same
``/api/static/songs/audio`` literal the API uses; the API layer keeps
its private alias for telemetry, this module keeps its own copy so the
``activities.interjection`` module is import-free of ``toybox.api`` (no
circular imports — the API depends on activities, not the other way).
A grep for the URL prefix surfaces both call sites at once, per the
code-quality §1 grep-all-consumers discipline.

Backward-compat: the returned dict shape is a SUPERSET of the
``steps[]`` items :func:`_persist_activity` already consumes (it adds
the K13 ``kind`` + ``metadata`` keys, which the writer already
reads). Callers building plain-text steps continue to pass the legacy
shape; this helper is the only call site that emits the K13 +
interjection fields together.
"""

from __future__ import annotations

from typing import Any, Final

from .generator import render_with_slot_fills
from .interjections import InterjectionKind
from .joke_corpus import Joke, apply_toy_substitution
from .song_corpus import Song

# ---------------------------------------------------------------------
# Audio URL prefix
# ---------------------------------------------------------------------

# Mirrors :data:`toybox.api.activities._SONG_AUDIO_URL_PREFIX`. Kept
# here so this module doesn't import from the API layer; a grep for
# either name lands on both call sites. Future CDN cutover is a
# two-line edit + a grep verification per code-quality §1.
_SONG_AUDIO_URL_PREFIX: Final[str] = "/api/static/songs/audio"


# Type alias for the corpus entry the picker hands us. Public so test
# fixtures + K15 parent-insert handlers can annotate cleanly.
CorpusEntry = Joke | Song


# ---------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------


def build_interjection_step(
    *,
    interjection: InterjectionKind,
    corpus_entry: CorpusEntry,
    slot_fills: dict[str, str],
    seq: int,
    toy_display_name: str | None = None,
) -> dict[str, Any]:
    """Return the persisted-row dict for one interjection step.

    Arguments:
        interjection: One of the four :class:`InterjectionKind` members.
            Stamped onto ``metadata.interjection`` so the labeled-events
            learning loop, parent telemetry, and the kiosk's
            narration-attribution layer can distinguish the four
            surfaces (K14 ends + embedded; K15 parent + spontaneity).
        corpus_entry: The :class:`Joke` or :class:`Song` the picker
            chose. The function dispatches on its concrete type rather
            than a discriminator field so a future third corpus type
            (e.g. ``Riddle``) surfaces as an explicit TypeError rather
            than a silent shape mismatch.
        slot_fills: The activity's persisted slot map. Used to render
            ``{role_name}`` / ``{toy}`` / ``{slot}`` placeholders in the
            corpus entry's display text (joke setup + punchline; song
            title). Pass ``{}`` for entries authored without
            placeholders.
        seq: The 1-indexed position to insert. Caller's responsibility
            to ensure no collision with existing rows (the version-
            bumped transaction in the API caller is the gate).
        toy_display_name: Optional toy display name for the joke's
            ``{toy}`` placeholder, threaded through
            :func:`apply_toy_substitution` so an
            ``optional_toy_slot=False`` joke or a missing toy degrades
            cleanly (defense-in-depth strip).

    Returns:
        A dict with the keys
        :func:`toybox.api.activities._persist_activity` consumes:

        * ``seq``, ``body``, ``sfx`` (always None for interjections —
          they own their audio surface or have none), ``expected_action``
          (None), ``current`` (False — the caller bumps current later
          atomically with the version bump), ``action_slot`` (None),
          ``step_id`` (None — interjections have no template-step id),
          ``choices_rendered`` (None — interjections never branch),
          ``kind`` ("song" or "joke"), ``metadata`` (the dict described
          in the module docstring).

    Raises:
        TypeError: ``corpus_entry`` is not a :class:`Joke` or
            :class:`Song`. The error surfaces as a programming bug
            rather than a silent default to keep the code-quality §2
            single-source-of-truth invariant readable.
    """
    if isinstance(corpus_entry, Song):
        # Render the title through slot fills so a K15 parent-insert
        # picking a song whose title carries a role placeholder still
        # substitutes. Song titles in the K11 corpus don't carry
        # placeholders today, but this keeps the renderer wired in case
        # the corpus authors evolve.
        body = render_with_slot_fills(corpus_entry.title, slot_fills)
        metadata: dict[str, Any] = {
            "interjection": interjection.value,
            "source_id": corpus_entry.id,
            "song_id": corpus_entry.id,
            "audio_url": f"{_SONG_AUDIO_URL_PREFIX}/{corpus_entry.id}.mp3",
        }
        kind: str = "song"
    elif isinstance(corpus_entry, Joke):
        # Joke setup + punchline both pass through the toy-slot
        # substitution helper first (handles optional_toy_slot=False,
        # missing toy, etc.), then through slot_fills for any non-toy
        # placeholders in the joke text (rare today, kept symmetric).
        setup, punchline = apply_toy_substitution(corpus_entry, toy_display_name)
        body = render_with_slot_fills(setup, slot_fills)
        rendered_punchline = render_with_slot_fills(punchline, slot_fills)
        metadata = {
            "interjection": interjection.value,
            "source_id": corpus_entry.id,
            "joke_id": corpus_entry.id,
            "punchline": rendered_punchline,
        }
        kind = "joke"
    else:
        # Defensive: a future caller passing a non-corpus type would
        # otherwise silently drop into one of the branches above (if
        # both isinstance() returns False) and emit a malformed step
        # missing the required keys. Fail loud instead.
        raise TypeError(
            f"build_interjection_step: corpus_entry must be Joke or Song, "
            f"got {type(corpus_entry).__name__}"
        )

    return {
        "seq": seq,
        "body": body,
        "sfx": None,
        "expected_action": None,
        "current": False,
        "action_slot": None,
        "step_id": None,
        "choices_rendered": None,
        "kind": kind,
        "metadata": metadata,
    }


__all__ = ["CorpusEntry", "build_interjection_step"]
