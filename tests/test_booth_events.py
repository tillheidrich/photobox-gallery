"""Die Box legt ihr Event selbst an.

Der Fall aus der Praxis: Der Betreiber steht mit der Box im Festzelt, das
Event ist im Backend nicht angelegt, und das Telefon hat kein Netz. Dann muss
es an der Box gehen — sonst nützt die beste Verwaltung nichts.

Drei Aufrufe: sehen, was es gibt; eines übernehmen; ein neues anlegen. Alle
drei über den Geräteschlüssel, der ohnehin zu dieser Installation gehört.
"""
import secrets

import pytest


def _device(client, name="Box am Zelt"):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    return db.create_device(name=f"{name} {secrets.token_hex(2)}")


def _event(client, name="Hochzeit Lina"):
    from app import db
    slug = "ev-" + secrets.token_hex(3)
    eid = db.create_event(name=name, slug=slug, upload_token=secrets.token_hex(6))
    return db.get_event_by_id(eid)


def _h(dev):
    return {"X-API-Key": dev["api_key"]}


# ── Sehen ─────────────────────────────────────────────────
def test_a_stranger_sees_nothing(client):
    _device(client)
    assert client.get("/api/v2/booth/events", headers={"X-API-Key": "falsch"}).status_code == 401


def _assign(dev, ev):
    """The admin puts the box on an event; from then on it is one of the box's events."""
    from app import db, ecosystem
    ecosystem.save_device_profile(dev["id"], ecosystem.device_profile(db.get_device_by_id(dev["id"])) or {},
                                  ev["id"])


def test_the_booth_sees_only_its_own_events_and_where_it_stands(client):
    """Until 15.11.2026 a box saw every event of the gallery (finding S-1)."""
    dev = _device(client)
    ev = _event(client)
    fremd = _event(client, "Fremdes Fest")
    antwort = client.get("/api/v2/booth/events", headers=_h(dev)).get_json()
    assert antwort["ok"] and antwort["events"] == []      # nothing assigned yet
    assert antwort["current"] is None
    _assign(dev, ev)
    antwort = client.get("/api/v2/booth/events", headers=_h(dev)).get_json()
    slugs = [e["slug"] for e in antwort["events"]]
    assert ev["slug"] in slugs and fremd["slug"] not in slugs
    assert antwort["current"] == ev["id"]


# ── Übernehmen ────────────────────────────────────────────
def test_the_booth_can_go_back_to_one_of_its_events(client):
    dev = _device(client)
    ev = _event(client)
    _assign(dev, ev)
    _assign(dev, _event(client, "Danach"))
    antwort = client.post("/api/v2/booth/events/select", json={"event_id": ev["id"]}, headers=_h(dev))
    assert antwort.status_code == 200 and antwort.get_json()["event"]["current"] is True

    from app import db
    assert db.get_device_by_id(dev["id"])["event_id"] == ev["id"]
    # und beim nächsten Sync bekommt die Box das Event mitsamt Schlüssel
    sync = client.post("/api/v2/booth/sync", json={"profile_version": 0}, headers=_h(dev)).get_json()
    assert sync["profile"]["event"]["slug"] == ev["slug"] and sync["profile"]["event"]["share_key"]


def test_choosing_by_slug_works_too(client):
    dev = _device(client)
    ev = _event(client)
    _assign(dev, ev)
    assert client.post("/api/v2/booth/events/select", json={"slug": ev["slug"]},
                       headers=_h(dev)).status_code == 200


def test_a_foreign_event_is_as_unknown_as_a_missing_one(client):
    dev = _device(client)
    ev = _event(client)
    for body in ({"event_id": ev["id"]}, {"slug": ev["slug"]}):
        assert client.post("/api/v2/booth/events/select", json=body,
                           headers=_h(dev)).status_code == 404


