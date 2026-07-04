"""Offline audition batch for the Phase Z persona voice casting (Step Z7-prep).

Renders ONE fixed sample line (:data:`AUDITION_LINE`) per candidate Kokoro
English voice PLUS one sample per current persona casting into
``<data_root>/tts/audition/`` (``<data_root>`` = ``TOYBOX_DATA_DIR``, default
``data/`` — the same resolution the :mod:`toybox.tts` package uses), then
prints a listen-order manifest. The operator listens (Step Z7) and either
accepts the default casting or edits ``voice_profile.neural_voice`` in
``src/toybox/personas/library/*.json`` and re-runs
``python -m toybox.db.migrate``.

Usage:
    uv run python scripts/batch_tts_audition.py [--dry-run] [--force]
        [--out-dir PATH]

Flags:
    --dry-run     Print every target (voice, output path, skip/would-render
                  status) and exit 0. No synthesis, no filesystem writes, no
                  heavy imports.
    --force       Re-render samples that already exist (default: skip them).
    --out-dir     Override the output dir (default: <data_root>/tts/audition).

Naming scheme (the documented contract):
    * voice-sweep samples:  ``<voice>.wav``                (``af_heart.wav``)
    * persona samples:      ``persona_<persona_id>_<voice>.wav``
                            (``persona_princess_af_bella.wav``)

Dedup decision: persona samples ALWAYS get their own persona-prefixed file,
even when the cast voice also appears in the voice sweep (today all four do).
The duplicate render costs seconds + ~250 KB and buys a self-contained
"listen to the princess" file the operator can play without cross-referencing
the sweep, so the total sample count is always
``len(KOKORO_EN_VOICES) + <number of library personas>``.

Exit code: 0 on success INCLUDING partial failures — a failing voice logs an
ERROR with traceback and the batch continues (per-item isolation,
batch_scenes.py shape);
1 only when EVERY attempted render failed (nothing new to listen to).
Deliberate deviation from batch_scenes.py's any-fail -> 1: an audition with
27 of 28 usable samples is still a successful audition. ``--dry-run`` always
exits 0.

Notes:
    * Persona castings are read at RUNTIME from the library JSONs
      (``voice_profile.neural_voice``) — never hardcoded here. Personas
      without a casting (or with an unsafe voice id) fall back to
      :data:`toybox.tts.engine.DEFAULT_NEURAL_VOICE`.
    * ``--help`` / ``--dry-run`` work WITHOUT the ``tts`` extra installed
      (lazy-import discipline; the toybox.tts modules are import-cheap by
      contract). A real render needs ``uv sync --extra tts`` +
      ``python -m toybox.tts --download`` first.
    * ``TOYBOX_TTS_STUB=1`` renders tiny valid stub WAVs — how the wiring
      tests run, and a way to smoke the script without model files.
    * CPU-only synthesis (engine contract) — safe to run next to the server,
      but each real sample takes a few seconds (RTF ~1.1).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger(__name__)

# All English voices shipped in the kokoro-onnx ``model-files-v1.0``
# voices bin (voices-v1.0.bin — the file ``python -m toybox.tts
# --download`` fetches), i.e. the hexgrad/Kokoro-82M v1.0 release:
# af_*/am_* = American English female/male, bf_*/bm_* = British English
# female/male. Source list: hexgrad/Kokoro-82M VOICES.md (mirrored by
# github.com/thewh1teagle/kokoro-onnx). Deliberately a plain list the
# operator can read/edit if a model rev adds or drops voices.
KOKORO_EN_VOICES: list[str] = [
    # American English — female
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_heart",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    # American English — male
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "am_puck",
    "am_santa",
    # British English — female
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    # British English — male
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
]

# ONE fixed sample line for every target. Kid-appropriate and prosody-rich
# on purpose: a greeting, a question, and exclamations around a fun
# activity, so rate/intonation differences between voices are audible.
AUDITION_LINE: str = (
    "Wow, what a great day for playing! Should we build a giant blanket "
    "fort, or race the toy cars around the rug? Ready, set, go!"
)

# Subdir of the tts clip tree (<data_root>/tts) holding audition samples.
AUDITION_SUBDIR: str = "audition"


@dataclass(frozen=True)
class _Target:
    """One WAV to render: a persona casting or a plain sweep voice."""

    label: str  # human-readable manifest label
    voice: str  # Kokoro voice id fed to synthesize()
    filename: str  # output filename under the audition dir


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="batch_tts_audition.py",
        description=(
            "Render one fixed sample line per Kokoro English voice plus one "
            "per current persona casting into <data_root>/tts/audition/ for "
            "the operator to listen to (Phase Z Step Z7)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print every target with its skip/would-render status and exit 0.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-render samples that already exist instead of skipping them.",
    )
    parser.add_argument(
        "--out-dir",
        metavar="PATH",
        default=None,
        help=(
            "Output directory (default: <data_root>/tts/audition, where "
            "<data_root> is TOYBOX_DATA_DIR or ./data)."
        ),
    )
    return parser.parse_args(argv)


def _persona_targets() -> list[_Target]:
    """Read the current per-persona castings from the library JSONs.

    Lazy imports keep ``--help`` importable without deps. A malformed
    persona JSON is warned and skipped (loader parity — one bad file
    must not kill the audition); a missing or unsafe
    ``voice_profile.neural_voice`` falls back to the package default
    voice so every persona still gets a sample.
    """
    from toybox.personas.loader import LIBRARY_DIR
    from toybox.tts.cache import is_safe_voice_id
    from toybox.tts.engine import DEFAULT_NEURAL_VOICE

    targets: list[_Target] = []
    for path in sorted(LIBRARY_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue  # _schema.json and friends
        try:
            persona = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _logger.warning("persona JSON unreadable, skipping: %s (%s)", path.name, exc)
            continue
        persona_id = persona.get("id")
        if not isinstance(persona_id, str) or not is_safe_voice_id(persona_id):
            # The id becomes a filename segment — reuse the voice-id rule
            # as the path-safety gate (defense-in-depth, cache.py parity).
            _logger.warning("persona JSON has no safe 'id', skipping: %s", path.name)
            continue
        profile = persona.get("voice_profile")
        voice = profile.get("neural_voice") if isinstance(profile, dict) else None
        if not isinstance(voice, str) or not voice:
            _logger.info(
                "persona %s has no voice_profile.neural_voice; auditioning default %s",
                persona_id,
                DEFAULT_NEURAL_VOICE,
            )
            voice = DEFAULT_NEURAL_VOICE
        elif not is_safe_voice_id(voice):
            _logger.warning(
                "persona %s neural_voice %r is not a safe voice id; auditioning default %s",
                persona_id,
                voice,
                DEFAULT_NEURAL_VOICE,
            )
            voice = DEFAULT_NEURAL_VOICE
        targets.append(
            _Target(
                label=f"persona {persona_id} -> {voice}",
                voice=voice,
                filename=f"persona_{persona_id}_{voice}.wav",
            )
        )
    return targets


def _voice_targets() -> list[_Target]:
    return [
        _Target(label=f"voice {voice}", voice=voice, filename=f"{voice}.wav")
        for voice in KOKORO_EN_VOICES
    ]


def _print_manifest(
    persona_targets: list[_Target],
    voice_targets: list[_Target],
    statuses: dict[str, str],
    out_dir: Path,
) -> None:
    """Listen-order manifest: persona castings first, then the full sweep."""
    print()
    print("== Audition listen order (persona castings first, then the voice sweep) ==")
    print("-- Persona castings --")
    for target in persona_targets:
        print(f"  {target.label}: {out_dir / target.filename}  [{statuses[target.filename]}]")
    print("-- Voice sweep (af_*/am_* American, bf_*/bm_* British) --")
    for target in voice_targets:
        print(f"  {target.label}: {out_dir / target.filename}  [{statuses[target.filename]}]")


def run(argv: list[str] | None = None) -> int:
    """Render the audition set. Returns a process exit code (0 = success).

    The toybox imports live here (batch_scenes.py idiom) and the tts
    package is import-cheap by contract, so ``--help``/``--dry-run``
    work without the ``tts`` extra installed and never synthesize.
    """
    args = _parse_args(argv)

    from toybox.tts import engine
    from toybox.tts.cache import clips_root

    out_dir = Path(args.out_dir) if args.out_dir is not None else clips_root() / AUDITION_SUBDIR

    persona_targets = _persona_targets()
    voice_targets = _voice_targets()
    # Listen order == render order: persona castings first so the operator
    # can stop early once the castings sound right.
    targets = persona_targets + voice_targets

    rendered = 0
    skipped = 0
    failed = 0
    statuses: dict[str, str] = {}

    for target in targets:
        out_path = out_dir / target.filename
        if out_path.exists() and not args.force:
            _logger.info("skip %s (exists; use --force to re-render)", out_path)
            statuses[target.filename] = "skipped (exists)"
            skipped += 1
            continue
        if args.dry_run:
            _logger.info("[dry-run] would render %s -> %s", target.voice, out_path)
            statuses[target.filename] = "would render"
            continue
        try:
            wav_bytes = engine.synthesize(AUDITION_LINE, target.voice)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(wav_bytes)
            _logger.info("rendered %s -> %s (%d bytes)", target.voice, out_path, len(wav_bytes))
            statuses[target.filename] = "rendered"
            rendered += 1
        except Exception:  # noqa: BLE001 — one bad voice must not kill the audition
            _logger.exception("failed to render %s (%s)", target.voice, target.filename)
            statuses[target.filename] = "FAILED"
            failed += 1

    _print_manifest(persona_targets, voice_targets, statuses, out_dir)

    if args.dry_run:
        would = sum(1 for status in statuses.values() if status == "would render")
        print(f"dry-run: {would} sample(s) would render, {skipped} already present")
        return 0

    print(
        f"done: {rendered} rendered, {skipped} skipped, {failed} failed (of {len(targets)} samples)"
    )
    attempted = rendered + failed
    if attempted > 0 and failed == attempted:
        _logger.error("every attempted render failed — nothing new to audition")
        return 1
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
