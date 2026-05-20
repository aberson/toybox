"""Unit tests for :func:`toybox.activities.generator._apply_category_filter`.

The helper mirrors the frontend ``categorize()`` precedence so a "Trigger
now" from a Play sub-tab generates an activity that lands in that same
sub-tab. Soft-fallback semantics on empty match (degrade to no-op rather
than starve the picker) match :func:`_apply_preferred_themes`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from toybox.activities.generator import _apply_category_filter
from toybox.activities.themes import Theme


@dataclass
class _FakeStep:
    element_id: str | None = None


@dataclass
class _FakeTemplate:
    id: str
    steps: tuple[_FakeStep, ...] = field(default_factory=tuple)
    recommended_themes: tuple[Theme, ...] = field(default_factory=tuple)


def _adv(name: str) -> _FakeTemplate:
    """Adventure: no element_id, no feelings theme."""
    return _FakeTemplate(id=name, steps=(_FakeStep(),), recommended_themes=(Theme.adventure,))


def _elem(name: str, element_id: str = "au-79") -> _FakeTemplate:
    """Element: at least one step with element_id set."""
    return _FakeTemplate(
        id=name,
        steps=(_FakeStep(element_id=element_id),),
        recommended_themes=(),
    )


def _sel(name: str) -> _FakeTemplate:
    """SEL: recommended_themes contains 'feelings'."""
    return _FakeTemplate(
        id=name,
        steps=(_FakeStep(),),
        recommended_themes=(Theme.feelings,),
    )


def test_none_category_is_passthrough() -> None:
    templates = [_adv("a"), _elem("b"), _sel("c")]
    assert _apply_category_filter(templates, None) is templates  # type: ignore[arg-type]


def test_elements_filters_to_element_bearing_templates() -> None:
    templates = [_adv("adv_a"), _elem("elem_b"), _sel("sel_c"), _elem("elem_d")]
    result = _apply_category_filter(templates, "elements")  # type: ignore[arg-type]
    assert {t.id for t in result} == {"elem_b", "elem_d"}


def test_feelings_friends_filters_to_feelings_themed_templates() -> None:
    templates = [_adv("adv_a"), _elem("elem_b"), _sel("sel_c"), _sel("sel_d")]
    result = _apply_category_filter(templates, "feelings-friends")  # type: ignore[arg-type]
    assert {t.id for t in result} == {"sel_c", "sel_d"}


def test_adventures_excludes_element_and_feelings() -> None:
    templates = [_adv("adv_a"), _elem("elem_b"), _sel("sel_c"), _adv("adv_d")]
    result = _apply_category_filter(templates, "adventures")  # type: ignore[arg-type]
    assert {t.id for t in result} == {"adv_a", "adv_d"}


def test_elements_falls_back_when_no_element_template() -> None:
    # No element template available -> soft fallback returns all templates
    # so the picker doesn't starve (matches _apply_preferred_themes shape).
    templates = [_adv("adv_a"), _sel("sel_b")]
    result = _apply_category_filter(templates, "elements")  # type: ignore[arg-type]
    assert [t.id for t in result] == ["adv_a", "sel_b"]


def test_feelings_falls_back_when_no_sel_template() -> None:
    templates = [_adv("adv_a"), _elem("elem_b")]
    result = _apply_category_filter(templates, "feelings-friends")  # type: ignore[arg-type]
    assert [t.id for t in result] == ["adv_a", "elem_b"]


def test_adventures_falls_back_when_pool_is_all_element_or_sel() -> None:
    templates = [_elem("elem_a"), _sel("sel_b")]
    result = _apply_category_filter(templates, "adventures")  # type: ignore[arg-type]
    assert [t.id for t in result] == ["elem_a", "sel_b"]


def test_unknown_category_is_passthrough() -> None:
    # Typo'd category shouldn't starve the picker. Degrade to no-op.
    templates = [_adv("adv_a"), _elem("elem_b")]
    result = _apply_category_filter(templates, "songs")  # type: ignore[arg-type]
    assert result is templates


def test_element_template_with_some_steps_lacking_id_still_matches() -> None:
    # Phase N element_microgame templates have element_id on every step
    # by design. Phase M meet_element_* templates also (per M4). But a
    # hypothetical mixed template (element_id only on some steps) should
    # still categorize as "elements" — any-step rule matches the
    # frontend categorize() precedence.
    template = _FakeTemplate(
        id="mixed_a",
        steps=(_FakeStep(), _FakeStep(element_id="h-1"), _FakeStep()),
    )
    result = _apply_category_filter([template], "elements")  # type: ignore[arg-type]
    assert [t.id for t in result] == ["mixed_a"]


def test_element_template_with_feelings_theme_categorizes_as_elements() -> None:
    # Hypothetically-mixed: element_id + feelings theme. Frontend's
    # categorize() precedence is Elements > Feelings, so the filter
    # should pick this up under "elements" but NOT under
    # "feelings-friends" — even though it has the feelings theme.
    # Actually re-read the helper: filtering rules are independent
    # category-of-membership checks, not precedence-respecting. The
    # "elements" filter accepts it; the "feelings-friends" filter ALSO
    # accepts it. Operator intent on the trigger is "from this tab give
    # me one that BELONGS here" — both surfaces would include it. The
    # frontend's categorize() picks one bucket on display; the trigger
    # category determines the POOL the picker draws from.
    template = _FakeTemplate(
        id="mixed_elem_feel",
        steps=(_FakeStep(element_id="au-79"),),
        recommended_themes=(Theme.feelings,),
    )
    elements_result = _apply_category_filter([template], "elements")  # type: ignore[arg-type]
    feelings_result = _apply_category_filter(
        [template],
        "feelings-friends",
    )  # type: ignore[arg-type]
    assert [t.id for t in elements_result] == ["mixed_elem_feel"]
    assert [t.id for t in feelings_result] == ["mixed_elem_feel"]
