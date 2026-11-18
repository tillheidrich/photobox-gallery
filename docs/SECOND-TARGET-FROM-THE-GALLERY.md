# Setting a booth's second upload target from the gallery

Status 17.11.2026 (evening), branch `feat/second-target-open`: no opt-in at
the booth any more, rule B2 for passwords, folder with placeholders. Before
that, branch `feat/zweites-ziel-galerie`. Built along the
design that stood here as "not built" on branch `fix/admin-1711`; the
differences are listed at the end. Rules and tests: `business_logic/index.md`
A-3 to A-3c.

The "Zweites Ziel im Netz" card of the booth's own admin (an FTP-fed proofing gallery over FTPS,
an FTP server or an SFTP NAS; ids `zt_*` in `booth/boothcore/web/admin.html`)
can now also be filled in on the gallery's sync page of a booth
(`/admin/devices/<id>/sync`, card `#zweites-ziel`) and through MCP with the
right `secure`. Both ways edit the same entry of the booth's local `targets`
(stored legacy name `"picdrop"`, or the older `zweites-ziel`).

## No opt-in at the booth (since 17.11.2026, evening)

The owner's decision: the switch "Zweites Ziel aus der Galerie einstellen
lassen" (`remote_targets.allowed`) is gone. Every paired booth publishes its
public key in the heartbeat and applies `set_second_target`,
`check_second_target`, `backfill_second_target` and
`accept_second_target_host_key` from its gallery. A `remote_targets` key
left in an old `local.json` is ignored. The booth's own card keeps working
without internet. An older booth (before this change) still reports
`allowed: false` and no key while its switch is off: the gallery card then
shows a plain hint and no form.

What protects the password instead is rule B2, decided at the booth: see
"A password belongs to one server" below and SECURITY.md.

## The password

* The booth makes an X25519 key pair on first use, stored in
  `<data>/keys/second_target.key` (base64, file mode 0600 where the OS has
  one). It is not in `local.json`, so it is not in `/api/admin/config`, a
  profile export, the doctor or the log. Its public key and a 12-hex key id
  travel in the heartbeat (`second_target.public_key`, `key_id`).
* The gallery's POST handler (`sync_second_target` in
  `app/blueprints/admin_booth.py`) seals the typed password with
  `nacl.public.SealedBox` to that key (`app/second_target.seal`) before
  anything is written. Only `password_sealed` (base64) and `key_id` go into
  the job payload. The clear password is never stored, logged, flashed or
  rendered.
* The booth opens it (`Keys.open`), checks it (at most 256 characters, no
  control characters) and writes it into its local target as before. A
  wrong key id or a box it cannot open is refused with "bitte in der Galerie
  neu eingeben".
* The payload is emptied in the gallery database when the booth answers
  (`finish_jobs`), when the job expires (`expire_jobs`, 10 minutes) and when
  a newer set job replaces an open one. Pages and MCP see `password_sealed`
  as `••••` even before that (`masked_payload`).
* The form field is write-only: `type="password"`, `value=""`,
  `autocomplete="new-password"`, placeholder "gespeichert — leer lassen"
  (or "noch keins gespeichert"). Empty keeps the stored password. "Zugang
  entfernen" (asks once more on the button) removes the whole target at the
  booth.
* PyNaCl comes with paramiko on both sides (pinned 1.6.2 in
  `constraints.txt`, listed in `THIRD_PARTY.md`); it is now named in
  `requirements.txt` and `booth/requirements.txt`, and `nacl.public`,
  `nacl.exceptions`, `nacl.bindings` are hidden imports of the booth's
  PyInstaller spec. Not checked on a built exe yet (see "Not verified").

## The narrow job

`set_second_target` payload: `v`, `action` (`set` | `clear`), `target`,
optionally `password_sealed` and `key_id`. Any other key refuses the job.
`target` holds exactly `type` (ftps, ftp, sftp), `host` (a name or an IP
address; no scheme, path, port, user or spaces; at most 253), `port` (0 =
usual, 1–65535, a real integer), `user` (at most 128), `path` (at most 255,
backslashes become slashes, empty parts and `.` dropped, `..` refused, a
leading slash kept), `flat`, `enabled`, `originals`, `print_sheets`
(booleans), and `direct` (boolean, since 18.11.2026; the gallery sends it
only when it is on, the booth reads a job without it as off: upload under the
final name, for live-push accounts, see business_logic rule L-S5). No control characters anywhere. The same lists and limits are in
`app/second_target.py` and `booth/boothcore/second_target.py`; a test in
each suite compares them.

