"""Unit tests for the SSRF-guarded photo fetcher (:mod:`toybox.core.photo_fetch`).

NO real network: the opener + host resolver are both injected. A stub
opener returns canned bytes; a fake resolver maps a host to whatever IP
the test wants (public to pass, private to prove the anti-rebinding
guard fires even for an allowlisted host).
"""

from __future__ import annotations

import http.client
import socket
import urllib.error
from typing import Any

import pytest

from toybox.core import photo_fetch
from toybox.core.photo_fetch import PhotoFetchBlocked, fetch_photo

# An allowlisted host (matches the default ``*.cdn-redfin.com`` /
# ``ssl.cdn-redfin.com``) used across the happy-path + rebinding tests.
ALLOWED_HOST_URL = "https://ssl.cdn-redfin.com/photo/1/bed.jpg"
ALLOWED_WILDCARD_URL = "https://images.cdn-redfin.com/photo/1/bed.jpg"

PUBLIC_IP = "93.184.216.34"  # example.com's public address


# ---------------------------------------------------------------------------
# Stub transport + resolver
# ---------------------------------------------------------------------------


class _StubResponse:
    """Minimal urlopen-style response: context manager + headers + read()."""

    def __init__(self, body: bytes, *, content_length: str | None = "use-actual") -> None:
        self._body = body
        self._pos = 0
        if content_length == "use-actual":
            self.headers = {"Content-Length": str(len(body))}
        elif content_length is None:
            self.headers = {}
        else:
            self.headers = {"Content-Length": content_length}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._body[self._pos :]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self) -> _StubResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _StubOpener:
    """Returns a pre-seeded response (or raises a pre-seeded exception)."""

    def __init__(self, response: Any = None, *, raises: Exception | None = None) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[Any] = []

    def open(self, fullurl: Any, timeout: float) -> Any:
        self.calls.append((fullurl, timeout))
        if self._raises is not None:
            raise self._raises
        return self._response


def _resolver_to(ip: str) -> Any:
    """Fake resolver mapping every host to a single ``ip``."""

    def _resolve(host: str) -> list[str]:
        return [ip]

    return _resolve


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_allowlisted_host_public_ip_returns_bytes() -> None:
    body = b"\xff\xd8\xff" + b"jpeg-bytes"
    opener = _StubOpener(_StubResponse(body))
    out = fetch_photo(ALLOWED_HOST_URL, opener=opener, resolver=_resolver_to(PUBLIC_IP))
    assert out == body
    assert len(opener.calls) == 1  # the opener was actually used


def test_wildcard_allowlist_match_returns_bytes() -> None:
    body = b"webp-bytes"
    opener = _StubOpener(_StubResponse(body))
    out = fetch_photo(ALLOWED_WILDCARD_URL, opener=opener, resolver=_resolver_to(PUBLIC_IP))
    assert out == body


# ---------------------------------------------------------------------------
# Check 1 — scheme
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:image/png;base64,AAAA",
        "javascript:alert(1)",
        "ftp://ssl.cdn-redfin.com/x.jpg",
    ],
)
def test_bad_scheme_blocked(url: str) -> None:
    # Opener/resolver that would explode if ever reached — they must not be.
    opener = _StubOpener(raises=AssertionError("opener must not be called"))
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo(url, opener=opener, resolver=_resolver_to(PUBLIC_IP))
    assert ei.value.code == "photo_fetch_blocked"
    assert opener.calls == []


# ---------------------------------------------------------------------------
# Check 2 — host allowlist
# ---------------------------------------------------------------------------


def test_non_allowlisted_host_blocked() -> None:
    opener = _StubOpener(raises=AssertionError("opener must not be called"))
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo("http://evil.com/x.jpg", opener=opener, resolver=_resolver_to(PUBLIC_IP))
    assert ei.value.code == "photo_fetch_blocked"
    assert opener.calls == []


def test_lookalike_host_not_suffix_matched() -> None:
    # "cdn-redfin.com.evil.com" must NOT match "*.cdn-redfin.com".
    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(
            "https://cdn-redfin.com.evil.com/x.jpg",
            opener=_StubOpener(_StubResponse(b"x")),
            resolver=_resolver_to(PUBLIC_IP),
        )


# ---------------------------------------------------------------------------
# Check 3 — DNS-resolved private-IP rejection (anti-rebinding)
# ---------------------------------------------------------------------------


