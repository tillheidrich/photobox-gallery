"""Gästemails über die Galerie: „Bild per Mail senden" an der Box.

Die Box hat kein eigenes Postfach mehr zu haben. Sie schickt Adresse, Text und
die Kennung des Durchgangs an die Galerie; die hängt das Bild an und verschickt
es von ihrer Adresse. Grund: Am Veranstaltungsort ist der SMTP-Port oft
gesperrt, und ein Absender an einer Stelle ist ein SPF/DKIM-Eintrag statt
vieler.

Der heikle Fall steht unten: Die Mail darf nicht leer rausgehen, nur weil das
Bild noch nicht hochgeladen ist. Dann antwortet die Galerie mit 409 und die Box
versucht es später erneut.
"""
import io
import json
import secrets

import pytest
from PIL import Image


class _FakeSMTP:
    sent: list = []

    def __init__(self, *a, **kw):
        pass

    def __call__(self, *a, **kw):
        return self

    def ehlo(self, *a, **kw):
        return (250, b"ok")

    def starttls(self, **kw):
        return None

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)

    def quit(self):
        return None


@pytest.fixture()
def postausgang(monkeypatch):
    import smtplib

    from app import emailer

    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP())
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP())
    emailer.set_smtp_config({"host": "smtp.example.de", "port": 465, "username": "mailuser",
                             "password": "geheim", "from_email": "mail@photobox-gallery.com",
                             "use_ssl": True, "use_tls": False})
    yield _FakeSMTP
    emailer.set_smtp_config({"host": "", "from_email": ""})


