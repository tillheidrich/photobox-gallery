# Changelog

One version number for the gallery, the booth and the sync program
([`VERSION`](VERSION)). The development history itself stays in the author's
private repository; this file says what changed for the people who run it.

## Unreleased

## 0.9.2 — 2026-11-18

### Windows: two editions, both free of charge

* From 0.9.2 there are two Windows editions of the same program. The **free
  edition** is built from this repository alone, is attached to the GitHub
  releases, is the only one built in CI and the only one that will be
  code-signed; it contains no file of Canon's SDK, as before. The **edition
  with camera SDK** is offered only on the project website: the same program
  plus the runtime libraries of the Canon EOS Digital SDK, so a Canon EOS
  camera works without the `--install-edsdk` step. Offered as a setup only:
  `Photobox-Setup-<version>-mit-Kamera-SDK.exe`,
  `SHA256SUMS-mit-Kamera-SDK.txt` (the setup alone),
  `THIRD-PARTY-NOTICES-mit-Kamera-SDK.txt`. It is built by the maintainer with
  `build.ps1 -Installer -WithCameraSdk <SDK Dll folder>` from a folder outside
  the repository, never on a CI runner, and stays unsigned. Canon's files are
  copied unmodified (checked by checksum), are not under the AGPL and come
  with end-user terms of their own (`CAMERA-SDK-LICENCE.txt`, German and
  English): the setup shows them on a second licence page that must be
  accepted; the booth's "Über" card lists the SDK as a
  separate proprietary component. `THIRD-PARTY-NOTICES.txt` of that edition
  has a section for it. Guards: build.ps1, the PyInstaller spec, the setup
  script and `packaging/windows/camera_sdk_edition.py` refuse a CI runner and
  any folder inside the repository; the free files are checked for Canon
  files before and after the second build; a test fails if a workflow names
  the switch. The former `Edition` define of the setup is gone.
* The booth recognises a bundled SDK by itself (`boothcore/camera_sdk_bundle.py`):
  automatic camera choice picks the Canon SDK, the self-check calls it ready.
  The free edition behaves as before; `--install-edsdk` stays.
