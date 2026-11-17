"""Guests read where a photo came from in their words (final review U2).

The source chips of the guest gallery showed the admin's labels ("Admin-
Upload", "Import", every booth by name). Guests now get three groups:
"Fotobox" (every booth), "Gäste" (guest uploads) and "Fotograf" (admin
upload, folder import, FTP intake) — in English "Photo booth", "Guests",
"Photographer". The admin keeps the detailed chips.
"""
import io
import json
import secrets

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import db, ecosystem, gallery_settings as gsm, media, media_source


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    ecosystem.init_schema()
    db.set_setting("guest_upload_enabled", "0")
    db.set_setting("site_languages", "both")
    yield


def _jpg():
    buf = io.BytesIO()
    Image.new("RGB", (50, 40), (200, 100, 50)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _three_sources(client):
    key = secrets.token_hex(4)
    eid = db.create_event(name=f"Chips {key}", slug=f"chips-{key}", upload_token=f"tok-{key}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(
        {"guest_upload": True, "show_source_filter": True}))})
    event = db.get_event_by_id(eid)
    client.post("/api/guest-upload", data={"file": (_jpg(), "g.jpg"), "event_slug": event["slug"]},
                content_type="multipart/form-data")
    with client.application.app_context():
        media.save_upload(eid, FileStorage(_jpg(), filename="a.jpg"), source="admin")
        media.save_upload(eid, FileStorage(_jpg(), filename="i.jpg"), source="import")
    return event


def test_guests_see_guest_words_not_the_admins(client):
    event = _three_sources(client)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert "Admin-Upload" not in page and "Gast-Upload" not in page
    assert ">Gäste (1)<" in page
    assert ">Fotograf (2)<" in page                      # admin and import are one group for guests
    assert 'data-filter="src:photographer"' in page and 'data-source="photographer"' in page


def test_in_english_too(client):
    event = _three_sources(client)
    with client.session_transaction() as s:
        s["lang"] = "en"
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert ">Guests (1)<" in page and ">Photographer (2)<" in page


def test_every_booth_is_one_chip_for_guests():
    rows = [{"id": 1, "device_id": 3}, {"id": 2, "device_id": 4}, {"id": 3, "source": "guest"}]
    summary = media_source.summarize(rows, for_guests=True)
    assert [c["key"] for c in summary["chips"]] == ["booth", "guest"]
    assert summary["chips"][0]["count"] == 2


def test_the_chip_row_shows_it_scrolls_instead_of_looking_cut_off_on_a_phone():
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "public.css").read_text(encoding="utf-8")
    phone = css[css.index("Gallery head on a phone"):]
    group = phone[phone.index("\n  .filter-group {"):]
    group = group[:group.index("}")]
    assert "overflow-x: auto" in group and "min-width: 0" in group
    assert "mask-image" in group and "scroll-padding-inline-end" in group
