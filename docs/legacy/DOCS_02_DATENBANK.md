# PhotoBox Gallery v2 — Datenbankschema

## Übersicht

### Datenbanktyp und Datei-Pfad

- **Datenbanksystem:** SQLite3 (dateibasiert, keine Server-Abhängigkeit)
- **Datei-Pfad:** `{DATA_DIR}/gallery.sqlite3`
  - Standardwert `DATA_DIR`: `./data` (relativ zur Applikation)
  - In Docker: `/data/gallery.sqlite3` (gemountetes Volume)
- **Zugriff:** Python `sqlite3` Standard-Modul mit WAL-Mode (Write-Ahead Logging) für bessere Parallelität
- **Foreign Keys:** `PRAGMA foreign_keys = ON;` wird in jeder Verbindung aktiviert für referenzielle Integrität

### Datenbank-Initialisierung

Die Funktion `init_db()` in `app/db.py` wird bei jedem Request aufgerufen (vor request-Handler):

```python
@app.before_request
def _init_db():
    from app import db
    db.init_db()
```

Diese idempotente Funktion:
1. Erstellt alle Base-Tables mit `CREATE TABLE IF NOT EXISTS`
2. Führt Lightweight-Migrations durch (ALTER TABLE ADD COLUMN für fehlende Spalten)
3. Indizes werden erstellt oder ignoriert falls bereits vorhanden
4. Bootstrap-Daten werden eingefügt (Admin-Credentials, Default-Settings)
5. Fehlende `access_token` werden auf Events gesetzt
6. Legacy-Daten werden migriert (z.B. `status` Spalten initialisiert)

**Idempotenz:** Da alle CREATE/ALTER mit IF NOT EXISTS laufen, kann die Funktion beliebig oft aufgerufen werden ohne Fehler.

---

## Tabellen (vollständig dokumentiert)

### 1. Tabelle: `events`

**Zweck:** Speichert Fotobooth-Events/Galerien. Jedes Event hat einen eigenen Upload-Token, Passwort-Schutz, Retention-Policy, und optionales Branding.

**Schema (CREATE TABLE IF NOT EXISTS events):**

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
```
- **Typ:** INTEGER, Primary Key, Auto-Increment
- **Beschreibung:** Eindeutige Event-ID

```sql
slug TEXT NOT NULL UNIQUE
```
- **Typ:** TEXT, NOT NULL, UNIQUE
- **Beschreibung:** URL-freundlicher Identifier (z.B. "hochzeit-mueller-2025"). Wird in der URL verwendet (/g/hochzeit-mueller-2025)

```sql
name TEXT NOT NULL
```
- **Typ:** TEXT, NOT NULL
- **Beschreibung:** Anzeigename des Events (z.B. "Hochzeit Müller 2025")

```sql
password_hash TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** PBKDF2-Hash des Event-Passworts (falls vorhanden). NULL = kein Passwort erforderlich

```sql
upload_token TEXT NOT NULL UNIQUE
```
- **Typ:** TEXT, NOT NULL, UNIQUE
- **Beschreibung:** Geheim-Token für Gast-Upload via /upload (Drag&Drop, Browser). Wird bei Event-Erstellung generiert

```sql
access_token TEXT
```
- **Typ:** TEXT, NULL erlaubt (aber wird in init_db() für alle Events gesetzt)
- **Beschreibung:** QR-Magic-Link Token für public/shared.html?token=... (Echtzeit-Share-Link ohne Login)

```sql
retention_days INTEGER NOT NULL DEFAULT 30
```
- **Typ:** INTEGER, NOT NULL, Default 30
- **Beschreibung:** Wie lange Media-Dateien aufbewahrt werden. Nach X Tagen können sie gelöscht werden (optional Pruner-Cron)

```sql
print_threshold INTEGER NOT NULL DEFAULT 350
```
- **Typ:** INTEGER, NOT NULL, Default 350
- **Beschreibung:** Print-Schwellwert (Druckqualität-Indikator, optionale Verwendung)

```sql
design_json TEXT NOT NULL DEFAULT '{}'
```
- **Typ:** TEXT, NOT NULL, Default '{}'
- **Beschreibung:** JSON-String mit Event-spezifischem Branding: {logo_path, headline, subtext, color_primary, ...}

```sql
cover_path TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Pfad zum Cover-Bild des Events (wird in Galerielisten-Vorschau angezeigt)

```sql
cover_original_path TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Pfad zum unbearbeiteten Original des Cover-Bildes (für Backup)

```sql
whatsapp_number TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** WhatsApp-Nummer für Share-Link (optional). Format: +49xxx. Wird für WhatsApp-Integrationen genutzt

```sql
whatsapp_message TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Benutzerdefinierte WhatsApp-Nachricht (Template-String mit Platzhalter)

```sql
privacy_url TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** URL zur Datenschutz-Seite (DSGVO-Link in der Galerie-UI)

```sql
followup_message TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Nachricht nach Gast-Upload (z.B. "Danke für die Fotos!")

```sql
google_review_url TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Link zu Google-Bewertung (optional, für Cross-Promotion)

```sql
booking_url TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Buchungs-Link (z.B. zu Fotograf-Webseite oder Booking-Plattform)

