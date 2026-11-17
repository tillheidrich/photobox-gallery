# Druckvorlagen bauen — Anleitung für eine KI

Diese Datei reicht allein: Eine KI (ChatGPT, Claude oder ein anderes Modell)
ohne Zugriff auf das Repository soll damit gültige, gut aussehende
Druckvorlagen für die Fotobox bauen können, gedacht für den **Canon SELPHY
CP1300** (gilt genauso für den CP1500). Alles hier ist aus dem Code
abgeleitet (`app/vorlagen_kern.py`, `app/vorlagen_render.py`, beide
byte-gleich auch in `booth/boothcore/media/`, und dem Druckerprofil in
`booth/boothcore/printing/models.py`). Was der Code nicht hergibt, steht
hier nicht — oder ausdrücklich als „nicht belegt“.

English twin: [`TEMPLATE-FORMAT-FOR-AI.md`](TEMPLATE-FORMAT-FOR-AI.md).
Die vier Beispiele unten werden bei jedem Testlauf durch den echten Prüfer
geschickt und gerendert (`tests/test_template_doc_examples.py`); ändert sich
das Format, schlägt der Test fehl, bevor diese Anleitung falsch wird.

<!-- schema: MAX_SLOTS=12 MAX_TEXTE=6 MIN_KANTE=80 MAX_FORMEN=20 MAX_BILDER=10 -->

## 1. Was eine Vorlage ist und wie sie zur Box kommt

Eine Vorlage ist **eine JSON-Datei**, die beschreibt, wo auf dem Druckblatt
die Fotos, Texte, Formen, Bilder und der QR-Code sitzen. Farben und Schrift
kommen zum großen Teil aus dem **Stil** der Box (Abschnitt 6): Dieselbe
Vorlage wird so zur Hochzeits- oder zur Firmenkarte.

Wege zur Box (die Boxen holen neue Vorlagen beim nächsten Kontakt mit der
Galerie):

1. **Vorlageneditor** in der Galerie: *Admin → Vorlagen → Editor*. Er prüft
   beim Speichern mit demselben Prüfer wie unten beschrieben und zeigt eine
   Vorschau aus demselben Renderer, mit dem die Box druckt.
2. **MCP-Werkzeug `save_template`** (für einen verbundenen KI-Assistenten,
   Recht `manage`): legt an oder ersetzt (gleiche `id` = ersetzen), geprüft
   wie im Editor. `validate_template` (Recht `read`) prüft nur, ohne zu
   speichern.
3. **Vorlagenpaket importieren** (*Admin → Vorlagen*, ZIP aus „Exportieren“):
   jede Vorlage darin wird geprüft.
4. **Hochladen unter „Medien & Vorlagen“** als Art „Collage-Vorlage (JSON)“.
   Achtung: Dieser Weg prüft nur, dass `id` und `slots` vorhanden sind — die
   Datei geht **ungeprüft** an die Box. Für eine von einer KI gebaute
   Vorlage deshalb Weg 1 oder 2 nehmen, oder vorher `validate_template`.

## 2. Blattformate (Feld `paper`)

Alles bei 300 dpi. `canvas` ist `[Breite, Höhe]` in Pixeln und **muss** zum
Papier passen: Der Prüfer übernimmt ein abweichendes `canvas` (nur auf
400…4000 px begrenzt), gedruckt wird dann ein Blatt mit falschem
Seitenverhältnis.

| `paper` | `canvas` (px) | Maß (mm) | Lage | SELPHY CP1300 |
|---|---|---|---|---|
| `10x15` | 1200 × 1800 | 101,6 × 152,4 | hoch | ja |
| `10x15-quer` | 1800 × 1200 | 152,4 × 101,6 | quer | ja |
| `streifen` | 1200 × 1800 | 101,6 × 152,4 | hoch | ja (zwei Streifen, von der Vorlage selbst nebeneinander gezeichnet) |
| `streifen-2x6` | 600 × 1800 | 50,8 × 152,4 | hoch | ja (einmal gestaltet, zweimal nebeneinander auf 10×15 gedruckt, von Hand geschnitten) |
| `quadrat` | 1200 × 1200 | 101,6 × 101,6 | — | nein |
| `13x18` | 1535 × 2126 | 130 × 180 | hoch | nein |
| `13x18-quer` | 2126 × 1535 | 180 × 130 | quer | nein |
| `15x20` | 1800 × 2400 | 152,4 × 203,2 | hoch | nein |
| `15x20-quer` | 2400 × 1800 | 203,2 × 152,4 | quer | nein |
| `15x23` | 1800 × 2700 | 152,4 × 228,6 | hoch | nein |
| `11x20` | 1350 × 2400 | 114,3 × 203,2 | hoch | nein |
| `20x25` | 2400 × 3000 | 203,2 × 254 | hoch | nein |

Für den SELPHY CP1300 gibt es also genau **vier** Werte:
`10x15`, `10x15-quer`, `streifen`, `streifen-2x6`. Ein unbekannter Wert wird
zu `10x15` (mit Hinweis).

## 3. Das JSON, Feld für Feld

