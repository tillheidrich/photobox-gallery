"""Layout approval by the customer (``/layout/<token>``, app/layout_freigabe.py).

The booth owner sends his customer one personal link. The customer picks one
of the offered layouts, fills in a few lines, picks colours and a font from
what the owner allows, may upload a logo, sees the server's own render and
submits. The owner approves (or sends it back with a note); the approved
layout becomes a plain template, assigned to the event, and reaches the
booth through the existing delivery.

The link is a secret on a public page, so most tests here describe an
attack: a guessed token, a token of the neighbour's event, a forged upload, a
text that tries to be a format string.
"""
import hashlib
import io
import json
import re
import secrets

import pytest
from PIL import Image


# ── Fixtures and helpers ─────────────────────────────────────

@pytest.fixture(autouse=True)
def _schema(client):
    client.get("/healthz")
    from app import layout_freigabe, sharelinks
    sharelinks.forget(layout_freigabe.rate_key("127.0.0.1"))
    yield
    sharelinks.forget(layout_freigabe.rate_key("127.0.0.1"))


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"


def _event(client, switch=True, name=None, date="2027-06-19"):
    from app import db, gallery_settings as gsm
    kennung = secrets.token_hex(4)
    eid = db.create_event(name=name or f"Hochzeit {kennung}", slug=f"lf-{kennung}",
                          upload_token=f"tok-{kennung}")
    db.update_event(eid, {"published": 1, "date": date,
                          "gallery_json": json.dumps(gsm.clean({"layout_approval": switch}))})
    return db.get_event_by_id(eid)


def _template(name=None, texts=None, shapes=None, images=None):
    """An own template with customer fields, saved like the editor saves it."""
    from app import vorlagen
    from app.blueprints.admin_booth import _assets_dir
    roh = {"name": name or f"Kundenkarte {secrets.token_hex(3)}", "paper": "10x15",
           "slots": [{"x": 60, "y": 60, "w": 1080, "h": 1200, "shot": 0}],
           "texts": texts if texts is not None else [
               {"x": 600, "y": 1350, "size": 80, "align": "center", "value": "{feld_namen}",
                "color": "@schrift"},
               {"x": 600, "y": 1480, "size": 44, "align": "center", "value": "{feld_datum} · {event_name}",
                "color": "@gedaempft"},
               {"x": 600, "y": 1580, "size": 36, "align": "center", "value": "{feld_spruch}",
                "color": "@akzent"}],
           "shapes": shapes or [], "images": images or []}
    vorlage, _ = vorlagen.pruefe(roh)
    eintrag = vorlagen.speichern(vorlage, _assets_dir())
    return eintrag["file"]


def _offer(templates, **extra):
    offer = {"templates": templates,
             "fields": [{"key": "namen", "label": "Namen", "max": 40, "default": "Anna & Ben"},
                        {"key": "datum", "label": "Datum", "max": 20, "default": ""},
                        {"key": "spruch", "label": "Spruch", "max": 60, "default": ""}],
             "palettes": ["hochzeit", "hell"], "fonts": ["serif", "display"], "logo": True}
    offer.update(extra)
    return offer


def _invite(event, templates=None, **extra):
    from app import layout_freigabe
    templates = templates or [_template()]
    return layout_freigabe.create(event["id"], _offer(templates, **extra), days=30, by="test")


def _page_csrf(client, url):
    page = client.get(url)
    assert page.status_code == 200, page.get_data(as_text=True)[:400]
    found = re.search(r'name="csrf-token" content="([^"]+)"', page.get_data(as_text=True))
    assert found, "the page carries its form token"
    return found.group(1)


def _post(client, url, body, csrf):
    return client.post(url, json=body, headers={"X-CSRF-Token": csrf})


def _values(template, **fields):
    return {"template": template, "fields": {"namen": "Mia & Tom", "datum": "19.06.2027",
                                            "spruch": "Danke!", **fields},
            "palette": "hochzeit", "font": "serif"}


