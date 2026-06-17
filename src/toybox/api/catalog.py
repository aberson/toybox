"""``GET /api/catalog`` — full offline template catalog.

Returns every template across all four ``SUPPORTED_INTENTS`` as a flat
list, deduplicated by template id (same logic as :mod:`toybox.api.search`
but without a query filter and without a 20-result cap).

No auth required — the catalog is read-only and contains no personal
data.  The route is intentionally simple: load all templates, deduplicate,
return.  The dataset (200–1 000 templates) is small enough to return in
full on each request without pagination.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from ..activities.generator import SUPPORTED_INTENTS, _load_intent_templates

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class CatalogEntry(BaseModel):
    """One template entry in the catalog response."""

    id: str
    title: str
    intent: str
    themes: list[str]
    step_count: int
    # SWR Step 4 wire-shape fix: the authoritative "Elements" discriminator
    # is a per-step ``element_id`` (see ``generator._filter_by_category`` and
    # the runtime ``categorize()`` helper), NOT a theme. ``periodic_table`` is
    # not a member of the :class:`~toybox.activities.themes.Theme` enum, so it
    # can never appear in ``themes``; element templates carry ordinary themes
    # like ``friendship``/``silly``. Surface a boolean so the CatalogPanel can
    # bucket Elements correctly off the wire instead of guessing from a theme
    # that does not exist.
    has_element: bool


class CatalogResponse(BaseModel):
    """Wire shape for ``GET /api/catalog``."""

    entries: list[CatalogEntry]
    total: int


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("", response_model=CatalogResponse)
def get_catalog() -> CatalogResponse:
    """Return all templates across all supported intents.

    Iterates over :data:`SUPPORTED_INTENTS` in order, loads each intent's
    template pool via :func:`_load_intent_templates`, and deduplicates by
    template id (first-seen wins, same as the search endpoint).  Per-intent
    load errors are logged and skipped — a bad intent does not crash the
    response.
    """
    entries: list[CatalogEntry] = []
    seen_ids: set[str] = set()

    for intent in SUPPORTED_INTENTS:
        try:
            templates = _load_intent_templates(intent)
        except Exception:
            _logger.exception("_load_intent_templates failed for intent=%r", intent)
            continue
        for tmpl in templates:
            if tmpl.id in seen_ids:
                continue
            seen_ids.add(tmpl.id)
            entries.append(
                CatalogEntry(
                    id=tmpl.id,
                    title=tmpl.title,
                    intent=intent,
                    themes=[t.value for t in tmpl.recommended_themes],
                    step_count=len(tmpl.steps),
                    has_element=any(
                        step.element_id is not None for step in tmpl.steps
                    ),
                )
            )

    return CatalogResponse(entries=entries, total=len(entries))


__all__ = [
    "CatalogEntry",
    "CatalogResponse",
    "router",
]
