"""Naming every picture of a page costs a few queries, not three per picture (review 17.11.2026).

``guest_namer`` resolved the event's settings (``_gs``) three times and read
the account settings two or three times per picture, each a SQLite
connection: 2000 pictures cost about 2.5 s more per gallery page. The
settings are now read once per page; the names stay exactly what they were.
Hidden rows shown to an admin all got the same fallback number before.
"""
import secrets
import time

import pytest

from app import db, ecosystem, gallery_settings as gsm


def _event(app, n, *, naming="", pattern=""):
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("file_naming", naming)
        db.set_setting("download_pattern", pattern)
        eid = db.create_event(slug="nb-" + secrets.token_hex(3), name="Hochzeit A",
                              upload_token=secrets.token_hex(8))
        con = db.get_db()
        for i in range(n):
            con.execute("INSERT INTO media(event_id,kind,filename,path_original,size_bytes,sha256,share_token,"
                        "created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (eid, "photo", f"x{i}_web.jpg", f"o/{i}.jpg", 1, secrets.token_hex(8),
                         secrets.token_hex(6), f"2026-11-18 18:{i // 60 % 60:02d}:{i % 60:02d}"))
        con.commit()
        con.close()
        return eid


def _rows(eid):
    con = db.get_db()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM media WHERE event_id=? ORDER BY id", (eid,))]
    finally:
        con.close()


def _old_names(event, rows):
    """The names as the per-picture path computes them (the reference)."""
    from app import dateinamen
    from app.blueprints.public import _abzugname
    from werkzeug.utils import secure_filename
    nummern = dateinamen.nummern(int(event["id"]))
    namen = dateinamen.event_datetime_namen(event)
    out = []
    for r in rows:
        base = secure_filename(r.get("filename") or "")
        out.append(_abzugname(event, r, base, "", nummer=nummern.get(int(r["id"])), namen=namen))
    return out


@pytest.mark.parametrize("naming,pattern", [("", ""), ("event_datetime", ""), ("", "{event}-{datum}-{nr}"),
                                            ("original", "")])
def test_the_names_are_the_same_as_before(app, naming, pattern):
    from app.blueprints.public import guest_namer
    eid = _event(app, 40, naming=naming, pattern=pattern)
    with app.app_context(), app.test_request_context("/"):
        ev = db.get_event_by_id(eid)
        rows = _rows(eid)
        name = guest_namer(ev)
        assert [name(r) for r in rows] == _old_names(ev, rows)


def test_2000_pictures_are_named_quickly_and_with_few_connections(app, monkeypatch):
    from app.blueprints.public import guest_namer
    eid = _event(app, 2000)
    with app.app_context(), app.test_request_context("/"):
        ev = db.get_event_by_id(eid)
        rows = _rows(eid)
        opened = {"n": 0}
        real = db.get_db

        def counting():
            opened["n"] += 1
            return real()
        monkeypatch.setattr(db, "get_db", counting)
        t = time.monotonic()
        name = guest_namer(ev)
        names = [name(r) for r in rows]
        took = time.monotonic() - t
    assert len(set(names)) == 2000
    assert opened["n"] <= 10, f"{opened['n']} connections for one page"
    assert took < 0.3, f"{took:.2f} s for 2000 names"


def test_rows_outside_the_numbering_get_numbers_of_their_own(app):
    """A hidden picture an admin sees is not in the guests' numbering; two of
    them shared ``len+1`` and so the same visible name."""
    from app.blueprints.public import guest_namer
    eid = _event(app, 5)
    with app.app_context(), app.test_request_context("/"):
        con = db.get_db()
        ids = [r["id"] for r in con.execute("SELECT id FROM media WHERE event_id=? ORDER BY id", (eid,))]
        con.execute("UPDATE media SET hidden=1 WHERE id IN (?,?)", (ids[1], ids[3]))
        con.commit()
        con.close()
        ev = db.get_event_by_id(eid)
        rows = _rows(eid)
        names = [guest_namer(ev)(r) for r in rows]
    assert len(set(names)) == 5, names
    # Visible pictures keep their numbers 1, 2, 3 in order.
    visible = [n for r, n in zip(rows, names) if r["id"] not in (ids[1], ids[3])]
    assert [v.rsplit("-", 1)[-1] for v in visible] == ["0001.jpg", "0002.jpg", "0003.jpg"]
