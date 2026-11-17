"""Where boomerang and video come from (``boomerang.source``, ``video.source``) in the gallery.

"live" (the live view) is the default on both sides; "camera" has the camera
film onto its card for a higher resolution. The form sets it and shows it
again with a hint about what it needs, the rules accept only the two words
(so an MCP profile update with a typo is refused), and the booth's editor
offers the same two.
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def test_live_by_default_and_the_same_on_both_sides():
    from app import ecosystem
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore import config
    for clip in ("boomerang", "video"):
        assert ecosystem.DEFAULT_PROFILE[clip]["source"] == "live"
        assert config.DEFAULT_PROFILE[clip] == ecosystem.DEFAULT_PROFILE[clip]


def test_the_form_sets_the_camera_source_and_shows_it_again(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Kamerafilm")["id"]
    token = _token(client, dev_id)
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_boomerang": "on", "mode_video": "on",
        "boomerang_source": "camera", "video_source": "nonsense"}, follow_redirects=True)
    assert r.status_code == 200
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["boomerang"]["source"] == "camera" and p["video"]["source"] == "live"
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'value="camera" selected' in page
    assert "SD-Karte" in page and "über USB filmen" in page


def test_the_rules_take_only_live_or_camera():
    from app import ecosystem, profile_rules
    good = copy.deepcopy(ecosystem.DEFAULT_PROFILE)
    good["video"]["source"] = "camera"
    clean, problems = profile_rules.check(good, ecosystem.DEFAULT_PROFILE)
    assert not problems and clean["video"]["source"] == "camera"
    bad = copy.deepcopy(ecosystem.DEFAULT_PROFILE)
    bad["boomerang"]["source"] = "4k"
    _clean, problems = profile_rules.check(bad, ecosystem.DEFAULT_PROFILE)
    assert any(path == "boomerang.source" for path, _r, _v in problems)


def test_the_rules_are_the_same_file_in_gallery_and_booth():
    assert (ROOT / "app" / "profile_rules.py").read_bytes() == \
        (ROOT / "booth" / "boothcore" / "profile_rules.py").read_bytes()


def test_the_booth_editor_offers_both_and_says_what_it_needs():
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "settings.js").read_text(encoding="utf-8")
    for clip in ("boomerang", "video"):
        assert f'"{clip}.source": ["live", "camera"]' in js
        assert f'"{clip}.source": "Quelle:' in js
    assert "SD-Karte in der Kamera" in js
