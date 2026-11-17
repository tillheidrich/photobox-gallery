# Eigenes Fenster statt Chrome

Stand: 15.11.2026. Die Box zeigt ihre Oberfläche in einem eigenen
Programmfenster, wie dslrBooth: ein Programm, ein Fenster, kein Browser
dahinter. Bis zu dieser Fassung startete `PhotoboxBooth.exe` den Server und
dann Google Chrome im Kiosk-Modus.

## Was sich für dich am Gerät ändert

* Nach dem Anmelden erscheint sofort ein dunkler Bildschirm „Photobox startet …“,
  kein weißes Fenster. Sobald die Box bereit ist, kommt der Startbildschirm.
* Chrome muss nicht mehr installiert sein. Das Fenster nutzt die
  WebView2-Laufzeit von Microsoft, die zu Windows 11 gehört.
* Kein Browser-Kram mehr: keine Frage nach „Seiten wiederherstellen“, keine
  Willkommensseiten, keine Updates von Chrome mitten am Abend, kein
  Kontextmenü, kein Zoomen (Zwei-Finger-Geste, Strg+Mausrad, Strg+Plus/Minus),
  kein Drucken- oder Speichern-Dialog, keine Wischgeste „Zurück“.
* Ein zweiter Doppelklick auf „Fotobox“ startet keine zweite Box, sondern
  holt das laufende Fenster nach vorn.
* Die Konsole (schwarzes Fenster) wird beim Start versteckt. Das Protokoll
  steht weiter in `%LOCALAPPDATA%\PhotoboxBooth\logs\booth.log`.
* Bleibt die Oberfläche hängen oder wird sie weiß (Absturz der Anzeige), lädt
  das Fenster sie nach spätestens 15 Sekunden von selbst neu – aber nie mitten
  in einem Durchgang (Countdown, Aufnahme, Verarbeitung, Druck): dann wartet
  es, bis der Durchgang fertig ist (höchstens 3 Minuten). Stirbt der Server der
  Box, beendet sie sich mit einem Fehlercode, und der Wächter startet sie neu.
* Fragt die Verwaltung etwas nach („Wirklich löschen?“), lädt das Fenster nicht
  neu, solange die Frage offen ist (bis zu 10 Minuten).

## Wie die Teile zusammenspielen

Die Aufgabe „Photobox Booth“ startet bei der Anmeldung `PhotoboxBooth.exe`
ohne Argumente. Diese .exe ist zuerst der **Wächter** (`boothcore/waechter.py`):
Er startet dieselbe .exe noch einmal als **Box** und wartet auf sie.

| Teil | Gehört ihm |
|---|---|
| Wächter (Elternprozess) | die Konsole (er versteckt sie), `logs/waechter.log`, die Neustarts |
| Box (Kindprozess) | Webserver, Kamera, Drucker, das eigene Fenster samt Taskleisten-Eintrag und Symbol, die Sperre „nur eine Box“ (`window/instance.lock`) und die Befehlsleitung (`show`, `selftest`, `quit`) |

Wie die Box endet, entscheidet, was der Wächter tut:

| Code | Wann | Wächter |
|---|---|---|
| 0 | Verwaltung → „Beenden“, `--quit`, Update-Übergabe | endet auch, nichts startet neu |
| 3 | Verwaltung → „Neustart“, Fernauftrag `restart_app`, tote Web-Ansicht | startet sofort neu |
| 4 | Es lief schon eine Box; ihr Fenster wurde nach vorn geholt | endet, nichts zu tun |
| sonst | Absturz, Webserver gestorben (1), hängende fremde Sperre (1) | startet nach 3 s, 6 s, 12 s … höchstens 60 s neu |

Ein zweiter Start der .exe (Desktop-Symbol, `schtasks /Run`) fragt zuerst über
die Befehlsleitung, ob schon eine Box läuft. Antwortet sie, holt sie ihr Fenster
nach vorn, und der zweite Wächter endet sofort – kein zweiter Kindprozess, der
erste Wächter merkt nichts. Ohne Wächter (`python -m boothcore`, Mac) startet
„Neustart“ wie bisher selbst einen Nachfolger; `start-booth.bat`/`.command`
hören bei 0 und 4 auf.

