# PhotoBox Gallery v2 — Projektstatus & Roadmap

**Stand**: 2026-03-04 bis 2026-04-20
**Versionen**: v2.0 (Kern), v2.5.1 (Quick-Fixes), v2.6+ (geplant)

---

## Aktueller Stand

### Implementiert & funktionsfähig

#### Kern-Features (vollständig)

| Feature | Status | Quelldatei(en) | Beschreibung |
|---------|--------|----------------|-------------|
| **Authentifizierung** | ✅ | `app/auth.py` | Login mit Username/Passwort, Sessions, SMTP-Test |
| **Datenbank-Schema** | ✅ | `app/db.py` | Events, Media, Devices, Settings, Analytics |
| **Media-Verwaltung** | ✅ | `app/media.py` | Foto-Upload, Thumbnail-Generierung, Metadaten |
| **Benachrichtigungen** | ✅ | `app/notifier.py` | Telegram, Discord, HTTP-Webhook, Quiet Hours |
| **E-Mail-System** | ✅ | `app/emailer.py` | SMTP-Integration für Test-Mails |
| **Internationalisierung** | ✅ | `app/i18n/{de,en}.py`, `app/i18n/__init__.py` | Deutsch + Englisch vollständig |
| **Öffentliche Galerie** | ✅ | `app/blueprints/public.py` | Galerie-Anzeige, Passwort-Schutz, Filter |
| **Admin Dashboard** | ✅ | `app/blueprints/admin.py` | Event-Verwaltung, CRUD-Operationen |
| **API Upload** | ✅ | `app/blueprints/api.py` | POST /api/upload, Rate-Limiting, Deduplication |
| **Windows Uploader** | ✅ | `windows_uploader/photobox_uploader_v2.py` | Auto-Upload mit SQLite-Tracking |
| **System-Tray-GUI** | ✅ | `windows_uploader/photobox_tray.py` | Tray-Anwendung mit Status-Anzeige |
| **Docker-Setup** | ✅ | `docker-compose.yml` | Containerisierte Bereitstellung |

#### Admin Panel Features (vollständig)

| Feature | Status | Quelldatei(en) |
|---------|--------|----------------|
| Event CRUD | ✅ | `admin.py`, `admin/events.html` |
| Device Management | ✅ | `admin.py`, `admin/devices.html` |
| Settings (SMTP, Notifications, Legal) | ✅ | `admin.py`, `admin/settings/` |
| Logo & Branding Upload | ✅ | `admin.py` |
| Foto-Verwaltung | ✅ | `admin.py`, `admin/gallery.html` |
| Statistiken-Grundlagen | ⚠️ | `admin.py` | Views, Downloads gezählt, aber kein Dashboard |

#### öffentliche Seiten (implementiert)

| Feature | Status | Quelldatei(en) |
|---------|--------|----------------|
| Homepage | ✅ | `public/home.html` |
| Galerie-Ansicht | ✅ | `public/gallery.html`, PhotoSwipe |
| Passwort-Dialog | ✅ | `public/password.html` |
| Kiosk-Seite | ✅ | `public/kiosk.html` |
| Impressum | ✅ | `public/impressum.html` |
| Datenschutz | ✅ | `public/datenschutz.html` |
| Footer | ✅ | `layout_public.html` |

---

### Teilweise implementiert / Work in Progress

#### Features mit TODOs oder fehlenden Komponenten

| Feature | Status | Problem | Code-Ort |
|---------|--------|---------|----------|
| **Dark/Light Mode** | ⚠️ | CSS vorhanden, aber kein UI-Toggle | CSS mit `--dark-mode`, kein .html-Toggle |
| **Sharepic** | ⚠️ | Grundlogik da, aber Bild-Upload fehlt | `media.py` hat `generate_sharepic()`, aber kein Upload-Interface |
| **Rate-Limiting** | ⚠️ | Einfaches IP-basiertes Limit, kein Sliding-Window | `api.py` nutzt `check_rate_limit()` |
| **Foto-Deduplication** | ⚠️ | SHA256-Vergleich vorhanden, aber nicht implementiert | `api.py` kommentiert: `# TODO: SHA256 dedup` |
| **Like-System** | ⚠️ | Database-Feld vorhanden, aber Stats nicht gezählt | `POST /api/like` vorhanden, aber kein Analytics |
| **Download-Statistiken** | ⚠️ | Gezählt, aber nicht im Admin-Dashboard sichtbar | `stats.downloads`, aber kein UI |
| **Logo-Upload** | ⚠️ | Settings-Feld vorhanden, Upload-Verarbeitung unklar | `admin/settings/branding.html` vorhanden |

