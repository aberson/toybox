"""Unit tests for ``toybox.ai.rubric`` — pure scoring functions."""

from __future__ import annotations

import pytest

from toybox.ai.rubric import (
    DIMENSION_KEYS,
    DIMENSIONS,
    DIMENSIONS_BY_KEY,
    MAX_SCORE,
    MIN_SCORE,
    SAFETY_AUTOFAIL,
    InvalidRubricScoresError,
    RubricScores,
    from_mapping,
    render_rubric_for_prompt,
)


def test_six_dimensions_in_canonical_order() -> None:
    """The six dimension keys in canonical order — load-bearing for prompts."""
    assert DIMENSION_KEYS == (
        "schema",
        "age_appropriateness",
        "doability",
        "persona_fidelity",
        "coherence",
        "safety",
    )
    assert len(DIMENSIONS) == 6
    assert set(DIMENSIONS_BY_KEY) == set(DIMENSION_KEYS)


def test_render_rubric_for_prompt_includes_all_keys() -> None:
    text = render_rubric_for_prompt()
    for d in DIMENSIONS:
        assert d.key in text
        # Anchor numbers appear as "5 = ..." and "1 = ..." per dim.
        assert "5 = " in text
        assert "1 = " in text


@pytest.mark.parametrize(
    ("safety_score", "expected_unsafe"),
    [
        (SAFETY_AUTOFAIL, True),
        (SAFETY_AUTOFAIL + 1, False),
        (5, False),
    ],
)
def test_safety_threshold(safety_score: int, expected_unsafe: bool) -> None:
    """``is_unsafe`` is True only at the auto-fail threshold."""
    s = RubricScores(
        schema=4,
        age_appropriateness=4,
        doability=4,
        persona_fidelity=4,
        coherence=4,
        safety=safety_score,
    )
    assert s.is_unsafe is expected_unsafe


def test_mean_quality_excludes_safety() -> None:
    """Headline metric for SFT export — safety must not weight it."""
    s = RubricScores(
        schema=5,
        age_appropriateness=5,
        doability=5,
        persona_fidelity=5,
        coherence=5,
        safety=1,  # would drag the average if not excluded
    )
    assert s.mean_quality == 5.0
    assert s.mean_all == pytest.approx(4.333, rel=1e-2)


def test_to_mapping_round_trips_via_from_mapping() -> None:
    s = RubricScores(
        schema=3,
        age_appropriateness=4,
        doability=5,
        persona_fidelity=2,
        coherence=4,
        safety=5,
        hallucinated_props=("imaginary_dragon",),
        judge_notes="solid but vocab too high",
    )
    payload = s.to_mapping()
    rebuilt = from_mapping(payload)
    assert rebuilt == s


def test_from_mapping_clamps_out_of_range() -> None:
    """Judge sometimes returns 6 ('really great') — clamp rather than reject."""
    s = from_mapping(
        {
            "schema": 7,
            "age_appropriateness": 0,
            "doability": 5,
            "persona_fidelity": 4,
            "coherence": 4,
            "safety": 4,
        }
    )
    assert s.schema == MAX_SCORE
    assert s.age_appropriateness == MIN_SCORE


def test_from_mapping_missing_dimension_raises() -> None:
    with pytest.raises(InvalidRubricScoresError) as exc:
        from_mapping(
            {
                "schema": 4,
                "age_appropriateness": 4,
                "doability": 4,
                "persona_fidelity": 4,
                "coherence": 4,
                # safety missing
            }
        )
    assert "safety" in str(exc.value)


def test_from_mapping_rejects_bool() -> None:
    with pytest.raises(InvalidRubricScoresError):
        from_mapping(
            {
                "schema": True,
                "age_appropriateness": 4,
                "doability": 4,
                "persona_fidelity": 4,
                "coherence": 4,
                "safety": 4,
            }
        )


def test_from_mapping_accepts_string_int() -> None:
    """The judge sometimes emits stringified ints."""
    s = from_mapping(
        {
            "schema": "4",
            "age_appropriateness": "4",
            "doability": "5",
            "persona_fidelity": "3",
            "coherence": "4",
            "safety": "5",
        }
    )
    assert s.schema == 4
    assert s.persona_fidelity == 3


def test_from_mapping_rejects_non_numeric_string() -> None:
    with pytest.raises(InvalidRubricScoresError):
        from_mapping(
            {
                "schema": "great",
                "age_appropriateness": 4,
                "doability": 4,
                "persona_fidelity": 4,
                "coherence": 4,
                "safety": 4,
            }
        )


def test_from_mapping_handles_hallucinated_props_list() -> None:
    s = from_mapping(
        {
            "schema": 4,
            "age_appropriateness": 4,
            "doability": 2,
            "persona_fidelity": 4,
            "coherence": 4,
            "safety": 4,
            "hallucinated_props": ["dragon", "spaceship"],
            "judge_notes": "two invented props",
        }
    )
    assert s.hallucinated_props == ("dragon", "spaceship")
    assert s.judge_notes == "two invented props"


def test_from_mapping_rejects_non_list_hallucinated_props() -> None:
    with pytest.raises(InvalidRubricScoresError):
        from_mapping(
            {
                "schema": 4,
                "age_appropriateness": 4,
                "doability": 4,
                "persona_fidelity": 4,
                "coherence": 4,
                "safety": 4,
                "hallucinated_props": "dragon",
            }
        )
