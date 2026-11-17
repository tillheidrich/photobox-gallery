# Photobox Sync

Überträgt Fotobox-Aufnahmen zur **Photobox Gallery**, auf ein **NAS** (SFTP) oder zu **Picdrop** (FTPS „Live Push“) — offline zuerst.

Zwei Einsatzarten, ein Paket:

1. **Eigenständig neben dslrBooth, LumaBooth & Co.** — beobachtet Ordner, nimmt dslrBooth-Trigger an, lädt hoch, sobald Netz da ist, und meldet sich in der Galerie wie eine Box (Status, Event-Zuordnung, Aufträge).
2. **Eingebaut in die Photobox Booth** — dieselbe Outbox, dieselbe API, dieselbe Verwaltung.

## Wie der Sync funktioniert

```
Aufnahme-Software ──► Ordner ──► Watcher (stabile Datei?) ──► Outbox (SQLite, pro Ziel)
        │                                                        │
        └── Trigger /trigger (Session, sofort) ──────────────────┤
                                                                 ▼
                     Upload-Schleife: kleine Dateien zuerst, Backoff je Datei,
                     Netz zurück ⇒ alle sofort fällig, doppelte Uploads erkennt der Server
                                                                 │
             ┌───────────────────────────────┬───────────────────┴─────────────┐
             ▼                               ▼                                 ▼
   Photobox Gallery (API v2)          NAS per SFTP                    Picdrop per FTPS
             ▲
             └── alle 30 s (wenn online): Status ⇄ Event-Zuordnung, Profil, Aufträge
```

* Der Server ruft den PC nie an. Der PC fragt nach — funktioniert hinter jedem Hotspot.
* Eine Datei geht erst raus, wenn sie sich einige Sekunden nicht mehr ändert.
* Kennung aus Pfad, Größe und Zeit: Neustart lädt nichts doppelt.
* Ist die Box den ganzen Tag offline und sieht zweimal kurz Netz, reicht das Fenster für die Web-Fassungen.

## Installation (Windows)

```powershell
Set-ExecutionPolicy -Scope Process Bypass; .\scripts\install-windows.ps1
```

Danach `http://127.0.0.1:8766` öffnen: Galerie-Adresse, Geräte-Schlüssel, Ordner. In dslrBooth unter Trigger `http://127.0.0.1:8766/trigger` eintragen.

## Entwicklung

```bash
pip install -e . && python -m photobox_sync --no-browser
pytest tests
```
