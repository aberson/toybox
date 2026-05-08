# Manual M2.5 — v1 bundled UI smoke (release gate before Phase E)

> **Scope:** the v1 release gate. A fresh-pair-of-hands operator UAT covering Phase C steps 16/17/18 and Phase D steps 21/22/23/24. Read this only when running the gate or auditing what got verified for v1. Step descriptions live in [phase-c.md](phase-c.md) + [phase-d.md](phase-d.md).

**Why bundled:** per the autonomous-build operating mode (see [`feedback_autonomous_build_bundled_ui.md`](../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md)) steps 16, 17, 18, 21, 22, 23, 24 ran with `--reviewers code` (no runtime `--ui` reviewer). Their `Status:` notes each defer "visual UI verification pending bundled test pass". M2.5 is that pass — the v1 release gate before Phase E begins.

**Scope (v1):** steps 16, 17, 18, 21, 22, 23, 24. **Out of scope:** step 29 (E5) — not built yet; will be covered by a future bundled pass when Phase E lands.

**Operator profile:** fresh pair of hands. Assume nothing.

**Test fixtures (place in `tests/fixtures/uat/m2-5/`):**
- `toy-1.png` — any 800×600 PNG of a toy
- `toy-1-dup.png` — exact byte-copy of `toy-1.png` (dedup check)
- `room-1.jpg` … `room-5.jpg` — 5 distinct room photos
- `room-bulk-51/` — folder with 51 photos (bulk-cap negative path)

## Global setup (agent runs once at start)

```powershell
# 1. Stop any running backend / frontend.
#    Note: PowerShell 5.1's Get-Process does NOT expose CommandLine, so
#    the previous `Get-Process | Where-Object CommandLine -match` form
#    silently no-op'd. Use Get-CimInstance Win32_Process to read
#    CommandLine, then stop by PID.
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe' OR Name='uv.exe'" |
  Where-Object { $_.CommandLine -match "toybox|vite" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2  # let SQLite WAL/SHM locks release before the Move-Item below

# 2. Back up current DB to a UAT-managed location (deletable later).
#    If Move-Item fails with "file in use", a backend from step 1 is
#    still holding the WAL — re-run step 1 and confirm the procs are
#    gone before retrying.
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$bakDir = "data/.uat-backups"
if (-not (Test-Path $bakDir)) { New-Item -ItemType Directory -Path $bakDir | Out-Null }
if (Test-Path data/toybox.db) {
  Move-Item data/toybox.db "$bakDir/toybox.db.bak-$ts"
  Write-Host "Backed up DB to $bakDir/toybox.db.bak-$ts"
}

# 3. Recreate schema on a clean DB
uv run python -m toybox.db.migrate

# 4. Confirm clean state
uv run python -c "import sqlite3; c = sqlite3.connect('data/toybox.db'); print('toys:', c.execute('SELECT count(*) FROM toys').fetchone()[0]); print('rooms:', c.execute('SELECT count(*) FROM rooms').fetchone()[0]); print('children:', c.execute('SELECT count(*) FROM children').fetchone()[0]); print('parent_pin_hash rows:', c.execute(\"SELECT count(*) FROM settings WHERE key='parent_pin_hash'\").fetchone()[0])"
# Expected: toys: 0, rooms: 0, children: 0, parent_pin_hash rows: 0

# 5. Bridge fresh OAuth from Claude CLI creds (tokens rotate ~daily, so
#    this is needed at the start of most M2.5 runs). The bridge writes
#    `~/.toybox/secrets.json` from `~/.claude/.credentials.json` using
#    the same on-disk shape `toybox.ai.oauth.save_token` produces.
uv run python scripts/uat/bridge_claude_creds.py
# Expected: "bridged → ...\\secrets.json (delta_h=N)" with delta_h > 0.

# 6. Confirm Claude vision capability (steps M2.5.3, M2.5.4 need this)
uv run python -m toybox.ai --check
# Expected: claude_capable=True. If False, the printed capability_reason
# (token_missing, token_expired, network_unreachable, breaker_open, ...)
# tells you what to fix; vision is silently skipped while the gate is
# closed and the suggested-fields path won't be exercised.

# 7. Generate UAT image fixtures (idempotent — skips existing files)
uv run python scripts/uat/generate_m2_5_fixtures.py

# 8. Start backend (background) with stdout/stderr captured to logs/
New-Item -ItemType Directory -Path logs -Force | Out-Null
Start-Process -FilePath "uv" -ArgumentList "run","python","-m","toybox.main","--host","127.0.0.1","--port","8000" -WindowStyle Hidden -RedirectStandardOutput logs/backend.log -RedirectStandardError logs/backend.err
$ok = $false; for ($i = 0; $i -lt 30; $i++) { try { Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 1 | Out-Null; $ok = $true; break } catch { Start-Sleep -Seconds 1 } }
if (-not $ok) { throw "backend did not start (check logs/backend.err)" }

# 9. Start frontend (background). Vite binds to `localhost` which on
#    Windows resolves to `[::1]` (IPv6); the health check MUST use
#    `localhost` (or `[::1]`), not the IPv4 literal `127.0.0.1`, or it
#    will time out forever even though vite is serving.
Push-Location frontend; Start-Process -FilePath "npm" -ArgumentList "run","dev" -WindowStyle Hidden; Pop-Location
$ok = $false; for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest http://localhost:4000 -TimeoutSec 1 -UseBasicParsing | Out-Null; $ok = $true; break } catch { Start-Sleep -Seconds 1 } }
if (-not $ok) { throw "frontend did not start" }
```

