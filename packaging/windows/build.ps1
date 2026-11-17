# Baut PhotoboxBooth.exe und PhotoboxSync.exe auf einem Windows-Rechner.
#
#   Rechtsklick auf "Fotobox bauen.bat" -> Ausführen
#   oder in PowerShell:  Set-ExecutionPolicy -Scope Process Bypass; .\build.ps1
#
# Ergebnis liegt danach in packaging\windows\dist\:
#   PhotoboxBooth.exe, PhotoboxSync.exe          ohne Version (Box-Update, Setup)
#   PhotoboxBooth-<Version>.exe                  die Box allein, zum Herunterladen
#   PhotoboxSync-<Version>.exe                   der Sync allein, zum Herunterladen
#   Photobox-Setup-<Version>.exe (-Installer)    Setup mit Box UND Sync
#   SHA256SUMS.txt                               genau die Dateien mit Version
# Die Version kommt aus der Datei VERSION im Projektstamm.
#
#   -Installer      zusaetzlich das Setup bauen (Inno Setup; fehlt es, wird es
#                   ueber winget installiert): dist\Photobox-Setup-<Version>.exe
#                   Das Setup enthaelt immer auch den Sync; mit -BoothOnly
#                   oder -SyncOnly zusammen bricht der Bau deshalb ab.
#   -InstallerOnly  nur das Setup, aus den vorhandenen dist\PhotoboxBooth.exe
#                   und dist\PhotoboxSync.exe
#   -Sign           signieren ist Pflicht: mit PHOTOBOX_SIGN_THUMBPRINT
#                   (Zertifikat im Windows-Zertifikatspeicher, auch Karte,
#                   Token oder Certum SimplySign) oder PHOTOBOX_SIGN_COMMAND
#                   (Befehl eines Cloud-Signierdienstes mit {file} als Platzhalter).
#                   Ohne -Sign wird signiert, wenn eine der beiden gesetzt ist.
# Am Ende liegt dist\SHA256SUMS.txt neben den Dateien. Siehe docs\INSTALLER.md.
param(
    [switch]$SyncOnly,
    [switch]$BoothOnly,
    [switch]$Installer,
    [switch]$InstallerOnly,
    [switch]$Sign,
    [string]$Python = ""
)
if ($InstallerOnly) { $Installer = $true }
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Root = (Resolve-Path "$Here\..\..").Path
$env:PHOTOBOX_ROOT = $Root
Write-Host "Photobox-Quelle: $Root" -ForegroundColor Cyan

# One version for every file name of this build, read once.
$AppVersion = (Get-Content "$Root\VERSION" -Raw).Trim()
if (-not ($AppVersion -match '^\d+\.\d+\.\d+')) { throw "VERSION ist leer oder unlesbar: '$AppVersion'" }
Write-Host "Version: $AppVersion" -ForegroundColor Cyan

# The setup always carries the sync program, so it cannot be built from half
# a build. Said before anything is installed, not after ten minutes of pip.
if ($Installer -and ($BoothOnly -or $SyncOnly)) {
    throw "-Installer braucht Box UND Sync: ohne -BoothOnly/-SyncOnly bauen (oder -InstallerOnly)."
}

# Native Befehle melden Fehler nicht von selbst. Ohne diese Hilfe lief das
# Skript nach einem gescheiterten pip oder PyInstaller einfach weiter und
# scheiterte 30 Zeilen spaeter beim Schreiben des Beipackzettels -- mit einer
# Meldung, die mit der Ursache nichts zu tun hat.
function Invoke-Schritt {
    param([string]$Was, [scriptblock]$Block)
    & $Block
    if ($LASTEXITCODE -ne 0) {
        throw "$Was ist fehlgeschlagen (Code $LASTEXITCODE). Alles Weitere wuerde darauf aufbauen."
    }
}