#### Features ohne Test-Implementierung

| Feature | Status | Anmerkung |
|---------|--------|-----------|
| **Lizenz-System** | ❌ | `license.py` aus v1 nicht integriert, kein Pricing-Tier-Check |
| **Setup-Wizard** | ❌ | `/setup` Route nicht vorhanden, manuell via Admin |
| **CLI-Befehle** | ❌ | `cli.py` nicht vorhanden (`prune`, `stats` Commands) |
| **E-Mail-Abo (Double Opt-In)** | ❌ | Subscribe-Form und Verification nicht implementiert |
| **Webhook-Verwaltung** | ❌ | Eingebaute Webhooks, aber keine Admin-UI zum Konfigurieren |
| **Analytics-Dashboard** | ❌ | Daten werden gesammelt, aber kein Reporting-Interface |
| **Automatischer Cleanup** | ❌ | `retention_days` Feld in DB, aber kein Cron/Scheduler |
| **iFrame Embed** | ❌ | `?embed=1` Parameter nicht implementiert |
| **Print QR-Code Seite** | ❌ | `/gallery/<slug>/print` nicht vorhanden |
| **Einzelnes Foto als Share-Link** | ❌ | `share_token` Feld fehlt in Media-Model |
| **Language Switcher UI** | ❌ | i18n-System vorhanden, aber keine Button/Dropdown in UI |
| **Access-Token-Auth** | ❌ | `?access_token=` Parameter nicht unterstützt |
| **2FA (TOTP)** | ❌ | Geplant, nicht implementiert |
| **Multi-Arch Docker (ARM64)** | ❌ | Dockerfile vorhanden, aber kein buildx-Setup |

---

### Geplant aber nicht implementiert

#### Aus PLAN_V2.md – PRIO 1 (Öffentliche Seiten)

**Status**: Einige Basics vorhanden, aber nicht vollständig

- ❌ **Homepage mit Event-Liste**: Vorhanden, aber keine Live-Event-Vorschau
- ❌ **Kiosk-Modus-Enhancement**: QR-Code da, aber kein Live-Refresh & Slideshow
- ✅ Impressum / Datenschutz: Vorhanden
- ❌ **Footer-Customization**: Powered-By-Toggle vorhanden, Custom-Link fehlt
- ⚠️ **Galerie-Filter**: Foto/Print/GIF/Video-Tabs vorhanden, aber nicht im aktuellen Design

#### Aus PLAN_V2.md – PRIO 2 (Admin-Einstellungen)

- ⚠️ **Homepage-Einstellungen**: Settings vorhanden, kein Hero-Image-Upload
- ✅ **Notification-Einstellungen**: Voll implementiert
- ✅ **Legal-Settings** (Impressum/DSE): Voll implementiert
- ❌ **Kiosk-Settings pro Event**: Nur global, nicht pro Event

#### Aus PLAN_V2.md – PRIO 3 (Design)

- ❌ **Masonry-Grid Galerie**: Aktuell einfaches Grid, kein Pinterest-Style
- ✅ **Lightbox**: PhotoSwipe v5 integriert
- ❌ **Design-System (CSS Custom Properties)**: Nicht dokumentiert/nicht genutzt
- ❌ **Dark/Light-Mode Toggle**: CSS vorbereitet, UI fehlt
- ⚠️ **Mobile-First Responsive**: Grund-Design responsive, aber nicht optimiert

#### Aus PLAN_V2.md – PRIO 4 (Neue Features)

- ⚠️ **HTTP-Webhooks**: Allgemeines System vorhanden, aber keine Admin-UI
- ❌ **Foto-Download (ZIP)**: Nur "alle" implementiert, Auswahl fehlt
- ❌ **Event-Status-Seite**: "Fotos werden hochgeladen..." nicht vorhanden
- ❌ **Video-Verarbeitung**: Video-Upload funktioniert, aber keine Video-Thumbnails
- ❌ **GIF-Verarbeitung**: GIF-Dateien werden hochgeladen, aber keine animierten Thumbnails

