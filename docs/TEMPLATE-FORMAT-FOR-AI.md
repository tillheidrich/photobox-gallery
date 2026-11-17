# Building print templates — a guide for an AI

This one file is enough: an AI (ChatGPT, Claude or another model) without
access to the repository should be able to build valid, good-looking print
templates for the photo booth with it, aimed at the **Canon SELPHY CP1300**
(the CP1500 is the same). Everything here is derived from the code
(`app/vorlagen_kern.py`, `app/vorlagen_render.py`, byte-identical copies in
`booth/boothcore/media/`, and the printer profile in
`booth/boothcore/printing/models.py`). What the code does not tell is not
here — or is marked "not backed".

German original: [`VORLAGEN-FORMAT-FUER-KI.md`](VORLAGEN-FORMAT-FUER-KI.md).
The four examples below go through the real check and are rendered on every
test run (`tests/test_template_doc_examples.py`); if the format changes, the
test fails before this guide becomes wrong.

Field values and placeholders are German in the format itself (`@schrift`,
`nur_mit_qr`, `streifen` …); use them exactly as written.

<!-- schema: MAX_SLOTS=12 MAX_TEXTE=6 MIN_KANTE=80 MAX_FORMEN=20 MAX_BILDER=10 -->

## 1. What a template is and how it reaches the booth

A template is **one JSON file** that says where on the print sheet the
photos, texts, shapes, pictures and the QR code sit. Colours and type come
largely from the booth's **style** (section 6): the same template becomes a
wedding card or a company card.

Ways to the booth (booths fetch new templates on their next contact with the
gallery):

1. **Template editor** in the gallery: *Admin → Vorlagen → Editor*. It runs
   the check described below on saving and previews with the very renderer
   the booth prints with.
2. **MCP tool `save_template`** (for a connected AI assistant, right
   `manage`): creates or replaces (same `id` = replace), checked as in the
   editor. `validate_template` (right `read`) only checks, it saves nothing.
3. **Importing a template package** (*Admin → Vorlagen*, a ZIP from
   "Exportieren"): every template in it is checked.
4. **Uploading under "Medien & Vorlagen"** as kind "Collage-Vorlage (JSON)".
   Careful: this path only checks that `id` and `slots` exist — the file
   reaches the booth **unchecked**. For a template built by an AI use way 1
   or 2, or run `validate_template` first.

## 2. Paper sizes (field `paper`)

All at 300 dpi. `canvas` is `[width, height]` in pixels and **must** match
the paper: the check keeps a different `canvas` (only clamped to
400…4000 px), and the sheet then prints with the wrong aspect ratio.

| `paper` | `canvas` (px) | Size (mm) | Orientation | SELPHY CP1300 |
|---|---|---|---|---|
| `10x15` | 1200 × 1800 | 101.6 × 152.4 | portrait | yes |
| `10x15-quer` | 1800 × 1200 | 152.4 × 101.6 | landscape | yes |
| `streifen` | 1200 × 1800 | 101.6 × 152.4 | portrait | yes (two strips, drawn side by side by the template itself) |
| `streifen-2x6` | 600 × 1800 | 50.8 × 152.4 | portrait | yes (designed once, printed twice side by side on 10×15, cut by hand) |
| `quadrat` | 1200 × 1200 | 101.6 × 101.6 | — | no |
| `13x18` | 1535 × 2126 | 130 × 180 | portrait | no |
| `13x18-quer` | 2126 × 1535 | 180 × 130 | landscape | no |
| `15x20` | 1800 × 2400 | 152.4 × 203.2 | portrait | no |
| `15x20-quer` | 2400 × 1800 | 203.2 × 152.4 | landscape | no |
| `15x23` | 1800 × 2700 | 152.4 × 228.6 | portrait | no |
| `11x20` | 1350 × 2400 | 114.3 × 203.2 | portrait | no |
| `20x25` | 2400 × 3000 | 203.2 × 254 | portrait | no |

