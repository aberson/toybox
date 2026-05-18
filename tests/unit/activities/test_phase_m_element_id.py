"""Phase M Step M3 — element_id field + cross-corpus validator gate.

Pins:

1. ``Step.element_id`` is an optional Pydantic field — pre-M3 templates
   that omit it parse unchanged.
2. The regex gate (``^[a-z]{1,3}-[0-9]{1,3}$``) rejects malformed ids
   at the Pydantic layer BEFORE the corpus lookup fires.
3. :func:`validate_template` raises :class:`TemplateGraphError` with a
   message containing the offending id when ``element_id`` doesn't
   resolve via :func:`toybox.activities.element_corpus.get_element`.
4. A valid ``element_id`` (e.g. ``au-79`` — present in the shipped
   corpus) passes validation.
5. ``element_id: null`` passes (the field is optional).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from toybox.activities._validator import (
    TemplateGraphError,
    validate_template,
)
from toybox.activities.models import (
    Step,
    Template,
)


def _build_template_with_element(element_id: str | None) -> Template:
    """Build a minimal 3-step template whose first step references
    ``element_id`` (or omits it when ``None``)."""
    first_step_kwargs: dict[str, object] = {"text": "look at this element"}
    if element_id is not None:
        first_step_kwargs["element_id"] = element_id
    return Template(
        id="m3_t",
        title="M3 Test",
        buckets=["always"],
        steps=[
            Step(**first_step_kwargs),  # type: ignore[arg-type]
            Step(text="next"),
            Step(text="last"),
        ],
    )


# ---------------------------------------------------------------------------
# Pydantic-layer field shape — trimmed in iter-2 per reviewer feedback.
#
# The iter-1 suite included three tautological tests (defaults to None,
# accepts au-79, accepts uut-113) that exercised library behavior rather
# than M3's own gate. The malformed-id parametrize was also wider than
# needed — three representative bad shapes cover the boundary
# (uppercase, missing separator, over-length number) without
# enumerating every variant the regex was designed to reject.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "AU-79",  # uppercase letters
        "au79",  # missing separator
        "au-1234",  # atomic number too long (>3 digits)
    ],
)
def test_step_element_id_rejects_malformed_value_at_pydantic_layer(
    bad_id: str,
) -> None:
    """The regex catches malformed ids BEFORE the corpus lookup fires
    — so a typo'd id surfaces with the schema's pattern message rather
    than a misleading "unknown element" error. Three representative
    shapes (case, separator, length) cover the boundary; the full
    iter-1 9-wide enumeration was redundant with the regex's own
    pattern."""
    with pytest.raises(ValidationError):
        Step(text="t", element_id=bad_id)


# ---------------------------------------------------------------------------
# Cross-corpus validator gate
# ---------------------------------------------------------------------------


def test_validate_template_accepts_null_element_id() -> None:
    """``element_id`` is optional — no corpus lookup when absent."""
    template = _build_template_with_element(None)
    validate_template(template)  # must not raise


def test_validate_template_accepts_known_element_id() -> None:
    """``au-79`` is in the shipped corpus — validator passes."""
    template = _build_template_with_element("au-79")
    validate_template(template)  # must not raise


def test_validate_template_rejects_unknown_element_id() -> None:
    """A syntactically valid but unknown id raises with the offending
    id in the message — the operator must be able to find the typo
    in the template JSON without grepping the corpus."""
    template = _build_template_with_element("xx-999")
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "xx-999" in msg
    assert "element" in msg.lower()


def test_validate_template_rejects_unknown_element_id_names_template_and_step() -> None:
    """The error message must name the template AND the step id (or
    array index when the step has no id) so the operator can pinpoint
    the offending row in a large template file."""
    template = Template(
        id="big_template",
        title="Big",
        buckets=["always"],
        steps=[
            Step(text="a"),
            Step(text="b", element_id="zz-111"),
            Step(text="c"),
        ],
    )
    with pytest.raises(TemplateGraphError) as excinfo:
        validate_template(template)
    msg = str(excinfo.value)
    assert "big_template" in msg
    assert "zz-111" in msg
    # No explicit step id was set → message falls back to the array index.
    assert "index 1" in msg
