# Business logic 6 — The booth's own guest page (download without internet)

> Written with the code on 2026-11-12, branch `work/booth-phases`. Kind: entry
> point. Unlike pages 1–5 this one is not a copy of a wiki page; it is in
> English like the rest of the repository.

## 1. Purpose

At many venues there is no internet. The gallery cannot be reached and the QR
code on the result screen leads nowhere until the booth has been online
again. With `wifi_share` on, a guest gets the picture from the booth itself:
"1. join the Wi-Fi", "2. load your photo". Nothing leaves the venue.

## 2. Parties

* **Guest** with a phone in the booth's Wi-Fi.
* **Operator** — puts a Wi-Fi router or hotspot next to the booth, connects the
  booth computer to it, switches the function on in the profile.
* **Booth** — signs the link, serves the page on a listener of its own.

## 3. Preconditions

* The booth does **not** set up a Wi-Fi and changes nothing on the system (no
  hotspot code, no firewall rule). The router or hotspot is the operator's.
* `wifi_share.enabled` and an SSID. `wifi_share.mode`: `auto` (only while the
  gallery is out of reach: the booth is offline, or the run has no gallery
  link), `always`, `off`.
* The booth knows an address in the local network (`guest_share.address` in
  `local.json`, otherwise the first private IPv4 address found) and the second
  listener is bound (`guest_share.port`, default 8766, never the booth's own port).
* The QR code on screen is on (`qr_on_screen`).

## 4. Flow

1. **Result screen.** If the preconditions hold, the run's payload carries
   `local_share`: the Wi-Fi code `WIFI:T:<WPA|WEP|nopass>;S:<ssid>;P:<pw>;[H:true;];`
   (`\ ; , : "` escaped) and the link `http://<address>:<port>/g/<code>`. The
   kiosk shows the two steps in place of the gallery code; each opens large on
   a tap, the Wi-Fi one with name and password as text.
2. **Link.** `<code>` is `<run id>-<expiry, hex unix time>-<signature>`; the
   signature (100 bits) covers run id and expiry and uses a key that is made
   once on the booth (`<data>/guest_share.key`) and never sent anywhere.
   Expiry: `wifi_share.link_hours` after the result was shown (default 24,
   1–168).
3. **Guest page.** `/g/<code>`: the picture or video of exactly this run,
   "Save" (download), for a collage with GIF "See it move", a line on how long
   the link works; German or English after the phone's language. No script, no
   font, no address from anywhere else (CSP `default-src 'none'`).

## 5. Rules

| When | Then |
|------|------|
| Signature wrong, run id changed, expiry changed or passed | 404 |
| `wifi_share` switched off, or data-minimal mode | 404 for every link, also old ones |
| Run is "Nur drucken" or deleted, or its files were cleaned up | 404 |
| Any other address on the guest port: `/`, `/api/…`, `/admin`, `/media/…`, `/assets/…`, `/static/…`, any method | 404, bare text |
| 12 wrong links from one address within 10 minutes | that address gets 429 with `Retry-After` for 10 minutes, valid links included; other addresses are not affected |
| Booth online and the run has a gallery link, mode `auto` | the usual QR code, no Wi-Fi steps |
| Not allowed on the booth itself (`guest_share.allowed`, local, default off, never settable from afar) | nothing opens, whatever the profile says; the self-diagnosis says so |
| Listener not bound or no local address | the usual QR code; the self-diagnosis warns ("Download im WLAN") |
| Data-minimal mode or "Nur drucken" | no local link, as there is no QR code |

The booth's own interface (kiosk, admin, API) keeps its binding (`--host`,
default 127.0.0.1). The guest page is a separate app with no other route on a
separate port; it does not open the booth.

## 6. States

None of its own: a link is valid until it expires, the function is switched
off, or the run is gone.

## 7. Money and allowances

None. Printing is not reachable from the guest page.

## 8. Edge cases

* The password of the Wi-Fi is not part of `/api/state`; it only travels in
  the payload of a run, because the screen shows it anyway (code and caption).
* A booth that changes its address (DHCP) after the result was shown leaves
  older links pointing at the old address. Setting `guest_share.address` or a
  fixed lease in the router avoids it.
* Phones that open a "captive portal" check after joining a Wi-Fi without
  internet may show a notice or switch back to mobile data; not tested.
* Windows asks once whether the program may accept connections on the
  network (firewall) when the listener binds; not tested.

## 9. What is read and written

**Written:** `<data>/guest_share.key` once. **Read:** profile `wifi_share`,
local `guest_share`, the library entry and files of the run.

## 10. Where

| What | Where |
|------|-------|
| Code, link, brake, listener, guest app | `booth/boothcore/guest_share.py` |
| Payload `local_share` | `booth/boothcore/app.py` (`_item_payload`) |
| Listener start | `booth/boothcore/__main__.py` |
| Self-diagnosis | `booth/boothcore/doctor.py` (`check_wifi_share`) |
| Two steps on the result screen | `booth/boothcore/web/static/app.js` (`openResult`, `openQr`) |
| Tests | `booth/tests/test_wifi_share.py`, `tests/test_wifi_share_settings.py` |
