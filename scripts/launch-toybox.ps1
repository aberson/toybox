#Requires -Version 5.1
<#
  launch-toybox.ps1 - one-click launcher for toybox (parent on this machine, child on iPad).

  What it does:
    1. Frees stale toybox ports (4000/8000) from a previous run.
    2. Migrates the DB (idempotent; avoids 'unable to open database file' 500s).
    3. Detects this machine's LAN IP and exports TOYBOX_LAN_IP so the iPad kiosk
       passes the Origin allow-list check.
    4. Starts the backend (single uvicorn worker) bound to 0.0.0.0 for the iPad.
       LAN bind requires the parent PIN to already be set - that's the startup guard.
    5. Starts the Vite frontend (vite.config.ts already binds all interfaces).
    6. Waits for the frontend, opens the Parent UI on this machine, prints the iPad URL.

  Close the spawned backend/frontend windows to stop the servers.

  Flags:
    -LoopbackOnly   Bind 127.0.0.1 only (parent on this machine, no iPad).
    -LanIp <ip>     Force a specific LAN IP instead of auto-detecting.
#>
[CmdletBinding()]
param(
    [switch] $LoopbackOnly,
    [string] $LanIp
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "=== toybox launcher ===" -ForegroundColor Cyan

# 1. Free stale ports so re-clicking is idempotent.
& "$PSScriptRoot\port_cleanup.ps1" | Out-Host

# 2. Resolve LAN IP (active adapter that owns the default route).
if (-not $LoopbackOnly -and -not $LanIp) {
    $LanIp = (Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } |
        Select-Object -First 1).IPv4Address.IPAddress
}
$bindHost = if ($LoopbackOnly -or -not $LanIp) { '127.0.0.1' } else { '0.0.0.0' }

# 3. DB migrate (synchronous, before the backend starts).
Write-Host "Migrating DB..." -ForegroundColor Cyan
uv run python -m toybox.db.migrate

# 4. Export Origin allow-list IP; inherited by the spawned backend window.
if ($LanIp) {
    $env:TOYBOX_LAN_IP = $LanIp
    Write-Host "LAN IP: $LanIp" -ForegroundColor Green
} else {
    Write-Host "No LAN IP (loopback-only): iPad kiosk will NOT be reachable." -ForegroundColor Yellow
}

# 5. Backend in its own window (single worker; SQLite WAL is single-writer).
$backendCmd = "Set-Location '$root'; uv run python -m toybox.main --host $bindHost --port 8000"
Start-Process powershell -ArgumentList '-NoExit', '-Command', $backendCmd | Out-Null

# 6. Frontend in its own window (vite.config.ts pins :4000 + binds all interfaces).
$frontendCmd = "Set-Location '$root\frontend'; npm run dev"
Start-Process powershell -ArgumentList '-NoExit', '-Command', $frontendCmd | Out-Null

# 7. Wait for the frontend, then open the Parent UI here.
Write-Host "Waiting for frontend on http://localhost:4000 ..." -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds(60)
$up = $false
while ((Get-Date) -lt $deadline) {
    try {
        Invoke-WebRequest -Uri 'http://localhost:4000/' -UseBasicParsing -TimeoutSec 2 | Out-Null
        $up = $true; break
    } catch { Start-Sleep -Milliseconds 800 }
}
if ($up) {
    Start-Process 'http://localhost:4000/parent'
    Write-Host "Parent UI opened: http://localhost:4000/parent" -ForegroundColor Green
} else {
    Write-Host "Frontend didn't answer in 60s - check its window, then open http://localhost:4000/parent manually." -ForegroundColor Yellow
}

if ($LanIp) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  iPad child kiosk:  http://$LanIp`:4000/child" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
}
Write-Host ""
Read-Host "Press Enter to close this launcher (servers keep running in their own windows)"
