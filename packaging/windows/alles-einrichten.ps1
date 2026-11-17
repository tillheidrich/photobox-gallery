# Photobox: alles in einem Zug auf einem frischen Windows-Rechner.
#
#   Rechtsklick auf "Alles einrichten.bat" -> Als Administrator ausfuehren
#
# Baut die .exe, legt sie nach C:\Fotobox, richtet Autostart und Energiesparen
# ein, sucht das Canon-SDK und ein Profil neben dem Quellordner und startet
# die Box. Jeder Schritt sagt, was er getan hat. Ein zweiter Lauf ist
# harmlos: er baut neu und ueberschreibt nur die Programmdateien.
#
# Not tried on a Windows machine when it was written (14.11.2026): read the
# output of the first run.
param(
    [string]$Ziel = "C:\Fotobox",
    [string]$Edsdk = "",
    [string]$Profil = ""
)
$ErrorActionPreference = "Stop"
$env:PHOTOBOX_NO_PAUSE = "1"      # the steps below must not each wait for the Enter key
$Here = $PSScriptRoot
$Root = (Resolve-Path "$Here\..\..").Path
$Neben = Split-Path -Parent $Root          # the folder the source lies in, e.g. the USB stick

function Schritt([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }

# 0. Administrator? Without it standby and firewall stay as they are.
$istAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $istAdmin) {
    Write-Host "Nicht als Administrator gestartet: Standby und Firewall werden nicht gesetzt." -ForegroundColor Yellow
    Write-Host "Besser abbrechen (Strg+C) und mit Rechtsklick 'Als Administrator ausfuehren' neu starten." -ForegroundColor Yellow
    Start-Sleep -Seconds 8
}

# 1. Build
Schritt "Programm bauen (dauert einige Minuten)"
& "$Here\build.ps1"
if (-not (Test-Path "$Here\dist\PhotoboxBooth.exe")) { throw "PhotoboxBooth.exe ist nicht entstanden." }

# 2. Copy. A running booth holds its .exe open, so it is stopped first.
Schritt "Nach $Ziel kopieren"
Get-Process PhotoboxBooth -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
New-Item -ItemType Directory -Force -Path $Ziel | Out-Null
Copy-Item "$Here\dist\PhotoboxBooth.exe", "$Here\dist\PhotoboxSync.exe", "$Here\dist\LIESMICH.txt", "$Here\Einrichten.bat" -Destination $Ziel -Force
Copy-Item "$Root\tools\soak.py" -Destination $Ziel -Force

# 3. Autostart, power, shortcut, firewall
Schritt "Einrichten (Autostart, kein Standby, Verknuepfung, Firewall)"
& "$Ziel\PhotoboxBooth.exe" --install
if ($LASTEXITCODE -ne 0) { Write-Host "Einrichten meldet einen Fehler (Code $LASTEXITCODE) - bitte die Zeilen darueber lesen." -ForegroundColor Yellow }

# 3b. Own window instead of Chrome: WebView2 present? old Chrome kiosk starters away.
Schritt "Eigenes Programmfenster (statt Chrome)"
& "$Here\Umstellen-auf-eigenes-Fenster.ps1" -Ziel $Ziel -WebView2Installieren