For the SELPHY CP1300 there are exactly **four** values: `10x15`,
`10x15-quer`, `streifen`, `streifen-2x6`. An unknown value becomes `10x15`
(with a hint).

## 3. The JSON, field by field

Missing fields are filled in by the check. For the result to be what was
meant, treat these as **required**: `id`, `name`, `paper`, `canvas`, `slots`.
What is there must be right: a wrong type, a value outside its range or a
reference that is not a plain file name **refuses the whole template** (see
section 5). Keys this format does not know are **dropped** and named in the
hints — at every door, so nothing reaches the renderer that it was not meant
to see.

### Head

| Field | Type | Rule | Default |
|---|---|---|---|
| `id` | text | lower-cased, `ä→ae ö→oe ü→ue ß→ss`, anything but `a-z 0-9 -` becomes `-`, at most 40 characters. Same `id` = replaces the existing template | from `name` |
| `name` | text | at most 60 characters | "Eigene Vorlage" |
| `paper` | text | see the table | `10x15` |
| `canvas` | `[w, h]` | whole pixels, 400…4000 | the paper's |
| `safe_area` | number | 0…15, percent per side; **only a guide line in the editor**, changes nothing in print | editor shows 3 |
| `order` | list of ids | drawing order bottom to top | photos, `overlay`, pictures, shapes, texts, QR |
| `overlay` | file name | PNG with alpha from the media, scaled to the full sheet; a plain file name (no path, no `..`, no URL) | none |
| `background` | colour or file name | the sheet's own colour: `#rrggbb` or a `@` token; or a picture from the media (plain file name). The guest's background chosen at the booth still wins | `"@hintergrund"` |
| `plain` | `true`/`false` | a plain sheet: its own `background` also in the margin of a fitted photo, no style frame (the built-in "Schlicht" family) | — |

The check also writes, by itself: `shots` (number of shots), `updated_at`, the
full `order`, and a unique `id` on every element.

### Photo slots `slots` (1 to 12)

| Field | Rule | Default |
|---|---|---|
| `x`, `y` | top left corner in px, pushed onto the sheet | 0 |
| `w`, `h` | at least 80 px, at most the sheet's width/height | half the sheet's width/height |
| `shot` | which shot (0 = first); numbers are renumbered from 0 without gaps | 0 |
| `radius` | corner radius in px (0 … half the shorter edge) or `"@rund"` = the style's radius | `"@rund"` |
| `rotation` | degrees clockwise about the middle, 0…360 | 0 |
| `fit` | `auto`, `beschneiden` (always crop to fill) or `einpassen` (always the whole photo); wins over the booth's setting for this slot | the booth's setting |
| `id`, `name`, `hidden`, `locked` | see "Every element" | |

Two slots with the same `shot` number show the same photo (that is how
`streifen` makes two equal strips). Without a slot the check adds one.

**How a photo goes into its slot:** if slot and photo have the same
orientation (both landscape or both portrait), it is cropped to fill, centred.
If the orientation differs, the whole photo is fitted and the rest stays in
the background colour — never distorted. That is `auto`, the booth's default;
a slot's own `fit` overrides it for that slot.

### Texts `texts` (up to 6)

| Field | Rule | Default |
|---|---|---|
| `value` | 1…120 characters; empty lines are dropped. Placeholders below | — |
| `x` | horizontal anchor (the middle for `center`, the right end for `right`) | 60 |
| `y` | **top edge** of the line, 0 … height−20 | height−200 |
| `size` | font size in px, 12…200 | 48 |
| `bold` | `true`/`false` | `true` |
| `align` | `left`, `center`, `right` | `left` |
| `color` | `#rrggbb` or `@schrift`, `@gedaempft`, `@akzent`, `@hintergrund`; anything else becomes `@schrift` | `@schrift` |
| `font` | `system`, `display`, `serif` or the file name of an uploaded `.ttf`/`.otf`; otherwise the style's font | style |
| `amp_font` | a font only for the `&` (a script face otherwise turns `&` into an "Et") | — |
| `w`, `h` | optional text field in px; without it the field reaches the safe margin or the next element below | — |
| `line_height` | 0.8…3.0 (factor of the font size) | — |
| `opacity` | 0…100 | 100 |
| `nur_mit_qr` | `true` = print only when a QR code is printed | — |

