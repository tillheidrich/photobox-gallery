@echo off
REM Einrichten ohne Befehle zu tippen. Neben die PhotoboxBooth.exe legen
REM und doppelklicken. Rechtsklick -> "Als Administrator ausfuehren", damit
REM auch Energiesparen und Firewall gesetzt werden koennen.
setlocal
cd /d "%~dp0"
if not exist "PhotoboxBooth.exe" (
  echo PhotoboxBooth.exe liegt nicht neben dieser Datei.
  echo Beide Dateien in denselben Ordner legen, zum Beispiel C:\Fotobox\
  pause
  exit /b 1
)
echo.
echo   1  Fotobox einrichten (Autostart, kein Standby, Verknuepfung)
echo   2  Canon-SDK einrichten (Ordner des entpackten Canon-Archivs angeben)
echo   3  Selbstdiagnose (was fehlt noch?)
echo   4  Einrichtung rueckgaengig machen
echo.
set /p wahl="Zahl eingeben und Enter: "
if "%wahl%"=="1" PhotoboxBooth.exe --install
if "%wahl%"=="2" goto edsdk
if "%wahl%"=="3" PhotoboxBooth.exe --doctor
if "%wahl%"=="4" PhotoboxBooth.exe --uninstall
goto ende

:edsdk
echo.
echo Den Ordner angeben, in den das Canon-Archiv entpackt wurde.
echo Darin liegen 32- und 64-Bit-Fassungen nebeneinander; die richtige
echo sucht die Box selbst heraus.
set /p sdk="Ordner (oder hierher ziehen): "
PhotoboxBooth.exe --install-edsdk %sdk%

:ende
echo.
pause
