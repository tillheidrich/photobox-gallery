# DOCS_04_FEATURES.md – Umfassende Dokumentation aller implementierten Features

Dieses Dokument dokumentiert die 12 Hauptfeatures des Photobox Gallery v2 Systems mit vollständiger technischer Detaillierung, Architektur-Übersichten, Parameterbeschreibungen, Workflow-Flüssen und allen relevanten Funktionen und Klassen aus dem Quellcode.

---

## 1. Upload-Pipeline & Datei-Verarbeitung

### Übersicht
Das Upload-Pipeline-System verarbeitet hochgeladene Fotos durch eine mehrstufige Pipeline: Validierung, Speicherung, Bildoptimierung (WebP-Konvertierung), Thumbnail-Generierung, QR-Code-Generierung und Benachrichtigungen. Das System unterstützt mehrere Upload-Kanäle (Device-Upload, Guest-Upload, Admin-Upload über die Web-UI) und handhalt alle Dateierkennung, Speicherung und mediale Verarbeitung.

### Architektur

**Dateisystem-Hierarchie:**
```
<base_data_dir>/
  events/
    <event_id>/
      originals/       # Original hochgeladene Bilder (JPEG)
      thumbnails/      # 300x300px Thumbnails (WebP)
      processed/       # Optimierte 1920px Breite Fotos (WebP)
      qrcodes/         # QR-Code PNG-Dateien
      sharepics/       # Social-Media Sharepics (WebP)
  app_files/           # App-Daten (Datenbank, Einstellungen)
  temp/                # Temporäre Dateien
```

**Verarbeitungs-Pipeline-Flow:**
1. Upload empfangen (Device, Guest oder Admin)
2. Datei validieren (Format, Größe, Sicherheit)
3. Original speichern in `originals/`
4. WebP-Konvertierung + Thumbnail (300x300) → `thumbnails/`
5. WebP-Konvertierung optimiert (max 1920px) → `processed/`
6. QR-Code generieren → `qrcodes/`
7. Sharepic generieren → `sharepics/`
8. Batch-Benachrichtigungen auslösen (Telegram, Discord, Webhook, Email)

### Implementierter Code (media.py)

**Pfad-Management-Funktionen:**

- `get_base_data_dir() → str`
  - Basis-Datenpfad abrufen (Standard: `./data` oder Umgebungsvariable `DATA_DIR`)
  - Verzeichnis erstellen falls nicht vorhanden
  - Return: Absoluter Pfad zum Datenverzeichnis

- `get_event_dir(event_id: int) → str`
  - Event-Hauptverzeichnis: `<base>/events/<event_id>/`
  - Verzeichnis + Unterverzeichnisse erstellen
  - Return: Pfad zum Event-Verzeichnis

- `get_originals_dir(event_id: int) → str`
  - Pfad: `<event_dir>/originals/`
  - Für Original-JPEG-Dateien

- `get_thumbnails_dir(event_id: int) → str`
  - Pfad: `<event_dir>/thumbnails/`
  - Für 300x300px WebP-Thumbnails

- `get_processed_dir(event_id: int) → str`
  - Pfad: `<event_dir>/processed/`
  - Für optimierte WebP-Fotos (max 1920px)

- `get_qrcodes_dir(event_id: int) → str`
  - Pfad: `<event_dir>/qrcodes/`
  - Für QR-Code PNG-Dateien

- `get_sharepics_dir(event_id: int) → str`
  - Pfad: `<event_dir>/sharepics/`
  - Für Social-Media Sharepics (WebP)

- `get_original_photo_path(event_id: int, filename: str) → str`
  - Sichere Pfad-Konstruktion mit Validierung
  - Prüft auf Path-Traversal-Angriffe (Punkt-Dot-Check)
  - Return: Vollständiger Pfad zu Original-Datei

- `get_processed_photo_path(event_id: int, filename: str) → str`
  - Sichere Pfad-Konstruktion für verarbeitete Fotos

- `get_thumbnail_path(event_id: int, filename: str) → str`
  - Sichere Pfad-Konstruktion für Thumbnails

- `get_qrcode_path(event_id: int, photo_id: int) → str`
  - Pfad zu QR-Code für spezifisches Foto: `<qrcodes_dir>/<photo_id>.png`

- `get_sharepic_path(event_id: int, photo_id: int) → str`
  - Pfad zu Sharepic für spezifisches Foto: `<sharepics_dir>/<photo_id>.webp`

**Bildverarbeitungs-Funktionen:**

- `process_uploaded_image(original_path: str, event_id: int, filename_base: str) → bool`
  - Hauptfunktion: Verarbeitet ein hochgeladenes Bild durch die komplette Pipeline
  - Parameter:
    - `original_path`: Pfad zu Original-Bilddatei
    - `event_id`: Event-ID für Verzeichnis-Struktur
    - `filename_base`: Basis-Dateiname (ohne Erweiterung)
  - Operationen:
    1. Original als JPEG speichern in `originals/`
    2. Thumbnail generieren (300x300px, WebP) → `thumbnails/`
    3. Optimiertes Foto generieren (max 1920px, WebP, Qualität 85) → `processed/`
    4. Bei Fehler: Original zurück in `originals/`, Fehler loggen
  - Return: `True` bei Erfolg, `False` bei Fehler
  - Exceptions: `ValueError` bei ungültigen Parametern, `IOError` bei Dateisystem-Fehler

- `_convert_to_webp(image: PIL.Image, max_width: int, quality: int, target_path: str) → None`
  - Konvertiert Pillow-Image zu WebP mit max. Breite
  - Parameter:
    - `image`: PIL.Image Objekt
    - `max_width`: Maximale Breite in Pixeln (0 = keine Begrenzung)
    - `quality`: JPEG-ähnliche Qualität (1-100, Standard: 85)
    - `target_path`: Ziel-Dateiname (wird zu .webp konvertiert)
  - Aspect-Ratio wird immer erhalten
  - Exceptions: `IOError` bei Schreibfehler

- `_optimize_color_space(pil_image: PIL.Image) -> PIL.Image`
  - Konvertiert Image zu RGB (falls nötig) für Thumbnail-Generierung
  - Basis für konsistente Verarbeitung

**QR-Code-Generierung:**

- `generate_qrcode(event_id: int, photo_id: int, share_url: str, error_correction: str = "L") → str`
  - Generiert QR-Code für Foto-Seite (Deep-Link zur Galerie)
  - Parameter:
    - `event_id`: Event-ID
    - `photo_id`: Foto-ID (eindeutige ID in Event)
    - `share_url`: URL zum QR-Code einbetten (z.B. `https://photobox.example/share/<token>/<photo_id>`)
    - `error_correction`: QR-Code-Fehlerkorrektur-Level
      - `"L"` (Low, 7%): Schneller, kleinere Dateien
      - `"M"` (Medium, 15%): Ausgewogen
      - `"Q"` (Quartile, 25%): Höhere Zuverlässigkeit
      - `"H"` (High, 30%): Maximum Robustheit
  - Generiert PNG-Image mit QR-Code
  - Speichert zu `<qrcodes_dir>/<photo_id>.png`
  - Return: Dateipfad zum QR-Code PNG

- Verwendete Bibliothek: `qrcode` (Pypackage) mit `PIL.Image` Rendering

**Social-Media Sharepic-Generierung:**

- `generate_sharepic(event_id: int, photo_id: int, photo_filename: str, event_name: str, share_url: str) → str`
  - Generiert Social-Media Share-Graphic (1200x630px Standard)
  - Komplexe Layout-Rendering mit PIL.ImageDraw:
    1. Basis-Bild: Event-Hero oder Fallback-Gradient
    2. Foto-Thumbnail oben (mit Schatten/Effekt)
    3. Event-Name und Beschreibung in Mitte
    4. QR-Code unten-rechts (100x100px)
    5. Logo unten-links oder Branding
    6. Text-Overlays mit beschnittener Größe
  - Parameter:
    - `event_id`: Event-ID für Datei-Struktur
    - `photo_id`: Foto-ID
    - `photo_filename`: Original-Dateiname für Thumbnail
    - `event_name`: Event-Name für Text-Overlay
    - `share_url`: Deep-Link URL für QR-Code
  - Speichert zu `<sharepics_dir>/<photo_id>.webp`
  - Return: Dateipfad zum Sharepic

- Layout-Elemente:
  - **Basis-Bild**: Hero-Bild aus Event (wenn gespeichert), sonst Gradient-Hintergrund
  - **Foto-Thumbnail**: Crop 300x300px aus verarbeitetem Foto, mit weißem Rand
  - **Text-Overlays**: Event-Name (groß, fett), optional Beschreibung (klein)
  - **QR-Code**: Weiß-auf-Grau mit schwarzer Rahmen
  - **Logo/Branding**: Kleine Instanz-Logos/Texte in den Ecken

**Event-Bild-Management:**

- `save_event_image(event_id: int, image_file, image_type: str) → bool`
  - Speichert Event-Bild (Hero, Logo, Cover) von Upload
  - Parameter:
    - `event_id`: Event-ID
    - `image_file`: File-Object oder PIL.Image
    - `image_type`: `"hero"`, `"logo"`, oder `"cover"`
  - Konvertiert zu WebP, speichert in Event-Verzeichnis
  - Return: `True` bei Erfolg

- `get_event_image_path(event_id: int, image_type: str) → str`
  - Pfad zum Event-Bild abrufen
  - Return: Dateipfad oder `None` wenn nicht vorhanden

- `delete_event_image(event_id: int, image_type: str) → bool`
  - Löscht Event-Bild von der Festplatte

### Parameter & Konfiguration

**Umgebungsvariablen:**

- `DATA_DIR` – Basis-Datenpfad (Standard: `./data`)
- `THUMBNAIL_SIZE` – Thumbnail-Dimension in Pixel (Standard: 300)
- `PROCESSED_WIDTH` – Max. Breite verarbeiteter Fotos (Standard: 1920px)
- `WEBP_QUALITY` – WebP-Kompression-Qualität (Standard: 85)

