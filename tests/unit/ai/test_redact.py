"""Unit tests for the Phase E Step 27 (E3) PII redactor.

The redactor is a pure-function PII scrubber at ``src/toybox/ai/redact.py``
that exports two names:

- ``PII_FILTER_VERSION`` — semver string constant pinned at ``"1.0"``.
  Test #1 below is the regression pin from the carve-out plan §"Risks":
  any change to the regex body that doesn't also bump the constant must
  fail the suite.
- ``redact_pii(text, *, child_names) -> str`` — pure function. No I/O,
  no DB access, no logging. ``child_names`` is keyword-only.

Scrub order: child names → emails → phones → addresses. Child-name
match is intentionally conservative (title-case + word-boundary only)
so ``Sage`` is redacted but ``sage advice`` is not. Each ``child_name``
is wrapped in ``re.escape()`` before pattern construction so display
names containing regex metacharacters do not crash compilation.
"""

from __future__ import annotations

import pytest
from toybox.ai.redact import PII_FILTER_VERSION, redact_pii

# --------------------------------------------------------------------- constant


def test_pii_filter_version_pinned_to_1_0() -> None:
    """Regression pin: ``PII_FILTER_VERSION`` must equal ``"1.0"``.

    Per the carve-out plan §"Risks": any change to the redactor body
    that doesn't also bump the constant must fail this test. Forces
    contributors to make the version bump explicit.
    """
    assert PII_FILTER_VERSION == "1.0"


# --------------------------------------------------------------------- empty / passthrough


def test_empty_input_returns_empty() -> None:
    assert redact_pii("", child_names=[]) == ""


def test_no_pii_passthrough() -> None:
    """Positive control: lowercase ``sage`` is NOT redacted.

    Documented in plan §"Design Decisions": title-case + word-boundary
    is intentional so ordinary play text isn't shredded.
    """
    text = "We had sage advice today."
    assert redact_pii(text, child_names=[]) == text


# --------------------------------------------------------------------- child names


def test_child_name_title_case_match() -> None:
    assert (
        redact_pii("Hello Sage, want to play?", child_names=["Sage"])
        == "Hello [REDACTED], want to play?"
    )


def test_child_name_lowercase_not_redacted() -> None:
    """Title-case + word-boundary intentional: ``sage`` lowercase survives."""
    assert (
        redact_pii("Hello Sage, I love sage advice.", child_names=["Sage"])
        == "Hello [REDACTED], I love sage advice."
    )


def test_multiple_child_names() -> None:
    assert (
        redact_pii("Sage and River played.", child_names=["Sage", "River"])
        == "[REDACTED] and [REDACTED] played."
    )


def test_child_name_with_dot_metacharacter() -> None:
    """``re.escape`` must be applied — ``Mr.Unicorn`` has a regex ``.``."""
    assert redact_pii("Hi Mr.Unicorn", child_names=["Mr.Unicorn"]) == "Hi [REDACTED]"


def test_child_name_with_bracket_metacharacters() -> None:
    """``re.escape`` covers ``[`` and ``]`` — would otherwise crash compile."""
    assert (
        redact_pii(
            "Hello Sage [the great] friend",
            child_names=["Sage [the great]"],
        )
        == "Hello [REDACTED] friend"
    )


# --------------------------------------------------------------------- email


def test_email_with_trailing_period() -> None:
    """Greedy ``\\S+@\\S+\\.\\S+`` consumes the trailing period.

    Acceptable per spec §"Step 2 done when": prefer the simpler
    greedy-match behavior. ``foo@bar.com.`` → the period after ``com``
    is part of the matched non-whitespace span.
    """
    assert redact_pii("Contact me at foo@bar.com.", child_names=[]) == "Contact me at [REDACTED]"


# --------------------------------------------------------------------- phone


@pytest.mark.parametrize(
    "phone_text",
    [
        "Call 555-123-4567",
        "Call 555.123.4567",
        "Call 555 123 4567",
        "Call 5551234567",
    ],
)
def test_phone_various_separators(phone_text: str) -> None:
    """NANP phone shape ``\\d{3}[-.\\s]?\\d{3}[-.\\s]?\\d{4}``."""
    assert redact_pii(phone_text, child_names=[]) == "Call [REDACTED]"


# --------------------------------------------------------------------- address


@pytest.mark.parametrize(
    "address_text, expected",
    [
        ("Meet at 123 Main St.", "Meet at [REDACTED]."),
        ("Meet at 456 Oak Avenue", "Meet at [REDACTED]"),
        ("Meet at 789 Pine Boulevard", "Meet at [REDACTED]"),
    ],
)
def test_address_various_suffixes(address_text: str, expected: str) -> None:
    """Closed street-suffix list with ``\\b`` boundaries on both ends."""
    assert redact_pii(address_text, child_names=[]) == expected


@pytest.mark.parametrize(
    "narrative_text",
    [
        "1 little duck waddled to the park",
        "5 jumping monkeys",
    ],
)
def test_address_negative_control_narrative_numbers(narrative_text: str) -> None:
    """Narrative ``<digit> <word> <word>`` must NOT match the address regex.

    The closed suffix list (St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|
    Ln|Lane|Dr|Drive|Ct|Court|Way|Pl|Place) with ``\\b`` boundaries
    prevents over-redaction of activity-step narratives. See plan
    §"Risks" — "1 little duck waddled to the park" is the canonical
    test case from the carve-out spec.
    """
    assert redact_pii(narrative_text, child_names=[]) == narrative_text


# --------------------------------------------------------------------- multi-PII


def test_multiple_pii_tokens_in_one_input() -> None:
    """Full pipeline: child name + email + phone + address in one string.

    Trace:
    - Child-name pass scrubs ``Sage`` (title-case, ``\\b``) → ``[REDACTED]``.
    - Email pass scrubs ``sage@example.com`` (lowercase inside email
      is caught by the email regex, not the child-name pass) → ``[REDACTED]``.
    - Phone pass scrubs ``555-123-4567`` → ``[REDACTED]``.
    - Address pass scrubs ``123 Main St`` → ``[REDACTED]`` (the trailing
      period is outside the address regex, so the final ``.`` survives).
    """
    text = "Email Sage at sage@example.com or call 555-123-4567 at 123 Main St."
    expected = "Email [REDACTED] at [REDACTED] or call [REDACTED] at [REDACTED]."
    assert redact_pii(text, child_names=["Sage"]) == expected


# --------------------------------------------------------------------- determinism


def test_pure_function_no_side_effects() -> None:
    """Same input → same output across multiple calls.

    Cheap smoke test: catches accidental shared-state regressions
    (e.g. a stateful regex cache that mutates input across calls).
    """
    text = "Hello Sage at 555-123-4567"
    out1 = redact_pii(text, child_names=["Sage"])
    out2 = redact_pii(text, child_names=["Sage"])
    assert out1 == out2


# --------------------------------------------------------------------- signature


def test_child_names_is_keyword_only() -> None:
    """Pins the signature ``def redact_pii(text, *, child_names)``.

    Positional call must raise ``TypeError`` so callers cannot
    accidentally bind a stray sequence to ``child_names`` without
    naming it.
    """
    with pytest.raises(TypeError):
        redact_pii("text", ["Sage"])  # type: ignore[misc]
