"""End-to-end coverage for the F4 worker via :func:`create_app`.

Wires the worker via the public app-factory + lifespan path
(:func:`toybox.app.image_gen_worker_lifespan`) and asserts the full
``queued`` → ``running`` → ``done`` lifecycle:

1. PNG written to ``data/images/toy_actions/<toy_id>/<slot>.png``.
2. DB row in ``done`` state with the persisted ``image_path``.
3. WS envelopes captured via the subscriber pattern used by
   :mod:`tests.integration.test_metrics_ws`.

The pipeline is the deterministic stub
(``TOYBOX_IMAGE_GEN_STUB=1``); we don't exercise torch / diffusers
in CI.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.image_gen.capability import (
    CapabilityReason,
    reset_image_gen_breaker_for_tests,
)
from toybox.image_gen.pipeline import DEFAULT_NEGATIVE_PROMPT
from toybox.image_gen.worker import (
    reset_image_gen_worker_for_tests,
    start_image_gen_worker,
    stop_image_gen_worker,
)
from toybox.ws.envelope import Envelope, build_envelope
from toybox.ws.topics import Topic


def _has_cuda() -> bool:
    """Best-effort CUDA probe used by tests that hit the real generator path.

    Imported lazily-via-function so the module-import contract for the
    pipeline (no torch at module scope of its public callers) is preserved.
    """
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False

_TOY_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    reset_image_gen_worker_for_tests()
    reset_image_gen_breaker_for_tests()
    yield
    reset_image_gen_worker_for_tests()
    reset_image_gen_breaker_for_tests()


@pytest.fixture
def configured_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Migrate a tmp DB, seed a toy + photo, redirect data dir."""
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOYBOX_DB_PATH", str(tmp_path / "toybox.db"))
    # Force the deterministic stub pipeline.
    monkeypatch.setenv("TOYBOX_IMAGE_GEN_STUB", "1")
    monkeypatch.delenv("TOYBOX_IMAGE_GEN_STUB_MODE", raising=False)
    monkeypatch.delenv("TOYBOX_IMAGE_GEN_STUB_DELAY_SEC", raising=False)

    db = tmp_path / "toybox.db"
    conn = connect(db)
    try:
        run_migrations(conn)
        with conn:
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    _TOY_ID,
                    "Bunny",
                    "data/images/toys/bunny.jpg",
                    "h1",
                    "2026-05-06T00:00:00Z",
                ),
            )
    finally:
        conn.close()

    photo = tmp_path / "images" / "toys" / "bunny.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01")
    return db


def _capable_probe() -> tuple[bool, CapabilityReason, str]:
    """Pin capability to CAPABLE so the worker dispatches to the pipeline."""
    return True, CapabilityReason.capable, "capable"


