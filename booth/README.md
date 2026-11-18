# Photobox Booth

Aufnahme-Software für die eigene Fotobox. Offline zuerst, ohne Abo, ohne KI.
Teil des Photobox-Ökosystems zusammen mit der **Photobox Gallery** (Ordner darüber).

**Status 17.09.2026: v0.1 — läuft vollständig mit Testbild-Kamera und Webcam. Canon-EDSDK
und SELPHY-Druck unter Windows sind geschrieben, aber noch nicht an der echten Hardware
getestet. Vor dem ersten Event: Dauertest (siehe unten).**

## Was sie kann

| Bereich | Stand |
|---|---|
| Modi Foto, Collage, Boomerang, Video | ✅ |
| Framing vor der Aufnahme (4:5, 9:16, 3:2 …), Live-Bild im gewählten Rahmen | ✅ |
| Collagen hoch **und** quer, eigene JSON-Vorlagen, wählbare Hintergründe | ✅ |
| Boomerang/Video 9:16 als Ausschnitt oder ganzes Querbild im unscharfen Rahmen | ✅ |
| Looks nach der Aufnahme (Warm, Kühl, S/W, Film, Kräftig, Weich) | ✅ |
| Nochmal, Löschen (auch auf dem Server), Galerie an der Box | ✅ |
| QR am Bildschirm (antippen = Vollbild) und optional auf dem Druck, gebrandet | ✅ |
| QR funktioniert sofort, auch offline (signierte Kennung, Seite „unterwegs“) | ✅ |
| Druckwarteschlange, Kopien, Limits je Aufnahme und je Event, Auto-Druck | ✅ |
| Countdown einstellbar (erstes/weitere Fotos, über Live-Bild oder eigener Screen), Töne | ✅ |
| Startbildschirm: Bilder, GIFs, Videos im Wechsel, Button-Anordnung und -Größe, alle Texte | ✅ |
| Mehrsprachig (DE/EN, weitere per Text-Profil) | ✅ |
| Fernsteuerung über die Galerie: Profil, Medien, Pause, Aufträge | ✅ |
| Outbox: lädt hoch, sobald Netz da ist, kleine Dateien zuerst | ✅ |
| Zusatzziele NAS (SFTP) und Online-Galerie per FTPS | ✅ (ungetestet gegen eine echte Online-Galerie) |
| PIN-Verwaltung an der Box, Log, Testfoto, Testdruck | ✅ |
| Plugins (pluggy) | ✅ |
| Buzzer/Fußschalter (Leertaste/Enter) | ✅ |
| Greenscreen, Unterschrift, Umfragen, E-Mail/SMS, Zweitdrucker | ⏳ Backlog |

## Schnellstart am Mac/Linux (ohne Kamera)

```bash
cd booth
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m boothcore --camera mock --no-kiosk
# http://127.0.0.1:8765  ·  Verwaltung: /admin (Werks-PIN 2580, verlangt sofort eine eigene)
```

## Windows-Box

1. Repo auf den Box-PC holen, `booth\scripts\install-windows.ps1` ausführen.
2. Canon **EDSDK** über das Canon Developer Programme holen, `EDSDK.dll` + `EdsImage.dll` nach `booth\EDSDK\`.
3. Kamera: Netzteil mit Akku-Attrappe (DR-E12), Auto-Abschaltung aus, Modus P oder M.
4. Drucker: SELPHY CP1300 per USB, im Treiber randlos einstellen.
5. Box starten, oben links 3 s drücken → PIN → Galerie-Adresse und Geräte-Schlüssel eintragen.

**Boomerang/Video-Auflösung:** Es wird genommen, was per USB von der Kamera kommt, ohne Hochskalieren.
Liefert die Kamera 1080p, wird es 1080p; die M50 liefert über das EDSDK ein kleineres Live-Bild
(etwa 960×640), dann wird in dieser Auflösung gespeichert. Kein Zusatzgerät nötig.

## Kameras

| Anschluss | Kameras | System |
|---|---|---|
| `edsdk` | Canon EOS, EOS M, EOS R (Canon EDSDK, kostenlos nach Registrierung, nicht im Repo) | Windows |
| `digicamcontrol` | Nikon, Sony, Canon, Fujifilm u. a. über [digiCamControl](https://digicamcontrol.com) (Open Source) mit eingeschaltetem Webserver | Windows |
| `gphoto2` | über 2 500 Modelle (libgphoto2) | Linux |
| `folder` | jede Kamera mit Hersteller-Tethering (Sony Imaging Edge, Nikon NX Tether, Fujifilm X Acquire …): die Box nimmt das neueste Foto aus dem Ordner | beide |
| `webcam` | Webcams, Capture-Karten, Hersteller-Webcam-Tools | beide |
| Plugin | eigenes Backend über `booth_register_cameras` | beide |

ISO, Blende, Zeit und Weißabgleich lassen sich in der Galerie je Box setzen (edsdk, digicamcontrol, gphoto2).
Das Live-Bild füllt beim Auslösen den ganzen Bildschirm, der Ausschnitt des Ergebnisses ist markiert (abschaltbar).

## Drucker

Beliebig viele, Verteilung nach kürzester Warteschlange oder der Reihe nach als Ersatz; je Drucker „nur Hochformat“ / „nur Querformat“.
Schlägt ein Druck fehl, übernimmt der nächste.

| Anschluss | Wofür |
|---|---|
| `windows` | alles mit Windows-Treiber: Canon SELPHY, DNP (DS620, DS-RX1, QW410), Mitsubishi, HiTi, im Windows eingerichtete Netzwerk-/AirPrint-Drucker |
| `cups` | Linux, inkl. AirPrint/IPP Everywhere per driverless |
| `ipp` | direkt an einen AirPrint-/IPP-Drucker im Netz, ohne Treiber: `ipp://192.168.1.40/ipp/print` |
| `hotfolder` | Datei in einen Ordner legen, z. B. DNP Hot Folder Print |

## Roadmap

* Greenscreen / Hintergrund ersetzen (klassisches Chroma-Key, ohne KI)
* Live-Test EDSDK, digiCamControl und DNP an echter Hardware

## Dauertest vor dem ersten bezahlten Event

200 Auslösungen am Stück, Kabel ziehen und wieder stecken, Kamera aus/an, Drucker ohne
Papier, WLAN weg und wieder da. Erst wenn das ohne Handgriff durchläuft: Event.

## Aufbau

```
boothcore/
  app.py            Dienste + HTTP-API für die Oberfläche
  camera/           mock · webcam · edsdk (Canon) · gphoto2 (Linux) + Kamera-Thread
  media/            Framing, Vorlagen, Looks, Boomerang/Video, gebrandete QR-Codes
  printing/         Windows-GDI · CUPS · Ordner + Warteschlange mit Limits
  sync/             Outbox (SQLite), Galerie-API v2, SFTP, FTPS, Agent
  library.py        Galerie an der Box, Löschungen
  plugins.py        Hook-Spezifikation
  web/              Kiosk-Oberfläche (ohne Build-Schritt) + Verwaltung
```

Lizenz: AGPL-3.0-or-later mit Zusatzerlaubnis für das Canon-EDSDK, siehe `LICENSE` und `LICENSE-EXCEPTION` im Stammordner. Das Canon-SDK ist nie Teil dieses Repositorys oder des freien Downloads.
