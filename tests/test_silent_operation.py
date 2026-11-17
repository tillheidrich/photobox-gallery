"""Stiller Betrieb: hochladen ja, zeigen nein.

Till fährt einen Firmenabend, bei dem die Bilder gesichert, aber nicht verteilt
werden sollen. Die Box nimmt auf, der Sync lädt hoch, im Admin ist alles da —
nach außen gibt es nichts: kein QR-Code, kein Sharepic, keine Galerie. Weder
über die Adresse noch über den Code noch über einen alten Durchgangs-Link.
"""
import json
import secrets

import pytest


#: Einstellungen, die diese Tests anfassen — sie werden vorher gesichert und
#: nachher zurückgestellt. Sonst färbt ein Test hier die Startseite aller
#: anderen Tests ein.
BERUEHRT = ("silent_mode", "homepage_show_galleries", "homepage_show_empty_events")


@pytest.fixture()
def aufgeraeumt(client):
    """Die Schalter stehen danach wieder da, wo sie vorher standen."""
    client.get("/healthz")
    from app import db
    vorher = {k: db.get_setting(k) for k in BERUEHRT}
    yield client
    for key, wert in vorher.items():
        db.set_setting(key, wert if wert is not None else "")


def _event(client, *, silent=False, name="Firmenabend"):
    client.get("/healthz")
    from app import db, gallery_settings as gsm
    slug = "still-" + secrets.token_hex(3)
    eid = db.create_event(name=name, slug=slug, upload_token=secrets.token_hex(8))
    cfg = dict(gsm.DEFAULTS)
    cfg["silent"] = silent
    con = db.get_db()
    con.execute("UPDATE events SET published=1, gallery_json=? WHERE id=?", (json.dumps(cfg), eid))
    con.commit(); con.close()
    return db.get_event_by_id(eid)


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"


def test_laut_ist_der_normalfall(aufgeraeumt):
    ev = _event(aufgeraeumt)
    assert aufgeraeumt.get(f"/gallery/{ev['slug']}").status_code == 200
    assert aufgeraeumt.get(f"/gallery/{ev['slug']}/sharepic.png").status_code == 200


def test_stilles_event_zeigt_gaesten_nichts(aufgeraeumt):
    ev = _event(aufgeraeumt, silent=True)
    for pfad in ("", "/sharepic.png", "/qr.png", "/share", "/print", "/download.zip"):
        assert aufgeraeumt.get(f"/gallery/{ev['slug']}{pfad}").status_code == 404, pfad


def test_globaler_schalter_legt_alle_still(aufgeraeumt):
    from app import silence
    ev = _event(aufgeraeumt)
    silence.set_global(True)
    assert aufgeraeumt.get(f"/gallery/{ev['slug']}").status_code == 404
    assert aufgeraeumt.get(f"/gallery/{ev['slug']}/sharepic.png").status_code == 404
    silence.set_global(False)
    assert aufgeraeumt.get(f"/gallery/{ev['slug']}").status_code == 200


def test_event_kann_den_globalen_schalter_nicht_aufmachen(aufgeraeumt):
    from app import silence
    ev = _event(aufgeraeumt, silent=False)
    silence.set_global(True)
    assert silence.is_silent(ev) is True


def test_der_betreiber_sieht_die_galerie_weiter(aufgeraeumt):
    ev = _event(aufgeraeumt, silent=True)
    _admin(aufgeraeumt)
    assert aufgeraeumt.get(f"/gallery/{ev['slug']}").status_code == 200


def test_stille_galerie_steht_auf_keiner_liste(aufgeraeumt):
    from app import db
    db.set_setting("homepage_show_galleries", "1")
    db.set_setting("homepage_show_empty_events", "1")
    laut = _event(aufgeraeumt, name="Laut")
    still = _event(aufgeraeumt, silent=True, name="Leise")
    seite = aufgeraeumt.get("/").get_data(as_text=True)
    assert laut["slug"] in seite
    assert still["slug"] not in seite


def test_code_suche_findet_die_stille_galerie_nicht(aufgeraeumt):
    ev = _event(aufgeraeumt, silent=True)
    from app import db
    code = db.get_event_by_id(ev["id"]).get("code")
    if not code:
        pytest.skip("Diese Installation vergibt keine Kurzcodes")
    antwort = aufgeraeumt.get(f"/g/{code}")
    assert f"/gallery/{ev['slug']}" not in (antwort.headers.get("Location") or "")


def test_alter_durchgangs_link_fuehrt_ins_leere(aufgeraeumt):
    from app import ecosystem
    laut = _event(aufgeraeumt)
    still = _event(aufgeraeumt, silent=True)
    uid = "abcd2345xy"
    offen = f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(dict(laut)), uid)}"
    zu = f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(dict(still)), uid)}"
    assert aufgeraeumt.get(f"/p/{offen}").status_code == 200
    assert aufgeraeumt.get(f"/p/{zu}").status_code == 404


def test_die_box_bekommt_kein_qr_und_keine_gaestemail(aufgeraeumt):
    from app import db, ecosystem
    ev = _event(aufgeraeumt, silent=True)
    dev_id = db.create_device(name="Stille Box " + secrets.token_hex(2))["id"]
    ecosystem.save_device_profile(dev_id, {}, int(ev["id"]))
    prof = ecosystem.profile_for_box(dict(db.get_device_by_id(dev_id)), "https://example.test")
    assert prof["qr"]["mode"] == "off"
    assert prof["qr_on_screen"] is False
    assert prof["print"]["qr_on_print"] is False
    assert prof["mail"]["enabled"] is False
    assert prof["event"]["silent"] is True