def _png(size=(400, 200), color=(200, 30, 30), **save):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG", **save)
    return buf.getvalue()


def _unknown_body(client):
    r = client.get(f"/layout/{secrets.token_urlsafe(24)}")
    assert r.status_code == 404
    return r.get_data(as_text=True)


# ── 6. The switch ────────────────────────────────────────────

def test_switch_is_off_by_default():
    from app import gallery_settings as gsm
    assert gsm.DEFAULTS["layout_approval"] is False
    assert gsm.clean({})["layout_approval"] is False


def test_with_the_switch_off_the_card_is_hidden_and_the_link_is_a_404(client):
    event = _event(client, switch=True)
    token = _invite(event)
    assert client.get(f"/layout/{token}").status_code == 200
    from app import db, gallery_settings as gsm
    db.update_event(event["id"], gallery_json=json.dumps(gsm.clean({"layout_approval": False})))
    assert client.get(f"/layout/{token}").status_code == 404
    _admin(client)
    page = client.get(f"/admin/events/{event['id']}/edit").get_data(as_text=True)
    assert 'id="layout-freigabe"' not in page
    assert 'name="layout_approval"' in page, "the switch sits with the other gallery settings"


def test_with_the_switch_on_the_event_page_shows_the_card(client):
    event = _event(client)
    _template()
    _admin(client)
    page = client.get(f"/admin/events/{event['id']}/edit").get_data(as_text=True)
    assert 'id="layout-freigabe"' in page
    assert "Layout vom Kunden gestalten lassen" in page


# ── 1. Data ──────────────────────────────────────────────────

def test_the_token_is_long_and_only_its_hash_is_stored(client):
    from app import db, layout_freigabe
    event = _event(client)
    token = _invite(event)
    assert len(token) >= 22 and len(secrets.token_bytes(0)) == 0
    assert len(token) * 6 >= 128, "at least 128 bit"
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute(f"SELECT * FROM {layout_freigabe.TABLE}")]
    finally:
        con.close()
    dump = json.dumps(rows)
    assert token not in dump
    assert hashlib.sha256(token.encode()).hexdigest() in dump
    row = next(r for r in rows if r["event_id"] == event["id"])
    for column in ("status", "templates_json", "fields_json", "palettes_json", "fonts_json", "logo_allowed",
                   "values_json", "note", "created_at", "updated_at", "submitted_at", "approved_at",
                   "expires_at", "approved_by"):
        assert column in row, column
    assert row["status"] == "offen"


def test_one_invitation_per_event_a_new_one_replaces_the_old(client):
    event = _event(client)
    first = _invite(event)
    second = _invite(event)
    assert client.get(f"/layout/{first}").status_code == 404
    assert client.get(f"/layout/{second}").status_code == 200


def test_the_offer_only_takes_own_templates_and_known_choices(client):
    from app import layout_freigabe
    event = _event(client)
    good = _template()
    with pytest.raises(ValueError):
        layout_freigabe.create(event["id"], _offer(["../../etc/passwd"]), days=30, by="t")
    with pytest.raises(ValueError):
        layout_freigabe.create(event["id"], _offer([]), days=30, by="t")
    token = layout_freigabe.create(event["id"], _offer([good], palettes=["hochzeit", "lila-erfunden"],
                                                       fonts=["serif", "comic.ttf"]), days=30, by="t")
    inv = layout_freigabe.by_token(token)
    assert inv["palettes"] == ["hochzeit"]
    assert inv["fonts"] == ["serif"]


# ── 2. Template fields, colours, fonts ───────────────────────

def test_fields_are_found_in_template_texts():
    from app import layout_freigabe
    vorlage = {"texts": [{"value": "{feld_namen} · {date}"}, {"value": "{feld_spruch}"}, {"value": "{event_name}"}]}
    assert layout_freigabe.fields_in(vorlage) == ["namen", "spruch"]


