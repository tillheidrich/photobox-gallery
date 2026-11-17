"""The inline scripts of a public gallery must be valid JavaScript.

Regression: ``{{ x|tojson|forceescape }}`` inside a ``<script>`` block printed
``&#34;...&#34;`` and the whole lightbox script died with
``SyntaxError: Unexpected token '&'``. ``|forceescape`` belongs only into HTML
attributes, never into script text.
"""
import re
import secrets
import shutil
import subprocess

import pytest


def _event_with_photos(app, n=2):
    from app import db, media
    slug = "sc-" + secrets.token_hex(3)
    with app.app_context():
        db.init_db()
        eid = db.create_event(slug=slug, name="Script-Event",
                              upload_token=secrets.token_hex(8))
        data_dir = media.data_dir()
        for i in range(n):
            rel = f"webp/{slug}-{i}.webp"
            full = data_dir / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(b"RIFFfakewebp")
            db.add_media(event_id=eid, kind="photo", filename=f"foto_{i}.jpg",
                         path_original=rel, path_webp=rel, path_thumb=rel,
                         size_bytes=12, sha256=secrets.token_hex(8),
                         share_token=secrets.token_hex(6))
    return slug


def _inline_scripts(html):
    out = []
    for m in re.finditer(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", html, re.S):
        if "src=" in m.group("attrs"):
            continue
        out.append((m.group("attrs"), m.group("body")))
    return out


def test_gallery_scripts_have_no_html_escaped_json(app, client):
    slug = _event_with_photos(app)
    r = client.get(f"/gallery/{slug}")
    assert r.status_code == 200
    scripts = _inline_scripts(r.get_data(as_text=True))
    assert scripts
    for _attrs, body in scripts:
        assert "&#34;" not in body and "&amp;" not in body and "&quot;" not in body, body[:200]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_gallery_inline_scripts_parse_with_node(app, client, tmp_path):
    slug = _event_with_photos(app)
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    n = 0
    for attrs, body in _inline_scripts(html):
        if "json" in attrs:
            continue
        f = tmp_path / f"s{n}.js"
        f.write_text(body, encoding="utf-8")
        res = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        n += 1
    assert n >= 1


# ── Inline handlers on /meine ────────────────────────────────
# ``{{ x|tojson }}`` inside a double-quoted ``onclick``/``onsubmit`` without
# ``|forceescape`` ends the attribute at the first quote of the JSON: the share
# button and the "clear list" confirmation of /meine were cut off that way and
# did nothing (found 15.11.2026). Here the attribute is read the way a browser
# reads it and the handler must parse as a function body.

def _handlers(html):
    from html.parser import HTMLParser

    found = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name.startswith("on") and value:
                    found.append((tag, name, value))

    P().feed(html)
    return found


def _scanned_run(app, client):
    import json
    from PIL import Image
    from app import db, ecosystem, gallery_settings as gsm, media
    slug = "mh-" + secrets.token_hex(3)
    uid = "mh" + secrets.token_hex(5)
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        eid = db.create_event(slug=slug, name='Sommerfest "Am See"', upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1, share_key=?, gallery_json=? WHERE id=?",
                    (secrets.token_urlsafe(16), json.dumps(gsm.clean({})), eid))
        con.commit(); con.close()
        rel = f"storage/{slug}/webp/{uid}.jpg"
        full = media.data_dir() / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 50), (1, 2, 3)).save(full, "JPEG")
        mid = db.add_media(event_id=eid, kind="photo", filename=f'{uid} "1".jpg', path_original=rel,
                           path_webp=rel, path_thumb=rel, size_bytes=1, sha256=secrets.token_hex(8),
                           share_token=secrets.token_hex(6))
        con = db.get_db()
        con.execute("UPDATE media SET session_uid=?, uid=?, variant='web' WHERE id=?", (uid, uid, mid))
        con.commit(); con.close()
    with client.session_transaction() as s:
        s["p_uids"] = [uid]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_handlers_on_my_shots_parse_with_node(app, client, tmp_path):
    _scanned_run(app, client)
    html = client.get("/meine").get_data(as_text=True)
    handlers = _handlers(html)
    names = {(tag, name) for tag, name, _ in handlers}
    assert ("button", "onclick") in names, "share button handler missing"
    assert ("form", "onsubmit") in names, "clear-list confirmation missing"
    for n, (tag, name, body) in enumerate(handlers):
        f = tmp_path / f"h{n}.js"
        f.write_text(f"function handler(event) {{ {body} }}", encoding="utf-8")
        res = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert res.returncode == 0, f"{tag} {name}: {body!r}\n{res.stderr}"
