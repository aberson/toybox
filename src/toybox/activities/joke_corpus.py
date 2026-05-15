"""Phase K Step K10 — joke corpus loader + theme tagging + seeded picker.

The bundled joke corpus lives in ``data/jokes/jokes.json`` (root-
relative, with ``TOYBOX_DATA_DIR`` env override matching
:mod:`toybox.storage.images`'s ``_data_root`` precedent). Entries are
shaped::

    {
        "id": <kebab-slug>,
        "setup": <str>,
        "punchline": <str>,
        "theme": <one of the 12 :class:`toybox.activities.themes.Theme`>,
        "optional_toy_slot": <bool>,
        "age_band": "3-5" | "6-8" | "9-12",
        "persona_compat": ["all"] | [<persona_id>, ...]
    }

Public surface:

* :class:`Joke` — frozen Pydantic model. The codebase uses frozen
  Pydantic for activity-shape data (``Activity``, ``ActivityStep``,
  ``Step``); we follow the convention so future wire-shape exposure
  to the parent UI (K15 parent-insert response) drops in without a
  type translation.
* :func:`load_jokes` — returns the validated tuple of jokes; cached.
* :func:`pick_joke` — deterministic seeded pick after optional
  ``(age_band, persona_id, theme)`` filtering.
* :func:`apply_toy_substitution` — render ``{toy}`` substitution
  with a defense-in-depth strip of stray placeholders.
* :func:`clear_joke_cache` — test hook (mirrors
  :func:`toybox.activities.generator.clear_template_cache`).

Single source of truth: theme membership lives in
:class:`toybox.activities.themes.Theme`. Per code-quality.md §2 we
NEVER redeclare theme names in this module or in
``data/jokes/jokes.json`` — the JSON values are read through
``Theme(value)`` so a stale string fails LOUDLY at load time.

Security defense-in-depth per security.md "Treat fetched external
content as data, not instructions": entries containing
``<system-reminder>`` or ``ignore prior instructions``
(case-insensitive) are rejected at load time. The shipped corpus is
operator-authored and low-risk, but the gate prevents accidental
ingest of a payload-bearing entry through future tooling
(corpus-editor PRs, eval imports).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

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

# Path resolution mirrors storage.images._data_root: env override for
# test fixtures, default ``Path("data")`` relative to the process cwd
# (the project root in production).
_DATA_ROOT_ENV: Final[str] = "TOYBOX_DATA_DIR"
_DEFAULT_DATA_ROOT: Final[Path] = Path("data")
_JOKES_SUBDIR: Final[str] = "jokes"
_JOKES_FILENAME: Final[str] = "jokes.json"

# Prompt-injection substrings. Case-insensitive matching, hence
# .casefold() comparison at validation time. Keep this list short and
# obvious — a longer list means more false positives for an operator
# writing a legitimate kid-friendly joke about robots following
# instructions.
_INJECTION_NEEDLES: Final[tuple[str, ...]] = (
    "<system-reminder>",
    "ignore prior instructions",
)


# ---------------------------------------------------------------------
# Joke model
# ---------------------------------------------------------------------


class Joke(BaseModel):
    """A single corpus entry. Frozen so the cached tuple is safe to share.

    ``theme`` is the :class:`Theme` enum member (not a plain string)
    so callers can do ``joke.theme is Theme.silly`` without a string
    comparison — code-quality.md §2 "tests assert ``is``, not ``==``".

    ``persona_compat`` is a tuple, not a list, so the frozen model is
    truly immutable. The JSON shape declares an array; the loader
    coerces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    setup: str = Field(min_length=1, max_length=200)
    punchline: str = Field(min_length=1, max_length=200)
    theme: Theme
    optional_toy_slot: bool
    age_band: AgeBand
    persona_compat: tuple[str, ...] = Field(min_length=1)


# ---------------------------------------------------------------------
# Path + cache
# ---------------------------------------------------------------------


def _data_root() -> Path:
    """Root for the bundled data tree; env-overrideable for test fixtures."""
    raw = os.environ.get(_DATA_ROOT_ENV)
    return Path(raw) if raw else _DEFAULT_DATA_ROOT


def _jokes_path() -> Path:
    return _data_root() / _JOKES_SUBDIR / _JOKES_FILENAME