Placeholders in `value`: `{event_name}` (the event's name), `{date}` (date of
the session, `DD.MM.YYYY`), `{gast}` (text the guest typed), `{headline}` and
`{subline}` (the style's two lines). An unknown placeholder prints empty.
Line break with `\n`.

**What happens when a line is too long:** it is first set smaller (down to
60 % of its size), then in two lines if the field is tall enough, then
smaller again; below 40 % the check warns "kaum lesbar" (hardly readable). A
line whose anchor lies outside the safe margin is pulled onto it. A QR code
beside a line ends that line's field before it.

Fonts: `system` = Inter (SemiBold/Regular), `display` = Archivo
(Bold/SemiBold) — both travel with the booth. `serif` takes Georgia from the
system (present on Windows), otherwise DejaVu Serif; it is **not** bundled.
Own fonts are uploaded by the operator under "Medien & Vorlagen" as kind
"Schriftart"; the template then names the file.

### QR code `qr` (one, optional)

| Field | Rule | Default |
|---|---|---|
| `size` | edge length in px, 80 … half the shorter sheet edge | 200 |
| `x`, `y` | top left corner | 60 px from the right and bottom edge |
| `rotation`, `hidden`, `locked`, `nur_mit_qr` | as for every element | |

The code is black on white, error correction M, with a quiet zone of
**2 modules** already inside the picture. It is printed only when the booth
has a link for it (paired, QR on print switched on); otherwise the place
stays empty and layers with `nur_mit_qr` are left out too. The code names no
minimum size for reliable scanning, and the bundled library carries no code.
The examples below use 220–260 px (≈ 19–22 mm); the editor adds a new code at
200 px.

### Shapes `shapes` (up to 20)

`{"kind": "rect" | "ellipse" | "line", "x", "y", "w", "h", "fill",
"stroke", "stroke_width" (0…200), "opacity" (0…100), "radius" (rect only)}`.
`fill`/`stroke`: `#rrggbb`, an `@` value or `""` for none. `w`/`h` at
least 2.

### Pictures `images` (up to 10)

`{"file": "<file name>", "x", "y", "w", "h", "opacity"}`. `file` is a
**file name among the gallery's media** (`.png`, `.jpg`, `.jpeg`, `.webp`;
only `A-Z a-z 0-9 . _ -`, no path, no URL), otherwise the picture is dropped.
The picture is fitted into its box.

### Every element

`id` (unique, `a-z 0-9 -`, up to 24 characters; `qr` and `overlay` are
taken), `name` (up to 40 characters, display only), `hidden: true` (not
printed), `locked: true` (locked in the editor), `rotation` (degrees).

## 4. What matters on the SELPHY CP1300

* **Paper:** postcard KP-108IN, 100 × 148 mm. The booth renders a
  1200 × 1800 px sheet for it (4 × 6 inch at 300 dpi) and sends it as is.
* **Borderless, cropped by the printer:** the printer profile is borderless
  with no bleed of its own. Canon's driver scales the 1200 × 1800 px to its
  1248 × 1872 px (figure from Gutenprint); what runs past the paper falls off.
  How much exactly no data sheet says reliably — the booth has a calibration
  sheet for that.
* **Safe margin:** the code works with **4 mm = 47 px** per side
  (`sicher_mm`). Text fields end there, and a line placed too far out is
  pulled back to it. The editor shows 3 % per side as a guide (36 px on the
  short edge, 54 px on the long one). Rule for templates: **text, QR code and
  anything that must be seen whole at least 47 px from the edge.** Photos and
  areas may run to the edge — they simply get cropped.
