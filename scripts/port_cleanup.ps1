#Requires -Version 5.1
# Find and kill processes listening on toybox's ports (8000 backend, 4000 Vite).
# Usage: .\scripts\port_cleanup.ps1 [-Ports 8000,4000] [-DryRun]

[CmdletBinding()]
param(
    [int[]] $Ports = @(8000, 4000),
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'

foreach ($port in $Ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "port $port : free"
        continue
    }

    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $name = if ($proc) { $proc.ProcessName } else { '<unknown>' }

        if ($DryRun) {
            Write-Host "port $port : would kill PID $procId ($name)"
        } else {
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Write-Host "port $port : killed PID $procId ($name)"
            } catch {
                Write-Warning "port $port : failed to kill PID $procId ($name) - $_"
            }
        }
    }
}
