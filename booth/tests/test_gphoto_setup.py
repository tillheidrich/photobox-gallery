"""Kamera am Kabel unter Linux — die Hürde, an der fast jeder hängen bleibt.

Sony und Nikon laufen unter Linux über libgphoto2, und der Code dafür steht
seit langem in ``camera/gphoto2cam.py``. Was fehlte, war nicht der Treiber,
sondern die Einrichtung: Sobald eine Kamera am USB-Kabel steckt, hängt der
Schreibtisch (GNOME, KDE) sie über ``gvfs-gphoto2-volume-monitor`` als
Wechseldatenträger ein und hält das Gerät fest. Jeder Auslöseversuch endet
dann mit „Could not claim the USB device" — ohne dass irgendetwas an der Box
oder an der Kamera falsch wäre.

Getestet wird gegen einen nachgebauten Systemaufruf. Das ist kein Ersatz für
eine echte Kamera, und dieser Test behauptet auch nicht, dass Auslösen
funktioniert. Er hält nur fest, dass die Box die Lage richtig erkennt und
richtig benennt — und genau das ist der Teil, der ohne Hardware prüfbar ist.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boothcore import gphoto_setup as g                       # noqa: E402


class Lauf:
    """Ein nachgebautes ``subprocess.run``: sagt, was es sagen soll."""

    def __init__(self, kameras=(), dienste=(), gphoto_da=True):
        self.kameras, self.dienste, self.gphoto_da = list(kameras), list(dienste), gphoto_da
        self.beendet = []

    def __call__(self, befehl, timeout=8.0):
        prog = befehl[0]
        if prog == "pgrep":
            treffer = befehl[-1] in self.dienste
            return subprocess.CompletedProcess(befehl, 0 if treffer else 1,
                                               "4711\n" if treffer else "", "")
        if prog == "pkill":
            name = befehl[-1]
            self.beendet.append(name)
            if name in self.dienste:
                self.dienste.remove(name)
            return subprocess.CompletedProcess(befehl, 0, "", "")
        if prog == "gphoto2":
            if not self.gphoto_da:
                raise FileNotFoundError("gphoto2")
            kopf = "Model                          Port\n" + "-" * 40 + "\n"
            zeilen = "".join(f"{k}                    usb:001,0{i}\n"
                             for i, k in enumerate(self.kameras, 2))
            return subprocess.CompletedProcess(befehl, 0, kopf + zeilen, "")
        return subprocess.CompletedProcess(befehl, 1, "", "")


# ── Erkennen ──────────────────────────────────────────────────────────

def test_eine_sony_wird_erkannt(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/gphoto2")
    b = g.pruefen(lauf=Lauf(kameras=["Sony Alpha-A6400 (Control)"]), system="Linux")
    assert b.kameras == ["Sony Alpha-A6400 (Control)"]


def test_eine_nikon_wird_erkannt(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/gphoto2")
    b = g.pruefen(lauf=Lauf(kameras=["Nikon DSC Z6_2 (PTP mode)"]), system="Linux")
    assert b.kameras == ["Nikon DSC Z6_2 (PTP mode)"]


def test_mehrere_kameras_werden_alle_genannt(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/gphoto2")
    b = g.pruefen(lauf=Lauf(kameras=["Sony Alpha-A7 III", "Canon EOS R50"]), system="Linux")
    assert len(b.kameras) == 2


def test_ohne_kamera_sagt_es_das_und_raet_richtig(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/gphoto2")
    b = g.pruefen(lauf=Lauf(kameras=[]), system="Linux")
    assert not b.bereit
    assert "USB-Modus" in b.zusammenfassung()


# ── die eigentliche Hürde ─────────────────────────────────────────────

def test_der_schreibtisch_der_die_kamera_festhaelt_wird_benannt(monkeypatch):
    """Der Kern: Kamera da, trotzdem geht nichts — und die Box sagt warum."""
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/gphoto2")
    b = g.pruefen(lauf=Lauf(kameras=["Sony Alpha-A6400 (Control)"],
                            dienste=["gvfs-gphoto2-volume-monitor"]), system="Linux")
    assert b.kameras and not b.bereit, "Kamera erkannt, aber blockiert — nicht bereit"
    assert "gvfs-gphoto2-volume-monitor" in b.blockierer
    text = b.zusammenfassung()
    assert "hält die Kamera" in text and "Sony" in text
    assert "gvfs-gphoto2-volume-monitor" in text


def test_beide_bekannten_dienste_werden_gesehen(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/gphoto2")
    b = g.pruefen(lauf=Lauf(kameras=["Nikon DSC Z6_2"],
                            dienste=list(g.STOERENFRIEDE)), system="Linux")
    assert len(b.blockierer) == 2


def test_ohne_blockierer_ist_die_box_bereit(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/gphoto2")
    monkeypatch.setattr(g, "pruefen", g.pruefen)
    b = g.pruefen(lauf=Lauf(kameras=["Canon EOS R50"]), system="Linux")
    b.python_gphoto2 = True                              # Anbindung als vorhanden annehmen
    assert b.bereit and "Bereit" in b.zusammenfassung()


# ── freigeben ─────────────────────────────────────────────────────────

def test_freigeben_beendet_genau_die_stoerenden_dienste():
    lauf = Lauf(dienste=["gvfs-gphoto2-volume-monitor"])
    ergebnis = g.freigeben(lauf=lauf)
    assert ergebnis["ok"] and ergebnis["beendet"] == ["gvfs-gphoto2-volume-monitor"]
    assert lauf.beendet == ["gvfs-gphoto2-volume-monitor"]


def test_freigeben_fasst_nichts_an_wenn_nichts_stoert():
    lauf = Lauf(dienste=[])
    ergebnis = g.freigeben(lauf=lauf)
    assert ergebnis["ok"] and ergebnis["beendet"] == []
    assert lauf.beendet == [], "Es wurde etwas beendet, das gar nicht im Weg war"


def test_freigeben_sagt_dass_es_nicht_von_dauer_ist():
    """Ehrlich bleiben: beim nächsten Anstecken ist der Dienst wieder da."""
    ergebnis = g.freigeben(lauf=Lauf(dienste=["gvfsd-gphoto2"]))
    assert "wieder" in ergebnis["hinweis"]
    assert "ohne Schreibtischoberfläche" in ergebnis["hinweis"]


def test_es_wird_nichts_ungefragt_beendet(monkeypatch):
    """pruefen() stellt fest und verändert nichts — freigeben() greift ein."""
    lauf = Lauf(kameras=["Sony Alpha-A6400"], dienste=["gvfsd-gphoto2"])
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/gphoto2")
    g.pruefen(lauf=lauf, system="Linux")
    assert lauf.beendet == [], "pruefen() hat einen Dienst beendet"
    assert lauf.dienste == ["gvfsd-gphoto2"]


# ── andere Systeme, fehlende Teile ────────────────────────────────────

def test_unter_windows_weist_es_auf_den_anderen_weg(monkeypatch):
    b = g.pruefen(lauf=Lauf(), system="Windows")
    assert not b.linux
    assert "digiCamControl" in b.zusammenfassung()


def test_fehlendes_gphoto2_programm_bricht_nicht(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: None)
    b = g.pruefen(lauf=Lauf(gphoto_da=False), system="Linux")
    assert b.kameras == []
    assert any("gphoto2" in h for h in b.hinweise)


def test_ein_absturz_des_systemaufrufs_wird_geschluckt(monkeypatch):
    def kracht(befehl, timeout=8.0):
        raise OSError("kein pgrep")
    monkeypatch.setattr(g.shutil, "which", lambda n: "/usr/bin/gphoto2")
    b = g.pruefen(lauf=kracht, system="Linux")           # darf nicht werfen
    assert b.blockierer == []


# ── was in der Kamera eingestellt sein muss ───────────────────────────

@pytest.mark.parametrize("marke,stichwort", [
    ("sony", "PC-Fernsteuerung"),
    ("nikon", "MTP/PTP"),
    ("fujifilm", "Tether"),
    ("panasonic", "PC(Tether)"),
])
def test_je_marke_steht_der_entscheidende_handgriff_da(marke, stichwort):
    assert stichwort in g.kamera_hinweis(marke)


def test_eine_unbekannte_marke_erfindet_nichts():
    assert g.kamera_hinweis("leica") == ""
    assert g.kamera_hinweis("") == ""


def test_sony_und_nikon_haben_beide_einen_hinweis():
    """Die beiden, nach denen gefragt wurde — sie dürfen nicht leer sein."""
    for marke in ("sony", "nikon"):
        assert len(g.kamera_hinweis(marke)) > 40


# ── Nachtrag: ein Test, den eine Mutation entlarvt hat ────────────────
# Die Prüfung „Kamera da, aber blockiert → nicht bereit" ging in diesem
# Container aus dem falschen Grund durch: Ohne installiertes python-gphoto2
# ist `bereit` ohnehin False, egal was die Blockierer machen. Eine Mutation,
# die den Blockierer schlicht ignorierte, blieb deshalb unbemerkt. Hier wird
# `bereit` isoliert geprüft — mit vorhandener Anbindung.

def _befund(*, kameras, blockierer):
    b = g.Befund(linux=True, gphoto2_programm=True, python_gphoto2=True,
                 kameras=list(kameras), blockierer=list(blockierer))
    return b


def test_blockiert_heisst_nicht_bereit_auch_mit_anbindung():
    b = _befund(kameras=["Sony Alpha-A6400"], blockierer=["gvfs-gphoto2-volume-monitor"])
    assert not b.bereit, "Ein festgehaltenes Gerät ist nicht einsatzbereit"


def test_ohne_blockierer_mit_anbindung_ist_bereit():
    assert _befund(kameras=["Nikon DSC Z6_2"], blockierer=[]).bereit


def test_ohne_kamera_nie_bereit():
    assert not _befund(kameras=[], blockierer=[]).bereit


def test_ohne_anbindung_nie_bereit():
    b = _befund(kameras=["Canon EOS R50"], blockierer=[])
    b.python_gphoto2 = False
    assert not b.bereit