* **Landscape:** a `10x15-quer` template is rendered landscape (1800 × 1200);
  the sheet is turned for printing, not the picture. Just design landscape.
* **Strips:** `streifen-2x6` is designed once (600 × 1800) and printed twice
  side by side on a 10×15 sheet; with the SELPHY it is cut **by hand** in the
  middle (the printer does not cut). Keep nothing important on that cut: the
  strip's edge is the middle of the sheet.
* **Dye sublimation:** it softens edges (the booth therefore sharpens lightly
  before printing) and tends to lose the lightest and darkest tones: 2 % and
  5 % grey both become white, detail in very dark areas disappears. So no
  information in near-white or near-black steps. The code names no minimum
  line width.
* **Perforation:** the code says nothing about tear-off edges of the postcard
  paper.

## 5. Limits and typical messages of the check

Since 17.11.2026 every way in — upload under "Medien & Vorlagen", the
editor (gallery and booth), MCP `save_template`/`validate_template`/`add_asset`,
a package, the library, a customer's layout, a template a booth sends, a
file read back after a restore, a file synced to or placed in the booth's
media folder — passes the same check (`vorlagen_kern.annehmen`). It works in
two steps:

1. **Refuse** what is not a template of this format, with every field named
   ("Keine gültige Vorlage — slots[0].x liegt außerhalb von 0 bis 1200; …").
   At the booth such a file is skipped with the reason in the log and is not
   offered to guests.
2. **Straighten** the rest and say so (hints in `hints` over MCP, shown in the
   editor): an element that reaches over the edge is moved back onto the
   paper, shot numbers are renumbered, unknown keys are dropped.

A template is good when `validate_template` returns **no hints** and the
values it returns are your own.

Limits: file at most 256 KB; 12 photo slots, 6 texts, 20 shapes, 10
pictures, one QR code, 200 entries in `order`; numbers are JSON numbers;
positions `x`, `y` on the paper (0 … width/height); slot edge 80 px … the
paper, shapes 2 px …, pictures 10 px …; QR 80 px … half the shorter edge;
font size 12…200 px; text up to 120 characters (a line break allowed, no
other control characters), `name` up to 60, element `id`/`name` up to 40;
sheet 400…4000 px per edge; colours `#rrggbb` or the four `@` tokens; fonts
`system`, `display`, `serif` or a plain `.ttf`/`.otf` file name; pictures,
overlay and a picture background plain file names (`A-Z a-z 0-9 . _ -`, no
path, no `..`, no URL).

| Message / effect | Cause | Fix |
|---|---|---|
| "Unbekanntes Blattformat — 10×15 hoch genommen." | `paper` not in the table | use a value from section 2 |
| "Ohne Bildfeld gibt es nichts zu zeigen — eines wurde angelegt." | `slots` empty or missing | at least one slot |
| "Die Nummern der Aufnahmen wurden lückenlos durchgezählt." | `shot` numbers with a gap (e.g. 0, 2) | number from 0 without gaps |
| "Ein Bild ohne gültige Datei aus den Medien wurde weggelassen." | `images[].file` is a path, URL or wrong extension | only a file name from the media |
| "Text passt nicht in sein Feld: … wird in N px statt M px gedruckt." | line too wide with the sample name | smaller type, wider field, or shorter text |
| "… steht außerhalb des sicheren Bereichs und wird an dessen Rand gesetzt." | text anchor closer than 47 px to the edge | move the anchor inwards |
| "Keine gültige Vorlage — slots[0].x liegt außerhalb von 0 bis 1200" | a position off the paper, a size larger than the paper or below its minimum | keep to the limits |
| "… ist keine Zahl" / "… ist kein Text" / "… muss true oder false sein" | a number as a string, a list where an object belongs | JSON types as in the examples |
| "… ist keine erlaubte Farbe (…)" | colour not `#rrggbb` and not a `@` token | `#rrggbb` or a token |
| "… muss ein Dateiname aus den Medien sein (…)" | font, picture or overlay with a path, `..` or a URL | only a file name from the media |
| "… ist kein bekanntes Blattformat (…)" / "… muss „auto“ oder … sein" | `paper`, `fit`, `align`, shape `kind` not one of the values | a value from the tables |
| "Unbekannte Felder weggelassen: …" (hint) | keys this format does not know | leave them out |
| moved onto the paper (hint-free) | an element that starts on the paper but reaches over its edge | keep it inside |
| MCP: "template muss ein Objekt sein …" | not a JSON object | build it like the examples |

