"""Alte Aufnahmen von der Platte nehmen — und vor allem: wann nicht.

Ein Löschwerkzeug wird nicht daran gemessen, dass es löscht. Es wird daran
gemessen, dass es in jedem Zweifelsfall die Finger stillhält. Deshalb prüfen
die meisten Tests hier, dass nichts passiert: keine Bestätigung, kein Ziel,
Warteschlange nicht leer, Pfad außerhalb des Sitzungsordners.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sync"))

from boothcore.aufraeumen import Aufraeumer                  # noqa: E402
from boothcore.library import Library                        # noqa: E402

TAG = 86400.0


class Warteschlange:
    """Nur das, was der Aufräumer von der Warteschlange braucht."""

    def __init__(self, stand=None):
        self.stand = stand or {}

    def offen_fuer_session(self, uid):
        return self.stand.get(uid, {"offen": 0, "erledigt": 0})


class OhneAuskunft:
    """Eine ältere Warteschlange, die die Frage nicht kennt."""


def bauen(tmp_path, *, stand=None, ziele=True, uhr=None, **cfg):
    grund = {"enabled": True, "nach_tagen": 14, "nur_bestaetigte": True,
             "behalten_mindestens": 0}
    grund.update(cfg)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    lib = Library(tmp_path / "lib.sqlite")
    jetzt = uhr or {"t": time.time()}
    a = Aufraeumer(sessions_dir=sessions, library=lib, outbox=Warteschlange(stand),
                   config=lambda: grund, hat_ziele=lambda: ziele,
                   clock=lambda: jetzt["t"])
    return a, lib, sessions, jetzt


def durchgang(lib, sessions, uid, *, alter_tage=30.0, jetzt=None):
    ordner = sessions / "event" / "2026-09-01" / uid
    ordner.mkdir(parents=True)
    (ordner / "result.jpg").write_bytes(b"x" * 1000)
    (ordner / "thumb.jpg").write_bytes(b"x" * 100)
    lib.upsert(uid=uid, event_slug="event", mode="photo", result=ordner / "result.jpg",
               thumb=ordner / "thumb.jpg", print_file=None, share_url="", look="original")
    if jetzt is not None:
        with lib._con() as con:
            con.execute("UPDATE items SET created=? WHERE uid=?",
                        (jetzt["t"] - alter_tage * TAG, uid))
            con.commit()
    return ordner


# ── es wird gelöscht, wenn alles stimmt ───────────────────────────────

def test_bestaetigte_alte_aufnahmen_verschwinden(tmp_path):
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"alt1": {"offen": 0, "erledigt": 3}})
    ordner = durchgang(lib, sessions, "alt1", alter_tage=30, jetzt=jetzt)
    bericht = a.lauf()
    assert bericht["geloescht"] == 1
    assert not ordner.exists()
    assert bericht["bytes"] > 1000


def test_der_eintrag_bleibt_die_dateien_gehen(tmp_path):
    """Die Box soll wissen, dass es den Durchgang gab — Druckzähler, Statistik."""
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"alt1": {"offen": 0, "erledigt": 1}})
    durchgang(lib, sessions, "alt1", alter_tage=30, jetzt=jetzt)
    a.lauf()
    with lib._con() as con:
        zeile = con.execute("SELECT * FROM items WHERE uid='alt1'").fetchone()
    assert zeile is not None and zeile["aufgeraeumt"] is not None
    assert zeile["deleted"] is None, "Aufräumen ist kein Löschauftrag an die Galerie"


def test_aufgeraeumtes_taucht_in_der_galerie_der_box_nicht_mehr_auf(tmp_path):
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"alt1": {"offen": 0, "erledigt": 1}})
    durchgang(lib, sessions, "alt1", alter_tage=30, jetzt=jetzt)
    assert lib.count("event") == 1
    a.lauf()
    assert lib.count("event") == 0 and lib.list("event") == []


# ── und alle Gründe, es nicht zu tun ──────────────────────────────────

def test_ohne_bestaetigung_bleibt_alles_liegen(tmp_path):
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"alt1": {"offen": 2, "erledigt": 0}})
    ordner = durchgang(lib, sessions, "alt1", alter_tage=30, jetzt=jetzt)
    bericht = a.lauf()
    assert bericht["geloescht"] == 0 and bericht["behalten"] == 1
    assert ordner.exists()


def test_ein_fehlgeschlagener_upload_ist_kein_angekommener(tmp_path):
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"alt1": {"offen": 1, "erledigt": 4}})
    ordner = durchgang(lib, sessions, "alt1", alter_tage=30, jetzt=jetzt)
    a.lauf()
    assert ordner.exists()


def test_ohne_auskunft_wird_nichts_geloescht(tmp_path):
    """Die Warteschlange räumt Erledigtes nach dreißig Tagen weg. Dann: behalten."""
    a, lib, sessions, jetzt = bauen(tmp_path, stand={})
    ordner = durchgang(lib, sessions, "alt1", alter_tage=60, jetzt=jetzt)
    bericht = a.lauf()
    assert bericht["geloescht"] == 0
    assert ordner.exists()


def test_eine_alte_warteschlange_ohne_die_frage_bedeutet_behalten(tmp_path):
    a, lib, sessions, jetzt = bauen(tmp_path)
    a.outbox = OhneAuskunft()
    ordner = durchgang(lib, sessions, "alt1", alter_tage=60, jetzt=jetzt)
    assert a.lauf()["geloescht"] == 0
    assert ordner.exists()


def test_ohne_ziel_wird_nichts_geloescht(tmp_path):
    """Ohne zweite Kopie ist diese Platte das Original."""
    a, lib, sessions, jetzt = bauen(tmp_path, ziele=False,
                                    stand={"alt1": {"offen": 0, "erledigt": 3}})
    ordner = durchgang(lib, sessions, "alt1", alter_tage=60, jetzt=jetzt)
    bericht = a.lauf()
    assert bericht["geloescht"] == 0
    assert "Kein Ziel" in str(bericht["grund"])
    assert ordner.exists()


def test_junge_aufnahmen_bleiben(tmp_path):
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"neu": {"offen": 0, "erledigt": 3}})
    ordner = durchgang(lib, sessions, "neu", alter_tage=2, jetzt=jetzt)
    assert a.lauf()["geprueft"] == 0
    assert ordner.exists()


def test_abgeschaltet_passiert_nichts(tmp_path):
    a, lib, sessions, jetzt = bauen(tmp_path, enabled=False,
                                    stand={"alt1": {"offen": 0, "erledigt": 3}})
    ordner = durchgang(lib, sessions, "alt1", alter_tage=60, jetzt=jetzt)
    assert a.lauf()["geloescht"] == 0
    assert ordner.exists()


def test_die_juengsten_bleiben_immer_liegen(tmp_path):
    """Am Morgen danach will der Betreiber noch nachdrucken können."""
    stand = {f"u{i}": {"offen": 0, "erledigt": 1} for i in range(5)}
    a, lib, sessions, jetzt = bauen(tmp_path, stand=stand, behalten_mindestens=2)
    ordner = []
    for i in range(5):
        ordner.append(durchgang(lib, sessions, f"u{i}", alter_tage=30 - i, jetzt=jetzt))
    bericht = a.lauf()
    assert bericht["geloescht"] == 3
    assert not ordner[0].exists() and ordner[3].exists() and ordner[4].exists()


def test_nichts_ausserhalb_des_sitzungsordners_wird_angefasst(tmp_path):
    """Ein Pfad aus der Datenbank ist kein Freibrief."""
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"fremd": {"offen": 0, "erledigt": 1}})
    fremd = tmp_path / "wichtig" / "fremd"
    fremd.mkdir(parents=True)
    (fremd / "result.jpg").write_bytes(b"nicht anfassen")
    lib.upsert(uid="fremd", event_slug="event", mode="photo", result=fremd / "result.jpg",
               thumb=None, print_file=None, share_url="", look="original")
    with lib._con() as con:
        con.execute("UPDATE items SET created=? WHERE uid='fremd'", (jetzt["t"] - 60 * TAG,))
        con.commit()
    a.lauf()
    assert (fremd / "result.jpg").exists(), "Der Aufräumer ist aus seinem Ordner ausgebrochen"


# ── knappe Platte ─────────────────────────────────────────────────────

def test_bei_voller_platte_wird_die_frist_vorgezogen(tmp_path):
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"neu": {"offen": 0, "erledigt": 1}})
    ordner = durchgang(lib, sessions, "neu", alter_tage=1, jetzt=jetzt)
    assert a.lauf(frei_gb=30.0)["geloescht"] == 0 and ordner.exists()
    bericht = a.lauf(frei_gb=0.5)
    assert bericht["geloescht"] == 1 and not ordner.exists()
    assert "knapp" in str(bericht["grund"])


def test_auch_bei_voller_platte_nur_bestaetigtes(tmp_path):
    """Die Frist wird unterschritten, die Bestätigung nie."""
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"neu": {"offen": 1, "erledigt": 0}})
    ordner = durchgang(lib, sessions, "neu", alter_tage=1, jetzt=jetzt)
    assert a.lauf(frei_gb=0.2)["geloescht"] == 0
    assert ordner.exists()


# ── Gedächtnis ────────────────────────────────────────────────────────

def test_die_zustellung_wird_notiert_sobald_sie_gesehen_wurde(tmp_path):
    """Sonst weiß nach dem Ausräumen der Warteschlange niemand mehr Bescheid."""
    a, lib, sessions, jetzt = bauen(tmp_path, nach_tagen=999,
                                    stand={"alt1": {"offen": 0, "erledigt": 2}})
    durchgang(lib, sessions, "alt1", alter_tage=30, jetzt=jetzt)
    a.lauf()                                     # zu jung zum Löschen, aber geprüft?
    # Frist absichtlich hoch: es wird nichts gelöscht. Jetzt direkt fragen.
    assert a._zugestellt("alt1", lib.get("alt1")) is True
    assert lib.get("alt1")["geliefert"]
    # Und jetzt vergisst die Warteschlange alles — die Notiz trägt trotzdem.
    a.outbox = Warteschlange({})
    assert a._zugestellt("alt1", lib.get("alt1")) is True


def test_ein_zweiter_lauf_prueft_nicht_alles_noch_einmal(tmp_path):
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"alt1": {"offen": 0, "erledigt": 1}})
    durchgang(lib, sessions, "alt1", alter_tage=30, jetzt=jetzt)
    assert a.lauf()["geloescht"] == 1
    assert a.lauf()["geprueft"] == 0


def test_verschwundene_dateien_werden_abgehakt(tmp_path):
    """Sonst prüft die Box denselben Eintrag bis in alle Ewigkeit."""
    a, lib, sessions, jetzt = bauen(tmp_path, stand={"alt1": {"offen": 0, "erledigt": 1}})
    ordner = durchgang(lib, sessions, "alt1", alter_tage=30, jetzt=jetzt)
    import shutil
    shutil.rmtree(ordner)
    assert a.lauf()["geloescht"] == 1
    assert a.lauf()["geprueft"] == 0


# ── Profil ────────────────────────────────────────────────────────────

def test_der_block_steht_in_beiden_profilfassungen():
    wurzel = Path(__file__).resolve().parents[2]
    for datei in (wurzel / "booth" / "boothcore" / "config.py", wurzel / "app" / "ecosystem.py"):
        text = datei.read_text(encoding="utf-8")
        assert '"aufraeumen": {' in text
        block = text[text.index('"aufraeumen": {'):]
        assert '"enabled": False' in block[:400], "Aufräumen darf nicht voreingestellt an sein"
        assert '"nur_bestaetigte": True' in block[:700]
