"""The first sync after pairing does not undo the operator's settings at the booth.

Owner, 16.11.2026: pairing replaced controls.mouse_start, start.gallery_style
and access.lock_icon at his booth with the gallery's defaults. Now a booth
just paired offers its own profile; a gallery whose profile for that booth
is untouched takes it as the starting point. A gallery where someone already
set something keeps its profile, as before.
"""
from app import db, ecosystem


def _seed():
    prof = ecosystem.deep_merge(ecosystem.DEFAULT_PROFILE, {})
    prof.pop("event", None)
    prof.pop("version", None)
    prof["controls"]["mouse_start"] = "left"
    prof["start"]["gallery_style"] = "prominent"
    prof["access"]["lock_icon"] = True
    prof["access"]["pin"] = ""
    return prof


def _sync(client, key, body):
    return client.post("/api/v2/booth/sync", json=body, headers={"X-API-Key": key, "Authorization": f"Bearer {key}"})


def test_an_untouched_gallery_takes_the_booth_profile(client):
    client.get("/healthz")
    ecosystem.init_schema()
    dev = db.create_device(name="Seed-Box 1")
    r = _sync(client, dev["api_key"], {"status": {}, "profile_version": -1, "profile_seed": _seed()})
    data = r.get_json()
    assert r.status_code == 200 and data["profile_seed"] == "taken"
    prof = data["profile"]
    assert prof["controls"]["mouse_start"] == "left"
    assert prof["start"]["gallery_style"] == "prominent"
    assert prof["access"]["lock_icon"] is True
    stored = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert stored["controls"]["mouse_start"] == "left"


def test_a_gallery_profile_someone_set_wins_as_before(client):
    client.get("/healthz")
    ecosystem.init_schema()
    dev = db.create_device(name="Seed-Box 2")
    prof = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    prof["controls"]["mouse_start"] = "right"
    ecosystem.save_device_profile(dev["id"], prof, None)
    data = _sync(client, dev["api_key"], {"status": {}, "profile_version": -1, "profile_seed": _seed()}).get_json()
    assert data["profile_seed"] == "kept"
    assert data["profile"]["controls"]["mouse_start"] == "right"


def test_a_seed_never_brings_secrets_or_the_event(client):
    client.get("/healthz")
    ecosystem.init_schema()
    dev = db.create_device(name="Seed-Box 3")
    seed = _seed()
    seed["access"]["pin"] = "8024"
    seed["mail"]["password"] = "geheim-im-test"
    seed["event"] = {"slug": "fremd", "share_key": "x"}
    _sync(client, dev["api_key"], {"status": {}, "profile_version": -1, "profile_seed": seed})
    stored = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert stored["access"]["pin"] == "" and stored["mail"]["password"] == ""
    assert (stored.get("event") or {}).get("slug") != "fremd"


def test_a_sync_without_a_seed_answers_nothing_about_it(client):
    client.get("/healthz")
    ecosystem.init_schema()
    dev = db.create_device(name="Seed-Box 4")
    data = _sync(client, dev["api_key"], {"status": {}, "profile_version": -1}).get_json()
    assert "profile_seed" not in data
