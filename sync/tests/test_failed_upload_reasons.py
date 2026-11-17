"""Why uploads gave up, in plain words, in the heartbeat - without file names or paths.

The live booth showed "8 Uploads endgültig fehlgeschlagen" and nothing else
(17.11.2026). The queue keeps the raw error; the heartbeat now carries one
short reason per entry and a count per reason, and neither the file name
(naming patterns can put a guest's text into it) nor the raw error (it can
hold the path).
"""
import json

import pytest

from photobox_sync import reasons
from photobox_sync.agent import Agent
from photobox_sync.outbox import Outbox


@pytest.mark.parametrize("raw,plain", [
    ('422: {"ok": false, "error": "no event assigned"}', "kein Event zugewiesen"),
    ("413: Request Entity Too Large", "Galerie lehnt ab: 413 (Datei zu groß)"),
    ("HTTP 413: Datei zu groß", "Galerie lehnt ab: 413 (Datei zu groß)"),
    ('409: {"error": "uid belongs to another event or device"}', "Aufnahme gehört zu einem anderen Event"),
    ("418: teapot", "Galerie lehnt ab: 418"),
    ("Datei fehlt: C:\\Users\\Box\\Fotobox\\Anna-und-Ben-0042.jpg", "Datei fehlt"),
    ("FileNotFoundError: [Errno 2] No such file or directory: '/data/x/Anna.jpg'", "Datei fehlt"),
    ("PermissionError: [Errno 13] Permission denied: '/mnt/nas/fest'", "keine Schreibrechte am Ziel"),
    ("/mnt/nas/fest: [Errno 28] No space left on device", "Ziel ist voll"),
    ("Anmeldung abgelehnt: Authentication failed", "Anmeldung am Ziel abgelehnt"),
    ("API-Schlüssel ungültig", "API-Schlüssel ungültig"),
    ("Ziel nicht konfiguriert", "Ziel nicht eingerichtet"),
    ("ValueError: something odd", "Fehler an der Box (ValueError)"),
    ("", "unbekannt"),
])
def test_a_raw_error_becomes_one_plain_reason(raw, plain):
    assert reasons.explain(raw) == plain


def test_no_reason_names_a_path_or_a_file():
    for raw in ("Datei fehlt: /data/Anna-Meier.jpg", "/srv/Anna-Meier: something", "OSError: /a/Anna-Meier.png"):
        assert "Anna" not in reasons.explain(raw)


def _box(tmp_path):
    box = Outbox(tmp_path / "outbox.sqlite")
    errors = ['422: {"ok": false, "error": "no event assigned"}'] * 5 + ["413: too large"] * 2 \
        + ["Datei fehlt: /daten/Anna-und-Ben-0042.jpg"]
    for i, e in enumerate(errors):
        f = tmp_path / f"Anna-und-Ben-{i:04d}.jpg"
        f.write_bytes(b"x")
        box.add(target="gallery", uid=f"u{i}", session_uid=f"run{i}", event_slug="", kind="photo", variant="", path=f)
        with box._con() as con:
            con.execute("UPDATE outbox SET status='failed', last_error=?, attempts=8 WHERE uid=?", (e, f"u{i}"))
    return box


def test_the_heartbeat_says_why_counted_and_names_no_guest_file(tmp_path):
    agent = Agent(outbox=_box(tmp_path), local_config=lambda: {"server": {"url": "", "api_key": ""}})
    st = agent.outbox_status()
    assert st["failed_reasons"] == [{"reason": "kein Event zugewiesen", "count": 5},
                                    {"reason": "Galerie lehnt ab: 413 (Datei zu groß)", "count": 2},
                                    {"reason": "Datei fehlt", "count": 1}]
    assert {f["reason"] for f in st["failed_uploads"]} == {"kein Event zugewiesen", "Datei fehlt",
                                                            "Galerie lehnt ab: 413 (Datei zu groß)"}
    text = json.dumps(st, ensure_ascii=False)
    assert "Anna" not in text and ".jpg" not in text and "/daten" not in text
    assert all("file" not in f and "error" not in f for f in st["failed_uploads"])


def test_short_is_one_line_for_the_self_check():
    assert reasons.short(["422: no event assigned"] * 3 + ["Datei fehlt: /x.jpg"]) \
        == "3× kein Event zugewiesen, 1× Datei fehlt"
