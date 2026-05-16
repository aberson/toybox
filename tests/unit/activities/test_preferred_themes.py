"""Unit tests for :func:`toybox.activities.generator._apply_preferred_themes`.

The helper biases template selection toward a caller-supplied theme
list when ANY eligible template overlaps. Empty preference list and
zero-overlap cases must be no-ops so manual buttons never starve the
picker just because nothing the kid said matched the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass

from toybox.activities.generator import _apply_preferred_themes
from toybox.activities.themes import Theme


@dataclass
class _FakeTemplate:
    id: str
    recommended_themes: tuple[Theme, ...]


def _t(name: str, *themes: Theme) -> _FakeTemplate:
    return _FakeTemplate(id=name, recommended_themes=themes)


def test_empty_preference_is_passthrough() -> None:
    templates = [_t("a", Theme.adventure), _t("b", Theme.magic)]
    assert _apply_preferred_themes(templates, ()) is templates  # type: ignore[arg-type]


def test_filters_to_overlapping_templates_when_any_match() -> None:
    templates = [
        _t("adventure_one", Theme.adventure),
        _t("magic_one", Theme.magic),
        _t("knights_one", Theme.knights),
    ]
    result = _apply_preferred_themes(templates, [Theme.magic])  # type: ignore[arg-type]
    assert [t.id for t in result] == ["magic_one"]


def test_keeps_full_pool_when_no_template_overlaps() -> None:
    # Hint is non-empty but no template matches. Picker must still have
    # something to choose from — preference is a hint, not a hard filter.
    templates = [
        _t("adventure_one", Theme.adventure),
        _t("magic_one", Theme.magic),
    ]
    result = _apply_preferred_themes(templates, [Theme.space])  # type: ignore[arg-type]
    assert [t.id for t in result] == ["adventure_one", "magic_one"]


def test_multi_theme_preference_unions_matchers() -> None:
    templates = [
        _t("adventure_one", Theme.adventure),
        _t("magic_one", Theme.magic),
        _t("knights_one", Theme.knights),
        _t("space_one", Theme.space),
    ]
    result = _apply_preferred_themes(
        templates,  # type: ignore[arg-type]
        [Theme.magic, Theme.knights],
    )
    assert {t.id for t in result} == {"magic_one", "knights_one"}


def test_template_with_multiple_themes_matches_if_any_overlap() -> None:
    templates = [
        _t("adv_magic", Theme.adventure, Theme.magic),
        _t("space_only", Theme.space),
    ]
    result = _apply_preferred_themes(templates, [Theme.magic])  # type: ignore[arg-type]
    assert [t.id for t in result] == ["adv_magic"]


def test_string_preference_values_work_alongside_enum() -> None:
    # Callers may pass raw strings (matches the existing banned_themes
    # convention). Both forms should be equivalent.
    templates = [_t("adv", Theme.adventure), _t("mag", Theme.magic)]
    by_enum = _apply_preferred_themes(templates, [Theme.adventure])  # type: ignore[arg-type]
    by_str = _apply_preferred_themes(templates, ["adventure"])  # type: ignore[arg-type]
    assert [t.id for t in by_enum] == [t.id for t in by_str]
