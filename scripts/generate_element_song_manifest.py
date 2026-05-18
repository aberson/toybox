"""Phase M Step M7a — author element-themed song manifest entries.

One-shot CLI that appends ~25 hand-authored song lyrics + metadata to
``data/songs/manifest.json``. Each entry is a short kid-friendly rhyme
(4-8 lines) for one element family OR one popular individual element.
No audio is rendered here — the operator runs the existing
``scripts/generate_song_corpus.py`` (Phase K Step K11) out-of-band; the
song corpus loader logs WARN for missing audio and continues per the
documented graceful path.

Coverage (25 total, per phase-m-plan §5.7):

Element families (10) — one song per :class:`toybox.activities.element_corpus.Family`
    * ``noble_gases_drift_quiet`` — noble gases
    * ``halogens_make_friends`` — halogens
    * ``alkali_metals_go_zoom`` — alkali metals
    * ``alkaline_earths_keep_strong`` — alkaline earths
    * ``transition_metals_shiny_song`` — transition metals
    * ``post_transition_metals_bendy`` — post-transition metals
    * ``metalloids_in_between`` — metalloids
    * ``nonmetals_everywhere`` — nonmetals
    * ``lanthanides_glow_soft`` — lanthanides
    * ``actinides_radiate_far`` — actinides

Popular individual elements (15)
    * ``gold-shiny-rhyme`` — au-79
    * ``silver-spoon-song`` — ag-47
    * ``iron-strong-rhyme`` — fe-26
    * ``helium-balloon-float`` — he-2
    * ``oxygen-breath-song`` — o-8
    * ``hydrogen-tiny-cheer`` — h-1
    * ``neon-glow-rhyme`` — ne-10
    * ``mercury-silver-river`` — hg-80
    * ``copper-penny-shine`` — cu-29
    * ``uranium-glow-song`` — u-92
    * ``sodium-salt-sparkle`` — na-11
    * ``calcium-bone-cheer`` — ca-20
    * ``carbon-best-buddy`` — c-6
    * ``nitrogen-air-song`` — n-7
    * ``chlorine-pool-rhyme`` — cl-17

Authoring style mirrors M5/M6: Python literals + JSON emitter,
idempotent. Existing element-song entries (matching the M7a id allow-list
below) are stripped before the new batch appends. CLI flags:
``--dry-run`` / ``--force`` / ``--validate`` / ``--output``.

Field order matches the 50 pre-existing Phase K K11 entries in
``data/songs/manifest.json`` and the :class:`toybox.activities.song_corpus.Song`
model:

    id, title, audio_path, duration_seconds, theme, age_band,
    persona_compat, license, credit, lyrics

Age-appropriate framing
-----------------------

Every lyric:

* Targets ``age_band: "3-5"`` (Child B is 4yo pre-reader; lyrics will be
  sung aloud — Child A 6yo's M9-M12 SEL content lives elsewhere).
* Avoids dangerous-behavior framings (no "lick the mercury",
  no "play with sodium", no "drink the chlorine"). Mercury's lyric
  explicitly uses a "watch from far away" / "behind the glass" framing.
* Uses picturable nouns (crown, balloon, penny, salt, bone, glow stick,
  star) over abstract chemistry terms.
* Avoids chemistry inaccuracies — e.g. helium (not neon) makes balloons
  float; chlorine cleans pools (not "tastes nice"); uranium glows in
  pictures (not "in your hand").
* Carries ``persona_compat: ["periodic_table", "all"]`` so the song
  surfaces both under Professor Iridia activities (Phase M) AND as a
  universal reward eligible across all personas.
* Theme is ``"silly"`` or ``"music"`` per §6.6 — no new science theme.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Final

_logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT: Final[Path] = Path("data/songs/manifest.json")

# Idempotence: this exact id allow-list is stripped from the manifest
# before re-emitting. A prefix-match would over-strip pre-existing
# Phase K entries (e.g. ``silly-noodle-dance``), so we list each M7a
# id explicitly. The list MUST stay in sync with the ``id`` fields in
# ``_SONGS`` below — the validator at write-time asserts the same.
_M7A_SONG_IDS: Final[frozenset[str]] = frozenset(
    {
        # Family songs (10).
        "noble-gases-drift-quiet",
        "halogens-make-friends",
        "alkali-metals-go-zoom",
        "alkaline-earths-keep-strong",
        "transition-metals-shiny-song",
        "post-transition-metals-bendy",
        "metalloids-in-between",
        "nonmetals-everywhere",
        "lanthanides-glow-soft",
        "actinides-radiate-far",
        # Individual-element songs (15).
        "gold-shiny-rhyme",
        "silver-spoon-song",
        "iron-strong-rhyme",
        "helium-balloon-float",
        "oxygen-breath-song",
        "hydrogen-tiny-cheer",
        "neon-glow-rhyme",
        "mercury-silver-river",
        "copper-penny-shine",
        "uranium-glow-song",
        "sodium-salt-sparkle",
        "calcium-bone-cheer",
        "carbon-best-buddy",
        "nitrogen-air-song",
        "chlorine-pool-rhyme",
    }
)


def _entry(
    *,
    song_id: str,
    title: str,
    duration_seconds: int,
    theme: str,
    lyrics: str,
) -> dict[str, Any]:
    """Build a manifest entry with the Phase K K11 field shape + M7a defaults.

    All M7a entries share ``age_band="3-5"``, ``persona_compat=["periodic_table", "all"]``,
    ``license="CC-BY-4.0"``, and ``credit="Coqui TTS XTTS-v2 (operator-rendered)"``,
    so the helper handles them once. Theme is per-entry (``"silly"`` or
    ``"music"``) and duration_seconds is per-entry (10-25).
    """
    return {
        "id": song_id,
        "title": title,
        "audio_path": f"audio/{song_id}.mp3",
        "duration_seconds": duration_seconds,
        "theme": theme,
        "age_band": "3-5",
        "persona_compat": ["periodic_table", "all"],
        "license": "CC-BY-4.0",
        "credit": "Coqui TTS XTTS-v2 (operator-rendered)",
        "lyrics": lyrics,
    }


# ---------------------------------------------------------------------
# Hand-authored songs
# ---------------------------------------------------------------------
#
# Conventions enforced across every entry:
#  * age_band="3-5", persona_compat=["periodic_table", "all"], license,
#    credit — set by :func:`_entry`.
#  * theme is "silly" or "music" only (per phase-m-plan §6.6).
#  * duration_seconds estimated at ~2s per line of 8-12 syllables — a
#    4-line rhyme runs ~10-12s; an 8-line rhyme runs ~20-25s.
#  * Lyrics are newline-separated (\n in the literal) so the Coqui TTS
#    line-breaks read naturally when rendered.
#  * Element-family songs name the family explicitly so the title
#    makes the subject unmistakable (Child B can't read; parent reads
#    the title aloud and the lyric reinforces the topic).
#  * Individual-element songs name the element in the first line.

_SONGS: Final[list[dict[str, Any]]] = [
    # =================================================================
    # ELEMENT FAMILY SONGS (10)
    # =================================================================
    _entry(
        song_id="noble-gases-drift-quiet",
        title="Noble Gases Drift Quiet",
        duration_seconds=16,
        theme="music",
        lyrics=(
            "Noble gases drift so quiet,\n"
            "Never grab a buddy, never start a riot.\n"
            "Helium floats and neon glows,\n"
            "Argon hides where nobody knows!"
        ),
    ),
    _entry(
        song_id="halogens-make-friends",
        title="Halogens Make Friends",
        duration_seconds=16,
        theme="music",
        lyrics=(
            "Halogens love to make a friend,\n"
            "Sticking close until the end.\n"
            "Chlorine, fluorine, bromine too,\n"
            "Grab a buddy, that's what they do!"
        ),
    ),
    _entry(
        song_id="alkali-metals-go-zoom",
        title="Alkali Metals Go Zoom",
        duration_seconds=16,
        theme="silly",
        lyrics=(
            "Alkali metals zip and zoom,\n"
            "Soft as butter, fast as broom!\n"
            "Sodium, potassium, lithium too,\n"
            "Quick to share what's inside of you!"
        ),
    ),
    _entry(
        song_id="alkaline-earths-keep-strong",
        title="Alkaline Earths Keep Strong",
        duration_seconds=18,
        theme="music",
        lyrics=(
            "Alkaline earths are strong and sure,\n"
            "Calcium keeps your bones secure.\n"
            "Magnesium makes the green leaves bright,\n"
            "Earth metals helping day and night!"
        ),
    ),
    _entry(
        song_id="transition-metals-shiny-song",
        title="Transition Metals Shiny Song",
        duration_seconds=20,
        theme="music",
        lyrics=(
            "Transition metals shine so bright,\n"
            "Iron, copper, silver light.\n"
            "Gold and zinc and nickel too,\n"
            "Bendy, shiny, strong and true.\n"
            "Coins and crowns and bridges tall,\n"
            "Transition metals build them all!"
        ),
    ),
    _entry(
        song_id="post-transition-metals-bendy",
        title="Post-Transition Metals Bendy",
        duration_seconds=18,
        theme="silly",
        lyrics=(
            "Post-transition metals bend and bow,\n"
            "Soft and shiny, take a bow!\n"
            "Aluminum cans and tin foil bright,\n"
            "Lead so heavy it sinks out of sight!"
        ),
    ),
    _entry(
        song_id="metalloids-in-between",
        title="Metalloids In Between",
        duration_seconds=18,
        theme="silly",
        lyrics=(
            "Metalloids are in between,\n"
            "Half-and-half, the in-between scene!\n"
            "Silicon makes computer chips hum,\n"
            "Boron, arsenic — here they come!"
        ),
    ),
    _entry(
        song_id="nonmetals-everywhere",
        title="Nonmetals Everywhere",
        duration_seconds=20,
        theme="music",
        lyrics=(
            "Nonmetals are everywhere you breathe,\n"
            "Oxygen, nitrogen, never leave!\n"
            "Carbon in the tree and in the bread,\n"
            "Hydrogen sparkles overhead.\n"
            "Soft and squishy, gas or stone,\n"
            "Nonmetals make a happy home!"
        ),
    ),
    _entry(
        song_id="lanthanides-glow-soft",
        title="Lanthanides Glow Soft",
        duration_seconds=18,
        theme="music",
        lyrics=(
            "Lanthanides glow soft and slow,\n"
            "Inside your phone they help things go!\n"
            "Cerium, neodymium, names so long,\n"
            "Tiny helpers, mighty strong!"
        ),
    ),
    _entry(
        song_id="actinides-radiate-far",
        title="Actinides Radiate Far",
        duration_seconds=18,
        theme="music",
        lyrics=(
            "Actinides hum a sleepy tune,\n"
            "Hidden away inside the room.\n"
            "Uranium, thorium, deep and rare,\n"
            "Scientists watch them way over there!"
        ),
    ),
    # =================================================================
    # POPULAR INDIVIDUAL ELEMENTS (15)
    # =================================================================
    _entry(
        song_id="gold-shiny-rhyme",
        title="Gold is Shiny",
        duration_seconds=14,
        theme="silly",
        lyrics=(
            "Gold is shiny, gold is bright,\n"
            "Gold makes crowns that catch the light!\n"
            "Gold won't rust, it won't turn green,\n"
            "Prettiest metal you've ever seen!"
        ),
    ),
    _entry(
        song_id="silver-spoon-song",
        title="Silver Spoon Song",
        duration_seconds=14,
        theme="music",
        lyrics=(
            "Silver, silver, soft and white,\n"
            "Spoons and forks that catch the light!\n"
            "Pretty as the moon at night,\n"
            "Silver shines and feels just right!"
        ),
    ),
    _entry(
        song_id="iron-strong-rhyme",
        title="Iron is Strong",
        duration_seconds=14,
        theme="silly",
        lyrics=(
            "Iron is strong, iron is tough,\n"
            "Bridges, nails, and stuff and stuff!\n"
            "Inside your blood it helps you go,\n"
            "Iron keeps you on the flow!"
        ),
    ),
    _entry(
        song_id="helium-balloon-float",
        title="Helium Balloon Float",
        duration_seconds=12,
        theme="silly",
        lyrics=(
            "Helium, helium, light as air,\n"
            "Lifts a balloon up everywhere!\n"
            "Hold the string and watch it rise,\n"
            "Helium soars into the skies!"
        ),
    ),
    _entry(
        song_id="oxygen-breath-song",
        title="Oxygen Breath Song",
        duration_seconds=14,
        theme="music",
        lyrics=(
            "Breathe in, breathe out, oxygen-O,\n"
            "Everywhere we breathe it goes!\n"
            "In the wind and in the trees,\n"
            "Oxygen brings the gentle breeze."
        ),
    ),
    _entry(
        song_id="hydrogen-tiny-cheer",
        title="Hydrogen Tiny Cheer",
        duration_seconds=12,
        theme="silly",
        lyrics=(
            "Hydrogen, hydrogen, smallest of all,\n"
            "Tiniest atom, light and small!\n"
            "Stars are made of hydrogen too,\n"
            "Sparkle, sparkle, just like you!"
        ),
    ),
    _entry(
        song_id="neon-glow-rhyme",
        title="Neon Glow Rhyme",
        duration_seconds=12,
        theme="silly",
        lyrics=(
            "Neon, neon, orange and pink,\n"
            "Glowing signs that never blink!\n"
            "Light it up and watch it shine,\n"
            "Neon's a glow that feels just fine!"
        ),
    ),
    _entry(
        song_id="mercury-silver-river",
        title="Mercury Silver River",
        duration_seconds=18,
        theme="music",
        lyrics=(
            "Mercury, mercury, behind the glass,\n"
            "Silver river we watch as it flows past!\n"
            "Never to touch, just look from far,\n"
            "Mercury shines like a silver star.\n"
            "Pretty to see in the thermometer line,\n"
            "Mercury sparkles, but it stays in time!"
        ),
    ),
    _entry(
        song_id="copper-penny-shine",
        title="Copper Penny Shine",
        duration_seconds=14,
        theme="silly",
        lyrics=(
            "Copper penny, copper bright,\n"
            "Orange-red and full of light!\n"
            "Inside wires, inside pots,\n"
            "Copper helps connect the dots!"
        ),
    ),
    _entry(
        song_id="uranium-glow-song",
        title="Uranium Glow Song",
        duration_seconds=16,
        theme="music",
        lyrics=(
            "Uranium, uranium, sleepy and slow,\n"
            "Locked in a vault with a quiet glow.\n"
            "Scientists keep it safe and sound,\n"
            "Far away from kids around!"
        ),
    ),
    _entry(
        song_id="sodium-salt-sparkle",
        title="Sodium Salt Sparkle",
        duration_seconds=14,
        theme="silly",
        lyrics=(
            "Sodium hides inside the salt,\n"
            "Sprinkled on bread is sodium's vault!\n"
            "Tiny crystals, sparkly white,\n"
            "Sodium makes the dinner right!"
        ),
    ),
    _entry(
        song_id="calcium-bone-cheer",
        title="Calcium Bone Cheer",
        duration_seconds=14,
        theme="music",
        lyrics=(
            "Calcium, calcium, strong and white,\n"
            "Builds your bones up tall and tight!\n"
            "Milk and cheese and broccoli too,\n"
            "Calcium grows the bones of you!"
        ),
    ),
    _entry(
        song_id="carbon-best-buddy",
        title="Carbon Best Buddy",
        duration_seconds=16,
        theme="music",
        lyrics=(
            "Carbon, carbon, friend to all,\n"
            "In a tree and in a ball!\n"
            "Diamonds shine, the pencil writes,\n"
            "Carbon's busy day and night.\n"
            "Bread and bug and bone and you,\n"
            "Carbon's in the whole world too!"
        ),
    ),
    _entry(
        song_id="nitrogen-air-song",
        title="Nitrogen Air Song",
        duration_seconds=14,
        theme="music",
        lyrics=(
            "Nitrogen, nitrogen, most of the air,\n"
            "Floating gently everywhere!\n"
            "In the breeze and in the sky,\n"
            "Nitrogen is drifting by!"
        ),
    ),
    _entry(
        song_id="chlorine-pool-rhyme",
        title="Chlorine Pool Rhyme",
        duration_seconds=14,
        theme="silly",
        lyrics=(
            "Chlorine, chlorine, in the pool,\n"
            "Keeps the water bright and cool!\n"
            "Smells a bit, but does its job,\n"
            "Chlorine scrubs each tiny glob!"
        ),
    ),
]


# ---------------------------------------------------------------------
# Self-checks (run at import time so a stale id list fails LOUDLY)
# ---------------------------------------------------------------------


def _assert_song_ids_match_allow_list() -> None:
    """Authored ids must equal :data:`_M7A_SONG_IDS`.

    A drift between the two means a re-run would either over-strip
    (existing entry missed from allow-list) or under-strip (allow-list
    entry not in the new batch). Fail at module import so the dev
    notices before they get to `--validate`.
    """
    authored = {s["id"] for s in _SONGS}
    if authored != _M7A_SONG_IDS:
        missing = _M7A_SONG_IDS - authored
        extra = authored - _M7A_SONG_IDS
        raise AssertionError(
            "_SONGS id set does not match _M7A_SONG_IDS allow-list; "
            f"missing={sorted(missing)!r}, extra={sorted(extra)!r}"
        )


_assert_song_ids_match_allow_list()


# ---------------------------------------------------------------------
# Load / strip / write helpers (mirror M5/M6 pattern)
# ---------------------------------------------------------------------


def _load_existing(path: Path) -> list[dict[str, Any]]:
    """Read the existing song manifest as a JSON array.

    Mirrors :func:`generate_shrink_journey_templates._load_existing`
    structurally but the manifest is a top-level array (not a wrapped
    ``{"intent": ..., "templates": [...]}`` object).
    """
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append element "
            f"song entries. Run from the worktree root."
        )
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError(
            f"output file {path} is not a JSON array (got "
            f"{type(payload).__name__}); refusing to overwrite"
        )
    return payload


def _strip_m7a_entries(songs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the input list with every M7a element-song entry removed.

    Idempotence: a re-run strips the previous batch by id allow-list
    BEFORE appending the new batch. Allow-list (not prefix) so the
    strip is surgical and never accidentally clobbers a Phase K entry
    whose id happens to share a substring with an M7a id.
    """
    return [s for s in songs if str(s.get("id", "")) not in _M7A_SONG_IDS]


