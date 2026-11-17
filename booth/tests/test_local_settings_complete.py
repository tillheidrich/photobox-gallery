"""Every setting of the booth can be changed at the booth, without internet.

Asked for on 14.11.2026: "like the settings in dslrBooth". Until then the
booth's own admin page knew devices and a part of the profile; start screen,
countdown, texts, modes, colours and access lived only in the gallery form —
out of reach in a venue without reception.

Three promises:
* the booth hands its admin the whole profile with the defaults next to it,
  secrets masked;
* a change made at the booth can be pinned, so the next contact with the
  gallery does not take it back;
* the admin page carries an editor that is built from that answer, so a new
  setting shows up there without anybody remembering to add a field.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-local-settings-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    yield b
    b.mail.stop()


@pytest.fixture()
def admin(booth):
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    token = client.post("/api/admin/login", json={"pin": "468213"}).json()["token"]
    client.headers.update({"X-Admin-Token": token})
    return client


def test_the_whole_profile_needs_the_pin(booth):
    from boothcore.app import create_app
    assert TestClient(create_app(booth)).get("/api/admin/profile/full").status_code == 401


def test_the_admin_gets_every_setting_with_its_default(admin):
    from boothcore.config import DEFAULT_PROFILE
    data = admin.get("/api/admin/profile/full").json()
    assert set(data["defaults"]) == set(DEFAULT_PROFILE)
    assert set(DEFAULT_PROFILE) <= set(data["profile"])
    assert data["hold"] is False


def test_secrets_are_masked_and_survive_a_save_untouched(admin, booth):
    booth._apply_profile({**booth.profile, "mail": {**booth.profile["mail"], "password": "geheim-1"},
                          "access": {**booth.profile["access"], "pin": "975310"},
                          "webhooks": {**booth.profile["webhooks"], "secret": "geheim-2"},
                          "http_trigger": {**booth.profile["http_trigger"], "token": "geheim-3"}})
    profile = admin.get("/api/admin/profile/full").json()["profile"]
    text = str(profile)
    assert "geheim" not in text and "975310" not in text
    # The page sends back what it got: the masks must not replace the secrets.
    assert admin.post("/api/admin/profile", json={k: profile[k] for k in
                                                  ("mail", "access", "webhooks", "http_trigger")}).status_code == 200
    assert booth.profile["mail"]["password"] == "geheim-1"
    assert booth.profile["access"]["pin"] == "975310"
    assert booth.profile["webhooks"]["secret"] == "geheim-2"
    assert booth.profile["http_trigger"]["token"] == "geheim-3"


def test_a_setting_from_the_gallery_form_can_be_changed_at_the_booth(admin, booth):
    patch = {"start": {**booth.profile["start"], "show_status": False},
             "access": {**booth.profile["access"], "lock_icon": True},
             "print": {**booth.profile["print"], "limit_session": 4}}
    assert admin.post("/api/admin/profile", json=patch).status_code == 200
    assert booth.profile["start"]["show_status"] is False
    assert booth.profile["access"]["lock_icon"] is True
    assert booth.profile["print"]["limit_session"] == 4


def test_without_the_hold_the_gallery_profile_wins(booth):
    assert booth._apply_server_profile({**booth.profile, "idle_seconds": 99}) is True
    assert booth.profile["idle_seconds"] == 99


def test_with_the_hold_the_gallery_profile_waits(admin, booth):
    assert admin.post("/api/admin/profile/hold", json={"hold": True}).json()["hold"] is True
    assert booth._apply_server_profile({**booth.profile, "idle_seconds": 99}) is False
    assert booth.profile["idle_seconds"] != 99
    assert admin.get("/api/admin/profile/full").json()["hold"] is True
    # …and released, it comes through again.
    admin.post("/api/admin/profile/hold", json={"hold": False})
    assert booth._apply_server_profile({**booth.profile, "idle_seconds": 99}) is True


def test_the_hold_is_set_at_the_booth_only():
    from boothcore.config import DEFAULT_LOCAL, REMOTE_LOCAL_KEYS
    assert DEFAULT_LOCAL["profile_hold"] is False
    assert not any(k.startswith("profile_hold") for k in REMOTE_LOCAL_KEYS)


def test_the_sync_agent_goes_through_the_hold(booth):
    import inspect
    from boothcore import app as app_mod
    assert "apply_profile=self._apply_server_profile" in inspect.getsource(app_mod.Booth.__init__)


def test_the_admin_page_carries_the_editor():
    html = page(WEB / "admin.html")
    js = (WEB / "static" / "settings.js").read_text(encoding="utf-8")
    assert 'id="alleEinstellungen"' in html and '/static/settings.js' in html
    assert "PhotoboxSettings.mount(" in html
    assert "/api/admin/profile/full" in js and "/api/admin/profile/hold" in js


def test_every_group_of_the_profile_has_a_german_name():
    import re
    from boothcore.config import DEFAULT_PROFILE
    js = (WEB / "static" / "settings.js").read_text(encoding="utf-8")
    block = re.search(r"const GROUPS = \{(.*?)\n  \};", js, re.S).group(1)
    named = set(re.findall(r'^\s*"?([a-z_]+)"?:\s*\[', block, re.M))
    missing = set(DEFAULT_PROFILE) - named - {"version"}
    assert not missing, f"no name for: {sorted(missing)}"
