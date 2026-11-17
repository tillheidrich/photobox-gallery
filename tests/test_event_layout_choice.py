"""Per event, the admin ticks which layouts the booth offers.

Owner, 15.11.2026: "Man muss je Event aktivieren können welche Layouts es
gibt". The event page lists every layout there is — own templates (with
those adopted from the library), the plain ones, the built-ins — each with a
picture rendered by the gallery with the real renderer and sample photos.
Nothing ticked: the box's own profile decides, as before. Ticked: the booth
offers exactly those while it is on this event (``collage_exact`` with the
two lists), offline once it has pulled the profile and the template files.
"""
import json
import secrets

import pytest
from PIL import Image

from app import branding, db, ecosystem, vorlagen
from app.blueprints.admin_booth import _assets_dir


@pytest.fixture()
def admin(client):
    client.get("/healthz")
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _event():
    k = secrets.token_hex(3)
    eid = db.create_event(name=f"Layoutwahl {k}", slug=f"lw-{k}", upload_token=f"t-{k}")
    return db.get_event_by_id(eid)


def _own_template(name="Eigene Karte"):
    vorlage, _ = vorlagen.pruefe({"name": f"{name} {secrets.token_hex(2)}", "paper": "10x15", "shots": 1,
                                  "slots": [{"x": 60, "y": 60, "w": 1080, "h": 1300}],
                                  "texts": [{"x": 60, "y": 1450, "size": 50, "value": "{event_name}"}]})
    return vorlagen.speichern(vorlage, _assets_dir())["file"]


def _box(event, profile=None):
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    ecosystem.save_device_profile(dev["id"], profile or {}, event["id"])
    return db.get_device_by_id(dev["id"])


def _save(admin, event, **fields):
    data = {"csrf_token": "test-token", "name": event["name"], "slug": event["slug"], "ev_layouts_form": "1"}
    data.update(fields)
    return admin.post(f"/admin/events/{event['id']}/edit", data=data)


def test_the_event_page_lists_every_layout_with_a_picture_and_a_box(admin):
    event = _event()
    datei = _own_template()
    page = admin.get(f"/admin/events/{event['id']}/edit").get_data(as_text=True)
    assert "Layouts für dieses Event" in page
    for kind, ident in (("t", datei), ("b", "duo"), ("b", "schlicht-1-weiss"), ("b", "schlicht-streifen4-schwarz")):
        assert f'/admin/events/{event["id"]}/layouts/{kind}/{ident}.png' in page, ident
    assert f'name="ev_collage_templates" value="{datei}"' in page
    assert 'name="ev_collage_builtins" value="schlicht-2-quer-weiss"' in page


def test_nothing_ticked_leaves_the_box_as_it_is(admin):
    event = _event()
    datei = _own_template()
    box = _box(event, {"collage_templates": [datei], "collage_builtins": ["grid4"]})
    _save(admin, event)
    assert "collage_exact" not in ecosystem.event_profile_overrides(db.get_event_by_id(event["id"]))
    prof = ecosystem.profile_for_box(box, "https://x.de")
    assert prof["collage_templates"] == [datei] and prof["collage_builtins"] == ["grid4"]
    assert prof["collage_exact"] is False


def test_ticked_layouts_are_exactly_what_the_box_offers_at_this_event(admin):
    event = _event()
    eigene = _own_template()
    andere = _own_template("Andere")
    box = _box(event, {"collage_templates": [andere], "collage_builtins": ["grid4"]})
    _save(admin, event, ev_collage_templates=[eigene], ev_collage_builtins=["schlicht-1-weiss", "duo"])
    prof = ecosystem.profile_for_box(box, "https://x.de")
    assert prof["collage_exact"] is True
    assert prof["collage_templates"] == [eigene]
    assert prof["collage_builtins"] == ["schlicht-1-weiss", "duo"]
    # The template file goes to the box with the profile: offline afterwards.
    assert eigene in {a["file"] for a in ecosystem.assets_for_profile(prof)}


def test_only_built_ins_ticked_still_beats_the_boxs_own_templates(admin):
    event = _event()
    andere = _own_template("Haus")
    box = _box(event, {"collage_templates": [andere]})
    _save(admin, event, ev_collage_builtins=["schlicht-3-schwarz"])
    prof = ecosystem.profile_for_box(box, "https://x.de")
    assert prof["collage_exact"] is True and prof["collage_templates"] == []
    assert prof["collage_builtins"] == ["schlicht-3-schwarz"]


def test_unticking_everything_goes_back_to_the_box(admin):
    event = _event()
    _save(admin, event, ev_collage_builtins=["duo"])
    _save(admin, event)
    assert not ecosystem.event_profile_overrides(db.get_event_by_id(event["id"])).get("collage_exact")


def test_a_layout_a_customer_approved_stays_ticked(admin):
    event = _event()
    datei = _own_template("Kunde")
    db.update_event(event["id"], design_json=branding.dump_design({"booth": {"collage_templates": [datei]}}))
    page = admin.get(f"/admin/events/{event['id']}/edit").get_data(as_text=True)
    i = page.index(f'name="ev_collage_templates" value="{datei}"')
    assert "checked" in page[i:i + 160].split(">")[0]


def test_unknown_names_are_dropped(admin):
    event = _event()
    _save(admin, event, ev_collage_templates=["../../etc/passwd", "gibtsnicht.json"],
          ev_collage_builtins=["gibtsnicht", "duo"])
    over = ecosystem.event_profile_overrides(db.get_event_by_id(event["id"]))
    assert over["collage_templates"] == [] and over["collage_builtins"] == ["duo"]


def test_the_preview_is_rendered_by_the_gallery_and_cached(admin):
    event = _event()
    url = f"/admin/events/{event['id']}/layouts/b/schlicht-4-schwarz.png"
    r = admin.get(url)
    assert r.status_code == 200 and r.mimetype == "image/png"
    from io import BytesIO
    img = Image.open(BytesIO(r.data))
    assert max(img.size) <= 480 and img.size[0] > img.size[1] * 0.6     # portrait 2:3
    assert img.convert("RGB").getpixel((2, 2)) == (0, 0, 0)               # black paper at the edge
    from app import layout_wahl
    assert any(layout_wahl.cache_dir().glob("*.png"))
    assert admin.get(url).data == r.data
    datei = _own_template()
    assert admin.get(f"/admin/events/{event['id']}/layouts/t/{datei}.png").status_code == 200
    assert admin.get(f"/admin/events/{event['id']}/layouts/b/gibtsnicht.png").status_code == 404
    assert admin.get(f"/admin/events/{event['id']}/layouts/t/gibtsnicht.json.png").status_code == 404


def test_the_preview_needs_the_admin(client):
    client.get("/healthz")
    event = _event()
    r = client.get(f"/admin/events/{event['id']}/layouts/b/duo.png")
    assert r.status_code in (302, 401, 403)
