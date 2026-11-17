# Checking the admin on a phone

The operator sets the booth up from his phone, at the venue, minutes before
the first guest. Every admin page has to work at 360 px wide with one thumb.
Two tools check that in a real browser; `tests/test_admin_on_a_phone.py`
checks the parts that need no browser.

## Run it

```bash
# 1. A throwaway gallery with data that hurts a phone layout (long names and
#    slugs, an event with 300 photos, eight boxes, one red and one yellow).
#    It deletes the folder it is given, so give it one under /tmp.
python tools/admin_phone_seed.py 5763 /tmp/admin-phone-data &

# 2. Every admin page at 360, 390 and 430 px, light and dark.
node tools/admin_phone_check.mjs /tmp/admin-phone-data/pages.json --out /tmp/admin-phone-shots

# 3. Stop the gallery from step 1 (kill its PID).
```

Needs Node 22 or newer (built-in `fetch` and `WebSocket`, no npm packages) and
Google Chrome or Chromium (`CHROME=/path/to/chrome` if it is not found).

Options:

| Option | What it does |
|---|---|
| `--only events,templates` | only these pages (names from `pages.json`) |
| `--widths 360` | only these widths |
| `--themes dark` | only this theme |
| `--no-shots` | measure only, no screenshots |
| `--expand` | open every folded section and `<details>` before measuring |
| `--eval "js"` | run JavaScript after load, e.g. type into the settings search |
| `--desktop` | 1440 × 900 instead of the phone widths |

It writes `gates.json` and `<page>-<width>-<theme>.png` into `--out`, prints
one line per page and exits with 1 when a hard gate fails at the narrowest
width.

## What it measures

Hard gates (the run fails):

* `document.documentElement.scrollWidth` larger than the width: the page
  scrolls sideways.
* An element whose box crosses the right edge and is not inside a container
  that scrolls on its own: something is cut off.

Reported, to be fixed page by page:

* Touch targets smaller than 44 px in both directions or 24 px in the other,
  and targets closer than 8 px to each other. Links inside a sentence are
  counted separately (`inlineLinksSmall`), as WCAG allows them to be small.
* Text under 11.5 px (`tinyText`), and text under 14 px that is not a label,
  badge, hint or code (`smallText`).
* Fields under 16 px (`zoomInputs`): Safari zooms in when they get focus.
* Siblings laid out as blocks whose boxes overlap by more than 2 px.

Miniature previews (template thumbnails, the editor sheet) carry
`data-check-skip`: their lettering is a picture, not page text.

## How the phone layout is built

* `app/static/css/admin.css`, the blocks after "Phone pass": everything that
  changes on a phone sits in `@media (max-width: 768px)`; the desk layout
  above it is unchanged.
* `app/static/js/admin-phone.js`: the section chip row, folding sections
  with a one-line summary (state kept in `localStorage`), the settings
  search (`data-section-search`) and the unsaved-changes note in the save
  bar (`form[data-dirty-watch]`). It reads data attributes only and never
  changes a field name or what a form sends.
* Tables: `class="table--stack"` plus `data-label` on each cell turns a row
  into a labelled card on a phone; `table--nums` keeps numbers three to a
  line.
* Actions that are icons on a desk: `adm-desk-only` hides them on a phone,
  `adm-phone-only` shows the same actions with words (a `<details>` "Mehr"
  menu), `adm-phone-label` adds a visible word to an icon button.
* The template editor (`vorlagen-editor.js`) is shared byte for byte with
  the booth and is not changed; `vorlagen-editor-seite.js` moves its side
  panel into a bottom panel and marks the toolbar buttons that stay.

## Guest pages

The same check runs over what a guest sees: the gallery (masonry and list),
the password page, the run page `/p/<code>` (ready and still waiting),
`/meine`, the FAQ, the 404 page, the client login, a share link and the
layout invitation.

```bash
python tools/guest_phone_seed.py 5764 /tmp/guest-phone-data &
node tools/admin_phone_check.mjs /tmp/guest-phone-data/pages.json --guest --first-screen \
     --sizes 360x740,390x844,430x932,844x390 --out /tmp/guest-shots
```

The seed's cookie is a guest's (no admin), with three scanned runs so
`/meine` and the nav link have something to show. Extra options:

| Option | What it does |
|---|---|
| `--sizes WxH,…` | explicit viewports; a height under 500 px is a phone held sideways (touch, mobile) |
| `--guest` | guest gates, see below, plus the first-screen numbers |
| `--first-screen` | the screenshot is the first screen only, named `<page>-<w>x<h>-<theme>.png` |