**Validation & Sicherheit:**

- Dateiname-Validierung: Keine `..` oder `/` in Dateinamen erlaubt (Path-Traversal-Schutz)
- Dateiformat: Nur JPEG, PNG, WebP akzeptiert
- Maximale Dateigröße: Konfigurierbar (Standard: 50MB)
- Sandbox-Prüfung: Alle Dateipfade müssen innerhalb `events/<event_id>/` liegen

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Upload-Verarbeitung:**
- Alle Dateien erstellt: Original, Thumbnail, Processed, QR-Code, Sharepic
- Datenbank-Eintrag für Foto erstellt
- Batch-Benachrichtigungen eingeplant (30 Sekunden Verzögerung)
- Return: HTTP 201 Created mit Foto-ID

**Fehlerszenarien:**
- Ungültige Datei-Extension → HTTP 400 Bad Request
- Datei zu groß → HTTP 413 Payload Too Large
- Verzeichnis-Fehler (keine Schreib-Berechtigung) → HTTP 500 Internal Error
- Bild-Verarbeitungsfehler (Corruption) → HTTP 400 Bad Request
- Path-Traversal-Versuch → HTTP 400 Bad Request

---

## 2. Benachrichtigungssystem (Telegram, Discord, Webhook, Email)

### Übersicht
Das Benachrichtigungssystem ist ein multi-Kanal-System, das Upload-, Event- und System-Benachrichtigungen über 4 parallele Kanäle verteilt: **Telegram Bot**, **Discord Webhook**, **Custom HTTP Webhook** und **Email (SMTP)**. Das System implementiert:
- Batch-Benachrichtigungen (30 Sekunden Verzögerung für Upload-Benachrichtigungen)
- Ruhezeit-Verwaltung (Benachrichtigungen unterdrücken zwischen definierten Zeiten)
- Thread-sichere Queue-Verwaltung für asynchrone Verarbeitung
- Fallback-Mechanismen bei Fehler
- Telegram-Command-Polling für `/status` Befehle

### Architektur

**Notification Flow (Upload-Benachrichtigungen):**
```
Foto hochgeladen
    ↓
batch_upload_notification(event_id, count=1)
    ↓
_upload_batch[event_id] += 1
    ↓
_upload_timer.cancel() → neu starten mit 30s Verzögerung
    ↓
(nach 30 Sekunden oder nächstem Timeout)
    ↓
_flush_batch()
    ↓
Prüfung: In Ruhezeit?
    ├─ Ja → Benachrichtigung unterdrücken
    └─ Nein → Senden an:
        ├─ Telegram (HTML-Format)
        ├─ Discord (Markdown-Format)
        ├─ Custom Webhook (JSON)
        └─ Email (an Subscriber)
```

### Implementierter Code (notifier.py)

**Konfiguration (mit DB-Fallback):**

- `_tg_token() → str`
  - Ruft Telegram-Bot-Token ab
  - Fallback: Umgebungsvariable `TG_TOKEN`
  - Backup: Datenbank-Setting `notify_tg_token`
  - Return: Token-String oder leerer String

- `_tg_chat_id() → str`
  - Chat-ID für Telegram-Ziel-Chat
  - Fallback: Umgebungsvariable `TG_CHAT_ID`
  - Backup: Datenbank-Setting `notify_tg_chat_id`
  - Format: Numerisch, positiv oder negativ (für Gruppen)

- `_discord_url() → str`
  - Discord-Webhook vollständige URL
  - Fallback: Umgebungsvariable `DISCORD_WEBHOOK`
  - Backup: Datenbank-Setting `notify_discord_webhook`

- `_quiet_from() → str`
  - Ruhezeit-Start (HH:MM Format)
  - Fallback: Umgebungsvariable `QUIET_FROM`
  - Backup: Datenbank-Setting `notify_quiet_from`
  - Standard: Leer (keine Ruhezeit)

- `_quiet_to() → str`
  - Ruhezeit-Ende (HH:MM Format)
  - Fallback: Umgebungsvariable `QUIET_TO`
  - Backup: Datenbank-Setting `notify_quiet_to`

**Ruhezeit-Verwaltung:**

- `_in_quiet_hours() → bool`
  - Prüft ob aktuelle Zeit in Ruhezeit liegt
  - Parameter: Keine
  - Logik:
    1. Ruhezeit-Einstellungen laden
    2. Falls leer → `False` (keine Ruhezeit aktiv)
    3. Start/End als Minuten-seit-Mitternacht konvertieren
    4. Aktuelle Zeit als Minuten konvertieren
    5. Wenn Start ≤ Ende (normal): Prüfe ob jetzt dazwischen
    6. Wenn Start > Ende (über Mitternacht): Prüfe ob jetzt ≥ Start ODER jetzt < Ende
  - Return: `True` wenn Benachrichtigungen unterdrückt sein sollten
  - Exception Handling: Invalid Format → Log Warning, Return `False`

- Beispiele Ruhezeit-Konfiguration:
  - `QUIET_FROM=22:00 QUIET_TO=08:00` → Nachts 22:00 bis 08:00 Uhr
  - `QUIET_FROM=13:00 QUIET_TO=14:00` → Mittags 13:00 bis 14:00 Uhr
  - `QUIET_FROM="" QUIET_TO=""` → Keine Ruhezeit (24/7 Benachrichtigungen)

**HTTP-Hilfsfunktion:**

- `_post_json(url: str, payload: dict, timeout: int = 10) → bool`
  - Sendet JSON-POST zu beliebiger URL
  - Parameter:
    - `url`: Ziel-URL
    - `payload`: Python-Dictionary (wird zu JSON konvertiert)
    - `timeout`: Socket-Timeout in Sekunden (Standard: 10)
  - Header: `Content-Type: application/json`
  - Return: `True` wenn HTTP-Status < 400 (2xx/3xx erfolgreich)
  - Fehlerbehandlung:
    - HTTPError (4xx/5xx) → Log Warning, Return `False`
    - Network Error (Timeout, Connection) → Log Warning, Return `False`

**Telegram-Integration:**

- `send_telegram(text: str, parse_mode: str = "HTML") → bool`
  - Sendet Text-Nachricht via Telegram-Bot
  - Parameter:
    - `text`: Nachrichtentext (kann HTML-Tags enthalten)
    - `parse_mode`: `"HTML"`, `"Markdown"`, oder `"MarkdownV2"`
  - Format: Telegram Bot API `sendMessage`
  - Return: `True` bei Erfolg, `False` wenn Token/Chat-ID fehlt oder HTTP-Fehler
  - Beispiel Text (HTML):
    ```html
    📸 <b>3 neue Fotos</b> in <b>Hochzeit 2026</b> hochgeladen!
    🔗 https://photobox.example/gallery/1
    ```

- `send_telegram_photo(photo_url: str, caption: str = "") → bool`
  - Sendet Foto-URL via Telegram (mit optionalem Caption)
  - Parameter:
    - `photo_url`: HTTP/HTTPS URL zum Bild
    - `caption`: Optionaler Bild-Text (HTML-Format unterstützt)
  - Format: Telegram Bot API `sendPhoto`
  - Return: `True` bei Erfolg

**Discord-Integration:**

- `send_discord(content: str, *, username: str = "Photobox", embeds: Optional[list] = None) → bool`
  - Sendet Nachricht via Discord-Webhook
  - Parameter:
    - `content`: Nachricht-Text (Markdown-Format)
    - `username`: Bot-Name in Discord (Standard: "Photobox")
    - `embeds`: Optional List von Embed-Objekten (komplexe Layouts)
  - Format: Discord Webhook JSON Payload
  - Return: `True` bei Erfolg, `False` wenn Webhook-URL fehlt
  - Beispiel Content:
    ```markdown
    📸 **3 neue Fotos** in **Hochzeit 2026** hochgeladen!
    https://photobox.example/gallery/1
    ```
  - Embeds Beispiel:
    ```python
    embeds=[{
        "title": "3 neue Fotos",
        "description": "Hochzeit 2026",
        "image": {"url": "https://..."},
        "color": 3447003
    }]
    ```

**Custom HTTP Webhook:**

- `send_webhook(payload: dict) → bool`
  - Sendet JSON-Payload an konfigurierte Webhook-URL
  - Parameter:
    - `payload`: Python-Dictionary (beliebiger Inhalt)
  - Konfiguration: Datenbank-Setting `notify_webhook_url`
  - Return: `True` bei Erfolg, `False` wenn URL nicht konfiguriert
  - Beispiel Payloads:
    - Upload-Batch: `{"event": "batch_upload", "event_id": 1, "count": 3, "gallery_url": "..."}`
    - Event Published: `{"event": "event_published", "event_name": "...", "gallery_url": "..."}`

**Batch-Upload-Benachrichtigungen:**

Globale Variablen:
- `_upload_lock`: `threading.Lock()` – Synchronisiert Zugriff auf _upload_batch
- `_upload_batch`: `dict[int, dict]` – Key: event_id, Value: `{count, event_name, gallery_url}`
- `_upload_timer`: `Optional[threading.Timer]` – Timer für Batch-Versand
- `_BATCH_DELAY`: `30` Sekunden – Verzögerung vor Batch-Versand

- `batch_upload_notification(event_id: int, event_name: str, gallery_url: str = "") → None`
  - Plant Upload-Benachrichtigung (gebündelt über 30 Sekunden)
  - Parameter:
    - `event_id`: Event-ID
    - `event_name`: Event-Name (für Benachrichtigungs-Text)
    - `gallery_url`: Galerie-URL (optional)
  - Logik:
    1. Lock erwerben
    2. Event in `_upload_batch` existiert?
       - Ja → Count inkrementieren
       - Nein → Neuer Eintrag mit Count=1
    3. Alten Timer abbrechen (falls vorhanden)
    4. Neuen Timer starten (30 Sekunden)
    5. Lock freigeben
  - Thread-sicher für mehrere gleichzeitige Uploads
  - Return: `None`

