"""Phase Q Step Q3 — LLM-authored element-song corpus generator.

One-shot operator CLI that produces a kid-friendly 4-8 line rhyme for
each of the ~103 elements NOT already covered by Phase M Step M7a's
popular-element backfill set (15 elements), and appends the entries to
``data/songs/manifest.json``. Coverage is per-element (not per-family),
to satisfy Phase Q's 1:1 ``element_id`` → song-reward mapping
(plan-q §3.4 — every element reward picker can find a per-element song
before falling back to family-tier or theme-tier candidates).

Lifecycle separation
--------------------

* **This script** (Q3) authors the structure: prompt building, response
  parsing, manifest insertion, idempotent strip-then-append. Live LLM
  calls require ``--live``; ``--dry-run`` (default) renders synthetic
  lyrics from each element's ``story_seed_hooks`` so the script can be
  exercised end-to-end with NO network access.
* **Step Q7** (operator) runs the live ``--live`` pass off-band and
  commits the resulting ``data/songs/manifest.json`` changes.

Coverage
--------

* Total elements in ``data/elements/elements.json``: 118.
* Skipped (already covered by M7a popular-element songs): 15 — see
  :data:`M7A_POPULAR_ELEMENT_IDS`.
* Authored here: 103.

Output entry shape (one per element, appended to the manifest array)::

    {
        "id": "element-song-<symbol>-<atomic_number>",
        "title": "<LLM-supplied display title>",
        "audio_path": "audio/element-song-<symbol>-<atomic_number>.mp3",
        "duration_seconds": <int, estimated 12-18 by line count>,
        "theme": "silly" | "music",
        "age_band": "3-5",
        "persona_compat": ["periodic_table", "all"],
        "license": "CC-BY-4.0",
        "credit": "LLM-authored, Coqui TTS XTTS-v2 (operator-rendered)",
        "lyrics": "<4-8 newline-separated lines, ≤500 chars>",
        "element_id": "<element.id, e.g. 'au-79'>",
        "family": "<element.family slug, e.g. 'noble_gas'>"
    }

``audio_path`` is REQUIRED for the Phase K K11 Coqui renderer to land
MP3s at the expected on-disk path — even though Q3 doesn't render any
audio, leaving the field blank would break Q7's downstream render step.

Idempotence
-----------

A re-run strips every existing manifest entry whose id starts with
``element-song-`` BEFORE appending the freshly-generated batch. The
M7a 15-element backfill uses different id slugs (``gold-shiny-rhyme``,
``hydrogen-tiny-cheer``, …) so the strip is surgical.

Flags
-----

``--dry-run`` renders the planned JSON to stdout WITHOUT touching the
manifest file and WITHOUT making network calls; synthetic lyrics are
built from each element's ``story_seed_hooks``. Default (no flag) is
the live ``AnthropicClient`` (OAuth+urllib) pass — this is the Q7
operator run.

``--force`` is a no-op tag for the log (idempotent regeneration is
always-on).

``--validate`` re-loads the manifest after the write via the production
:func:`toybox.activities.song_corpus.load_songs` and asserts every new
element-song entry parses cleanly.

``--output PATH`` overrides ``data/songs/manifest.json``.

``--limit N`` restricts the batch to the first N skip-filtered
elements; useful for ``--dry-run --limit 2`` smoke tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import urllib.error
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from toybox.activities.element_corpus import Family
from toybox.activities.song_corpus import Song
from toybox.ai.client import AIMessage, AnthropicClient
from toybox.ai.oauth import OAuthToken, load_token

_logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT: Final[Path] = Path("data/songs/manifest.json")
_DEFAULT_ELEMENTS: Final[Path] = Path("data/elements/elements.json")

# Element ids already covered by M7a's 15-element popular backfill.
# These element_ids are SKIPPED by Q3 so the two batches don't both
# emit a song for the same element. The M7a ids live under different
# slugs (gold-shiny-rhyme, hydrogen-tiny-cheer, …) so the strip-by-
# prefix idempotence below does not touch them.
M7A_POPULAR_ELEMENT_IDS: Final[frozenset[str]] = frozenset(
    {
        "au-79",
        "ag-47",
        "fe-26",
        "he-2",
        "o-8",
        "h-1",
        "ne-10",
        "hg-80",
        "cu-29",
        "u-92",
        "na-11",
        "ca-20",
        "c-6",
        "n-7",
        "cl-17",
    }
)

# Strip-by-prefix idempotence key. A prefix match is safe because M7a
# uses ad-hoc slugs (``gold-shiny-rhyme``) while Q3 uses a structured
# ``element-song-<sym>-<n>`` slug — no Phase K or M entry collides with
# the prefix.
ELEMENT_SONG_ID_PREFIX: Final[str] = "element-song-"

# Constant defaults shared by every entry — match the M7a generator's
# corresponding constants so the two cohorts are wire-shape uniform.
_DEFAULT_AGE_BAND: Final[str] = "3-5"
_DEFAULT_PERSONA_COMPAT: Final[tuple[str, ...]] = ("periodic_table", "all")
_DEFAULT_LICENSE: Final[str] = "CC-BY-4.0"
_DEFAULT_CREDIT: Final[str] = "LLM-authored, Coqui TTS XTTS-v2 (operator-rendered)"

# Theme heuristic: certain ``fun_fact`` keywords push the rhyme toward
# the ``music`` theme (anything glowy / sparkly / shiny reads as
# musical at age 3-5). Otherwise default to ``silly`` so the picker has
# a balanced theme distribution. Matches M7a's hand-applied logic.
_MUSIC_KEYWORDS: Final[tuple[str, ...]] = (
    "glow",
    "glows",
    "shine",
    "shines",
    "shiny",
    "sparkle",
    "sparkles",
    "sparkly",
    "dance",
    "dances",
    "sing",
    "sings",
    "song",
    "music",
    "rhythm",
    "bright",
    "light",
)

_VALID_THEMES: Final[frozenset[str]] = frozenset({"silly", "music"})

_MIN_LYRIC_LINES: Final[int] = 4
_MAX_LYRIC_LINES: Final[int] = 8
_MAX_LYRIC_CHARS: Final[int] = 500
_SECONDS_PER_LINE: Final[int] = 3
_LYRIC_DURATION_FLOOR: Final[int] = 12
_LYRIC_DURATION_CEILING: Final[int] = 18

_MAX_PROMPT_TOKENS: Final[int] = 600

# Per-element LLM retry policy. 429 (rate limit) and 503 (overload /
# upstream unavailable) are the transient classes the operator will
# hit in a 103-element tight loop; everything else fails through to the
# per-element drop path. Backoff doubles (1s, 2s, 4s) up to
# ``_LLM_RETRY_ATTEMPTS`` total tries.
_LLM_RETRY_ATTEMPTS: Final[int] = 3
_LLM_RETRY_BASE_DELAY_SEC: Final[float] = 1.0
_LLM_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({429, 503})

# Loose label-then-value matcher; tolerates leading whitespace,
# colon-or-dash separator, and surrounding markdown chrome (** ** /
# # #). Captures the trimmed value up to end-of-line.
_LABEL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*[*#>\-]*\s*(?P<label>title|theme|lyrics)\s*[:\-]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------
# Element corpus IO
# ---------------------------------------------------------------------


def load_elements(path: Path = _DEFAULT_ELEMENTS) -> list[dict[str, Any]]:
    """Read the element corpus as a list of raw dicts.

    The script does NOT go through :mod:`toybox.activities.element_corpus`'s
    loader because Q3 only needs five fields (id / symbol / name / family
    / fun_fact / story_seed_hooks); skipping the full Pydantic round-trip
    keeps the dry-run path dependency-light and fast.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"elements corpus {path} not found; run from the worktree root"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(
            f"elements corpus {path} must be a JSON array, got {type(raw).__name__}"
        )
    return raw


