#!/usr/bin/env python3
"""Seed a throwaway gallery with what a guest sees, then serve it.

    python tools/guest_phone_seed.py 5764 /tmp/guest-phone-data

The guest-side twin of tools/admin_phone_seed.py: a gallery with photos,
collages, a GIF, a boomerang and unframed originals; runs with signed QR
codes (``/p/<code>``); a password gallery; a gallery in list layout; a share
link and a layout invitation; a square grid in proofing mode. Writes
<data_dir>/pages.json with a *guest* session cookie (no admin, but with
scanned runs for ``/meine``) and a *client* cookie (``clientCookie``, a
client holding the first gallery, for the pick dots) for
tools/admin_phone_check.mjs and serves until killed.

Never point it at a real data folder: it deletes the folder it is given first.
The names are made up. See docs/ADMIN-PHONE-CHECK.md, section "Guest pages".
"""
import io, json, os, random, secrets, shutil, subprocess, sys
from pathlib import Path
root = str(Path(__file__).resolve().parent.parent)
port, data = int(sys.argv[1]), sys.argv[2]
if not data.startswith(("/tmp/", "/private/tmp/", "/var/folders/")):
    sys.exit("refusing: the data folder must be a throwaway folder under /tmp")
shutil.rmtree(data, ignore_errors=True)
os.makedirs(data)
os.environ["DATA_DIR"] = data
os.environ["SECRET_KEY"] = "local-guest-check-example-only"
sys.path.insert(0, root)
os.chdir(root)
from app import create_app  # noqa
from werkzeug.serving import make_server  # noqa
from werkzeug.datastructures import FileStorage  # noqa
from PIL import Image  # noqa

app = create_app()
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


def picture(w, h, seed):
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h), (rnd.randint(40, 220), rnd.randint(40, 220), rnd.randint(40, 220)))
    for _ in range(8):
        x, y = rnd.randint(0, w), rnd.randint(0, h)
        im.paste((rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)), (x, y, x + w // 4, y + h // 4))
    return im


def jpg(w, h, seed):
    b = io.BytesIO()
    picture(w, h, seed).save(b, "JPEG", quality=72)
    return b.getvalue()


def run_file(slug, name, im_or_bytes, folder="webp", ext="jpg"):
    from app import media
    rel = f"storage/{slug}/{folder}/{name}.{ext}"
    full = media.data_dir() / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(im_or_bytes, bytes):
        full.write_bytes(im_or_bytes)
    else:
        im_or_bytes.save(full, "JPEG", quality=80)
    return rel


def add_run_item(eid, slug, run, uid, kind, w, h, seed, *, variant="web", hidden=0, video=None):
    """One row the way a booth upload leaves it: web version, thumb, uid, run."""
    from app import db
    big = picture(w, h, seed)
    web = run_file(slug, uid, big)
    small = big.copy(); small.thumbnail((720, 720))
    thumb = run_file(slug, uid + "_t", small, folder="thumbs")
    original = web
    if video:
        original = run_file(slug, uid, video, folder="videos", ext="mp4")
    mid = db.add_media(event_id=eid, kind=kind, filename=f"{uid}.{'mp4' if video else 'jpg'}",
                       path_original=original, path_webp=web, path_thumb=thumb,
                       size_bytes=len(video) if video else 400_000, sha256=secrets.token_hex(8),
                       share_token=secrets.token_hex(6))
    con = db.get_db()
    con.execute("UPDATE media SET session_uid=?, uid=?, variant=?, hidden=?, width=?, height=?, taken_at=? WHERE id=?",
                (run, uid, variant, hidden, w, h, "2027-06-19T21:30:00", mid))
    con.commit(); con.close()
    return mid


def boomerang_mp4():
    """A two-second clip, if ffmpeg is there; the page only needs a playable file."""
    out = Path(data) / "boomerang.mp4"
    try:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=720x900:rate=15",
                        "-t", "2", "-pix_fmt", "yuv420p", str(out)], check=True, timeout=60)
        return out.read_bytes()
    except Exception:
        return b"\x00\x00\x00\x18ftypmp42"


