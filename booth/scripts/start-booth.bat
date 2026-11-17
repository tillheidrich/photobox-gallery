@echo off
REM Startet die Box und startet sie nach einem Absturz oder Fernneustart neu.
cd /d "%~dp0\.."
:loop
".venv\Scripts\python.exe" -m boothcore
if %ERRORLEVEL%==0 goto end
REM 4 = another booth already runs and was brought to the front.
if %ERRORLEVEL%==4 goto end
echo Box beendet mit Code %ERRORLEVEL% - Neustart in 3 Sekunden ...
timeout /t 3 /nobreak >nul
goto loop
:end
