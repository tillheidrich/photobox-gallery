# PhotoBox Gallery v2 — Windows Uploader Client

## Übersicht

Der **Windows Uploader** ist eine automatisierte Client-Anwendung, die auf dem Fotobox-PC (oder jedem anderen PC) läuft und neue Fotos automatisch in die Photobox Gallery v2 hochlädt. Das System besteht aus zwei parallelen Implementierungen und einer System-Tray-Integration.

### Wofür braucht man den Windows Uploader?

- **Automatische Foto-Synchronisation**: Dateien aus einem überwachten Ordner (z.B. DSLRBooth Output) werden automatisch in die Galerie hochgeladen
- **Dezentrale Infrastruktur**: Der Upload läuft unabhängig – auch offline speichert der Fotobox-PC lokal und lädt nach, sobald Internet verfügbar ist
- **Mehrere Upload-Modi**: Unterstützung für manuelle Events, Auto-Event-Modus (mehrere Events gleichzeitig) und verschiedene Dateitypen
- **Monitoring & Logging**: Detaillierte Logs für Debugging und Fehlerbehandlung
- **System-Tray-Integration**: Eine optionale grafische Oberfläche (photobox_tray.py) mit Status-Anzeige

---

## Architektur

Es gibt drei Komponenten im Windows Uploader System:

### photobox_uploader_v2.py

**Zweck**: Moderne, vereinfachte Version des Uploaders mit **Single-Event-Support**

**Charakteristiken**:
- Fokussiert auf ein einziges Event pro Instanz
- Moderne Python 3.10+ mit Type Hints und Dataclasses
- SQLite-basierte Datei-Zustands-Verwaltung (zur Deduplication)
- Unterstützung für Auto-Event-Modus (optional Event automatisch erstellen)
- Saubere Architektur mit klarer Separation of Concerns

**Dateigröße**: ~10.5 KB (323 Zeilen)

**Hauptfunktionen**:
- `load_config()`: Lädt config.json und validiert alle Einstellungen
- `setup_logging()`: Konfiguriert Logging in Datei + Console
- `init_state_db()`: SQLite-Datenbank für Uploads initialisieren
- `was_uploaded()`: Prüft, ob eine Datei bereits hochgeladen wurde (SHA256-basiert oder Größe/mtime)
- `mark_uploaded()`: Markiert Datei als hochgeladen in der Datenbank
- `upload_file()`: Upload mit Retry-Logik und Error-Handling
- `main()`: Hauptschleife mit Folder-Watching

### photobox_uploader.py

**Zweck**: Erweiterte Version mit **Auto-Events-Modus** für mehrere Events

**Charakteristiken**:
- Unterstützung für mehrere Events gleichzeitig
- Kann automatisch Event-Ordner erkennen und Events auf dem Server erstellen
- Konfigurierbare Subfolder-Verarbeitung (Originals, Prints, Animated)
- Folder-Pattern-Matching mit Glob-Unterstützung
- Slugification für Event-Namen
- Admin-API-Key für /api/events/ensure Endpoint

**Dateigröße**: ~11.8 KB (480 Zeilen)

**Besonderheit**: Dieser Uploader ist für **automatische Event-Verwaltung** ausgelegt:
- Root-Verzeichnis überwachen (z.B. C:\DSLRBooth)
- Neue Event-Ordner automatisch erkennen
- Event-Metadata automatisch generieren (Name aus Datum/Folder-Name)
- Separate Subordner für verschiedene Dateitypen verarbeiten

### photobox_tray.py

**Zweck**: Windows System-Tray-GUI für Uploader-Verwaltung

**Charakteristiken**:
- tkinter-basierte grafische Oberfläche
- System-Tray-Integration (über pystray/Pillow)
- Konfiguration speichern/laden aus Windows APPDATA
- Threads für Upload-Worker (asynchron)
- Status-Display: laufend, gestoppt, Fehler
- Sync-Statistiken: Anzahl Uploads, letzte Synchronisation, Fehlercount
- Logging Queue für thread-sichere Log-Ausgabe