def test_the_concrete_template_is_plain_and_passes_the_shared_check(client):
    from app import db, layout_freigabe, vorlagen, vorlagen_render
    event = _event(client, name="Hochzeit Mia")
    token = _invite(event)
    inv = layout_freigabe.by_token(token)
    vorlage = layout_freigabe.concrete(inv, _values(inv["templates"][0]), event)
    texte = [t["value"] for t in vorlage["texts"]]
    assert texte[0] == "Mia & Tom"
    assert texte[1] == "19.06.2027 · {event_name}", "the booth still fills its own placeholders"
    assert not any("{feld_" in t for t in texte)
    farben = vorlagen_render.STYLE_PRESETS["hochzeit"]
    assert vorlage["texts"][0]["color"] == farben["ink"]
    assert vorlage["texts"][2]["color"] == farben["accent"]
    assert all(t.get("font") == "serif" for t in vorlage["texts"])
    grund = next(s for s in vorlage["shapes"] if s["id"] == "kunde-grund")
    assert grund["fill"] == farben["background"] and vorlage["order"][0] == "kunde-grund"
    again, hinweise = vorlagen.pruefe(vorlage)
    assert again == vorlage and hinweise == [], "the booth gets a template it already understands"


def test_a_template_without_a_choice_keeps_the_booth_style(client):
    from app import layout_freigabe
    event = _event(client)
    token = _invite(event, palettes=[], fonts=[])
    inv = layout_freigabe.by_token(token)
    vorlage = layout_freigabe.concrete(inv, {"template": inv["templates"][0], "fields": {}}, event)
    assert vorlage["texts"][0]["color"] == "@schrift"
    assert vorlage["texts"][0]["value"] == "Anna & Ben", "the owner's default fills an empty field"
    assert "font" not in vorlage["texts"][0]
    assert not any(s["id"] == "kunde-grund" for s in vorlage.get("shapes") or [])


@pytest.mark.parametrize("evil", ["{event_name}", "{0.__class__}", "{headline:>999999999}",
                                  "{date.__class__.__mro__}", "}{", "{", "%s %(x)s"])
def test_a_typed_placeholder_or_format_string_stays_literal(client, evil):
    from app import layout_freigabe, vorlagen_render
    event = _event(client)
    token = _invite(event)
    inv = layout_freigabe.by_token(token)
    vorlage = layout_freigabe.concrete(inv, _values(inv["templates"][0], namen=evil), event)
    st = vorlagen_render.style({})
    value, _font = vorlagen_render._text_zeilen(vorlage["texts"][0], {"event_name": "GEHEIM", "date": "x"}, st)
    assert value == evil
    # and the whole sheet renders, as the booth would
    vorlagen_render.render_template(vorlage, vorlagen_render.beispielbilder(1),
                                    texts={"event_name": "GEHEIM", "date": "x"})


def test_a_text_that_would_be_cut_after_escaping_is_refused(client):
    """pruefe() cuts a text at 120 characters; a cut between two braces would leave
    a lone "{" that the booth's format_map cannot read. Refused instead of cut."""
    from app import layout_freigabe
    event = _event(client)
    tpl = _template(texts=[{"x": 600, "y": 1350, "size": 40, "value": "{feld_namen} " + "x" * 40}])
    token = _invite(event, templates=[tpl],
                    fields=[{"key": "namen", "label": "Namen", "max": 80, "default": ""}])
    inv = layout_freigabe.by_token(token)
    with pytest.raises(ValueError):
        layout_freigabe.concrete(inv, {"template": tpl, "fields": {"namen": "{" * 70}}, event)


