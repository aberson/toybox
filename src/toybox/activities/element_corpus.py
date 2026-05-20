"""Phase M Step M1 — element corpus loader + validator + seeded picker.

The bundled element corpus lives in ``data/elements/elements.json``
(root-relative, with ``TOYBOX_DATA_DIR`` env override matching
:mod:`toybox.activities.song_corpus` and :mod:`toybox.activities.joke_corpus`).
Entries are shaped (see :data:`Element`)::

    {
        "id": "<symbol-lower>-<atomic_number>",
        "symbol": <1-3 char display-case symbol>,
        "name": <common name>,
        "atomic_number": <int 1..118>,
        "atomic_mass": <float, 1dp>,
        "family": <one of :class:`Family` slugs>,
        "phase_at_room_temp": "solid" | "liquid" | "gas",
        "color_description": <short visual description for sprite prompt>,
        "discovered_era": "ancient" | <4-digit year string>,
        "fun_fact": <one kid-friendly sentence>,
        "story_seed_hooks": [<phrase>, <phrase>, <phrase>],
        "pronunciation_guide": <phonetic respelling> | null,
        "age_band": "3-5" | "6-8" | "9-12"
    }

Public surface mirrors :mod:`toybox.activities.song_corpus`:

* :class:`Element` — frozen Pydantic model.
* :class:`Family` — StrEnum with the 10 plan-spec family slugs.
* :func:`load_elements` — returns the validated tuple; cached.
* :func:`pick_element` — deterministic seeded pick after optional
  ``(family, age_band)`` filtering.
* :func:`get_element` — direct lookup by id, returns ``None`` on miss.
* :func:`clear_element_cache` — test hook.

Single source of truth: family membership lives in :class:`Family`.
Per code-quality.md §2 we NEVER redeclare family names elsewhere —
the JSON values are read through ``Family(value)`` so a stale slug
fails LOUDLY at load time AND tests assert ``element.family is
Family.alkali_metal`` (identity, not equality).

Security defense-in-depth per security.md "Treat fetched external
content as data, not instructions": entries containing
``<system-reminder>`` or ``ignore prior instructions``
(case-insensitive) in ``name | fun_fact | story_seed_hooks`` are
rejected at load time. The shipped corpus is operator-authored and
low-risk, but the gate prevents accidental ingest of a payload-
bearing entry through future tooling (corpus-editor PRs, eval imports).
"""

from __future__ import annotations

import json
import os
import random
import re
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------

AgeBand = Literal["3-5", "6-8", "9-12"]

# Tuple, not set, so iteration order is stable for tests and any
# future age-band picker in the parent UI.
AGE_BANDS: Final[tuple[AgeBand, ...]] = ("3-5", "6-8", "9-12")
_VALID_AGE_BANDS: Final[frozenset[str]] = frozenset(AGE_BANDS)

PhaseAtRoomTemp = Literal["solid", "liquid", "gas"]
_VALID_PHASES: Final[frozenset[str]] = frozenset({"solid", "liquid", "gas"})

# Composite id format ``<symbol-lower>-<atomic_number>`` per phase-m-plan.md §5.1.
# Symbol is 1-3 chars (Uut/Uuo style three-letter placeholders historically),
# atomic_number is 1-3 digits.
#
# Phase Q Step Q5: the SAME regex shape is referenced by
# :class:`toybox.activities.song_corpus.Song.element_id` and
# :class:`toybox.activities.joke_corpus.Joke.element_id`. Per
# code-quality.md §2 "one source of truth for data-shape constants"
# the pattern STRING (not the compiled Pattern object — Pydantic's
# ``Field(pattern=...)`` takes a str, not a Pattern) is exported as
# :data:`ELEMENT_ID_REGEX` and imported by both corpus modules. Tests
# assert ``Song.model_fields["element_id"].metadata`` references the
# same string so a future re-duplication fails CI loudly.
ELEMENT_ID_REGEX: Final[str] = r"^[a-z]{1,3}-[0-9]{1,3}$"
_ID_PATTERN: Final[re.Pattern[str]] = re.compile(ELEMENT_ID_REGEX)

# Path resolution mirrors storage.images._data_root and song_corpus._data_root:
# env override for test fixtures, default ``Path("data")`` relative to the
# process cwd (the project root in production).
_DATA_ROOT_ENV: Final[str] = "TOYBOX_DATA_DIR"
_DEFAULT_DATA_ROOT: Final[Path] = Path("data")
_ELEMENTS_SUBDIR: Final[str] = "elements"
_ELEMENTS_FILENAME: Final[str] = "elements.json"

