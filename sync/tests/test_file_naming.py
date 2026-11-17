"""File names "event name, date and time" at the booth's storage targets.

The rule travels with the event data (profile ``event.file_naming`` and
``event.name``, copied into each outbox entry's meta), so the booth names
files on a USB stick, in a folder or on an FTP/SFTP server offline. Without
the rule, or with ``original``, every name stays what it was.
"""
import datetime as dt
from pathlib import Path

from photobox_sync import targets as t


def _item(uid, path, *, variant="web", kind="photo", rule="event_datetime", name="Hochzeit Müller", **meta):
    return {"uid": uid, "path": path, "variant": variant, "kind": kind, "event_slug": "hochzeit",
            "meta": {"taken_at": "2026-07-20T21:14:03", "file_naming": rule, "event_name": name, **meta}}


def test_without_the_rule_the_name_is_what_it_was():
    for rule in ("", "original", None):
        assert t.file_name(_item("abcd2345xy", "/s/x/result.jpg", rule=rule)) == "abcd2345xy_result.jpg"


def test_the_run_and_its_parts_get_one_name_each_numbered_in_the_second():
    names = [t.file_name(_item("abcd2345xy", "/s/x/result.jpg", kind="collage", variant="original", n_singles=2)),
             t.file_name(_item("abcd2345xy.f1", "/s/x/f1.jpg", variant="original", n_singles=2)),
             t.file_name(_item("abcd2345xy.f2", "/s/x/f2.jpg", variant="original", n_singles=2)),
             t.file_name(_item("abcd2345xy.1", "/s/x/shot1.jpg", kind="original", variant="original",
                               n_singles=2)),
             t.file_name(_item("abcd2345xy.gif", "/s/x/anim.gif", kind="gif"))]
    assert names == ["Hochzeit-Mueller_2026-07-20_21-14-03.jpg", "Hochzeit-Mueller_2026-07-20_21-14-03_2.jpg",
                     "Hochzeit-Mueller_2026-07-20_21-14-03_3.jpg", "Hochzeit-Mueller_2026-07-20_21-14-03_4.jpg",
                     "Hochzeit-Mueller_2026-07-20_21-14-03.gif"]
    assert len(set(names)) == len(names)


def test_the_same_entry_gets_the_same_name_on_every_retry():
    item = _item("abcd2345xy.f2", "/s/x/f2.jpg", n_singles=3)
    assert t.file_name(item) == t.file_name(dict(item)) == t.file_name(item)


def test_a_flat_target_keeps_web_and_original_apart():
    web = _item("abcd2345xy", "/s/x/result_web.jpg", variant="web")
    orig = _item("abcd2345xy", "/s/x/result.jpg", variant="original")
    assert t.file_name(web, flat=True) != t.file_name(orig, flat=True)
    assert t.file_name(orig, flat=True) == "Hochzeit-Mueller_2026-07-20_21-14-03.jpg"
    # In folders by event and date they stand in fotos/ and originale/ under one name.
    assert t._local_name(web).endswith("fotos/Hochzeit-Mueller_2026-07-20_21-14-03.jpg")
    assert t._local_name(orig).endswith("originale/Hochzeit-Mueller_2026-07-20_21-14-03.jpg")


def test_videos_keep_their_extension_and_two_films_do_not_collide():
    film = t.file_name(_item("abcd2345xy.mp4", "/s/x/result.mp4", kind="video"))
    boom = t.file_name(_item("abcd2345xy.boom", "/s/x/boomerang.mp4", kind="boomerang"))
    assert film.endswith(".mp4") and boom.endswith(".mp4") and film != boom


def test_the_folder_target_writes_the_new_name(tmp_path):
    src = tmp_path / "result.jpg"
    src.write_bytes(b"x" * 10)
    target = t.FolderTarget({"path": str(tmp_path / "stick"), "layout": "flat"})
    out = target.upload(_item("abcd2345xy", str(src), variant="original"))
    assert Path(out["file"]).name == "Hochzeit-Mueller_2026-07-20_21-14-03.jpg"
    assert src.exists()                       # the booth's own file keeps its name


def test_the_safe_name_rules():
    assert t.sicherer_eventname("Straße  Ärger") == "Strasse-Aerger"
    assert t.sicherer_eventname("") == "Event" and t.sicherer_eventname("NUL") == "NUL-Event"
    name = t.event_datetime_name("Ü" * 300, dt.datetime(2026, 1, 2, 3, 4, 5), "jpeg", 12)
    assert len(name) <= 100 and name.endswith("_2026-01-02_03-04-05_12.jpeg")