def test_loopback_literal_host_blocked() -> None:
    # http://127.0.0.1/x — not allowlisted AND resolves to loopback. Even
    # if the resolver is bypassed, the allowlist already rejects it; here
    # we assert the block regardless.
    opener = _StubOpener(raises=AssertionError("opener must not be called"))
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo("http://127.0.0.1/x.jpg", opener=opener, resolver=_resolver_to("127.0.0.1"))
    assert ei.value.code == "photo_fetch_blocked"


@pytest.mark.parametrize(
    "private_ip",
    [
        "10.0.0.5",  # RFC1918 private
        "169.254.1.1",  # link-local
        "::1",  # IPv6 loopback
        "192.168.1.1",  # private
        "172.16.0.1",  # private
        "224.0.0.1",  # multicast
        "0.0.0.0",  # unspecified
        "100.64.0.1",  # CGNAT (RFC 6598) — not is_global
        "198.18.0.1",  # IETF benchmarking — not is_global
        "::ffff:10.0.0.5",  # IPv4-mapped-IPv6 private
        "240.0.0.1",  # reserved
    ],
)
def test_allowlisted_host_resolving_to_private_ip_blocked(private_ip: str) -> None:
    """The rebinding guard: an ALLOWLISTED host that resolves to a private
    IP is still blocked. Uses an allowlisted URL + a fake resolver that
    returns the private address — proving the IP check is independent of
    the allowlist.
    """
    opener = _StubOpener(_StubResponse(b"should-not-be-read"))
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo(ALLOWED_HOST_URL, opener=opener, resolver=_resolver_to(private_ip))
    assert ei.value.code == "photo_fetch_blocked"
    assert opener.calls == []  # blocked before any network read


def test_mixed_public_and_private_resolution_blocked() -> None:
    """If a host resolves to BOTH a public and a private IP, block it."""

    def _mixed(host: str) -> list[str]:
        return [PUBLIC_IP, "10.0.0.5"]

    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(ALLOWED_HOST_URL, opener=_StubOpener(_StubResponse(b"x")), resolver=_mixed)


def test_empty_resolution_blocked() -> None:
    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(
            ALLOWED_HOST_URL,
            opener=_StubOpener(_StubResponse(b"x")),
            resolver=lambda host: [],
        )


def test_dns_failure_blocked() -> None:
    def _boom(host: str) -> list[str]:
        raise OSError("name resolution failed")

    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(ALLOWED_HOST_URL, opener=_StubOpener(_StubResponse(b"x")), resolver=_boom)


# ---------------------------------------------------------------------------
# Check 4 — redirect policy: block any redirect
# ---------------------------------------------------------------------------


def test_redirect_response_blocked() -> None:
    """Chosen policy = block redirects entirely. A 30x surfaces from the
    opener as an HTTPError (the _NoRedirect handler turns it into one),
    which maps to photo_fetch_blocked.
    """
    err = urllib.error.HTTPError(
        url=ALLOWED_HOST_URL,
        code=302,
        msg="Found",
        hdrs=None,
        fp=None,  # type: ignore[arg-type]
    )
    opener = _StubOpener(raises=err)
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo(ALLOWED_HOST_URL, opener=opener, resolver=_resolver_to(PUBLIC_IP))
    assert ei.value.code == "photo_fetch_blocked"


def test_http_error_blocked() -> None:
    err = urllib.error.HTTPError(
        url=ALLOWED_HOST_URL,
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,  # type: ignore[arg-type]
    )
    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(
            ALLOWED_HOST_URL, opener=_StubOpener(raises=err), resolver=_resolver_to(PUBLIC_IP)
        )


# ---------------------------------------------------------------------------
# Check 5 — size cap (declared AND streamed)
# ---------------------------------------------------------------------------


def test_over_cap_declared_content_length_blocked() -> None:
    """A Content-Length over the cap is rejected before reading the body."""
    body = b"small"  # actual body is tiny...
    # ...but Content-Length claims it's huge.
    opener = _StubOpener(_StubResponse(body, content_length="999999999"))
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo(
            ALLOWED_HOST_URL,
            opener=opener,
            resolver=_resolver_to(PUBLIC_IP),
            max_bytes=100,
        )
    assert ei.value.code == "photo_fetch_blocked"


