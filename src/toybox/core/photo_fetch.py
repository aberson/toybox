"""Phase X Step X3 — SSRF-guarded photo download for the room importer.

:func:`fetch_photo` downloads a single photo URL and returns its raw
bytes, ready to hand to :func:`toybox.storage.images.validate_upload`.
The URLs come from :mod:`toybox.core.listing_parser`, which parses
**untrusted pasted listing HTML** — so a crafted page can point this
fetcher at an internal address (cloud metadata, ``127.0.0.1``, a LAN
host). The guard here is the only thing standing between that and an
SSRF. It is a **hard runtime control**, not documentation
(``security.md`` §"pair unsafe configs with startup safety checks").

Every guard failure raises :class:`PhotoFetchBlocked` with the stable
code ``photo_fetch_blocked``. The caller (X5 import-commit) catches it
per-URL and skips the photo (room → N/A) rather than failing the whole
commit.

Guard checks (ALL enforced, in order):

1. **Scheme allowlist** — only ``http`` / ``https``. ``file:``,
   ``data:``, ``javascript:``, ``ftp:``, etc. are rejected.
2. **Host allowlist** — the URL host must match a configured pattern.
   Default ``ssl.cdn-redfin.com`` + ``*.cdn-redfin.com`` (a leading
   ``*.`` is a suffix match). Override via
   ``TOYBOX_PHOTO_FETCH_ALLOWLIST`` (comma-separated host patterns).
3. **DNS-resolved private-IP rejection (anti-rebinding)** — the host is
   resolved with :func:`socket.getaddrinfo` and **every** resolved
   address must be a public, routable IP (``ip.is_global`` true, and not
   multicast). If any resolved address is private / loopback /
   link-local / reserved / multicast / unspecified / CGNAT / benchmark /
   future-non-global the fetch is blocked — even for an allowlisted host
   (a DNS-rebinding attack points an allowlisted name at ``127.0.0.1`` /
   ``169.254.x`` / ``::1``). The validated IP is then **pinned** into the
   connection (see below) so urllib does not re-resolve to a different
   address at connect time.
4. **No redirect following** — urllib follows 30x redirects by default,
   so a redirect to an internal URL would bypass checks 1-3. We disable
   redirect following entirely via :class:`_NoRedirect`; a 30x response
   raises ``photo_fetch_blocked``. (Chosen over re-running the full
   guard per hop: a hard block is the smaller, more auditable surface,
   and Redfin's CDN serves images directly without redirects.)
5. **Size cap** — at most :func:`toybox.storage.images.max_upload_bytes`
   bytes are read. A ``Content-Length`` over the cap is rejected up
   front, AND the streaming read is bounded (Content-Length can lie):
   we read in fixed chunks and abort the moment the running total
   exceeds the cap.
6. **Timeout** — a connect/read timeout (default 10s, env-overridable
   via ``TOYBOX_PHOTO_FETCH_TIMEOUT_SEC``); a timeout raises
   ``photo_fetch_blocked``.

**DNS-rebinding TOCTOU pinning.** Check 3 resolves + validates the IP,
but if we then hand the bare URL string to urllib, urllib RE-RESOLVES at
connect time — so an attacker controlling DNS for an allowlisted host
could return a public IP to the guard and a private IP to urllib (a
classic time-of-check / time-of-use race). To close this, the default
opener pins the validated IP into the connection: it connects to the
exact address the guard checked, while preserving the original hostname
for the ``Host`` header, TLS SNI, and certificate verification (cert is
verified against the hostname, NEVER disabled). See
:class:`_PinnedHTTPSConnection` / :class:`_PinnedHTTPConnection` and
:class:`_PinnedHandler`.

**Injectable transport.** The opener is a parameter (``opener=``)
defaulting to a pinned, redirect-blocking
:func:`urllib.request.build_opener`. Host resolution is a parameter too
(``resolver=``) defaulting to :func:`socket.getaddrinfo`. Both seams let
the test suite exercise the full guard with **no real network** — a stub
opener returns canned bytes and a fake resolver maps a host to any IP.
When the caller injects its own ``opener`` (as the tests do), pinning is
that opener's responsibility; the default opener built here always pins.

``urllib`` only — ``requests`` is banned (``.claude/rules/claude-auth.md``).
"""