The check measures lines with the sample name "Mia & Tom" (editor and
`validate_template`). A longer real name can still be too wide — the renderer
then sets it smaller or in two lines when printing.

## 6. Style, colours and background

A template best names colours as **placeholders**; at print time the booth's
style fills them in:

| Placeholder | Style value |
|---|---|
| `@hintergrund` | `background` (sheet colour) |
| `@schrift` | `ink` (text colour) |
| `@gedaempft` | `muted` (secondary lines) |
| `@akzent` | `accent` (lines, frames) |

`"radius": "@rund"` on a slot takes the style's corner radius. The style can
also draw a frame line in the accent colour around the sheet (`frame`), drawn
on the `overlay` layer.

Shipped styles (presets): `clean` (dark, background `#0f0d09`, text
`#f5f3f0`, accent `#e7ad59`, radius 14), `hell` (`#faf8f5` / `#1a1714` /
`#b07d2b`, radius 14), `geburtstag` (`#151033` / `#fdf7ff` / `#ffbf3c`,
radius 22, font display, headline "Happy Birthday"), `hochzeit` (`#f7f3ec` /
`#2a2620` / `#b39258`, radius 0, frame 10, font serif), `feier` (`#110d1a` /
`#fbf6ff` / `#ff5f9e`, radius 24), `firma` (`#ffffff` / `#101418` /
`#1d5fd0`, radius 6).

**Own background colour:** `background` takes `#rrggbb`, a `@` token or a
picture from the media; without it the sheet is `@hintergrund`. A rect over
the whole sheet (`x 0, y 0, w/h = canvas`, its `id` first in `order`) does the
same and also shows in the editor. A background the guest or the operator
picks at the booth ("Collage-Hintergrund") wins over the template's.

**Overlay / frame picture:** a PNG with transparency, uploaded as a medium
(kind "Overlay"), named in the template as `overlay: "<file name>"`. It is
scaled to the full sheet — so make it in the sheet's aspect ratio, best at
exactly its pixels (1200 × 1800 or 1800 × 1200). If it leaves a window for the
QR code and no code is printed, the renderer closes the window with the
surrounding colour.

## 7. Design on 10 × 15

* **Hierarchy:** one large line, one small. The bundled library (135
  templates) never sets the date below **30 px** on 10×15 (30–51 px) and the
  event name mostly two to three times as large (up to 138 px). 30 px is a
  2.5 mm font size on the print.
* **Margins:** the library places the photos on 10×15 with a **96 px** outer
  margin, well above the 47 px safe margin.
* **Slot aspect ratio:** the camera usually delivers 3:2 (the editor's sample
  pictures are 3:2 landscape). A 3:2 slot shows the whole photo; any other
  ratio in the same orientation crops in the middle (a 4:3 slot takes about
  11 % of the width, a square one a third). A portrait slot for a landscape
  photo shows the **whole** photo with bars in the background colour. With
  the camera mounted upright everything is the other way round (2:3). The
  formats 4:5, 9:16 and 3:2 a guest picks at the booth apply to single
  photos, not to the slots of a template.
* **No text on photos**, unless on purpose; never on the QR code.
* **Colours through placeholders**, so the template works with every style.

## 8. Four complete examples