def test_control_characters_are_stripped_and_length_is_limited(client):
    from app import layout_freigabe
    event = _event(client)
    token = _invite(event)
    inv = layout_freigabe.by_token(token)
    sauber = layout_freigabe.clean_values(inv, {"template": inv["templates"][0], "fields": {
        "namen": "Mia\x00\x07 &‮ Tom\n\r\t", "spruch": "x" * 500, "fremd": "nein"}})
    assert sauber["fields"]["namen"] == "Mia & Tom"
    assert len(sauber["fields"]["spruch"]) == 60
    assert "fremd" not in sauber["fields"]


def test_the_logo_spot_becomes_the_customers_logo(client):
    from app import layout_freigabe
    event = _event(client)
    tpl = _template(shapes=[{"kind": "rect", "x": 500, "y": 1620, "w": 200, "h": 120, "fill": "@akzent",
                             "name": "Logo"}])
    token = _invite(event, templates=[tpl])
    inv = layout_freigabe.by_token(token)
    ohne = layout_freigabe.concrete(inv, _values(tpl), event)
    assert not any((s.get("name") or "").lower() == "logo" for s in ohne.get("shapes") or []), \
        "an empty logo spot is not printed"
    layout_freigabe.save_logo(inv, _png())
    inv = layout_freigabe.by_token(token)
    mit = layout_freigabe.concrete(inv, dict(_values(tpl), logo=True), event, logo_name="logo-test.png")
    bild = next(b for b in mit["images"] if (b.get("name") or "").lower() == "logo")
    assert bild["file"] == "logo-test.png" and (bild["x"], bild["y"], bild["w"], bild["h"]) == (500, 1620, 200, 120)


# ── 4. Customer page and state machine ───────────────────────

def test_the_page_answers_with_private_headers(client):
    event = _event(client)
    token = _invite(event)
    for r in (client.get(f"/layout/{token}"), client.get(f"/layout/{secrets.token_urlsafe(24)}")):
        assert r.headers["Cache-Control"] == "no-store"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert "noindex" in r.headers.get("X-Robots-Tag", "")
    body = client.get(f"/layout/{token}").get_data(as_text=True)
    for word in ("Layout wählen", "Texte eintragen", "Farben und Schrift", "Vorschau und abschicken"):
        assert word in body
    assert "data-website-id" not in body and "https://" not in body.split("</head>")[0]


def test_tiles_and_preview_are_the_servers_render(client):
    event = _event(client)
    token = _invite(event)
    r = client.get(f"/layout/{token}/kachel/0.jpg")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert r.headers["Cache-Control"] == "no-store"
    assert client.get(f"/layout/{token}/kachel/5.jpg").status_code == 404
    csrf = _page_csrf(client, f"/layout/{token}")
    from app import layout_freigabe
    inv = layout_freigabe.by_token(token)
    r = _post(client, f"/layout/{token}/vorschau", _values(inv["templates"][0]), csrf)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    bild = Image.open(io.BytesIO(r.data))
    assert max(bild.size) <= 1000