# winget lives in the user's WindowsApps folder, which is on PATH only in an
# interactive logon. Over ssh or from a scheduled task the bare name is not
# found although winget is installed.
function Find-Winget {
    $w = (Get-Command winget.exe -ErrorAction SilentlyContinue).Source
    if ($w) { return $w }
    $w = "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe"
    if (Test-Path $w) { return $w }
    return ""
}
function Invoke-Winget([string]$Id) {
    $w = Find-Winget
    if (-not $w) { throw "winget fehlt (weder auf PATH noch unter %LOCALAPPDATA%\Microsoft\WindowsApps). $Id bitte von Hand installieren." }
    & $w install -e --id $Id --accept-source-agreements --accept-package-agreements
}

# 1. Python besorgen (einmalig)
if (-not $Python) {
    # `Get-Command py` sagt nur, dass es den Starter gibt -- nicht, dass 3.12
    # installiert ist. Auf einem Rechner mit 3.11 oder 3.13 scheiterte das
    # venv, und der Betreiber sah zwei Zeilen spaeter "is not recognized".
    $hat312 = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        # Under "Stop", Windows PowerShell turns a native tool's stderr into a
        # terminating error - and "no 3.12 here" is said on stderr.
        $alt = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & py -3.12 -c "import sys" *> $null
        $hat312 = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $alt
    }
    if ($hat312) { $Python = "py -3.12" }
    else {
        Write-Host "Python fehlt - installiere über winget ..." -ForegroundColor Yellow
        Invoke-Winget "Python.Python.3.12"
        $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
        $Python = "py -3.12"
    }
}

# 2. Eigene Umgebung, damit nichts am System hängen bleibt
$Venv = "$Here\.buildenv"
if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    Invoke-Expression "$Python -m venv `"$Venv`""
}
$Py = "$Venv\Scripts\python.exe"
Invoke-Schritt "pip aktualisieren" { & $Py -m pip install --upgrade pip wheel }

# booth\requirements.txt enthaelt `-e ../sync`. pip loest relative Pfade in
# Requirements-Dateien gegen das ARBEITSVERZEICHNIS auf, nicht gegen den Ort
# der Datei -- und beim Doppelklick auf die .bat ist das packaging\windows.
# Dort gibt es kein ..\sync, pip brach beim Parsen ab, installierte gar
# nichts, und PyInstaller baute anschliessend eine .exe ohne Pillow, die auf
# dem Fotobox-Rechner sofort starb.
#
# constraints.txt holds the exact versions the test suites passed with, so a
# build next month is made from the same packages as the one tested today.
# pywin32 is not in it (the reference environment is not Windows) and stays open.
$Constraints = "$Root\constraints.txt"
Push-Location "$Root\booth"
try   { Invoke-Schritt "Abhaengigkeiten der Box" { & $Py -m pip install -r "$Root\booth\requirements.txt" -c $Constraints } }
finally { Pop-Location }
Invoke-Schritt "Abhaengigkeiten des Sync" { & $Py -m pip install -r "$Root\sync\requirements.txt" -c $Constraints }
# In Anfuehrungszeichen: `>` ist in PowerShell eine Umleitung. Ohne sie hiess
# die Zeile "pip install pyinstaller" und legte eine Datei namens "=6.6" an.
Invoke-Schritt "PyInstaller" { & $Py -m pip install "pyinstaller>=6.6" }

# 2b. Licences of exactly this build. tools\third_party_notices.py reads the
# packages installed in .buildenv (the same ones PyInstaller packs) and stops
# the build when one has no licence text or a licence that does not go with
# the AGPL. The result rides inside the .exe (admin -> "Ueber") and next to it.
# Not under .work: PyInstaller --clean empties its work folder.
$Legal = "$Here\.legal"
New-Item -ItemType Directory -Force -Path $Legal | Out-Null
Invoke-Schritt "Lizenzhinweise (THIRD-PARTY-NOTICES.txt)" {
    & $Py "$Root\tools\third_party_notices.py" --platform win32 --out "$Legal\THIRD-PARTY-NOTICES.txt" `
        --json "$Legal\third-party-inventory.json" --publisher-out "$Legal\publisher.json"
}
$env:PHOTOBOX_LEGAL_DIR = $Legal