# Atomic number bounds: 1 (Hydrogen) through 118 (Oganesson) inclusive.
_MIN_ATOMIC_NUMBER: Final[int] = 1
_MAX_ATOMIC_NUMBER: Final[int] = 118

# Prompt-injection substrings. Case-insensitive matching via .casefold().
# Keep this list short and obvious to avoid false positives in legitimate
# kid-friendly content.
_INJECTION_NEEDLES: Final[tuple[str, ...]] = (
    "<system-reminder>",
    "ignore prior instructions",
)


# ---------------------------------------------------------------------
# Family StrEnum — single source of truth (code-quality.md §2)
# ---------------------------------------------------------------------


class Family(StrEnum):
    """The 10 canonical element family slugs from documentation/phase-m-plan.md §2.

    Member values are the lowercase strings stored in
    ``data/elements/elements.json`` ``family`` field and used in M5
    narration prose (e.g. ``noble_gas`` -> "noble gases"). Per
    code-quality.md §2 these are NEVER redeclared elsewhere; tests
    assert ``element.family is Family.alkali_metal`` (identity, not
    equality) so any re-duplication fails CI loudly.
    """

    alkali_metal = "alkali_metal"
    alkaline_earth = "alkaline_earth"
    transition_metal = "transition_metal"
    post_transition_metal = "post_transition_metal"
    metalloid = "metalloid"
    nonmetal = "nonmetal"
    halogen = "halogen"
    noble_gas = "noble_gas"
    lanthanide = "lanthanide"
    actinide = "actinide"


# ---------------------------------------------------------------------
# Element model
# ---------------------------------------------------------------------


