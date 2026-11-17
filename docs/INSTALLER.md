# Das Windows-Setup der Fotobox

`Photobox-Setup-<Version>.exe` installiert die Box auf einem Windows-10/11-Rechner
(64 Bit). Diese Seite beschreibt, wie es gebaut wird, was es tut, wie es ohne
Fenster (ssh) läuft, wie aktualisiert, zurückgerollt und deinstalliert wird und
was man vor einer Veröffentlichung noch braucht (Signatur). Stand: 15.11.2026.
Das Setup ist am 15.11.2026 auf dem Box-Rechner mit Inno Setup 6.7.3 gebaut
worden (ohne Warnungen), aber noch nicht ausgeführt – die Testliste am Ende ist
die Abnahme.

## Warum Inno Setup

Inno Setup ist ein Skript-Installer (eine `.iss`-Datei, kein XML, kein
MSI-Datenbankmodell), seit Jahrzehnten verbreitet, lässt sich per `winget`
installieren und über die Kommandozeile bauen. Seine Lizenz erlaubt jede
Nutzung, auch kommerziell; der Hersteller bittet kommerzielle Nutzer (Umsatz über
5 000 USD im Jahr) um den Kauf einer Lizenz, verlangt ihn aber nicht
(jrsoftware.org/isorder.php) – das ist eine Entscheidung des Herausgebers, siehe
„Offene Punkte“. NSIS hätte dasselbe gekonnt, aber mit mehr Handarbeit für
Deinstallation und Mehrsprachigkeit; WiX/MSI lohnt sich erst für Verteilung über
Gruppenrichtlinien, die hier niemand braucht. Das Skript braucht Inno Setup
6.4 oder neuer (ältere brechen mit einem klaren Satz ab), auch Inno Setup 7.

## Bauen