Fehlendes füllt der Prüfer auf. Damit das Ergebnis das ist, was gemeint war,
gelten diese Felder als **Pflicht**: `id`, `name`, `paper`, `canvas`, `slots`.
Was dasteht, muss stimmen: ein falscher Typ, ein Wert außerhalb seiner
Grenzen oder ein Verweis, der kein reiner Dateiname ist, **lehnt die ganze
Vorlage ab** (siehe Abschnitt 5). Felder, die dieses Format nicht kennt,
**fallen weg** und werden in den Hinweisen genannt — an jedem Eingang gleich,
damit nichts beim Renderer ankommt, das dort nicht hingehört.

### Kopf

| Feld | Typ | Regel | Vorgabe |
|---|---|---|---|
| `id` | Text | wird zu Kleinbuchstaben, `ä→ae ö→oe ü→ue ß→ss`, alles außer `a-z 0-9 -` wird `-`, höchstens 40 Zeichen. Gleiche `id` = ersetzt die vorhandene Vorlage | aus `name` |
| `name` | Text | höchstens 60 Zeichen | „Eigene Vorlage“ |
| `paper` | Text | siehe Tabelle | `10x15` |
| `canvas` | `[w, h]` | ganze Pixel, 400…4000 | die des Papiers |
| `safe_area` | Zahl | 0…15, Prozent je Seite; **nur Hilfslinie im Editor**, ändert nichts am Druck | Editor zeigt 3 |
| `order` | Liste von ids | Zeichenreihenfolge von unten nach oben | Fotos, `overlay`, Bilder, Formen, Texte, QR |
| `overlay` | Dateiname | PNG mit Alpha aus den Medien, wird auf die volle Blattgröße skaliert; reiner Dateiname (kein Pfad, kein `..`, keine URL) | keins |
| `background` | Farbe oder Dateiname | die eigene Blattfarbe: `#rrggbb` oder ein `@`-Wert; oder ein Bild aus den Medien (reiner Dateiname). Ein an der Box gewählter Gast-Hintergrund gewinnt weiterhin | `"@hintergrund"` |
| `plain` | `true`/`false` | schlichtes Blatt: die eigene `background`-Farbe auch im Rand eines eingepassten Fotos, kein Rahmen des Stils (die mitgelieferte Familie „Schlicht“) | — |

Der Prüfer schreibt außerdem selbst: `shots` (Zahl der Aufnahmen),
`updated_at`, `order` vollständig, und jedem Element eine eindeutige `id`.

### Bildfelder `slots` (1 bis 12)

| Feld | Regel | Vorgabe |
|---|---|---|
| `x`, `y` | linke obere Ecke in px, wird ins Blatt geschoben | 0 |
| `w`, `h` | mindestens 80 px, höchstens Blattbreite/-höhe | halbe Blattbreite/-höhe |
| `shot` | welche Aufnahme (0 = erste); Nummern werden lückenlos ab 0 durchgezählt | 0 |
| `radius` | Eckenradius in px (0 … halbe kürzere Kante) oder `"@rund"` = Radius des Stils | `"@rund"` |
| `rotation` | Grad im Uhrzeigersinn um die Mitte, 0…360 | 0 |
| `fit` | `auto`, `beschneiden` (immer formatfüllend) oder `einpassen` (immer das ganze Foto); gilt für dieses Feld vor der Einstellung der Box | Einstellung der Box |
| `id`, `name`, `hidden`, `locked` | siehe „Für jedes Element“ | |

Zwei Felder mit derselben `shot`-Nummer zeigen dasselbe Foto (so macht der
`streifen` zwei gleiche Streifen). Ohne Bildfeld legt der Prüfer eins an.

**Wie ein Foto ins Feld kommt:** Ist das Feld in derselben Lage wie das Foto
(beide quer oder beide hoch), wird formatfüllend mittig beschnitten. Ist die
Lage verschieden, wird das ganze Foto eingepasst und der Rest bleibt in der
Hintergrundfarbe — es wird nie verzerrt. Das ist `auto`, die Vorgabe der Box;
ein eigenes `fit` am Feld gilt für dieses Feld stattdessen.

### Texte `texts` (bis 6)

| Feld | Regel | Vorgabe |
|---|---|---|
| `value` | 1…120 Zeichen; leere Zeilen fallen weg. Platzhalter siehe unten | — |
| `x` | Ankerpunkt waagrecht (bei `center` die Mitte, bei `right` das rechte Ende) | 60 |
| `y` | **Oberkante** der Zeile, 0 … Höhe−20 | Höhe−200 |
| `size` | Schriftgröße in px, 12…200 | 48 |
| `bold` | `true`/`false` | `true` |
| `align` | `left`, `center`, `right` | `left` |
| `color` | `#rrggbb` oder `@schrift`, `@gedaempft`, `@akzent`, `@hintergrund`; alles andere wird `@schrift` | `@schrift` |
| `font` | `system`, `display`, `serif` oder Dateiname einer hochgeladenen `.ttf`/`.otf`; sonst Schrift des Stils | Stil |
| `amp_font` | eigene Schrift nur für das `&` (eine Schreibschrift macht aus `&` sonst ein „Et“) | — |
| `w`, `h` | optionales Textfeld in px; ohne reicht es bis zum sicheren Rand bzw. bis zum nächsten Element darunter | — |
| `line_height` | 0,8…3,0 (Faktor der Schriftgröße) | — |
| `opacity` | 0…100 | 100 |
| `nur_mit_qr` | `true` = nur drucken, wenn ein QR-Code gedruckt wird | — |

