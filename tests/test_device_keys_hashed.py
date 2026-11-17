"""Security review G3, finding S-6 (15.11.2026): device keys are hashed at rest.

A copy of the database (a backup on a stick, a restore folder, a log of a
failed migration) used to hold every booth's key in plain text. Now only the
SHA-256 of a key is stored; the key itself is shown once — in the answer to
creating or renewing it, or handed to the booth when it redeems a pairing
code. Existing keys are hashed in place by the migration, so a booth that is
paired keeps working without being touched.
"""
import hashlib
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sha(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "t"


def _all_cells(device_id):
    from app import db
    con = db.get_db()
    row = dict(con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())
    con.close()
    return " ".join(str(v) for v in row.values())


def test_the_database_holds_only_the_hash(client):
    from app import db
    client.get("/healthz")
    made = db.create_device(name="Box Hash")
    key = made["api_key"]
    assert len(key) >= 40
    assert key not in _all_cells(made["id"])
    assert _sha(key) in _all_cells(made["id"])
    assert db.get_device_by_api_key(key)["id"] == made["id"]
    assert db.get_device_by_api_key(_sha(key)) is None, "the hash itself is no key"
    assert key not in str(db.get_device_by_id(made["id"]))


def test_an_existing_plain_key_is_hashed_in_place_and_keeps_working(client):
    from app import db
    client.get("/healthz")
    key = secrets.token_hex(24)
    con = db.get_db()
    cur = con.execute("INSERT INTO devices(name, api_key, active) VALUES (?,?,1)", ("Alte Box", key))
    did = cur.lastrowid
    con.execute("UPDATE devices SET api_key_hash=NULL WHERE id=?", (did,))
    con.commit()
    con.close()
    db.init_db()
    assert key not in _all_cells(did)
    assert db.get_device_by_api_key(key)["id"] == did
    r = client.post("/api/v2/booth/sync", json={}, headers={"X-API-Key": key})
    assert r.status_code == 200


def test_the_comparison_is_constant_time():
    src = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    block = src[src.index("def get_device_by_api_key"):][:1200]
    assert "compare_digest" in block and "api_key_hash" in block


def test_the_admin_sees_a_key_once_after_renewing_it_and_never_again(client):
    from app import db
    client.get("/healthz")
    made = db.create_device(name="Box Einmal")
    _admin(client)
    assert made["api_key"] not in client.get("/admin/devices").get_data(as_text=True)
    r = client.post(f"/admin/devices/{made['id']}/regen-key", data={"csrf_token": "t"})
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "nur jetzt" in page
    assert db.get_device_by_api_key(made["api_key"]) is None, "the old key is dead"
    fresh = sorted({k for k in __import__("re").findall(r"[0-9a-f]{48}", page)
                    if db.get_device_by_api_key(k)})
    assert len(fresh) == 1
    assert fresh[0] not in client.get("/admin/devices").get_data(as_text=True)
    assert r.headers.get("Cache-Control", "").startswith("no-store")


def test_a_new_box_shows_its_key_once(client):
    from app import db
    client.get("/healthz")
    _admin(client)
    r = client.post("/admin/devices/new", data={"csrf_token": "t", "name": "Box Neu " + secrets.token_hex(2)})
    page = r.get_data(as_text=True)
    keys = {k for k in __import__("re").findall(r"[0-9a-f]{48}", page) if db.get_device_by_api_key(k)}
    assert r.status_code == 200 and len(keys) == 1


def test_redeeming_a_pairing_code_hands_out_a_fresh_key(client):
    from app import db, ecosystem
    from app.blueprints import booth_v2
    client.get("/healthz")
    ecosystem.init_schema()
    made = db.create_device(name="Box Code")
    code = ecosystem.new_pairing(made["id"])["code"]
    got = client.post("/api/v2/pair", json={"code": code}).get_json()
    assert got["ok"] and got["api_key"] != made["api_key"]
    assert db.get_device_by_api_key(got["api_key"])["id"] == made["id"]
    assert db.get_device_by_api_key(made["api_key"]) is None
    booth_v2._PAIR_FAILS.clear()


def test_the_config_download_is_a_post_that_renews_the_key(client):
    from app import db
    client.get("/healthz")
    made = db.create_device(name="Box Datei")
    _admin(client)
    assert client.get(f"/admin/devices/{made['id']}/config.json").status_code == 405
    r = client.post(f"/admin/devices/{made['id']}/config.json", data={"csrf_token": "t"})
    key = r.get_json()["api_key"]
    assert db.get_device_by_api_key(key)["id"] == made["id"]
    assert db.get_device_by_api_key(made["api_key"]) is None


def test_all_device_keys_can_be_renewed_at_once(client):
    """After a password change (or a lost laptop): one button cuts every box off."""
    from app import db
    client.get("/healthz")
    a, b = db.create_device(name="Box A"), db.create_device(name="Box B")
    _admin(client)
    assert "Alle Geräteschlüssel erneuern" in client.get("/admin/devices").get_data(as_text=True)
    client.post("/admin/devices/rotate-all", data={"csrf_token": "t"})
    assert db.get_device_by_api_key(a["api_key"]) is None
    assert db.get_device_by_api_key(b["api_key"]) is None
