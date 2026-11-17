"""File names "event name, date and time" when pictures are handed out.

Owner, 15.11.2026: "wenn die Dateinamen der Fotos definierbar wären …
entweder Original oder halt Name des Events … und Datum und Uhrzeit". Per
event (account default in the settings): ``original`` (default, today's
behaviour) or ``event_datetime`` → ``<Event>_<YYYY-MM-DD>_<HH-MM-SS>.<ext>``
from the capture time. Files on disk keep their names; only the name a
download carries changes, the same in a single download and in every ZIP.
"""
import datetime as dt
import io
import json
import secrets
import zipfile

import pytest
from PIL import Image

from app import db, dateinamen, ecosystem, gallery_settings as gsm, media as media_mod


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    ecosystem.init_schema()
    db.set_setting("download_pattern", "")
    db.set_setting("file_naming", "")
    db.set_setting("zip_download_enabled", "1")
    db.set_setting("zip_download_admin_only", "0")
    db.set_setting("selection_download_enabled", "1")
    yield
    db.set_setting("file_naming", "")


# ── The safe event name ───────────────────────────────────────────────
@pytest.mark.parametrize("name, safe", [
    ("Hochzeit Müller & Söhne", "Hochzeit-Mueller-Soehne"),
    ("Straße  Ärger", "Strasse-Aerger"),
    ("Café Crème brûlée", "Cafe-Creme-brulee"),
    ("Sommerfest 2026", "Sommerfest-2026"),
    ("a__b--c", "a_b-c"),
    ("  -Party-  ", "Party"),
    ("Grüße/aus\\Köln: 30.", "Gruesse-aus-Koeln-30"),
    ("", "Event"),
    ("!!!", "Event"),
    ("🎉", "Event"),
    ("CON", "CON-Event"),
    ("lpt1", "lpt1-Event"),
    ("Com9", "Com9-Event"),
])
def test_the_event_name_becomes_a_safe_file_name(name, safe):
    assert dateinamen.sicherer_eventname(name) == safe


def test_a_long_name_keeps_the_whole_file_name_within_100_characters():
    stem = dateinamen.sicherer_eventname("Ä" * 300)
    assert stem and len(stem) <= dateinamen.EVENT_MAX
    name = dateinamen.event_datetime_name("Ä" * 300, dt.datetime(2026, 7, 20, 21, 14, 3), "webm", 99)
    assert len(name) <= 100 and name.endswith("_99.webm")
    assert not name.rstrip(".webm").endswith((".", " "))


def test_the_sync_package_names_the_same_way():
    """The booth writes to USB/SFTP/FTP/folder with its own copy of the rule."""
    # Loaded from this checkout by path: an installed photobox_sync may be another copy.
    import importlib.util
    from pathlib import Path
    pfad = Path(__file__).resolve().parents[1] / "sync" / "photobox_sync" / "targets.py"
    spec = importlib.util.spec_from_file_location("_targets_here", pfad)
    targets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(targets)
    for name in ("Hochzeit Müller & Söhne", "CON", "", "Ä" * 300, "Grüße/aus\\Köln: 30."):
        assert targets.sicherer_eventname(name) == dateinamen.sicherer_eventname(name)
        when = dt.datetime(2026, 7, 20, 21, 14, 3)
        assert targets.event_datetime_name(name, when, "jpg", 2) == dateinamen.event_datetime_name(
            name, when, "jpg", 2)


# ── In a gallery ──────────────────────────────────────────────────────
def _event(rule="event_datetime", name="Hochzeit Müller", shots=(("2026-07-20T21:14:03", "a"),)):
    k = secrets.token_hex(4)
    eid = db.create_event(name=name, slug=f"fn-{k}", upload_token=f"tok-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(
        {"file_naming": rule, "download_zip": True, "selection": True}))})
    con = db.get_db()
    ids = []
    for i, (taken, uid) in enumerate(shots):
        rel = f"storage/fn-{k}/orig/{uid}_{i}.jpg"
        p = media_mod.data_dir() / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 50), (100 + i * 20, 90, 80)).save(p)
        cur = con.execute(
            "INSERT INTO media (event_id, filename, path_original, kind, share_token, created_at, taken_at, uid,"
            " variant) VALUES (?,?,?,?,?,?,?,?,?)",
            (eid, f"{uid}_{i}.jpg", rel, "photo", secrets.token_hex(8), "2026-07-20 19:00:00", taken,
             f"{k}{uid}", "web"))
        ids.append(int(cur.lastrowid))
    con.commit()
    con.close()
    return db.get_event_by_id(eid), ids


def _download_name(client, event, mid, size="original"):
    r = client.get(f"/gallery/{event['slug']}/photo/{mid}/download?size={size}")
    assert r.status_code == 200, r.status_code
    return r.headers["Content-Disposition"]


def test_original_chosen_on_purpose_keeps_the_boxs_name(client):
    event, ids = _event(rule="original")
    assert 'filename=a_0.jpg' in _download_name(client, event, ids[0]).replace('"', "")