Platzhalter in `value`: `{event_name}` (Name des Events), `{date}` (Datum
des Durchgangs, `TT.MM.JJJJ`), `{gast}` (Text, den der Gast eingegeben hat),
`{headline}` und `{subline}` (die beiden Zeilen des Stils). Ein unbekannter
Platzhalter wird leer gedruckt. Zeilenumbruch mit `\n`.

**Was passiert, wenn eine Zeile zu lang ist:** Sie wird zuerst kleiner
gesetzt (bis 60 % der Größe), dann zweizeilig, wenn das Feld hoch genug ist,
dann weiter kleiner; unter 40 % warnt der Prüfer „kaum lesbar“. Eine Zeile,
deren Anker außerhalb des sicheren Rands liegt, wird an den Rand gezogen.
Ein QR-Code neben einer Zeile beendet deren Feld davor.

Schriften: `system` = Inter (SemiBold/Regular), `display` = Archivo
(Bold/SemiBold) — beide reisen mit der Box. `serif` nimmt Georgia vom
System (unter Windows vorhanden) und sonst DejaVu Serif; diese Schrift ist
**nicht** mitgeliefert. Eigene Schriften lädt der Betreiber unter „Medien &
Vorlagen“ als Art „Schriftart“ hoch; die Vorlage nennt dann den Dateinamen.

### QR-Code `qr` (einer, optional)

| Feld | Regel | Vorgabe |
|---|---|---|
| `size` | Kantenlänge in px, 80 … halbe kürzere Blattkante | 200 |
| `x`, `y` | linke obere Ecke | 60 px vom rechten und unteren Rand |
| `rotation`, `hidden`, `locked`, `nur_mit_qr` | wie bei allen Elementen | |

Der Code ist schwarz auf weiß, Fehlerkorrektur M, mit einer Ruhezone von
**2 Modulen**, die im Bild schon enthalten ist. Gedruckt wird er nur, wenn
die Box einen Link dafür hat (gekoppelt, QR auf dem Druck an); sonst bleibt
die Stelle leer, und Ebenen mit `nur_mit_qr` fallen mit weg. Eine
Mindestgröße für sicheres Scannen nennt der Code nicht, und die mitgelieferte
Bibliothek trägt keinen Code. Die Beispiele unten nehmen 220–260 px
(≈ 19–22 mm); der Editor legt einen neuen Code mit 200 px an.

### Formen `shapes` (bis 20)

`{"kind": "rect" | "ellipse" | "line", "x", "y", "w", "h", "fill",
"stroke", "stroke_width" (0…200), "opacity" (0…100), "radius" (nur rect)}`.
`fill`/`stroke`: `#rrggbb`, ein `@`-Wert oder `""` für keins. `w`/`h`
mindestens 2.

### Bilder `images` (bis 10)

`{"file": "<Dateiname>", "x", "y", "w", "h", "opacity"}`. `file` ist ein
**Dateiname aus den Medien der Galerie** (`.png`, `.jpg`, `.jpeg`, `.webp`;
nur `A-Z a-z 0-9 . _ -`, kein Pfad, keine URL), sonst wird das Bild
weggelassen. Das Bild wird in sein Feld eingepasst.

### Für jedes Element

`id` (eindeutig, `a-z 0-9 -`, bis 24 Zeichen; `qr` und `overlay` sind
vergeben), `name` (bis 40 Zeichen, nur Anzeige), `hidden: true` (wird nicht
gedruckt), `locked: true` (im Editor gesperrt), `rotation` (Grad).

## 4. Was am SELPHY CP1300 zu beachten ist

* **Papier:** Postkarte KP-108IN, 100 × 148 mm. Die Box rendert dafür ein
  Blatt von 1200 × 1800 px (4 × 6 Zoll bei 300 dpi) und schickt es so an
  den Drucker.
* **Randlos, mit Beschnitt durch den Drucker:** Das Druckerprofil steht auf
  randlos ohne eigenen Anschnitt. Canons Treiber skaliert die 1200 × 1800 px
  auf seine 1248 × 1872 px (Angabe aus Gutenprint); was über das Papier
  hinausgeht, fällt ab. Wie viel genau, sagt kein Datenblatt verlässlich —
  die Box hat dafür eine Kalibrierungsseite.
* **Sicherer Rand:** Der Code rechnet mit **4 mm = 47 px** je Seite
  (`sicher_mm`). Bis dahin setzt er Textfelder, und dorthin zieht er eine
  Zeile, die zu weit außen steht. Der Editor zeigt als Hilfslinie 3 % je
  Seite (36 px an der kurzen, 54 px an der langen Kante). Regel für Vorlagen:
  **Text, QR-Code und alles, was ganz zu sehen sein muss, mindestens 47 px
  vom Rand.** Fotos und Flächen dürfen bis an die Kante gehen oder darüber
  hinaus gedacht sein — sie werden eben angeschnitten.
* **Querformat:** Eine `10x15-quer`-Vorlage wird quer gerendert (1800 × 1200);
  das Blatt wird beim Druck gedreht, nicht das Bild. Gestalte einfach quer.
* **Streifen:** `streifen-2x6` wird einmal gestaltet (600 × 1800) und
  zweimal nebeneinander auf ein 10×15-Blatt gedruckt; beim SELPHY wird
  **von Hand** in der Mitte geschnitten (der Drucker schneidet nicht). Also
  nichts Wichtiges auf die Schnittkante in der Mitte des Blattes legen; der
  Rand des Streifens ist diese Mitte.