**Eigenes Fenster scheitert immer wieder:** Jeder Start des Fensters zählt in
`window/fenster-fehlversuche.json` mit, bis es 2 Minuten lang lebt; dann ist der
Zähler wieder 0. „Neustart“ und „Beenden“ aus der Verwaltung zählen nicht.
Nach 3 Starts in Folge, die das nicht geschafft haben (Absturz, tote
Web-Ansicht, keine Oberfläche), startet die Box gleich mit Chrome und schreibt
eine Zeile ins Protokoll; `/api/state` → `window.fallback_reason` sagt warum.
Nach 12 Stunden oder nach `Umstellen-auf-eigenes-Fenster.ps1` versucht sie es
wieder mit dem eigenen Fenster.

**Abschied an die Galerie** (Warnung „Box schweigt“, `feat/alarm-akku-vormerken`):
Nur die Box verabschiedet sich, nie der Wächter, und höchstens einmal
(höchstens 2 s, dann die Kamera):

| Was passiert | Notiz an die Galerie |
|---|---|
| Verwaltung → Beenden, `--quit` (Code 0) | `quit`, kommt nicht wieder |
| Neustart, `restart_app`, tote Web-Ansicht (Code 3) | `restart`, kommt wieder |
| Webserver gestorben (Code 1) | `error`, kommt wieder – die Galerie schreibt „mit Fehler beendet … startet neu“, nicht „bewusst heruntergefahren“ |
| Absturz, `kill -9`, Stromausfall | nichts – die Galerie warnt nach ihrer Frist |
| Windows meldet ab / fährt herunter | `windows_logoff`/`windows_shutdown`, kommt nicht wieder |
| Wächter beendet (Task-Manager, Aufgabe gestoppt, SIGTERM) | `signal`, kommt nicht wieder; die Box merkt es nach höchstens 2 s und beendet sich geordnet |
| Alt+F4 | nichts, es passiert ja nichts |

Wie die Box vom Sitzungsende erfährt: Ein Prozess mit Fenster (die Box mit
eigenem Fenster lädt user32) bekommt von Windows kein Konsolenereignis für
Abmelden und Herunterfahren. Darum (1) hört der Wächter es – er lädt bewusst
kein user32, deshalb versteckt die Box die Konsole und nicht er – und gibt es
über die Befehlsleitung als `end:<grund>` an die Box weiter (wartet bis 4 s),
und (2) fragt das Fenster beim Schließwunsch Windows, ob die Sitzung endet
(`SM_SHUTTINGDOWN`): dann lässt es sich schließen statt das Herunterfahren
aufzuhalten, und die Box beendet sich geordnet. Was bleibt: Beendet Windows die
Prozesse hart (Update-Neustart mit Zwang, „Trotzdem herunterfahren“ nach der
Frist) oder fehlt beides, geht keine Notiz raus – die Galerie warnt dann wie
nach einem Stromausfall.

**Chrome wieder öffnen** (keine Seitenabfrage seit 90 s → Chrome neu, höchstens
alle 5 Minuten) gilt nur, wenn Chrome die Box zeigt. Im eigenen Fenster ist der
Herzschlag das Lebenszeichen; Chrome wird dort nie über das Fenster gelegt.

## Wie du rauskommst

* **Normal:** Ecke oben links 3 Sekunden halten → PIN → Verwaltung →
  **„Beenden“**. Die Box gibt die Kamera frei und schließt das Fenster.
* **Alt+F4 und Schließen in der Taskleiste** sind im Kioskbetrieb gesperrt.
  Ein Gast mit Tastatur soll die Box nicht mit zwei Tasten schließen können.
* **Ohne Bildschirm** (ssh): `C:\Fotobox\PhotoboxBooth.exe --quit` – wie
  „Beenden“: Kamera frei, Code 0, der Wächter bleibt aus.
* **Notausgang**, wenn die Oberfläche nicht reagiert: **Strg+Alt+Entf** →
  Task-Manager → **alle** Einträge „PhotoboxBooth“ beenden (Wächter und Box).
  Beendest du nur die Box, startet der Wächter sie nach wenigen Sekunden neu –
  so gewollt, das ist der Absturzschutz. Beendest du nur den Wächter, beendet
  sich die Box nach höchstens 2 s von selbst geordnet (Kamera frei). Danach startet die Box erst bei der
  nächsten Anmeldung wieder (oder über „Fotobox“ auf dem Desktop).
