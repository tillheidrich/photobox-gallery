"""Ein Event an der Box anlegen — auch ohne Netz.

Der Fall: Der Betreiber steht im Festzelt, die Galerie ist nicht erreichbar,
das Event ist nirgends angelegt. Trotzdem muss der Abend losgehen können.

Die Box erzeugt dann Name, Slug und Signaturschlüssel selbst. Der Schlüssel
ist der Kern: Mit ihm stimmen die QR-Codes ab dem ersten Foto, auch wenn die
Galerie das Event erst Stunden später zu sehen bekommt. Bis die Quittung da
ist, reist der Anspruch bei jedem Sync mit.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from _pages import page  # noqa: E402


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-event-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})  # the operator has replaced the factory PIN (see test_factory_pin.py)
    yield b


def _ohne_galerie(booth, monkeypatch):
    """Die Galerie ist eingerichtet, aber nicht erreichbar — der Normalfall im Zelt."""
    booth.store.save_local({"server": {"url": "https://musterbox.photobox-gallery.com",
                                       "api_key": "pb_test"}})
    monkeypatch.setattr(booth, "_gallery_json", lambda *a, **kw: None)


def test_without_a_gallery_the_event_still_exists(booth, monkeypatch):
    _ohne_galerie(booth, monkeypatch)
    antwort = booth.event_anlegen("Hochzeit Änne & Jörg")
    assert antwort["online"] is False
    event = booth.profile["event"]
    assert event["name"] == "Hochzeit Änne & Jörg"
    assert event["slug"] == "hochzeit-aenne-joerg"
    assert len(event["share_key"]) >= 20              # damit signiert die Box ab sofort


def test_the_qr_code_works_from_the_first_photo(booth, monkeypatch):
    """Ohne gültige Signatur wäre jeder gedruckte QR-Code an dem Abend Müll."""
    from boothcore import share
    _ohne_galerie(booth, monkeypatch)
    booth.event_anlegen("Sommerfest")
    key = booth.profile["event"]["share_key"]
    uid = share.new_uid()
    assert share.signature(key, uid)
    ziel = share.qr_target(booth.profile, uid)
    assert uid in ziel


def test_the_note_stays_until_the_gallery_confirms(booth, monkeypatch):
    _ohne_galerie(booth, monkeypatch)
    booth.event_anlegen("Betriebsfeier")
    claim = booth.store.local["event_claim"]
    assert claim["name"] == "Betriebsfeier" and claim["share_key"]
    # genau das reist beim nächsten Sync mit
    assert booth.agent.event_claim() == claim

    booth._event_angekommen({"slug": "betriebsfeier", "name": "Betriebsfeier",
                             "share_key": claim["share_key"]})
    assert booth.store.local["event_claim"] == {}
    assert booth.profile["event"]["slug"] == "betriebsfeier"


def test_a_different_slug_from_the_gallery_is_accepted(booth, monkeypatch):
    """War der Slug schon vergeben, gewinnt die Galerie — der Schlüssel bleibt."""
    _ohne_galerie(booth, monkeypatch)
    booth.event_anlegen("Sommerfest")
    key = booth.profile["event"]["share_key"]
    booth._event_angekommen({"slug": "sommerfest-2", "name": "Sommerfest", "share_key": key})
    assert booth.profile["event"]["slug"] == "sommerfest-2"
    assert booth.profile["event"]["share_key"] == key


def test_with_a_gallery_its_answer_wins(booth, monkeypatch):
    rufe = []

    def antwort(method, pfad, body=None):
        rufe.append((method, pfad, body))
        return {"ok": True, "event": {"slug": "hochzeit-lina", "name": "Hochzeit Lina", "current": True}}

    monkeypatch.setattr(booth, "_gallery_json", antwort)
    ergebnis = booth.event_anlegen("Hochzeit Lina")
    assert ergebnis["online"] is True
    assert booth.store.local["event_claim"] == {}        # nichts nachzutragen
    assert booth.profile["event"]["slug"] == "hochzeit-lina"
    # die Box schickt ihren Schlüssel mit, damit beide dieselbe Signatur haben
    assert len(rufe[0][2]["share_key"]) >= 20


def test_a_nameless_event_is_refused(booth, monkeypatch):
    from fastapi import HTTPException
    _ohne_galerie(booth, monkeypatch)
    for name in ("", " ", "x"):
        with pytest.raises(HTTPException):
            booth.event_anlegen(name)


def test_choosing_an_event_needs_a_gallery(booth, monkeypatch):
    from fastapi import HTTPException
    _ohne_galerie(booth, monkeypatch)
    with pytest.raises(HTTPException):
        booth.event_waehlen("irgendein-event")


def test_the_list_says_plainly_that_nobody_answers(booth, monkeypatch):
    _ohne_galerie(booth, monkeypatch)
    booth.event_anlegen("Feier ohne Netz")
    liste = booth.events_liste()
    assert liste["online"] is False and liste["events"] == []
    assert liste["current"]["name"] == "Feier ohne Netz"
    assert liste["claim"]["name"] == "Feier ohne Netz"


def test_the_admin_api_is_behind_the_pin(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    assert client.get("/api/admin/events").status_code in (401, 403)
    token = client.post("/api/admin/login", json={"pin": booth.store.local["admin_pin"]}).json()["token"]
    antwort = client.get("/api/admin/events", headers={"X-Admin-Token": token})
    assert antwort.status_code == 200 and "online" in antwort.json()


# ── Galerie-Art (20.09.2026) ──────────────────────────────────────────
# „Wer sieht die Bilder?" ist eine Entscheidung und keine Voreinstellung.
# Der Zettel, den die Box an die Galerie reicht, trägt sie mit — sonst
# entschiede die Galerie sie im Zweifel bequem statt sicher.

def test_der_zettel_traegt_die_galerie_art_mit(booth):
    booth.event_anlegen("Firmenfeier")
    claim = booth.store.local.get("event_claim") or {}
    assert claim.get("galerie") == "privat", "Ohne Angabe muss es die private Fassung sein"


def test_gemeinsam_wird_uebernommen(booth):
    booth.event_anlegen("Hochzeit", "gemeinsam")
    assert (booth.store.local.get("event_claim") or {}).get("galerie") == "gemeinsam"


def test_unfug_faellt_auf_privat_zurueck(booth):
    booth.event_anlegen("Irgendwas", "ja klar doch")
    assert (booth.store.local.get("event_claim") or {}).get("galerie") == "privat"


def test_die_verwaltung_fragt_danach():
    admin = page(Path(__file__).resolve().parents[1] / "boothcore" / "web" / "admin.html")
    assert 'id="eventGalerie"' in admin
    stelle = admin.index('id="eventGalerie"')
    block = admin[stelle:stelle + 500]
    assert 'value="privat" selected' in block, "Die sichere Antwort muss die voreingestellte sein"
    assert "galerie" in admin[admin.index('$("eventNew").onclick'):admin.index('$("eventNew").onclick') + 600]
