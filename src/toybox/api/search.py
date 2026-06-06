"""``GET /api/search`` — activity and template search.

Returns two lists:

* ``past_activities`` — LIKE scan on ``activities.summary`` (title
  field in the persisted JSON blob), newest first, up to 20.
* ``templates`` — case-insensitive substring match on template id or
  title across all loaded intents, up to 20.

No auth required: read-only, and the activity summaries contain the
same data the parent UI already displays in the queue.  The route is
intentionally simple — no pagination, no relevance ranking — because
the target dataset (200–1 000 templates + a few hundred activities) is
small enough that a LIKE scan is instantaneous on a local SQLite file.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..activities.generator import SUPPORTED_INTENTS, _load_intent_templates
from ..db import connect, resolve_db_path

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------


def get_search_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: open a SQLite connection, yield, close."""
    conn = connect(resolve_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class PastActivityResult(BaseModel):
    """One past activity hit from the LIKE scan."""

    id: str
    title: str | None
    template_id: str | None
    state: str
    created_at: str  # ISO 8601


class TemplateResult(BaseModel):
    """One template hit from the in-memory registry scan."""

    id: str
    title: str
    intent: str


class SearchResponse(BaseModel):
    """Wire shape for ``GET /api/search``."""

    past_activities: list[PastActivityResult]
    templates: list[TemplateResult]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("", response_model=SearchResponse)
def get_search(
    q: Annotated[str, Query(min_length=1, max_length=100)],
    conn: Annotated[sqlite3.Connection, Depends(get_search_db)],
) -> SearchResponse:
    """Search past activities and templates for ``q``.

    Returns at most 20 rows per section.  Empty/whitespace-only queries
    are caught by the ``min_length=1`` validator before reaching here;
    FastAPI returns 422 automatically.  Callers that want a
    "no results" response should pass a non-empty query.

    ``template_id`` is extracted from the ``summary`` JSON blob rather
    than a dedicated column — the activities table stores the full
    envelope in ``summary`` as:
    ``{"title": "...", "template_id": "...", "metadata": {...}}``.
    """
    stripped = q.strip()
    if not stripped:
        # Defensive: shouldn't reach here given min_length=1, but handle
        # whitespace-only strings gracefully.
        return SearchResponse(past_activities=[], templates=[])

    past = _search_past_activities(conn, stripped)
    templates = _search_templates(stripped)
    return SearchResponse(past_activities=past, templates=templates)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _search_past_activities(
    conn: sqlite3.Connection,
    q: str,
) -> list[PastActivityResult]:
    """LIKE scan on ``activities.summary`` for the title substring.

    ``template_id`` and ``title`` are both stored in the ``summary``
    JSON blob; there is no dedicated ``template_id`` column on the
    ``activities`` table.  We parse the blob for both fields.
    """
    # The summary column is a JSON blob whose ``title`` field looks like:
    #   {"title": "Treasure Hunt", "template_id": "...", ...}
    # We search for the pattern inside the encoded JSON so we avoid
    # deserialising every row.
    #
    # Escape LIKE metacharacters in ``q`` so a user query containing
    # ``_`` (single-char wildcard) or ``%`` (multi-char wildcard) is
    # treated literally.  Backslash is the escape character declared in
    # the ``ESCAPE '\\'`` clause; it must be escaped first so we don't
    # double-process backslashes that appear later in the replacement.
    safe_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f'%"title": "%{safe_q}%"%'
    try:
        rows = conn.execute(
            "SELECT id, summary, state, created_at "
            "FROM activities "
            "WHERE summary LIKE ? ESCAPE '\\' AND state != 'dismissed' "
            "ORDER BY created_at DESC LIMIT 20",
            (pattern,),
        ).fetchall()
    except Exception:
        _logger.exception("search_past_activities query failed for q=%r", q)
        return []

    results: list[PastActivityResult] = []
    for row in rows:
        title: str | None = None
        template_id: str | None = None
        try:
            if row["summary"]:
                blob = json.loads(row["summary"])
                if isinstance(blob, dict):
                    title = blob.get("title")
                    tid = blob.get("template_id")
                    if isinstance(tid, str) and tid:
                        template_id = tid
        except (json.JSONDecodeError, TypeError):
            pass
        results.append(
            PastActivityResult(
                id=row["id"],
                title=title,
                template_id=template_id,
                state=row["state"],
                created_at=row["created_at"],
            )
        )
    return results


def _search_templates(q: str) -> list[TemplateResult]:
    """Case-insensitive substring match on template id or title."""
    q_lower = q.lower()
    results: list[TemplateResult] = []
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
            if q_lower in tmpl.id.lower() or q_lower in tmpl.title.lower():
                seen_ids.add(tmpl.id)
                results.append(
                    TemplateResult(id=tmpl.id, title=tmpl.title, intent=intent)
                )
                if len(results) >= 20:
                    return results

    return results


__all__ = [
    "PastActivityResult",
    "SearchResponse",
    "TemplateResult",
    "get_search_db",
    "router",
]
