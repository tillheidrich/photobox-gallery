"""Der Weg in eine geschützte Galerie.

Drei Dinge, alle abschaltbar:

1. Das Passwort darf im QR- und Sharepic-Link mitreisen — wer an der Box war,
   tippt nichts. Wer nur die Adresse kennt, steht weiter vor dem Feld.
2. Wer den Link hat, aber nicht das Passwort, kann Zugriff anfragen. Der
   Betreiber bekommt eine Nachricht und entscheidet.
3. Freigeben heißt: Der Gast bekommt einen Link, der ohne Passwort öffnet.
"""
import json
import secrets

import pytest


@pytest.fixture()
def post(client):
    """Ein Postausgang, der nichts verschickt, aber alles mitschreibt."""
    client.get("/healthz")
    from app import db, emailer
    db.set_setting("admin_email", "chef@example.test")
    gesendet = []
    echt = emailer.send_text_mail
    echt_konf = emailer.smtp_configured
    emailer.send_text_mail = lambda **kw: gesendet.append(kw)
    emailer.smtp_configured = lambda: True
    yield gesendet
    emailer.send_text_mail = echt
    emailer.smtp_configured = echt_konf
    db.set_setting("admin_email", "")


def _event(client, *, passwort="geheim1234", link_password=False, access_request=False):
    client.get("/healthz")
    from app import db, gallery_settings as gsm
    slug = "zug-" + secrets.token_hex(3)
    eid = db.create_event(name="Hochzeit", slug=slug, upload_token=secrets.token_hex(8))
    cfg = dict(gsm.DEFAULTS)
    cfg["link_password"] = link_password
    cfg["access_request"] = access_request
    con = db.get_db()
    con.execute("UPDATE events SET published=1, gallery_json=? WHERE id=?", (json.dumps(cfg), eid))
    con.commit(); con.close()
    if passwort:
        db.set_event_password(eid, passwort)
    return db.get_event_by_id(eid)


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"


# ── 1. Passwort im Link ───────────────────────────────────────

def test_ohne_schalter_traegt_der_qr_code_kein_passwort(client):
    ev = _event(client)
    with client.application.test_request_context():
        from app.blueprints.public import gallery_link
        assert "access_token" not in gallery_link(ev)


def test_mit_schalter_traegt_der_qr_code_den_schluessel(client):
    ev = _event(client, link_password=True)
    with client.application.test_request_context():
        from app.blueprints.public import gallery_link
        url = gallery_link(ev)
    assert f"access_token={ev['access_token']}" in url


def test_ohne_passwort_bleibt_der_link_sauber(client):
    """Ein Schlüssel an einer offenen Galerie wäre nur Ballast."""
    ev = _event(client, passwort="", link_password=True)
    with client.application.test_request_context():
        from app.blueprints.public import gallery_link
        assert "access_token" not in gallery_link(ev)


def _ist_passwortseite(antwort) -> bool:
    return "Passwort eingeben" in antwort.get_data(as_text=True)


def test_der_schluessel_oeffnet_die_galerie_der_name_allein_nicht(client):
    ev = _event(client, link_password=True)
    frisch = client.application.test_client()
    assert _ist_passwortseite(frisch.get(f"/gallery/{ev['slug']}"))
    mit = frisch.get(f"/gallery/{ev['slug']}?access_token={ev['access_token']}")
    assert mit.status_code == 200 and not _ist_passwortseite(mit)


def test_ein_falscher_schluessel_oeffnet_nichts(client):
    ev = _event(client, link_password=True)
    frisch = client.application.test_client()
    assert _ist_passwortseite(frisch.get(f"/gallery/{ev['slug']}?access_token=" + "0" * 32))


# ── 2. Zugriff anfragen ───────────────────────────────────────

def test_der_knopf_erscheint_nur_wenn_alles_stimmt(client, post):
    from app import access
    aus = _event(client, access_request=False)
    an = _event(client, access_request=True)
    offen = _event(client, passwort="", access_request=True)
    assert access.enabled(aus) is False
    assert access.enabled(an) is True
    assert access.enabled(offen) is False          # ohne Passwort gibt es nichts anzufragen


def test_ohne_postausgang_kein_knopf(client):
    from app import access, db
    db.set_setting("admin_email", "")
    ev = _event(client, access_request=True)
    assert access.enabled(ev) is False


def test_der_knopf_steht_auf_der_passwortseite(client, post):
    ev = _event(client, access_request=True)
    seite = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert "/zugang" in seite