- `_flush_batch() → None`
  - Interne Funktion: Sendet gebündelte Benachrichtigungen
  - Logik:
    1. Lock erwerben
    2. `_upload_batch` kopieren, dann clearen
    3. Timer auf `None` setzen
    4. Lock freigeben
    5. Ruhezeit-Prüfung: Falls ja, Log und Return
    6. Für jedes Event in Batch:
       - Telegram-Nachricht senden (HTML)
       - Discord-Nachricht senden (Markdown)
       - Custom Webhook senden (JSON)
       - Email an Subscriber senden (via emailer.send_new_photos_email)
       - DB-Einträge als "notified" markieren
  - Fehlerbehandlung: Log Warnings, aber nicht abbrechen
  - Return: `None`

**Event-Veröffentlichungs-Benachrichtigung:**

- `notify_event_published(event_name: str, gallery_url: str = "") → None`
  - Sendet Benachrichtigung wenn Event online geht (sofort, nicht gebündelt)
  - Parameter:
    - `event_name`: Event-Name
    - `gallery_url`: Galerie-URL
  - Ruhezeit-Prüfung: Falls ja, Return sofort
  - Sendet an: Telegram, Discord, Webhook (kein Email an Subscriber)
  - Return: `None`

**Telegram-Command-Polling (optional):**

Globale Variablen:
- `_poll_thread`: `Optional[threading.Thread]` – Polling-Thread Referenz
- `_poll_stop`: `threading.Event()` – Signal zum Stoppen
- `_last_update_id`: `int` – Letzte verarbeitete Update-ID (für Polling-Offset)

- `start_command_polling() → None`
  - Startet Telegram-Polling-Thread (einmalig, beim Startup)
  - Prüft: Token existiert? Ja → Thread starten
  - Setzt `_poll_stop.clear()`, erstellt Thread, startet ihn
  - Log: "Telegram-Command-Polling gestartet."
  - Return: `None`

- `stop_command_polling() → None`
  - Stoppt den Polling-Thread
  - Setzt `_poll_stop.set()` (signalisiert Shutdown)
  - Return: `None`

- `_poll_commands() → None`
  - Interne Thread-Funktion: Pollt Telegram Bot API auf Updates
  - Endlosschleife (bis `_poll_stop` gesetzt):
    1. Token laden, falls nicht vorhanden → 30s warten, continue
    2. getUpdates-API aufrufen mit Timeout=20s, Offset=_last_update_id+1
    3. Für jedes Update:
       - Update-ID als größtes _last_update_id merken
       - Message extrahieren, Text extrahieren
       - Falls Text == "/status" und Chat-ID stimmt:
         - _handle_status_command() aufrufen
    4. Backoff-Logik: Bei Erfolg reset auf 1, bei Fehler exponentiell bis 120s
    5. _poll_stop.wait(backoff) – Wartet Backoff-Zeit oder bis Stop-Signal
  - Fehlerbehandlung: Exceptions loggen, Backoff exponentiell erhöhen

- `_handle_status_command(chat_id: int) → None`
  - Antwortet auf `/status` Befehl in Telegram
  - Logik:
    1. Events-Anzahl aus Datenbank laden
    2. Text zusammenstellen: "✅ Photobox läuft. N Events in der Datenbank."
    3. Telegram-Nachricht senden
  - Return: `None`

### Parameter & Konfiguration

**Umgebungsvariablen (mit DB-Fallback):**

| Variable | DB-Key | Format | Beispiel |
|----------|--------|--------|----------|
| `TG_TOKEN` | `notify_tg_token` | Bot-Token | `123456789:ABC...` |
| `TG_CHAT_ID` | `notify_tg_chat_id` | Numerisch | `-1001234567890` |
| `DISCORD_WEBHOOK` | `notify_discord_webhook` | URL | `https://discordapp.com/api/webhooks/...` |
| `QUIET_FROM` | `notify_quiet_from` | HH:MM | `22:00` |
| `QUIET_TO` | `notify_quiet_to` | HH:MM | `08:00` |
| (Custom) | `notify_webhook_url` | URL | `https://example.com/webhook` |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Benachrichtigung:**
- `send_telegram()` → `True` (HTTP 2xx/3xx)
- `send_discord()` → `True` (HTTP 2xx/3xx)
- `batch_upload_notification()` → Plant Versand, Return `None`

**Fehlerszenarien:**
- Kein Token/Webhook konfiguriert → Return `False` oder Skip
- HTTP-Fehler (4xx/5xx) → Log Warning, Return `False`
- Timeout → Log Warning, Return `False`
- In Ruhezeit → Benachrichtigung unterdrücken, Log Info, Return
- Datenbank-Fehler beim Subscriber-Laden → Log Warning, Skip Email
- Ungültiger Ruhezeit-Format → Log Warning, keine Ruhezeit anwenden

---

## 3. E-Mail-System (SMTP, Bestätigung, Benachrichtigungen)

### Übersicht
Das E-Mail-System verwaltet SMTP-Konfiguration und sendet zwei Arten von E-Mails:
1. **Bestätigungs-E-Mails**: Double-Opt-In Flow für Foto-Benachrichtigungen (7 Tage gültiger Link)
2. **Benachrichtigungs-E-Mails**: Neue-Fotos-Benachrichtigungen an bestätigte Abonnenten

Das System speichert SMTP-Konfiguration in der Datenbank und nutzt `smtplib` für den Versand.

### Architektur

**SMTP-Konfiguration Flow:**
```
Benutzer konfiguriert SMTP in Admin-UI
    ↓
/admin/settings/notifications (Admin)
    ↓
Konfiguration als JSON speichern
    ↓
Datenbank-Setting: smtp_config_json
    ↓
(beim Versand)
    ↓
get_smtp_config() lädt aus DB
    ↓
Fallback auf Standardwerte
```

**Double-Opt-In Email-Flow:**
```
Benutzer trägt Email in Galerie ein
    ↓
POST /event/<id>/notify/subscribe
    ↓
Subscription in DB anlegen (status: pending)
    ↓
Bestätigungs-Email senden mit Link
    ↓
Benutzer klickt Link: /notify/confirm/<token>
    ↓
Subscription markieren als confirmed
    ↓
Ab nächstem Upload: Benachrichtigungen senden
```

### Implementierter Code (emailer.py)

**SMTP-Konfiguration:**

- `_default_cfg() → Dict[str, Any]`
  - Gibt Standard-Konfiguration zurück:
    ```python
    {
        "host": "",           # SMTP-Server-Hostname
        "port": 587,          # SMTP-Port (Standard: STARTTLS)
        "username": "",       # SMTP-Benutzer
        "password": "",       # SMTP-Passwort
        "from_email": "",     # Absender-Email
        "use_tls": True,      # STARTTLS nutzen
        "use_ssl": False,     # SMTPS (meist port 465)
    }
    ```

- `get_smtp_config() → Dict[str, Any]`
  - Lädt SMTP-Konfiguration
  - Logik:
    1. Setting `smtp_config_json` aus DB laden
    2. Falls nicht leer: Als JSON parsen, in Standard-Dict mergen
    3. Typ-Normalisierung: Port zu int, Booleans zu bool
    4. Return: Konfiguration-Dict
  - Fallback: Wenn DB-Fehler oder ungültig → Standard-Werte
  - Return: Komplettes Config-Dictionary

- `set_smtp_config(cfg: Dict[str, Any]) → None`
  - Speichert SMTP-Konfiguration
  - Parameter:
    - `cfg`: Konfiguration-Dictionary (wird mit Standard-Defaults gemergt)
  - Logik:
    1. Standard-Config laden
    2. Übergebene Config mergen (überschreibt)
    3. Als JSON speichern in Datenbank-Setting `smtp_config_json`
  - Return: `None`
  - Exceptions: ValueError bei ungültiger JSON, DB-Fehler

- `smtp_configured() → bool`
  - Prüft ob SMTP vollständig konfiguriert ist
  - Bedingungen: `host` nicht leer AND `from_email` nicht leer
  - Return: `True` wenn konfiguriert, `False` sonst

- `mask_secret(s: str) → str`
  - Maskiert Geheimnis (Passwort) für Log-Ausgabe
  - Parameter:
    - `s`: Original-String
  - Logik:
    - Falls leer → `""`
    - Falls ≤ 4 Zeichen → `"****"`
    - Sonst: `s[:2] + "*"*(len-4) + s[-2:]` (erste 2 + letzte 2 Zeichen)
  - Beispiel: "mein_geheimes_passwort" → "me**es_passwort"
  - Return: Maskierter String

**Text-Email-Versand:**

- `send_text_mail(*, to_email: str, subject: str, body: str) → None`
  - Sendet einfache Text-Email
  - Parameter (alle named):
    - `to_email`: Empfänger-Email-Adresse
    - `subject`: Email-Betreff
    - `body`: Email-Text (Plain Text)
  - Logik:
    1. SMTP-Konfiguration laden
    2. host und from_email validieren (müssen nicht leer sein)
    3. EmailMessage erstellen:
       - Subject, From, To setzen
       - Body als Text-Content
    4. SSL-Context erstellen
    5. SMTP-Verbindung:
       - Falls use_ssl=True oder port==465 → SMTP_SSL
       - Sonst → SMTP (mit optional STARTTLS)
    6. Falls Username → Login
    7. Message senden
    8. Verbindung beenden (quit/close)
  - Timeout: 15 Sekunden
  - Exceptions: `RuntimeError` wenn nicht konfiguriert, `smtplib.*Error` bei SMTP-Fehler
  - Return: `None`

**Bestätigungs-Email (Double-Opt-In):**

