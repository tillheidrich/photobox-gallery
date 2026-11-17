"""``rehearsal.py --offen`` hands over a gallery you can actually log into.

It used to say the admin password was "in the output above". It was not: the
gallery writes a generated start password to its own log, and the rehearsal
swallows that log. Now the rehearsal picks a random password for its
throwaway gallery and names it next to the address.
"""
import importlib.util
import shutil
import sqlite3
from pathlib import Path

from werkzeug.security import check_password_hash

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rehearsal", ROOT / "tools" / "rehearsal.py")
rehearsal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rehearsal)


def test_the_password_it_names_is_the_one_that_opens_the_admin():
    probe = rehearsal.Rehearsal()
    try:
        probe.prepare_gallery()
        hint = probe.admin_hint()
        assert probe.admin_password and probe.admin_password in hint
        assert "oben" not in hint, "no more pointing at output that does not contain it"
        con = sqlite3.connect(str(probe.gallery_data / "gallery.sqlite3"))
        stored = dict(con.execute("SELECT key, value FROM settings").fetchall())
        con.close()
        assert check_password_hash(stored["admin_password_hash"], probe.admin_password)
        assert stored.get("admin_username") in hint
    finally:
        shutil.rmtree(probe.tmp, ignore_errors=True)


def test_every_rehearsal_gets_its_own_password():
    a, b = rehearsal.Rehearsal(), rehearsal.Rehearsal()
    try:
        assert a.admin_password != b.admin_password
        assert len(a.admin_password) >= 12
    finally:
        shutil.rmtree(a.tmp, ignore_errors=True)
        shutil.rmtree(b.tmp, ignore_errors=True)
