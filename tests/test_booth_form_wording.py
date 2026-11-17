"""Words of the box form (review of 15.11.2026).

The operator reads this page at a venue, on a phone. No leftovers of a
conversation with a former version ("wie bisher"), no competitor's name as a
yardstick, no key names where a sentence belongs, no raw state values from the
box ("mock · ready"), and help texts that fit in a few lines.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "app" / "templates" / "admin" / "booth_profile.html"
TABS = ROOT / "app" / "templates" / "admin" / "settings" / "_tabs.html"

#: About three lines on a phone, two on a desktop card. Longer explanations
#: belong in docs/ANLEITUNG-BOX-EINRICHTEN.md.
HELP_MAX = 240


def _admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def test_no_leftovers_of_an_older_version_or_a_competitor():
    source = TEMPLATE.read_text(encoding="utf-8")
    for phrase in ("wie bisher", "wie dslrBooth", "dslrBooth"):
        assert phrase not in source, phrase


def test_a_placeholder_is_explained_as_a_sentence():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "= Kennung" not in source
    assert "wird durch die Nummer der Aufnahme ersetzt" in source


def test_the_assistant_tab_has_a_name_a_person_understands():
    tabs = TABS.read_text(encoding="utf-8")
    assert "KI-Zugang" not in tabs
    assert "Assistenten-Zugang (MCP)" in tabs


def test_help_texts_are_short():
    source = TEMPLATE.read_text(encoding="utf-8")
    long = []
    for m in re.finditer(r"<(small|p)\b[^>]*>(.*?)</\1>", source, re.S):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|\{[%{#].*?[%}#]\}", "", m.group(2))).strip()
        if len(text) > HELP_MAX:
            long.append((source.count("\n", 0, m.start()) + 1, len(text), text[:60]))
    assert not long, f"help texts over {HELP_MAX} characters (line, length, start): {long}"


def test_the_state_of_the_box_is_shown_in_german_words(client, monkeypatch):
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Wortbox")["id"]
    states = [
        ({"camera_backend": "mock", "camera_state": "ready", "printer_name": "none", "printer_status": "none"},
         ("Testkamera", "bereit", "kein Drucker"), ("mock", "none ·")),
        ({"camera_backend": "edsdk", "camera_state": "reconnecting", "printer_name": "SELPHY", "printer_status": "offline"},
         ("Canon (USB)", "verbindet neu", "nicht erreichbar"), ("reconnecting", "edsdk")),
        ({"camera_backend": "webcam", "camera_state": "error", "printer_name": "DNP", "printer_status": "printing"},
         ("Webcam", "Fehler", "druckt"), ("error",)),
    ]
    for status, shown, hidden in states:
        monkeypatch.setattr(db, "get_device_status", lambda _id, s=status: dict(s, prints_today=0))
        card = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
        card = card.split("<h2>Zustand</h2>", 1)[1].split("</section>", 1)[0]
        for word in shown:
            assert word in card, (word, status)
        for word in hidden:
            assert word not in card, (word, status)


def test_a_state_nobody_mapped_is_shown_as_it_came(client, monkeypatch):
    """A new state of a newer box must not vanish from the page."""
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Wortbox")["id"]
    monkeypatch.setattr(db, "get_device_status",
                        lambda _id: {"camera_backend": "neue-kamera", "camera_state": "kaputt-neu", "prints_today": 0})
    card = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    card = card.split("<h2>Zustand</h2>", 1)[1].split("</section>", 1)[0]
    assert "neue-kamera" in card and "kaputt-neu" in card


def _job_row(dev_id, jtype, ok, result):
    from app import db
    con = db.get_db()
    con.execute("INSERT INTO device_jobs(device_id, type, payload_json, delivered_at, done_at, ok, result) "
                "VALUES (?,?,'{}',datetime('now'),datetime('now'),?,?)", (dev_id, jtype, ok, result))
    con.commit()
    con.close()


def test_a_screenshot_answer_is_a_short_line_and_never_a_wall_of_base64(client):
    """A screenshot job answers with image_base64 of 30-45 thousand characters; it made the
    Fernwartung table 376,000 px wide and the page scroll sideways."""
    import base64
    import json
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Bildbox")["id"]
    blob = base64.b64encode(bytes(range(256)) * 160).decode()[:40_000]
    _job_row(dev_id, "remote_control", 1, json.dumps({"action": "screenshot", "image_base64": blob, "w": 1280}))
    _job_row(dev_id, "doctor", 1, "x " * 400)
    _job_row(dev_id, "sync_now", 1, "A" * 5000)
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    card = page.split("<h2>Fernwartung</h2>", 1)[1].split("</section>", 1)[0]
    assert "Bild, 30 kB" in card
    assert "image_base64" not in card and len(card) < 12_000
    assert not re.search(r"[A-Za-z0-9+/=]{100,}", card), "an unbroken run would widen the table"
    assert blob[:100] not in page
    assert "overflow-wrap:anywhere" in page and "bp-job-table" in card
    assert "…" in card                      # the long text was cut, with an ellipsis


def test_the_sync_page_and_its_poll_show_the_same_short_answer(client):
    import base64
    import json
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Bildbox 2")["id"]
    from app import ecosystem
    ecosystem.save_sync_report(dev_id, {"version": "1.0", "log": [], "targets": [], "queue": {"pending": 0, "done": 0, "failed": 0},
                                       "autostart": {}, "last_files": [], "port": 8765})   # the full page needs a report
    blob = base64.b64encode(b"\xff\xd8\xff" * 12000).decode()
    _job_row(dev_id, "remote_control", 1, json.dumps({"image_base64": blob}))
    body = client.get(f"/admin/devices/{dev_id}/sync").get_data(as_text=True)
    assert blob[:200] not in body and "Bild, " in body
    recent = client.get(f"/admin/devices/{dev_id}/sync/state").get_json()["recent"]
    assert recent[0]["result"].startswith("Bild, ") and len(recent[0]["result"]) < 40
