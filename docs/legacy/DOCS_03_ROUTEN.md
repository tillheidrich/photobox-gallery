# PhotoBox Gallery v2 – Alle Routen & Endpunkte

Vollständige Referenz aller HTTP-Endpunkte und Routes im System. Die Routen sind in drei Flask Blueprints organisiert: `public` (Gäste-Interface), `admin` (Admin-Panel), und `api` (Programmatische APIs).

---

## PUBLIC Blueprint (`/`)

Öffentliche Routen für Gäste und Website-Besucher.

### GET `/`
- **Funktion:** `home()`
- **Auth erforderlich:** Nein
- **Beschreibung:** Homepage mit Liste aller veröffentlichten Events und deren Cover-Bilder
- **Parameter:** Keine
- **Rückgabe:** HTML (Template: `public/home.html`)
- **Features:** 
  - Zeigt nur veröffentlichte Events
  - Sortierung: Featured Events zuerst, dann nach Datum
  - Optionale Anzeige von Events ohne Fotos basierend auf Setting `homepage_show_empty_events`

### GET `/screen/<device_id>`
- **Funktion:** `device_landing(device_id: int)`
- **Auth erforderlich:** Nein
- **Beschreibung:** Per-Device Landing Page mit Gerät-spezifischen Inhalten
- **Parameter:** 
  - `device_id` (URL): Integer Device-ID
- **Rückgabe:** HTML (Template: `public/device_landing.html`)
- **Features:**
  - Zeigt zugeordnetes Event für das Gerät
  - Tracked Gerät-Views mit `db.touch_device_seen()`
  - Nur für aktive Geräte zugänglich

### GET/POST `/gallery/<slug>`
- **Funktion:** `gallery(slug: str)`
- **Auth erforderlich:** Nein (Passwort optional)
- **Beschreibung:** Haupt-Galerie mit allen Fotos eines Events
- **Parameter:**
  - `slug` (URL): Event-Slug
  - `embed=1` (Query): Embed-Modus (iframe)
  - `access_token` (Query): Direktzugriffs-Token
  - `src` (Query): Tracking-Quelle (default: "direct")
  - `password` (POST): Passwort-Input für geschützte Events
- **Rückgabe:** HTML (Template: `public/gallery.html`) oder Passwort-Form
- **Features:**
  - Passwort-Schutz mit Session-Speicherung
  - Access-Token für direktlinks
  - Lazy-Loading von Fotos
  - Unterscheidung zwischen Foto/Video (Kind)
  - ZIP-Download (optional, konfigurierbar)
  - Guest Upload (optional, konfigurierbar)
  - Analytics-Tracking für Views

### GET `/s/<share_token>`
- **Funktion:** `share_redirect(share_token: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** Redirect zu einzelnem Foto mit Share-Token
- **Parameter:**
  - `share_token` (URL): Eindeutiger Share-Token des Fotos
- **Rückgabe:** HTTP 301 Redirect zu `/gallery/<slug>#photo-<media_id>`
- **Features:** Link-Sharing für einzelne Fotos

