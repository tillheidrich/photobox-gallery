"""Every page a guest can land on speaks the language the guest chose.

The UI review of 12.11.2026 switched a browser to English and found four
pages that stayed German: the QR page of a run (/p/...), a shared link
(/l/...), the 404 page, and - in the admin - the booth pages (profile,
templates, media), whose language switch showed "DE" while they were German.
They belong to blueprints that never set the language; the gallery's own
pages did.
"""
import secrets

import pytest

from app import db, ecosystem, sharelinks


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    db.set_setting("site_languages", "both")
    yield


def _english(client):
    client.post("/lang/en", headers={"Referer": "http://localhost/"})


def _event():
    k = secrets.token_hex(4)
    eid = db.create_event(name="Feier", slug=f"lg-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1})
    return db.get_event_by_id(eid)


def test_the_404_page_is_english_for_an_english_visitor(client):
    _english(client)
    r = client.get("/this-address-does-not-exist")
    assert r.status_code == 404
    html = r.get_data(as_text=True)
    assert '<html lang="en"' in html
    assert "Zur Startseite" not in html


def test_a_shared_link_is_english_for_an_english_visitor(client):
    link = sharelinks.create(_event()["id"], title="Family", password="secret-words")
    _english(client)
    html = client.get(f"/l/{link['slug']}").get_data(as_text=True)
    assert '<html lang="en"' in html
    assert "Fotos ansehen" not in html


def test_the_qr_page_of_a_run_is_english_for_an_english_visitor(client):
    _english(client)
    r = client.get("/p/abcdefghij-abcdefgh")
    html = r.get_data(as_text=True)
    assert '<html lang="en"' in html


def test_the_booth_admin_pages_follow_the_admin_language(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
        s["admin_epoch"] = accounts.session_epoch()
        s["lang"] = "en"
    html = client.get("/admin/vorlagen").get_data(as_text=True)
    # The switch offers the other language: an English page switches to
    # German. Without a language the page switched to English while it
    # was already showing "DE" on the button.
    assert 'action="/lang/de"' in html
