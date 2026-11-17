"""Jede Seite einmal laden — öffentlich und angemeldet, im echten Browser.

Ein Rundgang, kein Funktionstest: Lädt die Seite, läuft ihr JavaScript,
holt sie alles, was sie braucht, und läuft sie auf einem Telefon nicht
seitlich über? Das klingt nach wenig und ist genau das, was an einem Tag
dreimal gefehlt hat — eine Oberfläche, die auf Höhe null zusammenfiel, eine
Verwaltungsseite, die wegen eines Anführungszeichens gar nicht startete, und
ein Wettrennen beim Anmelden. Serverseitig war jedes Mal alles grün.

Eine Warnung, die hier teuer bezahlt wurde: Beim ersten Anlauf schlug die
Anmeldung still fehl. Der Rundgang lief durch, meldete 26-mal „in Ordnung"
— und hatte 26-mal die Anmeldeseite vermessen. Ein Rundgang, der nicht
prüft, ob er überhaupt dort ist, wo er sein soll, ist schlimmer als keiner.
Deshalb steht unten eine eigene Prüfung dafür.
"""
from __future__ import annotations

import io
import json
import secrets
import socket
import threading

import pytest
from PIL import Image

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright nicht installiert")

PASSWORT = "rundgang-passwort"


def _jpeg(farbe):
    b = io.BytesIO()
    Image.new("RGB", (900, 1200), farbe).save(b, "JPEG")
    b.seek(0)
    return b


