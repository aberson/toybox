# F.5-5 Soak — toy action sprites, cartoon redo

**Date:** 2026-05-09
**Host:** Windows 11, RTX 4070 8 GB
**Issue:** [#68](https://github.com/aberson/toybox/issues/68)
**Closes (on soft-pass):** [#61](https://github.com/aberson/toybox/issues/61) — original c10.dll access violation
**F.5-4 dependency:** PASS — see [run-doc](2026-05-09-toy-action-sprites-cartoon-smoke.md)

## Pre-flight

- Master at `2cc09fb` (6 ahead of origin/master); F.5-4 docs + /fix-bug detour + image-gen-mode toggle all merged
- mypy strict src: clean, 89 source files
- ruff: clean
- pytest: 1200 passed (1179 baseline + 21 net new across the toy_ids fix and the toggle feature)
- frontend tsc + vitest: clean, 256 vitest passing
- `image_gen_mode` setting: `cartoon` (the path under test for #61's crash class)
- LocalDumps registry key installed for python.exe → `C:\Users\abero\dev\toybox\logs\crashdumps`, DumpType=2 (full), DumpCount=10
- VRAM logger running under agent task `b6yy6223z` → `logs/f55-vram.csv` (1-sec interval, same shape as F.5-4)
- F.5-4 confirmed: capability gate green; cartoon mode produces 10/10 sprites per toy at ~15 KB avg; peak VRAM 4599 MiB on the same hardware

## Step 1 — LocalDumps install (admin PowerShell)

**DONE.** Operator confirmed the registry key landed:

```
DumpFolder   : C:\Users\abero\dev\toybox\logs\crashdumps
DumpType     : 2
DumpCount    : 10
```

Insurance against the c10.dll-class native crash: if it fires, WER writes a minidump to `logs/crashdumps/`, providing the forensic data needed for follow-up investigation without requiring a re-run.

## Step 2 — DB reset (round 1)

**DONE.** Operator stopped backend, ran the reset script:

```
toys archived: (18,)
toy_actions remaining: (0,)
orphan dirs after cleanup: 0
```

Backend restarted at PID 44336; boot log: `image-gen capability=True reason=capable`.

## Step 3 — Cartoon-vs-composite mode mismatch caught

**The DB reset did NOT touch the `settings` table** (only `toys` + `toy_actions`). The operator's earlier toggle visual-verify left `image_gen_mode = composite` in the settings. So the first ingest round inadvertently exercised the Tier C composite path (Pillow + cutout, no GPU) — not the SD-1.5 cartoon pipeline that motivated F.5 + #61.

Caught via agent-side query at t+15 sec (14 done already, all sprites ~5 KB — too small for SD-1.5 cartoon output, too fast for GPU inference). Surfaced to operator per `feedback_uat_pushback_on_state_mismatch.md`.

## Step 4a — Composite-mode incidental soak (kept as bonus data)

Driven by `logs/_f55_ingest.py` (httpx + parent token + 30 random synthetic 384×384 PNGs). Useful baseline for the toggle feature's "force composite on capable host" path under load.

| Metric | Value |
|---|---|
| Toys uploaded | 30/30 (plus 1 stray active toy that survived the reset) |
| `toy_actions` final state | 310/310 done, 0 failed, 0 stuck |
| Drain wall-clock | ~95 s |
| Throughput | ~3.3 jobs/sec |
| Native crashes | 0 |
| Crash dumps | none |

PASS for the composite path under load.

## Step 4b — Reset round 2 + cartoon-mode re-ingest

Logs: `logs/_f55_reset_for_cartoon.py` archives all toys + truncates toy_actions + flips `image_gen_mode='cartoon'` + clears orphan PNG dirs.

```
toys archived: 49      (18 + 31 from the composite incidental soak)
toy_actions remaining: 0
image_gen_mode: cartoon
orphan dirs after cleanup: 0
```

Re-ran `_f55_ingest.py` against the same backend (no restart needed — worker reads mode per-job per the toggle feature's design):

| Metric | Value |
|---|---|
| Cartoon-soak t=0 | 2026-05-09T22:36:22Z |
| Toys uploaded | 30/30 |
| Ingest wall-clock | 4.9 s |
| Per-upload mean | 0.16 s (range 0.11–0.72 s) |

300 sprite jobs queued at the worker.

## Step 5 — Cartoon-mode unattended soak

**At t+45 s:** 15 done, 1 running, 284 queued, 0 failed, no crash dumps. Per-sprite cadence ~2 s — matches F.5-2 baseline.

**Audit scheduled** at t+10 min via background task `bsav6vbou` (`sleep 600 && uv run python logs/_f55_audit.py`). Output will land at `logs/_f55_final_audit.txt`.

## Step 6 — Audit

Background task `bsav6vbou` fired at t+10 min. Full output saved to `logs/_f55_final_audit.txt`. Re-parsed VRAM CSV as UTF-16 (PowerShell default redirect encoding) — see `logs/_f55_vram_fix.py`.

**Results:**

| Metric | Threshold | Observed | Status |
|---|---|---|---|
| Active toys | 30 | 30 | ✓ |
| `toy_actions` total | 300 | 300 | ✓ |
| `done` rows | — | 300 (100.0%) | ✓ |
| `failed` rows | — | 0 | ✓ |
| `running` rows post-drain | HARD: 0 | 0 | ✓ |
| `queued` rows post-drain | HARD: 0 | 0 | ✓ |
| Native crashes (`logs/crashdumps/`) | HARD: 0 | empty | ✓ |
| Cartoon-window VRAM peak | SOFT: <6144 MiB | 4645 MiB | ✓ |
| Cartoon-window VRAM median (steady-state with model loaded) | — | 4451 MiB | informational |
| PNGs on disk vs `done` rows | exact match | 300 == 300 | ✓ |
| Backend restarts during soak | 0 | 0 (PID 44336 stayed up) | ✓ |
| Breaker false-trips | 0 | 0 (no fail rows means no breaker activity) | ✓ |

**Drain wall-clock:** ingest finished at 2026-05-09T22:36:27Z (UTC); 300th sprite landed within the 10-min audit window. Average ~2 s/sprite matches F.5-2 baseline.

## Step 7 — Recovery on failed slots

**Skipped — 0 failures.** No regenerate-all needed.

## Outcome

**SOFT-PASS.** All HARD criteria met (0 crashes, 0 stuck rows). All SOFT criteria comfortably met (100% success, peak VRAM well under floor). The c10.dll access-violation class that triggered F.5 (see [#61](https://github.com/aberson/toybox/issues/61)) is **verified fixed** by the new pipeline — SD 1.5 + LCM-LoRA + cartoon checkpoint at 512² 4-step survives 300 sustained generations on RTX 4070 8 GB with peak VRAM ~4.6 GB.

**This run-doc closes [#61](https://github.com/aberson/toybox/issues/61).** Phase F.5 is DONE.

**Post-soak optional cleanup** (operator may run when convenient — the rollback option goes away after this):

```powershell
Remove-Item -Recurse -Force data/models/image_gen/sdxl, data/models/image_gen/ip_adapter, data/models/image_gen/pixel_art_lora
```

Reclaims ~9 GB. Soft-pass means the pre-F.5 SDXL/IPA path is no longer needed as a rollback option.

## Anomalies / follow-ups

- **DB-reset script (issue #68 step 2) doesn't reset `settings`.** This is a real procedure gap — the operator's mode preference from the previous session can leak into the soak's mode and silently change which dispatch path is exercised. Fix: extend the reset snippet in #68 to also normalize `image_gen_mode` to a known value (probably `cartoon`, since that's the soak's purpose). Or: have F.5-5 explicitly assert mode before ingesting and refuse to start if mismatched.
- **VRAM CSV is UTF-16 LE** (PowerShell default for redirected output). The audit script's default UTF-8 decode silently produced an empty parsed-values list. Filed for follow-up: either fix the logger to write UTF-8 (`Out-File -Encoding utf8`) or make the audit script explicitly UTF-16-aware. The fix-up parser at `logs/_f55_vram_fix.py` worked.
- **One stray toy survived the round-1 reset:** the round-1 archive count was 18 vs 49 after round 2, where 31 new toys came from the composite ingest — accounting for an extra `+1` somewhere upstream (likely a toy uploaded during the toggle visual-verify whose archive happened on a different code path). Cosmetic; doesn't affect the soak verdict.
- **Composite-mode soak as bonus data:** the inadvertent first ingest exercised the Tier C composite path under the same load shape (310/310 done in ~95 s, 0 failed). Useful baseline for future toggle-feature regression checks; recorded in Step 4a above.

## Pre-existing follow-ups carried forward (from F.5-4 + the toy_ids fix)

These are not blocking F.5 closure — they were captured in [the F.5-4 run-doc](2026-05-09-toy-action-sprites-cartoon-smoke.md#anomalies--follow-ups). Listed here for ease of reference:

- Loop-mode (Claude tool-loop) generator — Activity output gets default `toy_ids=()`; kiosk still won't render sprites in loop mode.
- LOW: `_pick_toy_entry` sort tiebreak (add `(id)` as secondary key for duplicate-display-name toys).
- STYLE: `_persist_activity` `Sequence[str]` → `list[str]` (file-local convention).
- Cosmetic: trim per-test docstrings in the new `tests/unit/activities/` files.
- Pre-existing test flakes: `test_ws_heartbeat::test_server_pings_periodically`, `test_version_conflicts::test_concurrent_propose_at_cap_evicts_consistently`.
- `.env.example` has never existed in the repo despite plan references — open as a fresh artifact.
- Issue #67 doc gaps (1) `(/api/health).ai.claude_capable` field doesn't exist; (2) `model_index.json` rename procedure assumes `lora` mode — should rename `cartoon_checkpoint/model_index.json` instead in default `checkpoint` mode.
