"""Every way a print template comes in passes the same full check (17.11.2026).

Before, an upload under "Medien & Vorlagen" was only asked for ``id`` and
``slots`` and the booth loaded the file raw; MCP ``add_asset`` did not even
ask that. Now one function, ``vorlagen_kern.annehmen``, stands at every door
(asset upload, editor save, customer layout, MCP ``save_template`` /
``validate_template`` / ``add_asset``, package import, the library, a booth
that pushes its own template, a stored file read back after a restore). It
refuses a file that is not a template of this format with every reason
named, drops keys the format does not know (and says which), and keeps what
the renderer really uses: a slot's ``fit``, a sheet's own ``background`` and
``plain``.
"""
from __future__ import annotations

import base64
import io
import json
import secrets
import zipfile
from pathlib import Path

import pytest

from app import vorlagen_kern as kern

ROOT = Path(__file__).resolve().parents[1]
GUT = {"id": "gate-gut", "name": "Gut", "paper": "10x15",
       "slots": [{"x": 60, "y": 60, "w": 1080, "h": 1300, "shot": 0}],
       "texts": [{"value": "{event_name}", "x": 600, "y": 1500, "size": 60, "align": "center"}]}


def _mit(**aenderung):
    v = json.loads(json.dumps(GUT))
    for pfad, wert in aenderung.items():
        ziel, teile = v, pfad.split("__")
        for t in teile[:-1]:
            ziel = ziel[int(t)] if t.isdigit() else ziel[t]
        ziel[teile[-1]] = wert
    return v


BOESE = [
    ("zu-viele-bildfelder", _mit(slots=[{"x": 0, "y": 0, "w": 100, "h": 100}] * 13), "slots"),
    ("anker-ausserhalb", _mit(slots__0__x=99999), "slots[0].x"),
    ("negativ", _mit(slots__0__y=-5), "slots[0].y"),
    ("groesser-als-das-blatt", _mit(slots__0__w=5000), "slots[0].w"),
    ("zahl-als-text", _mit(slots__0__x="60"), "slots[0].x"),
    ("unendlich", _mit(texts__0__size=float("inf")), "texts[0].size"),
    ("text-zu-lang", _mit(texts__0__value="x" * 121), "texts[0].value"),
    ("steuerzeichen", _mit(name="Gut\x07"), "name"),
    ("schrift-mit-pfad", _mit(texts__0__font="../fonts/x.ttf"), "texts[0].font"),
    ("bild-als-adresse", _mit(images=[{"file": "https://example.invalid/a.png", "x": 0, "y": 0, "w": 50, "h": 50}]),
     "images[0].file"),
    ("overlay-pfad", _mit(overlay="../../etc/rahmen.png"), "overlay"),
    ("farbe", _mit(texts__0__color="rot bitte"), "texts[0].color"),
    ("unbekanntes-papier", _mit(paper="din-a4"), "paper"),
    ("fit-unbekannt", _mit(slots__0__fit="strecken"), "slots[0].fit"),
    ("plain-kein-schalter", _mit(plain="ja"), "plain"),
    ("hintergrund-url", _mit(background="url(https://example.invalid)"), "background"),
    ("rahmen-statt-vorlage", {"kind": "motion_frame", "name": "x"}, "Rahmen"),
    ("kein-objekt", ["slots"], "JSON-Objekt"),
]


@pytest.mark.parametrize("roh, feld", [pytest.param(r, f, id=i) for i, r, f in BOESE])
def test_the_gate_refuses_and_names_the_field(roh, feld):
    with pytest.raises(kern.VorlageAbgelehnt) as e:
        kern.annehmen(roh)
    assert any(feld in g for g in e.value.gruende), e.value.gruende
    assert feld in str(e.value)


def test_a_file_larger_than_the_limit_is_refused_before_it_is_read():
    gross = json.dumps(_mit(name="x" * 10)).encode() + b" " * kern.MAX_DATEI_BYTES
    with pytest.raises(kern.VorlageAbgelehnt, match="größer"):
        kern.annehmen(gross)