def _jpeg(size=(800, 1000)):
    buf = io.BytesIO()
    Image.new("RGB", size, (190, 90, 60)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _box(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    slug = "mail-" + secrets.token_hex(3)
    eid = db.create_event(name="Mail Test", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
    con.commit(); con.close()
    _made = db.create_device(name="Box Mail")
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo"]}, eid)
    return db.get_event_by_id(eid), {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _upload(client, dev, event, uid):
    client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                content_type="multipart/form-data",
                data={"uid": uid, "session_uid": uid, "kind": "photo", "variant": "web",
                      "event_slug": event["slug"], "file": (_jpeg(), "result_web.jpg"),
                      "meta": json.dumps({"taken_at": "2026-09-20T20:00:00"})})


# ── Wer darf ──────────────────────────────────────────────
def test_a_stranger_gets_nothing(client):
    _box(client)
    r = client.post("/api/v2/booth/mail", json={"to": "gast@example.de"},
                    headers={"X-API-Key": "falsch"})
    assert r.status_code == 401


def test_without_a_mailbox_the_gallery_says_so(client):
    event, dev = _box(client)
    r = client.post("/api/v2/booth/mail", json={"to": "gast@example.de", "session_uid": "ohnepost01"},
                    headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 503 and "Postausgang" in r.get_json()["error"]


def test_a_broken_address_never_reaches_the_mailbox(client, postausgang):
    event, dev = _box(client)
    for bad in ("", "gast", "@example.de", "gast@example", "gast@.de",
                "gast..punkt@example.de", "x" * 260 + "@example.de"):
        r = client.post("/api/v2/booth/mail", json={"to": bad},
                        headers={"X-API-Key": dev["api_key"]})
        assert r.status_code == 400, bad
    assert postausgang.sent == []


# ── Der heikle Fall ───────────────────────────────────────
def test_no_photo_yet_means_try_again_later(client, postausgang):
    """409, nicht 200: Sonst bekäme der Gast eine Mail ohne sein Bild."""
    event, dev = _box(client)
    r = client.post("/api/v2/booth/mail", json={"to": "gast@example.de", "session_uid": "nochnicht01"},
                    headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 409
    assert postausgang.sent == []


def test_link_only_goes_out_even_before_the_upload(client, postausgang):
    event, dev = _box(client)
    r = client.post("/api/v2/booth/mail",
                    json={"to": "gast@example.de", "session_uid": "nochnicht02", "attach": "link"},
                    headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200 and r.get_json()["attached"] == 0
    assert len(postausgang.sent) == 1


# ── Der Normalfall ────────────────────────────────────────
def test_the_gallery_attaches_the_photo_and_adds_the_link(client, postausgang):
    event, dev = _box(client)
    uid = "mail2345ab"
    _upload(client, dev, event, uid)
    r = client.post("/api/v2/booth/mail",
                    json={"to": "gast@example.de", "session_uid": uid, "subject": "Deine Fotos"},
                    headers={"X-API-Key": dev["api_key"]})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["attached"] == 1
    # Der Link zeigt auf die Durchgangs-Seite — mit Signatur, sonst käme jeder
    # mit geratener Kennung an fremde Bilder.
    assert f"/p/{uid}-" in body["link"] and len(body["link"].rsplit("-", 1)[1]) >= 6

    msg = postausgang.sent[-1]
    assert msg["To"] == "gast@example.de" and msg["From"] == "mail@photobox-gallery.com"
    assert msg["Subject"] == "Deine Fotos"
    parts = [p.get_filename() for p in msg.iter_attachments()]
    assert len(parts) == 1 and parts[0]
    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert body["link"] in text


def _mail_texts(dev, subject, body):
    """What the operator sets in the gallery admin for this box (profile section "mail")."""
    from app import db, ecosystem
    current = db.get_device_by_id(dev["id"])
    profil = ecosystem.device_profile(current) or {}
    profil["mail"] = {**(profil.get("mail") or {}), "subject": subject, "body": body}
    ecosystem.save_device_profile(dev["id"], profil, current.get("event_id"))


def test_the_operators_text_from_the_gallery_is_used(client, postausgang):
    event, dev = _box(client)
    _mail_texts(dev, "Eure Fotos vom Fest", "Danke fürs Feiern!")
    uid = "mail2345cd"
    _upload(client, dev, event, uid)
    client.post("/api/v2/booth/mail",
                json={"to": "gast@example.de", "session_uid": uid},
                headers={"X-API-Key": dev["api_key"]})
    msg = postausgang.sent[-1]
    assert msg["Subject"] == "Eure Fotos vom Fest"
    assert "Danke fürs Feiern!" in msg.get_body(preferencelist=("plain",)).get_content()


def test_link_and_event_in_the_operators_text_are_filled_in(client, postausgang):
    """The form offers {event} and {link}; a guest must never read them raw."""
    event, dev = _box(client)
    _mail_texts(dev, "Fotos von {event}", "Hallo! Alle Aufnahmen: {link}\nFoto-Team {event}")
    uid = "mail2345kl"
    _upload(client, dev, event, uid)
    r = client.post("/api/v2/booth/mail", json={"to": "gast@example.de", "session_uid": uid},
                    headers={"X-API-Key": dev["api_key"]})
    link = r.get_json()["link"]
    msg = postausgang.sent[-1]
    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert msg["Subject"] == "Fotos von Mail Test"
    assert "{link}" not in text and "{event}" not in text
    assert f"Alle Aufnahmen: {link}" in text and text.count(link) == 1, "not appended twice"


def test_subject_and_text_sent_by_the_box_are_ignored(client, postausgang):
    """S-3: a device key is not a way to send any text to any address."""
    event, dev = _box(client)
    _mail_texts(dev, "Eure Fotos", "Viel Spaß damit.")
    client.post("/api/v2/booth/mail",
                json={"to": "gast@example.de", "session_uid": "relay0001", "attach": "link",
                      "subject": "Ihr Konto wurde gesperrt", "body": "Hier klicken: https://evil.example"},
                headers={"X-API-Key": dev["api_key"]})
    msg = postausgang.sent[-1]
    assert msg["Subject"] == "Eure Fotos"
    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert "evil.example" not in text and "gesperrt" not in text


def test_the_subject_cannot_be_endless(client, postausgang):
    event, dev = _box(client)
    _mail_texts(dev, "A" * 400, "")
    client.post("/api/v2/booth/mail",
                json={"to": "gast@example.de", "attach": "link", "session_uid": "lang0001"},
                headers={"X-API-Key": dev["api_key"]})
    assert len(postausgang.sent[-1]["Subject"]) == 200


def test_every_guest_mail_shows_up_in_the_device_log_without_the_address(client, postausgang):
    """Für die Fernwartung sichtbar — aber die Adresse des Gastes steht nicht drin."""
    event, dev = _box(client)
    uid = "mail2345ef"
    _upload(client, dev, event, uid)
    client.post("/api/v2/booth/mail", json={"to": "gast@example.de", "session_uid": uid},
                headers={"X-API-Key": dev["api_key"]})
    from app import ecosystem
    lines = [l["message"] for l in ecosystem.device_logs(dev["id"])]
    assert any("Foto-Mail" in l for l in lines)
    assert not any("gast@example.de" in l for l in lines)


def test_a_failing_mailbox_is_reported_as_temporary(client, postausgang, monkeypatch):
    """502 heißt für die Box: in der Warteschlange lassen, später erneut."""
    from app import emailer

    def boom(**kw):
        raise RuntimeError("Verbindung abgelehnt")

    monkeypatch.setattr(emailer, "send_text_mail", boom)
    event, dev = _box(client)
    r = client.post("/api/v2/booth/mail", json={"to": "gast@example.de", "attach": "link",
                                                "session_uid": "boom0001"},
                    headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 502 and "abgelehnt" in r.get_json()["error"]


# ── Sicherheitsdurchsicht 14.11.2026 (Befund S2) ──────────
def test_a_box_cannot_mail_the_pictures_of_another_boxs_event(client, postausgang):
    """Any device key could attach the pictures of any run, whoever's event it was."""
    event_a, dev_a = _box(client)
    event_b, dev_b = _box(client)
    _upload(client, dev_a, event_a, "fremd0001")
    r = client.post("/api/v2/booth/mail", json={"to": "gast@example.de", "session_uid": "fremd0001"},
                    headers={"X-API-Key": dev_b["api_key"]})
    assert r.status_code == 404
    assert postausgang.sent == []
    ok = client.post("/api/v2/booth/mail", json={"to": "gast@example.de", "session_uid": "fremd0001"},
                     headers={"X-API-Key": dev_a["api_key"]})
    assert ok.status_code == 200 and len(postausgang.sent) == 1


def test_a_subject_cannot_smuggle_in_a_header(client, postausgang):
    event, dev = _box(client)
    r = client.post("/api/v2/booth/mail", headers={"X-API-Key": dev["api_key"]},
                    json={"to": "gast@example.de", "attach": "link", "session_uid": "x1",
                          "subject": "Fotos\r\nBcc: alle@example.de"})
    assert r.status_code == 200
    msg = postausgang.sent[-1]
    assert "\n" not in str(msg["Subject"]) and "\r" not in str(msg["Subject"])
    assert msg["Bcc"] is None


def test_one_box_cannot_send_without_end(client, postausgang, monkeypatch):
    from app.blueprints import booth_v2
    monkeypatch.setattr(booth_v2, "MAILS_PER_HOUR", 3)
    event, dev = _box(client)
    codes = [client.post("/api/v2/booth/mail", headers={"X-API-Key": dev["api_key"]},
                         json={"to": "gast@example.de", "attach": "link", "session_uid": "x2"}).status_code
             for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]


# ── Security review G3, finding S-3 (15.11.2026): no mail relay ──
def test_exactly_one_recipient_per_call(client, postausgang):
    event, dev = _box(client)
    for many in ("a@example.de,b@example.de", "a@example.de; b@example.de", "a@example.de b@example.de",
                 ["a@example.de", "b@example.de"]):
        r = client.post("/api/v2/booth/mail", headers={"X-API-Key": dev["api_key"]},
                        json={"to": many, "attach": "link", "session_uid": "viele0001"})
        assert r.status_code == 400, many
    assert postausgang.sent == []


def test_a_mail_needs_a_run(client, postausgang):
    event, dev = _box(client)
    r = client.post("/api/v2/booth/mail", headers={"X-API-Key": dev["api_key"]},
                    json={"to": "gast@example.de", "attach": "link"})
    assert r.status_code == 400 and postausgang.sent == []


def test_one_run_is_mailed_only_a_few_times(client, postausgang):
    from app.blueprints import booth_v2
    event, dev = _box(client)
    codes = [client.post("/api/v2/booth/mail", headers={"X-API-Key": dev["api_key"]},
                         json={"to": f"g{i}@example.de", "attach": "link", "session_uid": "lauf0001"}).status_code
             for i in range(booth_v2.MAILS_PER_RUN + 1)]
    assert codes[:-1] == [200] * booth_v2.MAILS_PER_RUN and codes[-1] == 429


def test_one_event_has_a_ceiling_too(client, postausgang, monkeypatch):
    from app.blueprints import booth_v2
    monkeypatch.setattr(booth_v2, "MAILS_PER_EVENT", 2)
    event, dev = _box(client)
    codes = [client.post("/api/v2/booth/mail", headers={"X-API-Key": dev["api_key"]},
                         json={"to": "gast@example.de", "attach": "link", "session_uid": f"ev{i}0001"}).status_code
             for i in range(3)]
    assert codes == [200, 200, 429]


def test_the_hourly_cap_is_lower_than_before():
    from app.blueprints import booth_v2
    assert booth_v2.MAILS_PER_HOUR <= 60 and booth_v2.MAILS_PER_RUN <= 5


def test_a_run_of_a_foreign_event_is_refused_even_as_a_link(client, postausgang):
    """A run whose recordings sit in another event, or that was deleted there, is not this box's."""
    event_a, dev_a = _box(client)
    event_b, dev_b = _box(client)
    _upload(client, dev_a, event_a, "fremd0002")
    r = client.post("/api/v2/booth/mail", headers={"X-API-Key": dev_b["api_key"]},
                    json={"to": "gast@example.de", "attach": "link", "session_uid": "fremd0002"})
    assert r.status_code == 404 and postausgang.sent == []
