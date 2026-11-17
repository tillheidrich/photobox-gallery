"""After setting up, one action makes the booth neutral again: "Testbilder entfernen".

Asked for on 15.11.2026, before the first wedding: the operator has test
pictures on the booth and wants them gone before the guests come. The card
in the booth admin shows what is there and removes it with the booth's one
deletion path (library row, run folder, uploads not yet sent, prints still
waiting) — never what already reached a gallery, and never another event's
pictures. Guards: no removal while a guest is in the middle of a run; the
number to be removed must be confirmed; once the runs belong to an event or
are more than a handful, the operator must type that number.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from _pages import page  # noqa: E402


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-test-weg-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8}})
    b.store.profile["modes"] = ["photo", "collage", "gif"]
    b.store.profile["event"].update({"slug": "", "name": ""})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _lauf(booth):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _fremd(booth, tmp_path):
    """A run of an earlier event, still on this booth."""
    ordner = booth.sessions_dir / "alte-hochzeit" / "2026-10-01" / "alt111"
    ordner.mkdir(parents=True)
    (ordner / "result.jpg").write_bytes(b"x")
    booth.library.upsert(uid="alt111", event_slug="alte-hochzeit", mode="photo", result=ordner / "result.jpg",
                         thumb=None, print_file=None, share_url="", look="original")
    return ordner


def test_the_card_shows_what_would_go(booth, tmp_path):
    for _ in range(3):
        _lauf(booth)
    _fremd(booth, tmp_path)
    stand = booth.testbilder_stand()
    assert stand["anzahl"] == 3
    assert stand["heute"] == 3 and stand["ohne_event"] == 3
    assert stand["andere_events"] == 1, "an earlier event is named, and kept"
    assert stand["laeuft"] is False
    assert stand["tippen"] is False


def test_removing_takes_runs_files_uploads_and_waiting_prints(booth, tmp_path):
    laeufe = [_lauf(booth) for _ in range(2)]
    fremd = _fremd(booth, tmp_path)
    uid = laeufe[0].uid
    assert booth.outbox.offen_fuer_session(uid)["offen"] > 0
    spool_id = booth.printer.spool.add(path=str(laeufe[0].print_file), copies=1, session_uid=uid)
    booth.printer.spool.hold(spool_id, "Papier leer")

    bericht = booth.testbilder_entfernen(anzahl=2)

    assert bericht["entfernt"] == 2
    for s in laeufe:
        assert booth.library.get(s.uid) is None
        assert not s.folder.exists()
        assert booth.outbox.offen_fuer_session(s.uid)["offen"] == 0
    assert booth.printer.spool.zustand(spool_id) == "lost"
    assert bericht["drucke_verworfen"] == 1
    assert booth.library.pending_deletions() == [], "the gallery is not touched"
    assert booth.library.get("alt111") is not None and fremd.exists(), "another event stays"
    assert booth.testbilder_stand()["anzahl"] == 0


def test_a_stale_number_removes_nothing(booth):
    for _ in range(2):
        _lauf(booth)
    with pytest.raises(HTTPException) as e:
        booth.testbilder_entfernen(anzahl=1)
    assert e.value.status_code == 409
    assert booth.testbilder_stand()["anzahl"] == 2


def test_not_while_a_guest_is_in_the_middle_of_a_run(booth):
    _lauf(booth)
    s = booth.start_session("collage", "4:5", "grid4", None)
    booth.take_shot(s)                                    # one of four
    assert booth.testbilder_stand()["laeuft"] is True
    with pytest.raises(HTTPException) as e:
        booth.testbilder_entfernen(anzahl=1)
    assert e.value.status_code == 409
    assert s.folder.exists() and booth.testbilder_stand()["anzahl"] == 1


def test_runs_of_an_assigned_event_need_the_typed_number(booth):
    booth.store.profile["event"].update({"slug": "hochzeit", "name": "Hochzeit"})
    for _ in range(2):
        _lauf(booth)
    stand = booth.testbilder_stand()
    assert stand["anzahl"] == 2 and stand["tippen"] is True
    with pytest.raises(HTTPException):
        booth.testbilder_entfernen(anzahl=2)              # a tap is not enough
    with pytest.raises(HTTPException):
        booth.testbilder_entfernen(anzahl=2, getippt="3")
    assert booth.testbilder_entfernen(anzahl=2, getippt="2")["entfernt"] == 2


def test_many_runs_need_the_typed_number_too(booth, monkeypatch):
    monkeypatch.setattr(type(booth), "TESTBILDER_GRENZE", 2)
    for _ in range(3):
        _lauf(booth)
    assert booth.testbilder_stand()["tippen"] is True


def test_counters_only_when_asked_and_paper_only_when_asked_separately(booth):
    s = _lauf(booth)
    booth.library.add_prints(s.uid, 2)
    booth.zahlen.zaehle("", "prints", 2)
    booth.printer.printed = 5
    booth.printer.counter_file.write_text("5")

    booth.testbilder_entfernen(anzahl=1)
    assert booth.library.event_prints("") == 2, "without the tick the counters stay"
    assert booth.zahlen.tag("").get("runs.photo") == 1

    s = _lauf(booth)
    booth.library.add_prints(s.uid, 1)
    assert booth.library.event_prints("") == 3
    bericht = booth.testbilder_entfernen(anzahl=1, zaehler=True)
    assert booth.library.event_prints("") == 2, "this run's prints no longer count against the event"
    assert booth.zahlen.tag("") == {}
    assert booth.printer.printed == 5, "paper was really used: the sheet count stays"
    assert bericht["zaehler"] is True and bericht["papier"] is False

    _lauf(booth)
    booth.testbilder_entfernen(anzahl=1, papier=True)
    assert booth.printer.printed == 0


def test_the_admin_route_needs_the_pin_and_reports(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    _lauf(booth)
    client = TestClient(create_app(booth))
    assert client.get("/api/admin/testbilder").status_code == 401
    booth.admin_tokens["t"] = time.time() + 600
    head = {"X-Admin-Token": "t"}
    assert client.get("/api/admin/testbilder", headers=head).json()["anzahl"] == 1
    antwort = client.post("/api/admin/testbilder/entfernen", json={"anzahl": 1}, headers=head)
    assert antwort.status_code == 200 and antwort.json()["entfernt"] == 1
    assert client.post("/api/admin/testbilder/entfernen", json={"anzahl": 1},
                       headers=head).status_code == 409


def test_the_admin_page_has_the_card():
    html = page(Path(__file__).parents[1] / "boothcore" / "web" / "admin.html")
    assert "Testbilder entfernen" in html
    assert "/api/admin/testbilder/entfernen" in html
    assert "Galerie" in html[html.index("Testbilder entfernen"):][:3000], \
        "the card says that the gallery keeps what it already has"
