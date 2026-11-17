"""The editor's preview is drawn by the renderer the booth prints with.

Not a second drawing of the same numbers: the gallery and the booth carry
the same file (vorlagen_render.py), and the preview endpoint runs it.
"""
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL / "booth"))

from boothcore.media import compose  # noqa: E402


def _admin(client):
    client.get("/healthz")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
    return client


VORLAGE = {"name": "Vorschau", "paper": "10x15",
           "slots": [{"x": 100, "y": 100, "w": 1000, "h": 700, "shot": 0, "rotation": 10}],
           "shapes": [{"kind": "rect", "x": 100, "y": 1300, "w": 1000, "h": 100, "fill": "#dc1e1e"}],
           "texts": [{"value": "{event_name}", "x": 600, "y": 1500, "size": 70, "align": "center"}]}


def test_gallery_and_booth_share_the_renderer_word_for_word():
    galerie = (WURZEL / "app" / "vorlagen_render.py").read_bytes()
    box = (WURZEL / "booth" / "boothcore" / "media" / "vorlagen_render.py").read_bytes()
    assert galerie == box, "tools/sync_editor.py laufen lassen"
    assert compose.render_template.__module__.endswith("vorlagen_render")


def test_the_preview_is_what_the_booth_prints(client):
    from app import vorlagen, vorlagen_render
    from app.blueprints.admin_booth import _assets_dir
    v, _ = vorlagen.pruefe(VORLAGE)
    proben = vorlagen.beispielbilder(1)
    kw = dict(assets_dir=_assets_dir(), qr_url="https://example.invalid/v", texts={"event_name": "Mia & Tom"})
    galerie = vorlagen_render.render_template(v, proben, **kw)
    box = compose.render_template(v, proben, **kw)
    assert galerie.tobytes() == box.tobytes()


def test_the_editor_gets_a_preview_picture(client):
    _admin(client)
    r = client.post("/admin/vorlagen/vorschau", json={"template": VORLAGE},
                    headers={"X-CSRF-Token": "t"})
    assert r.status_code == 200 and r.mimetype == "image/png"
    bild = Image.open(io.BytesIO(r.data)).convert("RGB")
    assert max(bild.size) <= 1200
    skala = bild.width / 1200
    rot = bild.getpixel((int(600 * skala), int(1350 * skala)))
    assert rot[0] > 180 and rot[1] < 80, "the red bar is in the preview"


def test_the_preview_needs_the_token(client):
    _admin(client)
    assert client.post("/admin/vorlagen/vorschau", json={"template": VORLAGE}).status_code == 400


def test_the_editor_page_knows_where_to_ask_for_a_preview(client):
    _admin(client)
    seite = client.get("/admin/vorlagen/editor").get_data(as_text=True)
    daten = json.loads(re.search(r'id="pbEditorDaten">(.*?)</script>', seite, re.S).group(1))
    assert daten["vorschauUrl"].endswith("/vorlagen/vorschau")