def _write_payload(path: Path, payload: list[dict[str, Any]]) -> None:
    """Persist with the same indent + trailing newline shape as the K11 manifest."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_post_write(path: Path, *, expected_m7a: int) -> None:
    """Re-load via the production loader and assert all M7a entries loaded.

    Mirrors the M5/M6 validator pattern but goes through
    :func:`toybox.activities.song_corpus.load_songs` instead of the
    template loader. Audio-missing WARN log lines are expected per
    plan §5.7 and are NOT a failure — the loader retains entries even
    when ``data/songs/audio/<id>.mp3`` is absent.
    """
    # Local import: keep song_corpus deps out of the module-import path so
    # `--dry-run` works without ever touching the loader.
    from toybox.activities.song_corpus import clear_song_cache, load_songs

    clear_song_cache()
    songs = load_songs()
    loaded_m7a = [s for s in songs if s.id in _M7A_SONG_IDS]
    if len(loaded_m7a) != expected_m7a:
        raise SystemExit(
            f"--validate: expected {expected_m7a} M7a element-song entries "
            f"to load, got {len(loaded_m7a)}. Check {path} for shape errors "
            f"and re-run."
        )
    _logger.info(
        "--validate: %d M7a element-song entries loaded cleanly through "
        "toybox.activities.song_corpus.load_songs",
        len(loaded_m7a),
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append ~25 hand-authored element-themed song entries to "
            "data/songs/manifest.json (Phase M Step M7a)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merged JSON to stdout and exit; do not write the file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=("File to append to. Default: data/songs/manifest.json."),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Idempotent regeneration is always-on (existing M7a entries "
            "are stripped before appending); this flag just tags the run "
            "in the log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load via the production song_corpus loader "
            "and assert all M7a entries load cleanly."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    output: Path = args.output

    existing = _load_existing(output)
    pre_count = len(existing)

    stripped = _strip_m7a_entries(existing)
    stripped_count = pre_count - len(stripped)

    new_songs = list(_SONGS)
    merged = stripped + new_songs

    post_count = len(merged)
    _logger.info(
        "summary: pre=%d, removed_existing_m7a=%d, generated=%d, post=%d, force=%s",
        pre_count,
        stripped_count,
        len(new_songs),
        post_count,
        args.force,
    )

    if args.dry_run:
        sys.stdout.write(json.dumps(merged, indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0

    _write_payload(output, merged)
    _logger.info("wrote %d songs to %s", post_count, output)

    if args.validate:
        _validate_post_write(output, expected_m7a=len(new_songs))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