- `send_confirmation_email(to_email: str, event_name: str, confirm_url: str) → None`
  - Sendet Bestätigungs-Email mit Confirm-Link
  - Parameter:
    - `to_email`: Empfänger
    - `event_name`: Event-Name (für Kontext)
    - `confirm_url`: Vollständige URL zum Bestätigungs-Link
      - Beispiel: `https://photobox.example/notify/confirm/abc123token...`
  - Email-Text:
    ```
    Hallo,

    du hast dich für Foto-Benachrichtigungen der Galerie »{event_name}« angemeldet.

    Bitte bestätige deine E-Mail-Adresse mit einem Klick auf den folgenden Link:

      {confirm_url}

    Der Link ist 7 Tage gültig. Wenn du dich nicht angemeldet hast, kannst du diese Mail einfach ignorieren.

    Viele Grüße
    Dein Fotobox-Team
    ```
  - Subject: `"Bitte bestätige deine Anmeldung – {event_name}"`
  - Ruft `send_text_mail()` auf
  - Exceptions: `RuntimeError` wenn SMTP nicht konfiguriert, `smtplib.*Error` bei Versand-Fehler
  - Return: `None`

**Neue-Fotos-Benachrichtigungs-Email:**

- `send_new_photos_email(to_email: str, event_name: str, gallery_url: str, photo_count: int, unsubscribe_url: str) → None`
  - Sendet Benachrichtigung bei neuen Fotos an Subscriber
  - Parameter:
    - `to_email`: Empfänger
    - `event_name`: Event-Name
    - `gallery_url`: URL zur Galerie-Seite
    - `photo_count`: Anzahl neuer Fotos
    - `unsubscribe_url`: Link zum Abmelden
  - Email-Text:
    ```
    Hallo,

    es gibt {photo_count} neue {noun} in der Galerie »{event_name}«.

    Jetzt ansehen: {gallery_url}

    ---
    Du erhältst diese Mail, weil du Benachrichtigungen für diese Galerie aktiviert hast.
    Abmelden: {unsubscribe_url}
    ```
  - Subject: `"{photo_count} neue {noun} in »{event_name}«"` (noun = "Foto" oder "Fotos")
  - Ruft `send_text_mail()` auf
  - Exceptions: `RuntimeError` wenn SMTP nicht konfiguriert, `smtplib.*Error`
  - Return: `None`

### Parameter & Konfiguration

**SMTP-Config (in Database als JSON):**

```json
{
  "host": "smtp.gmail.com",
  "port": 587,
  "username": "notifications@example.com",
  "password": "app-specific-password",
  "from_email": "notifications@example.com",
  "use_tls": true,
  "use_ssl": false
}
```

**Bekannte SMTP-Server:**

| Provider | Host | Port | Auth | TLS |
|----------|------|------|------|-----|
| Gmail | `smtp.gmail.com` | 587 | Username/Password | Ja |
| Office 365 | `smtp.office365.com` | 587 | Username/Password | Ja |
| SendGrid | `smtp.sendgrid.net` | 587 | `apikey` / `SG.api_key` | Ja |
| AWS SES | `email-smtp.region.amazonaws.com` | 587 | IAM-User | Ja |
| Mailgun | `smtp.mailgun.org` | 587 | Username/Password | Ja |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Email:**
- `send_text_mail()` → `None` (Rückgabe bei Erfolg)
- `send_confirmation_email()` → `None`
- `send_new_photos_email()` → `None`

**Fehlerszenarien:**
- SMTP nicht konfiguriert → `RuntimeError: SMTP not configured`
- Ungültige Ziel-Email → `smtplib.SMTPRecipientsRefused`
- Authentifizierung fehlgeschlagen → `smtplib.SMTPAuthenticationError`
- Timeout → `smtplib.SMTPTimeoutError` oder `socket.timeout`
- Verbindungsfehler → `smtplib.SMTPException` oder `OSError`
- JSON-Parse-Fehler → Fallback auf Standard-Config, kein Fehler

---

## 4. Event-Management

### Übersicht
Event-Management umfasst CRUD-Operationen auf Events: Erstellen, Bearbeiten, Löschen, Metadaten ändern. Jedes Event ist eine Sammlung von Fotos mit zugeordneten Eigenschaften wie Name, Status (Entwurf/Öffentlich), Passwort-Schutz, und Status als "Featured".

### Architektur

**Event-Lebenszyklus:**
```
Event erstellen (Draft)
    ↓
Fotos hochladen (kiosk, upload API)
    ↓
Bearbeite Event-Metadaten
    ├─ Name, Beschreibung
    ├─ Passwort, Zugriffsebenen
    ├─ Hero-Bilder, Logo
    └─ Featured-Status
    ↓
Veröffentliche Event (Public)
    ↓
Besucher können Galerie ansehen
    ├─ Mit Passwort (optional)
    └─ Fotos liken, downloaden, teilen
    ↓
(Optional) Weitere Fotos hinzufügen
    ↓
(Optional) Event archivieren
    ↓
(Optional) Event löschen
```

### Implementierter Code (admin.py - Event Routes)

**Event-Listung & Dashboard:**

- `GET /admin/events` (admin_events)
  - Listet alle Events mit Statistiken
  - Requires: Admin-Session
  - Parameters: Page (optional, Standard: 1)
  - Response: HTML mit Tabelle
    - Spalten: Event-Name, Status, Fotos-Count, Featured, Erstellt, Aktionen
    - Aktionen: Edit, Delete, View, Settings
  - Template: `admin/events.html`

**Event-Details bearbeiten:**

- `GET /admin/events/<int:event_id>` (admin_edit_event)
  - Zeigt Event-Edit-Formular
  - Requires: Admin-Session
  - Parameters: event_id (URL-Parameter)
  - Response: HTML-Formular mit Feldern:
    - Name (Text)
    - Beschreibung (Text, optional)
    - Passwort (Text, optional)
    - Featured (Checkbox)
    - Hero-Image Upload
    - Logo Upload
  - Template: `admin/event_edit.html`

- `POST /admin/events/<int:event_id>` (admin_update_event)
  - Speichert Event-Änderungen
  - Requires: Admin-Session, CSRF-Token
  - Parameters (Form):
    - `name`: Event-Name (erforderlich)
    - `description`: Event-Beschreibung (optional)
    - `password`: Passwort (optional)
    - `is_featured`: Checkbox (0/1)
    - `hero_image`: File-Upload (optional)
    - `logo_image`: File-Upload (optional)
  - Database Updates:
    - `events` Tabelle: name, description, password, is_featured, updated_at
    - Media speichern via `media.save_event_image()`
  - Response: 302 Redirect zu Event-Edit oder Fehler-Message
  - Fehlerbehandlung: Validierung, File-Fehler, DB-Fehler

**Event erstellen:**

- `POST /admin/events/new` (admin_create_event)
  - Erstellt neues Event
  - Requires: Admin-Session, CSRF-Token
  - Parameters (Form):
    - `name`: Event-Name (erforderlich)
    - `description`: Beschreibung (optional)
  - Database: Neuer `events` Eintrag mit Status="draft"
  - Response: 302 Redirect zu Edit-Seite des neuen Events
  - Automatisch generiert:
    - Eindeutige Event-ID (auto-increment)
    - created_at, updated_at (Current timestamp)
    - is_published=0 (Draft)
    - is_featured=0

**Event löschen:**

- `POST /admin/events/<int:event_id>/delete` (admin_delete_event)
  - Löscht Event und alle zugehörigen Fotos
  - Requires: Admin-Session, CSRF-Token, Bestätigungs-Dialog
  - Parameters: event_id (URL)
  - Operationen:
    1. Event aus DB löschen
    2. Alle `photos` für Event-ID löschen
    3. Event-Verzeichnis mit allen Dateien löschen (originals, thumbnails, processed, qrcodes, sharepics)
  - Response: 302 Redirect zur Events-Liste mit Success-Message
  - Fehlerbehandlung: Verzeichnis-Fehler loggen, nicht blockieren

**Event veröffentlichen:**

- `POST /admin/events/<int:event_id>/publish` (admin_publish_event)
  - Wechselt Event von Draft zu Public
  - Requires: Admin-Session
  - Parameters: event_id (URL)
  - Database: Update `events.is_published=1, updated_at=NOW`
  - Triggers: `notify_event_published()` (Telegram, Discord, Webhook)
  - Response: JSON `{"success": true}` oder Error
  - Fehlerbehandlung: Event nicht existiert → 404

**Event-Statistiken:**

- `GET /admin/events/<int:event_id>/stats` (admin_event_stats)
  - Zeigt Event-Statistiken und Foto-Übersicht
  - Requires: Admin-Session
  - Parameters: event_id (URL)
  - Response: HTML oder JSON mit:
    - Foto-Count: Total, Downloads, Likes
    - Traffic: Views, Unique Visitors (Geo-Daten)
    - Featured-Status
    - Created/Published Timestamps
  - Template: `admin/event_stats.html`

### Database Schema (events Tabelle)

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    password TEXT,
    is_published BOOLEAN DEFAULT 0,
    is_featured BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Parameter & Konfiguration

**Event-Properties:**

| Property | Type | Min/Max | Beschreibung |
|----------|------|---------|-------------|
| `name` | String | 1-255 Chars | Event-Name (erforderlich) |
| `description` | String | 0-2000 Chars | Optionale Event-Beschreibung |
| `password` | String | 0-50 Chars | Optionales Passwort für Galerie-Zugriff |
| `is_published` | Boolean | - | Sichtbar für Öffentlichkeit? |
| `is_featured` | Boolean | - | Auf Homepage hervorheben? |
| `created_at` | Timestamp | - | Erstellungs-Zeitpunkt |
| `updated_at` | Timestamp | - | Letzte Änderung |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Operationen:**
- Event erstellen → 302 Redirect zu Edit-Seite
- Event aktualisieren → 302 Redirect oder JSON `{"success": true}`
- Event löschen → 302 Redirect zur Liste
- Event veröffentlichen → JSON `{"success": true}`