---

## Bekannte Probleme & Bugs

### TODO/FIXME Kommentare im Code

#### app/media.py
- **FIXME**: SHA256-Deduplication für Upload-Vermeidung ist kommentiert
- **TODO**: Video-Thumbnail-Generierung mit ffmpeg nicht implementiert
- **TODO**: GIF-Animation in Thumbnails nicht unterstützt

#### app/blueprints/api.py
- **TODO**: Selektiver ZIP-Download (Nutzer kann nur alle auswählen, nicht einzelne)
- **TODO**: Share-Token-System für einzelne Fotos nicht implementiert
- **TODO**: ETag-basierte Auslieferung für Caching nicht implementiert

#### app/blueprints/admin.py
- **TODO**: Geplantes Veröffentlichen (scheduled_live_at) nicht implementiert
- **TODO**: Analytics-Dashboard komplett fehlend
- **TODO**: Event-spezifische Kiosk-Konfiguration nicht vorhanden

#### windows_uploader/photobox_uploader.py
- **TODO**: Auto-Event-Slug-Generierung könnte robuster sein
- **FIXME**: Fehlerbehandlung bei fehlender API-Response könnte besser sein

### Bekannte Lücken aus GAP_ANALYSE.md

#### ÖFFENTLICHE SEITEN (4 Lücken)

| Feature | Website verspricht | v2 Status | Nächste Aktion |
|---------|-------------------|-----------|----------------|
| **Galerie-Filter (Fotos/Prints/GIFs/Videos)** | ✓ | ❌ | Media-Kind-Tabs in UI hinzufügen |
| **Dark/Light Mode Toggle** | ✓ | ⚠️ | Button in Header, localStorage für Preference |
| **noindex Meta-Tag (DSGVO)** | ✓ | ❌ | In `layout_public.html` hinzufügen |
| **iFrame Embed** | ✓ | ❌ | Route `/gallery/<slug>?embed=1` |
| **WhatsApp Share** | ✓ | ⚠️ | Vorhanden, aber Meta-Tags könnten besser sein |
| **Print QR-Code Seite** | ✓ | ❌ | `/gallery/<slug>/print` Endpoint + Template |
| **Email-Abo (Double Opt-In)** | ✓ | ❌ | Subscribe-Form + Email-Verification |
| **Einzelnes Foto Share-Link** | ✓ | ❌ | Media.share_token + Route |
| **Access Token Auth** | ✓ | ❌ | `?access_token=` Parameter-Support |

#### ADMIN PANEL (3 Lücken)

| Feature | Website verspricht | v2 Status |
|---------|-------------------|-----------|
| **Corporate Identity pro Event** | ✓ | ❌ |
| **Geplantes Veröffentlichen** | ✓ | ❌ |
| **Analytics-Dashboard** | ✓ | ❌ |
| **Logo Upload Logic** | ✓ | ⚠️ |
| **2FA (TOTP)** | – | ❌ |

#### UPLOAD / API (5 Lücken)

| Feature | v2 Status |
|---------|-----------|
| **Deduplication (SHA256)** | ❌ |
| **POST /api/events/ensure** | ✅ (vorhanden) |
| **Video-Thumbnails** | ❌ |
| **Analytics Tracking** | ⚠️ |
| **Selektiver ZIP-Download** | ❌ |

#### SETUP & DEPLOYMENT (6 Lücken)

| Feature | Status |
|---------|--------|
| **Setup-Wizard** | ❌ |
| **Lizenz-System** | ❌ |
| **BASE_URL für absolute URLs** | ❌ |
| **Multi-Arch Docker** | ⚠️ |
| **CLI-Befehle** | ❌ |
| **Automatischer Retention-Cron** | ❌ |

#### DSGVO / DATENSCHUTZ (2 Lücken)

| Feature | Status |
|---------|--------|
| **noindex Meta-Tag** | ❌ |
| **DSGVO-Checkbox auf Abo-Form** | ❌ |

---

## i18n / Mehrsprachigkeit

### Wie funktioniert das i18n-System?

**Dateistruktur**:
```
app/i18n/
├── __init__.py         # Translation-Funktion t()
├── de.py               # Deutsche Strings (STRINGS dict)
└── en.py               # Englische Strings (STRINGS dict)
```