def test_unknown_keys_are_dropped_everywhere_and_named():
    roh = _mit(spielerei=1)
    roh["slots"][0]["filter"] = "sepia"
    roh["texts"][0]["onclick"] = "x"
    vorlage, hinweise = kern.annehmen(roh)
    assert "spielerei" not in vorlage and "filter" not in vorlage["slots"][0]
    assert "onclick" not in vorlage["texts"][0]
    assert any("spielerei" in h and "slots[0].filter" in h and "texts[0].onclick" in h for h in hinweise)


def test_fit_background_and_plain_are_kept_because_the_renderer_uses_them():
    from app import vorlagen_render
    assert kern.EINPASSEN == vorlagen_render.EINPASSEN
    roh = _mit(background="#000000", plain=True, slots__0__fit="einpassen")
    vorlage, _ = kern.annehmen(roh)
    assert vorlage["background"] == "#000000" and vorlage["plain"] is True
    assert vorlage["slots"][0]["fit"] == "einpassen"
    vorlage, _ = kern.annehmen(_mit(background="wiese.jpg"))
    assert vorlage["background"] == "wiese.jpg"


def _bibliothek():
    for p in sorted((ROOT / "app" / "vorlagen_bibliothek" / "vorlagen").rglob("*.json")):
        yield p.name, json.loads(p.read_text(encoding="utf-8"))


def _beispiele():
    import re
    text = (ROOT / "docs" / "VORLAGEN-FORMAT-FUER-KI.md").read_text(encoding="utf-8")
    for block in re.findall(r"```json\n(.*?)\n```", text, flags=re.S):
        e = json.loads(block)
        if isinstance(e, dict) and e.get("kind") != "motion_frame":
            yield "doc-" + e["id"], e


@pytest.mark.parametrize("name, roh", [pytest.param(n, r, id=n) for n, r in list(_bibliothek()) + list(_beispiele())])
def test_every_bundled_template_passes_and_renders_byte_for_byte(name, roh):
    from app import vorlagen_render
    geprueft, hinweise = kern.annehmen(json.loads(json.dumps(roh)), messen=False)
    assert not any(h.startswith("Unbekannte") for h in hinweise), hinweise
    medien = ROOT / "app" / "vorlagen_bibliothek" / "medien"
    fotos = vorlagen_render.beispielbilder(geprueft.get("shots") or 1)
    texte = {"event_name": "Mia & Tom", "date": "01.01.2027"}
    vorher = vorlagen_render.render_template(roh, fotos, assets_dir=medien, texts=texte)
    nachher = vorlagen_render.render_template(geprueft, fotos, assets_dir=medien, texts=texte)
    assert vorher.tobytes() == nachher.tobytes()


# ── The doors ───────────────────────────────────────────────

@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    return client


def _hochladen(admin, inhalt: bytes, name="vorlage.json"):
    return admin.post("/admin/booth-assets", data={"kind": "template", "csrf_token": "test-token",
                                                   "files": (io.BytesIO(inhalt), name)},
                      content_type="multipart/form-data", follow_redirects=True)


def test_an_uploaded_template_is_checked_fully_and_stored_as_checked(admin):
    from app import ecosystem
    from app.blueprints.admin_booth import _assets_dir
    vorher = len(ecosystem.list_assets("template"))
    r = _hochladen(admin, json.dumps(_mit(slots__0__x=99999)).encode())
    assert r.status_code == 200
    assert "slots[0].x" in r.get_data(as_text=True)
    assert len(ecosystem.list_assets("template")) == vorher

    r = _hochladen(admin, json.dumps(_mit(id="gate-hoch-" + secrets.token_hex(2), extra=1)).encode())
    neu = [a for a in ecosystem.list_assets("template") if a["file"].startswith("template-vorlage-")][-1]
    gespeichert = json.loads((_assets_dir() / neu["file"]).read_text(encoding="utf-8"))
    assert "extra" not in gespeichert and gespeichert["order"]


def test_the_editor_save_refuses_with_the_fields(admin):
    r = admin.post("/admin/vorlagen/speichern", json=_mit(texts__0__font="/etc/x.ttf"),
                   headers={"X-CSRF-Token": "test-token"})
    assert r.status_code == 400
    assert any("texts[0].font" in f for f in r.get_json()["fields"])


