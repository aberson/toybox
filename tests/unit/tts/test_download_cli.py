"""Phase Z Z3 — ``python -m toybox.tts --download`` CLI contract.

The plan's done-when pins: ``--download --dry-run`` prints targets and
exits 0 WITHOUT the ``tts`` extra installed and without network. The
worktree/CI venv genuinely lacks the extra, so the subprocess test
exercises the lazy-import discipline for real (a clean interpreter, no
conftest imports to mask an eager import).

The orchestration path is exercised with ``_download_file``
monkeypatched to a recorder; ``_download_file`` itself is exercised
hermetically against a fake ``urllib.request.urlopen`` response —
network stays untouched throughout. The fake-response suite pins the
truncated-body fix (iteration 2): a premature clean EOF (``read()``
returns ``b""`` without raising, bytes written < Content-Length) must
NOT be promoted to the destination path, because the skip-if-exists
check is existence-only and would keep the corrupt model forever.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

import toybox.tts.__main__ as tts_main
from toybox.tts.engine import DATA_DIR_ENV, MODEL_FILENAME, VOICES_FILENAME


def test_dry_run_prints_targets_and_exits_zero_in_clean_interpreter(
    tmp_path: Path,
) -> None:
    env = dict(os.environ)
    env[DATA_DIR_ENV] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "toybox.tts", "--download", "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert MODEL_FILENAME in result.stdout
    assert VOICES_FILENAME in result.stdout
    assert "github.com/thewh1teagle/kokoro-onnx" in result.stdout
    # Dry-run must not touch the filesystem — not even a mkdir.
    assert list(tmp_path.iterdir()) == []


def test_dry_run_lists_both_targets_via_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    rc = tts_main.main(["--download", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("would download ")]
    assert len(lines) == 2
    assert lines[0].endswith(str(tmp_path / "models" / "tts" / MODEL_FILENAME))
    assert lines[1].endswith(str(tmp_path / "models" / "tts" / VOICES_FILENAME))


def test_bare_invocation_prints_help_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = tts_main.main([])
    assert rc == 0
    assert "--download" in capsys.readouterr().out


def test_download_skips_files_already_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    target_dir = tmp_path / "models" / "tts"
    target_dir.mkdir(parents=True)
    (target_dir / MODEL_FILENAME).write_bytes(b"cached-onnx")

    fetched: list[tuple[str, Path]] = []

    def _record(url: str, dest: Path) -> None:
        fetched.append((url, dest))
        dest.write_bytes(b"downloaded")

    monkeypatch.setattr(tts_main, "_download_file", _record)
    rc = tts_main.main(["--download"])
    assert rc == 0
    # Only the missing voices bin is fetched; the cached model is kept.
    assert [dest.name for _url, dest in fetched] == [VOICES_FILENAME]
    assert (target_dir / MODEL_FILENAME).read_bytes() == b"cached-onnx"


def test_download_failure_maps_to_exit_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))

    def _boom(url: str, dest: Path) -> None:
        raise OSError("simulated network failure")

    monkeypatch.setattr(tts_main, "_download_file", _boom)
    rc = tts_main.main(["--download"])
    assert rc == 1


def test_download_targets_point_into_models_tts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    targets = tts_main.download_targets()
    assert [dest.name for _url, dest in targets] == [MODEL_FILENAME, VOICES_FILENAME]
    for url, dest in targets:
        assert url.startswith("https://github.com/thewh1teagle/kokoro-onnx/releases/")
        assert dest.parent == tmp_path / "models" / "tts"


# ---------------------------------------------------------------------------
# _download_file — hermetic failure-path coverage (fake urlopen, no network)
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for the http.client response urlopen yields.

    Only the surface ``_download_file`` touches: context manager,
    ``status``, ``headers.get(...)``, chunked ``read(n)``. A dict
    satisfies the ``headers.get`` calls. ``content_length=None`` omits
    the header entirely (the chunked-transfer shape).
    """

    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/octet-stream",
        content_length: int | None = None,
    ) -> None:
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers: dict[str, str] = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, n: int) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> None:
    def _fake_urlopen(req: Any, timeout: float) -> _FakeResponse:
        return response

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)