**Verwendung in Templates (Jinja2)**:
```html
<h1>{{ t('dashboard.title') }}</h1>
<p>{{ t('msg.device.created', name=device.name) }}</p>
```

**Verwendung in Python**:
```python
from app.i18n import t
flash(t('msg.event.saved'))
```

**Fallback-Logik** (in `app/i18n/__init__.py`):
1. Prüfe aktuell angeforderte Sprache (aus `g.lang`)
2. Falls nicht gefunden und nicht DE: Fallback auf Deutsch (de.py)
3. Falls immer noch nicht gefunden: Rückgabe des Keys selbst

### Welche Sprachen sind implementiert?

| Sprache | Datei | Status | Strings |
|---------|-------|--------|---------|
| **Deutsch** | `de.py` | ✅ Vollständig | ~200+ Keys |
| **Englisch** | `en.py` | ⚠️ Teilweise | ~150+ Keys |

### Wie werden Strings referenziert?

**Key-Format**: `domain.feature.item`

**Beispiele**:
- `app.title` = "Fotobox Galerie"
- `nav.dashboard` = "Dashboard"
- `msg.device.created` = "Gerät angelegt: {name}"
- `setup.welcome` = "Willkommen bei Photobox Gallery!"
- `dashboard.stat.photos` = "Fotos"

**Parametrisierung**:
```python
t('msg.device.created', name=device.name)
# → "Gerät angelegt: Mein Gerät"
```

### Wie viele Strings gibt es?

**Deutsch (de.py)**: ca. 220+ Strings
**Englisch (en.py)**: ca. 150+ Strings

**Domains in de.py**:
- `app` (2 Keys)
- `layout` (2 Keys)
- `nav` (5 Keys)
- `msg` (33+ Keys für Benachrichtigungen)
- `setup` (30+ Keys für Setup-Wizard)
- `login` (7 Keys)
- `dashboard` (12+ Keys)
- `stats` (13 Keys)
- `devices` (20+ Keys)
- `new_event` (10+ Keys)
- `event` (10+ Keys)
- `gallery` (15+ Keys)
- `admin` (20+ Keys)

### Lücken: Strings die fehlen

**Englische Übersetzung unvollständig**:
- ❌ `setup.skip` Hinweis Text (zu lang, nicht übersetzt)
- ❌ Einige Admin-Interface Texte
- ❌ Error Messages teilweise nur auf Deutsch

**Generell fehlend**:
- ❌ Französisch / Weitere Sprachen
- ❌ Strings für "Kiosk-Modus" (kiosk.* Keys)
- ❌ Strings für Analytics-Dashboard
- ❌ Strings für neue Features (wenn hinzugefügt)

---

## Changelog / Versionshistorie

### v2.5.1 (2026-02-02) – Quick-Fixes Phase 1

**Type**: Bug-Fixes & Quick-Wins

**Gefixt**:
1. ✅ Windows ZIP Download – fehlende Imports (BytesIO, zipfile)
2. ✅ WhatsApp Share – Open Graph Meta-Tags hinzugefügt
3. ✅ Logo zu klein – von 34px auf 48px vergrößert
4. ✅ Foto-Ansicht zu breit – Max-Width 900px, object-fit: contain
5. ✅ Verwirrende Begriffe – "Haltedauer", "Druck-Schwelle" verbessert
6. ✅ Footer – Version, Links, Entwickler-Kontakt hinzugefügt

**Bekannte offene Aufgaben**:
- Sharepic komplett neu (4-6h)
- Headerbild Crop/Position (2-3h)
- Icon-Layout verbessern (2-3h)
- Lizenz-Info im Admin (1-2h)
- Startseite + Featured Galleries (4-6h)
- Impressum/Datenschutz/FAQ-Admin (3-4h)
- Vollständige Mehrsprachigkeit (6-8h)
- Apple-Like UI Redesign (12-16h)
- Picdrop-Features (20-30h)

**Gesamtaufwand noch offen**: ~52-75 Stunden (6-9 Arbeitstage)

### v2.0 (2026-03-04) – Komplett Neuaufbau

**Meilesteine**:
- Komplette Flask-Refaktorierung (Blueprints statt Monolith)
- Neue i18n-Integration (DE/EN)
- Windows Uploader v2 (Python, Single + Auto-Event Modi)
- System-Tray-GUI (photobox_tray.py)
- Docker-Compose Setup
- Event/Device Management
- Umfassende Admin-Funktionalität
- öffentliche Galerie mit PhotoSwipe

