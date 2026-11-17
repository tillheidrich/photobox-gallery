# Photobox Gallery auf einem NAS (Synology, QNAP)

Für alle, die keinen eigenen Server im Internet haben, aber ein NAS zu Hause
oder im Büro. Die Galerie läuft dort als Container. Diese Anleitung nutzt die
Datei `docker-compose.nas.yml`; die normale `docker-compose.yml` ist für einen
Server hinter einem Reverse Proxy (zum Beispiel Coolify) gedacht und lauscht nur
auf dem NAS selbst — im Heimnetz wäre sie nicht erreichbar.

## Was du vorher wissen solltest

| Funktion | Nur im Heimnetz (LAN) | Braucht Erreichbarkeit von außen |
|---|---|---|
| Box koppeln, Bilder hochladen, Verwaltung | ja, wenn die Box im selben Netz steht | nein |
| QR-Code an der Box, Galerie für Gäste | nur Gäste im selben WLAN | **ja** — Gäste öffnen den Link mit ihrem Handy über das Mobilfunknetz |
| Mails mit Link an Gäste | Link funktioniert nur im LAN | **ja** |
| Passwort-zurücksetzen-Mail an dich | ja (Link zeigt auf `BASE_URL`) | nein |

Kurz: Für eine Hochzeit, bei der Gäste ihre Bilder später zu Hause ansehen
sollen, muss die Galerie aus dem Internet erreichbar sein — mit Domain und
Zertifikat (siehe „Von außen erreichbar machen“). Für eine Box, die nur ins
Büro-NAS sichert, reicht das LAN.

## Voraussetzungen

* NAS mit Container-Unterstützung: Synology „Container Manager“ (DSM 7.2+),
  QNAP „Container Station“. Intel/AMD (amd64) und ARM (arm64) gehen beide.
* Ein Ordner auf einer Freigabe, zum Beispiel `/volume1/docker/photobox`.
* Etwa 1 GB Arbeitsspeicher frei, Speicherplatz für die Bilder.

## Schritt für Schritt (Synology Container Manager)

1. **Ordner anlegen.** In der File Station unter `docker` einen Ordner
   `photobox` und darin `data` anlegen. Den Quellcode (dieses Repository) nach
   `/volume1/docker/photobox/app` kopieren oder dort per Git holen.
2. **Benutzer-ID herausfinden.** Die Galerie läuft nicht als root, sondern als
   ein Benutzer des NAS. Per SSH: `id dein-benutzer` zeigt `uid=1026(...)
   gid=100(users)`. Diese Zahlen sind `PUID` und `PGID`. Dieser Benutzer muss
   im Ordner `data` lesen und schreiben dürfen (File Station → Eigenschaften →
   Berechtigung).
3. **`.env` anlegen** im Ordner `app` (neben `docker-compose.nas.yml`):

   ```
   NAS_DATA_DIR=/volume1/docker/photobox/data
   PUID=1026
   PGID=100
   PORT=5000
   SECRET_KEY=<32 oder mehr zufällige Zeichen>
   BASE_URL=http://192.168.1.20:5000
   ```

   `BASE_URL` ist Pflicht: Daraus baut die Galerie QR-Codes, Kopplungscodes und
   die Mail zum Passwort-Zurücksetzen. Ohne sie verweigert sie diese Links,
   statt eine Adresse aus der Anfrage zu übernehmen. Im LAN ist das die
   IP-Adresse des NAS mit Port; von außen deine Domain (`https://fotos.example.de`).
   `SECRET_KEY` erzeugen: `openssl rand -hex 32`.
4. **Projekt anlegen.** Container Manager → Projekt → Erstellen → Pfad
   `/volume1/docker/photobox/app`, Quelle „vorhandene docker-compose.yml
   verwenden“ und `docker-compose.nas.yml` auswählen. Erstellen; das erste
   Bauen dauert einige Minuten.
5. **Anmelden.** `http://<IP-des-NAS>:5000/admin`. Das Startpasswort steht
   einmalig im Protokoll des Containers (Container Manager → Container →
   Protokoll); danach sofort ein eigenes setzen.