### GET `/gallery/<slug>/qr.png`
- **Funktion:** `gallery_qr_image(slug: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** QR-Code zum Galerie-Link als PNG-Bild
- **Parameter:**
  - `slug` (URL): Event-Slug
- **Rückgabe:** PNG Binary (mimetype: `image/png`)
- **Features:**
  - Fehlerkorrektur Level M
  - Box-Größe: 10px
  - Border: 2px

### GET `/gallery/<slug>/sharepic.png`
- **Funktion:** `gallery_sharepic(slug: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** Social-Media Sharepic (1080x1920) mit QR Code und Event-Title
- **Parameter:**
  - `slug` (URL): Event-Slug
- **Rückgabe:** PNG Binary als Download (`sharepic-<slug>.png`)
- **Features:**
  - Event-Cover als Hintergrund (mit Blur-Effekt)
  - Event-Title mit Text-Wrapping
  - QR Code zum Galerie-Link
  - Logo-Integration
  - Gradient-Overlay
  - Optimiert für Instagram/WhatsApp Stories

### GET `/gallery/<slug>/print`
- **Funktion:** `print_qr(slug: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** A4-druckbare Seite mit QR Code (Print-Layout)
- **Parameter:**
  - `slug` (URL): Event-Slug
- **Rückgabe:** HTML (Template: `public/print_qr.html`)
- **Features:**
  - Print-optimiertes CSS
  - Logo-Integration
  - Footer mit "Powered by"

### GET `/gallery/<slug>/download.zip`
- **Funktion:** `gallery_download_zip(slug: str)`
- **Auth erforderlich:** Optional (Passwort-Prüfung)
- **Beschreibung:** Alle Fotos des Events als ZIP-Datei
- **Parameter:**
  - `slug` (URL): Event-Slug
  - `ids` (Query): Komma-separierte Liste von Media-IDs (selektiver Download)
- **Rückgabe:** ZIP Binary als Download (`<slug>-fotos.zip`)
- **Features:**
  - Setting: `zip_download_enabled` (aktiviert/deaktiviert)
  - Setting: `zip_download_admin_only` (nur für Admin sichtbar)
  - Selektiver Download nach Foto-IDs
  - Originale Dateierweiterungen (.jpg, .png, etc.)
  - Sequenzielle Nummerierung im ZIP

### GET `/kiosk/<slug>`
- **Funktion:** `kiosk(slug: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** Kiosk-Modus mit Auto-Refresh und Slideshow
- **Parameter:**
  - `slug` (URL): Event-Slug
- **Rückgabe:** HTML (Template: `public/kiosk.html`)
- **Features:**
  - Auto-Refresh für Live-Galerie-Ansicht
  - Slideshow der letzten 20 Fotos als Hintergrund
  - Großer QR Code zur Galerie
  - Normalisiert auf noindex

### GET `/impressum`
- **Funktion:** `impressum()`
- **Auth erforderlich:** Nein
- **Beschreibung:** Impressumseite (rechtliche Informationen)
- **Parameter:** Keine
- **Rückgabe:** HTML (Template: `public/impressum.html`)
- **Features:**
  - Inhalt aus Setting `impressum_text` (editierbar)
  - Markdown → HTML Umwandlung
  - 404 wenn leer

### GET `/datenschutz`
- **Funktion:** `datenschutz()`
- **Auth erforderlich:** Nein
- **Beschreibung:** Datenschutz/Privacy-Policy
- **Parameter:** Keine
- **Rückgabe:** HTML (Template: `public/datenschutz.html`)
- **Features:**
  - Inhalt aus Setting `datenschutz_text` (editierbar)
  - Markdown → HTML Umwandlung
  - 404 wenn leer

### GET/POST `/faq`
- **Funktion:** `faq()`
- **Auth erforderlich:** Nein
- **Beschreibung:** FAQ-Seite mit Fragen und Antworten
- **Parameter:** Keine
- **Rückgabe:** HTML (Template: `public/faq.html`)
- **Features:**
  - Items aus DB (mit Anhänge-Support)
  - Intro-Text mit Markdown
  - Markdown → HTML für Antworten
  - Dateianhänge (PDF, JPG, PNG, GIF)

### POST `/lang/<code>`
- **Funktion:** `set_language(code: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** UI-Sprache setzen (de/en)
- **Parameter:**
  - `code` (URL): Sprach-Code ("de" oder "en")
- **Rückgabe:** HTTP 302 Redirect zurück zu Referrer oder `/`
- **Features:** Session-basierte Sprach-Speicherung

### GET `/healthz`
- **Funktion:** `healthz()`
- **Auth erforderlich:** Nein
- **Beschreibung:** Health-Check Endpoint
- **Parameter:** Keine
- **Rückgabe:** JSON `{"ok": True}` (HTTP 200)
- **Features:** Für Load-Balancer/Monitoring

### GET `/branding/logo`
- **Funktion:** `branding_logo()`
- **Auth erforderlich:** Nein
- **Beschreibung:** Globales Branding-Logo
- **Parameter:** Keine
- **Rückgabe:** Datei (Logo-Datei mit Conditional HTTP)
- **Features:**
  - Aus Setting `branding_logo_path`
  - 404 wenn nicht konfiguriert

### GET `/data/<path:path>`
- **Funktion:** `serve_data_file(path: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** Datei-Auslieferung (Fotos, Assets, etc.)
- **Parameter:**
  - `path` (URL): Relativer Pfad ab DATA_DIR
- **Rückgabe:** Datei-Inhalt
- **Features:**
  - Path-Traversal-Schutz
  - Conditional HTTP (If-Modified-Since, ETag)
  - Alle relativen Pfade unter DATA_DIR

### GET `/notify/confirm/<token>`
- **Funktion:** `notify_confirm(token: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** Double Opt-In Bestätigungs-Link für E-Mail-Abonnements
- **Parameter:**
  - `token` (URL): Bestätigungs-Token
- **Rückgabe:** HTML (Template: `public/notify_result.html`)
- **Features:**
  - Markiert Subscription als bestätigt
  - Expires automatisch nach 7 Tagen

### GET `/notify/unsubscribe/<token>`
- **Funktion:** `notify_unsubscribe(token: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** Abmelde-Link für E-Mail-Benachrichtigungen
- **Parameter:**
  - `token` (URL): Abmelde-Token
- **Rückgabe:** HTML (Template: `public/notify_result.html`)
- **Features:**
  - Löscht Subscription aus DB
  - One-Click Unsubscribe (RFC 8058)

### GET/POST `/setup`
- **Funktion:** `setup()`
- **Auth erforderlich:** Nein (First-Run Wizard)
- **Beschreibung:** Initial Setup Wizard
- **Parameter:**
  - `admin_username` (POST): Admin-Benutzername (default: "admin")
  - `admin_password` (POST): Admin-Passwort (erforderlich)
  - `base_url` (POST): Öffentliche Base-URL
  - `language` (POST): Sprache (de/en)
  - `gallery_name` (POST): Galerie-Titel
- **Rückgabe:** HTML (Template: `setup.html`) oder HTTP 302 Redirect zu Admin-Login
- **Features:**
  - Zeigt nur wenn `setup_completed` nicht "1"
  - Setzt Admin-Credentials und Settings
  - Speichert Passwort als PBKDF2-Hash

---

## ADMIN Blueprint (`/admin`)

Admin-Interface für Verwaltung von Events, Settings, Geräten, etc. **Alle diese Routen erfordern Admin-Auth** (`session["admin_logged_in"] == True`).

### GET/POST `/login`
- **Funktion:** `login()`
- **Auth erforderlich:** Nein (Öffentlich, aber Auth wird überprüft)
- **Beschreibung:** Admin-Login-Formular
- **Parameter:**
  - `username` (POST): Admin-Benutzername
  - `password` (POST): Admin-Passwort
- **Rückgabe:** HTML (Template: `admin/login.html`) oder HTTP 302 Redirect zu Dashboard
- **Features:**
  - Unterstützt PBKDF2/Scrypt und Legacy SHA256
  - Speichert `admin_logged_in=True` in Session
  - Zeigt Setup-Link wenn Setup nicht abgeschlossen

### GET `/logout`
- **Funktion:** `logout()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Admin-Logout
- **Parameter:** Keine
- **Rückgabe:** HTTP 302 Redirect zu Login
- **Features:** Löscht `admin_logged_in` aus Session

### GET `/`
- **Funktion:** `index()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Admin-Start-Seite
- **Parameter:** Keine
- **Rückgabe:** HTTP 302 Redirect zu `/admin/dashboard`

### GET `/dashboard`
- **Funktion:** `dashboard()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Admin-Dashboard mit Statistiken und Event-Übersicht
- **Parameter:** Keine
- **Rückgabe:** HTML (Template: `admin/dashboard.html`)
- **Features:**
  - Statistiken: Gesamt Events, Fotos, Storage, aktive Geräte
  - Event-Liste mit Cover, Foto-Count, Datum
  - Storage-Berechnung in Human-Readable Format

### GET `/events`
- **Funktion:** `events()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Liste aller Events
- **Parameter:** Keine
- **Rückgabe:** HTML (Template: `admin/events/list.html`)
- **Features:**
  - Alle Events sortiert nach Datum (neueste zuerst)
  - Cover, Name, Foto-Count, Status
  - Inline Featured-Toggle
  - Links zu Event-Edit

### GET/POST `/events/new`
- **Funktion:** `new_event()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Neues Event erstellen
- **Parameter:**
  - `name` (POST): Event-Name (erforderlich)
  - `slug` (POST): Event-Slug (auto-generiert wenn leer)
  - `date` (POST): Datum
  - `description` (POST): Beschreibung
  - `password` (POST): Passwort für Galerie-Zugang
  - `retention_days` (POST): Aufbewahrungsdauer (default: 90)
  - `kiosk_text` (POST): Text im Kiosk-Modus
  - `published` (POST): Checkbox zum Veröffentlichen
- **Rückgabe:** HTML (Template: `admin/events/edit.html`) oder HTTP 302 Redirect zu Edit
- **Features:**
  - Slug-Auto-Generierung mit `_slugify()`
  - Upload-Token wird auto-generiert
  - Status: "draft" oder "live"
  - Trigger Event-Publish-Benachrichtigung wenn published

### GET/POST `/events/<event_id>/edit`
- **Funktion:** `edit_event(event_id: int)`
- **Auth erforderlich:** Ja
- **Beschreibung:** Event-Details bearbeiten
- **Parameter:**
  - `event_id` (URL): Event-ID
  - `name`, `slug`, `date`, `description`, `password`, `retention_days`, `kiosk_text`, `published` (POST)
  - `cover` (FILE): Cover-Bild
  - `action` (POST): "save" oder "delete_cover"
- **Rückgabe:** HTML (Template: `admin/events/edit.html`) oder HTTP 302 Redirect zu selbst
- **Features:**
  - Cover-Upload mit Optimierung
  - Foto-Galerie im Edit-Form
  - Cover-Delete Option
  - Passwort-Change Unterstützung
  - Trigger Publish-Benachrichtigung bei Status-Wechsel

### POST `/events/<event_id>/delete`
- **Funktion:** `delete_event(event_id: int)`
- **Auth erforderlich:** Ja
- **Beschreibung:** Event komplett löschen
- **Parameter:**
  - `event_id` (URL): Event-ID
- **Rückgabe:** HTTP 302 Redirect zu `/admin/events`
- **Features:** Löscht Event und alle zugeordneten Fotos aus DB

### POST `/media/<media_id>/delete`
- **Funktion:** `delete_media(media_id: int)`
- **Auth erforderlich:** Ja
- **Beschreibung:** Einzelnes Foto löschen
- **Parameter:**
  - `media_id` (URL): Media-ID
- **Rückgabe:** JSON `{"ok": True}`
- **Features:** Asynchron (JS-Call), Datei-Cleanup in DB

### POST `/events/<event_id>/toggle-featured`
- **Funktion:** `toggle_event_featured(event_id: int)`
- **Auth erforderlich:** Ja
- **Beschreibung:** Featured-Status togglen
- **Parameter:**
  - `event_id` (URL): Event-ID
- **Rückgabe:** HTTP 302 Redirect zu Referrer
- **Features:** Bestimmt Sortierung auf Homepage

### GET/POST `/settings`
- **Funktion:** `settings()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Admin-Account Settings (Tab: account)
- **Parameter:** Keine
- **Rückgabe:** HTML (Template: `admin/settings/index.html`)
- **Features:** Zeigt aktuellen Username und Branding-Logo-Status

### POST `/settings/account`
- **Funktion:** `settings_account()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Admin-Zugangsdaten ändern
- **Parameter:**
  - `admin_username` (POST): Neuer Benutzername
  - `new_password` (POST): Neues Passwort (optional)
- **Rückgabe:** HTTP 302 Redirect zu `/admin/settings`
- **Features:**
  - Hash-Berechnung nur bei Passwort-Input
  - Beide Felder optional

### POST `/settings/logo`
- **Funktion:** `settings_logo()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Branding-Logo hochladen
- **Parameter:**
  - `logo` (FILE): Logo-Datei
- **Rückgabe:** HTTP 302 Redirect zu Settings
- **Features:**
  - PNG-Optimierung mit Alpha-Channel
  - Thumbnail: 800x800px max

### POST `/settings/logo/delete`
- **Funktion:** `settings_logo_delete()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Logo löschen
- **Parameter:** Keine
- **Rückgabe:** HTTP 302 Redirect zu Settings
- **Features:** Setzt `branding_logo_path` auf leer

### GET/POST `/settings/homepage`
- **Funktion:** `settings_homepage()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Homepage-Einstellungen
- **Parameter:**
  - `homepage_headline` (POST): Haupt-Überschrift
  - `homepage_subtext` (POST): Sub-Text
  - `homepage_show_galleries` (POST): Checkbox
  - `homepage_show_empty_events` (POST): Checkbox
  - `footer_custom_link_label`, `footer_custom_link_url` (POST)
  - `footer_show_powered_by` (POST): Checkbox
  - `guest_upload_enabled`, `zip_download_enabled`, `zip_download_admin_only` (POST): Checkboxes
  - `hero_image` (FILE): Hero-Bild
  - `action` (POST): "delete_hero_image"
- **Rückgabe:** HTML (Template: `admin/settings/homepage.html`) oder HTTP 302
- **Features:**
  - Hero-Bild: JPEG optimiert, max 2400x1600px
  - Kontrolliert Features auf Homepage

### GET/POST `/settings/notifications`
- **Funktion:** `settings_notifications()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Benachrichtigungs-Konfiguration (Telegram, Discord, Webhook)
- **Parameter:**
  - `notify_tg_token`, `notify_tg_chat_id` (POST): Telegram
  - `notify_discord_webhook` (POST): Discord Webhook URL
  - `notify_webhook_url` (POST): Generischer Webhook
  - `notify_quiet_from`, `notify_quiet_to` (POST): Ruhezeit (HH:MM)
  - `notify_on_upload`, `notify_on_publish`, `notify_on_new_device` (POST): Feature-Flags
- **Rückgabe:** HTML (Template: `admin/settings/notifications.html`) oder HTTP 302
- **Features:** Speichert Konfiguration in DB

### POST `/settings/notifications/test/<channel>`
- **Funktion:** `settings_notifications_test(channel: str)`
- **Auth erforderlich:** Ja
- **Beschreibung:** Test-Benachrichtigung senden
- **Parameter:**
  - `channel` (URL): "telegram", "discord", oder "webhook"
- **Rückgabe:** JSON `{"ok": bool, "error": str or null}`
- **Features:** Sendet Test-Message für Konfiguration-Verifikation

### GET/POST `/settings/legal`
- **Funktion:** `settings_legal()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Impressum und Datenschutz-Text
- **Parameter:**
  - `impressum_text` (POST): Impressum (Markdown)
  - `datenschutz_text` (POST): Datenschutz (Markdown)
- **Rückgabe:** HTML (Template: `admin/settings/legal.html`) oder HTTP 302
- **Features:** Speichert als Markdown, wird im Public-Interface HTML-konvertiert

### GET/POST `/settings/faq`
- **Funktion:** `settings_faq()`
- **Auth erforderlich:** Ja
- **Beschreibung:** FAQ-Items verwalten
- **Parameter:**
  - `action` (POST): "save_intro", "add_item", "delete_item", "move_up", "move_down"
  - `intro_text` (POST): FAQ-Intro-Text
  - `q`, `a` (POST): Frage und Antwort
  - `attachment` (FILE): PDF/Image-Anhang
  - `item_id` (POST): Item-ID für Delete/Move
- **Rückgabe:** HTML (Template: `admin/settings/faq.html`) oder HTTP 302
- **Features:**
  - Items in JSON gespeichert
  - Attachments: PDF, JPG, PNG, GIF
  - Sortierbar (Move Up/Down)

### GET `/analytics`
- **Funktion:** `analytics()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Analytics-Übersicht (alle Events)
- **Parameter:** Keine
- **Rückgabe:** HTML (Template: `admin/analytics.html`)
- **Features:**
  - License-Check für Analytics
  - Aggregierte Totals: Views, Downloads, ZIP-Downloads, WhatsApp-Shares
  - Event-Liste mit Analytics

### GET `/analytics/<event_id>`
- **Funktion:** `analytics_event(event_id: int)`
- **Auth erforderlich:** Ja
- **Beschreibung:** Detaillierte Analytics für ein Event
- **Parameter:**
  - `event_id` (URL): Event-ID
- **Rückgabe:** HTML (Template: `admin/analytics_event.html`)
- **Features:**
  - Event-Stats
  - Top 12 Fotos nach Download-Count
  - Raw Stats aus DB

### GET `/devices`
- **Funktion:** `devices()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Übersicht aller registrierten Geräte
- **Parameter:** Keine
- **Rückgabe:** HTML (Template: `admin/devices.html`)
- **Features:** Geräte-Liste mit Status und Links

### GET/POST `/devices/new`
- **Funktion:** `new_device()`
- **Auth erforderlich:** Ja
- **Beschreibung:** Neues Gerät registrieren
- **Parameter:**
  - `name` (POST): Geräte-Name (erforderlich)
  - `default_watch_path` (POST): Überwachungs-Pfad
  - `default_event_glob` (POST): Event-Pattern
- **Rückgabe:** HTML (Template: `admin/device_edit.html`) oder HTTP 302
- **Features:** Auto-generiert API-Key

### GET/POST `/devices/<device_id>/edit`
- **Funktion:** `edit_device(device_id: int)`
- **Auth erforderlich:** Ja
- **Beschreibung:** Geräte-Konfiguration
- **Parameter:**
  - `device_id` (URL): Device-ID
  - `name`, `default_watch_path`, `default_event_glob` (POST)
  - `active` (POST): Checkbox
  - `landing_event_id`, `landing_headline`, `landing_subtext`, `landing_bg_path` (POST)
- **Rückgabe:** HTML (Template: `admin/device_edit.html`) oder HTTP 302
- **Features:**
  - Landing-Page Felder (für `/screen/<device_id>`)
  - Event-Dropdown
  - Active/Inactive Toggle

### POST `/devices/<device_id>/delete`
- **Funktion:** `delete_device(device_id: int)`
- **Auth erforderlich:** Ja
- **Beschreibung:** Gerät löschen
- **Parameter:**
  - `device_id` (URL): Device-ID
- **Rückgabe:** HTTP 302 Redirect zu `/admin/devices`
- **Features:** Löscht Geräte-Eintrag aus DB

### POST `/devices/<device_id>/regen-key`
- **Funktion:** `regen_device_key(device_id: int)`
- **Auth erforderlich:** Ja
- **Beschreibung:** API-Key erneuern
- **Parameter:**
  - `device_id` (URL): Device-ID
- **Rückgabe:** HTTP 302 Redirect zu `/admin/devices`
- **Features:** Generiert neuen Token für Gerät

### GET `/devices/<device_id>/config.json`
- **Funktion:** `device_config(device_id: int)`
- **Auth erforderlich:** Ja
- **Beschreibung:** JSON-Config-Export für Gerät
- **Parameter:**
  - `device_id` (URL): Device-ID
- **Rückgabe:** JSON mit Download-Header
- **Features:**
  - Server-URL
  - API-Key
  - Device-ID und Name
  - Watch-Paths
  - Verwendbar von Uploader-Tools

---

## API Blueprint (`/api`)

Programmatische API für Device-Upload, Guest-Upload, Analytics-Tracking, etc.

### POST `/upload`
- **Funktion:** `upload()`
- **Auth erforderlich:** Ja (X-API-Key Header oder Form/Query Parameter)
- **Beschreibung:** Haupt-Upload-Endpunkt für Fotobox-Geräte
- **Parameter:**
  - `X-API-Key` (Header): Device API-Key
  - `event_id` oder `event_slug` (Form/Query): Target Event
  - `file` oder `photo` (FILE): Zu uploadende Datei
- **Rückgabe:** JSON `{"ok": True, "id": media_id, "path": thumb_path}` (HTTP 200)
- **Errors:**
  - `{"ok": False, "error": "..."}` (HTTP 401/403/404/400/500)
- **Features:**
  - Auth via API-Key
  - Geräte-Activity Tracking
  - Batch-Upload-Benachrichtigungen
  - Media-Processing (WebP, Thumb)

### POST `/guest-upload`
- **Funktion:** `guest_upload()`
- **Auth erforderlich:** Nein (Gallery Session-Auth)
- **Beschreibung:** Gast-Upload zu Event
- **Parameter:**
  - `event_id` (Form): Event-ID
  - `file` (FILE): Zu uploadende Datei
- **Rückgabe:** JSON `{"ok": True, "id": media_id}`
- **Errors:** 403 wenn Guest-Upload deaktiviert, 404 wenn Event nicht gefunden
- **Features:**
  - Setting: `guest_upload_enabled` (default: "0")
  - Prüfung von Gallery-Access (Passwort)
  - Same Media-Processing wie Device-Upload

### POST `/admin-upload`
- **Funktion:** `admin_upload()`
- **Auth erforderlich:** Ja (Session Admin)
- **Beschreibung:** Admin-Photo-Upload (von Event-Edit-Page)
- **Parameter:**
  - `event_id` (Form): Event-ID
  - `file` (FILE): Zu uploadende Datei
- **Rückgabe:** JSON `{"ok": True, "id": media_id, "thumb_url": path}`
- **Errors:** 401 wenn nicht authenticated, 404 wenn Event nicht gefunden
- **Features:** Für Admin-Interface Drag-Drop Upload

### POST `/photo/<photo_id>/like`
- **Funktion:** `like_photo(photo_id: int)`
- **Auth erforderlich:** Nein (Device-ID in Session)
- **Beschreibung:** Like auf Foto toggle
- **Parameter:**
  - `photo_id` (URL): Media-ID
- **Rückgabe:** JSON `{"ok": True, "liked": bool}`
- **Features:**
  - Device-ID wird in Session gespeichert (anonym)
  - Like-Count wird in DB tracked

### GET `/status`
- **Funktion:** `status()`
- **Auth erforderlich:** Nein
- **Beschreibung:** API-Status und Version
- **Parameter:** Keine
- **Rückgabe:** JSON `{"ok": True, "events": count, "version": "2.0.0"}`
- **Features:** Für Health-Checks und Kompatibilität-Tests

### POST `/track`
- **Funktion:** `track()`
- **Auth erforderlich:** Nein
- **Beschreibung:** Analytics-Tracking Endpoint
- **Parameter (JSON):**
  - `event_id` (int, erforderlich)
  - `action` (string, erforderlich): view_gallery, download, zip_download, like, unlike, share_whatsapp, share_link, whatsapp_request
  - `media_id` (int, optional)
- **Rückgabe:** JSON `{"ok": True}` oder Error
- **Features:**
  - Speichert Stats in DB
  - Ermöglicht Analytics-Dashboard

### POST `/events/ensure`
- **Funktion:** `ensure_event()`
- **Auth erforderlich:** Ja (X-Api-Key Header)
- **Beschreibung:** Auto-Event-Creation für Windows-Uploader
- **Parameter (JSON):**
  - `name` (string, erforderlich): Event-Name
  - `slug` (string, optional): Event-Slug (auto-generiert wenn leer)
- **Rückgabe:** JSON `{"ok": True, "event_id": id, "slug": slug, "upload_token": token, "created": bool}` (201 wenn neu)
- **Features:**
  - Gibt existierendes Event zurück wenn vorhanden
  - Erstellt Event mit Auto-Slug wenn nicht existiert
  - Verwendbar von Auto-Upload-Tools

### POST `/notify/subscribe`
- **Funktion:** `notify_subscribe()`
- **Auth erforderlich:** Nein
- **Beschreibung:** E-Mail-Benachrichtigung abonnieren (Double Opt-In)
- **Parameter (JSON):**
  - `eventSlug` oder `event_id` (string/int, erforderlich)
  - `email` (string, erforderlich)
- **Rückgabe:** JSON `{"ok": True, "message": "Bestätigungsmail gesendet."}`
- **Errors:** 400 bei ungültiger Email, 404 wenn Event nicht gefunden, 409 wenn bereits abonniert
- **Features:**
  - Token-Generierung für Bestätigung
  - Confirmation-Email wird gesendet
  - RFC-konforme Email-Validierung

### GET `/notify/confirm/<token>`
- **Funktion:** `notify_confirm_redirect(token: str)`
- **Auth erforderlich:** Nein
- **Beschreibung:** API-Shortcut zu `/notify/confirm/<token>`
- **Parameter:**
  - `token` (URL): Confirmation-Token
- **Rückgabe:** HTTP 302 Redirect zu `public.notify_confirm`
- **Features:** Ermöglicht Zugriff über `/api/notify/confirm/<token>` Alternative

---

## Route-Zusammenfassung nach Kategorie

| Kategorie | Routes | Anzahl |
|-----------|--------|--------|
| **Authentifizierung** | /login, /logout, /setup | 3 |
| **Homepage & Navigation** | /, /lang/<code>, /healthz | 3 |
| **Galerie & Fotos** | /gallery/<slug>, /s/<share_token>, /gallery/<slug>/qr.png, /gallery/<slug>/sharepic.png, /gallery/<slug>/print, /gallery/<slug>/download.zip | 6 |
| **Kiosk & Display** | /kiosk/<slug>, /screen/<device_id> | 2 |
| **Impressum & Datenschutz** | /impressum, /datenschutz, /faq | 3 |
| **E-Mail Subscriptions** | /notify/confirm/<token>, /notify/unsubscribe/<token> | 2 |
| **Branding & Assets** | /branding/logo, /data/<path:path> | 2 |
| **Events Management** | /events, /events/new, /events/<id>/edit, /events/<id>/delete, /events/<id>/toggle-featured | 5 |
| **Media Management** | /media/<id>/delete | 1 |
| **Settings** | /settings, /settings/account, /settings/logo, /settings/logo/delete, /settings/homepage, /settings/notifications, /settings/notifications/test/<channel>, /settings/legal, /settings/faq | 9 |
| **Devices** | /devices, /devices/new, /devices/<id>/edit, /devices/<id>/delete, /devices/<id>/regen-key, /devices/<id>/config.json | 6 |
| **Analytics** | /analytics, /analytics/<event_id> | 2 |
| **Dashboard** | /dashboard, / (admin index) | 2 |
| **Device Upload (API)** | /upload, /guest-upload, /admin-upload | 3 |
| **Photo Actions (API)** | /photo/<id>/like | 1 |
| **Health & Info (API)** | /status | 1 |
| **Analytics Tracking (API)** | /track | 1 |
| **Auto-Event Creation (API)** | /events/ensure | 1 |
| **E-Mail Subscription (API)** | /notify/subscribe, /notify/confirm/<token> | 2 |

**Gesamt:** ~55 Routes und Endpoints