def test_an_unknown_event_is_a_clear_no(client):
    dev = _device(client)
    assert client.post("/api/v2/booth/events/select", json={"event_id": 99999},
                       headers=_h(dev)).status_code == 404
    assert client.post("/api/v2/booth/events/select", json={}, headers=_h(dev)).status_code == 404


# ── Anlegen ───────────────────────────────────────────────
def test_the_booth_creates_an_event_and_stands_on_it(client):
    dev = _device(client)
    antwort = client.post("/api/v2/booth/events", json={"name": "Geburtstag Änne & Jörg"}, headers=_h(dev))
    daten = antwort.get_json()
    assert antwort.status_code == 200 and daten["ok"]
    assert daten["event"]["name"] == "Geburtstag Änne & Jörg"
    assert daten["event"]["slug"] == "geburtstag-aenne-joerg"       # Umlaute werden umgeschrieben
    assert daten["event"]["current"] is True

    from app import db
    ev = db.get_event_by_slug(daten["event"]["slug"])
    assert ev and db.get_device_by_id(dev["id"])["event_id"] == ev["id"]


def test_the_new_event_is_live_at_once(client):
    """Der Gast darf nie in eine leere Seite laufen — auch nicht vor dem ersten Foto.

    Bis zum 20.09.2026 stand hier ``published == 1``, begründet mit genau
    diesem Satz. Die Begründung stimmte nicht: Die QR-Seite des Gastes hängt
    nicht an der Veröffentlichung, sie antwortet auch ohne sie — vor dem
    Upload mit „ist unterwegs", danach mit den Aufnahmen. An der
    Veröffentlichung hängt allein die **gemeinsame** Galerie, in der jeder
    alles sieht. Das ist eine Entscheidung des Betreibers und darf keine
    Voreinstellung sein, die eine Box im Vorbeigehen trifft.
    """
    import secrets as _s
    from app import db, ecosystem
    dev = _device(client)
    key = _s.token_urlsafe(24)
    slug = client.post("/api/v2/booth/events", json={"name": "Sommerfest", "share_key": key},
                       headers=_h(dev)).get_json()["event"]["slug"]
    event = db.get_event_by_slug(slug)
    code = f"abcd2345xy-{ecosystem.signature(ecosystem.ensure_share_key(event), 'abcd2345xy')}"
    assert client.get(f"/p/{code}").status_code == 200, "Der Gast läuft ins Leere"
    assert not event["published"], "Die Box veröffentlicht die Galerie ungefragt"
    assert client.get(f"/gallery/{slug}").status_code == 404


def test_the_booth_may_ask_for_a_shared_gallery(client):
    """„Gemeinsam" gibt es weiterhin — aber nur, wenn es jemand so will."""
    from app import db
    dev = _device(client)
    slug = client.post("/api/v2/booth/events", json={"name": "Hochzeit", "galerie": "gemeinsam"},
                       headers=_h(dev)).get_json()["event"]["slug"]
    assert db.get_event_by_slug(slug)["published"] == 1
    assert client.get(f"/gallery/{slug}").status_code == 200


def test_the_same_name_twice_does_not_collide(client):
    dev = _device(client)
    erst = client.post("/api/v2/booth/events", json={"name": "Sommerfest"}, headers=_h(dev)).get_json()
    zweit = client.post("/api/v2/booth/events", json={"name": "Sommerfest"}, headers=_h(dev)).get_json()
    assert erst["event"]["slug"] != zweit["event"]["slug"]


def test_a_nameless_event_is_refused(client):
    dev = _device(client)
    for name in ("", " ", "x"):
        assert client.post("/api/v2/booth/events", json={"name": name},
                           headers=_h(dev)).status_code == 400


def test_an_endless_name_is_cut_not_rejected(client):
    """Am Abend ist Abschneiden besser als eine Fehlermeldung."""
    dev = _device(client)
    daten = client.post("/api/v2/booth/events", json={"name": "A" * 300}, headers=_h(dev)).get_json()
    assert len(daten["event"]["name"]) == 80


