"""Die Testseite — und ihr wichtigster Grundsatz: im Zweifel nicht grün.

Eine Seite, die vor dem Event beruhigt, obwohl nichts nachgewiesen ist, ist
schlimmer als gar keine. Sie ersetzt die berechtigte Unruhe des Betreibers
durch falsche Sicherheit, und die fliegt am Abend auf, wenn niemand mehr
etwas ändern kann.

Deshalb prüfen die meisten Tests hier, wann NICHT „in Ordnung" herauskommt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boothcore import einrichtung as e                        # noqa: E402


class Antwort:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def antwortet(status=200):
    def oeffnen(url, timeout=None):
        return Antwort(status)
    return oeffnen


def schweigt(url, timeout=None):
    raise OSError("keine Verbindung")


# ── der Grundsatz ─────────────────────────────────────────────────────

def test_ohne_nachweis_nie_gruen():
    """Webcam und Ordner lassen sich nicht aus der Ferne feststellen."""
    for weg in ("webcam", "folder", "mock"):
        s = e.kamera_stand({"camera": {"backend": weg}})
        assert s["stand"] == e.UNKLAR, f"{weg} wurde ungeprüft für gut befunden"


def test_ein_eingerichteter_drucker_allein_ist_noch_kein_gruen():
    """Ob Größe und Rand stimmen, sagt nur das gedruckte Blatt."""
    s = e.drucker_stand({"printers": [{"backend": "windows"}]})
    assert s["stand"] == e.UNKLAR
    assert "Lineal" in s["tun"]


def test_gesamt_ist_nur_gruen_wenn_beides_gruen_ist():
    lokal = {"camera": {"backend": "ccapi", "ccapi_url": "http://10.0.0.5"},
             "printers": [{"backend": "windows"}]}
    d = e.stand(lokal, {}, oeffner=antwortet())
    assert d["kamera"]["stand"] == e.OK
    assert d["drucker"]["stand"] == e.UNKLAR
    assert d["gesamt"] == e.UNKLAR, "Ein gelber Teil darf das Ganze nicht grün machen"


def test_ein_fehlendes_stueck_schlaegt_auf_das_ganze_durch():
    d = e.stand({"camera": {"backend": "ccapi", "ccapi_url": ""}}, {})
    assert d["gesamt"] == e.FEHLT


# ── Canon über WLAN ───────────────────────────────────────────────────

def test_ccapi_ohne_adresse_sagt_was_zu_tun_ist():
    s = e.kamera_stand({"camera": {"backend": "ccapi"}})
    assert s["stand"] == e.FEHLT
    assert "CCAPI freischalten" in s["tun"]
    assert "Kein SDK" in s["tun"], "Der entscheidende Punkt gehört dazu"


def test_ccapi_das_nicht_antwortet_ist_nicht_in_ordnung():
    s = e.kamera_stand({"camera": {"backend": "ccapi", "ccapi_url": "http://10.0.0.5"}},
                       oeffner=schweigt)
    assert s["stand"] == e.FEHLT
    assert "WLAN" in s["tun"]


def test_ccapi_das_antwortet_ist_in_ordnung():
    s = e.kamera_stand({"camera": {"backend": "ccapi", "ccapi_url": "http://10.0.0.5"}},
                       oeffner=antwortet())
    assert s["stand"] == e.OK


# ── Canon am Kabel ────────────────────────────────────────────────────

def test_edsdk_ohne_bibliothek_erklaert_warum_sie_fehlt(monkeypatch, tmp_path):
    from boothcore import edsdk_setup
    monkeypatch.setattr(edsdk_setup, "target_dir", lambda: tmp_path / "EDSDK")
    monkeypatch.setattr(edsdk_setup, "has_sdk_platform", lambda: True)
    s = e.kamera_stand({"camera": {"backend": "edsdk"}})
    assert s["stand"] == e.FEHLT
    assert "nicht mitgeliefert" in s["text"]
    assert "ZIP" in s["tun"], "Der einfache Weg muss dastehen"


def test_edsdk_auf_linux_weist_auf_gphoto2(monkeypatch):
    from boothcore import edsdk_setup
    monkeypatch.setattr(edsdk_setup, "has_sdk_platform", lambda: False)
    s = e.kamera_stand({"camera": {"backend": "edsdk"}})
    assert s["stand"] == e.FEHLT
    assert "gphoto2" in s["tun"]


# ── Sony/Nikon unter Windows ──────────────────────────────────────────

def test_digicam_ausserhalb_von_windows_sagt_das_ehrlich(monkeypatch):
    monkeypatch.setattr(e, "_system", lambda: "linux")
    s = e.kamera_stand({"camera": {"backend": "digicamcontrol"}})
    assert s["stand"] == e.FEHLT
    assert "gphoto2" in s["tun"]


def test_digicam_ohne_adresse_nennt_den_versteckten_schalter(monkeypatch):
    monkeypatch.setattr(e, "_system", lambda: "windows")
    s = e.kamera_stand({"camera": {"backend": "digicamcontrol"}})
    assert "Webserver" in s["tun"], "Genau daran scheitert es in der Praxis"


def test_digicam_das_antwortet_ist_in_ordnung(monkeypatch):
    monkeypatch.setattr(e, "_system", lambda: "windows")
    s = e.kamera_stand({"camera": {"backend": "digicamcontrol", "digicam_url": "http://localhost:5513"}},
                       oeffner=antwortet())
    assert s["stand"] == e.OK


# ── Drucker ───────────────────────────────────────────────────────────

def test_ohne_drucker_wird_das_gesagt():
    s = e.drucker_stand({})
    assert s["stand"] == e.FEHLT
    assert "Ordner" in s["tun"], "Der Ausweg gehört dazu — nicht jeder druckt"


def test_cups_ohne_lp_faellt_auf():
    s = e.drucker_stand({"printers": [{"backend": "cups"}]}, hat_lp=lambda: False)
    assert s["stand"] == e.FEHLT
    assert "cups-client" in s["tun"]


def test_abgeschaltete_drucker_zaehlen_nicht():
    s = e.drucker_stand({"printers": [{"backend": "none"}, {"backend": "none"}]})
    assert s["stand"] == e.FEHLT


def test_der_alte_einzeldrucker_wird_noch_verstanden():
    """Ältere Konfigurationen haben printer statt printers."""
    s = e.drucker_stand({"printer": {"backend": "windows"}})
    assert s["stand"] == e.UNKLAR and "1 Druckweg" in s["text"]


# ── automatisch gewählter Weg ─────────────────────────────────────────

def test_auto_waehlt_je_system_und_sagt_es(monkeypatch):
    monkeypatch.setattr(e, "_system", lambda: "linux")
    s = e.kamera_stand({"camera": {"backend": "auto"}})
    assert s["weg"] == "gphoto2" and s["automatisch"] is True
    monkeypatch.setattr(e, "_system", lambda: "windows")
    s = e.kamera_stand({"camera": {"backend": "auto"}})
    assert s["weg"] == "edsdk" and s["automatisch"] is True


def test_ein_unbekannter_weg_wird_nicht_beschoenigt():
    s = e.kamera_stand({"camera": {"backend": "irgendwas"}})
    assert s["stand"] == e.UNKLAR and "Unbekannt" in s["text"]


# ── das SDK aus dem ZIP ───────────────────────────────────────────────

def test_kein_zip_wird_klar_abgewiesen():
    from boothcore import edsdk_setup
    ok, zeilen = edsdk_setup.aus_zip(b"das ist kein archiv")
    assert not ok and "ZIP" in zeilen[0]


def test_ein_archiv_bricht_nicht_aus_dem_ordner_aus(tmp_path):
    """Ein Archiv darf nicht bestimmen, wohin auf dieser Platte geschrieben wird."""
    import io
    import zipfile
    from boothcore import edsdk_setup
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("../../entwischt.txt", "boese")
        z.writestr("/absolut.txt", "auch boese")
        z.writestr("harmlos/EDSDK.dll", "x")
    opfer = Path(tmp_path).parent / "entwischt.txt"
    ok, zeilen = edsdk_setup.aus_zip(puffer.getvalue(), target=tmp_path / "ziel")
    assert not opfer.exists(), "Das Archiv hat außerhalb geschrieben"
    assert any("verdächtig" in z for z in zeilen)
