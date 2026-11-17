# PhotoBox Gallery v2 — Architektur & Setup

## Überblick

### Produktbeschreibung

PhotoBox Gallery v2 ist ein selbst gehostetes Fotogallerie-System spezialisiert für Fotobooth-Profis im deutschsprachigen Markt. Das System ermöglicht automatisierten Upload von Fotos und Videos von Fotobooth-Geräten, deren Verwaltung durch eine Weboberfläche mit Admin-Panel, und das Teilen von Galerielinks mit Gästen. Die Lösung bietet DSGVO-Konformität (lokale Datenspeicherung, kein CDN), Mehrsprachigkeit (DE/EN), und professionelle Features wie Analytics, Passwortschutz, Branding-Anpassungen, sowie Benachrichtigungen über Telegram und Discord. Mit Lizenz-Tiers (Starter €49–Pro €129–White-Label €249) richtet sich das Produkt gegen Cloud-Konkurrenten wie Pixieset, bietet aber einmalige Gebühren statt Abos.

### Tech Stack

- **Python Version:** 3.10+
- **Web-Framework:** Flask 3.0.0+
- **Datenbankmotor:** SQLite3 (mit WAL-Mode, Foreign Keys aktiviert)
- **Frontend-Template-Engine:** Jinja2 (Flask-Standard)
- **CSS/JavaScript:** Vanilla JavaScript (ES6 Module), CSS3
- **Bild-Verarbeitung:** Pillow 10.0.0+ (PNG, JPEG, WebP-Konvertierung, Thumbnails)
- **QR-Code-Generierung:** qrcode[pil] 7.4.2+
- **Passwort-Hashing:** Werkzeug 3.0.0+ (PBKDF2)
- **Markdown-Rendering:** markdown 3.5.0+
- **File-Watching (optional):** Watchdog (für Windows-Uploader-Integration)
- **HTTP-Client:** requests library (für API-Calls)
- **Web-Server (Production):** Gunicorn 21.2.0+ (2 Worker, 120s Timeout)

### Deployment-Modi

#### 1. Direkt mit run.py (Development)

```bash
python run.py
# Startet Flask Development Server auf http://localhost:5000
# Verwendet: Flask app.run(debug=False, host="0.0.0.0", port=5000)
# Geeignet für: Lokale Tests, kleine Installationen ohne Docker
```

#### 2. Docker (Production)

```bash
docker-compose up
# Baut Docker-Image aus Dockerfile
# Startet 2 Services: web (Gunicorn), pruner (Retention Job)
# Volumes: ./data:/data (SQLite DB, Media-Dateien)
# Port: 5000 (konfigurierbar via PORT env-var)
# Geeignet für: Production-Deployment, Multi-Server-Setup
```

---

## App-Struktur

Annotierter Verzeichnisbaum des `photobox_v2/`-Ordners:

