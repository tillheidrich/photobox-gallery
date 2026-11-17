# Photobox Booth — Installation unter Windows 10/11
# Ausführen in PowerShell (als normaler Benutzer der Box):
#   Set-ExecutionPolicy -Scope Process Bypass; .\scripts\install-windows.ps1
#
# Macht: Python prüfen/installieren, virtuelle Umgebung, Abhängigkeiten,
# Autostart bei Anmeldung (Vollbild), Desktop-Verknüpfung, Energiesparen aus.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "Photobox Booth wird eingerichtet in $Root" -ForegroundColor Cyan

# 1. Python 3.12
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Python fehlt – installiere über winget ..."
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
& py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Canon EDSDK-Ordner (Dateien kommen vom Canon Developer Programme, nicht aus diesem Repo)
New-Item -ItemType Directory -Force -Path "$Root\EDSDK" | Out-Null
if (-not (Test-Path "$Root\EDSDK\EDSDK.dll")) {
    Write-Host "Hinweis: EDSDK.dll und EdsImage.dll nach $Root\EDSDK kopieren (Canon Developer Programme)." -ForegroundColor Yellow
}

# 3. Autostart: Aufgabe bei Anmeldung, startet die Box im Vollbild
$Action  = New-ScheduledTaskAction -Execute "$Root\scripts\start-booth.bat" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "Photobox Booth" -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Write-Host "Autostart eingerichtet (Aufgabenplanung: Photobox Booth)."

# 4. Desktop-Verknüpfung
$Shell = New-Object -ComObject WScript.Shell
$Lnk = $Shell.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\Photobox Booth.lnk")
$Lnk.TargetPath = "$Root\scripts\start-booth.bat"; $Lnk.WorkingDirectory = $Root; $Lnk.Save()

# 5. Box soll nicht schlafen, Bildschirm nicht ausgehen, USB nicht abschalten
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setactive SCHEME_CURRENT

Write-Host "Fertig. Neu anmelden oder 'Photobox Booth' auf dem Desktop starten." -ForegroundColor Green
Write-Host "Verwaltung: 3 Sekunden oben links auf den Startbildschirm drücken, PIN (Standard 2580) sofort ändern."
