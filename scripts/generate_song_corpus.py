"""One-shot operator script: render the bundled song corpus via Coqui TTS.

Phase K Step K11. This script ships in K11; the .mp3 files do NOT —
the operator runs this once locally to populate
``data/songs/audio/<id>.mp3`` from the lyrics declared in
``data/songs/manifest.json``. K11 deliberately keeps Coqui out of the
runtime dependency tree (no entry in ``pyproject.toml``); the kiosk
just plays whatever .mp3 files are already on disk.

Why Coqui TTS over Piper / Festival / system TTS:

* **Voice quality**: XTTS-v2 (the pinned model below) is the current
  best open-source neural voice for the kid-friendly register we want
  ("warm, slightly playful, clear enunciation"). Piper is faster but
  flatter; system TTS is wildly variable across operator OS / locale.
* **One-shot install footprint**: Coqui pip-installs into a separate
  venv; once rendering completes, the operator can uninstall — there
  is zero runtime impact on the toybox backend.
* **Operator workflow is acceptable**: a single ``pip install TTS``
  + ``python scripts/generate_song_corpus.py`` invocation produces
  all 50 .mp3s in roughly 5-15 minutes on a modern laptop CPU
  (GPU optional). v2 may switch to recorded human vocals; see
  phase-k-plan.md §10 v2 ideas.

Operator workflow
-----------------

In a fresh venv (so Coqui's deps don't collide with toybox's)::

    python -m venv .coqui-venv
    .coqui-venv\\Scripts\\activate   # PowerShell
    pip install TTS                  # pulls Coqui + torch + ffmpeg-python
    # ffmpeg binary must be on PATH (winget install Gyan.FFmpeg, or
    # whatever the operator's package manager uses).
    python scripts/generate_song_corpus.py

Re-run with ``--force`` to overwrite existing .mp3s (e.g. after a
manifest edit). Otherwise existing files are skipped.

Pinned model
------------

The model identifier is ``tts_models/multilingual/multi-dataset/xtts_v2``
(Coqui XTTS-v2). This is the model Coqui itself documents as the
current stable multilingual voice-cloning + zero-shot model as of
2026-Q2. Switching models is a one-line change to
:data:`_COQUI_MODEL_ID`; commit the new model id + a note in
``data/songs/_credits.md`` whenever the bundled audio is re-rendered
under a new model so the licensing audit trail stays clean.

Output spec
-----------

* Mono.
* Target bitrate ≤ 64 kbps (libmp3lame ``-b:a 64k``).
* Each track is named ``<entry.id>.mp3`` under ``--output-dir``.
* No post-processing beyond the WAV → MP3 transcode (the model's
  native pacing + intonation is the v1 ship target).

K11 itself does NOT execute the synthesis loop — the script imports
+ ``--help`` without ``TTS`` installed (the heavy imports are lazy,
gated behind argparse). The K11 test suite asserts ``--help`` returns
0 to keep the import path honest.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Coqui model pin — see module docstring for the rationale + license
# audit trail. When this changes, update data/songs/_credits.md.
_COQUI_MODEL_ID: str = "tts_models/multilingual/multi-dataset/xtts_v2"

# Default speaker for XTTS-v2 — Coqui ships several built-in speaker
# embeddings; we pin one explicitly so re-runs are reproducible. If
# the model rev removes this speaker, swap in the closest current
# voice and note it in _credits.md.
_COQUI_DEFAULT_SPEAKER: str = "Daisy Studious"
_COQUI_DEFAULT_LANGUAGE: str = "en"

# Default paths, relative to repo root (the operator runs the script
# from the project root, same as every other ``scripts/`` entrypoint).
_DEFAULT_MANIFEST = Path("data/songs/manifest.json")
_DEFAULT_OUTPUT_DIR = Path("data/songs/audio")

# Output target. 64 kbps mono MP3 = ~150 KB for a 20s track, which
# keeps the bundled corpus comfortably under the phase-k risk-6
# 50 MB ceiling.
_MP3_BITRATE: str = "64k"


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI. Pure-function so K11's --help smoke test works without Coqui."""
    parser = argparse.ArgumentParser(
        prog="generate_song_corpus",
        description=(
            "Render the bundled song corpus to .mp3 via Coqui TTS. "
            "Requires `pip install TTS` in a separate venv. See module "
            "docstring for the operator workflow."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_DEFAULT_MANIFEST,
        help=f"path to manifest.json (default: {_DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"directory to write <id>.mp3 files (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing .mp3 files (default: skip existing)",
    )
    parser.add_argument(
        "--model",
        default=_COQUI_MODEL_ID,
        help=f"Coqui model id (default pinned: {_COQUI_MODEL_ID})",
    )
    parser.add_argument(
        "--speaker",
        default=_COQUI_DEFAULT_SPEAKER,
        help=f"XTTS-v2 built-in speaker (default: {_COQUI_DEFAULT_SPEAKER!r})",
    )
    parser.add_argument(
        "--language",
        default=_COQUI_DEFAULT_LANGUAGE,
        help=f"language code (default: {_COQUI_DEFAULT_LANGUAGE!r})",
    )
    return parser


def _load_manifest(manifest_path: Path) -> list[dict[str, object]]:
    """Read + parse the manifest. Validation is light here — the production
    loader (:mod:`toybox.activities.song_corpus`) does the strict gate; this
    script only needs ``id`` and ``lyrics`` to render.
    """
    if not manifest_path.is_file():
        raise SystemExit(f"manifest not found: {manifest_path}")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"manifest {manifest_path} must be a JSON array")
    return raw  # type: ignore[no-any-return]


def _render_one(
    tts: object,  # actually a TTS.api.TTS instance; typed as object to avoid the import
    *,
    entry_id: str,
    lyrics: str,
    output_path: Path,
    speaker: str,
    language: str,
) -> int:
    """Render a single manifest entry → MP3. Returns bytes written.

    Coqui's TTS API writes a WAV by default; we use its
    ``tts_to_file`` with a temp WAV then post-process to MP3 via
    ``ffmpeg-python`` (pulled in as a Coqui dep). The two-step path
    is the most reliable cross-platform; Coqui's claimed native MP3
    output has been unreliable across releases.
    """
    # Lazy imports so --help and import-only tests don't require Coqui.
    import ffmpeg  # type: ignore[import-not-found]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_wav = output_path.with_suffix(".tmp.wav")

    # Coqui's typed surface is loose; call via getattr to keep mypy
    # quiet without taking a dependency on the TTS stubs.
    tts_to_file = tts.tts_to_file  # type: ignore[attr-defined]
    tts_to_file(
        text=lyrics,
        file_path=str(tmp_wav),
        speaker=speaker,
        language=language,
    )

    # Mono + 64 kbps. ffmpeg-python is a thin wrapper; the .run() call
    # spawns ffmpeg on PATH. The operator must have ffmpeg installed —
    # surfaced in the module docstring's prerequisite list.
    (
        ffmpeg.input(str(tmp_wav))
        .output(
            str(output_path),
            ac=1,  # mono
            **{"b:a": _MP3_BITRATE},
        )
        .overwrite_output()
        .run(quiet=True)
    )
    try:
        tmp_wav.unlink()
    except OSError:
        # Non-fatal — operator can clean up.
        print(f"  warn: could not remove temp wav {tmp_wav}", file=sys.stderr)

    return output_path.stat().st_size


def main(argv: list[str] | None = None) -> int:
    """Entrypoint. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    manifest = _load_manifest(args.manifest)
    print(f"loaded {len(manifest)} manifest entries from {args.manifest}")

    # Lazy: TTS pulls torch + downloads a multi-GB model on first run.
    # Doing this AFTER manifest validation means a malformed manifest
    # fails the operator's command quickly, before the slow download.
    try:
        from TTS.api import TTS  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            "Coqui TTS is not installed. Run `pip install TTS` in a "
            "separate venv (see scripts/generate_song_corpus.py "
            "module docstring for the full operator workflow)."
        ) from exc

    print(f"loading Coqui model {args.model!r} (first run downloads weights)...")
    tts = TTS(args.model)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    total_bytes = 0
    rendered = 0
    skipped = 0
    for idx, entry in enumerate(manifest, start=1):
        if not isinstance(entry, dict):
            print(f"  [{idx}/{len(manifest)}] skip: entry is not an object")
            continue
        entry_id = entry.get("id")
        lyrics = entry.get("lyrics")
        if not isinstance(entry_id, str) or not entry_id:
            print(f"  [{idx}/{len(manifest)}] skip: missing id")
            continue
        if not isinstance(lyrics, str) or not lyrics:
            print(f"  [{idx}/{len(manifest)}] skip: {entry_id!r} missing lyrics")
            continue

        output_path = args.output_dir / f"{entry_id}.mp3"
        if output_path.exists() and not args.force:
            print(f"  [{idx}/{len(manifest)}] skip: {entry_id!r} exists ({output_path})")
            skipped += 1
            continue

        print(f"  [{idx}/{len(manifest)}] render: {entry_id!r} -> {output_path}")
        try:
            written = _render_one(
                tts,
                entry_id=entry_id,
                lyrics=lyrics,
                output_path=output_path,
                speaker=args.speaker,
                language=args.language,
            )
        except Exception as exc:  # pragma: no cover — operator surface
            print(f"    error: {exc}", file=sys.stderr)
            continue
        total_bytes += written
        rendered += 1

    elapsed = time.monotonic() - started
    print(
        f"\ndone: rendered={rendered} skipped={skipped} "
        f"total_bytes={total_bytes} elapsed={elapsed:.1f}s"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — operator entrypoint
    raise SystemExit(main())
