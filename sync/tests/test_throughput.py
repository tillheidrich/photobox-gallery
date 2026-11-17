"""Was ein Sync-Programm am Eventabend besser können muss als ein Dateikopierer.

Filter, Bremse, Pause, parallele Ziele, Nachweis über die angekommene Größe und
ein Verlauf, der die Frage „ist alles oben?" beantwortet.
"""
import threading
import time
from pathlib import Path

import pytest

from photobox_sync.agent import Agent
from photobox_sync.outbox import Outbox
from photobox_sync.targets import PermanentError, TemporaryError, ThrottledReader, open_source, verify_size
from photobox_sync.watcher import Watcher


# ── Filter ────────────────────────────────────────────────
def test_only_the_wanted_files_leave_the_pc(tmp_path):
    job = {"exclude": ["thumbs.db", "*_thumb.jpg"], "min_kb": 10}
    assert Watcher.wanted(tmp_path / "IMG_1.jpg", 200_000, job)
    assert not Watcher.wanted(tmp_path / "Thumbs.db", 200_000, job)       # Groß/klein egal
    assert not Watcher.wanted(tmp_path / "IMG_1_thumb.jpg", 200_000, job)
    assert not Watcher.wanted(tmp_path / "winzig.jpg", 500, job)          # unter der Mindestgröße

    only_video = {"include": ["*.mp4", "*.mov"]}
    assert Watcher.wanted(tmp_path / "clip.mp4", 9_000_000, only_video)
    assert not Watcher.wanted(tmp_path / "IMG_1.jpg", 900_000, only_video)
    assert Watcher.wanted(tmp_path / "IMG_1.jpg", 900_000, {})            # ohne Filter geht alles


def test_the_watcher_applies_the_filter(tmp_path):
    folder = tmp_path / "booth"
    folder.mkdir()
    seen = []
    cfg = [{"path": str(folder), "since": 0, "exclude": ["*_thumb.jpg"], "min_kb": 1}]
    w = Watcher(tmp_path / "w.sqlite", folders=lambda: cfg, on_file=lambda p, u, k, f=None: seen.append(p.name),
                stable_seconds=0, interval=0.1)
    (folder / "IMG_1.jpg").write_bytes(b"x" * 5000)
    (folder / "IMG_1_thumb.jpg").write_bytes(b"x" * 5000)
    (folder / "leer.jpg").write_bytes(b"x")
    time.sleep(0.1)
    w.scan_once()
    assert seen == ["IMG_1.jpg"]


# ── Bremse ────────────────────────────────────────────────
def test_the_speed_limit_actually_slows_reading(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 200_000)
    started = time.monotonic()
    with open(f, "rb") as raw:
        r = ThrottledReader(raw, kbps=100)          # 100 KB/s → ~2 s für 200 KB
        while r.read(64 * 1024):
            pass
    took = time.monotonic() - started
    assert 1.2 < took < 4.0, took


def test_without_a_limit_nothing_is_wrapped(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 100)
    plain = open_source({"path": str(f)}, {"limit_kbps": 0})
    assert not isinstance(plain, ThrottledReader)
    plain.close()
    slow = open_source({"path": str(f)}, {"limit_kbps": 50})
    assert isinstance(slow, ThrottledReader) and slow.read(10) == b"x" * 10
    slow.close()


# ── Nachweis ──────────────────────────────────────────────
def test_a_half_arrived_file_counts_as_not_arrived():
    verify_size(1000, 1000, "nas")          # passt
    verify_size(1000, None, "nas")          # Server nennt keine Größe: dann eben ohne
    with pytest.raises(TemporaryError) as e:
        verify_size(1000, 640, "nas")
    assert "640" in str(e.value)


# ── Pause ─────────────────────────────────────────────────
def _agent(cfg, outbox):
    return Agent(outbox=outbox, local_config=lambda: cfg)


def test_pausing_holds_the_upload_not_the_queue(tmp_path):
    ob = Outbox(tmp_path / "o.sqlite")
    cfg = {"paused": True, "targets": [{"type": "ftp", "name": "nas", "host": "h"},
                                       {"type": "ftp", "name": "picdrop", "host": "h", "paused": True}]}
    a = _agent(cfg, ob)
    assert a.paused() and a.paused("nas")            # global hält alles an
    cfg["paused"] = False
    assert not a.paused() and not a.paused("nas") and a.paused("picdrop")

    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1234)
    ob.add(target="nas", uid="u1", session_uid="s", event_slug="", kind="photo", variant="web", path=f)
    assert ob.counts()["pending"] == 1               # die Datei wartet weiter