c = app.test_client()
c.get("/healthz")
ids = {}
with app.app_context():
    from app import db, ecosystem, accounts, media, sharelinks, vorlagen
    from app import gallery_settings as gsm, layout_freigabe as lf
    db.init_db(); ecosystem.init_schema()
    db.set_setting("admin_username", "chef")  # local throwaway account, never a real one
    db.set_setting("setup_completed", "1")
    db.set_setting("homepage_headline", "Fotobox Muster Nordheide")
    accounts.set_password("ein-langes-start-passwort")

    def ev(name, slug, gallery=None, **f):
        eid = db.create_event(name=name, slug=slug, upload_token="tok-" + slug[:20])
        f.setdefault("published", 1)
        f["gallery_json"] = json.dumps(gsm.clean(gallery or {}))
        db.update_event(eid, f)
        return eid

    e1 = ev("Sommerfest Muster & Söhne", "sommerfest", {"layout_approval": True}, date="2027-06-19")
    e2 = ev("Abiball Gymnasium am Markt", "abiball-liste", {"layout": "list"}, date="2027-06-28")
    e3 = ev("Geschützte Feier", "geschuetzt", password="example-gast", date="2027-07-03")
    ids.update(gallery=e1, list=e2, password=e3)
    ev1 = db.get_event_by_id(e1)
    ecosystem.ensure_share_key(ev1)
    ev1 = db.get_event_by_id(e1)
    mp4 = boomerang_mp4()
    # Three runs: a photo with its unframed capture, a collage with a GIF and
    # a boomerang (what made /p/ load four media at once), a plain photo.
    add_run_item(e1, "sommerfest", "runa0001", "runa0001", "photo", 1800, 1200, 1)
    add_run_item(e1, "sommerfest", "runa0001", "runa0001.1", "original", 1800, 1200, 2)
    add_run_item(e1, "sommerfest", "runb0001", "runb0001", "collage", 1200, 1800, 3)
    add_run_item(e1, "sommerfest", "runb0001", "runb0001.gif", "gif", 720, 900, 4)
    add_run_item(e1, "sommerfest", "runb0001", "runb0001.boom", "boomerang", 720, 900, 5, video=mp4)
    add_run_item(e1, "sommerfest", "runb0001", "runb0001.1", "original", 2048, 1365, 6)
    add_run_item(e1, "sommerfest", "runc0001", "runc0001", "photo", 1200, 1800, 7)
    for i in range(30):
        w, h = (1800, 1200) if i % 3 else (1200, 1800)
        add_run_item(e1, "sommerfest", f"guest{i:03d}", f"guest{i:03d}", "photo" if i % 4 else "collage", w, h, 100 + i)
    for i in range(12):
        media.save_upload(e2, FileStorage(io.BytesIO(jpg(1800, 1200, 300 + i)), filename=f"IMG_{i:04d}.jpg"))
    # The square grid in proofing mode with colour flags on every tile, for
    # the touch selection check (--select-touch).
    e4 = ev("Hochzeit im Raster", "raster", {"layout": "grid", "mode": "proofing", "color_tags": True}, date="2027-07-10")
    ids["grid"] = e4
    for i in range(16):
        media.save_upload(e4, FileStorage(io.BytesIO(jpg(1200, 1800 if i % 2 else 1200, 500 + i)),
                                          filename=f"RAST_{i:04d}.jpg"))
    media.save_upload(e3, FileStorage(io.BytesIO(jpg(1200, 800, 400)), filename="geheim.jpg"))
    # Package D: guest upload with PIN and grace period, and one closed.
    from app import guest_upload
    from datetime import date as _date, timedelta as _td
    e5 = ev("Gartenparty mit Upload", "upload-pin", {"guest_upload": True, "upload_grace_days": "30",
                                                     "moderation": True, "show_source_filter": True},
            date=_date.today().isoformat())
    guest_upload.set_pin(e5, "2468")
    e6 = ev("Upload vorbei", "upload-zu", {"guest_upload": True, "upload_grace_days": "2"},
            date=(_date.today() - _td(days=20)).isoformat())
    ids.update(upload_pin=e5, upload_closed=e6)
    for i in range(6):
        media.save_upload(e5, FileStorage(io.BytesIO(jpg(1200, 1600, 600 + i)), filename=f"GART_{i:04d}.jpg"),
                          source="guest" if i % 2 else "admin")
        media.save_upload(e6, FileStorage(io.BytesIO(jpg(1600, 1200, 700 + i)), filename=f"VORB_{i:04d}.jpg"))
    ecosystem.approve_media(e5)
    # Sorting out (no. 62): two bursts of near-duplicates, one blurred shot.
    from PIL import ImageFilter as _IF, ImageEnhance as _IE
    e7 = ev("Aussortieren Probe", "aussortieren", {}, date=_date.today().isoformat())
    ids["sortout"] = e7

    def _put(im, name):
        b = io.BytesIO()
        im.save(b, "JPEG", quality=88)
        media.save_upload(e7, FileStorage(io.BytesIO(b.getvalue()), filename=name), source="admin")
    for s in (900, 901):
        base = picture(1600, 1200, s)
        _put(base, f"SERIE_{s}_a.jpg")
        _put(_IE.Brightness(base).enhance(1.06), f"SERIE_{s}_b.jpg")
        _put(base.filter(_IF.GaussianBlur(5)), f"SERIE_{s}_c.jpg")
    for s in range(902, 908):
        _put(picture(1600, 1200, s), f"EINZEL_{s}.jpg")
    _put(picture(1600, 1200, 950).filter(_IF.GaussianBlur(8)), "WACKLER.jpg")
    # Polaroid layout with the live badge, an event running today (no. 59).
    e8 = ev("Polaroid am Abend", "polaroid", {"layout": "polaroid", "live_badge": True, "show_date": False},
            date=_date.today().isoformat())
    ids["polaroid"] = e8
    for i in range(10):
        media.save_upload(e8, FileStorage(io.BytesIO(jpg(1600, 1200 if i % 3 else 1600, 980 + i)),
                                          filename=f"POLA_{i:04d}.jpg"), source="guest")
    # A trash with three pictures in it (no. 61).
    from app import trash
    from app.blueprints.admin import _purge_media
    trash.save_settings(14, False)
    for row in db.list_media(e6)[:3]:
        _purge_media(int(row["id"]))
    codes = {r: ecosystem.session_path(ev1, r) for r in ("runa0001", "runb0001", "runc0001", "wartet01")}
    link = sharelinks.create(e1, title="Fotos für die Familie")
    from app.blueprints.admin_booth import _assets_dir
    v, _ = vorlagen.pruefe({"name": "Klassisch hoch", "paper": "10x15",
                            "slots": [{"x": 70, "y": 70, "w": 1060, "h": 1180, "shot": 0}],
                            "texts": [{"x": 600, "y": 1330, "size": 92, "align": "center", "value": "Sommerfest",
                                       "color": "#222222"}]})
    tpl = vorlagen.speichern(v, _assets_dir())["file"]
    token = lf.create(e1, {"templates": [tpl], "fields": lf.DEFAULT_FIELDS, "palettes": ["hell"],
                           "fonts": ["serif", "system"], "logo": True}, days=30, by="admin")

    # A client who holds the first gallery: the pick dots and the stored
    # selection ("Merkliste") instead of the guest's "Meine Auswahl".
    from app import clients
    cid = clients.create("Muster Kundin")
    clients.new_code(cid)
    clients.assign(cid, e1)
    clients.assign(cid, e5)
    # The customer area as a hub (no. 63), with a contact address.
    db.set_setting("client_hub", "1")
    db.set_setting("legal_contact_email", "kontakt@example.org")
    db.set_setting("gallery_trust_line", "Gespeichert auf einem Server in Deutschland. Keine Werbung, kein Tracking.")
    client_row = clients.get(cid)