# 3. Bauen
Push-Location $Here
try {
    if ($InstallerOnly) {
        foreach ($n in "PhotoboxBooth.exe", "PhotoboxSync.exe") {
            if (-not (Test-Path "$Here\dist\$n")) { throw "-InstallerOnly: dist\$n fehlt - erst ohne -InstallerOnly bauen." }
        }
        Write-Host "Nur das Setup: vorhandene dist\PhotoboxBooth.exe und dist\PhotoboxSync.exe werden verwendet." -ForegroundColor Yellow
    } elseif (-not $SyncOnly) {
        Write-Host "Baue PhotoboxBooth.exe ..." -ForegroundColor Cyan
        Invoke-Schritt "Bau der Box" { & $Py -m PyInstaller --noconfirm --clean --distpath "$Here\dist" --workpath "$Here\.work" "$Here\photobox-booth.spec" }
    }
    if (-not $BoothOnly -and -not $InstallerOnly) {
        Write-Host "Baue PhotoboxSync.exe ..." -ForegroundColor Cyan
        Invoke-Schritt "Bau des Sync" { & $Py -m PyInstaller --noconfirm --clean --distpath "$Here\dist" --workpath "$Here\.work" "$Here\photobox-sync.spec" }
    }
} finally { Pop-Location }

# 3b. Sign, when a certificate is set up. Without one the files stay unsigned
# and Windows shows "unknown publisher" at the first start -- fine on one's own
# booth, not for a download in a shop. Set PHOTOBOX_SIGN_THUMBPRINT to the
# thumbprint of the code signing certificate in the Windows certificate store
# (card, token or cloud key such as Certum SimplySign all appear there).
# A cloud signing service (SignPath, a CLI of the certificate vendor) instead:
# PHOTOBOX_SIGN_COMMAND, a command line with {file} where the file goes.
$SignMode = if ($env:PHOTOBOX_SIGN_THUMBPRINT) { "store" } elseif ($env:PHOTOBOX_SIGN_COMMAND) { "command" } else { "" }
if ($Sign -and -not $SignMode) {
    throw "-Sign verlangt PHOTOBOX_SIGN_THUMBPRINT oder PHOTOBOX_SIGN_COMMAND (siehe docs\INSTALLER.md)."
}
$signtool = ""
if ($SignMode -eq "store") {
    $signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
    if (-not $signtool) {
        $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "x64" } | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $signtool) { throw "PHOTOBOX_SIGN_THUMBPRINT ist gesetzt, aber signtool.exe fehlt (Windows SDK installieren)." }
}
$stempel = if ($env:PHOTOBOX_SIGN_TIMESTAMP) { $env:PHOTOBOX_SIGN_TIMESTAMP } else { "http://time.certum.pl" }

