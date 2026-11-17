"""Farbmarkierungen — und wer sie überhaupt zu sehen bekommt.

Till am 19.09.2026: „Das Markieren von Fotos in der Testgalerie klappt nicht."
Es klappte tatsächlich nicht, und zwar so: Der Knopf erscheint nur im
Abstimmungsmodus, in den Modus kommt man nur über einen Schalter, und den
zeigt die Galerie nur, wenn ``allow_mode_switch`` an ist — Vorgabe: aus. In
einer Standardgalerie war die Funktion damit für niemanden erreichbar, auch
nicht für den Betreiber. Die Schnittstelle erlaubte ihm das Markieren seit
jeher („Admin immer"), nur die Oberfläche bot es nie an.
"""
import json
import secrets

import pytest

from app import db, ecosystem, gallery_settings as gsm


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _galerie(client, **gs_werte):
    kennung = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name="Markieren", slug=f"mark-{kennung}",
                              upload_token=f"tok-{kennung}")
        db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs_werte))})
        con = db.get_db()
        cur = con.execute(
            "INSERT INTO media (event_id, filename, path_original, path_webp, kind, "
            "share_token, created_at) VALUES (?,?,?,?,?,?,datetime('now'))",
            (eid, "f.jpg", f"storage/mark-{kennung}/orig/f.jpg",
             f"storage/mark-{kennung}/webp/f.jpg", "photo", secrets.token_hex(8)))
        con.commit()
        mid = int(cur.lastrowid)
        con.close()
        return db.get_event_by_id(eid), mid


def _als_admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"


# ── Der Betreiber ─────────────────────────────────────────────────────
def test_the_operator_can_mark_in_a_plain_gallery(client):
    """Vorgabe-Einstellungen, angemeldet — der Knopf muss da sein."""
    event, mid = _galerie(client)
    _als_admin(client)
    seite = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert "tag-btn" in seite, "Kein Markieren-Knopf an der Kachel"
    assert 'id="tagMenu"' in seite, "Ohne Menü tut der Knopf nichts"


def test_the_operator_may_switch_to_proofing_even_when_guests_may_not(client):
    event, _ = _galerie(client)
    _als_admin(client)
    assert "mode=proofing" in client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    antwort = client.get(f"/gallery/{event['slug']}?mode=proofing")
    assert antwort.status_code == 302
    with client.session_transaction() as s:
        assert s.get(f"gallery_mode_{event['id']}") == "proofing"


def test_the_mark_actually_sticks(client):
    event, mid = _galerie(client)
    _als_admin(client)
    antwort = client.post(f"/api/photo/{mid}/tag", json={"tag": "green"})
    assert antwort.status_code == 200
    with client.application.app_context():
        assert db.get_media(mid)["color_tag"] == "green"


# ── Die Gäste ─────────────────────────────────────────────────────────
def test_a_guest_sees_no_marking_in_a_plain_gallery(client):
    """Die Gegenprobe: Ohne Freigabe markiert kein Gast."""
    event, _ = _galerie(client)
    seite = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert "tag-btn" not in seite
    assert 'id="tagMenu"' not in seite


def test_a_guest_may_not_set_a_mark_in_a_plain_gallery(client):
    event, mid = _galerie(client)
    antwort = client.post(f"/api/photo/{mid}/tag", json={"tag": "red"})
    assert antwort.status_code == 403
    with client.application.app_context():
        assert not (db.get_media(mid)["color_tag"] or "")


def test_a_guest_may_mark_where_the_operator_allowed_it(client):
    event, mid = _galerie(client, mode="proofing", color_tags=True)
    seite = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert "tag-btn" in seite and 'id="tagMenu"' in seite
    assert client.post(f"/api/photo/{mid}/tag", json={"tag": "red"}).status_code == 200


def test_switching_the_colours_off_hides_them_from_everyone(client):
    event, _ = _galerie(client, mode="proofing", color_tags=False)
    _als_admin(client)
    seite = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert "tag-btn" not in seite


# ── Bedienbarkeit des Farbmenüs (Durchsicht 19.09.2026) ───────────────

def _mit_menue(client):
    event, _ = _galerie(client, mode="proofing", color_tags=True)
    return client.get(f"/gallery/{event['slug']}").get_data(as_text=True)


def test_das_farbmenue_sagt_dass_es_eines_ist(client):
    seite = _mit_menue(client)
    assert 'aria-haspopup="true"' in seite and 'aria-controls="tagMenu"' in seite
    assert 'role="menu"' in seite and 'role="menuitem"' in seite


def test_das_farbmenue_laesst_sich_mit_der_tastatur_bedienen(client):
    """Es ging nur mit der Maus: kein Fokus im Menü, kein Weg heraus."""
    menue = _mit_menue(client)
    menue = menue[menue.index("getElementById('tagMenu')"):]
    assert "erstes.focus()" in menue, "Der Fokus springt nicht ins Menü"
    assert "'Escape'" in menue and "knopf.focus()" in menue, "Kein Weg zurück"
    assert "ArrowDown" in menue, "Keine Pfeiltasten"


def test_der_knopf_verraet_nicht_laenger_den_falschen_zustand(client):
    """``aria-expanded`` blieb auf ``false``, während das Menü offen stand."""
    menue = _mit_menue(client)
    assert 'aria-expanded="false"' in menue
    assert "setAttribute('aria-expanded', 'true')" in menue