def select_target_elements(
    elements: list[dict[str, Any]],
    *,
    skip: frozenset[str] = M7A_POPULAR_ELEMENT_IDS,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Filter out M7a-covered ids and optionally cap to ``limit``."""
    targets = [e for e in elements if str(e.get("id", "")) not in skip]
    if limit is not None:
        return targets[:limit]
    return targets


# ---------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------


def pick_theme(element: dict[str, Any]) -> str:
    """Return ``"music"`` if the fun_fact has a musical keyword, else ``"silly"``."""
    fun_fact = str(element.get("fun_fact", "")).lower()
    if any(needle in fun_fact for needle in _MUSIC_KEYWORDS):
        return "music"
    return "silly"


def build_prompt(element: dict[str, Any]) -> str:
    """Build the per-element LLM prompt requesting a 4-8 line rhyme.

    The prompt:

    * Names the element (full name + symbol + atomic number) so the
      model can't drift onto a different element.
    * Quotes the element's ``fun_fact`` so the rhyme is grounded in
      one real-world detail children can already match.
    * Requests three labeled lines: ``title:``, ``theme:``, ``lyrics:``.
      ``lyrics:`` is followed by 4-8 lines of rhyming verse.
    * Warns against element-as-character personification — per commit
      ``ce710fc`` (Phase M template rewrite) the guide mentor is the
      actor, not the element itself. Q3 narration must follow the
      same rule so the song reward parses cleanly when narrated by
      Professor Iridia at runtime.
    """
    name = element.get("name", "the element")
    symbol = element.get("symbol", "?")
    atomic_number = element.get("atomic_number", "?")
    fun_fact = element.get("fun_fact", "")
    theme = pick_theme(element)

    return (
        f"Write a short kid-friendly rhyming song for a 3-5 year old about the "
        f"chemical element {name} (symbol {symbol}, atomic number {atomic_number}).\n"
        f"\n"
        f"Real-world detail to ground the rhyme in:\n"
        f"  {fun_fact}\n"
        f"\n"
        f"Requirements:\n"
        f"  - Exactly 4 to 8 lines of rhyming verse, total under 500 characters.\n"
        f"  - Picturable nouns (balloon, crown, glow stick, leaf) over abstract\n"
        f"    chemistry terms.\n"
        f"  - NO dangerous-behavior framings (no licking / drinking / touching\n"
        f"    radioactive or toxic elements — use 'watch from far away' framing).\n"
        f"  - The element is the SUBJECT of the song. A guide character (an\n"
        f"    adult mentor) may sing about it, but the element itself must NOT\n"
        f"    be personified as a character with a name and personality —\n"
        f"    keep it descriptive ('Iron is strong', not 'Iron says hello').\n"
        f"  - Theme is '{theme}' (silly or music).\n"
        f"\n"
        f"Respond with EXACTLY three labeled lines, no extra prose:\n"
        f"  title: <a short display title, max 10 words>\n"
        f"  theme: {theme}\n"
        f"  lyrics: <first line of rhyme>\n"
        f"  <second line>\n"
        f"  <... up to 8 total lyric lines>\n"
    )


# ---------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------


def parse_llm_response(raw: str, element: dict[str, Any]) -> dict[str, Any]:
    """Extract ``title`` / ``theme`` / ``lyrics`` from the LLM response.

    Tolerates surrounding markdown chrome (``**title**: foo``) and
    leading whitespace. Lyrics are accumulated from the line after the
    ``lyrics:`` label until the response ends or a blank-line sentinel
    appears.

    Validations:

    * ``theme`` must be ``"silly"`` or ``"music"`` (the only two themes
      M7a + Q3 admit per plan §6.6).
    * Lyrics must be 4-8 non-empty lines.
    * Total lyric character count must be ≤500 (matches Song.lyrics
      Pydantic ``max_length``).

    Raises :class:`ValueError` with the offending element id in the
    message on any defect — the caller (``main``) logs at WARN and
    drops the offending entry from the batch.
    """
    element_id = str(element.get("id", "<unknown>"))
    title: str | None = None
    theme: str | None = None
    lyric_lines: list[str] = []
    in_lyrics = False

    for line in raw.splitlines():
        if in_lyrics:
            stripped = line.rstrip()
            if not stripped.strip():
                # Blank line ends the lyrics block (defensive — some LLMs
                # add a trailing newline; some keep going with prose).
                if lyric_lines:
                    break
                continue
            # Strip optional markdown bullet prefix.
            cleaned = re.sub(r"^\s*[*\->]\s*", "", stripped)
            lyric_lines.append(cleaned)
            continue

        match = _LABEL_PATTERN.match(line)
        if match is None:
            continue
        label = match.group("label").lower()
        value = match.group("value").strip().strip("*").strip()
        if label == "title":
            title = value
        elif label == "theme":
            theme = value.lower()
        elif label == "lyrics":
            in_lyrics = True
            if value:
                lyric_lines.append(value)

    if title is None or not title:
        raise ValueError(f"element {element_id}: LLM response missing 'title:' label")
    if theme is None or theme not in _VALID_THEMES:
        raise ValueError(
            f"element {element_id}: LLM theme={theme!r} is not in {sorted(_VALID_THEMES)!r}"
        )
    if len(lyric_lines) < _MIN_LYRIC_LINES or len(lyric_lines) > _MAX_LYRIC_LINES:
        raise ValueError(
            f"element {element_id}: lyric line count {len(lyric_lines)} not in "
            f"[{_MIN_LYRIC_LINES}, {_MAX_LYRIC_LINES}]"
        )
    lyrics_blob = "\n".join(lyric_lines)
    if len(lyrics_blob) > _MAX_LYRIC_CHARS:
        raise ValueError(
            f"element {element_id}: lyrics length {len(lyrics_blob)} > "
            f"{_MAX_LYRIC_CHARS} (Song.lyrics max_length)"
        )

    return {
        "title": title,
        "theme": theme,
        "lyrics": lyrics_blob,
        "lyric_line_count": len(lyric_lines),
    }


# ---------------------------------------------------------------------
# Entry assembly
# ---------------------------------------------------------------------


def _song_id_for(element: dict[str, Any]) -> str:
    symbol = str(element.get("symbol", "")).lower()
    atomic_number = element.get("atomic_number")
    return f"{ELEMENT_SONG_ID_PREFIX}{symbol}-{atomic_number}"


def _audio_path_for(song_id: str) -> str:
    return f"audio/{song_id}.mp3"


def _estimate_duration(line_count: int) -> int:
    """Estimate duration as ``line_count * _SECONDS_PER_LINE``, clamped."""
    estimate = line_count * _SECONDS_PER_LINE
    return max(_LYRIC_DURATION_FLOOR, min(_LYRIC_DURATION_CEILING, estimate))


def build_entry(element: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """Assemble a manifest entry from an element + parsed LLM response.

    The field ORDER matches the Phase K K11 + M7a authored entries so
    a diff against the existing manifest is readable. ``element_id``
    and ``family`` are the Phase Q Step Q1 additions; see
    :class:`toybox.activities.song_corpus.Song` for the schema.
    """
    song_id = _song_id_for(element)
    line_count = int(parsed.get("lyric_line_count", _MIN_LYRIC_LINES))
    return {
        "id": song_id,
        "title": parsed["title"],
        "audio_path": _audio_path_for(song_id),
        "duration_seconds": _estimate_duration(line_count),
        "theme": parsed["theme"],
        "age_band": _DEFAULT_AGE_BAND,
        "persona_compat": list(_DEFAULT_PERSONA_COMPAT),
        "license": _DEFAULT_LICENSE,
        "credit": _DEFAULT_CREDIT,
        "lyrics": parsed["lyrics"],
        "element_id": element["id"],
        "family": element["family"],
    }


def synthetic_response(element: dict[str, Any]) -> str:
    """Build a synthetic LLM response from ``story_seed_hooks`` for ``--dry-run``.

    The result is byte-identical to a well-formed live LLM reply so
    :func:`parse_llm_response` exercises the same code-path on both
    branches — the only difference is the source of the text (a
    hand-templated rhyme vs. Claude).
    """
    name = element.get("name", "Element")
    hooks = element.get("story_seed_hooks") or []
    theme = pick_theme(element)

    # Pad / truncate hooks to land in [_MIN_LYRIC_LINES, _MAX_LYRIC_LINES].
    # Slice to _MAX_LYRIC_LINES (not _MIN) so the dry-run rehearsal exercises
    # the upper-bound parse path; real LLM output frequently lands in the
    # 5-8 line range and the dry-run path would otherwise never see it.
    rendered_hooks = [str(h).replace("{name}", str(name)) for h in hooks][:_MAX_LYRIC_LINES]
    while len(rendered_hooks) < _MIN_LYRIC_LINES:
        rendered_hooks.append(f"{name} is fun to learn about today.")

    lyrics_block = "\n".join(rendered_hooks[:_MAX_LYRIC_LINES])
    return (
        f"title: A Song About {name}\n"
        f"theme: {theme}\n"
        f"lyrics: {lyrics_block.splitlines()[0]}\n"
        + "\n".join(lyrics_block.splitlines()[1:])
        + "\n"
    )


# ---------------------------------------------------------------------
# Manifest IO
# ---------------------------------------------------------------------


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    """Read the existing manifest as a JSON array (mirrors M7a's helper)."""
    if not path.exists():
        raise FileNotFoundError(
            f"output manifest {path} does not exist; cannot append element-song "
            f"entries. Run from the worktree root."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(
            f"output manifest {path} is not a JSON array (got "
            f"{type(payload).__name__}); refusing to overwrite"
        )
    return payload


def strip_existing(
    manifest: list[dict[str, Any]], prefix: str = ELEMENT_SONG_ID_PREFIX
) -> list[dict[str, Any]]:
    """Return ``manifest`` with every entry whose id starts with ``prefix`` removed."""
    return [s for s in manifest if not str(s.get("id", "")).startswith(prefix)]


def _write_payload(path: Path, payload: list[dict[str, Any]]) -> None:
    """Atomically write the manifest. 2-space indent, trailing newline, UTF-8.

    Writes to a sibling ``<name>.json.tmp`` first and then atomically
    swaps via ``os.replace``. ``os.replace`` is atomic on POSIX and
    atomic on Windows when both paths sit on the same volume (which is
    guaranteed here because the temp file is the source path with a
    swapped suffix). This prevents a Ctrl-C / OOM / power-loss event
    mid-write from truncating ``data/songs/manifest.json`` and crashing
    the production backend boot at ``load_songs()``.
    """
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    # ``with_suffix`` replaces the final suffix, so for ``manifest.json``
    # this yields ``manifest.json.tmp`` sitting on the same volume.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------
# Live LLM driver (Q7 path)
# ---------------------------------------------------------------------


async def _call_llm(client: AnthropicClient, prompt: str) -> str:
    """Single-shot text completion with retry-on-transient; returns the raw text.

    Wraps ``client.complete_text`` in a bounded exponential-backoff
    retry loop for the two transient HTTP statuses that show up in a
    103-element tight loop: ``429`` (rate limit) and ``503`` (overload /
    upstream unavailable). Non-retryable HTTPErrors (auth expiry, 400,
    500, etc.) re-raise immediately so the caller can drop the entry.
    Other exceptions (``URLError``, timeouts, anything else) bubble up
    to the per-element try/except in ``_entry_for_element_async`` so a
    network blip on one element doesn't crash the whole batch.
    """
    last_exc: urllib.error.HTTPError | None = None
    for attempt in range(1, _LLM_RETRY_ATTEMPTS + 1):
        try:
            response = await client.complete_text(
                [AIMessage(role="user", content=prompt)],
                max_tokens=_MAX_PROMPT_TOKENS,
            )
            return response.text
        except urllib.error.HTTPError as exc:
            if exc.code not in _LLM_RETRYABLE_STATUSES:
                raise
            last_exc = exc
            if attempt >= _LLM_RETRY_ATTEMPTS:
                break
            # Exponential backoff: 1s → 2s → 4s. Async-sleep so the
            # event loop can service other tasks if the caller ever
            # parallelises this. ``2 ** (attempt - 1)`` is the standard
            # exponential form so attempt=1 → 1s, attempt=2 → 2s, …
            delay = _LLM_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
            _logger.info(
                "LLM HTTP %d on attempt %d/%d; retrying in %.1fs",
                exc.code,
                attempt,
                _LLM_RETRY_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
    # ``last_exc`` is always set when we exit the loop without returning;
    # the ``raise`` is unreachable with last_exc=None but mypy can't tell.
    assert last_exc is not None
    raise last_exc


def _load_live_token() -> OAuthToken:
    """Load the OAuth bearer; raise SystemExit if absent.

    Mirrors the production capability gate's failure mode — a missing
    token is a clean operator-actionable error, not a stacktrace.
    """
    token = load_token()
    if token is None:
        raise SystemExit(
            "live mode: no OAuth token at ~/.toybox/secrets.json (override via "
            "TOYBOX_SECRETS_PATH). Run the capability check first, or use "
            "--dry-run for a no-network rehearsal."
        )
    return token


# ---------------------------------------------------------------------
# Validation (post-write)
# ---------------------------------------------------------------------


def _validate_post_write(path: Path, *, expected_count: int) -> None:
    """Re-load via the production song-corpus loader and assert all Q3 entries land."""
    from toybox.activities.song_corpus import clear_song_cache, load_songs

    clear_song_cache()
    songs = load_songs()
    loaded = [s for s in songs if s.id.startswith(ELEMENT_SONG_ID_PREFIX)]
    if len(loaded) != expected_count:
        raise SystemExit(
            f"--validate: expected {expected_count} element-song entries to load, "
            f"got {len(loaded)}. Check {path} for shape errors and re-run."
        )
    for song in loaded:
        if song.element_id is None:
            raise SystemExit(f"--validate: entry {song.id} has no element_id")
        if song.family is None:
            raise SystemExit(f"--validate: entry {song.id} has no family")
        if not song.audio_path.startswith("audio/"):
            raise SystemExit(
                f"--validate: entry {song.id} audio_path={song.audio_path!r} "
                f"missing 'audio/' prefix"
            )
    _logger.info(
        "--validate: %d element-song entries loaded cleanly through "
        "toybox.activities.song_corpus.load_songs",
        len(loaded),
    )


# ---------------------------------------------------------------------
# Per-entry generation
# ---------------------------------------------------------------------


async def _entry_for_element_async(
    element: dict[str, Any],
    *,
    live_client: AnthropicClient | None,
) -> dict[str, Any] | None:
    """Generate one manifest entry from an element. Returns None on any failure.

    The dry-run path (``live_client is None``) uses
    :func:`synthetic_response` so no network call is made. The live path
    runs the prompt through ``_call_llm`` (which already retries on 429
    / 503). Any exception escaping ``_call_llm`` — auth expiry, network
    blip, timeout, non-retryable HTTPError, anything else — is caught
    here and turned into a per-element drop so a single transient
    failure can't crash the 103-element run and lose all prior work.
    The end-of-loop write happens regardless; the operator can re-run
    with ``--force`` to fill the gaps.
    """
    prompt = build_prompt(element)
    if live_client is None:
        raw = synthetic_response(element)
    else:
        try:
            raw = await _call_llm(live_client, prompt)
        except Exception as exc:  # noqa: BLE001 — operator-actionable per-element drop
            _logger.warning(
                "element %s: live LLM call failed (%s: %s) — dropping",
                element.get("id"),
                type(exc).__name__,
                exc,
            )
            return None

    try:
        parsed = parse_llm_response(raw, element)
    except ValueError as exc:
        _logger.warning("%s — dropping entry", exc)
        return None

    try:
        entry = build_entry(element, parsed)
    except (KeyError, ValueError) as exc:
        _logger.warning(
            "element %s: build_entry failed (%s) — dropping",
            element.get("id"),
            exc,
        )
        return None

    # Pydantic round-trip — defends against any field-shape drift that
    # _LABEL_PATTERN didn't catch (e.g. an injection payload in the
    # title text). Failing here drops the entry instead of poisoning
    # the manifest.
    try:
        Song(
            id=entry["id"],
            title=entry["title"],
            audio_path=entry["audio_path"],
            duration_seconds=entry["duration_seconds"],
            theme=entry["theme"],
            age_band=entry["age_band"],
            persona_compat=tuple(entry["persona_compat"]),
            license=entry["license"],
            credit=entry["credit"],
            lyrics=entry["lyrics"],
            element_id=entry["element_id"],
            family=Family(entry["family"]),
        )
    except ValidationError as exc:
        _logger.warning(
            "element %s: pydantic Song validation failed (%s) — dropping",
            element.get("id"),
            exc.errors()[0] if exc.errors() else exc,
        )
        return None

    return entry


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Q3 element-song manifest entries (one per element NOT "
            "covered by M7a). Use --dry-run for a no-network smoke run; "
            "default mode calls Claude live via OAuth (Q7 operator pass)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Render synthetic lyrics from story_seed_hooks, print the planned "
            "entries to stdout, exit. NO network calls, NO manifest write. "
            "Use for smoke tests; Q7 operator run omits this flag to call "
            "Claude live."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Manifest path. Default: data/songs/manifest.json.",
    )
    parser.add_argument(
        "--elements",
        type=Path,
        default=_DEFAULT_ELEMENTS,
        help="Element corpus path. Default: data/elements/elements.json.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Idempotent regeneration is always-on (existing element-song-* "
            "entries are stripped before appending); this flag just tags the "
            "run in the log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load via the production song_corpus loader "
            "and assert all element-song entries load cleanly."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Author only the first N skip-filtered elements (smoke testing).",
    )
    return parser.parse_args(argv)


async def _run_main_async(args: argparse.Namespace) -> int:
    """Async driver. One event loop for the whole batch.

    Per-element calls share this loop so we avoid the per-element
    ``asyncio.run`` overhead (and the noisy "Event loop is closed"
    teardown messages the per-call form sometimes emits on Windows).
    Cleanup semantics on Ctrl-C: ``KeyboardInterrupt`` is caught at the
    sync ``main()`` boundary; partial results gathered into ``entries``
    so far are written before re-raising so the operator can resume
    incrementally with ``--force``.
    """
    output: Path = args.output
    live_client: AnthropicClient | None = None

    if args.dry_run:
        _logger.info("--dry-run: synthetic lyrics from story_seed_hooks (no network)")
    else:
        live_client = AnthropicClient(_load_live_token())
        _logger.info("live: AnthropicClient ready (OAuth+urllib path)")

    elements = load_elements(args.elements)
    targets = select_target_elements(elements, limit=args.limit)
    _logger.info(
        "elements total=%d, skipped_m7a=%d, targets=%d",
        len(elements),
        len(elements) - len(targets) if args.limit is None else len(M7A_POPULAR_ELEMENT_IDS),
        len(targets),
    )

    entries: list[dict[str, Any]] = []
    dropped = 0
    try:
        for element in targets:
            entry = await _entry_for_element_async(element, live_client=live_client)
            if entry is None:
                dropped += 1
                continue
            entries.append(entry)
            _logger.info("authored %s (%s)", entry["id"], entry["title"])
    except KeyboardInterrupt:
        # Persist whatever we got so the operator can re-run --force for
        # the rest instead of starting from zero. Re-raise so the
        # process still exits with the conventional interrupt code.
        _logger.warning(
            "KeyboardInterrupt received after %d/%d targets — flushing partial batch",
            len(entries) + dropped,
            len(targets),
        )
        if not args.dry_run:
            _flush_partial(output, entries, args)
        raise

    _logger.info(
        "generation summary: targets=%d, succeeded=%d, dropped=%d",
        len(targets),
        len(entries),
        dropped,
    )

    if args.dry_run:
        # Pure preview: print the planned batch as a JSON array; do NOT
        # touch the on-disk manifest, do NOT read it either (so the
        # script runs against a clean checkout without a prior write).
        sys.stdout.write(json.dumps(entries, indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0

    existing = _load_manifest(output)
    pre_count = len(existing)
    stripped = strip_existing(existing)
    stripped_count = pre_count - len(stripped)
    merged = stripped + entries
    _logger.info(
        "summary: pre=%d, removed_existing_element_song=%d, generated=%d, post=%d, force=%s",
        pre_count,
        stripped_count,
        len(entries),
        len(merged),
        args.force,
    )

    _write_payload(output, merged)
    _logger.info("wrote %d entries to %s", len(merged), output)

    if args.validate:
        _validate_post_write(output, expected_count=len(entries))

    return 0


def _flush_partial(
    output: Path, entries: list[dict[str, Any]], args: argparse.Namespace
) -> None:
    """Best-effort partial-batch write triggered by KeyboardInterrupt.

    Mirrors the end-of-run write path but never raises: an interrupted
    operator should see the partial result, not a stacktrace on top of
    a stacktrace. ``_load_manifest`` failures (missing manifest, JSON
    corruption) are logged and the flush is skipped — the goal is to
    preserve progress, not to recover from an unrelated disk fault.
    """
    try:
        existing = _load_manifest(output)
    except (FileNotFoundError, ValueError) as exc:
        _logger.warning(
            "partial-flush: skipped (could not load existing manifest %s: %s)",
            output,
            exc,
        )
        return
    stripped = strip_existing(existing)
    merged = stripped + entries
    try:
        _write_payload(output, merged)
    except OSError as exc:
        _logger.warning("partial-flush: write to %s failed (%s)", output, exc)
        return
    _logger.warning(
        "partial-flush: wrote %d total entries (%d new) to %s; "
        "re-run with --force to fill remaining elements",
        len(merged),
        len(entries),
        output,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    try:
        return asyncio.run(_run_main_async(args))
    except KeyboardInterrupt:
        # Conventional 130 = 128 + SIGINT. The partial flush already
        # ran inside ``_run_main_async`` before the re-raise; here we
        # just convert the interrupt into a clean exit code.
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