* **Thermosublimation:** Sie weicht Kanten auf (die Box schärft deshalb vor
  dem Druck leicht nach) und verliert gern die hellsten und dunkelsten
  Tonwerte: 2 % und 5 % Grau werden beide Weiß, Zeichnung in sehr dunklen
  Flächen verschwindet. Also keine Information in fast-weißen oder
  fast-schwarzen Abstufungen. Eine Mindeststrichstärke nennt der Code nicht.
* **Perforation:** Zu Abrisskanten des Postkartenpapiers sagt der Code nichts.

## 5. Grenzen und typische Meldungen des Prüfers

Seit 17.11.2026 geht jeder Weg hinein — Upload unter „Medien & Vorlagen“,
der Editor (Galerie und Box), MCP `save_template`/`validate_template`/`add_asset`,
ein Paket, die Bibliothek, ein Kunden-Layout, eine Vorlage, die eine Box
schickt, eine nach einer Wiederherstellung gelesene Datei, eine Datei, die in
den Medienordner der Box synchronisiert oder dort abgelegt wurde — durch
dieselbe Prüfung (`vorlagen_kern.annehmen`). Sie arbeitet in zwei Schritten:

1. **Ablehnen**, was keine Vorlage dieses Formats ist, mit allen Feldern
   benannt („Keine gültige Vorlage — slots[0].x liegt außerhalb von 0 bis
   1200; …“). An der Box wird so eine Datei mit dem Grund im Protokoll
   übersprungen und den Gästen nicht angeboten.
2. **Geraderücken**, was bleibt, und es sagen (Hinweise in `hints` bei MCP, im
   Editor angezeigt): ein Element, das über den Rand reicht, wird aufs Blatt
   zurückgeschoben, Aufnahmenummern werden durchgezählt, unbekannte Felder
   fallen weg.

Eine Vorlage ist dann gut, wenn `validate_template` **keine Hinweise**
zurückgibt und die zurückgegebenen Werte den eigenen entsprechen.

Grenzen: Datei höchstens 256 KB; 12 Bildfelder, 6 Texte, 20 Formen, 10
Bilder, ein QR-Code, 200 Einträge in `order`; Zahlen sind JSON-Zahlen;
Positionen `x`, `y` auf dem Blatt (0 … Breite/Höhe); Bildfeldkante 80 px …
Blatt, Formen ab 2 px, Bilder ab 10 px; QR 80 px … halbe kürzere Kante;
Schrift 12…200 px; Text bis 120 Zeichen (Zeilenumbruch erlaubt, sonst keine
Steuerzeichen), `name` bis 60, `id`/`name` eines Elements bis 40; Blatt
400…4000 px je Kante; Farben `#rrggbb` oder die vier `@`-Werte; Schriften
`system`, `display`, `serif` oder ein reiner `.ttf`/`.otf`-Dateiname; Bilder,
Overlay und ein Bild-Hintergrund reine Dateinamen (`A-Z a-z 0-9 . _ -`, kein
Pfad, kein `..`, keine URL).

| Meldung / Wirkung | Ursache | Lösung |
|---|---|---|
| „Unbekanntes Blattformat — 10×15 hoch genommen.“ | `paper` nicht in der Tabelle | einen der Werte aus Abschnitt 2 nehmen |
| „Ohne Bildfeld gibt es nichts zu zeigen — eines wurde angelegt.“ | `slots` leer oder fehlt | mindestens ein Bildfeld |
| „Die Nummern der Aufnahmen wurden lückenlos durchgezählt.“ | `shot`-Nummern mit Lücke (z. B. 0, 2) | ab 0 ohne Lücke nummerieren |
| „Ein Bild ohne gültige Datei aus den Medien wurde weggelassen.“ | `images[].file` ist Pfad, URL oder falsche Endung | nur einen Dateinamen aus den Medien |
| „Text passt nicht in sein Feld: … wird in N px statt M px gedruckt.“ | Zeile mit dem Beispielnamen zu breit | Schrift kleiner, Feld breiter, oder kürzerer Text |
| „… steht außerhalb des sicheren Bereichs und wird an dessen Rand gesetzt.“ | Textanker näher als 47 px am Rand | Anker nach innen |
| „Keine gültige Vorlage — slots[0].x liegt außerhalb von 0 bis 1200“ | Position außerhalb des Blatts, Größe über dem Blatt oder unter ihrem Mindestmaß | Grenzen einhalten |
| „… ist keine Zahl“ / „… ist kein Text“ / „… muss true oder false sein“ | Zahl als Text, Liste statt Objekt | JSON-Typen wie in den Beispielen |
| „… ist keine erlaubte Farbe (…)“ | Farbe weder `#rrggbb` noch `@`-Wert | `#rrggbb` oder ein `@`-Wert |
| „… muss ein Dateiname aus den Medien sein (…)“ | Schrift, Bild oder Overlay mit Pfad, `..` oder URL | nur einen Dateinamen aus den Medien |
| „… ist kein bekanntes Blattformat (…)“ / „… muss „auto“ oder … sein“ | `paper`, `fit`, `align`, `kind` einer Form nicht aus der Liste | einen Wert aus den Tabellen |
| „Unbekannte Felder weggelassen: …“ (Hinweis) | Felder, die dieses Format nicht kennt | weglassen |
| aufs Blatt zurückgeschoben | ein Element beginnt auf dem Blatt, reicht aber über den Rand | innen halten |
| MCP: „template muss ein Objekt sein …“ | kein JSON-Objekt | Aufbau wie in den Beispielen |

