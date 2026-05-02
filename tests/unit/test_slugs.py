"""Tests for :func:`toybox.db.slugs.derive_slug`."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from toybox.core.errors import ErrorCode
from toybox.db.slugs import InvalidDisplayNameError, derive_slug


def test_basic_slugification() -> None:
    assert derive_slug("Mr. Unicorn", []) == "mr-unicorn"


def test_collision_chain() -> None:
    existing = ["mr-unicorn", "mr-unicorn-2"]
    assert derive_slug("Mr. Unicorn", existing) == "mr-unicorn-3"


def test_empty_string_raises() -> None:
    with pytest.raises(InvalidDisplayNameError) as exc:
        derive_slug("", [])
    assert exc.value.code == ErrorCode.invalid_display_name


def test_whitespace_only_raises() -> None:
    with pytest.raises(InvalidDisplayNameError) as exc:
        derive_slug("   ", [])
    assert exc.value.code == ErrorCode.invalid_display_name


def test_all_symbols_raises() -> None:
    with pytest.raises(InvalidDisplayNameError) as exc:
        derive_slug("!!!", [])
    assert exc.value.code == ErrorCode.invalid_display_name


def test_cjk_transliterates_via_unidecode() -> None:
    """python-slugify's Unidecode pass turns CJK into ASCII romaji.

    Documenting actual behavior: ``"日本語"`` becomes ``"ri-ben-yu"``,
    not an empty string. The "all-symbol rejection" contract only kicks
    in for inputs that strip to empty after the regex pattern.
    """
    assert derive_slug("日本語", []) == "ri-ben-yu"


def test_emoji_only_raises() -> None:
    """Emoji input has no transliteration, strips to empty, must raise.

    Pins behavior so a future regex_pattern refactor doesn't silently let
    symbol-only names slip through as empty slugs.
    """
    with pytest.raises(InvalidDisplayNameError) as exc:
        derive_slug("🦄", [])
    assert exc.value.code == ErrorCode.invalid_display_name


def test_existing_slugs_iterable_can_be_set() -> None:
    assert derive_slug("Mr. Unicorn", {"mr-unicorn"}) == "mr-unicorn-2"


def test_existing_slugs_iterable_can_be_generator() -> None:
    def gen() -> Iterator[str]:
        yield "mr-unicorn"

    assert derive_slug("Mr. Unicorn", gen()) == "mr-unicorn-2"
