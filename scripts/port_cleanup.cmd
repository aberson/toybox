@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0port_cleanup.ps1" %*
echo.
pause