**Fehlerszenarien:**
- Event nicht gefunden → 404 Not Found
- Ungültige Eingabe (Name leer) → 400 Bad Request oder Form-Re-Render mit Error
- Datenbank-Fehler → 500 Internal Error
- File-Upload-Fehler → Error-Message in Form, Event wird nicht aktualisiert

---

## 5. Galerie & Foto-Ansicht

### Übersicht
Die Galerie ist die öffentliche Foto-Browsing-Experience für Besucher. Sie unterstützt Passwort-Schutz, Pagination, Sortierung nach Upload-Zeit oder Likes, Detail-Ansicht mit Download/Share-Optionen und Interaktionen (Like, Download).

### Architektur

**Zugriffs-Flow (Public Gallery):**
```
Besucher greift auf /gallery/<event_id> zu
    ↓
Event existiert? Ist veröffentlicht?
    ├─ Nein → 404
    └─ Ja → Weiter
    ↓
Event hat Passwort?
    ├─ Ja → Passwort-Form zeigen
    │   └─ Benutzer sendet Passwort
    │       ├─ Richtig → Access-Token in Session/Cookie
    │       └─ Falsch → Error-Message
    └─ Nein → Direkter Zugriff
    ↓
Galerie anzeigen (Thumbnail-Grid)
    ├─ Pagination: 12-24 Bilder pro Seite
    ├─ Sortierung: Nach Zeit (neu) oder Likes
    └─ Jeder Thumbnail ist Link zu Detail-Seite
    ↓
Benutzer klickt Foto
    ↓
Detail-Ansicht mit:
    ├─ Großes Bild (1920px optimiert)
    ├─ Like-Button
    ├─ Download-Button
    ├─ Share-Button (QR, Sharepic, Social)
    └─ Navigation: Vorheriges/Nächstes
```

### Implementierter Code (public.py - Gallery Routes)

**Galerie-Übersicht:**

- `GET /gallery/<int:event_id>` (view_gallery)
  - Zeigt Galerie mit Thumbnail-Grid
  - Requires: None (Public) oder Passwort im Session
  - Parameters:
    - URL: event_id
    - Query: `page` (Standard: 1), `sort` ("newest" oder "popular", Standard: newest)
  - Logik:
    1. Event laden, prüfen ob veröffentlicht
    2. Falls Passwort erforderlich: Prüfen ob Session-Token gültig
    3. Fotos laden mit Pagination (20 Fotos/Seite)
    4. Nach `sort` Parameter sortieren:
       - `newest`: Sortiere nach created_at DESC
       - `popular`: Sortiere nach like_count DESC
    5. Thumbnails-URLs generieren
    6. Template-Variablen:
       - `event`: Event-Objekt
       - `photos`: List of Photo-Dicts
       - `current_page`: Aktuelle Seite
       - `total_pages`: Gesamtzahl Seiten
       - `sort_order`: Aktuelle Sortierung
  - Response: HTML mit Thumbnail-Grid
  - Template: `public/gallery.html`

- `POST /gallery/<int:event_id>/password` (submit_gallery_password)
  - Verarbeitet Passwort-Eingabe
  - Requires: None
  - Parameters (Form):
    - `password`: Eingegebenes Passwort
  - Logik:
    1. Event laden
    2. Eingabe mit event.password vergleichen
    3. Falls richtig:
       - Access-Token generieren (HMAC-basiert)
       - In Session speichern: `gallery_access[event_id] = token`
       - 302 Redirect zu /gallery/<event_id>
    4. Falls falsch:
       - Error-Message zeigen
       - Re-render Passwort-Formular
  - Response: 302 Redirect oder 403 Forbidden mit Form
  - Template: `public/password_form.html`

**Foto-Detail-Ansicht:**

- `GET /gallery/<int:event_id>/<int:photo_id>` (view_photo)
  - Zeigt Foto in Großansicht mit Metadaten
  - Requires: Public oder Passwort-Token gültig
  - Parameters:
    - URL: event_id, photo_id
  - Logik:
    1. Passwort-Check (wie oben)
    2. Photo laden
    3. Next/Previous Foto-IDs bestimmen für Navigation
    4. QR-Code-URL generieren
    5. Sharepic-URL generieren
    6. Template-Variablen:
       - `photo`: Photo-Objekt mit Meta
       - `event`: Event-Objekt
       - `next_photo_id`, `prev_photo_id`: Für Navigation
       - `qr_code_url`: URL zur QR-Code-View
       - `share_url`: URL für Social-Media
       - `download_url`: URL zum Download
  - Response: HTML mit Foto-Detail
  - Template: `public/photo_detail.html`

**Foto-Interaktionen:**

- `POST /api/photos/<int:photo_id>/like` (api_like_photo)
  - Registriert Like für Foto (IP-basiert, One-Like pro IP pro Foto)
  - Requires: None (Anonym)
  - Parameters:
    - URL: photo_id
    - Header: User-IP (aus request.remote_addr)
  - Database:
    - `photo_likes` Tabelle: photo_id, ip_address, created_at
    - `photos` Tabelle: like_count inkrementieren
  - Response: JSON `{"success": true, "likes": 42}`
  - Fehlerbehandlung: Photo nicht existiert → 404

- `GET /gallery/<int:event_id>/<int:photo_id>/download` (download_photo)
  - Sendet Foto zum Download
  - Requires: Public oder Passwort-Token
  - Parameters:
    - URL: event_id, photo_id
    - Query: `quality` (optional: "original", "hd", "preview", Standard: "hd")
  - Logik:
    1. Passwort-Check
    2. Photo-Datei wählen basierend auf `quality`:
       - `original`: `originals/<photo_id>.jpg`
       - `hd`: `processed/<photo_id>.webp` (1920px)
       - `preview`: `thumbnails/<photo_id>.webp` (300x300)
    3. Analytics tracken: Sende `download` Event an Tracker
    4. File-Response mit Proper Headers (Content-Disposition, Content-Type)
  - Response: Binary File Download
  - Fehlerbehandlung: Datei nicht existiert → 404

**Pagination & Sortierung:**

- Standard-Page-Size: 20 Fotos pro Seite (konfigurierbar)
- Sortier-Optionen:
  - `newest`: ORDER BY created_at DESC
  - `popular`: ORDER BY like_count DESC
  - Default: `newest`
- Page-Parameter: 1-basierend (Seite 1 = First Page)

### Database Schema

```sql
CREATE TABLE photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL FOREIGN KEY,
    filename TEXT NOT NULL,
    original_filename TEXT,
    like_count INTEGER DEFAULT 0,
    download_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE photo_likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id INTEGER NOT NULL FOREIGN KEY,
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(photo_id, ip_address)
);
```

### Parameter & Konfiguration

**Gallery Display:**

| Parameter | Wert | Beschreibung |
|-----------|------|-------------|
| `photos_per_page` | 20 | Fotos pro Galerie-Seite |
| `thumbnail_size` | 300px | Thumbnail-Dimension |
| `detail_view_width` | 1920px | Max. Breite in Detail-Ansicht |
| `default_sort` | newest | Standard-Sortierung |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Gallery-Ansicht:**
- `view_gallery()` → HTML mit Thumbnail-Grid
- `view_photo()` → HTML mit Großansicht
- `download_photo()` → Binary File mit Content-Disposition Header

**Fehlerszenarien:**
- Event nicht existiert → 404 Not Found
- Event nicht veröffentlicht → 404 Not Found (oder spezielle "Coming Soon" Seite)
- Passwort erforderlich aber nicht vorhanden → Zeige Passwort-Formular
- Falsches Passwort → 403 Forbidden mit Error-Message
- Foto nicht existiert → 404 Not Found
- Download-Datei nicht existiert → 404 Not Found oder 500 Internal Error

---

## 6. Kiosk-Modus

### Übersicht
Der Kiosk-Modus ist ein automatisches Photo-Slideshow-Display, das kontinuierlich Bilder eines Events anzeigt und rotiert. Er ist optimiert für öffentliche Event-Displays (Tablets, große Monitore). Kann mit Auto-Refresh und Slideshow-Timing konfiguriert werden.

### Architektur

**Kiosk-Flow:**
```
Besucher greift /kiosk/<event_id> auf
    ↓
Event laden, Passwort-Check (falls erforderlich)
    ↓
HTML mit Fullscreen-Galerie laden
    ├─ Slideshow startet (Fotos auto-rotieren)
    ├─ Intervall: 5-10 Sekunden pro Foto
    └─ Kontinuierliche Schleife
    ↓
JavaScript im Browser:
    ├─ Timer: Alle N Sekunden nächstes Foto zeigen
    ├─ Auto-Refresh: Seite neuladen alle 60 Min (neue Fotos holen)
    └─ Touch/Tap: Manuell vor/zurück navigieren
```

### Implementierter Code (public.py - Kiosk Route)

- `GET /kiosk/<int:event_id>` (view_kiosk)
  - Zeigt Kiosk-Slideshow-Ansicht
  - Requires: Public oder Passwort-Token
  - Parameters:
    - URL: event_id
    - Query: 
      - `interval` (Sekunden pro Foto, Standard: 5)
      - `auto_refresh` (Minuten, Standard: 60, 0 = disabled)
  - Logik:
    1. Event laden, Passwort-Check
    2. Alle Fotos in neustem Reihenfolge laden
    3. Fullscreen-HTML mit Vorlagen generieren
    4. JavaScript einbinden für:
       - Slideshow (rotiere Fotos auto)
       - Touch-Navigation (Links/Rechts für manuell)
       - Auto-Page-Refresh (optional)
  - Response: HTML Fullscreen-Seite
  - Template: `public/kiosk.html`
  - Client-side JavaScript:
    ```javascript
    let currentIndex = 0;
    const photos = [/* Array von Foto-URLs */];
    
    function showPhoto(index) {
        // Display photo[index]
        // Update Slide-Info (current of total)
    }
    
    function nextPhoto() {
        currentIndex = (currentIndex + 1) % photos.length;
        showPhoto(currentIndex);
    }
    
    function prevPhoto() {
        currentIndex = (currentIndex - 1 + photos.length) % photos.length;
        showPhoto(currentIndex);
    }
    
    setInterval(nextPhoto, interval * 1000);
    
    // Optional: Auto-refresh after X minutes
    if (autoRefresh > 0) {
        setTimeout(() => location.reload(), autoRefresh * 60 * 1000);
    }
    ```