```
photobox_v2/
├── app/
│   ├── __init__.py              # Flask App Factory (create_app), Blueprint-Registrierung, Jinja2 Globals
│   ├── auth.py                  # Admin-Login, Session-Management, @login_required Decorator
│   ├── db.py                    # SQLite Schema, Migration-Logik, Datenbankqueries (1312 Zeilen)
│   ├── license.py               # Lizenzprüfung, Feature-Flags (Starter/Pro/White-Label)
│   ├── media.py                 # Bildverarbeitung, WebP-Konvertierung, Thumbnails, GIF/Video-Handling
│   ├── notifier.py              # Telegram/Discord/Webhook Benachrichtigungen, Email-Versand
│   ├── emailer.py               # SMTP-Konfiguration, Email-Template-Rendering
│   ├── blueprints/
│   │   ├── __init__.py          # Blueprint-Modul-Init (leer)
│   │   ├── public.py            # Public Routes: Galerien (/g/<slug>), QR-Code (/qr), kiosk-Modus, Gast-Upload
│   │   ├── admin.py             # Admin-Routes: Event-Management, Device-Management, Settings-UI, Analytics
│   │   └── api.py               # REST-API: Device-Upload (/api/upload), Stats (/api/stats), Subscriptions
│   ├── i18n/
│   │   ├── __init__.py          # t() Translation-Funktion, DE/EN Locale-Loading
│   │   ├── de.json              # Deutsche Übersetzungen (Volltext)
│   │   └── en.json              # Englische Übersetzungen
│   ├── static/
│   │   ├── vendor/
│   │   │   └── photoswipe/      # PhotoSwipe v5.4.4 Local-Hosted (DSGVO-Konformität, kein CDN)
│   │   │       ├── photoswipe.min.css
│   │   │       ├── photoswipe.esm.min.js
│   │   │       └── photoswipe-lightbox.esm.min.js
│   │   ├── style.css            # Global Styles (Dark/Light Mode, Responsive Layout)
│   │   └── gallery.js           # Kiosk-Modus, Gast-Upload, Share-Link-Handler
│   └── templates/
│       ├── base.html            # Master-Template (Navigation, Footer, Dark-Mode-Toggle)
│       ├── admin/
│       │   ├── index.html       # Admin-Dashboard (Event-List mit Counts, Stats-Übersicht)
│       │   ├── event_create.html # Event-Erstellung (Slug, Name, Passwort, Branding-JSON)
│       │   ├── event_edit.html  # Event-Bearbeitung (Slug, Retention-Tage, Design, Cover-Upload)
│       │   ├── devices.html     # Device-Management (API-Keys, Landing-Pages, Device-Settings)
│       │   ├── settings.html    # Global Settings (Logo, SMTP, Telegram-Token, Discord-Webhook, BASE_URL)
│       │   └── analytics.html   # Event-Analytics (Views, Downloads, Shares, Top-Photos, Like-Counts)
│       ├── public/
│       │   ├── gallery.html     # Galerien-Listing, Photo-Grid, PhotoSwipe-Lightbox, Like-Button
│       │   ├── share.html       # Share-Link-Seite (/s/<token>, Einzelfoto-Anzeige, Download-Button)
│       │   ├── qr.html          # QR-Code-Display (auto-refresh 30s, Print-Button, Kiosk-Toggle)
│       │   └── kiosk.html       # Fullscreen-Slideshow, Automatisches Loop-Abspielen
│       └── login.html           # Admin-Login-Formular (Username/Password)
│
├── data/                        # Volume-Mount für Persistierung (bei Docker)
│   ├── gallery.sqlite3          # SQLite Database (Events, Media, Users, Stats, Devices, License)
│   ├── media/                   # Organisierte Media-Dateien (event_<id>/, original/, webp/, thumb/)
│   └── uploads/                 # Temporäre Upload-Verzeichnisse (vor Verarbeitung)
│
├── windows_uploader/            # Desktop-Application für automatischen Upload von Windows-Photobooth-PCs
│   ├── photobox_uploader_v2.py  # Hauptprogram (Folder-Polling, Device-API-Integration)
│   ├── photobox_uploader.py     # v1-Uploader (Referenz, Legacy)
│   ├── photobox_tray.py         # System-Tray-Integration (Windows)
│   ├── config.example.json      # Beispiel-Config für Event-Upload (Server-URL, API-Key, Event-Slug)
│   ├── config.auto.json         # Beispiel-Config für Auto-Event-Modus (Erstellung aus Ordner-Namen)
│   ├── requirements.txt         # Python-Abhängigkeiten (requests)
│   ├── StartUploader.bat        # Windows-Batch-Starter (mit Pause für Fehler-Anzeige)
│   ├── StartUploader_NoPause.bat # Windows-Batch-Starter (direkt schließen)
│   ├── README_V2.md             # Dokumentation für Windows-Uploader-Setup
│   └── INSTALL_WINDOWS_UPLOADER.md # v1-Installations-Anleitung (Referenz)
│
├── run.py                       # Application Entrypoint (create_app() aufrufen, Flask Server starten)
├── requirements.txt             # Python-Abhängigkeiten (Flask, Pillow, qrcode, Werkzeug, markdown)
├── Dockerfile                   # Docker-Image-Definition (Python 3.12-slim, Gunicorn, Fonts)
├── docker-compose.yml           # Docker Compose Configuration (web + pruner Service)
│
├── MIGRATION_SUMMARY.md         # Migration-Dokumentation (PhotoSwipe CDN → Lokal, Windows-Uploader v1 → v2)
├── BUSINESS_STRATEGY_2026.md    # Geschäftsstrategie, Pricing-Modell, Launch-Roadmap, CSFs
├── README_MIGRATION.md          # Migration-Anleitung von v1 zu v2
└── website-mockup-2026.html     # Marketing-Seite Mockup (nur für Demos)
```

---

## Flask App Factory (`app/__init__.py`)

### App-Erstellung (create_app Pattern)

