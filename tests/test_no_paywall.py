"""Nothing is held back: the statistics page always shows the statistics.

``app/license.py`` was a leftover from an older edition with Starter, Pro and
White-Label plans. Its last effect was a box on ``/admin/analytics`` that said
"Analytics im Pro-Plan … Jetzt upgraden" instead of the numbers — in an AGPL
project that promises every feature to everyone who runs it.
"""
import importlib.util
import secrets
from pathlib import Path

from app import db

ROOT = Path(__file__).resolve().parent.parent


def _admin(client):
    client.get("/healthz")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def test_the_overview_shows_the_numbers_without_a_licence(client):
    admin = _admin(client)
    slug = "zahlen-" + secrets.token_hex(4)
    db.create_event(name="Zahlenfest", slug=slug, upload_token=secrets.token_hex(8))
    page = admin.get("/admin/analytics").get_data(as_text=True)
    assert "Galerie-Aufrufe" in page and "Events im Überblick" in page
    assert "Zahlenfest" in page, "every event is listed, not an empty table"
    for word in ("Pro-Plan", "upgraden", "pricing", "White-Label"):
        assert word not in page


def test_the_numbers_count_each_view_once(client):
    """Visible to everyone now, so they had better be right.

    The query joined media and stats in one go, so every view was counted
    once per photo of the event: three photos, two views, "6 views".
    """
    _admin(client)
    eid = db.create_event(name="Dreifach", slug="drei-" + secrets.token_hex(4),
                          upload_token=secrets.token_hex(8))
    for i in range(3):
        db.add_media(event_id=eid, kind="photo", filename=f"{i}.jpg", path_original=f"x/{i}.jpg",
                     path_webp=None, path_thumb=None, size_bytes=1, sha256=secrets.token_hex(8),
                     share_token=secrets.token_hex(8))
    for _ in range(2):
        db.log_stat(eid, "view_gallery")
    db.log_stat(eid, "download")
    row = next(dict(r) for r in db.get_analytics_overview() if r["id"] == eid)
    assert (row["media_count"], row["views"], row["downloads"]) == (3, 2, 1)


def test_there_is_no_licence_module_left():
    assert importlib.util.find_spec("app.license") is None
    hits = [str(p.relative_to(ROOT)) for p in (ROOT / "app").rglob("*.py")
            if "app.license" in p.read_text(encoding="utf-8")
            or "from app import license" in p.read_text(encoding="utf-8")]
    assert hits == []


def test_the_page_uses_the_projects_icon_not_an_emoji():
    template = (ROOT / "app" / "templates" / "admin" / "analytics.html").read_text(encoding="utf-8")
    assert not any(ord(ch) >= 0x1F000 for ch in template), "no emoji"
    # pb-statistik from app/static/img/brand/photobox-icons.svg
    assert 'd="M4 3.5v16.5h16"' in template
