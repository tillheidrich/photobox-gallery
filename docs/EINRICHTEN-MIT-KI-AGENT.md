# Box-PC per Fernzugriff von einem KI-Agenten einrichten lassen

Ein KI-Agent (ein Programm, das Befehle ausführt, zum Beispiel ein Coding-Assistent)
kann den Box-PC einrichten, ohne dass jemand davor sitzt: Programm einspielen,
Autostart setzen, prüfen, ob alles läuft. Das spart Wege, öffnet aber für die Zeit
der Einrichtung eine Tür zum PC. Diese Seite sagt, wie du die Tür sauber auf- und
wieder zumachst, was der Agent tun darf und was nicht.

Sie gilt für jeden Agenten und jeden Anbieter. Sie nennt keinen Rechner beim Namen;
Platzhalter wie `<IP-ADRESSE-DES-BOX-PCS>` setzt du selbst ein.

**Die Faustregel:** Fernzugriff gibt es nur während der Einrichtung, nur mit
Schlüssel statt Passwort, nur im eigenen Netz, und am Ende wird er ausgeschaltet
und geprüft. Wer das nicht will, richtet die Box mit
[`ANLEITUNG-BOX-EINRICHTEN.md`](ANLEITUNG-BOX-EINRICHTEN.md) von Hand ein.

## Wann sich das lohnt, wann nicht

Es lohnt sich, wenn der Box-PC an einem Ort steht, an den du nicht ständig willst,
und wenn die Schritte wiederholbar sind (mehrere Boxen, Umstellung auf eine neue
Fassung). Es lohnt sich **nicht** für:

* die Dinge, die ein Mensch am PC tun muss: Kamera anschließen, Papier einlegen, Handy
  zum Koppeln halten, das Canon-SDK beschaffen, eine PIN wählen,
* einen PC, der direkt am offenen Internet hängt,
* alles, was du nicht rückgängig machen könntest.

## Was vorher stimmen muss

1. **Windows 10 oder 11** auf dem Box-PC, ein Benutzer mit Administratorrechten.
2. Der PC und der Rechner, von dem der Agent arbeitet, sind **im selben Netz**
   (oder über ein VPN, das du selbst betreibst). Der PC wird **nicht** ins
   offene Internet freigegeben, kein Port am Router weitergeleitet.
3. Du hast eine **Rückfalllinie**: Du kommst notfalls an den PC (Bildschirm,
   Tastatur oder jemand vor Ort), falls der Fernzugriff ausfällt.
4. Das Programm liegt bereit: eine fertig gebaute `PhotoboxBooth.exe` oder der
   Quellordner samt `packaging\windows\Fotobox bauen.bat`. Der Bau braucht
   Windows ([`../packaging/windows/README.md`](../packaging/windows/README.md)).
5. Das **Canon-SDK** liegt als entpackter Ordner auf dem PC oder einem Stick. Es
   gehört nicht ins Projekt und nicht in einen Chat.

## Den Fernzugriff einschalten (nur für die Einrichtung)

Das macht ein **Mensch** am PC, einmal, als Administrator, in PowerShell. Die Befehle
stammen aus der Dokumentation von Microsoft zu OpenSSH unter Windows; prüfe sie dort
(Quellen unten), bevor du sie ausführst.

```powershell
# 1. OpenSSH-Server installieren und starten
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
```

Absichtlich **kein** `Set-Service … Automatic`: Der Dienst soll nach einem
Neustart nicht von selbst wieder laufen. Kommt der PC mitten in der Einrichtung
neu hoch, startest du ihn von Hand wieder.

```powershell
# 2. Den öffentlichen Schlüssel des Agenten eintragen (Administratorkonto).
#    Die Datei liegt bei Administratoren hier, nicht im Benutzerordner:
$schluessel = '<HIER-DEN-OEFFENTLICHEN-SCHLUESSEL-EINFUEGEN>'
Add-Content -Force -Path $env:ProgramData\ssh\administrators_authorized_keys -Value $schluessel
icacls.exe "$env:ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
```