Auf dem Windows-Rechner, im Projektordner:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\build.ps1 -Installer
```

Das baut wie bisher `PhotoboxBooth.exe` und `PhotoboxSync.exe` und dann:

1. `tools\third_party_notices.py` liest die tatsächlich installierten Pakete der
   Bauumgebung und schreibt `THIRD-PARTY-NOTICES.txt`. **Fehlt einem Paket der
   Lizenztext oder passt eine Lizenz nicht zur AGPL, bricht der Bau ab.**
   Die Datei kommt in die `.exe` (Verwaltung → Über → „Komponenten Dritter“),
   neben die `.exe` und ins Setup.
2. `installer\make_installer_files.py` erzeugt die Lizenzseiten (deutscher
   Hinweis + `LICENSE-EXCEPTION` + `LICENSE`), die Seite „Was dieses Programm
   tut“ (aus `tools\outbound_connections.py`), die Setup-Bilder aus dem
   Programmsymbol und `publisher.isi` aus `packaging\windows\publisher.json`.
3. `ISCC.exe` sucht das Skript auf dem PATH, unter „Programme“, im eigenen
   `%LOCALAPPDATA%\Programs` und in `C:\Users\*\AppData\Local\Programs` aller
   Konten (winget installiert Inno Setup pro Benutzer; gebaut wird vielleicht
   unter einem anderen Konto). Fehlt es, installiert das Skript es mit
   `winget install -e --id JRSoftware.InnoSetup` (sonst von Hand:
   <https://jrsoftware.org/isdl.php>). `winget` steht über ssh oder in einer
   Aufgabe nicht auf dem PATH; das Skript ruft es dann direkt aus
   `%LOCALAPPDATA%\Microsoft\WindowsApps` auf. Ohne `-Installer` braucht der
   Bau Inno Setup nicht.
4. `ISCC` baut `dist\Photobox-Setup-<Version>.exe`. Das Setup enthält immer
   beide Programme: die Box und `PhotoboxSync.exe` (im Unterordner `sync\`).
   `-Installer` zusammen mit `-BoothOnly` oder `-SyncOnly` bricht deshalb ab.
5. Für den Einzel-Download liegen daneben `dist\PhotoboxBooth-<Version>.exe`
   und `dist\PhotoboxSync-<Version>.exe` (Kopien der signierten Dateien), dazu
   `LICENSE.txt`, `LICENSE-EXCEPTION.txt`, `THIRD-PARTY-NOTICES.txt` und
   `LIESMICH.txt`. `dist\PhotoboxBooth.exe` ohne Version bleibt für das
   Box-Update.
6. `dist\SHA256SUMS.txt` enthält genau die Dateien mit Version: Setup, Box und
   Sync (die Version kommt aus `VERSION`).

`-InstallerOnly` baut nur das Setup aus den vorhandenen `dist\PhotoboxBooth.exe`
und `dist\PhotoboxSync.exe`
(die Python-Pakete der Bauumgebung werden trotzdem geprüft, deshalb dauert es
gut eine Minute).

Ein Quellbaum, der vom Mac kopiert wurde, enthält `._*`-Dateien (AppleDouble)
und `.DS_Store`; das Setup lässt sie aus (`Excludes:`).

### Herausgeber-Angaben (Impressum der Software)

`packaging\windows\publisher.example.json` liegt im Repository,
`packaging\windows\publisher.json` mit Anschrift und Kontakt nicht (git-ignoriert).
Vor einem Release-Bau die Beispieldatei kopieren und ausfüllen. Werte, die noch
dem Beispiel entsprechen, landen in keinem Build: Ohne eigene Datei nennt die
Box nur den Namen „Till Heidrich“ und die Projektadresse. Die Angaben erscheinen
in der Verwaltung unter „Über“ und als Herausgeber im Setup und in „Apps“.

## Was das Setup tut

Seiten: Sprache (Deutsch/Englisch) → Willkommen → Lizenz → Komponenten Dritter
(scrollbar) → „Was dieses Programm tut“ → Ordner → Aufgaben → Bereit
(Zusammenfassung: Aktualisierung, Datenordner, was der Dauerbetrieb ändert, was
ohne Administratorrechte übersprungen wird, WebView2) → Installation → Fertig
(„Photobox jetzt starten“).

| Aufgabe | Standard | Was passiert | Rückgängig |
|---|---|---|---|
| Start bei der Anmeldung | an | `PhotoboxBooth.exe --autostart an` als angemeldeter Benutzer: Aufgabe „Photobox Booth“ | Deinstallation, oder `--autostart aus` |
| Desktop-Verknüpfung | an | „Photobox“ auf dem Desktop | Deinstallation |
| Update-Nutzungszeit 10–4 Uhr | an (nur Administrator) | `--dauerbetrieb aktivzeit` | Deinstallation stellt die vorigen Werte her |
| Dauerbetrieb | **aus** (nur Administrator) | `--dauerbetrieb empfohlen`: kein Standby, Bildschirm/Platte/USB an, Bildschirmschoner aus, Benachrichtigungen aus, Ruhezustand aus, Schnellstart aus, kein Update-Neustart bei Anmeldung, Nutzungszeit | Deinstallation oder Verwaltung → Dauerbetrieb → „Änderungen der Box zurücknehmen“ |
| WLAN-Gastseite | **aus** (nur Administrator) | Firewall-Regel „Photobox Gastseite (WLAN)“, TCP 8766, nur private Netze, nur Geräte im eigenen Subnetz (`remoteip=localsubnet`), nur für `PhotoboxBooth.exe` | Deinstallation |
| WebView2 | nur sichtbar, wenn sie fehlt | lädt Microsofts Evergreen-Bootstrapper von `go.microsoft.com` und installiert die Laufzeit | über Windows „Apps“ |

Der Dauerbetrieb nutzt den Mechanismus des Programms selbst
(`booth/boothcore/dauerbetrieb.py`): Jeder Wert wird vor der Änderung gemerkt
(`%LOCALAPPDATA%\PhotoboxBooth\dauerbetrieb.json`), und nur das, was die Box
geändert hat, wird zurückgesetzt – auf genau den vorgefundenen Wert.

**Kiosk-Sperren (einzeln, nie mit „empfohlen“):** Drei Maßnahmen nehmen auch dem
Betreiber etwas weg und werden deshalb nur einzeln gesetzt – in der Verwaltung
unter Dauerbetrieb oder mit `PhotoboxBooth.exe --dauerbetrieb <Schlüssel>`:

| Schlüssel | Was | Rechte | Wirkt | Rückgängig |
|---|---|---|---|---|
| `randgesten` | Wischen vom Bildschirmrand (Info-Center, Widgets) aus: `HKLM\SOFTWARE\Policies\Microsoft\Windows\EdgeUI` `AllowEdgeSwipe = 0` | Administrator | sofort | „Zurücksetzen“ in der Verwaltung |
| `windows_taste` | Beide Windows-Tasten ohne Funktion: `HKLM\SYSTEM\CurrentControlSet\Control\Keyboard Layout` `Scancode Map` (REG_BINARY). Gilt für alle Tastaturen und Benutzer des Rechners; eine eigene Tastenbelegung des Betreibers wird gemerkt und zurückgeschrieben | Administrator | nach Neustart | „Zurücksetzen“, danach Neustart |
| `touchgesten` | Drei- und Vierfinger-Gesten am Touchscreen aus: `HKCU\Control Panel\Desktop` `TouchGestureSetting = 0` | Benutzer | nach erneutem Anmelden | „Zurücksetzen“, danach abmelden |

Das eigene Fenster der Box schluckt darüber hinaus Zoom- und Wischgesten im
Fenster selbst (`booth/boothcore/window.py`). Alt+Tab, Strg+Alt+Entf und die
Tastenkombinationen mit der Windows-Taste bleiben ohne die Sperre oben möglich;
Strg+Alt+Entf lässt sich bewusst nicht sperren.

**Firewall:** Die Box hört nur auf 127.0.0.1 (Port 8765). Dafür braucht es keine
Regel, und das Setup legt keine an. Nur die WLAN-Gastseite („Herunterladen ohne
Internet“, `local.json: guest_share.allowed`) öffnet einen zweiten Port im Netz;
dafür ist die abwählbare Aufgabe da. Eine abweichende `guest_share.port` braucht
eine eigene Regel.

**WebView2:** Windows 11 bringt die Evergreen-Laufzeit mit. Das Setup prüft die
Registrierungsschlüssel, die Microsoft dafür nennt (`pv` unter
`EdgeUpdate\Clients\{F3017226-…}`). Fehlt sie, bietet es den Download an.
Microsoft erlaubt ausdrücklich, den Bootstrapper zu verlinken, programmgesteuert
herunterzuladen oder mit der App zu verpacken
(learn.microsoft.com/microsoft-edge/webview2/concepts/distribution). Das Setup
lädt ihn nur, wenn die Aufgabe gewählt ist, und packt ihn nicht ein; für die
Laufzeit gelten Microsofts Lizenzbedingungen, die der Bootstrapper mitbringt.
Vor dem Start prüft das Setup die Authenticode-Signatur der geladenen Datei
(`Get-AuthenticodeSignature`): nur gültig **und** von „Microsoft Corporation“
signiert wird sie ausgeführt, sonst gelöscht, im Protokoll vermerkt und am
Ende gemeldet.
Ohne WebView2 startet die Box Chrome oder Edge als Ersatz.

**Startmenü „Photobox“:** Photobox · Photobox Verwaltung (öffnet
`http://127.0.0.1:8765/admin` im Browser; PIN wie immer) · Photobox Selbsttest
(`--doctor`) · Photobox Sync (der Sync allein, für Rechner mit dslrBooth oder
LumaBooth; das Setup startet ihn nicht und richtet keinen Autostart für ihn
ein — die Box hat ihren Sync eingebaut, eingerichtet wird er bei Bedarf mit
`PhotoboxSync.exe --install`) · Handbuch · Wenn etwas hakt · Datenschutz an der Box ·
Photobox deinstallieren.