### Template Features (kiosk.html)

- **Layout**: Vollbild, keine Navigations-Balken
- **Bild-Anzeige**: Responsive, Aspect-Ratio erhalten, zentriert
- **Slideshow-Info**: Kleine Anzeige oben/unten: "Foto 5 von 23"
- **Touch-Navigation**: Links/Rechts Gesten oder Buttons (optional)
- **Auto-Refresh**: Optional Nachrichten-Ticker mit neuen Foto-Zahl

### Parameter & Konfiguration

**Kiosk-Parameter:**

| Parameter | Typ | Standard | Bereich |
|-----------|-----|---------|---------|
| `interval` | Int | 5 | 1-60 Sekunden |
| `auto_refresh` | Int | 60 | 0-480 Minuten (0 = disabled) |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Kiosk-Ansicht:**
- `view_kiosk()` → HTML mit Auto-Slideshow

**Fehlerszenarien:**
- Event nicht existiert → 404 Not Found
- Event nicht veröffentlicht → 404 Not Found
- Falsches Passwort → 403 Forbidden
- Keine Fotos im Event → Zeige Fallback-Seite ("Warten auf erste Fotos")

---

## 7. QR-Code Generierung & Verwaltung

### Übersicht
QR-Codes werden für jeden Upload erzeugt und ermöglichen schnellen mobilen Zugriff auf Fotos. Codes sind konfigurierbar für Fehlerkorrektur und können als PNG-Bild heruntergeladen oder in Sharepics eingebettet werden.

### Architektur

**QR-Code Lifecycle:**
```
Foto hochgeladen
    ↓
process_uploaded_image()
    ↓
generate_qrcode(event_id, photo_id, share_url)
    ↓
QR-Code PNG generiert → <qrcodes>/<photo_id>.png
    ↓
QR-Code URL in Datenbank gespeichert
    ↓
QR-Code in:
    ├─ Foto-Detail-Seite angezeigt
    ├─ Sharepic eingebettet
    └─ Download-Button verfügbar
```

### Implementierter Code (media.py)

- `generate_qrcode(event_id: int, photo_id: int, share_url: str, error_correction: str = "L") → str`
  - Parameter:
    - `event_id`: Event-ID
    - `photo_id`: Eindeutige Foto-ID
    - `share_url`: URL zum QR-Code einbetten (Deep-Link zur Foto-Seite)
    - `error_correction`: Fehlerkorrektur-Level ("L", "M", "Q", "H", Standard: "L")
  - Logik:
    1. QR-Code-Bibliothek initialisieren mit Fehlerkorrektur-Level
    2. share_url eincodieren
    3. PNG-Image generieren
    4. Speichern zu `<qrcodes_dir>/<photo_id>.png`
    5. Return: Dateipfad
  - Return: Absoluter Pfad zu QR-Code PNG

**QR-Code Display Route:**

- `GET /qrcode/<int:event_id>/<int:photo_id>` (view_qrcode)
  - Sendet QR-Code-Bild
  - Requires: Passwort-Check (falls Event passwort-geschützt)
  - Parameters:
    - URL: event_id, photo_id
    - Query: `size` (optional, 200-500px, Standard: 300)
  - Logik:
    1. QR-Code-Datei laden
    2. Optional skalieren (falls `size` Query-Param)
    3. Image-Response senden (PNG)
  - Response: `image/png` Binary
  - Fehlerbehandlung: Datei nicht existiert → 404

### QR-Code Fehlerkorrektur-Level

| Level | Fehlertoleranz | Größe | Geschwindigkeit | Use Case |
|-------|----------------|-------|-----------------|----------|
| `L` | 7% | Kleinste | Schnellste | Web-URLs |
| `M` | 15% | Klein | Schnell | Standard |
| `Q` | 25% | Mittel | Mittel | Druckmedien |
| `H` | 30% | Groß | Langsam | Sehr robuste Codes |

**Standard in diesem System:** `L` (7% Error Correction) für Web-URLs mit hohem Scanning-Erfolg.

### Parameter & Konfiguration

**QR-Code-Size:**

| Größe (px) | Nutzung |
|-----------|---------|
| 200 | Small (App-Badges) |
| 300 | Standard (Web-Display) |
| 500 | Groß (Druck, Events) |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche QR-Code:**
- `view_qrcode()` → PNG Image Binary
- `generate_qrcode()` → Dateipfad String

**Fehlerszenarien:**
- Foto nicht existiert → 404 Not Found
- QR-Code-Datei nicht vorhanden → 404 Not Found oder generiere on-the-fly
- Ungültiger `size` Parameter → Default (300px)

---

## 8. Subscriber-System (Email-Benachrichtigungen)

### Übersicht
Das Subscriber-System implementiert einen Double-Opt-In Flow für Email-Benachrichtigungen. Benutzer tragen ihre Email in der Galerie ein, erhalten einen Bestätigungs-Link (7 Tage gültig), und danach bekommen sie Benachrichtigungen bei neuen Fotos.

### Architektur

**Double-Opt-In Flow:**
```
1. Benutzer trägt Email in Galerie ein
    ↓
2. /event/<id>/notify/subscribe (POST)
    ↓
3. Token generieren, Subscription anlegen (status: pending)
    ↓
4. Bestätigungs-Email senden mit Confirm-Link
    ↓
5. Benutzer klickt Link (Token + Email)
    ↓
6. /notify/confirm/<token> (GET)
    ↓
7. Token validieren (nicht abgelaufen, Email passt)
    ├─ Token gültig → Subscription markieren als confirmed
    └─ Token ungültig → Error-Page zeigen
    ↓
8. (Optional) Benutzer klickt Unsubscribe-Link in Email
    ↓
9. /notify/unsubscribe/<token> (GET)
    ↓
10. Token validieren → Subscription löschen
```

### Implementierter Code (api.py & public.py - Subscriber Routes)

**Subscription anfragen (initiate):**

- `POST /event/<int:event_id>/notify/subscribe` (api_subscribe_notification)
  - Registriert neue Email für Benachrichtigungen
  - Requires: None (Anonymous)
  - Parameters (JSON/Form):
    - `email`: Benutzer-Email
  - Logik:
    1. Email validieren (Format, nicht leer)
    2. Check: Existiert schon pending/confirmed Subscription für Email + Event?
       - Ja → Error: "Already subscribed"
    3. Token generieren: HMAC-SHA256(`email + event_id + secret + timestamp`)
    4. Subscription anlegen in DB:
       - event_id, email, token, status="pending"
       - expires_at = NOW + 7 days
       - confirmed_at = NULL
    5. Bestätigungs-Email senden via `emailer.send_confirmation_email()`:
       - confirm_url = `https://photobox.example/notify/confirm/{token}`
       - event_name = aus DB
    6. Return: JSON `{"success": true, "message": "Check your email"}`
  - Response: JSON 200 OK oder Error
  - Fehlerbehandlung: Ungültige Email → 400 Bad Request

**Subscription bestätigen (confirm):**

- `GET /notify/confirm/<token>` (confirm_notification)
  - Bestätigt Email-Subscription
  - Requires: None (Anonymous)
  - Parameters:
    - URL: token (HMAC-Token aus Email-Link)
  - Logik:
    1. Token in DB suchen
    2. Validierungen:
       - Token existiert? → Nein → 404 oder Error-Page
       - Status ist "pending"? → Nein → Already confirmed oder Invalid
       - Nicht abgelaufen (expires_at > NOW)? → Nein → Zeige Error-Page
    3. Falls alle validiert:
       - Subscription aktualisieren:
         - status = "confirmed"
         - confirmed_at = NOW
       - Success-Page zeigen
  - Response: HTML Success-Page oder Error-Page
  - Template: `public/confirm_success.html` oder `public/confirm_error.html`
  - Fehlerbehandlung: Token ungültig/abgelaufen → Error-Page mit Resubscribe-Link

**Subscription abmelden (unsubscribe):**

- `GET /notify/unsubscribe/<token>` (unsubscribe_notification)
  - Löscht Subscription
  - Requires: None (Anonymous)
  - Parameters:
    - URL: token (aus Unsubscribe-Link in Email)
  - Logik:
    1. Token in DB suchen
    2. Subscription löschen
    3. Success-Page zeigen
  - Response: HTML mit "Successfully unsubscribed" Message
  - Template: `public/unsubscribe_success.html`

### Database Schema

```sql
CREATE TABLE email_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL FOREIGN KEY,
    email TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'pending',  -- 'pending', 'confirmed', 'unsubscribed'
    confirmed_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Indizes:**
- `(event_id, email)` UNIQUE – Verhindert Duplikate
- `(status)` – Schnelle Abfrage für confirmed Subscriptions
- `(token)` UNIQUE – Token-Lookups

### Functions (notifier.py Integration)

**Subscriber-E-Mails in _flush_batch():**

```python
# In _flush_batch(), for each event in batch:
try:
    from app import db, emailer
    if emailer.smtp_configured():
        base_url = db.get_setting("app_base_url") or ""
        pending = db.pending_confirmed_subscriptions(event_id)
        notified_tokens: list = []
        for sub in pending:
            try:
                unsub_url = f"{base_url}/notify/unsubscribe/{sub['token']}" if base_url else ""
                emailer.send_new_photos_email(
                    to_email=sub["email"],
                    event_name=name,
                    gallery_url=url,
                    photo_count=count,
                    unsubscribe_url=unsub_url,
                )
                notified_tokens.append(sub["token"])
            except Exception as mail_err:
                _log.warning("E-Mail an %s fehlgeschlagen: %s", sub["email"], mail_err)
        if notified_tokens:
            db.mark_notified(notified_tokens)
            _log.info("E-Mail-Benachrichtigungen gesendet...")