Der **öffentliche** Schlüssel darf in einen Chat; der **private** Schlüssel bleibt
auf dem Rechner des Agenten und geht nirgendwohin. Ein eigener Schlüssel nur für
diese Einrichtung ist besser als ein Schlüssel, der überall passt, denn du löschst
ihn danach.

```powershell
# 3. Passwort-Anmeldung ausschalten: in %ProgramData%\ssh\sshd_config
#    PasswordAuthentication no
Restart-Service sshd
```

4. **Die Firewall-Regel auf das eigene Netz beschränken.** Beim Installieren legt
   Windows die Regel `OpenSSH-Server-In-TCP` an (Port 22, eingehend). Ändere ihre
   Gegenstelle auf dein lokales Netz, zum Beispiel `192.168.0.0/24`, oder besser
   auf die eine Adresse des Rechners, von dem der Agent arbeitet:

   ```powershell
   Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -RemoteAddress '<ADRESSE-DES-AGENTEN-RECHNERS>'
   ```

5. Verbindung prüfen, vom Rechner des Agenten, mit seinem Schlüssel:

   ```
   ssh -i <PFAD-ZUM-PRIVATEN-SCHLUESSEL> <BENUTZER>@<IP-ADRESSE-DES-BOX-PCS> whoami
   ```

Weitere Hinweise zu Schlüsseln und zur Serverkonfiguration stehen in den
Microsoft-Seiten unter „Quellen“.

## Was der Agent darf

* Dateien in `C:\Fotobox\` und im Datenordner der Box lesen und schreiben.
* `PhotoboxBooth.exe` mit den Schaltern `--install`, `--install-edsdk <Ordner>`,
  `--doctor`, `--window-selftest`, `--quit` aufrufen.
* Windows-Einstellungen ändern, die die Einrichtung ausdrücklich verlangt
  (Autostart, kein Standby), und jede Änderung vorher benennen.
* Die Box über ihre lokale Adresse abfragen (`/api/state`) und Protokolle lesen.
* Den Ablauf und das Ergebnis jedes Schritts aufschreiben.

## Was der Agent nicht darf

* **Keine Passwörter, PINs, Schlüssel, Kopplungscodes oder Tokens** in eine Nachricht,
  einen Chat, ein Protokoll oder eine Datei im Projekt schreiben, auch nicht
  „nur zum Test“. Was er braucht, gibt ihm ein Mensch am PC.
* **Keine PIN der Box wählen oder ändern.** Das ist Sache des Betreibers am Gerät.
* **Nicht koppeln, ohne dass der Betreiber den Code vergleicht.** Beim Koppeln
  mit dem Handy sieht der Betreiber QR-Code und Code am Bildschirm der Box; der
  Agent muss sie nicht kennen und soll sie nicht abschreiben.
* **Keine Bilder von Gästen** öffnen, kopieren oder an Dritte schicken.
* **Keine anderen Rechner im Netz** ansprechen, kein Netzwerk durchsuchen.
* **Nichts löschen**, was die Einrichtung nicht selbst angelegt hat. Eine alte `.exe`
  bleibt als `.exe.vorher` liegen.
* **Keine Firewall-Regeln, Benutzer oder Dienste anlegen**, die nicht in dieser
  Anleitung stehen, ohne vorher zu fragen.
* **Nicht weitermachen, wenn etwas Unerwartetes passiert** (unbekannte Benutzer,
  fremde Prozesse, ein Programm, das niemand installiert hat). Anhalten und melden.

Der Betreiber kann dem Agenten diese Liste als Anweisung mitgeben.

## Die Schritte, die der Agent geht

Jeder Schritt hat einen Test. Ohne bestandenen Test geht der Agent nicht
weiter, sondern meldet, was er gesehen hat.

1. **Verbindung.** `whoami` und `hostname` über ssh. Erwartet: der Administrator-Benutzer. Den
   Rechnernamen schreibt der Agent nicht in Dateien, die ins Projekt gehören.
2. **Bestand.** Windows-Version, freier Platz (`Get-PSDrive C`), läuft schon eine Box
   (`Get-Process PhotoboxBooth`)? Eine laufende Box ordentlich beenden:
   `PhotoboxBooth.exe --quit` (kein `taskkill`, wenn es vermeidbar ist).
3. **Bauen oder holen.** Entweder die fertige `PhotoboxBooth.exe` auf den PC kopieren
   oder auf dem PC bauen: `packaging\windows\Fotobox bauen.bat`. Test: Die Datei
   `packaging\windows\dist\PhotoboxBooth.exe` existiert. Die Prüfsumme notieren
   (`Get-FileHash -Algorithm SHA256`), damit später klar ist, welche Fassung läuft.
4. **Einspielen.** Nach `C:\Fotobox\` kopieren. Eine vorhandene Datei bleibt als
   `PhotoboxBooth.exe.vorher` erhalten.
5. **Einrichten.** `PhotoboxBooth.exe --install` aus einer Administrator-Sitzung.
   Test: Die Ausgabe hat **kein** `--` hinter einem Schritt (das hieße: übersprungen,
   weil keine Administratorrechte).
6. **Canon-SDK.** `PhotoboxBooth.exe --install-edsdk <ORDNER-DES-SDK>`. Test: Die Ausgabe
   nennt die gewählte 64-Bit-Datei.
7. **Selbsttest.** `PhotoboxBooth.exe --doctor`. Alles Rote lesen und melden. Der falsche
   Druckertreiber („Microsoft IPP Class Driver“) wird hier gemeldet; ihn
   tauscht ein Mensch (Treiber des Herstellers installieren), nicht der Agent.
8. **Starten.** Die Box über die geplante Aufgabe oder den Desktop-Eintrag starten,
   nicht mit einem eigenen Dauerprozess. Dann `PhotoboxBooth.exe --window-selftest`.
   Test: Rückgabewert 0 heißt, das Fenster lebt.
9. **Zustand prüfen.** Von dem PC aus (der Agent führt es über ssh aus) die lokale
   Adresse abfragen, in PowerShell:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8765/api/state | ConvertTo-Json -Depth 4
   ```

   Gelesen werden: der Zustand der Kamera, des Druckers, `window` (läuft das eigene
   Fenster) und `supervisor` (läuft der Wächter, wie oft ist die Box neu gestartet).
   Der Agent meldet die Werte, er rät keine.