def test_the_whole_way_draft_submit_send_back_approve(client):
    from app import db, ecosystem, layout_freigabe
    ecosystem.init_schema()
    event = _event(client, name="Hochzeit Mia und Tom")
    dev = db.create_device(name=f"Box {secrets.token_hex(2)}")
    con = db.get_db()
    con.execute("UPDATE devices SET event_id=? WHERE id=?", (event["id"], dev["id"]))
    con.commit()
    con.close()
    token = _invite(event)
    url = f"/layout/{token}"
    csrf = _page_csrf(client, url)
    inv = layout_freigabe.by_token(token)
    tpl = inv["templates"][0]

    r = _post(client, f"{url}/entwurf", _values(tpl), csrf)
    assert r.status_code == 200 and r.get_json()["status"] == "entwurf"
    assert layout_freigabe.by_token(token)["values"]["fields"]["namen"] == "Mia & Tom"
    wieder = client.get(url).get_data(as_text=True)
    assert 'value="Mia &amp; Tom"' in wieder, "a reopened page shows the saved draft"
    assert re.search(r'value="hochzeit"\s+checked', wieder)

    r = _post(client, f"{url}/abschicken", _values(tpl), csrf)
    assert r.get_json()["status"] == "eingereicht"
    assert "wartet auf Freigabe" in client.get(url).get_data(as_text=True)
    r = _post(client, f"{url}/entwurf", _values(tpl, namen="Nachher"), csrf)
    assert r.status_code == 409, "submitted means locked"
    assert layout_freigabe.by_token(token)["values"]["fields"]["namen"] == "Mia & Tom"

    _admin(client)
    r = client.post(f"/admin/events/{event['id']}/layout/zurueck",
                    data={"csrf_token": "x", "note": "Bitte das Datum ohne Jahr."})
    assert r.status_code == 302
    page = client.get(url).get_data(as_text=True)
    assert "Bitte das Datum ohne Jahr." in page
    csrf = _page_csrf(client, url)        # one test client plays owner and customer: one session
    r = _post(client, f"{url}/entwurf", _values(tpl, datum="19.06."), csrf)
    assert r.get_json()["status"] == "entwurf"
    assert _post(client, f"{url}/abschicken", _values(tpl, datum="19.06."), csrf).get_json()["status"] == "eingereicht"

    vorher = db.get_device_by_id(dev["id"])["profile_version"]
    r = client.post(f"/admin/events/{event['id']}/layout/freigeben", data={"csrf_token": "x"})
    assert r.status_code == 302
    inv = layout_freigabe.by_token(token)
    assert inv["status"] == "freigegeben" and inv["approved_at"] and inv["approved_by"]
    datei = inv["result_file"]
    assert any(a["file"] == datei for a in ecosystem.list_assets("template"))
    overrides = ecosystem.event_profile_overrides(db.get_event_by_id(event["id"]))
    assert overrides["collage_templates"] == [datei]
    assert overrides["collage_style"]["preset"] == "hochzeit", \
        "corners, frame line and photo margins follow the chosen colours at the booth too"
    assert db.get_device_by_id(dev["id"])["profile_version"] > vorher
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "http://x")
    assert prof["collage_templates"] == [datei]
    assert datei in {a["file"] for a in ecosystem.assets_for_profile(prof)}
    gespeichert = json.loads((ecosystem_assets_dir() / datei).read_text(encoding="utf-8"))
    assert gespeichert["texts"][1]["value"].startswith("19.06.")

    page = client.get(url).get_data(as_text=True)
    assert "freigegeben" in page.lower()
    r = client.get(f"{url}/ergebnis.jpg")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert _post(client, f"{url}/entwurf", _values(tpl), csrf).status_code == 409


def ecosystem_assets_dir():
    from app.blueprints.admin_booth import _assets_dir
    return _assets_dir()


def test_approval_only_from_submitted(client):
    from app import layout_freigabe
    event = _event(client)
    token = _invite(event)
    _admin(client)
    client.post(f"/admin/events/{event['id']}/layout/freigeben", data={"csrf_token": "x"})
    assert layout_freigabe.by_token(token)["status"] == "offen"


def test_saving_the_event_page_keeps_the_assigned_layout(client, formular):
    from app import branding, db, ecosystem
    event = _event(client)
    design = {"booth": {"collage_templates": ["kunde-1-x.json"]}}
    db.update_event(event["id"], design_json=branding.dump_design(design))
    _admin(client)
    r = client.post(f"/admin/events/{event['id']}/edit",
                    data={"csrf_token": "x", "name": event["name"], "slug": event["slug"],
                          "retention_days": "30"})
    assert r.status_code == 302
    assert ecosystem.event_profile_overrides(db.get_event_by_id(event["id"]))["collage_templates"] == \
        ["kunde-1-x.json"]


