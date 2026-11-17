@echo off
REM Alles in einem Zug: bauen, nach C:\Fotobox legen, einrichten, Canon-SDK,
REM Profil, Selbstdiagnose, starten. Rechtsklick -> "Als Administrator
REM ausfuehren". Einzelheiten: docs\WINDOWS-INSTALLATION.md
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0alles-einrichten.ps1"
pause
