"""Gästemails über die Galerie — der Weg ohne eigenes Postfach auf der Box.

Am Veranstaltungsort ist Port 587 oft gesperrt und das WLAN fremd. Deshalb
schickt die Box nur Adresse, Text und die Kennung des Durchgangs an die
Galerie; die hat das Bild ohnehin und verschickt es von ihrer Adresse.

Entscheidend ist das Verhalten im Fehlerfall: Scheitert der Aufruf, muss die
Mail in der Warteschlange bleiben. Eine verlorene Gästemail merkt niemand —
bis der Gast am nächsten Tag fragt, wo seine Bilder sind.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from boothcore.delivery.mail import MailSender

BASE = {"enabled": True, "host": "smtp.example.de", "port": 587, "user": "booth@example.de",
        "password": "secret", "security": "starttls", "from_addr": "booth@example.de",
        "from_name": "Fotobox", "subject": "Deine Fotos", "body": "Viel Spaß!",
        "attach": "web", "bcc": "", "dry_run": False}


@pytest.fixture()
def sender(tmp_path):
    cfg = dict(BASE)
    s = MailSender(tmp_path / "mail.sqlite", lambda: cfg, start_worker=False)
    s.config = cfg                     # damit ein Test ihn umstellen kann
    return s


def _photo(tmp_path: Path) -> Path:
    f = tmp_path / "result_web.jpg"
    f.write_bytes(b"\xff\xd8\xff" + b"0" * 2000)
    return f


def test_the_gallery_gets_address_text_and_session_but_no_file(sender, tmp_path):
    seen = []
    sender.set_relay(seen.append)
    item = sender.enqueue(address="Gast@Example.DE", files=[_photo(tmp_path)],
                          subject="Deine Fotos", body="Viel Spaß!", session_uid="abcd2345xy")
    assert sender.send_now(item) is True

    assert len(seen) == 1
    payload = seen[0]
    assert payload["to"] == "Gast@example.de"          # Domain klein, Name unangetastet
    assert payload["session_uid"] == "abcd2345xy"
    assert payload["subject"] == "Deine Fotos" and payload["body"] == "Viel Spaß!"
    assert payload["attach"] == "web"
    assert "files" not in payload and "attachments" not in payload
    assert sender.get(item)["status"] == "sent"


def test_a_booth_without_a_gallery_still_uses_its_own_mailbox(sender, tmp_path, monkeypatch):
    """Kein Relay eingetragen — dann bleibt es beim alten Weg über SMTP."""
    sent = []
    monkeypatch.setattr(sender, "_send_smtp", lambda cfg, msg: sent.append(msg))
    item = sender.enqueue(address="gast@example.de", files=[_photo(tmp_path)],
                          subject="x", body="y")
    assert sender.send_now(item) is True
    assert len(sent) == 1 and sent[0]["To"] == "gast@example.de"


def test_smtp_stays_possible_when_it_is_chosen(sender, tmp_path, monkeypatch):
    sender.config["transport"] = "smtp"
    sender.set_relay(lambda payload: pytest.fail("Die Galerie darf hier nichts verschicken"))
    sent = []
    monkeypatch.setattr(sender, "_send_smtp", lambda cfg, msg: sent.append(msg))
    item = sender.enqueue(address="gast@example.de", files=[_photo(tmp_path)], subject="x", body="y")
    assert sender.send_now(item) is True and len(sent) == 1


def test_a_gallery_that_does_not_answer_keeps_the_mail_in_the_queue(sender, tmp_path):
    def broken(payload):
        raise RuntimeError("Aufnahme noch nicht in der Galerie — später erneut")

    sender.set_relay(broken)
    item = sender.enqueue(address="gast@example.de", files=[_photo(tmp_path)], subject="x", body="y")
    assert sender.send_now(item) is False
    row = sender.get(item)
    assert row["status"] == "pending" and row["attempts"] == 1
    assert "noch nicht in der Galerie" in row["last_error"]
    assert row["next_try"] > row["created_at"]         # Backoff, kein Dauerfeuer


def test_the_test_mode_beats_the_gallery(sender, tmp_path):
    """Testbetrieb heißt: nichts verlässt das Haus — auch nicht über die Galerie."""
    sender.config["dry_run"] = True
    sender.set_relay(lambda payload: pytest.fail("Im Testbetrieb darf nichts rausgehen"))
    item = sender.enqueue(address="gast@example.de", files=[_photo(tmp_path)], subject="x", body="y")
    assert sender.send_now(item) is True
    assert sender.sent_messages and sender.sent_messages[0]["to"] == "gast@example.de"


def test_a_switched_off_mail_function_sends_nothing_either_way(sender, tmp_path):
    sender.config["enabled"] = False
    sender.set_relay(lambda payload: pytest.fail("Aus ist aus"))
    item = sender.enqueue(address="gast@example.de", files=[_photo(tmp_path)], subject="x", body="y")
    assert sender.send_now(item) is False
    assert sender.get(item)["status"] == "pending"


def test_link_only_mails_carry_no_attachment_hint(sender, tmp_path):
    sender.config["attach"] = "link"
    seen = []
    sender.set_relay(seen.append)
    item = sender.enqueue(address="gast@example.de", files=[_photo(tmp_path)], subject="x", body="y",
                          session_uid="abcd2345xy")
    assert sender.send_now(item) is True
    assert seen[0]["attach"] == "link"


def test_the_gallery_path_needs_no_sender_address_on_the_box(tmp_path):
    """Der Punkt dieses Weges — und genau das ging nicht.

    Voreinstellung ist „über die Galerie", und das Admin-Formular sagt dazu,
    dass die Box dafür kein Postfach braucht. ``_attempt`` baute die Nachricht
    trotzdem immer zuerst, und ``_build`` wirft ohne Absenderadresse. Ergebnis:
    Der Gast sah „gesendet", jeder Versuch scheiterte still, nach zwölf
    Anläufen stand die Mail auf „failed". Gefunden am 19.09.2026.
    """
    cfg = {"enabled": True, "transport": "gallery", "host": "", "user": "",
           "password": "", "from_addr": "", "from_name": "", "subject": "Deine Fotos",
           "body": "Viel Spaß!", "attach": "web", "bcc": "", "dry_run": False}
    s = MailSender(tmp_path / "mail.sqlite", lambda: cfg, start_worker=False)
    gesehen = []
    s.set_relay(gesehen.append)
    item = s.enqueue(address="gast@example.de", files=[_photo(tmp_path)],
                     subject="", body="", session_uid="abcd2345xy")
    assert s.send_now(item) is True, s.get(item).get("last_error")
    assert len(gesehen) == 1
    assert gesehen[0]["subject"] == "Deine Fotos"      # aus der Einstellung
    assert gesehen[0]["body"] == "Viel Spaß!"
    assert s.get(item)["status"] == "sent"
