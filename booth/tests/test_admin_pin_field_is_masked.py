"""The booth admin's "Admin-PIN" field never shows the PIN (16.11.2026).

It was an ordinary text field filled with the stored PIN: anyone who saw the
screen over the operator's shoulder, or a screenshot sent for help, had it.
Now the field is masked and empty; empty means "unchanged", as everywhere
else a PIN is never erased by a field left alone (T-C4). The PIN does not
leave the box in /api/admin/config either.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-pinfeld-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "135790"})
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    token = c.post("/api/admin/login", json={"pin": booth.current_pin()}).json()["token"]
    c.headers.update({"X-Admin-Token": token})
    return c


def test_the_pin_does_not_leave_the_box_with_the_settings(client, booth):
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    assert booth.store.local["admin_pin"] not in r.text
    assert r.json()["local"]["admin_pin_set"] is True


@pytest.mark.parametrize("sent", ["", "••••", None])
def test_an_empty_or_masked_field_leaves_the_pin_as_it_is(client, booth, sent):
    body = {"booth_name": "Box"} if sent is None else {"booth_name": "Box", "admin_pin": sent}
    assert client.post("/api/admin/config", json=body).status_code == 200
    assert booth.store.local["admin_pin"] == "135790"


def test_a_new_pin_in_the_field_is_taken(client, booth):
    assert client.post("/api/admin/config", json={"admin_pin": "864209"}).status_code == 200
    assert booth.store.local["admin_pin"] == "864209"


def test_the_field_is_masked_and_never_filled():
    html = (WEB / "admin.html").read_text(encoding="utf-8")
    field = re.search(r'<input id="admin_pin"[^>]*>', html).group(0)
    assert 'type="password"' in field and 'inputmode="numeric"' in field and 'autocomplete="new-password"' in field
    js = (WEB / "static" / "admin-page.js").read_text(encoding="utf-8")
    assert '$("admin_pin").value = l.admin_pin' not in js
    assert "admin_pin_set" in js
