"""Overlay export as PNG (no. 65): a template's frame with transparent photo
fields, in the paper's pixel size, for other booth software. Admin only."""
import io

import pytest
from PIL import Image

from app import db, ecosystem


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _template(client):
    from app import vorlagen
    from app.blueprints.admin_booth import _assets_dir
    with client.application.app_context():
        v, problems = vorlagen.pruefe({
            "name": "Overlay Probe", "paper": "10x15", "background": "#204060",
            "slots": [{"x": 100, "y": 100, "w": 1000, "h": 1000, "shot": 0}],
            "texts": [{"x": 600, "y": 1500, "size": 90, "align": "center", "value": "Probe", "color": "#ffffff"}]})
        return v, vorlagen.speichern(v, _assets_dir())["file"]


def _login(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def test_the_overlay_is_for_the_admin_only(client):
    _, name = _template(client)
    assert client.get(f"/admin/vorlagen/overlay.png?datei={name}").status_code == 302


def test_the_overlay_has_the_paper_size_and_a_transparent_photo_field(client):
    v, name = _template(client)
    _login(client)
    r = client.get(f"/admin/vorlagen/overlay.png?datei={name}")
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert "attachment" in r.headers.get("Content-Disposition", "")
    img = Image.open(io.BytesIO(r.data))
    assert img.mode == "RGBA"
    assert list(img.size) == list(v["canvas"])
    assert img.getpixel((600, 600))[3] == 0          # inside the photo field
    # Outside the field: opaque and the colour the booth would print there.
    from app import vorlagen_render
    with client.application.app_context():
        printed = vorlagen_render.render_template(v, [], style_cfg={"preset": "clean"}).convert("RGB")
    margin = img.getpixel((30, 30))
    assert margin[3] == 255 and margin[:3] == printed.getpixel((30, 30))
    assert img.getpixel((600, 1500))[3] == 255           # the text below the field


def test_only_a_stored_template_is_exported(client):
    _login(client)
    assert client.get("/admin/vorlagen/overlay.png?datei=gibt-es-nicht.json").status_code == 404
    assert client.get("/admin/vorlagen/overlay.png?datei=../../gallery.json").status_code == 404


def test_the_template_list_offers_the_export(client):
    _, name = _template(client)
    _login(client)
    page = client.get("/admin/vorlagen").get_data(as_text=True)
    assert f"/admin/vorlagen/overlay.png?datei={name}" in page
