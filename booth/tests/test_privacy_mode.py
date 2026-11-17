"""Datensparsamer Betrieb (Kita, Schule): drucken, löschen, fertig.

Geprüft wird nicht, ob Knöpfe verschwinden, sondern ob tatsächlich nichts
hinausgeht und nichts liegen bleibt.
"""
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    d = Path(tempfile.mkdtemp(prefix="booth-privacy-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    # So sieht eine Box im Einsatz aus: Galerie und zweite Ablage eingerichtet …
    b.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8},
                        "storage": {"folder": str(tmp_path / "ablage")}})
    b.store.profile["print"].update({"enabled": True, "auto": False})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _shoot(booth, mode="photo"):
    s = booth.start_session(mode, "4:5", None, None)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def test_normal_mode_still_uploads(booth):
    booth.store.profile["event"].update({"slug": "fest", "name": "Fest", "share_key": "k",
                                         "base_url": "https://galerie.example"})
    s = _shoot(booth)
    assert booth.outbox.counts()["pending"] > 0
    assert s.share_url.startswith("https://galerie.example")


def test_print_only_uploads_nothing(booth):
    """… und trotzdem geht nichts raus, sobald der Modus an ist."""
    booth.store.profile["privacy"].update({"mode": "print_only"})
    s = _shoot(booth)
    assert booth.outbox.counts()["pending"] == 0, "Im Kita-Modus darf nichts in die Warteschlange"
    assert s.share_url == "", "Kein QR-Link, der irgendwohin zeigen könnte"


def test_the_shot_is_gone_after_printing(booth):
    booth.store.profile["privacy"].update({"mode": "print_only", "delete_after": "print"})
    s = _shoot(booth)
    folder = s.folder
    assert booth.library.get(s.uid) and folder.exists()
    booth.print_uid(s.uid, 1)
    # Der Druck läuft im Hintergrund. Gewartet wird auf das Ereignis, nicht auf
    # eine Uhr: erst bis die Warteschlange leer ist, dann bis der Eintrag weg
    # ist. Eine feste Zeitspanne wäre auf einer beschäftigten Maschine zu kurz
    # und auf einer ruhigen zu lang.
    for _ in range(600):
        if not booth.printer.pending:
            break
        time.sleep(0.1)
    for _ in range(600):
        if not booth.library.get(s.uid):
            break
        time.sleep(0.1)
    assert booth.library.get(s.uid) is None, "Eintrag muss nach dem Druck weg sein"
    assert not folder.exists(), "Auch die Dateien müssen weg sein"


def test_nothing_survives_a_failed_print(booth):
    """Sicherheitsnetz: Auch ohne Druck ist nach kurzer Zeit alles gelöscht."""
    booth.store.profile["privacy"].update({"mode": "print_only", "delete_seconds": 10})
    s = _shoot(booth)
    folder = s.folder
    booth._wipe(s.uid, reason="Test")          # das tut der Timer nach Ablauf
    assert booth.library.get(s.uid) is None and not folder.exists()


def test_a_print_that_never_went_through_deletes_too(booth):
    """Im Kita-Modus wartet niemand mit neuem Papier.

    Seit der Druck in einer Warteschlange liegt, bleibt ein gescheiterter
    Auftrag liegen statt zu verschwinden — das Bild darf trotzdem nicht bis
    zum Ablauf des Sicherheitsnetzes herumliegen.
    """
    from boothcore.printing.service import PrintJob
    booth.store.profile["privacy"].update({"mode": "print_only", "delete_after": "print"})
    s = _shoot(booth)
    folder = s.folder
    booth._on_print_failed(PrintJob(s.print_file, 1, s.uid))
    assert booth.library.get(s.uid) is None and not folder.exists()


def test_deleted_runs_are_not_reported_to_a_gallery(booth):
    """Löschen im Kita-Modus darf keine Meldung an eine Galerie erzeugen."""
    booth.store.profile["privacy"].update({"mode": "print_only"})
    s = _shoot(booth)
    booth._wipe(s.uid)
    assert booth.library.pending_deletions() == [s.uid]   # Vormerkung bleibt harmlos
    assert booth.outbox.counts()["pending"] == 0


