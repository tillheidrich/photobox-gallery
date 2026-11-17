# Documentation

* [`../AGENTS.md`](../AGENTS.md) — start here if you want to change something.
* [`../README.md`](../README.md) — what the three parts are and how to run them.
* [`MAINTENANCE.md`](MAINTENANCE.md) — the daily maintenance run (retention,
  NAS offload): how the gallery starts it itself, and the Coolify scheduled task.
* [`UPGRADE.md`](UPGRADE.md) — moving a running gallery to a new version: backup
  first (also from the old version), rehearsal with the real backup, variables,
  checks afterwards, the way back.
* [`MCP.md`](MCP.md) — connecting Claude or another assistant to the admin
  (OAuth), the tools and their scopes, what is deliberately impossible, revoking.
* [`INSTALL-NAS.md`](INSTALL-NAS.md) — für Betreiber ohne eigenen Server: die
  Galerie auf einem Synology- oder QNAP-NAS (`docker-compose.nas.yml`),
  Reverse Proxy und Zertifikat des NAS, was im LAN geht und was nicht, Sicherung.
* [`LEGAL.md`](LEGAL.md) — for operators: Impressum and privacy page, the fields
  under Einstellungen → Rechtliches, what the software does with personal data,
  why an unfinished page is never shown to guests.
* [`VORLAGEN-FORMAT-FUER-KI.md`](VORLAGEN-FORMAT-FUER-KI.md) (English:
  [`TEMPLATE-FORMAT-FOR-AI.md`](TEMPLATE-FORMAT-FOR-AI.md)) — eine Datei, mit
  der eine KI ohne Zugriff aufs Repository gültige Druckvorlagen für den
  SELPHY CP1300 baut: JSON-Format, Papiere, sicherer Rand, vier geprüfte
  Beispiele, Prüfliste. Die Beispiele laufen im Test durch den echten Prüfer.
* [`SECOND-TARGET-FROM-THE-GALLERY.md`](SECOND-TARGET-FROM-THE-GALLERY.md) — why a
  booth's second upload target (Picdrop, FTP, SFTP) is set at the booth and
  not from the gallery, and the smallest safe design if it ever should be.
* [`LAYOUT-FREIGABE.md`](LAYOUT-FREIGABE.md) — für Betreiber: den Kunden das
  Drucklayout seines Events gestalten lassen, Link verschicken, freigeben.
* [`WENN-ETWAS-HAKT.md`](WENN-ETWAS-HAKT.md) — eine Seite zum Ausdrucken für
  Gastgeber einer gemieteten Box: Papier, Farbband, Kamera, schwarzer
  Bildschirm, Strom und Internet weg — ohne PIN.
* [`ANLEITUNG-BOX-EINRICHTEN.md`](ANLEITUNG-BOX-EINRICHTEN.md) — für Betreiber:
  die Box Schritt für Schritt einrichten (Kamera, Blitz, Drucker, Koppeln,
  Probelauf), mit Platzhaltern für Bildschirmfotos.
* [`EINRICHTEN-MIT-KI-AGENT.md`](EINRICHTEN-MIT-KI-AGENT.md) — die Box mit einem
  KI-Agenten einrichten lassen: SSH nur während der Einrichtung, was der Agent
  darf und was nicht, wieder ausschalten.
* [`CODE-SIGNATUR.md`](CODE-SIGNATUR.md) — die Box-Datei signieren lassen
  (SignPath Foundation, Certum), SmartScreen, Prüfsumme veröffentlichen.
* [`legacy/`](legacy/) — older German documentation. It explains decisions that
  are still visible in the code, but where it disagrees with the code, the code
  wins.

The day-to-day project documentation (changelog, benchmark, hardware notes) is
kept in a private wiki. What matters for working in this repository is here.

`PUBLISHING.md` and `FIRST_ISSUES.md` exist only in the private repository —
they describe how the public mirror is made, and are left out of it by
[`../tools/make_public_snapshot.sh`](../tools/make_public_snapshot.sh).

## Aktualisieren

### Die Galerie (Web)

Sie läuft als Container. Aktualisiert wird dort, wo sie läuft — nicht aus der
eigenen Oberfläche heraus, denn ein Dienst, der sich selbst überschreibt, kann
sich auch selbst abschießen.

```bash
docker compose pull && docker compose up -d      # eigener Server
```

Mit Coolify: im Projekt auf **Redeploy**. Unter *Admin → Software* steht,
welche Fassung gerade läuft, damit man danach nachsehen kann.

Die Daten liegen im gemounteten `data/`-Verzeichnis und bleiben unberührt;
Schemaänderungen laufen beim Start selbst durch.

### Die Box (Windows)

1. Auf einem Windows-Rechner `packaging\windows\Fotobox bauen.bat`
   doppelklicken — heraus kommt `dist\PhotoboxBooth.exe`.
2. Im Web-Admin unter **Software** hochladen, Version eintragen, freigeben.
3. Jede Box bekommt die neue Fassung beim nächsten Sync angeboten. Der
   Betreiber (an der Box oder aus der Ferne) lädt sie herunter — die Box prüft
   die Prüfsumme — und spielt sie ein, wenn es gerade passt. Die Box startet
   dabei neu; mitten im Abend passiert das nie von selbst.

Passt die Prüfsumme nicht oder bricht der Download ab, bleibt die laufende
Fassung, wie sie ist. Die alte Datei wird beim Tausch als `.alt` danebengelegt.
