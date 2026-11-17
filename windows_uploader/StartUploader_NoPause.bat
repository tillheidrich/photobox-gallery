@echo off
setlocal
cd /d %~dp0
python photobox_uploader.py --config config.json
