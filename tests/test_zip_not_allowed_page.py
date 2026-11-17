"""A guest who may not download the ZIP gets a page that says so (final review U4).

``/gallery/<slug>/download.zip`` answered a bare 403 when "Alles
herunterladen" is off for guests (or the gallery shows a watermark). Now the
status stays 403, but the page explains it in the guest's language and leads
back to the gallery, where single photos can still be saved.
"""
import json
import secrets

import pytest

from app import db, gallery_settings as gsm


@pytest.fixture()
def closed_zip(client):
    client.get("/healthz")
    db.set_setting("zip_download_enabled", "0")
    db.set_setting("site_languages", "both")
    k = secrets.token_hex(3)
    eid = db.create_event(name=f"Zip {k}", slug=f"zip-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean({"download_zip": False}))})
    return db.get_event_by_id(eid)


def test_the_guest_reads_why_in_german(client, closed_zip):
    r = client.get(f"/gallery/{closed_zip['slug']}/download.zip")
    assert r.status_code == 403
    page = r.get_data(as_text=True)
    assert r.mimetype == "text/html"
    assert "Alle Fotos auf einmal" in page
    assert f'href="/gallery/{closed_zip["slug"]}"' in page


def test_the_guest_reads_why_in_english(client, closed_zip):
    with client.session_transaction() as s:
        s["lang"] = "en"
    page = client.get(f"/gallery/{closed_zip['slug']}/download.zip").get_data(as_text=True)
    assert "all photos at once" in page.lower()


def test_a_watermarked_gallery_explains_it_too(client, closed_zip):
    db.update_event(closed_zip["id"], {"gallery_json": json.dumps(gsm.clean({"download_zip": True, "watermark": True}))})
    r = client.get(f"/gallery/{closed_zip['slug']}/download.zip")
    assert r.status_code == 403 and "Alle Fotos auf einmal" in r.get_data(as_text=True)
