# Photobox: auf das eigene Programmfenster umstellen - und die Box so einrichten,
# wie sie seit 15.11.2026 laeuft: die Aufgabe "Photobox Booth" startet
# PhotoboxBooth.exe, die .exe ist der Waechter, der die Box als Kindprozess
# startet und nach einem Absturz wiederbringt; die Box zeigt sich im eigenen
# Fenster (WebView2), Chrome ist nur noch Ersatz.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File Umstellen-auf-eigenes-Fenster.ps1 [-Neustart] [-NeueExe <Pfad>]
#
# Laeuft ueber ssh als derselbe Benutzer, ohne dass jemand vor dem Bildschirm
# sitzt: kein Schritt braucht den Desktop. Beliebig oft ausfuehrbar - jeder
# Schritt prueft erst, ob er noetig ist. Schritte, die Administratorrechte
# brauchen, sagen das und werden ohne sie mit einer klaren Zeile uebersprungen.
#
#   0. -NeueExe: laufende Box geordnet beenden (--quit, sonst hart), neue .exe
#      nach $Ziel kopieren (die alte bleibt als PhotoboxBooth.exe.vorher liegen).
#      Gleiche Datei: nichts zu tun.
#   1. WebView2-Laufzeit pruefen (Registry). Fehlt sie: klare Meldung; die Box
#      faellt dann von selbst auf Chrome zurueck. -WebView2Installieren holt
#      sie ueber winget.
#   2. local.json: window.mode auf "own" (Sicherung daneben); ohne Eintrag gilt
#      "own" ohnehin. Fehlversuche des Fensters (window\fenster-fehlversuche.json)
#      auf 0: nach einer Reparatur probiert die Box ihr Fenster sofort wieder.
#   3. Aufgabe "Photobox Booth": Aktion = $Exe ohne Argumente (kein --no-kiosk,
#      kein --window chrome); Einstellungen: keine zweite Instanz (IgnoreNew),
#      Neustart bei Startfehler, keine Laufzeitgrenze, auch im Akkubetrieb.
#      Fehlt die Aufgabe, richtet PhotoboxBooth.exe --install sie ein.
#   4. Update-Nutzungszeit 10-4 Uhr (PhotoboxBooth.exe --dauerbetrieb aktivzeit).
#      Braucht Administratorrechte - sonst uebersprungen, mit Hinweis.
#   5. Alte Chrome-Kiosk-Verknuepfungen (Autostart, Desktop) und Run-Eintraege
#      werden in den Datenordner verschoben, nicht geloescht. Ein laufendes
#      Kiosk-Chrome der Box wird beendet (nur das mit dem Profil .photobox-kiosk).
#   6. -Neustart: Box geordnet beenden, ueber die Aufgabe neu starten (dann
#      laeuft sie in der angemeldeten Sitzung, nicht in der ssh-Sitzung), 40 s
#      warten, PhotoboxBooth.exe --window-selftest und /api/state ausgeben.
#
# Jede Ausfuehrung schreibt eine Zeile nach %LOCALAPPDATA%\PhotoboxBooth\logs\umstellung-fenster.log.
param(
    [string]$Ziel = "C:\Fotobox",
    [string]$NeueExe = "",
    [switch]$Neustart,
    [switch]$WebView2Installieren
)
$ErrorActionPreference = "Stop"
$env:PHOTOBOX_NO_PAUSE = "1"
$TaskName = "Photobox Booth"
$Exe = Join-Path $Ziel "PhotoboxBooth.exe"
$Daten = Join-Path $env:LOCALAPPDATA "PhotoboxBooth"
$Logs = Join-Path $Daten "logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$Ergebnis = New-Object System.Collections.Generic.List[string]

function Schritt([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }
function Notiz([string]$Text) { Write-Host $Text; $Ergebnis.Add($Text) | Out-Null }
function Ist-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
$Admin = Ist-Admin

# Ends the running booth: first the way the admin's "Beenden" does (camera
# handed back, exit code 0, the watcher stays down), then by force. An .exe
# from before the watcher has no --quit; it is ended by force.
function Wait-BoothGone {
    if (-not (Get-Process PhotoboxBooth -ErrorAction SilentlyContinue)) { return "lief nicht" }
    if (Test-Path $Exe) {
        & $Exe --quit | ForEach-Object { Write-Host "  $_" }
    }
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process PhotoboxBooth -ErrorAction SilentlyContinue)) { return "geordnet beendet" }
        Start-Sleep -Seconds 1
    }
    schtasks /End /TN "$TaskName" | Out-Null
    Get-Process PhotoboxBooth -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    return "hart beendet (falls die Kamera danach nicht antwortet: Kamera aus und wieder an)"
}