class Element(BaseModel):
    """A single corpus entry. Frozen so the cached tuple is safe to share.

    ``family`` is the :class:`Family` enum member (not a plain string)
    so callers can do ``element.family is Family.noble_gas`` without a
    string comparison — code-quality.md §2 "tests assert ``is``, not
    ``==``".

    ``story_seed_hooks`` is a tuple, not a list, so the frozen model
    is truly immutable. The JSON shape declares an array; the loader
    coerces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=16)
    symbol: str = Field(min_length=1, max_length=3)
    name: str = Field(min_length=1, max_length=64)
    atomic_number: int = Field(ge=_MIN_ATOMIC_NUMBER, le=_MAX_ATOMIC_NUMBER)
    atomic_mass: float = Field(gt=0.0)
    family: Family
    phase_at_room_temp: PhaseAtRoomTemp
    color_description: str = Field(min_length=1, max_length=120)
    discovered_era: str = Field(min_length=1, max_length=32)
    fun_fact: str = Field(min_length=1, max_length=240)
    story_seed_hooks: tuple[str, ...] = Field(min_length=1)
    pronunciation_guide: str | None = None
    age_band: AgeBand


# ---------------------------------------------------------------------
# Path + cache
# ---------------------------------------------------------------------


def _data_root() -> Path:
    """Root for the bundled data tree; env-overrideable for test fixtures."""
    raw = os.environ.get(_DATA_ROOT_ENV)
    return Path(raw) if raw else _DEFAULT_DATA_ROOT


def _elements_path() -> Path:
    return _data_root() / _ELEMENTS_SUBDIR / _ELEMENTS_FILENAME


def elements_root() -> Path:
    """Public accessor for the element sprite directory (``<data_root>/images/elements``).

    Phase M Step M3 — used by the FastAPI app factory to mount the
    directory as a ``StaticFiles`` route serving
    ``/api/static/elements/<element_id>.png``. Mirrors
    :func:`toybox.storage.images.images_root` and
    :func:`toybox.activities.song_corpus.songs_audio_root` so the
    static-mount on-disk path has ONE source of truth (per
    code-quality §2). Env-overrideable via ``TOYBOX_DATA_DIR`` like
    the rest of the bundled data tree.

    The sprite subdirectory lives under ``images/elements/`` (NOT
    under ``elements/`` next to ``elements.json``) so the kiosk's
    static mounts share a common parent (``data/images/``) and the
    kiosk's ``<img src="/api/static/elements/...">`` references land
    on real image bytes — the corpus JSON itself stays under
    ``data/elements/``.
    """
    return _data_root() / "images" / "elements"


# Cache: keyed on the resolved elements file path so a same-process
# change of TOYBOX_DATA_DIR (test monkeypatch) produces a fresh load.
_ELEMENT_CACHE: dict[Path, tuple[Element, ...]] = {}


def clear_element_cache() -> None:
    """Drop the in-process element cache (test hook).

    Production callers do not need to call this — the cache is keyed
    on the resolved path so an env change automatically forces a
    re-read.

    Phase Q Step Q5: also invalidates the per-path
    :data:`_FAMILY_BY_ID_CACHE` so a test monkeypatching
    ``TOYBOX_DATA_DIR`` sees the rebuilt id → Family mapping the next
    time :func:`family_for` is called.
    """
    _ELEMENT_CACHE.clear()
    _FAMILY_BY_ID_CACHE.clear()


# Phase Q Step Q5: id → Family lookup. Keyed on the resolved elements
# file path so a same-process change of TOYBOX_DATA_DIR (test
# monkeypatch) produces a fresh build alongside the underlying corpus
# cache. Per code-quality.md §1 "audit wire shape when storage
# representation changes" the cache is rebuilt eagerly on first call
# rather than mutated incrementally — keeps the producer-consumer
# coupling explicit and trivially inspectable.
_FAMILY_BY_ID_CACHE: dict[Path, dict[str, Family]] = {}


def family_for(element_id: str) -> Family | None:
    """Return the :class:`Family` for ``element_id``, or ``None`` if unknown.

    Phase Q Step Q5: the reward picker chain calls this once per
    activity-advance when the activity carries an ``element_id``. The
    answer feeds the family-tier fallback inside :func:`_try_pick_song`
    / :func:`_try_pick_joke` when the element-tier pick returns nothing.

    Cached: the underlying ``dict[str, Family]`` is built on first
    call by iterating the loaded corpus and stored against the
    resolved corpus path (mirrors :data:`_ELEMENT_CACHE`). The cache
    is invalidated alongside :func:`clear_element_cache`.

    Identity contract: the returned :class:`Family` IS a member of
    the canonical enum (the loader already coerces via ``Family(...)``)
    so callers can ``family_for(element_id) is Family.noble_gas``.
    """
    path = _elements_path()
    cached = _FAMILY_BY_ID_CACHE.get(path)
    if cached is None:
        cached = {e.id: e.family for e in load_elements()}
        _FAMILY_BY_ID_CACHE[path] = cached
    return cached.get(element_id)


# ---------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------


def _check_injection(element_id: str, text: str, *, field: str) -> None:
    """Reject prompt-injection payloads per security.md."""
    haystack = text.casefold()
    for needle in _INJECTION_NEEDLES:
        if needle in haystack:
            raise ValueError(
                f"element {element_id!r} {field}: injection payload {needle!r} detected; "
                f"reject per security.md (defense-in-depth)"
            )


def _validate_raw_entry(
    raw: dict[str, object],
    *,
    seen_ids: set[str],
    seen_atomic_numbers: set[int],
) -> Element:
    """Coerce one raw dict into an :class:`Element`. Raises ValueError on any defect.

    Validations exercised by tests in ``tests/unit/test_element_corpus.py``:

    * unknown family value -> ``Family(value)`` raises ``ValueError``
      with the offending value in the message; re-raised with the
      "family" token included for the test regex.
    * unknown age_band -> explicit check before Pydantic so the
      ``"age_band"`` token appears in the error message.
    * duplicate id / duplicate atomic_number -> membership checks.
    * empty / non-conforming id -> regex check + symbol/atomic_number
      consistency check.
    * atomic_number out of [1, 118] -> explicit bounds check before
      Pydantic for a clearer message that includes the
      ``"atomic_number"`` token.
    * ``<system-reminder>`` or ``ignore prior instructions`` substring
      in name / fun_fact / any story_seed_hooks entry -> rejection.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"corpus entry must be an object, got {type(raw).__name__}")

    # id: must be a non-empty string matching the composite format.
    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        raise ValueError(f"element id missing or empty: {raw_id!r}")
    if not _ID_PATTERN.fullmatch(raw_id):
        raise ValueError(
            f"element id {raw_id!r} is not a valid composite id "
            f"(expected ^[a-z]{{1,3}}-[0-9]{{1,3}}$)"
        )
    if raw_id in seen_ids:
        raise ValueError(f"duplicate element id {raw_id!r}")

    # atomic_number: explicit bounds check before Pydantic so the
    # rejection message names the field.
    raw_atomic_number = raw.get("atomic_number")
    if not isinstance(raw_atomic_number, int) or isinstance(raw_atomic_number, bool):
        raise ValueError(
            f"element {raw_id!r} atomic_number must be an int in "
            f"[{_MIN_ATOMIC_NUMBER}, {_MAX_ATOMIC_NUMBER}], got {raw_atomic_number!r}"
        )
    if raw_atomic_number < _MIN_ATOMIC_NUMBER or raw_atomic_number > _MAX_ATOMIC_NUMBER:
        raise ValueError(
            f"element {raw_id!r} atomic_number={raw_atomic_number} is out of range "
            f"[{_MIN_ATOMIC_NUMBER}, {_MAX_ATOMIC_NUMBER}]"
        )
    if raw_atomic_number in seen_atomic_numbers:
        raise ValueError(f"duplicate atomic_number {raw_atomic_number} (already seen)")

    # symbol consistency: id MUST equal ``<symbol-lower>-<atomic_number>``.
    raw_symbol = raw.get("symbol")
    if not isinstance(raw_symbol, str) or not raw_symbol:
        raise ValueError(f"element {raw_id!r} symbol missing or empty: {raw_symbol!r}")
    expected_id = f"{raw_symbol.lower()}-{raw_atomic_number}"
    if raw_id != expected_id:
        raise ValueError(
            f"element id {raw_id!r} is not consistent with symbol={raw_symbol!r} "
            f"atomic_number={raw_atomic_number}; expected id {expected_id!r}"
        )

    # family: coerce via Family(value) so the failure surfaces the
    # offending value AND the literal token "family" for the test regex.
    family_value = raw.get("family")
    try:
        family = Family(family_value)  # type: ignore[arg-type]
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"element {raw_id!r} family={family_value!r} is not a valid Family; "
            f"valid values: {[f.value for f in Family]!r}"
        ) from exc

    # age_band: explicit check before Pydantic so the validator
    # rejects with a clear "age_band" message.
    age_band = raw.get("age_band")
    if age_band not in _VALID_AGE_BANDS:
        raise ValueError(
            f"element {raw_id!r} age_band={age_band!r} is not in {sorted(_VALID_AGE_BANDS)!r}"
        )

    # phase_at_room_temp: explicit check before Pydantic for a clearer message.
    phase = raw.get("phase_at_room_temp")
    if phase not in _VALID_PHASES:
        raise ValueError(
            f"element {raw_id!r} phase_at_room_temp={phase!r} is not in {sorted(_VALID_PHASES)!r}"
        )

    # Injection scan over name / fun_fact / story_seed_hooks. Run BEFORE
    # Pydantic so the message names the element id (Pydantic strips
    # context).
    raw_name = raw.get("name")
    if isinstance(raw_name, str):
        _check_injection(raw_id, raw_name, field="name")
    raw_fun_fact = raw.get("fun_fact")
    if isinstance(raw_fun_fact, str):
        _check_injection(raw_id, raw_fun_fact, field="fun_fact")
    raw_hooks = raw.get("story_seed_hooks")
    if isinstance(raw_hooks, list):
        for idx, hook in enumerate(raw_hooks):
            if isinstance(hook, str):
                _check_injection(raw_id, hook, field=f"story_seed_hooks[{idx}]")

    # story_seed_hooks shape: must be a non-empty list of non-empty strings.
    if not isinstance(raw_hooks, list) or not raw_hooks:
        raise ValueError(f"element {raw_id!r} story_seed_hooks must be a non-empty list of strings")
    if not all(isinstance(h, str) and h for h in raw_hooks):
        raise ValueError(
            f"element {raw_id!r} story_seed_hooks must contain only non-empty strings, "
            f"got {raw_hooks!r}"
        )

    # pronunciation_guide is optional; accept absent, null, or a string.
    raw_guide = raw.get("pronunciation_guide", None)
    pronunciation_guide: str | None
    if raw_guide is None:
        pronunciation_guide = None
    elif isinstance(raw_guide, str):
        pronunciation_guide = raw_guide
    else:
        raise ValueError(
            f"element {raw_id!r} pronunciation_guide must be a string or null, "
            f"got {type(raw_guide).__name__}"
        )

    # atomic_mass: numeric (int or float OK; coerce to float).
    raw_atomic_mass = raw.get("atomic_mass")
    if not isinstance(raw_atomic_mass, (int, float)) or isinstance(raw_atomic_mass, bool):
        raise ValueError(
            f"element {raw_id!r} atomic_mass must be a number, got {raw_atomic_mass!r}"
        )

    # Hand to Pydantic for the per-field shape invariants
    # (min_length, max_length, gt/le on numerics, extra=forbid).
    element = Element(
        id=raw_id,
        symbol=raw_symbol,
        name=str(raw_name if raw_name is not None else ""),
        atomic_number=raw_atomic_number,
        atomic_mass=float(raw_atomic_mass),
        family=family,
        phase_at_room_temp=phase,  # type: ignore[arg-type]
        color_description=str(raw.get("color_description", "")),
        discovered_era=str(raw.get("discovered_era", "")),
        fun_fact=str(raw_fun_fact if raw_fun_fact is not None else ""),
        story_seed_hooks=tuple(raw_hooks),
        pronunciation_guide=pronunciation_guide,
        age_band=age_band,  # type: ignore[arg-type]
    )

    # Only register the id / atomic_number AFTER successful construction so
    # a downstream validation failure doesn't pollute the seen-sets.
    seen_ids.add(raw_id)
    seen_atomic_numbers.add(raw_atomic_number)
    return element


