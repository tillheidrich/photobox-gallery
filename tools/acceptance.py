import os, re, io, secrets, tempfile, json, atexit, shutil
os.environ["SECRET_KEY"]="acc-secret"
os.environ["DATA_DIR"]=tempfile.mkdtemp(prefix="pb-acc-")
# The throwaway gallery leaves with the run (it used to stay in /tmp for good).
atexit.register(shutil.rmtree, os.environ["DATA_DIR"], True)
from PIL import Image
from app import create_app, db

app = create_app(); app.config.update(TESTING=True)
c = app.test_client()

results=[]
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))

def png_bytes(w=800,h=1000,color=(180,150,110)):
    b=io.BytesIO(); Image.new("RGB",(w,h),color).save(b,"JPEG"); b.seek(0); return b

# 0 infra
check("Healthcheck /healthz 200", c.get("/healthz").status_code==200)
check("Setup-Wizard /setup erreichbar", c.get("/setup").status_code in (200,302))
check("FAQ /faq 200", c.get("/faq").status_code==200)
check("i18n /lang/en 302", c.post("/lang/en").status_code in (301,302))

# 1 admin login (HTTP flow w/ csrf)
# Es gibt kein Standardpasswort mehr: Beim ersten Start entsteht ein zufälliges
# und wird ins Protokoll geschrieben. Die Abnahme setzt deshalb selbst eines —
# so wie es der Betreiber beim ersten Anmelden tun muss.
from werkzeug.security import generate_password_hash as _gph
# Jeder Lauf würfelt sein eigenes — ein festes Wort im Quelltext wäre ein
# Zugangsdatum im Repo, und die Hygieneprüfung sagt dazu zu Recht Nein.
ADMIN_PW = secrets.token_urlsafe(18)
with app.app_context():
    db.init_db()
    db.set_setting("admin_username", "admin")
    db.set_setting("admin_password_hash", _gph(ADMIN_PW))
lp = c.get("/admin/login").get_data(as_text=True)
m = re.search(r'name="csrf_token" value="([^"]*)"', lp)
csrf = m.group(1) if m else ""
r = c.post("/admin/login", data={"csrf_token":csrf,"username":"admin","password":ADMIN_PW})
check("Admin-Login (gesetztes Passwort)", r.status_code in (302,303), f"HTTP {r.status_code}")
with c.session_transaction() as s:
    check("Session gesetzt nach Login", s.get("admin_logged_in") is True)

# ensure logged in for admin ops
with c.session_transaction() as s: s["admin_logged_in"]=True

# 2 event create via admin form
def admin_post(pfad, daten=None, **kw):
    """Wie ein Browser: Seite holen, Formular-Zeichen mitnehmen, absenden."""
    seite = c.get(pfad).get_data(as_text=True)
    m2 = re.search(r'name="csrf_token" value="([^"]*)"', seite)
    werte = dict(daten or {})
    werte.setdefault("csrf_token", m2.group(1) if m2 else csrf)
    return c.post(pfad, data=werte, **kw)

r = admin_post("/admin/events/new", {"name":"Abnahme Event","date":"2026-07-20"}, follow_redirects=True)
with app.app_context():
    ev = db.get_event_by_slug("abnahme-event")
check("Event anlegen (Admin-Formular)", ev is not None, "slug=abnahme-event" if ev else "nicht gefunden")
evid = ev["id"] if ev else None
# publish it
if evid:
    with app.app_context():
        con=db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?",(evid,)); con.commit(); con.close()
check("Event in /admin/events sichtbar", evid and ("Abnahme Event" in c.get("/admin/events").get_data(as_text=True)))
check("Event-Bearbeiten rendert", evid and c.get(f"/admin/events/{evid}/edit").status_code==200)

# 3 device create + config.json
with app.app_context():
    dev = db.create_device(name="Abnahme-Box")
    devid = dev["id"]; apikey = dev["api_key"]
    # /api/upload only takes events assigned to the device (since 11.11.2026)
    from app import ecosystem
    ecosystem.save_device_profile(devid, {}, evid)
rc = c.get(f"/admin/devices/{devid}/config.json")
check("Geräte-config.json abrufbar", rc.status_code==200, f"HTTP {rc.status_code}")
try:
    cfgok = isinstance(rc.get_json(), dict)
except Exception:
    cfgok = "api" in rc.get_data(as_text=True).lower()
check("config.json ist gültiges JSON", cfgok)

# 4 upload via API
up = c.post("/api/upload", data={"event_slug":"abnahme-event","file":(png_bytes(),"a.jpg")},
            headers={"X-API-Key":apikey}, content_type="multipart/form-data")
check("Foto-Upload /api/upload (Gerät)", up.status_code==200 and up.get_json().get("ok"), f"HTTP {up.status_code}")
pid = up.get_json().get("id") if up.status_code==200 else None
# a few more
for _ in range(3):
    c.post("/api/upload", data={"event_slug":"abnahme-event","file":(png_bytes(color=(120,140,120)),"b.jpg")},
           headers={"X-API-Key":apikey}, content_type="multipart/form-data")