except Exception as e:
    _log.warning("Subscriber-E-Mail-Batch fehlgeschlagen: %s", event_id, e)
```

**Database Functions (db.py):**

- `pending_confirmed_subscriptions(event_id: int) → List[Dict]`
  - Ruft alle "confirmed" Subscriptions für Event ab
  - Filter: `status = 'confirmed' AND event_id = ? AND (expires_at IS NULL OR expires_at > NOW)`
  - Return: List of Dicts mit `email`, `token`

- `mark_notified(tokens: List[str]) → None`
  - Markiert Subscriptions als "notified" (optional, für Analytics)
  - Update: `email_subscriptions SET notified_at = NOW WHERE token IN (...)`

### Parameter & Konfiguration

**Token-Validierung:**

| Aspekt | Wert | Beschreibung |
|--------|------|-------------|
| Token-Format | HMAC-SHA256 | Sicher, nicht zu lange |
| Gültigkeitsdauer | 7 Tage | Ausreichend für Benutzer zum Klicken |
| Retry-Mechanismus | Resubscribe-Link | Wenn Token abgelaufen |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Operationen:**
- `api_subscribe_notification()` → JSON `{"success": true}`
- `confirm_notification()` → HTML Success-Page
- `unsubscribe_notification()` → HTML Success-Page

**Fehlerszenarien:**
- Ungültige Email → 400 Bad Request
- Doppel-Subscription → 400 Bad Request ("Already subscribed")
- Token ungültig → 404 oder Error-Page
- Token abgelaufen → Error-Page mit Resubscribe-Option
- SMTP nicht konfiguriert → Info-Message, Subscription trotzdem angelegt

---

## 9. Analytics & Tracking

### Übersicht
Das Analytics-System verfolgt User-Interaktionen: Views, Downloads, Likes, Shares, Social-Media-Clicks. Daten werden aggregiert pro Event und Foto. Das System ist optimaliert für Performance und speichert Rohevents mit aggregierten Statistiken.

### Architektur

**Analytics Tracking Flow:**
```
Benutzer-Aktion (View, Download, Like)
    ↓
Event tracken: POST /api/analytics/track
    ↓
Analytics-Datenbank aktualisieren
    ├─ Raw-Event speichern (optional)
    ├─ Aggregated-Stats aktualisieren
    └─ Cache invalidieren
    ↓
Analytics-Dashboard aufbauen
    ├─ Views pro Event/Foto
    ├─ Favorite-Fotos (nach Likes)
    ├─ Download-Statistiken
    └─ Zeitstempel (Wann are populär?)
```

### Implementierter Code (api.py - Analytics Routes)

**Analytics Event tracken:**

- `POST /api/analytics/track` (api_track_analytics)
  - Trackt User-Aktion
  - Requires: None (Anonymous)
  - Parameters (JSON):
    - `event_id`: Event-ID
    - `photo_id`: Foto-ID (optional, falls Photo-Action)
    - `action`: "view", "download", "like", "share"
    - `source`: "gallery", "kiosk", "share_link", "social" (optional)
    - `metadata`: Custom-Objekt (optional, z.B. `{"platform": "facebook"}`)
  - Logik:
    1. Validiere Action (muss in ["view", "download", "like", "share"])
    2. Event existiert? Ja, weiter
    3. Raw-Event speichern (optional, für Details):
       - analytics_events Tabelle
       - Speichere: event_id, photo_id, action, source, user_ip, user_agent, timestamp
    4. Aggregated-Stats aktualisieren:
       - Falls `action="view"`:
         - `events.view_count += 1`
         - Wenn photo_id: `photos.view_count += 1`
       - Falls `action="download"`:
         - `events.download_count += 1`
         - Wenn photo_id: `photos.download_count += 1`
       - Falls `action="like"`: Bereits in Likes-System (anderer Table)
       - Falls `action="share"`:
         - `events.share_count += 1`
         - Wenn photo_id: `photos.share_count += 1`
    5. Return: JSON `{"success": true}`
  - Response: JSON 200 OK
  - Fehlerbehandlung: Ungültige Action → 400 Bad Request

**Analytics Dashboard (Admin):**

- `GET /admin/analytics` (admin_analytics_overview)
  - Zeigt System-weite Analytics
  - Requires: Admin-Session
  - Response: HTML mit:
    - Total Views, Downloads, Likes, Shares (alle Events)
    - Top 10 Events (nach Views)
    - Top 10 Fotos (nach Likes)
    - Traffic-Trends (Chart: Views pro Tag/Woche)
  - Template: `admin/analytics_overview.html`

- `GET /admin/events/<int:event_id>/analytics` (admin_event_analytics)
  - Zeigt Event-spezifische Analytics
  - Requires: Admin-Session
  - Parameters: event_id (URL)
  - Response: HTML mit:
    - Event-Statistiken: Total Views, Downloads, Likes, Shares
    - Geo-Daten (falls IP-Lookup konfiguriert): Länder, Städte
    - Zeitstempel-Daten: Wann waren Peak-Views?
    - Top-Fotos in diesem Event
    - Device-Info: Mobile vs Desktop
  - Template: `admin/event_analytics.html`

### Database Schema

```sql
-- Raw Analytics Events (optional, für Detailanalyse)
CREATE TABLE analytics_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL FOREIGN KEY,
    photo_id INTEGER,
    action TEXT NOT NULL,  -- 'view', 'download', 'like', 'share'
    source TEXT,  -- 'gallery', 'kiosk', 'share_link', 'social'
    user_ip TEXT,
    user_agent TEXT,
    metadata TEXT,  -- JSON, z.B. {"platform": "facebook"}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Aggregated Statistics (für schnelle Dashboards)
CREATE TABLE analytics_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE FOREIGN KEY,
    view_count INTEGER DEFAULT 0,
    download_count INTEGER DEFAULT 0,
    like_count INTEGER DEFAULT 0,
    share_count INTEGER DEFAULT 0,
    unique_visitors INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Client-side Tracking (JavaScript)

Tracking wird von Client-Seite ausgelöst (wenn JavaScript aktiv):

```javascript
// Gallery-View tracking
fetch('/api/analytics/track', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
        event_id: eventId,
        photo_id: photoId,
        action: 'view',
        source: 'gallery'
    })
});

// Download-Link tracking
fetch('/api/analytics/track', {
    method: 'POST',
    body: JSON.stringify({
        event_id: eventId,
        photo_id: photoId,
        action: 'download',
        source: 'gallery'
    })
}).then(() => {
    // Dann Download starten
    window.location.href = downloadUrl;
});
```

### Parameter & Konfiguration

**Tracking-Aktionen:**

| Action | Trigger | Aggregation |
|--------|---------|-------------|
| `view` | Foto-Seite laden | events.view_count, photos.view_count |
| `download` | Download-Button klicken | events.download_count, photos.download_count |
| `like` | Like-Button klicken | events.like_count, photos.like_count |
| `share` | Share-Button klicken | events.share_count, photos.share_count |

**Daten-Retention:**

| Typ | Aufbewahrung | Beschreibung |
|-----|--------------|-------------|
| Raw Events | 1 Jahr (optional) | Für detaillierte Analysen |
| Aggregated Stats | Unbegrenzt | Für Dashboard-Anzeige |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Tracking:**
- `api_track_analytics()` → JSON `{"success": true}`
- `admin_analytics_overview()` → HTML Dashboard
- `admin_event_analytics()` → HTML Event-Analytics

**Fehlerszenarien:**
- Ungültige Action → 400 Bad Request
- Event nicht existiert → Tracking trotzdem speichern (für fehlerhafte Links)
- Foto nicht existiert → Error ignorieren, trotzdem Event-Analytics aktualisieren

---

## 10. Per-Device Landing Pages

### Übersicht
Device Landing Pages (`/screen/<device_id>`) sind persönliche, konfigurierbare Seiten für registrierte Upload-Geräte (z.B. Fotobooth-Computer). Sie zeigen QR-Codes, Statistiken und Links zum Upload für das Gerät.

### Architektur

**Device Page Flow:**
```
Gerät wird registriert
    ↓
/screen/<device_id> erzeugt
    ↓
Landing-Page anzeigen:
    ├─ Gerät-Name
    ├─ Upload-QR-Code
    ├─ Event-Auswahl
    ├─ Statistiken (letzte Uploads)
    └─ Konfigurationslinks
```

### Implementierter Code (public.py - Device Route)

- `GET /screen/<string:device_id>` (view_device_page)
  - Zeigt Device-Landing-Page
  - Requires: None (Public)
  - Parameters:
    - URL: device_id (Eindeutige Device-ID, z.B. UUID)
  - Logik:
    1. Device laden aus DB (devices Tabelle)
    2. Falls nicht existiert → 404
    3. Geräte-Events laden (Events, für die dieses Gerät Fotos hochgeladen hat)
    4. QR-Code für Upload-URL generieren
    5. Statistiken laden: Letzte 5 Uploads, Total-Count
    6. Template-Variablen:
       - `device`: Device-Objekt
       - `events`: Liste zugehöriger Events
       - `upload_qr_url`: QR-Code zur Upload-API
       - `stats`: Device-Statistiken
  - Response: HTML Custom-Landing-Page
  - Template: `public/device_page.html`

### Database Schema