def test_lying_small_content_length_but_huge_stream_blocked() -> None:
    """Content-Length lies small, but the body actually exceeds the cap —
    the streaming guard must abort.
    """
    huge_body = b"A" * 500
    opener = _StubOpener(_StubResponse(huge_body, content_length="10"))  # lies: claims 10 bytes
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo(
            ALLOWED_HOST_URL,
            opener=opener,
            resolver=_resolver_to(PUBLIC_IP),
            max_bytes=100,
        )
    assert ei.value.code == "photo_fetch_blocked"


def test_absent_content_length_huge_stream_blocked() -> None:
    """No Content-Length header at all + huge body → streaming guard fires."""
    huge_body = b"B" * 500
    opener = _StubOpener(_StubResponse(huge_body, content_length=None))
    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(
            ALLOWED_HOST_URL,
            opener=opener,
            resolver=_resolver_to(PUBLIC_IP),
            max_bytes=100,
        )


def test_exactly_at_cap_accepted() -> None:
    """A body exactly at the cap is accepted (boundary)."""
    body = b"C" * 100
    opener = _StubOpener(_StubResponse(body))
    out = fetch_photo(
        ALLOWED_HOST_URL,
        opener=opener,
        resolver=_resolver_to(PUBLIC_IP),
        max_bytes=100,
    )
    assert out == body


# ---------------------------------------------------------------------------
# Check 6 — timeout
# ---------------------------------------------------------------------------


def test_timeout_blocked() -> None:
    opener = _StubOpener(raises=TimeoutError("read timed out"))
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo(ALLOWED_HOST_URL, opener=opener, resolver=_resolver_to(PUBLIC_IP))
    assert ei.value.code == "photo_fetch_blocked"


def test_socket_timeout_wrapped_in_urlerror_blocked() -> None:
    opener = _StubOpener(raises=urllib.error.URLError(TimeoutError("timed out")))
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo(ALLOWED_HOST_URL, opener=opener, resolver=_resolver_to(PUBLIC_IP))
    assert ei.value.code == "photo_fetch_blocked"


def test_generic_urlerror_blocked() -> None:
    opener = _StubOpener(raises=urllib.error.URLError("connection refused"))
    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(ALLOWED_HOST_URL, opener=opener, resolver=_resolver_to(PUBLIC_IP))


# ---------------------------------------------------------------------------
# Env override — allowlist widening / narrowing
# ---------------------------------------------------------------------------


def test_env_override_widens_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(photo_fetch.ALLOWLIST_ENV, "photos.example.org,*.imgcdn.net")
    body = b"img"
    opener = _StubOpener(_StubResponse(body))
    # A host now in the widened allowlist resolves + returns.
    out = fetch_photo(
        "https://photos.example.org/p.jpg", opener=opener, resolver=_resolver_to(PUBLIC_IP)
    )
    assert out == body
    # The wildcard entry works too.
    out2 = fetch_photo(
        "https://a.imgcdn.net/p.jpg",
        opener=_StubOpener(_StubResponse(body)),
        resolver=_resolver_to(PUBLIC_IP),
    )
    assert out2 == body


def test_env_override_narrows_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    # Narrow to ONLY example.org — the default Redfin host is now excluded.
    monkeypatch.setenv(photo_fetch.ALLOWLIST_ENV, "photos.example.org")
    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(
            ALLOWED_HOST_URL,
            opener=_StubOpener(_StubResponse(b"x")),
            resolver=_resolver_to(PUBLIC_IP),
        )


def test_empty_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(photo_fetch.ALLOWLIST_ENV, "   ,  ,")
    assert photo_fetch.allowlist() == photo_fetch.DEFAULT_ALLOWLIST


def test_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(photo_fetch.TIMEOUT_ENV, "3.5")
    assert photo_fetch.timeout_sec() == 3.5
    # Bad value falls back to default.
    monkeypatch.setenv(photo_fetch.TIMEOUT_ENV, "not-a-number")
    assert photo_fetch.timeout_sec() == photo_fetch.DEFAULT_TIMEOUT_SEC


# ---------------------------------------------------------------------------
# Finding 1 — un-wrapped read errors must surface as PhotoFetchBlocked
# ---------------------------------------------------------------------------