# ---------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------


def load_elements() -> tuple[Element, ...]:
    """Return the validated element corpus. Cached on first call.

    Re-reads when ``TOYBOX_DATA_DIR`` is changed in-process (cache is
    keyed on the resolved file path). Raises :class:`ValueError` on
    any validator defect — the corpus is bundled, so a failure is a
    packaging / authoring error and should crash loudly rather than
    silently degrade.
    """
    path = _elements_path()
    cached = _ELEMENT_CACHE.get(path)
    if cached is not None:
        return cached

    raw_bytes = path.read_text(encoding="utf-8")
    raw_payload = json.loads(raw_bytes)
    if not isinstance(raw_payload, list):
        raise ValueError(
            f"element corpus {path} must be a JSON array, got {type(raw_payload).__name__}"
        )

    seen_ids: set[str] = set()
    seen_atomic_numbers: set[int] = set()
    elements: list[Element] = []
    for raw_entry in raw_payload:
        elements.append(
            _validate_raw_entry(
                raw_entry,
                seen_ids=seen_ids,
                seen_atomic_numbers=seen_atomic_numbers,
            )
        )

    result = tuple(elements)
    _ELEMENT_CACHE[path] = result
    return result


# ---------------------------------------------------------------------
# Picker
# ---------------------------------------------------------------------


