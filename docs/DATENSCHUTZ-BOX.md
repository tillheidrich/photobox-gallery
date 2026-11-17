# Datenschutz an der Fotobox – für Betreiberinnen und Betreiber

Diese Seite sagt, was das Fotobox-Programm mit den Bildern der Gäste macht, wo
es sie ablegt, wohin es sich verbinden kann und was Sie als Betreiber selbst
regeln müssen. Sie beschreibt das Programm, nicht Ihre Veranstaltung.
**Kein Rechtsrat.** Für Ihre Pflichten im Einzelfall fragen Sie Ihre
Datenschutzberatung. English version: [PRIVACY-BOOTH.md](PRIVACY-BOOTH.md).

## Wer verantwortlich ist

Wer die Box aufstellt und Gäste fotografiert, entscheidet über Zweck und Mittel
der Verarbeitung und ist damit Verantwortlicher im Sinne der DSGVO. Der
Herausgeber der Software betreibt Ihre Box nicht und sieht keine Bilder. Nutzen
Sie eine gehostete Galerie eines Anbieters, ist dieser Anbieter für Sie
Auftragsverarbeiter; dafür braucht es einen Vertrag nach Art. 28 DSGVO mit ihm.

## Was die Box speichert und wo

| Was | Wo | Wie lange |
|---|---|---|
| Fotos, Videos, GIFs eines Durchgangs, Druckvorlagen | `%LOCALAPPDATA%\PhotoboxBooth\sessions\` | bis gelöscht (siehe unten) |
| Warteschlange für Galerie und Ablageziele, Bildbibliothek | `outbox.sqlite`, `library.sqlite` im Datenordner | bis zugestellt bzw. wie der Durchgang |
| Einwilligungs-Nachweis je Durchgang (wenn eingeschaltet): gewählte Option, Sprache, Prüfwert des Hinweistexts, Uhrzeit – **nichts über die Person** | beim Durchgang | wie der Durchgang |
| E-Mail-Adresse, wenn ein Gast sich das Bild schicken lässt | `mail.sqlite` im Datenordner | Mail-Warteschlange der Box |
| Protokolle (Technik, keine Bilder) | `logs\booth.log` | rollierend, 5 × 2 MB |
| Einstellungen | `profile.json`, `local.json` | dauerhaft |
| Kopie in einen zweiten Ordner / auf einen USB-Stick | nur wenn unter „Ablage“ eingeschaltet | bis Sie löschen |

**Aufräumen** (Profil `aufraeumen`, ab Werk **aus**): löscht Durchgänge nach
`nach_tagen` Tagen (Vorgabe 14), nur solche, die die Galerie bestätigt hat
(`nur_bestaetigte`), und lässt immer die jüngsten 20 liegen. Der
**datensparsame Modus** (Profil `privacy.mode = print_only`) löscht einen
Durchgang nach dem Druck, spätestens nach `delete_seconds` (120 s).

Installieren, Aktualisieren und Deinstallieren lassen den Datenordner stehen.
Nur beim Deinstallieren fragt das Setup, ob er gelöscht werden soll
(Voreinstellung: nein). Zweite Ablage und USB-Sticks fasst es nie an.

## Was an die Galerie geht

Nur wenn die Box mit einer Galerie gekoppelt ist (`local.json: server.url`):
die Bilder und Videos jedes Durchgangs mit Veranstaltung, Uhrzeit und – wenn
eingeschaltet – der Einwilligungs-Wahl; außerdem Lebenszeichen und
Zustandsmeldungen der Box (Kamera, Drucker, Speicher, Programmfassung,
Protokollauszüge für die Fernwartung). Wählt ein Gast „Nur drucken“, bleibt
dieser Durchgang auf der Box. Wie lange die Galerie Bilder aufbewahrt, legen Sie
in der Galerie fest (Aufbewahrungsfrist der Veranstaltung).

## Alle Netzverbindungen

Ab Werk verbindet sich die Box mit nichts außerhalb des Rechners. Jede Zeile
wird erst durch den genannten Schalter eingeschaltet. Die Tabelle wird aus
`tools/outbound_connections.py` erzeugt; ein Test hält sie mit dem Code gleich.

<!-- outbound:start -->
| Kennung | Was | Wohin | Eingeschaltet durch | Ab Werk |
|---|---|---|---|---|
| `gallery` | Galerie: Bilder hochladen, Profil und Aufträge abholen, Lebenszeichen, Mails über die Galerie, Update-Angebot, Abmeldung beim gewollten Beenden, stündlicher Neuversuch fehlgeschlagener Uploads | die Galerie-Adresse, die der Betreiber einträgt oder per Kopplung bekommt | local.json: server.url | aus |
| `pairing` | Kopplung mit einer Galerie über einen Kopplungscode | die Adresse im Code; ein Kurzcode wird zu https://<code>.photobox-gallery.com | Kopplungscode in der Verwaltung eingeben | aus |
| `remote_control` | Fernsteuerung durch die Galerie (Aufträge aus fester Liste; Antworten an die Galerie, auf Anfrage auch Livebild der Kamera und Bildschirmfoto, höchstens 640 px) | die gekoppelte Galerie (über die Galerie-Verbindung) | local.json: remote_control.allowed | aus |
| `update` | Neue Programmfassung herunterladen (nur wenn die gekoppelte Galerie sie anbietet und der Betreiber oder die Galerie den Abruf auslöst; Prüfung per SHA-256) | die Download-Adresse aus dem Angebot der Galerie | local.json: server.url | aus |
| `mail_smtp` | E-Mails an Gäste und Meldungen an den Betreiber direkt per SMTP | der eingetragene SMTP-Server | Profil: mail.enabled | aus |
| `webhooks` | Webhooks: eigene Adresse bekommt Bescheid, wenn ein Bild fertig ist | die eingetragene Webhook-Adresse | Profil: webhooks.enabled | aus |
| `targets` | Zusätzliche Ablageziele per SFTP, FTPS oder FTP | die eingetragenen Server | local.json: targets | aus |
| `camera_wifi` | Canon-Kamera über WLAN (CCAPI) | die Adresse der Kamera im lokalen Netz | local.json: camera.ccapi_url | aus |
| `digicamcontrol` | Kamera über digiCamControl (läuft auf demselben Rechner) | http://127.0.0.1:5513 (voreingestellt), nur mit Kamera-Backend „digicamcontrol“ | local.json: camera.backend=digicamcontrol | aus |
| `printer_ipp` | Drucker im Netz über IPP | die eingetragene Drucker-Adresse (ipp://...) | local.json: printers | aus |
| `guest_wifi` | Gastseite im WLAN der Box (Herunterladen ohne Internet): eine zweite Gegenstelle auf allen Netzadressen, Port 8766 - eingehend, Telefone der Gäste verbinden sich | eingehend aus dem lokalen Netz | local.json: guest_share.allowed | aus |
| `local_only` | Programmteile untereinander (Wächter, Fenster, Box) - nur 127.0.0.1 | 127.0.0.1 (verlässt den Rechner nicht) | - | an |
<!-- outbound:end -->

Nie: keine Telemetrie, keine Nutzungsstatistik an den Herausgeber, keine
Werbung, keine Gesichtserkennung, keine KI, keine Schriften oder Skripte aus
dem Internet, keine eigene Update-Prüfung ohne gekoppelte Galerie. QR-Codes
entstehen auf dem Rechner.

Außerhalb des Programms: Windows, die WebView2-Laufzeit und – falls die Box auf
Chrome oder Edge zurückfällt – der Browser bauen eigene Verbindungen zu Microsoft
bzw. Google auf (Updates u. a.). Die regeln Sie in Windows bzw. im Browser.

## Was Sie als Betreiber regeln müssen

1. **Informieren (Art. 13 DSGVO)** – vor dem ersten Foto, gut sichtbar an der
   Box. Vorlage unten. Die Box kann zusätzlich vor jeder Aufnahme einen
   Hinweis zeigen (Profil `disclaimer`) oder die Wahl „In die Galerie“ /
   „Nur drucken“ anbieten (Profil `consent`, Text unter `consent.note`).
2. **Rechtsgrundlage** klären (z. B. Einwilligung durch die Wahl an der Box,
   oder ein Vertrag mit dem Gastgeber). Die Box speichert, welche Option ein Gast
   gewählt hat und welchen Text er gesehen hat – nicht, wer er ist.
3. **Löschwünsche** erfüllen: Gäste können ihren Durchgang direkt am Ende an der
   Box verwerfen (solange `allow_delete` an ist, ab Werk an). In der Galerie
   löscht ein Gast seine Bilder über seinen QR-Code. Was danach noch auf der Box
   liegt, löschen Sie im Datenordner bzw. mit „Aufräumen“.
4. **Auskunft** geben können: Durchgänge sind nach Veranstaltung und Uhrzeit
   abgelegt; eine Zuordnung zu Personen gibt es nicht (keine Gesichtserkennung,
   keine Namen außer einer freiwillig eingegebenen E-Mail-Adresse).
5. **Zugriff schützen:** Verwaltungs-PIN ändern (die Werks-PIN wird nur zum
   Festlegen einer eigenen akzeptiert), Windows-Konto mit Passwort, USB-Sticks
   verschlüsseln oder nach der Veranstaltung leeren.

## Aushang an der Box (Vorlage)

Platzhalter in eckigen Klammern ersetzen. Kurz halten, die ausführliche
Information verlinken (QR-Code auf Ihre Datenschutzseite).

> **Fotobox – Hinweis zum Datenschutz**
>
> Diese Fotobox wird betrieben von **[Name, Anschrift, E-Mail des Betreibers]**.
> Wenn Sie ein Foto auslösen, speichern wir es auf dem Rechner der Box
> **[und laden es in die Online-Galerie dieser Veranstaltung hoch, erreichbar
> über Ihren QR-Code / mit Passwort]**. Zweck: Fotos für **[Gastgeber /
> Veranstaltung]** und für Sie. Rechtsgrundlage: **[Ihre Einwilligung durch
> Auslösen / Art. 6 Abs. 1 lit. … DSGVO]**. Gelöscht wird **[nach … Tagen]**.
> Sie können Ihr Foto direkt an der Box verwerfen und es in der Galerie über
> Ihren QR-Code löschen. Sie haben das Recht auf Auskunft, Berichtigung,
> Löschung, Einschränkung, Widerspruch und Beschwerde bei einer
> Datenschutzaufsicht. Es findet keine Gesichtserkennung statt.
> Ausführlich: **[Link oder QR-Code zur Datenschutzerklärung]**.

## Marken

Canon, EOS und SELPHY sind Marken der Canon Inc.; Windows, Edge und WebView2
sind Marken der Microsoft Corporation; DNP ist eine Marke der Dai Nippon
Printing Co., Ltd. Die Namen stehen hier nur, um zu beschreiben, womit das
Programm zusammenarbeitet. Das Projekt ist mit keinem dieser Unternehmen
verbunden.
