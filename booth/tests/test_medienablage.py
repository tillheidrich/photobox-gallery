"""Medien und Töne an der Box einlagern — und die zwei Regeln, die zählen.

Bis zum 20.09.2026 gab es in der ganzen Box genau einen Upload, und der nahm
nur Vorlagenpakete. Ein eigener Auslöseton, ein Hintergrund, ein Startvideo:
kein Weg durch die Verwaltung. Der Betreiber hätte an die Dateiablage des
Kiosk-Rechners gemusst — auf einem Gerät ohne Tastatur keine Antwort.

Die Regeln, die hier mehr wiegen als Bequemlichkeit: Namen werden entschärft
statt abgelehnt, und was in Gebrauch ist, wird nicht gelöscht.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boothcore import medienablage as m                       # noqa: E402


# ── Namen entschärfen ─────────────────────────────────────────────────

@pytest.mark.parametrize("roh,erwartet", [
    ("Mein Grüner Hintergrund (2).PNG", "mein-gruener-hintergrund-2.png"),
    ("klick.mp3", "klick.mp3"),
    ("Ölwechsel Ätzend Übung.wav", "oelwechsel-aetzend-uebung.wav"),
    ("Straße.jpg", "strasse.jpg"),
    ("  viele   leerzeichen  .png", "viele-leerzeichen.png"),
    ("../../etc/passwd.png", "passwd.png"),
    ("/absolut/pfad/bild.png", "bild.png"),
    # Rückstriche sind unter Linux kein Pfadtrenner — sie werden zu Bindestrichen.
    # Sicher ist das trotzdem: Ohne "/" und ohne ".." geht es nirgendwohin.
    ("..\\windows\\system32\\ton.mp3", "windows-system32-ton.mp3"),
])
def test_namen_werden_entschaerft(roh, erwartet):
    assert m.sauberer_name(roh) == erwartet


def test_umlaute_werden_umgeschrieben_nicht_weggeworfen():
    """„gruen" ist lesbar, „gr n" ist Müll."""
    assert m.sauberer_name("Grün.png") == "gruen.png"
    assert "n" in m.sauberer_name("Grün.png")


def test_ein_name_aus_lauter_sonderzeichen_ergibt_trotzdem_etwas():
    assert m.sauberer_name("???.png") == "datei.png"
    assert m.sauberer_name("") == "datei"


def test_kein_name_bricht_aus_dem_ordner_aus():
    for boese in ("../../../etc/passwd.png", "....//....//x.png", "/etc/shadow.mp3"):
        assert "/" not in m.sauberer_name(boese) and ".." not in m.sauberer_name(boese)


# ── nie überschreiben ─────────────────────────────────────────────────

def test_gleichnamiges_wird_nicht_ueberschrieben(tmp_path):
    (tmp_path / "ton.mp3").write_bytes(b"alt")
    assert m.freier_name(tmp_path, "ton.mp3") == "ton-2.mp3"
    (tmp_path / "ton-2.mp3").write_bytes(b"auch alt")
    assert m.freier_name(tmp_path, "ton.mp3") == "ton-3.mp3"


def test_freier_name_laesst_neues_in_ruhe(tmp_path):
    assert m.freier_name(tmp_path, "neu.png") == "neu.png"


# ── Art erkennen ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name,art", [
    ("a.jpg", "bild"), ("a.PNG", "bild"), ("a.webp", "bild"),
    ("a.mp4", "video"), ("a.webm", "video"),
    ("a.mp3", "ton"), ("a.wav", "ton"), ("a.m4a", "ton"),
    ("a.json", ""), ("a.exe", ""), ("a", ""),
])
def test_art(name, art):
    assert m.art(name) == art


def test_ausfuehrbares_kommt_nicht_durch():
    for boese in ("schad.exe", "skript.sh", "x.bat", "y.py", "z.html"):
        assert m.art(boese) == "", f"{boese} darf nicht als Medium gelten"


# ── Verwendung erkennen ───────────────────────────────────────────────

def _profil():
    return {"sounds": {"shutter": "klick.mp3", "done": "", "countdown": "", "print": ""},
            "start": {"media": ["intro.mp4", "bild.png"]},
            "backgrounds": ["strand.jpg"],
            "branding": {"logo": "logo.png", "watermark": ""}}


def test_verwendung_wird_auf_deutsch_benannt():
    assert m.verwendung(_profil(), "klick.mp3") == ["Ton beim Auslösen"]
    assert m.verwendung(_profil(), "intro.mp4") == ["Medien auf dem Startbildschirm"]
    assert m.verwendung(_profil(), "strand.jpg") == ["Hintergrund für die Freistellung"]
    assert m.verwendung(_profil(), "logo.png") == ["Logo"]


def test_unbenutztes_ist_unbenutzt():
    assert m.verwendung(_profil(), "nie-benutzt.png") == []


def test_ein_leeres_feld_faengt_nicht_alles_ein():
    """``done: ""`` darf nicht plötzlich jede Datei als verwendet melden."""
    assert "Ton, wenn das Bild fertig ist" not in m.verwendung(_profil(), "")
    assert m.verwendung(_profil(), "") == []


def test_mehrfachverwendung_wird_vollstaendig_gemeldet():
    profil = _profil()
    profil["backgrounds"] = ["klick.mp3"]           # unsinnig, aber möglich
    assert len(m.verwendung(profil, "klick.mp3")) == 2


def test_vorlagen_zaehlen_mit():
    vorlagen = [{"id": "duo", "name": "Hochzeit · 2", "overlay": "ranken.png"},
                {"id": "solo", "background": "beige.png"}]
    assert m.in_vorlagen(vorlagen, "ranken.png") == ["Hochzeit · 2"]
    assert m.in_vorlagen(vorlagen, "beige.png") == ["solo"]
    assert m.in_vorlagen(vorlagen, "anderes.png") == []


def test_kaputte_vorlagen_werfen_nicht():
    assert m.in_vorlagen([None, "text", {}, {"overlay": None}], "x.png") == []


# ── die Liste ─────────────────────────────────────────────────────────

def test_liste_zeigt_art_groesse_und_verwendung(tmp_path):
    (tmp_path / "klick.mp3").write_bytes(b"x" * 500)
    (tmp_path / "frei.png").write_bytes(b"y" * 100)
    (tmp_path / "vorlage.json").write_text("{}")      # kein Medium
    eintraege = {e["name"]: e for e in m.liste(tmp_path, _profil())}
    assert set(eintraege) == {"klick.mp3", "frei.png"}, "JSON gehört nicht in die Medienliste"
    assert eintraege["klick.mp3"]["art"] == "ton"
    assert eintraege["klick.mp3"]["bytes"] == 500
    assert eintraege["klick.mp3"]["verwendet_in"] == ["Ton beim Auslösen"]
    assert eintraege["frei.png"]["verwendet_in"] == []


def test_liste_ohne_ordner_ist_leer(tmp_path):
    assert m.liste(tmp_path / "gibtsnicht", {}) == []


# ── die Endpunkte, an einer echten Box ────────────────────────────────

@pytest.fixture()
def box(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from fastapi.testclient import TestClient
    from boothcore.app import Booth, create_app
    b = Booth(base=tmp_path, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})  # the operator has replaced the factory PIN (see test_factory_pin.py)
    c = TestClient(create_app(b))
    pin = str(b.profile.get("admin_pin") or b.store.local.get("admin_pin") or "1234")
    token = c.post("/api/admin/login", json={"pin": pin}).json().get("token", "")
    c.headers.update({"X-Admin-Token": token})
    yield b, c


def _hoch(c, name, roh=b"x" * 64):
    return c.post("/api/admin/assets", files={"datei": (name, roh, "application/octet-stream")})


def test_hochladen_und_wiederfinden(box):
    b, c = box
    r = _hoch(c, "Mein Ton.MP3")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "mein-ton.mp3"
    assert (b.assets_dir / "mein-ton.mp3").exists()
    namen = [a["name"] for a in c.get("/api/admin/assets").json()["assets"]]
    assert "mein-ton.mp3" in namen


def test_zweimal_derselbe_name_ueberschreibt_nicht(box):
    b, c = box
    _hoch(c, "ton.mp3", b"erste")
    zweite = _hoch(c, "ton.mp3", b"zweite")
    assert zweite.json()["name"] == "ton-2.mp3"
    assert (b.assets_dir / "ton.mp3").read_bytes() == b"erste", "Die erste Datei wurde überschrieben"


def test_ausfuehrbares_wird_abgewiesen(box):
    b, c = box
    r = _hoch(c, "schad.exe")
    assert r.status_code == 400
    assert not (b.assets_dir / "schad.exe").exists()


def test_leere_datei_wird_abgewiesen(box):
    _, c = box
    assert _hoch(c, "leer.png", b"").status_code == 400


def test_zu_grosses_wird_abgewiesen(box):
    b, c = box
    from boothcore import medienablage as mm
    r = _hoch(c, "riesig.mp4", b"x" * (mm.MAX_BYTES + 1))
    assert r.status_code == 413
    assert not list(b.assets_dir.glob("riesig*"))


def test_kein_upload_ohne_anmeldung(box):
    b, _ = box
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    frisch = TestClient(create_app(b))
    r = frisch.post("/api/admin/assets", files={"datei": ("x.png", b"x", "image/png")})
    assert r.status_code in (401, 403)


def test_loeschen_geht(box):
    b, c = box
    _hoch(c, "weg.png")
    assert c.delete("/api/admin/assets/weg.png").status_code == 200
    assert not (b.assets_dir / "weg.png").exists()


def test_was_in_gebrauch_ist_wird_nicht_geloescht(box):
    b, c = box
    _hoch(c, "klick.mp3")
    b.store.profile.setdefault("sounds", {})["shutter"] = "klick.mp3"
    r = c.delete("/api/admin/assets/klick.mp3")
    assert r.status_code == 409
    assert "Ton beim Auslösen" in r.json()["detail"], "Die Absage muss sagen, wo es gebraucht wird"
    assert (b.assets_dir / "klick.mp3").exists()


def test_nach_dem_abwaehlen_geht_es_doch(box):
    b, c = box
    _hoch(c, "klick.mp3")
    b.store.profile.setdefault("sounds", {})["shutter"] = "klick.mp3"
    assert c.delete("/api/admin/assets/klick.mp3").status_code == 409
    b.store.profile["sounds"]["shutter"] = ""
    assert c.delete("/api/admin/assets/klick.mp3").status_code == 200


def test_loeschen_bricht_nicht_aus_dem_ordner_aus(box):
    b, c = box
    # Eine Datei außerhalb der Medienablage, die es wirklich gibt.
    opfer = b.data / "nicht-anfassen.txt"
    opfer.write_text("wichtig")
    for boese in ("..%2F..%2Flocal.json", "....//local.json", "%2Fetc%2Fpasswd"):
        assert c.delete(f"/api/admin/assets/{boese}").status_code in (404, 405)
    assert opfer.exists(), "Der Löschweg ist aus der Medienablage ausgebrochen"
