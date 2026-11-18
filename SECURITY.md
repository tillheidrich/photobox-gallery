# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting
(*Security → Report a vulnerability* in
<https://github.com/tillheidrich/photobox-gallery>) with:

* what you found and where (file, route, version from `/ueber`),
* how to reproduce it, ideally with the smallest possible example,
* what an attacker could actually do with it.

You will get a first answer within a few days. This is a project run alongside a
day job — that is the honest expectation, not a service level agreement. Fixes
for anything that exposes guest photos or lets someone take over a gallery jump
the queue.

## Supported versions

There is one line of development. Security fixes go into the `ecosystem`
integration branch first and reach `main` (the release branch) with the next release; there are no backports to older releases.

| Version | Supported |
|---|---|
| the latest release (see [`VERSION`](VERSION) and `/ueber`) | yes |
| `ecosystem` (integration branch) | yes, fixes land here first |
| anything older | no — update first, then report if it still happens |

If you run a modified version, tell us what you changed; a finding that only
exists in a fork belongs with the fork.

## What is in scope

* The gallery (`app/`): authentication, share links, share-code signatures,
  file serving, uploads, the booth API `/api/v2/booth/*`, the booth status
  page `/status/<token>`.
* The booth (`booth/`): its local HTTP API, the admin PIN, the sync agent.
* The sync package (`sync/`): upload targets, credential handling.

## What is not

* Anything requiring physical access to an unlocked booth PC. The booth trusts
  the machine it runs on; that is a deliberate trade-off for a device standing
  in a venue.
* Self-hosted deployments configured against the documentation — for example a
  gallery served over plain HTTP, or a device key shared between events.
* Denial of service by uploading very large files from an authenticated booth.

## What we do on our side

* Guest photos are served from an allowlist of folders; the database file and
  configuration are never reachable over HTTP. (This was a real bug, found and
  fixed on 17.09.2026 — it is why the allowlist exists.)
* Share links are random ids signed with the event key, so guessing an id
  without the signature gets you a 404, not someone else's photo.
* The booth status page `GET /status/<token>` is off per booth until the
  operator switches it on; the token is 192 random bits, only its SHA-256 is
  stored, a new link or switching off ends the old one, and any other token
  gets the ordinary 404. Whoever holds the link sees: the booth's name, a
  traffic light with one sentence, time since the last contact, camera and
  printer ready or not (with paper lines), today's sessions and prints, and
  notices about camera, printer and disk. Never the event's name, photos,
  gallery links, e-mail or IP addresses, keys or paths (booth text that looks
  like one is dropped). Read-only, `no-store`, `noindex`, `no-referrer`, no
  analytics. A deactivated booth has no status page. The token is looked up
  first: a valid link is served without counting against any limit (behind a
  reverse proxy all visitors may share one address); only a token that opens
  no page counts, 120 per address in 15 min, after which such requests get
  429 instead of 404.
* Device keys are scoped to their own events: a key lists, selects, uploads to
  and mails from only the events its booth is or was assigned to (since
  15.11.2026; before that, listing and selecting were open to any key). Keys
  are stored as SHA-256 only and shown once.
* Booth admin actions are behind a PIN and a short-lived token; remote jobs come
  from a fixed list, and no job takes a command line. **But the gallery is
  trusted with code:** it can offer a new booth version and send the jobs
  `fetch_update` and `install_update` (through MCP only with the extra right
  `updates` and a two-step confirm); a booth that gets them runs that file. What protects this path
  today: the offer must carry a SHA-256 the download is checked against (no
  hash, no update), an equal or older version is refused (no downgrade), the
  device key is sent only to the paired gallery's own host, and redirects are
  not followed. What is **not** there yet: signed updates. Whoever controls
  the gallery's admin (or the server) can therefore ship code to its booths.
  Signing the builds with a key that is not on the gallery is open work.

## Protection class K3

Class **K3** under the HD security standard for app projects (OWASP Top 10:2025
as outline, ASVS 5.0 level 2 as yardstick). Reason: the gallery is public
(guests open it from a QR code), it holds personal data of third parties
(guest photos, guest e-mail addresses), it holds secrets (device keys, SMTP
login, the MCP door to the whole administration, upload targets), and one
installation serves several customers' events. Classified on 15.11.2026
together with the fixes S-1 … S-13 of security review G3.

### Data

* Guest photos and videos (gallery `DATA_DIR`, booth `sessions/`, optional
  USB stick and NAS offload). Faces, so personal data; never biometric
  processing (AGENTS.md rule 4).
* Guest e-mail addresses for "photo by mail" (booth queue, gallery outbox).
* Customer accounts (client access to held galleries), the admin account,
  device keys, the static MCP key and OAuth grants, SMTP and upload-target
  credentials.

### Who may do what

* **Guest:** sees their own run through the signed QR code; sees a gallery
  only if it is published or they know its password. No account.
* **Customer (client):** sees and selects in the galleries assigned to them.
* **Booth (device key):** uploads to, lists, selects and mails from only its
  own events; the key is stored as a hash and shown once.
* **Admin:** everything; password plus optional second factor; sessions end
  on logout (server side) and on a new password.
* **Assistant (MCP):** what its scope allows. A new access gets `read` and
  `manage` — no guest photos or addresses, no mail/webhooks/access/QR
  target, no software, no live control, and (since the final review) no
  change of who sees or adds pictures: publishing, a gallery password,
  visibility dates, guest upload, access requests and a booth's event need
  `photos` resp. `secure` as well. Since 15.11.2026 four extra rights
  open those (`control`, `photos`, `secure`, `updates`); each is ticked by
  the admin with the password typed again, logged, withdrawable per access.
  Secrets are write-only through MCP; deleting, signing off, quitting and
  installing are two-step; a booth obeys live control only when its operator
  allowed it at the booth (default off). See `docs/MCP.md`.

### The worst that can happen

1. Guest photos of one event reach someone who was not there (other
   customers, the public, a stranger with a stolen key or a USB stick).
2. The gallery's mailbox is used to send mail of someone else's choosing.
3. Booths run code that the operator did not build (update path).

### Acceptance tests (A01/A07)

| Nr. | Test | Expectation | Automated |
|-----|------|-------------|-----------|
| 1 | Booth A lists, selects, uploads to, mails from booth B's event | 404/403, nothing changes | `tests/test_booth_event_ownership.py` |
| 2 | Phone pairing confirmed for an existing box | the old key dies, the requester gets a fresh key once | `tests/test_pairing_by_phone.py` |
| 3 | Booth mail with foreign text, several recipients, foreign run | text from the gallery, 400/404/429 | `tests/test_guest_mail.py` |
| 4 | Reset or pairing link with a forged Host header | refused | `tests/test_link_base_and_proxy.py` |
| 5 | Database copy | holds no device key and no MCP key in plain text | `tests/test_device_keys_hashed.py`, `tests/test_mcp_key_hashed.py` |
| 6 | Copied admin cookie after logout | no login | `tests/test_admin_session_ends_on_logout.py` |
| 7 | New admin password | ends other sessions, the static MCP key and OAuth grants; offers renewing all device keys | `tests/test_mcp_key_hashed.py`, `tests/test_mcp_oauth.py`, `tests/test_device_keys_hashed.py` |
| 8 | Unknown USB stick at the booth | gets nothing | `booth/tests/test_usb_backup_only_to_trusted_sticks.py` |
| 9 | Update offer without hash, older version, foreign host | refused, key not sent | `booth/tests/test_updater.py` |
| 10 | MCP access without extra rights calls a `control`/`photos`/`secure`/`updates` tool | 403, nothing happens; default access sees only the old tools | `tests/test_mcp_extra_rights.py` |
| 11 | Extra right without the admin password; destructive call with a wrong, foreign, reused or expired confirmation | nothing granted / nothing done | `tests/test_mcp_extra_rights.py` |
| 12 | Any tool answer, with every right | contains no PIN, password, token, key | `tests/test_mcp_extra_rights.py` |
| 13 | Control job for booth A fetched or answered by booth B; control job at a booth that does not allow it | B sees nothing, A's answer stays A's; the booth refuses | `tests/test_mcp_extra_rights.py`, `booth/tests/test_remote_control_at_the_booth.py` |
| 14 | Access with `read manage` publishes a gallery with its own password, moves its dates, opens guest upload or access requests, decides an access request, points a booth at an event | 403, nothing changes | `tests/test_mcp_visibility_needs_photos.py`, `tests/test_mcp_access_request_needs_photos.py`, `tests/test_mcp_final_review.py` |
| 15 | Access with `read` reads booth diagnostics after a failed guest mail | no full guest address, no secret; marked as untrusted text | `tests/test_device_logs_carry_no_guest_address.py`, `booth/tests/test_logs_carry_no_guest_address.py` |
| 16 | Script, event handler, `javascript:`/`data:` link in imprint, privacy text or FAQ (admin form or MCP `set_legal_texts`) | shown as text, no active content | `tests/test_markdown_is_escaped.py` |
| 17 | Another site frames a gallery whose event does not allow embedding | `frame-ancestors 'self'`, `SAMEORIGIN` | `tests/test_embedding_needs_the_switch.py` |

### Threats added with the extra MCP rights (15.11.2026)

* **Prompt injection through data.** File names, greetings, client names,
  booth log lines and a pairing request's user agent reach the assistant in
  tool answers. Text there can try to instruct it. Mitigation: those tools
  say in their description that the answer is untrusted data, destructive
  tools need a second call with a confirmation the human sees, every call
  beyond `read` is in the change log. Residual: an assistant that follows
  injected text *and* a human who confirms without reading.
* **A stolen token with `control`.** Can start runs, print up to the
  allowances, lock or quit a booth — only where remote control is allowed at
  that booth, at most 30 jobs per booth and minute, jobs expire after 60 s
  undelivered, every call logged with the assistant's name. Ends with
  *Zusatzrechte entziehen*, *Remove*, or a new admin password.
* **A stolen token with `photos`.** Can read every event's pictures and
  delete them (two-step, but the thief confirms their own call). Same ends.
  Give `photos` only for the time it is needed.
* **`updates` = code on booths.** Unchanged residual risk of S-4: builds are
  not signed; whoever holds `updates` can install any build the gallery
  stores (hash checked, no downgrade).
* **Re-authentication.** Giving an extra right needs the admin password at
  that moment (brake: 5 failures in 15 min); a stolen admin session alone
  cannot hand a third party extra rights.

### Deliberate exceptions

* The booth's own pages (kiosk, booth admin) have `script-src 'self'` since
  R2-U1; they allow inline *styles* only (`style-src 'unsafe-inline'`): the
  kiosk sets layout values such as `--i` in markup, and a style runs no script.
* `script-src 'unsafe-inline'` in the gallery's content security policy: the templates
  still carry inline handlers (see `app/security_headers.py`). Review when
  the handlers move into files.
* The booth trusts the PC it runs on (see "What is not" above).

### Findings that share a number

The review of 11.11.2026, the security review G3 of 15.11.2026 and the
final review of 15.11.2026 numbered their findings independently, so some
numbers were used twice. Here they are named by number **and** subject:

* **S-17a XMP location in guest uploads** — closed: XMP, EXIF GPS, camera
  owner and serial numbers are removed from JPEG, PNG and WebP
  (`tests/test_upload_metadata_location_and_owner.py`, final review V7).
* **S-17b `create_share_link` with `manage` alone** — closed: making and
  reading share links needs `photos` (`tests/test_mcp_share_links_need_photos.py`).
* **S-18a link key compared in constant time** (review 11.11.2026) — closed
  (`tests/test_sessions_tokens_and_logs.py`).
* **S-19a sanitising operator Markdown** — closed: imprint, privacy page and
  FAQ are escaped and filtered by an allow-list (`app/safe_markdown.py`,
  `tests/test_markdown_is_escaped.py`, final review A5).
* **S-16 login brake per account** (G3) and **S16 first-start log**
  (11.11.2026) are both closed; see `business_logic/index.md`.

### Second review (15.11.2026, numbered R2-…)

An independent second security and publication review. Its findings are
named R2-U… (urgent), R2-N… (normal) and M… (publication).

* **R2-U1 Kiosk XSS through the booth profile** — closed. A profile entry
  (`modes`, `looks`, `formats` …) written with `manage` reached an HTML
  attribute in the kiosk and ran script on the booth's own origin, which can
  read guest pictures. Now: one set of profile rules on both sides
  (`app/profile_rules.py`, refused in the gallery, dropped and logged on the
  booth), escaping at every kiosk sink with a static check
  (`tools/check_kiosk_escaping.py`), and a Content-Security-Policy without
  inline script on the kiosk and the booth admin pages. On the gallery side a
  folder name from a sync PC could break out of an inline handler on the sync
  page; closed the same way.
* **R2-U2 Live jobs through `send_booth_job`** — closed: `restart_app`,
  `lock`, `test_capture` need `control` (live control) and the booth refuses
  them while a guest's run is open or a print is on its way.
* **R2-U3 Redaction gaps** — closed: IDN/unicode and `%40` addresses, Wi-Fi
  QR passwords, "PIN 123456", "Passwort ist …", short pairing codes; the sync
  program redacts too.
* **R2-U4 Upload metadata** — closed: appended images/videos (MPF, Motion
  Photo) are cut, IPTC and comments removed, animations keep all frames.
* **R2-U5 ReDoS in operator Markdown** — closed: linear link parsing, 20,000
  characters, cached rendering.
* **R2-U6 `TRUST_PROXY=1` in the template** — closed: the template says 0,
  the NAS port binding is an explicit choice (`BIND_ADDR`).
* **R2-N1 Clean-up deleting unsent runs** — closed: `photos` for the setting
  through MCP; a paired booth deletes unconfirmed runs only with a yes in its
  own `local.json`.
* **R2-N2/N3 Where data goes** — closed: webhook URL, mail settings, operator
  warning address, Wi-Fi name and QR targets are masked without `secure` and
  can be changed only with `secure`.
* **M1/M2 Publication** — closed: no real mail host or login in the tree; the
  snapshot script runs on macOS and checks an external deny-list.

Still open after this review: **S-4 signed booth updates** (see below).

### Gaps closed on 17.11.2026 (branch `fix/luecken-1711`)

| Finding | Severity | Status | Date | Pinned by |
|---|---|---|---|---|
| **L-T1 Uploaded templates barely checked** — an upload under "Medien & Vorlagen" was asked only for `id` and `slots`, MCP `add_asset` for nothing, and the booth loaded template files raw (`compose.load_template`); `overlay` could name a path below the media folder | medium | closed: one gate (`vorlagen_kern.annehmen`) at every door and at every load at the booth; refused files are skipped with the reason | 17.11.2026 | `tests/test_templates_pass_one_gate.py`, `booth/tests/test_booth_loads_templates_through_the_gate.py` |
| **L-S1 SFTP accepted any host key** (A-3c) — anyone between booth and NAS could pose as the server and read the password | high | closed: trust on first use, then pinned; a different key fails closed before any credential or file is sent; explicit acceptance of exactly the shown fingerprint (at the booth; from the gallery only with the booth's opt-in) | 17.11.2026 | `sync/tests/test_sftp_host_key_is_pinned.py`, `booth/tests/test_second_target_host_key_at_the_booth.py`, `tests/test_second_target_host_key_in_the_gallery.py` |
| **L-S1b FTPS certificate and host name** — said to be checked, never tested | low | confirmed by a test against a local FTPS server with a wrong-name certificate from a trusted test CA: refused before the password; the right name logs in | 17.11.2026 | `sync/tests/test_sftp_host_key_is_pinned.py` |
| **L-S1c Plain FTP** — sends user, password and pictures in clear | low (operator's choice) | labelled "ohne Verschlüsselung – … lesbar" with a warning in booth admin, gallery card, gallery sync page and Photobox Sync | 17.11.2026 | `booth/tests/test_second_target_host_key_at_the_booth.py`, `tests/test_second_target_host_key_in_the_gallery.py` |
| **L-S2 Photobox Sync target passwords in clear in the gallery DB** — `set_sync_config` stored them in `device_jobs.payload_json` for good | high | closed: sealed in the request handler to the program's own key, payload emptied on answer, expiry and replacement, refused for versions without a key, old rows blanked at start | 17.11.2026 | `tests/test_sync_target_passwords_are_sealed.py`, `sync/tests/test_sealed_target_passwords.py` |


Left as it is: backups taken before this update still contain the clear
`set_sync_config` passwords of that time (the clean-up cannot reach into an
archive) — delete or keep them as carefully as the passwords themselves, and
change those target passwords if a backup may have left the operator's
hands. A gallery admin session (or an attacker in it) can still point a
booth at another server (since 17.11.2026 evening without any switch at the
booth, see the section below); that server's key is then pinned on first use
(the trade-off of trust on first use, see
`docs/SECOND-TARGET-FROM-THE-GALLERY.md`).

### Second target without the booth's opt-in (17.11.2026, evening, branch `feat/second-target-open`)

The owner removed the switch "Zweites Ziel aus der Galerie einstellen
lassen": every paired booth takes its second target (FTP/FTPS/SFTP)
from the gallery. The orchestrated compromise, as implemented:

| Rule | What it means | Enforced where | Pinned by |
|---|---|---|---|
| **B1** Gallery admin form | A logged-in gallery admin (session + CSRF) may set every field of the second target and type a password; the password is sealed to the booth's key in the request handler, never stored, logged or pre-filled; empty keeps | `app/blueprints/admin_booth.py::sync_second_target`, `app/second_target.py` | `tests/test_second_target_from_the_gallery.py` |
| **B2** A password belongs to one server | When type, host, port or user change from any source (gallery form, MCP, booth form, booth "Verbindung testen") and no new password comes with the change, the booth deletes the stored password and the old server's pinned SFTP key and sets `awaiting_password`; uploads to that target wait (queue kept, `AwaitingPasswordTarget` connects nowhere) until a password for the new server is typed in the gallery form or at the booth. Each stored password carries `password_for` (`type|host|port|user`); a target whose endpoint does not match it is never given the password, even if local.json was changed by hand or the gallery is wrong or hostile. The booth check substitutes the stored password only for the same endpoint | the booth: `booth/boothcore/second_target.py::bind_password`, `booth/boothcore/app.py` (config save, targets/check), `sync/photobox_sync/targets.py` (`endpoint_key`, `needs_password`, `build_extra_targets`) | `booth/tests/test_second_target_password_follows_the_server.py` |
| **B3** Assistants (MCP, right `secure`) | `set_second_target` changes only the fields it names; path, flat, enabled, originals, print_sheets freely; type/host/port/user too, but then B2 applies and the answer says the owner must type the password. MCP can never set a password (any `password*` argument refused) and never accept an SFTP host key (no tool; `accept_second_target_host_key` is not in `send_booth_job`) | `app/mcp_more.py`, `app/mcp.py::NEEDS_SECURE` | `tests/test_second_target_from_the_gallery.py::test_mcp_*` |
| **B4** Audit | Every change (form or MCP) is one change-log line "Zweites Ziel · Box #id · source: fields; password state" with who and when (`app/protokoll.py`), never the password; the card shows the last three under "Zuletzt geändert"; the booth writes its own line to its log and the gallery's device log | `app/second_target.py::audit_text`, `last_changes` | `tests/test_second_target_from_the_gallery.py::test_every_change_is_in_the_change_log_*` |

What this does not prevent (accepted by the owner): a gallery admin session
(or an attacker holding one) can point a booth's pictures at another server
and type a password for it; an assistant with `secure` can point a target at
another server, but it then sends nothing until the owner types a password.
A target that needs no password at all can be redirected only by the booth's
own form (from the gallery and MCP a new endpoint always waits for a
password). Trust on first use for SFTP keys stays: the first login to a new
server pins its key.
### Link previews (17.11.2026, branch `feat/gallery-preview`)

Every guest page names an Open Graph image `/gallery/<slug>/og.jpg`. A chat
app (WhatsApp, iMessage, Signal, Telegram, Facebook, Slack) fetches it
without cookies and shows it to everybody in the chat, so the decision
(`app/link_preview.py`) never reads the session — the logged-in operator sees
the same card as a stranger, and the endpoint sets no cookie:

| Gallery | Card | Description |
|---|---|---|
| public, published, no password | admin's own image, else the cover, else up to three visible photos, with the title | the event description, else "n Fotos" |
| password (own or the parent's), link password, expired | title and brand colour only — the admin's own image only if "auch ohne Passwort zeigen" is ticked | "Passwortgeschützte Fotogalerie" / "Fotogalerie", never the event description |
| watermark on | title and colour (an unwatermarked photo must not leave), or the admin's image | as public |
| silent mode, draft (or a folder under one), not yet live (`scheduled_live_at` ahead), or a name that does not exist | one neutral card ("Fotogalerie"): same status, bytes and headers as an unknown name (since 18.11.2026; before, a draft's card carried its title) | generic |
| run page `/p/…`, share link `/l/…` | title and colour, never photos | as closed |

A photo counts only while a guest sees it: hidden, pending, red-marked (when
the gallery hides red for guests), deleted or trashed pictures never reach a
card, and a cover that is one of the gallery's pictures is dropped as soon as
that picture is hidden. Cards are cached under `cache/og/` and keyed by
mode, title, colours and the source files; the key travels in `?v=`, so a
change gives a new address (a chat app that cached the old one keeps it until
its own cache ends — that cannot be revoked from here).
Pinned by `tests/test_link_preview.py`, `tests/test_og_hides_unpublished_galleries.py` and the
guard `tests/test_hidden_gallery_is_unknown_everywhere.py`, which walks every
public route with a gallery slug (from the url_map) and compares a draft with
an unknown name byte for byte. Known and kept: the countdown page of a gallery
not yet live (`/gallery/<slug>` with a start ahead) shows its name on purpose.

### Open (as of 15.11.2026, after the final review)

* **S-4 signed booth updates**: only the minimal part (hash, no downgrade,
  key only to the paired host) is done. Whoever controls the gallery's admin
  or holds `updates` can still install any build the gallery stores.
* Admin sessions opened before 15.11.2026 carry no server-side id; they end
  with the next password change, not on logout.
* `script-src 'unsafe-inline'` stays (deliberate exception above); operator
  Markdown no longer reaches a page unescaped.
* **S-15 (G3)**: not re-examined in this round; its description lives in the
  G3 review, not in this repository.
* Windows setup (final review A12): the Authenticode check of the WebView2
  bootstrapper, the subnet-limited firewall rule and "no elevated start from
  `C:\Fotobox`" are checked on the script text only, not run on Windows.
* Remote control and typing at the kiosk (final review A10): the kiosk tells
  the booth when an e-mail, greeting or PIN dialog opens or closes, and the
  booth refuses a screenshot or live image meanwhile and for a few seconds
  after. A kiosk that has not reported lately (an older page, a frozen
  browser) falls back to the earlier guess: the result time plus a minute
  after a run. The report is trusted only from the booth PC itself.
* An independent review against ASVS level 2 before the gallery is offered
  to other operators.