* Zum Arbeiten am Gerät: `PhotoboxBooth.exe --window-debug` – das Fenster
  lässt sich dann schließen, Rechtsklick und Entwicklerwerkzeuge (F12) gehen.

## Umschalten

In `%LOCALAPPDATA%\PhotoboxBooth\local.json`:

```json
"window": {"mode": "own", "screen": -1, "on_top": false, "debug": false, "hide_console": true}
```

| Schlüssel | Bedeutung |
|---|---|
| `mode` | `own` = eigenes Fenster (Standard) · `chrome` = wie früher Chrome im Kiosk-Modus · `none` = nur der Server, kein Fenster |
| `screen` | `-1` = Hauptbildschirm, `0`, `1`, … = dieser Bildschirm |
| `on_top` | Fenster immer im Vordergrund. Aus lassen, wenn du die Windows-Bildschirmtastatur brauchst (siehe unten) |
| `debug` | Wie `--window-debug` |
| `hide_console` | Konsole beim Start verstecken |

Einmalig über die Befehlszeile: `PhotoboxBooth.exe --window own|chrome|none`.
`--no-kiosk` heißt weiter „kein Fenster“, ebenso `"kiosk": false`.

Die Einstellung lässt sich nur an der Box ändern, nicht aus der Galerie: Ein
falscher Wert aus der Ferne könnte einen schwarzen Bildschirm hinterlassen.

**Rückfall auf Chrome:** Fehlt die WebView2-Laufzeit oder kommt das Fenster
innerhalb von 60 Sekunden nicht hoch, öffnet die Box von selbst Chrome im
Kiosk-Modus (wenn Chrome oder Edge installiert ist) und schreibt genau eine
Zeile ins Protokoll: „Eigenes Fenster nicht möglich (…) – die Box öffnet Chrome
im Kiosk-Modus.“

## Aus der Ferne prüfen (ssh)

Über ssh siehst du den Bildschirm nicht. Drei Wege:

1. `C:\Fotobox\PhotoboxBooth.exe --window-selftest` – fragt die laufende Box.
   Sie schreibt `fenster-selbsttest.json` und `fenster-bildschirm.png` in
   `%LOCALAPPDATA%\PhotoboxBooth\` und gibt den Bericht aus: Modus, WebView2-
   Fassung, Fenster da, letzter Herzschlag, Größe und Pixelverhältnis der
   Seite, ob Ton ohne Berührung starten darf, Neuladungen, Rückfallgrund.
   Rückgabe 0 = Fenster lebt.
2. `GET http://127.0.0.1:8765/api/state` → Abschnitt `window`
   (`mode`, `backend`, `backend_version`, `alive`, `last_heartbeat`,
   `heartbeat_age`, `reloads`, `reload_deferred`, `fallback_reason`,
   `failures_in_a_row`, `screen`, `page`) und Abschnitt `supervisor`
   (`watched` = läuft unter dem Wächter, `restarts` seit dem Start des
   Wächters, `last_exit_code` und `last_reason` = `crash`/`restart`/…,
   `watcher_pid`, `watching_since`).
3. Bildschirmfoto mit PIN:
   `POST /api/admin/login {"pin": "…"}` → Token, dann
   `GET /api/admin/window/screenshot.png` mit Kopfzeile `X-Admin-Token`.
   Das Bild liegt danach auch als `fenster-bildschirm.png` im Datenordner.

## Bekannte Grenzen

* **Bildschirmtastatur von Windows:** Die Gast-Oberfläche hat für Grüße und
  E-Mail-Adressen eine eigene Tastatur, braucht die von Windows also nicht.
  In der Verwaltung (echte Eingabefelder) blendet Windows 11 die
  Bildschirmtastatur ein wie in Chrome – nur, wenn kein physisches Keyboard
  angeschlossen ist und die Touch-Tastatur in den Windows-Einstellungen nicht
  abgeschaltet ist. Mit `on_top: true` kann das Fenster die Tastatur verdecken.
