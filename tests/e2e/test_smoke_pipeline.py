"""End-to-end synthetic-audio smoke for the v1 listening loop.

This is the slow E2E that wires every Phase B surface together:

* Boots the real backend in ``--smoke`` mode (synthetic-audio
  :class:`toybox.audio.test_adapter.WavToBufferStream` replaces the live
  PortAudio mic).
* Boots the real frontend via ``npm run dev`` (vite on :4000 with a
  ``/api`` + ``/ws`` proxy to the backend).
* Opens BOTH parent + child browser contexts BEFORE the WAV-driven
  suggestion fires. Both clients are subscribed live so they each see
  the propose/approve envelopes — the spec's literal wording "Child UI
  loads, recovers active step via reconnect-resync, renders step 1"
  matches this in-flight subscriber path. (The ws hub does not replay
  state-on-subscribe today; a fresh subscriber that connected AFTER the
  approve envelope went out would land on the idle screen, which is the
  whole point of opening the child early.)
* Drives the parent UI through Playwright: waits for the suggestion
  card (transcript -> VAD -> STT -> trigger -> propose), approves it,
  and asserts the activity panel transitions to ``approved``.
* Asserts the child UI leaves the idle screen and renders step 1
  (visible ``step-card`` test id; ``child-idle`` no longer present).

Marked ``@pytest.mark.slow`` so the default ``uv run pytest`` skips it.
The build-step orchestrator runs it explicitly in the evidence step:
``uv run pytest -m slow tests/e2e/test_smoke_pipeline.py``.

Invocation matrix (``addopts`` is intentionally NOT set in
``pyproject.toml`` to avoid the ``-m 'not slow'`` + ``-m slow``
collision on pytest <8):

* ``uv run pytest`` — runs ALL tests including slow. The slow E2E
  ``skipif``s itself when artifacts (whisper cache, fixture WAV,
  playwright runtime, npm) are absent, so a fresh checkout still
  collects + runs cleanly with the heavy test reported as ``s``.
* ``uv run pytest -m "not slow"`` — fast subset, the recommended
  default for an interactive developer loop.
* ``uv run pytest -m slow tests/e2e/test_smoke_pipeline.py`` — smoke
  only; this is what the build-step orchestrator runs.

External requirements (the harness skips with a clear message when
absent so a clean ``--collect-only`` still works):

* ``playwright`` Python package + an installed Chromium runtime
  (``uv run playwright install chromium``).
* ``npm`` on PATH for the frontend dev server.
* ``data/models/silero_vad.onnx`` and the
  ``models--Systran--faster-whisper-small`` cache under ``data/models/``.
* ``tests/fixtures/audio/lets_play_unicorns.wav`` (regenerate via
  ``uv run python scripts/gen_smoke_wav.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Final

import pytest

_logger = logging.getLogger(__name__)

# Boot timing budgets. The whole flow must finish in <60s including
# capture latency + VAD + STT + trigger + propose + Playwright drive.
# Per-step budgets sum to <= OVERALL_TIMEOUT_SEC so a single-stage
# slowness can't outrun the outer wait_for guard. (Iter-2 had per-step
# sub-budgets summing to 120s with an outer 60s guard — the outer guard
# would always fire first on a cold whisper-model load and the per-step
# error message would never surface.)
BACKEND_READY_TIMEOUT_SEC: Final[float] = 15.0
FRONTEND_READY_TIMEOUT_SEC: Final[float] = 15.0
SUGGESTION_TIMEOUT_SEC: Final[float] = 20.0
ACTIVITY_TIMEOUT_SEC: Final[float] = 5.0
CHILD_TIMEOUT_SEC: Final[float] = 5.0
OVERALL_TIMEOUT_SEC: Final[float] = 60.0

# Smoke uses the backend's default port + vite's pinned :4000 because
# vite's dev proxy hard-codes ``http://localhost:8000`` (see
# ``frontend/vite.config.ts``). Reusing the defaults keeps the proxy
# untouched. The build-step orchestrator runs each evidence step in a
# fresh worktree so port reuse is fine.
BACKEND_HOST: Final[str] = "127.0.0.1"
BACKEND_PORT: Final[int] = 8000
FRONTEND_HOST: Final[str] = "localhost"
FRONTEND_PORT: Final[int] = 4000
BACKEND_URL: Final[str] = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
FRONTEND_URL: Final[str] = f"http://{FRONTEND_HOST}:{FRONTEND_PORT}"

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SMOKE_WAV: Final[Path] = REPO_ROOT / "tests" / "fixtures" / "audio" / "lets_play_unicorns.wav"
WHISPER_CACHE: Final[Path] = REPO_ROOT / "data" / "models"

# Required artifacts gate the test with skip rather than fail so a
# fresh checkout (no models downloaded) still runs ``--collect-only``
# clean. The orchestrator's evidence step verifies the artifacts are
# present before invoking pytest.
_REQUIRED_PATHS: tuple[Path, ...] = (
    SMOKE_WAV,
    WHISPER_CACHE / "silero_vad.onnx",
)


def _missing_artifacts() -> list[Path]:
    return [p for p in _REQUIRED_PATHS if not p.exists()]


def _playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def _npm_available() -> bool:
    return shutil.which("npm") is not None


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _playwright_available(),
        reason="playwright python package not installed (uv pip install playwright)",
    ),
    pytest.mark.skipif(
        not _npm_available(),
        reason="npm not on PATH (frontend dev server cannot start)",
    ),
]


# ---------------------------------------------------------------------
# Subprocess + readiness helpers
# ---------------------------------------------------------------------


async def _wait_until_ready(
    url: str,
    *,
    timeout: float,
    label: str,
    expect_status: int = 200,
) -> None:
    """Poll ``url`` until it returns ``expect_status`` or ``timeout`` elapses."""
    deadline = asyncio.get_event_loop().time() + timeout
    last_err: str | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status == expect_status:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_err = repr(exc)
        await asyncio.sleep(0.5)
    raise TimeoutError(f"{label} not ready at {url} after {timeout:.0f}s (last_err={last_err})")


@contextlib.asynccontextmanager
async def _start_backend(artifact_dir: Path) -> AsyncIterator[subprocess.Popen[bytes]]:
    """Boot ``python -m toybox.main --smoke`` and wait for ``/api/health``."""
    log_path = artifact_dir / "backend.log"
    log_handle = log_path.open("wb")
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "toybox.main",
                "--smoke",
                "--smoke-wav",
                str(SMOKE_WAV),
                "--host",
                BACKEND_HOST,
                "--port",
                str(BACKEND_PORT),
            ],
            cwd=REPO_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except BaseException:
        # Popen raised before returning a handle — close the log file we
        # just opened so the test temp dir can be cleaned up. Without
        # this guard a Windows file-handle leak makes pytest's tmp_path
        # cleanup choke on a held log.
        with contextlib.suppress(OSError):
            log_handle.close()
        raise
    try:
        await _wait_until_ready(
            f"{BACKEND_URL}/api/health",
            timeout=BACKEND_READY_TIMEOUT_SEC,
            label="backend",
        )
        yield proc
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        with contextlib.suppress(OSError):
            log_handle.close()


@contextlib.asynccontextmanager
async def _start_frontend(artifact_dir: Path) -> AsyncIterator[subprocess.Popen[bytes]]:
    """Boot ``npm run dev`` and wait for ``GET /``."""
    log_path = artifact_dir / "frontend.log"
    log_handle = log_path.open("wb")
    npm_cmd = shutil.which("npm")
    if npm_cmd is None:
        with contextlib.suppress(OSError):
            log_handle.close()
        raise RuntimeError("npm not on PATH (skipif should have prevented this)")
    try:
        proc = subprocess.Popen(
            [npm_cmd, "run", "dev"],
            cwd=REPO_ROOT / "frontend",
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            # On Windows, npm.cmd needs a shell to resolve correctly; on
            # POSIX the explicit npm path is enough. shell=False is fine on
            # both because shutil.which returns the .cmd shim path on
            # Windows that subprocess can launch directly.
        )
    except BaseException:
        with contextlib.suppress(OSError):
            log_handle.close()
        raise
    try:
        await _wait_until_ready(
            f"{FRONTEND_URL}/",
            timeout=FRONTEND_READY_TIMEOUT_SEC,
            label="frontend",
        )
        yield proc
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        with contextlib.suppress(OSError):
            log_handle.close()


# ---------------------------------------------------------------------
# Envelope-shape assertion
# ---------------------------------------------------------------------


def _assert_envelope_shape(env: dict[str, Any]) -> None:
    """Every ws envelope on the wire matches ``{topic, ts, payload, schema_version}``."""
    assert set(env.keys()) >= {"topic", "ts", "payload", "schema_version"}, (
        f"envelope missing required fields: keys={sorted(env.keys())!r}"
    )
    assert isinstance(env["topic"], str)
    assert isinstance(env["ts"], str)
    assert isinstance(env["payload"], dict)
    assert isinstance(env["schema_version"], int) and env["schema_version"] >= 1


# ---------------------------------------------------------------------
# The slow test
# ---------------------------------------------------------------------


async def test_smoke_synthetic_audio_full_loop(tmp_path: Path) -> None:
    """End-to-end smoke: WAV -> VAD -> STT -> trigger -> propose -> approve -> child."""
    missing = _missing_artifacts()
    if missing:
        pytest.skip(
            "smoke artifacts missing: " + ", ".join(str(p.relative_to(REPO_ROOT)) for p in missing)
        )

    # Lazy import: keeps the module importable on hosts without
    # playwright (the skipif guard already handles those, but a defensive
    # import is friendlier to ``pytest --collect-only``).
    from playwright.async_api import async_playwright  # noqa: PLC0415

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        async with _start_backend(artifact_dir), _start_frontend(artifact_dir):
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    parent_ctx = await browser.new_context()
                    child_ctx = await browser.new_context()
                    await parent_ctx.tracing.start(screenshots=True, snapshots=True)
                    await child_ctx.tracing.start(screenshots=True, snapshots=True)

                    captured_envelopes: list[dict[str, Any]] = []

                    parent_page = await parent_ctx.new_page()
                    parent_page.on(
                        "websocket",
                        lambda ws: ws.on(
                            "framereceived",
                            lambda payload: _record_frame(captured_envelopes, payload),
                        ),
                    )
                    # Open the CHILD page BEFORE the suggestion fires so
                    # both clients are subscribed live. The ws hub does
                    # not replay state-on-subscribe; a child that
                    # connected AFTER the approve envelope went out would
                    # remain on the idle screen forever (this was the
                    # iter-2 bug — child opened post-approve and the test
                    # passed only because the assertion was loose).
                    child_page = await child_ctx.new_page()
                    await parent_page.goto(f"{FRONTEND_URL}/parent")
                    await child_page.goto(f"{FRONTEND_URL}/child")

                    # Suggestion appears after the WAV plays through the pipeline.
                    suggestion = parent_page.get_by_test_id("suggestion-card")
                    await suggestion.wait_for(
                        state="visible",
                        timeout=int(SUGGESTION_TIMEOUT_SEC * 1000),
                    )

                    # Approve -> activity panel renders at state=approved.
                    # (The /approve route transitions proposed -> approved
                    # only; running requires a separate /advance call.
                    # See src/toybox/api/activities.py:post_approve.)
                    await parent_page.get_by_test_id("approve-button").click()
                    activity_panel = parent_page.get_by_test_id("activity-panel")
                    await activity_panel.wait_for(
                        state="visible",
                        timeout=int(ACTIVITY_TIMEOUT_SEC * 1000),
                    )
                    # Tight assertion: the panel reflects an actually-approved
                    # row, not just a transient `proposed` render. Per
                    # ActivityPanel.tsx the panel exposes ``data-activity-state``.
                    from playwright.async_api import expect  # noqa: PLC0415

                    await expect(activity_panel).to_have_attribute(
                        "data-activity-state",
                        "approved",
                        timeout=int(ACTIVITY_TIMEOUT_SEC * 1000),
                    )

                    # Child UI: assert it leaves idle and renders step 1.
                    # ``step-card`` is the testid that marks "an active
                    # activity is rendered" (see StepCard.tsx). The
                    # ``showActive`` branch in child/App.tsx requires
                    # state in {approved, running} so step-card is the
                    # signal that the approve envelope was applied.
                    step_card = child_page.get_by_test_id("step-card")
                    await step_card.wait_for(
                        state="visible",
                        timeout=int(CHILD_TIMEOUT_SEC * 1000),
                    )
                    # And the idle screen is gone.
                    child_idle_count = await child_page.get_by_test_id(
                        "child-idle"
                    ).count()
                    assert child_idle_count == 0, (
                        f"child still showing idle screen after approve "
                        f"(child-idle count={child_idle_count})"
                    )

                    # Verify at least one ws envelope crossed the wire and matched the schema.
                    assert captured_envelopes, "no ws envelopes captured during smoke"
                    _assert_envelope_shape(captured_envelopes[0])
                finally:
                    # Always export the trace so a failed run leaves
                    # diagnostic artefacts behind for the orchestrator.
                    with contextlib.suppress(Exception):
                        await parent_ctx.tracing.stop(path=str(artifact_dir / "parent-trace.zip"))
                    with contextlib.suppress(Exception):
                        await child_ctx.tracing.stop(path=str(artifact_dir / "child-trace.zip"))
                    await browser.close()

    try:
        await asyncio.wait_for(_run(), timeout=OVERALL_TIMEOUT_SEC)
    except TimeoutError:
        pytest.fail(
            f"smoke pipeline exceeded {OVERALL_TIMEOUT_SEC:.0f}s overall budget; "
            f"artefacts in {artifact_dir}"
        )


def _record_frame(sink: list[dict[str, Any]], payload: str) -> None:
    """Best-effort JSON parse of a ws frame, append envelope-shaped messages.

    The wire carries non-envelope frames too (``ready``, ``ping``,
    ``pong``); we filter on the presence of ``topic`` so the assertion
    later only inspects envelope-shaped messages.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    if "topic" not in data:
        return
    sink.append(data)
