---
name: uat-ui
description: Run a vision-judged UI UAT against the toybox parent/kiosk UI. Stands up the app against an isolated DB, clears the PIN gate, drives a named flow with Playwright, and calls /judge-ui to render a PASS/FAIL verdict with stage screenshots + an /api read-back. Use when the operator says "UAT the <feature> UI", "run a UI UAT on toybox", "vision-judge the parent UI", or wants the look-at-the-screen UAT done for them. This is the toybox ADAPTER for the dev-level /judge-ui engine. Invoke as "/uat-ui <flow> [--keep-up]".
user-invocable: true
---

# UAT UI (toybox adapter)

The toybox half of the visual-tier UAT pair: this skill supplies the **project-specific** bits
(server bring-up, auth, navigation, the flow library) and delegates the generic drive + vision
verdict to **`/judge-ui`**. It exists because the toybox parent UI is PIN-gated by design
(`feedback_buildstep_pin_gate_blocks_ui_evidence`) and has no `webServer` auto-start — so a
naive Playwright run either false-passes behind the gate or can't reach the app at all. This
adapter encodes the recipe that works.

First proven on the Phase X room-import feature (2026-06-21): spec
`frontend/playwright/room-import.spec.ts`, verdict
`documentation/runs/2026-06-21-room-import-uat.md`. See `project_playwright_uat_harness` memory.

## Invocation

```text
/uat-ui room-import          # run the room-import flow end-to-end + vision verdict
/uat-ui room-import --keep-up # leave the isolated servers running after (default: tear down)
```

## Step 1 — Bring up the app (isolated, non-destructive)

**Never run against the operator's real DB or fight for shared ports.** Use an isolated DB and
guard the ports.

**Port-ownership guard (ALWAYS the first action — never skip, never blind-kill).** Inspect who
owns :4000/:8000 *before* killing or starting anything. A listener you did not start is the
operator's app (or a parallel session) on a different DB, and driving it with PIN `4242` **locks
out their parent account** — so an un-owned port is a STOP, not a kill (see Safety).

```powershell
Get-NetTCPConnection -LocalPort 4000,8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue } | Select-Object Id, ProcessName, Path
```

- If any listener is **not** your own isolated `uat-<flow>` instance (confirm via its command
  line / CWD / `TOYBOX_DB_PATH`), **STOP and surface the owning PID(s) to the operator. Do NOT
  kill it and do NOT drive the flow** — produce no PASS verdict.
- Only once every listener is your own prior `uat-<flow>` instance (or the ports are free) free
  them, then set up the isolated DB:

```powershell
Get-NetTCPConnection -LocalPort 4000,8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
$env:TOYBOX_DB_PATH = "C:\Users\abero\dev\toybox\data\uat-<flow>.db"
$env:TOYBOX_DISABLE_AUDIO = "1"
Remove-Item "$env:TOYBOX_DB_PATH*" -Force -ErrorAction SilentlyContinue   # fresh DB → clean PinSetup
uv run python -m toybox.db.migrate
```

