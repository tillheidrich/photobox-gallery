@echo off
cd /d "%~dp0\.."
:loop
start "" /b /wait ".venv\Scripts\pythonw.exe" -m photobox_sync
if %ERRORLEVEL%==0 goto end
timeout /t 3 /nobreak >nul
goto loop
:end
