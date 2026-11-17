@echo off
REM Baut die beiden .exe-Dateien. Einfach doppelklicken.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
pause
