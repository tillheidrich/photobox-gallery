"""The owner's logo at the top of every page of the backend, and safely served.

Asked for on 15.11.2026 ("Meine Backend-Galerie soll bitte das Logo von mir
oben anzeigen"). The login page showed a generic picture icon, the sidebar
cropped a wide logo into a 32 px square on a coloured tile, and an SVG upload
failed with a technical error while the hint recommended SVG.

Now: one helper (branding.global_logo, template _brand_logo.html) for the
admin sidebar, login, recovery, client area and gallery header; an optional
second logo for dark surfaces; uploads only PNG, JPEG, WebP up to 2 MB,
always stored as a PNG made by the gallery, transparent padding trimmed,
the upload kept as it came; served as image/png with nosniff.
"""
import io

import pytest
from PIL import Image

from app import accounts, db


def _png(size=(512, 512), box=(0, 122, 512, 390), colour=(0, 0, 0, 255)):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), colour), box[:2])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


@pytest.fixture()
def admin(client):
    client.get("/healthz")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "t"
    yield client
    for key in ("branding_logo_path", "branding_logo_dark_path"):
        db.set_setting(key, "")


def _upload(client, buf, name="logo.png", variant="light"):
    return client.post("/admin/settings/logo", data={"csrf_token": "t", "variant": variant,
                                                      "logo": (buf, name)},
                       content_type="multipart/form-data", follow_redirects=True)


def test_the_login_page_shows_the_logo_when_there_is_one(admin, app):
    guest = app.test_client()
    html = guest.get("/admin/login").get_data(as_text=True)
    assert 'class="login-logo"' in html and "/branding/logo" not in html      # the generic icon
    _upload(admin, _png())
    html = guest.get("/admin/login").get_data(as_text=True)
    assert 'class="login-logo"' not in html and 'src="/branding/logo?v=' in html
    assert "/branding/logo?v=" in guest.get("/admin/recover").get_data(as_text=True)


def test_the_logo_is_open_without_login_and_served_as_png(admin, app):
    _upload(admin, _png())
    r = app.test_client().get("/branding/logo?v=1")
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "max-age=86400" in r.headers.get("Cache-Control", "")


def test_transparent_padding_is_trimmed_and_the_upload_kept(admin):
    from app import media
    _upload(admin, _png())
    stored = media.data_dir() / db.get_setting("branding_logo_path")
    w, h = Image.open(stored).size
    assert w == 512 and h < 320, (w, h)              # the wide mark (268 px high) plus a margin, not 512
    assert (stored.parent / "logo-original.png").is_file()


def test_an_svg_is_refused_with_a_plain_sentence(admin):
    svg = io.BytesIO(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    html = _upload(admin, svg, "logo.svg").get_data(as_text=True)
    assert "SVG wird nicht angenommen" in html
    assert not db.get_setting("branding_logo_path")


def test_other_formats_are_refused_too(admin):
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 0, 0)).save(buf, "GIF")
    buf.seek(0)
    html = _upload(admin, buf, "logo.gif").get_data(as_text=True)
    assert "Bitte PNG, JPEG oder WebP" in html
    assert not db.get_setting("branding_logo_path")


def test_a_second_logo_for_dark_surfaces(admin, app):
    _upload(admin, _png())
    _upload(admin, _png(colour=(255, 255, 255, 255)), variant="dark")
    html = admin.get("/admin/devices").get_data(as_text=True)
    assert 'class="brand-logo-light"' in html and 'class="brand-logo-dark"' in html
    r = app.test_client().get("/branding/logo?variant=dark")
    assert r.status_code == 200
    dark = Image.open(io.BytesIO(r.data)).convert("RGBA")
    assert dark.getpixel((dark.width // 2, dark.height // 2))[0] > 200     # the white one
    # Without it, the one logo goes on a light chip in the dark theme
    admin.post("/admin/settings/logo/delete", data={"csrf_token": "t", "variant": "dark"})
    assert "brand-logo-only" in admin.get("/admin/devices").get_data(as_text=True)


def test_the_client_area_and_a_gallery_without_own_logo_show_it(admin, app):
    _upload(admin, _png())
    guest = app.test_client()
    assert "/branding/logo?v=" in guest.get("/kunde").get_data(as_text=True)


def test_the_upload_is_the_first_thing_on_the_settings_page(admin):
    html = admin.get("/admin/settings").get_data(as_text=True)
    assert html.index('id="logo"') < html.index("Admin-Account")
    assert "PNG, JPEG oder WebP bis 2 MB" in html and "Hochladen" in html
