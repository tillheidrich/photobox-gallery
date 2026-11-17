"""Fremde Ablagen wollen den Dateinamen, nicht unsere Kennung.

Nextcloud kennt für eine einzelne Datei in einem geteilten Ordner nur
``…/index.php/s/<token>/download?path=%2F&files=<name>``. Damit ein QR-Code
darauf zeigen kann, muss die Box den Namen kennen, **bevor** das Foto
existiert — sonst steht auf dem Abzug ein Link ins Leere.

Er steht auch fest: Kennung des Durchgangs plus ein fester Dateiname je
Betriebsart. Diese Datei hält genau diese Kopplung fest, damit sie nicht
still auseinanderläuft.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sync"))

from boothcore import share                                  # noqa: E402

NEXTCLOUD = "https://wolke.example.de/index.php/s/AbCdEf123/download?path=%2F&files={filename}"


def _profil(url: str, **event):
    return {"event": {"slug": "fest", "name": "Sommerfest", "share_key": "", **event},
            "qr": {"mode": "custom", "url": url}}


def test_der_dateiname_landet_in_der_adresse():
    url = share.qr_target(_profil(NEXTCLOUD), "abcd2345xy")
    assert url.endswith("files=abcd2345xy_result_web.jpg")


@pytest.mark.parametrize("mode,erwartet", [
    ("photo", "result_web.jpg"), ("collage", "result_web.jpg"),
    ("gif", "result.gif"), ("boomerang", "boomerang.mp4"), ("video", "video.mp4"),
])
def test_je_betriebsart_der_richtige_name(mode, erwartet):
    assert share.ziel_dateiname("uid1", mode) == f"uid1_{erwartet}"


def test_ein_unbekannter_modus_faellt_auf_das_foto_zurueck():
    assert share.ziel_dateiname("uid1", "irgendwas").endswith("result_web.jpg")


def test_der_name_wird_kodiert():
    """Ein Leerzeichen oder Umlaut zerlegt sonst die ganze Adresse."""
    gefuellt = share._fill("x={filename}", "u", "c", {}, "Sommerfest Foto ä.jpg")
    assert gefuellt == "x=Sommerfest%20Foto%20%C3%A4.jpg"
    assert " " not in gefuellt


def test_ohne_platzhalter_aendert_sich_nichts():
    url = share.qr_target(_profil("https://example.de/p/{uid}"), "abcd2345xy")
    assert url == "https://example.de/p/abcd2345xy"


def test_der_link_steht_schon_vor_der_aufnahme():
    """Der ganze Sinn: Der QR ist auf dem Abzug, bevor es den Abzug gibt."""
    from boothcore.session import new_session
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        s = new_session(Path(t), "photo", "4:5", _profil(NEXTCLOUD), None, None)
        assert s.share_url.endswith(f"files={s.uid}_result_web.jpg")
        assert not s.shots, "Der Durchgang hat noch kein einziges Foto"


# ── die Kopplung, die leicht auseinanderläuft ─────────────────────────

def test_der_vorhergesagte_name_ist_der_name_am_ziel():
    """Was die Box vorhersagt, muss das Ziel auch so nennen.

    ``targets.file_name()`` stellt jedem Ziel-Dateinamen die Kennung voran.
    Wer dort oder an den Dateinamen in ``finalize`` etwas ändert, bekommt
    hier einen roten Test statt eines QR-Codes ins Leere.
    """
    from photobox_sync.targets import file_name
    for mode, datei in share.WEB_DATEI.items():
        am_ziel = file_name({"path": f"/irgendwo/sessions/x/{datei}", "uid": "abcd2345xy"})
        assert am_ziel == share.ziel_dateiname("abcd2345xy", mode)


# ── und derselbe Abgleich an einem echten Durchgang ───────────────────

@pytest.fixture()
def booth(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    import time as _t
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        _t.sleep(0.1)
    yield b
    b.camera.stop()


def test_ein_echter_durchgang_heisst_wie_vorhergesagt(booth):
    """Kein Tabellenvergleich: ein Foto machen und nachsehen, wie es heißt."""
    from photobox_sync.targets import file_name
    booth.store.profile["qr"] = {"mode": "custom", "url": NEXTCLOUD}
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    assert s.result_web and s.result_web.exists()
    am_ziel = file_name({"path": str(s.result_web), "uid": s.uid})
    assert am_ziel == share.ziel_dateiname(s.uid, "photo")
    assert s.share_url.endswith(f"files={am_ziel}"), \
        f"Der QR zeigt auf {s.share_url}, die Datei heißt {am_ziel}"