Der Prüfer misst Textzeilen mit dem Beispielnamen „Mia & Tom“ (Editor und
`validate_template`). Ein längerer echter Name kann trotzdem zu breit
werden — dann setzt der Renderer ihn beim Druck kleiner oder zweizeilig.

## 6. Stil, Farben und Hintergrund

Die Vorlage nennt Farben am besten als **Platzhalter**; eingesetzt wird beim
Druck, was der Stil der Box sagt:

| Platzhalter | Stilwert |
|---|---|
| `@hintergrund` | `background` (Blattfarbe) |
| `@schrift` | `ink` (Textfarbe) |
| `@gedaempft` | `muted` (Nebenzeilen) |
| `@akzent` | `accent` (Linien, Rahmen) |

`"radius": "@rund"` an einem Bildfeld nimmt den Eckenradius des Stils. Der
Stil kann außerdem eine Rahmenlinie in der Akzentfarbe ums Blatt ziehen
(`frame`), gezeichnet auf der Ebene `overlay`.

Mitgelieferte Stile (Voreinstellungen): `clean` (dunkel, Hintergrund
`#0f0d09`, Schrift `#f5f3f0`, Akzent `#e7ad59`, Radius 14), `hell`
(`#faf8f5` / `#1a1714` / `#b07d2b`, Radius 14), `geburtstag`
(`#151033` / `#fdf7ff` / `#ffbf3c`, Radius 22, Schrift display, Kopfzeile
„Happy Birthday“), `hochzeit` (`#f7f3ec` / `#2a2620` / `#b39258`, Radius 0,
Rahmen 10, Schrift serif), `feier` (`#110d1a` / `#fbf6ff` / `#ff5f9e`,
Radius 24), `firma` (`#ffffff` / `#101418` / `#1d5fd0`, Radius 6).

**Eigene Hintergrundfarbe:** `background` nimmt `#rrggbb`, einen `@`-Wert oder
ein Bild aus den Medien; ohne das Feld ist das Blatt `@hintergrund`. Ein
`rect` über das ganze Blatt (`x 0, y 0, w/h = canvas`, seine `id` an den
Anfang von `order`) tut dasselbe und ist im Editor zu sehen. Ein Hintergrund,
den Gast oder Betreiber an der Box wählen („Collage-Hintergrund“), gewinnt
über den der Vorlage.

**Overlay/Rahmenbild:** ein PNG mit Transparenz, als Medium hochgeladen
(Art „Overlay“), in der Vorlage als `overlay: "<Dateiname>"`. Es wird auf
die volle Blattgröße skaliert — also im Seitenverhältnis des Blatts und am
besten genau in dessen Pixeln anlegen (1200 × 1800 bzw. 1800 × 1200). Lässt
es ein Fenster für den QR-Code frei und wird kein Code gedruckt, schließt
der Renderer das Fenster mit der umgebenden Farbe.

## 7. Gestaltung auf 10 × 15

* **Hierarchie:** eine große Zeile, eine kleine. Die mitgelieferte
  Bibliothek (135 Vorlagen) setzt auf 10×15 das Datum nie kleiner als
  **30 px** (30–51 px) und den Eventnamen meist zwei- bis dreimal so groß
  (bis 138 px). 30 px sind 2,5 mm Schriftgröße auf dem Abzug.
* **Ränder:** Die Bibliothek setzt die Fotos auf 10×15 mit **96 px**
  Außenrand, gut über dem sicheren Rand von 47 px.
* **Seitenverhältnis der Felder:** Die Kamera liefert in der Regel 3:2
  (die Beispielbilder des Editors sind 3:2 quer). Ein Feld in 3:2 zeigt das
  ganze Foto; jedes andere Verhältnis in derselben Lage beschneidet mittig
  (ein 4:3-Feld nimmt etwa 11 % der Breite weg, ein quadratisches ein
  Drittel). Ein hochkantes Feld für ein queres Foto zeigt das **ganze** Foto
  mit Balken in der Hintergrundfarbe. Hängt die Kamera hochkant, gilt alles
  umgekehrt (2:3). Die Formate 4:5, 9:16 und 3:2, die der Gast an der Box
  wählt, gelten für Einzelfotos, nicht für die Felder einer Vorlage.
* **Texte nicht auf Fotos legen**, außer mit Absicht; niemals auf den
  QR-Code.
* **Farben über Platzhalter**, damit die Vorlage mit jedem Stil funktioniert.

## 8. Vier vollständige Beispiele

Alle vier sind für den SELPHY CP1300, gehen ohne Hinweis durch den Prüfer
und werden im Testlauf gerendert. Texte sind neutrale Platzhalter.

**Ein Foto quer mit Zeile** (`10x15-quer`, Feld 3:2):

```json
{
  "id": "ein-foto-quer",
  "name": "Ein Foto quer mit Zeile",
  "paper": "10x15-quer",
  "canvas": [1800, 1200],
  "slots": [
    {"id": "foto-1", "x": 225, "y": 66, "w": 1350, "h": 900, "shot": 0, "radius": 0}
  ],
  "texts": [
    {"id": "titel", "value": "{event_name}", "x": 900, "y": 1000, "size": 72, "bold": true, "align": "center", "color": "@schrift", "font": "display"},
    {"id": "datum", "value": "{date}", "x": 900, "y": 1092, "size": 34, "bold": false, "align": "center", "color": "@gedaempft"}
  ]
}
```

