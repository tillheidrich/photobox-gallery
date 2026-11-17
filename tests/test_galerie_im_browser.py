"""Die Galerie, wie ein Gast sie bedient — im echten Browser.

Der Anlass: An einem Tag sind drei Fehler aufgefallen, die keine der 900
Python-Prüfungen sehen konnte, weil keine davon eine Seite lädt — eine
gespiegelte Oberfläche, die auf Höhe null zusammenfiel, eine
Verwaltungsseite, die wegen eines Anführungszeichens gar nicht startete,
und ein Wettrennen beim Anmelden. Serverseitig war jedes Mal alles grün.

Diese Datei prüft deshalb nicht, was der Server *ausliefert*, sondern was im
Browser *passiert*: Kacheln da, Auswahl geht, Zähler zählt, Lightbox öffnet
und schließt, und nirgends ein JavaScript-Fehler.
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


def _jpeg(farbe):
    b = io.BytesIO()
    Image.new("RGB", (900, 1200), farbe).save(b, "JPEG")
    b.seek(0)
    return b


FARBEN = [(200, 120, 120), (120, 200, 140), (120, 140, 200),
          (210, 190, 120), (180, 140, 200), (140, 200, 200)]


@pytest.fixture(scope="module")
def galerie(tmp_path_factory):
    """Eine echte Galerie mit echten Fotos hinter einem echten Server."""
    # WICHTIG: DATA_DIR bleibt, wie conftest es gesetzt hat. Ein eigener
    # Ordner hier hat beim ersten Versuch 24 andere Tests umgeworfen — die
    # Umgebungsvariable gilt für den ganzen Lauf, nicht für dieses Modul.
    # Die Galerie braucht hier auch keinen eigenen Ordner: Sie legt ihr
    # Event unter eigenem Slug an und fasst nichts Fremdes an.
    import os
    os.environ.setdefault("SECRET_KEY", "test")
    from werkzeug.serving import make_server

    from app import create_app, db, ecosystem
    from booth.boothcore import share

    app = create_app()
    app.config.update(TESTING=True)
    c = app.test_client()
    c.get("/healthz")

    slug = "demo-" + secrets.token_hex(3)
    eid = db.create_event(name="Demo Sommerfest", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
    con.commit()
    con.close()
    dev = db.create_device(name="Demobox")
    ecosystem.save_device_profile(dev["id"], {"modes": ["photo"]}, eid)
    uids = []
    for i, farbe in enumerate(FARBEN):
        uid = share.new_uid()
        uids.append(uid)
        r = c.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                   content_type="multipart/form-data",
                   data={"uid": uid, "session_uid": uid, "kind": "photo", "variant": "web",
                         "event_slug": slug, "file": (_jpeg(farbe), "result_web.jpg"),
                         "meta": json.dumps({"taken_at": f"2026-09-20T2{i}:00:00"})})
        assert r.status_code == 200, r.data

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = make_server("127.0.0.1", port, app, threaded=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield {"basis": f"http://127.0.0.1:{port}", "slug": slug,
               "event": db.get_event_by_id(eid), "uids": uids, "anzahl": len(FARBEN)}
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


@pytest.fixture()
def seite(browser, galerie):
    """Für jeden Test eine frische Seite — Auswahlzustand darf nicht überlaufen."""
    pg = browser.new_page(viewport={"width": 1280, "height": 1000})
    fehler = []
    pg.on("pageerror", lambda e: fehler.append(str(e)))
    pg.goto(f"{galerie['basis']}/gallery/{galerie['slug']}")
    pg.wait_for_timeout(900)
    yield pg, fehler
    pg.close()


# ── die Frage, mit der es losging ─────────────────────────────────────

def test_fotos_lassen_sich_markieren(seite, galerie):
    """Ging vorher nicht. Deshalb steht es hier und nicht in einer Notiz."""
    pg, fehler = seite
    kacheln = pg.query_selector_all(".photo-item")
    assert len(kacheln) == galerie["anzahl"], "Nicht alle Fotos sind da"
    pg.click("#selectToggle")
    pg.wait_for_timeout(300)
    assert "is-active" in pg.get_attribute("#selectionBar", "class")
    kacheln[0].click()
    pg.wait_for_timeout(250)
    assert pg.inner_text("#downloadCount").strip() == "1"
    assert "is-selected" in (kacheln[0].get_attribute("class") or "")
    kacheln[1].click()
    pg.wait_for_timeout(250)
    assert pg.inner_text("#downloadCount").strip() == "2"
    assert fehler == []


def test_abwaehlen_zaehlt_zurueck(seite):
    """Ein Zähler, der nur hochgeht, ist ein kaputter Zähler."""
    pg, _ = seite
    pg.click("#selectToggle")
    pg.wait_for_timeout(250)
    kacheln = pg.query_selector_all(".photo-item")
    kacheln[0].click()
    kacheln[1].click()
    pg.wait_for_timeout(250)
    assert pg.inner_text("#downloadCount").strip() == "2"
    kacheln[0].click()
    pg.wait_for_timeout(250)
    assert pg.inner_text("#downloadCount").strip() == "1"
    assert "is-selected" not in (kacheln[0].get_attribute("class") or "")


def test_ohne_auswahlmodus_oeffnet_ein_klick_die_lightbox(seite):
    """Die beiden Betriebsarten dürfen sich nicht in die Quere kommen."""
    pg, fehler = seite
    pg.query_selector_all(".photo-item")[0].click()
    pg.wait_for_timeout(500)
    # Gesteuert wird sie über aria-hidden — das ist auch das, was ein
    # Screenreader auswertet, also der richtige Prüfstein.
    assert pg.get_attribute("#lightbox", "aria-hidden") == "false", "Die Lightbox öffnet nicht"
    assert fehler == []


def test_die_lightbox_geht_mit_escape_wieder_zu(seite):
    pg, _ = seite
    pg.query_selector_all(".photo-item")[0].click()
    pg.wait_for_timeout(400)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(400)
    assert pg.get_attribute("#lightbox", "aria-hidden") == "true", \
        "Die Lightbox bleibt offen — der Gast sitzt fest"


def test_der_auswahlmodus_laesst_sich_wieder_verlassen(seite):
    pg, _ = seite
    pg.click("#selectToggle")
    pg.wait_for_timeout(250)
    assert "is-active" in pg.get_attribute("#selectionBar", "class")
    pg.click("#selectToggle")
    pg.wait_for_timeout(250)
    assert "is-active" not in pg.get_attribute("#selectionBar", "class")


# ── die Seite als Ganzes ──────────────────────────────────────────────

def test_die_galerie_laeuft_ohne_javascript_fehler(seite):
    pg, fehler = seite
    assert fehler == [], f"JavaScript-Fehler beim Laden: {fehler}"


def test_die_bilder_werden_wirklich_geladen(seite, galerie):
    """Ein <img> im Quelltext ist noch kein Bild auf dem Schirm."""
    pg, _ = seite
    geladen = pg.eval_on_selector_all(
        ".photo-item img", "els => els.filter(e => e.naturalWidth > 0).length")
    assert geladen == galerie["anzahl"], f"Nur {geladen} Bilder wirklich geladen"


def test_auf_dem_telefon_bleibt_die_seite_bedienbar(browser, galerie):
    """Die meisten Gäste kommen mit dem Telefon, nicht mit dem Laptop."""
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    fehler = []
    pg.on("pageerror", lambda e: fehler.append(str(e)))
    try:
        pg.goto(f"{galerie['basis']}/gallery/{galerie['slug']}")
        pg.wait_for_timeout(900)
        breite = pg.evaluate("document.documentElement.scrollWidth")
        assert breite <= 390 + 2, f"Die Seite läuft seitlich über ({breite}px)"
        assert pg.is_visible("#selectToggle")
        pg.click("#selectToggle")
        pg.wait_for_timeout(250)
        pg.query_selector_all(".photo-item")[0].click()
        pg.wait_for_timeout(250)
        assert pg.inner_text("#downloadCount").strip() == "1"
        assert fehler == []
    finally:
        pg.close()
