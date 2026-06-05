@echo off
REM One-click toybox launcher (parent here, child on iPad).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-toybox.ps1" %*