def test_admin_actions_need_login_and_csrf_and_land_in_the_protocol(client):
    from app import layout_freigabe, protokoll
    event = _event(client)
    tpl = _template()
    r = client.post(f"/admin/events/{event['id']}/layout", data={"templates": [tpl]})
    assert r.status_code in (302, 400, 403)
    _admin(client)
    r = client.post(f"/admin/events/{event['id']}/layout", data={"templates": [tpl], "csrf_token": "falsch"})
    assert r.status_code == 400
    r = client.post(f"/admin/events/{event['id']}/layout",
                    data={"csrf_token": "x", "templates": [tpl], "field_key": ["namen", "datum", "spruch"],
                          "field_label": ["Namen", "Datum", "Spruch"], "field_max": ["40", "20", "60"],
                          "field_default": ["", "", ""], "palettes": ["hell"], "fonts": ["serif"],
                          "logo": "1", "days": "30"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert r.headers["Cache-Control"] == "no-store"
    token = re.search(r"/layout/([A-Za-z0-9_-]{32})", body).group(1)
    assert layout_freigabe.by_token(token)["event_id"] == event["id"]
    assert "Link kopieren" in body
    eintrag = protokoll.liste(limit=1)[0]
    assert eintrag["was"] == "Layout-Link erzeugt"
    assert token not in json.dumps(eintrag)
    client.post(f"/admin/events/{event['id']}/layout/sperren", data={"csrf_token": "x"})
    assert client.get(f"/layout/{token}").status_code == 404
    assert protokoll.liste(limit=1)[0]["was"] == "Layout-Link gesperrt"


# ── 5. Security ──────────────────────────────────────────────

def test_unknown_expired_and_locked_links_look_alike(client):
    from app import db, layout_freigabe
    event = _event(client)
    unknown = _unknown_body(client)
    expired = _invite(event)
    con = db.get_db()
    con.execute(f"UPDATE {layout_freigabe.TABLE} SET expires_at=datetime('now','-1 minute') WHERE event_id=?",
                (event["id"],))
    con.commit()
    con.close()
    r = client.get(f"/layout/{expired}")
    assert r.status_code == 404 and r.get_data(as_text=True) == unknown
    other = _event(client)
    locked = _invite(other)
    layout_freigabe.revoke(other["id"])
    r = client.get(f"/layout/{locked}")
    assert r.status_code == 404 and r.get_data(as_text=True) == unknown


def test_one_invitation_cannot_read_or_write_another(client):
    """IDOR: the only identifier the page trusts is the token itself."""
    from app import layout_freigabe
    a_event, b_event = _event(client), _event(client)
    a, b = _invite(a_event), _invite(b_event)
    inv_b = layout_freigabe.by_token(b)
    csrf = _page_csrf(client, f"/layout/{a}")
    # a template offered to B is not one A may choose
    r = _post(client, f"/layout/{a}/entwurf", _values(inv_b["templates"][0]), csrf)
    assert r.status_code == 400
    r = _post(client, f"/layout/{a}/vorschau", _values(inv_b["templates"][0]), csrf)
    assert r.status_code == 400
    # ids in the body are ignored
    inv_a = layout_freigabe.by_token(a)
    body = dict(_values(inv_a["templates"][0]), id=inv_b["id"], event_id=b_event["id"])
    assert _post(client, f"/layout/{a}/entwurf", body, csrf).status_code == 200
    assert layout_freigabe.by_token(b)["status"] == "offen"
    assert layout_freigabe.by_token(b)["values"] == {}
    client.post(f"/layout/{a}/logo", data={"logo": (io.BytesIO(_png()), "l.png")},
                headers={"X-CSRF-Token": csrf}, content_type="multipart/form-data")
    assert not layout_freigabe.logo_path(layout_freigabe.by_token(b))
    assert layout_freigabe.logo_path(layout_freigabe.by_token(a))
    # the page of A does not name B's event
    assert b_event["name"] not in client.get(f"/layout/{a}").get_data(as_text=True)


def test_customer_posts_need_the_form_token(client):
    event = _event(client)
    token = _invite(event)
    from app import layout_freigabe
    inv = layout_freigabe.by_token(token)
    _page_csrf(client, f"/layout/{token}")
    r = client.post(f"/layout/{token}/entwurf", json=_values(inv["templates"][0]))
    assert r.status_code == 400
    r = client.post(f"/layout/{token}/entwurf", json=_values(inv["templates"][0]),
                    headers={"X-CSRF-Token": "falsch"})
    assert r.status_code == 400


def test_misses_are_braked_and_a_valid_link_is_never_counted(client):
    from app import layout_freigabe
    event = _event(client)
    token = _invite(event)
    for _ in range(layout_freigabe.RATE_LIMIT):
        assert client.get(f"/layout/{secrets.token_urlsafe(24)}").status_code == 404
    assert client.get(f"/layout/{secrets.token_urlsafe(24)}").status_code == 429
    assert client.post(f"/layout/{secrets.token_urlsafe(24)}/entwurf", json={}).status_code == 429
    assert client.get(f"/layout/{token}").status_code == 200


def test_the_preview_is_rate_limited_and_one_at_a_time(client, monkeypatch):
    from app import layout_freigabe
    event = _event(client)
    token = _invite(event)
    inv = layout_freigabe.by_token(token)
    csrf = _page_csrf(client, f"/layout/{token}")
    monkeypatch.setattr(layout_freigabe, "LOCK_WAIT", 0.2)
    lock = layout_freigabe._render_lock(inv["id"])
    assert lock.acquire(blocking=False)
    try:
        r = _post(client, f"/layout/{token}/vorschau", _values(inv["templates"][0]), csrf)
        assert r.status_code == 429
    finally:
        lock.release()
    monkeypatch.setattr(layout_freigabe, "PREVIEW_LIMIT", 3)  # the locked try above counted too
    codes = [_post(client, f"/layout/{token}/vorschau", _values(inv["templates"][0]), csrf).status_code
             for _ in range(4)]
    assert codes[:2] == [200, 200] and codes[-1] == 429


def _upload(client, token, csrf, data, name="logo.png"):
    return client.post(f"/layout/{token}/logo", data={"logo": (io.BytesIO(data), name)},
                       headers={"X-CSRF-Token": csrf}, content_type="multipart/form-data")


def test_a_logo_is_re_encoded_without_metadata_and_kept_private(client):
    from app import layout_freigabe, media
    event = _event(client)
    token = _invite(event)
    csrf = _page_csrf(client, f"/layout/{token}")
    exif = Image.Exif()
    exif[0x010F] = "Kamera mit Geheimnis"
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), (10, 120, 10)).save(buf, "JPEG", exif=exif.tobytes())
    r = _upload(client, token, csrf, buf.getvalue(), "foto.jpg")
    assert r.status_code == 200, r.get_data(as_text=True)
    pfad = layout_freigabe.logo_path(layout_freigabe.by_token(token))
    assert pfad and pfad.is_file()
    root = media.data_dir()
    rel = pfad.resolve().relative_to(root).as_posix()
    assert not rel.startswith(("storage/", "assets/")), "not in the public or booth media"
    assert client.get(f"/data/{rel}").status_code == 404
    with Image.open(pfad) as im:
        assert im.format == "PNG"
        assert not im.getexif() and "exif" not in im.info
    assert b"Geheimnis" not in pfad.read_bytes()


