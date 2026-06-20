"""Filename + CLIP room-type matcher (Phase X Step X4).

:func:`match_photo` guesses the room type for an uploaded room photo using
two signals, cheapest first:

1. **Filename keyword heuristic** — if the upload filename contains a
   keyword mapped to a ROOM_TYPE (``master-bedroom.jpg`` → ``bedroom``),
   return that immediately with ``source="filename"`` and NEVER call the
   classifier. This is free, deterministic, and handles the common case
   where a parent names photos by room.
2. **Local CLIP classifier** — otherwise call the injected classifier's
   ``classify``, take the top-scoring label, and accept it only if its
   raw-cosine score clears :data:`CLIP_CONFIDENCE_THRESHOLD`. Below the
   floor → ``room_type=None``, ``source="none"`` (the UI renders N/A).

The classifier is **injected** (X5 wires the real
:func:`toybox.ai.room_classifier.load_default_classifier` result; tests
pass a fake) so this module stays free of the ONNX import graph.

Hard invariant: :func:`match_photo` **never raises**. Any classifier
failure — :class:`RoomClassifierUnavailable` (model not downloaded), a
decode error, or anything else — is caught and degrades to the filename
result (if any) or N/A. A broken/absent model must not break upload.

The keyword map is DERIVED from :data:`toybox.core.room_types.ROOM_TYPES`
— every ROOM_TYPE has at least one keyword and the map's keys ARE the
ROOM_TYPES (asserted by a regression test, per code-quality.md §2 "one
source of truth for data-shape constants").
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from toybox.ai.room_classifier import RoomClassifierUnavailable
from toybox.core.room_types import ROOM_TYPES

_logger = logging.getLogger(__name__)

# Confidence floor for the CLIP path. Scores are RAW COSINE similarity in
# [-1, 1] (see room_classifier module docstring). CLIP image/text cosines
# for a correct match typically land ~0.25-0.35 while mismatches sit
# ~0.15-0.20, so 0.22 is a conservative floor: above it we trust the top
# label; below it we return N/A rather than guess. Tunable as the single
# constant the room_match consumer gates on.
CLIP_CONFIDENCE_THRESHOLD: Final[float] = 0.22

MatchSource = Literal["filename", "clip", "none"]


@dataclass(frozen=True, slots=True)
class RoomGuess:
    """Result of a room-type guess for one uploaded photo.

    ``room_type`` is a :data:`ROOM_TYPES` string, or ``None`` when no
    signal was confident enough (the parent UI renders N/A and lets them
    pick manually). ``confidence`` is the supporting score: ``1.0`` for a
    filename keyword hit (exact, deterministic), the raw cosine for a CLIP
    hit, ``0.0`` for N/A. ``source`` records which signal won.
    """

    room_type: str | None
    confidence: float
    source: MatchSource


class _Classifier(Protocol):
    """Structural type for the injected classifier (real one or a fake)."""

    def classify(self, image_bytes: bytes) -> dict[str, float]: ...


# Keyword → ROOM_TYPE map, DERIVED from ROOM_TYPES. Every ROOM_TYPE has at
# least one keyword (the type's own name is always included below). Order
# does not matter for correctness — a filename matching keywords for two
# types is rare and resolved by first-ROOM_TYPES-order match.
#
# A regression test asserts ``set(_KEYWORD_TO_ROOM) covers every value``
# and that every mapped room is a real ROOM_TYPE.
def _build_keyword_map() -> dict[str, str]:
    """Build the keyword→room map, seeded from ROOM_TYPES then enriched.

    Each ROOM_TYPE always maps its own underscore-joined name AND its
    space-free variants as keywords so the map is exhaustive by
    construction; the curated synonyms below add the natural-language
    aliases a parent actually types in filenames.
    """
    synonyms: dict[str, tuple[str, ...]] = {
        "bedroom": ("bed", "bedroom", "master", "primary", "guest"),
        "bathroom": ("bath", "bathroom", "powder", "ensuite", "washroom", "restroom"),
        "kitchen": ("kitchen",),
        "living_room": ("living", "livingroom", "lounge", "family", "den"),
        "garage": ("garage",),
        "yard": ("yard", "backyard", "garden", "patio", "outdoor"),
        "playroom": ("play", "playroom", "nursery", "kids", "rumpus"),
        "dining_room": ("dining", "diningroom", "dinner"),
        "office": ("office", "study", "desk", "workspace"),
    }
    mapping: dict[str, str] = {}
    for room in ROOM_TYPES:
        # Guarantee exhaustiveness: the type's own name (and its no-space
        # form) are always keywords, even if a synonym entry is missing.
        mapping[room] = room
        mapping[room.replace("_", "")] = room
        for kw in synonyms.get(room, ()):
            mapping[kw] = room
    return mapping


_KEYWORD_TO_ROOM: Final[dict[str, str]] = _build_keyword_map()

# Split filenames into lowercase alphanumeric tokens so "master-bedroom",
# "master_bedroom", "MasterBedroom.JPG", and "IMG bedroom 2.jpg" all
# surface the "bedroom" token.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")

# Minimum keyword length eligible for the separator-less SUBSTRING fallback.
# The exact-token pass already catches short synonyms ("bed", "den", "play",
# "yard") when they appear as their own token. Letting those same short
# synonyms match as an arbitrary substring produces false positives —
# "bedford.jpg"→bed→bedroom, "denver.png"→den→living_room,
# "my-playlist.jpg"→play→playroom, "garden-state"→...→yard. Restricting the
# substring fallback to longer, distinctive keywords (e.g. "bedroom",
# "playroom", "livingroom", "bathroom") keeps the no-separator case
# ("masterbedroom.jpg") working without those spurious hits.
_SUBSTRING_MIN_KEYWORD_LEN: Final[int] = 5


def _match_filename(filename: str | None) -> str | None:
    """Return the ROOM_TYPE for the first keyword found in ``filename``.

    Tokenizes on non-alphanumeric boundaries AND also scans for keyword
    substrings (so "masterbedroom.jpg" with no separator still hits).
    Returns the matched ROOM_TYPE in ROOM_TYPES order on the first hit,
    else ``None``.

    The substring fallback is restricted to keywords of length ≥
    :data:`_SUBSTRING_MIN_KEYWORD_LEN` so short synonyms ("bed", "den",
    "play", "yard") only match as whole tokens, never as an arbitrary
    substring — "bedford.jpg" / "denver.png" / "my-playlist.jpg" fall
    through to the classifier instead of being mis-labeled.
    """
    if not filename:
        return None
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    lowered = stem.lower()
    tokens = set(_TOKEN_RE.findall(lowered))

    # First: exact token matches (most precise) — checked in ROOM_TYPES
    # order via a stable scan so a multi-keyword filename is deterministic.
    for room in ROOM_TYPES:
        for keyword, mapped in _KEYWORD_TO_ROOM.items():
            if mapped != room:
                continue
            if keyword in tokens:
                return room
    # Fallback: substring match for separator-less filenames, restricted to
    # longer/distinctive keywords (see _SUBSTRING_MIN_KEYWORD_LEN). Longer
    # keywords first so "bedroom" wins over a shorter overlapping keyword
    # inside "masterbedroom".
    for keyword in sorted(_KEYWORD_TO_ROOM, key=len, reverse=True):
        if len(keyword) < _SUBSTRING_MIN_KEYWORD_LEN:
            continue
        if keyword in lowered:
            return _KEYWORD_TO_ROOM[keyword]
    return None


def match_photo(
    filename: str | None,
    image_bytes: bytes,
    *,
    classifier: _Classifier,
) -> RoomGuess:
    """Guess the room type for an uploaded photo. Never raises.

    1. Filename keyword hit → ``RoomGuess(room, 1.0, "filename")`` with NO
       classifier call.
    2. Else ``classifier.classify(image_bytes)``; top label with score ≥
       :data:`CLIP_CONFIDENCE_THRESHOLD` → ``RoomGuess(room, score, "clip")``.
    3. Below the floor, an empty result, or any classifier error →
       ``RoomGuess(None, 0.0, "none")`` (N/A).
    """
    filename_room = _match_filename(filename)
    if filename_room is not None:
        return RoomGuess(room_type=filename_room, confidence=1.0, source="filename")

    try:
        scores = classifier.classify(image_bytes)
    except RoomClassifierUnavailable as exc:
        _logger.info("room classifier unavailable; falling back to N/A (%s)", exc)
        return RoomGuess(room_type=None, confidence=0.0, source="none")
    except Exception:  # noqa: BLE001 — never let a classifier defect break upload
        _logger.warning("room classifier raised; falling back to N/A", exc_info=True)
        return RoomGuess(room_type=None, confidence=0.0, source="none")

    if not scores:
        return RoomGuess(room_type=None, confidence=0.0, source="none")

    top_room, top_score = max(scores.items(), key=lambda kv: kv[1])
    if top_score >= CLIP_CONFIDENCE_THRESHOLD:
        return RoomGuess(room_type=top_room, confidence=float(top_score), source="clip")
    return RoomGuess(room_type=None, confidence=0.0, source="none")


__all__ = [
    "CLIP_CONFIDENCE_THRESHOLD",
    "MatchSource",
    "RoomGuess",
    "match_photo",
]
