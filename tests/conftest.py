import importlib.util as _ilu
import os
import sys
import tempfile
from pathlib import Path

import pytest

# The sync package of this checkout, as in booth/tests/conftest.py: a shared
# virtualenv may have photobox_sync installed (editable) from another worktree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

# The whole run lives in one temp folder of its own, removed at the end and
# swept by the next run if this one was killed (tools/tmp_guard.py). First,
# so that the data folder below and everything after it lands inside.
_spec = _ilu.spec_from_file_location(
    "photobox_tmp_guard", Path(__file__).resolve().parents[1] / "tools" / "tmp_guard.py")
_tmp_guard = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_tmp_guard)
RUN_TMP = _tmp_guard.RunRoot("gallery")
_tmp_report: list = ["", False]

# Isolated DATA_DIR before the app imports anything.
os.environ.setdefault("SECRET_KEY", "test-secret")
_TMP = tempfile.mkdtemp(prefix="photobox-test-")
os.environ["DATA_DIR"] = _TMP

# Every mkdtemp folder of this run (the data folder above, a test's own
# booth) is removed when the session ends, pass or fail: they used to stay,
# one "photobox-test-*" per run, until /tmp filled the disk (15.11.2026).
import shutil as _shutil  # noqa: E402

_every = [_TMP]
_real_mkdtemp = tempfile.mkdtemp


def _noted_mkdtemp(*args, **kwargs):
    path = _real_mkdtemp(*args, **kwargs)
    _every.append(path)
    return path


tempfile.mkdtemp = _noted_mkdtemp


def pytest_sessionfinish(session, exitstatus):
    for p in _every:
        _shutil.rmtree(p, ignore_errors=True)
    RUN_TMP.remove()
    _tmp_report[:] = RUN_TMP.report()
    if _tmp_report[1] and session.exitstatus == 0:
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter):
    if _tmp_report[0]:
        terminalreporter.write_line(_tmp_report[0], red=True, bold=True)


@pytest.fixture()
def app():
    from app import create_app
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def formular(client):
    """Absenden wie ein Browser: Seite holen, Zeichen mitnehmen, abschicken.

    Die Verwaltung prüft seit dem 19.09.2026 bei jeder schreibenden Anfrage das
    Formular-Zeichen (CSRF). Ein Test, der ohne postet, prüft einen Weg, den es
    im Browser nicht gibt.
    """
    import re as _re

    def absenden(pfad, daten=None, *, von=None, **kwargs):
        seite = client.get(von or pfad).get_data(as_text=True)
        treffer = _re.search(r'name="csrf_token" value="([^"]*)"', seite)
        werte = dict(daten or {})
        werte.setdefault("csrf_token", treffer.group(1) if treffer else "")
        return client.post(pfad, data=werte, **kwargs)

    return absenden


@pytest.fixture(autouse=True)
def _einstellungen_zuruecksetzen():
    """Nach jedem Test stehen die globalen Einstellungen wieder, wie sie waren.

    Alle Tests teilen sich ein ``DATA_DIR`` — das macht den Lauf schnell, aber
    die Reihenfolge mitentscheidend. Ein Formular wie
    ``/admin/settings/homepage`` setzt jeden Schalter, den es nicht mitschickt,
    auf aus; wer danach lief, prüfte etwas anderes als gedacht. Bei umgekehrter
    Dateireihenfolge fielen dadurch zwei Tests um, und zwei Tests im Bestand
    trugen handgeschriebene Krücken („Defaults wiederherstellen"), die nach
    einer fehlgeschlagenen Behauptung ohnehin nie liefen.

    Die Tabelle ist klein; ein Abzug vorher und ein Abgleich hinterher kostet
    nichts und nimmt der Reihenfolge ihre Wirkung. Events und Aufnahmen bleiben
    stehen — die tragen in den Tests eigene Kennungen.
    """
    from app import db

    def abzug():
        try:
            con = db.get_db()
        except Exception:                   # noqa: BLE001 — vor dem ersten Schema
            return None
        try:
            return {z["key"]: z["value"] for z in con.execute("SELECT key, value FROM settings")}
        except Exception:                   # noqa: BLE001
            return None
        finally:
            con.close()

    vorher = abzug()
    yield
    nachher = abzug()
    if vorher is None or nachher is None or vorher == nachher:
        return
    con = db.get_db()
    try:
        for schluessel in set(vorher) | set(nachher):
            alt, neu = vorher.get(schluessel), nachher.get(schluessel)
            if alt == neu:
                continue
            if alt is None:
                con.execute("DELETE FROM settings WHERE key=?", (schluessel,))
            else:
                con.execute("INSERT INTO settings(key, value) VALUES (?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (schluessel, alt))
        con.commit()
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _fehlversuche_vergessen():
    """Every test starts with no recorded failed attempts.

    The doors (login, second factor, recovery, gallery password) count
    failures per address, and every test client comes from 127.0.0.1. Without
    this, ten wrong passwords spread over the suite would lock a later test out.
    """
    from app import db
    try:
        con = db.get_db()
        try:
            con.execute("DELETE FROM auth_attempts")
            con.commit()
        finally:
            con.close()
    except Exception:                       # noqa: BLE001 — before the first schema
        pass
    yield


#: Everything Einstellungen → Rechtliches needs for pages without placeholders.
#: Fictitious operator; a test that wants a gap clears one field.
LEGAL_FACTS = {
    "legal_operator": "Muster Fotobox",
    "legal_address": "Musterweg 1, 12345 Musterstadt",
    "legal_contact_email": "kontakt@example.org",
    "legal_hoster": "Beispiel Hosting GmbH, Musterstadt",
    "legal_authority": "Die Landesbeauftragte für den Datenschutz Musterland, Musterplatz 1, 12345 Musterstadt",
    "legal_server_logs": "Der Reverse-Proxy schreibt keine Zugriffsprotokolle.",
}


@pytest.fixture()
def legal_facts(client):
    """Fill the legal facts so /impressum and /datenschutz show their text to guests."""
    from app import db
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        for k, v in LEGAL_FACTS.items():
            db.set_setting(k, v)
        for k in ("impressum_text", "datenschutz_text", "impressum_text_en", "datenschutz_text_en"):
            db.set_setting(k, "")
    return dict(LEGAL_FACTS)