# 4. Canon SDK: given, or a folder next to the source whose name starts with EDSDK
if (-not $Edsdk) {
    $fund = Get-ChildItem -Path $Neben -Directory -Filter "EDSDK*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($fund) { $Edsdk = $fund.FullName }
}
# Settings for a first start, written before the SDK step: that step saves
# the settings file itself, and afterwards "nothing exists yet" was never true, only where nothing exists yet
$Daten = Join-Path $env:LOCALAPPDATA "PhotoboxBooth"
New-Item -ItemType Directory -Force -Path $Daten | Out-Null
if (-not (Test-Path "$Daten\local.json")) {
    Schritt "Grundeinstellungen anlegen"
    # D: only if it is a built-in disk: it may just as well be the stick this runs from
    $d = Get-Volume -DriveLetter D -ErrorAction SilentlyContinue
    $ablage = if ($d -and $d.DriveType -eq "Fixed") { "D:\\Fotobox" } else { "C:\\Fotobox\\Bilder" }
    $kamera = if ($Edsdk) { "edsdk" } else { "auto" }
    $json = "{`"camera`": {`"backend`": `"$kamera`"}, `"storage`": {`"folder`": `"$ablage`", `"layout`": `"event/date`", `"originals`": true, `"usb`": {`"enabled`": true, `"path`": `"`", `"originals`": true}}}"
    [IO.File]::WriteAllText("$Daten\local.json", $json, (New-Object Text.UTF8Encoding($false)))
    Write-Host "Zweiter Ordner: $($ablage -replace '\\\\','\')   Stick-Sicherung: an   Kamera: $kamera"
} else {
    Write-Host "`nEinstellungen vorhanden, bleiben unveraendert: $Daten\local.json"
}
if ($Edsdk) {
    Schritt "Canon-SDK einrichten aus $Edsdk"
    & "$Ziel\PhotoboxBooth.exe" --install-edsdk "$Edsdk"
} else {
    Write-Host "`nKein Canon-SDK gefunden. Spaeter: Einrichten.bat -> 2." -ForegroundColor Yellow
}

if (-not $Profil) {
    $fund = Get-ChildItem -Path $Neben -File -Filter "*profil*.json" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($fund) { $Profil = $fund.FullName }
}
if ($Profil -and -not (Test-Path "$Daten\profile.json")) {
    Schritt "Profil uebernehmen: $Profil"
    Copy-Item $Profil "$Daten\profile.json"
}

# Own card templates (frame PNG plus JSON) from a "Vorlagen" folder next to the source
$Vorlagen = Join-Path $Neben "Vorlagen"
if (Test-Path $Vorlagen) {
    Schritt "Kartenvorlagen uebernehmen: $Vorlagen"
    New-Item -ItemType Directory -Force -Path "$Daten\assets" | Out-Null
    Copy-Item "$Vorlagen\*" "$Daten\assets" -Force
}

# 6. What is still missing?
Schritt "Selbstdiagnose"
$diagnose = & "$Ziel\PhotoboxBooth.exe" --doctor 2>&1 | ForEach-Object { "$_" }
$diagnose | ForEach-Object { Write-Host $_ }
# Printing on Windows needs win32print, win32gui and win32timezone inside the
# .exe (no Visual C++ runtime since 15.11.2026: win32ui/MFC is no longer used).
# A build without them prints but cannot follow its jobs - said loudly here.
$druckmodul = $diagnose | Where-Object { $_ -match '^X\s+Druckmodul:' }
if ($druckmodul) {
    Write-Host "`nACHTUNG: Das Druckmodul der Box funktioniert nicht:" -ForegroundColor Red
    $druckmodul | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host "So nicht auf ein Event: Fehlermeldung oben lesen, Drucker in der Verwaltung pruefen, dann neu bauen." -ForegroundColor Red
} elseif ($diagnose | Where-Object { $_ -match '^!\s+Druckmodul:' }) {
    # Windows 11 sets up the SELPHY with "Microsoft IPP Class Driver" by itself;
    # with it the CP1300 accepted no job (booth PC, 15.11.2026).
    Write-Host "`nACHTUNG: Der Drucker laeuft mit dem falschen Treiber:" -ForegroundColor Yellow
    $diagnose | Where-Object { $_ -match 'Druckmodul:' } | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    Write-Host "Den Canon-Treiber von canon.de installieren (docs\WINDOWS-INSTALLATION.md, Abschnitt Druckertreiber)." -ForegroundColor Yellow
} elseif (-not ($diagnose | Where-Object { $_ -match 'Druckmodul:' })) {
    Write-Host "`nHinweis: Die Selbstdiagnose hat keine Zeile 'Druckmodul' ausgegeben - Ausgabe oben pruefen." -ForegroundColor Yellow
}

Schritt "Fertig"
Write-Host "Die Box liegt in $Ziel und startet bei der naechsten Anmeldung von selbst."
Write-Host "Jetzt starten: 'Fotobox' auf dem Desktop."
Write-Host "Verwaltung: Ecke oben links 3 Sekunden halten, PIN 2580, dann eine eigene PIN festlegen."
Write-Host "Alle Funktionen erklaert die Verwaltung unter 'Hilfe'."
