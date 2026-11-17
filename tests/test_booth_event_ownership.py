"""A device key opens the events of its own box, not every event in the gallery.

Until 15.11.2026 ``GET /api/v2/booth/events`` listed every event and
``/events/select`` put the box on any of them: one leaked device key was a
key to every gallery, including its signing key (it arrives with the next
profile). A box now sees and selects only events it is or was assigned to
(``device_event_history``: assigned in the admin, created by the box itself,
or claimed by it). Everything else answers 404, as if it did not exist.
"""
import io
import secrets


def _device(name="Box"):
    from app import db, ecosystem
    ecosystem.init_schema()
    row = db.create_device(name=f"{name} {secrets.token_hex(3)}")
    return dict(row)


def _event(name="Fest"):
    from app import db
    eid = db.create_event(name=f"{name} {secrets.token_hex(2)}", slug="own-" + secrets.token_hex(4),
                          upload_token=secrets.token_hex(6))
    return db.get_event_by_id(eid)


def _assign(dev, ev):
    """What the admin does on the booth page: put the box on the event."""
    from app import ecosystem, db
    ecosystem.save_device_profile(dev["id"], ecosystem.device_profile(db.get_device_by_id(dev["id"])) or {},
                                  ev["id"])


def _h(dev):
    return {"X-API-Key": dev["api_key"]}


def _pair(client):
    client.get("/healthz")
    a, b = _device("A"), _device("B")
    ea, eb = _event("A"), _event("B")
    _assign(a, ea)
    _assign(b, eb)
    return a, b, ea, eb


def test_a_box_lists_only_its_own_events(client):
    a, b, ea, eb = _pair(client)
    slugs = {e["slug"] for e in client.get("/api/v2/booth/events", headers=_h(a)).get_json()["events"]}
    assert ea["slug"] in slugs
    assert eb["slug"] not in slugs
    stranger = _event("niemand")
    slugs = {e["slug"] for e in client.get("/api/v2/booth/events", headers=_h(a)).get_json()["events"]}
    assert stranger["slug"] not in slugs


def test_a_box_cannot_select_a_foreign_event_by_id_or_slug(client):
    a, b, ea, eb = _pair(client)
    for body in ({"event_id": eb["id"]}, {"slug": eb["slug"]}):
        r = client.post("/api/v2/booth/events/select", json=body, headers=_h(a))
        assert r.status_code == 404, body          # 404, not 403: a foreign event does not exist for A
    from app import db
    assert db.get_device_by_id(a["id"])["event_id"] == ea["id"]


def test_a_box_cannot_upload_into_a_foreign_event(client):
    from tests.test_booth_v2 import _jpeg
    a, b, ea, eb = _pair(client)
    r = client.post("/api/v2/booth/media", headers=_h(a), content_type="multipart/form-data",
                    data={"uid": "own" + secrets.token_hex(4), "kind": "photo", "variant": "web",
                          "event_slug": eb["slug"], "file": (_jpeg(), "a.jpg")})
    assert r.status_code in (403, 404)


def test_a_box_cannot_mail_from_a_foreign_event(client, monkeypatch):
    from tests.test_booth_v2 import _jpeg
    from app import emailer
    sent = []
    monkeypatch.setattr(emailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(emailer, "send_text_mail", lambda **kw: sent.append(kw))
    a, b, ea, eb = _pair(client)
    uid = "own" + secrets.token_hex(4)
    client.post("/api/v2/booth/media", headers=_h(b), content_type="multipart/form-data",
                data={"uid": uid, "session_uid": uid, "kind": "photo", "variant": "web",
                      "event_slug": eb["slug"], "file": (_jpeg(), "a.jpg")})
    for attach in ("web", "link"):
        r = client.post("/api/v2/booth/mail", headers=_h(a),
                        json={"to": "gast@example.de", "session_uid": uid, "attach": attach})
        assert r.status_code == 404, attach
    assert sent == []


def test_a_box_cannot_take_over_a_foreign_event_with_its_signing_key(client):
    """The claim path matched by share key alone: whoever knew it got the event."""
    from app import db, ecosystem
    a, b, ea, eb = _pair(client)
    key = ecosystem.ensure_share_key(db.get_event_by_id(eb["id"]))
    r = client.post("/api/v2/booth/sync", headers=_h(a),
                    json={"event_claim": {"name": "Kapern", "share_key": key}}).get_json()
    assert "event_claimed" not in r
    assert db.get_device_by_id(a["id"])["event_id"] == ea["id"]
    assert not ecosystem.geraet_kennt_event(a["id"], eb["id"])


def test_a_box_keeps_working_on_its_current_and_earlier_events(client):
    """A box with exactly its assigned event keeps working; earlier ones stay selectable."""
    a, b, ea, eb = _pair(client)
    second = _event("zweites")
    _assign(a, second)
    events = client.get("/api/v2/booth/events", headers=_h(a)).get_json()
    assert events["current"] == second["id"]
    assert {ea["slug"], second["slug"]} <= {e["slug"] for e in events["events"]}
    r = client.post("/api/v2/booth/events/select", json={"event_id": ea["id"]}, headers=_h(a))
    assert r.status_code == 200 and r.get_json()["event"]["current"] is True


def test_an_event_the_box_created_itself_is_its_own(client):
    a, b, ea, eb = _pair(client)
    made = client.post("/api/v2/booth/events", json={"name": "Neu am Zelt"}, headers=_h(a)).get_json()
    slugs = {e["slug"] for e in client.get("/api/v2/booth/events", headers=_h(a)).get_json()["events"]}
    assert made["event"]["slug"] in slugs
    slugs_b = {e["slug"] for e in client.get("/api/v2/booth/events", headers=_h(b)).get_json()["events"]}
    assert made["event"]["slug"] not in slugs_b