10. **Koppeln und Event.** Das macht der **Betreiber** am Gerät oder mit seinem
    Handy ([`ANLEITUNG-BOX-EINRICHTEN.md`](ANLEITUNG-BOX-EINRICHTEN.md), Schritte 8 und 9).
    Der Agent wartet und prüft danach in `/api/state`, dass die Galerie als
    gekoppelt erscheint.
11. **Probelauf.** Aus der Galerie ein Testfoto und einen Testdruck als Auftrag
    schicken (Fernwartung, „Testfoto“, „Testdruck“), das Ergebnis in der Galerie
    ansehen. Ein Mensch beurteilt den Abzug.
12. **Bericht.** Was gemacht wurde, welche Prüfsumme läuft, was rot war und noch
    offen ist. Ohne Geheimnisse.
13. **Fernzugriff ausschalten**, siehe nächster Abschnitt, und diesen Schritt
    **nicht** dem Agenten allein überlassen: Der Betreiber prüft das Ergebnis selbst.

## Den Fernzugriff wieder ausschalten

Das ist der Teil, der am häufigsten vergessen wird. Eine offene Tür, die niemand
mehr braucht, ist ein Risiko, das nur noch Kosten hat.

**Der normale Weg:** Im Ordner `packaging\windows` die Datei
**`Fernzugriff ausschalten.bat`** mit Rechtsklick → „Als Administrator ausführen“.
Sie ist als Gegenstück zu den Schritten oben gedacht: Dienst anhalten und
abschalten, die Firewall-Regel entfernen, den eingetragenen Schlüssel löschen.
Lies ihre Ausgabe zu Ende.

