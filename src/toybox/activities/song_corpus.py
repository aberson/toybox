"""Phase K Step K11 — song corpus loader + theme tagging + seeded picker.

The bundled song corpus lives in ``data/songs/manifest.json`` (root-
relative, with ``TOYBOX_DATA_DIR`` env override matching
:mod:`toybox.storage.images`'s ``_data_root`` precedent and the K10
:mod:`toybox.activities.joke_corpus` shape). Audio files live under
``data/songs/audio/`` and are rendered one-shot via
``scripts/generate_song_corpus.py`` — they are NOT shipped through this
module's loader (the loader only validates the manifest + reports audio
presence; missing audio is INFO/WARN, never a load-time failure).

Manifest entry shape::

    {
        "id": <kebab-slug>,
        "title": <display title>,
        "audio_path": "audio/<id>.mp3",
        "duration_seconds": <int 1-30>,
        "theme": <one of the 12 :class:`toybox.activities.themes.Theme`>,
        "age_band": "3-5" | "6-8" | "9-12",
        "persona_compat": ["all"] | [<persona_id>, ...],
        "license": "CC-BY-4.0" | similar,
        "credit": "Coqui TTS XTTS-v2 (operator-rendered)",
        "lyrics": <short rhyme; TTS input>
    }

Public surface:

* :class:`Song` — frozen Pydantic model. Same convention as :class:`Joke`
  so K15 parent-insert / K12 kiosk wire shape drop in without a type
  translation.
* :func:`load_songs` — returns the validated tuple of songs; cached.
  **Audio existence handling**: at load time the loader probes
  ``<data_root>/songs/<audio_path>`` and logs INFO if present, WARN if
  missing. The entry is retained either way — K12 kiosk-side handles a
  404 gracefully so unrendered entries don't kill the surface.
* :func:`pick_song` — deterministic seeded pick after optional
  ``(age_band, persona_id, theme, require_audio)`` filtering. The
  ``require_audio`` flag (default False) filters to only entries whose
  audio file exists on disk — production callers (K12+) flip it on
  to avoid suggesting a song the kiosk would 404 on; tests + K11
  itself leave it False.
* :func:`clear_song_cache` — test hook (mirrors
  :func:`toybox.activities.joke_corpus.clear_joke_cache`).

Single source of truth: theme membership lives in
:class:`toybox.activities.themes.Theme`. Per code-quality.md §2 we
NEVER redeclare theme names in this module or in
``data/songs/manifest.json`` — the JSON values are read through
``Theme(value)`` so a stale string fails LOUDLY at load time.

Security defense-in-depth per security.md "Treat fetched external
content as data, not instructions": entries containing
``<system-reminder>`` or ``ignore prior instructions``
(case-insensitive) are rejected at load time. The shipped corpus is
operator-authored and low-risk, but the gate prevents accidental
ingest of a payload-bearing entry through future tooling (manifest-
editor PRs, eval imports). Lyrics are TTS input — an injection there
would render into an .mp3, but defense-in-depth applies regardless.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .element_corpus import ELEMENT_ID_REGEX, Family
from .themes import Theme

# ---------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------

AgeBand = Literal["3-5", "6-8", "9-12"]

# Tuple, not set, so iteration order is stable for tests and for the
# parent UI's age-band picker (Phase K future surface).
AGE_BANDS: Final[tuple[AgeBand, ...]] = ("3-5", "6-8", "9-12")
_VALID_AGE_BANDS: Final[frozenset[str]] = frozenset(AGE_BANDS)

# "all" sentinel means the entry is compatible with every persona.
PERSONA_COMPAT_ALL: Final[str] = "all"

# Kebab-slug per phase-k-plan.md §2 "Corpus entry ID format":
# lowercase letters / digits, hyphen-separated, no leading or trailing
# hyphen. Same shape as the in-plan examples ``space-rhyme-01`` and
# ``why-chicken``.
_KEBAB_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Path resolution mirrors storage.images._data_root + joke_corpus._data_root:
# env override for test fixtures, default ``Path("data")`` relative to
# the process cwd (the project root in production).
_DATA_ROOT_ENV: Final[str] = "TOYBOX_DATA_DIR"
_DEFAULT_DATA_ROOT: Final[Path] = Path("data")
_SONGS_SUBDIR: Final[str] = "songs"
_MANIFEST_FILENAME: Final[str] = "manifest.json"

# Audio path convention. ``audio_path`` is relative to ``data/songs/``,
# must NOT contain ``..`` (traversal guard), and ships in the
# ``audio/`` subdirectory by convention. The prefix check is a soft
# convention — the validator only enforces "starts with ``audio/``";
# the traversal check is the hard security gate.
_AUDIO_PATH_PREFIX: Final[str] = "audio/"

# duration_seconds bounds per phase-k-plan.md §1: songs are short
# (5-25s authored; we cap at 30 for headroom and at 1 for a non-zero
# floor — Pydantic Field(gt=0, le=30) would also reject 0 but using
# explicit bounds matches the joke_corpus pattern of "check before
# Pydantic so the error message names the field").
_MIN_DURATION_SECONDS: Final[int] = 1
_MAX_DURATION_SECONDS: Final[int] = 30

# Prompt-injection substrings. Case-insensitive matching, hence
# .casefold() comparison at validation time. Keep this list short and
# obvious — a longer list means more false positives for an operator
# writing legitimate kid-friendly lyrics about robots following
# instructions.
_INJECTION_NEEDLES: Final[tuple[str, ...]] = (
    "<system-reminder>",
    "ignore prior instructions",
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Song model
# ---------------------------------------------------------------------


class Song(BaseModel):
    """A single corpus entry. Frozen so the cached tuple is safe to share.

    ``theme`` is the :class:`Theme` enum member (not a plain string)
    so callers can do ``song.theme is Theme.space`` without a string
    comparison — code-quality.md §2 "tests assert ``is``, not ``==``".

    ``persona_compat`` is a tuple, not a list, so the frozen model is
    truly immutable. The JSON shape declares an array; the loader
    coerces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    audio_path: str = Field(min_length=1, max_length=200)
    duration_seconds: int = Field(gt=0, le=_MAX_DURATION_SECONDS)
    theme: Theme
    age_band: AgeBand
    persona_compat: tuple[str, ...] = Field(min_length=1)
    license: str = Field(min_length=1, max_length=64)
    credit: str = Field(min_length=1, max_length=200)
    lyrics: str = Field(min_length=1, max_length=500)
    element_id: str | None = Field(default=None, pattern=ELEMENT_ID_REGEX)
    family: Family | None = None


