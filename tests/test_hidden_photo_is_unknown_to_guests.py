"""Guard: a picture a guest may not see answers like a picture that does not
exist, on EVERY guest route that hands out a file (or anything else) by its id.

"May not see" is: hidden by the operator (``hidden=1``, not only "pending"),
and marked red while the gallery hides red pictures from guests
(``hide_red_in_presentation``, viewer not in proofing). Ids are sequential, so
a guest who counts can ask for any of them (review 18.11.2026: the download
route checked only ``review_state``).

The id routes are collected from the Flask url_map, so a route added later is
covered without touching this file. A route that cannot be exercised this way
is listed in ``SKIP`` with the reason; a new skip needs one. The listing pages
and ZIPs that take ids in the query (selection, "download all", run page,
"Meine Aufnahmen", /data) are checked below the parametrised guard.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import zipfile

import pytest
from PIL import Image

from app import db, ecosystem, gallery_settings as gsm, media, sharelinks

#: Prefixes of routes that are not for guests (operator, booth API with device key).
NOT_PUBLIC = ("/admin", "/api/", "/mcp", "/kunde", "/client")

#: (rule, state) -> why it is not compared here.
SKIP = {
    ("/l/<slug>/file/<int:media_id>", "red"):
        "an operator's share link shows what the operator put in it; red hiding is the gallery's presentation",
}

STATES = ("hidden", "red")


def _id_rules():
    from app import create_app
    from werkzeug.routing import IntegerConverter
    app = create_app()
    out = []
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith(NOT_PUBLIC) or "GET" not in rule.methods:
            continue
        conv = rule._converters.get("media_id")
        if not isinstance(conv, IntegerConverter):
            continue
        for state in STATES:
            for access in ("gallery", "run code"):
                out.append(pytest.param(rule.rule, state, access, id=f"{state} {access} {rule.rule}"))
    return out


def _pic(ev, run, **cols):
    d = media.event_storage_dir(ev["slug"])
    stem = f"{run}_{secrets.token_hex(3)}"
    orig, webp, thumb = d / "orig" / f"{stem}.jpg", d / "webp" / f"{stem}.webp", d / "thumb" / f"{stem}.webp"
    for p, size in ((orig, (1600, 1200)), (webp, (900, 675)), (thumb, (300, 225))):
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, (90, 120, 150)).save(p, "JPEG" if p.suffix == ".jpg" else "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
        " share_token, session_uid) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ev["id"], "photo", f"{stem}.jpg", rel(orig), rel(webp), rel(thumb), 1, stem, secrets.token_hex(8), run))
    mid = cur.lastrowid
    for k, v in cols.items():
        con.execute(f"UPDATE media SET {k}=? WHERE id=?", (v, mid))
    con.commit()
    con.close()
    return db.get_media(mid)


@pytest.fixture()
def world(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    db.set_setting("silent_mode", "0")
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Feier {k}", slug=f"feier-{k}", upload_token=f"t-{k}")
    # Everything a guest could use is on, so a refusal can only come from the picture.
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(
        {"exif": True, "hide_red_in_presentation": True, "download_single": True, "download_original": True}))})
    ev = db.get_event_by_id(eid)
    ecosystem.ensure_share_key(ev)
    ev = db.get_event_by_id(eid)
    uid = "run" + secrets.token_hex(3)
    pics = {
        "visible": _pic(ev, uid),
        "hidden": _pic(ev, uid, hidden=1),
        "red": _pic(ev, uid, color_tag="red"),
    }
    link = sharelinks.create(eid)
    code = f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(ev), uid)}"
    con = db.get_db()
    missing = con.execute("SELECT COALESCE(MAX(id), 0) + 1000 FROM media").fetchone()[0]
    con.close()
    return {"event": ev, "pics": pics, "missing": missing, "link": link, "code": code, "uid": uid}


def _url(rule, w, media_id, access):
    slug = w["link"]["slug"] if rule.startswith("/l/") else w["event"]["slug"]
    url = rule.replace("<slug>", slug).replace("<int:media_id>", str(media_id)).replace("<which>", "full")
    assert "<" not in url, f"{rule}: an argument this guard does not know - teach _url or add a SKIP"
    return url + (f"?code={w['code']}" if access == "run code" else "")


@pytest.mark.parametrize("rule,state,access", _id_rules())
def test_a_picture_guests_may_not_see_answers_like_a_missing_one(client, world, rule, state, access):
    if (rule, state) in SKIP:
        pytest.skip(SKIP[(rule, state)])
    ok = client.get(_url(rule, world, world["pics"]["visible"]["id"], access))
    assert ok.status_code == 200, f"the visible picture must work here, got {ok.status_code}"
    a = client.get(_url(rule, world, world["pics"][state]["id"], access))
    b = client.get(_url(rule, world, world["missing"], access))
    assert (a.status_code, a.get_data()) == (b.status_code, b.get_data()), \
        f"{state}: {a.status_code} vs missing {b.status_code}"


def _zip_names(resp):
    return sorted(zipfile.ZipFile(io.BytesIO(resp.get_data())).namelist()) if resp.status_code == 200 else []


@pytest.mark.parametrize("state", STATES)
def test_selection_and_gallery_zip_leave_the_picture_out(client, world, state):
    slug, pid = world["event"]["slug"], world["pics"][state]["id"]
    for path in ("selection.zip", "download.zip"):
        a = client.get(f"/gallery/{slug}/{path}?ids={pid}")
        b = client.get(f"/gallery/{slug}/{path}?ids={world['missing']}")
        assert (a.status_code, _zip_names(a)) == (b.status_code, _zip_names(b)), (path, state)


@pytest.mark.parametrize("state", STATES)
def test_run_page_my_shots_and_data_files_do_not_hand_it_out(client, world, state):
    pic = world["pics"][state]
    with client.session_transaction() as s:
        s["p_uids"] = [world["uid"]]
    for page in (f"/p/{world['code']}", "/meine"):
        html = client.get(page).get_data(as_text=True)
        assert str(world["pics"]["visible"]["id"]) in html, page
        assert not re.search(rf"/photo/{pic['id']}/", html), (page, state)
        for col in ("path_original", "path_webp", "path_thumb"):
            assert pic[col] not in html, (page, state, col)
    for col in ("path_webp", "path_thumb"):
        assert client.get(f"/data/{pic[col]}").status_code == 404, (state, col)


def test_red_pictures_stay_downloadable_in_proofing(client, world):
    ev = world["event"]
    db.update_event(ev["id"], {"gallery_json": json.dumps(gsm.clean(
        {"hide_red_in_presentation": True, "mode": "proofing"}))})
    r = client.get(f"/gallery/{ev['slug']}/photo/{world['pics']['red']['id']}/download")
    assert r.status_code == 200


def test_the_unframed_original_of_a_visible_web_version_still_downloads(client, world):
    """The booth stores the full-size original as its own hidden row next to
    the web version (same uid, ``variant='original'``); that is not a picture
    the operator hid, and its download is the point of "Original"."""
    ev = world["event"]
    uid = "u" + secrets.token_hex(3)
    web = _pic(ev, world["uid"], uid=uid, variant="web")
    orig = _pic(ev, world["uid"], uid=uid, variant="original", hidden=1)
    r = client.get(f"/gallery/{ev['slug']}/photo/{web['id']}/download?size=original")
    assert r.status_code == 200
    with Image.open(io.BytesIO(r.get_data())) as im:
        assert max(im.size) == 1600
    assert client.get(f"/gallery/{ev['slug']}/photo/{orig['id']}/download?size=original").status_code == 200
    db.update_event(ev["id"], {"gallery_json": json.dumps(gsm.clean({"download_original": False}))})
    assert client.get(f"/gallery/{ev['slug']}/photo/{orig['id']}/download?size=original").status_code == 403
    # The operator hides the web version: its original goes with it.
    con = db.get_db()
    con.execute("UPDATE media SET hidden=1 WHERE id=?", (web["id"],))
    con.commit()
    con.close()
    db.update_event(ev["id"], {"gallery_json": json.dumps(gsm.clean({}))})
    missing = client.get(f"/gallery/{ev['slug']}/photo/{world['missing']}/download")
    gone = client.get(f"/gallery/{ev['slug']}/photo/{orig['id']}/download?size=original")
    assert gone.status_code == missing.status_code == 404