from __future__ import annotations

import http.client
import ipaddress
import logging
import os
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Final, Protocol

from ..storage.images import max_upload_bytes

_logger = logging.getLogger(__name__)

# Stable error code surfaced to the importer + the wire envelope.
PHOTO_FETCH_BLOCKED_CODE: Final[str] = "photo_fetch_blocked"

# Default host allowlist: Redfin's photo CDN. A leading ``*.`` is a
# suffix match (``*.cdn-redfin.com`` matches ``foo.cdn-redfin.com`` and
# ``cdn-redfin.com`` itself). Override via the env var below.
DEFAULT_ALLOWLIST: Final[tuple[str, ...]] = (
    "ssl.cdn-redfin.com",
    "*.cdn-redfin.com",
)
ALLOWLIST_ENV: Final[str] = "TOYBOX_PHOTO_FETCH_ALLOWLIST"

# Connect/read timeout (seconds), env-overridable.
DEFAULT_TIMEOUT_SEC: Final[float] = 10.0
TIMEOUT_ENV: Final[str] = "TOYBOX_PHOTO_FETCH_TIMEOUT_SEC"

# Streaming read chunk size.
_CHUNK_BYTES: Final[int] = 64 * 1024

_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})


class PhotoFetchBlocked(Exception):
    """Raised on ANY photo-fetch guard failure.

    The ``code`` is always :data:`PHOTO_FETCH_BLOCKED_CODE`
    (``photo_fetch_blocked``) — a single stable code the importer
    matches on to skip the URL (room → N/A) without failing the commit.
    ``reason`` is a short machine-ish tag for logs/telemetry (it is NOT
    surfaced to the client; the client only ever sees the stable code).
    """

    code: Final[str] = PHOTO_FETCH_BLOCKED_CODE

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class _Opener(Protocol):
    """Minimal transport seam: ``open(req, timeout=...) -> response``.

    :class:`urllib.request.OpenerDirector` satisfies this; tests pass a
    stub returning a canned response object exposing ``.status``,
    ``.headers`` (``Content-Length``), and a ``.read(n)`` /
    context-manager interface.
    """

    def open(self, fullurl: urllib.request.Request, timeout: float) -> object: ...


# ---------------------------------------------------------------------------
# Config accessors (env-overridable)
# ---------------------------------------------------------------------------


def allowlist() -> tuple[str, ...]:
    """Return the configured host allowlist (env-overrideable).

    ``TOYBOX_PHOTO_FETCH_ALLOWLIST`` is comma-separated host patterns;
    blanks are dropped. An empty/whitespace-only env value falls back to
    :data:`DEFAULT_ALLOWLIST` (never an empty allowlist — that would be a
    silent open-everything failure mode).
    """
    raw = os.environ.get(ALLOWLIST_ENV)
    if raw is None:
        return DEFAULT_ALLOWLIST
    patterns = tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    if not patterns:
        _logger.warning("%s is empty; using default allowlist", ALLOWLIST_ENV)
        return DEFAULT_ALLOWLIST
    return patterns