Die `create_app()`-Funktion folgt dem Flask App Factory Pattern, das Dependency Injection und Testbarkeit ermöglicht:

```python
def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
    # ... Blueprint-Registrierung, Globale Funktionen, Init-Hooks
    return app
```

**Zweck:** Ermöglicht mehrere unabhängige App-Instanzen (z.B. für Testing) und zentrale Konfiguration.

### Blueprint-Registrierung und URL-Prefixe

Vier Blueprints werden registriert:

1. **public** (`app.register_blueprint(public_bp)`)
   - **Prefix:** / (Keine Präfix)
   - **Routes:** /g/<slug> (Galerie), /s/<token> (Share-Link), /qr (QR-Code), /upload (Gast-Upload), /kiosk, /lang/<locale>
   - **Zugriff:** Öffentlich

2. **admin** (`app.register_blueprint(admin_bp, url_prefix="/admin")`)
   - **Prefix:** /admin
   - **Routes:** /admin/ (Dashboard), /admin/login, /admin/events/create, /admin/events/<id>/edit, /admin/devices, /admin/settings, /admin/analytics
   - **Zugriff:** Geschützt mit @login_required Decorator

3. **api** (`app.register_blueprint(api_bp, url_prefix="/api")`)
   - **Prefix:** /api
   - **Routes:** /api/upload, /api/stats, /api/subscriptions/confirm, /api/likes/toggle, /api/media/<token>/download
   - **Zugriff:** Öffentlich (mit API-Key-Auth für bestimmte Routes)

4. **Keine weitere Blueprint**
   - Health-Check Route: / (Redirect zu /g oder Startseite)

### Extensions und Jinja2 Globals

#### Konfigurierte Extensions
- **Session Management:** Flask Session (cookie-basiert, signiert mit SECRET_KEY)
- **Keine weitere externe Extensions registriert** (Flask-SQLAlchemy, Flask-Login etc. nicht verwendet)

#### Globale Jinja2-Funktionen
```python
app.jinja_env.globals["t"] = t
```
Die `t(key)` Funktion wird in allen Templates global verfügbar gemacht und ermöglicht Übersetzung:
```jinja2
<h1>{{ t('gallery_title') }}</h1>  <!-- Gibt "Galerie" (DE) oder "Gallery" (EN) aus -->
```

### i18n und t() Funktion

**Datei:** `app/i18n/__init__.py`

```python
def t(key: str, locale: str = None) -> str:
    """Translate key to current locale or default (de)."""
    if locale is None:
        # Ermittelt aus request.path (/de/... oder /en/...)
        locale = get_current_locale()
    translations = TRANSLATIONS.get(locale, TRANSLATIONS["de"])
    return translations.get(key, key)  # Fallback auf Key selbst
```

Die Übersetzungen werden aus `i18n/de.json` und `i18n/en.json` geladen und im RAM gecacht. Locale-Wechsel geschieht über URL-Präfixe oder Query-Parameter (`?lang=en`).

### Startup-Logik

#### In create_app()

```python
@app.before_request
def _init_db():
    from app import db
    db.init_db()
```

Bei jedem Request wird `db.init_db()` aufgerufen (idempotent). Diese Funktion:
1. Erstellt oder aktualisiert das Datenbankschema (CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD COLUMN)
2. Führt Lightweight-Migrations durch (fehlende Spalten hinzufügen)
3. Bootet Admin-Credentials in DB (aus ENV-Variablen oder Defaults)
4. Erstellt Slug-Alias-Tabelle (Release 2.2)
5. Stellt sicher, dass jedes Event einen `access_token` hat

#### Keine weiteren Hooks in create_app()

Es gibt keine `@app.teardown_request` oder Notifier-Start-Logik in `__init__.py`. Diese werden in Blueprints oder als eigenständige Prozesse (z.B. Docker Pruner-Container) verwaltet.

### Konfigurationsschlüssel

Die App erwartet folgende Environment-Variablen (mit Defaults):

