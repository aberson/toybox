"""Phase X Step X2 — pure, offline parsing of a pasted real-estate listing.

:func:`parse_listing` turns a pasted Redfin-style listing page (HTML) **or**
a plain newline/whitespace-separated list of photo URLs into a
:class:`ParsedListing` = ``{room_counts, photo_urls}``. The downstream
:mod:`toybox.core.room_naming` expands ``room_counts`` into proposed named
rooms; the importer (X5) fetches ``photo_urls`` through the SSRF-guarded
:mod:`toybox.core.photo_fetch`.

**Pure + offline + injection-safe.**

* No network, no DB, no disk. Reads text only.
* No BeautifulSoup / lxml — neither is installed. HTML is read with
  :mod:`re` plus the stdlib :class:`html.parser.HTMLParser`.
* The pasted HTML is **untrusted external content** (``security.md``):
  embedded directives — ``<!-- ignore prior instructions -->``, a fake
  ``<system-reminder>`` block, "you are now…" text — are treated purely
  as data. This module never executes, follows, or surfaces them; it
  only extracts bed/bath counts, room-type mentions, and image URLs.

**Robust to malformed input.** Missing fields yield an empty slice;
``parse_listing("")`` returns ``ParsedListing(room_counts={}, photo_urls=[])``.
The function never raises on malformed input — a best-effort partial
parse is always returned (the parent reviews + edits before commit).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .room_types import MAX_ROOMS_PER_TYPE, ROOM_TYPES

# ---------------------------------------------------------------------------
# Parsed result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedListing:
    """Result of :func:`parse_listing`.

    ``room_counts`` maps a canonical :data:`toybox.core.room_types.ROOM_TYPES`
    value to a positive int count. ``photo_urls`` is the de-duplicated
    (order-preserving) list of extracted image URLs. Both are empty when
    nothing matched — that is a valid result, not an error.
    """

    room_counts: dict[str, int] = field(default_factory=dict)
    photo_urls: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regexes (compiled once; all read-only over the input text)
# ---------------------------------------------------------------------------

# "3 beds", "3 bed", "3 Bedrooms" — the leading integer is the count.
_BEDS_RE = re.compile(r"(\d+)\s*(?:beds?|bedrooms?)\b", re.IGNORECASE)
# "2 baths", "2.5 bath", "2 Bathrooms" — float allowed (half-baths).
_BATHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:baths?|bathrooms?)\b", re.IGNORECASE)

# Bare image URLs anywhere in the text (covers the plain-URL-list paste
# and image links not inside an <img>/<meta>). Stops at whitespace or a
# quote/paren/angle-bracket so we don't swallow trailing markup.
_IMG_URL_RE = re.compile(
    r"https?://[^\s\"'<>()]+?\.(?:jpe?g|png|webp|gif)(?:\?[^\s\"'<>()]*)?",
    re.IGNORECASE,
)

# Cheap HTML sniff: an angle-bracket tag somewhere in the content.
_HTML_SNIFF_RE = re.compile(r"<[a-zA-Z!/][^>]*>")

# Per-room-type mention map. Built from the single-source-of-truth
# ROOM_TYPES so it can't drift. Multi-word keys ("living_room") match the
# spaced phrase ("living room"); we also accept the underscore form.
_ROOM_MENTION_RES: dict[str, re.Pattern[str]] = {
    rt: re.compile(r"\b" + rt.replace("_", r"[ _]") + r"s?\b", re.IGNORECASE) for rt in ROOM_TYPES
}


# ---------------------------------------------------------------------------
# HTML image-source collector (stdlib, no external parser)
# ---------------------------------------------------------------------------


class _ImageSrcParser(HTMLParser):
    """Collect ``<img src>`` and ``og:image``-style ``<meta content>`` URLs.

    Treats every attribute as inert data — it only reads attribute
    values, never acts on tag/attribute semantics beyond URL extraction.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag == "img":
            for key in ("src", "data-src", "data-srcset", "srcset"):
                val = attr.get(key)
                if val:
                    self._add_from_srcset(val)
        elif tag == "meta":
            prop = (attr.get("property") or attr.get("name") or "").lower()
            if prop in {"og:image", "twitter:image", "og:image:url"}:
                content = attr.get("content")
                if content:
                    self.urls.append(content.strip())

    def _add_from_srcset(self, value: str) -> None:
        # ``srcset`` is "url 320w, url2 640w"; a plain ``src`` is one URL.
        # Split on commas, then take the first whitespace-delimited token
        # of each candidate (the URL; any descriptor follows).
        for candidate in value.split(","):
            token = candidate.strip().split()
            if token:
                self.urls.append(token[0].strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_html(content: str) -> bool:
    """True when ``content`` contains at least one HTML-ish tag."""
    return _HTML_SNIFF_RE.search(content) is not None


def _is_http_url(url: str) -> bool:
    """True only for ``http://`` / ``https://`` URLs.

    Defense-in-depth (don't rely solely on X3's SSRF guard): the
    ``<img src>`` / ``<meta content>`` attribute path appends URLs
    verbatim, so a ``javascript:`` / ``data:`` / ``file:`` URI could
    otherwise land in ``photo_urls``. We keep only the two safe web
    schemes; everything else is dropped.
    """
    return url[:7].lower() == "http://" or url[:8].lower() == "https://"


def _dedup_preserving_order(urls: list[str]) -> list[str]:
    """Drop blanks, non-http(s) schemes, and duplicates, preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        u = url.strip()
        if not u or u in seen or not _is_http_url(u):
            continue
        seen.add(u)
        out.append(u)
    return out


def _extract_room_counts(text: str) -> dict[str, int]:
    """Best-effort bed/bath + room-mention counts from listing text.

    * Beds -> ``bedroom``; baths -> ``bathroom`` (the bath float is
      floored to an int room count, e.g. ``2.5`` -> ``2``).
    * A room-type word that appears at least once (kitchen, garage,
      living room, …) contributes a count of 1 — listings rarely state
      "2 kitchens", so a single mention seeds one room the parent can
      duplicate. Bed/bath explicit counts take precedence over a bare
      "bedroom"/"bathroom" mention.
    """
    counts: dict[str, int] = {}

    beds = _BEDS_RE.search(text)
    if beds is not None:
        n = int(beds.group(1))
        if n > 0:
            # Clamp: a pasted "99999999 beds" must not balloon downstream
            # room generation into an OOM. Capped at MAX_ROOMS_PER_TYPE.
            counts["bedroom"] = min(n, MAX_ROOMS_PER_TYPE)

    baths = _BATHS_RE.search(text)
    if baths is not None:
        n = int(float(baths.group(1)))  # floor half-baths to room count
        if n > 0:
            counts["bathroom"] = min(n, MAX_ROOMS_PER_TYPE)

    for room_type, pattern in _ROOM_MENTION_RES.items():
        if room_type in counts:
            # bedroom/bathroom already set from the explicit "N beds/baths".
            continue
        if pattern.search(text) is not None:
            counts[room_type] = 1

    return counts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_listing(content: str) -> ParsedListing:
    """Parse pasted listing HTML or a plain URL list into a :class:`ParsedListing`.

    Detects HTML vs a plain URL list by sniffing for a tag. For HTML it
    extracts bed/bath counts + room-type mentions from the page text and
    photo URLs from ``<img>`` srcs, ``og:image`` metas, and any bare
    image URLs. For a plain list it just collects the bare image URLs
    (and still scans for any bed/bath text, harmlessly absent in a pure
    URL list).

    Never raises on malformed input — returns whatever was found. An
    empty result (``room_counts={}, photo_urls=[]``) is valid.

    The input is treated strictly as data; embedded directives are
    ignored (see module docstring / ``security.md``).
    """
    if not content or not content.strip():
        return ParsedListing()

    is_html = _looks_like_html(content)

    photo_urls: list[str] = []
    text_for_counts = content

    if is_html:
        # Stdlib HTML parse for <img>/<meta> srcs. Defensive: a
        # pathological document must not raise out of a pure parser.
        parser = _ImageSrcParser()
        try:
            parser.feed(content)
            parser.close()
        except Exception:  # noqa: BLE001 - never raise on malformed HTML
            pass
        photo_urls.extend(parser.urls)

    # Bare image URLs anywhere in the raw content (covers plain URL lists
    # and HTML attributes/links the tag parser didn't surface).
    photo_urls.extend(m.group(0) for m in _IMG_URL_RE.finditer(content))

    room_counts = _extract_room_counts(text_for_counts)

    return ParsedListing(
        room_counts=room_counts,
        photo_urls=_dedup_preserving_order(photo_urls),
    )


__all__ = [
    "ParsedListing",
    "parse_listing",
]