function Sign-Datei([string]$Pfad) {
    $name = Split-Path -Leaf $Pfad
    if ((Get-AuthenticodeSignature $Pfad).Status -eq "Valid") { Write-Host "$name ist bereits signiert."; return }
    Write-Host "Signiere $name ..." -ForegroundColor Cyan
    if ($SignMode -eq "store") {
        Invoke-Schritt "Signieren von $name" { & $signtool sign /sha1 $env:PHOTOBOX_SIGN_THUMBPRINT /fd SHA256 /tr $stempel /td SHA256 /d "Photobox" $Pfad }
    } else {
        $cmd = $env:PHOTOBOX_SIGN_COMMAND.Replace("{file}", "`"$Pfad`"")
        Invoke-Schritt "Signieren von $name" { cmd /c $cmd }
    }
    $sig = Get-AuthenticodeSignature $Pfad
    if ($sig.Status -ne "Valid") { throw "Signatur von $name ist nicht gueltig: $($sig.StatusMessage)" }
}

if ($SignMode) {
    foreach ($n in "PhotoboxBooth.exe", "PhotoboxSync.exe") {
        if (Test-Path "$Here\dist\$n") { Sign-Datei "$Here\dist\$n" }
    }
} else {
    Write-Host "Nicht signiert (weder PHOTOBOX_SIGN_THUMBPRINT noch PHOTOBOX_SIGN_COMMAND gesetzt)." -ForegroundColor Yellow
    Write-Host "Windows zeigt beim ersten Start 'Unbekannter Herausgeber' bzw. die SmartScreen-Warnung." -ForegroundColor Yellow
}
Copy-Item "$Legal\THIRD-PARTY-NOTICES.txt" "$Here\dist\THIRD-PARTY-NOTICES.txt" -Force
# The standalone downloads go without the setup's licence page: the licence
# texts lie next to them (and are packed inside each .exe as well).
Copy-Item "$Root\LICENSE" "$Here\dist\LICENSE.txt" -Force
Copy-Item "$Root\LICENSE-EXCEPTION" "$Here\dist\LICENSE-EXCEPTION.txt" -Force

# 3c. Standalone downloads with the version in the name. Copies of the signed
# files, so they carry the same signature. The un-versioned files stay: the
# setup packs them, and a box update copies exactly dist\PhotoboxBooth.exe.
# Only what this run built (or, with -InstallerOnly, what it packs) gets this
# version's name -- an older PhotoboxSync.exe left in dist must not be
# relabelled by a -BoothOnly build.
$Release = @()
if (-not $SyncOnly) { $Release += "PhotoboxBooth" }
if (-not $BoothOnly) { $Release += "PhotoboxSync" }
foreach ($n in $Release) {
    if (-not (Test-Path "$Here\dist\$n.exe")) { throw "dist\$n.exe fehlt nach dem Bau." }
    Copy-Item "$Here\dist\$n.exe" "$Here\dist\$n-$AppVersion.exe" -Force
    Write-Host "Download-Datei: dist\$n-$AppVersion.exe"
}

# 4. Beipackzettel neben die Dateien legen
$version = (Get-Content "$Root\booth\boothcore\__init__.py" | Select-String '__version__' | Select-Object -First 1)
@"
Photobox — Windows-Dateien
Gebaut am $(Get-Date -Format 'dd.MM.yyyy HH:mm')  $version

Zum Herunterladen (Pruefsummen in SHA256SUMS.txt):
  Photobox-Setup-$AppVersion.exe   Setup: Box und Sync, Startmenue, Autostart (nur mit -Installer gebaut)
  PhotoboxBooth-$AppVersion.exe    die Box allein (gleiche Datei wie PhotoboxBooth.exe)
  PhotoboxSync-$AppVersion.exe     der Sync allein (gleiche Datei wie PhotoboxSync.exe)

PhotoboxBooth.exe   Die Fotobox selbst. Erster Start:
                    PhotoboxBooth.exe --install   (Autostart, Energiesparen aus,
                    Verknüpfung, Firewall). Danach einfach doppelklicken.
PhotoboxSync.exe    Nur der Sync, für Rechner mit dslrBooth/LumaBooth.
                    Erster Start als Administrator:
                    PhotoboxSync.exe --install   (Autostart bei der Anmeldung,
                    kein Standby, Verknüpfung, Firewall nur für diesen PC).
                    Danach läuft er ohne Fenster; Symbol neben der Uhr,
                    Status unter http://127.0.0.1:8766

Canon-Kameras ueber das Canon-SDK:
                    Das SDK bei Canon holen, entpacken, dann einmal
                    PhotoboxBooth.exe --install-edsdk <entpackter Ordner>
                    Das sucht die zur .exe passende Fassung heraus (im Archiv
                    liegen 32 und 64 Bit nebeneinander) und legt sie richtig ab.
                    Von Hand kopieren geht auch, dann aber ohne diese Pruefung:
                    die falsche DLL meldet nur "keine zulaessige Win32-Anwendung".

Die Schritte, die --install einrichtet, brauchen zum Teil Administratorrechte
(Energiesparen aus, Firewall). Ohne sie richtet es Autostart und Verknuepfung
ein und sagt bei den uebrigen Schritten, dass sie nicht gegriffen haben --
lies die Ausgabe. Ohne "Standby aus" schlaeft der Rechner mitten im Abend ein.
"@ | Set-Content -Encoding UTF8 "$Here\dist\LIESMICH.txt"

# 5. Setup (optional): Inno Setup compiles installer\photobox.iss.
if ($Installer) {
    function Find-Iscc {
        $c = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
        if ($c) { return $c }
        foreach ($d in "${env:ProgramFiles(x86)}\Inno Setup 7", "$env:ProgramFiles\Inno Setup 7",
                       "${env:ProgramFiles(x86)}\Inno Setup 6", "$env:ProgramFiles\Inno Setup 6",
                       "$env:LOCALAPPDATA\Programs\Inno Setup 7", "$env:LOCALAPPDATA\Programs\Inno Setup 6") {
            if (Test-Path "$d\ISCC.exe") { return "$d\ISCC.exe" }
        }
        # winget installs Inno Setup per user. The build may run under another
        # account than the one that installed it: look in every profile.
        foreach ($v in "7", "6") {
            $hit = Get-ChildItem "$env:SystemDrive\Users\*\AppData\Local\Programs\Inno Setup $v\ISCC.exe" -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty FullName
            if ($hit) { return $hit }
        }
        return ""
    }
    $iscc = Find-Iscc
    if (-not $iscc) {
        Write-Host "Inno Setup fehlt - installiere ueber winget (JRSoftware.InnoSetup) ..." -ForegroundColor Yellow
        Invoke-Winget "JRSoftware.InnoSetup"
        $iscc = Find-Iscc
    }
    if (-not $iscc) { throw "ISCC.exe nicht gefunden. Inno Setup von https://jrsoftware.org/isdl.php installieren und neu starten." }

    $nums = @([regex]::Matches($AppVersion, "\d+") | ForEach-Object { $_.Value } | Select-Object -First 4)
    while ($nums.Count -lt 4) { $nums += "0" }
    $AppVersionNumeric = ($nums -join ".")

    Invoke-Schritt "Setup-Seiten und -Bilder" { & $Py "$Here\installer\make_installer_files.py" }
    $isccArgs = @("/DAppVersion=$AppVersion", "/DAppVersionNumeric=$AppVersionNumeric",
                  "/DDistDir=$Here\dist", "/DOutputDir=$Here\dist", "/DBuildDir=$Here\installer\build")
    if ($SignMode -eq "store") {
        # Inno replaces $f with the quoted file name and $q with a quote.
        $isccArgs += "/Sphotobox=`$q$signtool`$q sign /sha1 $($env:PHOTOBOX_SIGN_THUMBPRINT) /fd SHA256 /tr $stempel /td SHA256 /d `$qPhotobox Setup`$q `$f"
        $isccArgs += "/DSignToolName=photobox"
    } elseif ($SignMode -eq "command") {
        $isccArgs += "/Sphotobox=" + $env:PHOTOBOX_SIGN_COMMAND.Replace("{file}", "`$f")
        $isccArgs += "/DSignToolName=photobox"
    }
    Write-Host "Baue das Setup (Inno Setup: $iscc) ..." -ForegroundColor Cyan
    Invoke-Schritt "Bau des Setups" { & $iscc @isccArgs "$Here\installer\photobox.iss" }
    $setup = "$Here\dist\Photobox-Setup-$AppVersion.exe"
    if ($SignMode -and (Get-AuthenticodeSignature $setup).Status -ne "Valid") { throw "Das Setup ist nicht gueltig signiert." }
}

# 6. Checksums for the download page: sha256sum -c and Get-FileHash both read this.
# Exactly the files that are offered for download, all with this version in the
# name -- not every .exe that happens to lie in dist (older setups, the
# un-versioned copies).
$Offered = @()
foreach ($n in $Release) { $Offered += "$n-$AppVersion.exe" }
if ($Installer) { $Offered += "Photobox-Setup-$AppVersion.exe" }
$sums = @()
foreach ($name in ($Offered | Sort-Object)) {
    $file = "$Here\dist\$name"
    if (-not (Test-Path $file)) { throw "Fuer SHA256SUMS.txt fehlt dist\$name." }
    $sums += "$((Get-FileHash $file -Algorithm SHA256).Hash.ToLower())  $name"
}
[IO.File]::WriteAllLines("$Here\dist\SHA256SUMS.txt", [string[]]$sums, (New-Object Text.UTF8Encoding($false)))
Write-Host "Pruefsummen: $Here\dist\SHA256SUMS.txt"

Get-ChildItem "$Here\dist" | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}} | Format-Table
Write-Host "Fertig: $Here\dist" -ForegroundColor Green
