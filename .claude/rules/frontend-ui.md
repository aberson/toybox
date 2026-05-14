---
description: Vite + Windows dev-server traps for toybox's frontend.
paths:
  - "frontend/**"
---

# toybox frontend UI rules

Frontend dev server runs on **port 4000** (proxies `/api` + `/ws` to backend on **8000**). This is a **deliberate exception** from the typical `:3000` — do not "fix" it. Pin in `frontend/vite.config.ts` via `server.port: 4000` and `server.strictPort: true`.

## DB migrate before backend start

DB-backed routes return 500 with `unable to open database file` if migrations haven't been run. The orchestrator pre-flight for `/build-step --ui` must invoke:

```powershell
uv run python -m toybox.db.migrate
```

before starting the backend.

## Server cleanup

`/build-step --ui` leaves both backend and frontend bound on Windows. Between steps:

```powershell
Get-NetTCPConnection -LocalPort 4000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## IPv6-only loopback

Vite binds `::1` on Windows by default. Readiness probes that hit `http://127.0.0.1:4000/` will fail; use `http://localhost:4000/` or pin `--host 127.0.0.1` on launch.

## Single uvicorn worker

The backend MUST run with a single uvicorn worker (SQLite WAL is single-writer; `--workers >1` silently corrupts). Don't add `--workers` to dev-server launch commands.