@pytest.fixture(scope="module")
def welt(tmp_path_factory):
    """Server, Event, Fotos, Gerät, Zugang — einmal für alle Seiten."""
    import os
    os.environ.setdefault("SECRET_KEY", "test")
    from werkzeug.serving import make_server

    from app import accounts, create_app, db, ecosystem
    from booth.boothcore import share

    app = create_app()
    app.config.update(TESTING=True)
    c = app.test_client()
    c.get("/healthz")
    with app.app_context():
        db.set_setting("admin_username", "admin")
        accounts.set_password(PASSWORT)

    slug = "rund-" + secrets.token_hex(3)
    eid = db.create_event(name="Rundgang", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
    con.commit()
    con.close()
    dev = db.create_device(name="Rundbox")
    ecosystem.save_device_profile(dev["id"], {"modes": ["photo"]}, eid)
    event = db.get_event_by_id(eid)
    uids = []
    for i, farbe in enumerate([(200, 120, 120), (120, 200, 140), (120, 140, 200)]):
        uid = share.new_uid()
        uids.append(uid)
        c.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
               content_type="multipart/form-data",
               data={"uid": uid, "session_uid": uid, "kind": "photo", "variant": "web",
                     "event_slug": slug, "file": (_jpeg(farbe), "result_web.jpg"),
                     "meta": json.dumps({"taken_at": f"2026-09-20T2{i}:00:00"})})

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = make_server("127.0.0.1", port, app, threaded=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield {"basis": f"http://127.0.0.1:{port}", "slug": slug, "eid": eid,
               "dev": dev["id"],
               "code": f"{uids[0]}-{ecosystem.signature(ecosystem.ensure_share_key(event), uids[0])}"}
    finally:
        server.shutdown()
        t.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            br = p.chromium.launch()
        except Exception as e:                       # pragma: no cover
            pytest.skip(f"Chromium nicht startbar: {e}")
        yield br
        br.close()


@pytest.fixture(scope="module")
def angemeldet(browser, welt):
    """Ein Browser, der wirklich angemeldet ist — und der es beweist."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    pg.goto(welt["basis"] + "/admin/login")
    pg.fill("input[name=username]", "admin")
    pg.fill("input[name=password]", PASSWORT)
    pg.click("button[type=submit]")
    pg.wait_for_timeout(800)
    angekommen = pg.url
    pg.close()
    if "/admin/login" in angekommen:
        pytest.fail(f"Anmeldung fehlgeschlagen — gelandet auf {angekommen}")
    yield ctx
    ctx.close()


def _oeffentlich(welt):
    s, c = welt["slug"], welt["code"]
    return [("Startseite", "/"), ("Galerie", f"/gallery/{s}"), ("Gast-QR", f"/p/{c}"),
            ("Meine Bilder", "/meine"), ("Finden", "/finden"), ("Kiosk", f"/kiosk/{s}"),
            ("Diaschau", f"/slideshow/{s}"), ("Teilen", f"/gallery/{s}/share"),
            ("Impressum", "/impressum"), ("Datenschutz", "/datenschutz"),
            ("FAQ", "/faq"), ("Über", "/ueber"), ("Anmeldung", "/admin/login")]


def _verwaltung(welt):
    e, d = welt["eid"], welt["dev"]
    return [("Übersicht", "/admin/dashboard"), ("Events", "/admin/events"),
            ("Event neu", "/admin/events/new"), ("Event ändern", f"/admin/events/{e}/edit"),
            ("Durchsicht", f"/admin/events/{e}/review"), ("Speicher", f"/admin/events/{e}/storage"),
            ("Löschungen", "/admin/deletions"), ("Einstellungen", "/admin/settings"),
            ("Erfassung", "/admin/settings/tracking"), ("Eingang", "/admin/settings/eingang"),
            ("Bewertungen", "/admin/settings/bewertungen"), ("MCP", "/admin/settings/mcp"),
            ("Startseite", "/admin/settings/homepage"),
            ("Benachrichtigung", "/admin/settings/notifications"),
            ("E-Mail", "/admin/settings/mail"), ("Rechtliches", "/admin/settings/legal"),
            ("FAQ", "/admin/settings/faq"), ("Auswertung", "/admin/analytics"),
            ("Auswertung Event", f"/admin/analytics/{e}"),
            ("Auswertung Box", f"/admin/analytics/box/{d}"),
            ("Geräte", "/admin/devices"), ("Gerät neu", "/admin/devices/new"),
            ("Gerät ändern", f"/admin/devices/{d}/edit"),
            ("Geräteprotokoll", f"/admin/devices/{d}/logs"),
            ("Boxen", "/admin/booths"), ("Protokoll", "/admin/protokoll")]


def _besuchen(pg, url, breite):
    """Eine Seite laden und alles einsammeln, was schiefgehen kann."""
    fehler, schlecht = [], []
    pg.on("pageerror", lambda e: fehler.append(str(e)))
    pg.on("response", lambda r: schlecht.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    antwort = pg.goto(url, wait_until="domcontentloaded", timeout=20000)
    pg.wait_for_timeout(600)
    return {
        "status": antwort.status if antwort else 0,
        "js": fehler,
        "anfragen": schlecht,
        "ueberlauf": pg.evaluate("document.documentElement.scrollWidth") - breite,
        # Ein <img> ohne src ist kein kaputtes Bild — das ist der leere
        # Platzhalter der geschlossenen Lightbox. Nur src-Bilder zählen.
        "kaputte_bilder": pg.eval_on_selector_all(
            "img[src]", "els => els.filter(e => e.complete && e.naturalWidth === 0).length"),
        "url": pg.url,
    }


@pytest.mark.parametrize("breite", [1280, 390], ids=["laptop", "telefon"])
def test_jede_oeffentliche_seite_laedt_sauber(browser, welt, breite):
    pg = browser.new_page(viewport={"width": breite, "height": 900})
    probleme = []
    try:
        for name, pfad in _oeffentlich(welt):
            r = _besuchen(pg, welt["basis"] + pfad, breite)
            if r["status"] >= 400:
                probleme.append(f"{name}: HTTP {r['status']}")
            if r["js"]:
                probleme.append(f"{name}: JavaScript-Fehler {r['js'][0][:90]}")
            if r["anfragen"]:
                probleme.append(f"{name}: fehlgeschlagene Anfrage {r['anfragen'][0][:90]}")
            if r["ueberlauf"] > 2:
                probleme.append(f"{name}: läuft {r['ueberlauf']}px seitlich über")
            if r["kaputte_bilder"]:
                probleme.append(f"{name}: {r['kaputte_bilder']} Bild(er) laden nicht")
    finally:
        pg.close()
    assert probleme == [], "\n".join(probleme)


def test_jede_verwaltungsseite_laedt_sauber(angemeldet, welt):
    probleme = []
    for name, pfad in _verwaltung(welt):
        pg = angemeldet.new_page()
        try:
            r = _besuchen(pg, welt["basis"] + pfad, 1440)
            # Der teuerste Fehler dieses Rundgangs: auf der Anmeldeseite
            # landen und das für eine geprüfte Seite halten.
            if "/admin/login" in r["url"]:
                probleme.append(f"{name}: auf die Anmeldung umgeleitet — nicht geprüft")
                continue
            if r["status"] >= 400:
                probleme.append(f"{name}: HTTP {r['status']}")
            if r["js"]:
                probleme.append(f"{name}: JavaScript-Fehler {r['js'][0][:90]}")
            if r["anfragen"]:
                probleme.append(f"{name}: fehlgeschlagene Anfrage {r['anfragen'][0][:90]}")
            if r["ueberlauf"] > 2:
                probleme.append(f"{name}: läuft {r['ueberlauf']}px seitlich über")
        finally:
            pg.close()
    assert probleme == [], "\n".join(probleme)


def test_der_rundgang_merkt_wenn_er_gar_nicht_angemeldet_ist(browser, welt):
    """Der Wächter über dem Wächter.

    Ohne Anmeldung muss der Verwaltungsteil auf der Anmeldeseite landen. Tut
    er das nicht, prüft der Test oben etwas anderes als er glaubt — oder die
    Verwaltung steht offen, was schlimmer wäre.
    """
    pg = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        pg.goto(welt["basis"] + "/admin/dashboard", wait_until="domcontentloaded")
        pg.wait_for_timeout(400)
        assert "/admin/login" in pg.url, \
            f"Die Verwaltung ist ohne Anmeldung erreichbar: {pg.url}"
    finally:
        pg.close()
