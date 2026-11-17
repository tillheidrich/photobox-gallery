"""Kopplung: wie der Event-PC und die Galerie zueinander finden.

Von Hand ist das der einzige Schritt der ganzen Einrichtung, also muss er
sitzen. Geprüft wird beides — der kurze Code, den man vorlesen kann, und der
lange, der auch ohne Netz funktioniert — und dass das Format auf beiden Seiten
dasselbe ist.
"""
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

from app import pairing as gallery_pairing          # noqa: E402
from photobox_sync import pairing as pc_pairing     # noqa: E402


@pytest.fixture()
def admin_client(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _device(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    return db.get_device_by_id(db.create_device(name="Event-PC " + secrets.token_hex(2))["id"])


# ── Langer Code ───────────────────────────────────────────
def test_both_sides_speak_the_same_format():
    """Die Galerie schreibt, der PC liest — zwei Dateien, ein Format."""
    key = secrets.token_urlsafe(32)
    from_gallery = gallery_pairing.make_code("https://musterbox.photobox-gallery.com/", key, "Event-PC")
    read_on_pc = pc_pairing.read_code(from_gallery)
    assert read_on_pc == {"url": "https://musterbox.photobox-gallery.com",
                          "api_key": key, "name": "Event-PC"}
    # und andersherum
    assert gallery_pairing.read_code(pc_pairing.make_code("https://x.de", key)) ["api_key"] == key


def test_a_code_cut_in_half_is_refused_not_half_applied():
    code = gallery_pairing.make_code("https://x.de", "schluessel")
    for broken in (code[:-1], code[: len(code) // 2], code.replace("PBX1", "PBX9"), "", "hallo"):
        with pytest.raises(ValueError):
            pc_pairing.read_code(broken)


# ── Kurzer Code ───────────────────────────────────────────
def test_the_short_code_can_be_written_the_way_people_write_it():
    für_produkt = pc_pairing.parse_input("musterbox-ABCD-EFGH")
    assert für_produkt == {"mode": "fetch", "url": "https://musterbox.photobox-gallery.com",
                           "code": "ABCDEFGH"}
    # Kleinschreibung, Leerzeichen, eigene Adresse, Schrägstrich — alles dasselbe
    assert pc_pairing.parse_input("  musterbox-abcd-efgh ")["code"] == "ABCDEFGH"
    assert pc_pairing.parse_input("meine-galerie.de ABCD-EFGH")["url"] == "https://meine-galerie.de"
    assert pc_pairing.parse_input("https://foto.example.de/ABCD-EFGH")["url"] == "https://foto.example.de"
    # Nur der Code: geht, wenn die Adresse schon steht — der Fall nach „Schlüssel erneuern"
    assert pc_pairing.parse_input("ABCD-EFGH", known_url="https://alt.de/")["url"] == "https://alt.de"
    with pytest.raises(ValueError, match="Adresse"):
        pc_pairing.parse_input("ABCD-EFGH")
    with pytest.raises(ValueError, match="acht Zeichen"):
        pc_pairing.parse_input("ABC-EF")


def test_the_alphabet_has_no_characters_that_get_misread():
    """Ein Code wird vorgelesen und abgetippt — I, O, 0 und 1 gehören da nicht hinein."""
    from app import ecosystem

    for verwechselbar in "IO01":
        assert verwechselbar not in pc_pairing.ALPHABET
    assert pc_pairing.ALPHABET == ecosystem.PAIR_ALPHABET      # beide Seiten, ein Alphabet


def test_a_code_works_once_and_then_never_again(client, admin_client):
    from app import db, ecosystem

    dev = _device(client)
    made = admin_client.post(f"/admin/devices/{dev['id']}/pairing",
                             headers={"X-CSRF-Token": "test-token"}).get_json()
    # The long code carries a key; since S-6 it is made only on request (it renews the key)
    assert made["ok"] and made["long"] == ""
    long_made = admin_client.post(f"/admin/devices/{dev['id']}/pairing", json={"long": True},
                                  headers={"X-CSRF-Token": "test-token"}).get_json()
    assert long_made["long"].startswith("PBX1-")
    # (a new code replaces the older one of the same device)
    code = pc_pairing.parse_input(long_made["short"], known_url=made["url"])["code"]

    first = client.post("/api/v2/pair", json={"code": code}).get_json()
    # Redeeming hands out a fresh key (only hashes are stored, S-6)
    assert db.get_device_by_api_key(first["api_key"])["id"] == dev["id"]
    assert first["device_name"] == dev["name"]

    again = client.post("/api/v2/pair", json={"code": code})
    assert again.status_code == 404
    assert ecosystem.redeem_pairing(code) is None


def test_an_expired_code_is_no_key(client, admin_client):
    from app import db, ecosystem

    dev = _device(client)
    ecosystem.new_pairing(dev["id"], minutes=15)
    con = db.get_db()
    con.execute("UPDATE device_pairings SET expires_at = datetime('now', '-1 minute') WHERE device_id=?",
                (dev["id"],))
    con.commit()
    row = con.execute("SELECT code FROM device_pairings WHERE device_id=?", (dev["id"],)).fetchone()
    con.close()
    assert ecosystem.redeem_pairing(row["code"]) is None


def test_a_refused_code_says_whether_it_was_used_or_expired(client, admin_client):
    """The booth shows a different next step for each; the status stays 404."""
    from app import db, ecosystem
    from app.blueprints import booth_v2

    booth_v2._PAIR_FAILS.clear()
    dev = _device(client)
    used = ecosystem.new_pairing(dev["id"])["code"]
    assert client.post("/api/v2/pair", json={"code": used}).status_code == 200
    again = client.post("/api/v2/pair", json={"code": used})
    assert again.status_code == 404 and again.get_json()["reason"] == "used"

    old = ecosystem.new_pairing(dev["id"])["code"]
    con = db.get_db()
    con.execute("UPDATE device_pairings SET expires_at = datetime('now', '-1 minute') WHERE code=?", (old,))
    con.commit()
    con.close()
    late = client.post("/api/v2/pair", json={"code": old})
    assert late.status_code == 404 and late.get_json()["reason"] == "expired"
    assert client.post("/api/v2/pair", json={"code": "ZZZZZZZZ"}).get_json()["reason"] == "unknown"
    booth_v2._PAIR_FAILS.clear()


def test_the_pair_answer_names_the_gallery(client):
    from app import db, ecosystem
    from app.blueprints import booth_v2

    booth_v2._PAIR_FAILS.clear()
    db.set_setting("homepage_headline", "Fotobox Heidrich")
    dev = _device(client)
    code = ecosystem.new_pairing(dev["id"])["code"]
    answer = client.post("/api/v2/pair", json={"code": code}).get_json()
    assert answer["gallery_name"] == "Fotobox Heidrich"


def test_the_booth_profile_says_whether_the_event_is_published(client):
    from app import db, ecosystem

    dev = _device(client)
    event_id = db.create_event(slug="fest-" + secrets.token_hex(3), name="Fest", upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=0 WHERE id=?", (event_id,))
    con.commit()
    con.close()
    ecosystem.save_device_profile(dev["id"], {}, event_id)
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://galerie.example.de")
    assert prof["event"]["slug"].startswith("fest-") and prof["event"]["published"] is False
    con = db.get_db()
    con.execute("UPDATE events SET published=1 WHERE id=?", (event_id,))
    con.commit()
    con.close()
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://galerie.example.de")
    assert prof["event"]["published"] is True


def test_guessing_codes_gets_you_locked_out(client):
    from app.blueprints import booth_v2

    booth_v2._PAIR_FAILS.clear()
    codes = ["AAAA" + f"{i:04d}".translate(str.maketrans("0123456789", "ABCDEFGHJK")) for i in range(12)]
    statuses = [client.post("/api/v2/pair", json={"code": c}).status_code for c in codes]
    assert statuses[0] == 404 and 429 in statuses, statuses
    booth_v2._PAIR_FAILS.clear()


def test_the_code_endpoint_is_the_only_thing_open_without_login(client):
    dev = _device(client)
    # Erzeugen braucht den Admin …
    assert client.post(f"/admin/devices/{dev['id']}/pairing").status_code in (302, 400, 401, 403)
    # … einlösen nicht, sonst käme der PC nie an seinen Schlüssel
    assert client.post("/api/v2/pair", json={"code": "ZZZZZZZZ"}).status_code == 404


# ── Der ganze Weg ─────────────────────────────────────────
def test_pairing_a_real_sync_program_over_http(client, admin_client, tmp_path, monkeypatch):
    """Der PC hat nichts als den Code — danach kennt er Adresse und Schlüssel."""
    import httpx

    from photobox_sync.standalone import SyncApp

    dev = _device(client)
    made = admin_client.post(f"/admin/devices/{dev['id']}/pairing",
                             headers={"X-CSRF-Token": "test-token"}).get_json()

    # Die Galerie läuft hier im Testclient, nicht auf einem Port: httpx dorthin umbiegen
    def fake_post(url, json=None, **kw):
        r = client.post("/api/v2/pair", json=json)
        return httpx.Response(r.status_code, json=r.get_json())
    monkeypatch.setattr(httpx, "post", fake_post)

    pc = SyncApp(base=tmp_path / "data", start_threads=False)
    assert not (pc.config.get("server") or {}).get("api_key")
    out = pc.pair(made["short"])

    from app import db
    paired_key = pc.config["server"]["api_key"]
    assert db.get_device_by_api_key(paired_key)["id"] == dev["id"]
    assert pc.config["server"]["url"] == made["url"]
    assert pc.config["device_name"] == dev["name"]        # Name kommt mit, wenn noch keiner gesetzt war
    assert out["url"] == made["url"]

    # Zweiter Versuch mit demselben Code: sauberer Satz statt kaputter Konfiguration
    # (since 15.11.2026 the gallery says why: this code was used, not expired)
    with pytest.raises(ValueError, match="schon eingelöst"):
        pc.pair(made["short"])
    assert pc.config["server"]["api_key"] == paired_key


def test_pairing_with_the_long_code_needs_no_network(tmp_path):
    from photobox_sync.standalone import SyncApp

    # .invalid never resolves: this test must not reach any real server (S-8)
    code = gallery_pairing.make_code("https://galerie.invalid", "geheimer-schluessel", "Box 1")
    pc = SyncApp(base=tmp_path / "data", start_threads=False)
    pc.save_config({"device_name": "Mein PC"})
    out = pc.pair(code)
    assert pc.config["server"]["api_key"] == "geheimer-schluessel"
    assert pc.config["device_name"] == "Mein PC"          # eigener Name bleibt stehen
    assert out["url"].endswith("galerie.invalid")