@pytest.mark.parametrize("data,name", [
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "logo.svg"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "logo.png"),
    (b"GIF89a" + b"\x00" * 64, "logo.gif"),
    (b"\x89PNG\r\n\x1a\n" + b"kaputt" * 20, "logo.png"),
    (b"MZ\x90\x00 nicht ein Bild", "logo.jpg"),
])
def test_forged_or_unwanted_uploads_are_refused(client, data, name):
    from app import layout_freigabe
    event = _event(client)
    token = _invite(event)
    csrf = _page_csrf(client, f"/layout/{token}")
    r = _upload(client, token, csrf, data, name)
    assert r.status_code == 400
    assert not layout_freigabe.logo_path(layout_freigabe.by_token(token))


def test_oversized_uploads_are_refused(client, monkeypatch):
    from app import layout_freigabe
    event = _event(client)
    token = _invite(event)
    csrf = _page_csrf(client, f"/layout/{token}")
    gross = b"\x89PNG\r\n\x1a\n" + b"\x00" * (layout_freigabe.LOGO_MAX_BYTES + 10)
    assert _upload(client, token, csrf, gross).status_code in (400, 413)
    monkeypatch.setattr(layout_freigabe, "LOGO_MAX_PIXELS", 100 * 100)
    assert _upload(client, token, csrf, _png((200, 200))).status_code == 400
    assert not layout_freigabe.logo_path(layout_freigabe.by_token(token))