> **Note on for-loop pattern:** Earlier drafts used `1..30 | ForEach-Object { ...; return }` which has a subtle bug — `return` inside `ForEach-Object` exits only the current iteration's scriptblock, not the outer pipeline, so all 30 iterations always run even after the health check passes. The `for / break` form above breaks correctly on first success.

## Step M2.5.1 — PIN first-run + lockout + countdown (covers step 21)

**Setup (agent):** global setup left `parent_pin_hash` empty so first-run will trigger.

**Action (human):**
1. Open `http://localhost:4000/parent` in a fresh incognito tab. (Use `localhost`, not `127.0.0.1` — see global setup step 9 note: vite binds IPv6-only.)
2. PinSetup screen renders. Type `1234` → Next → type `1234` → Submit.
3. After token issues, log out (clear localStorage `parent_token` via DevTools, refresh).
4. PinLogin screen renders. Submit 5 wrong PINs (e.g., `9999`) within 30 seconds.
5. After the 5th, attempt login with the correct `1234`.
6. **Token capture for later steps' verify (agent) calls.** The localStorage value isn't visible to a fresh agent shell, so the M2.5 helpers source a parent token from disk instead. Run once after step 2:
   ```powershell
   uv run python scripts/uat/mint_parent_token.py > data/.uat-backups/parent_token.txt
   ```
   Subsequent verify (agent) blocks read it via `$env:PARENT_TOKEN = (Get-Content data/.uat-backups/parent_token.txt -Raw)` (PowerShell) or `TOKEN=$(cat data/.uat-backups/parent_token.txt)` (Bash). Default TTL is 4h — re-run the helper if your M2.5 session takes longer.

**Verify (agent):**
```powershell
# After step 2 — PIN was set
Invoke-RestMethod http://127.0.0.1:8000/api/auth/parent/status
# Expected: pin_set=True, locked=False
uv run python -c "import sqlite3; print(sqlite3.connect('data/toybox.db').execute(\"SELECT key FROM settings WHERE key='parent_pin_hash'\").fetchone())"
# Expected: ('parent_pin_hash',)

# After step 5 — lock takes precedence even with correct PIN.
# IMPORTANT: this verify must run DURING the ~30s lockout window. Once
# the window expires, the lockout state clears and the call below would
# succeed with 200 — masking a regression where the lockout never
# actually fired in the first place.
$body = @{pin="1234"} | ConvertTo-Json
try { Invoke-RestMethod http://127.0.0.1:8000/api/auth/parent -Method Post -Body $body -ContentType "application/json" } catch { $_.Exception.Response.StatusCode.value__; $_.ErrorDetails.Message }
# Expected: 423 with body containing pin_locked and seconds_until_unlock > 0
```