**Protokoll:** Jeder Schritt steht in `%LOCALAPPDATA%\PhotoboxBooth\logs\installer.log`
(und im Inno-Protokoll, wenn `/LOG=` angegeben ist).

## Aktualisieren

Das neue Setup einfach über das alte laufen lassen. Es

1. beendet eine laufende Box geordnet (`PhotoboxBooth.exe --quit`, wie
   Verwaltung → Beenden: Kamera wird freigegeben, der Wächter bleibt unten).
   Erst wenn sie nach 30 s noch läuft: Aufgabe beenden, Prozesse beenden.
   Lässt sie sich gar nicht beenden, bricht das Setup ab, ohne etwas zu ändern
   (Rückgabewert 7);
2. legt die bisherige `PhotoboxBooth.exe` als `PhotoboxBooth.exe.vorher` daneben;
3. ersetzt die Programmdateien; der Datenordner bleibt unberührt;
4. richtet die Aufgabe neu ein (sie zeigt danach auf den Installationsordner).

**Bestehende Box in `C:\Fotobox`** (eingerichtet mit `Alles einrichten.bat`):
Das Setup erkennt `C:\Fotobox\PhotoboxBooth.exe` und schlägt diesen Ordner vor.
So bleibt alles gültig, was auf `C:\Fotobox` zeigt (Aufgabe, ssh-Skripte,
`Einrichten.bat`, ein `EDSDK`-Ordner daneben, ein Ablageordner
`C:\Fotobox\Bilder`). Ein älteres `PhotoboxSync.exe`, `LIESMICH.txt` und alte Dateien dort
fasst das Setup nicht an (sein eigener Sync liegt in `sync\`), auch beim Deinstallieren nicht. Wer stattdessen nach
`C:\Program Files\Photobox` will: Ordner im Setup ändern; die Aufgabe wird dann
dorthin umgestellt, die alte `.exe` in `C:\Fotobox` bleibt liegen.
`C:\Fotobox` dürfen normale Benutzer beschreiben. Deshalb startet das Setup
`PhotoboxBooth.exe` von dort nie mit Administratorrechten: Nutzungszeit,
Dauerbetrieb, die Aufgabe „als Administrator“, `--quit` beim Deinstallieren
und `--uninstall` werden übersprungen; die Bereit-Seite und das Protokoll sagen
das, und weil `aktivzeit` vorausgewählt ist, meldet das Setup am Ende „Einige
Schritte haben nicht geklappt … Nutzungszeit (übersprungen …)“ – das ist
erwartet, die Box selbst ist dann trotzdem aktualisiert. Wer diese Schritte
will, installiert nach `C:\Program Files\Photobox`. Bei einer Installation
„nur für mich“ wird nichts mit Administratorrechten gestartet; dort laufen
`--quit` und `--uninstall` beim Deinstallieren aus jedem Ordner.

### Zurückrollen

```powershell
& "C:\Fotobox\PhotoboxBooth.exe" --quit
Copy-Item "C:\Fotobox\PhotoboxBooth.exe.vorher" "C:\Fotobox\PhotoboxBooth.exe" -Force
schtasks /Run /TN "Photobox Booth"
```

(Pfad bei einer Installation nach `C:\Program Files\Photobox` entsprechend, dann
in einer Administrator-Konsole.) Das ältere Setup erneut auszuführen geht auch.

## Ohne Fenster (ssh)

```powershell
.\Photobox-Setup-0.9.1.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- `
  /DIR="C:\Fotobox" /TASKS="autostart,desktopicon,aktivzeit" `
  /LOG="C:\Fotobox\setup-0.9.1.log"
echo $LASTEXITCODE
```

* `/TASKS=` legt die Auswahl fest (Namen: `autostart`, `desktopicon`,
  `aktivzeit`, `dauerbetrieb`, `lanfreigabe`, `webview2`). Ohne `/TASKS` gilt die
  Auswahl der letzten Installation, beim ersten Mal die Voreinstellung.
* `/CURRENTUSER` installiert ohne Administratorrechte nach
  `%LOCALAPPDATA%\Programs\Photobox`; `aktivzeit`, `dauerbetrieb` und
  `lanfreigabe` entfallen dann.
* **Kein Probelauf neben einer laufenden Box.** Auch mit `/CURRENTUSER`, eigenem
  `/DIR=` und `/TASKS=""` ist das Setup nicht abgeschottet: Es beendet jede
  laufende `PhotoboxBooth.exe` (egal aus welchem Ordner), stellt die Aufgabe
  „Photobox Booth“ um (`--autostart aus` löscht sie, wenn `autostart` nicht
  gewählt ist) und schreibt in `%LOCALAPPDATA%\PhotoboxBooth\logs\installer.log`.
  Die Deinstallation ruft `--uninstall` auf und entfernt die Aufgabe ebenfalls.
  Probeinstallationen gehören auf einen anderen Rechner oder in eine VM.
* `/START=1` startet die Box danach über die Aufgabe. Ohne den Schalter wird
  sie nur dann wieder gestartet, wenn sie vorher lief.
* Das Setup startet den Rechner **nie** neu, auch nicht still. Nötig ist ein
  Neustart nur für „Schnellstart aus“ (Dauerbetrieb), und der wirkt einfach
  beim nächsten Neustart.
* `/LANG=de` oder `/LANG=en` wählt die Sprache.

Rückgabewerte (von Inno Setup): 0 fertig · 1 Setup konnte nicht starten ·
2 vor der Installation abgebrochen · 3 schwerer Fehler vor der Installation ·
4 schwerer Fehler während der Installation · 5 vom Benutzer abgebrochen ·
6 von außen beendet · 7 Vorbereitung gescheitert (z. B. Box ließ sich nicht
beenden; nichts wurde geändert) · 8 Vorbereitung verlangt einen Neustart (kommt
hier nicht vor). Schritte nach dem Kopieren (Aufgabe, Dauerbetrieb, Firewall,
WebView2), die nicht klappen, ändern den Rückgabewert nicht – sie stehen mit
`--` in `logs\installer.log`.

Über ssh läuft das Setup als der angemeldete Benutzer der Box (OpenSSH unter
Windows: ohne UAC-Abfrage). Der Datenordner, in den das Setup protokolliert und in
dem der Dauerbetrieb seine gemerkten Werte ablegt, ist der dieses Benutzers. Ein
anderes Administratorkonto als das der Box würde die benutzerbezogenen
Einstellungen (Bildschirmschoner, Benachrichtigungen) in seinem eigenen Profil
setzen – also immer als Box-Benutzer installieren.

## Deinstallieren

Windows → Apps → Photobox → Deinstallieren, oder Startmenü → „Photobox
deinstallieren“. Das

1. beendet die Box (wie oben),
2. ruft `PhotoboxBooth.exe --uninstall` auf: entfernt die Aufgabe „Photobox
   Booth“ und die alte Firewall-Regel von `--install`, setzt jede
   Windows-Einstellung zurück, die die Box geändert hat (nur diese, auf den
   vorgefundenen Wert),
3. entfernt die Firewall-Regel „Photobox Gastseite (WLAN)“, falls angelegt,
4. löscht Programmdateien, Verknüpfungen und `PhotoboxBooth.exe.vorher`,
5. **fragt**, ob der Datenordner `%LOCALAPPDATA%\PhotoboxBooth` mit allen
   Aufnahmen gelöscht werden soll. Voreinstellung: **Nein**. Weitere
   Ablageordner und USB-Sticks fasst es nie an.

Still: `"C:\Fotobox\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`
behält den Datenordner; `/DELETEDATA=1` löscht ihn.

## Signieren

Solange kein Zertifikat da ist (SignPath Foundation / Certum sind beantragt),
sind Setup und `.exe` unsigniert. Dann zeigt Windows:

* beim Herunterladen im Browser eventuell „wird selten heruntergeladen“,
* beim Start den blauen SmartScreen-Dialog „Der Computer wurde durch Windows
  geschützt“ mit „Herausgeber: Unbekannter Herausgeber“ – über **Weitere
  Informationen → Trotzdem ausführen** geht es weiter,
* bei der UAC-Abfrage „Unbekannter Herausgeber“ statt „Till Heidrich“.

Sobald es ein Zertifikat gibt, eine der beiden Variablen setzen und mit `-Sign`
bauen (dann ist die Signatur Pflicht, ein unsignierter Bau bricht ab):

```powershell
# Zertifikat im Windows-Zertifikatspeicher (Karte, Token, Certum SimplySign):
$env:PHOTOBOX_SIGN_THUMBPRINT = "<Fingerabdruck>"
# optional: $env:PHOTOBOX_SIGN_TIMESTAMP = "http://time.certum.pl"
# oder ein Cloud-Dienst mit eigener Kommandozeile, {file} = die Datei:
$env:PHOTOBOX_SIGN_COMMAND = "C:\Tools\sign.cmd {file}"
.\packaging\windows\build.ps1 -Installer -Sign
```

Signiert werden `PhotoboxBooth.exe`, `PhotoboxSync.exe`, das Setup und das
Deinstallationsprogramm (Inno `SignTool`/`SignedUninstaller`). Im
Cloud-Befehl keine Anführungszeichen verwenden – lieber eine kleine `.cmd`
dazwischen, die den Dienst aufruft. Auch signiert baut sich der
SmartScreen-Ruf erst mit der Zeit auf.

<a id="pruefsumme"></a>
## Prüfsumme prüfen

Neben dem Setup liegt `SHA256SUMS.txt`. Vor dem Ausführen:

```powershell
Get-FileHash .\Photobox-Setup-0.9.1.exe -Algorithm SHA256
```

Der angezeigte Wert muss (ohne Rücksicht auf Groß-/Kleinschreibung) mit der Zeile
in `SHA256SUMS.txt` übereinstimmen. Unter Linux/macOS: `sha256sum -c SHA256SUMS.txt`
bzw. `shasum -a 256 -c SHA256SUMS.txt`. Die Prüfsummendatei gehört auf die
Release-Seite, nicht nur neben die Datei auf demselben Server.

## Später: die Canon-Ausgabe

Im Setup gibt es einen Ausgaben-Schalter (`#define Edition`). Erlaubt ist nur
`community`; jeder andere Wert bricht den Bau ab. Eine spätere Canon-Ausgabe
(nur für die Website, gebaut außerhalb dieses Repositorys) würde dort einen
`EDSDK`-Ordner in den Datenordner legen und eine zweite Lizenzseite mit Canons
Bedingungen zeigen. Das setzt eine Weitergabeerlaubnis von Canon voraus; bis dahin
enthält weder das Repository noch das Setup eine Canon-Datei.

## Testliste auf dem Windows-Rechner

Siehe den Bericht zum Branch `feat/installer` (Abschnitt „Manuelle Tests“); kurz:
frische Installation (Administrator), Installation „nur für mich“, Aktualisierung
der bestehenden `C:\Fotobox`-Box still über ssh, Zurückrollen, Deinstallieren mit
„Daten behalten“ und mit „Daten löschen“, stilles Deinstallieren.