```sql
kiosk_text TEXT DEFAULT ''
```
- **Typ:** TEXT, Default ''
- **Beschreibung:** Text, der im Kiosk-Modus angezeigt wird (Fullscreen-Slideshow)

```sql
date TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Event-Datum (ISO-Format oder beliebiger Text). Wird in Event-Listen angezeigt

```sql
description TEXT DEFAULT ''
```
- **Typ:** TEXT, Default ''
- **Beschreibung:** Ausführliche Event-Beschreibung (optional)

```sql
created_at TEXT NOT NULL DEFAULT (datetime('now'))
```
- **Typ:** TEXT, NOT NULL, Default datetime('now')
- **Beschreibung:** Erstellungszeitpunkt (ISO-Datetime-String, SQLite-Format)

```sql
last_upload_at TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Zeitpunkt des letzten Uploads. Wird aktualisiert beim Hinzufügen von Media. Verwendet für Sortierung ("letzte Updates zuerst")

```sql
status TEXT NOT NULL DEFAULT 'live'
```
- **Typ:** TEXT, NOT NULL, Default 'live'
- **Beschreibung:** Event-Status:
  - `live` – Öffentlich sichtbar (optional mit Passwort)
  - `draft` – Nur via Token oder Passwort sichtbar
  - `archived` – Nicht sichtbar, optional mit Redirect-URL

```sql
scheduled_live_at TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** ISO-ähnlicher Timestamp für automatische Veröffentlichung (z.B. "2025-12-31T20:00"). Feature Release 2.1

```sql
redirect_url TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** URL für Redirect, wenn Event archived ist (z.B. zu nächstem Event)

```sql
published INTEGER DEFAULT 1
```
- **Typ:** INTEGER, Default 1
- **Beschreibung:** Boolean (0 oder 1): Ist Event sichtbar? (unterschiedlich von status, für schnelle Sichtbarkeit)

```sql
featured INTEGER NOT NULL DEFAULT 0
```
- **Typ:** INTEGER, NOT NULL, Default 0
- **Beschreibung:** Boolean (0 oder 1): Ist Event auf der Startseite Featured? (Release 2.4)

**Wichtige Indizes:**

```sql
-- Keine expliziten Indizes in Tabellendefinition, aber Slug-Unique-Constraint nutzt B-Tree
```

**Relationen zu anderen Tabellen:**
- `media.event_id` → `events.id` (ON DELETE CASCADE)
- `stats.event_id` → `events.id` (ON DELETE CASCADE)
- `likes.event_id` → `events.id` (ON DELETE CASCADE)
- `notify_subscriptions.event_id` → `events.id` (ON DELETE CASCADE)
- `slug_aliases.event_id` → `events.id` (ON DELETE CASCADE)
- `devices.landing_event_id` → `events.id` (ON DELETE SET NULL, Release 2.4)

---

### 2. Tabelle: `media`

**Zweck:** Speichert alle hochgeladenen Dateien (Fotos, Videos, GIFs). Jede Media-Datei hat Original-, WebP-, und Thumbnail-Versionen.

**Schema:**

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
```
- **Typ:** INTEGER, Primary Key, Auto-Increment
- **Beschreibung:** Eindeutige Media-ID

```sql
event_id INTEGER NOT NULL
```
- **Typ:** INTEGER, NOT NULL
- **Beschreibung:** Foreign Key zu `events.id`. Definiert, zu welchem Event die Datei gehört
- **Constraint:** FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE

```sql
kind TEXT NOT NULL
```
- **Typ:** TEXT, NOT NULL
- **Beschreibung:** Dateityp:
  - `photo` – Bildformat (JPEG, PNG, WebP)
  - `video` – Video (MP4, WebM)
  - `gif` – Animiertes GIF
  - `print` – Druckversion

```sql
filename TEXT NOT NULL
```
- **Typ:** TEXT, NOT NULL
- **Beschreibung:** Originalname der Datei (z.B. "DSC_0001.jpg"). Wird Benutzern angezeigt bei Download

```sql
path_original TEXT NOT NULL
```
- **Typ:** TEXT, NOT NULL
- **Beschreibung:** Filesystem-Pfad zur Original-Datei (z.B. "/data/media/event_123/original/DSC_0001.jpg")

```sql
path_webp TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Pfad zur WebP-konvertierten Version (moderne Bildformat, bessere Kompression). Wird als Vorschau in Galerielisten genutzt

```sql
path_thumb TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Pfad zur Thumbnail-Version (klein, schnelles Laden). Wird in Galerielisten als Vorschau angezeigt

```sql
size_bytes INTEGER NOT NULL DEFAULT 0
```
- **Typ:** INTEGER, NOT NULL, Default 0
- **Beschreibung:** Größe der Original-Datei in Bytes (für Anzeige und Storage-Monitoring)

```sql
sha256 TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** SHA256-Hash der Original-Datei. Wird für Deduplication und Integrität genutzt (optional)

```sql
share_token TEXT NOT NULL UNIQUE
```
- **Typ:** TEXT, NOT NULL, UNIQUE
- **Beschreibung:** Geheim-Token für einzelne Foto-Share-Links (/s/<share_token>). Wird bei Upload generiert

```sql
created_at TEXT NOT NULL DEFAULT (datetime('now'))
```
- **Typ:** TEXT, NOT NULL, Default datetime('now')
- **Beschreibung:** Upload-Zeitpunkt