def test_eine_anfrage_erreicht_den_betreiber(client, post):
    ev = _event(client, access_request=True)
    antwort = client.post(f"/gallery/{ev['slug']}/zugang",
                          data={"email": "gast@example.test", "name": "Mia", "note": "Bin die Schwester"})
    assert antwort.status_code == 200
    assert "die Anfrage ist unterwegs" in antwort.get_data(as_text=True)
    assert post and post[-1]["to_email"] == "chef@example.test"
    assert "gast@example.test" in post[-1]["body"]

    from app import access
    offen = access.list_open(ev["id"])
    assert len(offen) == 1 and offen[0]["email"] == "gast@example.test"


def test_zweimal_fragen_legt_nur_eine_anfrage_an(client, post):
    ev = _event(client, access_request=True)
    for _ in range(2):
        client.post(f"/gallery/{ev['slug']}/zugang", data={"email": "gast@example.test"})
    from app import access
    assert len(access.list_open(ev["id"])) == 1


def test_eine_kaputte_adresse_wird_abgewiesen(client, post):
    ev = _event(client, access_request=True)
    antwort = client.post(f"/gallery/{ev['slug']}/zugang", data={"email": "keine-adresse"})
    assert antwort.status_code == 400
    from app import access
    assert access.list_open(ev["id"]) == []


def test_ohne_schalter_gibt_es_die_route_nicht(client, post):
    ev = _event(client, access_request=False)
    assert client.post(f"/gallery/{ev['slug']}/zugang", data={"email": "gast@example.test"}).status_code == 404


def test_im_stillen_betrieb_gibt_es_keine_anfragen(client, post):
    from app import db, gallery_settings as gsm
    ev = _event(client, access_request=True)
    cfg = gsm.parse(ev["gallery_json"]); cfg["silent"] = True
    con = db.get_db(); con.execute("UPDATE events SET gallery_json=? WHERE id=?", (json.dumps(cfg), ev["id"])); con.commit(); con.close()
    assert client.post(f"/gallery/{ev['slug']}/zugang", data={"email": "gast@example.test"}).status_code == 404


# ── 3. Freigeben und ablehnen ─────────────────────────────────

def test_freigeben_schickt_einen_link_der_ohne_passwort_oeffnet(client, post):
    from app import access
    ev = _event(client, access_request=True)
    client.post(f"/gallery/{ev['slug']}/zugang", data={"email": "gast@example.test"})
    req = access.list_open(ev["id"])[0]

    _admin(client)
    antwort = client.post(f"/admin/events/{ev['id']}/access/{req['id']}/grant",
                          data={"csrf_token": "test-token"}, follow_redirects=True)
    assert antwort.status_code == 200
    mail = post[-1]
    assert mail["to_email"] == "gast@example.test"
    assert f"access_token={ev['access_token']}" in mail["body"]
    assert access.list_open(ev["id"]) == []

    # Und der Link tut, was die Mail verspricht.
    url = [zeile.strip() for zeile in mail["body"].splitlines() if "access_token=" in zeile][0]
    frisch = client.application.test_client()
    assert frisch.get(url[url.index("/gallery/"):]).status_code == 200


def test_ablehnen_sagt_auch_ab(client, post):
    from app import access
    ev = _event(client, access_request=True)
    client.post(f"/gallery/{ev['slug']}/zugang", data={"email": "gast@example.test"})
    req = access.list_open(ev["id"])[0]
    _admin(client)
    client.post(f"/admin/events/{ev['id']}/access/{req['id']}/deny",
                data={"csrf_token": "test-token"}, follow_redirects=True)
    assert access.list_open(ev["id"]) == []
    assert post[-1]["to_email"] == "gast@example.test"


def test_geht_die_mail_nicht_raus_bleibt_die_anfrage_offen(client, post):
    from app import access, emailer
    ev = _event(client, access_request=True)
    client.post(f"/gallery/{ev['slug']}/zugang", data={"email": "gast@example.test"})
    req = access.list_open(ev["id"])[0]

    def kaputt(**kw):
        raise RuntimeError("SMTP down")
    echt, emailer.send_text_mail = emailer.send_text_mail, kaputt
    try:
        _admin(client)
        client.post(f"/admin/events/{ev['id']}/access/{req['id']}/grant",
                    data={"csrf_token": "test-token"}, follow_redirects=True)
    finally:
        emailer.send_text_mail = echt
    assert len(access.list_open(ev["id"])) == 1


def test_die_antwort_kommt_in_der_sprache_des_gastes(client, post):
    from app import access, db
    db.set_setting("site_languages", "both")
    ev = _event(client, access_request=True)
    client.post("/lang/en")
    client.post(f"/gallery/{ev['slug']}/zugang", data={"email": "guest@example.test"})
    req = access.list_open(ev["id"])[0]
    assert req["lang"] == "en"
    _admin(client)
    client.post(f"/admin/events/{ev['id']}/access/{req['id']}/grant",
                data={"csrf_token": "test-token"}, follow_redirects=True)
    assert "Hello" in post[-1]["body"]
    client.post("/lang/de")
