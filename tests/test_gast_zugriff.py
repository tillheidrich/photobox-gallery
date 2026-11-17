"""Was ein Gast sehen darf — und was er mit Tricks nicht sehen können soll.

Die Zusage lautet: Wer den QR-Code seines Durchgangs hat, bekommt seine
Aufnahmen. Sonst niemand. Diese Datei greift das an, statt es zu behaupten:
geratene Kennungen, gefälschte Signaturen, Signaturen eines anderen Events,
Pfade mit ``..``, direkte Dateizugriffe an der Seite vorbei.

Ein Punkt ist bewusst kein Angriff, sondern eine Einstellung: Eine
veröffentlichte Event-Galerie zeigt allen alles — das ist der Sinn einer
Hochzeitsgalerie. Der Test hält fest, dass es genau an diesem Schalter hängt
und an keinem anderen.
"""
import io
import json
import secrets

import pytest
from PIL import Image


def _jpeg(color=(120, 160, 200), size=(800, 1000)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _uid() -> str:
    """Jeder Test seine eigene Kennung.

    Die Galerie bindet eine Kennung an Event und Gerät und weist eine zweite
    Verwendung mit 409 ab — selbst eine Schutzmaßnahme, aber sie macht
    wiederverwendete Kennungen in Tests unbrauchbar.
    """
    from booth.boothcore import share
    return share.new_uid()


def _event(client, *, published=1):
    client.get("/healthz")
    from app import db, ecosystem
    slug = "gast-" + secrets.token_hex(3)
    eid = db.create_event(name="Gasttest", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=? WHERE id=?", (published, eid))
    con.commit(); con.close()
    _made = db.create_device(name="Box " + secrets.token_hex(2))
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo"]}, eid)
    return db.get_event_by_id(eid), {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _code(event, uid):
    from app import ecosystem
    return f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"


def _hochladen(client, event, dev, uid, farbe=(120, 160, 200)):
    r = client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                    content_type="multipart/form-data",
                    data={"uid": uid, "session_uid": uid, "kind": "photo", "variant": "web",
                          "event_slug": event["slug"], "file": (_jpeg(farbe), "result_web.jpg"),
                          "meta": json.dumps({"taken_at": "2026-09-20T20:00:00"})})
    assert r.status_code == 200, r.data
    from app import db
    return [m for m in db.list_media(event["id"]) if m["session_uid"] == uid][0]


# ── Raten und Fälschen ────────────────────────────────────────────────

def test_ohne_signatur_gibt_es_nichts(client):
    event, dev = _event(client)
    uid = _uid()
    _hochladen(client, event, dev, uid)
    assert client.get(f"/p/{uid}").status_code == 404
    assert client.get(f"/p/{uid}-").status_code == 404


@pytest.mark.parametrize("gefaelscht", [
    "aaaaaaaa", "AAAAAAAA", "1", "z" * 8, "z" * 64, "../../etc/passwd",
])
def test_eine_falsche_signatur_fuehrt_ins_leere(client, gefaelscht):
    event, dev = _event(client)
    uid = _uid()
    _hochladen(client, event, dev, uid)
    assert client.get(f"/p/{uid}-{gefaelscht}").status_code == 404


def test_eine_abgeschnittene_signatur_genuegt_nicht(client):
    """Sonst wäre die Signatur so kurz, wie der Angreifer sie macht."""
    event, dev = _event(client)
    uid = _uid()
    _hochladen(client, event, dev, uid)
    echt = _code(event, uid).rsplit("-", 1)[1]
    for laenge in range(1, len(echt)):
        assert client.get(f"/p/{uid}-{echt[:laenge]}").status_code == 404


def test_die_signatur_eines_anderen_events_zaehlt_nicht(client):
    """Zwei Events auf derselben Instanz dürfen sich nicht gegenseitig öffnen."""
    a, dev_a = _event(client)
    b, dev_b = _event(client)
    uid = _uid()
    _hochladen(client, a, dev_a, uid)
    from app import ecosystem
    fremd = ecosystem.signature(ecosystem.ensure_share_key(b), uid)
    seite = client.get(f"/p/{uid}-{fremd}")
    # Die Signatur gehört zu Event B, die Aufnahme zu Event A: Der Gast landet
    # nie auf den Bildern von A.
    if seite.status_code == 200:
        assert b"result_web" not in seite.data


def test_die_eigene_signatur_oeffnet_keine_fremde_kennung(client):
    event, dev = _event(client)
    meins, fremd = _uid(), _uid()
    _hochladen(client, event, dev, meins)
    _hochladen(client, event, dev, fremd, farbe=(20, 200, 20))
    meine_sig = _code(event, meins).rsplit("-", 1)[1]
    assert client.get(f"/p/{fremd}-{meine_sig}").status_code == 404


def test_die_eigene_seite_zeigt_nur_die_eigene_aufnahme(client):
    event, dev = _event(client)
    meins, fremd = _uid(), _uid()
    meine = _hochladen(client, event, dev, meins)
    fremde = _hochladen(client, event, dev, fremd, farbe=(20, 200, 20))
    seite = client.get(f"/p/{_code(event, meins)}")
    assert seite.status_code == 200
    text = seite.get_data(as_text=True)
    # The page names a picture as its download does (17.11.2026), not by the box's storage id.
    assert f"{event['slug']}-0001.jpg" in text
    assert meine["filename"] not in text
    assert f"{event['slug']}-0002.jpg" not in text and fremde["filename"] not in text, \
        "Die Seite verrät die Aufnahme eines anderen Gastes"


# ── Dateien direkt ────────────────────────────────────────────────────

@pytest.mark.parametrize("pfad", [
    "../app.sqlite", "storage/../../etc/passwd", "storage/..%2f..%2fapp.sqlite",
    "app.sqlite", "cache/irgendwas.jpg", "assets/box/profile.json",
])
def test_an_der_galerie_vorbei_kommt_man_nicht_an_die_daten(client, pfad):
    _event(client)
    assert client.get(f"/data/{pfad}").status_code in (400, 404)


def test_ohne_veroeffentlichung_ist_die_datei_eines_anderen_gastes_dicht(client):
    """Der Fall für Firmenveranstaltungen: Galerie nicht öffentlich."""
    event, dev = _event(client, published=0)
    fremd = _hochladen(client, event, dev, _uid())
    frisch = client.application.test_client()          # anderer Gast, eigene Sitzung
    assert frisch.get(f"/data/{fremd['path_webp'] or fremd['path_original']}").status_code == 404


def test_der_eigene_qr_code_oeffnet_die_eigene_datei_auch_ohne_veroeffentlichung(client):
    event, dev = _event(client, published=0)
    uid = _uid()
    meine = _hochladen(client, event, dev, uid)
    assert client.get(f"/p/{_code(event, uid)}").status_code == 200   # merkt sich die Kennung
    assert client.get(f"/data/{meine['path_webp'] or meine['path_original']}").status_code == 200


def test_der_qr_code_eines_gastes_oeffnet_nicht_die_datei_des_naechsten(client):
    """Der eigentliche Trick, den jemand versuchen würde."""
    event, dev = _event(client, published=0)
    meins, fremd = _uid(), _uid()
    _hochladen(client, event, dev, meins)
    fremde = _hochladen(client, event, dev, fremd, farbe=(20, 200, 20))
    assert client.get(f"/p/{_code(event, meins)}").status_code == 200
    antwort = client.get(f"/data/{fremde['path_webp'] or fremde['path_original']}")
    assert antwort.status_code == 404, "Eine gemerkte Kennung öffnet die Dateien der anderen"


# ── Löschen ───────────────────────────────────────────────────────────

def test_loeschen_trifft_nur_den_eigenen_durchgang(client, formular):
    event, dev = _event(client)
    meins, fremd = _uid(), _uid()
    _hochladen(client, event, dev, meins)
    _hochladen(client, event, dev, fremd, farbe=(20, 200, 20))
    code = _code(event, meins)
    client.get(f"/p/{code}")
    formular(f"/p/{code}/delete", von=f"/p/{code}")
    from app import db
    uebrig = {m["session_uid"] for m in db.list_media(event["id"])}
    assert fremd in uebrig and meins not in uebrig


def test_mit_fremder_signatur_laesst_sich_nichts_loeschen(client, formular):
    event, dev = _event(client)
    fremd = _uid()
    _hochladen(client, event, dev, fremd)
    antwort = formular(f"/p/{fremd}-aaaaaaaa/delete", von=f"/p/{_code(event, fremd)}")
    assert antwort.status_code in (403, 404)
    from app import db
    assert [m for m in db.list_media(event["id"]) if m["session_uid"] == fremd]


# ── Wie schwer ist Raten überhaupt ────────────────────────────────────

def test_die_kennung_ist_nicht_zu_erraten():
    """Zehn Zeichen aus 31 sind rund 2^49 — Durchprobieren ist keine Option."""
    from booth.boothcore import share
    kennungen = {share.new_uid() for _ in range(500)}
    assert len(kennungen) == 500, "Zwei gleiche Kennungen in 500 Zügen"
    eine = share.new_uid()
    assert len(eine) >= 10
    assert set(eine) <= set(share._ALPHABET)
    assert not (set("01ilo") & set(share._ALPHABET)), "Verwechselbare Zeichen gehören nicht hinein"


def test_die_signatur_haengt_am_schluessel_des_events():
    from booth.boothcore import share
    a = share.signature("schluessel-a", _uid())
    b = share.signature("schluessel-b", _uid())
    assert a != b and len(a) == 8


def test_die_pruefung_vergleicht_in_gleicher_zeit():
    """Ein Vergleich mit == verrät über die Laufzeit, wie viel schon stimmt."""
    quelle = (__import__("pathlib").Path(__file__).resolve().parents[1]
              / "app" / "ecosystem.py").read_text(encoding="utf-8")
    stelle = quelle.index("def find_event_for_share")
    block = quelle[stelle:stelle + 1200]
    assert "compare_digest" in block, "Signaturvergleich ohne compare_digest"


# ── Galerie-Art: eine Entscheidung, keine Voreinstellung (20.09.2026) ──
# Bis dahin stand im Anlege-Weg der Box hart published=1. Eine Box, die ihr
# Event selbst anlegt — der Normalfall ohne Netz —, machte damit die Galerie
# für jeden lesbar, der die Adresse kennt. Ohne dass irgendwer das gewählt
# hätte, und ohne dass es irgendwo stand.

def _box_legt_event_an(client, galerie=None):
    client.get("/healthz")
    from app import db, ecosystem
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    claim = {"name": "Firmenfeier " + secrets.token_hex(2),
             "share_key": secrets.token_urlsafe(24)}
    if galerie is not None:
        claim["galerie"] = galerie
    r = client.post("/api/v2/booth/events", json=claim,
                    headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200, r.data
    slug = r.get_json()["event"]["slug"]
    return db.get_event_by_slug(slug), {**db.get_device_by_id(dev["id"]), "api_key": dev["api_key"]}


def test_ohne_angabe_legt_die_box_eine_private_galerie_an():
    """Der sichere Zustand ist der voreingestellte, nicht der bequeme."""
    from app import create_app
    app = create_app(); app.config.update(TESTING=True)
    c = app.test_client()
    event, _ = _box_legt_event_an(c)
    assert not event["published"], "Die Box veröffentlicht die Galerie ungefragt"


def test_gemeinsam_veroeffentlicht_ausdruecklich():
    from app import create_app
    app = create_app(); app.config.update(TESTING=True)
    c = app.test_client()
    event, _ = _box_legt_event_an(c, galerie="gemeinsam")
    assert event["published"] == 1


def test_privat_heisst_nicht_dass_der_gast_nichts_bekommt(client):
    """Der wichtigste Satz dieser Entscheidung — sonst wird sie nie gewählt."""
    event, dev = _box_legt_event_an(client, galerie="privat")
    uid = _uid()
    meine = _hochladen(client, event, dev, uid)
    seite = client.get(f"/p/{_code(event, uid)}")
    assert seite.status_code == 200
    assert client.get(f"/data/{meine['path_webp'] or meine['path_original']}").status_code == 200


def test_bei_privat_sieht_ein_fremder_die_datei_nicht(client):
    event, dev = _box_legt_event_an(client, galerie="privat")
    fremde = _hochladen(client, event, dev, _uid())
    frisch = client.application.test_client()
    assert frisch.get(f"/data/{fremde['path_webp'] or fremde['path_original']}").status_code == 404