async def test_full_lifecycle_via_app_lifespan(
    configured_paths: Path,
    tmp_path: Path,
) -> None:
    """The full app-lifespan path drives queue → stub pipeline → DB → WS.

    Uses :func:`start_image_gen_worker` directly with a per-test
    pubsub-emit so we avoid touching the global pubsub. The worker is
    the same singleton the FastAPI lifespan would set up.
    """
    pubsub = PubSub(coalesce_window_ms=0)

    async def _emit(topic: Topic, payload: dict[str, object]) -> None:
        pubsub.publish(build_envelope(topic=topic, payload=payload))

    def _conn_factory() -> object:
        return connect(configured_paths, check_same_thread=False)

    # Use the real (stubbed) pipeline path — no override. Pin
    # capability to CAPABLE so the F.5-3a dispatch routes to the
    # diffusion pipeline (i.e. the stub) rather than the composite
    # path; on a CI host without torch, the real capability gate
    # otherwise reports NO_CUDA.
    worker = await start_image_gen_worker(_conn_factory, _emit, capability_probe=_capable_probe)
    try:
        # Subscribe BEFORE enqueue so the ``queued`` envelope isn't
        # missed.
        sub = pubsub.subscribe([Topic.toy_actions])
        try:
            await worker.enqueue(_TOY_ID, "idle", seed=12345)
            collected: list[Envelope] = []

            async def _collect_until_done() -> None:
                async with asyncio.timeout(5.0):
                    while True:
                        env = await sub.get()
                        if env.topic is not Topic.toy_actions:
                            continue
                        collected.append(env)
                        if env.payload.get("status") == "done":
                            return

            await _collect_until_done()
        finally:
            sub.close()
    finally:
        await stop_image_gen_worker()

    statuses = [env.payload["status"] for env in collected]
    assert statuses == ["queued", "running", "done"], statuses

    done = collected[-1]
    assert done.payload["toy_id"] == _TOY_ID
    assert done.payload["slot"] == "idle"
    assert done.payload["image_path"] == (f"data/images/toy_actions/{_TOY_ID}/idle.png")
    assert done.payload["error"] is None

    # PNG actually written by the stub pipeline.
    out_path = tmp_path / "images" / "toy_actions" / _TOY_ID / "idle.png"
    assert out_path.is_file()
    # Stub pipeline produces a valid PNG (16x16 RGBA per the stub
    # fixture); just sanity-check the magic bytes.
    raw = out_path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    # DB row reflects ``done``.
    conn = connect(configured_paths)
    try:
        row = conn.execute(
            "SELECT status, image_path FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "idle"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    assert row["image_path"] == f"data/images/toy_actions/{_TOY_ID}/idle.png"


# ---------------------------------------------------------------------
# Phase P P4: IP-Adapter wire-shape integration test
# ---------------------------------------------------------------------
#
# Per workspace ``code-quality.md`` § "New components require an
# integration test through the production caller": the IPA load +
# scale + ``ip_adapter_image`` kwarg are new wiring that unit tests
# of ``pipeline.py`` alone can't prove reaches production. This test
# drives the FastAPI/worker dispatch path → real
# ``generate_action`` → real ``_run_pipeline_sync`` (NOT the stub) and
# asserts the production ``pipe(...)`` call carries
# ``ip_adapter_image=<rembg-cutout Pillow Image>``.
#
# To avoid pulling a real GPU + ~5 GB of weights into CI we replace
# THREE seams just below ``_run_pipeline_sync``:
#
#   * ``rembg.new_session`` → a sentinel object.
#   * ``rembg.remove`` → identity on the input bytes (the "cutout" is
#     the reference photo's bytes, valid PNG/JPEG either way).
#   * ``pipeline._build_pipeline`` → returns a ``_FakePipe`` that
#     records the kwargs of its ``__call__`` and returns a fake
#     ``result.images[0]`` Pillow image.
#
# The stub-pipeline path (``TOYBOX_IMAGE_GEN_STUB=1``) bypasses
# ``_run_pipeline_sync`` entirely so it cannot exercise the
# ``ip_adapter_image`` kwarg — this test deliberately disables it.


def _make_png_bytes(rgba: tuple[int, int, int, int] = (200, 50, 50, 255)) -> bytes:
    """Build a tiny valid RGBA PNG that PIL + rembg-passthrough accept."""
    img = Image.new("RGBA", (8, 8), rgba)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeResult:
    """Stand-in for the diffusers ``StableDiffusionPipelineOutput``."""

    def __init__(self, images: list[Any]) -> None:
        self.images = images


class _FakePipe:
    """Captures the kwargs of the production ``pipe(...)`` call.

    The real ``_run_pipeline_sync`` calls ``pipe(...)`` with
    ``prompt``, ``negative_prompt``, ``ip_adapter_image``,
    ``generator``, ``num_inference_steps``, ``guidance_scale``,
    ``height``, ``width``. Recording all of them lets the test
    assert the new ``ip_adapter_image`` wiring landed.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> _FakeResult:
        # Snapshot the kwargs for later assertions.
        self.calls.append(dict(kwargs))
        # Build a deterministic 512×512 RGBA image so the downstream
        # rembg-passthrough + resize() steps work.
        img = Image.new("RGBA", (512, 512), (10, 20, 30, 255))
        return _FakeResult(images=[img])


@pytest.mark.skipif(
    not _has_cuda(),
    reason="requires CUDA for torch.Generator('cuda') seeding in _run_pipeline_sync",
)
async def test_worker_e2e_passes_ip_adapter_image_to_pipe(
    configured_paths: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: worker → real ``generate_action`` → fake ``pipe(...)``
    sees ``ip_adapter_image`` set to the rembg-cutout Pillow image.

    This is the silent-wiring guard for P4: if a future change drops
    the ``ip_adapter_image`` kwarg from the production ``pipe(...)``
    call (or never adds it), the unit-test-only path of pipeline.py
    can't catch it; this test does.
    """
    from toybox.image_gen import pipeline as pipeline_mod

    # 1. Disable the stub path so ``_run_pipeline_sync`` enters the
    #    real branch that calls ``_build_pipeline`` + ``pipe(...)``.
    monkeypatch.delenv("TOYBOX_IMAGE_GEN_STUB", raising=False)

    # 2. Refresh the toy's reference photo with a valid PNG so the
    #    rembg-passthrough returns valid PNG bytes that PIL can
    #    re-open in ``_run_pipeline_sync`` step 1.
    photo_path = tmp_path / "images" / "toys" / "bunny.jpg"
    photo_path.write_bytes(_make_png_bytes())

    # 3. Reset the module-level pipeline cache so the fake
    #    ``_build_pipeline`` actually runs on this test's first call.
    pipeline_mod.reset_pipeline_cache_for_tests()

    # 4. Patch the rembg source module directly. The function-local
    #    ``from rembg import new_session, remove`` inside
    #    ``_run_pipeline_sync`` resolves names against the rembg module,
    #    so patching ``rembg.new_session`` / ``rembg.remove`` is the only
    #    real interception seam — there's no module-scope alias on
    #    ``pipeline_mod`` to swap.
    def _fake_new_session(**_kwargs: Any) -> object:
        return object()  # sentinel; rembg.remove ignores it below.

    def _fake_remove(data: bytes, **_kwargs: Any) -> bytes:
        return data  # identity passthrough.

    import rembg as _rembg_mod

    monkeypatch.setattr(_rembg_mod, "new_session", _fake_new_session)
    monkeypatch.setattr(_rembg_mod, "remove", _fake_remove)

    # 5. Patch ``_build_pipeline`` to return our recording fake. The
    #    function-local ``import torch`` still runs (real torch is
    #    installed in this worktree) but nothing GPU-bound executes
    #    because the fake ``pipe(...)`` and fake ``torch.Generator``
    #    short-circuit the heavy paths. We let the real torch run
    #    ``Generator("cuda")`` since that's cheap on a CUDA box; if
    #    a future CI runner lacks CUDA, replace with a CPU fake.
    fake_pipe = _FakePipe()

    def _fake_build_pipeline(_torch_mod: Any) -> _FakePipe:
        return fake_pipe

    monkeypatch.setattr(pipeline_mod, "_build_pipeline", _fake_build_pipeline)

    # 6. Drive the worker end-to-end exactly like the lifecycle test.
    pubsub = PubSub(coalesce_window_ms=0)

    async def _emit(topic: Topic, payload: dict[str, object]) -> None:
        pubsub.publish(build_envelope(topic=topic, payload=payload))

    def _conn_factory() -> object:
        return connect(configured_paths, check_same_thread=False)

    worker = await start_image_gen_worker(_conn_factory, _emit, capability_probe=_capable_probe)
    try:
        sub = pubsub.subscribe([Topic.toy_actions])
        try:
            await worker.enqueue(_TOY_ID, "idle", seed=99)

            async def _collect_until_done() -> None:
                async with asyncio.timeout(10.0):
                    while True:
                        env = await sub.get()
                        if env.topic is not Topic.toy_actions:
                            continue
                        if env.payload.get("status") == "done":
                            return
                        if env.payload.get("status") == "failed":
                            raise AssertionError(
                                f"unexpected failure envelope: {env.payload!r}"
                            )

            await _collect_until_done()
        finally:
            sub.close()
    finally:
        await stop_image_gen_worker()
        # Drop the cached fake so other tests that import the module
        # don't see it persist between runs.
        pipeline_mod.reset_pipeline_cache_for_tests()

    # 7. The production pipe(...) was called exactly once with the
    #    expected kwarg shape.
    assert len(fake_pipe.calls) == 1, fake_pipe.calls
    call = fake_pipe.calls[0]
    assert "ip_adapter_image" in call, (
        "production pipe(...) call must pass ip_adapter_image — Phase P "
        "IPA wiring regression"
    )
    ipa_image = call["ip_adapter_image"]
    assert isinstance(ipa_image, Image.Image), (
        f"ip_adapter_image must be a PIL Image (the rembg cutout); got {type(ipa_image)!r}"
    )
    assert ipa_image.mode == "RGBA", (
        f"ip_adapter_image must be RGBA (rembg cutout); got mode={ipa_image.mode!r}"
    )
    # Sanity-check the other plan-pinned generation kwargs are still
    # there so a future refactor that drops them breaks this test.
    assert call["num_inference_steps"] == 4
    assert call["guidance_scale"] == 1.0
    assert call["height"] == 512
    assert call["width"] == 512
    assert "prompt" in call
    # Content-level negative-prompt pin: the extended negative prompt
    # is load-bearing for suppressing the text/glyph artifacts that
    # showed up after the palette-hex tokens were dropped (Phase P).
    assert call["negative_prompt"] == DEFAULT_NEGATIVE_PROMPT

    # 8. PNG round-trips through storage + WS envelope (same gates as
    #    the lifecycle test above; the worker → fake pipe → storage
    #    → DB seams are all real).
    out_path = tmp_path / "images" / "toy_actions" / _TOY_ID / "idle.png"
    assert out_path.is_file()
    raw = out_path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    # DEFAULT_OUTPUT_DIM (512) flows through to the on-disk artifact.
    # This locks the no-op resize() path in ``_run_pipeline_sync``.
    assert Image.open(out_path).size == (512, 512)

    conn = connect(configured_paths)
    try:
        row = conn.execute(
            "SELECT status, image_path FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "idle"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    assert row["image_path"] == f"data/images/toy_actions/{_TOY_ID}/idle.png"