Write-Host "Administratorrechte: $(if ($Admin) { 'ja' } else { 'nein - Schritte, die sie brauchen, werden uebersprungen' })"

# 0. New .exe -----------------------------------------------------------------
if ($NeueExe) {
    Schritt "Neue PhotoboxBooth.exe"
    if (-not (Test-Path $NeueExe)) { throw "$NeueExe fehlt." }
    $gleich = (Test-Path $Exe) -and ((Get-FileHash $Exe).Hash -eq (Get-FileHash $NeueExe).Hash)
    if ($gleich) {
        Notiz "Exe unveraendert (gleiche Datei)"
    } else {
        Notiz "Box: $(Wait-BoothGone)"
        New-Item -ItemType Directory -Force -Path $Ziel | Out-Null
        if (Test-Path $Exe) { Copy-Item $Exe "$Exe.vorher" -Force }
        Copy-Item $NeueExe $Exe -Force
        Notiz "Exe ersetzt (alte als PhotoboxBooth.exe.vorher)"
    }
}
if (-not (Test-Path $Exe)) { throw "$Exe fehlt - erst bauen und kopieren (Alles einrichten.bat) oder -NeueExe angeben." }

# 1. WebView2 ---------------------------------------------------------------
Schritt "WebView2-Laufzeit"
function Get-WebView2Version {
    $keys = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
            "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}", "{65C35B14-6C1D-4122-AC46-7148CC9D6497}"
    foreach ($k in $keys) {
        foreach ($p in "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$k",
                       "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$k",
                       "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$k") {
            $pv = (Get-ItemProperty -Path $p -Name pv -ErrorAction SilentlyContinue).pv
            if ($pv -and $pv -ne "0.0.0.0") {
                try { if ([version]$pv -ge [version]"86.0.622.0") { return $pv } } catch { }
            }
        }
    }
    return ""
}
$wv2 = Get-WebView2Version
if (-not $wv2 -and $WebView2Installieren) {
    Write-Host "Installiere WebView2 ueber winget ..." -ForegroundColor Yellow
    winget install -e --id Microsoft.EdgeWebView2Runtime --accept-source-agreements --accept-package-agreements --silent
    $wv2 = Get-WebView2Version
}
if ($wv2) { Notiz "WebView2 $wv2" }
else {
    Write-Host "WebView2-Laufzeit FEHLT. Die Box startet dann Chrome als Ersatz." -ForegroundColor Red
    Write-Host "Nachholen: dieses Skript mit -WebView2Installieren, oder" -ForegroundColor Red
    Write-Host "  winget install -e --id Microsoft.EdgeWebView2Runtime" -ForegroundColor Red
    Notiz "WebView2 FEHLT"
}

# 2. local.json and the window's failed starts -------------------------------
Schritt "Einstellung window.mode"
$LocalJson = Join-Path $Daten "local.json"
if (Test-Path $LocalJson) {
    $raw = [IO.File]::ReadAllText($LocalJson)
    $cfg = $raw | ConvertFrom-Json
    $modus = if ($cfg.PSObject.Properties.Name -contains "window") { $cfg.window.mode } else { "" }
    if ($modus -and $modus -ne "own") {
        Copy-Item $LocalJson "$LocalJson.vor-fenster-$(Get-Date -Format yyyyMMdd-HHmmss)" -Force
        $cfg.window.mode = "own"
        $neu = $cfg | ConvertTo-Json -Depth 32
        [IO.File]::WriteAllText($LocalJson, $neu, (New-Object Text.UTF8Encoding($false)))
        Notiz "window.mode $modus -> own"
    } else {
        Notiz "window.mode: $(if ($modus) { $modus } else { 'nicht gesetzt = own' })"
    }
    if ($cfg.PSObject.Properties.Name -contains "kiosk" -and $cfg.kiosk -eq $false) {
        Write-Host "Achtung: ""kiosk"": false in local.json - die Box zeigt dann GAR kein Fenster." -ForegroundColor Yellow
        Notiz "kiosk=false (kein Fenster!)"
    }
} else {
    Notiz "local.json fehlt noch (erster Start legt sie an, Standard own)"
}
$Fehlversuche = Join-Path $Daten "window\fenster-fehlversuche.json"
if (Test-Path $Fehlversuche) {
    $f = [IO.File]::ReadAllText($Fehlversuche) | ConvertFrom-Json
    if ($f.in_a_row -gt 0) {
        Copy-Item $Fehlversuche "$Fehlversuche.vorher" -Force
        [IO.File]::WriteAllText($Fehlversuche, '{"in_a_row": 0, "reason": "", "last": 0}', (New-Object Text.UTF8Encoding($false)))
        Notiz "Fehlversuche des Fensters zurueckgesetzt (waren $($f.in_a_row): $($f.reason))"
    } else { Notiz "keine Fehlversuche des Fensters gespeichert" }
} else { Notiz "keine Fehlversuche des Fensters gespeichert" }

