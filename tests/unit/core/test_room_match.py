"""Unit coverage for :mod:`toybox.core.room_match` (Phase X Step X4).

All paths run with fake/stub classifiers — no ONNX model, no download.
Asserts the filename-first short-circuit, the CLIP confidence floor, the
N/A fallback, the never-raise invariant, and (code-quality.md §2) that the
keyword map is derived from and covers every ROOM_TYPE.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from toybox.ai.room_classifier import RoomClassifier, RoomClassifierUnavailable
from toybox.core.room_match import (
    _KEYWORD_TO_ROOM,
    CLIP_CONFIDENCE_THRESHOLD,
    RoomGuess,
    match_photo,
)
from toybox.core.room_types import ROOM_TYPES


class _RecordingClassifier:
    """Classifier stub that records whether it was called + canned scores."""

    def __init__(self, scores: dict[str, float] | None = None) -> None:
        self._scores = scores or {}
        self.calls: list[bytes] = []

    def classify(self, image_bytes: bytes) -> dict[str, float]:
        self.calls.append(image_bytes)
        return self._scores


class _RaisingClassifier:
    """Classifier stub that raises the given exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[bytes] = []

    def classify(self, image_bytes: bytes) -> dict[str, float]:
        self.calls.append(image_bytes)
        raise self._exc


_IMG = b"\x89PNG\r\n\x1a\n-fake-pixels"


class _FakeImageSession:
    """Minimal real-classifier session: canned embed + named outputs."""

    def __init__(self, embedding: NDArray[np.float32]) -> None:
        self._embedding = embedding

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, NDArray[np.float32]],
    ) -> list[NDArray[np.float32]]:
        return [self._embedding.reshape(1, -1)]

    def get_inputs(self) -> list[object]:
        class _Inp:
            name = "pixel_values"

        return [_Inp()]

    def get_outputs(self) -> list[object]:
        class _Out:
            name = "image_embeds"

        return [_Out()]


def _real_classifier(embed_dim: int = 4) -> RoomClassifier:
    """A genuine RoomClassifier (real preprocess + Pillow decode path)."""
    image_embed = np.zeros(embed_dim, dtype=np.float32)
    image_embed[0] = 1.0
    text_embeds = np.full((len(ROOM_TYPES), embed_dim), 0.01, dtype=np.float32)
    return RoomClassifier(
        image_session=_FakeImageSession(image_embed),
        text_embeds=text_embeds,
    )


def test_filename_keyword_wins_without_calling_classifier() -> None:
    clf = _RecordingClassifier({"bathroom": 0.99})  # would mislead if called
    guess = match_photo("master-bedroom.jpg", _IMG, classifier=clf)
    assert guess == RoomGuess(room_type="bedroom", confidence=1.0, source="filename")
    assert clf.calls == []  # classifier NOT consulted


def test_ambiguous_filename_uses_clip_top_label() -> None:
    clf = _RecordingClassifier(
        {"bedroom": 0.10, "bathroom": 0.41, "kitchen": 0.15},
    )
    guess = match_photo("IMG_1234.jpg", _IMG, classifier=clf)
    assert guess.room_type == "bathroom"
    assert guess.source == "clip"
    assert guess.confidence == 0.41
    assert clf.calls == [_IMG]  # classifier consulted exactly once


def test_all_scores_below_threshold_returns_na() -> None:
    below = CLIP_CONFIDENCE_THRESHOLD - 0.05
    clf = _RecordingClassifier({rt: below for rt in ROOM_TYPES})
    guess = match_photo("IMG_1234.jpg", _IMG, classifier=clf)
    assert guess == RoomGuess(room_type=None, confidence=0.0, source="none")


def test_score_exactly_at_threshold_is_accepted() -> None:
    clf = _RecordingClassifier({"office": CLIP_CONFIDENCE_THRESHOLD})
    guess = match_photo("IMG_1234.jpg", _IMG, classifier=clf)
    assert guess.room_type == "office"
    assert guess.source == "clip"


def test_classifier_unavailable_falls_back_to_na_no_raise() -> None:
    clf = _RaisingClassifier(RoomClassifierUnavailable("model not downloaded"))
    guess = match_photo("IMG_1234.jpg", _IMG, classifier=clf)
    assert guess == RoomGuess(room_type=None, confidence=0.0, source="none")


def test_classifier_unavailable_with_filename_hit_uses_filename() -> None:
    # Filename signal wins BEFORE the classifier is even called, so an
    # unavailable model doesn't matter.
    clf = _RaisingClassifier(RoomClassifierUnavailable("model not downloaded"))
    guess = match_photo("kitchen-2.png", _IMG, classifier=clf)
    assert guess == RoomGuess(room_type="kitchen", confidence=1.0, source="filename")
    assert clf.calls == []