```sql
CREATE TABLE devices (
    id TEXT PRIMARY KEY,  -- UUID oder Custom-ID
    name TEXT NOT NULL,
    api_key TEXT UNIQUE NOT NULL,
    event_id INTEGER FOREIGN KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used TIMESTAMP,
    upload_count INTEGER DEFAULT 0
);
```

### Parameter & Konfiguration

**Device-ID Format:**

- UUID v4 (Empfohlen): `550e8400-e29b-41d4-a716-446655440000`
- Custom-String: `photobooth-01`, `camera-main-floor`
- Keine Spezialzeichen, URL-safe

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Device-Page:**
- `view_device_page()` → HTML Custom-Landing-Page

**Fehlerszenarien:**
- Device nicht existiert → 404 Not Found
- Keine Events für Device → Zeige Empty-State

---

## 11. FAQ-System

### Übersicht
Das FAQ-System ermöglicht Admin-Verwaltung von FAQs mit Markdown-Rendering und optionalen Datei-Anhängen. FAQs werden auf der Public-Seite angezeigt und sind pro Event konfigurierbar.

### Architektur

**FAQ Management Flow:**
```
Admin bearbeitet FAQs in Admin-Dashboard
    ↓
/admin/settings/faq (POST)
    ↓
FAQ-Einträge speichern in DB
    ├─ Question (Text)
    ├─ Answer (Markdown)
    └─ Anhänge (optional)
    ↓
Auf Public-Seite anzeigen (/gallery/...)
    ├─ Fragen anzeigen
    └─ Markdown rendern (HTML)
```

### Implementierter Code (admin.py - FAQ Routes)

- `GET /admin/settings/faq` (admin_faq_settings)
  - Zeigt FAQ-Edit-Form
  - Requires: Admin-Session
  - Response: HTML-Formular mit FAQ-Einträgen
  - Template: `admin/faq_settings.html`

- `POST /admin/settings/faq` (admin_update_faq)
  - Speichert FAQ-Änderungen
  - Requires: Admin-Session, CSRF-Token
  - Parameters (Form):
    - `faqs[]`: Array von FAQ-Objekten
      - `question`: Frage (Text)
      - `answer`: Antwort (Markdown)
      - `attachments[]`: Optional Datei-Anhänge
  - Logik:
    1. Datenbank-Einträge löschen (alte FAQs)
    2. Neue FAQs speichern:
       - faqs Tabelle: question, answer (markdown), order
       - faq_attachments Tabelle: faq_id, file_path
    3. Datei-Anhänge speichern (falls vorhanden)
    4. Return: 302 Redirect mit Success-Message
  - Response: 302 Redirect
  - Fehlerbehandlung: Ungültige Markdown → Log Warning, trotzdem speichern

### Database Schema

```sql
CREATE TABLE faqs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT,  -- Markdown
    ordering INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE faq_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    faq_id INTEGER NOT NULL FOREIGN KEY,
    file_path TEXT NOT NULL,
    original_filename TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Public FAQ Display

- `GET /gallery/<int:event_id>/#faq` (Fragment auf gallery.html)
  - Zeigt FAQs auf Galerie-Seite
  - Markdown wird zu HTML konvertiert
  - Anhänge als Download-Links angezeigt

### Markdown Rendering

- Library: `markdown` (Python) oder `mistune`
- Unterstützte Syntax:
  - Headers: `# H1`, `## H2`, `### H3`
  - Lists: `- Item`, `1. Numbered`
  - Links: `[Text](URL)`
  - Code: `` `inline` `` oder Fenced-Blocks
  - Bold/Italic: `**bold**`, `*italic*`

### Parameter & Konfiguration

**FAQ-Limits:**

| Aspekt | Limit |
|--------|-------|
| FAQs pro Seite | Unbegrenzt (sollte <20 sein) |
| Frage-Länge | 500 Chars |
| Antwort-Länge | 5000 Chars |
| Anhang-Größe | 10 MB pro Datei |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche FAQ-Verwaltung:**
- `admin_update_faq()` → 302 Redirect mit Success
- FAQ-Anzeige auf Public-Seite → HTML mit Markdown

**Fehlerszenarien:**
- Ungültiges Markdown → Trotzdem speichern, raw anzeigen
- Datei-Upload-Fehler → FAQ speichern, Anhang-Error anzeigen
- Datei zu groß → Error-Message, Anhang nicht speichern

---

## 12. Impressum & Datenschutz (Editable Legal Texts)

### Übersicht
Das Impressum/Datenschutz-System ermöglicht Admins, eigene rechtliche Texte zu bearbeiten und zu speichern. Diese werden auf Public-Seiten angezeigt und unterstützen Markdown-Rendering für flexible Formatierung.

### Architektur

**Legal Text Management Flow:**
```
Admin bearbeitet Impressum/Datenschutz
    ↓
/admin/settings/legal (POST)
    ↓
Texte speichern in DB
    ├─ Impressum (Markdown)
    └─ Datenschutz (Markdown)
    ↓
Public-Links
    ├─ /impressum → Impressum-Seite
    └─ /datenschutz → Datenschutz-Seite
```

### Implementierter Code (admin.py - Legal Routes)

- `GET /admin/settings/legal` (admin_legal_settings)
  - Zeigt Legal-Edit-Form
  - Requires: Admin-Session
  - Response: HTML mit Textareas
  - Template: `admin/legal_settings.html`

- `POST /admin/settings/legal` (admin_update_legal)
  - Speichert Legal-Texte
  - Requires: Admin-Session, CSRF-Token
  - Parameters (Form):
    - `impressum`: Impressum-Markdown
    - `datenschutz`: Datenschutz-Markdown
  - Logik:
    1. Texte in Datenbank speichern:
       - Settings-Tabelle Keys:
         - `legal_impressum` = Impressum-Text
         - `legal_datenschutz` = Datenschutz-Text
    2. Cache invalidieren (falls Caching)
    3. Return: 302 Redirect mit Success-Message
  - Response: 302 Redirect
  - Fehlerbehandlung: Keine (Markdown ist beliebig)

**Public Legal Pages:**

- `GET /impressum` (view_impressum)
  - Zeigt Impressum-Seite
  - Requires: None (Public)
  - Logik:
    1. Impressum-Text aus DB laden
    2. Markdown zu HTML konvertieren
    3. Template mit HTML-Inhalt rendern
  - Response: HTML mit Impressum
  - Template: `public/impressum.html`

- `GET /datenschutz` (view_datenschutz)
  - Zeigt Datenschutz-Seite
  - Requires: None (Public)
  - Logik: Wie oben, mit `legal_datenschutz`
  - Response: HTML mit Datenschutz
  - Template: `public/datenschutz.html`

- `GET /privacy` (view_privacy)
  - Alias für `/datenschutz` (englisch)

### Database Schema

```sql
-- Settings-Tabelle (Key-Value)
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Beispiel-Einträge:
-- INSERT INTO settings (key, value) VALUES ('legal_impressum', '...');
-- INSERT INTO settings (key, value) VALUES ('legal_datenschutz', '...');
```

### Markdown Features für Legal Texts

**Typische Struktur:**

```markdown
# Impressum

## Betreiber
[Name und Anschrift der Website]

## Kontakt
- Email: contact@example.com
- Telefon: +49 (0) 123 456789

## Verantwortlich für redaktionelle Inhalte
...

---

# Datenschutz

## 1. Datenschutzerklärung
[Disclaimer]

## 2. Erfassung persönlicher Daten
...

## 3. Cookies
...
```

### Template Features (legal_settings.html)

- **Textareas**: Große Eingabefelder für Markdown
- **Preview**: Live-Vorschau der Markdown-Renderers
- **Help**: Markdown-Syntax-Hilfe
- **Länge-Anzeige**: Character-Count

### Parameter & Konfiguration

**Legal Text Limits:**

| Aspekt | Limit |
|--------|-------|
| Impressum-Länge | 50,000 Chars |
| Datenschutz-Länge | 50,000 Chars |

### Rückgabewerte und Fehlerbehandlung

**Erfolgreiche Legal-Verwaltung:**
- `admin_update_legal()` → 302 Redirect mit Success
- `view_impressum()` → HTML mit Markdown
- `view_datenschutz()` → HTML mit Markdown

**Fehlerszenarien:**
- Texte nicht konfiguriert → Zeige Placeholder-Seite oder 404
- Ungültiges Markdown → Trotzdem rendern (raw)
- Datenbankfehler → 500 Internal Error

---

## Feature-Matrix Summary

| Feature | Entry Points | Key Database Tables | Key Files |
|---------|--------------|---------------------|-----------|
| 1. Upload-Pipeline | `/api/upload/*` | `photos`, `events` | `media.py` |
| 2. Notifications | (Internal) | `settings`, `email_subscriptions` | `notifier.py` |
| 3. Email-System | (Internal) | `email_subscriptions` | `emailer.py` |
| 4. Event-Mgmt | `/admin/events/*` | `events` | `admin.py` |
| 5. Gallery | `/gallery/*` | `photos`, `photo_likes` | `public.py` |
| 6. Kiosk | `/kiosk/*` | `photos`, `events` | `public.py` |
| 7. QR-Codes | `/qrcode/*` | (Dateiystem) | `media.py`, `public.py` |
| 8. Subscribers | `/event/*/notify/subscribe` | `email_subscriptions` | `api.py`, `public.py` |
| 9. Analytics | `/api/analytics/*` | `analytics_events`, `analytics_stats` | `api.py`, `admin.py` |
| 10. Device Pages | `/screen/*` | `devices` | `public.py` |
| 11. FAQ-System | `/admin/settings/faq` | `faqs`, `faq_attachments` | `admin.py` |
| 12. Legal Texts | `/admin/settings/legal` | `settings` | `admin.py`, `public.py` |

---

## Dokument-End

Diese Dokumentation deckt alle 12 Hauptfeatures des Photobox Gallery v2 Systems ab mit vollständiger technischer Detaillierung.
