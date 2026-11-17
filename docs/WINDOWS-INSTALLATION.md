# Fotobox unter Windows einrichten

Für den Box-Rechner (Windows 10 oder 11, Touchscreen). Am Ende startet die Box
beim Anmelden von selbst im Vollbild. Stand: 14.11.2026.

**Was an echter Hardware geprüft ist und was nicht**, steht ganz unten. Die
Abnahme in Abschnitt 8 gehört dazu: Erst wenn sie durchläuft, ist die Box
bereit für eine Veranstaltung.

## 1. Was bereitliegen muss

* Der Box-Rechner mit Internet (nur für die Einrichtung).
* Canon-Kamera mit USB-Kabel und Netzteil (Akku-Attrappe), Objektiv ausgefahren.
* Drucker mit USB-Kabel, Papier und Farbband.
* Das Canon EDSDK als entpackter Ordner (Canon Developer Programme). Es darf
  nicht im Projekt liegen und steckt nicht in der `.exe`.
* Ein USB-Stick für die Sicherung.
* Die **WebView2-Laufzeit** von Microsoft. Sie gehört zu Windows 11 und ist
  fast immer schon da; die Einrichtung prüft das und holt sie sonst über
  winget. Die Box zeigt sich damit in ihrem **eigenen Programmfenster**
  (siehe [Eigenes Fenster statt Chrome](EIGENES-FENSTER.md)). Google Chrome
  braucht es nicht mehr; ist es installiert, dient es nur als Ersatz, falls
  das eigene Fenster einmal nicht startet.
* Für eine Galerie: ihre Adresse und ein Handy, mit dem du dich dort anmelden
  kannst (oder ein Kopplungscode aus der Galerie, siehe „Box mit der Galerie
  koppeln“).

## Der normale Weg: das Setup

Seit 15.11.2026 gibt es ein Setup: **`Photobox-Setup-<Version>.exe`**. Es ist
der normale Weg für jeden Box-Rechner; die Skripte unten bleiben für
Entwicklerinnen und für Rechner, auf denen gebaut wird.