**Wichtige Indizes:**

```sql
CREATE INDEX idx_media_event ON media(event_id);
```
- Beschleunigt Abfragen wie "SELECT * FROM media WHERE event_id=?" (Galerien-Listings)

**Relationen zu anderen Tabellen:**
- `stats.media_id` → `media.id` (ON DELETE SET NULL)
- `likes.media_id` → `media.id` (ON DELETE CASCADE)

---

### 3. Tabelle: `stats`

**Zweck:** Speichert alle Benutzer-Aktionen für Analytics (Aufrufe, Downloads, Shares, Likes, etc.)

**Schema:**

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
```
- **Typ:** INTEGER, Primary Key, Auto-Increment
- **Beschreibung:** Eindeutige Stat-ID

```sql
event_id INTEGER NOT NULL
```
- **Typ:** INTEGER, NOT NULL
- **Beschreibung:** Foreign Key zu `events.id`
- **Constraint:** FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE

```sql
media_id INTEGER
```
- **Typ:** INTEGER, NULL erlaubt
- **Beschreibung:** Foreign Key zu `media.id` (optional). Einige Stats beziehen sich auf gesamtes Event, nicht einzelne Fotos
- **Constraint:** FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE SET NULL

```sql
action TEXT NOT NULL
```
- **Typ:** TEXT, NOT NULL
- **Beschreibung:** Action-Typ. Mögliche Werte:
  - `view_gallery` – Galerieseite aufgerufen
  - `view_photo` – Einzelfoto angesehen
  - `download` – Einzelfoto heruntergeladen
  - `zip_download` – ZIP mit Fotos heruntergeladen
  - `share_whatsapp` – Über WhatsApp geteilt
  - `share_file` – Datei-Share-Link erstellt
  - `link_share` – Link in anderen Kanal geteilt
  - `upload_guest` – Gast-Upload durchgeführt
  - `like` – Like-Button geklickt (falls als Stat geloggt)

```sql
meta_json TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** JSON-String mit zusätzlichen Metadaten:
  - `{src: "whatsapp"}` – Herkunftskanal
  - `{device_id: "..."}` – Geräte-Identifier
  - `{user_agent: "..."}` – Browser-Info
  - (beliebig erweiterbar)

```sql
created_at TEXT NOT NULL DEFAULT (datetime('now'))
```
- **Typ:** TEXT, NOT NULL, Default datetime('now')
- **Beschreibung:** Zeitpunkt der Aktion

**Wichtige Indizes:**

```sql
CREATE INDEX idx_stats_event ON stats(event_id);
CREATE INDEX idx_stats_media ON stats(media_id);
CREATE INDEX idx_stats_action ON stats(action);
```

- Beschleunigung von Abfragen nach Event, Media, oder Action-Typ

---

### 4. Tabelle: `settings`

**Zweck:** Global Key-Value Store für Anwendungs-Einstellungen (Admin-Credentials, Logo, SMTP, Telegram, etc.)

**Schema:**

```sql
key TEXT PRIMARY KEY
```
- **Typ:** TEXT, Primary Key
- **Beschreibung:** Eindeutiger Setting-Schlüssel (z.B. "admin_username", "smtp_host", "license_key")

```sql
value TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Wert des Settings (kann NULL sein für gelöschte Settings)

```sql
updated_at TEXT NOT NULL DEFAULT (datetime('now'))
```
- **Typ:** TEXT, NOT NULL, Default datetime('now')
- **Beschreibung:** Zeitpunkt der letzten Änderung

**Wichtige Settings:**

| key | Typ | Beschreibung |
|-----|-----|-------------|
| `admin_username` | string | Admin-Benutzername |
| `admin_password_hash` | string | PBKDF2-Hash des Admin-Passworts |
| `license_key` | string | Base64url-kodierter Lizenzschlüssel |
| `license_plan` | string | Lizenzplan-Name (starter/pro/white_label) |
| `homepage_headline` | string | Startseiten-Titel |
| `homepage_subtext` | string | Startseiten-Untertitel |
| `homepage_hero_image_path` | string | Pfad zum Hero-Bild |
| `homepage_show_galleries` | bool | Galerien auf Startseite anzeigen? (0/1) |
| `homepage_show_empty_events` | bool | Leere Events anzeigen? (0/1) |
| `notify_tg_token` | string | Telegram Bot Token |
| `notify_tg_chat_id` | string | Telegram Chat ID |
| `notify_discord_webhook` | string | Discord Webhook URL |
| `notify_webhook_url` | string | Generischer Webhook URL |
| `notify_quiet_from` | string | Stille-Stunden von (HH:MM) |
| `notify_quiet_to` | string | Stille-Stunden bis (HH:MM) |
| `notify_on_upload` | bool | Benachrichtigung bei Upload? (0/1) |
| `notify_on_publish` | bool | Benachrichtigung bei Veröffentlichung? (0/1) |
| `notify_on_new_device` | bool | Benachrichtigung bei neuem Gerät? (0/1) |
| `impressum_text` | text | Impressum-Text |
| `datenschutz_text` | text | Datenschutz/Impressum-Text |
| `footer_custom_link_label` | string | Custom Link Label im Footer |
| `footer_custom_link_url` | string | Custom Link URL im Footer |
| `footer_show_powered_by` | bool | "Powered by" anzeigen? (0/1) |
| `guest_upload_enabled` | bool | Gast-Upload aktiviert? (0/1) |
| `zip_download_enabled` | bool | ZIP-Download aktiviert? (0/1) |
| `zip_download_admin_only` | bool | ZIP nur für Admin? (0/1) |
| `setup_completed` | bool | Setup-Wizard abgeschlossen? (0/1) |
| `app_base_url` | string | Base URL der Anwendung (für absolute Links) |
| `faq_items_json` | json | FAQ-Einträge als JSON |

---

### 5. Tabelle: `likes`

**Zweck:** Speichert Like-Markierungen (Nutzer bewerten einzelne Fotos). Release 2.1

**Schema:**

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
```
- **Typ:** INTEGER, Primary Key, Auto-Increment

