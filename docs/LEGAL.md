# Legal pages: Impressum and Datenschutzerklärung

For operators. What the gallery ships, what you have to fill in, and what the
software does with personal data so that your privacy page can say so. This is
help for drafting, not legal advice. Where the law is unsettled it says so.

## The short version

1. Open **Admin → Einstellungen → Rechtliches**.
2. Fill in at least: *Betreiber*, *Anschrift*, *Kontakt-E-Mail*,
   *Hosting-Anbieter*, *Zuständige Aufsichtsbehörde*, *Server-Protokolle*.
3. Leave the two big text fields empty unless you want your own text. Empty
   means: the pages are built from the facts above, and the privacy page names
   the deletion date of the gallery it was opened from.
4. Open `/impressum` and `/datenschutz` once while logged in and read them.

Until every placeholder is gone, guests see one neutral line ("Der Betreiber hat
diese Seite noch nicht ausgefüllt") instead of the page, the dashboard shows a
red notice, and the page shows you, and only you, what is missing.

## What counts as a placeholder

Anything in square brackets that starts with a capital letter and is not a
Markdown link: `[Name des Betreibers]`, `[OFFEN: Rechenzentrum]`,
`[PRÜFEN: Rollenverteilung]`, `[TODO]`. This applies to your own texts too, so
you can paste a draft with open points without it going live by accident.
`[Impressum](/impressum)`, `[x]` and `[1]` are not placeholders.

## The fields

| Field | Goes into | Notes |
|---|---|---|
| Rolle des Betreibers | Datenschutz | *Verantwortlicher* (your own galleries and customers) or *Auftragsverarbeitung* (you run the gallery for one customer who decides about it). For photo booth rentals the honest answer is often "it depends on the contract" — see below. |
| Betreiber | both | Name or company exactly as in your other Impressum. |
| Anschrift | both | A real address (ladungsfähig), no PO box. A comma separates the lines. |
| Kontakt-E-Mail | both | Where guests ask for access, deletion or objection. |
| Telefon | Impressum | Optional. § 5 DDG asks for a quick electronic contact plus e-mail; a phone number is the usual second channel. |
| Registereintrag | Impressum | Only if you are in a register (HRB, HRA, VR, PR). |
| Umsatzsteuer | Impressum | USt-IdNr. or Wirtschafts-IdNr. if you have one (§ 5 Abs. 1 Nr. 6 DDG). Small businesses often write "Kleinunternehmer nach § 19 UStG". The Wirtschafts-IdNr. is being issued by the BZSt in waves since late 2024 — check your letters. |
| Verantwortlich nach § 18 Abs. 2 MStV | Impressum | Only for journalistic-editorial content (blog, magazine). A photo gallery is normally not. Leave empty, and the line is not printed. |
| Verbraucherstreitbeilegung | Impressum | Required from more than ten employees (§ 36 VSBG). Do **not** link the EU ODR platform: it was switched off on 20.07.2025 (Regulation (EU) 2024/3228); an old link can be read as misleading. |
| Auftraggeber | Datenschutz | Only with *Auftragsverarbeitung*. |
| Hosting-Anbieter | Datenschutz | Company and seat; you need a data processing agreement (AVV) with them. |
| Externer Speicher | Datenschutz | Optional, e.g. your NAS that receives the originals. |
| E-Mail-Versand | Datenschutz | Optional: whose mailbox sends notifications and booth mails. |
| Zuständige Aufsichtsbehörde | Datenschutz | The data protection authority of the state where you are based, with address. |
| Server-Protokolle | Datenschutz | Whether your reverse proxy or web server logs requests with IP address and for how long. The gallery itself writes no access log (gunicorn's access log is off); check your proxy's settings. |
| KI-Assistent | Datenschutz | Only if you connect the admin to an assistant over MCP (see `MCP.md`): name the service, provider and seat. With the base rights (`read`, `manage`) that provider sees administrative data (event and customer names, settings, counts) — not photos; open access requests to password-protected galleries (guest name, note, e-mail address shortened) are included; deciding them needs `photos` (since the final review of 15.11.2026). Each extra right you tick for an access widens that, and the template says so: `photos` — guest pictures (view, delete, and whether a picture came from a guest, the operator or a booth), creating share links and reading their addresses, deciding access requests (granting mails the link that opens without the password), publishing galleries and setting their password, the e-mail addresses of guests subscribed to notifications, and the booth's clean-up (it deletes pictures on the booth's disk; second review R2-N1); `control` — drive a booth live, restart the program, lock the screen, take a test photo (R2-U2), including a small camera live image (whoever stands in front of it) and a kiosk screenshot (both refused while a guest is at the booth), restart or shut down the booth PC and switch the kiosk language, only if remote control is also allowed at the booth; `secure` — seeing and changing where QR codes, mails, operator warnings and webhooks lead (R2-N2/N3) and which event a booth gives its pictures to, the imprint and privacy texts (`set_legal_texts`), plus open pairing requests with the booth's IP address; `updates` — install booth software. Secrets (passwords, tokens, keys, the guests' Wi-Fi password) are never returned. Booth log lines it can read carry guest addresses only shortened. The template also says that the gallery does not analyse photos with AI and recognises no faces, and that the assistant's provider processes pictures the assistant sees with `photos`. |

