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
#   -WithCameraSdk <Ordner>
#                   ZUSAETZLICH die Fassung mit Kamera-SDK (nur fuer die
#                   Website, nie in CI, nie signiert): <Ordner> ist der
#                   Dll-Ordner des Canon EOS Digital SDK (64 Bit) AUSSERHALB
#                   des Repositorys. Braucht -Installer. Die freie Fassung
#                   entsteht dabei genau wie sonst in dist\ und wird vorher
#                   geprueft (keine Canon-Datei). Die Fassung mit Kamera-SDK
#                   landet in -CameraSdkOut (Standard:
#                   %USERPROFILE%\Photobox-Build\mit-Kamera-SDK), ebenfalls
#                   ausserhalb des Repositorys. Veroeffentlicht wird nur das
#                   Setup, also genau diese drei Dateien:
#                     Photobox-Setup-<Version>-mit-Kamera-SDK.exe
#                     SHA256SUMS-mit-Kamera-SDK.txt
#                     THIRD-PARTY-NOTICES-mit-Kamera-SDK.txt
#                   Die portable Box mit Kamera-SDK entsteht nur fuer die
#                   Pruefung, in nicht-veroeffentlichen\ daneben.
# Am Ende liegt dist\SHA256SUMS.txt neben den Dateien. Siehe docs\INSTALLER.md.
param(
    [switch]$SyncOnly,
    [switch]$BoothOnly,
    [switch]$Installer,
    [switch]$InstallerOnly,
    [switch]$Sign,
    [string]$WithCameraSdk = "",
    [string]$CameraSdkOut = "",
    [string]$Python = ""
)
if ($InstallerOnly) { $Installer = $true }
$ErrorActionPreference = "Stop"
# Set only by step 7 for the second build. Left over in this shell from an
# earlier, aborted run, it would make photobox-booth.spec pack Canon's files
# into the FREE program; so it is cleared before anything is built.
Remove-Item Env:PHOTOBOX_CAMERA_SDK_STAGE -ErrorAction SilentlyContinue
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