Then start backend + vite as **background** processes (each needs the env vars set in its own
shell — PowerShell tool state doesn't persist between calls):

- Backend: `$env:TOYBOX_DB_PATH=...; $env:TOYBOX_DISABLE_AUDIO="1"; uv run python -m toybox.main --host 127.0.0.1 --port 8000`
- Vite: `Set-Location C:\Users\abero\dev\toybox\frontend; npm run dev`

Readiness probe before driving: poll `http://127.0.0.1:8000/api/auth/parent/status` (expect
`{"pin_set":false,...}` on a fresh DB) and `http://localhost:4000/` (200). Note vite binds `::1`
on Windows — probe `localhost`, not `127.0.0.1`, for :4000.

**Playwright install.** `@playwright/test` is NOT a declared frontend dependency (a `test:ui`
script invokes it but no dep is listed — a latent gap; declaring it is the recommended fix).
Install locally without touching manifests, pinned to match the cached browser:

```powershell
Set-Location C:\Users\abero\dev\toybox\frontend
npm install --no-save --no-package-lock "@playwright/test@1.60.0"
```

Pin **1.60.0** → chromium **v1223** (already in the ms-playwright cache). 1.61.0 wants 1228 →
forces a download. The `playwright.config.ts` has **no `webServer`** block, so servers must be
up before `npx playwright test` (Step 1 above handles that).

## Step 2 — Reach an authed state (the PIN gate)

The parent main app (tabs) mounts only after a PIN flow completes. Make the helper tolerate both
landing screens so it works on a fresh DB *and* a re-run:

- Fresh DB (`pin_set:false`) → **PinSetup**: fill `pin-setup-pin-input` + `pin-setup-confirm-input`
  with `4242`, click `pin-setup-submit`.
- PIN already set → **PinLogin**: fill `pin-login-pin-input` with `4242`, click `pin-login-submit`.
- Wait for `tab-kids-toyboxes` to confirm the main app rendered.

**Auth for read-backs.** The app sends its token as the **`X-Toybox-Token`** header (NOT
`Authorization: Bearer`). To read `/api` out of band, capture it from a live request header
(`req.headers()['x-toybox-token']`) and pass it as `X-Toybox-Token` on `page.request.get(...)`.

## Step 3 — Navigate + drive the flow

Nav testids (parent UI): top tab `tab-kids-toyboxes` → sub-tab `subtab-rooms` →
`toggle-import-panel-button`. Room-import flow testids: `listing-content-input`,
`parse-listing-button`, `import-rooms-table`, `import-room-row`, `import-room-name`,
`import-room-type` (select; values are the `ROOM_TYPE_OPTIONS`), `import-room-active`,
`create-rooms-button`; success collapses `room-import-body`, failure shows `import-commit-error`.

### Flow library

| Flow | What it drives | Ground truth / rubric |
|---|---|---|
| `room-import` | paste listing → Parse → review table → edit name+type → Create → `/api/rooms` read-back | See `frontend/playwright/room-import.spec.ts`. For the standard Option-2 paste, the parser yields **exactly 7** proposed rooms: `Bedroom #1, Bathroom #1–4, Kitchen #1, Dining Room #1` — NOT "4 bedrooms" (a documented parser quirk: Redfin writes counts *after* the word, and `"(Maximum): 4\nBathroom"` bleeds into 4 bathrooms). The rubric judges a faithful parse→review→edit→create→persist, NOT a "correct" room count. |

**Adding a flow:** add a row above, write/extend a Playwright spec under `frontend/playwright/`
that captures stage screenshots to `frontend/playwright/test-results/<flow>/` + writes an `/api`
read-back JSON, then list the per-stage rubric for the vision judge. Keep the toybox-specific
auth/nav in this skill; keep the generic drive + verdict in `/judge-ui`.

## Step 4 — Judge + verdict

Hand the stage screenshots + the `/api` read-back JSON + the flow rubric to **`/judge-ui`** (or
dispatch the vision-judge sub-agent directly per `/judge-ui`'s run loop). It cross-checks pixels
against the read-back, and on uncertainty escalates to you rather than auto-passing. The verdict
doc lands in `documentation/runs/<date>-<flow>-uat.md`, last line `VERDICT: PASS|FAIL|ESCALATE`.

## Step 5 — Teardown (default on; `--keep-up` to skip)

```powershell
Get-NetTCPConnection -LocalPort 4000,8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Remove-Item C:\Users\abero\dev\toybox\data\uat-<flow>.db* -Force -ErrorAction SilentlyContinue
```

## Safety (toybox-specific, learned the hard way)

- **Isolated DB only.** `TOYBOX_DB_PATH=data/uat-<flow>.db` — never the operator's
  `data/toybox.db`. The imported rooms etc. live only in the isolated DB.
- **Do NOT drive a backend you don't own.** If `:4000`/`:8000` are held by another session or the
  operator's own running app, your PIN `4242` is the *wrong* PIN for their DB — repeated attempts
  **lock out their parent account** (the auth backend has a lockout). Check ownership first
  (`Get-NetTCPConnection ... | Get-Process`); if it's not your isolated instance, STOP and surface
  it rather than driving. This workspace runs concurrent sessions (see `dev/CLAUDE.md` §Parallel
  session safety).
- **Single uvicorn worker, migrate-before-run, LAN-needs-PIN** — the standing toybox gotchas
  (`toybox/CLAUDE.md`). Loopback (`127.0.0.1`) bring-up sidesteps the LAN-PIN startup guard.
- **Touch only test artifacts.** This adapter adds Playwright specs + verdict docs; it never
  edits the feature under test.
