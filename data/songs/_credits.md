# Song corpus — credits and licensing

The bundled song corpus is split into two layers:

1. **Lyrics** (operator-authored prose committed in
   [`manifest.json`](manifest.json)). These short kid-friendly rhymes
   are released under **CC-BY-4.0** by the toybox project authors.
   Attribution: "toybox song corpus, Phase K (2026)".

2. **Audio renderings** (`audio/*.mp3`, not committed in K11). The
   `.mp3` files are produced by the operator running
   [`scripts/generate_song_corpus.py`](../../scripts/generate_song_corpus.py),
   which drives **Coqui TTS** with the pinned model
   `tts_models/multilingual/multi-dataset/xtts_v2`. Coqui XTTS-v2 is
   released by Coqui under the Coqui Public Model License (CPML);
   audio rendered by this model is subject to the model license at
   render time. See https://coqui.ai/cpml for the current text.

When the operator re-renders the corpus under a different Coqui
model revision (or substitutes recorded human vocals in v2 per
phase-k-plan.md §10), update this file with the new model id and
licensing terms BEFORE committing the regenerated `audio/` tree —
the licensing audit trail lives here.

## Per-track credit line

Every manifest entry carries a per-track `credit` field. K11's
default value is `"Coqui TTS XTTS-v2 (operator-rendered)"`. When
swapping to recorded vocals or a different TTS, update both the
manifest's `credit` field AND this file.

## Why no audio in K11

Phase K Step K11 ships the manifest + loader + render script +
this credit document. The `.mp3` files are an explicit one-shot
operator action — the Coqui dep is heavyweight (~2 GB of weights
on first download) and is NOT pulled into the runtime venv. The
toybox kiosk plays whatever audio files already exist on disk;
missing files surface as kiosk-side 404s (K12 handles gracefully).