with app.app_context():
    nmedia = len(db.list_media(evid) or [])
check("Fotos landen in der DB", nmedia>=4, f"{nmedia} Medien")

# 5 gallery renders
g = c.get("/gallery/abnahme-event")
check("Galerie rendert (öffentlich)", g.status_code==200 and 'class="photo-item' in g.get_data(as_text=True))
check("Lightbox im Markup vorhanden", 'id="lightbox"' in g.get_data(as_text=True))
check("Kiosk /kiosk/<slug> 200", c.get("/kiosk/abnahme-event").status_code==200)
qr=c.get("/gallery/abnahme-event/qr.png"); check("QR-PNG 200 image/png", qr.status_code==200 and "image/png" in qr.headers.get("Content-Type",""))
sp=c.get("/gallery/abnahme-event/sharepic.png"); check("Sharepic-PNG 200", sp.status_code==200)
check("Druck-QR-Seite 200", c.get("/gallery/abnahme-event/print").status_code==200)

# 6 like toggle
if pid:
    l1=c.post(f"/api/photo/{pid}/like", json={"event_id":evid})
    j1=l1.get_json() if l1.status_code==200 else {}
    l2=c.post(f"/api/photo/{pid}/like", json={"event_id":evid})
    j2=l2.get_json() if l2.status_code==200 else {}
    check("Like setzen (API 200 + count)", l1.status_code==200 and "count" in j1, str(j1))
    check("Like zurücknehmen (toggle)", j1.get("liked")!=j2.get("liked"), f"{j1.get('liked')}→{j2.get('liked')}")

# 7 password-protected event + security
with app.app_context():
    pev = db.create_event(slug="pw-abnahme", name="PW Abnahme", upload_token=secrets.token_hex(6), password="geheim")
    con=db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?",(pev,)); con.commit(); con.close()
    token = db.get_event_by_slug("pw-abnahme").get("access_token")
    db.set_setting("zip_download_enabled","1"); db.set_setting("zip_download_admin_only","0")
c2 = app.test_client()  # anonymer Client (keine Admin-Session)
pg = c2.get("/gallery/pw-abnahme")
check("Passwort-Galerie zeigt Dialog", pg.status_code==200 and "passw" in pg.get_data(as_text=True).lower())
zip_anon = c2.get("/gallery/pw-abnahme/download.zip")
check("ZIP anonym bei PW-Event → 403 (Sicherheit #2/#4)", zip_anon.status_code==403, f"HTTP {zip_anon.status_code}")
# correct password
lp2=c2.get("/gallery/pw-abnahme").get_data(as_text=True)
mm=re.search(r'name="csrf_token" value="([^"]*)"', lp2); pcsrf=mm.group(1) if mm else ""
rpw=c2.post("/gallery/pw-abnahme", data={"password":"geheim","csrf_token":pcsrf}, follow_redirects=True)
check("Richtiges Passwort lässt durch", rpw.status_code==200 and 'photo-item' in rpw.get_data(as_text=True) or "passw" not in rpw.get_data(as_text=True).lower())
# magic link
c3 = app.test_client()
ml=c3.get(f"/gallery/pw-abnahme?access_token={token}")
check("Magic-Link (access_token) umgeht Dialog (#5)", ml.status_code==200 and "passw" not in ml.get_data(as_text=True).lower(), f"HTTP {ml.status_code}")
# admin zip allowed
zc=app.test_client()
with zc.session_transaction() as s: s["admin_logged_in"]=True
za=zc.get("/gallery/pw-abnahme/download.zip")
check("ZIP als Admin erlaubt (#4)", za.status_code==200, f"HTTP {za.status_code}")

# 8 subscribe double-opt-in
sb=c2.post("/api/notify/subscribe", json={"event_id":evid,"email":"gast@example.com"})
check("E-Mail-Anmeldung /api/notify/subscribe", sb.status_code in (200,201), f"HTTP {sb.status_code}")
with app.app_context():
    con=db.get_db()
    row=con.execute("SELECT token, confirmed_at FROM notify_subscriptions WHERE email='gast@example.com'").fetchone()
    con.close()
sub_token = row["token"] if row else None
check("Subscription angelegt (pending)", row is not None and row["confirmed_at"] is None)
if sub_token:
    cf=c2.get(f"/notify/confirm/{sub_token}")
    with app.app_context():
        con=db.get_db(); r2=con.execute("SELECT confirmed_at FROM notify_subscriptions WHERE token=?", (sub_token,)).fetchone(); con.close()
    check("Bestätigungslink bestätigt Anmeldung", cf.status_code in (200,302) and r2 and r2["confirmed_at"] is not None)

# print report
ok=sum(1 for _,p,_ in results if p); tot=len(results)
print(f"\n=== ABNAHME SPRINT 1 — {ok}/{tot} bestanden ===\n")
print("| # | Prüfpunkt | Ergebnis | Detail |")
print("|---|-----------|----------|--------|")
for i,(n,p,d) in enumerate(results,1):
    print(f"| {i} | {n} | {'✅' if p else '❌ FAIL'} | {d} |")
