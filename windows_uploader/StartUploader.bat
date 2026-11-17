@echo off
setlocal
cd /d %~dp0

REM Starte den Uploader mit der config.json im gleichen Ordner
python photobox_uploader.py --config config.json

REM Für Debugging: Fenster offen halten
pause