def test_no_logo_when_the_owner_did_not_allow_it(client):
    event = _event(client)
    token = _invite(event, logo=False)
    csrf = _page_csrf(client, f"/layout/{token}")
    assert _upload(client, token, csrf, _png()).status_code == 403


def test_nothing_of_this_is_reachable_through_mcp():
    from app import mcp
    # The switch itself is a gallery setting (17.11.2026, every key of
    # gallery_settings is reachable); the approval flow has no tool.
    assert "layout_approval" not in mcp.EVENT_SCHALTER
    namen = " ".join(mcp.TOOLS) if isinstance(mcp.TOOLS, dict) else json.dumps(list(mcp.TOOLS))
    assert "layout" not in namen.lower()


# ── Client area tile ─────────────────────────────────────────

def test_a_client_with_a_code_finds_a_tile_without_the_token(client):
    from app import clients
    event = _event(client)
    token = _invite(event)
    cid = clients.create(f"Paar {secrets.token_hex(2)}")
    code = clients.new_code(cid)
    clients.assign(cid, event["id"], True)
    page = client.get("/kunde").get_data(as_text=True)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    assert client.post("/kunde", data={"code": code, "csrf_token": csrf}).status_code == 302
    home = client.get("/kunde").get_data(as_text=True)
    assert "Drucklayout" in home
    assert f"/kunde/drucklayout/{event['slug']}" in home
    assert token not in home
    r = client.get(f"/kunde/drucklayout/{event['slug']}")
    assert r.status_code == 200 and "Layout wählen" in r.get_data(as_text=True)
    assert token not in r.get_data(as_text=True)
    other = _event(client)
    _invite(other)
    assert client.get(f"/kunde/drucklayout/{other['slug']}").status_code == 404


def test_an_approved_layout_is_not_offered_again(client):
    from app import layout_freigabe
    event = _event(client)
    token = _invite(event)
    inv = layout_freigabe.by_token(token)
    layout_freigabe.submit(inv, _values(inv["templates"][0]), event)
    datei = layout_freigabe.approve(event["id"], "test")["result_file"]
    assert datei not in {t["file"] for t in layout_freigabe.own_templates()}
    with pytest.raises(ValueError):
        layout_freigabe.create(_event(client)["id"], _offer([datei]), days=30, by="t")


def test_the_preview_says_when_a_text_runs_over_the_edge(client):
    from app import layout_freigabe
    event = _event(client)
    token = _invite(event)
    inv = layout_freigabe.by_token(token)
    csrf = _page_csrf(client, f"/layout/{token}")
    kurz = _post(client, f"/layout/{token}/vorschau", _values(inv["templates"][0]), csrf)
    assert kurz.headers["X-Layout-Too-Wide"] == "0"
    lang = _post(client, f"/layout/{token}/vorschau", _values(inv["templates"][0], namen="W" * 40), csrf)
    assert lang.status_code == 200 and lang.headers["X-Layout-Too-Wide"] == "1"
