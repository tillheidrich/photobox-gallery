# Design

Three surfaces, one system: the booth a guest touches, the gallery a guest opens
on their phone, the admin an operator works in. They share a typeface and a way
of thinking, not a stylesheet.

This file exists so the next person, human or agent, changes things on purpose
rather than by accident.

## Typeface

Three families, each with one job, all shipped with the software.

* **Inter** — the working face. Body text, controls, tables, everything a
  person reads rather than glances at. It was drawn for screens at small
  sizes: open counters, a generous x-height, figures that do not wobble in
  a column.
* **Archivo** — the display face. Wordmark, kiosk headlines, the product
  page. Deliberately broader and firmer than Inter; from two metres it
  reads like signage, not like text.
* **JetBrains Mono** — keys, paths, logs. Admin and sync only. It is never
  loaded on a guest-facing page.

The booth ships Inter and Archivo; it has nothing to show in monospace.

All three are self-hosted, **never fetched**. The booth frequently has no
internet, and in the data-minimal mode it must not call a third-party
address at all. A font that phones Google would quietly undo that promise.
The files sit in `app/static/fonts/` and `booth/boothcore/web/static/fonts/`
as variable `woff2`; `app/static/fonts/ttf/` holds the static cuts the print
layout needs, because the renderer cannot read a variable axis.

An operator may upload their own typeface per booth; that choice wins over
this one. The stack always ends in a system font, so a missing file leaves
a readable booth rather than an unreadable one.

Changing the typeface means changing four places: the `@font-face` blocks in
`app/static/css/brand/photobox-fonts.css` and the booth's copy, the
`--pb-font` / `--pb-font-display` / `--pb-font-mono` tokens in
`photobox-brand.css`, the font files themselves, and the print layout's
static cuts.

> **Open for Till.** Until commit `37ec381` this file said Schibsted
> Grotesk, and it said so for months after the code had moved to
> Inter/Archivo. Inter is competent and invisible; it is also what a great
> many web applications use, so it buys no distinctiveness. Archivo already
> carries the character at display sizes. Whether the body face stays Inter
> or moves to something rarer is a decision, not a defect — the machinery
> above makes the swap a one-afternoon job.

> **Done (11.11.2026).** Inter, Archivo and JetBrains Mono are under the SIL
> Open Font License 1.1, which requires the copyright notice and the licence
> text to travel with the font files. The texts are in `docs/lizenzen/`, every
> font folder has a `LICENSES.txt` pointing at them, `THIRD_PARTY.md` lists
> the fonts, and the gallery image and the booth builds carry all of it.

## Colour

The booth's palette lives in the profile, not in the stylesheet: six colours the
operator sets per event, turned into CSS variables at runtime. The defaults are
dark because of where the booth stands. Write the scene before changing them:
*a guest at a wedding reception, in a room dimmed for dancing, looking at a
bright screen at arm's length.* A light default would glare; a light theme is a
choice an operator makes for a daytime kindergarten, and the booth supports it
properly (veils and panels derive from the background colour).

Rules that hold regardless of the operator's choices:

* No pure black, no pure white as a surface. Neutrals carry a trace of the
  accent hue.
* One accent. It marks the primary action and nothing else.
* Second surfaces are mixed from the panel colour toward the text colour, in
  the booth and in the admin preview with the same arithmetic, so what the
  operator sees is what the guest gets.

## Type detail

* Figures are tabular where numbers change or stack: countdowns, shot counters,
  the status line, tables. In running text they are proportional.
* Headlines balance across lines rather than leaving one word stranded.
* Body text stops at roughly 65 to 75 characters.
* Hierarchy comes from weight and colour before size.

## What we do not do

* No glassmorphism as decoration. The booth uses one blurred layer, over the
  live camera image, where it has a job.
* No gradient text, no neon glow, no coloured side stripes on cards.
* No emoji in the interface.
* No card for every piece of content. Cards are for things that lift off the
  surface.
* No animation of layout properties. Transform and opacity only, ease-out.

## Where the files are

| Surface | Stylesheet | Notes |
|---|---|---|
| Booth | `booth/boothcore/web/static/app.css` | Variables set at runtime by `applyTheme()` in `app.js` |
| Gallery | `app/static/css/design.css`, `public.css` | `design.css` holds the tokens |
| Admin | same as gallery | The booth profile form has its own scoped block |

Changing the typeface means changing three places: the two `@font-face` blocks,
the `--font` token in the gallery, and `--font-stack` plus the `FONTS` table in
the booth. The test suite does not catch a mismatch there, so do it in one go.
