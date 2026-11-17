"""Every picture also without its frame, offered as such.

Owner's expectation (15.11.2026): guests get the collage sheet, the single
pictures and each picture also without the frame, clearly offered. Until
then the unframed capture (``upload_originals``, kind ``original``) showed up
as one more loose picture after the others, and "Original" under a photo
downloaded the framed full-size file.

Pairing rule, from the ids the booth uses: ``<uid>.<i>`` (unframed capture)
belongs to ``<uid>.f<i>`` (framed single of a collage) and ``<uid>.1`` to
``<uid>`` when that is a photo. On the run's page (``/p/<code>``) and in the
gallery's large view such a picture offers "Mit Rahmen" and "Ohne Rahmen";
the capture is no longer a tile of its own. Captures without a framed
counterpart stay listed. When the booth says no frame was applied
(meta ``frame: false``), one download is enough.
"""
import io
import json
import re
import secrets

from PIL import Image


def _jpeg(colour=(200, 140, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (600, 750), colour).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _box(client):
    client.get("/healthz")
    from app import db, ecosystem, gallery_settings as gsm
    ecosystem.init_schema()
    slug = "rahmen-" + secrets.token_hex(3)
    eid = db.create_event(name="Rahmen", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=1, gallery_json=? WHERE id=?",
                (json.dumps(gsm.clean({"download_single": True, "download_original": True})), eid))
    con.commit()
    con.close()
    _made = db.create_device(name="Box Rahmen")
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo", "collage"]}, eid)
    return db.get_event_by_id(eid), {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _upload(client, dev, event, uid, run, kind, variant="web", frame=True, colour=(200, 140, 30)):
    r = client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                    content_type="multipart/form-data",
                    data={"uid": uid, "session_uid": run, "kind": kind, "variant": variant,
                          "event_slug": event["slug"], "file": (_jpeg(colour), "r.jpg"),
                          "meta": json.dumps({"mode": "photo", "frame": frame})})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["id"]


def _page(client, event, run):
    from app import ecosystem
    code = f"{run}-{ecosystem.signature(ecosystem.ensure_share_key(event), run)}"
    return client.get(f"/p/{code}").get_data(as_text=True), code


def test_the_pairing_rule():
    from app.ecosystem import unframed_partners
    rows = [
        {"id": 1, "uid": "abc", "kind": "photo", "event_id": 7, "meta_json": "{}"},
        {"id": 2, "uid": "abc.1", "kind": "original", "event_id": 7},
        {"id": 3, "uid": "col", "kind": "collage", "event_id": 7, "meta_json": "{}"},
        {"id": 4, "uid": "col.f1", "kind": "photo", "event_id": 7, "meta_json": "{}"},
        {"id": 5, "uid": "col.1", "kind": "original", "event_id": 7},
        {"id": 6, "uid": "col.2", "kind": "original", "event_id": 7},      # no framed single 2
        {"id": 7, "uid": "gif", "kind": "gif", "event_id": 7, "meta_json": "{}"},
        {"id": 8, "uid": "gif.1", "kind": "original", "event_id": 7},      # never paired with a GIF
        {"id": 9, "uid": "nof", "kind": "photo", "event_id": 7, "meta_json": '{"frame": false}'},
        {"id": 10, "uid": "nof.1", "kind": "original", "event_id": 7},
    ]
    partner_of, absorbed = unframed_partners(rows)
    assert {k: v["id"] for k, v in partner_of.items()} == {1: 2, 4: 5}
    assert absorbed == {2, 5, 10}


def test_the_run_page_offers_with_and_without_frame(client):
    event, dev = _box(client)
    run = "run" + secrets.token_hex(4)
    framed = _upload(client, dev, event, run, run, "photo")
    _upload(client, dev, event, run, run, "photo", variant="original")
    raw = _upload(client, dev, event, f"{run}.1", run, "original", variant="original", colour=(10, 20, 30))
    html, code = _page(client, event, run)
    assert "Mit Rahmen" in html and "Ohne Rahmen" in html
    assert f"/photo/{framed}/download?size=original&amp;code={code}" in html or \
        f"/photo/{framed}/download?size=original&code={code}" in html
    assert re.search(rf"/photo/{raw}/download\?size=original&(amp;)?code=", html)
    assert html.count('class="sess-item"') == 1, "the capture is not a second loose picture"
    # And the link really delivers the unframed capture to the guest
    r = client.get(f"/gallery/{event['slug']}/photo/{raw}/download?size=original&code={code}")
    assert r.status_code == 200
    px = Image.open(io.BytesIO(r.data)).convert("RGB").getpixel((5, 5))
    assert px[2] > px[0], "the capture, not the framed picture"


def test_a_capture_without_framed_partner_stays_listed(client):
    event, dev = _box(client)
    run = "col" + secrets.token_hex(4)
    _upload(client, dev, event, run, run, "collage")
    _upload(client, dev, event, f"{run}.1", run, "original", variant="original")
    html, _ = _page(client, event, run)
    assert html.count('class="sess-item"') == 2
    assert "Ohne Rahmen" not in html


def test_without_a_frame_one_download_is_enough(client):
    event, dev = _box(client)
    run = "nof" + secrets.token_hex(4)
    _upload(client, dev, event, run, run, "photo", frame=False)
    _upload(client, dev, event, f"{run}.1", run, "original", variant="original")
    html, _ = _page(client, event, run)
    assert html.count('class="sess-item"') == 1
    assert "Ohne Rahmen" not in html and "Mit Rahmen" not in html


def test_the_gallery_lists_the_picture_once_and_its_large_view_offers_both(client):
    event, dev = _box(client)
    run = "gal" + secrets.token_hex(4)
    _upload(client, dev, event, run, run, "collage")
    single = _upload(client, dev, event, f"{run}.f1", run, "photo")
    raw = _upload(client, dev, event, f"{run}.1", run, "original", variant="original")
    html = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert f'data-id="{raw}"' not in html, "no tile of its own"
    assert re.search(rf'data-id="{single}"\s+data-unframed="{raw}"', html)
    assert 'id="lbUnframed"' in html and "Ohne Rahmen" in html
    # Not hidden behind the "originals" filter either
    assert f'data-id="{raw}"' not in client.get(f"/gallery/{event['slug']}?show=originals").get_data(as_text=True)


def test_the_switch_is_named_after_what_guests_get(client):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    form = (root / "app" / "templates" / "admin" / "booth_profile.html").read_text(encoding="utf-8")
    booth = (root / "booth" / "boothcore" / "web" / "static" / "settings.js").read_text(encoding="utf-8")
    assert "Bilder zusätzlich ohne Rahmen hochladen" in form
    assert 'upload_originals: ["Bilder zusätzlich ohne Rahmen hochladen"' in booth