class _IncompleteReadResponse:
    """Response whose ``.read()`` raises ``http.client.IncompleteRead`` — a
    mid-body connection drop. ``IncompleteRead`` is an ``HTTPException``,
    NOT an ``OSError``, so the pre-fix handler chain let it escape raw.
    """

    headers: dict[str, str] = {}

    def read(self, n: int = -1) -> bytes:
        raise http.client.IncompleteRead(partial=b"AB", expected=100)

    def __enter__(self) -> _IncompleteReadResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_incomplete_read_maps_to_blocked() -> None:
    """A truncated download (IncompleteRead / HTTPException from read())
    must surface as PhotoFetchBlocked, not the raw http.client error — the
    X5 import-commit caller only handles PhotoFetchBlocked, so a raw error
    would fail the whole commit instead of skipping one photo.
    """
    opener = _StubOpener(_IncompleteReadResponse())
    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo(ALLOWED_HOST_URL, opener=opener, resolver=_resolver_to(PUBLIC_IP))
    assert ei.value.code == "photo_fetch_blocked"
    assert ei.value.reason == "read_error"


def test_only_photofetchblocked_escapes() -> None:
    """A bare HTTPException from the transport is wrapped, never escapes."""
    opener = _StubOpener(raises=http.client.HTTPException("protocol error"))
    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(ALLOWED_HOST_URL, opener=opener, resolver=_resolver_to(PUBLIC_IP))


# ---------------------------------------------------------------------------
# Finding 2 — getaddrinfo UnicodeError must be caught by the resolver guard
# ---------------------------------------------------------------------------


def test_resolver_unicode_error_maps_to_blocked() -> None:
    """A malformed host (from untrusted parsed HTML) makes getaddrinfo
    raise UnicodeError — must map to PhotoFetchBlocked, not escape raw.
    """

    def _unicode_boom(host: str) -> list[str]:
        raise UnicodeError("label empty or too long")

    with pytest.raises(PhotoFetchBlocked) as ei:
        fetch_photo(
            ALLOWED_HOST_URL,
            opener=_StubOpener(_StubResponse(b"x")),
            resolver=_unicode_boom,
        )
    assert ei.value.code == "photo_fetch_blocked"


def test_default_resolver_malformed_host_blocked() -> None:
    """End-to-end through the real default resolver: a malformed host that
    getaddrinfo rejects with UnicodeError must be blocked, not raise raw.
    A leading-dot label makes getaddrinfo's IDNA encoder raise UnicodeError
    (verified on the target platform). The host must be allowlisted so we
    reach the resolver step (``.cdn-redfin.com`` suffix-matches
    ``*.cdn-redfin.com``).
    """
    bad_url = "http://.cdn-redfin.com/x.jpg"
    with pytest.raises(PhotoFetchBlocked):
        # No opener/resolver injected → exercises _default_resolver.
        fetch_photo(bad_url)


# ---------------------------------------------------------------------------
# Finding 3 — DNS-rebinding TOCTOU: the connection targets the pinned IP
# ---------------------------------------------------------------------------


def test_default_opener_pins_validated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default opener connects to the VALIDATED IP, not a re-resolved
    one. We capture socket.create_connection and assert the address it is
    handed is the pinned (validated) IP — proving urllib cannot re-resolve
    the host to a different (attacker-flipped) address at connect time.
    """
    captured: list[tuple[Any, ...]] = []

    def _fake_create_connection(address: tuple[str, int], *args: Any, **kw: Any) -> Any:
        captured.append(address)
        raise OSError("stop before real I/O")  # we only need the target

    monkeypatch.setattr(socket, "create_connection", _fake_create_connection)

    # http (no TLS) so the pinned HTTPConnection.connect runs immediately.
    with pytest.raises(PhotoFetchBlocked):
        fetch_photo(
            "http://images.cdn-redfin.com/p.jpg",
            resolver=_resolver_to(PUBLIC_IP),  # validated public IP
        )

    assert captured, "socket.create_connection was never called"
    target_host = captured[0][0]
    assert target_host == PUBLIC_IP  # connected to the validated IP, not the hostname


def test_pinned_https_connection_carries_pinned_ip() -> None:
    """The pinned HTTPS connection keeps self.host = hostname (for SNI /
    cert / Host header) but stores the pinned IP for the socket target.
    """
    conn = photo_fetch._PinnedHTTPSConnection("ssl.cdn-redfin.com", pinned_ip=PUBLIC_IP)
    assert conn.host == "ssl.cdn-redfin.com"  # SNI + cert verify against hostname
    assert conn._pinned_ip == PUBLIC_IP  # socket lands on the validated IP