def test_gallery_and_mail_are_closed_not_just_hidden(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["privacy"].update({"mode": "print_only"})
    booth.store.profile["mail"].update({"enabled": True, "host": "smtp.example", "from_addr": "a@b.de"})
    client = TestClient(create_app(booth))
    s = _shoot(booth)
    assert client.get("/api/gallery").status_code == 403
    r = client.post(f"/api/session/{s.uid}/mail", json={"address": "kind@example.de"})
    assert r.status_code == 403 and "privacy" in r.json()["detail"]


def test_the_kiosk_is_told_what_not_to_offer(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["privacy"].update({"mode": "print_only", "notice": "Fotos bleiben hier."})
    state = TestClient(create_app(booth)).get("/api/state").json()
    assert state["privacy"] == {"print_only": True, "notice": "Fotos bleiben hier.", "seconds": 120}
    assert state["profile"]["qr_on_screen"] is False
    assert state["profile"]["gallery_on_booth"] is False
    assert state["profile"]["mail"]["enabled"] is False


def test_doctor_names_the_contradiction(booth):
    from boothcore.doctor import Doctor
    booth.store.profile["privacy"].update({"mode": "print_only"})
    check = next(c for c in Doctor(booth).run() if c.name == "Datensparsamer Betrieb")
    assert check.state == "warn"
    assert "Galerie" in check.message and "zweite Ablage" in check.message


def test_doctor_is_happy_on_a_clean_kita_box(booth):
    from boothcore.doctor import Doctor
    booth.store.save_local({"server": {"url": "", "api_key": ""}, "storage": {"folder": ""}})
    booth.store.profile["modes"] = ["photo", "collage"]        # nur, was gedruckt werden kann
    booth.store.profile["privacy"].update({"mode": "print_only"})
    check = next(c for c in Doctor(booth).run() if c.name == "Datensparsamer Betrieb")
    assert check.state == "ok" and "löschen" in check.message


def test_modes_nobody_could_take_home_are_not_offered(booth):
    """Video und Boomerang lassen sich nicht drucken — im Kita-Modus gibt es sie nicht."""
    from fastapi.testclient import TestClient

    import pytest as _pytest
    from fastapi import HTTPException
    from boothcore.app import create_app
    booth.store.profile["modes"] = ["photo", "collage", "gif", "boomerang", "video"]
    booth.store.profile["privacy"].update({"mode": "print_only"})
    state = TestClient(create_app(booth)).get("/api/state").json()
    assert state["profile"]["modes"] == ["photo", "collage", "gif"]
    with _pytest.raises(HTTPException):
        booth.start_session("video", "9:16", None, None)      # auch serverseitig zu


def test_doctor_mentions_the_modes_it_had_to_drop(booth):
    from boothcore.doctor import Doctor
    booth.store.save_local({"server": {"url": "", "api_key": ""}, "storage": {"folder": ""}})
    booth.store.profile["modes"] = ["photo", "video"]
    booth.store.profile["privacy"].update({"mode": "print_only"})
    check = next(c for c in Doctor(booth).run() if c.name == "Datensparsamer Betrieb")
    assert check.state == "warn" and "video" in check.message


def test_the_deletion_leaves_a_trace_that_reaches_the_gallery(booth):
    """Im Kita-Modus ist der Löschnachweis das Einzige, was die Box je verschickt."""
    booth.store.profile["privacy"].update({"mode": "print_only"})
    booth.logbuf.take(500)
    s = _shoot(booth)
    booth._wipe(s.uid, reason="Test")
    entries = booth.logbuf.peek(20)
    proof = [e for e in entries if s.uid in e["message"]]
    assert proof, "Ohne Beleg lässt sich nicht zeigen, dass gelöscht wurde"
    assert proof[0]["code"] == "privacy" and proof[0]["level"] == "info"