1. Setup starten (Doppelklick). Solange es nicht signiert ist, zeigt Windows
   „Der Computer wurde durch Windows geschützt“: **Weitere Informationen →
   Trotzdem ausführen**. Vorher die Prüfsumme vergleichen (siehe
   [INSTALLER.md](INSTALLER.md#pruefsumme)).
2. Sprache wählen, Lizenz, Komponenten Dritter und „Was dieses Programm tut“
   lesen, Ordner bestätigen (Standard `C:\Program Files\Photobox`; liegt schon
   eine Box in `C:\Fotobox`, wird sie dort aktualisiert).
3. Aufgaben wählen: Start bei der Anmeldung (an), Desktop-Verknüpfung (an),
   Update-Nutzungszeit 10–4 Uhr (an), Dauerbetrieb (aus – für eine Box, die
   unbeaufsichtigt steht, einschalten), WLAN-Gastseite (aus), WebView2 (nur
   wenn sie fehlt).
4. Fertig: „Photobox jetzt starten“.

Danach weiter bei Abschnitt 4 (Canon-SDK). Alles Weitere zum Setup – ohne
Fenster über ssh, Aktualisieren, Deinstallieren, Signieren – steht in
[INSTALLER.md](INSTALLER.md); was die Box speichert und wohin sie sich
verbindet, in [DATENSCHUTZ-BOX.md](DATENSCHUTZ-BOX.md).

## Kurzweg für Entwickler: ein Doppelklick

Wer den vorbereiteten Ordner „Fotobox-Windows-Paket“ auf einem Stick hat
(Quelle, Canon-SDK, Profil nebeneinander):

1. Stick in den Box-Rechner, Ordner `photobox\packaging\windows` öffnen.
2. **`Alles einrichten.bat`** mit Rechtsklick „Als Administrator ausführen“.

Das erledigt die Abschnitte 2 bis 4 und legt Grundeinstellungen an (Kamera
über das Canon-SDK, zweiter Ordner, Stick-Sicherung an, Profil vom Stick).
Danach weiter bei Abschnitt 5. Das Skript ist am 14.11.2026 geschrieben und
noch auf keinem Windows-Rechner gelaufen: die Ausgabe des ersten Laufs lesen.

## 2. Programm bauen

Die `.exe` entsteht nur auf einem Windows-Rechner.

1. Das Projekt auf den Rechner holen: im Browser die Projektseite öffnen und
   als ZIP laden, dann entpacken, zum Beispiel nach `C:\Fotobox-Quelle\`.
2. Im Ordner `packaging\windows` die Datei **`Fotobox bauen.bat`**
   doppelklicken. Das Skript holt Python, baut und legt
   `PhotoboxBooth.exe` und `PhotoboxSync.exe` nach `packaging\windows\dist\`.
3. Den Ordner `C:\Fotobox\` anlegen. `PhotoboxBooth.exe` und `Einrichten.bat`
   (liegt in `packaging\windows`) hineinkopieren.

## 3. Einrichten

1. `Einrichten.bat` mit Rechtsklick **„Als Administrator ausführen“**, dann `1`.
   Das richtet ein: Autostart, kein Standby, Bildschirm bleibt an, kein
   USB-Energiesparen, Desktop-Verknüpfung, Firewall-Regel nur für diesen Rechner.
2. Die Ausgabe lesen. Steht hinter einem Schritt `--`, wurde er übersprungen
   (dann lief das Skript nicht als Administrator).

## 4. Canon-SDK

1. `Einrichten.bat` starten, `2` wählen.
2. Den entpackten Canon-Ordner angeben (in das Fenster ziehen geht). Die Box
   sucht die passende 64-Bit-Fassung selbst heraus.

## 5. Kamera

* Moduswahlrad auf **M**. Mit Blitz etwa 1/125 s, f/5,6 bis f/8, ISO 200 bis 400.
* Automatische Abschaltung: aus. Bildrückschau: aus.
* Per USB anschließen, einschalten. EOS Utility darf nicht laufen (es nimmt
  der Box die Kamera weg): in EOS Utility den Autostart abschalten oder es
  gar nicht installieren.

## 6. Drucker

1. Den Windows-Treiber des Druckers installieren und den Drucker anschließen.
2. In Windows unter „Drucker & Scanner“ → Druckeinstellungen: Papier Postkarte
   (100 × 148 mm), **randlos**.
3. Eine Windows-Testseite drucken. Erst wenn die kommt, weiter.

### Druckertreiber

Windows 11 richtet einen SELPHY am USB-Kabel von selbst mit dem Treiber
„Microsoft IPP Class Driver“ ein. Damit hat der SELPHY CP1300 am Box-PC
(15.11.2026) keinen einzigen Auftrag angenommen. Mit dem Canon-Treiber von
canon.de (Support-Seite des Druckers, „Treiber und Downloads“, Windows 11)
druckte er sofort, und die Box konnte jeden Auftrag bis zum fertigen Blatt
verfolgen.

1. Den Canon-Treiber von canon.de laden und installieren.
2. Unter „Drucker & Scanner“ den Drucker öffnen → Druckereigenschaften →
   Erweitert: Treiber muss „Canon SELPHY CP1300“ heißen, nicht „Microsoft IPP
   Class Driver“. Sonst den von Windows angelegten Drucker entfernen und den
   SELPHY mit dem Canon-Treiber neu hinzufügen.
3. Die Selbstdiagnose (`PhotoboxBooth.exe --doctor`, auch am Ende von
   `alles-einrichten.ps1`) warnt unter „Druckmodul“, solange der falsche
   Treiber eingestellt ist.

## 7. Box starten und einstellen

1. „Fotobox“ auf dem Desktop starten. Es erscheint „Photobox startet …“ auf
   dunklem Grund, dann der Startbildschirm im eigenen Fenster. Alt+F4 ist
   gesperrt; beenden über Verwaltung → „Beenden“, im Notfall über den
   Task-Manager ([Eigenes Fenster statt Chrome](EIGENES-FENSTER.md)).
2. Die Ecke oben links 3 Sekunden halten → PIN `2580` → eigene PIN festlegen
   (mindestens 4 Ziffern).
3. In der Verwaltung:
   * **Kamera:** Anschluss `edsdk`.
   * **Drucker:** Anschluss `windows`, den Drucker aus der Liste wählen,
     Blätter je Kassette eintragen (SELPHY: 18).
   * **Ablage:** „Zweiter Ordner auf diesem Rechner“, zum Beispiel
     `D:\Fotobox`. Stick einstecken und „Laufend auf den USB-Stick sichern“
     einschalten.
   * **Galerie:** koppeln, siehe „Box mit der Galerie koppeln“ unten. Ohne
     Galerie den Haken „Ohne Galerie betreiben“ setzen.
   * „Speichern & neu starten“.
4. Profil der Veranstaltung: kommt aus der Galerie. Ohne Galerie unter „Alle
   Einstellungen“ einstellen (Veranstaltung, Modi, Drucken, Vorlagen).
5. **„Box prüfen“** drücken. Alles Rote beheben.

Die Erklärung aller Funktionen steht in der Verwaltung unter **Hilfe · Help**.

### Box mit der Galerie koppeln

Drei Wege, alle in der Box-Verwaltung unter **Galerie-Server**. Danach steht
dort, mit welcher Galerie die Box gekoppelt ist, wie sie dort heißt und
welches Event ihr zugewiesen ist. Ein Neustart ist nicht nötig.

| Weg | An der Box | In der Galerie | Wann |
|-----|------------|----------------|------|
| **Mit dem Handy** | „Mit dem Handy koppeln“ (beim ersten Mal die Adresse der Galerie eintragen). Die Box zeigt einen QR-Code und einen Code. | QR-Code mit dem Handy scannen, anmelden (mit zweitem Faktor), prüfen, dass der Code auf dem Handy derselbe ist wie an der Box, Box wählen oder „Neue Box anlegen“, optional Event, „Koppeln“. | Der Normalfall. Nichts abzutippen. Die Anfrage gilt 10 Minuten. |
| **Kopplungscode** | Code in „Kopplungscode“ eintragen, „Koppeln“. | Unter „Boxen“ bei der Box „Kopplungscode erzeugen“. Kurzer Code (Adresse und `ABCD-EFGH`) oder langer `PBX1-…`-Code. | Wenn kein Handy zur Hand ist. Kurzer Code: 15 Minuten, einmal. Der lange Code geht auch ohne Netz. |
| **`kopplung.txt`** | Datei mit dem Code in den Datenordner der Box legen (oder neben die `.exe`), Box starten. Nach dem Einlösen wird die Datei gelöscht; klappt es nicht, bleibt sie liegen und der Grund steht im Protokoll. | Wie beim Kopplungscode. | Box ohne Tastatur, Code kommt per USB-Stick. Nur, solange die Box noch nicht gekoppelt ist. |

Beim Handy-Weg sieht das Handy nie den Schlüssel der Box: Die Box holt ihn
selbst ab, mit einem Geheimnis, das nur sie kennt. QR-Code und Code allein
öffnen nichts.

## 8. Abnahme vor einer Veranstaltung

Der Reihe nach, ohne etwas zu überspringen:

1. „Testfoto“ in der Verwaltung: Bild kommt.
2. Ein Foto, eine Collage, ein GIF über den Bildschirm: jedes Ergebnis erscheint.
3. Je ein Abzug: kommt, randlos, richtig herum. Den QR-Code scannen.
4. Stick: In der Verwaltung steht „zuletzt gesichert“. Stick abziehen, am
   anderen Rechner ansehen, wieder einstecken.
5. Zweiter Ordner: die Dateien liegen dort.
6. Netzwerkkabel ziehen oder WLAN aus: drei Durchgänge machen, Netz wieder an,
   die Bilder erscheinen in der Galerie.
7. Kamera aus- und einschalten: die Box findet sie ohne Neustart wieder.
8. Papierkassette herausziehen und drucken: die Box meldet es. Kassette
   wieder rein, der Abzug kommt.
9. Rechner neu starten: Die Box erscheint von selbst im Vollbild.
10. Dauerlauf, mindestens eine Stunde, besser über Nacht. In einer
    Eingabeaufforderung im Projektordner:

    ```
    py tools\soak.py --url http://127.0.0.1:8765 --hours 9 --every 60 --no-print
    ```

    Am Ende steht eine Zeile mit Läufen, Fehlern und Speicher. Null Fehler ist
    das Ziel. Das Protokoll liegt in `soak.csv`.

## 9. Wenn die Kamera nicht auslöst

* Die Box sagt den Grund, wenn die Kamera ihn nennt (Objektiv eingefahren,
  steht auf Film, Karte fehlt).
* „Die Kamera ist beschäftigt“: Kamera aus- und wieder einschalten.
* Kommt trotz Live-Bild nie ein Foto: Die Box drückt den Auslöser seit dem
  14.11.2026 anders (erst scharfstellen, dann halten). Der alte Weg ist eine
  Einstellung entfernt: in `%LOCALAPPDATA%\PhotoboxBooth\local.json` im
  Abschnitt `"camera"` die Zeile `"edsdk_release": "classic"` ergänzen und die
  Box neu starten.

## Eine ältere Box (mit Chrome) umstellen

Neue `.exe` bauen (oder `Alles einrichten.bat`, das erledigt auch den nächsten
Schritt), dann in PowerShell – geht auch über ssh als derselbe Benutzer, ohne
dass jemand am Bildschirm sitzt:

```
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\Umstellen-auf-eigenes-Fenster.ps1 -NeueExe dist\PhotoboxBooth.exe -WebView2Installieren -Neustart
```

Das beendet die laufende Box geordnet (`--quit`, eine alte `.exe` hart),
legt die neue `.exe` nach `C:\Fotobox` (die alte bleibt als
`PhotoboxBooth.exe.vorher`), prüft WebView2, stellt `window.mode` auf `own`,
setzt gespeicherte Fehlversuche des Fensters zurück, richtet die Aufgabe
„Photobox Booth“ auf die `.exe` ohne Argumente aus (sie startet den Wächter,
der die Box startet und nach einem Absturz wiederbringt) samt Einstellungen
(keine zweite Instanz, Neustart bei Startfehler, kein Zeitlimit), setzt die
Update-Nutzungszeit auf 10–4 Uhr, räumt alte Chrome-Kiosk-Verknüpfungen in den
Datenordner, startet die Box über die Aufgabe neu und gibt den Selbsttest
(`PhotoboxBooth.exe --window-selftest`, Rückgabe 0 = Fenster lebt) und
`/api/state` (`window`, `supervisor`) aus. Beliebig oft ausführbar.

Die Nutzungszeit und das Wegräumen von Verknüpfungen für alle Benutzer brauchen
Administratorrechte. Ohne sie überspringt das Skript diese Schritte mit einer
Zeile, wie sie sich nachholen lassen (`PhotoboxBooth.exe --dauerbetrieb aktivzeit`
in einer Administrator-Konsole). Ein Administrator, der sich per ssh anmeldet,
hat sie bereits.

Chrome bleibt auf Wunsch der Ersatz: `"window": {"mode": "chrome"}` in
`local.json`, oder von selbst, wenn das eigene Fenster nicht startet
(siehe [Eigenes Fenster statt Chrome](EIGENES-FENSTER.md)).

## 10. Update und Daten

* Daten liegen in `%LOCALAPPDATA%\PhotoboxBooth` (Aufnahmen, Warteschlange,
  Einstellungen, Protokoll).
* Update: neue `PhotoboxBooth.exe` über die alte kopieren, oder die Galerie
  bietet es an und die Box tauscht beim nächsten Start. Die Daten bleiben.
* Rückgängig: `Einrichten.bat` → `4`.

## 11. Signieren, bevor die `.exe` verkauft oder verteilt wird

Ohne Signatur zeigt Windows beim ersten Start „unbekannter Herausgeber“. Auf
der eigenen Box stört das nicht. Für einen Download im Shop braucht es ein
Code-Signing-Zertifikat. Das kauft der Herausgeber selbst, mit Ausweisprüfung.

* **Certum (Polen, EU):** Code Signing als Standard-Zertifikat, auf den Namen
  einer Person oder Firma, Schlüssel in der Cloud (SimplySign) oder auf Karte.
* **SignPath Foundation:** kostenlos für Open-Source-Projekte mit
  öffentlichem Repository und anerkannter Lizenz. Auf Antrag.
* **Azure Artifact Signing (Microsoft):** für Firmen in der EU, für
  Einzelpersonen nur in den USA und Kanada.

Auch mit Signatur warnt SmartScreen bei einer neuen Datei zunächst, bis genug
Leute sie geladen haben. Das gilt für alle drei Wege.

Ist das Zertifikat im Windows-Zertifikatsspeicher eingerichtet, signiert der
Bau von selbst:

```
set PHOTOBOX_SIGN_THUMBPRINT=<Fingerabdruck des Zertifikats>
"Fotobox bauen.bat"
```

Dafür muss `signtool.exe` da sein (Windows SDK). Der Bau prüft jede Signatur
und bricht ab, wenn sie nicht stimmt.

## Stand der Prüfung (15.11.2026)

Am echten Box-PC (i5-6200U, 8 GB, Windows 11, EOS M50, SELPHY CP1300):

| Was | Geprüft |
|---|---|
| Ablauf Aufnahme → Galerie, ohne Netz, nach Stromausfall | ja, automatisch (Generalprobe, 21 Schritte, mit Testkamera) |
| Windows: Bau der `.exe` | ja, am Box-PC am 15.11. |
| Canon EOS M50 über EDSDK unter Windows | ja: verbunden und ausgelöst, 1,7–2,4 s je Aufnahme |
| Uhrzeit der Kamera beim Verbinden stellen | ja, am Box-PC |
| Autostart nach Neustart | ja, drei Neustarts |
| Canon SELPHY CP1300 unter Windows | ja: zwei Blätter mit dem Canon-Treiber gedruckt, Aufträge bis zum Ende verfolgt; mit „Microsoft IPP Class Driver“ kein Druck (siehe Druckertreiber) |
| Druck ohne win32ui (ohne Visual-C++-Laufzeit) | **nein**, erst im Code; am Box-PC noch zu prüfen |
| Touchscreen | **nein** |
| Blitz | **nein** |
| Galerie koppeln | **nein** |
| FTP-Ziel | **nein** |
| USB-Sicherung | mit einem Ordner als Stick; am Box-PC lief sie mit echtem Stick, startete aber alle 15–30 s neu (seit 15.11. behoben, dort noch zu prüfen) |
| Dauerlauf 9 Stunden | am Mac mit Testkamera gestartet am 14.11., Ergebnis im Wiki |
