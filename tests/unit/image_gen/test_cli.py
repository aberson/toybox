"""End-to-end CLI coverage via the in-process ``main()`` entry point.

The CLI shells out via ``uv run python -m toybox.image_gen ...`` in
production; for tests we call :func:`toybox.image_gen.__main__.main`
directly to keep the test fast and avoid subprocess flakiness on
Windows. The stub fixture (:envvar:`TOYBOX_IMAGE_GEN_STUB=1`) means
no torch / GPU is required.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from toybox.db import DB_PATH_ENV, connect
from toybox.db.migrations import run_migrations
from toybox.image_gen.__main__ import main
from toybox.storage import images as images_storage


def _png_bytes(
    size: tuple[int, int] = (32, 32),
    color: tuple[int, int, int] = (180, 50, 50),
) -> bytes:
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """Stand up a tmp DB + data root + seeded toy row.

    Returns ``(data_root, toy_id)``. The CLI reads
    ``TOYBOX_DATA_DIR`` for the output dir + ``TOYBOX_DB_PATH`` for
    the DB file; both point at ``tmp_path``.
    """
    data_root = tmp_path
    db_path = data_root / "toybox.db"
    monkeypatch.setenv(images_storage.DATA_ROOT_ENV, str(data_root))
    monkeypatch.setenv(DB_PATH_ENV, str(db_path))
    # Override the model dir to a sub-tmp so the marker-file write
    # never lands in the real repo's data/models/image_gen.
    monkeypatch.setenv("TOYBOX_IMAGE_GEN_MODEL_DIR", str(data_root / "models" / "image_gen"))
    # Capability env defaults to auto; we don't need it set explicitly.

    # Build the migrated DB.
    conn = connect(db_path)
    try:
        run_migrations(conn)
        # Seed one toy row + matching reference photo on disk.
        toy_id = "550e8400-e29b-41d4-a716-446655440000"
        toys_dir = images_storage.committed_dir("toys")
        photo_path = toys_dir / f"{toy_id}.png"
        photo_path.write_bytes(_png_bytes())
        relative_path = images_storage.relative_committed_path("toys", f"{toy_id}.png")
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        conn.execute(
            """
            INSERT INTO toys (
                id, display_name, image_path, image_hash, type, tags,
                persona_id, archived, created_at, last_used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL)
            """,
            (
                toy_id,
                "Bunny",
                relative_path,
                "deadbeef",
                "plush",
                json.dumps(["plush", "soft"]),
                None,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return data_root, toy_id


def test_cli_use_stub_writes_png_and_marker(
    cli_env: tuple[Path, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root, toy_id = cli_env

    exit_code = main(["--probe", toy_id, "--slot", "idle", "--use-stub", "--seed", "42"])

    assert exit_code == 0
    out_path = data_root / "images" / "toy_actions" / toy_id / "idle.png"
    assert out_path.is_file()
    # Valid PNG with alpha.
    img = Image.open(out_path)
    assert img.format == "PNG"
    assert img.mode == "RGBA"

    # Marker file written under the model dir.
    marker_dir = data_root / "models" / "image_gen"
    markers = list(marker_dir.glob(".probe-pass-*.json"))
    assert len(markers) == 1
    payload = json.loads(markers[0].read_text())
    assert payload["toy_id"] == toy_id
    assert payload["slot"] == "idle"
    assert payload["seed"] == 42
    assert payload["stub"] is True
    assert payload["peak_vram_gb"] is None
    assert isinstance(payload["wall_clock_secs"], (int, float))

    # Stdout envelope ok=True.
    captured = capsys.readouterr()
    stdout_payload = json.loads(captured.out.strip().splitlines()[-1])
    assert stdout_payload["ok"] is True
    assert stdout_payload["toy_id"] == toy_id


def test_cli_unknown_toy_returns_lookup_error(
    cli_env: tuple[Path, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, _real_toy_id = cli_env
    bogus_id = "00000000-0000-4000-8000-000000000000"

    exit_code = main(["--probe", bogus_id, "--slot", "idle", "--use-stub"])

    assert exit_code == 2
    captured = capsys.readouterr()
    err_payload = json.loads(captured.err.strip().splitlines()[-1])
    assert err_payload["ok"] is False
    assert err_payload["error"] == "lookup"


def test_cli_rejects_invalid_slot(cli_env: tuple[Path, str]) -> None:
    _, toy_id = cli_env
    # argparse choices= rejects with SystemExit code 2 from the parser.
    with pytest.raises(SystemExit):
        main(["--probe", toy_id, "--slot", "not-a-slot", "--use-stub"])


def test_cli_rejects_path_traversal_toy_id(
    cli_env: tuple[Path, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-UUIDv4 ``--probe`` must be rejected before any path is built.

    Mitigates a path-traversal attack via a value like ``../../etc``;
    argparse alone accepts any string for ``--probe``, so the CLI
    re-validates against the canonical UUIDv4 regex up front.
    """
    _, _real_toy_id = cli_env
    bogus_id = "../../etc"

    exit_code = main(["--probe", bogus_id, "--slot", "idle", "--use-stub"])

    assert exit_code == 2
    captured = capsys.readouterr()
    err_payload = json.loads(captured.err.strip().splitlines()[-1])
    assert err_payload["ok"] is False
    assert err_payload["error"] == "invalid_argument"
    # The traversal output dir MUST NOT have been created.
    data_root, _ = cli_env
    assert not (data_root / "images" / "toy_actions" / bogus_id).exists()