def _assert_no_artifacts(dest: Path) -> None:
    """Neither the destination nor its ``.part`` temp may survive a failure."""
    assert not dest.exists(), "failed download must not promote the destination"
    assert not dest.with_suffix(dest.suffix + ".part").exists(), (
        "failed download must clean up its .part temp file"
    )


def test_download_file_happy_path_promotes_and_cleans_part(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = b"x" * 2048
    _patch_urlopen(monkeypatch, _FakeResponse(body, content_length=len(body)))
    dest = tmp_path / MODEL_FILENAME
    tts_main._download_file("https://example.invalid/model.onnx", dest)
    assert dest.read_bytes() == body
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_download_file_truncated_body_is_not_promoted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Premature clean EOF: 2 KiB arrive of an advertised 4 KiB. Pre-fix
    this passed the tiny-payload floor and was promoted; the corrupt
    file was then kept forever by the existence-only skip check."""
    _patch_urlopen(monkeypatch, _FakeResponse(b"x" * 2048, content_length=4096))
    dest = tmp_path / MODEL_FILENAME
    with pytest.raises(OSError, match="truncated"):
        tts_main._download_file("https://example.invalid/model.onnx", dest)
    _assert_no_artifacts(dest)


def test_download_file_rejects_html_error_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = b"<html>captive portal</html>" * 100
    _patch_urlopen(
        monkeypatch,
        _FakeResponse(body, content_type="text/html; charset=utf-8", content_length=len(body)),
    )
    dest = tmp_path / MODEL_FILENAME
    with pytest.raises(OSError, match="HTML body"):
        tts_main._download_file("https://example.invalid/model.onnx", dest)
    _assert_no_artifacts(dest)


def test_download_file_rejects_tiny_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A complete-but-tiny payload (< _MIN_ASSET_BYTES) is rejected even
    when it matches its own Content-Length."""
    _patch_urlopen(monkeypatch, _FakeResponse(b"x" * 10, content_length=10))
    dest = tmp_path / MODEL_FILENAME
    with pytest.raises(OSError, match="only 10 bytes"):
        tts_main._download_file("https://example.invalid/model.onnx", dest)
    _assert_no_artifacts(dest)


def test_download_file_without_content_length_stays_graceful(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Chunked transfer (no Content-Length header): the shortfall check
    is skipped and a plausible body still promotes."""
    body = b"x" * 2048
    _patch_urlopen(monkeypatch, _FakeResponse(body, content_length=None))
    dest = tmp_path / MODEL_FILENAME
    tts_main._download_file("https://example.invalid/model.onnx", dest)
    assert dest.read_bytes() == body


def test_download_file_rejects_non_200_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_urlopen(monkeypatch, _FakeResponse(b"x" * 2048, status=503, content_length=2048))
    dest = tmp_path / MODEL_FILENAME
    with pytest.raises(OSError, match="HTTP 503"):
        tts_main._download_file("https://example.invalid/model.onnx", dest)
    _assert_no_artifacts(dest)


# ---------------------------------------------------------------------------
# _download_timeout — env parse fallbacks
# ---------------------------------------------------------------------------


def test_download_timeout_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tts_main.DOWNLOAD_TIMEOUT_ENV, raising=False)
    assert tts_main._download_timeout() == tts_main.DEFAULT_DOWNLOAD_TIMEOUT


def test_download_timeout_parses_valid_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tts_main.DOWNLOAD_TIMEOUT_ENV, "42.5")
    assert tts_main._download_timeout() == 42.5


def test_download_timeout_falls_back_on_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tts_main.DOWNLOAD_TIMEOUT_ENV, "soon")
    assert tts_main._download_timeout() == tts_main.DEFAULT_DOWNLOAD_TIMEOUT


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_download_timeout_falls_back_on_non_positive(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(tts_main.DOWNLOAD_TIMEOUT_ENV, raw)
    assert tts_main._download_timeout() == tts_main.DEFAULT_DOWNLOAD_TIMEOUT
