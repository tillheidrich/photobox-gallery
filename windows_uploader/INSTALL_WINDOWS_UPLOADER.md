# Windows Mini-PC: Auto-Upload (DSLRBooth → Online Galerie)

Ziel: Auf dem Fotobox-PC läuft ein kleiner Uploader im Hintergrund und lädt automatisch jede neue Datei (Foto/Print/GIF/Video) in dein Event hoch.

## 1) Ordner anlegen

Empfohlen:

- `C:\FotoboxUploader\`

Kopiere den Inhalt dieses Ordners `windows_uploader/` dorthin.

## 2) Python installieren

1. Python 3.11+ installieren (Windows Installer).
2. Wichtig:
   - **"Add Python to PATH"** anhaken
   - Danach: PC einmal neu starten

Test:

```bat
python --version
pip --version
```

## 3) Uploader Abhängigkeiten installieren

In `C:\FotoboxUploader\`:

```bat
pip install -r requirements.txt
```

## 4) config.json erstellen

1. `config.example.json` kopieren zu `config.json`
2. In `config.json` anpassen:
   - `server_url`: deine Galerie-URL (HTTPS)
   - `upload_token`: Upload Token aus dem Event im Admin
   - `watch[0].path`: DSLRBooth Output-Ordner (oder Unterordner)

Beispiel:

```json
{
  "server_url": "https://galerie.deine-domain.de",
  "upload_token": "...",
  "watch": [{"path": "C:/DSLRBooth/Output", "kind": "auto"}]
}
```

### Typische Watch-Pfade

- DSLRBooth: oft `C:\DSLRBooth\Output\` oder ein Event-Output Pfad.

Tipp: Wenn du getrennte Ordner hast, kannst du mehrere Watch-Einträge machen:

```json
"watch": [
  {"path": "C:/DSLRBooth/Output/Photos", "kind": "photo"},
  {"path": "C:/DSLRBooth/Output/Prints", "kind": "print"},
  {"path": "C:/DSLRBooth/Output/GIFs", "kind": "gif"},
  {"path": "C:/DSLRBooth/Output/Videos", "kind": "video"}
]
```

## 5) Testlauf

Doppelklick auf:
- `StartUploader.bat`

Dann ein Testfoto im Output erzeugen und prüfen:
- Im Browser: `https://galerie.deine-domain.de/gallery/<slug>`

Wenn OK, beende das Fenster (STRG+C) oder schließe.

## 6) Autostart beim Windows-Login (Task Scheduler)

So läuft es wirklich "vollautomatisch".

1. Windows -> **Task Scheduler** öffnen
2. "Aufgabe erstellen" (nicht "Einfache Aufgabe")
3. Reiter **Allgemein**
   - Name: `FotoboxUploader`
   - "Unabhängig von der Benutzeranmeldung ausführen" (optional)
   - "Mit höchsten Privilegien" (empfohlen)
4. Reiter **Trigger**
   - Neu… -> "Bei Anmeldung" (des Fotobox-Benutzers)
5. Reiter **Aktionen**
   - Neu… -> Programm/Skript: `C:\FotoboxUploader\StartUploader_NoPause.bat`
6. Reiter **Bedingungen**
   - Haken bei "Nur starten, wenn Computer im Netzbetrieb" raus (damit es auch mobil klappt)
7. OK speichern.

Test:
- Abmelden/Anmelden oder PC neu starten.
- Prüfen, ob im `C:\FotoboxUploader\uploader.log` neue Einträge kommen.

## 7) Wenn vor Ort kein Internet da ist

Kein Problem:
- DSLRBooth speichert lokal.
- Der Uploader versucht weiter.
- Sobald Internet wieder da ist, lädt er automatisch nach.