| Key | Typ | Zweck | Default |
|-----|-----|-------|---------|
| `SECRET_KEY` | str | Session-Signatur (CSRF-Protection) | `change-me-in-production` |
| `ADMIN_USER` | str | Standard Admin-Benutzername | `admin` |
| `ADMIN_PASSWORD` | str | Standard Admin-Passwort (wird gehasht) | `admin` |
| `DATA_DIR` | path | Verzeichnis für SQLite DB und Media | `./data` |
| `LICENSE_KEY` | str | Lizenzschlüssel (Base64url, offline validiert) | (leer) |
| `LICENSE_SECRET` | str | Secret zur HMAC-Validierung von Lizenzschlüsseln | `photobox-default-secret` |
| `PORT` | int | HTTP-Port (Docker) | `5000` |
| `TG_TOKEN` | str | Telegram Bot Token | (leer) |
| `TG_CHAT_ID` | str | Telegram Chat ID | (leer) |
| `DISCORD_WEBHOOK` | str | Discord Webhook URL | (leer) |
| `SMTP_HOST` | str | SMTP-Server (E-Mail) | (leer) |
| `SMTP_PORT` | int | SMTP-Port | `587` |
| `SMTP_USER` | str | SMTP-Benutzername | (leer) |
| `SMTP_PASSWORD` | str | SMTP-Passwort | (leer) |
| `SMTP_FROM_EMAIL` | str | Absender-Adresse für E-Mails | (leer) |

**Anmerkung:** Nach der Erste Installation werden viele Settings in die DB verschoben (settings-Tabelle). ENV-Variablen dienen als Bootstrap/Fallback.

---

## Authentifizierung (`auth.py`)

### Admin-Login-Mechanismus

Der Admin-Login funktioniert nach folgendem Ablauf:

1. **Credentials-Speicherung:** Benutzername und Passwort-Hash werden in der `settings`-Tabelle der SQLite-Datenbank gespeichert.

2. **Initiales Bootstrapping:** Beim ersten Start werden Admin-Credentials aus den Environment-Variablen `ADMIN_USER` und `ADMIN_PASSWORD` in die Datenbank eingefügt (falls nicht bereits vorhanden).

3. **Login-Verarbeitung:**
   ```python
   def login(username: str, password: str) -> bool:
       if username == admin_user() and check_password_hash(admin_password_hash(), password or ""):
           session["admin_logged_in"] = True
           return True
       return False
   ```
   - Benutzername wird mit `admin_user()` verglichen (DB-Fallback zu ENV)
   - Passwort wird mit PBKDF2-Hash (Werkzeug.security) validiert
   - Bei erfolgreicher Auth wird `session["admin_logged_in"] = True` gesetzt

4. **Session-Management:** Flask-Sessions sind standardmäßig Cookie-basiert, signiert mit `SECRET_KEY` und verfallen bei Browser-Schließung.

### @login_required Decorator

```python
def require_login(view_fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view_fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not is_logged_in():
            return redirect(url_for("admin_login", next=request.path))
        return view_fn(*args, **kwargs)
    return wrapper
```

**Verwendung:** Wird auf alle Admin-Routen angewendet (z.B. `/admin/`, `/admin/events/create`). 
**Verhalten:** Nicht-angemeldete Nutzer werden zu `/admin/login` umgeleitet mit `next=...` Parameter zum Weiterleiten nach erfolgreichem Login.

### Passwort-Änderung

Funktion `change_credentials()` in `auth.py`:

```python
def change_credentials(*, current_password: str, new_username: str, new_password: Optional[str] = None) -> bool:
    """Sicherheit: Erfordert aktuelles Passwort, speichert Hash in DB."""
    if not check_password_hash(admin_password_hash(), current_password or ""):
        return False  # Aktuelle Passwort falsch
    u = (new_username or "").strip()
    if not u:
        return False  # Neuer Username leer
    db.set_setting("admin_username", u)
    if new_password is not None:
        pw = (new_password or "").strip()
        if pw:
            db.set_setting("admin_password_hash", generate_password_hash(pw))
    return True
```

**Sicherheitsmerkmale:**
- Erfordert aktuelles Passwort als Verifikation
- Speichert nur Passwort-Hashes, nie Klartext
- Neue Credential-Einträge sind optional (kann nur Username oder nur Passwort ändern)

---

## Lizenz-System (`license.py`)

### Lizenzprüfung

Das Lizenz-System arbeitet **offline** (keine Server-Kommunikation) mit HMAC-SHA256-signierten Schlüsseln:

