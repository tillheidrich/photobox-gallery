# Windows Uploader – Photobox Gallery v2

Automatischer Datei-Uploader für Windows, macOS und Linux.
Überwacht einen Ordner und lädt neue Fotos automatisch in die Galerie hoch.

## Voraussetzungen

- **Python 3.10+** auf dem Fotobox-PC installiert
- **Ein Gerät im Admin-Panel angelegt** (generiert den API-Key)
- **requests-Bibliothek**: `pip install requests`

## Installation

### 1. Python-Abhängigkeiten installieren

```bash
pip install requests
```

### 2. Konfiguration anlegen

Kopiere `config.example.json` zu `config.json`:

```bash
cp config.example.json config.json
```

### 3. config.json konfigurieren

Öffne die Datei und passe folgende Einstellungen an:

```json
{
  "server_url": "http://localhost:5000",
  "api_key": "YOUR_DEVICE_API_KEY",
  "event_slug": "hochzeit-2026",
  "watch_folder": "C:\\Fotobox\\Bilder",
  "upload_interval": 2,
  "supported_extensions": [".jpg", ".jpeg", ".png", ".gif", ".mp4"],
  "delete_after_upload": false,
  "log_file": "uploader.log"
}
```

**Erklärung:**

- `server_url`: URL deines Photobox-Servers (z.B. `http://192.168.1.100:5000`)
- `api_key`: API-Key aus dem Admin-Panel (siehe unten)
- `event_slug`: URL-Slug des Events (z.B. `hochzeit-2026`)
  - Findest du in der URL der Galerie: `/gallery/hochzeit-2026`
- `watch_folder`: Ordner, den die Fotobox bespielt (z.B. `C:\DSLRBooth\Output`)
- `upload_interval`: Wie oft der Uploader scannt (in Sekunden)
- `supported_extensions`: Welche Dateitypen hochgeladen werden
- `delete_after_upload`: Sollen Dateien nach Upload gelöscht werden?
- `log_file`: Logdatei für Debugging

### 4. Uploader starten

#### Windows

Führe `StartUploader.bat` aus (doppelklick):

```batch
python photobox_uploader_v2.py --config config.json
```

Oder von der Kommandozeile:

```cmd
python photobox_uploader_v2.py --config config.json
```

#### macOS / Linux

```bash
python3 photobox_uploader_v2.py --config config.json
```

Das Skript läuft dann im Hintergrund und überwacht den Ordner kontinuierlich.

## API-Key erstellen

1. Öffne Admin-Panel → **Geräte**
2. Klicke auf **+ Neues Gerät**
3. Gib einen Namen ein (z.B. "Fotobox PC")
4. Aktiviere das Gerät
5. Kopiere den **API-Key**
6. Füge den Key in `config.json` ein:
   ```json
   "api_key": "device_xxx..."
   ```

Der API-Key wird bei jedem Upload im HTTP-Header `X-API-Key` mitgeschickt.

## Auto-Event-Modus (optional)

Wenn du mehrere Events gleichzeitig supportest, kannst du den Auto-Event-Modus nutzen:

```json
{
  "server_url": "http://localhost:5000",
  "api_key": "YOUR_DEVICE_API_KEY",
  "watch_folder": "C:\\DSLRBooth\\Events",
  "auto_event": true,
  "event_name_template": "Event {date}",
  "upload_interval": 2,
  "supported_extensions": [".jpg", ".jpeg", ".png", ".gif", ".mp4"],
  "delete_after_upload": false,
  "log_file": "uploader.log"
}
```

Damit der Auto-Event-Modus funktioniert:
- Der `event_name_template` wird mit dem aktuellen Datum ersetzt
- Für jeden neuen Subfolder wird automatisch ein Event erstellt
- **Hinweis**: Dies erfordert Admin-API-Support im Backend

## Troubleshooting

### "requests not found"
```bash
pip install requests
```

### "Cannot find watch_folder"
Prüfe den Pfad in `config.json`. Windows-Pfade können backslashes brauchen:
```json
"watch_folder": "C:\\Users\\Admin\\Pictures"
```

### "Upload error: HTTP 401"
Der API-Key ist ungültig oder nicht mehr aktiv. Prüfe im Admin-Panel, ob das Gerät aktiv ist.

### "Event not found"
Der `event_slug` existiert nicht. Prüfe die URL der Galerie im Browser.

### Log-Datei
Schau in `uploader.log` für Details:
```bash
tail -f uploader.log
```

## Dateien

- `photobox_uploader_v2.py` – Das Uploader-Skript (neu für v2)
- `config.example.json` – Beispielkonfiguration
- `config.auto.json` – Beispiel für Auto-Event-Modus
- `config.json` – Deine Konfiguration (erstelle diese Datei selbst)
- `uploader.log` – Log-Datei mit Upload-Details
- `StartUploader.bat` – Windows-Startdatei
- `README_V2.md` – Diese Dokumentation

## API-Details

Der Uploader sendet POST-Requests an:

```
POST http://server:5000/api/upload
```

Mit folgenden Parametern:

- **Header**: `X-API-Key: <device_api_key>`
- **Form-Daten**:
  - `event_slug` = slug des Events
  - `file` = Bilddatei

Beispiel mit curl:

```bash
curl -X POST \
  -H "X-API-Key: device_xxx" \
  -F "event_slug=hochzeit-2026" \
  -F "file=@foto.jpg" \
  http://localhost:5000/api/upload
```

## Support

Für Fehler oder Fragen:
- Schau in `uploader.log`
- Prüfe `config.json`
- Teste die Server-Verbindung: `curl http://server:5000`
- Überprüfe Netzwerkfirewall und Router-Einstellungen

---

**Photobox Gallery v2** – https://github.com/photobox-gallery
