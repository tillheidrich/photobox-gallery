"""Mehrere Fotoboxen auf einem Event.

Bei größeren Veranstaltungen stehen zwei oder drei Boxen. Die Galerie muss
das nicht nur aushalten, sondern auseinanderhalten können: Wenn eine Box
schief stand, ausgefallen ist oder das Licht nicht passte, will jemand sehen,
was von *dieser* Box kam — ohne die anderen mit durchzugehen.
"""
import secrets

import pytest


@pytest.fixture()
def admin(client, app):
    client.get("/healthz")
    with app.app_context():
        from app import db, ecosystem
        db.init_db()
        ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _event(app, name="Sommerfest"):
    from app import db
    slug = "boxen-" + secrets.token_hex(3)
    with app.app_context():
        return db.get_event_by_id(db.create_event(name=name, slug=slug,
                                                  upload_token=secrets.token_hex(8)))


def _box(app, name):
    from app import db
    with app.app_context():
        return db.create_device(name=f"{name} {secrets.token_hex(2)}")


def _foto(app, event_id, device_id, uid):
    """Ein Medium anlegen, wie es ein Upload täte."""
    from app import db
    with app.app_context():
        con = db.get_db()
        con.execute("""INSERT INTO media (event_id, kind, filename, path_original, share_token,
                                          created_at, uid, variant, device_id)
                       VALUES (?,?,?,?,?,datetime('now'),?,?,?)""",
                    (event_id, "photo", f"{uid}.jpg", f"x/{uid}.jpg", f"tok-{uid}",
                     uid, "web", device_id))
        con.commit()
        con.close()


def test_two_boxes_can_serve_the_same_event(admin, app):
    from app import db, ecosystem
    ev = _event(app)
    a, b = _box(app, "Box links"), _box(app, "Box rechts")
    with app.app_context():
        ecosystem.save_device_profile(a["id"], {}, ev["id"])
        ecosystem.save_device_profile(b["id"], {}, ev["id"])
        zugeordnet = [d for d in db.list_devices() if d.get("event_id") == ev["id"]]
    assert len(zugeordnet) == 2, "Ein Event gehört nicht einer einzigen Box"


def test_every_picture_remembers_which_box_took_it(admin, app):
    from app import db
    ev = _event(app)
    a, b = _box(app, "A"), _box(app, "B")
    _foto(app, ev["id"], a["id"], "aaa" + secrets.token_hex(2))
    _foto(app, ev["id"], b["id"], "bbb" + secrets.token_hex(2))
    with app.app_context():
        fotos = db.list_media(ev["id"])
    assert {f["device_id"] for f in fotos} == {a["id"], b["id"]}


def test_the_event_page_can_filter_by_box(admin, app):
    ev = _event(app)
    a, b = _box(app, "Box links"), _box(app, "Box rechts")
    for n in range(2):
        _foto(app, ev["id"], a["id"], f"li{n}" + secrets.token_hex(2))
    _foto(app, ev["id"], b["id"], "re" + secrets.token_hex(2))

    alle = admin.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert f"{a['name']} (2)" in alle and f"{b['name']} (1)" in alle
    assert alle.count('class="photo-thumb"') == 3

    nur_a = admin.get(f"/admin/events/{ev['id']}/edit?box={a['id']}").get_data(as_text=True)
    assert nur_a.count('class="photo-thumb"') == 2, "Gefiltert stehen nur die Bilder dieser Box da"


def test_with_one_box_the_filter_stays_out_of_the_way(admin, app):
    """Eine Box ist der Normalfall — dann braucht niemand eine Auswahl."""
    ev = _event(app)
    a = _box(app, "Einzelbox")
    _foto(app, ev["id"], a["id"], "ein" + secrets.token_hex(2))
    seite = admin.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert f"{a['name']} (1)" not in seite


def test_uploaded_pictures_without_a_box_are_named_as_such(admin, app):
    ev = _event(app)
    a = _box(app, "Box A")
    _foto(app, ev["id"], a["id"], "vb" + secrets.token_hex(2))
    _foto(app, ev["id"], None, "hg" + secrets.token_hex(2))
    seite = admin.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert "ohne Box" in seite


def test_statistics_stay_per_box():
    """Die Zahlen je Box gab es schon — sie dürfen durch nichts vermischt werden."""
    from app import stats
    assert hasattr(stats, "device") and hasattr(stats, "device_csv")
