"""The admin sets the deletion period per event and as account default, or switches it off.

Owner, 15.11.2026: "Löschfrist muss man als Admin selbst einstellen können oder
sogar deaktivieren". "No automatic deletion" is the existing ``auto_delete = 0``
of an event (app/retention.py); this file pins what must never happen around
it: a gallery switched off being emptied by the run, a period of 0 days read as
"delete now", a booth or a privacy page still promising a date, and an event
that cannot be switched back to a number.
"""
import json
import secrets

import pytest

from app import db, ecosystem, gallery_settings as gsm, legal, retention


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    ecosystem.init_schema()
    yield
    db.set_setting("retention_auto_default", "")
    db.set_setting("retention_days_default", "")


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _event(date="2020-01-01", days=30, auto_delete=None):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Alt {k}", slug=f"roff-{k}", upload_token=f"t-{k}", retention_days=days)
    db.update_event(eid, {"published": 1, "date": date, "gallery_json": json.dumps(gsm.clean({}))})
    if auto_delete is not None:
        retention.set_auto_delete(eid, auto_delete)
    con = db.get_db()
    con.execute("INSERT INTO media(event_id, kind, filename, path_original, size_bytes, sha256, share_token)"
                " VALUES (?,?,?,?,?,?,?)", (eid, "photo", "a.jpg", f"storage/roff-{k}/orig/a.jpg", 1, k,
                                             secrets.token_hex(8)))
    con.commit()
    con.close()
    return db.get_event_by_id(eid)


def test_the_run_skips_an_event_without_automatic_deletion(client):
    ev = _event(auto_delete=False)
    assert retention.deadline(ev) is None
    assert ev["slug"] not in [e["slug"] for e in retention.due()]
    assert ev["slug"] not in retention.sweep()["events"]
    assert db.count_media(ev["id"]) == 1


def test_an_existing_booth_event_is_still_deleted_after_its_period(client):
    ev = _event()
    assert ev["slug"] in retention.sweep()["events"]


def test_a_period_of_zero_days_never_means_delete_now(client):
    """Old rows or a hand-edited database can hold 0: that is "no period", not "now"."""
    ev = _event(days=0)
    assert retention.deadline(ev) is None
    assert ev["slug"] not in retention.sweep()["events"]
    assert db.count_media(ev["id"]) == 1
    # The booth is not told "deleted after 0 days" either.
    note = _booth_note(ev)
    assert "0 Tagen" not in note["de"] and "{tage}" not in note["de"]


def _booth_note(ev):
    dev = db.create_device(name=f"Box {ev['slug']}")
    ecosystem.save_device_profile(dev["id"], {"consent": {"enabled": True, "days": 30}}, ev["id"])
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://galerie.example")
    return prof["consent"]["note"]


def test_a_booth_on_an_event_without_deletion_promises_no_date(client):
    note = _booth_note(_event(auto_delete=False))
    for lang in ("de", "en"):
        assert "{tage}" not in note[lang] and "30" not in note[lang]
    assert "nicht automatisch gelöscht" in note["de"]
    assert "not deleted automatically" in note["en"]


def test_the_privacy_page_says_who_deletes_instead_of_a_date(client):
    ev = _event(auto_delete=False)
    md_de = legal.page_markdown("datenschutz", "de", event=ev)
    assert "Eine automatische Löschung gibt es für diese Galerie nicht" in md_de
    assert "Betreiber oder der Gastgeber" in md_de
    assert "gelöscht (eingestellte Frist" not in md_de
    md_en = legal.page_markdown("datenschutz", "en", event=ev)
    assert "no automatic deletion" in md_en.lower()
    assert "operator or the host" in md_en


def test_the_account_default_off_applies_to_new_events_only(client):
    old = _event()
    db.set_setting("retention_auto_default", "0")
    new = _event()
    assert retention.auto_delete(db.get_event_by_id(old["id"]))
    assert not retention.auto_delete(db.get_event_by_id(new["id"]))
    assert new["slug"] not in retention.sweep()["events"]


def test_the_account_default_period_is_used_for_new_events(client, formular):
    db.set_setting("retention_days_default", "60")
    assert retention.default_days() == 60
    db.set_setting("retention_days_default", "0")
    assert retention.default_days() == db.DEFAULT_RETENTION_DAYS
    _admin(client)
    db.set_setting("retention_days_default", "45")
    page = client.get("/admin/events/new").get_data(as_text=True)
    assert 'value="45"' in page


def test_the_account_defaults_are_set_in_the_settings(client, formular):
    _admin(client)
    page = client.get("/admin/settings/homepage").get_data(as_text=True)
    assert 'name="retention_days_default"' in page and 'name="retention_auto_default"' in page
    formular("/admin/settings/homepage", {"retention_days_default": "21", "retention_auto_default": "0",
                                          "download_sizes_enabled": "1"})
    assert db.get_setting("retention_days_default") == "21"
    assert db.get_setting("retention_auto_default") == "0"
    formular("/admin/settings/homepage", {"retention_days_default": "0", "retention_auto_default": "1",
                                          "download_sizes_enabled": "1"})
    assert db.get_setting("retention_days_default") == "21"      # 0 is refused, the old value stays


def test_the_event_form_never_stores_zero_or_less(client, formular):
    ev = _event(days=14)
    _admin(client)
    for raw in ("0", "-3", "abc"):
        formular(f"/admin/events/{ev['id']}/edit", {"name": ev["name"], "slug": ev["slug"], "date": "2020-01-01",
                                                     "retention_days": raw, "auto_delete": "1"})
        assert db.get_event_by_id(ev["id"])["retention_days"] == 14


def test_switching_off_and_back_to_a_number_works(client, formular):
    ev = _event(days=30)
    _admin(client)
    formular(f"/admin/events/{ev['id']}/edit", {"name": ev["name"], "slug": ev["slug"], "date": "2020-01-01",
                                                 "retention_days": "30", "auto_delete": ""})
    fresh = db.get_event_by_id(ev["id"])
    assert not retention.auto_delete(fresh)
    assert fresh["slug"] not in retention.sweep()["events"]
    formular(f"/admin/events/{ev['id']}/edit", {"name": ev["name"], "slug": ev["slug"], "date": "2020-01-01",
                                                 "retention_days": "10", "auto_delete": "1"})
    fresh = db.get_event_by_id(ev["id"])
    assert retention.auto_delete(fresh) and fresh["retention_days"] == 10
    assert fresh["slug"] in retention.sweep()["events"]