**Zwei Fotos quer mit QR-Code** (`10x15-quer`, zwei Felder 3:2):

```json
{
  "id": "zwei-fotos-quer",
  "name": "Zwei Fotos quer mit QR",
  "paper": "10x15-quer",
  "canvas": [1800, 1200],
  "slots": [
    {"id": "foto-1", "x": 72, "y": 120, "w": 804, "h": 536, "shot": 0, "radius": "@rund"},
    {"id": "foto-2", "x": 924, "y": 120, "w": 804, "h": 536, "shot": 1, "radius": "@rund"}
  ],
  "texts": [
    {"id": "titel", "value": "{event_name}", "x": 120, "y": 760, "size": 80, "bold": true, "align": "left", "color": "@schrift"},
    {"id": "datum", "value": "{date}", "x": 120, "y": 870, "size": 36, "bold": false, "align": "left", "color": "@akzent"},
    {"id": "qr-hinweis", "value": "Alle Bilder online", "x": 1480, "y": 1010, "size": 30, "bold": false, "align": "right", "color": "@gedaempft", "nur_mit_qr": true}
  ],
  "qr": {"x": 1508, "y": 908, "size": 220}
}
```

**Fotostreifen mit drei Fotos** (`streifen-2x6`, zweimal auf 10×15; Felder
504 × 400, das 3:2-Foto verliert links und rechts je etwa 8 %):

```json
{
  "id": "drei-fotos-streifen",
  "name": "Fotostreifen mit drei Fotos",
  "paper": "streifen-2x6",
  "canvas": [600, 1800],
  "slots": [
    {"id": "foto-1", "x": 48, "y": 48, "w": 504, "h": 400, "shot": 0, "radius": 0},
    {"id": "foto-2", "x": 48, "y": 480, "w": 504, "h": 400, "shot": 1, "radius": 0},
    {"id": "foto-3", "x": 48, "y": 912, "w": 504, "h": 400, "shot": 2, "radius": 0}
  ],
  "texts": [
    {"id": "titel", "value": "{event_name}", "x": 300, "y": 1430, "size": 56, "bold": true, "align": "center", "color": "@schrift"},
    {"id": "datum", "value": "{date}", "x": 300, "y": 1510, "size": 30, "bold": false, "align": "center", "color": "@gedaempft"}
  ],
  "shapes": [
    {"id": "linie", "kind": "line", "x": 200, "y": 1380, "w": 200, "h": 4, "fill": "", "stroke": "@akzent", "stroke_width": 4, "opacity": 100}
  ]
}
```

**Vier Fotos hoch im Raster** (`10x15`, Felder 510 × 400, mit QR-Code):

```json
{
  "id": "vier-fotos-hoch",
  "name": "Vier Fotos hoch im Raster",
  "paper": "10x15",
  "canvas": [1200, 1800],
  "slots": [
    {"id": "foto-1", "x": 72, "y": 72, "w": 510, "h": 400, "shot": 0, "radius": "@rund"},
    {"id": "foto-2", "x": 618, "y": 72, "w": 510, "h": 400, "shot": 1, "radius": "@rund"},
    {"id": "foto-3", "x": 72, "y": 508, "w": 510, "h": 400, "shot": 2, "radius": "@rund"},
    {"id": "foto-4", "x": 618, "y": 508, "w": 510, "h": 400, "shot": 3, "radius": "@rund"}
  ],
  "texts": [
    {"id": "titel", "value": "{event_name}", "x": 600, "y": 1000, "size": 96, "bold": true, "align": "center", "color": "@schrift", "font": "display"},
    {"id": "datum", "value": "{date}", "x": 600, "y": 1130, "size": 40, "bold": false, "align": "center", "color": "@gedaempft"},
    {"id": "qr-hinweis", "value": "Alle Bilder online", "x": 600, "y": 1550, "size": 32, "bold": false, "align": "center", "color": "@gedaempft", "nur_mit_qr": true}
  ],
  "qr": {"x": 470, "y": 1260, "size": 260}
}
```

## 9. Testen ohne Drucker

* **MCP `validate_template`** mit der Vorlage aufrufen: Gibt die bereinigte
  Vorlage und `hints` zurück. Ziel: `hints` leer, Werte unverändert.
* **Editor-Vorschau** (*Admin → Vorlagen → Editor*): rendert mit genau dem
  Renderer der Box, mit nummerierten Beispielfotos („Beispielbild · sample“)
  und dem Beispielnamen.
* **Testdruck** aus dem Editor an eine gekoppelte Box schickt das Blatt mit
  der Marke „Testdruck“ an deren Drucker — das braucht dann doch einen.

## 10. Text zum Einfügen für eine andere KI