ser = app.session_interface.get_signing_serializer(app)
# A guest: no admin, but three scanned runs, so /meine and the nav link show.
cookie = ser.dumps({"p_uids": ["runa0001", "runb0001", "runc0001"]})
import time  # noqa
client_cookie = ser.dumps({clients.SESSION_KEY: {"id": cid, "v": int(client_row["code_version"]),
                                                 "t": int(time.time())}})
pages = {
    "home": "/", "gallery": "/gallery/sommerfest", "gallery-list": "/gallery/abiball-liste",
    "gallery-grid": "/gallery/raster",
    "gate": "/gallery/geschuetzt", "run": codes["runb0001"], "run-photo": codes["runa0001"],
    "run-pending": codes["wartet01"], "mine": "/meine", "faq": "/faq", "not-found": "/gibt-es-nicht",
    "client-login": "/kunde", "share-link": f"/l/{link['slug']}", "layout": f"/layout/{token}",
    "upload-pin": "/gallery/upload-pin", "upload-closed": "/gallery/upload-zu",
    "polaroid": "/gallery/polaroid",
}
with open(os.path.join(data, "pages.json"), "w") as f:
    json.dump({"base": f"http://127.0.0.1:{port}", "cookie": cookie, "clientCookie": client_cookie,
               "pages": pages, "ids": ids,
               "photoSelector": ".photo-item img, .sess-item img, .sess-item video"}, f, indent=1)
# The admin screens of package D, for screenshots with the same tool.
with app.app_context():
    admin_cookie = ser.dumps({"admin_logged_in": True, "admin_epoch": accounts.session_epoch(),
                              "csrf_token": "seed-csrf"})
admin_pages = {
    "edit-upload": f"/admin/events/{ids['upload_pin']}/edit#gast-upload",
    "table-sign-a6": f"/admin/events/{ids['upload_pin']}/table-sign?size=a6",
    "table-sign-a5": f"/admin/events/{ids['upload_pin']}/table-sign?size=a5",
    "trash": "/admin/trash",
    "sortout": f"/admin/events/{ids['sortout']}/aussortieren",
    "source-chips": f"/admin/events/{ids['upload_pin']}/edit#fotos",
    "settings-galerie": "/admin/settings/galerie",
    "embed-presets": f"/admin/events/{ids['polaroid']}/edit#einbetten",
}
with open(os.path.join(data, "pages-admin.json"), "w") as f:
    json.dump({"base": f"http://127.0.0.1:{port}", "cookie": admin_cookie, "pages": admin_pages, "ids": ids,
               "photoSelector": ".photo-item img"}, f, indent=1)
with open(os.path.join(data, "pages-client.json"), "w") as f:
    json.dump({"base": f"http://127.0.0.1:{port}", "cookie": client_cookie,
               "pages": {"client-hub": "/kunde", "client-sign": "/kunde/tisch-schild/upload-pin"},
               "ids": ids, "photoSelector": ".hub-tiles a"}, f, indent=1)
print("seeded", ids, flush=True)
srv = make_server("127.0.0.1", port, app, threaded=True)
srv.serve_forever()
