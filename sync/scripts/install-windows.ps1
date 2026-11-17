# Photobox Sync — Installation unter Windows (neben dslrBooth, LumaBooth o. Ä.)
#   Set-ExecutionPolicy -Scope Process Bypass; .\scripts\install-windows.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
& py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e . imageio-ffmpeg
$Action  = New-ScheduledTaskAction -Execute "$Root\scripts\start-sync.bat" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName "Photobox Sync" -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Write-Host "Fertig. Status und Einstellungen: http://127.0.0.1:8766" -ForegroundColor Green
Start-Process "$Root\scripts\start-sync.bat"
