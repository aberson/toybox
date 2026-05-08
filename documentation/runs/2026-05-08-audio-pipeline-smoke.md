# 2026-05-08 — production audio pipeline smoke

Verify Fix C wired the live `MicCapture` + `WhisperTranscriber` + `TranscriptPipeline` into the non-smoke lifespan, and Fix B's mute toggle gates persistence end-to-end.

## Pre-flight (one-time)

1. Confirm parent PIN is set (Fix C bootstraps a `production-session` row but the bind guard still requires PIN-on-disk):
   ```powershell
   uv run python -m toybox.tools.session_check
   ```
   Expect: PIN set, DB reachable. If PIN not set, run the parent UI's first-boot setup before continuing.

2. Pre-fetch the Whisper model so the first utterance doesn't stall on a ~500 MB download:
   ```powershell
   uv run python -m toybox.audio.stt --download
   ```
   Expect: `whisper model downloaded` (or "already present"). One-time cost.

3. Confirm the frontend dev server is up on `:4000` (it is — port check confirmed). If not:
   ```powershell
   cd frontend; npm run dev
   ```

## Test 1 — production lifespan starts the audio pipeline

In a fresh terminal, start the backend:

```powershell
uv run python -m toybox.main --host 127.0.0.1 --port 8000
```

**Watch the logs for ONE of these three outcomes:**

| Log line | Meaning | Action |
|---|---|---|
| `production audio pipeline started (session_id=production-session, mic_id=production-mic)` | ✅ Fix C works. Continue to Test 2. | proceed |
| `mic capture failed to start; transcripts will not flow (...)` | Graceful degrade tripped — PortAudio couldn't open a device | report the exception class + message |
| `whisper transcriber failed to init; tearing down mic (...)` | Graceful degrade tripped — Whisper couldn't load (CUDA mismatch, missing cublas, etc.) | report the exception class + message |
| Process hangs > 60s with no log | Whisper model load is silently blocking | Ctrl+C, report |

If you see a degrade message, the API is still up — that's the design — but transcripts will not flow. Report the exception so I can debug.

## Test 2 — Operator tab shows live audio status

With backend + frontend both running, open `http://localhost:4000/parent` and navigate to the Operator tab.

Verify the **Audio** card:
- `mic device`: should show your real device name (e.g., `Microphone (USB Audio)` or whatever Windows calls it). NOT `—`.
- `mic enabled`: `yes`
- `buffer overruns`: `0`

Verify the **Mic** card (new from Fix B) is present, showing a green `● listening` button.

## Test 3 — talk produces a transcript row

1. Talk near the mic for 5-10 seconds. Say something distinctive like *"this is a smoke test five eight twenty twenty six"*.
2. Wait ~3-5 seconds for VAD to close + Whisper to transcribe.
3. Switch to the **Transcripts tab**. A row should appear with your text. Confidence should be > 0.5 for clear speech.

If no row appears within ~15 seconds:
- Re-check Operator tab → Audio card. Did `buffer overruns` increment? (suggests Whisper is blocking the consumer)
- Check the backend log for `transcribe failed; skipping chunk` warnings.

## Test 4 — mute gate skips persistence

1. On the Operator tab, click the **● listening** button. It should flip to red `○ muted`.
2. Talk again for 5-10 seconds. Say something different like *"this should be silently dropped"*.
3. Wait ~5 seconds. Switch to the Transcripts tab — **no new row** should appear.
4. Click `○ muted` to flip back to `● listening`.
5. Talk again — *"unmuted again"*. A new row SHOULD appear within ~5 seconds.

This proves the pipeline mute gate at [audio/pipeline.py:_handle_chunk](src/toybox/audio/pipeline.py) consults `settings.mic_enabled` per-utterance.

## Test 5 — clean shutdown

In the backend terminal, hit `Ctrl+C`. Watch for:
- `transcript pipeline stopped (session_id=production-session)`
- No tracebacks. (A `KeyboardInterrupt` from uvicorn is fine.)

## Reporting back

Tell me:
- Which of Test 1's four outcomes you hit (with the exception text if a degrade)
- Whether Tests 2-5 passed or failed, and at which step
- Any unexpected log lines

That's enough to either commit the bundle or open an issue.