# ── Ziele laufen nebeneinander ────────────────────────────
class SlowTarget:
    def __init__(self, name, seconds=0.4):
        self.name, self.seconds, self.got = name, seconds, []

    def upload(self, item):
        time.sleep(self.seconds)
        self.got.append(Path(item["path"]).name)
        return {}


class DeadTarget:
    name = "tot"

    def upload(self, item):
        raise TemporaryError("Netz weg")


def test_one_dead_target_does_not_hold_up_the_others(tmp_path):
    ob = Outbox(tmp_path / "o.sqlite")
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 10)
    slow_a, slow_b = SlowTarget("a"), SlowTarget("b")
    a = _agent({}, ob)
    a.targets = lambda: [slow_a, slow_b, DeadTarget()]
    for name in ("a", "b", "tot"):
        for i in range(3):
            ob.add(target=name, uid=f"{name}{i}", session_uid="s", event_slug="", kind="photo",
                   variant="web", path=f)

    started = time.monotonic()
    th = threading.Thread(target=a._upload_loop, daemon=True)
    th.start()
    for _ in range(60):
        if ob.counts()["done"] >= 6:
            break
        time.sleep(0.1)
    took = time.monotonic() - started
    a.stop()

    assert len(slow_a.got) == 3 and len(slow_b.got) == 3
    # Sechs Uploads à 0,4 s: nacheinander wären es 2,4 s, nebeneinander gut die Hälfte
    assert took < 2.0, took
    assert ob.counts()["pending"] == 3          # das tote Ziel wartet weiter, nichts ist verloren


def test_speed_and_eta_come_from_real_uploads(tmp_path):
    ob = Outbox(tmp_path / "o.sqlite")
    a = _agent({}, ob)
    a._note_speed(1_000_000, 1.0)
    a._note_speed(1_000_000, 1.0)
    assert 900_000 < a.speed_bps() < 1_100_000
    assert a.eta_seconds() == 0                 # nichts offen

    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 2_000_000)
    ob.add(target="nas", uid="u1", session_uid="s", event_slug="", kind="photo", variant="web", path=f)
    assert 1 <= a.eta_seconds() <= 3
    assert ob.pending_by_target()["nas"] == {"files": 1, "bytes": 2_000_000}


# ── Verlauf ───────────────────────────────────────────────
def test_the_history_answers_is_everything_up_there(tmp_path):
    ob = Outbox(tmp_path / "o.sqlite")
    f = tmp_path / "IMG_9.jpg"
    f.write_bytes(b"x" * 4321)
    ob.add(target="nas", uid="u1", session_uid="s", event_slug="", kind="print", variant="web", path=f)
    ob.add(target="picdrop", uid="u1", session_uid="s", event_slug="", kind="print", variant="web", path=f)
    rows = ob.due(10)
    ob.mark_done(rows[0]["id"])
    for _ in range(9):
        ob.mark_retry(rows[1]["id"], "Anmeldung abgelehnt", permanent=True)

    hist = ob.history(10)
    assert {h["target"] for h in hist} == {"nas", "picdrop"}
    good = next(h for h in hist if h["target"] == "nas")
    bad = next(h for h in hist if h["target"] == "picdrop")
    assert good["ok"] and good["file"] == "IMG_9.jpg" and good["bytes"] == 4321
    assert not bad["ok"] and "abgelehnt" in bad["error"]
    assert ob.history(10, target="nas") == [good]


def test_old_entries_age_out_but_the_recent_ones_stay(tmp_path):
    ob = Outbox(tmp_path / "o.sqlite")
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    for i in range(3):
        ob.add(target="nas", uid=f"u{i}", session_uid="s", event_slug="", kind="photo", variant="web", path=f)
    for row in ob.due(10):
        ob.mark_done(row["id"])
    with ob._con() as con:                       # zwei Einträge künstlich altern lassen
        con.execute("UPDATE outbox SET done=? WHERE uid IN ('u0','u1')", (time.time() - 90 * 86400,))
    assert ob.prune(days=30, keep=1) == 2
    assert len(ob.history(10)) == 1
