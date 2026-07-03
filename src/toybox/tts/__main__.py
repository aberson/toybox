"""``python -m toybox.tts`` — operator entrypoint for model downloads.

Idiom: ``python -m toybox.audio.stt --download`` / ``python -m
toybox.ai.room_classifier --download``. Fetches the Kokoro-82M ONNX
model + voices bin into ``<data_root>/models/tts/`` so the first real
synth doesn't stall on a ~300 MB download.

``--download --dry-run`` prints the download targets (URL → dest)
WITHOUT touching the network or the filesystem — and, per the tts
package's lazy-import contract, works without the ``tts`` extra
installed (this module only needs the stdlib + the engine module's
path constants).

The download itself also needs no optional deps: it streams via
``urllib`` exactly like the room-classifier downloader.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import urllib.request
from pathlib import Path
from typing import Final

from .engine import MODEL_FILENAME, VOICES_FILENAME, model_dir

_logger = logging.getLogger(__name__)

# Model source: the kokoro-onnx project's released model files
# (github.com/thewh1teagle/kokoro-onnx, MIT wrapper around the
# Apache-2.0 hexgrad/Kokoro-82M weights on HuggingFace). The
# ``model-files-v1.0`` release is the wrapper's documented install
# source for exactly the two files below; pinning the release tag
# keeps the download reproducible.
_RELEASE_BASE: Final[str] = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)
MODEL_URL: Final[str] = f"{_RELEASE_BASE}/{MODEL_FILENAME}"
VOICES_URL: Final[str] = f"{_RELEASE_BASE}/{VOICES_FILENAME}"

# Per-request download timeout (seconds); a stalled GitHub connection
# must not hang the operator's --download forever. Env-overridable
# (mirrors TOYBOX_CLIP_DOWNLOAD_TIMEOUT in room_classifier).
DOWNLOAD_TIMEOUT_ENV: Final[str] = "TOYBOX_TTS_DOWNLOAD_TIMEOUT"
DEFAULT_DOWNLOAD_TIMEOUT: Final[float] = 300.0

# A real ONNX / voices asset is comfortably larger than this; anything
# smaller is almost certainly an error page that slipped through with
# a 200 status.
_MIN_ASSET_BYTES: Final[int] = 1024


def download_targets() -> list[tuple[str, Path]]:
    """Return the ``(url, destination)`` pairs --download would fetch."""
    base = model_dir()
    return [
        (MODEL_URL, base / MODEL_FILENAME),
        (VOICES_URL, base / VOICES_FILENAME),
    ]


def _download_timeout() -> float:
    """Resolve the download timeout (seconds); env-overridable, fail-safe."""
    raw = os.environ.get(DOWNLOAD_TIMEOUT_ENV)
    if not raw:
        return DEFAULT_DOWNLOAD_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        _logger.warning(
            "ignoring non-numeric %s=%r; using default %.0fs",
            DOWNLOAD_TIMEOUT_ENV,
            raw,
            DEFAULT_DOWNLOAD_TIMEOUT,
        )
        return DEFAULT_DOWNLOAD_TIMEOUT
    return value if value > 0 else DEFAULT_DOWNLOAD_TIMEOUT


def _download_file(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest`` via urllib (no extra HTTP dep).

    Mirrors the room-classifier downloader's robustness (per-request
    timeout, ``.part`` temp file removed on ANY mid-stream failure so a
    half-written model is never promoted, HTML-error-body + tiny-payload
    rejection) and adds a Content-Length shortfall check: http.client
    signals a premature clean EOF by returning ``b""`` from ``read()``
    WITHOUT raising, so a truncated body ≥ the tiny-payload floor would
    otherwise be promoted — and, because :func:`download_models`'
    skip-if-exists check is existence-only, the corrupt model would then
    be kept forever while ``is_tts_capable()`` reports True. When the
    server sends no usable Content-Length (chunked transfer), the
    shortfall check is skipped and the other guards still apply.
    """
    _logger.info("downloading %s -> %s", url, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "toybox-tts"})
    timeout = _download_timeout()
    written = 0
    expected_bytes: int | None = None
    promoted = False
    try:
        with (
            urllib.request.urlopen(req, timeout=timeout) as resp,  # noqa: S310 — pinned GH host
            tmp.open("wb") as fh,
        ):
            status = getattr(resp, "status", None)
            if status is not None and status != 200:
                raise OSError(f"download of {url} returned HTTP {status}")
            content_type = resp.headers.get("Content-Type", "") if resp.headers else ""
            if "text/html" in content_type.lower():
                raise OSError(
                    f"download of {url} returned an HTML body "
                    f"(Content-Type={content_type!r}) — likely an error page, "
                    "not the model asset"
                )
            content_length = resp.headers.get("Content-Length") if resp.headers else None
            if content_length:
                try:
                    expected_bytes = int(content_length)
                except ValueError:
                    expected_bytes = None
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
        if expected_bytes is not None and written != expected_bytes:
            raise OSError(
                f"download of {url} truncated: got {written} of "
                f"{expected_bytes} bytes (premature EOF)"
            )
        if written < _MIN_ASSET_BYTES:
            raise OSError(
                f"download of {url} produced only {written} bytes "
                f"(< {_MIN_ASSET_BYTES}); treating as a failed/error response"
            )
        os.replace(tmp, dest)
        promoted = True
    finally:
        if not promoted:
            tmp.unlink(missing_ok=True)


def download_models(*, dry_run: bool = False) -> int:
    """Fetch the Kokoro model + voices bin; skip files already present.

    ``dry_run=True`` prints each target WITHOUT network or filesystem
    side effects. Returns a process exit code.
    """
    targets = download_targets()
    if dry_run:
        for url, dest in targets:
            print(f"would download {url} -> {dest}")
        return 0
    for url, dest in targets:
        if dest.is_file():
            _logger.info("%s already present; skipping download", dest)
            continue
        _download_file(url, dest)
    print(f"kokoro tts model files ready in {model_dir()}")
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toybox.tts",
        description=(
            "Kokoro TTS operator entrypoint. Use --download to fetch the "
            "Kokoro-82M ONNX model + voices bin into data/models/tts/ "
            "before the first synth."
        ),
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help=(
            f"Fetch {MODEL_FILENAME} + {VOICES_FILENAME} from the kokoro-onnx "
            "project's model-files release into data/models/tts/ "
            "(TOYBOX_DATA_DIR-relative). Exits cleanly when files are cached."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "With --download: print the download targets (URL -> dest) and "
            "exit without touching the network or filesystem."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if not args.download:
        parser.print_help()
        return 0
    try:
        return download_models(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        _logger.exception("tts --download failed")
        print(
            f"download failed: dir={model_dir()}, exc_type={type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover — operator entry
    raise SystemExit(main())


__all__ = [
    "DEFAULT_DOWNLOAD_TIMEOUT",
    "DOWNLOAD_TIMEOUT_ENV",
    "MODEL_URL",
    "VOICES_URL",
    "download_models",
    "download_targets",
    "main",
]
