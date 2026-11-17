"""A share link to a gallery opens its folders too - never looser than above.

Same rule as password and client access (page 4, section 13): what the parent
opens, its folders open, unless a folder is stricter (own password,
unpublished, silent, expired). A link to selected pictures stays exactly that.
"""
import io
import json
import secrets
import zipfile

import pytest
from PIL import Image

from app import db, folders, gallery_settings as gsm, media, sharelinks


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    db.set_setting("silent_mode", "0")
    yield


def _event():
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Freigabe {k}", slug=f"fg-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean({}))})
    return db.get_event_by_id(eid)


def _photo(event, name):
    d = media.event_storage_dir(event["slug"])
    stem = secrets.token_hex(4)
    orig, webp, thumb = d / "orig" / f"{stem}.jpg", d / "webp" / f"{stem}.webp", d / "thumb" / f"{stem}.webp"
    Image.new("RGB", (40, 30)).save(orig, "JPEG")
    Image.new("RGB", (40, 30)).save(webp, "WEBP")
    Image.new("RGB", (20, 15)).save(thumb, "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
        " share_token) VALUES (?,?,?,?,?,?,?,?,?)",
        (event["id"], "photo", name, rel(orig), rel(webp), rel(thumb), 1, stem, secrets.token_hex(8)))
    con.commit()
    con.close()
    return cur.lastrowid


def _setup():
    parent = _event()
    own = _photo(parent, "parent.jpg")
    open_child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    in_folder = _photo(open_child, "party.jpg")
    locked = folders.create_child(parent["id"], "Privat")
    db.set_event_password(locked, "geheim1234")
    in_locked = _photo(db.get_event_by_id(locked), "privat.jpg")
    hidden = folders.create_child(parent["id"], "Entwurf")
    db.update_event(hidden, published=0)
    in_hidden = _photo(db.get_event_by_id(hidden), "entwurf.jpg")
    return parent, own, in_folder, in_locked, in_hidden


def test_a_whole_gallery_link_opens_the_open_folders(client):
    parent, own, in_folder, in_locked, in_hidden = _setup()
    link = sharelinks.create(parent["id"])
    page = client.get(f"/l/{link['slug']}").get_data(as_text=True)
    assert f"/file/{own}" in page and f"/file/{in_folder}" in page and "Party" in page
    assert f"/file/{in_locked}" not in page and f"/file/{in_hidden}" not in page
    assert client.get(f"/l/{link['slug']}/file/{in_folder}").status_code == 200
    assert client.get(f"/l/{link['slug']}/file/{in_locked}").status_code == 404
    names = zipfile.ZipFile(io.BytesIO(client.get(f"/l/{link['slug']}/all.zip").data)).namelist()
    # Named as a download is named (no rule chosen: "<event>-<nr>"), the folder's picture in its folder.
    assert len(names) == 2 and sum(n.startswith("Party/") for n in names) == 1
    assert "parent.jpg" not in names and "Party/party.jpg" not in names
    assert not any("privat" in n for n in names)


def test_a_link_to_chosen_pictures_stays_exactly_that(client):
    parent, own, in_folder, *_ = _setup()
    link = sharelinks.create(parent["id"], media_ids=[own])
    assert client.get(f"/l/{link['slug']}/file/{in_folder}").status_code == 404


def test_a_silent_folder_stays_closed_under_the_link(client):
    parent, own, in_folder, *_ = _setup()
    child = next(c for c in folders.children_of(parent["id"]) if c["name"] == "Party")
    db.update_event(child["id"], gallery_json=json.dumps({"silent": True}))
    link = sharelinks.create(parent["id"])
    assert client.get(f"/l/{link['slug']}/file/{in_folder}").status_code == 404