## What the software does with personal data

The privacy template describes exactly this list. If you write your own text,
check it against the list.

| Processing | Data | Legal basis used by the template | Kept |
|---|---|---|---|
| Booth shots in the gallery | photos, videos, run id | Art. 6 (1) f | retention of the event (default 30 days, account default under Settings → Homepage), deletion log as proof; with "nicht automatisch löschen" no date: the privacy page and the booth's consent note say that the operator or the host deletes. Choose that only when the client explicitly wants the gallery kept longer, and delete by hand once the purpose is fulfilled (storage limitation, Art. 5 (1) e) |
| Guest uploads | photos only, no videos (location in EXIF and XMP, camera owner and serial numbers removed in JPEG, PNG and WebP) | Art. 6 (1) f | as above |
| Customer contract | contact and contract data | Art. 6 (1) b | outside the gallery |
| Customer access code | client name, galleries, last login, selections, comments; code only as HMAC | Art. 6 (1) b | until the operator deletes the client |
| Delivery and abuse brakes | IP address in failed-attempt keys | Art. 6 (1) f | 24 hours |
| Gallery statistics | per event: views, downloads, shares — no IP, no id | Art. 6 (1) f | with the event or until reset |
| Likes | random id in the session cookie + photo id | Art. 6 (1) f | with the event |
| "Meine Aufnahmen" | up to 20 run ids in the session cookie | — (browser only) | until the browser is closed |
| New-photo e-mail | address, token, confirmation time | Art. 6 (1) a, double opt-in | unconfirmed: 7 days; otherwise with the event |
| Photo e-mail at the booth | address | Art. 6 (1) b | gallery: not stored; booth queue: one day after sending, 14 days if undeliverable |
| Access request for a protected gallery | name, e-mail, message, language | Art. 6 (1) f | decided requests: 90 days |
| Share links | views/downloads, keyed hash of the IP, browser and OS | Art. 6 (1) f | 90 days |
| Admin change log | admin's shortened IP | Art. 6 (1) f | 365 days |
| Booth device log | events of the booth; a guest address only shortened (`a***@example.org`), no secrets — masked on the booth and again in the gallery | Art. 6 (1) f | 30 days |
| Umami (only if configured) | page, referrer, country, browser, OS, device, language; a session hash from IP + UA, IP not stored | Art. 6 (1) f | per Umami's settings |
| Messenger notifications (only if configured) | event name, gallery link, client name | Art. 6 (1) f | at Telegram/Discord |

Not loaded from other servers: no web fonts, no maps, no CDN scripts. The only
exception is the Umami script if you configure one. Umami is never loaded on
pages whose address is a key (`/p/…`, `/s/…`, `/l/…`, `/g/…`, `/notify/…`), and
query strings and fragments are never reported.

Cookies: `session` (HttpOnly, SameSite=Lax, Secure behind HTTPS; ends with the
browser for guests), `pbshare_<slug>` (6 hours, after a share-link passphrase).
Local storage: `pb-theme`, `pb-dl-size`, `pb-bewertung-<slug>`. All strictly
necessary for a function the visitor uses (§ 25 Abs. 2 Nr. 2 TDDDG), so no
consent banner. If you add a tracking service that sets cookies, that changes.

## Who is responsible for the photos at a private party

There is no settled answer, and the template does not pretend there is one.
A defensible reading:

* For the **gallery service** (hosting, links, deletion periods, security) you
  decide purpose and means: you are controller (*Verantwortlicher*).
* The **host** (the couple, the company) decides that a booth is at the party
  and who gets the link. Courts and authorities have not settled whether that
  makes the host a joint controller (Art. 26), a separate controller, or the
  controller with you as processor (Art. 28).
* A purely private wedding may fall under the household exemption for the
  couple (Art. 2 (2) c GDPR); it does not cover you, because you act
  commercially.

Whatever you choose, write it into your rental contract and set the role here
to match. For images of people the relation between the GDPR and the German
KUG (§§ 22, 23) is also unsettled; the template relies on Art. 6 (1) f and
offers objection and self-deletion.

## Before you go live

* Conclude the AVV with your hosting provider (Hetzner: account → *DPA* /
  *Auftragsverarbeitung*, choose data types and data subjects, tick the box).
* Check whether your mail provider needs an AVV too.
* Keep a record of processing activities (Art. 30) — the table above is a
  start.
* Leave `allow_delete` on in the booth profile (default) so guests can remove
  their own run from the page their QR code opens; the privacy page mentions it.
