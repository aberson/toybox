"""Slug derivation for entity IDs.

Pure function: callers fetch ``existing_slugs`` from the DB and pass them in.
"""

from __future__ import annotations

from collections.abc import Iterable

from slugify import slugify

from ..core.errors import ErrorCode

_SLUG_REGEX_PATTERN = r"[^a-z0-9\-]"


class InvalidDisplayNameError(ValueError):
    """Raised when ``display_name`` slugifies to an empty string."""

    def __init__(self, display_name: str) -> None:
        self.display_name = display_name
        self.code = ErrorCode.invalid_display_name
        super().__init__(
            f"display_name {display_name!r} cannot be slugified (code={self.code.value})"
        )


def derive_slug(display_name: str, existing_slugs: Iterable[str]) -> str:
    """Return a unique slug for ``display_name``.

    The base slug is produced via ``python-slugify`` with the regex pattern
    fixed to ``[^a-z0-9\\-]`` so the output is restricted to lowercase
    ASCII letters, digits, and hyphens. On collision with any value in
    ``existing_slugs`` we append ``-2``, ``-3``, ... until unique.

    Args:
        display_name: Human-entered name, e.g. ``"Mr. Unicorn"``.
        existing_slugs: Slugs already in use for the same entity kind.

    Returns:
        A non-empty unique slug.

    Raises:
        InvalidDisplayNameError: If ``display_name`` slugifies to an empty
            string (empty input, whitespace only, or all symbols).
    """
    base = slugify(
        display_name,
        lowercase=True,
        separator="-",
        regex_pattern=_SLUG_REGEX_PATTERN,
    )
    if not base:
        raise InvalidDisplayNameError(display_name)

    taken = set(existing_slugs)
    if base not in taken:
        return base

    suffix = 2
    while True:
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
        suffix += 1


__all__ = ["InvalidDisplayNameError", "derive_slug"]
