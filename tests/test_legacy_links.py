"""The same links after the move from another gallery service.

Addresses there look like ``/<account>/<10-character id>`` and, for one
picture, ``?file=<32 hex>``. After the move only the domain changes: the
gallery answers ``/<account>/<id>`` for the one configured account name and
for nothing else, so no existing route is swallowed.
"""
import csv
import json
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from app import db, gallery_settings as gsm, legacy

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    db.set_setting("legacy_account", "musterbox")
    yield
    db.set_setting("legacy_account", "")


def _event(name=None):
    k = secrets.token_hex(4)
    eid = db.create_event(name=name or f"Alt {k}", slug=f"alt-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean({}))})
    return db.get_event_by_id(eid)


def _media(event, filename="IMG_0001.jpg"):
    con = db.get_db()
    cur = con.execute("INSERT INTO media(event_id, kind, filename, path_original, size_bytes, sha256, share_token)"
                      " VALUES (?,?,?,?,?,?,?)", (event["id"], "photo", filename, "storage/x/" + filename, 1,
                                                  secrets.token_hex(4), secrets.token_hex(8)))
    con.commit()
    con.close()
    return cur.lastrowid


def _fid():
    return secrets.token_hex(5)[:10].upper()


def test_the_old_address_leads_to_the_gallery(client):
    ev = _event()
    fid = _fid()
    legacy.set_event_id(ev["id"], fid)
    r = client.get(f"/musterbox/{fid}")
    assert r.status_code in (301, 302) and r.headers["Location"].endswith(f"/gallery/{ev['slug']}")


def test_the_old_picture_address_opens_the_picture(client):
    ev = _event()
    fid, file_id = _fid(), secrets.token_hex(16)
    legacy.set_event_id(ev["id"], fid)
    mid = _media(ev)
    legacy.set_media_id(mid, file_id)
    r = client.get(f"/musterbox/{fid}?file={file_id}")
    assert r.headers["Location"].endswith(f"/gallery/{ev['slug']}#photo-{mid}")
    r = client.get(f"/musterbox/{fid}?file={secrets.token_hex(16)}")
    assert r.headers["Location"].endswith(f"/gallery/{ev['slug']}")


def test_only_the_configured_account_counts(client):
    ev = _event()
    fid = _fid()
    legacy.set_event_id(ev["id"], fid)
    assert client.get(f"/someoneelse/{fid}").status_code == 404
    assert client.get(f"/musterbox/{_fid()}").status_code == 404
    db.set_setting("legacy_account", "")
    assert client.get(f"/musterbox/{fid}").status_code == 404


def test_the_route_swallows_no_existing_route(client):
    ev = _event()
    assert client.get("/admin/login").status_code == 200
    assert client.get(f"/gallery/{ev['slug']}").status_code == 200
    assert client.get("/static/css/design.css").status_code == 200
    assert client.get("/api/status").status_code != 404
    assert client.get("/lang/en").status_code == 405           # POST-only stays POST-only
    for reserved in ("admin", "api", "static", "gallery", "data", "kunde", "client", "p", "l", "s", "g"):
        assert not legacy.valid_account(reserved), reserved
    assert legacy.valid_account("musterbox")


def test_ids_are_unique(client):
    a, b = _event(), _event()
    fid = _fid()
    legacy.set_event_id(a["id"], fid)
    with pytest.raises(ValueError):
        legacy.set_event_id(b["id"], fid)


def test_the_admin_sets_the_id_and_the_account(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
        s["admin_epoch"] = accounts.session_epoch()
    ev = _event()
    fid = _fid()
    client.post(f"/admin/events/{ev['id']}/legacy-id", data={"csrf_token": "test-token", "legacy_id": fid})
    assert db.get_event_by_id(ev["id"])["foreign_id"] == fid
    client.post("/admin/clients/account", data={"csrf_token": "test-token", "legacy_account": "admin"})
    assert db.get_setting("legacy_account") == "musterbox"          # refused
    client.post("/admin/clients/account", data={"csrf_token": "test-token", "legacy_account": "studio-nord"})
    assert db.get_setting("legacy_account") == "studio-nord"


def test_the_import_tool_reports_set_missing_and_double(client, tmp_path):
    a, b = _event(name="Hochzeit Import A"), _event()
    _media(a, "IMG_4711.jpg")
    f1, f2, f3 = _fid(), _fid(), _fid()
    path = tmp_path / "ids.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["kennung", "slug", "titel", "datei", "datei_kennung"])
        w.writerow([f1, "", "Hochzeit Import A", "IMG_4711.jpg", "a" * 32])
        w.writerow([f2, b["slug"], "", "", ""])
        w.writerow([f3, "gibt-es-nicht", "", "", ""])
        w.writerow([f2, a["slug"], "", "", ""])
    report = legacy.import_csv(path)
    assert report["set"] == 2 and report["missing"] == 1 and report["double"] == 1 and report["files"] == 1
    assert db.get_event_by_id(a["id"])["foreign_id"] == f1
    proc = subprocess.run([sys.executable, "-m", "app.import_ids", str(path)], cwd=ROOT, capture_output=True,
                          text=True, env={**__import__("os").environ})
    assert proc.returncode == 0 and "gesetzt" in proc.stdout
