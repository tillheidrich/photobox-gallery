"""Was die Box meldet — an den Betreiber, nie an den Gast.

Geprüft wird vor allem das, was man beim ersten Bauen falsch macht: dieselbe
Meldung im Fünf-Sekunden-Takt verschicken, nach dem Papierwechsel weiter
„kein Papier" anzeigen, und die Warnung dem Gast über das Gesicht legen.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boothcore import meldungen as M                          # noqa: E402
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


class Postfach:
    def __init__(self, kaputt: bool = False):
        self.raus = []
        self.kaputt = kaputt

    def enqueue(self, *, address, files, subject, body, session_uid=""):
        if self.kaputt:
            raise RuntimeError("SMTP kaputt")
        self.raus.append({"an": address, "betreff": subject, "text": body})
        return len(self.raus)


def bauen(**cfg):
    grund = {"mail": {"enabled": True, "to": "wirt@example.de", "ab_stufe": "warnung"},
             "box": {"enabled": True, "ab_stufe": "kritisch"},
             "wiederholen_minuten": 60}
    for k, v in cfg.items():
        if isinstance(v, dict) and isinstance(grund.get(k), dict):
            grund[k] = {**grund[k], **v}
        else:
            grund[k] = v
    post = Postfach()
    uhr = {"t": 1000.0}
    m = M.Meldungen(lambda: grund, mail_enqueue=post.enqueue, clock=lambda: uhr["t"])
    return m, post, uhr


# ── Stufen und Schwellen ──────────────────────────────────────────────

def test_ein_hinweis_erreicht_die_schwelle_nicht():
    m, post, _ = bauen()
    m.melden("x", M.INFO, "Nur ein Hinweis")
    assert post.raus == []


def test_warnung_und_kritisch_gehen_hinaus():
    m, post, _ = bauen()
    m.melden("a", M.WARNUNG, "Papier wird knapp")
    m.melden("b", M.KRITISCH, "Kamera weg")
    assert [x["betreff"] for x in post.raus] == [
        "[Fotobox · Bitte ansehen] Papier wird knapp",
        "[Fotobox · Die Box steht] Kamera weg"]


def test_die_schwelle_laesst_sich_hochziehen():
    m, post, _ = bauen(mail={"ab_stufe": "kritisch"})
    m.melden("a", M.WARNUNG, "Papier wird knapp")
    assert post.raus == []
    m.melden("b", M.KRITISCH, "Kamera weg")
    assert len(post.raus) == 1


def test_ohne_adresse_wird_nichts_verschickt():
    m, post, _ = bauen(mail={"to": ""})
    m.melden("a", M.KRITISCH, "Kamera weg")
    assert post.raus == []


def test_ein_kaputter_mailweg_bringt_die_box_nicht_um():
    post = Postfach(kaputt=True)
    m = M.Meldungen(lambda: {"mail": {"enabled": True, "to": "a@b.de", "ab_stufe": "info"}},
                    mail_enqueue=post.enqueue)
    antwort = m.melden("a", M.KRITISCH, "Kamera weg")
    assert antwort["gemailt"] is False
    assert m.offen()[0]["titel"] == "Kamera weg"


# ── Dämpfung ──────────────────────────────────────────────────────────

def test_dieselbe_ursache_geht_nicht_alle_fuenf_sekunden_hinaus():
    """Die Kamera meldet ihren Verlust im Takt. Der Betreiber braucht das einmal."""
    m, post, uhr = bauen()
    for _ in range(20):
        m.melden("kamera", M.KRITISCH, "Kamera weg")
        uhr["t"] += 5
    assert len(post.raus) == 1


def test_nach_der_wartezeit_erinnert_sie_wieder():
    m, post, uhr = bauen(wiederholen_minuten=30)
    m.melden("kamera", M.KRITISCH, "Kamera weg")
    uhr["t"] += 29 * 60
    m.melden("kamera", M.KRITISCH, "Kamera weg")
    assert len(post.raus) == 1
    uhr["t"] += 2 * 60
    m.melden("kamera", M.KRITISCH, "Kamera weg")
    assert len(post.raus) == 2


def test_verschiedene_ursachen_daempfen_sich_nicht_gegenseitig():
    m, post, _ = bauen()
    m.melden("kamera", M.KRITISCH, "Kamera weg")
    m.melden("drucker", M.KRITISCH, "Drucker weg")
    assert len(post.raus) == 2


def test_nach_der_entwarnung_meldet_dieselbe_ursache_sofort_wieder():
    """Sonst schweigt die Box eine Stunde, obwohl es zweimal passiert ist."""
    m, post, uhr = bauen()
    m.melden("kamera", M.KRITISCH, "Kamera weg")
    m.entwarnung("kamera")
    uhr["t"] += 10
    m.melden("kamera", M.KRITISCH, "Kamera weg")
    assert len(post.raus) == 2


# ── Was anliegt ───────────────────────────────────────────────────────

def test_entwarnung_raeumt_den_offenen_punkt_weg():
    """Eine Box, die nach dem Papierwechsel weiter warnt, wird ignoriert."""
    m, _, _ = bauen()
    m.melden("blatt", M.WARNUNG, "Noch 20 Blatt")
    assert [x["key"] for x in m.offen()] == ["blatt"]
    m.entwarnung("blatt")
    assert m.offen() == []
    assert any(x["key"] == "blatt" for x in m.verlauf()), "Der Verlauf soll es behalten"


def test_hinweise_stehen_nicht_als_offener_punkt_herum():
    m, _, _ = bauen()
    m.melden("x", M.INFO, "Sitzung gestartet")
    assert m.offen() == []
    assert m.schlimmste() == "ok"


def test_die_box_zeigt_nur_die_dringendste():
    m, _, _ = bauen(box={"ab_stufe": "warnung"})
    m.melden("blatt", M.WARNUNG, "Noch 20 Blatt")
    m.melden("kamera", M.KRITISCH, "Kamera weg")
    m.melden("platte", M.WARNUNG, "Platz knapp")
    assert m.fuer_die_box()["titel"] == "Kamera weg"


def test_unter_der_schwelle_zeigt_die_box_nichts():
    m, _, _ = bauen(box={"ab_stufe": "kritisch"})
    m.melden("blatt", M.WARNUNG, "Noch 20 Blatt")
    assert m.fuer_die_box() is None


def test_die_anzeige_laesst_sich_ganz_abschalten():
    m, _, _ = bauen(box={"enabled": False})
    m.melden("kamera", M.KRITISCH, "Kamera weg")
    assert m.fuer_die_box() is None


def test_der_verlauf_laeuft_nicht_ueber():
    m = M.Meldungen(lambda: {}, groesse=10)
    for i in range(50):
        m.melden(f"k{i}", M.INFO, f"Nummer {i}")
    assert len(m.verlauf(100)) == 10


# ── Probe ─────────────────────────────────────────────────────────────

def test_die_probe_schickt_eine_meldung_je_stufe():
    m, post, _ = bauen(mail={"ab_stufe": "info"})
    ergebnis = m.probe()
    assert len(post.raus) == 3
    assert "3 von 3" in ergebnis["hinweis"]


def test_die_probe_laesst_sich_nicht_von_der_daempfung_aufhalten():
    """Eine Probe, die wegen einer Sperre nicht losgeht, prüft nichts."""
    m, post, _ = bauen(mail={"ab_stufe": "info"})
    m.probe()
    m.probe()
    assert len(post.raus) == 6


def test_die_probe_hinterlaesst_keine_offenen_punkte():
    m, _, _ = bauen(mail={"ab_stufe": "info"})
    m.probe()
    assert m.offen() == []


def test_die_probe_sagt_es_wenn_mail_aus_ist():
    m, post, _ = bauen(mail={"enabled": False})
    assert "aus" in m.probe()["hinweis"]
    assert post.raus == []


def test_die_probe_sagt_es_wenn_die_adresse_fehlt():
    m, _, _ = bauen(mail={"to": ""})
    assert "Empfängeradresse" in m.probe()["hinweis"]


# ── aus der Selbstdiagnose ────────────────────────────────────────────

class Pruefung:
    def __init__(self, name, state, message="", hint=""):
        self._d = {"name": name, "state": state, "message": message, "hint": hint}

    def as_dict(self):
        return dict(self._d)


def test_die_selbstdiagnose_wird_uebersetzt():
    m, post, _ = bauen()
    M.aus_diagnose(m, [Pruefung("Kamera", "fail", "Keine Kamera.", "Kabel prüfen."),
                       Pruefung("Speicherplatz", "warn", "Wird knapp."),
                       Pruefung("Drucker", "ok", "Bereit."),
                       Pruefung("E-Mail", "skip", "Nicht eingerichtet.")])
    offen = {x["titel"]: x["stufe"] for x in m.offen()}
    assert offen == {"Kamera": M.KRITISCH, "Speicherplatz": M.WARNUNG}
    assert len(post.raus) == 2


def test_was_wieder_in_ordnung_ist_verschwindet():
    m, _, _ = bauen()
    M.aus_diagnose(m, [Pruefung("Kamera", "fail", "Keine Kamera.")])
    assert m.offen()
    M.aus_diagnose(m, [Pruefung("Kamera", "ok", "Bereit.")])
    assert m.offen() == []


# ── Oberfläche ────────────────────────────────────────────────────────

def test_die_meldung_erscheint_nur_auf_dem_startbildschirm():
    """Über dem Gesicht eines Gastes steht sie auf jedem Handyfoto."""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    stelle = js.index("betriebsmeldung")
    block = js[stelle - 400:stelle + 400]
    assert 'app.dataset.screen === "start"' in block, \
        "Die Meldung ist nicht auf den Startbildschirm beschränkt"


def test_die_meldung_benutzt_kein_rot():
    """Rot ist der Aufnahme und dem Löschen vorbehalten."""
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    start = css.index(".betriebsmeldung{")
    block = css[start:css.index("}", css.index(".betriebsmeldung b{")) + 1]
    assert "--rot" not in block and "#f" not in block.lower().replace("#ffffff", "")


def test_die_verwaltung_hat_eine_probemeldung():
    admin = page(WEB / "admin.html")
    assert 'data-act="test_meldung"' in admin
    assert 'id="meldungenCard"' in admin
