"""Pure-function PII scrubber for Phase E step 27 (E3).

Scrubs four classes of PII from a free-text string, in this order:

1. Child names (title-case + word-boundary only — lowercase ``sage`` survives
   on purpose so ordinary play text isn't shredded). Each ``child_name`` is
   wrapped in :func:`re.escape` before pattern construction so display names
   containing regex metacharacters do not crash compilation or match
   unintended text.
2. Emails — greedy ``\\S+@\\S+\\.\\S+`` (consumes a trailing period).
3. Phones — NANP shape ``\\d{3}[-.\\s]?\\d{3}[-.\\s]?\\d{4}``.
4. Addresses — ``<digits> <word> <closed-suffix>`` with word boundaries on
   both ends so narratives like "1 little duck" do not match.

The substitution token is the literal string ``[REDACTED]`` (fixed; not
configurable). No I/O, no DB access, no logging — stdlib :mod:`re` only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

PII_FILTER_VERSION: Final[str] = "1.0"

_REDACTED: Final[str] = "[REDACTED]"

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"\S+@\S+\.\S+")
_PHONE_RE: Final[re.Pattern[str]] = re.compile(r"\d{3}[-.\s]?\d{3}[-.\s]?\d{4}")
_ADDRESS_RE: Final[re.Pattern[str]] = re.compile(
    r"\d+\s+\w+\s+"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|"
    r"Ln|Lane|Dr|Drive|Ct|Court|Way|Pl|Place)\b"
)


def redact_pii(text: str, *, child_names: Sequence[str]) -> str:
    """Return ``text`` with child names, emails, phones, and addresses scrubbed.

    Child-name match is title-case + word-boundary only by design — lowercase
    occurrences (e.g. ``sage advice``) survive. Each name in ``child_names``
    is passed through :func:`re.escape` so regex metacharacters in display
    names do not crash compilation.
    """
    if child_names:
        name_pattern = re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(name) for name in child_names) + r")(?!\w)"
        )
        text = name_pattern.sub(_REDACTED, text)
    text = _EMAIL_RE.sub(_REDACTED, text)
    text = _PHONE_RE.sub(_REDACTED, text)
    text = _ADDRESS_RE.sub(_REDACTED, text)
    return text