**Von Hand**, wenn die Datei fehlt oder du sichergehen willst (PowerShell als
Administrator):

```powershell
Stop-Service sshd
Set-Service -Name sshd -StartupType Disabled
Remove-Item "$env:ProgramData\ssh\administrators_authorized_keys" -Force
Disable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP'
# Wer den Server ganz entfernen will (danach Windows neu starten):
Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
```

**Prüfen, dass es wirklich aus ist:**

* `Get-Service sshd` zeigt „Stopped“ (oder der Dienst ist verschwunden).
* Von einem **anderen** Rechner im Netz: `ssh <BENUTZER>@<IP-ADRESSE-DES-BOX-PCS>` meldet,
  dass die Verbindung abgelehnt wird oder nicht zustande kommt.
* `Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'` zeigt
  „NotPresent“, wenn du den Server entfernt hast.
* Der Schlüssel, den du dem Agenten gegeben hast, ist auch auf **seiner** Seite gelöscht.

Dieselbe Zeile steht auf der Liste „Vor jeder Veranstaltung ausschalten“ in
[`ANLEITUNG-BOX-EINRICHTEN.md`](ANLEITUNG-BOX-EINRICHTEN.md).

## Risiken, ehrlich

| Risiko | Was dagegen hilft | Was übrig bleibt |
|---|---|---|
| Jemand im selben Netz erreicht den ssh-Dienst | Schlüssel statt Passwort, Firewall-Regel auf eine Adresse, Dienst nur während der Einrichtung | Während der Einrichtung ist die Tür offen. |
| Der Schlüssel des Agenten gelangt in falsche Hände | Eigener Schlüssel nur für diese Einrichtung, danach löschen | Bis zum Löschen gilt er. |
| Der Agent macht etwas, das du nicht wolltest | Die „Darf nicht“-Liste als Anweisung mitgeben, jeden Schritt einzeln freigeben, Bericht lesen | Ein Agent kann sich irren. Er hat Administratorrechte. |
| Ein Geheimnis landet im Chat oder Protokoll | Geheimnisse nie an den Agenten geben, Kopplung und PIN am Gerät | Was einmal im Chat stand, ist dort gespeichert. Ändere es. |
| Der Dienst bleibt nach der Einrichtung an | `Fernzugriff ausschalten.bat`, Prüfung von einem anderen Rechner, Punkt auf der Liste vor jeder Veranstaltung | Menschen vergessen. |
| Der PC fällt mitten in der Einrichtung aus | Rückfalllinie (Schritt „Vorher“), alte `.exe` bleibt als `.vorher` | Eine halbe Einrichtung ist möglich. Ein Neustart des Dienstes ist Handarbeit. |
| Bilder von Gästen sind auf dem PC | Der Agent öffnet sie nicht; die Einrichtung braucht keine echten Aufnahmen | Wer ssh hat, kann sie technisch lesen. Deshalb **vor** dem ersten Event, nicht danach. |

Richte eine Box mit echten Gästebildern nicht aus der Ferne ein oder um. Wer
nachträglich Fernzugriff braucht, schaltet ihn wie oben für genau diese Aufgabe
ein und danach wieder aus.

## Quellen

Geprüft am 15.11.2026. Die Seiten ändern sich; was hier steht, ersetzt nicht ihren
Wortlaut.

* Microsoft: OpenSSH-Server unter Windows installieren und starten:
  <https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse>
  (auf dieser Seite stehen die Firewall-Regel `OpenSSH-Server-In-TCP` und der
  Hinweis, nach dem Entfernen Windows neu zu starten, wenn der Dienst in Benutzung
  war; die PowerShell-Befehle zum Installieren und Entfernen stehen dort hinter
  Karteikarten und wurden nicht einzeln geprüft).
* Microsoft: Schlüsselverwaltung, Datei `administrators_authorized_keys` und ihre
  Zugriffsrechte:
  <https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement>
* Microsoft: Konfiguration des OpenSSH-Servers (hier stehen die Einstellungen in
  `sshd_config`, darunter die zur Passwort-Anmeldung):
  <https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration>