def pick_element(
    seed: int,
    *,
    family: Family | None = None,
    age_band: str | None = None,
) -> Element | None:
    """Deterministic seeded pick from the element corpus.

    Filters (all optional, all ANDed):

    * ``family`` — entry's ``family`` must be the given :class:`Family`.
    * ``age_band`` — entry's ``age_band`` must equal the argument.

    Tie-break: candidates sorted by ``id`` ASC before the seeded pick,
    so the same ``(seed, filters)`` always produces the same element
    regardless of corpus file order. Returns ``None`` when no entry
    matches.

    The picker uses ``seed % len(candidates)`` rather than
    :class:`random.Random` so the deterministic contract is trivially
    inspectable in tests and unaffected by future Python ``Random``
    algorithm changes.
    """
    candidates = [
        e
        for e in load_elements()
        if (family is None or e.family is family) and (age_band is None or e.age_band == age_band)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda e: e.id)
    return candidates[seed % len(candidates)]


# ---------------------------------------------------------------------
# Direct lookup
# ---------------------------------------------------------------------


def get_element(element_id: str) -> Element | None:
    """Return the element with the given id, or ``None`` if not found.

    Used by the M4 template validator (and downstream callers) to
    confirm an ``element_id`` referenced by a template resolves to a
    real corpus entry without raising on miss.
    """
    for element in load_elements():
        if element.id == element_id:
            return element
    return None


