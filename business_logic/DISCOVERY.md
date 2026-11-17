# Discovery — what was read, and what was not

Date: 2026-11-11 · Branch `ecosystem`, commit `1fd8505` · Method: read the
code for what it does (phase 1) and list the gaps (phase 3). No rule on these
pages was invented; where something was not read, the page says so.

A German original of this inventory ("Photobox — Geschäftslogik:
Bestandsaufnahme und Lücken") is kept by the maintainer outside the repository.

## Inventory

202 HTTP entry points in two programs, plus four things that run without a
call from outside.

| Part | File | Entry points | What for |
|------|------|--------------|----------|
| Gallery · guests | `app/blueprints/public.py` | 34 | Gallery, password, downloads, ZIP, slideshow, kiosk, share picture, legal texts |
| Gallery · admin | `app/blueprints/admin.py` | 56 | Login, second factor, events, approval, deletion log, settings, statistics, devices, change log |
| Gallery · managing a booth | `app/blueprints/admin_booth.py` | 23 | Booth profile, jobs, remote sync maintenance, pairing, software, templates, media, QR target, domains |
| Gallery · old interface | `app/blueprints/api.py` | 11 | Upload of the old Windows uploader, guest upload, heart, mark, old heartbeat, notification |
| Gallery · booth interface | `app/blueprints/booth_v2.py` | 12 | Sync, upload, guest mail, events of the booth, pairing, QR page of a run |
| Gallery · share links | `app/blueprints/sharing.py` | 9 | Links with passphrase and expiry, watermark, access requests |
| Booth | `booth/boothcore/app.py` | 57 | Run, live view, print, kiosk gallery, lock, admin behind the PIN |

Without a call from outside: the booth's sync (every 30 seconds,
`sync/photobox_sync/agent.py`), the upload loop with one queue per target
(`outbox.py`), the gallery's daily maintenance (`python -m app.maintenance` —
run by the operator as a scheduled task) and the database
trigger that holds back new items while approval is on (`app/ecosystem.py`).

The gallery's data model had 28 tables at `1fd8505`. The load-bearing ones:
`events`, `media` (unique by id and variant), `devices`, `device_jobs`,
`device_event_history`, `share_links`, `deletion_log`, `admin_vorgaenge`. The
booth keeps a small library of its own (`booth/boothcore/library.py`) with
print counters and deletion slips. The commits after `1fd8505` added tables
(deleted ids, deletion confirmations per device); this inventory does not
count them.

## Ranking

**Tier 1 — money, privacy or the evening depends on it. Documented:** the five
pages listed in [index.md](index.md).

**Tier 2 and 3 — not documented:** listed at the end of [index.md](index.md).

## What the pages themselves say they did not read

* Page 1: what happens to a running run when a new profile arrives; how
  "again" behaves while the first print is still running (read in part).
* Page 2: whether `restart_app` reports its own result before restarting.
* Page 5: the print queue itself (`printing/service.py`, `spool.py`); whether
  the allowance comes back when a queued print is dropped with its run;
  whether the older paper calculation (`media_left`) is still shown anywhere.

## What nobody has checked

* None of this has run against a real camera, a real printer or the kiosk PC.
* The pages have not been re-read against the code after `1fd8505`; see
  "The pages are behind the code" in [index.md](index.md).
* The entry-point counts above are those of `1fd8505` and were not recounted.

## Keeping this alive

A change that alters a rule updates the page and the register in the same
commit. `tests/test_business_logic_docs.py` only guards the skeleton — that
the files exist, that the register names every gap, that `AGENTS.md` points
here. It cannot tell whether a page is true. That stays a review question.
