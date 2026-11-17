"""Galerie-Code + personalisierbarer Slug (mit Alias)."""
import re, secrets, pytest


def _event(app, slug="ev", pw=""):
    with app.app_context():
        from app import db
        db.init_db()
        eid = db.create_event(slug=slug, name="E", upload_token=secrets.token_hex(6), password=pw)
        con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (eid,)); con.commit(); con.close()
        return eid


def test_event_gets_unique_code(app):
    from app import db
    eid = _event(app, "code-ev")
    with app.app_context():
        ev = db.get_event_by_id(eid)
    code = ev["code"]
    assert code and len(code) == 6
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{6}", code), code  # keine 0/O/1/I/L


def test_get_event_by_code_normalizes(app):
    from app import db
    eid = _event(app, "norm-ev")
    with app.app_context():
        code = db.get_event_by_id(eid)["code"]
        # klein + Leerzeichen/Bindestrich werden normalisiert
        assert db.get_event_by_code(code.lower())["id"] == eid
        assert db.get_event_by_code(f" {code[:3]}-{code[3:]} ")["id"] == eid
        assert db.get_event_by_code("ZZZZZZ") is None


def test_g_code_redirects_to_gallery(app, client):
    from app import db
    eid = _event(app, "glink")
    with app.app_context():
        code = db.get_event_by_id(eid)["code"]
    r = client.get(f"/g/{code}")
    assert r.status_code in (301, 302)
    assert "/gallery/glink" in r.headers["Location"]


def test_find_by_code_and_unknown(app, client):
    from app import db
    eid = _event(app, "findme")
    with app.app_context():
        code = db.get_event_by_id(eid)["code"]
    ok = client.get(f"/finden?code={code}")
    assert "/gallery/findme" in ok.headers["Location"]
    bad = client.get("/finden?code=NOPE12")
    assert "code_error" in bad.headers["Location"]


def test_slug_change_keeps_old_as_alias(app, client):
    from app import db
    eid = _event(app, "alt-slug")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    lp = client.get(f"/admin/events/{eid}/edit").get_data(as_text=True)
    csrf = re.search(r'name="csrf_token" value="([^"]*)"', lp)
    csrf = csrf.group(1) if csrf else ""
    client.post(f"/admin/events/{eid}/edit",
                data={"csrf_token": csrf, "name": "E", "slug": "neu-slug", "published": "on", "retention_days": "90"})
    # neuer Slug erreichbar, alter leitet weiter (Alias)
    assert client.get("/gallery/neu-slug").status_code == 200
    r_old = client.get("/gallery/alt-slug")
    assert r_old.status_code in (200, 301, 302)
