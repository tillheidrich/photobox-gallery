"""Two owner's decisions of 15.11.2026, from the gallery form to the booth.

* ``start.corner_size`` (m / l / xl): gallery button, language button and
  lock on the start screen, sized together. Default ``l``; ``m`` is the old
  look.
* ``boomerang.layout`` defaults to ``crop``: a portrait boomerang fills its
  frame instead of showing the whole landscape frame on blurred bars. A
  profile that already says ``blur`` keeps it; the film (``video.layout``)
  and the GIF's boomerang keep ``blur``.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "booth"))


def _admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _token(client, dev_id) -> str:
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        return s.get("csrf_token") or ""


def test_the_defaults_on_both_sides():
    from app import ecosystem
    from boothcore import config
    for prof in (ecosystem.DEFAULT_PROFILE, config.DEFAULT_PROFILE):
        assert prof["start"]["corner_size"] == "l"
        assert prof["boomerang"]["layout"] == "crop"
        assert prof["video"]["layout"] == "blur"
        assert prof["gif"]["boomerang"]["layout"] == "blur"
    assert config.DEFAULT_PROFILE["start"] == ecosystem.DEFAULT_PROFILE["start"]
    assert config.DEFAULT_PROFILE["boomerang"] == ecosystem.DEFAULT_PROFILE["boomerang"]


def test_a_stored_profile_that_says_blur_keeps_blur(client):
    _admin(client)
    from app import db, ecosystem
    from boothcore import config
    dev_id = db.create_device(name="Altbox")["id"]
    ecosystem.save_device_profile(dev_id, {"boomerang": {"layout": "blur"}}, None)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["boomerang"]["layout"] == "blur"
    assert config._merge(config.DEFAULT_PROFILE, {"boomerang": {"layout": "blur"}})["boomerang"]["layout"] == "blur"
    # Without the key, the new default.
    merged = config._merge(config.DEFAULT_PROFILE, json.loads('{"boomerang": {"seconds": 3}}'))
    assert merged["boomerang"]["layout"] == "crop"
    assert config._merge(config.DEFAULT_PROFILE, {"start": {"gallery_style": "prominent"}})["start"]["corner_size"] == "l"


def test_the_form_shows_the_defaults(client):
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Neubox")["id"]
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'name="corner_size"' in page
    corner = page.split('name="corner_size"', 1)[1].split("</select>", 1)[0]
    assert '<option value="l" selected>' in corner
    boom = page.split('name="boomerang_layout"', 1)[1].split("</select>", 1)[0]
    assert '<option value="crop" selected>' in boom
    film = page.split('name="video_layout"', 1)[1].split("</select>", 1)[0]
    assert '<option value="blur" selected>' in film


def test_the_form_stores_every_value_and_falls_back_to_the_defaults(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Formbox")["id"]
    for sent, corner, boom in (({"corner_size": "xl", "boomerang_layout": "blur"}, "xl", "blur"),
                               ({"corner_size": "m", "boomerang_layout": "crop"}, "m", "crop"),
                               ({"corner_size": "riesig", "boomerang_layout": "quer"}, "l", "crop"),
                               ({}, "l", "crop")):
        r = client.post(f"/admin/devices/{dev_id}/booth", data={
            "csrf_token": _token(client, dev_id), "mode_photo": "on", **sent}, follow_redirects=True)
        assert r.status_code == 200
        p = ecosystem.device_profile(db.get_device_by_id(dev_id))
        assert p["start"]["corner_size"] == corner, sent
        assert p["boomerang"]["layout"] == boom, sent
        # The film keeps its own default.
        assert p["video"]["layout"] == "blur", sent


def test_the_booth_admin_knows_the_setting():
    js = (ROOT / "booth/boothcore/web/static/settings.js").read_text(encoding="utf-8")
    assert '"start.corner_size": ["m", "l", "xl"]' in js
    assert '"start.corner_size": "' in js