* Review of the edition with camera SDK (18.11.2026): a silent install
  (`/SILENT`, `/VERYSILENT`) no longer accepts Canon's terms tacitly - it stops
  unless `/ACCEPTSDKTERMS=1` is given, and says why in the setup log and in
  `installer.log`. Only a bundle inside the program or the setup's own
  installation folder (with its uninstaller data) counts as the edition; an
  `EDSDK` folder and a licence file next to some other `.exe` do not, and the
  booth no longer writes `CAMERA-SDK-LICENCE.txt` next to itself (the copy
  goes to the data folder). `build.ps1` clears a left-over
  `PHOTOBOX_CAMERA_SDK_STAGE` before it builds and checks the free programs
  for Canon files before it signs anything; the portable booth with camera
  SDK is still built for the check, but lies in `nicht-veroeffentlichen\`
  and is not in the checksum file. `--doctor` and "Prüfen" name the
  `EDSDK.dll` that was actually loaded; the documented load order is now the
  real one: `edsdk_dll`, data folder, program folder, bundle.
* Voluntary support: a "Support the project" link (PayPal) in the README and
  `.github/FUNDING.yml`. Nothing in the booth or the gallery asks for money.

### Wording

* User-visible texts, help and documentation name the booth's second upload
  target neutrally ("Online-Galerie per FTP/FTPS/SFTP", "an FTP-fed proofing
  gallery") instead of a service's name. Behaviour is unchanged, including
  "everything into one folder" and the name under which existing booths keep
  their target. `tools/check_hygiene.py` stops the name from coming back.

### Changed

* Booth: the guest can choose how long a boomerang records. Switch on
  `boomerang.seconds_guest_choice` and list two to four lengths in
  `boomerang.seconds_choices` (1–15 s each, e.g. 2, 5, 10; with the camera's
  own film at most 10 s): the set-up screen shows them as big buttons, the
  title and the recording ring follow the choice, and the finished film runs
  15–30 s (`min_total_seconds` / `max_total_seconds`; 2 s plays 4 times =
  16 s, 5 s twice = 20 s, 10 s once = 20 s). Off by default; without it
  `seconds` and `loops` work as before.

* Booth: title and status line on an own start picture are now white on a
  dark shade by default, also in the light colour scheme (dark text stood on
  dark photos). `start.text_tone` (`auto`, `hell`, `dunkel`) forces either;
  tiles and buttons keep the scheme's colours.

* Booth: a collage is now also saved as an animated GIF by default
  (`gif.from_collage` on, from two pictures); switch it off per booth.

* Booth: photo mode offers the chosen layouts with one photo (event layouts,
  own templates, ticked built-ins) on the same cards as the collage; the
  collage then lists only layouts with two or more photos. The layout makes
  the result and the print, the plain photo is kept as well. Booths without
  chosen layouts are unchanged.

* Booth: the left mouse button is the buzzer by default (`controls.mouse_start`
  `left`; `off` makes the mouse a mouse again). Start keys for space-bar
  buzzers are unchanged. Every buzzer or start-key press now leaves one line
  in booth.log saying on which screen it came and what it did, or why not
  (camera not ready, a window open, bounce guard, run in progress).

* Booth: GIF is set up like Boomerang. Guests choose the format (same switch
  as for photos, or `gif.guest_choice`), and the boomerang's frames can be
  offered for the GIF too (`gif.frames` and the same frame keys); the frame
  is drawn around every picture. Without these settings the GIF now uses
  `gif.format` when nobody chooses (before: the photo's default format).

### Fixed

* Booth: the event's file-name rule ("Event_date_time") now reaches the
  booth. The booth dropped `event.file_naming` from the gallery's profile, so
  USB, folder and FTP/SFTP targets got the run id as file name. Files that
  were already queued keep their old names.

### Development

* Windows release workflow (`.github/workflows/windows-release.yml`),
  prepared for free code signing through the SignPath Foundation, which is
  **not active yet**: the files are still unsigned. A tag `vX.Y.Z` (or a run
  by hand) builds booth, sync and setup on GitHub-hosted Windows runners with
  `packaging/windows/build.ps1`, stops if a file of Canon's SDK is in the
  build tree or inside a program (`tools/check_hygiene.py --build-output
  --pyinstaller`), and makes a draft pre-release named "unsigned". Once the
  SignPath settings exist in the repository, booth and sync are signed first,
  the setup is built from the signed programs and signed too, every signature
  is checked, and `SHA256SUMS.txt` is written over the signed files; the
  release stays a draft for the owner to publish. Owner's settings:
  `docs/CODE-SIGNING.md`; public policy: `docs/CODE-SIGNING-POLICY.md` and
  the README's "Code signing policy".
* `PhotoboxSync.exe` now carries a Windows version resource like the booth
  (product "Photobox", version from `VERSION`, publisher, AGPL copyright,
  original file name); both come from `packaging/windows/version_resource.py`.
  The setup writes the version as text too, so all three files name the same
  product and the same product version, as SignPath requires. Explorer shows
  "Photobox Booth" / "Photobox Sync" as the file description.

* CI: the gallery, booth and sync suites failed on GitHub Actions (Ubuntu,
  Python 3.12) for 0.9.1. Each suite installed only its own part, but
  the suites test across the boundaries: the gallery tests drive the booth
  (httpx), the booth tests import the gallery app (Flask), the sync tests
  run a local FTP server (pyftpdlib). Every suite now installs all three
  parts plus `requirements-dev.txt`, which names these cross-suite packages
  and pytest; the dependency audit covers the test packages too. A gallery
  test checks the workflow against what the test files import.
* Built-in looks: `tools/make_luts.py` no longer sums colour channels with
  `@` (BLAS). Accelerate and OpenBLAS add in a different order, and one
  warm-film value on an exact six-decimal tie came out 0.670319 on Linux
  instead of 0.670318. The sums are now written out in a fixed order; the
  shipped `.cube` files are unchanged.
* Booth tests: the kiosk geometry comparison hands its script to Node on
  stdin instead of `node -e`; the collage cases were larger than Linux allows
  for one command-line argument ("Argument list too long").
* Windows build: `SHA256SUMS.txt` is written with LF line ends and without
  BOM; with CRLF, `sha256sum -c` on Linux and macOS reported every file as
  missing. Checked statically in the tests; not run on Windows.

### Booth and sync

* Fix: nothing offers to print a run that has no print sheet. The consent
  step offered "Nur drucken" before a video or a boomerang without
  `boomerang.print`; a guest who took it got neither a print nor a gallery
  link. For those modes the step now shows only its note (recorded as
  "notice"), and the booth refuses "Nur drucken" for them (400
  `consent.option`, also from a kiosk page that is still open from before).
  The admin's reprint list ("Nachdruck") shows only runs with a print sheet;
  a film's "Drucken" there only ever answered 409. Photo, collage and GIF
  (whose sheet carries its stills) are unchanged. The result screen and the
  kiosk gallery also hide "Drucken" when a run's print file is gone.
* Second upload target (FTPS) on Windows: a server whose certificate chain
  ends in a root the Windows store had not fetched yet (a newer Sectigo
  root, for example) was refused with "unable to get local issuer
  certificate", while the gallery upload on the same PC worked. Python's
  default context reads only the roots already in the Windows store; httpx
  brings the certifi bundle. FTPS (explicit and port 990, control and data
  connection) and the booth's SMTP mail now trust the system roots plus the
  certifi bundle, through one helper (`photobox_sync/trust.py`); certificate
  and host name are always checked, there is no switch to turn that off.
  certifi is now a declared dependency of the sync package (it came with
  httpx before); a missing, damaged or unreadable bundle leaves the system
  store alone in use, verification stays on.
  Both Windows specs pack certifi's `cacert.pem` explicitly. Tested on macOS
  against a local FTPS server with an empty "system store"; the frozen .exe
  was not run on Windows.
* The booth's message for this case says what it is ("Das Zertifikat des
  Servers ließ sich nicht prüfen …", or "… ist abgelaufen") instead of
  asking whether the chosen kind of target fits the server.
* Second upload target (FTPS), next step on the same host: with the
  certificate accepted, "Ziel prüfen" failed with "EOF occurred in violation
  of protocol". Python's ftplib ends every data transfer with a TLS shutdown
  and waits for the server's answer; some servers (here a Go FTP server)
  close the data connection without one, and OpenSSL 3 calls that an error
  although all bytes went through. The FTPS client now accepts exactly that
  at the end of a data connection; the server's reply on the control
  connection and the size check after an upload still decide, and a
  connection cut during the transfer still fails. Data connections also
  resume the control connection's TLS session, which servers with
  "require_ssl_reuse" (vsftpd and others) demand. Explicit and implicit
  (port 990) FTPS, TLS 1.2 and 1.3, tested against local pyftpdlib servers
  that close bare, require session reuse or cut an upload; not yet run
  against the real host. If the error shows anyway, the booth says "Der
  Server hat die verschlüsselte Datenverbindung abgebrochen …".
* Second upload target (FTP/FTPS), live-push accounts: logins that drop
  every upload straight into a gallery chosen on the service's side took the
  login but refused every file with "550 Permission denied" or "EOF occurred
  in violation of protocol". The booth uploaded `<name>.part` and renamed it;
  such accounts take picture and film names only and refuse rename, delete
  and folders. New choice "Direkt hochladen, ohne Zwischennamen" (`direct`,
  default off): the file goes up under its final name, no rename, no delete;
  proof by SIZE where the server answers, otherwise its 226 and the bytes
  sent. Without the choice, a refused in-between upload or rename is tried
  once more under the final name in a fresh connection; if that works, the
  booth remembers it for this server and login (next to the SFTP host keys,
  so it survives a restart) and sends final names from then on: at once after
  a real 5xx refusal, after a TLS end-of-file with no reply only once that
  worked twice in the same run. 552/452 (quota, disk full) never trigger the
  extra attempt and never give a file up. If the note cannot be saved, it is
  logged once and holds until restart. Never more
  than one extra attempt per file and try; a reset or 426 with no refusal is
  still the network and waits as before. An in-between file the server will
  neither rename nor delete is logged once, not an error. "Prüfen" falls
  back to a 285-byte JPEG `photobox-test.jpg` and says "angemeldet, direkter
  Upload möglich (Live-Push)", and that the test picture stays in the target
  when the account lets nothing be deleted. A final name refused in direct
  mode is said in plain words ("Das Konto auf dem Server nimmt die Datei …
  nicht an"): permanent for a name that is no picture or film, otherwise the
  files wait. Tested against a local pyftpdlib server in an "ingest-only"
  mode (550 or a dropped TLS data connection); not yet run against the real
  service.

### Gallery

* Clips are never a black box: the large view in the light theme now has
  the theme's surface (white) behind picture and clip, with its buttons in
  the text colour (the dark theme is unchanged); on the run page and "Meine
  Aufnahmen" a clip without poster loads its first frame; a poster-less clip
  tile has the surface colour behind it, not near-black. At the booth the
  full-size view of a film shows its poster until it plays.
* Fix: in Chrome (Android and desktop, version 133 and later) gallery tiles
  were 0 px high until their picture had loaded, and stayed so when it never
  did. The rule `aspect-ratio: attr(width) / attr(height)` is read by newer
  Chrome as text and falls back to `auto`, which also switched off the
  browser's own shape from the tile's width and height. Portrait clips and
  GIF films whose poster had not arrived vanished from the masonry, and
  tiles jumped while the page loaded. The rule is gone; tiles keep their
  shape from the stored size. A clip without a poster now shows its own
  first frame as the tile instead of an empty image.
* Fix: after saving the booth's second upload target ("Zweites Ziel",
  FTP/FTPS/SFTP) the sync page showed the form empty and "Noch kein
  zweites Ziel eingerichtet" with the job "erteilt", although the booth took
  the setting moments later. The page rendered the booth's last report, which
  still predated the change, ignored the job it had just queued and never
  looked again. The form is now filled from the change on its way (marked
  "gesendet, wartet auf die Box"; never the password) or from what the booth
  reports; a line under the password field says whether one is stored at the
  booth or awaited. "Stand der Box" names the last contact (and says so when
  the booth is silent), the change or check waiting and since when, the
  folder layout, and the last check with its age; the job list says the
  booth's answer in plain words. While something waits the card asks the
  gallery every 4 s for up to two minutes and shows the answer without a
  reload. The check button is now "Verbindung prüfen" and is offered while
  the first setting is still on its way.
* Event page: "Box bei diesem Event" lists the booths on the event and
  whether each has taken the event's settings, is about to fetch them or is
  not calling, with its last contact, and follows them after saving. It also
  says that the second upload target is set per booth (with links to the
  booths' sync pages); a folder and on/off per event needs a booth update and
  is not offered yet.
* Security: a picture the operator hid could still be downloaded by its
  number (`/gallery/<slug>/photo/<id>/download`), and its watermarked copy
  and photo QR code came too; a red-marked picture the gallery hides from
  guests came down the same way, in the selection and "download all" ZIPs,
  on the run page, in "Meine Aufnahmen" and through `/data`. Every guest
  route now answers both like a picture that does not exist. The booth's
  full-size original of a visible web version still downloads where the
  event allows the original.
* Fix: with the download size choice switched off for the whole gallery,
  every download was the original, also `size=large` in an event that bans
  originals; the gallery asked for `size=original` and such an event answered
  403. Without a choice the download is now the best size the event allows,
  on the server and in the gallery script.
* Privacy: guests no longer find the operator's colour marks in the page
  source (`data-tag` on every tile); only proofing and manage views carry it.
* Fix: "Teilen" on the run page and in "Meine Aufnahmen" shared whatever came
  back, so a 403 or 404 page could go out as "foto.jpg". A failed fetch now
  falls back to the link, as in the gallery.
* Fix (iPhone): "Teilen" fetched the full original (several MB) before
  opening the share sheet; on a weak network Safari could refuse the sheet
  because the tap was too long ago. Sharing now sends the best allowed size
  up to 2048 px (the 640 px size only where it is all the event allows); the
  download stays the best size. The picture to long-press in the iOS
  "Datei-Download" sheet ("Zu Fotos hinzufügen") is that same up-to-2048 px
  JPEG, fetched once, never the display copy; "Als Datei laden" still
  fetches the best download. A share fetch no longer counts as a download in
  the statistics a second time.
* Fix: an animated GIF came down as a still JPEG (its first frame) when a
  size was chosen, when shared and in the iPhone save sheet. A size that
  cannot be animated now gives the animated GIF itself, with its name and
  type, in single downloads, shares and the selection ZIP.
* Fix: shares from the run page and "Meine Aufnahmen" are counted in the
  statistics like shares in the gallery.
* Privacy: the owner's views of a gallery ("Verwalten" with hidden and
  pending pictures, "Vorschau") are sent with `Cache-Control: no-store`.
* Fix: in the large view on a phone the previous arrow was covered by the
  picture (only a sliver showed at the left edge). Both arrows now lie above
  the picture, sit mirrored inside the screen with the safe area, are at least
  44 px and have a darker disc that reads on bright pictures.
* Fix: a guest's download is the best quality the event allows (the original
  if offered, else the largest size) unless the guest changed the size. The
  size sheet no longer pops up on the first download with the 640 px line on
  top; it opens from "Download-Größe: … · ändern". A 1800 px print had come
  down as about 23 KB. Sharing a picture now sends that same file instead of
  the display copy.
* Fix: on iPhone and iPad "Herunterladen" and "Teilen" no longer both end in
  the share sheet. Download shows the picture to long-press plus "Als Datei
  laden" (a real file download); share opens the share sheet.
* Privacy fix: the link-preview image of a draft gallery (or a folder under
  one, or a gallery not live yet) answered with the event's title, while an
  unknown name got the neutral card — a guesser could confirm a name and read
  its title. It now answers byte for byte like an unknown name. A guard test
  walks every public route with a gallery slug and compares a draft with an
  unknown name.
* Fix: the photo count in a gallery's link preview ("981 Fotos") counted every
  stored row, including the unframed captures and red pictures the page hides;
  it now says the number the page shows (568).
* Faster on phones: text answers (HTML, CSS, JS, JSON, SVG) go out gzip-
  compressed when the browser accepts it — in the app, standard library only,
  so it works behind Coolify/Traefik and on a NAS without a proxy alike. A
  gallery page with 200 tiles: 326 KB → 28 KB. Media, ZIPs, streams, range
  requests and answers already encoded are left alone; ETags become weak so a
  revalidation still answers 304.
* Faster on phones: in the photo grid a phone loads the thumbnail (≥ 600 px
  long edge) instead of the web version (1200–1600 px), which it took at 3x
  pixel density — 3 to 7 times the bytes per tile. The large view still loads
  the web version.
* Wording: the German guest pages speak to the guests as a group ("ihr")
  throughout; "Tippe lange", "Bitte gib", "Bitte wähle", "Schau bitte",
  "Scanne den QR Code" and the guest upload's "dich/deine" are now "ihr" forms
  like the rest. A guard test checks every guest string and template.
* Sizes shown to guests read "ca. 61 KB" / "ca. 1,4 MB" (German comma, sensible
  rounding, "1.4 MB" in English) instead of "ca. 61.42 KB": one helper on the
  server (`media.guest_bytes`) and the same rule in the page's script.
* The guest filter "Originalfotos" is now "Einzelfotos ohne Rahmen" ("Single
  shots without frame"): it lists the camera's single shots uploaded without
  frame, mostly those that went into a collage. `?show=originals` still works.
* The run page (`/p/…`) and "Meine Aufnahmen" offer one "Herunterladen", the
  best size the event allows (the original if offered), instead of 2048 px
  next to a separate "Original"; their share button sends that same file
  instead of the display copy. A guard test checks every guest page.

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
