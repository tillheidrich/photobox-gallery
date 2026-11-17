# Boomerang und Video aus dem Film der Kamera

English: [CAMERA-MOVIE.md](CAMERA-MOVIE.md)

Normalerweise entstehen Boomerang und Video aus dem Live-Bild, das die Kamera
über USB schickt. Bei einer Canon EOS sind das rund 960×640 – ein 9:16-Film
hat dann 360×640.

`boomerang.source` und `video.source` schalten das je Modus um:

| Wert | was passiert |
|---|---|
| `live` (Standard) | Bilder aus dem Live-Bild, wie bisher |
| `camera` | die Kamera filmt selbst auf ihre SD-Karte, die Box lädt den Film, löscht ihn von der Karte und macht daraus den Clip |

Einstellen in der Galerie (Box-Profil, „Boomerang & Video“, Feld „Quelle“)
oder in der Verwaltung an der Box (`boomerang.source` / `video.source`).

## Was es braucht

* Den Canon-SDK-Anschluss (`edsdk`) und eine Kamera, die über USB filmen kann
  (getestet mit einer EOS M50). gphoto2, digiCamControl, Webcam und Ordner
  melden „nicht unterstützt“, die Box filmt dann aus dem Live-Bild.
* Eine SD-Karte in der Kamera mit freiem Platz. Jeder Film wird erst gelöscht,
  wenn er vollständig geladen ist; sonst rührt die Box nichts auf der Karte an.
  Die Karte möglichst leer halten: Die Box liest vor und nach jedem Film die
  DCIM-Ordner.
* Die Filmgröße stellt man an der Kamera ein. Empfehlung: Full HD 25p oder
  30p. Eine Sekunde 4K sind rund 15 MB und laden länger.

## Der Clip

* Feste Größe: Hochformat und quadratisch 1080 breit (9:16 1080×1920, 4:5
  1080×1350, 1:1 1080×1080), Querformat 1920 breit (16:9 1920×1080, 3:2
  1920×1280). Ein Full-HD-Ausschnitt im Hochformat (608×1080) wird mit
  Lanczos hochskaliert.
* „Ausschnitt“ (und Rahmen) nehmen die Mitte des Films im Format des Clips,
  „Unscharfer Rahmen“ zeigt das ganze Filmbild auf dem unscharfen Hintergrund.
* Die Bildrate kommt aus der Datei; der Clip hat nie mehr Bilder pro Sekunde
  als der Film und nie mehr als `fps`.
* Ohne Ton: die Tonspur der Kamera fällt weg, wie bei jedem Film der Box.
* Die Kamera schreibt erst einen Moment nach dem Startbefehl, deshalb filmt sie
  0,7 s länger und der Clip nimmt die letzten Sekunden der Datei.
* Ohne Rahmen und ohne Druckbogen macht ffmpeg den Film in einem Durchgang
  direkt aus dem Film der Kamera.
* booth.log: `Film: Zeiten ab Anfrage {...}` und `Warten auf den Film …, ffmpeg {...}`.
* Der Rohfilm liegt in `<Daten>/cache/kamerafilm/`, nie im Ordner des
  Durchgangs, in der Upload-Warteschlange oder im Backup, und wird gelöscht,
  sobald der Clip fertig (oder gescheitert) ist. Reste älter als zehn Minuten
  räumt die nächste Aufnahme weg.

## Wenn es nicht klappt

Der Gast bekommt in jedem Fall einen Clip, im selben Durchgang; der Grund
steht im Log der Box („Film der Kamera …“):

* Kamera lehnt Filmmodus oder Start ab (keine Karte, Moduswahlrad, Kamera
  kann nicht filmen): Die Box nimmt stattdessen aus dem Live-Bild auf; der
  Gast stand bis zu etwa 6 s länger vor „Aufnahme“.
* Keine Datei, Download abgebrochen, ffmpeg scheitert: Die Live-Bilder, die
  *während* des Films mitliefen, werden genommen – niemand nimmt zweimal auf.

Die Kamera wird immer zurückgestellt: Aufnahme gestoppt, Filmmodus aus,
Bilder wieder zum PC mit derselben Einrichtung wie beim Verbinden (SaveTo Host,
dann Kapazität, Einzelbild, Uhr, Live-Bild), Betriebsart geprüft, SaveTo gelesen. Klappt das nicht,
verbindet die Box die Kamera neu, wie nach jedem Kamerafehler.