**Dateigröße**: ~12.3 KB (494 Zeilen)

**Abhängigkeiten**:
- `pystray`: Für System-Tray-Integration
- `Pillow`: Für Tray-Icon

**Funktionen**:
- `load_config()` / `save_config()`: Konfiguration aus APPDATA\PhotoboxSync\config.json
- `validate_config()`: Validierung aller erforderlichen Felder
- `_sync_worker()`: Daemon-Thread für Synchronisation
- `_do_sync()`: Durchführung eines Sync-Zyklus
- GUI mit Start/Stop-Buttons und Log-Anzeige

---

## Funktionsweise

### Ordner-Überwachung: Wie wird der Watch-Folder beobachtet?

**In photobox_uploader_v2.py:**
1. Der Uploader startet in einer Endlosschleife mit konfigurierbarem Intervall (`upload_interval`, default: 2 Sekunden)
2. Listet alle Dateien im `watch_folder` auf
3. Filterung nach `supported_extensions` (z.B. .jpg, .jpeg, .png, .gif, .mp4)
4. Für jede Datei wird geprüft:
   - Existiert die Datei noch?
   - Ist die Dateigröße und Modifikationszeit gleich wie beim letzten Check?
   - War die Datei bereits hochgeladen (Abfrage in SQLite-Datenbank)?

**In photobox_uploader.py (Auto-Events):**
1. Der Uploader überwacht das `event_root` Verzeichnis
2. Mit `event_folder_glob` (z.B. "*") werden Event-Ordner gefunden
3. Für jeden Event-Ordner:
   - Automatisch ein Event auf dem Server erstellen (via /api/events/ensure + Admin-Key)
   - Für jeden konfiguriert Subfolder (Originals, Prints, Animated):
     - Dateien filtern nach `kind` und `include_ext`
     - Ausschlusse nach `exclude_substrings` (z.B. "thumb")
     - Alle gültigen Dateien hochladen

### Upload-Mechanismus: Wie werden Fotos zum Server hochgeladen?

**POST-Request Structure:**

```http
POST /api/upload HTTP/1.1
Host: your-server:5000
X-API-Key: device_xxx...
X-Filename: photo.jpg
Content-Type: application/octet-stream
Content-Length: 2845293

[binary image data]
```

**Prozess:**
1. Datei öffnen und binäre Daten lesen
2. HTTP POST Request konstruieren mit:
   - Header `X-API-Key`: Device API Key (von Admin Panel)
   - Header `X-Filename`: Originaler Dateiname
   - Header `Content-Type`: application/octet-stream
   - Body: Rohe Bilddaten
3. Request senden (mit Timeout, default: 30 Sekunden)
4. Response prüfen:
   - Status < 300: Erfolg → Datei in SQLite markieren, optional löschen
   - Status >= 400: Fehler → In Log schreiben, Retry beim nächsten Zyklus

**Beispiel mit curl:**
```bash
curl -X POST \
  -H "X-API-Key: device_xxx" \
  -H "X-Filename: hochzeit-001.jpg" \
  --data-binary "@hochzeit-001.jpg" \
  http://localhost:5000/api/upload
```

### Retry-Logik

**photobox_uploader_v2.py:**
- Bei Upload-Fehler: Datei wird **nicht** als hochgeladen markiert
- Beim nächsten Zyklus wird versucht, erneut hochzuladen
- Unbegrenzte Retry-Versuche (bis Benutzer das Skript stoppt)
- Timeout: 30 Sekunden pro Upload

**photobox_uploader.py:**
- Ähnliche Retry-Logik
- Bei HTTP-Fehler wird die Datei stehen gelassen
- Logging für Debugging

**Hinweis**: Beide Uploader sind defensiv programmiert – wenn der Server antwortet (auch mit Fehler), wird das gezielt gehandhabt. Nur bei Netzwerk-Problemen wird automatisch erneut versucht.

