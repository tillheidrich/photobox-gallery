# PhotoBox Gallery v2 — Dokumentations-Index

> Erstellt: April 2026 | Basis: photobox_v2 Codestand

Diese Dokumentation beschreibt den vollständigen Ist-Stand von **PhotoBox Gallery v2** — einer Flask-basierten Web-Applikation, die Fotobox-Betreibern ermöglicht, Event-Fotos automatisch als Live-Galerie bereitzustellen.

---

## Dokumentations-Übersicht

| Datei | Inhalt | Größe |
|-------|--------|-------|
| [DOCS_01_ARCHITEKTUR.md](DOCS_01_ARCHITEKTUR.md) | App-Struktur, Flask Factory, Auth, Lizenz, Deployment | 23 KB |
| [DOCS_02_DATENBANK.md](DOCS_02_DATENBANK.md) | Vollständiges DB-Schema, alle Tabellen & Helper-Funktionen | 36 KB |
| [DOCS_03_ROUTEN.md](DOCS_03_ROUTEN.md) | Alle HTTP-Routen (Public, Admin, API) mit Parametern | 26 KB |
| [DOCS_04_FEATURES.md](DOCS_04_FEATURES.md) | Alle Features im Detail: Upload, Notifications, Gallery, etc. | 66 KB |
| [DOCS_05_WINDOWS_CLIENT.md](DOCS_05_WINDOWS_CLIENT.md) | Windows Uploader Client: Architektur, Config, Installation | 16 KB |
| [DOCS_06_PROJEKTSTATUS.md](DOCS_06_PROJEKTSTATUS.md) | Projektstatus, Roadmap, i18n, bekannte Lücken | 20 KB |

---

## Kurzübersicht: Was ist PhotoBox Gallery v2?

**PhotoBox Gallery** ist eine lokal laufende Web-App für Fotobox-Betreiber. Fotos werden automatisch per Ordner-Watching erfasst, verarbeitet und als Live-Galerie für Gäste bereitgestellt.

### Tech Stack
- **Backend:** Python 3.10, Flask, SQLite, Watchdog
- **Frontend:** Jinja2-Templates, Vanilla CSS/JS, PhotoSwipe (Lightbox)
- **Notifications:** Telegram Bot API, Discord Webhooks, SMTP E-Mail
- **Client:** Windows Python-Uploader mit System-Tray
- **Deployment:** Direkt via `run.py` oder Docker

### Kernfunktionen (implementiert)

| Feature | Status |
|---------|--------|
| Automatischer Foto-Upload via Ordner-Watching | ✅ |
| Live-Galerie für Gäste (Masonry-Grid) | ✅ |
| QR-Code pro Event | ✅ |
| Passwortschutz für Galerien | ✅ |
| Kiosk-Modus (TV/Beamer Slideshow) | ✅ |
| Admin-Panel (Events, Devices, Settings) | ✅ |
| Telegram-Benachrichtigungen | ✅ |
| Discord-Webhook | ✅ |
| Gäste E-Mail-Abonnements (Double-Opt-In) | ✅ |
| Per-Device Landingpages | ✅ |
| Featured Events auf Startseite | ✅ |
| FAQ-Seite (editierbar, mit Datei-Upload) | ✅ |
| Impressum / Datenschutz (editierbar) | ✅ |
| Analytics (Views, Downloads, Likes) | ✅ |
| Mehrsprachigkeit (DE/EN) | ✅ |
| Lizenz-System | ✅ (basis) |
| Windows Uploader Client | ✅ |
| Setup-Wizard (Erst-Einrichtung) | ✅ |
| WebP-Konvertierung + Thumbnails | ✅ |

### Bekannte Lücken / Nächste Schritte

| Feature | Priorität |
|---------|-----------|
| Setup-Wizard: Basis-URL-Konfiguration | Hoch |
| Admin UI für Telegram/Discord-Einrichtung (guided) | Hoch |
| Lizenz-Server vollständig produktionsreif | Mittel |
| Mac-Support für Uploader | Mittel |
| White-Label / eigene Domain | Niedrig |

Details in [DOCS_06_PROJEKTSTATUS.md](DOCS_06_PROJEKTSTATUS.md).

---

## Projektstruktur (Kurzübersicht)

```
photobox_v2/
├── run.py                          # Einstiegspunkt
├── requirements.txt                # Python-Abhängigkeiten
├── Dockerfile / docker-compose.yml # Container-Deployment
├── app/
│   ├── __init__.py                 # Flask App Factory
│   ├── db.py                       # Datenbankschicht (SQLite)
│   ├── auth.py                     # Admin-Login / Session
│   ├── license.py                  # Lizenzprüfung
│   ├── media.py                    # Foto-Verarbeitung (WebP, Thumb)
│   ├── notifier.py                 # Benachrichtigungs-Pipeline
│   ├── emailer.py                  # SMTP E-Mail
│   ├── blueprints/
│   │   ├── public.py               # Öffentliche Routen (/, /e/, /faq, ...)
│   │   ├── admin.py                # Admin-Panel (/admin/...)
│   │   └── api.py                  # REST API (/api/...)
│   ├── templates/                  # Jinja2-Templates
│   ├── static/                     # CSS, JS, Vendor-Libs
│   └── i18n/                       # Übersetzungen (de.py, en.py)
├── data/
│   ├── photobox.db                 # SQLite-Datenbank
│   └── storage/                    # Foto-Dateien (orig/webp/thumb)
└── windows_uploader/               # Windows-Client
    ├── photobox_uploader_v2.py     # Hauptprogramm (aktuell)
    ├── photobox_tray.py            # System-Tray
    └── config.example.json         # Konfigurationsvorlage
```

---

## Für einen Neustart: Was du brauchst

Um das Projekt neu aufzubauen, lies in dieser Reihenfolge:

1. **DOCS_01** — Architektur verstehen, wie die App zusammengebaut ist
2. **DOCS_02** — Datenbankschema, bevor du irgendwas codest
3. **DOCS_03** — Alle Routen, damit klar ist was wo passiert
4. **DOCS_04** — Feature-Details für die Kernlogik
5. **DOCS_05** — Windows-Client (falls relevant)
6. **DOCS_06** — Projektstatus: was fehlt noch?

---

*Dokumentation generiert aus dem Quellcode von photobox_v2 | Stand: April 2026*
