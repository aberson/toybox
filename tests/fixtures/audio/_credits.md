# Audio fixture credits

## `lets_play_unicorns.wav`

- **Source**: rendered offline via `pyttsx3` (Windows SAPI5 / Microsoft TTS).
- **Generator script**: `scripts/gen_smoke_wav.py`.
- **Phrase**: `"Let's play unicorns."` (must match the
  `lets_play_X` trigger regex in
  `src/toybox/triggers/defaults.json`).
- **Format**: 16 kHz mono int16 PCM WAV.
- **Duration**: ~2.2 s (varies slightly with the SAPI5 voice picked up
  on the rendering host; re-running the script overwrites the file).
- **License**: synthesized speech via Microsoft Speech API. The audio
  output is not subject to per-render licensing under Microsoft's
  documented terms. Re-render via the script if you need to refresh it.

The fixture is used by `tests/e2e/test_smoke_pipeline.py` (the slow E2E
smoke). It must transcribe via `faster-whisper-small` above the
`DEFAULT_CONFIDENCE_FLOOR` (0.55) so the trigger registry fires the
`request_play` intent with `slot=unicorns`. If a future
`faster-whisper` upgrade drops confidence below the floor, re-render
via `uv run --with pyttsx3 python scripts/gen_smoke_wav.py` (and slow
the `--rate` if needed); do **not** lower the confidence floor.

`pyttsx3` is intentionally **not** a tracked project dependency: its
cross-platform extras (notably the macOS `pyobjc` framework collection)
balloon `uv.lock` for what is a one-shot helper. The script
self-bootstraps it via `uv run --with pyttsx3 …`.
