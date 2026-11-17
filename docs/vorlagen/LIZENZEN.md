# Vorlagen-Bibliothek — Lizenzen

The template collection in `app/vorlagen_bibliothek/` (templates, artwork,
previews, `katalog.json`) and its generator in `tools/kollektion/` are part
of this project and are licensed like the rest of it: AGPL-3.0-or-later
(`LICENSE`, with the section 7 permission in `LICENSE-EXCEPTION`).

## Artwork

Every picture in `app/vorlagen_bibliothek/medien/kol-*.png` is drawn by
`tools/kollektion/kunst.py` from polygons, Bézier curves and strokes
(sprigs, line flowers and sprigs, dried grasses, arches, suns, stripes, film
perforations, sparkles, balloons, confetti, snowflakes, fir twigs, washes).
No clip art, stock illustration, traced artwork, photograph or third-party
template was used; no AI image generator was involved. Run

    python -m tools.kollektion.bauen --neu

to redraw them all from the code. The contact sheets in this folder are
renders of the templates with the project's own sample pictures
(`vorlagen_render.beispielbilder`), not photos of people.

## Fonts

Shipped as template media, unmodified, each under the SIL Open Font License
1.1 (licence texts in `docs/lizenzen/`, overview in `THIRD_PARTY.md`):

| File | Family | Licence text | Source |
|---|---|---|---|
| `PinyonScript-Regular.ttf` | Pinyon Script | `docs/lizenzen/OFL-PinyonScript.txt` | github.com/google/fonts `ofl/pinyonscript` |
| `GreatVibes-Regular.ttf` | Great Vibes | `docs/lizenzen/OFL-GreatVibes.txt` | github.com/google/fonts `ofl/greatvibes` |
| `CormorantSC-Medium.ttf`, `CormorantSC-SemiBold.ttf` | Cormorant SC | `docs/lizenzen/OFL-Cormorant.txt` | github.com/google/fonts `ofl/cormorantsc` |
| `DMSerifDisplay-Regular.ttf`, `DMSerifDisplay-Italic.ttf` | DM Serif Display | `docs/lizenzen/OFL-DMSerifDisplay.txt` | github.com/google/fonts `ofl/dmserifdisplay` |
| `CourierPrime-Regular.ttf` | Courier Prime | `docs/lizenzen/OFL-CourierPrime.txt` | github.com/google/fonts `ofl/courierprime` |

Inter and Archivo, which some templates name as `system` and `display`, ship
with the program itself (see `THIRD_PARTY.md`).

The fonts travel inside exported `.abzug` packages and are copied to a
booth with the templates that use them; the OFL allows that as long as the
font files are not sold on their own and keep their names. Swapping a font
is one line in `tools/kollektion/bau.py` (`SCHRIFT_*`, `ET_SERIF`).