* **Links nach außen** (Projektseite, Quelltext in der Verwaltung) öffnen sich
  im Standardbrowser von Windows, nicht im Box-Fenster.
* **Datei-Auswahl** in der Verwaltung (Canon-SDK als ZIP, Medien, Vorlagen)
  öffnet den normalen Windows-Dialog „Öffnen“, wie in Chrome.
* **Herunterladen** ist im Fenster abgeschaltet. Die Box bietet Gästen keine
  Downloads am Gerät an; Fotos gehen über QR-Code, Galerie, Mail oder Stick.
* **Wischgesten am Bildschirmrand** (Windows-Startmenü, Info-Center): die
  gehören Windows, nicht dem Fenster. Abschalten über die Gruppenrichtlinie
  „Edge-Wischen zulassen“ = deaktiviert.
* **macOS:** Das eigene Fenster läuft auf dem Mac nur mit `pip install pywebview`
  und nicht zusammen mit dem Canon-SDK (beide brauchen dort den Haupt-Thread);
  dann nimmt die Box Chrome. Linux: noch nicht eingerichtet, die Box nimmt Chrome.
* Am Box-PC noch nicht erprobt (Stand 15.11.2026): Die Prüfliste steht unten.

## Prüfliste am Box-PC

| # | Was | Erwartet |
|---|---|---|
| 1 | Anmelden (Autologon) | dunkler Bildschirm „Photobox startet …“, nach wenigen Sekunden der Startbildschirm, Vollbild ohne Rahmen, keine Konsole sichtbar |
| 2 | Antippen, Durchgang mit Kamera | reagiert wie in Chrome, keine Tipp-Verzögerung |
| 3 | Buzzer-Taste und Maustaste | lösen aus wie vorher |
| 4 | Live-Bild | flüssig wie in Chrome (gleiches MJPEG) |
| 5 | Startvideo / Video mit Ton | läuft ohne Antippen mit Ton; Selbsttest meldet `autoplay_sound: allowed` |
| 6 | Zwei Finger spreizen, Strg+Mausrad, Strg+Plus | kein Zoom |
| 7 | Wischen vom linken Rand in die Seite | kein „Zurück“ |
| 8 | Alt+F4 | nichts passiert (Protokoll: „Fenster schließen abgelehnt“) |
| 9 | Task-Manager: `msedgewebview2.exe` (alle) beenden | nach höchstens ~15–25 s lädt die Oberfläche neu; `/api/state` → `window.reloads` steigt |
| 10 | „Fotobox“ auf dem Desktop noch einmal starten | keine zweite Box; das Fenster kommt nach vorn |
| 11 | Neustart des Rechners | Box kommt von selbst, wie Punkt 1 |
| 12 | 4K-Bildschirm mit 150 % / 200 % Skalierung | alles scharf, Größen wie in Chrome; Selbsttest zeigt `page.dpr` 1,5 / 2 |
| 13 | Verwaltung → Beenden | Fenster schließt, Kamera frei (Neustart der Box geht ohne Kamera aus/an); im Task-Manager kein „PhotoboxBooth“ mehr |
| 14 | Task-Manager: den Box-Prozess (der mit dem Fenster) beenden | nach ~3 s wieder da; `/api/state` → `supervisor.restarts` +1, `last_reason: crash` |
| 15 | Verwaltung → Neustart | nach wenigen Sekunden wieder da; `supervisor.last_exit_code: 3` |
| 16 | Taskleiste | genau ein Eintrag „Photobox“ mit dem Box-Symbol, keine Konsole |
| 17 | Verwaltung: eine Aktion mit Rückfrage (z. B. Stick freigeben), Frage 30 s offen lassen | Seite lädt nicht neu; Protokoll ohne „Keine Lebenszeichen“ |
| 18 | Foto mit Druck; während der Drucker druckt `msedgewebview2.exe` beenden | kein Neuladen, bis der Druck durch ist (Protokoll: „Neuladen wartet“, `window.reload_deferred: true`), der Druck kommt; danach lädt die Oberfläche neu |
| 19 | Fenster mit einem anderen Programm verdecken, 6 min warten | kein Neuladen (`window.reloads` bleibt) |