SFTP takes a password only from here. A `key_file` set at the booth stays
(and is dropped when the type changes away from SFTP); the job cannot set one.

Transport security: FTPS checks the server's certificate and host name
against the system's CA list (`ssl.create_default_context`; tested on
17.11.2026 against a local server with a certificate for another name), FTP
sends user, password and pictures in clear and says so wherever it is
chosen. SFTP pins the server's host key since 17.11.2026 (A-3c closed,
`business_logic/index.md` L-S1): the first successful login stores its
SHA-256 fingerprint in `<data>/sftp_host_keys.json`; a different key later
ends the connection before anything is sent ("Der Server zeigt einen anderen
Schlüssel als beim Einrichten …"). The card shows the stored fingerprint
and, after a refusal, the one the server showed; "Neuen Schlüssel annehmen"
is its own button and job `accept_second_target_host_key` {v, fingerprint},
refused unless it is exactly the fingerprint the booth reported (gallery
form only, never MCP). Saving the form never accepts a key. A changed type,
host, port or user forgets the old pin together with the password (rule
B2), so the next login is a first use again — which also means a gallery
admin who points a booth at another server gets that server pinned (the
price of trust on first use). ssh/scp count as SFTP everywhere.

Photobox Sync's own target passwords (`set_sync_config`) are sealed the same
way since 17.11.2026, to a key of that program (`app/sync_secrets.py`,
`sync/photobox_sync/sealing.py`, L-S2).

## A password belongs to one server (rule B2)

* Each stored password carries `password_for` = `type|host|port|user`
  (`photobox_sync.targets.endpoint_key`, usual port filled in, host lower
  case). It is set whenever a password is typed (gallery form, booth form).
* A change of type, host, port or user without a new password — from a
  gallery job, an MCP job, the booth's own form (the "••••" dots) — deletes
  the stored password, forgets the old server's pinned SFTP key and sets
  `awaiting_password`. A new target from the gallery or MCP without a
  password waits too. At the booth, a new server with an empty password
  field and nothing stored before needs no password (anonymous FTP, key
  file).
* A waiting target, or one whose endpoint does not match `password_for`
  (local.json edited by hand, a bug), is built as `AwaitingPasswordTarget`:
  no connection, uploads raise a temporary error, the queue keeps every
  file, the check says "Wartet auf das Passwort …". The booth's "Verbindung
  testen" puts the stored password back only for the same endpoint.
* The wait ends when a password for the new endpoint arrives (sealed from
  the gallery form, or typed at the booth). The gallery card, the booth
  card and MCP `get_second_target` show "Wartet auf das Passwort".

## The folder

Rule and placeholders: `app/zielordner.py` (shared byte for byte with
`sync/photobox_sync/zielordner.py`). Empty = the login's start folder
(an FTP-fed proofing gallery: usually the gallery's folder); a leading `/` = from the top of the
server; otherwise relative to the start folder; made when missing, nested;
no `..`, no control characters, no unknown `{…}`. Placeholders, filled per
file: `{event}`, `{event_name}`, `{datum}`, `{jahr}`, `{monat}`, `{tag}`.
"Alles in einen Ordner" (flat): every file directly there — FTP too now;
off: `<folder>/<event>/<YYYY-MM-DD>/fotos|originale/`. Both cards show an
example path under the field.

Planned, not built: a per-event override of the folder and on/off in the
event form. The design stays open for it — the folder is already resolved
per file from the item's event slug and name, so an override would be one
more lookup by `event_slug` before `zielordner.parts`.

## Check

"Ziel prüfen" queues `check_second_target`. The booth builds the stored
target, runs its `check()` (login; the folder up to its first placeholder
made if missing; a small file written and deleted, so "in Ordnung" means an
upload can land), strips the password from any error text, turns
it into the same plain sentences as the booth card (`ziel_fehler_text`),
passes it through `redact.text` and keeps `{ok, message, at}` in local
`second_target_check`. The heartbeat carries it; the card shows "Letzte
Prüfung: in Ordnung / fehlgeschlagen · time · sentence". A check started in
the booth's own card for the stored target updates the same record.

## What the gallery shows

Enabled, type, host(:port), user, folder, "Passwort gespeichert: ja/nein",
last check with time — never the password — plus "Uploads je Ziel" (sent /
waiting / failed per target from the heartbeat's `upload_targets`) and the
last three jobs of the card. The heartbeat's `second_target` passes
`app/second_target.clean_report`: known keys only, so a password a booth
might send is dropped. Admin only, CSRF (the blueprint's guard), German and
English through `t()`, 44 px controls, one column below 768 px.

## Everything reaches the target

Before 17.11.2026 the second target got the same queue entries as the
gallery: `collage_upload = photos` kept the collage sheet from it,
`upload_originals = false` kept the camera originals from it, print sheets
never went anywhere, and files made before the target was set up (or while
it was off) were never queued for it.

Now a network target (`ftps`, `ftp`, `sftp` in local `targets`) gets every
file the booth keeps of a run: photo (web and full size), collage sheet and
its singles, camera originals, GIF with its MP4 and boomerang, boomerang and
video; named by the event's rule (`event_datetime` gives
`<Event>_<date>_<time>[_n]`, in flat mode web copies get `-web`). Its own two
choices, decided per file at upload: `originals` (default on; off = web
copies and films only) and `print_sheets` (default off; `…-druck.jpg`).
Deleted runs, "Nur drucken" and the data-minimal mode send nothing, as
before. Posters and thumbnails are not sent. Files that wait while the
target is down stay queued and go when it answers (unchanged). In flat mode
(an FTP-fed proofing gallery) the folder field is not used, as before.

"Bisherige Aufnahmen dieses Events nachsenden" (booth card, gallery card,
job `backfill_second_target`) queues once, for the current event only, the
files the other targets were given and the target does not have, and for
runs whose queue rows have aged out the library's main file, its moving
version and (when chosen) the print sheet. Never automatically, never
another event, never a file twice.

## MCP

`get_second_target`, `set_second_target`, `check_second_target`, all with
the right `secure`. `set_second_target` changes only the fields it names
(the rest is taken from the booth's report). Path, flat, enabled, originals
and print_sheets freely; type, host, port and user too, but then rule B2
applies: the booth deletes the password and waits, and the answer says the
owner must type the password in the gallery form or at the booth. Never a
password (any `password*` argument is refused) and never an SFTP host key
(no tool; the job is not in `send_booth_job`). Each call is one change-log
line with the assistant's name and the fields.

## Old and new

* Old booth, new gallery: no `second_target` in the heartbeat, so the hint
  card says the version is too old, the route refuses, nothing is queued.
* New booth, old gallery: the old gallery drops the unknown heartbeat keys
  and never sends the jobs; the booth card works as before.
* Both ways edit the same entry; the booth card shows a change from the
  gallery after its reload, and its own save writes the whole list back.

## Who could do what (security review, 17.11.2026 evening)

| Attacker has | Can | Cannot |
|---|---|---|
| (a) a copy of the gallery database | see host, user, folder, type, "password stored", the last check sentence, the change log and the booth's public key; see a sealed password only in a set job not yet answered (at most 10 minutes) | read the password |
| (b) a gallery admin session | point any paired booth's second target at their own server and type a password for it, so new pictures (and with "nachsenden" the event's earlier ones) go there; remove the target; trigger checks; accept a new SFTP key the booth reported | read the stored password; make the booth send the stored password to another server (B2); set a key file |
| (c) the assistant's MCP token with `secure` | change folder and switches; change host/user/type/port — the target then waits and sends nothing until the owner types a password | set or read a password; accept a server key; make an old password go to a new server |

Every change of (b) and (c) is in the change log and on the card.

## Differences from the earlier design

* Sealing in the request handler, not in the browser (the brief's choice):
  one implementation, no JavaScript crypto; the clear password exists only in
  the memory of that request.
* (17.11.2026 evening) The switch is gone; the public key is always published; rule B2 protects the password.
* Two upload choices (`originals`, `print_sheets`) and the backfill job were
  added for the owner's "everything must reach the second target".
* The audit line goes to the booth log and, as an `info` entry, to the
  gallery's device log; the gallery's change log names the action.

## Not verified

* Not run on the Windows booth or with a built exe: whether PyInstaller
  packs libsodium with the added hidden imports is an assumption based on
  paramiko already needing it.
* No real FTP/SFTP server was used; checks and uploads were tested against
  unreachable addresses and fakes.