# The edition with camera SDK: Canon's files never enter the repository, CI or
# a GitHub release. Refused on a CI runner, refused for a folder inside the
# repository, and only together with a full build (free edition first).
$SdkSource = ""
if ($WithCameraSdk) {
    if ($env:GITHUB_ACTIONS -or $env:CI) {
        throw "-WithCameraSdk laeuft nie auf einem CI-Runner (GITHUB_ACTIONS/CI ist gesetzt). CI baut nur die freie Fassung."
    }
    if (-not $Installer -or $InstallerOnly -or $BoothOnly -or $SyncOnly) {
        throw "-WithCameraSdk braucht -Installer und einen vollen Bau (ohne -InstallerOnly, -BoothOnly, -SyncOnly)."
    }
    if (-not (Test-Path -LiteralPath $WithCameraSdk -PathType Container)) { throw "SDK-Ordner nicht gefunden: $WithCameraSdk" }
    $SdkSource = (Resolve-Path -LiteralPath $WithCameraSdk).Path
    if (-not $CameraSdkOut) { $CameraSdkOut = Join-Path $env:USERPROFILE "Photobox-Build\mit-Kamera-SDK" }
    $CameraSdkOut = [IO.Path]::GetFullPath($CameraSdkOut)
    foreach ($p in @($SdkSource, $CameraSdkOut)) {
        if (("$p\").StartsWith("$Root\", [StringComparison]::OrdinalIgnoreCase)) {
            throw "$p liegt im Repository. Canon-Dateien kommen nie ins Repository: Ordner ausserhalb waehlen."
        }
    }
    Write-Host "Zusaetzlich: Fassung mit Kamera-SDK aus $SdkSource nach $CameraSdkOut" -ForegroundColor Cyan
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

# 3a. Guard for the free edition, on every build and BEFORE anything is
# signed: no file of Canon's SDK in dist or inside the two programs (the same
# check CI runs). Fails the build; a signature never lands on such a file.
function Test-FreeEdition {
    $progs = @()
    foreach ($n in "PhotoboxBooth.exe", "PhotoboxSync.exe") { if (Test-Path "$Here\dist\$n") { $progs += "$Here\dist\$n" } }
    $guardArgs = @("$Root\tools\check_hygiene.py", "--build-output", "$Here\dist", "$Legal")
    if ($progs.Count) { $guardArgs += "--pyinstaller"; $guardArgs += $progs }
    Invoke-Schritt "Waechter der freien Fassung (keine Canon-Datei)" { & $Py @guardArgs }
}
Test-FreeEdition

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
    $isccBase = @("/DAppVersion=$AppVersion", "/DAppVersionNumeric=$AppVersionNumeric",
                  "/DDistDir=$Here\dist", "/DBuildDir=$Here\installer\build")
    $isccArgs = $isccBase + @("/DOutputDir=$Here\dist")
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
# LF and no BOM: WriteAllLines ends lines with CRLF on Windows, and
# "sha256sum -c" elsewhere then looks for "name\r" and calls it missing.
[IO.File]::WriteAllText("$Here\dist\SHA256SUMS.txt", ($sums -join "`n") + "`n", (New-Object Text.UTF8Encoding $false))
Write-Host "Pruefsummen: $Here\dist\SHA256SUMS.txt"

# 6b. The guard once more, now that the setup lies in dist as well.
Test-FreeEdition

# 7. The edition with camera SDK (only with -WithCameraSdk; website only,
# never CI, never signed). Everything happens in $CameraSdkOut, outside the
# repository; dist\ and the free files are not touched.
if ($WithCameraSdk) {
    $Edition = "$Here\camera_sdk_edition.py"
    $Stage = Join-Path $CameraSdkOut "stage"
    $SdkLegal = Join-Path $Stage "legal"
    New-Item -ItemType Directory -Force -Path $CameraSdkOut | Out-Null
    Invoke-Schritt "Kamera-SDK unveraendert bereitlegen" { & $Py $Edition stage --sdk $SdkSource --out $Stage }
    New-Item -ItemType Directory -Force -Path $SdkLegal | Out-Null
    Copy-Item "$Legal\*" $SdkLegal -Force
    Invoke-Schritt "Lizenzhinweise mit Kamera-SDK" {
        & $Py $Edition notices --free "$Legal\THIRD-PARTY-NOTICES.txt" --stage $Stage --out "$SdkLegal\THIRD-PARTY-NOTICES.txt"
    }
    $env:PHOTOBOX_CAMERA_SDK_STAGE = $Stage
    $env:PHOTOBOX_LEGAL_DIR = $SdkLegal
    Push-Location $Here
    try {
        Write-Host "Baue PhotoboxBooth mit Kamera-SDK ..." -ForegroundColor Cyan
        Invoke-Schritt "Bau der Box mit Kamera-SDK" {
            & $Py -m PyInstaller --noconfirm --clean --distpath "$CameraSdkOut\pyi" --workpath "$CameraSdkOut\.work" "$Here\photobox-booth.spec"
        }
    } finally {
        Pop-Location
        Remove-Item Env:PHOTOBOX_CAMERA_SDK_STAGE -ErrorAction SilentlyContinue
        $env:PHOTOBOX_LEGAL_DIR = $Legal
    }
    # The portable program with camera SDK is built and checked, but not
    # published (decision 18.11.2026: this edition is offered as a setup
    # only). It lies apart, in a folder whose name says so.
    $Portable = "PhotoboxBooth-$AppVersion-mit-Kamera-SDK.exe"
    $SdkSetup = "Photobox-Setup-$AppVersion-mit-Kamera-SDK.exe"
    $Internal = Join-Path $CameraSdkOut "nicht-veroeffentlichen"
    New-Item -ItemType Directory -Force -Path $Internal | Out-Null
    Remove-Item "$CameraSdkOut\$Portable" -ErrorAction SilentlyContinue   # from a build before 0.9.2's decision
    Copy-Item "$CameraSdkOut\pyi\PhotoboxBooth.exe" "$Internal\$Portable" -Force
    Invoke-Schritt "Pruefung der Box mit Kamera-SDK" { & $Py $Edition verify-portable --exe "$Internal\$Portable" --stage $Stage }

    # The setup packs the free programs from dist\ plus EDSDK\ and the terms;
    # never signed (no SignTool define), whatever PHOTOBOX_SIGN_* says.
    $isccSdk = $isccBase + @("/DOutputDir=$CameraSdkOut", "/DCameraSdkDir=$Stage", "/DNoticesFile=$SdkLegal\THIRD-PARTY-NOTICES.txt")
    Write-Host "Baue das Setup mit Kamera-SDK ..." -ForegroundColor Cyan
    Invoke-Schritt "Bau des Setups mit Kamera-SDK" { & $iscc @isccSdk "$Here\installer\photobox.iss" }

    # Published next to the setup: its checksum file and the notices of
    # exactly this edition (with the SDK section).
    Copy-Item "$SdkLegal\THIRD-PARTY-NOTICES.txt" "$CameraSdkOut\THIRD-PARTY-NOTICES-mit-Kamera-SDK.txt" -Force
    $sdkSums = @()
    foreach ($name in @($SdkSetup)) {
        $file = "$CameraSdkOut\$name"
        if (-not (Test-Path $file)) { throw "Fuer SHA256SUMS-mit-Kamera-SDK.txt fehlt $file." }
        $sdkSums += "$((Get-FileHash $file -Algorithm SHA256).Hash.ToLower())  $name"
    }
    [IO.File]::WriteAllText("$CameraSdkOut\SHA256SUMS-mit-Kamera-SDK.txt", ($sdkSums -join "`n") + "`n", (New-Object Text.UTF8Encoding $false))
    Write-Host "Pruefsummen: $CameraSdkOut\SHA256SUMS-mit-Kamera-SDK.txt"

    # The free files once more: nothing of the second build may have reached them.
    Test-FreeEdition
    Get-ChildItem $CameraSdkOut -File | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}} | Format-Table
    Write-Host "Fassung mit Kamera-SDK: $CameraSdkOut (nur fuer die Website, nicht fuer GitHub)" -ForegroundColor Green
    Write-Host "Veroeffentlichen: $SdkSetup, SHA256SUMS-mit-Kamera-SDK.txt, THIRD-PARTY-NOTICES-mit-Kamera-SDK.txt - nicht den Ordner nicht-veroeffentlichen\." -ForegroundColor Green
}

Get-ChildItem "$Here\dist" | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}} | Format-Table
Write-Host "Fertig: $Here\dist" -ForegroundColor Green