```python
def validate_key(key_str: str) -> Optional[License]:
    """Validiert einen Lizenzschlüssel offline."""
    raw = base64.urlsafe_b64decode(key_str + "==")  # Dekodierung mit Padding
    
    # Struktur (45 Bytes):
    # [0]       : Version (1)
    # [1-4]     : issued_at (Unix Timestamp)
    # [5-8]     : expires_at (Unix Timestamp, 0 = unbegrenzt)
    # [9-10]    : features (Bit-Flags)
    # [11-12]   : device_limit (0 = unbegrenzt)
    # [13-44]   : HMAC-SHA256 (Signatur über Bytes 0-12)
    
    header = raw[:13]
    signature = raw[13:45]
    
    expected = hmac.new(_get_secret(), header, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, signature):
        return None  # Signatur ungültig
    
    version, issued_at, expires_at, features, device_limit = struct.unpack(..., header)
    lic = License(version, issued_at, expires_at, features, device_limit)
    
    if lic.is_expired:
        return None  # Lizenz abgelaufen
    
    return lic
```

**Validierungsschritte:**
1. Base64url-Dekodierung
2. Header/Signatur-Trennung
3. HMAC-SHA256 Verifikation
4. Struktur-Parsing
5. Ablauf-Prüfung

### Kein Lizenzserver

Es gibt **keine externe Server-Kommunikation**. Das System vertraut auf offline-validierte Signaturen. Der Secret zum Validieren wird aus ENV-Variablen genommen:

```python
def _get_secret() -> bytes:
    s = os.environ.get("LICENSE_SECRET", "").strip()
    if not s:
        s = os.environ.get("SECRET_KEY", "photobox-default-secret")
    return s.encode()
```

### Feature-Flags und Lizenz-Tiers

**Feature-Flags (als Bit-Flags in Bytes 9-10):**
- `0x0001` - UNLIMITED_DEVICES (Pro+)
- `0x0002` - TEMPLATES (für zukünftige Verwendung)
- `0x0004` - ANALYTICS (Pro+)
- `0x0008` - WHITE_LABEL (nur White-Label-Tier)

**Lizenz-Klasse:**
```python
@property
def plan_name(self) -> str:
    if self.has_feature(FEATURE_WHITE_LABEL):
        return "white_label"
    if self.has_feature(FEATURE_ANALYTICS):
        return "pro"
    return "starter"
```

Logik:
- **Starter:** Keine Feature-Flags, 1 Gerät-Limit, kein Analytics
- **Pro:** ANALYTICS-Flag, unbegrenzte Geräte
- **White-Label:** WHITE_LABEL-Flag, alle Pro-Features + Branding-Optionen

### Lizenzgesperrt Features

Folgende Funktionen basieren auf der Lizenz:

| Feature | Starter | Pro | White-Label |
|---------|---------|-----|------------|
| Gerät-Limit | 1 | unbegrenzt | unbegrenzt |
| Analytics | Nein | Ja | Ja |
| "Powered by Photobox"-Footer | Ja | Ja | Nein |
| Lizenz-Info im Admin-Panel | Ja | Ja | Ja |

**Implementierung:**

```python
def has_analytics() -> bool:
    lic = get_license()
    if lic is None:
        return False  # Unlizenziert → Starter-Defaults
    return lic.has_feature(FEATURE_ANALYTICS)

def get_device_limit() -> int:
    lic = get_license()
    if lic is None:
        return 1  # Starter: 1 Gerät
    if lic.has_feature(FEATURE_UNLIMITED_DEVICES):
        return 0  # Unbegrenzt
    return lic.device_limit or 1
```

### Globale Lizenzinstanz

```python
_CURRENT_LICENSE: Optional[License] = None

def get_license() -> Optional[License]:
    global _CURRENT_LICENSE
    if _CURRENT_LICENSE is None:
        _CURRENT_LICENSE = load_license()
    return _CURRENT_LICENSE
```

Die Lizenz wird beim ersten Zugriff geladen (lazy-loading) und im RAM gecacht. Beim Anwendungs-Restart wird sie neu geladen.

---

## Deployment

### Direkt (run.py)

**Datei:** `run.py`

```python
"""Application entrypoint."""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
```

**Startbefehl:**
```bash
python run.py
```

**Verhalten:**
- Erstellt Flask-App mit `create_app()`
- Startet Flask Development Server auf `0.0.0.0:5000`
- Verwendet Werkzeug-WSGI-Server (nicht für Production empfohlen)
- Geeignet für: Lokale Tests, kleine Single-Server-Installationen

**Limitierungen:**
- Keine Auto-Reload bei Code-Änderungen (debug=False)
- Single-threaded (nicht skalierbar)
- Kein Process-Management

