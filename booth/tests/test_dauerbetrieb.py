"""Windows für den Dauerbetrieb — und vor allem: wieder zurück.

Der Kern dieser Tests ist nicht, dass eine Einstellung gesetzt wird. Das ist
ein Einzeiler. Der Kern ist, dass die Box hinterher weiß, was vorher dastand,
und dass sie nichts anfasst, was sie nicht selbst verstellt hat. Ein Rechner,
der nach dem Ausprobieren nie wieder schlafen geht, ist der eigentliche Schaden.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boothcore import dauerbetrieb as db                      # noqa: E402
from tests.fake_windows import FakeWindows                     # noqa: E402
from _pages import page  # noqa: E402

RUHE_PFAD = r"HKLM\SYSTEM\CurrentControlSet\Control\Power"
TOAST_PFAD = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\PushNotifications"
SCHONER_PFAD = r"HKCU\Control Panel\Desktop"


def bauen(tmp_path, *, admin=True) -> tuple:
    w = FakeWindows(admin=admin)
    # Ein frisch aufgesetztes Windows 11 am Netzteil: Standby nach 30 Minuten,
    # Bildschirm nach 10, USB-Energiesparen an.
    w.setze_power(db.SUB_SLEEP, db.STANDBYIDLE, 1800)
    w.setze_power(db.SUB_VIDEO, db.VIDEOIDLE, 600)
    w.setze_power(db.SUB_DISK, db.DISKIDLE, 1200)
    w.setze_power(db.SUB_USB, db.USBSELECTIVE, 1)
    w.setze_registry(SCHONER_PFAD, "ScreenSaveActive", "1", "REG_SZ")
    w.setze_registry(TOAST_PFAD, "ToastEnabled", "1")
    w.setze_registry(RUHE_PFAD, "HibernateEnabled", "1")
    d = db.Dauerbetrieb(tmp_path / "dauerbetrieb.json", run=w.run, windows=True)
    return d, w


# ── lesen ─────────────────────────────────────────────────────────────

def test_der_netzwert_wird_gelesen_nicht_der_akkuwert():
    """powercfg druckt beide; die Box hängt an der Steckdose."""
    w = FakeWindows()
    w.setze_power(db.SUB_SLEEP, db.STANDBYIDLE, netz=1800, akku=300)
    d = db.Dauerbetrieb(Path("/nirgendwo/zustand.json"), run=w.run, windows=True)
    assert d.lesen(db.NACH_KEY["standby"]) == "1800"


def test_registry_wird_in_beiden_typen_gelesen(tmp_path):
    d, _ = bauen(tmp_path)
    assert d.lesen(db.NACH_KEY["benachrichtigungen"]) == "1"   # REG_DWORD, kommt als 0x1
    assert d.lesen(db.NACH_KEY["bildschirmschoner"]) == "1"    # REG_SZ, kommt als "1"


def test_ein_fehlender_wert_ist_unbekannt_kein_fehler(tmp_path):
    d, w = bauen(tmp_path)
    w.registry.pop((TOAST_PFAD.lower(), "toastenabled"))
    eintrag = [e for e in d.status() if e["key"] == "benachrichtigungen"][0]
    assert eintrag["ist"] is None
    assert eintrag["zustand"] == "unbekannt"


def test_auf_allem_was_nicht_windows_ist_passiert_nichts(tmp_path):
    w = FakeWindows()
    d = db.Dauerbetrieb(tmp_path / "z.json", run=w.run, windows=False)
    assert all(e["zustand"] == "nicht-windows" for e in d.status())
    assert d.anwenden("standby")["ok"] is False
    assert w.aufrufe == [], "Kein Befehl darf abgesetzt worden sein"


# ── anwenden ──────────────────────────────────────────────────────────

def test_anwenden_setzt_und_merkt_sich_den_vorherigen_wert(tmp_path):
    d, w = bauen(tmp_path)
    assert d.anwenden("standby")["ok"] is True
    assert w.power[(db.SUB_SLEEP, db.STANDBYIDLE)][0] == 0
    assert d._gesichert()["standby"] == "1800"


def test_energieplan_wird_nach_dem_setzen_aktiviert(tmp_path):
    """Ohne /setactive steht der Wert im Plan und wirkt trotzdem nicht."""
    d, w = bauen(tmp_path)
    d.anwenden("bildschirm")
    assert w.aktiviert == 1


def test_zweimal_anwenden_ueberschreibt_das_gedaechtnis_nicht(tmp_path):
    d, _ = bauen(tmp_path)
    d.anwenden("standby")
    d.anwenden("standby")
    assert d._gesichert()["standby"] == "1800", \
        "Der zweite Aufruf hat unseren eigenen Zielwert gesichert"


def test_was_schon_richtig_steht_wird_nicht_gemerkt(tmp_path):
    d, w = bauen(tmp_path)
    w.setze_power(db.SUB_SLEEP, db.STANDBYIDLE, 0)
    antwort = d.anwenden("standby")
    assert antwort["ok"] is True and antwort["geaendert"] is False
    assert "standby" not in d._gesichert(), \
        "Sonst setzt „Zurücksetzen“ später auf den Zielwert zurück"


def test_ein_fehlschlag_hinterlaesst_keinen_gesicherten_wert(tmp_path):
    """Sonst gäbe es etwas zurückzunehmen, das nie geändert wurde."""
    d, _ = bauen(tmp_path, admin=False)
    antwort = d.anwenden("schnellstart")
    assert antwort["ok"] is False
    assert "Administratorrechte" in antwort["meldung"]
    assert d._gesichert() == {}


def test_neustart_wird_in_der_meldung_gesagt(tmp_path):
    d, _ = bauen(tmp_path)
    assert "Neustart" in str(d.anwenden("schnellstart")["meldung"])


def test_der_ruhezustand_wird_mit_powercfg_gesetzt_nicht_in_der_registry(tmp_path):
    """powercfg entfernt dabei die hiberfil.sys — ein reg add täte das nicht."""
    d, w = bauen(tmp_path)
    d.anwenden("ruhezustand")
    assert ["powercfg", "/hibernate", "off"] in w.aufrufe
    assert not any(a[:1] == ["reg"] and a[1:2] == ["add"] and "HibernateEnabled" in a
                   for a in w.aufrufe)


# ── zurücksetzen ──────────────────────────────────────────────────────

def test_zuruecksetzen_stellt_genau_den_alten_wert_her(tmp_path):
    d, w = bauen(tmp_path)
    d.anwenden("standby")
    assert d.zuruecksetzen("standby")["ok"] is True
    assert w.power[(db.SUB_SLEEP, db.STANDBYIDLE)][0] == 1800, \
        "Zurückgesetzt wurde auf einen geratenen Standard statt auf den Istwert"
    assert "standby" not in d._gesichert()


def test_ohne_gesicherten_wert_wird_nichts_angefasst(tmp_path):
    d, w = bauen(tmp_path)
    vorher = dict(w.power)
    antwort = d.zuruecksetzen("standby")
    assert antwort["ok"] is False
    assert "nicht verändert" in str(antwort["meldung"])
    assert w.power == vorher


def test_ein_wert_der_vorher_fehlte_wird_beim_zuruecksetzen_geloescht(tmp_path):
    """„Vorher stand da nichts“ ist ein Zustand, kein fehlender."""
    d, w = bauen(tmp_path)
    w.registry.pop((TOAST_PFAD.lower(), "toastenabled"))
    d._sichern("benachrichtigungen", None)
    w.setze_registry(TOAST_PFAD, "ToastEnabled", "0")
    assert d.zuruecksetzen("benachrichtigungen")["ok"] is True
    assert (TOAST_PFAD.lower(), "toastenabled") not in w.registry


def test_alles_zuruecksetzen_nimmt_nur_das_eigene_zurueck(tmp_path):
    d, w = bauen(tmp_path)
    d.anwenden("standby")
    d.anwenden("bildschirm")
    vorher_platte = w.power[(db.SUB_DISK, db.DISKIDLE)]
    ergebnisse = d.alles_zuruecksetzen()
    assert {e["key"] for e in ergebnisse} == {"standby", "bildschirm"}
    assert w.power[(db.SUB_SLEEP, db.STANDBYIDLE)][0] == 1800
    assert w.power[(db.SUB_VIDEO, db.VIDEOIDLE)][0] == 600
    assert w.power[(db.SUB_DISK, db.DISKIDLE)] == vorher_platte
    assert d._gesichert() == {}


def test_empfohlen_laesst_die_randgesten_in_ruhe(tmp_path):
    """Sie nehmen auch dem Betreiber die Wischgeste — das entscheidet er selbst."""
    d, _ = bauen(tmp_path)
    keys = {e["key"] for e in d.empfohlen_anwenden()}
    assert "randgesten" not in keys
    assert "standby" in keys


# ── Oberfläche ────────────────────────────────────────────────────────

def test_jede_massnahme_sagt_warum_es_sie_gibt(tmp_path):
    d, _ = bauen(tmp_path)
    for e in d.status():
        assert len(str(e["warum"])) > 30, f"{e['key']} erklärt sich nicht"
        assert str(e["titel"]).strip()


def test_adminrechte_und_neustart_stehen_im_status(tmp_path):
    d, _ = bauen(tmp_path)
    nach_key = {e["key"]: e for e in d.status()}
    assert nach_key["schnellstart"]["admin"] is True
    assert nach_key["schnellstart"]["neustart"] is True
    assert nach_key["standby"]["admin"] is False
    assert nach_key["standby"]["neustart"] is False


def test_der_status_unterscheidet_offen_und_optimiert(tmp_path):
    d, _ = bauen(tmp_path)
    assert [e for e in d.status() if e["key"] == "standby"][0]["zustand"] == "offen"
    d.anwenden("standby")
    assert [e for e in d.status() if e["key"] == "standby"][0]["zustand"] == "optimiert"


def test_gesichert_ist_im_status_sichtbar(tmp_path):
    """Die Oberfläche darf „Zurücksetzen“ nur anbieten, wenn es etwas gibt."""
    d, _ = bauen(tmp_path)
    assert [e for e in d.status() if e["key"] == "usb"][0]["gesichert"] is False
    d.anwenden("usb")
    assert [e for e in d.status() if e["key"] == "usb"][0]["gesichert"] is True


def test_unbekannte_einstellung_wird_abgewiesen(tmp_path):
    d, w = bauen(tmp_path)
    assert d.anwenden("mondphase")["ok"] is False
    assert d.zuruecksetzen("mondphase")["ok"] is False
    assert w.aufrufe == []


@pytest.mark.parametrize("m", db.MASSNAHMEN, ids=lambda m: m.key)
def test_jede_massnahme_ist_vollstaendig_beschrieben(m):
    assert m.art in ("power", "registry")
    if m.art == "power":
        assert m.sub and m.setting
    else:
        assert m.pfad and m.name
    assert m.ziel != ""


# ── Verwaltungsoberfläche ─────────────────────────────────────────────

def test_die_karte_bietet_beide_richtungen_an():
    """Anwenden ohne Zurücksetzen wäre genau der alte Fehler, nur mit Knopf."""
    admin = page(Path(__file__).resolve().parents[1] / "boothcore" / "web" / "admin.html")
    assert "dauerbetriebCard" in admin
    assert 'data-was="anwenden"' in admin and 'data-was="zuruecksetzen"' in admin
    assert "/api/admin/dauerbetrieb/alle/zuruecksetzen" in admin


def test_zuruecksetzen_ist_gesperrt_solange_nichts_gesichert_ist():
    admin = page(Path(__file__).resolve().parents[1] / "boothcore" / "web" / "admin.html")
    stelle = admin.index('data-was="zuruecksetzen"')
    assert "e.gesichert ?" in admin[stelle:stelle + 200], \
        "Der Knopf muss aus sein, wenn es nichts zurückzunehmen gibt"


def test_die_karte_bleibt_weg_wo_es_kein_windows_gibt():
    admin = page(Path(__file__).resolve().parents[1] / "boothcore" / "web" / "admin.html")
    assert 'id="dauerbetriebCard" hidden' in admin
    assert "hidden = !d.windows" in admin


def test_a_wedding_night_lies_inside_the_update_active_hours():
    """Windows 11 restarts for updates outside its active hours; office
    defaults (8-17 h, or adjusted automatically) put the evening outside."""
    werte = {m.key: m for m in db.MASSNAHMEN}
    assert werte["aktivzeit_automatik"].ziel == "0"
    beginn, ende = int(werte["aktivzeit_beginn"].ziel), int(werte["aktivzeit_ende"].ziel)
    spanne = (ende - beginn) % 24
    assert spanne <= 18, "Windows refuses active hours longer than 18 h"
    for stunde in (18, 21, 0, 2, 3):                       # the party
        assert (stunde - beginn) % 24 < spanne
    assert all(werte[k].empfohlen and werte[k].admin
               for k in ("aktivzeit_automatik", "aktivzeit_beginn", "aktivzeit_ende"))
