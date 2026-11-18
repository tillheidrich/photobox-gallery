# Windows: zwei Dateien, sonst nichts

Auf dem Fotobox-Rechner (Windows 10, Touchscreen, kleine Tastatur) soll niemand
Befehle tippen. Deshalb kommt alles als `.exe`.

| Datei | Wofür |
|---|---|
| `PhotoboxBooth.exe` | Die Fotobox: Kamera, Aufnahme, Druck, Sync, Verwaltung – in ihrem eigenen Programmfenster (WebView2, siehe `docs/EIGENES-FENSTER.md`). Ohne Argumente gestartet ist sie zuerst der Wächter, der die Box als Kindprozess startet und nach einem Absturz wiederbringt |
| `Umstellen-auf-eigenes-Fenster.ps1` | Eine bestehende Box auf den aktuellen Stand bringen: neue `.exe` einspielen, eigenes Fenster, Aufgabe, Nutzungszeit (auch über ssh, beliebig oft) |
| `PhotoboxSync.exe` | Nur der Sync — für Rechner, auf denen andere Fotobox-Software läuft |

## Das Setup (der normale Weg zur Box)

`build.ps1 -Installer` baut zusätzlich `dist\Photobox-Setup-<Version>.exe`
(Inno Setup, Skript in `installer\photobox.iss`) und `dist\SHA256SUMS.txt`.
Das Setup enthält immer Box **und** Sync. Beide gibt es auch einzeln, mit
Version im Namen: `PhotoboxBooth-<Version>.exe` und `PhotoboxSync-<Version>.exe`.
`SHA256SUMS.txt` listet genau diese drei Dateien. `dist\PhotoboxBooth.exe`
ohne Version bleibt daneben liegen, weil das Box-Update genau diese Datei
kopiert.
Das Setup installiert die Box, die Aufgabe für den Start bei der Anmeldung, die
Startmenü-Einträge und auf Wunsch die Windows-Einstellungen für den
Dauerbetrieb – alles umkehrbar, der Datenordner bleibt. Bau, stille Parameter
für ssh, Aktualisieren einer Box in `C:\Fotobox`, Zurückrollen, Signieren:
[`docs/INSTALLER.md`](../../docs/INSTALLER.md). Die Skripte unten bleiben für
Entwickler und für den Bau-Rechner.

## Einmal bauen (auf einem Windows-Rechner)

`Fotobox bauen.bat` doppelklicken. Das Skript holt sich Python, baut beide
Dateien und legt sie in `packaging\windows\dist\`. Der Bau muss auf Windows
laufen — eine Windows-`.exe` entsteht nicht auf dem Mac. In der Cloud baut
`.github/workflows/windows-release.yml` mit demselben `build.ps1` auf einem
Windows-Runner von GitHub, für Releases und die (noch nicht aktive) Signatur
über die SignPath Foundation: [`docs/CODE-SIGNING.md`](../../docs/CODE-SIGNING.md).

Installiert wird mit `constraints.txt` aus dem Projektstamm
(`pip install -r requirements.txt -c constraints.txt`, das erledigt
`build.ps1`): genau die Fassungen, mit denen die Tests zuletzt grün waren.
Eine Ausnahme: `pywin32` steht nicht darin, weil die Referenzumgebung kein
Windows ist. Nach dem ersten erprobten Bau die Fassung aus `pip freeze` in
`constraints.txt` nachtragen.

Für das eigene Fenster installiert `build.ps1` über `booth\requirements.txt`
auch `pywebview` (mit `pythonnet`). Fehlt es beim Bau, entsteht trotzdem eine
`.exe` – die öffnet dann Chrome und schreibt das ins Protokoll. Die `.exe`
trägt eine Versionsangabe (Produkt „Photobox“, Firma „Till Heidrich“), die
Explorer und Task-Manager anzeigen.

Jede `.exe` enthält `LICENSE`, `LICENSE-EXCEPTION`, `THIRD_PARTY.md` und
`docs/lizenzen/`. Das Canon-SDK ist nie enthalten.

## Auf die Fotobox bringen

1. `PhotoboxBooth.exe` auf den Rechner kopieren, zum Beispiel nach `C:\Fotobox\`.
2. `Einrichten.bat` daneben legen und **als Administrator** doppelklicken,
   dann `1` wählen. Das richtet ein: Autostart bei Anmeldung, kein Standby,
   kein Bildschirm-Abschalten, kein USB-Energiesparen, Desktop-Verknüpfung und
   eine Firewall-Regel nur für diesen Rechner.
3. Ab jetzt reicht Doppelklick — oder der Rechner startet die Box beim Anmelden
   von selbst.

**Warum Administrator.** Autostart und Verknüpfung gehen ohne; „Standby aus"
(`powercfg`) und die Firewall-Regel (`netsh`) nicht. Als normaler Benutzer
läuft `--install` trotzdem durch und schreibt hinter die übersprungenen
Schritte ein `--`. Wer das überliest, hat einen Rechner, der mitten im Abend
einschläft. Die Ausgabe also lesen.

`Einrichten.bat` ist nur eine Fassade vor den Schaltern `--install`,
`--install-edsdk`, `--doctor` und `--uninstall`. Ein Rechtsklick auf eine
`.exe` bietet kein Feld für Argumente — ohne diese Datei müsste am
Fotobox-Rechner doch jemand eine Eingabeaufforderung öffnen, und genau das
soll hier niemand.

## Canon EDSDK

Die Canon-Bibliothek darf nicht im Repository liegen und steckt deshalb auch
nicht in der `.exe`. Das SDK beim Canon Developer Programme holen, entpacken,
dann `Einrichten.bat` → `2` (oder von Hand
`PhotoboxBooth.exe --install-edsdk <entpackter Ordner>`).

Das ist nicht dasselbe wie „die DLLs hinkopieren". Im Canon-Archiv liegen
mehrere `EDSDK.dll` nebeneinander — 32 Bit, 64 Bit, mehrere Sprachordner. Die
falsche ergibt beim Start nur „%1 ist keine zulässige Win32-Anwendung", eine
Meldung, die niemandem sagt, was zu tun ist. `--install-edsdk` liest den
PE-Kopf, wählt die zur `.exe` passende Architektur, nimmt `EdsImage.dll` mit
und legt beides in den Datenordner. Dort sucht die Box zuerst; ein Ordner
`EDSDK\` neben der `.exe` wird weiterhin gefunden.

## Wo die Daten liegen

`%LOCALAPPDATA%\PhotoboxBooth` — Aufnahmen, Warteschlange, Protokoll,
Einstellungen. Ein Update ist ein Austausch der `.exe`; die Daten bleiben.