def _mcp(client, name, arguments):
    import secrets as s
    from app import accounts, db, oauth
    cid = "pbc_" + s.token_hex(6)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    answer = oauth._issue(con, cid, s.token_hex(6), "read manage", "http://localhost/mcp", accounts.session_epoch())
    con.commit()
    con.close()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": name, "arguments": arguments}},
                    headers={"Authorization": f"Bearer {answer['access_token']}"})
    result = r.get_json()["result"]
    return bool(result.get("isError")), result["content"][0]["text"]


def test_mcp_save_validate_and_add_asset_use_the_gate(admin):
    fehler, text = _mcp(admin, "save_template", {"template": _mit(slots__0__w=5000)})
    assert fehler and "slots[0].w" in text
    fehler, text = _mcp(admin, "validate_template", {"template": _mit(texts__0__color="lila")})
    assert fehler and "texts[0].color" in text
    roh = base64.b64encode(json.dumps(_mit(overlay="../x.png")).encode()).decode()
    fehler, text = _mcp(admin, "add_asset", {"kind": "template", "filename": "a.json", "content_base64": roh})
    assert fehler and "overlay" in text
    gut = base64.b64encode(json.dumps(_mit(id="gate-mcp", extra=True)).encode()).decode()
    fehler, text = _mcp(admin, "add_asset", {"kind": "template", "filename": "a.json", "content_base64": gut})
    assert not fehler
    from app.blueprints.admin_booth import _assets_dir
    assert "extra" not in json.loads((_assets_dir() / json.loads(text)["file"]).read_text(encoding="utf-8"))


def test_a_package_keeps_out_what_the_gate_refuses():
    from app import vorlagen_paket
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("paket.json", json.dumps({"format": vorlagen_paket.FORMAT}))
        z.writestr("vorlagen/gut.json", json.dumps(GUT))
        z.writestr("vorlagen/boese.json", json.dumps(_mit(id="boese", slots__0__x=99999)))
    paket = vorlagen_paket.lesen(puffer.getvalue())
    assert [v["id"] for v in paket.vorlagen] == ["gate-gut"]
    assert any("boese.json" in h and "slots[0].x" in h for h in paket.hinweise)


def test_a_template_a_booth_pushes_is_checked_and_a_refused_one_is_acknowledged(admin):
    from app import db, ecosystem
    from app.blueprints import booth_v2
    from app.blueprints.admin_booth import _assets_dir
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    with admin.application.app_context():
        quitt = booth_v2._vorlagen_von_der_box(dev, [
            {"file": "boese.json", "template": _mit(id="von-box-boese", texts__0__value="y" * 500)},
            {"file": "gut.json", "template": _mit(id="von-box-gut")}])
    assert quitt == ["boese.json", "gut.json"]
    namen = {a["file"] for a in ecosystem.list_assets("template")}
    assert "von-box-gut.json" in namen and "von-box-boese.json" not in namen
    assert not (_assets_dir() / "von-box-boese.json").exists()


def test_a_stored_file_that_does_not_pass_is_left_out_not_shown_raw(admin):
    """A restored backup or a copied file: read back through the gate."""
    from app import ecosystem, vorlagen
    from app.blueprints.admin_booth import _assets_dir
    datei = f"zurueck-{secrets.token_hex(3)}.json"
    (_assets_dir()).mkdir(parents=True, exist_ok=True)
    (_assets_dir() / datei).write_text(json.dumps(_mit(overlay="../../geheim.png")), encoding="utf-8")
    ecosystem.add_asset("template", "Zurück", datei, "0" * 64, 10)
    assert vorlagen.laden(datei, _assets_dir()) is None
    assert datei not in {e["asset"]["file"] for e in vorlagen.eigene(_assets_dir())}
    ok = f"zurueck-ok-{secrets.token_hex(3)}.json"
    (_assets_dir() / ok).write_text(json.dumps(_mit(unbekannt=1)), encoding="utf-8")
    assert "unbekannt" not in vorlagen.laden(ok, _assets_dir())
