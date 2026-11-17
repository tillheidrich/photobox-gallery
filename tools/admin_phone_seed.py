#!/usr/bin/env python3
"""Seed a throwaway gallery with data that stresses the admin pages, then serve it.

    python tools/admin_phone_seed.py 5763 /tmp/admin-phone-data

Never point it at a real data folder: it deletes the folder it is given first.
The data is made to hurt a phone layout: long event names and slugs, an event
with 300 photos, eight boxes (one red, one yellow), a long client name, empty
lists elsewhere. Writes <data_dir>/pages.json (base URL, an admin session
cookie, the page list) for tools/admin_phone_check.mjs and serves until killed.
See docs/ADMIN-PHONE-CHECK.md.
"""
import io, json, os, random, shutil, sys
from pathlib import Path
root = str(Path(__file__).resolve().parent.parent)
port, data = int(sys.argv[1]), sys.argv[2]
if not data.startswith(("/tmp/", "/private/tmp/", "/var/folders/")):
    sys.exit("refusing: the data folder must be a throwaway folder under /tmp")
shutil.rmtree(data, ignore_errors=True)
os.makedirs(data)
os.environ["DATA_DIR"] = data
os.environ["SECRET_KEY"] = "local-phone-check-example-only"
sys.path.insert(0, root)
os.chdir(root)
from app import create_app  # noqa
from werkzeug.serving import make_server  # noqa
from werkzeug.datastructures import FileStorage  # noqa
from PIL import Image  # noqa
from flask import render_template  # noqa

app = create_app()
# Edited templates show on the next request: this server is for looking at pages.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
@app.get("/_shot/login2fa")
def _l2():
    return render_template("admin/login.html", error=None, csrf_token="t", setup_available=False, second_factor=True)


@app.get("/_shot/login")
def _l1():
    return render_template("admin/login.html", error="Ungültige Zugangsdaten", csrf_token="t", setup_available=False, second_factor=False)



@app.get("/_shot/layout-link")
def _ll():
    from app import accounts
    tc = app.test_client()
    with tc.session_transaction() as s:
        s["admin_logged_in"] = True; s["admin_epoch"] = accounts.session_epoch(); s["csrf_token"] = "t"
    r = tc.post(f"/admin/events/{ids['e_big']}/layout", data={"csrf_token": "t", "templates": ids["tpl"], "days": "30", "field_key": "namen", "field_label": "Namen", "field_max": "40", "field_default": ""})
    return r.get_data(as_text=True)


c = app.test_client()
c.get("/healthz")