```sql
event_id INTEGER NOT NULL
```
- **Typ:** INTEGER, NOT NULL
- **Beschreibung:** Foreign Key zu `events.id`
- **Constraint:** FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE

```sql
media_id INTEGER NOT NULL
```
- **Typ:** INTEGER, NOT NULL
- **Beschreibung:** Foreign Key zu `media.id`
- **Constraint:** FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE

```sql
device_id TEXT NOT NULL
```
- **Typ:** TEXT, NOT NULL
- **Beschreibung:** Eindeutige Device-ID (Browser-basiert, z.B. localStorage-ID, oder IP+User-Agent-Hash)

```sql
created_at TEXT NOT NULL DEFAULT (datetime('now'))
```
- **Typ:** TEXT, NOT NULL, Default datetime('now')

**Wichtige Indizes:**

```sql
CREATE UNIQUE INDEX idx_likes_unique ON likes(media_id, device_id);
CREATE INDEX idx_likes_event ON likes(event_id);
CREATE INDEX idx_likes_media ON likes(media_id);
```

- UNIQUE Constraint verhindert mehrfach-Likes vom gleichen Device

---

### 6. Tabelle: `notify_subscriptions`

**Zweck:** Speichert E-Mail-Abos für Event-Benachrichtigungen. Release 2.1

**Schema:**

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
```

```sql
event_id INTEGER NOT NULL
```
- **Typ:** INTEGER, NOT NULL
- **Constraint:** FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE

```sql
email TEXT NOT NULL
```
- **Typ:** TEXT, NOT NULL
- **Beschreibung:** E-Mail-Adresse des Abonnenten

```sql
token TEXT NOT NULL UNIQUE
```
- **Typ:** TEXT, NOT NULL, UNIQUE
- **Beschreibung:** Geheim-Token für Bestätigungslink (/api/subscriptions/confirm?token=...) und Abmeldung

```sql
confirmed_at TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Zeitpunkt der E-Mail-Bestätigung (NULL = unbestätigt)

```sql
notified_at TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Zeitpunkt der letzten Benachrichtigung

```sql
unsubscribed_at TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Zeitpunkt der Abmeldung (NULL = abonniert)

```sql
created_at TEXT NOT NULL DEFAULT (datetime('now'))
```
- **Typ:** TEXT, NOT NULL, Default datetime('now')

**Wichtige Indizes:**

```sql
CREATE INDEX idx_notify_event ON notify_subscriptions(event_id);
CREATE INDEX idx_notify_email ON notify_subscriptions(email);
```

---

### 7. Tabelle: `devices`

**Zweck:** Speichert Fotobooth-Geräte (PCs) und deren API-Keys. Release 2.2

**Schema:**

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
```

```sql
name TEXT NOT NULL
```
- **Typ:** TEXT, NOT NULL
- **Beschreibung:** Gerätename (z.B. "Fotobox Raum A", "Fotobooth Eingang")

```sql
notes TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Admin-Notizen (optionale Beschreibung)

```sql
api_key TEXT NOT NULL UNIQUE
```
- **Typ:** TEXT, NOT NULL, UNIQUE
- **Beschreibung:** Geheim-API-Key für Uploads (48 hex-Zeichen, secrets.token_hex(24)). Wird in X-API-Key Header gesendet

```sql
active INTEGER NOT NULL DEFAULT 1
```
- **Typ:** INTEGER, NOT NULL, Default 1
- **Beschreibung:** Boolean (0 oder 1): Ist das Gerät aktiv/erlaubt uploads?

```sql
default_watch_path TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Standardpfad zum Überwachen (Windows Uploader)

```sql
default_event_glob TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Standard-Event-Globmuster (z.B. "event_*") für auto-event-Modus

```sql
landing_event_id INTEGER
```
- **Typ:** INTEGER, NULL erlaubt
- **Beschreibung:** Foreign Key zu `events.id`. Event für gerätespezifisches Landing Page (Release 2.4)
- **Constraint:** FOREIGN KEY(landing_event_id) REFERENCES events(id) ON DELETE SET NULL

```sql
landing_headline TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Headline für Gerät-Landing Page

```sql
landing_subtext TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Subtext für Gerät-Landing Page

```sql
landing_bg_path TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Hintergrund-Bildpfad für Gerät-Landing Page

```sql
created_at TEXT NOT NULL DEFAULT (datetime('now'))
```

```sql
last_seen_at TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** Zeitpunkt des letzten Uploads