# ---------------------------------------------------------------------
# Path + cache
# ---------------------------------------------------------------------


def _data_root() -> Path:
    """Root for the bundled data tree; env-overrideable for test fixtures."""
    raw = os.environ.get(_DATA_ROOT_ENV)
    return Path(raw) if raw else _DEFAULT_DATA_ROOT


def _songs_dir() -> Path:
    return _data_root() / _SONGS_SUBDIR


def songs_audio_root() -> Path:
    """Public accessor for ``<data_root>/songs/audio`` (K13 static mount).

    Used by the FastAPI app factory to mount the directory as a
    ``StaticFiles`` route serving ``/api/static/songs/audio/<id>.mp3``.
    Mirrors :func:`toybox.storage.images.images_root` — a thin public
    helper around the resolved on-disk path so the static-mount path
    has ONE source of truth (per code-quality §2). Env-overrideable via
    ``TOYBOX_DATA_DIR`` like the rest of the bundled data tree.
    """
    return _songs_dir() / "audio"


def _manifest_path() -> Path:
    return _songs_dir() / _MANIFEST_FILENAME


def _audio_file_path(audio_path: str) -> Path:
    """Resolve an entry's ``audio_path`` against the songs dir."""
    return _songs_dir() / audio_path


# Cache: keyed on the resolved manifest path so a same-process change
# of TOYBOX_DATA_DIR (test monkeypatch) produces a fresh load.
_SONG_CACHE: dict[Path, tuple[Song, ...]] = {}

# Phase Q Step Q5: bucket caches for the element-tier and family-tier
# reward picks. Built once per corpus-load (same path key as
# :data:`_SONG_CACHE` so test monkeypatch reuses the same lifecycle).
# Per pick the consumer does an O(1) dict lookup + a small filter over
# the bucket. Per code-quality.md §2 the bucket VALUES are entries
# from the same :data:`_SONG_CACHE` tuple — identity is preserved so
# tests can assert ``picked is _SONG_CACHE[path][i]`` if needed.
_SONGS_BY_ELEMENT_ID: dict[Path, dict[str, list[Song]]] = {}
_SONGS_BY_FAMILY: dict[Path, dict[Family, list[Song]]] = {}


def clear_song_cache() -> None:
    """Drop the in-process song cache (test hook).

    Production callers do not need to call this — the cache is keyed
    on the resolved path so an env change automatically forces a
    re-read.

    Phase Q Step Q5: also invalidates the per-element_id and
    per-family bucket caches so a same-process change of
    ``TOYBOX_DATA_DIR`` produces freshly-rebuilt buckets the next
    time :func:`pick_song` is called with ``element_id`` /
    ``family_hint``.
    """
    _SONG_CACHE.clear()
    _SONGS_BY_ELEMENT_ID.clear()
    _SONGS_BY_FAMILY.clear()