def jpg(w=480, h=360, seed=0):
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h), (rnd.randint(40, 220), rnd.randint(40, 220), rnd.randint(40, 220)))
    for i in range(6):
        x, y = rnd.randint(0, w), rnd.randint(0, h)
        im.paste((rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)), (x, y, x + w // 4, y + h // 4))
    b = io.BytesIO(); im.save(b, "JPEG", quality=70); return b.getvalue()


ids = {}
with app.app_context():
    from app import db, ecosystem, accounts, media, retention, clients, domains, vorlagen, phone_pairing
    from app import layout_freigabe as lf
    db.init_db(); ecosystem.init_schema()
    db.set_setting("admin_username", "chef")  # local throwaway account, never a real one
    db.set_setting("setup_completed", "1")
    db.set_setting("homepage_headline", "Fotobox Muster")
    accounts.set_password("ein-langes-start-passwort")

    def ev(name, slug, **f):
        eid = db.create_event(name=name, slug=slug, upload_token="tok-" + slug[:20])
        if f:
            db.update_event(eid, f)
        return eid

    e1 = ev("Hochzeit Mia & Tom", "mia-tom", published=1, date="2027-06-19")
    db.set_event_featured(e1, True)
    from app import gallery_settings as gsm
    db.update_event(e1, {"gallery_json": json.dumps(gsm.clean({"layout_approval": True}))})
    e2 = ev("Sommerfest der Freiwilligen Feuerwehr Musterstadt-Süd mit Tag der offenen Tür 2027",
            "sommerfest-freiwillige-feuerwehr-musterstadt-sued-tag-der-offenen-tuer-2027",
            published=0, date="2027-08-14", description="Langer Name, langer Slug, kein Bild.")
    e3 = ev("Firmenjubiläum Muster & Söhne GmbH", "muster-jubilaeum", published=1, date="2026-11-20")
    ev("Abiball Gymnasium am Markt", "abiball-markt", published=1, date="2026-06-28")
    e5 = ev("Standesamt", "mia-tom-standesamt", published=1, date="2027-06-18")
    con = db.get_db(); con.execute("UPDATE events SET parent_id=? WHERE id=?", (e1, e5)); con.commit(); con.close()
    ids.update(e_big=e1, e_long=e2, e_small=e3, e_child=e5)
    for i in range(300):
        media.save_upload(e1, FileStorage(io.BytesIO(jpg(seed=i)), filename=f"IMG_{i:04d}.jpg"))
    for i in range(7):
        media.save_upload(e3, FileStorage(io.BytesIO(jpg(seed=1000 + i)), filename=f"j{i}.jpg"))
    for i in range(40):
        db.add_stat(e1, random.choice(["view", "download", "share", "like"]), meta={"src": "qr"})

    k1 = clients.create("Familie Brandt-Oppermann aus Musterhausen-Nord", "Ruft lieber an als zu mailen.")
    k2 = clients.create("Muster & Söhne GmbH")
    clients.create("Mia")
    clients.set_events(k1, [e1, e5]); clients.set_events(k2, [e3])
    ids["client"] = k1

    names = ["Box 1 Saal", "Box 2", "Fotobox im Foyer des Gemeindezentrums Musterhausen (Leihgerät)",
             "Spiegelbox", "Reservebox", "Box Werkstatt", "Box 7", "Box 8 Test"]
    devs = [db.create_device(name=n, notes="Seriennummer 4711-0815" if i == 2 else None) for i, n in enumerate(names)]
    ids["dev"] = devs[2]["id"]; ids["dev_ok"] = devs[0]["id"]
    beats = [
        {"booth_running": True, "disk_free_gb": 88.0, "disk_total_gb": 256.0, "queue_pending": 2, "agent_version": "2.1.0"},
        {"booth_running": True, "disk_free_gb": 6.0, "disk_total_gb": 256.0, "queue_pending": 60, "agent_version": "2.1.0"},
        {"booth_running": False, "disk_free_gb": 1.0, "disk_total_gb": 128.0, "queue_failed": 7, "printer_status": "error", "agent_version": "2.0.3"},
        {"booth_running": True, "disk_free_gb": 40.0, "disk_total_gb": 128.0, "printer_status": "offline"},
    ]
    for d, b in zip(devs, beats):
        c.post("/api/booth/heartbeat", json=b, headers={"X-API-Key": d["api_key"]})
    ecosystem.add_device_logs(devs[2]["id"], [
        {"level": "error", "code": "PRINTER_JAM", "message": "Drucker meldet Papierstau im Fach 2. Druckauftrag 18 wurde angehalten und wartet.",
         "context": {"printer": "DNP DS620A", "job": 18, "path": "C:\\Users\\Photobox\\AppData\\Local\\photobox\\spool\\job-0018-very-long-file-name.jpg"}},
        {"level": "warning", "message": "Wenig Speicher: 1,0 GB frei"},
        {"level": "info", "message": "Start"},
    ] * 6)

    for i in range(6):
        retention.record(scope="session", reason="guest_request", actor="admin", event_slug="mia-tom",
                         session_uid=f"s-{i:04d}-a1b2c3d4e5f6", files=3, size=4_200_000, note="Gast hat am Telefon um Löschung gebeten")
    retention.record(scope="event", reason="retention", event_slug="sommerfest-freiwillige-feuerwehr-musterstadt-sued-tag-der-offenen-tuer-2027", files=812, size=2_400_000_000)
    try:
        domains.anlegen("fotos.mia-und-tom-heiraten-im-sommer.de", e1, "Domain der Familie")
        domains.anlegen("galerie.muster-und-soehne.de", e3)
    except Exception as exc:
        print("domains", exc)

    from app.blueprints.admin_booth import _assets_dir
    files = []
    for roh in [
        {"name": "Klassisch hoch mit sehr langem Vorlagennamen für den Saal", "paper": "10x15",
         "slots": [{"x": 70, "y": 70, "w": 1060, "h": 1180, "shot": 0}],
         "texts": [{"x": 600, "y": 1330, "size": 92, "align": "center", "value": "Mia & Tom", "color": "#222222"},
                   {"x": 600, "y": 1470, "size": 44, "align": "center", "value": "19. Juni 2027", "color": "#666666"}]},
        {"name": "Zwei Bilder", "paper": "10x15",
         "slots": [{"x": 70, "y": 70, "w": 1060, "h": 700, "shot": 0}, {"x": 70, "y": 800, "w": 1060, "h": 700, "shot": 1}],
         "texts": [{"x": 600, "y": 1560, "size": 70, "align": "center", "value": "Danke!", "color": "#222222"}]},
    ]:
        v, _ = vorlagen.pruefe(roh)
        files.append(vorlagen.speichern(v, _assets_dir())["file"])
    ids["tpl"] = files[0]
    lf.create(e1, {"templates": files, "fields": lf.DEFAULT_FIELDS, "palettes": ["hochzeit", "hell"],
                   "fonts": ["serif", "system"], "logo": True}, days=30, by="admin")
    try:
        pr = phone_pairing.open_request("a" * 64, "Box im Flur des Gemeindezentrums", "2.1.0", "inst-1", "127.0.0.1")
        ids["pair"] = pr.get("code", "")
    except Exception as exc:
        print("pair", exc); ids["pair"] = "ABCDEFGH"

with c.session_transaction() as s:
    s["admin_logged_in"] = True
    s["admin_epoch"] = accounts.session_epoch()
    s["csrf_token"] = "t"
for i, img in enumerate([jpg(1200, 800, 5), jpg(800, 800, 6)]):
    c.post("/admin/booth-assets", data={"csrf_token": "t", "kind": "start",
           "files": (io.BytesIO(img), f"Startbild-Hochzeit-{i}.jpg")}, content_type="multipart/form-data")
for t in ("Impressum A", "Impressum B mit einer ziemlich langen Zeile, die umbrechen muss"):
    c.post("/admin/settings/legal", data={"csrf_token": "t", "impressum": t})


ser = app.session_interface.get_signing_serializer(app)
cookie = ser.dumps({"admin_logged_in": True, "admin_epoch": accounts.session_epoch(), "csrf_token": "t"})
E, D = ids["e_big"], ids["dev"]
pages = {
    "login": "/_shot/login", "login-2fa": "/_shot/login2fa", "recover": "/admin/recover", "setup": "/setup",
    "dashboard": "/admin/dashboard", "events": "/admin/events", "event-new": "/admin/events/new",
    "event-edit": f"/admin/events/{E}/edit", "event-edit-long": f"/admin/events/{ids['e_long']}/edit",
    "event-review": f"/admin/events/{E}/review", "event-storage": f"/admin/events/{E}/storage",
    "event-layout": "/_shot/layout-link",
    "clients": "/admin/clients", "client-edit": f"/admin/clients/{ids['client']}",
    "booths": "/admin/booths", "devices": "/admin/devices", "device-new": "/admin/devices/new",
    "device-edit": f"/admin/devices/{D}/edit", "device-logs": f"/admin/devices/{D}/logs",
    "device-sync": f"/admin/devices/{D}/sync", "booth-profile": f"/admin/devices/{D}/booth",
    "pair": f"/admin/devices/pair?c={ids['pair'].replace('-', '')}",
    "booth-assets": "/admin/booth-assets", "templates": "/admin/vorlagen",
    "template-editor": f"/admin/vorlagen/editor?datei={ids['tpl']}", "software": "/admin/software",
    "domains": "/admin/domains", "analytics": "/admin/analytics", "analytics-event": f"/admin/analytics/{E}",
    "analytics-box": f"/admin/analytics/box/{ids['dev_ok']}",
    "deletions": "/admin/deletions", "protokoll": "/admin/protokoll",
    "settings": "/admin/settings", "settings-homepage": "/admin/settings/homepage",
    "settings-notifications": "/admin/settings/notifications", "settings-mail": "/admin/settings/mail",
    "settings-legal": "/admin/settings/legal", "settings-faq": "/admin/settings/faq",
    "settings-tracking": "/admin/settings/tracking", "settings-eingang": "/admin/settings/eingang",
    "settings-bewertungen": "/admin/settings/bewertungen", "settings-mcp": "/admin/settings/mcp",
    "settings-assistants": "/admin/settings/assistants", "settings-qr": "/admin/settings/qr",
}
with open(os.path.join(data, "pages.json"), "w") as f:
    json.dump({"base": f"http://127.0.0.1:{port}", "cookie": cookie, "pages": pages, "ids": ids}, f, indent=1)
print("seeded", ids, flush=True)
srv = make_server("127.0.0.1", port, app, threaded=True)
srv.serve_forever()
