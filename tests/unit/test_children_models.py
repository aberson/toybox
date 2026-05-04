"""Unit tests for Pydantic models in :mod:`toybox.api.children`.

Pins the *custom-validator* paths only — ``_strip_display_name`` and
``_check_birthdate`` — plus partial-update semantics
(``model_dump(exclude_unset=True)``). Stock Pydantic features
(``min_length``, ``max_length``, ``Literal``, required-field detection,
trivial round-trips) are exercised end-to-end by
``tests/integration/test_children_api.py`` and don't need duplicate
coverage here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from toybox.api.children import ChildProfileCreate, ChildProfileUpdate


def test_create_strips_display_name() -> None:
    body = ChildProfileCreate(display_name="  Alice  ")
    assert body.display_name == "Alice"


def test_create_rejects_whitespace_only_display_name() -> None:
    # Custom validator: stock min_length=1 accepts "   " (length 3 > 0).
    with pytest.raises(ValidationError):
        ChildProfileCreate(display_name="   ")


def test_create_accepts_valid_birthdate() -> None:
    body = ChildProfileCreate(display_name="A", birthdate="2020-01-15")
    assert body.birthdate == "2020-01-15"


def test_create_rejects_bad_birthdate_format() -> None:
    with pytest.raises(ValidationError):
        ChildProfileCreate(display_name="A", birthdate="01/15/2020")


def test_create_rejects_impossible_birthdate() -> None:
    # ``date.fromisoformat`` rejects month/day out of range — the
    # custom ``_check_birthdate`` lifts that into a ValidationError.
    with pytest.raises(ValidationError):
        ChildProfileCreate(display_name="A", birthdate="2020-13-40")


def test_update_strips_display_name_when_present() -> None:
    body = ChildProfileUpdate(display_name="  Bob  ")
    assert body.display_name == "Bob"


def test_update_rejects_whitespace_display_name_when_present() -> None:
    with pytest.raises(ValidationError):
        ChildProfileUpdate(display_name="   ")


def test_update_allows_clearing_optional_fields_with_none() -> None:
    # Pins the wire semantics: passing ``None`` keeps the key in
    # exclude_unset output, so the PATCH handler runs ``col = NULL``.
    body = ChildProfileUpdate(birthdate=None, pronouns=None)
    dumped = body.model_dump(exclude_unset=True)
    assert dumped == {"birthdate": None, "pronouns": None}
