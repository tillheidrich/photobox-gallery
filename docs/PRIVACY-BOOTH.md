# Privacy at the photo booth – for operators

What the booth program does with guests' photos, where it keeps them, where it
can connect to, and what you as the operator have to take care of. It describes
the program, not your event. **Not legal advice.** German version:
[DATENSCHUTZ-BOX.md](DATENSCHUTZ-BOX.md).

## Who is responsible

Whoever sets up the booth and photographs guests decides on purpose and means
and is the controller under the GDPR. The publisher of the software does not
run your booth and sees no photos. If you use a gallery hosted by a provider,
that provider processes data on your behalf; you need a data processing
agreement (Art. 28 GDPR) with them.

## What the booth stores and where

| What | Where | How long |
|---|---|---|
| Photos, videos, GIFs of a run, print layouts | `%LOCALAPPDATA%\PhotoboxBooth\sessions\` | until deleted (see below) |
| Queue for gallery and storage targets, photo library | `outbox.sqlite`, `library.sqlite` in the data folder | until delivered / as long as the run |
| Consent record per run (when switched on): option chosen, language, hash of the notice text, time – **nothing about the person** | with the run | as long as the run |
| E-mail address when a guest has the photo sent | `mail.sqlite` in the data folder | the booth's mail queue |
| Logs (technical, no photos) | `logs\booth.log` | rotating, 5 × 2 MB |
| Settings | `profile.json`, `local.json` | permanent |
| Copy to a second folder / USB stick | only when switched on under "storage" | until you delete it |

**Clean-up** (profile `aufraeumen`, **off** by default) deletes runs after
`nach_tagen` days (default 14), only those the gallery confirmed, and always
keeps the latest 20. **Data-minimal mode** (profile `privacy.mode =
print_only`) deletes a run after printing, at the latest after
`delete_seconds` (120 s).

Installing, updating and uninstalling leave the data folder alone. Only the
uninstaller asks whether to delete it (default: no). Second folder and USB
sticks are never touched.

## What goes to the gallery

Only when the booth is paired with a gallery (`local.json: server.url`): the
photos and videos of each run with event, time and – when switched on – the
consent choice; plus heartbeats and status reports (camera, printer, storage,
program version, log excerpts for remote support). A run where the guest chose
"print only" stays on the booth. How long the gallery keeps photos is set in
the gallery (the event's retention period).

## Every network connection

Out of the box the booth connects to nothing outside the PC. Each row is only
switched on by the setting named. The table is generated from
`tools/outbound_connections.py`; a test keeps it in line with the code.

<!-- outbound:start -->
| Id | What | Where to | Switched on by | Default |
|---|---|---|---|---|
| `gallery` | Gallery: upload photos, fetch profile and jobs, heartbeat, mail via the gallery, update offer, sign-off when shut down on purpose, hourly retry of failed uploads | the gallery address the operator enters or gets by pairing | local.json: server.url | off |
| `pairing` | Pairing with a gallery using a pairing code | the address inside the code; a short code becomes https://<code>.photobox-gallery.com | entering a pairing code in the admin | off |
| `remote_control` | Remote control by the gallery (orders from a fixed list; answers go to the gallery, on request also the camera's live image and a screenshot, 640 px at most) | the paired gallery (over the gallery connection) | local.json: remote_control.allowed | off |
| `update` | Download a new program version (only when the paired gallery offers one and the operator or the gallery starts the download; checked by SHA-256) | the download address in the gallery's offer | local.json: server.url | off |
| `mail_smtp` | E-mails to guests and alerts to the operator directly over SMTP | the SMTP server entered | profile: mail.enabled | off |
| `webhooks` | Webhooks: your own address is told when a photo is ready | the webhook address entered | profile: webhooks.enabled | off |
| `targets` | Additional storage targets over SFTP, FTPS or FTP | the servers entered | local.json: targets | off |
| `camera_wifi` | Canon camera over Wi-Fi (CCAPI) | the camera's address on the local network | local.json: camera.ccapi_url | off |
| `digicamcontrol` | Camera via digiCamControl (runs on the same PC) | http://127.0.0.1:5513 (default), only with camera backend "digicamcontrol" | local.json: camera.backend=digicamcontrol | off |
| `printer_ipp` | Network printer over IPP | the printer address entered (ipp://...) | local.json: printers | off |
| `guest_wifi` | Guest page on the booth's Wi-Fi (download without internet): a second listener on all addresses, port 8766 - incoming, guests' phones connect | incoming from the local network | local.json: guest_share.allowed | off |
| `local_only` | Parts of the program talking to each other (watcher, window, booth) - 127.0.0.1 only | 127.0.0.1 (never leaves the PC) | - | on |
<!-- outbound:end -->

Never: no telemetry, no usage statistics sent to the publisher, no advertising,
no face recognition, no AI, no fonts or scripts loaded from the internet, no
update check of its own without a paired gallery. QR codes are generated on
the PC.

Outside the program: Windows, the WebView2 Runtime and – if the booth falls back
to Chrome or Edge – the browser open connections of their own to Microsoft or
Google (updates and more). You control those in Windows or the browser.

## What you have to take care of

1. **Inform (Art. 13 GDPR)** – before the first photo, clearly visible at the
   booth. Template below. The booth can also show a notice before each shot
   (profile `disclaimer`) or offer "to the gallery" / "print only" (profile
   `consent`, text in `consent.note`).
2. **Legal basis** (e.g. consent through the choice at the booth, or a contract
   with the host). The booth records which option a guest chose and which text
   they saw – not who they are.
3. **Erasure requests:** guests can discard their run right at the booth (while
   `allow_delete` is on, the default). In the gallery a guest deletes their
   photos with their QR code. Anything left on the booth you delete in the data
   folder or with clean-up.
4. **Access requests:** runs are stored by event and time; there is no link to
   persons (no face recognition, no names apart from an e-mail address a guest
   chose to enter).
5. **Protect access:** change the admin PIN (the factory PIN is only accepted
   for choosing your own), use a Windows account with a password, encrypt USB
   sticks or empty them after the event.

## Notice next to the booth (template)

Replace the placeholders in brackets. Keep it short and link the full
information (QR code to your privacy page).

> **Photo booth – privacy notice**
>
> This photo booth is operated by **[name, address, e-mail of the operator]**.
> When you take a photo we store it on the booth's computer **[and upload it to
> this event's online gallery, reachable with your QR code / a password]**.
> Purpose: photos for **[host / event]** and for you. Legal basis: **[your
> consent by taking the photo / Art. 6(1)(…) GDPR]**. Deleted **[after … days]**.
> You can discard your photo right at the booth and delete it in the gallery
> with your QR code. You have the right of access, rectification, erasure,
> restriction, objection and to lodge a complaint with a supervisory authority.
> No face recognition takes place. Details: **[link or QR code to the privacy
> policy]**.

## Trademarks

Canon, EOS and SELPHY are trademarks of Canon Inc.; Windows, Edge and WebView2
are trademarks of Microsoft Corporation; DNP is a trademark of Dai Nippon
Printing Co., Ltd. The names are used only to describe what the program works
with. The project is not affiliated with any of these companies.
