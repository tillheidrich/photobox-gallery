"""Settings that used to exist only at the booth now live in the gallery form.

The booth takes a profile from the gallery whole (``Store.apply_profile``
merges it onto the booth's defaults, not onto what is set at the booth), and
the gallery's profile always carries ``aufraeumen``, ``meldungen`` and
``collage_fit`` (from its ``DEFAULT_PROFILE``). So a value set at the booth
was replaced by the gallery's at the next sync — with no field in the gallery
to set it there. Found 15.11.2026; the gallery form is the one place now.
Also: the dead ``privacy.keep_originals`` write is gone (nothing reads it),
and the colour fallbacks of the form come from ``DEFAULT_PROFILE["theme"]``.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _token(client, dev_id) -> str:
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        return s.get("csrf_token") or ""


def test_the_booth_replaces_its_profile_with_the_gallerys(tmp_path):
    """The evidence for the defect: a local value does not survive a sync."""
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore.config import Store
    store = Store(tmp_path)
    store.profile["aufraeumen"]["nach_tagen"] = 3            # set at the booth
    from app import ecosystem
    store.apply_profile({"version": 2, **ecosystem.DEFAULT_PROFILE})
    assert store.profile["aufraeumen"]["nach_tagen"] == 14


def test_the_form_sets_them(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Dauerbox")["id"]
    token = _token(client, dev_id)
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "collage_fit": "einpassen",
        "aufraeumen_enabled": "on", "aufraeumen_nach_tagen": "999", "aufraeumen_behalten_mindestens": "7",
        "meldungen_box_enabled": "on", "meldungen_box_ab_stufe": "warnung",
        "meldungen_mail_enabled": "on", "meldungen_mail_to": "technik@example.de",
        "meldungen_mail_ab_stufe": "quatsch", "meldungen_wiederholen_minuten": "30",
    }, follow_redirects=True)
    assert r.status_code == 200
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["collage_fit"] == "einpassen"
    assert p["aufraeumen"] == {"enabled": True, "nach_tagen": 365, "nur_bestaetigte": False,
                               "behalten_mindestens": 7}
    assert p["meldungen"] == {"mail": {"enabled": True, "to": "technik@example.de", "ab_stufe": "warnung"},
                              "box": {"enabled": True, "ab_stufe": "warnung"}, "wiederholen_minuten": 30}
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'name="aufraeumen_enabled" checked' in page
    assert re.search(r'name="aufraeumen_behalten_mindestens" value="7"', page)
    assert '<option value="einpassen" selected>' in page


def test_the_colour_fallbacks_are_the_shipped_theme(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Farbbox")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "theme_accent": "kein-hex", "theme_bg": ""},
        follow_redirects=True)
    theme = ecosystem.device_profile(db.get_device_by_id(dev_id))["theme"]
    assert theme["accent"] == ecosystem.DEFAULT_PROFILE["theme"]["accent"]
    assert theme["bg"] == ecosystem.DEFAULT_PROFILE["theme"]["bg"]


def test_the_parser_no_longer_writes_keep_originals():
    src = (ROOT / "app" / "blueprints" / "admin_booth.py").read_text(encoding="utf-8")
    assert '"keep_originals"' not in src