# ---------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------


def _check_injection(song_id: str, text: str, *, field: str) -> None:
    """Reject prompt-injection payloads per security.md."""
    haystack = text.casefold()
    for needle in _INJECTION_NEEDLES:
        if needle in haystack:
            raise ValueError(
                f"song {song_id!r} {field}: injection payload {needle!r} detected; "
                f"reject per security.md (defense-in-depth)"
            )


def _validate_audio_path(song_id: str, audio_path: object) -> str:
    """Audio path must be a relative string, no ``..``, starts with ``audio/``.

    The traversal guard is the hard security check — an entry like
    ``"../../../etc/passwd.mp3"`` would otherwise let a malicious
    manifest probe outside ``data/songs/``. The ``audio/`` prefix is
    a soft convention enforced for consistency with the bundled
    layout.
    """
    if not isinstance(audio_path, str) or not audio_path:
        raise ValueError(f"song {song_id!r} audio_path missing or empty: {audio_path!r}")
    if audio_path.startswith("/") or (len(audio_path) > 1 and audio_path[1] == ":"):
        # Absolute POSIX path or Windows drive-letter path.
        raise ValueError(
            f"song {song_id!r} audio_path {audio_path!r} must be relative, got absolute"
        )
    if ".." in Path(audio_path).parts:
        raise ValueError(
            f"song {song_id!r} audio_path {audio_path!r} contains '..' (traversal guard)"
        )
    if not audio_path.startswith(_AUDIO_PATH_PREFIX):
        raise ValueError(
            f"song {song_id!r} audio_path {audio_path!r} must start with "
            f"{_AUDIO_PATH_PREFIX!r} (convention)"
        )
    return audio_path