All four are for the SELPHY CP1300, pass the check without a hint and are
rendered in the test run. Texts are neutral placeholders.

**One photo landscape with a caption line** (`10x15-quer`, 3:2 slot):

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

**Two photos landscape with QR code** (`10x15-quer`, two 3:2 slots):

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

**Photo strip with three photos** (`streifen-2x6`, twice on 10×15; slots
504 × 400, the 3:2 photo loses about 8 % on the left and on the right):

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

**Four photos portrait in a grid** (`10x15`, slots 510 × 400, with QR code):

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

## 9. Testing without a printer

* **MCP `validate_template`** with the template: returns the cleaned template
  and `hints`. Aim: `hints` empty, values unchanged.
* **Editor preview** (*Admin → Vorlagen → Editor*): rendered with exactly the
  booth's renderer, with numbered sample photos ("Beispielbild · sample") and
  the sample name.
* **Test print** from the editor to a paired booth sends the sheet with the
  mark "Testdruck" to its printer — that one does need a printer.

## 10. Text to paste for another AI

> You are building a print template (JSON) for a photo booth with a Canon
> SELPHY CP1300. Follow only the attached file TEMPLATE-FORMAT-FOR-AI.md.
> Invent no fields. Return exactly one JSON object in a ```json block, then
> two sentences on what the template shows. My wish: [occasion, number of
> photos, portrait/landscape/strip, QR yes/no, mood].

## 11. Checklist before the AI answers

1. `paper` is `10x15`, `10x15-quer`, `streifen` or `streifen-2x6`, and
   `canvas` is exactly the table's.
2. `id` only `a-z 0-9 -`, at most 40 characters; `name` at most 60.
3. 1–12 slots, every edge ≥ 80 px, all on the sheet; `shot` from 0 without
   gaps.
4. Slots in the camera's orientation (usually landscape, 3:2), otherwise
   bars.
5. At most 6 texts, `size` 12…200, `value` ≤ 120 characters, only known
   placeholders; text anchors and QR code ≥ 47 px from the edge.
6. The longest line fits its field with a long name (measured on the bundled
   fonts Inter and Archivo: width ≈ 0.55 × size × number of characters).
7. Colours only `#rrggbb` or `@hintergrund/@schrift/@gedaempft/@akzent`.
8. `font` only `system`, `display`, `serif` or a named uploaded file name;
   `images[].file`/`overlay` only file names that exist.
9. QR code (if wanted) 220–260 px, nothing on top of it, its caption with
   `nur_mit_qr: true`.
10. Only fields from section 3 (unknown ones are dropped and named in the
    hints), plain file names without a path, numbers as numbers — otherwise
    the check refuses the whole template.

## 12. Frames for boomerang and video

A **frame** ("Rahmen") is not a print template but a small JSON file of its
own (`"kind": "motion_frame"`). The boomerang or film plays in **one
window** on a canvas in a phone format; around it are the background and
the event's title and date. It is checked by `app/motion_frames.py` (the
same file runs on the booth) and stored as a media file of the kind
`motion` (`rahmen-<id>.json`). Frames are switched per booth and mode
(`boomerang.frames`, `video.frames`, off by default) and never appear in
the lists of print templates. A frame has no QR code: the films carry
none; the link is on the result screen and on the print.

### Canvases (field `format`)

| `format` | Canvas in pixels | For |
|---|---|---|
| `9:16` | 1080 × 1920 | Story, Reels, WhatsApp status |
| `16:9` | 1920 × 1080 | screens, projectors, YouTube |
| `4:5` | 1080 × 1350 | Instagram feed (a boomerang can have this format) |

The film has exactly this size (with `max_width` 1080 the short edge stays
1080; a fixed resolution `720p`/`1080p`/`1440p` in the booth profile sets the
short edge). The frame's format is the film's format.

### Fields