> Du baust eine Druckvorlage (JSON) für eine Fotobox mit Canon SELPHY CP1300.
> Halte dich ausschließlich an die angehängte Datei VORLAGEN-FORMAT-FUER-KI.md.
> Erfinde keine Felder. Gib genau ein JSON-Objekt in einem ```json-Block
> zurück, danach zwei Sätze, was die Vorlage zeigt. Mein Wunsch: [Anlass,
> Anzahl Fotos, hoch/quer/Streifen, QR ja/nein, Stimmung].

## 11. Prüfliste, bevor die KI antwortet

1. `paper` ist `10x15`, `10x15-quer`, `streifen` oder `streifen-2x6`, und
   `canvas` ist genau das aus der Tabelle.
2. `id` nur `a-z 0-9 -`, höchstens 40 Zeichen; `name` höchstens 60.
3. 1–12 Bildfelder, jede Kante ≥ 80 px, alle im Blatt; `shot` ab 0 ohne
   Lücke.
4. Bildfelder in der Lage der Kamera (meist quer, 3:2), sonst Balken.
5. Höchstens 6 Texte, `size` 12…200, `value` ≤ 120 Zeichen, nur bekannte
   Platzhalter; Textanker und QR-Code ≥ 47 px vom Rand.
6. Die längste Zeile passt mit einem langen Namen ins Feld (gemessen an
   den mitgelieferten Schriften Inter und Archivo: Breite ≈ 0,55 × size ×
   Zeichenzahl).
7. Farben nur `#rrggbb` oder `@hintergrund/@schrift/@gedaempft/@akzent`.
8. `font` nur `system`, `display`, `serif` oder ein genannter hochgeladener
   Dateiname; `images[].file`/`overlay` nur Dateinamen, die es gibt.
9. QR-Code (falls gewünscht) 220–260 px, nichts darüber, Hinweistext mit
   `nur_mit_qr: true`.
10. Nur Felder aus Abschnitt 3 (unbekannte fallen weg und stehen in den
    Hinweisen), reine Dateinamen ohne Pfad, Zahlen als Zahlen — sonst lehnt
    der Prüfer die ganze Vorlage ab.

## 12. Rahmen für Boomerang und Video

Ein **Rahmen** ist keine Druckvorlage, sondern eine eigene kleine JSON-Datei
(`"kind": "motion_frame"`). Der Boomerang oder das Video läuft in **einem
Fenster** auf einer Fläche im Handyformat; drumherum stehen Hintergrund,
Titel und Datum des Events. Geprüft wird in `app/motion_frames.py` (dieselbe
Datei läuft auf der Box), gespeichert als Medium der Art `motion`
(`rahmen-<id>.json`). Rahmen sind je Box und Modus abschaltbar
(`boomerang.frames`, `video.frames`, Standard aus) und erscheinen nie in den
Listen der Druckvorlagen. Einen QR-Code gibt es im Rahmen nicht: Die Filme
tragen keinen, der Link steht am Ergebnisbildschirm und auf dem Druck.

### Flächen (Feld `format`)

| `format` | Fläche in Pixeln | Wofür |
|---|---|---|
| `9:16` | 1080 × 1920 | Story, Reels, WhatsApp-Status |
| `16:9` | 1920 × 1080 | Bildschirm, Leinwand, YouTube |
| `4:5` | 1080 × 1350 | Instagram-Feed (der Boomerang kann dieses Format haben) |

Der fertige Film hat genau diese Größe (mit `max_width` 1080 bleibt die kurze
Kante 1080; eine feste Auflösung `720p`/`1080p`/`1440p` im Box-Profil setzt
die kurze Kante). Das Format des Rahmens ist das Format des Films.

### Felder

| Feld | Pflicht | Inhalt |
|---|---|---|
| `kind` | ja | immer `"motion_frame"` |
| `name` | nein | Anzeigename, höchstens 60 Zeichen |
| `id` | nein | `a-z 0-9 -`, sonst aus dem Namen; bestimmt den Dateinamen `rahmen-<id>.json` (gleiche `id` = ersetzen) |
| `format` | ja | `9:16`, `16:9` oder `4:5` |
| `canvas` | nein | wird aus `format` gesetzt; ein abweichender Wert ergibt einen Hinweis |
| `background` | nein | `#rrggbb` oder Dateiname eines hochgeladenen Bildes (PNG/JPG/WebP); füllt die Fläche (beschnitten, nicht verzerrt). Fehlt er: `#111111` mit Hinweis |
| `background_to` | nein | zweite Farbe `#rrggbb`: senkrechter Verlauf von `background` (oben) nach `background_to` (unten); nur mit Farbhintergrund |
| `overlay` | nein | PNG mit Transparenz, über dem Film, auf die ganze Fläche gezogen |
| `window` | ja | das Videofenster: `x`, `y`, `w`, `h` in Pixeln der Fläche, mindestens 120 × 120, ganz auf der Fläche (ragt es hinaus, wird es mit Hinweis hineingeschoben); `radius` Eckenrundung (0 … halbe kürzere Kante); `shadow` Schatten 0–60 px; `border` `{width: 0–40, color: "#rrggbb"}` Linie außen ums Fenster |
| `texts` | nein | bis 6 Textzeilen, **genau wie bei den Druckvorlagen** (Abschnitt 3): `value` mit `{event_name}`, `{date}`, `{headline}`, `{subline}`; `x`, `y`, `size` 12–200, `bold`, `align`, `color`, `font` (`system`, `display`, `serif` oder hochgeladene TTF/OTF), optional `w`, `h`, `line_height`, `opacity`, `rotation` |
| `shapes` | nein | Formen wie bei den Druckvorlagen; sie liegen **unter** dem Film (auf dem Hintergrund) |
| `modes` | nein | `["boomerang", "video"]` (Standard beide) oder nur einer |

