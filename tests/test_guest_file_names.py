"""What a guest sees and hears is the name the download would get (17.11.2026).

The guest gallery printed the storage name of a picture ("9rq3r2txww_web.jpg",
"5asa97v9pu.f1_web.jpg") in the file-info line, in the aria-labels a screen
reader speaks and in the name handed to the download and share buttons. The
download itself already had a naming rule (app/dateinamen.py); now every
guest-visible name comes from the same helper, computed once per page, and an
event without any rule gets a readable built-in name (``<event>-<nr>``)
instead of the storage id.
"""
import re
import secrets

import pytest
from PIL import Image

from app import db, ecosystem, gallery_settings as gsm, media

STORAGE = ["9rq3r2txww_web.jpg", "5asa97v9pu.f1_web.jpg"]


def _event(app, *, naming="", pattern="", info=True, name="Sommerfest Müller"):
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("download_sizes_enabled", "1")
        db.set_setting("file_naming", naming)
        db.set_setting("download_pattern", pattern)
        slug = "nm-" + secrets.token_hex(3)
        eid = db.create_event(slug=slug, name=name, upload_token=secrets.token_hex(8))
        ev = db.get_event_by_id(eid)
        gs = gsm.for_event(ev)
        gs.update({"show_file_info": info, "download_single": True, "selection_enabled": True})
        db.update_event(eid, published=1, gallery_json=gsm.store(ev, gs))
        mids = []
        for i, fn in enumerate(STORAGE):
            full = media.data_dir() / f"orig/{slug}-{i}.jpg"
            full.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (40, 30), (120, 80, 40)).save(full, "JPEG")
            mid = db.add_media(event_id=eid, kind="photo", filename=fn,
                               path_original=f"orig/{slug}-{i}.jpg", path_webp=f"orig/{slug}-{i}.jpg",
                               path_thumb=f"orig/{slug}-{i}.jpg", size_bytes=1000 + i,
                               sha256=secrets.token_hex(8), share_token=secrets.token_hex(6))
            con = db.get_db()
            con.execute("UPDATE media SET taken_at=?, created_at=? WHERE id=?",
                        (f"2026-11-1{i + 1}T18:0{i}:05", f"2026-11-1{i + 1} 18:0{i}:05", mid))
            con.commit()
            con.close()
            mids.append(mid)
        return slug, eid, mids


def _guest_visible(html: str) -> str:
    """Everything a person or a screen reader gets from the page, links and picture paths left out."""
    body = html.split("<body", 1)[-1]
    body = re.sub(r"<(script|style)\b.*?</\1>", "", body, flags=re.S)
    attrs = " ".join(re.findall(r'\b(?:aria-label|alt|title|data-filename|data-name)="([^"]*)"', body))
    handlers = " ".join(re.findall(r'\bonclick="([^"]*)"', body))
    text = re.sub(r"<[^>]+>", " ", body)
    return f"{text} {attrs} {handlers}"


def _storage_ids(visible: str):
    return [s for s in STORAGE + [x.rsplit("_web", 1)[0] for x in STORAGE] if s in visible]


def test_a_fixed_rule_names_what_guests_see_and_hear(app, client):
    slug, _, mids = _event(app, naming="event_datetime")
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    visible = _guest_visible(html)
    assert _storage_ids(visible) == []
    assert "Sommerfest-Mueller_2026-11-11_18-00-05.jpg" in visible      # the file-info line
    assert 'aria-label="' in html and "Sommerfest-Mueller_2026-11-12_18-01-05.jpg" in visible
    # The name in the download button is the one the download delivers.
    r = client.get(f"/gallery/{slug}/photo/{mids[0]}/download?size=original")
    assert "Sommerfest-Mueller_2026-11-11_18-00-05.jpg" in r.headers.get("Content-Disposition", "")


def test_a_pattern_names_what_guests_see_and_hear(app, client):
    slug, _, mids = _event(app, pattern="{event}-{datum}-{nr}")
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    visible = _guest_visible(html)
    assert _storage_ids(visible) == []
    assert re.search(r"nm-[0-9a-f]{6}-2026-11-11-0001\.jpg", visible)
    assert re.search(r"nm-[0-9a-f]{6}-2026-11-12-0002\.jpg", visible)
    cd = client.get(f"/gallery/{slug}/photo/{mids[1]}/download?size=original").headers["Content-Disposition"]
    assert re.search(r"nm-[0-9a-f]{6}-2026-11-12-0002\.jpg", cd)


def test_without_any_rule_the_name_is_readable_not_the_storage_id(app, client):
    slug, _, mids = _event(app)
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    visible = _guest_visible(html)
    assert _storage_ids(visible) == []
    assert f"{slug}-0001.jpg" in visible and f"{slug}-0002.jpg" in visible
    # ... and the download hands out the same name.
    cd = client.get(f"/gallery/{slug}/photo/{mids[0]}/download?size=original").headers["Content-Disposition"]
    assert f"{slug}-0001.jpg" in cd and STORAGE[0].split("_")[0] not in cd


def test_an_explicit_pattern_is_left_as_it_is(app, client):
    slug, _, mids = _event(app, pattern="Fest {nr}")
    cd = client.get(f"/gallery/{slug}/photo/{mids[0]}/download?size=original").headers["Content-Disposition"]
    assert 'filename="Fest 0001.jpg"' in cd


def test_the_page_asks_for_the_numbers_once_not_once_per_photo(app, client, monkeypatch):
    from app import dateinamen
    slug, _, _ = _event(app, pattern="{event}-{nr}")
    calls = {"nummern": 0, "laufende": 0}
    real_n, real_l = dateinamen.nummern, dateinamen.laufende_nummer

    def counting_n(*a, **k):
        calls["nummern"] += 1
        return real_n(*a, **k)

    def counting_l(*a, **k):
        calls["laufende"] += 1
        return real_l(*a, **k)

    monkeypatch.setattr(dateinamen, "nummern", counting_n)
    monkeypatch.setattr(dateinamen, "laufende_nummer", counting_l)
    client.get(f"/gallery/{slug}")
    assert calls["nummern"] == 1 and calls["laufende"] == 0


def test_the_share_link_page_and_the_session_page_show_no_storage_id_either(app, client):
    slug, eid, mids = _event(app, naming="event_datetime")
    from app import sharelinks
    with app.app_context():
        link = sharelinks.create(eid, media_ids=mids, allow_download=True) if hasattr(sharelinks, "create") else None
    if not link:
        pytest.skip("share link helper not available")
    html = client.get(f"/l/{link['slug']}").get_data(as_text=True)
    assert _storage_ids(_guest_visible(html)) == []
