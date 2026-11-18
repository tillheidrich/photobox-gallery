"""The event page says whether the booths on the event took its settings (18.11.2026).

"Box bei diesem Event" (layouts, frames, looks, collage style) reaches a booth
only at its next contact. The page said "Gespeichert" and nothing else: the
operator could not tell whether the booth had the change, was about to get it
or had not called for an hour. Now the card lists the booths on the event with
"übernommen" / "holt die Änderung ab" / "meldet sich nicht" and the last
contact, follows them while one waits, and says where the second upload
target (FTP/FTPS/SFTP) is set: per booth, on its sync page — a per-event
second target needs a booth update and is not offered yet.
"""
import json

import pytest

from app import db, ecosystem
from app.i18n import de

_N = [0]


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
        s["admin_epoch"] = accounts.session_epoch()
        s["lang"] = "de"


def _event():
    _N[0] += 1
    slug = f"boxen-fest-{_N[0]}-{id(_N)}"
    ev = db.create_event(slug=slug, name="Boxen Fest", upload_token=f"tok-{slug}")
    return ev if isinstance(ev, dict) else db.get_event_by_id(ev)


def _booth(event_id, reported_version=None, minutes_ago=0, name="Eventbox"):
    made = db.create_device(name=name)
    dev = db.get_device_by_id(made["id"])
    version = (dev.get("profile_version") or 1) if reported_version is None else reported_version
    con = db.get_db()
    con.execute("UPDATE devices SET event_id=?, last_heartbeat_at=datetime('now', ?), status_json=? WHERE id=?",
                (event_id, f"-{int(minutes_ago)} minutes", json.dumps({"profile_version": version}), made["id"]))
    con.commit()
    con.close()
    return {**db.get_device_by_id(made["id"]), "api_key": made["api_key"]}


def _section(html):
    part = html[html.index("data-event-booths"):]
    return part[:part.index("</section>")]


def test_a_booth_that_has_the_current_settings_says_so(client):
    _admin(client)
    ev = _event()
    dev = _booth(ev["id"])
    part = _section(client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True))
    assert "Eventbox" in part and 'data-booth-state="taken"' in part
    assert de.STRINGS["events.booths.taken"] in part
    assert "vor " in part or "gerade eben" in part
    assert f"/admin/devices/{dev['id']}/sync#zweites-ziel" in part


def test_saving_a_booth_setting_says_the_booth_will_fetch_it_and_follows_it(client):
    _admin(client)
    ev = _event()
    dev = _booth(ev["id"])
    r = client.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "t", "name": ev["name"], "slug": ev["slug"], "ev_looks_mode": "auto"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert de.STRINGS["events.booths.saved_waiting"].format(n=1) in html
    part = _section(html)
    assert 'data-booth-state="waiting"' in part and 'data-live-busy="1"' in html
    state = client.get(f"/admin/events/{ev['id']}/booths-state").get_json()
    assert state["busy"] is True and "waiting" in state["html"]
    # The booth's next contact fetches the profile, the one after reports its version.
    new_version = db.get_device_by_id(dev["id"])["profile_version"]
    client.post("/api/v2/booth/sync", json={"status": {"profile_version": new_version}},
                headers={"X-API-Key": dev["api_key"]})
    state = client.get(f"/admin/events/{ev['id']}/booths-state").get_json()
    assert state["busy"] is False and 'data-booth-state="taken"' in state["html"]


def test_a_silent_booth_is_named_with_its_last_contact(client):
    _admin(client)
    ev = _event()
    _booth(ev["id"], reported_version=0, minutes_ago=40)
    part = _section(client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True))
    assert 'data-booth-state="silent"' in part and "40 min" in part


def test_without_a_booth_the_page_says_so_and_where_the_second_target_lives(client):
    _admin(client)
    ev = _event()
    html = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    part = _section(html)
    assert de.STRINGS["events.booths.none"] in part
    assert de.STRINGS["events.booths.second_target"] in part and 'href="/admin/devices"' in part
    # Where an operator looks for the second target: the QR target card points there too.
    qr = html[html.index('id="qr-ziel"'):]
    assert de.STRINGS["events.booths.second_target_qr"] in qr[:6000]


def test_the_state_endpoint_needs_the_admin(client):
    ev = _event()
    r = client.get(f"/admin/events/{ev['id']}/booths-state")
    assert r.status_code in (302, 401, 403) and not r.is_json
    _admin(client)
    assert client.get("/admin/events/999999/booths-state").status_code == 404
    assert client.post(f"/admin/events/{ev['id']}/booths-state", data={"csrf_token": "t"}).status_code == 405