Ebenen von unten nach oben: Hintergrund (mit Formen und Fensterschatten) →
Film im Fenster (skaliert und beschnitten, bis er das Fenster füllt; das
Seitenverhältnis des Fensters ist frei) → Overlay → Fensterlinie → Texte.
Die Live-Ansicht an der Box zeigt beim Countdown den Ausschnitt des Fensters.

**Harte Fehler** (Speichern wird abgelehnt, Meldung auf Deutsch): falsches
`kind`, unbekanntes `format`, kein `window` oder keine Zahlen darin, Fenster
kleiner als 120 × 120 oder größer als die Fläche, ein Dateiname mit Pfad,
ein Overlay, das kein PNG ist, und (beim Speichern in der Galerie und über
MCP) ein Hintergrund oder Overlay, das nicht unter den Medien liegt.

### Sichere Bereiche fürs Handy

Instagram, TikTok und WhatsApp legen beim Abspielen im Hochformat Bedienelemente
über den oberen und unteren Rand. Als Faustregel, die viele Anleitungen zu
Metas Story-Vorgaben nennen: oben etwa 250 px und unten etwa 320 px einer
1080 × 1920-Fläche frei von wichtigem Text halten; das Videofenster darf
dorthin reichen. Das ist eine Empfehlung, keine Regel des Prüfers. Die
mitgelieferten 9:16-Rahmen halten ihre Texte oberhalb von y = 1600.

### Drei vollständige Beispiele

Titel unten, Farbverlauf, ohne Bilddatei:

```json
{
 "kind": "motion_frame",
 "id": "sommerfest-titel",
 "name": "Sommerfest Titel unten",
 "format": "9:16",
 "background": "#1b1b21",
 "background_to": "#2e2e38",
 "window": {"x": 60, "y": 120, "w": 960, "h": 1290, "radius": 36, "shadow": 24},
 "texts": [
  {"value": "{event_name}", "x": 540, "y": 1446, "size": 80, "bold": true, "align": "center", "color": "#ffffff", "w": 960},
  {"value": "{date}", "x": 540, "y": 1548, "size": 42, "bold": false, "align": "center", "color": "#c9c9d2"}
 ]
}
```

Eigenes Hintergrundbild und Overlay (beide vorher mit `add_asset` hochladen und
die zurückgegebenen Dateinamen eintragen):

```json
{
 "kind": "motion_frame",
 "id": "sommerfest-bild",
 "name": "Sommerfest mit Bild",
 "format": "9:16",
 "background": "hintergrund-sommerfest.png",
 "overlay": "overlay-sommerfest.png",
 "window": {"x": 90, "y": 260, "w": 900, "h": 1200, "radius": 0, "border": {"width": 8, "color": "#ffffff"}},
 "texts": [
  {"value": "{event_name}", "x": 540, "y": 1500, "size": 72, "bold": true, "align": "center", "color": "#ffffff", "font": "display"}
 ],
 "modes": ["boomerang"]
}
```

Querformat für Leinwand und Bildschirm:

```json
{
 "kind": "motion_frame",
 "id": "leinwand-titel-links",
 "name": "Leinwand Titel links",
 "format": "16:9",
 "background": "#1a1c23",
 "background_to": "#272b35",
 "window": {"x": 660, "y": 60, "w": 1200, "h": 960, "radius": 28, "shadow": 20},
 "shapes": [{"kind": "rect", "x": 80, "y": 520, "w": 120, "h": 6, "fill": "#d9b26a"}],
 "texts": [
  {"value": "{event_name}", "x": 80, "y": 380, "size": 72, "bold": true, "align": "left", "color": "#ffffff", "w": 540},
  {"value": "{date}", "x": 80, "y": 556, "size": 40, "bold": false, "align": "left", "color": "#c3c7cf"}
 ]
}
```

### Ablauf über MCP

1. Hintergrund hochladen: `add_asset` mit `kind: "background"`, `filename:
   "hintergrund-sommerfest.png"`, `content_base64`. Die Antwort nennt `file`
   (ein neuer Name aus der Prüfsumme) — **diesen** Namen als `background`
   eintragen. Ein Overlay genauso (`kind: "overlay"`, PNG).
2. `validate_template` mit dem Rahmen (`template: {...}`): gibt den bereinigten
   Rahmen und `hints` zurück, oder einen Fehler auf Deutsch.
3. `save_template` mit demselben Rahmen: Antwort `file` (`rahmen-<id>.json`).
4. Zuweisen an eine Box: `update_booth_profile` mit `changes: {"boomerang":
   {"frames": true, "frame_templates": ["rahmen-sommerfest-bild.json"],
   "frame_builtins": [], "frame_none": true, "frame_default": "",
   "frame_guest_choice": true}}`. Mitgelieferte Rahmen stehen mit ihrer
   Kennung in `frame_builtins` (`titel-unten`, `rahmen-hell`,
   `rahmen-dunkel`, `titel-links`); beide Listen leer = alle mitgelieferten.
5. Oder für ein Event (gewinnt über die Box): `configure_event` mit
   `frames: {"video": {"frames": ["titel-links"], "none": true}}`. Eine leere
   Liste gibt die Wahl an die Box zurück.
6. `list_templates` zeigt unter `rahmen` alle Rahmen, `get_template` liest
   einen (Dateiname oder Kennung).