# ---------------------------------------------------------------------
# Phase N Step N3 — peer-picker helpers
# ---------------------------------------------------------------------
#
# Feed the Phase N "element microgame" template generator (N4). Two
# adjacent forks per template each need one same-family peer and one
# cross-family distractor. Both helpers are age-band-aware so a 3-5
# Gold activity never offers Plutonium (9-12) as a candidate.
#
# Design contract (per documentation/phase-n-plan.md §5 Step N3 and §7
# risks):
#
#   * RNG: caller passes a seeded ``random.Random`` instance. Matches
#     the project-wide convention (``content_resolver``, ``slots``,
#     ``feedback``, ``generator`` all type ``rng: random.Random``).
#     ``random.Random(seed)`` -> deterministic call sequence.
#
#   * Filter: candidates restricted to same ``age_band`` as the
#     requesting element. peer_in_family additionally requires same
#     ``family`` and excludes the requesting element itself.
#     peer_out_of_family requires a different ``family`` (which already
#     excludes self).
#
#   * Determinism: candidates are sorted by ``id`` (lexicographic)
#     before the seeded pick, so corpus file order does NOT leak into
#     the picker output. Same ``(element_id, seed)`` ALWAYS yields the
#     same peer regardless of how ``elements.json`` is ordered. Mirrors
#     the ``pick_element`` sort-then-pick pattern.
#
#   * Error contract (stricter than ``get_element`` which returns None):
#     - Unknown ``element_id`` -> ``ValueError`` per N3 done-when
#       ("both raise on unknown element_id").
#     - Empty candidate pool after filtering -> ``ValueError``. We
#       NEVER loop, NEVER return None, NEVER return the requesting
#       element itself. Callers (N4 generator) handle this by picking
#       elements with adequate peer coverage.
#
# Corpus shape gotcha (verified against shipped data/elements/elements.json):
# 15 entries are age_band="3-5" (NOT all 118 as the plan §2 narrative
# implies). Two of those 15 have NO same-band same-family peer:
# ``na-11`` (only alkali_metal at 3-5) and ``ca-20`` (only
# alkaline_earth at 3-5). The N4 generator must skip element_ids where
# peer_in_family raises — confirmed at plan-time.


def peer_in_family(element_id: str, rng: random.Random) -> Element:
    """Pick a same-family same-age-band peer of ``element_id``, excluding self.

    Args:
        element_id: Composite id (``<symbol-lower>-<atomic_number>``)
            of the requesting element.
        rng: Pre-seeded :class:`random.Random` instance. Determinism
            contract: ``random.Random(seed)`` produces identical output
            across calls with the same seed.

    Returns:
        An :class:`Element` with the same ``family`` and ``age_band``
        as the requesting element, but a different ``id``.

    Raises:
        ValueError: If ``element_id`` is not in the corpus, or if no
            other element in the corpus shares both the requesting
            element's ``family`` AND ``age_band``. Per N3 §7 risks we
            NEVER loop, return None, or return self on an empty pool.
    """
    target = get_element(element_id)
    if target is None:
        raise ValueError(f"peer_in_family: unknown element_id {element_id!r} (not in corpus)")
    candidates = [
        e
        for e in load_elements()
        if e.family is target.family and e.age_band == target.age_band and e.id != target.id
    ]
    if not candidates:
        raise ValueError(
            f"peer_in_family: no same-family same-age-band peer for {element_id!r} "
            f"(family={target.family.value!r}, age_band={target.age_band!r}); "
            f"corpus exhausted after filtering"
        )
    # Sort by id so the same (element_id, seed) is stable regardless of
    # corpus file order — matches the pick_element tie-break.
    candidates.sort(key=lambda e: e.id)
    return rng.choice(candidates)


def peer_out_of_family(element_id: str, rng: random.Random) -> Element:
    """Pick a cross-family same-age-band distractor for ``element_id``.

    Args:
        element_id: Composite id of the requesting element.
        rng: Pre-seeded :class:`random.Random` instance.

    Returns:
        An :class:`Element` with a DIFFERENT ``family`` from the
        requesting element but the SAME ``age_band``. (Different family
        already excludes the requesting element itself.)

    Raises:
        ValueError: If ``element_id`` is not in the corpus, or if no
            other-family element in the corpus shares the requesting
            element's ``age_band``.
    """
    target = get_element(element_id)
    if target is None:
        raise ValueError(f"peer_out_of_family: unknown element_id {element_id!r} (not in corpus)")
    candidates = [
        e
        for e in load_elements()
        if e.family is not target.family and e.age_band == target.age_band
    ]
    if not candidates:
        raise ValueError(
            f"peer_out_of_family: no cross-family same-age-band peer for {element_id!r} "
            f"(target family={target.family.value!r}, age_band={target.age_band!r}); "
            f"corpus exhausted after filtering"
        )
    candidates.sort(key=lambda e: e.id)
    return rng.choice(candidates)


__all__ = [
    "AGE_BANDS",
    "ELEMENT_ID_REGEX",
    "AgeBand",
    "Element",
    "Family",
    "PhaseAtRoomTemp",
    "clear_element_cache",
    "elements_root",
    "family_for",
    "get_element",
    "load_elements",
    "peer_in_family",
    "peer_out_of_family",
    "pick_element",
]