def test_arbitrary_classifier_error_never_raises() -> None:
    clf = _RaisingClassifier(RuntimeError("onnx blew up"))
    guess = match_photo("IMG_9999.jpg", _IMG, classifier=clf)
    assert guess == RoomGuess(room_type=None, confidence=0.0, source="none")


def test_real_classifier_bad_bytes_falls_back_to_na_no_raise() -> None:
    # NOT a stub: a genuine RoomClassifier whose real Pillow decode path
    # raises UnidentifiedImageError on garbage bytes. match_photo must
    # catch it and degrade to N/A (never raise) — the real decode→catch
    # round trip the simulated raising-stub test cannot exercise.
    clf = _real_classifier()
    guess = match_photo("IMG_garbage.jpg", b"not-a-png", classifier=clf)
    assert guess == RoomGuess(room_type=None, confidence=0.0, source="none")


def test_empty_scores_returns_na() -> None:
    clf = _RecordingClassifier({})
    guess = match_photo("IMG_1234.jpg", _IMG, classifier=clf)
    assert guess == RoomGuess(room_type=None, confidence=0.0, source="none")


def test_none_filename_falls_through_to_classifier() -> None:
    clf = _RecordingClassifier({"garage": 0.5})
    guess = match_photo(None, _IMG, classifier=clf)
    assert guess.room_type == "garage"
    assert guess.source == "clip"


def test_filename_separatorless_substring_match() -> None:
    clf = _RecordingClassifier({"bathroom": 0.99})
    guess = match_photo("masterbedroom.jpg", _IMG, classifier=clf)
    assert guess.room_type == "bedroom"
    assert guess.source == "filename"
    assert clf.calls == []


def test_short_synonym_substring_does_not_false_positive() -> None:
    # "bedford" embeds "bed" and "denver" embeds "den", but neither is a
    # whole token and both short synonyms are below the substring-fallback
    # length floor → must NOT match; the classifier is consulted instead.
    clf = _RecordingClassifier({"kitchen": 0.40})
    bedford = match_photo("bedford.jpg", _IMG, classifier=clf)
    assert bedford.source == "clip"  # fell through to classifier, not "filename"
    assert bedford.room_type == "kitchen"

    clf2 = _RecordingClassifier({"kitchen": 0.40})
    denver = match_photo("denver.png", _IMG, classifier=clf2)
    assert denver.source == "clip"
    assert denver.room_type == "kitchen"


def test_short_synonym_false_positive_falls_through_to_na() -> None:
    # With a low-confidence classifier the same filenames resolve to N/A
    # rather than being mislabeled bedroom / living_room by substring.
    clf = _RecordingClassifier({rt: 0.10 for rt in ROOM_TYPES})
    assert match_photo("bedford.jpg", _IMG, classifier=clf).room_type is None
    assert match_photo("denver.png", _IMG, classifier=clf).room_type is None
    assert match_photo("my-playlist.jpg", _IMG, classifier=clf).room_type is None


def test_short_synonym_still_matches_as_whole_token() -> None:
    # The exact-token pass is untouched: "bed" / "den" / "play" as their own
    # token still map correctly — only the loose substring path is tightened.
    clf = _RecordingClassifier({"kitchen": 0.99})
    assert match_photo("my-bed-photo.jpg", _IMG, classifier=clf).room_type == "bedroom"
    assert match_photo("the-den-2.png", _IMG, classifier=clf).room_type == "living_room"
    assert match_photo("play-area.jpg", _IMG, classifier=clf).room_type == "playroom"


# ---- code-quality.md §2: keyword map derived from + covers every ROOM_TYPE


def test_keyword_map_covers_every_room_type() -> None:
    covered = set(_KEYWORD_TO_ROOM.values())
    assert covered == set(ROOM_TYPES), (
        f"keyword map must map at least one keyword to every ROOM_TYPE; "
        f"missing: {set(ROOM_TYPES) - covered}"
    )


def test_keyword_map_only_references_real_room_types() -> None:
    # No orphan / drifted room values in the map (one source of truth).
    assert set(_KEYWORD_TO_ROOM.values()) <= set(ROOM_TYPES)


def test_every_room_type_has_at_least_one_keyword() -> None:
    per_room: dict[str, int] = {rt: 0 for rt in ROOM_TYPES}
    for room in _KEYWORD_TO_ROOM.values():
        per_room[room] += 1
    for room, count in per_room.items():
        assert count >= 1, f"ROOM_TYPE {room!r} has no keyword"
