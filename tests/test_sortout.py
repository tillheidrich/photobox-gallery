"""Sorting out duplicates and blurry photos by image statistics (no. 62).

No AI, no model: a 64-bit difference hash (dHash) for near-duplicates and
the variance of an edge image for sharpness, both with Pillow only. The
admin view proposes, the operator decides; nothing is hidden or deleted on
its own.
"""
import io
import json
import random
import secrets

import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from werkzeug.datastructures import FileStorage

from app import db, ecosystem, gallery_settings as gsm, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("trash_days", "0")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
    yield


def _scene(seed, size=(900, 600)):
    rnd = random.Random(seed)
    im = Image.new("RGB", size, (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    d = ImageDraw.Draw(im)
    for _ in range(40):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        d.rectangle([x, y, x + rnd.randint(20, 160), y + rnd.randint(20, 160)],
                    fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    for _ in range(60):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        d.line([x, y, x + rnd.randint(-200, 200), y + rnd.randint(-200, 200)], fill=(0, 0, 0), width=2)
    return im


def _jpg(im):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    buf.seek(0)
    return buf


def _event(client):
    key = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"Sort {key}", slug=f"sort-{key}", upload_token=f"tok-{key}")
        db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean({}))})
        return db.get_event_by_id(eid)


def _add(client, event, im, name="p.jpg"):
    with client.application.app_context():
        return media.save_upload(event["id"], FileStorage(_jpg(im), filename=name))["id"]


def test_near_duplicates_have_close_hashes_and_different_scenes_do_not():
    from app import sortout
    a = _scene(1)
    brighter = ImageEnhance.Brightness(a).enhance(1.08)
    shifted = a.crop((6, 4, 900, 600)).resize((900, 600))
    other = _scene(2)
    ha, hb, hs, ho = (sortout.dhash(x) for x in (a, brighter, shifted, other))
    assert sortout.distance(ha, hb) <= sortout.NEAR
    assert sortout.distance(ha, hs) <= sortout.NEAR
    assert sortout.distance(ha, ho) > sortout.NEAR


def test_a_blurred_picture_is_less_sharp():
    from app import sortout
    a = _scene(3)
    assert sortout.sharpness(a) > 3 * sortout.sharpness(a.filter(ImageFilter.GaussianBlur(4)))


def test_an_upload_gets_hash_and_sharpness(client):
    event = _event(client)
    mid = _add(client, event, _scene(4))
    with client.application.app_context():
        row = db.get_media(mid)
    assert len(row["phash"]) == 16 and row["sharpness"] > 0


def test_groups_keep_the_sharpest_and_blurry_ones_are_listed(client):
    from app import sortout
    event = _event(client)
    a = _scene(5)
    sharp = _add(client, event, a, "a.jpg")
    soft = _add(client, event, a.filter(ImageFilter.GaussianBlur(3)), "a2.jpg")
    for s in (6, 7, 8, 9):
        _add(client, event, _scene(s), f"o{s}.jpg")
    with client.application.app_context():
        result = sortout.analyse(event["id"])
    groups = result["groups"]
    assert len(groups) == 1
    ids = [m["id"] for m in groups[0]["items"]]
    assert set(ids) == {sharp, soft}
    assert groups[0]["keep"] == sharp
    assert soft in [m["id"] for m in result["blurry"]]
    assert sharp not in [m["id"] for m in result["blurry"]]


def test_the_view_changes_nothing_by_itself(client):
    event = _event(client)
    a = _scene(10)
    _add(client, event, a)
    _add(client, event, a.filter(ImageFilter.GaussianBlur(3)))
    page = client.get(f"/admin/events/{event['id']}/aussortieren")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Aussortieren" in html and "Doppelte" in html
    with client.application.app_context():
        assert all(not int(m.get("hidden") or 0) for m in db.list_media(event["id"]))
        assert len(db.list_media(event["id"])) == 2


def test_bulk_hide_needs_the_token_and_hides_only_what_was_chosen(client):
    event = _event(client)
    a = _scene(11)
    keep = _add(client, event, a)
    drop = _add(client, event, a.filter(ImageFilter.GaussianBlur(3)))
    path = f"/admin/events/{event['id']}/aussortieren"
    assert client.post(path, data={"action": "hide", "media_ids": [str(drop)]}).status_code == 400
    client.post(path, data={"action": "hide", "media_ids": [str(drop)], "csrf_token": "t"})
    with client.application.app_context():
        assert int(db.get_media(drop)["hidden"]) == 1
        assert int(db.get_media(keep)["hidden"] or 0) == 0


def test_bulk_delete_only_touches_this_events_pictures(client):
    event = _event(client)
    other = _event(client)
    mine = _add(client, event, _scene(12))
    foreign = _add(client, other, _scene(13))
    client.post(f"/admin/events/{event['id']}/aussortieren",
                data={"action": "delete", "media_ids": [str(mine), str(foreign)], "csrf_token": "t"})
    with client.application.app_context():
        assert db.get_media(mine) is None
        assert db.get_media(foreign) is not None


def test_the_backfill_works_in_small_batches(client):
    from app import sortout
    event = _event(client)
    ids = [_add(client, event, _scene(20 + i)) for i in range(5)]
    with client.application.app_context():
        con = db.get_db()
        con.execute(f"UPDATE media SET phash=NULL, sharpness=NULL WHERE id IN ({','.join('?' * len(ids))})", ids)
        con.commit()
        con.close()
        assert sortout.backfill(limit=2)["done"] == 2
        assert sortout.backfill(limit=10)["done"] >= 3
        assert all(db.get_media(i)["phash"] for i in ids)
