"""Der Eingang: fremde Aufnahme-Programme laden per FTP und SFTP hoch.

Geprüft wird nicht, ob die Bibliotheken funktionieren, sondern die Zusagen
dieser Galerie: Ein Konto gehört zu genau einem Event, es kommt aus seinem
Ordner nicht heraus, eine hochgeladene Datei geht denselben Weg wie jeder
andere Upload, und im Eingang bleibt nichts liegen.
"""
import io
import secrets
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from app import db, ecosystem, eingang


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _event(client, name="Eingang"):
    kennung = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=name, slug=f"eing-{kennung}", upload_token=f"tok-{kennung}")
        db.update_event(eid, {"published": 1})
        return db.get_event_by_id(eid)


def _bilddatei(ziel: Path, farbe=(140, 120, 100)) -> Path:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 500), farbe).save(ziel)
    return ziel


# ── Konten ────────────────────────────────────────────────────────────
def test_a_password_is_shown_once_and_stored_only_as_a_hash(client):
    event = _event(client)
    with client.application.app_context():
        passwort = eingang.neues_passwort(event["id"])
        gespeichert = eingang.konto(event["id"])
    assert len(passwort) >= 15
    assert passwort not in gespeichert["passwort_hash"]
    assert gespeichert["benutzer"] == event["slug"]


def test_the_right_password_opens_the_right_event(client):
    a, b = _event(client, "A"), _event(client, "B")
    with client.application.app_context():
        pw_a = eingang.neues_passwort(a["id"])
        eingang.neues_passwort(b["id"])
        konto = eingang.pruefe(a["slug"], pw_a)
    assert konto and konto["event_id"] == a["id"]
    assert konto["ordner"].endswith(a["slug"])


def test_a_wrong_password_says_nothing_at_all(client):
    event = _event(client)
    with client.application.app_context():
        eingang.neues_passwort(event["id"])
        assert eingang.pruefe(event["slug"], "geraten") is None
        assert eingang.pruefe("gibtsnicht", "egal") is None


def test_a_new_password_makes_the_old_one_useless(client):
    event = _event(client)
    with client.application.app_context():
        alt = eingang.neues_passwort(event["id"])
        eingang.neues_passwort(event["id"])
        assert eingang.pruefe(event["slug"], alt) is None


def test_removing_the_account_closes_the_door(client):
    event = _event(client)
    with client.application.app_context():
        pw = eingang.neues_passwort(event["id"])
        eingang.konto_loeschen(event["id"])
        assert eingang.pruefe(event["slug"], pw) is None


# ── Einlesen ──────────────────────────────────────────────────────────
def test_a_file_in_the_inbox_reaches_the_gallery(client):
    event = _event(client)
    with client.application.app_context():
        eingang.neues_passwort(event["id"])
        datei = _bilddatei(eingang.ordner_fuer(event["slug"]) / "IMG_1.jpg")
        # Der Eingang nimmt nur, was seit Sekunden unverändert daliegt.
        import os
        alt = time.time() - 30
        os.utime(datei, (alt, alt))
        assert eingang.einlesen() == 1
        medien = db.list_media(event["id"]) or []
    assert len(medien) == 1 and medien[0]["kind"] == "photo"
    assert not datei.exists(), "Die Datei blieb im Eingang liegen"


def test_a_file_that_is_still_being_written_is_left_alone(client):
    event = _event(client)
    with client.application.app_context():
        eingang.neues_passwort(event["id"])
        _bilddatei(eingang.ordner_fuer(event["slug"]) / "IMG_2.jpg")
        assert eingang.einlesen() == 0
        assert not (db.list_media(event["id"]) or [])


@pytest.mark.parametrize("name, art", [
    ("IMG_1.jpg", "photo"),
    ("print_0001.jpg", "print"),
    ("original_0001.jpg", "original"),
    ("boomerang_1.mp4", "boomerang"),
    ("clip.mp4", "video"),
    ("anim.gif", "gif"),
])
def test_the_kind_is_read_from_the_name_like_in_the_sync_programme(name, art):
    assert eingang.art_aus_name(Path(name)) == art


def test_something_that_is_not_a_photo_never_enters_the_gallery(client):
    event = _event(client)
    with client.application.app_context():
        eingang.neues_passwort(event["id"])
        fremd = eingang.ordner_fuer(event["slug"]) / "liesmich.txt"
        fremd.write_text("kein Bild")
        import os
        alt = time.time() - 30
        os.utime(fremd, (alt, alt))
        eingang.einlesen()
        assert not (db.list_media(event["id"]) or [])
    assert not fremd.exists()


def test_a_moderated_event_holds_an_ftp_upload_back_too(client):
    """Ein zweiter Weg in die Galerie darf keine zweite Freigaberegel haben."""
    import json

    from app import gallery_settings as gsm
    event = _event(client)
    with client.application.app_context():
        db.update_event(event["id"], {"gallery_json": json.dumps(gsm.clean({"moderation": True}))})
        eingang.neues_passwort(event["id"])
        datei = _bilddatei(eingang.ordner_fuer(event["slug"]) / "IMG_3.jpg")
        import os
        alt = time.time() - 30
        os.utime(datei, (alt, alt))
        eingang.einlesen()
        assert not (db.list_media(event["id"]) or []), "Sichtbar, obwohl Moderation an ist"
        assert ecosystem.pending_review_count(event["id"]) == 1


# ── Die Dienste sind aus, bis jemand sie einschaltet ──────────────────
def test_both_services_are_off_by_default(client):
    with client.application.app_context():
        assert eingang.an("ftp") is False
        assert eingang.an("sftp") is False


def test_the_settings_page_opens_and_saves(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    assert client.get("/admin/settings/eingang").status_code == 200
    client.post("/admin/settings/eingang", data={"csrf_token": "x", "tat": "dienste",
                                                 "eingang_sftp": "on", "eingang_sftp_port": "2299"})
    with client.application.app_context():
        assert eingang.an("sftp") is True
        assert eingang.einstellung("eingang_sftp_port") == "2299"


def test_a_nonsense_port_falls_back_instead_of_breaking_the_server(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post("/admin/settings/eingang", data={"csrf_token": "x", "tat": "dienste",
                                                 "eingang_ftp_port": "hallo",
                                                 "eingang_ftp_passiv": "unfug"})
    with client.application.app_context():
        assert eingang.einstellung("eingang_ftp_port") == eingang.STANDARD["eingang_ftp_port"]
        assert eingang.einstellung("eingang_ftp_passiv") == eingang.STANDARD["eingang_ftp_passiv"]