**Verify (human):**
- After step 2: PinSetup screen replaced by parent home (no flash of login screen between them).
- After step 4: lockout banner shows a countdown that ticks once per second (watch ~5 seconds — the second digit changes).
- After step 5: input field disabled while locked; lock-takes-precedence message appears even with correct PIN.

**Fail signals:**
- Flash of login screen before setup completes.
- Countdown stalls (no ticking).
- Correct PIN bypasses lockout.
- Status endpoint returns `pin_set: false` after setup.

**Source of truth:** [src/toybox/api/auth.py](../../src/toybox/api/auth.py), [frontend/src/parent/components/PinSetup.tsx:30](../../frontend/src/parent/components/PinSetup.tsx#L30), [phase-d.md "Step 21"](phase-d.md#step-21-parent-pin-gate-argon2id--rate-limit)

## Step M2.5.2 — Child profile CRUD + banned_themes round-trip (covers step 18)

**Setup (agent):** PIN set from M2.5.1; reuse `$env:PARENT_TOKEN`. Database has zero `children` rows.

**Action (human):**
1. From parent home, open the Children/Profiles modal.
2. Create child: display_name `Ada`, birthdate `2020-01-15`, reading_level `pre-reader`, banned_themes `violence, scary monsters`.
3. Edit `Ada`: change reading_level to `early-reader`. Save.
4. Create a second child `Bob`. Confirm list ordering puts `Ada` first (alphabetical).
5. Delete `Ada` (no activities reference her yet → should succeed).

**Verify (agent):**
```powershell
$h = @{Authorization="Bearer $env:PARENT_TOKEN"}
# The endpoint returns a wrapped envelope: {"children": [...]}. Project
# `.children` to get the bare list before counting / iterating.
(Invoke-RestMethod http://127.0.0.1:8000/api/children -Headers $h).children | ConvertTo-Json -Depth 5
# Expected after step 2: one child, banned_themes="violence, scary monsters"
# Expected after step 3: same id, reading_level="early-reader"
# Expected after step 4: two children, ordered Ada then Bob
# Expected after step 5: only Bob remains
uv run python -c "import sqlite3; c = sqlite3.connect('data/toybox.db'); print(c.execute('SELECT display_name, banned_themes, reading_level FROM children ORDER BY display_name COLLATE NOCASE').fetchall())"
```

**Verify (human):**
- banned_themes input renders as chips OR comma-separated text (either acceptable per step 18 spec); saved value displays as entered.
- Save button gets disabled during request, re-enables after.

**Fail signals:**
- List ordering not alphabetical.
- Reading level form sends freeform text instead of validated enum.
- banned_themes split or trimmed unexpectedly.

**Source of truth:** [frontend/src/parent/components/ChildProfileEditor.tsx](../../frontend/src/parent/components/ChildProfileEditor.tsx), [src/toybox/api/children.py](../../src/toybox/api/children.py), [phase-c.md "Step 18"](phase-c.md#step-18-child-profile-editor)

## Step M2.5.3 — Toy ingest happy path + dedup (covers step 16)

**Setup (agent):** clean `toys` table; `data/images/toys/` and `data/images/.staging/` empty.
```powershell
ls data/images/toys/, data/images/.staging/ -ErrorAction SilentlyContinue
uv run python -c "import sqlite3; print(sqlite3.connect('data/toybox.db').execute('SELECT count(*) FROM toys').fetchone())"
# Expected: (0,)
```

**Action (human):**
1. Open the Toys modal. Upload `tests/fixtures/uat/m2-5/toy-1.png`.
2. Vision returns suggested fields. Confirm or adjust display_name → submit.
3. Re-upload the same file (`toy-1-dup.png`, byte-identical) → expect a 409 dedup response surfaced to the operator.

**Verify (agent):**
```powershell
$h = @{Authorization="Bearer $env:PARENT_TOKEN"}
Invoke-RestMethod http://127.0.0.1:8000/api/toys -Headers $h | ConvertTo-Json -Depth 4
# Expected after step 2: one toy with image_hash + display_name; one .png under data/images/toys/{uuid}.png
ls data/images/toys/  # expected: exactly one .png
uv run python -c "import sqlite3; print(sqlite3.connect('data/toybox.db').execute('SELECT count(*) FROM toys WHERE archived=0').fetchone())"
# Expected: (1,)
ls data/images/.staging/ -ErrorAction SilentlyContinue  # expected: empty (file moved out on confirm)
```

**Verify (human):**
- Vision-suggested display_name is non-empty and reasonable.
- On dedup re-upload (step 3): UI surfaces a "this toy already exists" message (not a generic error toast); the existing toy is highlighted or referenced.

**Fail signals:**
- File lingers in `.staging/` after confirm (janitor sweep skipped — log it but don't fail UAT; janitor TTL is 1h).
- Dedup returns 200 instead of 409 (would create duplicate row).
- Vision call hangs >30s without surfacing an error.

**Source of truth:** [src/toybox/api/toys.py](../../src/toybox/api/toys.py), [frontend/src/parent/components/ToyIngest.tsx](../../frontend/src/parent/components/ToyIngest.tsx), [phase-c.md "Step 16"](phase-c.md#step-16-toy-ingest-vision--ui)

## Step M2.5.4 — Room bulk ingest + bulk cap (covers step 17)

**Setup (agent):** clean `rooms` and `room_features`; vision capability already verified in global setup.

**Action (human):**
1. Upload 5 room photos (`room-1.jpg`…`room-5.jpg`) via the bulk modal.
2. In the tabbed review UI, assign each photo to a room (some new, some merged) and confirm features.
3. Submit.
4. Then attempt to upload the 51-photo folder → expect a 413 bulk-cap-exceeded error surfaced to the operator.

**Verify (agent):**
```powershell
$h = @{Authorization="Bearer $env:PARENT_TOKEN"}
Invoke-RestMethod http://127.0.0.1:8000/api/rooms -Headers $h | ConvertTo-Json -Depth 5
# Expected: rooms count matches the operator's assignment; room_features populated
uv run python -c "import sqlite3; c = sqlite3.connect('data/toybox.db'); print('rooms:', c.execute('SELECT count(*) FROM rooms').fetchone()[0]); print('features:', c.execute('SELECT count(*) FROM room_features').fetchone()[0])"
ls data/images/rooms/  # expected: one .jpg per canonical room photo
```

**Verify (human):**
- Tabbed UI groups photos by suggested room with thumbnails visible.
- 51-photo upload surfaces a clear "bulk cap exceeded" message (not a silent failure or generic 500).
- Atomic rollback: if the operator deliberately picks a duplicate room name to trigger 409, no partial inserts remain (verify (agent) re-runs after this negative-path try).

**Fail signals:**
- Photos uploaded but not assigned to any room (orphan files in `data/images/rooms/`).
- 51-photo upload silently truncates to 50 instead of rejecting.
- Partial state after a failed atomic confirm.

**Source of truth:** [src/toybox/api/rooms.py](../../src/toybox/api/rooms.py), [frontend/src/parent/components/RoomIngestBulk.tsx](../../frontend/src/parent/components/RoomIngestBulk.tsx), [phase-c.md "Step 17"](phase-c.md#step-17-room-ingest-bulk-vision--ui)

## Step M2.5.5 — Live activity polish + "why this?" + End confirm (covers step 23)

**Setup (agent):** spin up a proposed activity via the API (don't depend on a real trigger phrase firing). The propose body requires `intent`, `hour`, and `seed`; child assignment happens at approve-time via a separate request body. Approve, pause, resume, and end all require an `If-Match-Version` header for optimistic concurrency.
```powershell
$h = @{Authorization="Bearer $env:PARENT_TOKEN"; "Content-Type"="application/json"}
$body = @{ intent="dramatic_play"; hour=14; seed=42; trigger_phrase="lets play knights"; persona_reasoning="Ada loves castle stories" } | ConvertTo-Json
$resp = Invoke-RestMethod http://127.0.0.1:8000/api/activities/propose -Headers $h -Method Post -Body $body
$env:ACTIVITY_ID = $resp.id

# (Optional, agent-driven) approve via API instead of UI click — useful when
# the operator can't / won't click through. child_ids belong on the approve
# request, NOT propose. Note the If-Match-Version header.
$ada = ((Invoke-RestMethod http://127.0.0.1:8000/api/children -Headers $h).children)[0].id
$approveBody = @{ child_ids=@($ada) } | ConvertTo-Json
$approveHeaders = $h + @{ "If-Match-Version" = "1" }
Invoke-RestMethod "http://127.0.0.1:8000/api/activities/$($env:ACTIVITY_ID)/approve" -Headers $approveHeaders -Method Post -Body $approveBody | Out-Null
```

**Action (human):**
1. Refresh parent UI; SuggestionCard renders with a "why this?" expandable panel.
2. Expand it — confirm `trigger_phrase`, `persona_reasoning`, and `intent` are visible (with sensible labels, not raw JSON).
3. Approve activity. ActivityPanel renders.
4. Click End — confirm dialog appears. Cancel.
5. Click End again — confirm. Accept.

> **Pause/Resume UI is deliberately deferred for v1** (see [`frontend/src/parent/App.tsx:35-37`](../../frontend/src/parent/App.tsx#L35)). The backend `/pause` and `/resume` endpoints + the api client wrappers exist and are exercised by the verify (agent) below; the UI buttons are not part of v1 scope. Earlier drafts of this script told operators to "Click Pause" — that step is unimplementable and has been removed.

**Verify (agent):**
```powershell
$h = @{Authorization="Bearer $env:PARENT_TOKEN"}

# Pause idempotency from running state. Pause/resume require the activity
# to be in `running`; an approved activity needs one resume to transition
# approved → running before pause is accepted. Adjust the version values
# to match the live activity (each state-changing call bumps it by 1).
$cur = Invoke-RestMethod "http://127.0.0.1:8000/api/activities/$($env:ACTIVITY_ID)" -Headers $h
# pause #1: running → paused (envelope publishes, version bumps)
$h2 = $h + @{ "If-Match-Version" = "$($cur.version)" }
Invoke-WebRequest "http://127.0.0.1:8000/api/activities/$($env:ACTIVITY_ID)/pause" -Headers $h2 -Method Post | Select-Object StatusCode
# Expected: 200
# pause #2 (idempotent): version is unchanged so the same If-Match-Version
# value succeeds; no new envelope is published.
Invoke-WebRequest "http://127.0.0.1:8000/api/activities/$($env:ACTIVITY_ID)/pause" -Headers $h2 -Method Post | Select-Object StatusCode
# Expected: 200 (idempotent)

# After step 5: activity.state = "ended"
Invoke-RestMethod "http://127.0.0.1:8000/api/activities/$($env:ACTIVITY_ID)" -Headers $h | Select-Object state, version
# Expected: state="ended"

# trigger_phrase NOT leaked on the child WS topic (PII strip).
# Mint a child token, then subscribe with child scope. The strip is
# unconditional in `_emit_state`, but the same fields are also persisted
# into `metadata` -- a top-level membership check would miss the metadata
# copy. ws_inspect.py walks the payload tree recursively (verified at
# 008061e: a 2-line strip-from-metadata fix landed alongside a recursive
# test assertion).
$env:WS_TOKEN = (uv run python scripts/uat/mint_child_token.py)
# Run while the activity is still mid-flight (running or paused) so a
# state-change envelope actually fires within the listening window. The
# pause #1 above is a good trigger if you start ws_inspect just before it.
uv run python scripts/uat/ws_inspect.py --topic activity.state --duration 8 --filter trigger_phrase
# Expected: exit 0; stderr summary "seen>=1 matches=0"
```

**Verify (human):**
- "Why this?" panel renders all three fields with sensible labels (not raw JSON).
- End-confirm dialog actually appears (window.confirm or modal — either acceptable).
- ActivityPanel re-renders cleanly when state changes (no flicker, no stuck-on-loading).

**Fail signals:**
- Second pause returns 409 in the agent verify (broken idempotency).
- End button skips the confirm dialog.
- ws_inspect.py exits 1 (`trigger_phrase` appeared on the child topic — privacy regression).

**Source of truth:** [frontend/src/parent/components/SuggestionCard.tsx](../../frontend/src/parent/components/SuggestionCard.tsx), [frontend/src/parent/components/ActivityPanel.tsx](../../frontend/src/parent/components/ActivityPanel.tsx), [frontend/src/parent/App.tsx:35-37](../../frontend/src/parent/App.tsx#L35) (deferred pause/resume UI), [src/toybox/api/activities.py](../../src/toybox/api/activities.py) (`_emit_state`, ProposeRequest, ApproveRequest), [scripts/uat/ws_inspect.py](../../scripts/uat/ws_inspect.py), [scripts/uat/mint_child_token.py](../../scripts/uat/mint_child_token.py)

## Step M2.5.6 — Transcripts manager: list, search, delete one, wipe-all (covers step 22)

**Setup (agent):** seed `transcripts` with 3 distinct rows so list, search, delete-one, wipe-all all have something to act on. The transcript content column is named `text`, not `content`.
```powershell
uv run python -c @"
import sqlite3, datetime
c = sqlite3.connect('data/toybox.db')
now = datetime.datetime.now(datetime.UTC).isoformat()
c.execute('INSERT INTO sessions (id, started_at) VALUES (?, ?)', ('sess-uat-1', now))
for i, body in enumerate(['hello world', 'lets play knights', 'tea party time']):
    c.execute('INSERT INTO transcripts (id, session_id, started_at, ended_at, text) VALUES (?, ?, ?, ?, ?)',
              (f'tr-uat-{i}', 'sess-uat-1', now, now, body))
c.commit()
print(c.execute('SELECT count(*) FROM transcripts').fetchone())
"@
# Expected: (3,)
```

**Action (human):**
1. Open the Transcripts modal.
2. Confirm 3 rows render.
3. Search for `knights` → confirm only one row remains visible.
4. Clear search; delete one row.
5. Click Wipe All. Modal opens with PIN field.
6. Enter wrong PIN twice → see attempts-remaining decrement.
7. Enter correct PIN → confirm wipe.

**Verify (agent):**
```powershell
$h = @{Authorization="Bearer $env:PARENT_TOKEN"}
# After step 4: 2 rows
(Invoke-RestMethod http://127.0.0.1:8000/api/transcripts -Headers $h).Count
# Expected: 2
# After step 7: 0 transcripts; sessions row STILL EXISTS (no cascade)
uv run python -c "import sqlite3; c = sqlite3.connect('data/toybox.db'); print('transcripts:', c.execute('SELECT count(*) FROM transcripts').fetchone()[0]); print('sessions:', c.execute(\"SELECT count(*) FROM sessions WHERE id='sess-uat-1'\").fetchone()[0])"
# Expected: transcripts: 0, sessions: 1
# Wipe attempts logged at WARNING with NO PIN value (logs/backend.log
# captured by global setup step 7 redirect; logs/backend.err for stderr)
Select-String -Path "logs/backend.log","logs/backend.err" -Pattern "wipe.*pin" | Select-Object -Last 5
# Expected: WARN lines reference wipe + attempt counts but never a 4-digit PIN value
```

**Verify (human):**
- Search debounce visibly delays results by ~250ms (not instant).
- Wipe-all modal shows attempts-remaining inline with the failed PIN entry.
- Optimistic delete (step 4): row disappears immediately, then either stays gone (success) or reappears with a "couldn't delete" message (network failure).

**Fail signals:**
- Wipe cascades — `sessions` row disappears too.
- PIN value appears in a log line (PII leak).
- Modal accepts wipe without re-confirming PIN.

**Source of truth:** [frontend/src/parent/components/TranscriptsManager.tsx](../../frontend/src/parent/components/TranscriptsManager.tsx), [src/toybox/api/transcripts.py](../../src/toybox/api/transcripts.py), [phase-d.md "Step 22"](phase-d.md#step-22-transcript-management-ui)

## Step M2.5.7 — Operator metrics dashboard + ws → REST fallback (covers step 24)

**Setup (agent):** state from prior steps is fine (some toys, rooms, children, transcripts wiped, one ended activity).

**Action (human):**
1. Open the Operator tab in parent UI.
2. Watch for an initial render of all metric panels (activities, transcripts, audio, AI status, eval-judge, breaker).
3. Wait ~30 seconds — confirm the panel auto-refreshes (timestamp updates without manual reload, "via ws" indicator visible).
4. **Negative path (ws fallback):** in DevTools → Network → WS tab, find the active `/ws` connection and **close just that one connection** (right-click → Close, or browser-specific equivalent). Do NOT stop the backend — that kills the REST endpoint too and the panel will surface a real `/api/metrics` failure rather than exercising the fallback.
5. Wait ≤30 seconds — confirm the panel keeps refreshing via REST poll (timestamps still tick, indicator switches to "via rest").

**Verify (agent):**
```powershell
$h = @{Authorization="Bearer $env:PARENT_TOKEN"}
Invoke-RestMethod http://127.0.0.1:8000/api/metrics -Headers $h | ConvertTo-Json -Depth 5
# Expected top-level keys: activities, activity_quality, ai, audio,
# eval_gate, generated_at, transcripts, ws_subscribers.
# Notable nested fields:
#   activities.{proposed,approved,running,paused,ended,dismissed,didnt_work,completed}_current + last_24h
#   audio.{mic_device, queue_depth, buffer_overruns_total}
#   ai.{breaker_state, claude_capable, claude_capability_reason, listening_mode}
#   activity_quality.{judge_parent_agreement, last_24h_mean_scores, safety_autofails_last_24h}
#   eval_gate.{mean_dimension_scores, regressions_detected, last_run_at, placeholder_baseline}
```

> **No-DevTools fallback verify.** If you can't easily close a single WS row in DevTools, the OperatorTab fallback is also covered by [`frontend/src/parent/components/OperatorTab.test.tsx`](../../frontend/src/parent/components/OperatorTab.test.tsx) — specifically `falls back to REST poll on stale ws` and `resumes polling after a ws envelope goes stale`. Run them with `cd frontend; npx vitest run src/parent/components/OperatorTab.test.tsx`. Together with a curl-verified `/api/metrics` (this verify-agent block), they exercise the same code paths a live ws-kill would.

**Verify (human):**
- All panels render — none stuck on "loading…".
- Timestamp visibly updates on the auto-refresh tick.
- After ws kill (step 4): panel keeps updating (proves REST fallback).

**Fail signals:**
- Any panel stuck on a stale value while others update.
- REST fallback never kicks in after ws death (panel freezes).

**Source of truth:** [frontend/src/parent/components/OperatorTab.tsx](../../frontend/src/parent/components/OperatorTab.tsx), [src/toybox/api/metrics.py:35](../../src/toybox/api/metrics.py#L35), [phase-d.md "Step 24"](phase-d.md#step-24-metrics-endpoint--ws-topic--parent-operator-dashboard)

## Teardown (agent)

```powershell
# Stop backend + frontend (same Win32_Process pattern as global setup
# step 1 -- Get-Process can't see CommandLine on PowerShell 5.1, so a
# CommandLine-filtered Get-Process pipeline silently no-ops).
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe' OR Name='uv.exe'" |
  Where-Object { $_.CommandLine -match "toybox|vite" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2  # release SQLite WAL/SHM locks before any Move-Item

# Restore the operator's pre-UAT DB if they had one (most recent backup)
$bak = Get-ChildItem data/.uat-backups/toybox.db.bak-* -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($bak) {
  Move-Item -Force $bak.FullName data/toybox.db
  Write-Host "Restored DB from $($bak.FullName)"
}

# Older backups in data/.uat-backups/ can be deleted once the operator
# is confident the run was clean: Remove-Item data/.uat-backups/toybox.db.bak-*
# Or, if M2.5 was a clean test pass on test-only data: Remove-Item -Recurse -Force data/.uat-backups
```
