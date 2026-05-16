"""Unit tests for :mod:`toybox.activities.topic_extract`."""

from __future__ import annotations

from toybox.activities.themes import Theme
from toybox.activities.topic_extract import extract_themes


def test_single_keyword_returns_its_theme() -> None:
    assert extract_themes(["let us go camping today"]) == [Theme.adventure]


def test_no_keyword_returns_empty_list() -> None:
    # Caller treats empty as a no-bias signal.
    assert extract_themes(["the sky is wide and open"]) == []


def test_empty_input_returns_empty_list() -> None:
    assert extract_themes([]) == []
    assert extract_themes(["", None, "  "]) == []  # type: ignore[list-item]


def test_multiple_themes_ranked_by_vote_count() -> None:
    # "castle" + "dragon" => 2 knights votes; "magic" + "spell" => 2 magic
    # votes. Tie broken by Theme enum order: magic appears before knights
    # in the enum, so magic ranks first.
    result = extract_themes(["castle and dragon, magic and spell"])
    assert Theme.knights in result
    assert Theme.magic in result
    # Ties resolve to enum order (magic declared before knights).
    assert result.index(Theme.magic) < result.index(Theme.knights)


def test_vote_count_beats_enum_order() -> None:
    # 3 adventure votes vs 1 magic vote: adventure wins even though
    # magic appears earlier in the enum.
    result = extract_themes(["camping camp explorer with a single spell"])
    assert result[0] is Theme.adventure
    assert Theme.magic in result


def test_case_insensitive_matching() -> None:
    assert extract_themes(["CAMPING in the JUNGLE"]) == [Theme.adventure]


def test_word_boundary_avoids_substring_false_positives() -> None:
    # "sun" maps to weather; "Sunday" should not match.
    assert extract_themes(["happy Sunday everyone"]) == []


def test_multiple_transcripts_aggregate_votes() -> None:
    # Two transcripts, one pirate-vote each. Total of 2 pirate votes.
    result = extract_themes(["look at the pirate ship", "ahoy from the captain"])
    assert result == [Theme.pirates]


def test_each_keyword_maps_to_exactly_one_theme() -> None:
    # Build-time invariant: no synonym appears in two theme sets. Reading
    # the dict triggers the module-level guard. If a future edit duplicates
    # a keyword, this test imports topic_extract again and the duplicate
    # check raises RuntimeError at import time — pytest will surface it.
    import importlib

    import toybox.activities.topic_extract as te

    importlib.reload(te)  # re-runs the build-time duplicate check