### Docker

**Dockerfile:**

```dockerfile
FROM python:3.12-slim

# System-Abhängigkeiten für Pillow und Fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python-Abhängigkeiten installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Applikation kopieren
COPY . .

# Data-Verzeichnis erstellen
RUN mkdir -p /data

# Mit Gunicorn starten (2 Worker, 120s Timeout)
CMD ["python", "-m", "gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "run:app"]

EXPOSE 5000
```

**docker-compose.yml:**

```yaml
version: "3.9"

services:
  web:
    build: .
    restart: unless-stopped
    ports:
      - "${PORT:-5000}:5000"
    volumes:
      - ./data:/data
    environment:
      - DATA_DIR=/data
      - SECRET_KEY=${SECRET_KEY:-change-me-in-production}
      # Optionale Legacy-Fallbacks für ENV-Variablen:
      - TG_TOKEN=${TG_TOKEN:-}
      - TG_CHAT_ID=${TG_CHAT_ID:-}
      - DISCORD_WEBHOOK=${DISCORD_WEBHOOK:-}
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/healthz')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

  pruner:
    build: .
    restart: unless-stopped
    command: python -c "
from app import create_app;
app = create_app();
with app.app_context():
    from app import db, media;
    import time;
    while True:
        try:
            db.prune_expired_media()
        except Exception as e:
            print(f'Pruner error: {e}')
        time.sleep(3600)
"
    volumes:
      - ./data:/data
    environment:
      - DATA_DIR=/data
      - SECRET_KEY=${SECRET_KEY:-change-me-in-production}
    depends_on:
      - web
```

**Services:**

1. **web:** 
   - Container mit Gunicorn (2 Worker)
   - Port 5000 (konfigurierbar via `PORT` env-var)
   - Volume: `./data:/data` für SQLite DB und Media-Dateien
   - Healthcheck: Prüft `http://localhost:5000/healthz` alle 30s

2. **pruner:**
   - Separater Container für Cleanup-Jobs
   - Looped `db.prune_expired_media()` alle 3600 Sekunden
   - Abhängig von `web` Service

**Startbefehl:**
```bash
docker-compose up -d
# oder
docker-compose up
```

**Port-Anpassung:**
```bash
PORT=8080 docker-compose up
# Lädt auf 8080:5000
```

---

## Abhängigkeiten (requirements.txt)

Alle Pakete aus `requirements.txt`:

| Paket | Version | Zweck |
|-------|---------|-------|
| `Flask` | >=3.0.0 | Web-Framework, Routing, Templates |
| `Pillow` | >=10.0.0 | Bildverarbeitung (PNG/JPEG/WebP), Resize, Thumbnails |
| `qrcode[pil]` | >=7.4.2 | QR-Code-Generierung (mit Pillow-Support) |
| `Werkzeug` | >=3.0.0 | WSGI-Toolkit, Passwort-Hashing (generate_password_hash, check_password_hash) |
| `markdown` | >=3.5.0 | Markdown-zu-HTML-Rendering (für FAQ, Impressum) |

**Zusätzliche (im Code, aber nicht in requirements.txt):**
- `requests` – HTTP-Client (in Windows-Uploader und für API-Calls, optional)
- `watchdog` – File-Systemwatcher (optional, für Auto-Upload-Scenarios)

**Installation:**
```bash
pip install -r requirements.txt
```

---

## Zusammenfassung

PhotoBox Gallery v2 ist eine modular aufgebaute Flask-Anwendung mit:
- **Kernmodul:** `app/__init__.py` (Factory-Pattern, Blueprint-Registrierung)
- **Persistierung:** SQLite (`app/db.py`) mit Migrations-Logik
- **Authentifizierung:** Session-basierter Admin-Login (`app/auth.py`)
- **Lizenzierung:** Offline-validierte HMAC-Schlüssel (`app/license.py`)
- **Drei Blueprints:** Public (Galerien), Admin (Dashboard), API (Device-Upload)
- **Deployment:** Lokal mit `run.py` oder Docker mit `docker-compose.yml`
- **Tech Stack:** Python 3.10+, Flask 3, SQLite3, Pillow, Werkzeug, Markdown

Die Architektur ist auf **Zero-Vendor-Lock-in** und **DSGVO-Konformität** ausgelegt: Alle Daten bleiben lokal, kein CDN, selbst-gehostet mit einfachen ENV-Konfigurationen.