# 3. Scheduled task: starts the watcher (the .exe, no arguments) ---------------
Schritt "Aufgabe '$TaskName'"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Aufgabe fehlt - PhotoboxBooth.exe --install richtet sie ein." -ForegroundColor Yellow
    & $Exe --install | ForEach-Object { Write-Host $_ }
    Notiz "Aufgabe neu eingerichtet (Zeilen mit -- darueber brauchen evtl. Administratorrechte)"
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
if ($task) {
    $a = $task.Actions | Select-Object -First 1
    $exeIst = ($a.Execute -replace '"', '')
    $argsIst = "$($a.Arguments)".Trim()
    # The watcher passes its arguments on to the booth; the task needs none.
    $falsch = ($exeIst -ne $Exe) -or ($argsIst -match '--no-kiosk|--window(\s|=)+(chrome|none)')
    if ($falsch) {
        $neueAktion = New-ScheduledTaskAction -Execute $Exe -WorkingDirectory $Ziel
        Set-ScheduledTask -TaskName $TaskName -Action $neueAktion | Out-Null
        Notiz "Aufgabe korrigiert: '$exeIst $argsIst' -> '$Exe'"
    } else {
        Notiz "Aufgabe ok: $exeIst $argsIst"
    }
    # Settings: a second "schtasks /Run" while the booth runs must not start a
    # second watcher (IgnoreNew); a failed start is retried; no time limit.
    $s = $task.Settings
    $soll = ($s.MultipleInstances -eq "IgnoreNew") -and ($s.RestartCount -ge 3) -and
            ($s.ExecutionTimeLimit -eq "PT0S") -and (-not $s.DisallowStartIfOnBatteries) -and (-not $s.StopIfGoingOnBatteries)
    if ($soll) {
        Notiz "Aufgaben-Einstellungen ok (IgnoreNew, RestartCount $($s.RestartCount), ohne Zeitlimit)"
    } else {
        try {
            $neu = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries `
                   -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
                   -RestartInterval (New-TimeSpan -Minutes 1)
            Set-ScheduledTask -TaskName $TaskName -Settings $neu | Out-Null
            Notiz "Aufgaben-Einstellungen gesetzt (IgnoreNew, RestartCount 3, ohne Zeitlimit, Akku erlaubt)"
        } catch {
            Notiz "Aufgaben-Einstellungen uebersprungen ($($_.Exception.Message)) - als Administrator wiederholen"
        }
    }
} else {
    Notiz "Aufgabe fehlt weiterhin - PhotoboxBooth.exe --install in einer Konsole am PC ausfuehren"
}

# 4. Update active hours 10-04 (administrator) ------------------------------------
Schritt "Update-Nutzungszeit 10-4 Uhr"
$ux = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -ErrorAction SilentlyContinue
$aktivOk = $ux -and ($ux.ActiveHoursStart -eq 10) -and ($ux.ActiveHoursEnd -eq 4) -and ($ux.SmartActiveHoursState -eq 0)
if ($aktivOk) {
    Notiz "Nutzungszeit ok (10-4 Uhr, nicht automatisch)"
} elseif ($Admin) {
    & $Exe --dauerbetrieb aktivzeit | ForEach-Object { Write-Host "  $_" }
    Notiz "Nutzungszeit gesetzt: Code $LASTEXITCODE (0 = ok)"
} else {
    Write-Host "Nutzungszeit uebersprungen: braucht Administratorrechte." -ForegroundColor Yellow
    Write-Host "  Nachholen in einer Administrator-Konsole: `"$Exe`" --dauerbetrieb aktivzeit" -ForegroundColor Yellow
    Notiz "Nutzungszeit uebersprungen (keine Administratorrechte)"
}

# 5. Old Chrome kiosk ----------------------------------------------------------
Schritt "Alte Chrome-Kiosk-Starter"
$Ablage = Join-Path $Daten "alte-chrome-verknuepfungen"
$shell = New-Object -ComObject WScript.Shell
$ordner = @([Environment]::GetFolderPath("Startup"), [Environment]::GetFolderPath("CommonStartup"),
            [Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("CommonDesktopDirectory")) |
          Where-Object { $_ -and (Test-Path $_) }
$verschoben = 0
foreach ($o in $ordner) {
    foreach ($lnk in Get-ChildItem -Path $o -Filter *.lnk -ErrorAction SilentlyContinue) {
        $s = $shell.CreateShortcut($lnk.FullName)
        $istKiosk = ($s.TargetPath -match '(chrome|msedge|chromium)\.exe$') -and
                    ($s.Arguments -match '--kiosk|127\.0\.0\.1:87|localhost:87|photobox-kiosk')
        if ($istKiosk) {
            New-Item -ItemType Directory -Force -Path $Ablage | Out-Null
            try {
                Move-Item $lnk.FullName (Join-Path $Ablage $lnk.Name) -Force
                Notiz "Verknuepfung weggeraeumt: $($lnk.FullName)"
                $verschoben++
            } catch { Notiz "Verknuepfung nicht verschiebbar (braucht Administratorrechte, uebersprungen): $($lnk.FullName)" }
        }
    }
}
foreach ($run in "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run", "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run") {
    $werte = Get-ItemProperty -Path $run -ErrorAction SilentlyContinue
    if (-not $werte) { continue }
    foreach ($p in $werte.PSObject.Properties) {
        if ($p.Name -like "PS*") { continue }
        $v = "$($p.Value)"
        if ($v -match '(chrome|msedge)\.exe' -and $v -match '--kiosk|127\.0\.0\.1:87|photobox-kiosk') {
            New-Item -ItemType Directory -Force -Path $Ablage | Out-Null
            Add-Content -Path (Join-Path $Ablage "run-eintraege.txt") -Value "$run`t$($p.Name)`t$v"
            try {
                Remove-ItemProperty -Path $run -Name $p.Name
                Notiz "Autostart-Eintrag entfernt: $($p.Name) (gesichert in $Ablage)"
                $verschoben++
            } catch { Notiz "Autostart-Eintrag nicht entfernbar (braucht Administratorrechte, uebersprungen): $($p.Name)" }
        }
    }
}
if ($verschoben -eq 0) { Notiz "keine alten Chrome-Kiosk-Starter gefunden" }
$kioskChrome = Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe'" -ErrorAction SilentlyContinue |
               Where-Object { $_.CommandLine -match 'photobox-kiosk' }
if ($kioskChrome) {
    $kioskChrome | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Notiz "Kiosk-Chrome der Box beendet ($(@($kioskChrome).Count) Prozesse)"
}

# 6. Restart and self-test --------------------------------------------------------
if ($Neustart) {
    Schritt "Box neu starten"
    Notiz "Box: $(Wait-BoothGone)"
    # Through the task: the booth then runs in the logged-on session with its
    # window, not in this ssh session (which has no desktop).
    schtasks /Run /TN "$TaskName" | Out-Null
    Notiz "Neustart ueber die Aufgabe angestossen"
    Write-Host "Warte 40 s auf den Start ..."
    Start-Sleep -Seconds 40
    Schritt "Selbsttest (PhotoboxBooth.exe --window-selftest)"
    & $Exe --window-selftest | ForEach-Object { Write-Host $_ }
    Notiz "Selbsttest: Code $LASTEXITCODE (0 = Fenster lebt)"
    try {
        $port = 8765
        if (Test-Path $LocalJson) { $c = [IO.File]::ReadAllText($LocalJson) | ConvertFrom-Json; if ($c.port) { $port = $c.port } }
        $st = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/state" -TimeoutSec 10
        Write-Host ("window:     " + ($st.window | ConvertTo-Json -Compress -Depth 4))
        Write-Host ("supervisor: " + ($st.supervisor | ConvertTo-Json -Compress -Depth 4))
        Notiz "Zustand: Fenster $($st.window.mode), lebt=$($st.window.alive), Waechter=$($st.supervisor.watched)"
    } catch { Notiz "Zustand nicht abrufbar: $($_.Exception.Message)" }
}

$zeile = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  " + ($Ergebnis -join " | ")
Add-Content -Path (Join-Path $Logs "umstellung-fenster.log") -Value $zeile
Write-Host "`nProtokoll: $(Join-Path $Logs 'umstellung-fenster.log')" -ForegroundColor Green
