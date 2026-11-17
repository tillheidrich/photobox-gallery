#!/usr/bin/env python3
"""Rehearse the jump of a production gallery from the old code to this one.

    python tools/migration_rehearsal.py                     # synthetic gallery
    python tools/migration_rehearsal.py --from-backup X.tar # a real backup

What it does, each step on a copy in a temporary folder:

1. **Old gallery.** Synthetic mode: the OLD code (``git archive`` of
   ``origin/main`` unless ``--old-tree`` names a checkout) builds a gallery
   through its own test client and functions: admin password, settings,
   events with and without password, a draft, a slug change, devices with
   keys, uploads by device/admin/guest, likes, statistics, subscriptions,
   a logo, FAQ entries. With ``--from-backup`` the archive is verified and
   restored instead; it is never modified.
2. **Backup.** ``app.backup`` of THIS tree backs up the old folder — the old
   schema must not stop it, and the old database must stay byte-identical.
3. **Restore.** The archive is restored into a fresh folder (``app.backup``).
4. **Upgrade.** THIS code starts against it (``init_db`` + migrations), is
   timed, and starts a second time: the second start must change nothing.
5. **Checks.** No old table lost a row, ``integrity_check`` is clean, every
   event page answers, every media file is served byte-identical, old slugs,
   codes and share links still lead to the gallery, device keys are
   accepted. Synthetic mode also logs in with the old admin password, opens
   password galleries with their passwords and uploads with an old key.
6. **Way back.** The OLD code starts against the migrated database and the
   same checks run. If it fails, the report names where; the documented way
   back is then "restore the backup taken before the upgrade".

The report prints counts and findings only — never names, addresses, keys
or file names from the data. Exit code 0: nothing lost; 1: data lost or a
check failed on the new code; 2: the rehearsal itself could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import backup  # noqa: E402  (after the path tweak on purpose)

PYTHON = sys.executable
#: Signing key of both codes in the rehearsal; an example value, never a real one.
SECRET = "example-key-of-the-rehearsal"


# ── Child programs ──────────────────────────────────────────────────────────
# Each runs in its own interpreter with the tree it tests first on sys.path,
# so the old and the new ``app`` package never meet in one process. Input and
# output go through JSON files; nothing secret is printed.

_COMMON = r'''
import io, json, os, re, secrets, sys, time, traceback
from pathlib import Path
OUT = Path(os.environ["REHEARSAL_OUT"])
FACTS = json.loads(Path(os.environ["REHEARSAL_FACTS"]).read_text()) if os.environ.get("REHEARSAL_FACTS") else {}

# No network at all: a restored production database carries real SMTP,
# Telegram and webhook settings, and a rehearsal must not mail a guest or
# ping the operator's phone. Binding is blocked too (the inbox would listen).
import socket
def _no_network(*_a, **_k):
    raise OSError("network disabled during the migration rehearsal")
socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = _no_network
socket.create_connection = socket.getaddrinfo = _no_network

def csrf(page):
    m = re.search(r'name="csrf_token" value="([^"]*)"', page)
    return m.group(1) if m else ""

def jpeg(w=1200, h=800, color=(180, 40, 40)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG", quality=85)
    b.seek(0)
    return b
'''

#: Synthetic production gallery, built by the OLD code through its own routes.
POPULATE = _COMMON + r'''
from app import create_app, db, emailer
app = create_app()
app.config.update(TESTING=True)
c = app.test_client()
notes = []
assert c.get("/healthz").status_code == 200

# Admin: start password from ADMIN_PASSWORD, then changed in the settings form.
page = c.get("/admin/login").get_data(as_text=True)
r = c.post("/admin/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"],
                                 "csrf_token": csrf(page)})
assert r.status_code == 302, ("login", r.status_code)
admin_pw = "Old-Admin-Pass-" + secrets.token_hex(4)
page = c.get("/admin/settings").get_data(as_text=True)
r = c.post("/admin/settings/account", data={"admin_username": "admin", "new_password": admin_pw,
                                            "csrf_token": csrf(page)})
assert r.status_code == 302, ("account", r.status_code)

# Settings as the old admin pages store them.
emailer.set_smtp_config({"host": "smtp.example.invalid", "port": 587, "user": "rehearsal",
                         "password": "synthetic-only", "from_addr": "gallery@example.invalid",
                         "use_tls": True})
for k, v in {"impressum_text": "Rehearsal imprint", "datenschutz_text": "Rehearsal privacy",
             "guest_upload_enabled": "1", "zip_download_enabled": "1", "zip_download_admin_only": "0",
             "homepage_headline": "Rehearsal", "notify_on_upload": "0",
             "footer_custom_link_label": "Home", "footer_custom_link_url": "https://example.invalid"}.items():
    db.set_setting(k, v)
db.save_faq_items([{"q": "Where are my photos?", "a": "In the gallery."}])
logo = io.BytesIO()
from PIL import Image
Image.new("RGBA", (300, 120), (20, 90, 200, 255)).save(logo, "PNG")
logo.seek(0)
r = c.post("/admin/settings/logo", data={"logo": (logo, "logo.png"), "csrf_token": csrf(page)},
           content_type="multipart/form-data")
assert r.status_code == 302, ("logo", r.status_code)

# Events through the admin form: public, password, draft.
def new_event(name, **extra):
    page = c.get("/admin/events/new").get_data(as_text=True)
    form = {"name": name, "csrf_token": csrf(page), "retention_days": "90", **extra}
    r = c.post("/admin/events/new", data=form)
    assert r.status_code == 302, ("event", name, r.status_code)
    return db.get_event_by_id(int(r.headers["Location"].rstrip("/").split("/")[-2]))

ev_public = new_event("Sommerfest Rehearsal", slug="sommerfest-alt", published="1", date="2026-08-01")
ev_pw = new_event("Hochzeit Rehearsal", slug="hochzeit-probe", published="1", password="gast-pw-1")
ev_draft = new_event("Entwurf Rehearsal", slug="entwurf-probe")
# An event from early summer: its 90 days are over, the old code never cared.
ev_old = new_event("Fruehling Rehearsal", slug="fruehling-probe", published="1", date="2026-06-01")

# Slug change through the edit form: the old slug becomes an alias.
page = c.get(f"/admin/events/{ev_public['id']}/edit").get_data(as_text=True)
r = c.post(f"/admin/events/{ev_public['id']}/edit",
           data={"name": ev_public["name"], "slug": "sommerfest-2026", "published": "1",
                 "retention_days": "90", "csrf_token": csrf(page)})
assert r.status_code == 302, ("edit", r.status_code)
ev_public = db.get_event_by_id(ev_public["id"])
assert ev_public["slug"] == "sommerfest-2026"

# Devices through the admin form; the first one gets its event assigned.
for name in ("Box Rehearsal A", "Box Rehearsal B"):
    page = c.get("/admin/devices/new").get_data(as_text=True)
    r = c.post("/admin/devices/new", data={"name": name, "csrf_token": csrf(page)})
    assert r.status_code == 302, ("device", r.status_code)
dev_a, dev_b = sorted(db.list_devices(), key=lambda d: d["id"])[:2]
db.update_device(dev_a["id"], {"landing_event_id": ev_public["id"]})

def device_upload(dev, event, color, name):
    r = c.post("/api/upload", headers={"X-API-Key": dev["api_key"]},
               data={"event_slug": event["slug"], "file": (jpeg(color=color), name)},
               content_type="multipart/form-data")
    assert r.status_code == 200, ("upload", r.status_code, r.get_data(as_text=True)[:200])
    return r.get_json()["id"]

for i in range(3):
    device_upload(dev_a, ev_public, (200, 40 * i, 40), f"IMG_{i:04d}.jpg")
for i in range(2):
    device_upload(dev_b, ev_pw, (40, 200, 40 * i), f"HZ_{i:04d}.jpg")
device_upload(dev_b, ev_old, (120, 120, 0), "SPRING_0001.jpg")

# The old Windows uploader: /api/events/ensure creates an event, then uploads into it.
r = c.post("/api/events/ensure", json={"name": "Uploader Rehearsal"}, headers={"X-Api-Key": dev_b["api_key"]})
assert r.status_code == 201, ("ensure", r.status_code)
ev_ensure = db.get_event_by_id(r.get_json()["event_id"])
device_upload(dev_b, ev_ensure, (90, 90, 200), "AUTO_0001.jpg")

# Admin upload into the draft, guest upload into the public event.
r = c.post("/api/admin-upload", data={"event_id": str(ev_draft["id"]), "file": (jpeg(color=(9, 9, 9)), "admin.jpg")},
           content_type="multipart/form-data")
assert r.status_code == 200, ("admin-upload", r.status_code)
guest = app.test_client()
r = guest.post("/api/guest-upload", data={"event_id": str(ev_public["id"]), "file": (jpeg(color=(1, 2, 3)), "gast.jpg")},
               content_type="multipart/form-data")
notes.append(f"old guest upload: HTTP {r.status_code}")

# A video: the old upload path only takes images, so it is registered the
# way an older client put videos in (media row with kind 'video').
video = Path(os.environ["REHEARSAL_VIDEO"])
if video.exists():
    from app import media as old_media
    dest = old_media.event_storage_dir(ev_public["slug"]) / "originals" / "clip.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(video.read_bytes())
    rel = dest.relative_to(old_media.data_dir()).as_posix()
    import hashlib
    db.add_media(event_id=ev_public["id"], kind="video", filename="clip.mp4", path_original=rel,
                 path_webp=None, path_thumb=None, size_bytes=dest.stat().st_size,
                 sha256=hashlib.sha256(dest.read_bytes()).hexdigest(), share_token=secrets.token_urlsafe(16))
    r = c.post("/api/upload", headers={"X-API-Key": dev_a["api_key"]},
               data={"event_slug": ev_public["slug"], "file": (io.BytesIO(video.read_bytes()), "clip2.mp4")},
               content_type="multipart/form-data")
    notes.append(f"old device upload of a video: HTTP {r.status_code}")

# Likes from two guests, statistics, gallery views.
media_public = db.list_media(ev_public["id"])
for who in range(2):
    g = app.test_client()
    for m in media_public[: 2 + who]:
        assert g.post(f"/api/photo/{m['id']}/like").status_code == 200
    g.get(f"/gallery/{ev_public['slug']}?src=qr")
    g.post("/api/track", json={"event_id": ev_public["id"], "action": "download", "media_id": media_public[0]["id"]})

# Subscriptions: one confirmed, one unsubscribed, one pending.
tokens = []
for i in range(3):
    r = guest.post("/api/notify/subscribe", json={"event_id": ev_public["id"], "email": f"guest{i}@example.invalid"})
    assert r.status_code == 200, ("subscribe", r.status_code)
con = db.get_db()
tokens = [row["token"] for row in con.execute("SELECT token FROM notify_subscriptions ORDER BY id")]
con.close()
guest.get(f"/notify/confirm/{tokens[0]}")
guest.get(f"/notify/unsubscribe/{tokens[1]}")

# A heartbeat from the first box.
r = c.post("/api/booth/heartbeat", headers={"X-API-Key": dev_a["api_key"]}, json={"booth_running": True})
notes.append(f"old heartbeat: HTTP {r.status_code}")

events = [db.get_event_by_id(e["id"]) for e in (ev_public, ev_pw, ev_draft, ev_ensure, ev_old)]
facts = {
    "admin_user": "admin", "admin_password": admin_pw,
    "passwords": {str(ev_pw["id"]): "gast-pw-1"},
    "public_event": ev_public["id"], "password_event": ev_pw["id"], "draft_event": ev_draft["id"],
    "ensure_event": ev_ensure["id"], "ensure_event_name": "Uploader Rehearsal",
    "old_slugs": {"sommerfest-alt": "sommerfest-2026"},
    "device_assigned": dev_a["id"], "device_unassigned": dev_b["id"],
    "device_b_uploaded_to": [ev_pw["id"], ev_ensure["id"]],
    "notes": notes,
}
OUT.write_text(json.dumps(facts))
'''

#: First start of a code against a folder: schema check and migration.
MIGRATE = _COMMON + r'''
t0 = time.perf_counter()
result = {}
try:
    from app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    r = app.test_client().get("/healthz")
    result = {"ok": r.status_code == 200, "status": r.status_code}
except Exception as e:
    tb = traceback.extract_tb(e.__traceback__)
    where = [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in tb if "/app/" in f.filename][-3:]
    result = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "where": where}
result["ms"] = round((time.perf_counter() - t0) * 1000)
OUT.write_text(json.dumps(result))
'''

#: The checks, the same for the new code and (on the way back) the old one.
CHECK = _COMMON + r'''
import sqlite3
ROLE = os.environ["REHEARSAL_ROLE"]
DATA = Path(os.environ["DATA_DIR"])
results = []

def check(name, ok, detail="", **numbers):
    results.append({"name": name, "ok": bool(ok), "detail": detail, **numbers})

class Failed:
    def __init__(self, e):
        tb = traceback.extract_tb(e.__traceback__)
        where = [f"{Path(f.filename).name}:{f.lineno}" for f in tb if "/app/" in f.filename][-2:]
        self.status_code = 599
        self.error = f"{type(e).__name__}: {str(e)[:120]} @ {' < '.join(where)}"
        self.headers = {}
        self.data = b""
    def get_data(self, as_text=False):
        return "" if as_text else b""

def req(client, url, method="GET", **kw):
    try:
        return client.open(url, method=method, **kw)
    except Exception as e:                      # the old code under TESTING raises instead of 500
        return Failed(e)

def first_error(responses):
    for r in responses:
        if getattr(r, "error", None):
            return r.error
    return ""

try:
    from app import create_app, db
    app = create_app()
    app.config.update(TESTING=True)
    guest = app.test_client()
    r = req(guest, "/healthz")
    check("start", r.status_code == 200, getattr(r, "error", f"HTTP {r.status_code}"))
except Exception as e:
    check("start", False, Failed(e).error)
    OUT.write_text(json.dumps({"checks": results}))
    raise SystemExit(0)

def admin_client():
    a = app.test_client()
    with a.session_transaction() as s:
        s["admin_logged_in"] = True
        try:
            from app import accounts
            s["admin_epoch"] = accounts.session_epoch()
        except ImportError:
            pass                                    # the old code has no session epochs
    return a

con = sqlite3.connect(DATA / "gallery.sqlite3")
con.row_factory = sqlite3.Row
events = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY id")]
media_rows = [dict(r) for r in con.execute("SELECT * FROM media ORDER BY id")]
aliases = [dict(r) for r in con.execute("SELECT * FROM slug_aliases ORDER BY id")]
devices = [dict(r) for r in con.execute("SELECT * FROM devices ORDER BY id")]
subs = [dict(r) for r in con.execute("SELECT * FROM notify_subscriptions ORDER BY id")]
settings = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}
con.close()
by_id = {e["id"]: e for e in events}
admin = admin_client()

# Event pages for a guest: nothing may break; visible ones show their media.
statuses = {}
broken = []
for e in events:
    r = req(app.test_client(), f"/gallery/{e['slug']}")
    statuses[str(r.status_code)] = statuses.get(str(r.status_code), 0) + 1
    if r.status_code >= 500:
        broken.append(r)
check("event pages answer (guest)", not broken, first_error(broken) or json.dumps(statuses, sort_keys=True),
      events=len(events), failed=len(broken))
broken = [r for r in (req(admin, f"/gallery/{e['slug']}") for e in events) if r.status_code >= 500]
check("event pages answer (admin)", not broken, first_error(broken), failed=len(broken))

# Media: every file the database names is served byte for byte as it lies on disk.
served = missing = wrong = 0
errors = []
for m in media_rows:
    local = (m.get("storage") or "local") == "local"
    paths = {m.get("path_thumb"), m.get("path_webp")} | ({m.get("path_original")} if local else set())
    for p in filter(None, paths):
        f = DATA / p
        if not f.is_file():
            missing += 1
            continue
        r = req(admin, f"/data/{p}")
        if r.status_code == 200 and r.data == f.read_bytes():
            served += 1
        else:
            wrong += 1
            errors.append(r)
check("media files served identically", wrong == 0,
      first_error(errors) or (f"{missing} referenced files were already missing on disk" if missing else ""),
      served=served, wrong=wrong, missing_on_disk=missing)

# Old addresses: aliases, codes and share links still lead into the gallery.
bad = []
for a in aliases:
    target = by_id.get(a["event_id"])
    r = req(app.test_client(), f"/gallery/{a['alias_slug']}")
    if not (300 <= r.status_code < 400 and target and target["slug"] in r.headers.get("Location", "")):
        bad.append(r)
check("old slugs redirect", not bad, first_error(bad), aliases=len(aliases), failed=len(bad))
bad = []
coded = [e for e in events if e.get("code") and e.get("published")]
for e in coded:
    r = req(app.test_client(), f"/g/{e['code']}")
    if not (300 <= r.status_code < 400):
        bad.append(r)
check("gallery codes redirect", not bad, first_error(bad), codes=len(coded), failed=len(bad))
bad = []
sample = [m for m in media_rows if m.get("share_token") and by_id.get(m["event_id"], {}).get("published")][:300]
for m in sample:
    r = req(app.test_client(), f"/s/{m['share_token']}")
    if not (300 <= r.status_code < 400):
        bad.append(r)
check("share links redirect", not bad, first_error(bad), links=len(sample), failed=len(bad))

# Device keys: every active box is still recognised.
bad = []
active = [d for d in devices if d.get("active")]
for d in active:
    r = req(app.test_client(), "/api/booth/heartbeat", "POST", headers={"X-API-Key": d["api_key"]},
            json={"booth_running": True})
    if r.status_code != 200:
        bad.append(r)
check("device keys accepted (heartbeat)", not bad, first_error(bad) or (f"HTTP {bad[0].status_code}" if bad else ""),
      devices=len(active), failed=len(bad))

# Admin pages render against this database.
pages = ["/admin/dashboard", "/admin/events", "/admin/devices", "/admin/settings", "/admin/analytics"]
pages += [f"/admin/events/{e['id']}/edit" for e in events[:50]]
bad = [r for r in (req(admin, p) for p in pages) if r.status_code >= 500]
check("admin pages render", not bad, first_error(bad), pages=len(pages), failed=len(bad))

# Public pages around the galleries.
pages = ["/", "/impressum", "/datenschutz", "/faq", "/branding/logo"]
for e in [e for e in events if e.get("published")][:20]:
    pages += [f"/gallery/{e['slug']}/qr.png", f"/slideshow/{e['slug']}", f"/kiosk/{e['slug']}"]
bad = [r for r in (req(app.test_client(), p) for p in pages) if r.status_code >= 500]
check("public pages render", not bad, first_error(bad), pages=len(pages), failed=len(bad))

if subs:
    r = req(app.test_client(), f"/notify/unsubscribe/{subs[-1]['token']}")
    check("unsubscribe link works", r.status_code == 200, getattr(r, "error", f"HTTP {r.status_code}"))

if ROLE == "new":
    # Production never ran retention (gap L3). The first maintenance run
    # after the upgrade empties every gallery past its period — say how many.
    try:
        from app import retention
        with app.app_context():
            due = retention.due()
        files = sum(sum(1 for m in media_rows if m["event_id"] == e["id"]) for e in due)
        results.append({"name": "first maintenance run would empty (retention)", "ok": True, "note": True,
                        "detail": f"{len(due)} of {len(events)} galleries, {files} media rows"})
    except Exception as e:
        results.append({"name": "retention dry run", "ok": False, "detail": Failed(e).error})

stored = settings.get("admin_password_hash") or ""
fmt_ok = stored.startswith(("pbkdf2:", "scrypt:")) or bool(re.fullmatch(r"[0-9a-f]{64}", stored))
check("admin password hash in a format the login reads", fmt_ok,
      "werkzeug" if stored.startswith(("pbkdf2:", "scrypt:")) else ("legacy sha256" if fmt_ok else "unknown format"))
'''

#: Only with a synthetic gallery: the rehearsal knows its passwords.
CHECK_KNOWN = r'''
if FACTS:
    def thumbs(event_id):
        return [m["path_thumb"] for m in media_rows if m["event_id"] == event_id and m.get("path_thumb")]

    # Admin login with the password set in the old code.
    a = app.test_client()
    page = req(a, "/admin/login").get_data(as_text=True)
    wrong = req(a, "/admin/login", "POST", data={"username": FACTS["admin_user"], "password": "wrong",
                                                 "csrf_token": csrf(page)})
    page = req(a, "/admin/login").get_data(as_text=True)
    r = req(a, "/admin/login", "POST", data={"username": FACTS["admin_user"], "password": FACTS["admin_password"],
                                             "csrf_token": csrf(page)})
    dash = req(a, "/admin/dashboard")
    check("admin login with the old password", r.status_code == 302 and dash.status_code == 200
          and wrong.status_code != 302, getattr(r, "error", "") or f"login HTTP {r.status_code}, dashboard HTTP {dash.status_code}, wrong password HTTP {wrong.status_code}")

    # Public gallery shows its photos to a guest.
    pub = by_id[FACTS["public_event"]]
    body = req(app.test_client(), f"/gallery/{pub['slug']}").get_data(as_text=True)
    check("public gallery shows its media", all(t in body for t in thumbs(pub["id"])),
          f"{sum(t in body for t in thumbs(pub['id']))}/{len(thumbs(pub['id']))} thumbnails on the page")
    g = app.test_client()
    ok = [req(g, f"/data/{t}").status_code == 200 for t in thumbs(pub["id"])]
    check("guest can load the public thumbnails", all(ok), f"{sum(ok)}/{len(ok)}")

    # Password gallery: closed without, open with the old password.
    pw = by_id[FACTS["password_event"]]
    g = app.test_client()
    page = req(g, f"/gallery/{pw['slug']}").get_data(as_text=True)
    closed = 'name="password"' in page and not any(t in page for t in thumbs(pw["id"]))
    req(g, f"/gallery/{pw['slug']}", "POST", data={"password": "wrong", "csrf_token": csrf(page)})
    still = req(g, f"/gallery/{pw['slug']}").get_data(as_text=True)
    page = still
    req(g, f"/gallery/{pw['slug']}", "POST", data={"password": FACTS["passwords"][str(pw["id"])], "csrf_token": csrf(page)})
    opened = req(g, f"/gallery/{pw['slug']}").get_data(as_text=True)
    check("gallery password still applies", closed and not any(t in still for t in thumbs(pw["id"]))
          and all(t in opened for t in thumbs(pw["id"])),
          f"closed={closed}, wrong password kept it closed={not any(t in still for t in thumbs(pw['id']))}, "
          f"right password opened={all(t in opened for t in thumbs(pw['id']))}")
    g = app.test_client()
    magic = req(g, f"/gallery/{pw['slug']}?access_token={pw['access_token']}")
    after = req(g, f"/gallery/{pw['slug']}").get_data(as_text=True) if magic.status_code in (200, 301, 302, 303) else ""
    body = magic.get_data(as_text=True) + after
    check("magic link (access token) opens the password gallery", all(t in body for t in thumbs(pw["id"])),
          getattr(magic, "error", f"HTTP {magic.status_code}"))

    draft = by_id[FACTS["draft_event"]]
    body = req(app.test_client(), f"/gallery/{draft['slug']}").get_data(as_text=True)
    # The old "new event" form stores status 'draft' but leaves published at
    # its column default 1, and the old gallery only asks published: such an
    # event was public. Recorded, not judged — the upgrade must not change it.
    visible = any(t in body for t in thumbs(draft["id"]))
    results.append({"name": "event saved unpublished in the old form", "ok": True, "note": True,
                    "detail": "visible to guests" if visible else "hidden from guests"})

    for old, new in FACTS["old_slugs"].items():
        r = req(app.test_client(), f"/gallery/{old}")
        check(f"renamed gallery: old address redirects", 300 <= r.status_code < 400 and new in r.headers.get("Location", ""),
              getattr(r, "error", f"HTTP {r.status_code}"))

    # Device keys at work: the assigned box uploads; the old uploader flow.
    devs = {d["id"]: d for d in devices}
    da, db_ = devs[FACTS["device_assigned"]], devs[FACTS["device_unassigned"]]
    r = req(app.test_client(), "/api/upload", "POST", headers={"X-API-Key": da["api_key"]},
            data={"event_slug": pub["slug"], "file": (jpeg(color=(5, 6, 7)), "after-upgrade.jpg")},
            content_type="multipart/form-data")
    check("old device key uploads into its assigned event", r.status_code == 200, getattr(r, "error", f"HTTP {r.status_code}"))
    for eid in FACTS["device_b_uploaded_to"]:
        ev = by_id[eid]
        r = req(app.test_client(), "/api/upload", "POST", headers={"X-API-Key": db_["api_key"]},
                data={"event_slug": ev["slug"], "file": (jpeg(color=(7, 6, 5)), "unassigned.jpg")},
                content_type="multipart/form-data")
        kind = "event it created via /api/events/ensure" if eid == FACTS["ensure_event"] else "event it uploaded to before"
        results.append({"name": f"unassigned old box uploads into an {kind}", "ok": r.status_code == 200,
                        "detail": getattr(r, "error", f"HTTP {r.status_code}"), "behaviour": True})
    r = req(app.test_client(), "/api/events/ensure", "POST", headers={"X-Api-Key": db_["api_key"]},
            json={"name": FACTS["ensure_event_name"]})
    token = (r.get_json(silent=True) or {}).get("upload_token") if hasattr(r, "get_json") else None
    results.append({"name": "old uploader: ensure on its existing event", "ok": r.status_code in (200, 201),
                    "detail": getattr(r, "error", f"HTTP {r.status_code}, upload token {'returned' if token else 'withheld'}"),
                    "behaviour": True})

    g = app.test_client()
    m = next(m for m in media_rows if m["event_id"] == pub["id"])
    r = req(g, f"/api/photo/{m['id']}/like", "POST")
    check("guest can like a photo", r.status_code == 200, getattr(r, "error", f"HTTP {r.status_code}"))
    r = req(app.test_client(), "/api/notify/subscribe", "POST",
            json={"event_id": pub["id"], "email": "after-upgrade@example.invalid"})
    check("guest can subscribe", r.status_code in (200, 201), getattr(r, "error", f"HTTP {r.status_code}"))
    if ROLE == "new":
        r = req(app.test_client(), "/setup")
        check("/setup is closed to guests", r.status_code in (302, 303, 403, 404), f"HTTP {r.status_code}")

OUT.write_text(json.dumps({"checks": results}))
'''


# ── Orchestration ───────────────────────────────────────────────────────────

class Rehearsal:
    def __init__(self, workdir: Path, old_tree: Path, *, scrub: bool):
        self.work = workdir
        self.old_tree = old_tree
        self.scrub = scrub
        self.lines: List[str] = []
        self.loss: List[str] = []           # exit code 1
        self.findings: List[str] = []
        self.report: Dict[str, Any] = {}

    # -- output --------------------------------------------------------------
    def say(self, text: str = "") -> None:
        self.lines.append(text)
        print(text, flush=True)

    def clean(self, text: str) -> str:
        """With real data: no quoted values, addresses or tokens in the report."""
        if not self.scrub or not text:
            return text
        import re
        text = re.sub(r"'[^']*'|\"[^\"]*\"", "'…'", text)
        text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<address>", text)
        return re.sub(r"\b[0-9A-Za-z_-]{16,}\b", "<token>", text)

    # -- children ------------------------------------------------------------
    def child(self, code: str, tree: Path, data: Path, *, role: str, facts: Optional[Path] = None,
              extra: Optional[Dict[str, str]] = None, timeout: int = 600) -> Dict[str, Any]:
        out = self.work / f"result-{time.monotonic_ns()}.json"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ORIGINALS_", "PHOTOBOX_", "SMTP_", "TG_", "DISCORD_"))
               and k not in ("DATA_DIR", "BASE_URL", "SECRET_KEY", "PYTHONPATH", "ADMIN_PASSWORD")}
        env.update(DATA_DIR=str(data), PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1",
                   SECRET_KEY=SECRET, PHOTOBOX_MAINTENANCE="off", ORIGINALS_BACKEND="local",
                   PHOTOBOX_BACKUP="off", REHEARSAL_OUT=str(out), REHEARSAL_ROLE=role)
        if facts:
            env["REHEARSAL_FACTS"] = str(facts)
        env.update(extra or {})
        proc = subprocess.run([PYTHON, "-c", code], cwd=str(tree), env=env, capture_output=True,
                              text=True, timeout=timeout)
        if not out.exists():
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
            raise RuntimeError(self.clean(f"{role} child exited with {proc.returncode}: " + " | ".join(tail)))
        result = json.loads(out.read_text())
        out.unlink()
        return result

    def migrate(self, tree: Path, data: Path, role: str) -> Dict[str, Any]:
        return self.child(MIGRATE, tree, data, role=role)

    def checks(self, tree: Path, data: Path, role: str, facts: Optional[Path]) -> List[Dict[str, Any]]:
        return self.child(CHECK + CHECK_KNOWN, tree, data, role=role, facts=facts)["checks"]


def snapshot(db_file: Path) -> Dict[str, Any]:
    """Schema and a content hash per table: two equal snapshots, nothing changed."""
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        schema = sorted((r[0], r[1], r[2] or "") for r in con.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        tables = {}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                   "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            h = hashlib.sha256()
            n = 0
            for row in con.execute(f'SELECT * FROM "{name}" ORDER BY 1'):
                h.update(repr(tuple(row)).encode())
                n += 1
            tables[name] = (n, h.hexdigest())
        return {"schema": hashlib.sha256(repr(schema).encode()).hexdigest(), "tables": tables,
                "objects": len(schema)}
    finally:
        con.close()


def compare_rows(before: Path, after: Path) -> Dict[str, Dict[str, Any]]:
    """Per old table: rows before/after, old rows missing, old columns whose values changed."""
    con = sqlite3.connect(f"file:{after}?mode=ro", uri=True)
    con.execute("ATTACH DATABASE ? AS b", (f"file:{before}?mode=ro",))
    out: Dict[str, Dict[str, Any]] = {}
    try:
        old_tables = [r[0] for r in con.execute(
            "SELECT name FROM b.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        new_tables = {r[0] for r in con.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
        for t in old_tables:
            n_before = con.execute(f'SELECT COUNT(*) FROM b."{t}"').fetchone()[0]
            if t not in new_tables:
                out[t] = {"before": n_before, "after": 0, "missing": n_before, "changed": {}, "dropped": True}
                continue
            n_after = con.execute(f'SELECT COUNT(*) FROM main."{t}"').fetchone()[0]
            missing = con.execute(f'SELECT COUNT(*) FROM b."{t}" WHERE rowid NOT IN '
                                  f'(SELECT rowid FROM main."{t}")').fetchone()[0]
            old_cols = [r[1] for r in con.execute(f'PRAGMA b.table_info("{t}")')]
            new_cols = {r[1] for r in con.execute(f'PRAGMA main.table_info("{t}")')}
            changed = {}
            for col in old_cols:
                if col not in new_cols:
                    changed[col] = "column dropped"
                    continue
                n = con.execute(f'SELECT COUNT(*) FROM b."{t}" o JOIN main."{t}" a ON a.rowid=o.rowid '
                                f'WHERE a."{col}" IS NOT o."{col}"').fetchone()[0]
                if n:
                    changed[col] = n
            out[t] = {"before": n_before, "after": n_after, "missing": missing, "changed": changed}
    finally:
        con.close()
    return out


def db_checks(db_file: Path) -> Dict[str, Any]:
    facts = backup.db_facts(db_file)
    return {"integrity": facts["integrity"], "foreign_key_violations": facts["foreign_key_violations"]}


def old_code(work: Path, ref: str) -> Path:
    """The old code as files: ``git archive`` of ``ref`` — no worktree, nothing written to the repo."""
    import tarfile

    dest = work / "old-code"
    dest.mkdir()
    proc = subprocess.Popen(["git", "-C", str(ROOT), "archive", "--format=tar", ref], stdout=subprocess.PIPE)
    with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
        tar.extractall(dest, filter="data")
    if proc.wait() != 0:
        raise RuntimeError(f"git archive {ref} failed")
    return dest


def make_video(work: Path) -> Path:
    """One second of test pattern as a real MP4, if ffmpeg is there."""
    out = work / "clip.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=duration=1:size=160x120:rate=10", "-pix_fmt", "yuv420p", str(out)],
                       check=False, timeout=60)
    return out


def _scale_up(db_file: Path, n: int) -> None:
    """Volume like a season of events, for a realistic migration time.
    Extra media rows point at existing files, so every check still applies."""
    con = sqlite3.connect(db_file)
    try:
        ev = con.execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()[0]
        con.executemany("INSERT INTO stats(event_id, action, meta_json) VALUES (?, 'view_gallery', '{}')",
                        [(ev,)] * n)
        dev = con.execute("SELECT id FROM devices ORDER BY id LIMIT 1").fetchone()[0]
        con.executemany("INSERT INTO device_heartbeats(device_id, health, payload_json) VALUES (?, 'green', '{}')",
                        [(dev,)] * (n // 10))
        cols = [r[1] for r in con.execute("PRAGMA table_info(media)") if r[1] not in ("id", "share_token")]
        template = con.execute(f"SELECT {', '.join(cols)} FROM media WHERE path_thumb IS NOT NULL LIMIT 1").fetchone()
        con.executemany(f"INSERT INTO media({', '.join(cols)}, share_token) VALUES ({', '.join('?' * (len(cols) + 1))})",
                        [(*template, os.urandom(12).hex()) for _ in range(n // 20)])
        con.commit()
    finally:
        con.close()


def run(r: Rehearsal, *, from_backup: Optional[Path], scale: int = 0) -> int:
    work = r.work
    facts_file: Optional[Path] = None
    say = r.say

    # 1 + 2: an old gallery and a backup of it.
    if from_backup:
        say(f"Source: backup archive ({from_backup.stat().st_size / 1e6:.1f} MB) — used read-only, restored into a copy")
        archive = from_backup
        checked = backup.verify(archive)
        say(f"  verify: {'OK' if checked['ok'] else 'FAILED'} — {checked['files']} files, "
            f"{checked['bytes'] / 1e6:.1f} MB, written by version {checked.get('app_version')}")
        if not checked["ok"]:
            for p in checked["problems"][:10]:
                say(f"    - {r.clean(p)}")
            return 2
    else:
        say("Source: synthetic gallery built by the OLD code")
        old_data = work / "old-data"
        old_data.mkdir()
        facts_file = work / "facts.json"
        facts = r.child(POPULATE, r.old_tree, old_data, role="old-populate",
                        extra={"ADMIN_PASSWORD": "start-" + os.urandom(6).hex(),
                               "REHEARSAL_VIDEO": str(make_video(work))})
        facts_file.write_text(json.dumps(facts))
        for note in facts.get("notes", []):
            say(f"  note: {note}")
        if scale:
            _scale_up(old_data / backup.DB_NAME, scale)
            say(f"  scaled up: +{scale} statistics rows, +{scale // 10} heartbeats, +{scale // 20} media rows")
        before_sha = backup._sha256_file(old_data / backup.DB_NAME)
        t = time.monotonic()
        made = backup.create(data_dir=old_data, out_dir=work / "backups")
        say(f"  backup of the old folder with the NEW command: {made['files']} files, "
            f"{made['size'] / 1e6:.1f} MB, {time.monotonic() - t:.2f} s, integrity {made['integrity']}")
        if backup._sha256_file(old_data / backup.DB_NAME) != before_sha:
            r.loss.append("the backup command changed the old database")
        checked = backup.verify(made["path"])
        say(f"  verify: {'OK' if checked['ok'] else 'FAILED'}")
        if not checked["ok"]:
            r.loss.append("backup of the old folder does not verify")
        archive = Path(made["path"])
    manifest = backup.read_manifest(archive)
    tables_before = (manifest.get("database") or {}).get("tables") or {}
    say(f"  old schema: {len(tables_before)} tables, {sum(tables_before.values())} rows, "
        f"{len(manifest.get('files') or [])} files, media {'included' if manifest.get('media_included') else 'NOT included'}")
    if (manifest.get("originals") or {}).get("remote_media"):
        say(f"  {manifest['originals']['remote_media']} originals are on the offload target (not checked)")

    # 3: restore into a fresh folder.
    new_data = work / "new-data"
    t = time.monotonic()
    restored = backup.restore(archive, new_data)
    say(f"Restore: {'OK' if restored['ok'] else 'FAILED'} in {time.monotonic() - t:.2f} s"
        + "".join(f"\n  ! {r.clean(w)}" for w in restored["warnings"]))
    if not restored["ok"]:
        r.loss.extend(r.clean(p) for p in restored["problems"])
        return 1
    pristine = work / "before.sqlite3"
    shutil.copy2(new_data / backup.DB_NAME, pristine)

    # 4: upgrade, timed; then a second start that must change nothing.
    say("")
    say("Upgrade: NEW code against the restored folder")
    first = r.migrate(ROOT, new_data, "new")
    if not first["ok"]:
        r.loss.append("the new code does not start: " + r.clean(first.get("error", "")) +
                      (" at " + " < ".join(first.get("where", [])) if first.get("where") else ""))
        say(f"  FAILED: {r.loss[-1]}")
        return 1
    say(f"  first start incl. migration: {first['ms']} ms")
    s1 = snapshot(new_data / backup.DB_NAME)
    second = r.migrate(ROOT, new_data, "new")
    s2 = snapshot(new_data / backup.DB_NAME)
    changed = [t for t in s1["tables"] if s1["tables"].get(t) != s2["tables"].get(t)]
    idem = s1["schema"] == s2["schema"] and not changed and set(s1["tables"]) == set(s2["tables"])
    say(f"  second start: {second['ms']} ms, {'changes nothing (idempotent)' if idem else 'CHANGED ' + ', '.join(changed or ['the schema'])}")
    if not idem:
        r.loss.append("the migration is not idempotent: " + ", ".join(changed or ["schema"]))
    say(f"  schema after: {len(s1['tables'])} tables ({len(s1['tables']) - len(tables_before)} new), {s1['objects']} objects")

    rows = compare_rows(pristine, new_data / backup.DB_NAME)
    say("  old tables, rows before → after:")
    for t, info in rows.items():
        mark = "LOST " if info["missing"] else "     "
        extra = f", {info['missing']} rows missing" if info["missing"] else ""
        if info["changed"]:
            extra += "; values changed in " + ", ".join(f"{c} ({n})" for c, n in info["changed"].items())
        say(f"    {mark}{t}: {info['before']} → {info['after']}{extra}")
        if info["missing"] or info.get("dropped"):
            r.loss.append(f"{t}: {info['missing']} rows lost")
        if info["changed"]:
            r.findings.append(f"migration rewrites old values in {t}: " + ", ".join(info["changed"]))
    dbc = db_checks(new_data / backup.DB_NAME)
    say(f"  integrity_check: {dbc['integrity']}; foreign_key_check: "
        + (", ".join(f"{t} {n}" for t, n in dbc["foreign_key_violations"].items()) or "clean"))
    if dbc["integrity"] != "ok":
        r.loss.append("integrity_check after the migration: " + dbc["integrity"])
    before_fk = backup.db_facts(pristine)["foreign_key_violations"]
    for t, n in dbc["foreign_key_violations"].items():
        if n > before_fk.get(t, 0):
            r.loss.append(f"the migration added foreign key violations in {t}")

    rollback = work / "rollback-data"
    shutil.copytree(new_data, rollback)

    # 5: what a user of the new code sees.
    say("")
    say("Checks against the NEW code:")
    _print_checks(r, r.checks(ROOT, new_data, "new", facts_file), fatal=True)

    # 6: the way back.
    say("")
    say("Way back: OLD code against the MIGRATED database")
    back = r.migrate(r.old_tree, rollback, "old")
    if not back["ok"]:
        say(f"  the old code does NOT start: {r.clean(back.get('error', ''))}"
            + (f" at {' < '.join(back.get('where', []))}" if back.get("where") else ""))
        r.report["way_back"] = False
    else:
        say(f"  old code starts: {back['ms']} ms")
        failed = _print_checks(r, r.checks(r.old_tree, rollback, "old", facts_file), fatal=False)
        r.report["way_back"] = not failed
        again = r.migrate(ROOT, rollback, "new")
        say(f"  and forward again (new code after the old one ran): {'OK' if again['ok'] else 'FAILED ' + r.clean(again.get('error', ''))}")
        if not again["ok"]:
            r.report["way_back"] = False
    say("  → way back on the migrated database: " + ("YES" if r.report["way_back"] else
        "NO — the way back is: restore the backup taken before the upgrade"))
    return 1 if r.loss else 0


def _print_checks(r: Rehearsal, checks: List[Dict[str, Any]], *, fatal: bool) -> int:
    failed = 0
    for c in checks:
        numbers = ", ".join(f"{k} {v}" for k, v in c.items() if k not in ("name", "ok", "detail", "behaviour"))
        detail = r.clean(c.get("detail") or "")
        if c.get("note"):
            r.say(f"  [   NOTE] {c['name']} — {detail}")
            finding = f"{c['name']}: {detail} ({'new' if fatal else 'old'} code)"
            r.findings.append(finding)
            continue
        if c.get("behaviour"):
            tag = "same" if c["ok"] else "CHANGED"
            r.say(f"  [{tag:>7}] {c['name']}" + (f" — {detail}" if detail else ""))
            if not c["ok"] and fatal:
                r.findings.append(f"behaviour change: {c['name']} ({detail})")
            continue
        r.say(f"  [{'OK' if c['ok'] else 'FAIL':>7}] {c['name']}" + (f" ({numbers})" if numbers else "")
              + (f" — {detail}" if detail else ""))
        if not c["ok"]:
            failed += 1
            if fatal:
                r.loss.append(f"{c['name']}: {detail}")
    return failed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from-backup", type=Path, help="rehearse against this backup archive (read-only)")
    parser.add_argument("--old-tree", type=Path, help="checkout of the old code (default: git archive of --old-ref)")
    parser.add_argument("--old-ref", default="origin/main", help="git ref of the old code (default origin/main)")
    parser.add_argument("--scale", type=int, default=0,
                        help="synthetic only: add N statistics rows (and N/20 media rows) for timing")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder (synthetic data only)")
    parser.add_argument("--json", type=Path, help="also write the report as JSON here")
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="photobox-migration-"))
    os.chmod(work, 0o700)
    started = time.monotonic()
    r = Rehearsal(work, args.old_tree.resolve() if args.old_tree else Path(), scrub=bool(args.from_backup))
    code = 2
    try:
        if not args.old_tree:
            r.old_tree = old_code(work, args.old_ref)
        r.say(f"Migration rehearsal — new code {backup._app_identity()['app_version']} "
              f"({backup._app_identity()['app_commit'][:7] or 'no commit'}), old code "
              f"{args.old_tree or args.old_ref}")
        code = run(r, from_backup=args.from_backup.resolve() if args.from_backup else None,
                   scale=args.scale)
    except Exception as e:                              # noqa: BLE001 — the report must say why
        r.say(f"Rehearsal could not run: {r.clean(f'{type(e).__name__}: {e}')}")
        code = 2
    finally:
        r.say("")
        for f in r.findings:
            r.say(f"Finding: {f}")
        for loss in r.loss:
            r.say(f"LOSS/FAILURE: {loss}")
        r.say(f"Result: {'nothing lost' if code == 0 else ('FAILED' if code == 1 else 'did not run')} — "
              f"{time.monotonic() - started:.1f} s in total")
        if args.json:
            args.json.write_text(json.dumps({"exit": code, "lines": r.lines, "loss": r.loss,
                                             "findings": r.findings, **r.report}, indent=1))
        if args.keep and not args.from_backup:
            print(f"(kept: {work})")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