```sql
last_ip TEXT
```
- **Typ:** TEXT, NULL erlaubt
- **Beschreibung:** IP-Adresse des letzten Uploads

**Wichtige Indizes:**

```sql
CREATE INDEX idx_devices_active ON devices(active);
```

---

### 8. Tabelle: `slug_aliases`

**Zweck:** Speichert alte Event-Slugs als Aliases (für URL-Kompatibilität nach Umbenennung). Release 2.2

**Schema:**

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
```

```sql
alias_slug TEXT NOT NULL UNIQUE
```
- **Typ:** TEXT, NOT NULL, UNIQUE
- **Beschreibung:** Alte/alternative Slug

```sql
event_id INTEGER NOT NULL
```
- **Typ:** INTEGER, NOT NULL
- **Constraint:** FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE

```sql
created_at TEXT NOT NULL DEFAULT (datetime('now'))
```

**Verwendung:** Wenn ein Event von "hochzeit-mueller" zu "hochzeit-mueller-2025" umbenannt wird, wird ein Alias-Eintrag erstellt. Beide URLs funktionieren dann.

**Wichtige Indizes:**

```sql
CREATE INDEX idx_slug_alias_event ON slug_aliases(event_id);
```

---

## Helper-Funktionen in db.py

### Settings Management

#### `get_setting(key: str) -> Optional[str]`
**Parameter:** Setting-Schlüssel
**Rückgabewert:** Wert-String oder None
**Beschreibung:** Liest einen Setting-Wert aus der Tabelle. Wird aufgerufen bei App-Startup um Konfigurationen zu laden.

#### `set_setting(key: str, value: Optional[str]) -> None`
**Parameter:** Setting-Schlüssel, Wert (oder None)
**Rückgabewert:** None
**Beschreibung:** Schreibt/aktualisiert einen Setting-Wert mit INSERT OR REPLACE (upsert). Wird von Admin-UI aufgerufen.

#### `delete_setting(key: str) -> None`
**Parameter:** Setting-Schlüssel
**Rückgabewert:** None
**Beschreibung:** Löscht einen Setting-Wert aus der Tabelle.

### Events

#### `create_event(*, slug, name, upload_token, password="", retention_days=30, print_threshold=350, design_json="{}", access_token=None, status="live", scheduled_live_at=None) -> int`
**Parameter:** Slug, Name, Upload-Token, optionales Passwort, Retention-Tage, Print-Schwellwert, Design-JSON, Access-Token, Status, geplanter Live-Zeitpunkt
**Rückgabewert:** Neue Event-ID
**Beschreibung:** Erstellt ein neues Event mit allen Parametern. Passwort wird gehashed. Access-Token wird generiert falls nicht vorhanden.

#### `update_event(event_id: int, fields: Optional[Dict[str, Any]] = None, **kwargs) -> None`
**Parameter:** Event-ID, Dictionary oder Keyword-Argumente mit zu aktualisierenden Feldern
**Rückgabewert:** None
**Beschreibung:** Aktualisiert Event-Felder (nur erlaubte Felder: name, slug, date, description, password, retention_days, etc.). Password wird automatisch gehasht.

#### `get_event_by_id(event_id: int) -> Optional[Dict[str, Any]]`
**Parameter:** Event-ID
**Rückgabewert:** Event-Dictionary oder None
**Beschreibung:** Holt Event nach ID.

#### `get_event_by_slug(slug: str) -> Optional[Dict[str, Any]]`
**Parameter:** Event-Slug
**Rückgabewert:** Event-Dictionary oder None
**Beschreibung:** Holt Event nach Slug. Fallback zu Alias-Lookup (Release 2.2).

#### `resolve_event_slug(slug: str) -> tuple[Optional[Dict[str, Any]], Optional[str], bool]`
**Parameter:** Slug
**Rückgabewert:** Tuple (event_dict, canonical_slug, is_alias)
**Beschreibung:** Gibt Event, kanonischen Slug, und Info ob Alias zurück.

#### `slug_taken(slug: str) -> bool`
**Parameter:** Slug
**Rückgabewert:** Boolean
**Beschreibung:** Prüft ob Slug als canonical slug oder alias bereits verwendet wird.

#### `add_slug_alias(event_id: int, alias_slug: str) -> None`
**Parameter:** Event-ID, Alias-Slug
**Rückgabewert:** None
**Beschreibung:** Fügt einen Alias hinzu (für alte URLs nach Slug-Umbenennung).

#### `get_event_by_upload_token(token: str) -> Optional[sqlite3.Row]`
**Parameter:** Upload-Token
**Rückgabewert:** Event-Row oder None
**Beschreibung:** Holt Event nach Upload-Token (für Gast-Uploads).

#### `list_events() -> List[Dict[str, Any]]`
**Parameter:** Keine
**Rückgabewert:** Liste aller Events
**Beschreibung:** Gibt alle Events sortiert nach creation_date DESC zurück.

#### `list_events_with_counts() -> List[Dict[str, Any]]`
**Parameter:** Keine
**Rückgabewert:** Liste von Events mit Counts
**Beschreibung:** Gibt Events mit Media-Count, View-Count, Download-Count, Like-Count, Subscriber-Count, etc. zurück (aggregierte Queries).

#### `delete_event(event_id: int) -> None`
**Parameter:** Event-ID
**Rückgabewert:** None
**Beschreibung:** Löscht ein Event und alle zugehörigen Media, Stats, Likes, Subscriptions (wegen ON DELETE CASCADE).

#### `set_event_password(event_id: int, password: str) -> None`
**Parameter:** Event-ID, neues Passwort
**Rückgabewert:** None
**Beschreibung:** Setzt/ändert Event-Passwort.

#### `check_event_password(event_row: sqlite3.Row, password: str) -> bool`
**Parameter:** Event-Row, Passwort
**Rückgabewert:** Boolean
**Beschreibung:** Prüft ob Passwort korrekt ist (NULL-Passwort erlaubt Zugang).

#### `count_media(event_id: int) -> int`
**Parameter:** Event-ID
**Rückgabewert:** Anzahl Media
**Beschreibung:** Zählt Media-Dateien für ein Event.

#### `set_event_featured(event_id: int, featured: bool) -> None`
**Parameter:** Event-ID, Featured-Status
**Rückgabewert:** None
**Beschreibung:** Setzt Featured-Flag (für Homepage-Highlighting, Release 2.4).

### Media

#### `add_media(*, event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256, share_token) -> int`
**Parameter:** Event-ID, Kind (photo/video/gif/print), Filename, Pfade, Größe, SHA256, Share-Token
**Rückgabewert:** Media-ID
**Beschreibung:** Fügt Media-Datei zur DB hinzu und updated Event.last_upload_at.

#### `list_media(event_id: int, *, limit: Optional[int] = None) -> List[Dict[str, Any]]`
**Parameter:** Event-ID, optionales Limit
**Rückgabewert:** Media-Liste
**Beschreibung:** Listet Media für ein Event (sortiert nach created_at DESC).

#### `get_media(media_id: int) -> Optional[Dict[str, Any]]`
**Parameter:** Media-ID
**Rückgabewert:** Media-Dictionary oder None

#### `get_media_by_share_token(token: str) -> Optional[Dict[str, Any]]`
**Parameter:** Share-Token
**Rückgabewert:** Media-Dictionary oder None
**Beschreibung:** Holt Media nach Share-Token (für /s/<token> Share-Links).

#### `delete_media(media_id: int) -> Optional[Dict[str, Any]]`
**Parameter:** Media-ID
**Rückgabewert:** Gelöschte Media-Dictionary
**Beschreibung:** Löscht Media-Eintrag aus DB. Caller ist verantwortlich für Datei-Löschung auf Filesystem.

#### `update_media_thumb(media_id: int, thumb_path: str) -> None`
**Parameter:** Media-ID, Thumbnail-Pfad
**Rückgabewert:** None
**Beschreibung:** Aktualisiert path_thumb nach Thumbnail-Generierung.

### Stats / Analytics

#### `add_stat(event_id: int, action: str, media_id: int | None = None, meta: Optional[Dict[str, Any]] = None) -> None`
**Parameter:** Event-ID, Action-String, optionale Media-ID, optionale Meta-JSON
**Rückgabewert:** None
**Beschreibung:** Loggt eine Action in Stats-Tabelle.

#### `event_stats(event_id: int) -> Dict[str, int]`
**Parameter:** Event-ID
**Rückgabewert:** Dictionary {action: count, ...}
**Beschreibung:** Aggregiert Stats-Counts pro Action für ein Event.

#### `action_src_breakdown(event_id: int, action: str) -> Dict[str, int]`
**Parameter:** Event-ID, Action-String
**Rückgabewert:** Dictionary {src: count, ...}
**Beschreibung:** Zählt Stats gruppiert nach meta.src (Channel-Breakdown).

#### `top_media_actions(event_id: int, action: str, limit: int = 10) -> List[Tuple[sqlite3.Row, int]]`
**Parameter:** Event-ID, Action-String, Limit
**Rückgabewert:** Liste von (media_row, count) Tuples
**Beschreibung:** Gibt meistgebundene/heruntergeladene Fotos zurück.

#### `log_stat(event_id: int, action: str, media_id: int = None, meta: dict = None) -> None`
**Parameter:** Event-ID, Action, optionale Media-ID, optionale Meta
**Rückgabewert:** None
**Beschreibung:** Alias/Duplikat von `add_stat()`.

#### `get_event_stats(event_id: int) -> dict`
**Parameter:** Event-ID
**Rückgabewert:** Dictionary {action: count, ...}
**Beschreibung:** Alias/Duplikat von `event_stats()`.

#### `get_analytics_overview() -> list`
**Parameter:** Keine
**Rückgabewert:** Liste aller Events mit Aggregates
**Beschreibung:** Multi-Event Analytics (Views, Downloads, ZIP-Downloads, WhatsApp-Shares pro Event).

### Likes

#### `like_counts_for_event(event_id: int) -> Dict[int, int]`
**Parameter:** Event-ID
**Rückgabewert:** Dictionary {media_id: like_count, ...}
**Beschreibung:** Gibt Likes pro Media für Event zurück.

#### `like_count(media_id: int) -> int`
**Parameter:** Media-ID
**Rückgabewert:** Like-Count
**Beschreibung:** Gibt Like-Count für einzelnes Foto zurück.

#### `toggle_like(event_id: int, media_id: int, device_id: str) -> Tuple[bool, int]`
**Parameter:** Event-ID, Media-ID, Device-ID
**Rückgabewert:** Tuple (liked_now, like_count)
**Beschreibung:** Togglet Like (inseriert oder löscht). Rückgabewert sagt ob jetzt liked und wie viele Likes total.

#### `top_liked_media(event_id: int, limit: int = 10) -> List[Tuple[sqlite3.Row, int]]`
**Parameter:** Event-ID, Limit
**Rückgabewert:** Liste von (media_row, like_count) Tuples
**Beschreibung:** Gibt meistgelikt Fotos zurück.

### Subscriptions

#### `get_subscription_by_token(token: str) -> Optional[sqlite3.Row]`
**Parameter:** Subscription-Token
**Rückgabewert:** Subscription-Row oder None

#### `upsert_subscription(event_id: int, email: str, token: str) -> int`
**Parameter:** Event-ID, E-Mail, Token
**Rückgabewert:** Subscription-ID
**Beschreibung:** Erstellt oder refreshed Subscription.

#### `confirm_subscription(token: str) -> Optional[sqlite3.Row]`
**Parameter:** Token
**Rückgabewert:** Subscription-Row oder None
**Beschreibung:** Markiert Subscription als confirmed.

#### `unsubscribe(token: str) -> Optional[sqlite3.Row]`
**Parameter:** Token
**Rückgabewert:** Subscription-Row oder None
**Beschreibung:** Markiert Subscription als unsubscribed.

#### `pending_confirmed_subscriptions(event_id: int) -> List[sqlite3.Row]`
**Parameter:** Event-ID
**Rückgabewert:** Liste bestätigter, noch nicht benachrichtigter Subscriptions
**Beschreibung:** Wird vom Notifier verwendet um Benachrichtigungen zu versenden.

#### `mark_notified(tokens: List[str]) -> None`
**Parameter:** Liste von Tokens
**Rückgabewert:** None
**Beschreibung:** Markiert Subscriptions als notified nach Versand.

#### `confirmed_subscriber_count(event_id: int) -> int`
**Parameter:** Event-ID
**Rückgabewert:** Anzahl bestätigter Subscriber
**Beschreibung:** Gibt Subscriber-Count für Event zurück.

#### `add_subscription(event_id: int, email: str, token: str) -> Optional[int]`
**Parameter:** Event-ID, E-Mail, Token
**Rückgabewert:** Subscription-ID oder None
**Beschreibung:** Fügt neue Subscription hinzu (Fallback zu upsert_subscription).

#### `confirm_subscription_by_token(token: str) -> bool`
**Parameter:** Token
**Rückgabewert:** Boolean (erfolgreich bestätigt?)
**Beschreibung:** Alias-Funktion für confirm_subscription.

#### `get_confirmed_subscribers(event_id: int) -> list`
**Parameter:** Event-ID
**Rückgabewert:** Liste bestätigter Subscriptions
**Beschreibung:** Alias-Funktion für pending_confirmed_subscriptions.

### Devices

#### `list_devices() -> List[Dict[str, Any]]`
**Parameter:** Keine
**Rückgabewert:** Liste aller Devices
**Beschreibung:** Listet alle Geräte sortiert nach ID DESC.

#### `get_device_by_id(device_id: int) -> Optional[Dict[str, Any]]`
**Parameter:** Device-ID
**Rückgabewert:** Device-Dictionary oder None

#### `get_device_by_api_key(api_key: str) -> Optional[Dict[str, Any]]`
**Parameter:** API-Key
**Rückgabewert:** Device-Dictionary oder None
**Beschreibung:** Holt Device nach API-Key (wird bei Upload-Authentifizierung verwendet).

#### `create_device(*, name, notes=None, default_watch_path=None, default_event_glob=None) -> sqlite3.Row`
**Parameter:** Name, Notes, Default-Watch-Path, Default-Event-Glob
**Rückgabewert:** Device-Row (mit auto-generiertem API-Key)
**Beschreibung:** Erstellt neues Gerät mit 48-Character API-Key.

#### `update_device(device_id: int, fields: Dict[str, Any]) -> None`
**Parameter:** Device-ID, Felder-Dictionary
**Rückgabewert:** None
**Beschreibung:** Aktualisiert Device-Felder (name, notes, landing_* Felder, etc.).

#### `set_device_active(device_id: int, active: bool) -> None`
**Parameter:** Device-ID, Active-Status
**Rückgabewert:** None
**Beschreibung:** Aktiviert/deaktiviert Gerät.

#### `regenerate_device_key(device_id: int) -> sqlite3.Row`
**Parameter:** Device-ID
**Rückgabewert:** Aktualisierte Device-Row
**Beschreibung:** Generiert neuen API-Key und invalidiert alten (für Sicherheit).

#### `touch_device_seen(device_id: int, ip: str | None = None) -> None`
**Parameter:** Device-ID, optionale IP
**Rückgabewert:** None
**Beschreibung:** Updated last_seen_at und last_ip (wird bei Upload aufgerufen).

#### `get_device_landing_event(device_id: int) -> Optional[Dict[str, Any]]`
**Parameter:** Device-ID
**Rückgabewert:** Event-Dictionary oder None
**Beschreibung:** Holt Event für Gerät-Landing-Page (Release 2.4).

#### `delete_device(device_id: int) -> None`
**Parameter:** Device-ID
**Rückgabewert:** None
**Beschreibung:** Löscht Gerät.

### Setup & FAQ

#### `is_first_run() -> bool`
**Parameter:** Keine
**Rückgabewert:** Boolean
**Beschreibung:** Prüft ob Setup-Wizard noch nicht abgeschlossen (setup_completed Setting nicht gesetzt).

#### `mark_setup_completed() -> None`
**Parameter:** Keine
**Rückgabewert:** None
**Beschreibung:** Setzt setup_completed Setting.

#### `get_faq_items() -> list`
**Parameter:** Keine
**Rückgabewert:** Liste FAQ-Items als JSON
**Beschreibung:** Laden FAQ aus Setting.

#### `save_faq_items(items: list) -> None`
**Parameter:** Liste FAQ-Items
**Rückgabewert:** None
**Beschreibung:** Speichert FAQ in Setting als JSON.

---

## Migrations-Strategie

### Lightweight Migrations (ALTER TABLE ADD COLUMN)

Die `init_db()` Funktion nutzt ein **Try-Except Pattern** für ALTER TABLE:

```python
def init_db() -> None:
    con = get_db()
    cur = con.cursor()
    
    # Create base table
    cur.execute("CREATE TABLE IF NOT EXISTS events (...)")
    
    # Lightweight migrations
    event_cols = _table_cols(cur, "events")
    
    if "cover_path" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN cover_path TEXT;")
    
    if "redirect_url" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN redirect_url TEXT;")
    
    # ... weitere columns
    
    con.commit()
    con.close()