def _validate_raw_entry(raw: dict[str, object], *, seen_ids: set[str]) -> Song:
    """Coerce one raw dict into a :class:`Song`. Raises ValueError on any defect.

    Validations exercised by tests in
    ``tests/unit/test_song_corpus.py``:

    * unknown theme value → ``Theme(value)`` raises ``ValueError``
      with the offending value in the message; we re-raise as-is.
    * unknown age_band → explicit check before Pydantic so the
      ``"age_band"`` token appears in the error message.
    * duplicate id → membership check against ``seen_ids``.
    * empty / non-kebab id → regex check.
    * empty title / audio_path / license / credit / lyrics → Pydantic
      ``min_length``.
    * non-relative audio_path / traversal / missing ``audio/`` prefix
      → :func:`_validate_audio_path`.
    * duration_seconds out of (0, 30] → Pydantic ``gt=0, le=30`` plus
      an explicit ``< _MIN_DURATION_SECONDS`` floor check before
      Pydantic for a clearer message.
    * ``<system-reminder>`` or ``ignore prior instructions`` substring
      in title / credit / lyrics → :func:`_check_injection`.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"corpus entry must be an object, got {type(raw).__name__}")

    # Theme: coerce via Theme(value) so the failure surfaces the
    # offending value AND the literal token "theme" for the test regex.
    # Theme is a StrEnum, so its constructor accepts str at runtime; the
    # cast keeps mypy happy without weakening the type of ``raw``.
    theme_value = raw.get("theme")
    try:
        theme = Theme(theme_value)  # type: ignore[arg-type]
    except (ValueError, TypeError) as exc:
        song_id = raw.get("id", "<unknown>")
        raise ValueError(
            f"song {song_id!r} theme={theme_value!r} is not a valid Theme; "
            f"valid values: {[t.value for t in Theme]!r}"
        ) from exc

    # age_band: explicit check before Pydantic so the validator
    # rejects with a clear "age_band" message.
    age_band = raw.get("age_band")
    if age_band not in _VALID_AGE_BANDS:
        song_id = raw.get("id", "<unknown>")
        raise ValueError(
            f"song {song_id!r} age_band={age_band!r} is not in {sorted(_VALID_AGE_BANDS)!r}"
        )

    # id: kebab-slug + uniqueness.
    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        raise ValueError(f"song id missing or empty: {raw_id!r}")
    if not _KEBAB_SLUG_PATTERN.fullmatch(raw_id):
        raise ValueError(
            f"song id {raw_id!r} is not a valid kebab-slug (expected ^[a-z0-9]+(-[a-z0-9]+)*$)"
        )
    if raw_id in seen_ids:
        raise ValueError(f"duplicate song id {raw_id!r}")
    seen_ids.add(raw_id)

    # audio_path: relative, no traversal, starts with audio/.
    audio_path = _validate_audio_path(raw_id, raw.get("audio_path"))

    # duration_seconds: explicit floor check for a clearer message;
    # Pydantic handles the upper bound + non-int rejection.
    raw_duration = raw.get("duration_seconds")
    if isinstance(raw_duration, int) and raw_duration < _MIN_DURATION_SECONDS:
        raise ValueError(
            f"song {raw_id!r} duration_seconds={raw_duration} must be >= "
            f"{_MIN_DURATION_SECONDS} (got non-positive)"
        )

    # Injection scan over title / credit / lyrics / element_id / family.
    # Run BEFORE Pydantic so the message names the song id (Pydantic
    # strips the context). element_id + family added in Phase Q Step Q1
    # for defense-in-depth — the per-field regex/enum gates would also
    # catch a payload but the scan keeps the audit trail uniform.
    for field in ("title", "credit", "lyrics", "element_id", "family"):
        text = raw.get(field)
        if isinstance(text, str):
            _check_injection(raw_id, text, field=field)

    # persona_compat: must be non-empty list of strings.
    raw_pc = raw.get("persona_compat")
    if not isinstance(raw_pc, list) or not raw_pc:
        raise ValueError(f"song {raw_id!r} persona_compat must be a non-empty list of strings")
    if not all(isinstance(p, str) and p for p in raw_pc):
        raise ValueError(
            f"song {raw_id!r} persona_compat must contain only non-empty strings, got {raw_pc!r}"
        )

    # element_id + family are Phase Q optional. Pass through only when
    # present in raw — omitting the key lets the model default kick in
    # AND keeps the model_dump() shape parity for entries that don't
    # carry the fields (otherwise dump would always emit element_id=null).
    extras: dict[str, object] = {}
    if "element_id" in raw:
        extras["element_id"] = raw["element_id"]
    if "family" in raw:
        extras["family"] = raw["family"]

    # Now hand the coerced fields to Pydantic for the per-field shape
    # invariants (min_length, max_length, extra=forbid, gt/le on duration,
    # element_id regex, family enum coercion).
    return Song(
        id=raw_id,
        title=str(raw.get("title", "")),
        audio_path=audio_path,
        duration_seconds=int(raw_duration) if isinstance(raw_duration, int) else 0,
        theme=theme,
        age_band=age_band,  # type: ignore[arg-type]
        persona_compat=tuple(raw_pc),
        license=str(raw.get("license", "")),
        credit=str(raw.get("credit", "")),
        lyrics=str(raw.get("lyrics", "")),
        **extras,  # type: ignore[arg-type]
    )


def _probe_audio(song: Song) -> None:
    """Log INFO if audio file exists, WARN if missing. Non-fatal either way.

    Per K11 spec: K11 ships the manifest + loader + Coqui render
    script, but the .mp3 files are operator-rendered later. The
    loader MUST tolerate missing audio without raising — K12 kiosk-
    side handles 404 gracefully so unrendered entries don't kill the
    surface.
    """
    path = _audio_file_path(song.audio_path)
    if path.is_file():
        _logger.info("song %s audio present at %s", song.id, path)
    else:
        _logger.warning(
            "song %s audio MISSING at %s — run scripts/generate_song_corpus.py to render",
            song.id,
            path,
        )


# ---------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------


def load_songs() -> tuple[Song, ...]:
    """Return the validated song corpus. Cached on first call.

    Re-reads when ``TOYBOX_DATA_DIR`` is changed in-process (cache is
    keyed on the resolved file path). Raises :class:`ValueError` on
    any validator defect — the corpus is bundled, so a failure is
    a packaging / authoring error and should crash loudly rather
    than silently degrade.

    Audio file existence is probed for each entry and logged at INFO
    (present) or WARN (missing). Missing audio does NOT prevent the
    entry from loading — see :func:`_probe_audio` rationale.
    """
    path = _manifest_path()
    cached = _SONG_CACHE.get(path)
    if cached is not None:
        return cached

    raw_bytes = path.read_text(encoding="utf-8")
    raw_payload = json.loads(raw_bytes)
    if not isinstance(raw_payload, list):
        raise ValueError(
            f"song corpus {path} must be a JSON array, got {type(raw_payload).__name__}"
        )

    seen_ids: set[str] = set()
    songs: list[Song] = []
    for raw_entry in raw_payload:
        songs.append(_validate_raw_entry(raw_entry, seen_ids=seen_ids))

    # Audio probe after validation so the entry id in the log line is
    # already validated as a kebab-slug — no chance of a malformed id
    # leaking through to the log.
    for song in songs:
        _probe_audio(song)

    result = tuple(songs)
    _SONG_CACHE[path] = result

    # Phase Q Step Q5: build the bucket caches once per corpus load so
    # per-pick element-tier / family-tier lookups stay O(1) bucket +
    # O(small) filter. The buckets reference the same :class:`Song`
    # instances as the tuple — identity is preserved.
    by_element: dict[str, list[Song]] = {}
    by_family: dict[Family, list[Song]] = {}
    for song in songs:
        if song.element_id is not None:
            by_element.setdefault(song.element_id, []).append(song)
        if song.family is not None:
            by_family.setdefault(song.family, []).append(song)
    _SONGS_BY_ELEMENT_ID[path] = by_element
    _SONGS_BY_FAMILY[path] = by_family

    return result


# ---------------------------------------------------------------------
# Picker
# ---------------------------------------------------------------------


def pick_song(
    seed: int,
    *,
    age_band: str | None = None,
    persona_id: str | None = None,
    theme: Theme | None = None,
    require_audio: bool = False,
    element_id: str | None = None,
    family_hint: Family | None = None,
) -> Song | None:
    """Deterministic seeded pick from the song corpus.

    Filters (all optional, all ANDed):

    * ``age_band`` — entry's ``age_band`` must equal the argument.
    * ``persona_id`` — entry's ``persona_compat`` must contain
      ``"all"`` or the given id.
    * ``theme`` — entry's ``theme`` must be the given :class:`Theme`.
    * ``require_audio`` — when True, only consider entries whose
      audio file currently exists on disk. Default False so tests
      and K11 itself can exercise the picker before the operator
      runs ``scripts/generate_song_corpus.py``.
    * ``element_id`` — Phase Q Step Q5: when set, only entries whose
      :attr:`Song.element_id` equals this value qualify. The bucket
      cache (:data:`_SONGS_BY_ELEMENT_ID`) is consulted for O(1)
      lookup before the per-pick filter.
    * ``family_hint`` — Phase Q Step Q5: when set, only entries whose
      :attr:`Song.family` IS this :class:`Family` member (identity,
      not equality — per code-quality.md §2) qualify. Consulted via
      :data:`_SONGS_BY_FAMILY`.

    When ``element_id`` is set, ``family_hint`` is ignored (element
    tier wins). When ``element_id`` is unset and ``family_hint`` is
    set, the family bucket is the candidate pool. When both are
    unset the picker walks the full corpus (legacy behaviour).

    Tie-break: candidates sorted by ``id`` ASC before the seeded pick,
    so the same ``(seed, filters)`` always produces the same song
    regardless of corpus file order. Returns ``None`` when no entry
    matches.

    The picker uses ``seed % len(candidates)`` rather than
    :class:`random.Random` so the deterministic contract is trivially
    inspectable in tests and unaffected by future Python ``Random``
    algorithm changes.
    """

    def _persona_match(entry: Song) -> bool:
        if persona_id is None:
            return True
        return PERSONA_COMPAT_ALL in entry.persona_compat or persona_id in entry.persona_compat

    def _audio_match(entry: Song) -> bool:
        if not require_audio:
            return True
        return _audio_file_path(entry.audio_path).is_file()

    # Force corpus load so the bucket caches are populated. Bucket
    # caches are keyed on the same resolved path as :data:`_SONG_CACHE`.
    load_songs()
    path = _manifest_path()

    # Phase Q Step Q5: pick the candidate pool based on element_id /
    # family_hint. Element tier wins when both are passed — the caller
    # in :mod:`toybox.activities.content_resolver` already encodes the
    # element → family → theme fallback by issuing successive picks.
    pool: list[Song]
    if element_id is not None:
        pool = list(_SONGS_BY_ELEMENT_ID.get(path, {}).get(element_id, ()))
    elif family_hint is not None:
        pool = list(_SONGS_BY_FAMILY.get(path, {}).get(family_hint, ()))
    else:
        pool = list(load_songs())

    candidates = [
        s
        for s in pool
        if (age_band is None or s.age_band == age_band)
        and _persona_match(s)
        and (theme is None or s.theme is theme)
        and _audio_match(s)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda s: s.id)
    return candidates[seed % len(candidates)]


__all__ = [
    "AGE_BANDS",
    "AgeBand",
    "PERSONA_COMPAT_ALL",
    "Song",
    "clear_song_cache",
    "load_songs",
    "pick_song",
    "songs_audio_root",
]
