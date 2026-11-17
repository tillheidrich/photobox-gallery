# Die Box einrichten: vom Auspacken bis zum ersten Abzug

Für alle, die einen Box-PC mit Windows zum ersten Mal einrichten. Am Ende
druckt die Box ihren ersten Abzug, ist mit der Galerie gekoppelt und startet
nach jedem Einschalten von selbst. Die technischen Hintergründe stehen in
[`WINDOWS-INSTALLATION.md`](WINDOWS-INSTALLATION.md); hier steht der Weg, in
der Reihenfolge, in der du ihn gehst.

**Die Bildschirmfotos fehlen noch.** Wo eines hingehört, steht eine Zeile
`[Bildschirmfoto: …]` mit dem genauen Bild, das aufgenommen werden soll
(Windows-Taste + Umschalt + S, dann als PNG in `docs/bilder/` speichern, Dateiname
wie angegeben). Ohne die Fotos funktioniert die Anleitung auch; sie ist nur
weniger leicht zu lesen.

## Vorher: was bereitliegen muss

| Was | Wozu |
|---|---|
| Box-PC mit Windows 10 oder 11, Touchscreen, Netzteil | Hier läuft die Box. Für die Einrichtung braucht er Internet. |
| Kamera (Canon), USB-Kabel, Netzteil mit Akku-Attrappe | Das Netzteil ist wichtig: Ein leerer Akku beendet den Abend. |
| Aufsteckblitz, falls du mit Blitz arbeitest | Siehe Schritt 5. |
| Drucker (Canon SELPHY oder DNP), USB-Kabel | Und der **Treiber** des Herstellers, siehe Schritt 6. |
| Papier und Farbband (bei der SELPHY: Kassette und Papierfach) | Einmal voll, einmal Ersatz. |
| Das Canon-SDK als entpackter Ordner | Gibt es beim Canon Developer Programme. Es liegt nie im Projekt. |
| USB-Stick | Für die Sicherung während des Abends. |
| Ein Handy | Zum Koppeln mit der Galerie. |
| Zugang zur Galerie | Adresse und Anmeldung. |

## Schritt 1: Aufstellen und anschließen

1. Box-PC auf den festen Platz stellen, Strom und Internetkabel anschließen.
2. Kamera auf Stativ oder Halterung, mit Netzteil. Objektiv ausfahren.
3. Drucker daneben, Strom dran, **noch nicht** per USB mit dem PC verbinden
   (erst nach dem Treiber, Schritt 6).

## Schritt 2: Die Box auf den PC bringen

Es gibt zwei Wege. Der erste ist der normale.

**Weg A: ein Doppelklick.** Du hast den vorbereiteten Ordner „Fotobox-Windows-Paket“
auf einem USB-Stick (Programm, Canon-SDK und Profil nebeneinander).

1. Stick in den PC stecken, Ordner `packaging\windows` öffnen.
2. **`Alles einrichten.bat`** mit Rechtsklick → „Als Administrator ausführen“.
3. Die Ausgabe zu Ende lesen. Steht hinter einem Schritt `--`, wurde er
   übersprungen, meist weil das Skript nicht als Administrator lief.

`[Bildschirmfoto: Das Fenster von „Alles einrichten.bat“ nach dem Lauf, alle Schritte sichtbar, Dateiname schritt2-alles-einrichten.png]`

**Weg B: Schritt für Schritt.** Wenn du die Box selbst bauen oder nur Teile
einspielen willst:

1. `PhotoboxBooth.exe` nach `C:\Fotobox\` kopieren.
2. `Einrichten.bat` daneben legen, Rechtsklick → „Als Administrator ausführen“,
   dann `1` eingeben. Das richtet Autostart, „kein Standby“, „Bildschirm bleibt
   an“, kein USB-Energiesparen, die Verknüpfung auf dem Desktop und eine
   Firewall-Regel nur für diesen PC ein.
3. Noch einmal `Einrichten.bat`, dann `2`, und den entpackten Canon-Ordner angeben
   (in das Fenster ziehen geht). Die Box sucht die passende 64-Bit-Datei selbst.

`[Bildschirmfoto: Das Menü von „Einrichten.bat“ mit den Punkten 1 bis 4, Dateiname schritt2-einrichten-menue.png]`

**Warnung beim ersten Start.** Eine `.exe` ohne Signatur zeigt Windows als
„unbekannter Herausgeber“. Auf deinem eigenen PC: „Weitere Informationen“ →
„Trotzdem ausführen“. Auch signierte Dateien warnen zuerst, bis genug Leute sie
geladen haben; warum das so ist, steht in [`CODE-SIGNATUR.md`](CODE-SIGNATUR.md).

`[Bildschirmfoto: Der blaue Windows-Dialog „Der Computer wurde durch Windows geschützt“ mit aufgeklapptem „Weitere Informationen“ und dem Knopf „Trotzdem ausführen“, Dateiname schritt2-smartscreen.png]`

## Schritt 3: Das eigene Fenster

Die Box läuft in einem **eigenen Programmfenster**, nicht in Chrome. Beim
Start erscheint „Photobox startet …“ auf dunklem Grund, dann der
Startbildschirm im Vollbild. Alt+F4 ist gesperrt, damit kein Gast die Box
schließt. Beenden geht über die Verwaltung („Beenden“), im Notfall über den
Task-Manager. Mehr dazu: [`EIGENES-FENSTER.md`](EIGENES-FENSTER.md).

Die Verwaltung öffnest du, indem du die **Ecke** 3 Sekunden hältst, die in der Galerie
unter „Bildschirm: Ränder und Größe“ eingestellt ist (Voreinstellung: oben rechts). Die PIN ab Werk ist `2580`. Lege sofort
eine eigene fest: mindestens 4 Ziffern, keine Zahlenreihe, nicht 2580.
Später änderst du sie im Menü hinter der Ecke unter „PIN ändern“ (alte PIN, dann
zweimal die neue) oder in der Galerie im Box-Profil unter „Pause und Zugang“.
Eine an der Box geänderte PIN bleibt, auch wenn die Galerie danach ihr Profil
schickt; erst eine neue PIN, die in der Galerie eingetragen wird, ersetzt sie.
Nach fünf falschen Eingaben pausiert die Box eine Minute, jede weitere Runde
doppelt so lange (höchstens 15 Minuten), auch über einen Neustart hinweg.

`[Bildschirmfoto: Der Startbildschirm der Box im Vollbild, Dateiname schritt3-start.png]`
`[Bildschirmfoto: Die PIN-Abfrage nach dem Halten der Ecke, Dateiname schritt3-pin.png]`

## Schritt 4: Die Kamera

1. Moduswahlrad auf **M** (manuell) oder **Av** (Zeitautomatik). Was besser ist,
   hängt vom Licht ab:
   * **M** hält die Helligkeit den ganzen Abend gleich, der Blitz macht das Licht.
     Mit Blitz etwa 1/125 s, Blende 5,6 bis 8, ISO 200 bis 400.
   * **Av** lässt den Raum im Bild sichtbar. Dafür wandert die Belichtungszeit mit
     dem Umgebungslicht und kann im Dunkeln lang genug für Bewegungsunschärfe werden.
2. Automatische Abschaltung der Kamera: **aus**. Bildrückschau: **aus**.
3. Kamera per USB mit dem PC verbinden und einschalten.
4. **EOS Utility darf nicht laufen.** Es nimmt der Box die Kamera weg. Autostart
   von EOS Utility abschalten oder das Programm gar nicht installieren.
5. In der Box-Verwaltung steht der Anschluss auf `edsdk` (Canon über USB).
   Über die Galerie geht das auch: **Box-Einstellungen → Kamera, Drucker und
   Ablage an der Box → Anschluss der Kamera**.

`[Bildschirmfoto: Die Galerie-Seite „Box-Einstellungen“, die Karte „Kamera, Drucker und Ablage an der Box“ mit aufgeklapptem Abschnitt „Kamera“, Dateiname schritt4-kamera-galerie.png]`

Hängt die Box gerade nicht im Netz, geht es auch an der Box selbst, in ihrer
Verwaltung.

## Schritt 5: Der Blitz

Mit Aufsteckblitz musst du der Box zwei Dinge sagen, sonst wird das zweite
Bild einer Collage dunkel: wie lange der Blitz zum Nachladen braucht und
welche Betriebsart die Kamera haben soll.

1. In der Galerie: **Box-Einstellungen → Aufsteckblitz**.
2. **Aufsteckblitz im Einsatz** ankreuzen. **Ladezeit** so lassen (2,5 Sekunden) oder
   länger, wenn der Blitz bei Serienbildern nicht mitkommt. Die Box wartet
   nach jeder Auslösung so lange.
3. **Erwartete Betriebsart** auf M oder Av stellen, je nachdem, was du in Schritt 4 gewählt hast.
   Dreht jemand das Rad weg, warnt die Box. „egal“ schaltet die Prüfung ab.
4. Das Live-Bild ist bei Blitzbelichtung fast schwarz. **Live-Bild aufhellen**
   hilft: entweder ein fester Wert oder „automatisch“. Das betrifft nur das
   Live-Bild, nie die Fotos.

Ohne Blitzgerät kannst du den **Bildschirm als Licht** nehmen (Abschnitt „Bildschirm als Blitz“,
gleiche Seite). Der Vorlauf ist wichtig: Das Licht muss stehen, bevor die
Kamera belichtet.

`[Bildschirmfoto: Die Karte „Aufsteckblitz“ in den Box-Einstellungen, Dateiname schritt5-blitz.png]`

## Schritt 6: Der Drucker

Der wichtigste Fehler steht gleich vorn: **Windows richtet einen SELPHY am
USB-Kabel von selbst mit dem Treiber „Microsoft IPP Class Driver“ ein. Damit
nimmt er keinen einzigen Auftrag an.** Du brauchst den Treiber des Herstellers.

1. Treiber von der Seite des Herstellers laden und installieren:
   * Canon SELPHY: canon.de, Support-Seite des Druckers, „Treiber und Downloads“, Windows 11.
   * DNP: den Treiber des Herstellers oder des Händlers, der das Gerät geliefert hat.
2. **Dann erst** das USB-Kabel anschließen und den Drucker einschalten.
3. In Windows: **Einstellungen → Drucker & Scanner → dein Drucker → Druckereigenschaften → Erweitert**.
   Der Treiber muss „Canon SELPHY CP1300“ (oder dein DNP-Modell) heißen, **nicht**
   „Microsoft IPP Class Driver“. Sonst den Drucker entfernen und mit dem
   richtigen Treiber neu hinzufügen.
4. **Druckeinstellungen**: Papier „Postkarte“ (100 × 148 mm), **randlos**.
   Beim DNP für Streifen im Treiber „2 inch cut“ einstellen, damit der Drucker
   das 10 × 15-Blatt in der Mitte schneidet. Bei anderen Druckern mit der Schere.
5. Eine **Windows-Testseite** drucken. Erst wenn sie kommt, weiter.
6. In der Box: Anschluss `windows`, den Drucker aus der Liste wählen. In der
   Galerie geht das unter **Box-Einstellungen → Kamera, Drucker und Ablage an
   der Box → Drucker**; die Blätter je Kassette (SELPHY: 18) stehen dort auch.

`[Bildschirmfoto: Windows „Drucker & Scanner“ → Druckereigenschaften → Erweitert mit dem Treibernamen im Feld „Treiber“, Dateiname schritt6-treiber.png]`
`[Bildschirmfoto: Die Druckeinstellungen mit Papierformat Postkarte und „randlos“, Dateiname schritt6-papier.png]`

Die Papiergröße im Abzug stellst du in der Galerie ein: **Box-Einstellungen →
Papier und Format**. Wenn der Drucker meldet, was er kann, zeigt die Seite nur
diese Formate.

## Schritt 7: Papier und Farbband einlegen

1. Papier und Farbband nach der Anleitung des Druckers einlegen. Die SELPHY hat
   beides getrennt: **Papierfach** und **Farbbandkassette**, die unterschiedlich
   schnell leer werden.
2. Die Box kann den Vorrat nicht messen, nur mitzählen, was sie selbst druckt.
   Trage deshalb die **Blätter je Kassette** ein (SELPHY: 18).
3. Nach jedem Wechsel in der Galerie unter **Zustand** auf **Papier nachgelegt**
   beziehungsweise **Farbband gewechselt** drücken. Dann steht der Zähler wieder auf voll.

`[Bildschirmfoto: Die Karte „Zustand“ in den Box-Einstellungen mit den Knöpfen „Papier nachgelegt“ und „Farbband gewechselt“, Dateiname schritt7-zustand.png]`

## Schritt 8: Mit der Galerie koppeln

Drei Wege; der erste ist der normale. Alle in der Box-Verwaltung unter
**Galerie-Server**. Ein Neustart ist nicht nötig.

**Mit dem Handy.**

1. An der Box: **Mit dem Handy koppeln**. Beim ersten Mal die Adresse der Galerie eintragen.
2. Die Box zeigt einen QR-Code und einen kurzen Code.
3. QR-Code mit dem Handy scannen, in der Galerie anmelden (mit zweitem Faktor).
4. **Prüfen, dass der Code auf dem Handy derselbe ist wie an der Box.** Dann die Box
   wählen oder „Neue Box anlegen“, bei Bedarf gleich das Event, dann „Koppeln“.

Die Anfrage gilt 10 Minuten. Das Handy sieht den Schlüssel der Box nie.

`[Bildschirmfoto: Der Bildschirm der Box mit QR-Code und Code, Dateiname schritt8-box-qr.png]`
`[Bildschirmfoto: Die Koppeln-Seite der Galerie auf dem Handy, mit Code und Box-Auswahl, Dateiname schritt8-handy-koppeln.png]`

**Mit einem Kopplungscode.** Wenn kein Handy zur Hand ist: In der Galerie unter
**Boxen** bei der Box „Kopplungscode erzeugen“. Den Code an der Box in das Feld
„Kopplungscode“ eintragen. Der kurze Code (Adresse und `ABCD-EFGH`) gilt 15
Minuten und einmal; der lange `PBX1-…`-Code geht auch ohne Netz.

**Mit einer Datei.** Für eine Box ohne Tastatur: Die Datei `kopplung.txt` mit dem
Code in den Datenordner der Box legen (oder neben die `.exe`) und die Box starten.
Nach dem Einlösen löscht die Box die Datei. Klappt es nicht, bleibt sie liegen und
der Grund steht im Protokoll.

Behandle Kopplungscodes wie Passwörter: nicht in Chats, Mails oder Fotos.

## Schritt 9: Das Event zuweisen

Aufnahmen gehen in das Event, das der Box zugewiesen ist. Ohne Event bleiben sie
auf der Box, bis eines zugewiesen ist.

1. In der Galerie: **Boxen → deine Box → Box-Einstellungen**.
2. Karte **Event**: das Event wählen, **Speichern** (unten, die Leiste klebt am Bildschirmrand).
3. Die Box holt die Änderung beim nächsten Kontakt ab, das dauert wenige Sekunden.
   Oben auf der Seite steht „Profil-Version“; an der Box zeigt sich dieselbe Zahl.

`[Bildschirmfoto: Die Karte „Event“ mit gewähltem Event, Dateiname schritt9-event.png]`

## Schritt 10: Probelauf

Der Reihe nach, ohne etwas zu überspringen:

1. **Testfoto** (Galerie: Box-Seite → Fernwartung → „Testfoto“, oder in der Box-Verwaltung): ein Bild kommt.
2. Ein Foto über den Bildschirm der Box: Countdown, Ergebnis, QR-Code.
3. Eine Collage und, wenn du sie anbietest, ein GIF.
4. **Testdruck**: ein Abzug kommt, randlos, richtig herum. Den QR-Code auf dem
   Abzug mit dem Handy scannen: Die Galerie öffnet sich.
5. Die Bilder erscheinen im Event in der Galerie.

Kommt etwas nicht an, hilft [`WENN-ETWAS-HAKT.md`](WENN-ETWAS-HAKT.md), eine
Seite zum Ausdrucken.

`[Bildschirmfoto: Das Ergebnis eines Testfotos in der Galerie, Dateiname schritt10-testfoto.png]`
`[Foto: Ein Probeabzug mit QR-Code, flach hingelegt, Dateiname schritt10-abzug.jpg]`

## Schritt 11: „Alles bereit?“

Zum Schluss lässt du die Box sich selbst prüfen. Sie sagt, was fehlt, bevor es
ein Gast merkt.

1. In der Box-Verwaltung: **Alles bereit?** (zuvor: „Box prüfen“).
2. Alles **Rote** beheben. Gelbes ist ein Hinweis, kein Verbot, aber lies es.
3. Am Ende noch einmal drücken, bis nichts Rotes mehr dasteht.

Die Prüfung meldet zum Beispiel einen falschen Druckertreiber. Sie ersetzt den
Probelauf nicht: Sie sagt, ob die Teile da sind, nicht, ob ein Abzug gut aussieht.

`[Bildschirmfoto: Das Ergebnis von „Alles bereit?“ in der Box-Verwaltung, alles grün, Dateiname schritt11-alles-bereit.png]`
`[Bildschirmfoto: Dasselbe mit einem roten Eintrag, damit man sieht, wie ein Fehler aussieht, Dateiname schritt11-rot.png]`

Zusätzlich vor der ersten Veranstaltung die **Abnahme** in
[`WINDOWS-INSTALLATION.md`](WINDOWS-INSTALLATION.md) (Abschnitt 8) durchgehen:
Netzwerkkabel ziehen, Kamera aus- und einschalten, Papier herausziehen, Rechner
neu starten.

## Vor jeder Veranstaltung ausschalten und prüfen

Eine Liste zum Abhaken, am Tag vorher und noch einmal vor Ort:

* [ ] **Fernzugriff aus.** Wurde der PC aus der Ferne eingerichtet
      ([`EINRICHTEN-MIT-KI-AGENT.md`](EINRICHTEN-MIT-KI-AGENT.md)), `Fernzugriff ausschalten.bat`
      als Administrator ausführen. Danach darf kein Fernzugriff mehr offen sein.
* [ ] **EOS Utility** und andere Kamera-Programme beenden und aus dem Autostart nehmen.
* [ ] **Eigene PIN** gesetzt, nicht 2580.
* [ ] **Probeaufnahmen aufgeräumt**: Testfotos und Testdrucke sind keine Gäste.
      Den Druckzähler vor dem Event zurücksetzen (Fernwartung → „Druckzähler zurücksetzen“),
      wenn du pro Event abrechnest.
* [ ] **Papier und Farbband voll**, Ersatz griffbereit, „Papier nachgelegt“ gedrückt.
* [ ] **Kamera mit Netzteil**, Moduswahlrad richtig, Autoabschaltung aus.
* [ ] **Blitz** geladen, Batterien frisch, wenn der Blitz mit Batterien läuft.
* [ ] **Event zugewiesen**, richtiges Datum, Galerie nicht im „stillen Betrieb“, wenn Gäste sie sehen sollen.
* [ ] **USB-Stick** steckt.
* [ ] **Zeitfenster** („Gebuchte Zeit“) gesetzt, wenn die Box schon vor dem Beginn steht.
* [ ] **Warnung, wenn die Box schweigt** an, mit einer Adresse, die jemand liest.
* [ ] Auf der Galerie-Seite der Box zeigt **Zustand**: Letzter Kontakt vor wenigen Sekunden, Kamera bereit, Drucker bereit.
* [ ] **Rechner neu gestartet**, die Box erscheint von selbst im Vollbild.

## Wenn etwas nicht klappt

* Die Kamera löst nicht aus: [`WINDOWS-INSTALLATION.md`](WINDOWS-INSTALLATION.md), Abschnitt 9.
* Der Drucker druckt nichts: Treiber prüfen (Schritt 6), Windows-Testseite drucken.
* Die Box meldet sich nicht in der Galerie: Netzwerk prüfen, „Letzter Kontakt“ auf der Box-Seite lesen.
* Etwas Grundsätzliches: [`WENN-ETWAS-HAKT.md`](WENN-ETWAS-HAKT.md).

## Anhang: einzelne Einstellungen genauer erklärt

Die Hilfetexte in den Box-Einstellungen sind kurz gehalten. Was dort nicht mehr
Platz hat, steht hier.

**Einwilligung je Durchgang** (Karte „Betrieb an der Box“). Vor der Aufnahme
wählen die Gäste „In die Galerie“ oder „Nur drucken“, zwei gleich große Knöpfe,
keiner vorausgewählt. „Nur drucken“ verlässt die Box nicht (kein Upload, kein
QR-Code) und wird nach dem Druck gelöscht; es wird nur angeboten, wenn Drucken an
ist. Die Box merkt sich je Durchgang nur die Wahl, welcher Text galt und wann,
nichts über die Person. `{tage}` im Text wird durch die Aufbewahrungsfrist des
Events ersetzt, denn so lange hält die Galerie die Bilder wirklich; die Zahl im
Feld gilt nur ohne Event. Gibt es nur eine Möglichkeit (kein Drucker oder „Nur
drucken“ aus), zeigt die Box den Hinweis auf dem Aufnahme-Bildschirm, statt den
Schritt spurlos wegzulassen.

**Datensparsamer Betrieb.** Die Box kann in diesem Modus nichts übertragen. Sie
blendet die Knöpfe nicht nur aus. Es gibt keine Übertragung ins Netz, keine
Speicherung über den Druck hinaus und keinen Zugriff aus der Ferne auf Bilder.
Das ist der Text für den Aushang. „Alles bereit?“ meldet, wenn trotzdem eine
Galerie oder ein Stick eingetragen ist.

**Download ohne Internet.** Ohne Internet am Veranstaltungsort führt der QR-Code in
die Galerie ins Leere, bis die Box wieder online war. Stelle einen WLAN-Router oder
Hotspot neben die Box (die Box richtet selbst kein WLAN ein) und verbinde den
Box-PC damit. Der Ergebnisbildschirm zeigt dann zwei Codes: „1. WLAN beitreten“
und „2. Foto laden“. Das Bild kommt direkt von der Box, über einen eigenen Port
nur für diese Seite (Voreinstellung 8766). Der Link gilt nur für diesen einen
Durchgang und läuft ab. Im datensparsamen Betrieb und bei „Nur drucken“ gibt es
ihn nicht. Adresse und Port stehen in den lokalen Einstellungen der Box
(`guest_share`).

**Mirror-Booth.** Hinter einem halbdurchlässigen Spiegel sieht der Gast sein
Spiegelbild; die Oberfläche läuft dann seitenverkehrt. Beschriftungen, Zahlen und
der QR-Code werden automatisch zurückgedreht, sonst wäre nichts lesbar und nichts
scannbar.

**Vorschau im Live-Bild bei Blitz** (Karte „Kamera-Belichtung“). Das Live-Bild
bekommt eigene, hellere Werte; unmittelbar vor dem Auslösen stellt die Box die
Werte der Aufnahme wieder ein. „auto“ hebt die ISO stufenweise bis zur Obergrenze,
danach (wenn „Zeit im Live-Bild“ leer ist) die Zeit bis zur längsten. Reicht beides
nicht, hellt die Box das Live-Bild zusätzlich in Software auf. Die Aufnahmen
bleiben unberührt.

**E-Mail über die Galerie.** Die Box schickt Adresse und Durchgang, die Galerie
hängt das Bild an und verschickt von ihrer Adresse. Am Veranstaltungsort ist der
SMTP-Port oft gesperrt; dieser Weg braucht ihn nicht. Der Postausgang steht in der
Galerie unter Einstellungen → E-Mail.

**Webhooks.** Ist ein Geheimnis gesetzt, unterschreibt die Box jeden Aufruf mit
HMAC-SHA256 im Header `X-Photobox-Signature`. So kann die Gegenstelle prüfen, dass
der Aufruf von dieser Box kommt. Das Geheimnis geht nur an die Box und wird in der
Galerie nie wieder angezeigt.

**Rahmen und Textzeilen.** x, y und Größe stehen in Prozent (die Größe bezogen auf
die kürzere Bildseite), damit dieselbe Einstellung für 4:5, 9:16 und die Druckkarte
passt. `{event_name}` und `{date}` füllt die Box beim Aufnehmen ein.

**Streifen drucken.** Fotodrucker können keine Streifen. Wer 5 × 15 cm will, druckt
ein 10 × 15-Blatt mit zwei Streifen nebeneinander und lässt den Drucker in der
Mitte schneiden. Beim DNP ist das eine Einstellung im Treiber („2 inch cut“), bei
anderen geschieht es mit der Schere. Die Box baut das Blatt, der Drucker bleibt auf
10 × 15 stehen.

**Hell oder dunkel** (Karte „Aussehen“). Dunkel ist die Voreinstellung: Im
abgedunkelten Raum blendet eine helle Fläche, und das Foto soll das Hellste im Bild
sein. Bei Tageslicht, im Schaufenster oder in einer hellen Markenwelt liest sich
Hell besser. „Eigene Farben“ nimmt die sechs Farben der Karte.

**Anleitung auf dem Startbildschirm.** Die Schritte stehen unter „Texte“ als
`start.steps`, eine Zeile je Schritt und je Sprache eigen. Die Box macht daraus die
Nummerierung. Sobald die Anleitung an ist, rücken die Modus-Kacheln nach rechts; sie
liegt also nie über den Knöpfen. Für ein eigenes Startbild mit eingebrannter
Überschrift, Anleitung und QR: Titelzeile und Statusleiste aus, Abdunklung 0 %,
Einpassung „steht still“. Ohne Statusleiste bleibt eine Betreiber-Meldung als
farbiger Punkt oben rechts sichtbar.

**Hintergründe je Bildschirm.** Ein Bild aus den Medien (Startbildschirm, Collage-
oder Greenscreen-Hintergrund, Pausenbild) oder eine Farbe. Leer heißt: Hintergrundfarbe
aus „Aussehen“. Die Abdunklung hält Text darauf lesbar. Beim Countdown bleibt das
Kamerabild vorn; der Hintergrund füllt nur die Fläche daneben.

**Medien für die Momente dazwischen.** Keine Phase wartet auf ihr Medium: Es läuft,
solange die Phase ohnehin dauert, und wird abgeschnitten, wenn sie endet. Nur „Nach
dem Ergebnis“ verlängert den Durchgang, und nur um die eingestellten Sekunden
(0 = aus). Der Text kommt aus „Texte“ (`phase.between`, `phase.after`; Warten und
Fehler behalten ihre Texte). Für den Leerlauf gibt es den Bildschirmschoner mit
eigener Auswahl.

**Texte.** Leer heißt: Es bleibt beim mitgelieferten Text, der grau im Feld steht.
Jede Sprache aus „Sprachen“ bekommt eine Spalte. `{n}` und `{s}` sind Platzhalter
(Anzahl, Sekunden) und müssen stehen bleiben. Nicht sicher, welcher Text wo steht?
An der Box `/?keys=1` aufrufen: Dort steht überall der Schlüssel statt des Textes.

**Buzzer und Tasten.** Ein Buzzer ist meist eine ausgeschlachtete Maus oder eine
Tastatur mit einer Taste; beides kommt ohne Treiber an. Der Auslöser drückt immer die
Hauptschaltfläche des Bildschirms, auf dem der Gast gerade steht: Start → Modus,
Vorbereitung → Auslösen, Ergebnis → fertig. Hat der Buzzer mehr als eine Taste,
bekommt jede weitere eine eigene Aufgabe. Steckt im Gehäuse kein Touchscreen, sondern
zwei oder drei Knöpfe, reicht „auslösen“ allein nicht, denn auf dem
Vorbereitungsbildschirm gibt es etwas zu wählen: `next` bewegt die Markierung,
`select` drückt das Markierte. Die Markierung ist ein kräftiger Ring, der aus zwei
Metern zu sehen ist. Zwei Knöpfe: einer auf `next`, einer auf `select`. Drei Knöpfe:
dazu `back`. Ein Knopf: `trigger`, und unter „Was der Gast selbst wählen darf“ die
Auswahlschritte abschalten; dann fragt die Box nie, und ein Druck macht ein Bild.
Tasten mit eigener Aufgabe gelten vor allen anderen Zuordnungen.

**Pfeiltasten** (`Bedienung` → „Pfeiltasten bedienen die Oberfläche“, ab Werk an). Die
Box ist dann ohne Touch und Maus nur mit der Tastatur bedienbar. Was welche Taste tut:

| Taste | Pfeiltasten an (Standard) | Pfeiltasten aus |
|---|---|---|
| ← → ↑ ↓ | Markierung zum nächsten Bedienelement in dieser Richtung; in einer Auswahlreihe (Format, Layout, Hintergrund, Filter) wählen ← → sofort aus, ↑ ↓ wechseln die Reihe. In der großen Ansicht aus der Galerie der Box: ← → voriges/nächstes Bild. Lösen nie aus, auch wenn Pfeil rechts unter „Tasten zum Auslösen“ steht | → löst aus, wenn es unter „Tasten zum Auslösen“ steht (ab Werk ja); die anderen nichts |
| Enter, Leertaste | drücken, was mit den Pfeilen markiert wurde (innerhalb der letzten 15 Sekunden, nicht nach einem Fingertipp); sonst – und auf der Hauptschaltfläche („Los geht's“, „Fertig“, Modus des Auslösers) – lösen sie aus wie bisher, mit Prellschutz | lösen aus |
| Bild ab, F13, NumpadEnter | lösen immer aus (Buzzer): Start → Modus des Auslösers, Vorbereitung → Aufnahme, Ergebnis → Fertig | lösen aus |
| Escape, Rückschritt | zurück: erst ein offenes Fenster schließen, sonst zum Start (nie während Aufnahme und Verarbeitung) | nur Escape (bzw. „Zurück zum Start“) |
| Tasten mit eigener Aufgabe | gelten vor allem anderen, auch ein dort eingetragener Pfeil | ebenso |

Beim Wechsel des Bildschirms liegt die Markierung für einen Tastatur-Gast auf der
Hauptschaltfläche. Wer einen Presenter benutzt, dessen „weiter“ Pfeil rechts ist und der
als Buzzer dient, schaltet die Pfeiltasten aus; dann verhält sich alles wie vorher.

**Ränder des Bildschirms.** Wird das Panel getauscht oder läuft die Box an einem
Fernseher, liegt die Ecke schnell außerhalb des Sichtbaren, und der Entsperr-Knopf ist
weg. Statt das Gestaltungsraster zu ändern, rückt man hier alles ein Stück ein, in
Prozent der jeweiligen Bildschirmseite.

**Warnung, wenn die Box schweigt.** Eine Mail, wenn die Box sich eine Weile nicht
meldet, und eine, wenn sie wieder da ist. Fährt jemand die Box bewusst herunter
(Beenden an der Box, Windows herunterfahren), meldet sie das vorher; dann kommt keine
Warnung. Ob die Box steht oder nur das Internet vor Ort weg ist, kann die Galerie nicht
unterscheiden. Ein Stromausfall oder Absturz wird nicht angekündigt; dann kommt die
Warnung nach der eingestellten Zeit.
