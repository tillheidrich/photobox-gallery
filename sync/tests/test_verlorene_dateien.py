"""Zwei Wege, auf denen eine Aufnahme verschwand.

Beide gefunden bei der Durchsicht am 19.09.2026. Der erste schickte ein halbes
Bild hinaus, der zweite hakte eine Datei ab, die nie ein Ziel gesehen hatte.
Eine verlorene Aufnahme merkt niemand — bis der Gast am nächsten Tag fragt.
"""
from __future__ import annotations

import os
import time
import pathlib
from pathlib import Path

from photobox_sync.watcher import Watcher


def _uhr_zurueck(datei: Path, sekunden: float) -> None:
    """Der Datei eine alte Änderungszeit geben — wie robocopy oder eine SD-Karte."""
    alt = time.time() - sekunden
    os.utime(datei, (alt, alt))


def test_a_file_that_is_still_growing_does_not_go_out_because_it_looks_old(tmp_path):
    """Alte Änderungszeit plus wachsende Datei: früher ging sie halb hinaus."""
    ordner = tmp_path / "booth"
    ordner.mkdir()
    gesehen = []
    w = Watcher(tmp_path / "w.sqlite", folders=lambda: [{"path": str(ordner), "since": 0}],
                on_file=lambda p, u, k, f=None: gesehen.append((p.name, p.stat().st_size)),
                stable_seconds=0.2, interval=0.05)

    datei = ordner / "halb.jpg"
    datei.write_bytes(b"x" * 100)          # erst 100 von 1000 Bytes
    _uhr_zurueck(datei, 60)

    w.scan_once()
    assert gesehen == [], f"Halb geschriebene Datei übernommen: {gesehen}"

    datei.write_bytes(b"x" * 1000)         # fertig geschrieben
    _uhr_zurueck(datei, 60)
    w.scan_once()                          # erster Blick auf die fertige Datei
    time.sleep(0.25)
    w.scan_once()                          # zweiter Blick: unverändert, also raus
    assert [g[1] for g in gesehen] == [1000], f"erwartet genau die fertige Datei, bekommen: {gesehen}"


def test_switching_the_stability_check_off_still_works(tmp_path):
    """Die Gegenprobe: ``stable_seconds: 0`` heißt weiterhin „sofort"."""
    ordner = tmp_path / "booth"
    ordner.mkdir()
    gesehen = []
    w = Watcher(tmp_path / "w.sqlite", folders=lambda: [{"path": str(ordner), "since": 0}],
                on_file=lambda p, u, k, f=None: gesehen.append(p.name),
                stable_seconds=0, interval=0.05)
    (ordner / "sofort.jpg").write_bytes(b"x" * 500)
    w.scan_once()
    assert gesehen == ["sofort.jpg"]


def test_a_file_without_a_target_is_not_ticked_off_for_good(tmp_path):
    """Noch nicht gekoppelt — die Datei muss nach dem Koppeln noch da sein."""
    ordner = tmp_path / "booth"
    ordner.mkdir()
    ziele = []
    angenommen = []

    def on_file(p, uid, kind, folder=None):
        if not ziele:
            return False                   # kein Ziel: nicht abhaken
        angenommen.append(p.name)
        return True

    w = Watcher(tmp_path / "w.sqlite", folders=lambda: [{"path": str(ordner), "since": 0}],
                on_file=on_file, stable_seconds=0, interval=0.05)
    (ordner / "IMG_1.jpg").write_bytes(b"x" * 500)

    assert w.scan_once() == 0 and angenommen == []
    ziele.append("gallery")                # jetzt gekoppelt
    assert w.scan_once() == 1
    assert angenommen == ["IMG_1.jpg"], "Die Datei war nach dem Koppeln übersprungen"


def test_a_file_that_was_taken_is_not_taken_twice(tmp_path):
    """Die Gegenprobe zur vorigen: Abgehaktes bleibt abgehakt."""
    ordner = tmp_path / "booth"
    ordner.mkdir()
    angenommen = []
    w = Watcher(tmp_path / "w.sqlite", folders=lambda: [{"path": str(ordner), "since": 0}],
                on_file=lambda p, u, k, f=None: angenommen.append(p.name),
                stable_seconds=0, interval=0.05)
    (ordner / "IMG_1.jpg").write_bytes(b"x" * 500)
    w.scan_once()
    w.scan_once()
    assert angenommen == ["IMG_1.jpg"]


# ── Weitere Funde derselben Durchsicht ────────────────────────────────

def test_the_same_target_never_runs_twice_at_once(tmp_path, monkeypatch):
    """``join(timeout=300)`` gab die Runde auf, ohne den Thread zu stoppen.

    Danach stand der Eintrag weiter auf „pending", die nächste Runde nahm ihn
    erneut, und für dieselbe Datei liefen zwei Uploads nebeneinander.
    """
    import threading
    import time as _t

    from photobox_sync.agent import Agent

    agent = object.__new__(Agent)
    agent._laufend = set()
    agent._laufend_lock = threading.Lock()

    gleichzeitig, hoechstens = [], []
    tor = threading.Event()

    def arbeite(name):
        with agent._laufend_lock:
            if name in agent._laufend:
                return False
            agent._laufend.add(name)

        def lauf():
            gleichzeitig.append(1)
            hoechstens.append(len(gleichzeitig))
            tor.wait(5)
            gleichzeitig.pop()
            with agent._laufend_lock:
                agent._laufend.discard(name)

        threading.Thread(target=lauf, daemon=True).start()
        return True

    assert arbeite("gallery") is True
    _t.sleep(0.1)
    assert arbeite("gallery") is False, "Zweiter Thread für dasselbe Ziel"
    tor.set()
    _t.sleep(0.2)
    assert max(hoechstens) == 1


def test_ftp_writes_under_a_temporary_name_first(tmp_path):
    """Sonst steht ein halbes Bild unter dem richtigen Namen beim Empfänger."""
    quelle = (pathlib.Path(__file__).resolve().parents[1]
              / "photobox_sync" / "targets.py").read_text()
    stelle = quelle.index("class FtpsTarget")
    block = quelle[stelle:quelle.index("class FtpTarget")]
    assert 'STOR {zwischen}' in block
    assert "ftp.rename(zwischen, remote)" in block


def test_giving_up_is_written_down(tmp_path, caplog):
    """Eine Datei, die dauerhaft scheitert, verschwand lautlos aus dem Betrieb."""
    import logging
    import threading

    from photobox_sync.agent import Agent
    from photobox_sync.outbox import Outbox

    box = Outbox(tmp_path / "outbox.sqlite")
    datei = tmp_path / "bild.jpg"
    datei.write_bytes(b"x" * 10)
    box.add(target="ftp", uid="u1", session_uid="u1", event_slug="e",
            kind="photo", variant="web", path=datei, poster=None, meta={}, priority=1)
    eintrag = box.due(limit=1)[0]["id"]

    agent = object.__new__(Agent)
    agent.outbox = box
    with caplog.at_level(logging.WARNING):
        for _ in range(9):
            agent._gescheitert({"id": eintrag, "path": str(datei), "target": "ftp"}, "Server sagt nein")
    assert box.status_of(eintrag) == "failed"
    assert any("Aufgegeben" in r.message for r in caplog.records), \
        "Das Aufgeben steht in keinem Protokoll"