def timeout_sec() -> float:
    """Return the configured fetch timeout (env-overrideable)."""
    raw = os.environ.get(TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_TIMEOUT_SEC
    try:
        parsed = float(raw)
    except ValueError:
        _logger.warning("%s=%r is not a number; using default", TIMEOUT_ENV, raw)
        return DEFAULT_TIMEOUT_SEC
    if parsed <= 0:
        _logger.warning("%s=%r <= 0; using default", TIMEOUT_ENV, raw)
        return DEFAULT_TIMEOUT_SEC
    return parsed


# ---------------------------------------------------------------------------
# Guard primitives
# ---------------------------------------------------------------------------


def _host_matches(host: str, pattern: str) -> bool:
    """True when ``host`` matches an allowlist ``pattern``.

    A leading ``*.`` is a suffix match: ``*.cdn-redfin.com`` matches
    both ``x.cdn-redfin.com`` and the bare ``cdn-redfin.com``. Otherwise
    the match is exact. Comparison is case-insensitive (callers pass a
    lowercased host).
    """
    if pattern.startswith("*."):
        suffix = pattern[2:]  # "cdn-redfin.com"
        return host == suffix or host.endswith("." + suffix)
    return host == pattern


def _is_allowlisted(host: str, patterns: tuple[str, ...]) -> bool:
    """True when ``host`` matches at least one allowlist pattern."""
    return any(_host_matches(host, p) for p in patterns)


def _is_unsafe_ip(addr: str) -> bool:
    """True when a resolved address is non-public / non-routable.

    Gates on ``not ip.is_global`` — which excludes private, loopback,
    link-local, reserved, unspecified, CGNAT (100.64.0.0/10, RFC 6598),
    IETF benchmarking (198.18.0.0/15), and any future non-global range in
    one check, and correctly unwraps IPv4-mapped-IPv6 (``::ffff:10.0.0.5``
    is non-global). Multicast is added explicitly: the stdlib reports
    multicast addresses (``224.0.0.1``, ``ff02::1``) as ``is_global``
    True, so ``not is_global`` alone would let them through. A host that
    resolves to any such address is blocked — this is the
    anti-DNS-rebinding check.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        # An unparseable address from the resolver is suspect → unsafe.
        return True
    return not ip.is_global or ip.is_multicast


def _resolve_ips(
    host: str,
    resolver: Callable[[str], list[str]],
) -> list[str]:
    """Resolve ``host`` to a list of IP strings via the injected resolver."""
    try:
        return resolver(host)
    except (OSError, UnicodeError) as exc:
        # ``socket.getaddrinfo`` raises OSError on resolution failure AND
        # ``UnicodeError`` / ``UnicodeEncodeError`` (a UnicodeError
        # subclass) for malformed hostnames (leading dot, non-ASCII that
        # fails IDNA encoding). The host comes from untrusted parsed HTML,
        # so both must map to a clean block, not an un-wrapped raise.
        raise PhotoFetchBlocked(
            "dns_failure",
            f"could not resolve host {host!r}: {exc}",
        ) from exc


def _default_resolver(host: str) -> list[str]:
    """Default host resolver: every A/AAAA address via getaddrinfo.

    Returns ALL resolved addresses so the guard can reject a host that
    resolves to even one unsafe IP (a rebinding attack may mix a public
    and a private record).
    """
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    # ``sockaddr`` is ``(ip, port)`` for IPv4 and ``(ip, port, flow, scope)``
    # for IPv6; the IP is element 0 either way.
    return [str(info[4][0]) for info in infos]


# ---------------------------------------------------------------------------
# Redirect-blocking opener
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirect handler that refuses to follow ANY 30x.

    urllib follows redirects by default, which would let a 30x to an
    internal URL bypass the scheme/host/IP guard. We disable following
    entirely: a redirect raises :class:`urllib.error.HTTPError`, which
    :func:`fetch_photo` maps to ``photo_fetch_blocked``.
    """

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


# ---------------------------------------------------------------------------
# DNS-rebinding TOCTOU pinning: connect to the validated IP, verify the cert
# against the original hostname.
# ---------------------------------------------------------------------------


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """``HTTPConnection`` that opens the socket to a pinned IP.

    The base class connects to ``(self.host, self.port)``; we override
    :meth:`connect` to connect to ``pinned_ip`` instead, while leaving
    ``self.host`` (the hostname) intact so the ``Host`` request header is
    still the hostname. This is what closes the DNS-rebinding TOCTOU: the
    socket lands on the exact address the guard validated, not on whatever
    a second resolution would return.
    """

    def __init__(self, host: str, *, pinned_ip: str, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """``HTTPSConnection`` that opens the socket to a pinned IP.

    Connects to ``pinned_ip`` but wraps the socket with
    ``server_hostname=host`` so TLS SNI and certificate verification both
    run against the ORIGINAL hostname — the cert chain is verified as
    normal (verification is NEVER disabled). The ``Host`` header stays the
    hostname because ``self.host`` is unchanged.
    """

    def __init__(self, host: str, *, pinned_ip: str, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Verify the cert against the hostname (self.host), NOT the IP.
        # ``_context`` is the SSLContext the base class built from
        # ``context=None`` — a full-verification default context (cert
        # checks are NEVER disabled here).
        ctx: ssl.SSLContext = self._context  # type: ignore[attr-defined]
        self.sock = ctx.wrap_socket(sock, server_hostname=self.host)


class _PinnedHandler(urllib.request.HTTPSHandler, urllib.request.HTTPHandler):
    """urllib handler that builds pinned connections for one fetch.

    Bound to a single validated ``pinned_ip`` (the address the guard
    checked for the one host being fetched). Both the http and https
    ``do_open`` paths route through a connection factory that pins to that
    IP. A fresh handler is built per :func:`fetch_photo` call.
    """

    def __init__(self, pinned_ip: str) -> None:
        super().__init__()
        self._pinned_ip = pinned_ip

    def https_open(self, req: urllib.request.Request) -> Any:
        def _factory(host: str, **kwargs: Any) -> _PinnedHTTPSConnection:
            return _PinnedHTTPSConnection(host, pinned_ip=self._pinned_ip, **kwargs)

        return self.do_open(_factory, req)

    def http_open(self, req: urllib.request.Request) -> Any:
        def _factory(host: str, **kwargs: Any) -> _PinnedHTTPConnection:
            return _PinnedHTTPConnection(host, pinned_ip=self._pinned_ip, **kwargs)

        return self.do_open(_factory, req)


def _build_default_opener(pinned_ip: str) -> urllib.request.OpenerDirector:
    """Build a urllib opener that blocks redirects and pins to ``pinned_ip``.

    ``build_opener`` with our handlers REPLACES the default
    ``HTTPHandler`` / ``HTTPSHandler`` / ``HTTPRedirectHandler`` with the
    pinning + no-redirect versions, so every connection this opener makes
    targets the validated IP and no 30x is followed.
    """
    return urllib.request.build_opener(_NoRedirect, _PinnedHandler(pinned_ip))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch_photo(
    url: str,
    *,
    opener: _Opener | None = None,
    resolver: Callable[[str], list[str]] | None = None,
    max_bytes: int | None = None,
    timeout: float | None = None,
) -> bytes:
    """Download ``url`` through the SSRF guard and return its raw bytes.

    Raises :class:`PhotoFetchBlocked` (code ``photo_fetch_blocked``) on
    ANY guard failure: bad scheme, non-allowlisted host, a host that
    resolves to a private/loopback/link-local/reserved/multicast/
    unspecified IP, a redirect response, an over-cap response (declared
    OR streamed), a timeout, or a transport error.

    ``opener`` / ``resolver`` are injectable for tests (no real network).
    ``max_bytes`` defaults to the storage upload cap; ``timeout`` to the
    configured fetch timeout.
    """
    cap = max_bytes if max_bytes is not None else max_upload_bytes()
    to = timeout if timeout is not None else timeout_sec()
    patterns = allowlist()
    resolve = resolver if resolver is not None else _default_resolver

    # --- Check 1: scheme + parse -------------------------------------------
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise PhotoFetchBlocked("unparseable_url", f"could not parse url: {exc}") from exc
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise PhotoFetchBlocked(
            "bad_scheme",
            f"scheme {scheme!r} not in {sorted(_ALLOWED_SCHEMES)}",
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise PhotoFetchBlocked("no_host", "url has no host")

    # --- Check 2: host allowlist -------------------------------------------
    if not _is_allowlisted(host, patterns):
        raise PhotoFetchBlocked(
            "host_not_allowlisted",
            f"host {host!r} not in allowlist {patterns}",
        )

    # --- Check 3: DNS-resolved private-IP rejection (anti-rebinding) -------
    resolved = _resolve_ips(host, resolve)
    if not resolved:
        raise PhotoFetchBlocked("dns_empty", f"host {host!r} resolved to no addresses")
    for addr in resolved:
        if _is_unsafe_ip(addr):
            raise PhotoFetchBlocked(
                "private_ip",
                f"host {host!r} resolves to non-public address {addr}",
            )
    # All resolved IPs are public+safe; pin the first into the connection
    # so urllib connects to a VALIDATED address (not a re-resolved one).
    pinned_ip = resolved[0]

    # --- Checks 4-6: fetch with redirects blocked, size + timeout caps -----
    # The default opener pins ``pinned_ip`` (closing the DNS-rebinding
    # TOCTOU). An injected opener owns its own connection policy.
    open_with = opener if opener is not None else _build_default_opener(pinned_ip)
    req = urllib.request.Request(url, method="GET")
    try:
        with open_with.open(req, timeout=to) as resp:  # type: ignore[union-attr]
            return _read_capped(resp, cap)
    except urllib.error.HTTPError as exc:
        # A blocked redirect surfaces here (the _NoRedirect handler turns
        # a 30x into an HTTPError); so does any 4xx/5xx.
        raise PhotoFetchBlocked(
            "http_error",
            f"http error fetching {host!r}: {exc.code}",
        ) from exc
    except TimeoutError as exc:
        raise PhotoFetchBlocked("timeout", f"timeout fetching {host!r}: {exc}") from exc
    except urllib.error.URLError as exc:
        # A timeout may also arrive wrapped in URLError(reason=timeout).
        # ``socket.timeout`` is an alias of ``TimeoutError`` on modern Python.
        if isinstance(exc.reason, TimeoutError):
            raise PhotoFetchBlocked("timeout", f"timeout fetching {host!r}: {exc}") from exc
        raise PhotoFetchBlocked("url_error", f"transport error fetching {host!r}: {exc}") from exc
    except PhotoFetchBlocked:
        raise
    except Exception as exc:
        # Catch-all so ONLY PhotoFetchBlocked can escape fetch_photo. This
        # covers OSError AND non-OSError transport faults that the specific
        # handlers above miss — notably ``http.client.IncompleteRead`` /
        # ``http.client.HTTPException`` (raised by ``resp.read()`` on a
        # mid-body connection drop), which are NOT OSError subclasses. The
        # module docstring promises PhotoFetchBlocked on ANY transport
        # error, and the X5 import-commit caller only handles
        # PhotoFetchBlocked — an un-wrapped raise here would fail the whole
        # commit instead of skipping one photo (room → N/A).
        raise PhotoFetchBlocked(
            "read_error",
            f"error fetching {host!r}: {type(exc).__name__}: {exc}",
        ) from exc


def _read_capped(resp: object, cap: int) -> bytes:
    """Read at most ``cap`` bytes from ``resp``; block if it exceeds.

    Enforces the size cap two ways (Content-Length can lie):

    * If the response declares a ``Content-Length`` over the cap, reject
      before reading the body.
    * Stream in fixed chunks and abort the moment the running total
      would exceed the cap — defends against a lying-small (or absent)
      Content-Length on a huge body.
    """
    # Declared Content-Length (best-effort; may be absent or false).
    headers = getattr(resp, "headers", None)
    declared = None
    if headers is not None:
        raw_len = headers.get("Content-Length")
        if raw_len is not None:
            try:
                declared = int(raw_len)
            except (TypeError, ValueError):
                declared = None
    if declared is not None and declared > cap:
        raise PhotoFetchBlocked(
            "too_large_declared",
            f"Content-Length {declared} exceeds cap {cap}",
        )

    read = getattr(resp, "read", None)
    if not callable(read):
        raise PhotoFetchBlocked("no_body", "response has no readable body")

    chunks: list[bytes] = []
    total = 0
    # Read one byte past the cap so an exactly-at-cap body is accepted but
    # an over-cap body is detected without reading the whole thing.
    while True:
        chunk = read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise PhotoFetchBlocked(
                "too_large_streamed",
                f"response body exceeds cap {cap} (read >{total} bytes)",
            )
        chunks.append(chunk)
    return b"".join(chunks)


__all__ = [
    "ALLOWLIST_ENV",
    "DEFAULT_ALLOWLIST",
    "DEFAULT_TIMEOUT_SEC",
    "PHOTO_FETCH_BLOCKED_CODE",
    "TIMEOUT_ENV",
    "PhotoFetchBlocked",
    "allowlist",
    "fetch_photo",
    "timeout_sec",
]