```

**Strategie:**

1. **Helper-Funktion** `_table_cols(cur, table_name)` gibt Set aller Spalten zurück (via PRAGMA table_info)
2. **Spalten-Check:** Jede neue Spalte wird checked mit `if "columnname" not in existing_cols`
3. **Sichere ALTER:** Jede ALTER wird in try-except gepackt (implizit durch SQLite-Fehlerbehandlung)
4. **Default-Werte:** Neue Spalten haben DEFAULT-Klauseln um bestehende Daten nicht zu brechen
5. **Idempotenz:** Kann beliebig oft aufgerufen werden (IF NOT EXISTS, IF [column] NOT IN [cols])

### Bootstrap-Daten

Admin-Credentials werden beim ersten Start aus ENV-Variablen eingefügt:

```python
row_user = cur.execute("SELECT value FROM settings WHERE key='admin_username'").fetchone()
row_hash = cur.execute("SELECT value FROM settings WHERE key='admin_password_hash'").fetchone()

if not row_user:
    cur.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_username', ?)",
        (os.environ.get("ADMIN_USER", "admin"),),
    )

if not row_hash:
    pw = os.environ.get("ADMIN_PASSWORD", "admin")
    cur.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password_hash', ?)",
        (generate_password_hash(pw),),
    )
```

### Defensive Deletes

Beim Event-Löschen werden explizite DELETE-Statements für alle Foreign-Keys gemacht, auch wenn CASCADE nicht aktiviert ist:

```python
def delete_event(event_id: int) -> None:
    con = get_db()
    con.execute("DELETE FROM media WHERE event_id=?", (event_id,))
    con.execute("DELETE FROM stats WHERE event_id=?", (event_id,))
    try:
        con.execute("DELETE FROM likes WHERE event_id=?", (event_id,))
        con.execute("DELETE FROM notify_subscriptions WHERE event_id=?", (event_id,))
    except Exception:
        pass  # Fallback falls Tabelle nicht existiert
    con.execute("DELETE FROM events WHERE id=?", (event_id,))
    con.commit()