def test_nothing_chosen_gives_a_readable_name_not_the_storage_id(client):
    """17.11.2026: with no rule anywhere the guest got "9rq3r2txww_web.jpg"; now "<event>-<nr>"."""
    event, ids = _event(rule="")
    header = _download_name(client, event, ids[0]).replace('"', "")
    assert f"filename={event['slug']}-0001.jpg" in header and "a_0" not in header


def test_a_single_download_is_named_after_event_and_capture_time(client):
    event, ids = _event()
    header = _download_name(client, event, ids[0])
    assert "Hochzeit-Mueller_2026-07-20_21-14-03.jpg" in header
    assert header.startswith("attachment")
    # The file on disk keeps its name.
    assert db.get_media(ids[0])["path_original"].endswith("a_0.jpg")


def test_two_shots_in_one_second_are_numbered_the_same_everywhere(client):
    same = "2026-07-20T21:14:03"
    event, ids = _event(shots=((same, "a"), (same, "b"), (same, "c"), ("2026-07-20T21:15:00", "d")))
    single = [_download_name(client, event, i) for i in ids]
    again = [_download_name(client, event, i) for i in ids]
    assert single == again
    expected = ["Hochzeit-Mueller_2026-07-20_21-14-03.jpg", "Hochzeit-Mueller_2026-07-20_21-14-03_2.jpg",
                "Hochzeit-Mueller_2026-07-20_21-14-03_3.jpg", "Hochzeit-Mueller_2026-07-20_21-15-00.jpg"]
    for header, name in zip(single, expected):
        assert name in header, (header, name)
    r = client.get(f"/gallery/{event['slug']}/download.zip")
    assert r.status_code == 200
    assert sorted(zipfile.ZipFile(io.BytesIO(r.data)).namelist()) == sorted(expected)
    sel = client.get(f"/gallery/{event['slug']}/selection.zip?ids={ids[2]},{ids[0]}&size=original")
    assert sel.status_code == 200
    assert sorted(zipfile.ZipFile(io.BytesIO(sel.data)).namelist()) == sorted([expected[0], expected[2]])


def test_without_a_capture_time_the_upload_time_counts_in_local_time(client):
    event, ids = _event(shots=(("", "a"),))
    local = dt.datetime(2026, 7, 20, 19, 0, 0, tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
    assert local.strftime("%Y-%m-%d_%H-%M-%S") in _download_name(client, event, ids[0])


def test_the_account_default_applies_unless_the_event_says_original(client):
    db.set_setting("file_naming", "event_datetime")
    inherit, ids = _event(rule="")
    assert "Hochzeit-Mueller_2026-07-20_21-14-03.jpg" in _download_name(client, inherit, ids[0])
    own, ids2 = _event(rule="original")
    assert "a_0.jpg" in _download_name(client, own, ids2[0])


def test_a_share_link_hands_out_the_same_names(client):
    from app import sharelinks
    event, ids = _event()
    link = sharelinks.create(event["id"], title="Eltern", days=7, allow_download=True)
    r = client.get(f"/l/{link['slug']}/file/{ids[0]}?download=1")
    assert r.status_code == 200
    assert "Hochzeit-Mueller_2026-07-20_21-14-03.jpg" in r.headers["Content-Disposition"]
    z = client.get(f"/l/{link['slug']}/all.zip")
    assert zipfile.ZipFile(io.BytesIO(z.data)).namelist() == ["Hochzeit-Mueller_2026-07-20_21-14-03.jpg"]


def test_the_rule_is_a_gallery_setting_and_an_account_setting(client, formular):
    assert gsm.clean({"file_naming": "event_datetime"})["file_naming"] == "event_datetime"
    assert gsm.clean({"file_naming": "../x"})["file_naming"] == ""
    event, _ = _event(rule="")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    page = client.get(f"/admin/events/{event['id']}/edit").get_data(as_text=True)
    assert 'name="file_naming"' in page
    formular(f"/admin/events/{event['id']}/gallery-settings", {"file_naming": "event_datetime"},
             von=f"/admin/events/{event['id']}/edit")
    assert gsm.for_event(db.get_event_by_id(event["id"]))["file_naming"] == "event_datetime"
    settings = client.get("/admin/settings/homepage").get_data(as_text=True)
    assert 'name="file_naming"' in settings
    formular("/admin/settings/homepage", {"file_naming": "event_datetime", "download_sizes_enabled": "1"})
    assert db.get_setting("file_naming") == "event_datetime"


def test_the_booth_gets_the_rule_with_the_event_data(client):
    """The booth names files on its storage targets offline: rule and name come along."""
    event, _ = _event()
    dev = db.create_device(name=f"Box {event['slug']}")
    ecosystem.save_device_profile(dev["id"], {}, event["id"])
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://galerie.example")
    assert prof["event"]["file_naming"] == "event_datetime"
    assert prof["event"]["name"] == "Hochzeit Müller"
    plain, _ = _event(rule="")
    ecosystem.save_device_profile(dev["id"], {}, plain["id"])
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://galerie.example")
    assert prof["event"]["file_naming"] == "original"