def test_creating_shows_up_in_the_device_log(client):
    dev = _device(client)
    client.post("/api/v2/booth/events", json={"name": "Betriebsfeier"}, headers=_h(dev))
    from app import ecosystem
    assert any("Betriebsfeier" in l["message"] for l in ecosystem.device_logs(dev["id"]))


def test_a_profile_survives_the_switch(client):
    """Die Box behält ihre Einstellungen — nur das Event wechselt."""
    dev = _device(client)
    from app import db, ecosystem
    ecosystem.save_device_profile(dev["id"], {"modes": ["photo"], "print": {"enabled": True}}, None)
    dev = {**db.get_device_by_id(dev["id"]), "api_key": dev["api_key"]}
    client.post("/api/v2/booth/events", json={"name": "Jubiläum"}, headers=_h(dev))
    profil = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert profil["modes"] == ["photo"] and profil["print"]["enabled"] is True


# ── Ohne Netz angelegt, später nachgetragen ───────────────
def test_an_event_created_offline_arrives_with_the_next_sync(client):
    """Der Abend läuft ohne Netz. Am nächsten Morgen muss alles zusammenpassen."""
    dev = _device(client)
    claim = {"name": "Zeltfest", "slug": "zeltfest", "share_key": secrets.token_urlsafe(24)}

    antwort = client.post("/api/v2/booth/sync", json={"event_claim": claim, "profile_version": 0},
                          headers=_h(dev)).get_json()
    assert antwort["event_claimed"]["slug"] == "zeltfest"
    assert antwort["profile"]["event"]["slug"] == "zeltfest"
    # und vor allem: derselbe Schlüssel, mit dem am Abend signiert wurde
    assert antwort["profile"]["event"]["share_key"] == claim["share_key"]


def test_the_qr_codes_printed_offline_still_open(client):
    """Die gedruckten Karten kennen nur Kennung und Signatur — die müssen aufgehen."""
    import hashlib
    import hmac as _hmac

    dev = _device(client)
    key = secrets.token_urlsafe(24)
    client.post("/api/v2/booth/sync", json={"event_claim": {"name": "Zeltfest", "share_key": key}},
                headers=_h(dev))

    from app import ecosystem
    uid = "zelt2345ab"
    sig = ecosystem.signature(key, uid)
    seite = client.get(f"/p/{uid}-{sig}")
    assert seite.status_code == 200 and b"unterwegs" in seite.data


def test_the_same_claim_twice_creates_one_event(client):
    """Bleibt eine Quittung aus, meldet die Box den Anspruch erneut."""
    dev = _device(client)
    name = "Zeltfest " + secrets.token_hex(3)
    claim = {"name": name, "share_key": secrets.token_urlsafe(24)}
    for _ in range(3):
        client.post("/api/v2/booth/sync", json={"event_claim": claim}, headers=_h(dev))
    from app import db
    assert len([e for e in db.list_events() if e["name"] == name]) == 1


def test_a_taken_slug_does_not_stop_the_evening(client):
    dev = _device(client)
    _event(client)
    from app import db
    belegt = db.list_events()[0]["slug"]
    key = secrets.token_urlsafe(24)
    antwort = client.post("/api/v2/booth/sync",
                          json={"event_claim": {"name": "Zweites", "slug": belegt, "share_key": key}},
                          headers=_h(dev)).get_json()
    assert antwort["event_claimed"]["slug"] != belegt
    # der Schlüssel bleibt — also auch die gedruckten Codes
    from app import ecosystem
    assert ecosystem.event_by_share_key(key)["name"] == "Zweites"


def test_a_claim_without_a_key_is_ignored(client):
    """Ohne Schlüssel wäre das Event nicht das, für das die Box QR-Codes gedruckt hat."""
    dev = _device(client)
    antwort = client.post("/api/v2/booth/sync", json={"event_claim": {"name": "Ohne Schlüssel"}},
                          headers=_h(dev)).get_json()
    assert "event_claimed" not in antwort
