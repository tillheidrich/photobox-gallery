# Changelog

One version number for the gallery, the booth and the sync program
([`VERSION`](VERSION)). The development history itself stays in the author's
private repository; this file says what changed for the people who run it.

## Unreleased

Nothing yet.

## 0.9.1 — 2026-11-17

The evening build after 0.9.0. `VERSION`, the booth, the sync program and the
MCP server say 0.9.1. Several items need a new booth build (marked). Known
limits: see the [README](README.md#known-limits).

### Booth

* Fix (Canon EDSDK): "no focus" (0x8D01) no longer fails a guest's run. In a
  dim, static scene the EOS M50 answered the press without autofocus itself
  with 0x8D01; that was treated as a link error: the shot failed with
  `camera.focus`, the camera was reconnected and the operator saw "Die Kamera
  antwortet nicht" (3 of 117 runs in a 30-minute endurance run). Now the booth
  lets the button up and fires again without autofocus, up to three more
  times, one INFO line and one `focus_fallbacks` count each. A shot the camera
  refuses for good (no focus every time, card full, lens retracted) fails
  without a reconnect, and the kiosk takes a focus-refused shot once more
  instead of ending the run on the error screen. "No focus" stays a quiet
  retry.
* Fix (Canon): a camera that refused a shot for "card" or "movie mode" (on the
  EOS M50 often after a movie, sleep or a turn of the dial) refused every
  following shot until it was switched off and on. The booth now sets the
  camera up for photos again and fires once more within the same shot; if the
  camera still refuses, it is opened afresh, and two such refusals in a row
  always reconnect. A shot with all its retries ends within 30 s.
* New, off by default: boomerang and video can come from a movie the camera
  records itself (`boomerang.source` / `video.source` = `camera`, field
  "Quelle" in the gallery form). The booth downloads the movie from the
  camera's SD card, deletes it there and builds the clip from it: 9:16 in
  1080x1920 instead of 360x640 from the live view, without sound. Needs the
  Canon SDK backend, a body that films over USB and a card in the camera; any
  failure gives the guest the live-view clip in the same run. See
  docs/CAMERA-MOVIE.md.
* Fix (camera movie source): the first photo after a camera-movie clip was
  refused with 0x8D07 ("card"). After the movie the camera now gets the same
  photo set-up as at connect (SaveTo host, then the host's capacity, single
  shot, clock, live view) and SaveTo is read back. The clip keeps the full set
  length (the camera records 0.7 s longer and the end is kept), and without a
  motion frame or print sheet ffmpeg makes the film in one pass from the movie
  (a 2 s boomerang about 4x faster than before). The booth log names the time
  of each step.
* Fix (camera movie source): downloading a long camera movie no longer makes
  the booth report "Die Kamera antwortet nicht"; the download goes in parts
  with a sign of life between them.
* Fix (camera movie source): with no or a full card every run waited several
  seconds for the camera before filming from the live view, long after the
  guest's "move now". After such a failure the booth now films from the live
  view directly for 10 minutes (or until the camera is reconnected), says so
  once in the log ("Film der Kamera pausiert bis …") and in the camera state,
  then tries the camera again. A retake of a clip waits for the film of the
  take before, films are written under their own name first, a movie ffmpeg
  cannot read no longer ends the run without a clip, and leftover raw movies
  are removed at start.
* Change (booth admin): the jump bar at the top of "Verwaltung" shows every
  card at once, as wrapping chips at least 48 px high, instead of one row that
  scrolled sideways (the owner could not find "Zweites Ziel im Netz"). A
  "Nach oben" button appears while scrolling and leads back to the chips.

### Gallery

* New: link previews. Every gallery page (and sub-gallery, password page, run
  page, share link) carries Open Graph/Twitter tags with an absolute 1200x630
  JPEG at `/gallery/<slug>/og.jpg`, so WhatsApp, iMessage, Signal, Telegram,
  Facebook and Slack show a picture. The admin can upload an own
  "Vorschaubild für Link-Vorschauen" in the event form (with a live preview of
  the chat card); otherwise the cover or the first visible photos are used,
  with the title. What a card may show is under Security below.
* New: "Vorschau" for the owner, as in Picdrop. From the event list and the
  event page the gallery opens exactly as a guest sees it — also as a draft or
  with a password, without typing it — under a slim bar "Vorschau ·
  Entwurf/veröffentlicht · mit Passwort" with "Verwalten", "Link teilen",
  "Diashow" and close.
* New: "Verwalten" — the same gallery page with the owner's rights: filters
  (alle, ausgeblendet, wartet auf Freigabe, gelikt, by colour), multi-select
  with bulk hide/show/mark/delete, and in the large view hide/show, colour,
  "Als Titelbild", delete (into the trash, with "Rückgängig") and the original
  download. Hidden pictures are dimmed and badged, like counts show on the
  tiles.
* Change: a calmer guest page. Nothing on the tiles any more (share, download
  and like live in the large view; the colour flag stays where marking is the
  job), the event description as a card with "mehr lesen", one primary "Alle
  herunterladen". The large view on a phone: close at the top, actions bottom
  left, "2 / 44" bottom right; the back gesture closes it. On iPhone/iPad a
  download opens a "Datei-Download" sheet with the picture and "Tippe lange
  auf die Datei, um diese zu speichern." (plus the share sheet); elsewhere it
  downloads directly.
* Removed: the WhatsApp button and its switch. Sharing is the native share
  sheet and "Link kopieren". `share_whatsapp` stays readable in stored
  settings, now defaults to off and left "Schlichte Galerie".
* Change: the switch "hide_red_in_presentation" now reads "Rot markierte
  Bilder für Gäste ausblenden" and says where they stay visible.
* Change (guest gallery): the download size a guest picks was remembered but
  could never be changed again. A button next to the photo count now shows the
  remembered size ("Download-Größe: Mittel · ändern") and opens the size sheet
  again. Tile, lightbox and "Meine Auswahl" keep using the remembered size.
* Fix (guest gallery): pictures showed and handed out the box's storage name
  ("9rq3r2txww_web.jpg") in the file-info line, in the labels a screen reader
  speaks and in the names of the download and share buttons. Everything a
  guest sees now carries the name the download delivers (the event's rule or
  pattern); an event with no rule at all gets `<event>-<number>` instead of the
  storage id, in the download as well. "Original" chosen on purpose still
  keeps the box's name. Same on the run page, "Meine Fotos", share links and
  the slideshow.
* Fix (guest gallery): naming the pictures of a page read the settings two to
  five times per picture; a gallery of 2000 pictures loaded about 2.5 s slower
  because of it. The settings are now read once per page, the names are the
  same. Hidden pictures shown to an admin no longer share one number. A
  strange remembered download size can no longer stop the page's script.
* New (admin): a search over the long settings forms. "Box-Einstellungen", the
  event form and "Einstellungen › Startseite" get a field at the top
  ("Einstellung suchen", sticky under the top bar on a desk) that filters while
  you type: settings and sections that do not match fold away, the matched
  words are marked, a line says "n Treffer" (or "Nichts gefunden." with a
  button to clear), Escape or the cross brings everything back. Case and
  umlauts do not matter (ä = ae), several words must all match. Hidden
  settings stay in the form and are still saved. The event form also gets a
  row of section chips; the box page keeps its jump list. On a phone the same
  field replaces the older phone-only search. The other settings tabs are
  short and have no field.
* Fix (box page): the "Fernwartung" table printed a screenshot job's answer as
  one 40,000-character string and made the whole page scroll sideways.
  Answers now show as one short line ("Bild, 34 kB"; other text cut at 200
  characters), and a long word can no longer widen the table. Same on the sync
  page and in its live list.
* Fix: the gallery refused the original photos of Canon cameras (for example
  the EOS M50) with "unsupported file type" (415). Such files are
  multi-picture JPEGs (Pillow: "MPO"); they are now accepted and stored as
  ordinary JPEGs.

### Second upload target (gallery and booth)

* Change: the second target (Picdrop, FTPS, SFTP, plain FTP) is set in the
  gallery for every paired booth. The switch "Zweites Ziel aus der Galerie
  einstellen lassen" at the booth and the "Noch nicht freigegeben" hint are
  gone, and "Zweites Ziel prüfen" on the box page works again. (Earlier
  tonight the card had learnt to explain that switch and to disable its
  fields while it was off; with the switch gone, that hint went too.) The
  password rule that replaces the switch is under Security. Every change is in
  the change log and on the card ("Zuletzt geändert"). Needs a new booth
  build.
* New: a target folder, optional — empty = the login's start folder, "/" in
  front = from the top of the server, created when missing (nested), with
  placeholders {event} {event_name} {datum} {jahr} {monat} {tag} and an
  example path under the field. Behaviour change: with "alles in einen Ordner"
  FTP/FTPS now use the folder field too (it was ignored), and an empty folder
  on SFTP means the start folder, no longer "/".
* New: "Prüfen" also proves the folder writable (writes and deletes a small
  file); clearer words for a full disk/quota, an unwritable folder and a
  certificate for another name; port 990 speaks implicit FTPS.

### Sync

* Fix (Photobox Sync): a key file that was held for a moment (virus scanner,
  lock) made the program create a new sealing key over the old one, and
  passwords sealed in the gallery could no longer be opened. The key is now
  made only when there is none, created 0600 from the start, and a file that
  cannot be read is retried and then reported, never replaced.

### MCP

* New: an event can be run and supervised from a chat. `get_dashboard` shows
  per booth its light, last contact and unread messages, the server's storage,
  maintenance and backups, and the pictures waiting for approval;
  `acknowledge_booth_messages` is "Als gelesen markieren", `start_backup_now`
  the backup button (two-step), `get_booth_queues` the booth's print and
  upload queues, `discard_failed_uploads` throws failed uploads away (right
  `control`, two-step), `get_silence_alarm` reads the silence alarm.
* Change: every gallery setting is reachable through `set_event_settings`,
  `setup_event` and `configure_event`, checked by the admin form's rules — a
  value the form would quietly drop is refused with its name. Settings that
  open doors need `photos` (`embed`, `allow_mode_switch`,
  `upload_grace_days`) or `secure` (`embed_origins`, `review_url`, `mode_pin`;
  the PIN is never shown). `configure_event` sets date, description, kiosk
  text and a new address (the old one keeps redirecting). `list_events` names
  state, date, kind and dates.
* Change: profile changes answer with only the touched values, the new profile
  version and `ignored_keys`; a typo such as `boomerang.sorce` no longer
  vanishes silently, and a change with nothing known is refused.
  `return: "full"` gives the whole profile as before. `get_booth_profile`
  takes `sections`, `get_booth_texts` sends its catalogue only on request,
  `booth_diagnostics` sends 15 lines by default (was 40) and can leave the
  status out.

### Security

* Fix (MCP): `create_event` returned the gallery's upload token, a secret,
  into the chat. It now says where it is (in the admin).
* Second target: when server, port, type or user change without a new
  password, the booth deletes the stored password (and a pinned SFTP key) and
  holds the uploads until the password for the new server is typed — a
  password never goes to another server. Assistants (MCP) can change the
  fields but never set a password or accept a server key.
* Link previews: password, draft, expired and watermarked galleries show only
  title and colour unless "auch ohne Passwort zeigen" is ticked for an own
  image; silent and unknown galleries get one neutral card. Hidden, pending,
  red-hidden and deleted pictures never reach a card (SECURITY.md, "Link
  previews").
* Guests no longer see the owner's colour marks on the tiles.
* Photobox Sync target passwords: a `set_sync_config` job no longer carries
  FTP/SFTP passwords in clear. Photobox Sync makes an X25519 key pair on first
  use and reports only the public key; the gallery seals every typed password
  to it before the job is written, an empty field keeps the stored one, and a
  version without a key gets no password at all ("Fassung zu alt — Passwort am
  PC eintragen"). The payload is emptied when the program answers, on expiry
  and when a newer settings job replaces it; pages and MCP show dots. Old rows
  are blanked once at start. Backups made before this still contain them;
  SECURITY.md says so.
* SFTP targets pin the server's host key: the first successful login stores
  its SHA-256 fingerprint (shared by the booth and Photobox Sync), a different
  key later ends the connection before user name, password or a file is sent,
  with the files kept queued. The operator accepts exactly the fingerprint the
  server showed (booth with PIN, Photobox Sync, or a gallery button). A
  changed host or port forgets the old pin; the heartbeat carries fingerprints
  only. FTPS refuses a certificate for another name before the password
  (tested); plain FTP says it is unencrypted.
* The booth's own sealing key for the second target is never overwritten
  either (same fix as Photobox Sync above): made only when there is no file,
  0600 from the start, an unreadable file retried and then reported.
* MCP: an unreadable or past expiry date is refused on every path
  (`setup_event`, `configure_event`, `set_event_settings`), with the key named,
  whatever right allows the change.
* Templates: one check at every way in (asset upload, both editors, customer
  layout, MCP `save_template` / `validate_template` / `add_asset`, package
  import, library, a template a booth pushes, every load at the booth). It
  refuses with every field named and drops unknown keys with a hint; a refused
  file at the booth is skipped and not offered to guests. All bundled
  templates pass and render byte for byte as before.
* The event name travels in a file's meta only to network targets (needed for
  `{event_name}` in the second target's folder); the gallery and local copies
  keep their meta as before.

### Docs

* README (German and English) rewritten for 0.9.1: MCP and open source first,
  status, known limits, links to https://photobox-gallery.com.
* Windows packaging: the build now produces the installer (booth and sync),
  a standalone `PhotoboxBooth-<version>.exe` and a standalone
  `PhotoboxSync-<version>.exe`, all listed in `SHA256SUMS.txt`
  (`packaging/windows/README.md`).

## 0.9.0 (17.11.2026)

Everything since 0.2.0 (18.09.2026). `VERSION` and the booth now say 0.9.0.
Known limits: see the [README](README.md#known-limits).

### Booth and gallery, new since 0.2.0

* Touch screen: the booth admin is readable on a touch screen, a finger pans
  every page and list, and there is an on-screen keyboard.
* PIN: four digits are allowed, the booth changes it and the gallery never
  undoes that; there is a lockout after wrong entries. PIN fields are masked
  and an empty field keeps the PIN.
* Start screen: built-in backgrounds, chosen with a preview at the booth and
  in the gallery.
* Queues: the booth says what waits; prints and failed uploads can be cleared
  one by one.
* Templates page at the booth: a way back from every state, and one tap
  decides which layouts guests see.
* Pairing: the booth's own settings become the gallery's starting profile.
* Second upload target: set from the gallery (sync page and MCP), the password
  is sealed for the booth, and every file reaches the target.
* Frames for boomerang and video: guests pick the frame on the set-up screen,
  assistants can build and assign them.
* One button takes a gallery live, and back to draft.
* Errors can be marked as read and are grouped by cause; maintenance and
  backup cards say what to do, and a backup can be made on demand.
* A guide that lets any AI write valid print templates for the SELPHY.
* Tests keep all temporary files in one folder of their own and remove it.

Compatibility: the booth and the gallery agree on one version number, but
neither refuses the other because of it. Update the booths before the gallery
when a change below says so.

### Licence and project

* One licence story everywhere: AGPL-3.0-or-later, complete source, no paid
  edition. The Canon EDSDK is never shipped; every operator gets it from
  Canon. Money comes only from running galleries and booths as a managed
  service.
* `LICENSE-EXCEPTION`: an additional permission under AGPL section 7 for
  combining the booth with the Canon EDSDK. `LICENSE` stays the unmodified
  AGPL text.
* `THIRD_PARTY.md` lists fonts, ffmpeg, every Python package and the built-in
  looks with their licences; the font folders, the gallery image and the
  Windows and macOS builds carry the licence texts.
* The gallery's About page and the booth's admin card name the source address
  (settable with `PHOTOBOX_SOURCE_URL`) and the running commit.
* **Renamed looks.** Seven built-in looks were named after Kodak and Fujifilm
  films. They are now named after what they do (for example *Golden Warm*,
  *Natural Portrait*, *Classic Mono*). Old names in profiles keep working on
  an updated booth. **Update the booths before the gallery:** a gallery of
  this version saves the new names, which an older booth does not know.
* The paywall leftover from an older edition is gone; the statistics page
  always shows the statistics.
* `constraints.txt` pins the versions the test suites passed with; CI runs
  `pip-audit` and a secret scan.

### Booth

* Runs only when it is booked; can be locked on the spot; the factory PIN only
  opens "choose your own PIN".
* Guests choose per run "In die Galerie" or "Nur drucken", can cancel during
  countdown and recording, and can delete their own run with the QR code as
  proof.
* A data-minimal mode for kindergartens and school fairs: print, then delete.
* Printing: print jobs are never lost, two printers share the load again,
  waiting time and paper supply of slow dye-sub printers are accounted for,
  strips, postcards and the other common paper sizes, a calibration page, a
  printer that is taken over or cannot print is explained to the guest.
* Cameras: Canon over the network (CCAPI, nothing of Canon's in the build),
  a camera picker that answers "I have a Sony A7", Sony and Nikon under Linux,
  a smoother path from the Canon SDK download to the first shot.
* The kiosk: every text editable from the gallery, the brand typeface shipped
  with the program, configurable colours, button shape, countdown ring,
  backdrops for setup, countdown and result, readable at Full HD, a template
  editor at the booth and in the browser, collages that bring a GIF along.
* Setup on Windows, Linux and macOS; a signed, notarised macOS app; Windows
  power and autostart settings, each one reversible.
* The doctor (`--doctor`) checks camera, printer, disk, clock and queue.

### Gallery

* Deletion in the gallery reaches the booth, and a deleted run stays deleted
  even when a late upload arrives.
* Retention runs without a cron job, with a 30-day default and a receipt for
  every deletion.
* Security: a brake on every door (login, second factor, recovery, gallery
  password, booth PIN), getting back into your own gallery without touching
  the server, browser-side protections on every answer, uploads
  judged by content, `/data` following the gallery's gates, guest uploads lose
  their GPS position, password changes end other sessions.
* Remote management: jobs arrive or expire and never run twice, updates are
  one upload and one click, numbers per event and per box.
* Galleries under the customer's own address, two languages or one, a
  sharepic designer, a second-screen slideshow, keyboard and screen-reader
  use, a phone view that shows pictures rather than buttons.
* An MCP endpoint with its own key, so an assistant can run the gallery.
* A privacy-policy template with a legal basis per purpose and real periods.

### Sync

* Pairing with one short code instead of a copied key.
* Several jobs with their own FTP targets, offline; remote admin, folder
  picker, background service; never two runs at once, never half a file.