---

## Nächste Schritte (empfohlen)

### Phase 1: MVP Verbesserungen (1-2 Wochen)

**Priorisiert nach Impact + Aufwand**:

1. **Setup-Wizard** (~2-3h) 🔴
   - /setup Route mit Wizard für First-Run
   - Admin-Credentials, Sprache, Base-URL
   - Großer Impact für User Experience
   - **Empfehlung**: JETZT machen

2. **Impressum/Datenschutz Admin-UI** (~3-4h) 🔴
   - Admin kann Texte eingeben
   - Markdown-Support
   - Vorlagen mit DSGVO-Compliance
   - **Warum**: Rechtliche Anforderung

3. **License-System Integration** (~4-6h) 🔴
   - license.py aus v1 integrieren
   - Tier-Check (Free/Pro/Enterprise)
   - Lizenz-Info im Admin Dashboard
   - Trial-Status anzeigen
   - **Warum**: Monetarisierung

4. **Sharepic Redesign** (~4-6h) 🔴
   - Hintergrund-Upload (Admin)
   - Text/QR-Code konfigurierbar
   - Generierung verbessern
   - **Warum**: User-Feature, hohes Impact

5. **Startseite + Featured Events** (~4-6h) 🟡
   - Homepage mit Event-Vorschau
   - Featured Galleries
   - Konfigurierbar im Admin
   - **Warum**: Public-Deployment ready

### Phase 2: Design-Überarbeitung (1-2 Wochen)

6. **CSS Design-System** (~6-8h) 🟡
   - CSS Custom Properties implementieren
   - Konsistente Typografie
   - Dark/Light Mode Toggle
   - Mobile-first Breakpoints

7. **UI/UX Überarbeitung** (~12-16h) 🟡
   - Inspiriert von picdrop/Google Photos
   - Modernere Icons
   - Besseres Layout für Admin
   - Smooth Animations

### Phase 3: Feature-Komplexität (2-3 Wochen)

8. **Analytics Dashboard** (~6-8h) 🟡
   - Views, Downloads, Likes visualisieren
   - Trends über Zeit
   - Export-Optionen (CSV)

9. **Picdrop-Level Features** (~20-30h) 🟢
   - Layout-Optionen (Raster/Ziegel/Liste)
   - Feature-Toggles pro Event
   - Wasserzeichen
   - Kunden-Upload
   - Activity-Log

10. **Video/GIF Processing** (~8-10h) 🟡
    - ffmpeg Integration für Video-Thumbnails
    - Animated GIF Thumbnails
    - Video-Playback in Lightbox

### Geschätzter Gesamtaufwand

| Phase | Aufwand | Priorität |
|-------|---------|-----------|
| Phase 1 (MVP) | 15-22h | 🔴 HOCH |
| Phase 2 (Design) | 18-24h | 🟡 MITTEL |
| Phase 3 (Features) | 34-48h | 🟢 NICE-TO-HAVE |
| **TOTAL** | **67-94h** | (8-12 Wochen) |

**Empfehlung**: Phase 1 + Phase 2 für solides Produkt, Phase 3 für Picdrop-Parität

---

## Konzept: Photobooth-Integration

**Datei**: KONZEPT_PHOTOBOOTH_INTEGRATION.md
**Stand**: 22. März 2026
**Status**: Entwurf

### Überblick

Das Konzept zielt auf Integration des Open-Source **Photobooth Project** (PHP-basiert, gphoto2) mit der Photobox Gallery v2 ab, um DSLR Booth zu ersetzen.

### Heute (Status Quo)
- DSLR Booth ($79/Jahr) steuert Kamera und Collage-Erstellung
- Windows Uploader lädt Fotos manuell hoch
- Unidirektionale Kommunikation (nur Upload)
- Keine Remote-Verwaltung des Event-PCs

### Ziel-Architektur

