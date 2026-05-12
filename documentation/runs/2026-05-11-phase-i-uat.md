# Phase I — operator iPad UAT run

- **Date:** 2026-05-11
- **Phase:** I (transcript retention + display refresh)
- **Master at start of UAT:** `3d7c383` (I1+I2+I3+I4 all merged)
- **Operator:** abero
- **Device:** iPad on LAN, Safari → `http://<TOYBOX_LAN_IP>:4000/parent`
- **Verdict:** ✅ PASS

## Pre-flight

| Check | Status |
|---|---|
| `data/toybox.db.pre-i2.bak` exists | ✅ confirmed |
| Backend started (`uv run python -m toybox.main --host 0.0.0.0 --port 8000`) | ✅ |
| Frontend started (`cd frontend; npm run dev -- --host 0.0.0.0`) | ✅ |
| iPad → PIN-unlocked parent app | ✅ |

## Walkthrough results

| # | Action | Result |
|---|---|---|
| 1 | Settings → Settings shows "Transcript retention" section with 5 buttons; 1m highlighted | ✅ PASS |
| 2 | At 1m retention, spoke "hello toybox"; transcript prepended live via WS push | ✅ PASS |
| 3 | Waited ~60–70s; transcript faded out (opacity drop + height collapse over ~600ms) and disappeared | ✅ PASS |
| 4 | Switched to 15m; spoke another utterance; waited 90s; transcript persisted | ✅ PASS |
| 5 | Switched to 3m; older rows faded out together; fresh row from step 4 remained | ✅ PASS |
| 6 | No per-row delete button present on any row | ✅ PASS |
| 7 | Wipe-all + PIN confirm → all rows cleared immediately | ✅ PASS |
| 8 | Hard-refresh → last-selected retention preset still highlighted; new utterance renders fresh | ✅ PASS |
| 9 | Sweep activity in backend logs | ✅ PASS (verified indirectly — see below) |

## Step 9 evidence — sweep verified live

Operator wasn't able to inspect the backend terminal scrollback (logging goes to stdout, no file capture configured). Instead the orchestrator verified the sweep loop is running live via a direct DB probe against the running backend:

**Probe procedure** (run against the live `data/toybox.db` while backend was still up, retention=180s as the operator left it):
1. Inserted two rows with pipeline-format ISO timestamps:
   - `i5-probe-expired-<id>` aged 240s (past the 180s retention)
   - `i5-probe-fresh-<id>` aged 5s (within retention)
2. Slept 15s (one full sweep cadence of 10s + buffer)
3. Queried both rows

**Result:**
- Expired row was DELETED from the table within the 15s window → sweep loop is firing and DELETE executed
- Fresh row was PRESERVED → sweep correctly skips rows within retention
- No ERROR-level evidence (the row would survive if the sweep had errored on the tick)

**Cleanup:** the surviving probe row was deleted; transcripts table is empty.

**Inferred from probe outcome:**
- `transcript_sweep_lifespan` is composed into the running production lifespan
- `run_transcript_sweep_loop` is firing at the 10s cadence
- `sweep_expired_transcripts` is using a cutoff that matches pipeline ISO format byte-for-byte (the lexicographic `ended_at < cutoff` comparison would not have matched the synthetic 240s-old row otherwise)
- `idx_transcripts_ended_at` is present (confirmed via `PRAGMA index_list('transcripts')`)
- The retention setting (180s = 3m) persisted across hard-refresh (step 8) and was the active value when the probe ran

## Observations

- Fade animation timing felt natural at 600ms — no flicker, no abrupt cutoff
- Switching retention mid-session (1m → 15m → 3m) worked smoothly; step 5's "wave of fades" when shrinking retention was visible but acceptable per plan R4
- No per-row delete button anywhere; only the top-of-panel wipe-all remains
- WS push still prepends new transcripts live (Phase H behavior unaffected)
- Retention setting persists across hard-refresh

## Defects

None observed.

## Follow-ups (non-blocking, not for Phase I)

- Backend log file capture would have made step 9 directly observable instead of requiring a DB probe. Consider adding a `--log-file` flag or rotating-file handler in a future hardening pass.
- The style reviewers on I3 noted `aria-pressed` is now used on `TranscriptRetentionControl` but not on the sister `ListeningModeControl` / `ImageGenModeToggle`. A backport for accessibility consistency is a small follow-up.

## Conclusion

Phase I ships. All 9 UAT steps PASS. The retention setting, the backend sweep, the filter-on-read, and the frontend fade animation all work end-to-end against the live system on iPad Safari.
