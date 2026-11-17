"""Second review R2-N1: a profile from the gallery cannot make the booth delete unsent runs.

``update_booth_profile {"aufraeumen": {"enabled": true, "nur_bestaetigte":
false}}`` with the right ``manage`` made the booth delete guest pictures that
had never reached the gallery. On the gallery side that change now needs
``photos``; here, deleting unconfirmed runs needs a yes on this PC
(``local.json`` ``aufraeumen_ohne_bestaetigung``) whenever the booth is
paired with a gallery.
"""
import tempfile
from pathlib import Path

from boothcore.aufraeumen import Aufraeumer
from boothcore.library import Library
from test_aufraeumen import Warteschlange, durchgang


def _bauen(tmp_path, erlaubt):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    lib = Library(tmp_path / "lib.sqlite")
    import time
    jetzt = {"t": time.time()}
    cfg = {"enabled": True, "nach_tagen": 14, "nur_bestaetigte": False, "behalten_mindestens": 0}
    a = Aufraeumer(sessions_dir=sessions, library=lib, outbox=Warteschlange({"neu1": {"offen": 1, "erledigt": 0}}),
                   config=lambda: cfg, hat_ziele=lambda: True, clock=lambda: jetzt["t"],
                   unbestaetigt_erlaubt=lambda: erlaubt)
    return a, lib, sessions, jetzt


def test_without_a_local_yes_unsent_runs_stay(tmp_path):
    a, lib, sessions, jetzt = _bauen(tmp_path, erlaubt=False)
    ordner = durchgang(lib, sessions, "neu1", alter_tage=30, jetzt=jetzt)
    bericht = a.lauf()
    assert bericht["geloescht"] == 0 and ordner.exists()


def test_with_the_local_yes_the_profile_setting_applies(tmp_path):
    a, lib, sessions, jetzt = _bauen(tmp_path, erlaubt=True)
    ordner = durchgang(lib, sessions, "neu1", alter_tage=30, jetzt=jetzt)
    assert a.lauf()["geloescht"] == 1 and not ordner.exists()


def test_the_booth_asks_its_own_local_json(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-cleanup-yes-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    from boothcore.config import DEFAULT_LOCAL
    assert DEFAULT_LOCAL["aufraeumen_ohne_bestaetigung"] is False
    b = Booth(base=d, start_threads=False)
    try:
        assert b.aufraeumer._erlaubt() is True             # no gallery: the profile is this booth's own
        b.store.save_local({"server": {**b.store.local["server"], "url": "https://galerie.example.org"}})
        assert b.aufraeumer._erlaubt() is False            # paired: the profile comes from elsewhere
        b.store.save_local({"aufraeumen_ohne_bestaetigung": True})
        assert b.aufraeumer._erlaubt() is True
    finally:
        b.mail.stop()


def test_the_gallery_cannot_set_the_local_yes():
    import ast
    src = (Path(__file__).resolve().parents[2] / "app" / "ecosystem.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keys = next(ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "REMOTE_LOCAL_KEYS")
    assert "aufraeumen_ohne_bestaetigung" not in keys
