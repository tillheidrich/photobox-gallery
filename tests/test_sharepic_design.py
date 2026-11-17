"""Das Sharepic gestalten — global und je Event.

Marco bedient an einem Wochenende drei Kunden. Zwei bekommen das Haus-Aussehen,
einer hat eine Marke, die eingehalten werden muss. Deshalb zwei Ebenen: eine
globale Gestaltung und, wo nötig, eine eigene je Event. Leer heißt überall
„wie global" — sonst müsste man jedes Feld dreimal pflegen.
"""
import io
import secrets

import pytest
from PIL import Image


@pytest.fixture()
def admin(client):
    """Angemeldet — und am Ende steht die globale Gestaltung wieder auf Standard.

    Sonst färbt ein Test hier die Sharepics aller anderen Tests ein.
    """
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    from app import sharepic
    vorher = sharepic.global_design()
    yield client
    sharepic.save_global(vorher)


def _event(client, name="Hochzeit"):
    client.get("/healthz")
    from app import db
    slug = "sp-" + secrets.token_hex(3)
    eid = db.create_event(name=name, slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (eid,)); con.commit(); con.close()
    return db.get_event_by_id(eid)


def _bild(farbe=(200, 40, 40)):
    buf = io.BytesIO()
    Image.new("RGB", (1200, 1600), farbe).save(buf, "JPEG")
    buf.seek(0)
    return buf


# ── Global ────────────────────────────────────────────────
def test_the_defaults_are_sane(client):
    from app import sharepic
    client.get("/healthz")
    cfg = sharepic.global_design()
    assert cfg["background"] == "blur" and cfg["show_url"] is True
    assert 0 <= cfg["blur"] <= 40 and 0 <= cfg["dim"] <= 85


def test_nonsense_never_reaches_the_picture(client):
    from app import sharepic
    client.get("/healthz")
    sauber = sharepic._sauber({"background": "regenbogen", "blur": "999", "dim": "-5",
                               "bg_color": "kein-hex", "eyebrow": "x" * 300})
    assert sauber["background"] == "blur" and sauber["blur"] == 40 and sauber["dim"] == 0
    assert sauber["bg_color"] == "" and len(sauber["eyebrow"]) == 120


def test_the_form_saves_the_global_design(admin, client):
    antwort = admin.post("/admin/settings/qr", data={
        "csrf_token": "test-token", "background": "color", "bg_color": "#2b1a5c",
        "blur": "8", "dim": "60", "eyebrow": "Unsere Bilder", "title": "", "hint": "",
        "show_url": "on", "modules": "rounded", "dark": "#111111", "light": "#ffffff"})
    assert antwort.status_code in (200, 302)
    from app import sharepic
    cfg = sharepic.global_design()
    assert cfg["background"] == "color" and cfg["bg_color"] == "#2b1a5c"
    assert cfg["blur"] == 8 and cfg["dim"] == 60 and cfg["eyebrow"] == "Unsere Bilder"


def test_a_global_background_image_is_stored_in_story_size(admin, client):
    admin.post("/admin/settings/qr", data={
        "csrf_token": "test-token", "background": "image", "blur": "0", "dim": "0",
        "bg_image": (_bild(), "hintergrund.jpg"), "modules": "square"},
        content_type="multipart/form-data")
    from app import media, sharepic
    pfad = sharepic.global_design()["bg_path"]
    assert pfad
    with Image.open(media.data_dir() / pfad) as im:
        assert im.size == (1080, 1920)


# ── Je Event ──────────────────────────────────────────────
def test_an_event_overrides_only_what_it_sets(admin, client):
    from app import db, sharepic
    ev = _event(client)
    admin.post("/admin/settings/qr", data={"csrf_token": "test-token", "background": "dark",
                                           "eyebrow": "Global", "modules": "square"})
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"],
        "sp_eyebrow": "Nur dieses Event"})
    ev = db.get_event_by_id(ev["id"])
    cfg = sharepic.settings(ev)
    assert cfg["eyebrow"] == "Nur dieses Event"        # vom Event
    assert cfg["background"] == "dark"                 # von global


def test_clearing_the_event_fields_falls_back_to_global(admin, client):
    from app import db, sharepic
    ev = _event(client)
    admin.post(f"/admin/events/{ev['id']}/edit", data={"csrf_token": "test-token", "name": ev["name"],
                                                  "slug": ev["slug"], "sp_eyebrow": "Eigenes"})
    assert sharepic.event_design(db.get_event_by_id(ev["id"]))["eyebrow"] == "Eigenes"
    admin.post(f"/admin/events/{ev['id']}/edit", data={"csrf_token": "test-token", "name": ev["name"],
                                                  "slug": ev["slug"], "sp_eyebrow": ""})
    assert sharepic.event_design(db.get_event_by_id(ev["id"])) == {}


def test_the_picture_really_changes(admin, client):
    """Nicht nur gespeichert — auch gezeichnet."""
    from app import db, sharepic
    ev = _event(client)
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"],
        "sp_background": "color", "sp_bg_color": "#eaff00"})
    ev = db.get_event_by_id(ev["id"])
    png = sharepic.render(ev, "https://example.de/gallery/x")
    with Image.open(io.BytesIO(png)) as im:
        assert im.getpixel((20, 20))[:3] == (234, 255, 0)


def test_a_bright_background_gets_dark_text(admin, client):
    """Sonst schreibt eine helle Kundenfarbe den Titel unlesbar."""
    from app import db, sharepic
    ev = _event(client, name="Sommer")
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"],
        "sp_background": "color", "sp_bg_color": "#f5d8a8"})
    ev = db.get_event_by_id(ev["id"])
    bild, hell = sharepic._hintergrund(ev, sharepic.settings(ev))
    assert hell is True


def test_the_title_can_be_replaced(admin, client):
    from app import db, sharepic
    ev = _event(client, name="Interner Name GmbH")
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"],
        "sp_title": "Lina & Jan"})
    ev = db.get_event_by_id(ev["id"])
    assert sharepic.settings(ev)["title"] == "Lina & Jan"


def test_the_preview_in_the_admin_answers(admin, client):
    _event(client)
    antwort = admin.get("/admin/settings/sharepic-preview.png")
    assert antwort.status_code == 200 and antwort.mimetype == "image/png"
    with Image.open(io.BytesIO(antwort.data)) as im:
        assert im.size == (1080, 1920)


def test_the_title_can_sit_left_or_right(admin, client):
    """Manche Marken setzen alles links — dann sähe ein zentrierter Titel falsch aus."""
    from app import db, sharepic
    ev = _event(client, name="Lina und Jan")
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"], "sp_align": "left"})
    ev = db.get_event_by_id(ev["id"])
    assert sharepic.settings(ev)["align"] == "left"

    # und es steht auch wirklich woanders
    links = sharepic.render(ev, "https://example.de/x")
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"], "sp_align": "right"})
    rechts = sharepic.render(db.get_event_by_id(ev["id"]), "https://example.de/x")
    assert links != rechts


def test_an_invented_alignment_falls_back_to_centre(client):
    from app import sharepic
    client.get("/healthz")
    assert sharepic._sauber({"align": "schraeg"})["align"] == "center"