```
EVENT-PC (Ubuntu)
├── Photobooth Project (PHP, gphoto2)  [Open Source, kostenlos]
│   ├── Live-Kamera-Preview
│   ├── Touchscreen-UI
│   ├── Collage/Overlay-Engine
│   ├── Green Screen
│   ├── GIF/Boomerang-Funktion
│   └── Lokales Drucken
│
└── Booth Agent (Python)  [neue Komponente]
    ├── Upload → Gallery API
    ├── Config-Management
    ├── Asset-Sync
    ├── Heartbeat/Status
    └── Remote-Commands

        ↓ HTTPS (bidirektional)

GALLERY-SERVER (v2)
├── Admin Panel für Booth-Verwaltung
├── Asset-Delivery (Overlays, Rahmen, etc.)
├── Remote-Befehl-Annahme
├── Client-Dashboard
└── Foto-Moderation
```

### Die drei Bausteine

1. **Photobooth Project** (existiert)
   - Open Source PHP-App
   - Steuert DSLR via gphoto2
   - Erstellt Collagen + GIFs
   - Alternativlose Replacement für DSLR Booth

2. **Booth Agent** (zu entwickeln)
   - Python Script (ähnlich Windows Uploader)
   - Uploaded Fotos zur Gallery
   - Empfängt Configs vom Server
   - Sendet Heartbeat + Status

3. **Gallery Server** (zu erweitern)
   - POST /api/booth/config/:device_id
   - POST /api/booth/assets/:device_id
   - POST /api/booth/command/:device_id
   - Client-Dashboard für Booth-Management
   - Asset-Management (Overlays, Rahmen, GIFs)

### Vorteil der Integration

| Aspekt | DSLR Booth | Photobooth + Booth Agent |
|--------|-----------|-------------------------|
| **Kosten** | $79/Jahr | Kostenlos (Open Source) |
| **Remote-Verwaltung** | ❌ Keine | ✅ Vollständig über Gallery |
| **Bidirektionale Kommunikation** | ❌ Nur Upload | ✅ Befehl + Status |
| **Anpassbarkeit** | ❌ Eingeschränkt | ✅ Vollständig quelloffen |
| **Ökosystem** | 🔒 Proprietär | 🔓 Open Source Community |

### Implementierungs-Roadmap

1. **Booth Agent v1** (~3-4 Wochen)
   - Python Script + API-Integration
   - Config-Empfang
   - Asset-Sync
   - Status-Tracking

2. **Gallery Server Enhancement** (~2-3 Wochen)
   - /api/booth/config, /api/booth/assets, /api/booth/command Endpoints
   - Asset-Management UI im Admin
   - Client-Dashboard für Booth-Verwaltung

3. **Testing & Dokumentation** (~1 Woche)
   - Setup-Guide für Photobooth + Agent
   - Windows/Linux/macOS Support
   - Troubleshooting Guide

**Gesamtaufwand**: ~6-8 Wochen Entwicklung
**Abhängigkeit**: Nicht kritisch für v2.0, aber strategisch wichtig für v2.5+

---

## Zusammenfassung: Was funktioniert, was nicht

### Grüner Bereich (ready for production)
✅ Authentifizierung & Auth
✅ Datenbank & Schema
✅ Media-Upload & Handling
✅ Benachrichtigungen (Telegram/Discord/Webhook)
✅ öffentliche Galerie
✅ Admin-Dashboard (CRUD)
✅ API Upload
✅ Windows Uploader
✅ Docker Setup
✅ Internationalisierung (DE/EN)

### Gelber Bereich (needs refinement)
⚠️ Dark/Light Mode (CSS da, UI fehlt)
⚠️ Sharepic-Funktion (basic, braucht Upload)
⚠️ Rate-Limiting (einfach, kein Sliding-Window)
⚠️ Analytics (Daten sammeln, kein Dashboard)
⚠️ Logo-Upload (Feld da, Logik unklar)

### Roter Bereich (nicht implementiert)
❌ Setup-Wizard
❌ Lizenz-System
❌ CLI-Befehle
❌ Automatischer Cleanup
❌ E-Mail-Abo
❌ Video-Thumbnails
❌ Analytics-Dashboard
❌ 2FA / TOTP
❌ Photobooth-Integration

---

**Entwickler**: Till Heidrich
**Projekt**: PhotoBox Gallery v2
**Repository**: /sessions/inspiring-gifted-cray/mnt/Fotobox-Programm/photobox_v2/
**Kontakt**: über das Projekt auf GitHub (https://github.com/tillheidrich/photobox-gallery)

