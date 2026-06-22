"""Regression: the static-file mounts must serve sprite formats with the
correct ``Content-Type``.

``StaticFiles`` derives the header from the stdlib ``mimetypes`` registry,
which on a stock Windows install has no entry for ``.webp`` (served as
``text/plain``) and may lack ``.svg``. ``create_app`` registers both
explicitly via ``_register_static_mime_types``; this test pins that so the
``.webp``→``text/plain`` bug — and the ``.svg`` type the Claude-image path
depends on — can't silently regress.
"""

from __future__ import annotations

import mimetypes

from toybox.app import create_app


def test_create_app_registers_webp_and_svg_mime_types() -> None:
    # create_app() is the seam that registers the types (idempotent).
    create_app()
    assert mimetypes.guess_type("idle.webp")[0] == "image/webp"
    assert mimetypes.guess_type("idle.svg")[0] == "image/svg+xml"