### API-Endpoint

Der Uploader spricht folgende Endpoints an:

**1. POST /api/upload** (Standard)
- Datei hochladen
- Header: `X-API-Key`, `X-Filename`
- Response: 200 OK bei Erfolg

**2. POST /api/events/ensure** (nur Auto-Events-Modus)
- Neues Event automatisch erstellen
- Header: `X-Admin-Key`
- Body JSON: `{"name": "Event Name", "slug": "event-slug"}`
- Response: `{"event_id": "...", "slug": "event-slug"}`

---

## Konfiguration

### photobox_uploader_v2.py Config (config.example.json)

```json
{
  "server_url": "http://your-server:5000",
  "api_key": "YOUR_DEVICE_API_KEY",
  "event_slug": "your-event-slug",
  "watch_folder": "C:\\Fotobox\\Bilder",
  "upload_interval": 2,
  "supported_extensions": [".jpg", ".jpeg", ".png", ".gif", ".mp4"],
  "delete_after_upload": false,
  "log_file": "uploader.log"
}
```

| Feldname | Typ | Beschreibung | Default | Pflicht |
|----------|-----|-------------|---------|---------|
| `server_url` | String (URL) | Base URL des Photobox Gallery v2 Servers (z.B. http://192.168.1.100:5000 oder https://galerie.domain.de) | - | ✅ Ja |
| `api_key` | String | Device API Key aus Admin Panel → Devices | - | ✅ Ja |
| `event_slug` | String | URL-Slug des Events (z.B. "hochzeit-2026"). Findest du in der Galerie-URL: /gallery/hochzeit-2026 | - | ⚠️ Ja (außer auto_event=true) |
| `watch_folder` | String (Pfad) | Lokaler Ordner, der überwacht wird (z.B. C:\DSLRBooth\Output). Wird kontinuierlich auf neue Dateien gescannt. | - | ✅ Ja |
| `upload_interval` | Number (Sekunden) | Wie oft der Ordner gescannt wird | 2 | Nein |
| `supported_extensions` | Array[String] | Welche Dateitypen hochgeladen werden (z.B. .jpg, .png, .gif, .mp4) | [".jpg", ".jpeg", ".png", ".gif", ".mp4"] | Nein |
| `delete_after_upload` | Boolean | Sollen Dateien nach erfolgreichem Upload gelöscht werden? (Vorsicht: nicht rückgängig zu machen!) | false | Nein |
| `log_file` | String (Pfad) | Logdatei für Debug-Infos (absolut oder relativ zu config.json) | "uploader.log" | Nein |
| `auto_event` | Boolean | Automatisch Event erstellen wenn nicht vorhanden? | false | Nein |
| `event_name_template` | String | Template für Auto-Event-Namen (z.B. "Event {date}") | "Event {date}" | Nein |

### photobox_uploader.py Config (config.auto.example.json)

```json
{
  "server_url": "https://photobox.example.org",
  "auto_events": true,
  "admin_api_key": "PASTE_ADMIN_API_KEY_HERE",
  "ensure_endpoint": "/api/events/ensure",
  "event_root": "C:\\DSLRBooth",
  "event_folder_glob": "*",
  "poll_interval_seconds": 2,
  "stable_seconds": 3,
  "recursive": true,
  "allowed_ext": [".jpg", ".jpeg", ".png", ".webp", ".mp4", ".gif"],
  "event_subfolders": [
    {
      "subdir": "Originals",
      "kind": "photo",
      "include_ext": [".jpg", ".jpeg", ".png", ".webp"],
      "exclude_substrings": ["\\\\thumb\\\\", "/thumb/"]
    },
    {
      "subdir": "Prints",
      "kind": "print",
      "include_ext": [".jpg", ".jpeg", ".png", ".webp"],
      "exclude_substrings": ["\\\\thumb\\\\", "/thumb/"]
    },
    {
      "subdir": "Animated",
      "kind": "video",
      "include_ext": [".mp4"],
      "exclude_substrings": ["_mp4.jpg", ".jpg"]
    }
  ],
  "state_db": "uploader_state.sqlite3",
  "log_file": "uploader.log"
}
```

| Feldname | Typ | Beschreibung | Default | Pflicht |
|----------|-----|-------------|---------|---------|
| `server_url` | String (URL) | Basis-URL der Galerie | - | ✅ Ja |
| `auto_events` | Boolean | Auto-Event-Modus aktivieren? | false | ✅ Ja |
| `admin_api_key` | String | Admin API Key (für /api/events/ensure) | - | ✅ Ja (wenn auto_events=true) |
| `ensure_endpoint` | String | API-Endpoint für Event-Erstellung | "/api/events/ensure" | Nein |
| `event_root` | String (Pfad) | Root-Ordner mit Event-Subordnern (z.B. C:\DSLRBooth) | - | ✅ Ja (wenn auto_events=true) |
| `event_folder_glob` | String | Pattern für Event-Ordner-Namen (z.B. "*", "Event_*") | "*" | Nein |
| `poll_interval_seconds` | Number | Scan-Intervall | 2 | Nein |
| `stable_seconds` | Number | Wie lange eine Datei unverändert sein muss, bevor Upload versucht wird | 3 | Nein |
| `recursive` | Boolean | Ordner rekursiv durchsuchen? | true | Nein |
| `allowed_ext` | Array[String] | Erlaubte Datei-Endungen | [".jpg", ".jpeg", ".png", ".webp", ".mp4", ".gif"] | Nein |
| `event_subfolders` | Array[Object] | Konfiguriere Subordner mit ihrem Kind (photo/print/gif/video) | (siehe Beispiel) | Nein |
| `event_subfolders[].subdir` | String | Name des Subordners (z.B. "Originals", "Prints") | - | ✅ Ja |
| `event_subfolders[].kind` | String | Typ: "photo", "print", "gif" oder "video" | - | ✅ Ja |
| `event_subfolders[].include_ext` | Array[String] | Welche Extensions in diesem Subfolder hochgeladen werden | [] | Nein |
| `event_subfolders[].exclude_substrings` | Array[String] | Welche Strings im Pfad dazu führen, dass die Datei **nicht** hochgeladen wird (z.B. "thumb") | [] | Nein |
| `state_db` | String (Pfad) | SQLite DB für Upload-Status | "uploader_state.sqlite3" | Nein |
| `log_file` | String (Pfad) | Log-Datei | "uploader.log" | Nein |

---

## auto-config (config.auto.json)

### Unterschied zwischen normaler config und auto-config

| Aspekt | config.json (v2) | config.auto.json (auto-events) |
|--------|-----------------|--------------------------------|
| **Event-Verwaltung** | 1 festes Event (event_slug) | Mehrere Events gleichzeitig, automatisch erstellt |
| **Subfolder-Verarbeitung** | Alle Dateien gehen ins gleiche Event | Verschiedene Subfolders können verschiedene Event-Typen haben (photo/print/gif/video) |
| **Auto-Event-API** | Nicht genutzt | Nutzt /api/events/ensure mit Admin-Key |
| **Admin-Berechtigung** | Device API Key (begrenzt) | Admin API Key (voll berechtigt) |
| **Anwendungsfall** | Single-Event-Setup: 1 Fotobox = 1 Event | Multi-Event-Setup: 1 Fotobox = viele Events gleichzeitig |
| **Folder-Struktur** | `C:\DSLRBooth\Output` → alles ins Event | `C:\DSLRBooth\Event1\Originals`, `Event1\Prints`, `Event2\Originals` usw. |

**Wann welche Config?**
- **config.json**: Du hast eine Fotobox, die immer nur für 1 Event auf einmal läuft
- **config.auto.json**: Du möchtest mehrere Events gleichzeitig supporten, oder die Software soll Events automatisch anlegen

---

## Installation & Setup

### Schritt 1: Python 3.10+ installieren
- Download von python.org
- **Wichtig**: "Add Python to PATH" aktivieren
- Nach Installation: PC neu starten
- Test: `python --version` in Kommandozeile

### Schritt 2: Abhängigkeiten installieren
```bash
pip install -r requirements.txt
```

### Schritt 3: Konfiguration anlegen
1. `config.example.json` kopieren zu `config.json`
2. Folgende Felder anpassen:
   - `server_url`: Deine Galerie-URL
   - `api_key`: Device API Key (aus Admin Panel)
   - `event_slug`: Event URL-Slug (z.B. "hochzeit-2026")
   - `watch_folder`: DSLRBooth Output-Ordner

### Schritt 4: Uploader testen
```bash
python photobox_uploader_v2.py --config config.json
```

### Schritt 5: Autostart konfigurieren (Windows)
- **Task Scheduler** öffnen
- "Aufgabe erstellen"
- Trigger: "Bei Anmeldung"
- Aktion: `python photobox_uploader_v2.py --config config.json`
- Optional: Mit `/admin/settings` den Auto-Start aktivieren

---

## Abhängigkeiten

### requirements.txt
```
requests==2.32.3
```

| Package | Zweck | Version |
|---------|-------|---------|
| `requests` | HTTP-Requests für API-Kommunikation | 2.32.3 |

**Optional (für Tray-GUI):**
```
pystray
Pillow
```

| Package | Zweck | Version |
|---------|-------|---------|
| `pystray` | System Tray Integration (photobox_tray.py) | neueste |
| `Pillow` | Bild-Verarbeitung für Tray-Icon | neueste |

---

## Troubleshooting

### "requests not found"
```bash
pip install requests
```

### "watch_folder does not exist"
Pfad in config.json prüfen. Windows-Pfade können Backslashes brauchen:
```json
"watch_folder": "C:\\Users\\Admin\\Bilder"
```

### "Upload error: HTTP 401"
API-Key ist ungültig oder Gerät ist deaktiviert. Prüfe im Admin Panel.

### "Event not found (HTTP 404)"
Der event_slug existiert nicht. Prüfe die Galerie-URL.

### Log-Datei lesen
```bash
tail -f uploader.log
```

---

## Dateien im Windows Uploader Verzeichnis

- `photobox_uploader_v2.py` – Hauptuploader (Single-Event, modern)
- `photobox_uploader.py` – Erweiterte Version (Auto-Events)
- `photobox_tray.py` – System Tray GUI
- `config.example.json` – Beispielkonfiguration (Single-Event)
- `config.auto.example.json` – Beispielkonfiguration (Auto-Events)
- `requirements.txt` – Python-Abhängigkeiten
- `README_V2.md` – Benutzer-Dokumentation
- `INSTALL_WINDOWS_UPLOADER.md` – Installations-Anleitung

---

## API-Details

### POST /api/upload

**Authentifizierung**: Device API Key im Header `X-API-Key`

**Header**:
```http
X-API-Key: device_xxx...
X-Filename: foto.jpg
Content-Type: application/octet-stream
```

**Body**: Rohe Bilddaten (binary)

**Response**:
- 200 OK: Upload erfolgreich
- 400 Bad Request: Ungültige Anfrage
- 401 Unauthorized: API-Key falsch oder deaktiviert
- 404 Not Found: Event oder Gerät existiert nicht
- 500 Server Error: Fehler auf Server-Seite

### POST /api/events/ensure (Auto-Events)

**Authentifizierung**: Admin API Key im Header `X-Admin-Key`

**Header**:
```http
X-Admin-Key: admin_xxx...
Content-Type: application/json
```

**Body**:
```json
{
  "name": "Hochzeit 2026",
  "slug": "hochzeit-2026",
  "kind": "photo"
}
```

**Response**:
```json
{
  "event_id": "abc123",
  "slug": "hochzeit-2026",
  "created": true
}
```

