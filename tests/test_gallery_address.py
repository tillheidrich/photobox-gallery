"""Die Adresse der Galerie gehört dem Kunden.

Sie darf jederzeit heißen, wie er sie nennt — und was schon gedruckt und
verschickt ist, muss weiter ankommen. Deshalb: Umbenennen legt die alte
Adresse als Weiterleitung ab, und alles, was an der Galerie hängt (QR-Bild,
Sharepic, Druckseite, Kiosk), bedient den alten Namen ebenfalls weiter. Wer
den alten Link wirklich abschalten will, kann das — mit Ansage.
"""
import secrets

import pytest


def _event(client, name="Sommerfest"):
    client.get("/healthz")
    from app import db
    slug = "adr-" + secrets.token_hex(3)
    eid = db.create_event(name=name, slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (eid,)); con.commit(); con.close()
    return db.get_event_by_id(eid)


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    return client


def _umbenennen(admin, ev, neu, **extra):
    daten = {"csrf_token": "test-token", "name": ev["name"], "slug": neu,
             "published": "1", "retention_days": "90"}
    daten.update(extra)
    return admin.post(f"/admin/events/{ev['id']}/edit", data=daten, follow_redirects=True)


def test_umbenennen_leitet_die_alte_adresse_weiter(admin):
    ev = _event(admin)
    _umbenennen(admin, ev, "hochzeit-mia-und-tom")
    from app import db
    assert db.get_event_by_id(ev["id"])["slug"] == "hochzeit-mia-und-tom"
    alt = admin.application.test_client().get(f"/gallery/{ev['slug']}")
    assert alt.status_code == 301
    assert "/gallery/hochzeit-mia-und-tom" in alt.headers["Location"]


def test_der_alte_qr_code_und_das_sharepic_gehen_weiter(admin):
    ev = _event(admin)
    _umbenennen(admin, ev, "firmenfeier-2026")
    gast = admin.application.test_client()
    for pfad in ("/qr.png", "/sharepic.png", "/share", "/print"):
        assert gast.get(f"/gallery/{ev['slug']}{pfad}").status_code == 200, pfad
    assert gast.get(f"/kiosk/{ev['slug']}").status_code == 200


def test_wer_nicht_weiterleiten_will_muss_es_sagen(admin):
    ev = _event(admin)
    _umbenennen(admin, ev, "nur-neu", drop_old_slug="1")
    assert admin.application.test_client().get(f"/gallery/{ev['slug']}").status_code == 404


def test_eine_adresse_wird_zurechtgeschliffen(admin):
    ev = _event(admin)
    _umbenennen(admin, ev, "Müllers Groß-Fete 2026!")
    from app import db
    assert db.get_event_by_id(ev["id"])["slug"] == "muellers-gross-fete-2026"


def test_eine_vergebene_adresse_wird_nicht_genommen(admin):
    ev = _event(admin)
    fremd = _event(admin, name="Andere")
    _umbenennen(admin, ev, fremd["slug"])
    from app import db
    assert db.get_event_by_id(ev["id"])["slug"] == ev["slug"]


def test_zurueck_auf_den_alten_namen_geht(admin):
    """Zweimal hin und her: Die frühere Adresse darf wieder die aktuelle werden."""
    ev = _event(admin)
    _umbenennen(admin, ev, "zwischenname")
    from app import db
    ev2 = db.get_event_by_id(ev["id"])
    _umbenennen(admin, ev2, ev["slug"])
    assert db.get_event_by_id(ev["id"])["slug"] == ev["slug"]
    assert ev["slug"] not in db.list_slug_aliases(ev["id"])
    assert "zwischenname" in db.list_slug_aliases(ev["id"])


def test_eine_alte_adresse_laesst_sich_abschalten(admin):
    ev = _event(admin)
    _umbenennen(admin, ev, "neue-adresse")
    from app import db
    assert db.list_slug_aliases(ev["id"]) == [ev["slug"]]
    admin.post(f"/admin/events/{ev['id']}/alias/delete",
               data={"csrf_token": "test-token", "alias": ev["slug"]}, follow_redirects=True)
    assert db.list_slug_aliases(ev["id"]) == []
    assert admin.application.test_client().get(f"/gallery/{ev['slug']}").status_code == 404