| Field | Required | Content |
|---|---|---|
| `kind` | yes | always `"motion_frame"` |
| `name` | no | display name, at most 60 characters |
| `id` | no | `a-z 0-9 -`, else made from the name; gives the file name `rahmen-<id>.json` (same `id` = replace) |
| `format` | yes | `9:16`, `16:9` or `4:5` |
| `canvas` | no | set from `format`; a different value gives a hint |
| `background` | no | `#rrggbb` or the file name of an uploaded picture (PNG/JPG/WebP); fills the canvas (cropped, never stretched). Missing: `#111111` with a hint |
| `background_to` | no | second colour `#rrggbb`: vertical gradient from `background` (top) to `background_to` (bottom); colour backgrounds only |
| `overlay` | no | PNG with transparency, above the film, stretched to the canvas |
| `window` | yes | the film's window: `x`, `y`, `w`, `h` in canvas pixels, at least 120 × 120, entirely on the canvas (one that sticks out is moved in, with a hint); `radius` corner rounding (0 … half the shorter side); `shadow` 0–60 px; `border` `{width: 0–40, color: "#rrggbb"}` a line around the window |
| `texts` | no | up to 6 lines, **exactly as for print templates** (section 3): `value` with `{event_name}`, `{date}`, `{headline}`, `{subline}`; `x`, `y`, `size` 12–200, `bold`, `align`, `color`, `font` (`system`, `display`, `serif` or an uploaded TTF/OTF), optional `w`, `h`, `line_height`, `opacity`, `rotation` |
| `shapes` | no | shapes as for print templates; they lie **under** the film (on the background) |
| `modes` | no | `["boomerang", "video"]` (default both) or just one |

Layers bottom to top: background (with shapes and the window's shadow) →
the film in the window (scaled and cropped until it fills the window; the
window's aspect is free) → overlay → window line → texts. The booth's live
view shows the window's crop during the countdown.

**Hard errors** (saving is refused, message in German): wrong `kind`,
unknown `format`, no `window` or no numbers in it, a window smaller than
120 × 120 or larger than the canvas, a file name with a path, an overlay
that is not a PNG, and (when saving in the gallery and through MCP) a
background or overlay that is not among the media.

### Safe areas for phones

Instagram, TikTok and WhatsApp lay their controls over the top and bottom
of a portrait film. As a rule of thumb that many guides to Meta's Story
specifications give: keep about 250 px at the top and about 320 px at the
bottom of a 1080 × 1920 canvas free of important text; the window may reach
there. This is advice, not a rule of the check. The built-in 9:16 frames
keep their text above y = 1600.

### Three complete examples

Title at the bottom, colour gradient, no picture file:

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

Own background picture and overlay (upload both with `add_asset` first and
put in the file names it returns):

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

Landscape for screens and projectors:

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

### The MCP sequence

1. Upload the background: `add_asset` with `kind: "background"`, `filename:
   "hintergrund-sommerfest.png"`, `content_base64`. The answer names `file`
   (a new name made from the checksum) — put **that** name into
   `background`. An overlay the same way (`kind: "overlay"`, PNG).
2. `validate_template` with the frame (`template: {...}`): returns the
   cleaned frame and `hints`, or an error in German.
3. `save_template` with the same frame: the answer's `file` is
   `rahmen-<id>.json`.
4. Assign it to a booth: `update_booth_profile` with `changes: {"boomerang":
   {"frames": true, "frame_templates": ["rahmen-sommerfest-bild.json"],
   "frame_builtins": [], "frame_none": true, "frame_default": "",
   "frame_guest_choice": true}}`. Built-in frames go into `frame_builtins` by
   id (`titel-unten`, `rahmen-hell`, `rahmen-dunkel`, `titel-links`); both
   lists empty = every built-in.
5. Or for an event (wins over the booth): `configure_event` with
   `frames: {"video": {"frames": ["titel-links"], "none": true}}`. An empty
   list gives the choice back to the booth.
6. `list_templates` shows every frame under `rahmen`, `get_template` reads
   one (file name or id).