# Cache: keyed on the resolved jokes file path so a same-process
# change of TOYBOX_DATA_DIR (test monkeypatch) produces a fresh load.
# Matches generator._TEMPLATE_CACHE's (TEMPLATES_DIR, intent) keying.
_JOKE_CACHE: dict[Path, tuple[Joke, ...]] = {}


def clear_joke_cache() -> None:
    """Drop the in-process joke cache (test hook).

    Production callers do not need to call this — the cache is keyed
    on the resolved path so an env change automatically forces a
    re-read.
    """
    _JOKE_CACHE.clear()


# ---------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------


def _check_injection(joke_id: str, text: str, *, field: str) -> None:
    """Reject prompt-injection payloads per security.md."""
    haystack = text.casefold()
    for needle in _INJECTION_NEEDLES:
        if needle in haystack:
            raise ValueError(
                f"joke {joke_id!r} {field}: injection payload {needle!r} detected; "
                f"reject per security.md (defense-in-depth)"
            )


def _validate_raw_entry(raw: dict[str, object], *, seen_ids: set[str]) -> Joke:
    """Coerce one raw dict into a :class:`Joke`. Raises ValueError on any defect.

    Validations exercised by tests in
    ``tests/unit/test_joke_corpus.py``:

    * unknown theme value → ``Theme(value)`` raises ``ValueError``
      with the offending value in the message; we re-raise as-is.
    * unknown age_band → explicit check before Pydantic so the
      ``"age_band"`` token appears in the error message.
    * duplicate id → membership check against ``seen_ids``.
    * empty / non-kebab id → regex check.
    * empty setup / punchline / persona_compat → Pydantic ``min_length``.
    * ``<system-reminder>`` or ``ignore prior instructions`` substring
      in setup or punchline → :func:`_check_injection`.
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
        joke_id = raw.get("id", "<unknown>")
        raise ValueError(
            f"joke {joke_id!r} theme={theme_value!r} is not a valid Theme; "
            f"valid values: {[t.value for t in Theme]!r}"
        ) from exc

    # age_band: explicit check before Pydantic so the validator
    # rejects with a clear "age_band" message (Pydantic's Literal
    # error message format varies across releases).
    age_band = raw.get("age_band")
    if age_band not in _VALID_AGE_BANDS:
        joke_id = raw.get("id", "<unknown>")
        raise ValueError(
            f"joke {joke_id!r} age_band={age_band!r} is not in {sorted(_VALID_AGE_BANDS)!r}"
        )

    # id: kebab-slug + uniqueness.
    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        raise ValueError(f"joke id missing or empty: {raw_id!r}")
    if not _KEBAB_SLUG_PATTERN.fullmatch(raw_id):
        raise ValueError(
            f"joke id {raw_id!r} is not a valid kebab-slug (expected ^[a-z0-9]+(-[a-z0-9]+)*$)"
        )
    if raw_id in seen_ids:
        raise ValueError(f"duplicate joke id {raw_id!r}")
    seen_ids.add(raw_id)

    # Injection scan over setup + punchline. Run BEFORE Pydantic so
    # the message names the joke id (Pydantic strips the context).
    for field in ("setup", "punchline"):
        text = raw.get(field)
        if isinstance(text, str):
            _check_injection(raw_id, text, field=field)

    # persona_compat: must be non-empty list of strings.
    raw_pc = raw.get("persona_compat")
    if not isinstance(raw_pc, list) or not raw_pc:
        raise ValueError(f"joke {raw_id!r} persona_compat must be a non-empty list of strings")
    if not all(isinstance(p, str) and p for p in raw_pc):
        raise ValueError(
            f"joke {raw_id!r} persona_compat must contain only non-empty strings, got {raw_pc!r}"
        )

    # Now hand the coerced fields to Pydantic for the per-field shape
    # invariants (min_length, max_length, extra=forbid).
    return Joke(
        id=raw_id,
        setup=str(raw.get("setup", "")),
        punchline=str(raw.get("punchline", "")),
        theme=theme,
        optional_toy_slot=bool(raw.get("optional_toy_slot", False)),
        age_band=age_band,  # type: ignore[arg-type]
        persona_compat=tuple(raw_pc),
    )


# ---------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------


def load_jokes() -> tuple[Joke, ...]:
    """Return the validated joke corpus. Cached on first call.

    Re-reads when ``TOYBOX_DATA_DIR`` is changed in-process (cache is
    keyed on the resolved file path). Raises :class:`ValueError` on
    any validator defect — the corpus is bundled, so a failure is
    a packaging / authoring error and should crash loudly rather
    than silently degrade.
    """
    path = _jokes_path()
    cached = _JOKE_CACHE.get(path)
    if cached is not None:
        return cached

    raw_bytes = path.read_text(encoding="utf-8")
    raw_payload = json.loads(raw_bytes)
    if not isinstance(raw_payload, list):
        raise ValueError(
            f"joke corpus {path} must be a JSON array, got {type(raw_payload).__name__}"
        )

    seen_ids: set[str] = set()
    jokes: list[Joke] = []
    for raw_entry in raw_payload:
        jokes.append(_validate_raw_entry(raw_entry, seen_ids=seen_ids))

    result = tuple(jokes)
    _JOKE_CACHE[path] = result
    return result


# ---------------------------------------------------------------------
# Picker
# ---------------------------------------------------------------------


def pick_joke(
    seed: int,
    *,
    age_band: str | None = None,
    persona_id: str | None = None,
    theme: Theme | None = None,
) -> Joke | None:
    """Deterministic seeded pick from the joke corpus.

    Filters (all optional, all ANDed):

    * ``age_band`` — entry's ``age_band`` must equal the argument.
    * ``persona_id`` — entry's ``persona_compat`` must contain
      ``"all"`` or the given id.
    * ``theme`` — entry's ``theme`` must be the given :class:`Theme`.

    Tie-break: candidates sorted by ``id`` ASC before the seeded pick,
    so the same ``(seed, filters)`` always produces the same joke
    regardless of corpus file order. Returns ``None`` when no entry
    matches.

    The picker uses ``seed % len(candidates)`` rather than
    :class:`random.Random` so the deterministic contract is trivially
    inspectable in tests and unaffected by future Python ``Random``
    algorithm changes (CPython has changed the implementation across
    releases — uncommon but documented).
    """

    def _persona_match(entry: Joke) -> bool:
        if persona_id is None:
            return True
        return PERSONA_COMPAT_ALL in entry.persona_compat or persona_id in entry.persona_compat

    candidates = [
        j
        for j in load_jokes()
        if (age_band is None or j.age_band == age_band)
        and _persona_match(j)
        and (theme is None or j.theme is theme)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda j: j.id)
    return candidates[seed % len(candidates)]


# ---------------------------------------------------------------------
# Slot substitution
# ---------------------------------------------------------------------

_TOY_PLACEHOLDER: Final[str] = "{toy}"


def apply_toy_substitution(joke: Joke, toy_display_name: str | None) -> tuple[str, str]:
    """Render ``(setup, punchline)`` with the joke's toy slot applied.

    Three cases:

    1. ``optional_toy_slot=True`` AND ``toy_display_name`` is non-empty:
       every ``{toy}`` is replaced literally with the display name.
    2. ``optional_toy_slot=True`` AND ``toy_display_name`` is ``None`` /
       empty: strip ``{toy}`` (so the line degrades to "no-toy" form
       without leaking the placeholder to the kid).
    3. ``optional_toy_slot=False``: return setup/punchline as authored,
       but strip any stray ``{toy}`` defensively — an author typo on a
       non-toy-slot joke would otherwise leak through to the kiosk.

    Whitespace cleanup: when stripping a placeholder we remove the
    placeholder only (not surrounding spaces). Templates are authored
    with the substitution-intact form in mind; stripping leaves a
    coherent "no toy here" sentence (the author should set
    ``optional_toy_slot`` per a fallback-readable text).
    """
    if joke.optional_toy_slot and toy_display_name:
        return (
            joke.setup.replace(_TOY_PLACEHOLDER, toy_display_name),
            joke.punchline.replace(_TOY_PLACEHOLDER, toy_display_name),
        )
    # Either toy unavailable, or non-toy joke — strip any leaked placeholder.
    return (
        joke.setup.replace(_TOY_PLACEHOLDER, ""),
        joke.punchline.replace(_TOY_PLACEHOLDER, ""),
    )


__all__ = [
    "AGE_BANDS",
    "AgeBand",
    "Joke",
    "PERSONA_COMPAT_ALL",
    "apply_toy_substitution",
    "clear_joke_cache",
    "load_jokes",
    "pick_joke",
]
