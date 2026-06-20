"""Unit coverage for :mod:`toybox.core.listing_parser` (Phase X Step X2).

Exercises the HTML path against a saved Redfin-style fixture, the plain
URL-list path, malformed/empty input, and the prompt-injection-safety
contract (embedded directives are parsed as data only).
"""

from __future__ import annotations

from pathlib import Path

from toybox.core.listing_parser import ParsedListing, parse_listing
from toybox.core.room_types import MAX_ROOMS_PER_TYPE

FIXTURES_DIR: Path = Path(__file__).resolve().parents[2] / "fixtures" / "listings"


def _read(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML path
# ---------------------------------------------------------------------------


def test_html_extracts_bed_bath_counts() -> None:
    result = parse_listing(_read("redfin_sample.html"))
    assert result.room_counts["bedroom"] == 3
    assert result.room_counts["bathroom"] == 2


def test_html_extracts_other_room_mentions() -> None:
    result = parse_listing(_read("redfin_sample.html"))
    # Single-mention room types seed a count of 1.
    for rt in ("kitchen", "living_room", "garage", "yard", "office", "dining_room"):
        assert result.room_counts.get(rt) == 1, f"expected one {rt}"


def test_html_extracts_photo_urls_from_img_meta_and_srcset() -> None:
    result = parse_listing(_read("redfin_sample.html"))
    urls = result.photo_urls
    assert "https://ssl.cdn-redfin.com/photo/og/123-maple-hero.jpg" in urls  # og:image
    assert "https://ssl.cdn-redfin.com/photo/123/genMid.bedroom-1.jpg" in urls  # <img src>
    assert "https://ssl.cdn-redfin.com/photo/123/genMid.kitchen-1.jpg" in urls  # srcset first url
    assert "https://ssl.cdn-redfin.com/photo/123/genMid.garage-1.jpg" in urls  # data-src


def test_html_twitter_image_meta_collected() -> None:
    result = parse_listing(_read("redfin_sample.html"))
    assert "https://ssl.cdn-redfin.com/photo/tw/123-maple-twitter.jpg" in result.photo_urls


def test_html_photo_urls_deduped_preserving_order() -> None:
    result = parse_listing(_read("redfin_sample.html"))
    urls = result.photo_urls
    # The hero image appears as both og:image and a duplicate <img>; only once.
    hero = "https://ssl.cdn-redfin.com/photo/og/123-maple-hero.jpg"
    assert urls.count(hero) == 1
    assert len(urls) == len(set(urls))


# ---------------------------------------------------------------------------
# Plain URL-list path
# ---------------------------------------------------------------------------


def test_plain_url_list_extracts_urls() -> None:
    result = parse_listing(_read("photo_url_list.txt"))
    assert result.photo_urls == [
        "https://ssl.cdn-redfin.com/photo/456/photo-01.jpg",
        "https://ssl.cdn-redfin.com/photo/456/photo-02.png",
        "https://ssl.cdn-redfin.com/photo/456/photo-03.webp",
        "https://ssl.cdn-redfin.com/photo/456/photo-04.jpeg",
    ]
    # A pure URL list has no stats text -> no counts.
    assert result.room_counts == {}


# ---------------------------------------------------------------------------
# Malformed / empty input — never raises
# ---------------------------------------------------------------------------


def test_empty_string_returns_empty_parse() -> None:
    result = parse_listing("")
    assert result == ParsedListing(room_counts={}, photo_urls=[])


def test_whitespace_only_returns_empty_parse() -> None:
    assert parse_listing("   \n\t  ") == ParsedListing()


def test_malformed_html_does_not_raise() -> None:
    junk = "<html><body><img src=https://ssl.cdn-redfin.com/x.jpg <<< broken"
    result = parse_listing(junk)  # must not raise
    assert isinstance(result, ParsedListing)
    assert "https://ssl.cdn-redfin.com/x.jpg" in result.photo_urls


def test_garbage_text_returns_empty_no_raise() -> None:
    result = parse_listing("just some words with no urls and no stats")
    assert result.photo_urls == []
    assert result.room_counts == {}


# ---------------------------------------------------------------------------
# Count clamping — unbounded counts can't balloon downstream room gen
# ---------------------------------------------------------------------------


def test_absurd_bed_count_is_clamped() -> None:
    # A pasted "99999999 beds" must clamp to MAX_ROOMS_PER_TYPE, not feed
    # ~100M dicts into the room generator once X5 wires pasted input.
    result = parse_listing("99999999 beds, 88888888 baths")
    assert result.room_counts["bedroom"] == MAX_ROOMS_PER_TYPE
    assert result.room_counts["bathroom"] == MAX_ROOMS_PER_TYPE


# ---------------------------------------------------------------------------
# URL scheme filtering — only http(s) photo URLs survive
# ---------------------------------------------------------------------------


def test_non_http_schemes_excluded_from_photo_urls() -> None:
    html = (
        '<img src="javascript:alert(1)">'
        '<img src="data:image/png;base64,AAAA">'
        '<img src="file:///etc/passwd">'
        '<img src="https://cdn.example.com/good.jpg">'
        '<img src="http://cdn.example.com/also-good.png">'
    )
    result = parse_listing(html)
    assert result.photo_urls == [
        "https://cdn.example.com/good.jpg",
        "http://cdn.example.com/also-good.png",
    ]


# ---------------------------------------------------------------------------
# Robustness — huge / binary / control-char blobs never raise
# ---------------------------------------------------------------------------


def test_huge_input_does_not_raise() -> None:
    result = parse_listing("x" * 2_000_000)  # must not raise or hang
    assert isinstance(result, ParsedListing)
    assert result.room_counts == {}
    assert result.photo_urls == []


def test_control_char_garbage_blob_does_not_raise() -> None:
    blob = "".join(chr(c) for c in range(0, 32)) * 1000 + "\x00\xff<<<>>>&;"
    result = parse_listing(blob)  # must not raise
    assert isinstance(result, ParsedListing)


# ---------------------------------------------------------------------------
# Injection safety — directives are data, not instructions
# ---------------------------------------------------------------------------


def test_injection_html_parsed_as_data_only() -> None:
    result = parse_listing(_read("redfin_injection.html"))
    # The embedded <system-reminder> / "ignore prior instructions" text is
    # ignored as a directive: only listing data is extracted.
    assert result.room_counts["bedroom"] == 2
    assert result.room_counts["bathroom"] == 1
    assert result.photo_urls == [
        "https://ssl.cdn-redfin.com/photo/og/789-elm-hero.jpg",
        "https://ssl.cdn-redfin.com/photo/789/genMid.living-room.jpg",
    ]