With `--guest` the run fails, at every size, when the page scrolls
sideways, a touch target is under 44 px in either direction (links inside a
sentence excepted), an input is under 16 px, or a gallery shows no photo in
its first screen at the narrowest size. Each page line also prints the band
the sticky bars take at the top once scrolled (`sticky 56px/8%`), where the
first photo starts (`photo1 yes@412`) and how many videos load or play on
their own (`videos-loading`).

## Selecting by touch

`--select-touch` drives the gallery with real touch input over the DevTools
protocol (`Input.dispatchTouchEvent`: touch start, move, end - not mouse
clicks), with touch emulation on, as a guest ("Meine Auswahl" in masonry,
the square grid in proofing mode with a colour flag on every tile, and the
list) and as a logged-in client (the pick dots), light and dark.

```bash
python tools/guest_phone_seed.py 5764 /tmp/guest-phone-data &
node tools/admin_phone_check.mjs /tmp/guest-phone-data/pages.json --select-touch \
     --sizes 390x844,360x740,844x390 --out /tmp/select-shots
```

Default sizes are 390×844 and 360×740; `--only gallery,gallery-grid` limits
the guest pages, `--themes` the themes. It writes `select-touch.json` (every
step, see below) and one screenshot per run, `select-<guest|client>-<page>-<size>-<theme>.png`
(the guest's after ten tiles were chosen, the client's after ten dots).
The page counts large-view openings, context menus and every change of a
tile's selected state; a tile that changes twice within 700 ms is a double
toggle (on, then off by a ghost click).

The gate (exit code 1 if any fails, at every size and theme):

| Step | Pass |
|---|---|
| Enter selection mode by touch, tap a tile at once | 1 selected |
| 10 taps on the middle of 10 tiles | 10 selected, 0 large views, 0 scroll jumps, 0 double toggles |
| 5 taps on the check mark (24 px from the top left corner) | 5 selected |
| 4 taps 4 px inside the left, right, top and bottom edge | 4 selected |
| 2 taps on the bottom right corner (where the tile's buttons sit) | 2 selected |
| 2 taps on two tiles 90 ms apart | 2 selected |
| 2 taps with the finger moving 6 and 10 px | 2 selected |
| Drag over the tiles to scroll, down and back | 0 toggles, the page moved |
| What a finger hits on a tile (9×9 points) | 100 % the tile, no button |
| Long press (900 ms) | no context menu, no text selected |
| Scrolled to the end | bar in view, no tile of the last row under it |
| Leave selection mode | 0 selected |
| Client: 10 pick dots, 90 ms apart, 400 ms network latency | ≥ 9 shown before the server answered, 10 picked, 0 large views, 1 selection holding 10 on the server |
| Client: the same dot twice, quickly | not picked, on the tile and on the server |
| Client: pick dot | 44 × 44 px |

Reported, not gated: a tap right after a fast flick (`flickTap`; headless
Chrome makes no momentum, a real phone stops the glide and fires no click),
"Alle" under a filter (`filterAll`: how many are shown, chosen, and still
chosen after the filter is lifted), whether the selection ZIP answers
(`zip`), and the name and state of the chosen tile's check box (`marked`).

What emulation cannot show, and how the code handles it on an iPhone:
the long-press image sheet ("Bild sichern", preview) - `-webkit-touch-callout:
none` on tiles in selection mode; `:hover` staying on the last tapped tile -
the hover zoom only under `@media (hover: hover)`; double-tap zoom and the
300 ms wait - `touch-action: manipulation` (and the viewport is
`width=device-width`); the grey tap flash - `-webkit-tap-highlight-color:
transparent`; a click on a plain `div` - the tile has a click listener of its
own and `cursor: pointer`. Check these on a real phone.

## Kiosk with the arrow keys

`node tools/admin_phone_check.mjs --kiosk-keys http://127.0.0.1:<port> --out /tmp/kiosk-keys [--sizes 1707x960]`
drives a running booth's kiosk (start it with `BOOTH_CAMERA=mock`) with key events
only: the first arrow shows the focus on the start screen, the arrows reach the
boomerang tile, Enter opens it, ArrowUp/ArrowLeft change the format (gate: the live
preview's box changes), ArrowDown/ArrowRight change the layout, Enter on the start
button records, on the result the arrows reach "Drucken" (not pressed) and Enter on
"Fertig" goes home; then PageDown and F13 still start the operator's mode while an
arrow focus stands on another control, the booth gallery's tile, big view and next
picture are reached, and Backspace goes back. A booth with
`controls.arrow_navigation` off is checked with `--expect-arrows off` (ArrowRight
must start, as before). Writes `kiosk-keys.json` and screenshots with the focus
ring; exit 1 if a gate fails. Needs a profile with `boomerang` among the modes
and more than one format and layout offered for the boomerang.
