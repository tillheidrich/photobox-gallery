# AGENTS.md — how to work in this repository

Written for AI coding agents, useful for humans. If you are an agent and read
only one file, read this one. It is meant to get you from "I have the repo" to
"my change is right here" without guessing.

## The project in four lines

Self-hosted photo booth software in one repository: a **gallery** (Flask,
SQLite, server-rendered Jinja, vanilla CSS), a **booth** (FastAPI plus a
vanilla-JS kiosk page, runs on the booth PC) and a shared **sync** package
(offline-first upload queue). No build step, no framework churn. Everything a
guest experiences at the booth must work with the network cable pulled.

## Repository map

```
app/                 Gallery (Flask). Blueprints in app/blueprints/,
                     templates/ and static/ next to them.
  ecosystem.py       Everything the booth talks to: device profiles, assets,
                     jobs, device logs, share codes, QR/UTM.
  media.py           Image/video handling, download sizes, thumbnails.
  db.py              SQLite schema and migrations (additive, never destructive).
booth/boothcore/     Booth. app.py holds the Booth class and the HTTP API.
  camera/            Backends: edsdk, digicamcontrol, gphoto2, webcam, folder, mock.
  printing/          Backends: windows, cups, ipp, hotfolder, folder.
  media/             compose (templates, print cards), looks, luts, chroma, boomerang.
  delivery/mail.py   E-mail queue. diagnostics.py: log buffer. webhooks.py.
  web/               The kiosk page and the booth admin page (no build step).
sync/photobox_sync/  Agent (heartbeat, profile, jobs), outbox, targets.
packaging/windows/   PyInstaller specs and the one-click build script.
business_logic/      What the software does, as rules: five pages, a glossary and
                     the register of known gaps. Start at index.md.
tests/, booth/tests/, sync/tests/   Three suites, three roots.
```

Longer background documentation is kept by the maintainer outside this
repository. This file, `docs/`, `business_logic/` and the code comments are the
contract; nothing you need to make a change lives elsewhere.

One exception travels with the code: `business_logic/index.md`. It lists the
rules the booth and the gallery follow (when a deleted id is refused, when a
remote job expires, which gate closes a gallery first) and every gap the last
audit found, open or closed. Read the page for the area you change before you
change it; if your change closes or opens a gap, update the register in the
same commit.

## Setup and the loop you should run

```bash
pip install -r requirements.txt -c constraints.txt
(cd booth && pip install -r requirements.txt -c ../constraints.txt)   # -e ../sync is cwd-relative
python -m pytest tests -q          # gallery   (must stay green)
cd booth && python -m pytest tests -q
cd sync  && python -m pytest tests -q
```

The booth runs without hardware: `BOOTH_CAMERA=mock python -m boothcore`. Use
it — a change that only works with a real camera attached cannot be reviewed.

For a change that crosses the booth/gallery boundary, run the rehearsal — it
starts a real gallery and a real booth on this machine and walks the path a
guest takes, with the mock camera:

```bash
python tools/rehearsal.py          # ~25 s, 15 steps, needs no hardware
```

Before you push, run the same check the pipeline runs:

```bash
python tools/check_hygiene.py    # vendor binaries, credentials, version drift
```

CI (`.github/workflows/tests.yml`) runs the three suites and that script on
every pull request. It is your feedback loop — if it is red, the change is not
finished, whatever the diff looks like.

## Hard rules

These are not style preferences. A pull request that breaks one of them is
closed, however good the code is.

1. **No Canon EDSDK in the repository, ever.** It is under NDA. The booth loads
   `EDSDK.dll` from a folder the operator provides. Same for any other vendor
   SDK that is not freely redistributable. The AGPL side of loading it is
   settled by the section 7 permission in `LICENSE-EXCEPTION`; do not edit
   `LICENSE` itself, it is the unmodified AGPL text.
2. **No secrets.** No API keys, no passwords, no `.env` with real values, not
   even in tests or fixtures.
3. **No AI features in the product.** Not a stylistic objection — the promise is
   that the booth works offline with no running costs and no third party seeing
   guests' faces. Filters are maths (LUTs, tone curves), not models.
4. **No biometrics.** No face recognition, no face grouping, no "find my
   photos by selfie". Guests are matched to their run by the button they
   pressed. This is a legal position (GDPR Art. 9), not a backlog item.
5. **Offline first.** Anything that needs the network gets a queue and a retry,
   never a blocked screen. If you add a delivery path, it has an outbox.
6. **Additive database migrations.** New columns and tables, never a destructive
   change to a live gallery's data.
7. **Do not touch `windows_uploader/`.** It is the old client still running at
   real events.
8. **Branch `main` is production.** Work on `ecosystem` or a feature branch. Do
   not open pull requests against `main` unless the issue says so.

## Style

* **Code, comments, commit messages and documentation in English.** User-facing
  strings are translated (`app/i18n/`, `booth/boothcore/web/static/i18n.js`);
  German and English both exist. Older code still has German comments — when you
  touch it, translating it is welcome, but as its own commit.
* **Comments explain why, not what.** `# retry here, a printer that is warming
  up answers 503 for a few seconds` is useful. `# loop over items` is not.
* **No new dependencies** without saying in the pull request why the standard
  library will not do. Pillow, numpy, httpx, FastAPI, Flask are there; adding a
  framework is a discussion, not a commit.
* **No build step for the front end.** The kiosk and the admin pages are plain
  ES2020 and hand-written CSS on purpose: they have to survive on an old booth
  PC and be debuggable at 2 a.m. in a venue with no internet.
* **Tests are the spec.** New behaviour arrives with a test that fails without
  the change. Name tests after the behaviour, not the function.

## Definition of done

* All three suites pass.
* The change works with `BOOTH_CAMERA=mock` or with the Flask test client.
* No new warnings in the booth log during a normal run.
* If you changed the booth profile schema, both sides know about it:
  `booth/boothcore/config.py` **and** `app/ecosystem.py` (`DEFAULT_PROFILE`),
  plus the admin form in `app/blueprints/admin_booth.py`.
* If you added an asset kind, extend `ASSET_KINDS` and the allowed extensions.
* One commit, a message that says what changes for a user and why. Not "fix
  stuff", not a list of files.

## What we will not merge

* Drive-by reformatting, renaming or "modernising" that hides the actual change.
* A framework or build tool nobody asked for.
* Code that only works on one operating system without saying so.
* A pull request that says it was tested when it was not. Say what you ran; "I
  could not test the printer path, no printer here" is a fine sentence and
  costs you nothing.
* Anything that needs a paid service to work.

## Where to start

Issues labelled `good first issue` are self-contained and have an acceptance
criterion in the description. `needs-hardware` means it cannot be finished
without a camera or printer on the desk — take it only if you have one. The
open items we know about are in the issue tracker; if the thing you want to fix
is not there, open an issue first and say what you intend to do. A small,
finished, tested change beats a large one that needs three rounds of review.

## Things that will surprise you

* The QR code exists **before** the photo. The booth signs a random id with the
  event key, so the link works while the upload is still queued.
* Print counters live on the booth, not in the gallery — the booth prints even
  when the gallery is unreachable.
* Moderation is enforced by a SQLite trigger, not only in the view layer, so no
  import path can slip past it.
* The booth's outbox has one queue per target. A USB stick that was pulled out
  only stalls its own queue; the gallery upload keeps going.