```

### Versionskontrolle

Es gibt **keine explizite Schema-Versionierung** (kein `schema_version` Table). Stattdessen wird die Existenz neuer Spalten geprüft. Eine Migration ist "abgeschlossen" wenn alle Spalten existieren.

**Probleme mit diesem Ansatz:**
- Keine Rollback-Möglichkeit
- Keine Tracking welche Migrations angewendet wurden
- Bei größeren Änderungen könnte ein `schema_version` besser sein

**Vorteile:**
- Einfach, robust, keine zusätzliche Komplexität
- Perfekt für kleine Produktionen

---

## Zusammenfassung

Die PhotoBox Gallery v2 Datenbank ist eine **leichte, migrationsfreundliche SQLite-Lösung** mit:
- **8 Kern-Tabellen** (events, media, stats, settings, likes, subscriptions, devices, slug_aliases)
- **Idempotente init_db()** für sichere Updates über Container-Restarts
- **Foreign Key Cascades** für referenzielle Integrität
- **Lightweight ALTER TABLE** Migrations für neue Spalten
- **Bootstrap-Daten** aus ENV-Variablen für erste Installation
- **JSON-Meta-Felder** für flexible Erweiterung (design_json, meta_json, faq_items_json)
- **Defensive Deletes** für Robustheit gegen Pragma-Deaktivierung

Datenbank-Zugriff ist zentral in `app/db.py` mit **90+ Helper-Funktionen** für alle CRUD-Operationen.