6. **Box koppeln.** In der Verwaltung unter „Boxen“ eine Box anlegen und einen
   Kopplungscode erzeugen, oder an der Box „Mit dem Handy koppeln“ wählen.

**QNAP Container Station:** Anwendung erstellen → den Inhalt von
`docker-compose.nas.yml` einfügen, die Werte aus der `.env` als Variablen
eintragen. `PUID`/`PGID` stehen bei QNAP meist auf `1000`/`100`.

## Von außen erreichbar machen (Reverse Proxy und Zertifikat des NAS)

Der Container selbst spricht nur HTTP. Für Gäste aus dem Internet gehört davor
der Reverse Proxy des NAS mit einem Let’s-Encrypt-Zertifikat:

1. Domain oder DDNS-Namen auf die öffentliche Adresse zeigen lassen (Synology:
   Systemsteuerung → Externer Zugriff → DDNS) und im Router Port 443 auf das NAS
   weiterleiten. **Port 5000 nicht weiterleiten.**
2. Zertifikat: Systemsteuerung → Sicherheit → Zertifikat → Hinzufügen →
   Let’s Encrypt für `fotos.example.de`.
3. Reverse Proxy: Systemsteuerung → Anmeldeportal → Erweitert → Reverse Proxy →
   Erstellen. Quelle `HTTPS`, `fotos.example.de`, Port `443`; Ziel `HTTP`,
   `localhost`, Port `5000`. Unter „Benutzerdefinierte Kopfzeile“ →
   „Erstellen“ → „WebSocket“ ist nicht nötig. Dem Eintrag das Zertifikat
   zuweisen.
4. In der `.env`: `BASE_URL=https://fotos.example.de`.
5. **`TRUST_PROXY=1` nur setzen, wenn der Reverse Proxy der einzige Weg zur
   Galerie ist** — also Port 5000 vom Router aus nicht erreichbar ist und im LAN
   niemand direkt auf `:5000` zugreift, dem du nicht traust. Dann zeigt die
   Galerie die echten Adressen der Besucher und die Bremse gegen
   Passwort-Raten zählt richtig. Ist Port 5000 zusätzlich direkt erreichbar,
   lass `TRUST_PROXY=0`: Sonst könnte jemand die Adresse fälschen, die die
   Bremse zählt.
6. **Port 5000 nur noch am NAS selbst:** `BIND_ADDR=127.0.0.1` in der `.env`.
   Ohne diese Zeile veröffentlicht `docker-compose.nas.yml` den Port bewusst im
   ganzen LAN (`0.0.0.0`) — so erreichen die Box und die Handys der Gäste die
   Galerie unter `http://<IP-des-NAS>:5000`. Mit `127.0.0.1` geht es nur noch
   über den Reverse Proxy; erst dann ist `TRUST_PROXY=1` richtig. Die Vorlage
   `.env.example` steht deshalb auf `TRUST_PROXY=0`.

## Datensicherung

* Alles Wichtige liegt in `NAS_DATA_DIR`: Datenbank, Bilder, Schlüssel. Die
  Galerie legt selbst täglich eine Sicherung unter `data/backups` an (siehe
  [`MAINTENANCE.md`](MAINTENANCE.md)).
* Zusätzlich den ganzen Ordner `data` mit Hyper Backup (Synology) bzw. HBS 3
  (QNAP) auf ein zweites Ziel sichern — ein externes Laufwerk oder ein anderes
  NAS. Eine Sicherung auf demselben Datenträger schützt nicht vor dessen
  Ausfall.
* Snapshots der Freigabe (Btrfs) sind eine gute Ergänzung gegen versehentliches
  Löschen.

## Häufige Probleme

* **„Permission denied“ beim Start:** `PUID`/`PGID` passen nicht zum Besitzer
  von `NAS_DATA_DIR`. Rechte des Ordners für diesen Benutzer setzen.
* **Kopplungscode lässt sich nicht erzeugen („Keine Adresse der Galerie
  eingerichtet“):** `BASE_URL` fehlt.
* **Alle Besucher erscheinen mit derselben Adresse:** Der Reverse Proxy ist
  davor, aber `TRUST_PROXY` steht auf `0`. Siehe oben, wann `1` richtig ist.
